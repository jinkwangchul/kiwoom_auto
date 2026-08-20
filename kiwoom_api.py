# -*- coding: utf-8 -*-
"""Central Kiwoom OpenAPI wrapper for broker, TR, and shadow realtime I/O."""

from __future__ import annotations

import ctypes
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import os
import threading
from time import monotonic
from typing import Any, Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

try:
    from PyQt5.QAxContainer import QAxWidget
except Exception as exc:  # pragma: no cover - depends on Windows/COM runtime.
    QAxWidget = None
    _QAX_IMPORT_ERROR: Exception | None = exc
else:
    _QAX_IMPORT_ERROR = None

from candle_manager import DEFAULT_CANDLES_MAX_COUNT
from account_funds_foundation import ACCOUNT_AUTHENTICATION_REQUIRED
from kiwoom_candle_adapter import commit_minute_candles_for_stock
from kiwoom_trade_cost_diagnostic import record_trade_cost_chejan_diagnostic
from production_recovery_contract import RecoverySessionIdentity, build_snapshot_part
from event_journal_production import observe_production_exception
from kiwoom_screen_allocator import (
    ACCOUNT_TR,
    MARKET_TR,
    REALTIME,
    KiwoomScreenAllocator,
    ScreenAllocationError,
)
from kiwoom_realtime_fids import (
    REALTIME_CUMULATIVE_VOLUME_FID,
    REALTIME_CURRENT_PRICE_FID,
    REALTIME_EXECUTION_TIME_FID,
    REALTIME_EXECUTION_TYPE,
    REALTIME_SHADOW_FIDS,
)
from kiwoom_realtime_shadow import (
    RealtimeShadowBarBuilder,
    normalize_realtime_shadow_tick,
)


Opt10080Callback = Callable[[dict[str, Any]], None]
RecoverySnapshotCallback = Callable[[dict[str, Any]], None]
AccountFundsCallback = Callable[[dict[str, Any]], None]


def _empty_candle_commit_projection() -> dict[str, Any]:
    return {
        "commit_verified": False,
        "changed": False,
        "canonical_content_hash": "",
        "commit_identity": "",
        "bar_key": "",
        "bar_identity": "",
        "bar_time": "",
    }


@dataclass(frozen=True)
class BrokerSessionSnapshot:
    api_available: bool
    connected: bool
    login_requested: bool
    login_session_id: str
    connection_epoch: int
    last_login_error: int | None
    message: str
    reason: str


@dataclass(frozen=True)
class BrokerReadinessSnapshot:
    api_available: bool
    connection_ready: bool
    login_session_ready: bool
    broker_request_ready: bool
    blockers: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class RealtimeShadowScreenBatch:
    screen_no: str
    owner: str
    stock_codes: tuple[str, ...]
    raw_registration_result: Any = None


@dataclass(frozen=True)
class RealtimeShadowRegistrationSnapshot:
    active: bool
    connection_epoch: int
    login_session_id: str
    target_stock_codes: tuple[str, ...]
    fid_list: tuple[int, ...]
    screen_batches: tuple[RealtimeShadowScreenBatch, ...]
    last_error: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def account_authentication_required_message(message: object) -> bool:
    """Recognize the installed OpenAPI password prompt from its semantic text."""

    clean_message = " ".join(str(message or "").split())
    return clean_message in {"(5)", "(55)"} or (
        "비밀번호" in clean_message
        and any(token in clean_message for token in ("입력", "확인", "등록"))
    )


def _windows_process_names_by_pid() -> dict[int, str]:
    """Return a read-only Windows process snapshot without spawning a shell."""

    if os.name != "nt":
        return {}
    try:
        from ctypes import wintypes

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = (
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        create_snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
        process_next.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        snapshot = create_snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return {}
        result: dict[int, str] = {}
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not process_first(snapshot, ctypes.byref(entry)):
                return {}
            while True:
                result[int(entry.th32ProcessID)] = str(entry.szExeFile or "").lower()
                if not process_next(snapshot, ctypes.byref(entry)):
                    break
        finally:
            close_handle(snapshot)
        return result
    except Exception:
        return {}


def _visible_top_level_window_handles_for_pid(pid: int) -> frozenset[int]:
    """Return visible top-level HWNDs owned by one process."""

    if os.name != "nt":
        return frozenset()
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        handles: set[int] = set()

        @callback_type
        def collect(hwnd: int, _lparam: int) -> bool:
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if int(owner_pid.value) == int(pid) and user32.IsWindowVisible(hwnd):
                handles.add(int(hwnd))
            return True

        user32.EnumWindows(collect, 0)
        return frozenset(handles)
    except Exception:
        return frozenset()


def _visible_open_api_login_window_handles(
    processes: dict[int, str],
) -> frozenset[int]:
    """Return the installed OpenAPI login dialogs owned by opstarter."""

    if os.name != "nt":
        return frozenset()
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        handles: set[int] = set()

        @callback_type
        def collect(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            owner_name = str(processes.get(int(owner_pid.value), "") or "").lower()
            if owner_name != "opstarter.exe":
                return True
            title_length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title, title_length + 1)
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if title.value == "Open API Login" and class_name.value == "#32770":
                handles.add(int(hwnd))
            return True

        user32.EnumWindows(collect, 0)
        return frozenset(handles)
    except Exception:
        return frozenset()


def _login_handoff_process_pids(processes: dict[int, str]) -> frozenset[int]:
    return frozenset(
        pid
        for pid, name in processes.items()
        if str(name or "").lower() in {"nkstarter.exe", "opstarter.exe"}
    )


def _windows_input_desktop_name() -> str:
    """Return the current interactive desktop name without switching desktops."""

    if os.name != "nt":
        return ""
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        open_input_desktop = user32.OpenInputDesktop
        open_input_desktop.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        open_input_desktop.restype = wintypes.HANDLE
        get_user_object_information = user32.GetUserObjectInformationW
        close_desktop = user32.CloseDesktop
        desktop = open_input_desktop(0, False, 0x0001)
        if not desktop:
            return f"UNAVAILABLE:{ctypes.get_last_error()}"
        try:
            required = wintypes.DWORD()
            get_user_object_information(
                desktop,
                2,
                None,
                0,
                ctypes.byref(required),
            )
            buffer = ctypes.create_unicode_buffer(max(1, required.value // 2))
            if not get_user_object_information(
                desktop,
                2,
                buffer,
                required.value,
                ctypes.byref(required),
            ):
                return f"UNAVAILABLE:{ctypes.get_last_error()}"
            return str(buffer.value or "")
        finally:
            close_desktop(desktop)
    except Exception:
        return "UNAVAILABLE"

TRADE_COST_DIAGNOSTIC_FIDS = (
    "9201",
    "9203",
    "904",
    "9001",
    "302",
    "907",
    "900",
    "901",
    "910",
    "911",
    "902",
    "903",
    "913",
    "908",
    "938",
    "939",
)


class KiwoomApi(QObject):
    """Minimal Kiwoom API wrapper for opt10080 candle lookup."""

    login_state_changed = pyqtSignal(dict)
    raw_chejan_received = pyqtSignal(dict)
    account_authentication_required = pyqtSignal(dict)
    bar_committed = pyqtSignal(object)
    realtime_shadow_tick_received = pyqtSignal(object)
    realtime_shadow_bar_completed = pyqtSignal(object)

    CONTROL_NAME = "KHOPENAPI.KHOpenAPICtrl.1"
    OPT10080_FIELDS = ("체결시간", "시가", "고가", "저가", "현재가", "거래량")
    OPW00018_FIELDS = (
        "종목번호",
        "종목명",
        "평가손익",
        "수익률(%)",
        "매입가",
        "보유수량",
        "매매가능수량",
        "현재가",
        "평가금액",
    )
    OPT10075_FIELDS = (
        "계좌번호",
        "주문번호",
        "종목코드",
        "주문상태",
        "종목명",
        "주문수량",
        "주문가격",
        "미체결수량",
        "원주문번호",
        "주문구분",
        "매매구분",
        "시간",
        "체결량",
    )
    HOLDINGS_SCREEN_NO = None
    OPEN_ORDERS_SCREEN_NO = None
    ACCOUNT_FUNDS_SCREEN_NO = None
    RECOVERY_TR_TIMEOUT_MS = 15_000
    ACCOUNT_FUNDS_TR_TIMEOUT_MS = 15_000
    MINUTE_CANDLE_TR_TIMEOUT_MS = 10_000
    BROKER_CONNECTION_OBSERVATION_INTERVAL_MS = 5_000
    # Project-conservative pacing, aligned with auto_candle_refresh.REQUEST_SPACING_MS.
    TR_GOVERNOR_MIN_INTERVAL_MS = 1_000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._control: Any = None
        self._available = False
        self._unavailable_reason = ""
        self._connected = False
        self._login_requested = False
        self._login_session_id = ""
        self._connection_epoch = 0
        self._login_bootstrap_timer = QTimer(self)
        self._login_bootstrap_timer.setInterval(200)
        self._login_bootstrap_timer.timeout.connect(
            self._observe_login_bootstrap
        )
        self._connection_observation_timer = QTimer(self)
        self._connection_observation_timer.setInterval(
            self.BROKER_CONNECTION_OBSERVATION_INTERVAL_MS
        )
        self._connection_observation_timer.timeout.connect(
            self._observe_broker_connection
        )
        self._login_bootstrap_baseline_consent_pids: frozenset[int] = frozenset()
        self._login_bootstrap_baseline_starter_pids: frozenset[int] = frozenset()
        self._login_bootstrap_baseline_window_handles: frozenset[int] = frozenset()
        self._login_bootstrap_baseline_login_window_handles: frozenset[int] = (
            frozenset()
        )
        self._login_bootstrap_consent_observed = False
        self._login_bootstrap_observed_consent_pids: set[int] = set()
        self._login_bootstrap_starter_observed = False
        self._login_bootstrap_secure_desktop_observed = False
        self._login_bootstrap_desktop_probe_stop = threading.Event()
        self._login_bootstrap_desktop_probe_thread: threading.Thread | None = None
        self._login_bootstrap_closed_observations = 0
        self.last_login_error: int | None = None
        self.last_login_message = "login not requested"
        self._pending_tr: dict[str, dict[str, Any]] = {}
        self._account_funds_request_accounts: dict[str, str] = {}
        self._tr_request_queue: deque[dict[str, Any]] = deque()
        self._tr_last_dispatch_monotonic_ms: int | None = None
        self._tr_governor_timer_scheduled = False
        self._tr_governor_dispatching = False
        self._screen_allocator = KiwoomScreenAllocator()
        self._realtime_shadow_builder = RealtimeShadowBarBuilder()
        self._realtime_shadow_registration = self._empty_realtime_shadow_snapshot()

        if QAxWidget is None:
            self._unavailable_reason = f"QAxContainer import failed: {_QAX_IMPORT_ERROR}"
            return
        if QApplication.instance() is None:
            self._unavailable_reason = "QApplication is required before creating QAxWidget"
            return

        try:
            control = QAxWidget(parent)
            if not control.setControl(self.CONTROL_NAME):
                self._unavailable_reason = f"control unavailable: {self.CONTROL_NAME}"
                self._control = control
                return
            control.OnEventConnect.connect(self._on_event_connect)
            control.OnReceiveTrData.connect(self._on_receive_tr_data)
            receive_realtime = getattr(control, "OnReceiveRealData", None)
            if receive_realtime is not None:
                receive_realtime.connect(self._on_receive_real_data)
            receive_message = getattr(control, "OnReceiveMsg", None)
            if receive_message is not None:
                receive_message.connect(self._on_receive_msg)
            chejan_signal = getattr(control, "OnReceiveChejanData", None)
            if chejan_signal is not None:
                chejan_signal.connect(self._on_receive_chejan_data)
            self._control = control
            self._available = True
            self._connection_observation_timer.start()
        except Exception as exc:  # pragma: no cover - depends on Kiwoom OCX.
            self._control = None
            self._available = False
            self._unavailable_reason = str(exc)

    def is_available(self) -> bool:
        return bool(self._available and self._control is not None)

    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def broker_session_snapshot(self) -> BrokerSessionSnapshot:
        return BrokerSessionSnapshot(
            api_available=self.is_available(),
            connected=bool(self._connected),
            login_requested=bool(self._login_requested),
            login_session_id=str(self._login_session_id or ""),
            connection_epoch=int(self._connection_epoch),
            last_login_error=self.last_login_error,
            message=str(self.last_login_message or ""),
            reason=(
                ""
                if self.is_available()
                else self._unavailable_reason or "kiwoom api unavailable"
            ),
        )

    def broker_readiness_snapshot(self) -> BrokerReadinessSnapshot:
        session = self.broker_session_snapshot()
        blockers: list[str] = []
        if not session.api_available:
            blockers.append("API_UNAVAILABLE")
        if not session.connected:
            blockers.append("DISCONNECTED")
        if not session.login_session_id:
            blockers.append("LOGIN_SESSION_MISSING")
        ready = not blockers
        return BrokerReadinessSnapshot(
            api_available=session.api_available,
            connection_ready=session.connected,
            login_session_ready=bool(session.login_session_id),
            broker_request_ready=ready,
            blockers=tuple(blockers),
            reason="READY" if ready else ",".join(blockers),
        )

    @staticmethod
    def _empty_realtime_shadow_snapshot(
        *,
        connection_epoch: int = 0,
        login_session_id: str = "",
        last_error: str = "",
    ) -> RealtimeShadowRegistrationSnapshot:
        return RealtimeShadowRegistrationSnapshot(
            active=False,
            connection_epoch=int(connection_epoch or 0),
            login_session_id=str(login_session_id or ""),
            target_stock_codes=(),
            fid_list=tuple(REALTIME_SHADOW_FIDS),
            screen_batches=(),
            last_error=str(last_error or ""),
        )

    def _ensure_realtime_shadow_state(self) -> None:
        if not hasattr(self, "_screen_allocator"):
            self._screen_allocator = KiwoomScreenAllocator()
        if not hasattr(self, "_realtime_shadow_builder"):
            self._realtime_shadow_builder = RealtimeShadowBarBuilder()
        if not isinstance(
            getattr(self, "_realtime_shadow_registration", None),
            RealtimeShadowRegistrationSnapshot,
        ):
            self._realtime_shadow_registration = self._empty_realtime_shadow_snapshot()

    def realtime_shadow_registration_snapshot(
        self,
    ) -> RealtimeShadowRegistrationSnapshot:
        self._ensure_realtime_shadow_state()
        return self._realtime_shadow_registration

    def sync_realtime_shadow_registration(
        self,
        stock_codes: object,
    ) -> dict[str, Any]:
        """Replace shadow-only registrations for the current broker session."""

        self._ensure_realtime_shadow_state()
        session = self.broker_session_snapshot()
        if not (
            session.api_available
            and session.connected
            and session.login_session_id
        ):
            self.clear_realtime_shadow_registration(
                remove_from_broker=False,
                reason="BROKER_SESSION_NOT_READY",
            )
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "BROKER_SESSION_NOT_READY",
                "snapshot": self._realtime_shadow_registration,
            }

        candidates = stock_codes if isinstance(stock_codes, (list, tuple, set, frozenset)) else ()
        targets = tuple(sorted({str(code or "").strip() for code in candidates if str(code or "").strip()}))
        current = self._realtime_shadow_registration
        if (
            current.active
            and current.connection_epoch == session.connection_epoch
            and current.login_session_id == session.login_session_id
            and current.target_stock_codes == targets
            and current.fid_list == tuple(REALTIME_SHADOW_FIDS)
        ):
            return {
                "ok": True,
                "changed": False,
                "active": True,
                "reason_code": "REALTIME_SHADOW_UNCHANGED",
                "snapshot": current,
            }

        had_registration = bool(current.screen_batches)
        if had_registration:
            clear_result = self.clear_realtime_shadow_registration(
                remove_from_broker=True,
                reason="REALTIME_SHADOW_TARGET_REPLACED",
            )
            if clear_result.get("ok") is not True:
                return {
                    "ok": False,
                    "changed": True,
                    "active": False,
                    "reason_code": "REALTIME_SHADOW_REPLACEMENT_CLEAR_FAILED",
                    "errors": tuple(clear_result.get("errors", ())),
                    "snapshot": self._realtime_shadow_registration,
                }
        if not targets:
            return {
                "ok": True,
                "changed": had_registration,
                "active": False,
                "reason_code": "REALTIME_SHADOW_EMPTY_TARGET",
                "snapshot": self._realtime_shadow_registration,
            }

        fid_text = ";".join(str(fid) for fid in REALTIME_SHADOW_FIDS)
        registered: list[RealtimeShadowScreenBatch] = []
        try:
            for offset in range(0, len(targets), 100):
                codes = targets[offset:offset + 100]
                owner = f"realtime_shadow:{session.connection_epoch}:{offset // 100}"
                lease = self._screen_allocator.claim(REALTIME, owner)
                batch = RealtimeShadowScreenBatch(
                    screen_no=lease.screen_no,
                    owner=owner,
                    stock_codes=codes,
                )
                registered.append(batch)
                raw_result = self._control.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    lease.screen_no,
                    ";".join(codes),
                    fid_text,
                    "0",
                )
                registered[-1] = RealtimeShadowScreenBatch(
                    screen_no=lease.screen_no,
                    owner=owner,
                    stock_codes=codes,
                    raw_registration_result=raw_result,
                )
        except Exception as exc:
            self._clear_realtime_shadow_batches(registered, remove_from_broker=True)
            self._realtime_shadow_builder.reset()
            self._realtime_shadow_registration = self._empty_realtime_shadow_snapshot(
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                last_error=str(exc),
            )
            return {
                "ok": False,
                "changed": True,
                "active": False,
                "reason_code": "REALTIME_SHADOW_REGISTRATION_FAILED",
                "error": str(exc),
                "snapshot": self._realtime_shadow_registration,
            }

        self._realtime_shadow_registration = RealtimeShadowRegistrationSnapshot(
            active=True,
            connection_epoch=session.connection_epoch,
            login_session_id=session.login_session_id,
            target_stock_codes=targets,
            fid_list=tuple(REALTIME_SHADOW_FIDS),
            screen_batches=tuple(registered),
            last_error="",
        )
        return {
            "ok": True,
            "changed": True,
            "active": True,
            "reason_code": "REGISTER_CALL_RETURNED",
            "snapshot": self._realtime_shadow_registration,
        }

    def clear_realtime_shadow_registration(
        self,
        *,
        remove_from_broker: bool | None = None,
        reason: str = "REALTIME_SHADOW_CLEARED",
    ) -> dict[str, Any]:
        self._ensure_realtime_shadow_state()
        current = self._realtime_shadow_registration
        batches = list(current.screen_batches)
        if remove_from_broker is None:
            remove_from_broker = bool(self._connected and self._control is not None)
        errors = self._clear_realtime_shadow_batches(
            batches,
            remove_from_broker=bool(remove_from_broker),
        )
        self._realtime_shadow_builder.reset()
        self._realtime_shadow_registration = self._empty_realtime_shadow_snapshot(
            connection_epoch=int(getattr(self, "_connection_epoch", 0) or 0),
            login_session_id=str(getattr(self, "_login_session_id", "") or ""),
            last_error="; ".join(errors),
        )
        return {
            "ok": not errors,
            "changed": bool(batches),
            "active": False,
            "reason_code": reason if not errors else "REALTIME_SHADOW_CLEAR_FAILED",
            "errors": tuple(errors),
            "snapshot": self._realtime_shadow_registration,
        }

    def _clear_realtime_shadow_batches(
        self,
        batches: list[RealtimeShadowScreenBatch],
        *,
        remove_from_broker: bool,
    ) -> list[str]:
        errors: list[str] = []
        for batch in batches:
            if remove_from_broker and self._control is not None:
                try:
                    self._control.dynamicCall(
                        "SetRealRemove(QString, QString)",
                        batch.screen_no,
                        "ALL",
                    )
                except Exception as exc:
                    errors.append(str(exc))
            self._screen_allocator.release(batch.owner, batch.screen_no)
        return errors

    def _observe_connected_state(self, connected: bool, *, reason: str) -> bool:
        connected = bool(connected)
        if connected:
            self._connected = True
            return False
        if not self._connected:
            self._connected = False
            return False
        self._invalidate_login_session(reason=reason, emit=True, increment_epoch=True)
        return True

    def _establish_login_session(self, *, account_payload: str) -> str:
        connected_at = datetime.now().isoformat(timespec="microseconds")
        next_epoch = self._connection_epoch + 1
        digest = hashlib.sha256(
            f"{next_epoch}|{connected_at}|{account_payload}".encode("utf-8")
        ).hexdigest().upper()
        self._connection_epoch = next_epoch
        self._connected = True
        self._login_session_id = f"KIWOOM_LOGIN_SESSION_{digest}"
        return self._login_session_id

    def _invalidate_login_session(
        self,
        *,
        reason: str,
        emit: bool,
        increment_epoch: bool,
        err_code: int | None = None,
        status: str = "disconnected",
        message: str = "kiwoom api disconnected",
    ) -> None:
        had_session = bool(self._connected or self._login_session_id)
        reset_shadow = getattr(self, "clear_realtime_shadow_registration", None)
        if callable(reset_shadow):
            reset_shadow(
                remove_from_broker=False,
                reason="REALTIME_SHADOW_SESSION_INVALIDATED",
            )
        self._connected = False
        self._login_session_id = ""
        if increment_epoch and had_session:
            self._connection_epoch += 1
        self.last_login_message = message
        if emit:
            self.login_state_changed.emit(
                {
                    "connected": False,
                    "err_code": err_code,
                    "status": status,
                    "message": self.last_login_message,
                    "connection_epoch": self._connection_epoch,
                    "login_session_id": "",
                    "reason": reason,
                }
            )

    def _observe_broker_connection(self) -> None:
        self.is_connected()

    def _capture_broker_request_identity(self) -> dict[str, Any] | None:
        readiness = self.broker_readiness_snapshot()
        if readiness.broker_request_ready is not True:
            return None
        session = self.broker_session_snapshot()
        if not session.connected or not session.login_session_id:
            return None
        return {
            "request_connection_epoch": session.connection_epoch,
            "request_login_session_id": session.login_session_id,
            "started_at": datetime.now().isoformat(timespec="microseconds"),
        }

    def _broker_request_not_ready_error(self) -> dict[str, Any]:
        readiness = self.broker_readiness_snapshot()
        return {
            "ok": False,
            "error": "broker request is not ready",
            "error_kind": "BROKER_REQUEST_NOT_READY",
            "blockers": list(readiness.blockers),
        }

    def _pending_tr_matches_current_session(self, pending: dict[str, Any]) -> bool:
        session = self.broker_session_snapshot()
        return bool(
            session.connected
            and session.login_session_id
            and pending.get("request_connection_epoch") == session.connection_epoch
            and pending.get("request_login_session_id") == session.login_session_id
        )

    def _stale_broker_session_error(
        self,
        rqname: str,
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        session = self.broker_session_snapshot()
        return {
            "ok": False,
            "rqname": str(rqname),
            "error": "stale broker session",
            "error_kind": "STALE_BROKER_SESSION",
            "request_connection_epoch": pending.get("request_connection_epoch"),
            "current_connection_epoch": session.connection_epoch,
            "request_login_session_id": pending.get("request_login_session_id", ""),
            "current_login_session_id": session.login_session_id,
        }

    def _ensure_screen_allocator(self) -> None:
        if not hasattr(self, "_screen_allocator"):
            self._screen_allocator = KiwoomScreenAllocator()

    def _claim_tr_screen(
        self,
        *,
        purpose: str,
        rqname: str,
        screen_no: str | None,
        callback: Callable[[dict[str, Any]], None] | None,
        failure_payload: dict[str, Any],
    ) -> tuple[str | None, dict[str, Any] | None]:
        self._ensure_screen_allocator()
        try:
            lease = self._screen_allocator.claim(
                purpose,
                str(rqname),
                requested_screen_no=str(screen_no or "").strip() or None,
            )
        except ScreenAllocationError as exc:
            payload = dict(failure_payload)
            payload.update(
                {
                    "ok": False,
                    "rqname": str(rqname),
                    "error": str(exc),
                    "error_kind": exc.error_kind,
                }
            )
            self._finish_callback(callback, payload)
            return None, payload
        return lease.screen_no, None

    def _release_pending_tr_screen(
        self,
        rqname: str,
        pending: dict[str, Any] | None,
    ) -> None:
        if not isinstance(pending, dict):
            return
        screen_no = str(pending.get("screen_no") or "").strip()
        if not screen_no:
            return
        self._ensure_screen_allocator()
        self._screen_allocator.release(str(rqname), screen_no)

    def _finish_stale_pending_tr(
        self,
        rqname: str,
        pending: dict[str, Any],
    ) -> None:
        request_name = str(rqname)
        current = self._pending_tr.pop(request_name, None)
        if current is not pending:
            return
        self._release_pending_tr_screen(request_name, pending)
        pending_type = pending.get("type")
        if pending_type == "account_funds":
            self._account_funds_request_accounts.pop(request_name, None)
            callback = pending.get("callback")
            payload = self._stale_broker_session_error(request_name, pending)
            payload.update(
                {
                    "account_id": pending.get("account_id", ""),
                    "request_id": pending.get("request_id"),
                }
            )
            self._finish_callback(callback if callable(callback) else None, payload)
            return
        if pending_type == "recovery_snapshot":
            stale = self._stale_broker_session_error(request_name, pending)
            pending["rows"] = []
            self._finish_recovery_snapshot(
                request_name,
                pending,
                collection_complete=False,
                errors=(str(stale["error_kind"]),),
            )
            return
        if pending_type == "minute_candles":
            callback = pending.get("callback")
            payload = self._stale_broker_session_error(request_name, pending)
            payload.update(
                {
                    "type": "minute_candles",
                    "code": pending.get("code", ""),
                    "name": pending.get("name", ""),
                    **_empty_candle_commit_projection(),
                }
            )
            self._finish_callback(callback if callable(callback) else None, payload)

    def _ensure_tr_governor_state(self) -> None:
        if not hasattr(self, "_tr_request_queue"):
            self._tr_request_queue = deque()
        if not hasattr(self, "_tr_last_dispatch_monotonic_ms"):
            self._tr_last_dispatch_monotonic_ms = None
        if not hasattr(self, "_tr_governor_timer_scheduled"):
            self._tr_governor_timer_scheduled = False
        if not hasattr(self, "_tr_governor_dispatching"):
            self._tr_governor_dispatching = False

    def _tr_governor_now_ms(self) -> int:
        return int(monotonic() * 1000)

    def _schedule_tr_governor_dispatch(self, delay_ms: int) -> None:
        self._ensure_tr_governor_state()
        if self._tr_governor_timer_scheduled:
            return
        self._tr_governor_timer_scheduled = True
        QTimer.singleShot(
            max(int(delay_ms or 0), 0),
            self._on_tr_governor_timer,
        )

    def _on_tr_governor_timer(self) -> None:
        self._ensure_tr_governor_state()
        self._tr_governor_timer_scheduled = False
        self._drain_tr_governor()

    def _submit_governed_tr_request(
        self,
        *,
        rqname: str,
        trcode: str,
        prev_next: int,
        screen_no: str,
        inputs: tuple[tuple[str, str], ...],
        pending: dict[str, Any],
        on_dispatched: Callable[[Any], None] | None = None,
        on_failed: Callable[[Any, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self._ensure_tr_governor_state()
        request = {
            "rqname": str(rqname),
            "trcode": str(trcode),
            "prev_next": int(prev_next),
            "screen_no": str(screen_no),
            "inputs": tuple((str(field), str(value)) for field, value in inputs),
            "pending": pending,
            "on_dispatched": on_dispatched,
            "on_failed": on_failed,
        }
        self._tr_request_queue.append(request)
        return self._drain_tr_governor()

    def _drain_tr_governor(self) -> dict[str, Any]:
        self._ensure_tr_governor_state()
        if self._tr_governor_dispatching:
            return {"ok": True, "status": "QUEUED"}
        if not self._tr_request_queue:
            return {"ok": True, "status": "IDLE"}

        now_ms = self._tr_governor_now_ms()
        last_ms = self._tr_last_dispatch_monotonic_ms
        if last_ms is not None:
            elapsed_ms = now_ms - int(last_ms)
            if elapsed_ms < self.TR_GOVERNOR_MIN_INTERVAL_MS:
                self._schedule_tr_governor_dispatch(
                    self.TR_GOVERNOR_MIN_INTERVAL_MS - elapsed_ms
                )
                return {"ok": True, "status": "QUEUED"}

        request = self._tr_request_queue.popleft()
        self._tr_governor_dispatching = True
        try:
            result = self._dispatch_governed_tr_request(request)
        finally:
            self._tr_governor_dispatching = False
        if self._tr_request_queue:
            self._schedule_tr_governor_dispatch(self.TR_GOVERNOR_MIN_INTERVAL_MS)
        return result

    def _dispatch_governed_tr_request(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        rqname = str(request.get("rqname") or "")
        pending = request.get("pending")
        if not isinstance(pending, dict) or self._pending_tr.get(rqname) is not pending:
            return {"ok": False, "status": "DROPPED", "rqname": rqname}
        if not self._pending_tr_matches_current_session(pending):
            self._finish_stale_pending_tr(rqname, pending)
            return {
                "ok": False,
                "status": "STALE_BROKER_SESSION",
                "rqname": rqname,
                "error_kind": "STALE_BROKER_SESSION",
            }

        try:
            for field, value in request.get("inputs", ()):
                self._control.dynamicCall(
                    "SetInputValue(QString, QString)",
                    str(field),
                    str(value),
                )
            result = self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname,
                str(request.get("trcode") or ""),
                int(request.get("prev_next") or 0),
                str(request.get("screen_no") or ""),
            )
            self._tr_last_dispatch_monotonic_ms = self._tr_governor_now_ms()
        except Exception as exc:
            result = -1
            error = str(exc)
        else:
            error = "CommRqData failed"

        if int(result or 0) == 0:
            on_dispatched = request.get("on_dispatched")
            if callable(on_dispatched):
                on_dispatched(result)
            return {
                "ok": True,
                "status": "REQUESTED",
                "rqname": rqname,
                "result": result,
            }

        on_failed = request.get("on_failed")
        if callable(on_failed):
            return on_failed(result, error)
        return {
            "ok": False,
            "status": "REQUEST_FAILED",
            "rqname": rqname,
            "result": result,
            "error": error,
        }

    def show_account_password_window(self) -> dict[str, Any]:
        """Open the installed OpenAPI account-password dialog without reading it."""

        if not self.is_available():
            return {
                "ok": False,
                "status": "UNAVAILABLE",
                "error": self._unavailable_reason or "kiwoom api unavailable",
            }
        if not self.is_connected():
            return {
                "ok": False,
                "status": "DISCONNECTED",
                "error": "kiwoom api is not connected",
            }
        try:
            result = self._control.dynamicCall(
                "KOA_Functions(QString, QString)",
                "ShowAccountWindow",
                "",
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "REQUEST_FAILED",
                "error": str(exc),
            }
        return {
            "ok": True,
            "status": "ACCOUNT_PASSWORD_WINDOW_CLOSED",
            "result": "" if result is None else str(result),
        }

    def login(self) -> dict[str, Any]:
        if not self.is_available():
            self._login_requested = False
            self.last_login_error = None
            self.last_login_message = self._unavailable_reason or "kiwoom api unavailable"
            return {
                "ok": False,
                "status": "unavailable",
                "connected": False,
                "error": self.last_login_message,
            }
        try:
            self._prepare_login_bootstrap_observation()
            self._login_requested = True
            self._login_session_id = ""
            self.last_login_error = None
            self.last_login_message = "login requested"
            self._start_login_bootstrap_desktop_probe()
            try:
                result = self._control.dynamicCall("CommConnect()")
            finally:
                self._stop_login_bootstrap_desktop_probe()
            if int(result or 0) != 0:
                self._login_requested = False
                self._stop_login_bootstrap_observation()
                self.last_login_error = int(result or -1)
                self.last_login_message = "login request failed"
                return {
                    "ok": False,
                    "status": "login_request_failed",
                    "connected": False,
                    "result": result,
                    "error": self.last_login_message,
                }
            if self._login_requested:
                self._login_bootstrap_timer.start()
            return {
                "ok": True,
                "status": "login_requested",
                "connected": self.is_connected(),
                "result": result,
                "message": self.last_login_message,
            }
        except Exception as exc:
            self._login_requested = False
            self._stop_login_bootstrap_observation()
            self.last_login_error = None
            self.last_login_message = str(exc)
            return {
                "ok": False,
                "status": "login_request_failed",
                "connected": False,
                "error": self.last_login_message,
            }

    def _prepare_login_bootstrap_observation(self) -> None:
        processes = _windows_process_names_by_pid()
        self._login_bootstrap_baseline_consent_pids = frozenset(
            pid for pid, name in processes.items() if name == "consent.exe"
        )
        self._login_bootstrap_baseline_starter_pids = (
            _login_handoff_process_pids(processes)
        )
        self._login_bootstrap_baseline_window_handles = (
            _visible_top_level_window_handles_for_pid(os.getpid())
        )
        self._login_bootstrap_baseline_login_window_handles = (
            _visible_open_api_login_window_handles(processes)
        )
        self._login_bootstrap_consent_observed = False
        self._login_bootstrap_observed_consent_pids = set()
        self._login_bootstrap_starter_observed = False
        self._login_bootstrap_secure_desktop_observed = False
        self._login_bootstrap_closed_observations = 0

    def _start_login_bootstrap_desktop_probe(self) -> None:
        """Observe only the UAC desktop while CommConnect blocks the Qt thread."""

        self._login_bootstrap_desktop_probe_stop.set()
        previous = self._login_bootstrap_desktop_probe_thread
        if previous is not None and previous.is_alive():
            previous.join(timeout=0.2)
        self._login_bootstrap_desktop_probe_stop = threading.Event()

        def observe() -> None:
            try:
                while not self._login_bootstrap_desktop_probe_stop.is_set():
                    input_desktop = _windows_input_desktop_name()
                    if input_desktop and input_desktop != "Default":
                        self._login_bootstrap_secure_desktop_observed = True
                        return
                    self._login_bootstrap_desktop_probe_stop.wait(0.05)
            except Exception:  # pragma: no cover - Windows-only probe.
                return

        thread = threading.Thread(
            target=observe,
            name="KiwoomLoginDesktopProbe",
            daemon=True,
        )
        self._login_bootstrap_desktop_probe_thread = thread
        thread.start()

    def _stop_login_bootstrap_desktop_probe(self) -> None:
        self._login_bootstrap_desktop_probe_stop.set()
        thread = self._login_bootstrap_desktop_probe_thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=0.2)
        self._login_bootstrap_desktop_probe_thread = None

    def _stop_login_bootstrap_observation(self) -> None:
        self._stop_login_bootstrap_desktop_probe()
        self._login_bootstrap_timer.stop()
        self._login_bootstrap_baseline_consent_pids = frozenset()
        self._login_bootstrap_baseline_starter_pids = frozenset()
        self._login_bootstrap_baseline_window_handles = frozenset()
        self._login_bootstrap_baseline_login_window_handles = frozenset()
        self._login_bootstrap_consent_observed = False
        self._login_bootstrap_observed_consent_pids = set()
        self._login_bootstrap_starter_observed = False
        self._login_bootstrap_secure_desktop_observed = False
        self._login_bootstrap_closed_observations = 0

    def _observe_login_bootstrap(self) -> None:
        if not self._login_requested:
            self._stop_login_bootstrap_observation()
            return
        connect_state = self.is_connected()
        if connect_state:
            self._stop_login_bootstrap_observation()
            return

        processes = _windows_process_names_by_pid()
        input_desktop = _windows_input_desktop_name()
        consent_pids = frozenset(
            pid for pid, name in processes.items() if name == "consent.exe"
        )
        starter_pids = _login_handoff_process_pids(processes)
        new_starter_pids = (
            starter_pids - self._login_bootstrap_baseline_starter_pids
        )
        login_window_handles = _visible_open_api_login_window_handles(processes)
        new_login_window_handles = (
            login_window_handles
            - self._login_bootstrap_baseline_login_window_handles
        )
        handoff_observed = bool(new_starter_pids or new_login_window_handles)
        if handoff_observed:
            self._login_bootstrap_starter_observed = True
            self._stop_login_bootstrap_observation()
            return

        current_windows = _visible_top_level_window_handles_for_pid(os.getpid())
        new_window_handles = (
            current_windows - self._login_bootstrap_baseline_window_handles
        )
        if consent_pids and input_desktop and input_desktop != "Default":
            self._login_bootstrap_secure_desktop_observed = True
        uac_lifecycle_observed = bool(
            self._login_bootstrap_consent_observed
            or self._login_bootstrap_secure_desktop_observed
        )
        uac_completed = bool(
            uac_lifecycle_observed
            and (
                not consent_pids
                or (
                    self._login_bootstrap_secure_desktop_observed
                    and input_desktop == "Default"
                )
            )
        )
        if new_window_handles:
            self._stop_login_bootstrap_observation()
            return

        if consent_pids:
            self._login_bootstrap_consent_observed = True
            self._login_bootstrap_observed_consent_pids.update(consent_pids)
            if not uac_completed:
                self._login_bootstrap_closed_observations = 0
                return
        if not (
            self._login_bootstrap_consent_observed
            or self._login_bootstrap_secure_desktop_observed
        ):
            return
        if self._login_bootstrap_starter_observed:
            self._stop_login_bootstrap_observation()
            return

        self._login_bootstrap_closed_observations += 1
        if self._login_bootstrap_closed_observations < 3:
            return
        if self.is_connected():
            self._stop_login_bootstrap_observation()
            return

        self._login_requested = False
        self.last_login_error = None
        self._stop_login_bootstrap_observation()
        self._invalidate_login_session(
            reason="login bootstrap rejected",
            emit=True,
            increment_epoch=False,
            err_code=None,
            status="login_bootstrap_rejected",
            message="미연결 상태",
        )

    def is_connected(self) -> bool:
        if not self.is_available():
            return False
        try:
            connected = int(self._control.dynamicCall("GetConnectState()") or 0) == 1
            self._observe_connected_state(
                connected,
                reason="GetConnectState observed disconnected",
            )
            return self._connected
        except Exception:
            return bool(self._connected)

    def account_numbers(self) -> list[str]:
        """Return Kiwoom login account numbers from the active OpenAPI session."""
        if not self.is_available() or not self.is_connected():
            return []

        try:
            raw_value = self._control.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        except Exception:
            return []

        accounts: list[str] = []
        seen: set[str] = set()
        for item in str(raw_value or "").split(";"):
            account = item.strip()
            if not account or account in seen:
                continue
            accounts.append(account)
            seen.add(account)
        return accounts

    def account_server_type(self) -> str:
        """Return the official login-server classification for UI projection."""
        if not self.is_available() or not self.is_connected():
            return ""
        try:
            raw_value = self._control.dynamicCall(
                "GetLoginInfo(QString)",
                "GetServerGubun",
            )
        except Exception:
            return ""
        value = str(raw_value or "").strip()
        if value == "1":
            return "SIMULATION"
        return "REAL" if value else ""

    def login_session_id(self) -> str:
        """Return the current process-local Kiwoom login session identity."""
        return self._login_session_id if self.is_connected() else ""

    def send_order(
        self,
        screen_no: str,
        order_name: str,
        account_no: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str,
        original_order_no: str,
    ) -> Any:
        """Call Kiwoom OpenAPI SendOrder once with the official 9 arguments."""
        if not self.is_available():
            raise RuntimeError(self._unavailable_reason or "kiwoom api unavailable")
        if not self.is_connected():
            raise RuntimeError("kiwoom api is not connected")
        return self._control.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            str(screen_no or ""),
            str(order_name or ""),
            str(account_no or ""),
            int(order_type),
            str(code or ""),
            int(quantity),
            int(price),
            str(hoga or ""),
            str(original_order_no or ""),
        )

    def request_minute_candles(
        self,
        code: str,
        name: str = "",
        interval: int = 1,
        count: int = 300,
        max_count: int = DEFAULT_CANDLES_MAX_COUNT,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        """Request opt10080 minute candles and save the response on receipt."""
        clean_code = str(code or "").strip()
        if not clean_code:
            return self._finish_callback(
                callback,
                {"ok": False, "error": "stock code is required"},
            )
        if not self.is_available():
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "error": self._unavailable_reason or "kiwoom api unavailable"},
            )
        if not self.is_connected():
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "error": "kiwoom api is not connected"},
            )
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result["code"] = clean_code
            return self._finish_callback(callback, result)

        try:
            clean_interval = max(int(interval), 1)
        except (TypeError, ValueError):
            clean_interval = 1
        try:
            clean_count = max(int(count), 1)
        except (TypeError, ValueError):
            clean_count = 300
        try:
            clean_max_count = max(int(max_count), clean_count)
        except (TypeError, ValueError):
            clean_max_count = DEFAULT_CANDLES_MAX_COUNT

        rqname = f"opt10080_{clean_code}_{datetime.now().strftime('%H%M%S%f')}"
        claimed_screen_no, claim_error = self._claim_tr_screen(
            purpose=MARKET_TR,
            rqname=rqname,
            screen_no=screen_no,
            callback=callback,
            failure_payload={"code": clean_code},
        )
        if claimed_screen_no is None:
            return claim_error or {"ok": False, "code": clean_code, "rqname": rqname}
        self._pending_tr[rqname] = {
            "type": "minute_candles",
            "code": clean_code,
            "name": str(name or "").strip(),
            "interval": clean_interval,
            "count": clean_count,
            "max_count": clean_max_count,
            "screen_no": claimed_screen_no,
            "callback": callback,
            "rows": [],
            **request_identity,
        }

        def start_timeout(_result: Any) -> None:
            QTimer.singleShot(
                self.MINUTE_CANDLE_TR_TIMEOUT_MS,
                lambda request_name=rqname: self._expire_minute_candle_request(request_name),
            )

        def fail_request(result: Any, error: str) -> dict[str, Any]:
            pending = self._pending_tr.pop(rqname, None)
            self._release_pending_tr_screen(rqname, pending)
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "code": clean_code,
                    "rqname": rqname,
                    "result": result,
                    "error": error,
                },
            )

        dispatched = self._submit_governed_tr_request(
            rqname=rqname,
            trcode="opt10080",
            prev_next=0,
            screen_no=claimed_screen_no,
            inputs=(
                ("종목코드", clean_code),
                ("틱범위", str(clean_interval)),
                ("수정주가구분", "1"),
            ),
            pending=self._pending_tr[rqname],
            on_dispatched=start_timeout,
            on_failed=fail_request,
        )
        if not dispatched.get("ok"):
            return dispatched
        return {
            "ok": True,
            "status": dispatched.get("status", "REQUESTED"),
            "code": clean_code,
            "rqname": rqname,
            "screen_no": claimed_screen_no,
            "result": dispatched.get("result"),
        }

    def _expire_minute_candle_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "minute_candles":
            return
        self._release_pending_tr_screen(str(rqname), pending)
        callback = pending.get("callback")
        self._finish_callback(
            callback if callable(callback) else None,
            {
                "ok": False,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "error": "minute candle request timed out",
                **_empty_candle_commit_projection(),
            },
        )

    def request_account_holdings_snapshot(
        self,
        identity: RecoverySessionIdentity,
        *,
        screen_no: str | None = HOLDINGS_SCREEN_NO,
        callback: RecoverySnapshotCallback | None = None,
        timeout_ms: int = RECOVERY_TR_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Request a complete OPW00018 holdings snapshot without writing Runtime."""
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery identity is required"},
            )
        return self._start_recovery_snapshot_request(
            identity,
            kind="HOLDINGS",
            trcode="opw00018",
            screen_no=screen_no,
            callback=callback,
            timeout_ms=timeout_ms,
            inputs=(
                ("계좌번호", identity.account_no),
                ("비밀번호", ""),
                ("비밀번호입력매체구분", "00"),
                ("조회구분", "1"),
                ("거래소구분", ""),
            ),
        )

    def request_open_orders_snapshot(
        self,
        identity: RecoverySessionIdentity,
        *,
        screen_no: str | None = OPEN_ORDERS_SCREEN_NO,
        callback: RecoverySnapshotCallback | None = None,
        timeout_ms: int = RECOVERY_TR_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Request a complete OPT10075 open-order snapshot without mutation."""
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery identity is required"},
            )
        return self._start_recovery_snapshot_request(
            identity,
            kind="OPEN_ORDERS",
            trcode="opt10075",
            screen_no=screen_no,
            callback=callback,
            timeout_ms=timeout_ms,
            inputs=(
                ("계좌번호", identity.account_no),
                ("전체종목구분", "0"),
                ("매매구분", "0"),
                ("종목코드", ""),
                ("체결구분", "1"),
                ("거래소구분", "0"),
            ),
        )

    def request_account_funds_snapshot(
        self,
        account_id: str,
        *,
        request_id: int,
        screen_no: str | None = ACCOUNT_FUNDS_SCREEN_NO,
        callback: AccountFundsCallback | None = None,
        timeout_ms: int = ACCOUNT_FUNDS_TR_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Request OPW00001 summary values without persisting broker data."""
        clean_account = str(account_id or "").strip()
        if not self.is_available():
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "error": self._unavailable_reason or "kiwoom api unavailable"},
            )
        if not self.is_connected():
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "error": "kiwoom api is not connected"},
            )
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result.update({"account_id": clean_account, "request_id": request_id})
            return self._finish_callback(callback, result)
        if not clean_account or clean_account not in self.account_numbers():
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "error": "account is not in the login session"},
            )

        rqname = "OPW00001_ACCOUNT_FUNDS_{}_{}".format(
            int(request_id),
            datetime.now().strftime("%H%M%S%f"),
        )
        claimed_screen_no, claim_error = self._claim_tr_screen(
            purpose=ACCOUNT_TR,
            rqname=rqname,
            screen_no=screen_no,
            callback=callback,
            failure_payload={"account_id": clean_account, "request_id": request_id},
        )
        if claimed_screen_no is None:
            return claim_error or {
                "ok": False,
                "account_id": clean_account,
                "request_id": request_id,
                "rqname": rqname,
            }
        pending = {
            "type": "account_funds",
            "trcode": "opw00001",
            "screen_no": claimed_screen_no,
            "account_id": clean_account,
            "request_id": int(request_id),
            "callback": callback,
            **request_identity,
        }
        self._pending_tr[rqname] = pending
        self._account_funds_request_accounts[rqname] = clean_account
        try:
            clean_timeout = max(int(timeout_ms), 1)
        except (TypeError, ValueError):
            clean_timeout = self.ACCOUNT_FUNDS_TR_TIMEOUT_MS

        def start_timeout(_result: Any) -> None:
            QTimer.singleShot(
                clean_timeout,
                lambda request_name=rqname: self._expire_account_funds_request(request_name),
            )
            QTimer.singleShot(
                clean_timeout + 1000,
                lambda request_name=rqname: self._account_funds_request_accounts.pop(
                    request_name,
                    None,
                ),
            )

        def fail_request(result: Any, error: str) -> dict[str, Any]:
            pending = self._pending_tr.pop(rqname, None)
            self._release_pending_tr_screen(rqname, pending)
            self._account_funds_request_accounts.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "rqname": rqname, "result": result, "error": error},
            )

        dispatched = self._submit_governed_tr_request(
            rqname=rqname,
            trcode="opw00001",
            prev_next=0,
            screen_no=claimed_screen_no,
            inputs=(
                ("계좌번호", clean_account),
                ("비밀번호", ""),
                ("비밀번호입력매체구분", "00"),
                ("조회구분", "2"),
            ),
            pending=pending,
            on_dispatched=start_timeout,
            on_failed=fail_request,
        )
        if not dispatched.get("ok"):
            return dispatched
        return {
            "ok": True,
            "status": dispatched.get("status", "REQUESTED"),
            "account_id": clean_account,
            "request_id": int(request_id),
            "rqname": rqname,
            "screen_no": claimed_screen_no,
            "result": dispatched.get("result"),
        }

    def _expire_account_funds_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        self._account_funds_request_accounts.pop(str(rqname), None)
        if not pending or pending.get("type") != "account_funds":
            return
        self._release_pending_tr_screen(str(rqname), pending)
        callback = pending.get("callback")
        self._finish_callback(
            callback if callable(callback) else None,
            {
                "ok": False,
                "account_id": pending.get("account_id", ""),
                "request_id": pending.get("request_id"),
                "rqname": str(rqname),
                "error": "account funds request timed out",
            },
        )

    def _start_recovery_snapshot_request(
        self,
        identity: RecoverySessionIdentity,
        *,
        kind: str,
        trcode: str,
        screen_no: str | None,
        callback: RecoverySnapshotCallback | None,
        timeout_ms: int,
        inputs: tuple[tuple[str, str], ...],
    ) -> dict[str, Any]:
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery identity is required"},
            )
        if not self.is_available():
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "is_complete": False,
                    "error": self._unavailable_reason or "kiwoom api unavailable",
                },
            )
        if not self.is_connected():
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "kiwoom api is not connected"},
            )
        if identity.account_no not in self.account_numbers():
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery account is not in the login session"},
            )
        if (
            not identity.login_session_id
            or identity.login_session_id != self.login_session_id()
        ):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery login session is stale"},
            )
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result["is_complete"] = False
            return self._finish_callback(callback, result)

        rqname = "{}_RECOVERY_{}".format(
            trcode.upper(),
            datetime.now().strftime("%H%M%S%f"),
        )
        claimed_screen_no, claim_error = self._claim_tr_screen(
            purpose=ACCOUNT_TR,
            rqname=rqname,
            screen_no=screen_no,
            callback=callback,
            failure_payload={"is_complete": False, "kind": str(kind)},
        )
        if claimed_screen_no is None:
            return claim_error or {
                "ok": False,
                "is_complete": False,
                "kind": str(kind),
                "rqname": rqname,
            }
        pending = {
            "type": "recovery_snapshot",
            "kind": str(kind),
            "trcode": str(trcode),
            "screen_no": claimed_screen_no,
            "callback": callback,
            "identity": identity,
            "inputs": tuple(inputs),
            "rows": [],
            "pages": 0,
            **request_identity,
        }
        self._pending_tr[rqname] = pending
        try:
            clean_timeout = max(int(timeout_ms), 1)
        except (TypeError, ValueError):
            clean_timeout = self.RECOVERY_TR_TIMEOUT_MS

        def start_timeout(_result: Any) -> None:
            QTimer.singleShot(
                clean_timeout,
                lambda request_name=rqname: self._expire_recovery_snapshot_request(request_name),
            )

        dispatched = self._submit_recovery_snapshot_page(
            rqname,
            pending,
            prev_next=0,
            on_dispatched=start_timeout,
        )
        if not dispatched.get("ok"):
            return dispatched
        return {
            "ok": True,
            "is_complete": False,
            "status": dispatched.get("status", "REQUESTED"),
            "kind": kind,
            "account_no": identity.account_no,
            "trading_day": identity.trading_day,
            "recovery_session_id": identity.recovery_session_id,
            "rqname": rqname,
            "screen_no": claimed_screen_no,
            "result": dispatched.get("result"),
        }

    def _submit_recovery_snapshot_page(
        self,
        rqname: str,
        pending: dict[str, Any],
        *,
        prev_next: int,
        on_dispatched: Callable[[Any], None] | None = None,
    ) -> dict[str, Any]:
        def fail_request(result: Any, error: str) -> dict[str, Any]:
            pending_for_release = self._pending_tr.pop(str(rqname), None)
            if pending_for_release is pending:
                self._release_pending_tr_screen(str(rqname), pending)
            return self._finish_recovery_snapshot(
                str(rqname),
                pending,
                collection_complete=False,
                errors=(error,),
            )

        return self._submit_governed_tr_request(
            rqname=str(rqname),
            trcode=str(pending.get("trcode") or ""),
            prev_next=int(prev_next),
            screen_no=str(pending.get("screen_no") or ""),
            inputs=tuple(pending.get("inputs", ())),
            pending=pending,
            on_dispatched=on_dispatched,
            on_failed=fail_request,
        )

    def _expire_recovery_snapshot_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "recovery_snapshot":
            return
        self._release_pending_tr_screen(str(rqname), pending)
        self._finish_recovery_snapshot(
            str(rqname),
            pending,
            collection_complete=False,
            errors=("broker snapshot request timed out",),
        )

    def _on_event_connect(self, err_code: Any) -> None:
        self._login_requested = False
        self._stop_login_bootstrap_observation()
        try:
            code = int(err_code)
        except (TypeError, ValueError):
            code = -9999

        self.last_login_error = code
        if code == 0:
            self.clear_realtime_shadow_registration(
                remove_from_broker=False,
                reason="REALTIME_SHADOW_NEW_SESSION",
            )
            account_payload = ";".join(self.account_numbers())
            self._establish_login_session(account_payload=account_payload)
            self.last_login_message = "login succeeded"
            self.login_state_changed.emit(
                {
                    "connected": True,
                    "err_code": code,
                    "message": self.last_login_message,
                    "connection_epoch": self._connection_epoch,
                    "login_session_id": self._login_session_id,
                }
            )
            return

        messages = {
            -100: "user info exchange failed",
            -101: "server connection failed",
            -102: "version processing failed",
        }
        self.last_login_message = messages.get(code, f"login failed: {code}")
        self._invalidate_login_session(
            reason="OnEventConnect failed",
            emit=True,
            increment_epoch=True,
            err_code=code,
            status="login_failed",
            message=self.last_login_message,
        )

    def _on_receive_chejan_data(self, gubun: Any, item_cnt: Any, fid_list: Any) -> None:
        if not self.is_available():
            return
        fids: list[str] = []
        for item in str(fid_list or "").split(";"):
            fid = item.strip()
            if fid:
                fids.append(fid)

        observed_fids = list(dict.fromkeys([*fids, *TRADE_COST_DIAGNOSTIC_FIDS]))
        fid_raw_values: dict[str, str] = {}
        fid_values: dict[str, str] = {}
        for fid in observed_fids:
            try:
                value = self._control.dynamicCall("GetChejanData(int)", int(fid))
            except Exception:
                value = ""
            raw_text = "" if value is None else str(value)
            fid_raw_values[fid] = raw_text
            fid_values[fid] = raw_text.strip()

        try:
            count = int(item_cnt)
        except (TypeError, ValueError):
            count = len(fids)

        raw_event = {
            "source": "kiwoom_chejan",
            "gubun": str(gubun or "").strip(),
            "item_count": count,
            "fid_list": fids,
            "observed_fid_list": observed_fids,
            "fid_raw_values": fid_raw_values,
            "fid_values": fid_values,
            "received_at": datetime.now().isoformat(sep=" ", timespec="milliseconds"),
        }
        if raw_event["gubun"] == "0":
            try:
                record_trade_cost_chejan_diagnostic(raw_event)
            except Exception:
                pass
        self.raw_chejan_received.emit(raw_event)

    def _on_receive_real_data(self, *args: Any) -> None:
        """Read the three verified 주식체결 FIDs into process-local shadow state."""

        if len(args) < 2:
            return
        self._ensure_realtime_shadow_state()
        stock_code = str(args[0] or "").strip()
        real_type = str(args[1] or "").strip()
        registration = self._realtime_shadow_registration
        if (
            not registration.active
            or stock_code not in registration.target_stock_codes
            or real_type != REALTIME_EXECUTION_TYPE
        ):
            return
        session = self.broker_session_snapshot()
        if (
            not session.connected
            or session.connection_epoch != registration.connection_epoch
            or session.login_session_id != registration.login_session_id
        ):
            return
        try:
            values = {
                fid: self._control.dynamicCall(
                    "GetCommRealData(QString, int)",
                    stock_code,
                    fid,
                )
                for fid in REALTIME_SHADOW_FIDS
            }
            tick = normalize_realtime_shadow_tick(
                stock_code=stock_code,
                real_type=real_type,
                execution_time_raw=values[REALTIME_EXECUTION_TIME_FID],
                current_price_raw=values[REALTIME_CURRENT_PRICE_FID],
                cumulative_volume_raw=values[REALTIME_CUMULATIVE_VOLUME_FID],
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
            )
            if tick is None:
                return
            self.realtime_shadow_tick_received.emit(tick.to_payload())
            _status, completed = self._realtime_shadow_builder.accept_tick(tick)
            if completed is not None:
                self.realtime_shadow_bar_completed.emit(completed.to_payload())
        except Exception as exc:
            observe_production_exception(
                type(exc),
                exc,
                exc.__traceback__,
                component="realtime_shadow",
                operation="receive_realtime_tick",
                source="kiwoom_api.KiwoomApi._on_receive_real_data",
                target_type="MARKET_DATA",
                target_id=stock_code,
                target_name=stock_code,
                reason_code="REALTIME_SHADOW_TICK_FAILED",
                failure_scope=f"realtime_shadow_tick:{stock_code}",
            )

    def _on_receive_msg(self, *args: Any) -> None:
        """Project only verified account-password messages into account UI evidence."""

        if len(args) < 4:
            return
        screen_no, rqname, trcode, message = args[:4]
        request_name = str(rqname or "")
        account_id = str(
            self._account_funds_request_accounts.get(request_name, "") or ""
        ).strip()
        if not account_id or not account_authentication_required_message(message):
            return

        pending = self._pending_tr.get(request_name)
        if pending and pending.get("type") == "account_funds":
            if not self._pending_tr_matches_current_session(pending):
                self._finish_stale_pending_tr(request_name, pending)
                return

        self._account_funds_request_accounts.pop(request_name, None)
        pending = self._pending_tr.pop(request_name, None)
        self._release_pending_tr_screen(request_name, pending)
        payload = {
            "ok": False,
            "account_id": account_id,
            "request_id": pending.get("request_id") if pending else None,
            "rqname": request_name,
            "trcode": str(trcode or ""),
            "screen_no": str(screen_no or ""),
            "error": str(message or "").strip(),
            "error_kind": ACCOUNT_AUTHENTICATION_REQUIRED,
        }
        if pending and pending.get("type") == "account_funds":
            callback = pending.get("callback")
            self._finish_callback(
                callback if callable(callback) else None,
                payload,
            )
        self.account_authentication_required.emit(dict(payload))

    def _on_receive_tr_data(self, *args: Any) -> None:
        if len(args) < 5:
            return
        _screen_no, rqname, trcode, _record_name, prev_next = args[:5]
        request_name = str(rqname)
        pending = self._pending_tr.get(request_name)
        if not pending:
            return
        if not self._pending_tr_matches_current_session(pending):
            self._finish_stale_pending_tr(request_name, pending)
            return
        if pending.get("type") == "recovery_snapshot":
            self._on_receive_recovery_snapshot_page(
                request_name,
                str(trcode),
                str(prev_next).strip(),
                pending,
            )
            return
        if pending.get("type") == "account_funds":
            self._on_receive_account_funds(request_name, str(trcode), pending)
            return
        pending = self._pending_tr.pop(request_name, None)
        if not pending or pending.get("type") != "minute_candles":
            return
        self._release_pending_tr_screen(request_name, pending)

        callback = pending.get("callback")
        try:
            rows = self._read_opt10080_rows(str(trcode), str(rqname), int(pending.get("count") or 300))
            pending["rows"] = rows
            commit = commit_minute_candles_for_stock(
                str(pending.get("code", "")),
                str(pending.get("name", "")),
                rows,
                max_count=int(pending.get("max_count") or DEFAULT_CANDLES_MAX_COUNT),
                rqname=str(rqname),
                trcode=str(trcode),
                connection_epoch=int(pending.get("request_connection_epoch") or 0),
            )
            result = {
                "ok": commit.ok,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "trcode": str(trcode),
                "rows_count": len(rows),
                "saved_count": commit.saved_count,
                "commit_verified": bool(commit.ok and commit.readback_verified),
                "changed": commit.changed,
                "canonical_content_hash": commit.canonical_content_hash,
                "canonical_path": commit.path,
                "commit_identity": commit.commit_identity,
                "bar_key": commit.bar_key,
                "bar_identity": commit.bar_identity,
                "bar_time": commit.bar_time,
                "trade_date": commit.trade_date,
                "error_kind": commit.error_kind,
                "error": commit.error,
                "has_more": str(prev_next).strip() == "2",
                "warning": "additional pages available" if str(prev_next).strip() == "2" else "",
            }
            if commit.ok and commit.readback_verified and commit.changed and commit.notification is not None:
                self.bar_committed.emit(commit.notification.to_payload())
        except Exception as exc:
            result = {
                "ok": False,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "trcode": str(trcode),
                "error": str(exc),
                **_empty_candle_commit_projection(),
            }

        self._finish_callback(callback if callable(callback) else None, result)

    def _on_receive_account_funds(
        self,
        rqname: str,
        trcode: str,
        pending: dict[str, Any],
    ) -> None:
        current = self._pending_tr.pop(rqname, None)
        if current is not pending:
            return
        self._release_pending_tr_screen(rqname, pending)
        callback = pending.get("callback")
        try:
            raw_deposit = self._control.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                trcode,
                rqname,
                0,
                "예수금",
            )
            raw_orderable = self._control.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                trcode,
                rqname,
                0,
                "주문가능금액",
            )
            raw_deposit_text = str(raw_deposit or "").strip()
            raw_orderable_text = str(raw_orderable or "").strip()
            authentication_error = next(
                (
                    value
                    for value in (raw_deposit_text, raw_orderable_text)
                    if account_authentication_required_message(value)
                ),
                "",
            )
            if authentication_error:
                result = {
                    "ok": False,
                    "account_id": pending.get("account_id", ""),
                    "request_id": pending.get("request_id"),
                    "rqname": rqname,
                    "error": authentication_error,
                    "error_kind": ACCOUNT_AUTHENTICATION_REQUIRED,
                }
                self._account_funds_request_accounts.pop(rqname, None)
                self.account_authentication_required.emit(dict(result))
            else:
                result = {
                    "ok": True,
                    "account_id": pending.get("account_id", ""),
                    "request_id": pending.get("request_id"),
                    "rqname": rqname,
                    "raw_deposit": str(raw_deposit or ""),
                    "raw_orderable_cash": str(raw_orderable or ""),
                    "account_type": self.account_server_type(),
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                }
        except Exception as exc:
            result = {
                "ok": False,
                "account_id": pending.get("account_id", ""),
                "request_id": pending.get("request_id"),
                "rqname": rqname,
                "error": f"account funds response parsing failed: {exc}",
            }
        self._finish_callback(callback if callable(callback) else None, result)

    def _on_receive_recovery_snapshot_page(
        self,
        rqname: str,
        trcode: str,
        prev_next: str,
        pending: dict[str, Any],
    ) -> None:
        try:
            kind = str(pending.get("kind") or "")
            fields = (
                self.OPW00018_FIELDS
                if kind == "HOLDINGS"
                else self.OPT10075_FIELDS
            )
            rows = self._read_tr_rows(trcode, rqname, fields)
            pending.setdefault("rows", []).extend(rows)
            pending["pages"] = int(pending.get("pages") or 0) + 1
        except Exception as exc:
            self._pending_tr.pop(rqname, None)
            self._finish_recovery_snapshot(
                rqname,
                pending,
                collection_complete=False,
                errors=(f"broker snapshot response parsing failed: {exc}",),
            )
            return

        if prev_next == "2":
            if not self._pending_tr_matches_current_session(pending):
                self._finish_stale_pending_tr(rqname, pending)
                return
            result = self._submit_recovery_snapshot_page(
                rqname,
                pending,
                prev_next=2,
            )
            if result.get("ok"):
                return
            return

        self._pending_tr.pop(rqname, None)
        self._finish_recovery_snapshot(
            rqname,
            pending,
            collection_complete=True,
        )

    def _finish_recovery_snapshot(
        self,
        rqname: str,
        pending: dict[str, Any],
        *,
        collection_complete: bool,
        errors: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        self._release_pending_tr_screen(str(rqname), pending)
        identity = pending.get("identity")
        callback = pending.get("callback")
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback if callable(callback) else None,
                {
                    "ok": False,
                    "is_complete": False,
                    "rqname": rqname,
                    "error": "recovery identity was lost",
                },
            )
        part = build_snapshot_part(
            identity=identity,
            kind=str(pending.get("kind") or ""),
            rows=pending.get("rows") or (),
            completed_at=datetime.now().isoformat(timespec="microseconds"),
            source="KIWOOM_OPENAPI_TR",
            collection_complete=collection_complete,
            collection_errors=errors,
        )
        result = {
            "ok": part.is_complete,
            "is_complete": part.is_complete,
            "status": "COMPLETED" if part.is_complete else "INCOMPLETE",
            "kind": part.kind,
            "account_no": part.account_no,
            "trading_day": part.trading_day,
            "recovery_session_id": part.recovery_session_id,
            "rqname": rqname,
            "pages": int(pending.get("pages") or 0),
            "rows_count": len(part.items),
            "snapshot": part,
            "errors": list(part.errors),
        }
        return self._finish_callback(
            callback if callable(callback) else None,
            result,
        )

    def _read_tr_rows(
        self,
        trcode: str,
        rqname: str,
        fields: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        repeat_count = int(
            self._control.dynamicCall(
                "GetRepeatCnt(QString, QString)",
                trcode,
                rqname,
            )
            or 0
        )
        rows: list[dict[str, Any]] = []
        for index in range(repeat_count):
            row: dict[str, Any] = {}
            for field in fields:
                value = self._control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    trcode,
                    rqname,
                    index,
                    field,
                )
                row[field] = str(value or "").strip()
            rows.append(row)
        return rows

    def _read_opt10080_rows(self, trcode: str, rqname: str, count: int) -> list[dict[str, Any]]:
        repeat_count = int(self._control.dynamicCall(
            "GetRepeatCnt(QString, QString)",
            trcode,
            rqname,
        ) or 0)
        limit = min(max(int(count or 0), 0), repeat_count) if count else repeat_count

        rows: list[dict[str, Any]] = []
        for index in range(limit):
            row: dict[str, Any] = {}
            for field in self.OPT10080_FIELDS:
                value = self._control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    trcode,
                    rqname,
                    index,
                    field,
                )
                row[field] = str(value or "").strip()
            rows.append(row)
        return rows

    @staticmethod
    def _finish_callback(
        callback: Callable[[dict[str, Any]], None] | None,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if callable(callback):
            try:
                callback(result)
            except Exception as exc:
                execution_id = str(result.get("execution_id") or "").strip()
                order_id = str(result.get("order_id") or "").strip()
                observe_production_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    component="kiwoom_api",
                    operation="finish_callback",
                    source="kiwoom_api.KiwoomApi._finish_callback",
                    target_type="BROKER_CALLBACK",
                    target_id="kiwoom_callback",
                    target_name="키움 OpenAPI callback",
                    reason_code="KIWOOM_CALLBACK_FAILED",
                    execution_id=execution_id,
                    order_id=order_id,
                    correlation_id=execution_id or order_id,
                    details={
                        "callback": str(getattr(callback, "__name__", "") or type(callback).__name__)
                    },
                )
                result = dict(result)
                result["callback_error"] = str(exc)
        return result
