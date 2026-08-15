# -*- coding: utf-8 -*-
"""Data-query-only Kiwoom OpenAPI wrapper.

This first wrapper only supports login status checks and opt10080 minute candle
queries. It does not place orders, register realtime feeds, call the routine
engine, or write rules.
"""

from __future__ import annotations

import ctypes
from datetime import datetime
import hashlib
import os
import threading
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
from kiwoom_candle_adapter import save_minute_candles_for_stock
from kiwoom_trade_cost_diagnostic import record_trade_cost_chejan_diagnostic
from production_recovery_contract import RecoverySessionIdentity, build_snapshot_part


Opt10080Callback = Callable[[dict[str, Any]], None]
RecoverySnapshotCallback = Callable[[dict[str, Any]], None]
AccountFundsCallback = Callable[[dict[str, Any]], None]


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
    HOLDINGS_SCREEN_NO = "9101"
    OPEN_ORDERS_SCREEN_NO = "9102"
    ACCOUNT_FUNDS_SCREEN_NO = "9103"
    RECOVERY_TR_TIMEOUT_MS = 15_000
    ACCOUNT_FUNDS_TR_TIMEOUT_MS = 15_000
    MINUTE_CANDLE_TR_TIMEOUT_MS = 10_000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._control: Any = None
        self._available = False
        self._unavailable_reason = ""
        self._connected = False
        self._login_requested = False
        self._login_session_id = ""
        self._login_bootstrap_timer = QTimer(self)
        self._login_bootstrap_timer.setInterval(200)
        self._login_bootstrap_timer.timeout.connect(
            self._observe_login_bootstrap
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
            receive_message = getattr(control, "OnReceiveMsg", None)
            if receive_message is not None:
                receive_message.connect(self._on_receive_msg)
            chejan_signal = getattr(control, "OnReceiveChejanData", None)
            if chejan_signal is not None:
                chejan_signal.connect(self._on_receive_chejan_data)
            self._control = control
            self._available = True
        except Exception as exc:  # pragma: no cover - depends on Kiwoom OCX.
            self._control = None
            self._available = False
            self._unavailable_reason = str(exc)

    def is_available(self) -> bool:
        return bool(self._available and self._control is not None)

    def unavailable_reason(self) -> str:
        return self._unavailable_reason

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
            self._connected = False
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
                self._connected = False
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
            self._connected = False
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
        self._connected = False
        self._login_session_id = ""
        self.last_login_error = None
        self.last_login_message = "미연결 상태"
        self._stop_login_bootstrap_observation()
        self.login_state_changed.emit(
            {
                "connected": False,
                "err_code": None,
                "status": "login_bootstrap_rejected",
                "message": self.last_login_message,
            }
        )

    def is_connected(self) -> bool:
        if not self.is_available():
            self._connected = False
            return False
        try:
            self._connected = int(self._control.dynamicCall("GetConnectState()") or 0) == 1
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
        screen_no: str = "9001",
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
        self._pending_tr[rqname] = {
            "type": "minute_candles",
            "code": clean_code,
            "name": str(name or "").strip(),
            "interval": clean_interval,
            "count": clean_count,
            "max_count": clean_max_count,
            "screen_no": str(screen_no or "9001"),
            "callback": callback,
            "rows": [],
        }

        try:
            self._control.dynamicCall("SetInputValue(QString, QString)", "종목코드", clean_code)
            self._control.dynamicCall("SetInputValue(QString, QString)", "틱범위", str(clean_interval))
            self._control.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            result = self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname,
                "opt10080",
                0,
                str(screen_no or "9001"),
            )
        except Exception as exc:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "rqname": rqname, "error": str(exc)},
            )

        if int(result or 0) != 0:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "rqname": rqname, "result": result},
            )

        QTimer.singleShot(
            self.MINUTE_CANDLE_TR_TIMEOUT_MS,
            lambda request_name=rqname: self._expire_minute_candle_request(request_name),
        )
        return {"ok": True, "code": clean_code, "rqname": rqname, "result": result}

    def _expire_minute_candle_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "minute_candles":
            return
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
            },
        )

    def request_account_holdings_snapshot(
        self,
        identity: RecoverySessionIdentity,
        *,
        screen_no: str = HOLDINGS_SCREEN_NO,
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
        screen_no: str = OPEN_ORDERS_SCREEN_NO,
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
        screen_no: str = ACCOUNT_FUNDS_SCREEN_NO,
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
        pending = {
            "type": "account_funds",
            "trcode": "opw00001",
            "screen_no": str(screen_no),
            "account_id": clean_account,
            "request_id": int(request_id),
            "callback": callback,
            "started_at": datetime.now().isoformat(timespec="microseconds"),
        }
        self._pending_tr[rqname] = pending
        self._account_funds_request_accounts[rqname] = clean_account
        try:
            for field, value in (
                ("계좌번호", clean_account),
                ("비밀번호", ""),
                ("비밀번호입력매체구분", "00"),
                ("조회구분", "2"),
            ):
                self._control.dynamicCall(
                    "SetInputValue(QString, QString)",
                    field,
                    value,
                )
            result = self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname,
                "opw00001",
                0,
                str(screen_no),
            )
        except Exception:
            result = -1
        if int(result or 0) != 0:
            self._pending_tr.pop(rqname, None)
            self._account_funds_request_accounts.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "rqname": rqname, "result": result, "error": "CommRqData failed"},
            )

        try:
            clean_timeout = max(int(timeout_ms), 1)
        except (TypeError, ValueError):
            clean_timeout = self.ACCOUNT_FUNDS_TR_TIMEOUT_MS
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
        return {
            "ok": True,
            "status": "REQUESTED",
            "account_id": clean_account,
            "request_id": int(request_id),
            "rqname": rqname,
            "result": result,
        }

    def _expire_account_funds_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        self._account_funds_request_accounts.pop(str(rqname), None)
        if not pending or pending.get("type") != "account_funds":
            return
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
        screen_no: str,
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

        rqname = "{}_RECOVERY_{}".format(
            trcode.upper(),
            datetime.now().strftime("%H%M%S%f"),
        )
        pending = {
            "type": "recovery_snapshot",
            "kind": str(kind),
            "trcode": str(trcode),
            "screen_no": str(screen_no),
            "callback": callback,
            "identity": identity,
            "inputs": tuple(inputs),
            "rows": [],
            "pages": 0,
            "started_at": datetime.now().isoformat(timespec="microseconds"),
        }
        self._pending_tr[rqname] = pending
        result = self._submit_recovery_snapshot_page(rqname, pending, prev_next=0)
        if int(result or 0) != 0:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "is_complete": False,
                    "rqname": rqname,
                    "result": result,
                    "error": "CommRqData failed",
                },
            )
        try:
            clean_timeout = max(int(timeout_ms), 1)
        except (TypeError, ValueError):
            clean_timeout = self.RECOVERY_TR_TIMEOUT_MS
        QTimer.singleShot(
            clean_timeout,
            lambda request_name=rqname: self._expire_recovery_snapshot_request(request_name),
        )
        return {
            "ok": True,
            "is_complete": False,
            "status": "REQUESTED",
            "kind": kind,
            "account_no": identity.account_no,
            "trading_day": identity.trading_day,
            "recovery_session_id": identity.recovery_session_id,
            "rqname": rqname,
            "result": result,
        }

    def _submit_recovery_snapshot_page(
        self,
        rqname: str,
        pending: dict[str, Any],
        *,
        prev_next: int,
    ) -> Any:
        try:
            for field, value in pending.get("inputs", ()):
                self._control.dynamicCall(
                    "SetInputValue(QString, QString)",
                    str(field),
                    str(value),
                )
            return self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                str(rqname),
                str(pending.get("trcode") or ""),
                int(prev_next),
                str(pending.get("screen_no") or ""),
            )
        except Exception:
            return -1

    def _expire_recovery_snapshot_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "recovery_snapshot":
            return
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
        self._connected = code == 0
        if code == 0:
            connected_at = datetime.now().isoformat(timespec="microseconds")
            account_payload = ";".join(self.account_numbers())
            digest = hashlib.sha256(
                f"{connected_at}|{account_payload}".encode("utf-8")
            ).hexdigest().upper()
            self._login_session_id = f"KIWOOM_LOGIN_SESSION_{digest}"
            self.last_login_message = "login succeeded"
            self.login_state_changed.emit(
                {
                    "connected": True,
                    "err_code": code,
                    "message": self.last_login_message,
                }
            )
            return

        messages = {
            -100: "user info exchange failed",
            -101: "server connection failed",
            -102: "version processing failed",
        }
        self._login_session_id = ""
        self.last_login_message = messages.get(code, f"login failed: {code}")
        self.login_state_changed.emit(
            {
                "connected": False,
                "err_code": code,
                "message": self.last_login_message,
            }
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

        self._account_funds_request_accounts.pop(request_name, None)
        pending = self._pending_tr.pop(request_name, None)
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

        callback = pending.get("callback")
        try:
            rows = self._read_opt10080_rows(str(trcode), str(rqname), int(pending.get("count") or 300))
            pending["rows"] = rows
            saved = save_minute_candles_for_stock(
                str(pending.get("code", "")),
                str(pending.get("name", "")),
                rows,
                max_count=int(pending.get("max_count") or DEFAULT_CANDLES_MAX_COUNT),
            )
            result = {
                "ok": True,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "trcode": str(trcode),
                "rows_count": len(rows),
                "saved_count": len(saved),
                "has_more": str(prev_next).strip() == "2",
                "warning": "additional pages available" if str(prev_next).strip() == "2" else "",
            }
        except Exception as exc:
            result = {
                "ok": False,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "trcode": str(trcode),
                "error": str(exc),
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
            result = self._submit_recovery_snapshot_page(
                rqname,
                pending,
                prev_next=2,
            )
            if int(result or 0) == 0:
                return
            self._pending_tr.pop(rqname, None)
            self._finish_recovery_snapshot(
                rqname,
                pending,
                collection_complete=False,
                errors=("broker snapshot continuation request failed",),
            )
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
                result = dict(result)
                result["callback_error"] = str(exc)
        return result
