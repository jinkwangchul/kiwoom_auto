# -*- coding: utf-8 -*-
"""Independent entry and replay flow for indicator signal Validation V2."""

from __future__ import annotations

from collections.abc import Callable
import weakref

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QWidget

from gui_toast import show_toast
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
)
from gui_indicator_follow_validation_host import IndicatorFollowValidationHost
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_validation_average_price_context,
    build_signal_validation_snapshot,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalProvider,
    ValidationHistoricalResult,
)
from routines.지표추종매매.routine_validation_operation_reader import (
    is_operation_active,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
    ValidationReplayResult,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT = 100
_OWNER_FLOW_ATTRIBUTE = "_indicator_follow_signal_validation_flow"


class IndicatorFollowSignalValidationFlow(QObject):
    """Own V2 picker/window lifetime and fresh read-only replay requests."""

    validation_failed = pyqtSignal(str)

    def __init__(
        self,
        broker: object,
        parent: QObject | None = None,
        *,
        host: IndicatorFollowValidationHost | None = None,
        operation_active_reader: Callable[[], bool] | None = is_operation_active,
        historical_count: int = DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT,
        historical_provider_factory: Callable[..., object] = ValidationHistoricalProvider,
        replay_factory: Callable[..., object] = ValidationHistoricalReplay,
        window_factory: Callable[..., object] = IndicatorFollowSignalValidationWindow,
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
            ("window_factory", window_factory),
        ):
            if not callable(factory):
                raise TypeError(f"{name} must be callable")
        self._broker = broker
        self._operation_active_reader = operation_active_reader
        self._historical_count = historical_count
        self._historical_provider_factory = historical_provider_factory
        self._replay_factory = replay_factory
        self._window_factory = window_factory
        self._host = host if host is not None else IndicatorFollowValidationHost(
            parent,
            operation_active_reader=operation_active_reader,
        )
        self._bound_dialog_ids: set[int] = set()
        self._pending_entry: tuple[
            IndicatorFollowSignalValidationSeed,
            weakref.ReferenceType[object],
        ] | None = None
        self._open_windows: dict[int, object] = {}
        self._request_generation: dict[int, int] = {}
        self._active_providers: dict[tuple[int, int], object] = {}
        self._host.validation_blocked.connect(self._forward_host_failure)
        self._host.validation_session_ready.connect(self._open_signal_window)

    @property
    def host(self) -> IndicatorFollowValidationHost:
        return self._host

    @property
    def open_windows(self) -> tuple[object, ...]:
        return tuple(self._open_windows.values())

    def bind_dialog(self, dialog: object) -> bool:
        signal = getattr(dialog, "signal_validation_requested", None)
        connect = getattr(signal, "connect", None)
        if not callable(connect):
            return False
        key = id(dialog)
        if key in self._bound_dialog_ids:
            return True
        try:
            dialog_ref = weakref.ref(dialog)
        except TypeError:
            return False

        def start_request(
            seed: object,
            requester_ref: weakref.ReferenceType[object] = dialog_ref,
        ) -> object | None:
            return self._start_entry(seed, requester_ref())

        connect(start_request)
        self._bound_dialog_ids.add(key)
        destroyed = getattr(dialog, "destroyed", None)
        if callable(getattr(destroyed, "connect", None)):
            destroyed.connect(
                lambda _obj=None, dialog_key=key: self._bound_dialog_ids.discard(dialog_key)
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
    def _valid_requester(requester: object) -> bool:
        if not isinstance(requester, QWidget):
            return False
        try:
            requester.window()
        except RuntimeError:
            return False
        return True

    def _start_entry(self, seed: object, requester: object) -> object | None:
        if not isinstance(seed, IndicatorFollowSignalValidationSeed):
            self.validation_failed.emit("INVALID_SIGNAL_VALIDATION_SEED")
            return None
        if not self._valid_requester(requester):
            self.validation_failed.emit("INVALID_VALIDATION_REQUESTER")
            return None
        if not self._server_authenticated():
            try:
                show_toast(
                    requester,
                    "키움 서버에 로그인되어 있지 않습니다.",
                    duration_ms=2500,
                )
            except RuntimeError:
                pass
            self.validation_failed.emit("SERVER_NOT_CONNECTED")
            return None
        try:
            initial_snapshot = build_signal_validation_snapshot(
                seed.settings_snapshot.to_dict(),
                ui_state=seed.to_ui_state(),
            )
        except Exception as exc:
            self.validation_failed.emit(f"SIGNAL_PROJECTION_ERROR: {exc}")
            return None
        try:
            requester_ref = weakref.ref(requester)
        except TypeError:
            self.validation_failed.emit("INVALID_VALIDATION_REQUESTER")
            return None
        self._pending_entry = (seed, requester_ref)
        try:
            return self._host.start(initial_snapshot, ui_parent=requester)
        finally:
            if self._pending_entry is not None and self._pending_entry[0] is seed:
                self._pending_entry = None

    def _open_signal_window(self, session: object) -> None:
        pending = self._pending_entry
        if not isinstance(session, ValidationSession) or pending is None:
            self.validation_failed.emit("INVALID_SIGNAL_VALIDATION_SESSION")
            return
        seed, source_ref = pending
        try:
            window = self._window_factory(
                session.request.stock,
                seed,
                parent=None,
            )
            set_historical_candle_count = getattr(
                window,
                "set_historical_candle_count",
                None,
            )
            if callable(set_historical_candle_count):
                set_historical_candle_count(self._historical_count)
            if not callable(getattr(window, "show", None)):
                raise TypeError("signal validation window show is unavailable")
            run_signal = getattr(window, "validation_run_requested", None)
            if not callable(getattr(run_signal, "connect", None)):
                raise TypeError("signal validation run signal is unavailable")
            window_ref = weakref.ref(window)
            run_signal.connect(
                lambda snapshot, ref=window_ref: self._run_validation(ref(), snapshot)
            )
            apply_signal = getattr(window, "settings_apply_requested", None)
            if not callable(getattr(apply_signal, "connect", None)):
                raise TypeError("signal validation apply signal is unavailable")
            apply_signal.connect(
                lambda payload, ref=window_ref, source=source_ref: self._apply_to_source(
                    ref(),
                    source,
                    payload,
                )
            )
            if callable(getattr(window, "setAttribute", None)):
                window.setAttribute(Qt.WA_DeleteOnClose, True)
            key = id(window)
            self._open_windows[key] = window
            self._request_generation[key] = 0
            destroyed = getattr(window, "destroyed", None)
            if callable(getattr(destroyed, "connect", None)):
                destroyed.connect(
                    lambda _obj=None, window_key=key: self._release_window(window_key)
                )
            window.show()
            initial_request = getattr(window, "request_initial_validation", None)
            if not callable(initial_request):
                raise TypeError("initial signal validation request is unavailable")
            QTimer.singleShot(
                0,
                lambda ref=window_ref: self._request_initial_validation(ref()),
            )
        except Exception as exc:
            self.validation_failed.emit(f"SIGNAL_WINDOW_ERROR: {exc}")

    @staticmethod
    def _request_initial_validation(window: object) -> None:
        request = getattr(window, "request_initial_validation", None)
        if callable(request):
            request()

    def _apply_to_source(
        self,
        window: object,
        source_ref: weakref.ReferenceType[object],
        payload: object,
    ) -> None:
        show_result = getattr(window, "show_settings_apply_result", None)
        if not isinstance(payload, IndicatorFollowSignalValidationApplyPayload):
            if callable(show_result):
                show_result("설정 반영 데이터가 올바르지 않습니다.", success=False)
            return
        source = source_ref()
        if not self._valid_requester(source):
            if callable(show_result):
                show_result(
                    "원본 설정창이 닫혀 있어 설정을 반영할 수 없습니다.",
                    success=False,
                )
            return
        apply_state = getattr(source, "apply_signal_validation_ui_state", None)
        if not callable(apply_state):
            if callable(show_result):
                show_result("원본 설정창에 신호설정을 반영할 수 없습니다.", success=False)
            return
        try:
            result = apply_state(payload.to_ui_state())
        except Exception:
            if callable(show_result):
                show_result("신호설정 반영 중 오류가 발생했습니다.", success=False)
            return
        skipped = result.get("skipped", []) if isinstance(result, dict) else ["invalid_result"]
        if skipped:
            if callable(show_result):
                show_result("일부 신호설정을 반영할 수 없습니다.", success=False)
            return
        if callable(show_result):
            show_result("설정 반영 완료", success=True)

    def _release_window(self, window_key: int) -> None:
        self._open_windows.pop(window_key, None)
        self._request_generation.pop(window_key, None)
        for request_key in list(self._active_providers):
            if request_key[0] == window_key:
                self._active_providers.pop(request_key, None)

    def _run_validation(self, window: object, run_request: object) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            self.validation_failed.emit("SIGNAL_WINDOW_UNAVAILABLE")
            return
        if not isinstance(run_request, IndicatorFollowSignalValidationRunRequest):
            self._fail_window(window, "INVALID_SIGNAL_VALIDATION_RUN_REQUEST")
            return
        snapshot = run_request.settings_snapshot
        try:
            timeframe = snapshot.to_dict()["bar"]["bar_minutes"]
            request = ValidationRequest(window.stock, snapshot, timeframe)
            session = ValidationSession(
                request,
                operation_active_reader=self._operation_active_reader,
            )
            availability = session.readiness()
            if not availability.allowed:
                self._fail_window(window, str(availability.reason or "VALIDATION_BLOCKED"))
                return
            provider = self._historical_provider_factory(session, self._broker)
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROVIDER_ERROR: {exc}")
            return

        generation = self._request_generation.get(window_key, 0) + 1
        self._request_generation[window_key] = generation
        request_key = (window_key, generation)
        self._active_providers[request_key] = provider
        window_ref = weakref.ref(window)

        def completed(result: object) -> None:
            self._active_providers.pop(request_key, None)
            target = window_ref()
            if (
                target is None
                or self._request_generation.get(window_key) != generation
            ):
                return
            self._handle_historical_result(target, session, result)

        request_latest = getattr(provider, "request_latest", None)
        if not callable(request_latest):
            self._active_providers.pop(request_key, None)
            self._fail_window(window, "HISTORICAL_PROVIDER_UNAVAILABLE")
            return
        try:
            request_latest(run_request.candle_count, completed)
        except Exception as exc:
            self._active_providers.pop(request_key, None)
            self._fail_window(window, f"HISTORICAL_REQUEST_ERROR: {exc}")

    def _handle_historical_result(
        self,
        window: object,
        session: ValidationSession,
        result: object,
    ) -> None:
        if (
            not isinstance(result, ValidationHistoricalResult)
            or result.ok is not True
            or result.snapshot is None
        ):
            self._fail_window(window, self._failure_text("HISTORICAL", result))
            return
        try:
            replay = self._replay_factory(session)
            evaluate_with_context = getattr(replay, "evaluate_with_context", None)
            evaluate = getattr(replay, "evaluate", None)
            if callable(evaluate_with_context):
                replay_result = evaluate_with_context(
                    result.snapshot,
                    context_provider=build_validation_average_price_context,
                )
            elif callable(evaluate):
                replay_result = evaluate(result.snapshot)
            else:
                raise TypeError("replay evaluator is unavailable")
        except Exception as exc:
            self._fail_window(window, f"REPLAY_ERROR: {exc}")
            return
        if (
            not isinstance(replay_result, ValidationReplayResult)
            or replay_result.ok is not True
            or replay_result.snapshot is None
        ):
            self._fail_window(window, self._failure_text("REPLAY", replay_result))
            return
        try:
            window.set_replay_snapshot(replay_result.snapshot)
        except Exception as exc:
            self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")

    def _fail_window(self, window: object, message: str) -> None:
        text = str(message or "VALIDATION_BLOCKED")
        show_error = getattr(window, "show_validation_error", None)
        if callable(show_error):
            show_error(text)
        self.validation_failed.emit(text)

    def _forward_host_failure(self, reason: str) -> None:
        self.validation_failed.emit(str(reason or "VALIDATION_BLOCKED"))

    @staticmethod
    def _failure_text(stage: str, result: object) -> str:
        reason = str(getattr(result, "reason", "") or "UNKNOWN_FAILURE")
        error = str(getattr(result, "error", "") or "").strip()
        return f"{stage}: {reason}" + (f" ({error})" if error else "")


def _present_signal_validation_failure(owner: object, message: str) -> None:
    if message == "SERVER_NOT_CONNECTED":
        return
    text = f"검증차트2: {str(message or '실패')}"
    status_message = getattr(owner, "statusBarMessage", None)
    if callable(status_message):
        status_message(text)
        return
    status_bar_getter = getattr(owner, "statusBar", None)
    if not callable(status_bar_getter):
        return
    try:
        show_message = getattr(status_bar_getter(), "showMessage", None)
        if callable(show_message):
            show_message(text, 5000)
    except Exception:
        return


def bind_indicator_follow_signal_validation_flow(
    owner: object,
    dialog: object,
) -> IndicatorFollowSignalValidationFlow | None:
    signal = getattr(dialog, "signal_validation_requested", None)
    if not callable(getattr(signal, "connect", None)):
        return None
    flow = getattr(owner, _OWNER_FLOW_ATTRIBUTE, None)
    if not isinstance(flow, IndicatorFollowSignalValidationFlow):
        qobject_parent = owner if isinstance(owner, QObject) else None
        flow = IndicatorFollowSignalValidationFlow(
            getattr(owner, "kiwoom_api", None),
            parent=qobject_parent,
        )
        flow.validation_failed.connect(
            lambda message, target=owner: _present_signal_validation_failure(
                target,
                message,
            )
        )
        setattr(owner, _OWNER_FLOW_ATTRIBUTE, flow)
    flow.bind_dialog(dialog)
    return flow
