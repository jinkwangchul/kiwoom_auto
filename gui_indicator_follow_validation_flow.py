# -*- coding: utf-8 -*-
"""End-to-end wiring for the indicator-follow Validation user flow."""

from __future__ import annotations

from collections.abc import Callable
import weakref

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget

from gui_toast import show_toast
from gui_indicator_follow_validation_chart_window import (
    IndicatorFollowValidationChartWindow,
)
from gui_indicator_follow_validation_host import IndicatorFollowValidationHost
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalProvider,
    ValidationHistoricalResult,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
    ValidationReplayResult,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


DEFAULT_VALIDATION_HISTORICAL_COUNT = 300
_OWNER_FLOW_ATTRIBUTE = "_indicator_follow_validation_flow"


class IndicatorFollowValidationFlow(QObject):
    """Connect one Validation host to read-only history, replay, and chart UI."""

    validation_failed = pyqtSignal(str)

    def __init__(
        self,
        broker: object,
        parent: QObject | None = None,
        *,
        host: IndicatorFollowValidationHost | None = None,
        historical_count: int = DEFAULT_VALIDATION_HISTORICAL_COUNT,
        historical_provider_factory: Callable[..., object] = (
            ValidationHistoricalProvider
        ),
        replay_factory: Callable[..., object] = ValidationHistoricalReplay,
        chart_factory: Callable[..., object] = (
            IndicatorFollowValidationChartWindow
        ),
    ) -> None:
        super().__init__(parent)
        if (
            isinstance(historical_count, bool)
            or not isinstance(historical_count, int)
            or historical_count <= 0
        ):
            raise ValueError("historical_count must be a positive integer")
        for name, factory in (
            ("historical_provider_factory", historical_provider_factory),
            ("replay_factory", replay_factory),
            ("chart_factory", chart_factory),
        ):
            if not callable(factory):
                raise TypeError(f"{name} must be callable")

        self._broker = broker
        self._historical_count = historical_count
        self._historical_provider_factory = historical_provider_factory
        self._replay_factory = replay_factory
        self._chart_factory = chart_factory
        self._host = (
            host
            if host is not None
            else IndicatorFollowValidationHost(parent)
        )
        self._bound_dialog_ids: set[int] = set()
        self._active_providers: dict[int, object] = {}
        self._open_charts: dict[int, object] = {}

        self._host.validation_blocked.connect(self._forward_host_failure)
        self._host.validation_session_ready.connect(self._request_history)

    @property
    def host(self) -> IndicatorFollowValidationHost:
        return self._host

    @property
    def historical_count(self) -> int:
        return self._historical_count

    @property
    def open_charts(self) -> tuple[object, ...]:
        return tuple(self._open_charts.values())

    def bind_dialog(self, dialog: object) -> bool:
        signal = getattr(dialog, "validation_chart_requested", None)
        connect = getattr(signal, "connect", None)
        if not callable(connect):
            return False
        dialog_id = id(dialog)
        if dialog_id in self._bound_dialog_ids:
            return True
        try:
            dialog_ref = weakref.ref(dialog)
        except TypeError:
            return False

        def start_request(
            snapshot: object,
            requester_ref: weakref.ReferenceType[object] = dialog_ref,
        ) -> object | None:
            return self._start_validation_request(snapshot, requester_ref())

        connect(start_request)
        self._bound_dialog_ids.add(dialog_id)
        destroyed = getattr(dialog, "destroyed", None)
        destroyed_connect = getattr(destroyed, "connect", None)
        if callable(destroyed_connect):
            destroyed_connect(
                lambda _obj=None, key=dialog_id: self._bound_dialog_ids.discard(key)
            )
        return True

    def _server_authenticated(self) -> bool:
        checker = getattr(self._broker, "is_connected", None)
        if not callable(checker):
            return False
        try:
            return checker() is True
        except Exception:
            return False

    @staticmethod
    def _valid_ui_requester(requester_dialog: object) -> bool:
        if not isinstance(requester_dialog, QWidget):
            return False
        try:
            requester_dialog.window()
        except RuntimeError:
            return False
        return True

    def _start_validation_request(
        self,
        snapshot: object,
        requester_dialog: object,
    ) -> object | None:
        if not self._valid_ui_requester(requester_dialog):
            self.validation_failed.emit("INVALID_VALIDATION_REQUESTER")
            return None
        if not self._server_authenticated():
            try:
                show_toast(
                    requester_dialog,
                    "키움 서버에 로그인되어 있지 않습니다.",
                    duration_ms=2500,
                )
            except RuntimeError:
                pass
            self.validation_failed.emit("SERVER_NOT_CONNECTED")
            return None
        return self._host.start(snapshot, ui_parent=requester_dialog)

    def _forward_host_failure(self, reason: str) -> None:
        self.validation_failed.emit(str(reason or "VALIDATION_BLOCKED"))

    def _request_history(self, session: object) -> None:
        if not isinstance(session, ValidationSession):
            self.validation_failed.emit("INVALID_VALIDATION_SESSION")
            return
        try:
            provider = self._historical_provider_factory(session, self._broker)
        except Exception as exc:
            self.validation_failed.emit(f"HISTORICAL_PROVIDER_ERROR: {exc}")
            return

        key = id(session)
        self._active_providers[key] = provider

        def completed(result: object) -> None:
            self._active_providers.pop(key, None)
            self._handle_historical_result(session, result)

        request_latest = getattr(provider, "request_latest", None)
        if not callable(request_latest):
            self._active_providers.pop(key, None)
            self.validation_failed.emit("HISTORICAL_PROVIDER_UNAVAILABLE")
            return
        try:
            request_latest(self._historical_count, completed)
        except Exception as exc:
            self._active_providers.pop(key, None)
            self.validation_failed.emit(f"HISTORICAL_REQUEST_ERROR: {exc}")

    def _handle_historical_result(
        self,
        session: ValidationSession,
        result: object,
    ) -> None:
        if (
            not isinstance(result, ValidationHistoricalResult)
            or result.ok is not True
            or result.snapshot is None
        ):
            self.validation_failed.emit(
                self._failure_text("HISTORICAL", result)
            )
            return

        try:
            replay = self._replay_factory(session)
            evaluate = getattr(replay, "evaluate", None)
            if not callable(evaluate):
                raise TypeError("replay evaluator is unavailable")
            replay_result = evaluate(result.snapshot)
        except Exception as exc:
            self.validation_failed.emit(f"REPLAY_ERROR: {exc}")
            return
        if (
            not isinstance(replay_result, ValidationReplayResult)
            or replay_result.ok is not True
            or replay_result.snapshot is None
        ):
            self.validation_failed.emit(self._failure_text("REPLAY", replay_result))
            return

        try:
            chart = self._chart_factory(
                replay_result.snapshot,
                parent=None,
            )
            show = getattr(chart, "show", None)
            if not callable(show):
                raise TypeError("chart show is unavailable")
            set_attribute = getattr(chart, "setAttribute", None)
            if callable(set_attribute):
                set_attribute(Qt.WA_DeleteOnClose, True)
            chart_key = id(chart)
            self._open_charts[chart_key] = chart
            destroyed = getattr(chart, "destroyed", None)
            destroyed_connect = getattr(destroyed, "connect", None)
            if callable(destroyed_connect):
                destroyed_connect(
                    lambda _obj=None, key=chart_key: self._open_charts.pop(key, None)
                )
            show()
        except Exception as exc:
            chart_key = id(chart) if "chart" in locals() else None
            if chart_key is not None:
                self._open_charts.pop(chart_key, None)
            self.validation_failed.emit(f"CHART_ERROR: {exc}")

    @staticmethod
    def _failure_text(stage: str, result: object) -> str:
        reason = str(getattr(result, "reason", "") or "UNKNOWN_FAILURE")
        error = str(getattr(result, "error", "") or "").strip()
        return f"{stage}: {reason}" + (f" ({error})" if error else "")


def _present_validation_failure(owner: object, message: str) -> None:
    if message == "SERVER_NOT_CONNECTED":
        return
    text = f"검증차트: {str(message or '실패')}"
    status_message = getattr(owner, "statusBarMessage", None)
    if callable(status_message):
        status_message(text)
        return
    status_bar_getter = getattr(owner, "statusBar", None)
    if not callable(status_bar_getter):
        return
    try:
        status_bar = status_bar_getter()
        show_message = getattr(status_bar, "showMessage", None)
        if callable(show_message):
            show_message(text, 5000)
    except Exception:
        return


def bind_indicator_follow_validation_flow(
    owner: object,
    dialog: object,
) -> IndicatorFollowValidationFlow | None:
    """Bind a compatible settings dialog to one owner-scoped Flow instance."""
    signal = getattr(dialog, "validation_chart_requested", None)
    if not callable(getattr(signal, "connect", None)):
        return None

    flow = getattr(owner, _OWNER_FLOW_ATTRIBUTE, None)
    if not isinstance(flow, IndicatorFollowValidationFlow):
        qobject_parent = owner if isinstance(owner, QObject) else None
        flow = IndicatorFollowValidationFlow(
            getattr(owner, "kiwoom_api", None),
            parent=qobject_parent,
        )
        flow.validation_failed.connect(
            lambda message, target=owner: _present_validation_failure(target, message)
        )
        setattr(owner, _OWNER_FLOW_ATTRIBUTE, flow)
    flow.bind_dialog(dialog)
    return flow
