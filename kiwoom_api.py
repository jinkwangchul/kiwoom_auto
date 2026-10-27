# -*- coding: utf-8 -*-
"""Central Kiwoom OpenAPI wrapper for broker, TR, and shadow realtime I/O."""

from __future__ import annotations

import ctypes
from collections import deque
from dataclasses import asdict, dataclass, replace
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
from kiwoom_candle_adapter import (
    CanonicalMinuteCandleCommitResult,
    commit_minute_candles_for_stock,
    commit_realtime_primary_bar_for_stock,
    filter_verified_krx_opt10080_rows,
)
from kiwoom_trade_cost_diagnostic import record_trade_cost_chejan_diagnostic
from production_recovery_contract import RecoverySessionIdentity, build_snapshot_part
from event_journal_production import append_production_event, observe_production_exception
from stock_library_master_diagnostics import build_master_code_diagnostic_projection
from stock_code_contract import (
    is_broker_action_stock_code,
    is_valid_stock_code,
    normalize_broker_stock_code,
    normalize_stock_code,
)
from kiwoom_screen_allocator import (
    ACCOUNT_TR,
    MARKET_TR,
    REALTIME,
    KiwoomScreenAllocator,
    ScreenAllocationError,
)
from kiwoom_realtime_fids import (
    NXT_DISPLAY_REALTIME_FIDS,
    NXT_REALTIME_CUMULATIVE_VOLUME_FID,
    NXT_REALTIME_CURRENT_PRICE_FID,
    NXT_REALTIME_EXECUTION_TIME_FID,
    NXT_REALTIME_EXECUTION_TYPE,
    NXT_REALTIME_TRADE_VOLUME_FID,
    REALTIME_CHANGE_RATE_FID,
    REALTIME_CUMULATIVE_VOLUME_FID,
    REALTIME_CURRENT_PRICE_FID,
    REALTIME_EXECUTION_STRENGTH_FID,
    REALTIME_EXECUTION_TIME_FID,
    REALTIME_EXECUTION_TYPE,
    REALTIME_HIGH_PRICE_FID,
    REALTIME_LOW_PRICE_FID,
    REALTIME_OPEN_PRICE_FID,
    REALTIME_ORDERBOOK_FIDS,
    REALTIME_ORDERBOOK_TYPE,
    REALTIME_PREVIOUS_DAY_VOLUME_RATE_FID,
    REALTIME_SHADOW_FIDS,
    REALTIME_TRADE_VOLUME_FID,
    SOR_MOCK_EXECUTION_FIDS,
)
from mock_validation_market_data import normalize_mock_orderbook_snapshot, normalize_mock_trade_snapshot
from kiwoom_realtime_shadow import (
    RealtimeShadowBar,
    RealtimeShadowBarBuilder,
    normalize_nxt_display_realtime_tick,
    normalize_realtime_shadow_tick,
)
from kiwoom_initial_market_snapshot import (
    OPTKWFID_MARKET_FIELDS,
    normalize_optkwfid_market_row,
)
from historical_candle_row_normalizer import normalize_date_based_candle_row
from indicator_follow_signal_validation_runtime_trace import (
    signal_validation_trace_enabled,
    trace_signal_validation_event,
)
from opt10080_al_m1_page_diagnostic import AlM1PageDiagnostic

V5_AL_EXECUTION_FIDS = (
    REALTIME_EXECUTION_TIME_FID, REALTIME_CURRENT_PRICE_FID,
    REALTIME_CUMULATIVE_VOLUME_FID, REALTIME_TRADE_VOLUME_FID,
)


Opt10080Callback = Callable[[dict[str, Any]], None]
RecoverySnapshotCallback = Callable[[dict[str, Any]], None]
AccountFundsCallback = Callable[[dict[str, Any]], None]
InitialMarketSnapshotCallback = Callable[[dict[str, Any]], None]
StockRankingSnapshotCallback = Callable[[dict[str, Any]], None]


def _trace_validation_readonly_callback(result: dict[str, Any]) -> None:
    """Opt-in, bounded provenance for exactly the two approved KRX TRs."""
    if not signal_validation_trace_enabled() or result.get("code") != "005930":
        return
    timeframe = (
        "Y1" if result.get("type") == "period_candles"
        and result.get("timeframe_key") == "Y1"
        else "M120" if result.get("type") == "minute_candles"
        and result.get("interval") == 120 else ""
    )
    if not timeframe:
        return
    rows = result.get("rows")
    times = (
        [str(row.get("체결시간") or "") for row in rows if isinstance(row, dict)]
        if isinstance(rows, list) else []
    )
    trace_signal_validation_event(
        "broker.validation.readonly.callback",
        stock_code="005930",
        timeframe=timeframe,
        trcode=str(result.get("trcode") or ""),
        request_id=str(result.get("request_id") or result.get("rqname") or ""),
        market_source=str(result.get("market_source") or ""),
        market_data_identity=str(result.get("market_data_identity") or ""),
        ok=result.get("ok") is True,
        rows_count=len(times),
        first_row_time=times[0] if times else "",
        latest_row_time=max(times, default=""),
        contains_2026=any(stamp.startswith("2026") for stamp in times),
        has_more=result.get("has_more"),
        error=str(result.get("error") or ""),
    )


def _decode_legacy_koa_text(value: object) -> str:
    """Decode KOA_Functions text exposed as CP949 bytes in Latin-1 code points."""
    text = str(value or "").strip()
    if not text or any(ord(char) > 0xFF for char in text):
        return text
    try:
        decoded = text.encode("latin-1").decode("cp949")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text
    if any("\uac00" <= char <= "\ud7a3" for char in decoded):
        return decoded
    return text


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
    shadow_target_stock_codes: tuple[str, ...] | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NxtDisplayRegistrationSnapshot:
    active: bool
    connection_epoch: int
    login_session_id: str
    canonical_stock_codes: tuple[str, ...]
    broker_code_identities: tuple[str, ...]
    fid_list: tuple[int, ...]
    screen_batches: tuple[RealtimeShadowScreenBatch, ...]
    last_error: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class MockOrderbookScreenBatch:
    screen_no: str
    owner: str
    stock_codes: tuple[str, ...]
    raw_registration_result: Any = None


@dataclass(frozen=True)
class MockOrderbookRegistrationSnapshot:
    active: bool
    connection_epoch: int
    login_session_id: str
    target_stock_codes: tuple[str, ...]
    fid_list: tuple[int, ...]
    screen_batches: tuple[MockOrderbookScreenBatch, ...]
    last_error: str
    registration_id: str = ""
    sor_stock_codes: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrGovernorMetricsSnapshot:
    total_enqueued: int
    total_dispatched: int
    current_queue_depth: int
    last_rqname: str
    last_trcode: str
    last_dispatch_monotonic: int | None
    dispatch_count_last_60s: int
    last_queue_wait_ms: float
    max_queue_wait_ms: float
    timeout_count: int
    stale_count: int
    error_count: int
    last_error_reason: str


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

ORDER_RECONCILIATION_EVIDENCE_FIDS = (
    "9203",  # 주문번호
    "904",   # 원주문번호
    "909",   # 체결번호
    "2134",  # 거래소 구분 코드
    "2135",  # 거래소명
    "2136",  # SOR 여부
)


class KiwoomApi(QObject):
    """Minimal Kiwoom API wrapper for opt10080 candle lookup."""

    login_state_changed = pyqtSignal(dict)
    raw_chejan_received = pyqtSignal(dict)
    raw_message_received = pyqtSignal(dict)
    raw_order_tr_received = pyqtSignal(dict)
    account_authentication_required = pyqtSignal(dict)
    bar_committed = pyqtSignal(object)
    realtime_shadow_tick_received = pyqtSignal(object)
    realtime_shadow_bar_completed = pyqtSignal(object)
    nxt_display_tick_received = pyqtSignal(object)
    mock_orderbook_received = pyqtSignal(object)
    mock_execution_tick_received = pyqtSignal(object)
    mock_sor_trade_received = pyqtSignal(object)
    v5_al_tick_received = pyqtSignal(object)

    CONTROL_NAME = "KHOPENAPI.KHOpenAPICtrl.1"
    OPT10080_FIELDS = ("체결시간", "시가", "고가", "저가", "현재가", "거래량")
    PERIOD_CANDLE_FIELDS = ("\uc77c\uc790", "\ud604\uc7ac\uac00", "\uac70\ub798\ub7c9", "\uc2dc\uac00", "\uace0\uac00", "\uc800\uac00")
    VALIDATION_PERIOD_TR_CODES = {
        "D1": "opt10081",
        "W1": "opt10082",
        "MO1": "opt10083",
        "Y1": "opt10094",
    }
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
    INITIAL_MARKET_SNAPSHOT_TIMEOUT_MS = 15_000
    INITIAL_MARKET_SNAPSHOT_BATCH_SIZE = 100
    STOCK_RANKING_SNAPSHOT_TIMEOUT_MS = 15_000
    STOCK_RANKING_LIMIT = 100
    STOCK_RANKING_SPECS = {
        "VOLUME_TOP": {
            "trcode": "OPT10030",
            "fields": (
                "종목코드",
                "종목명",
                "현재가",
                "등락률",
                "거래량",
                "거래금액",
            ),
            "inputs": (
                ("시장구분", "000"),
                ("정렬구분", "1"),
                ("관리종목포함", "0"),
                ("신용구분", "0"),
                ("거래량구분", "0"),
                ("가격구분", "0"),
                ("거래대금구분", "0"),
                ("장운영구분", "0"),
                ("거래소구분", "1"),
            ),
        },
        "VALUE_TOP": {
            "trcode": "OPT10032",
            "fields": (
                "종목코드",
                "종목명",
                "현재가",
                "등락률",
                "현재거래량",
                "거래대금",
            ),
            "inputs": (
                ("시장구분", "000"),
                ("관리종목포함", "1"),
                ("거래소구분", "1"),
            ),
        },
        "RISE_TOP": {
            "trcode": "OPT10027",
            "fields": (
                "종목코드",
                "종목명",
                "현재가",
                "등락률",
                "현재거래량",
                "체결강도",
            ),
            "inputs": (
                ("시장구분", "000"),
                ("정렬구분", "1"),
                ("거래량조건", "0"),
                ("종목조건", "0"),
                ("신용조건", "0"),
                ("상하한포함", "1"),
                ("가격조건", "0"),
                ("거래대금조건", "0"),
                ("거래소구분", "1"),
            ),
        },
        "FALL_TOP": {
            "trcode": "OPT10027",
            "fields": (
                "종목코드",
                "종목명",
                "현재가",
                "등락률",
                "현재거래량",
                "체결강도",
            ),
            "inputs": (
                ("시장구분", "000"),
                ("정렬구분", "3"),
                ("거래량조건", "0"),
                ("종목조건", "0"),
                ("신용조건", "0"),
                ("상하한포함", "1"),
                ("가격조건", "0"),
                ("거래대금조건", "0"),
                ("거래소구분", "1"),
            ),
        },
    }
    BROKER_CONNECTION_OBSERVATION_INTERVAL_MS = 5_000
    # Project-conservative pacing, aligned with auto_candle_refresh.REQUEST_SPACING_MS.
    TR_GOVERNOR_MIN_INTERVAL_MS = 1_000
    MASTER_STOCK_MARKETS = (
        ("KOSPI", "0"),
        ("KOSDAQ", "10"),
        ("NXT", "NXT"),
    )

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._control: Any = None
        self._available = False
        self._unavailable_reason = ""
        self._connected = False
        self._login_requested = False
        self._login_session_id = ""
        self._connection_epoch = 0
        self._al_m1_page_diagnostic = AlM1PageDiagnostic.from_environment()
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
        self._send_order_request_evidence: dict[tuple[str, str], dict[str, Any]] = {}
        self._tr_request_queue: deque[dict[str, Any]] = deque()
        self._tr_last_dispatch_monotonic_ms: int | None = None
        self._tr_governor_timer_scheduled = False
        self._tr_governor_dispatching = False
        self._tr_governor_total_enqueued = 0
        self._tr_governor_total_dispatched = 0
        self._tr_governor_last_rqname = ""
        self._tr_governor_last_trcode = ""
        self._tr_governor_dispatch_history: deque[float] = deque(maxlen=4096)
        self._candle_tr_dispatch_history: deque[float] = deque()
        self._tr_governor_last_queue_wait_ms = 0.0
        self._tr_governor_max_queue_wait_ms = 0.0
        self._tr_governor_timeout_count = 0
        self._tr_governor_stale_count = 0
        self._tr_governor_error_count = 0
        self._tr_governor_last_error_reason = ""
        self._screen_allocator = KiwoomScreenAllocator()
        self._realtime_shadow_builder = RealtimeShadowBarBuilder()
        self._realtime_shadow_registration = self._empty_realtime_shadow_snapshot()
        self._realtime_receive_sequence = 0
        self._nxt_display_registration = self._empty_nxt_display_snapshot()
        self._nxt_display_receive_sequence = 0
        self._native_receive_sequence_by_code: dict[str, dict[str, int]] = {
            "NXT": {}, "KRX": {}}
        self._mock_orderbook_registration = self._empty_mock_orderbook_snapshot()
        self._mock_orderbook_receive_sequence = 0
        self._mock_sor_trade_receive_sequence = 0
        self._mock_sor_receipts: dict[str, dict[str, dict[str, None]]] = {}
        self._mock_krx_receipts: dict[str, dict[str, dict[str, None]]] = {}
        self._v5_al_registration: dict[str, Any] = {}
        self._v5_al_receive_sequence = 0
        self._v5_al_receive_sequence_by_code: dict[str, int] = {}

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
            shadow_target_stock_codes=(),
        )

    @staticmethod
    def _registration_shadow_targets(
        snapshot: RealtimeShadowRegistrationSnapshot,
    ) -> tuple[str, ...]:
        targets = getattr(snapshot, "shadow_target_stock_codes", None)
        return tuple(snapshot.target_stock_codes if targets is None else targets)

    def _ensure_realtime_shadow_state(self) -> None:
        native_counts = self.__dict__.setdefault(
            "_native_receive_sequence_by_code", {"NXT": {}, "KRX": {}})
        native_counts.setdefault("NXT", {})
        native_counts.setdefault("KRX", {})
        if not hasattr(self, "_screen_allocator"):
            self._screen_allocator = KiwoomScreenAllocator()
        if not hasattr(self, "_realtime_shadow_builder"):
            self._realtime_shadow_builder = RealtimeShadowBarBuilder()
        if not hasattr(self, "_realtime_receive_sequence"):
            self._realtime_receive_sequence = 0
        if "_realtime_boundary_diagnostic_counts" not in self.__dict__:
            self._realtime_boundary_diagnostic_counts = {}
        if "_realtime_boundary_diagnostic_last" not in self.__dict__:
            self._realtime_boundary_diagnostic_last = {}
        if "_realtime_boundary_diagnostic_transition" not in self.__dict__:
            self._realtime_boundary_diagnostic_transition = {}
        if not isinstance(
            getattr(self, "_realtime_shadow_registration", None),
            RealtimeShadowRegistrationSnapshot,
        ):
            self._realtime_shadow_registration = self._empty_realtime_shadow_snapshot()

    def _observe_realtime_boundary(
        self,
        stage: str,
        stock_code: object,
        *,
        reason: str = "",
        channel: str = "",
        **details: Any,
    ) -> None:
        """Keep bounded process-local realtime callback diagnostics."""

        self._ensure_realtime_shadow_state()
        code = normalize_stock_code(stock_code) or str(stock_code or "").strip()
        stage_text = str(stage or "").strip()
        if not stage_text:
            return
        counts = self._realtime_boundary_diagnostic_counts.setdefault(code, {})
        counts[stage_text] = counts.get(stage_text, 0) + 1
        key = (code, stage_text, str(channel or "").strip())
        evidence = {
            "stage": stage_text,
            "stock_code": code,
            "reason": str(reason or "").strip(),
            **details,
            "count": counts[stage_text],
        }
        self._realtime_boundary_diagnostic_last[key] = evidence
        self._realtime_boundary_diagnostic_transition[key] = (
            evidence["reason"] or stage_text
        )

    def realtime_boundary_diagnostic_snapshot(
        self,
        stock_code: object,
    ) -> dict[str, Any]:
        """Return read-only process-local realtime callback diagnostics."""

        self._ensure_realtime_shadow_state()
        code = normalize_stock_code(stock_code) or str(stock_code or "").strip()
        return {
            "stock_code": code,
            "counts": dict(
                self._realtime_boundary_diagnostic_counts.get(code, {})
            ),
            "last": tuple(
                dict(evidence)
                for key, evidence in self._realtime_boundary_diagnostic_last.items()
                if key[0] == code
            ),
        }

    def _next_realtime_receive_sequence(self) -> int:
        self._ensure_realtime_shadow_state()
        self._realtime_receive_sequence += 1
        return self._realtime_receive_sequence

    def realtime_shadow_registration_snapshot(
        self,
    ) -> RealtimeShadowRegistrationSnapshot:
        self._ensure_realtime_shadow_state()
        return self._realtime_shadow_registration

    @staticmethod
    def _empty_nxt_display_snapshot(
        *,
        connection_epoch: int = 0,
        login_session_id: str = "",
        last_error: str = "",
    ) -> NxtDisplayRegistrationSnapshot:
        return NxtDisplayRegistrationSnapshot(
            active=False,
            connection_epoch=int(connection_epoch or 0),
            login_session_id=str(login_session_id or ""),
            canonical_stock_codes=(),
            broker_code_identities=(),
            fid_list=tuple(NXT_DISPLAY_REALTIME_FIDS),
            screen_batches=(),
            last_error=str(last_error or ""),
        )

    def _ensure_nxt_display_state(self) -> None:
        state = self.__dict__
        native_counts = state.setdefault(
            "_native_receive_sequence_by_code", {"NXT": {}, "KRX": {}})
        native_counts.setdefault("NXT", {})
        native_counts.setdefault("KRX", {})
        if "_screen_allocator" not in state:
            self._screen_allocator = KiwoomScreenAllocator()
        if not isinstance(
            state.get("_nxt_display_registration"),
            NxtDisplayRegistrationSnapshot,
        ):
            self._nxt_display_registration = self._empty_nxt_display_snapshot()
        if "_nxt_display_receive_sequence" not in state:
            self._nxt_display_receive_sequence = 0

    def _next_nxt_display_receive_sequence(self) -> int:
        self._ensure_nxt_display_state()
        self._nxt_display_receive_sequence += 1
        return self._nxt_display_receive_sequence

    def nxt_display_registration_snapshot(self) -> NxtDisplayRegistrationSnapshot:
        self._ensure_nxt_display_state()
        return self._nxt_display_registration

    def sync_nxt_display_registration(self, stock_codes: object) -> dict[str, Any]:
        """Register verified NXT-eligible canonical observations as ``_NX``."""

        self._ensure_nxt_display_state()
        session = self.broker_session_snapshot()
        if not session.api_available or not session.connected or not session.login_session_id:
            self.clear_nxt_display_registration(
                remove_from_broker=False,
                reason="NXT_DISPLAY_BROKER_SESSION_NOT_READY",
            )
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "BROKER_SESSION_NOT_READY",
                "snapshot": self._nxt_display_registration,
            }
        candidates = stock_codes if isinstance(stock_codes, (list, tuple, set, frozenset)) else ()
        canonical_targets = tuple(
            sorted(
                {
                    normalize_stock_code(code)
                    for code in candidates
                    if is_broker_action_stock_code(normalize_stock_code(code))
                }
            )
        )
        broker_targets = tuple(f"{code}_NX" for code in canonical_targets)
        current = self._nxt_display_registration
        if (
            current.active
            and current.connection_epoch == session.connection_epoch
            and current.login_session_id == session.login_session_id
            and current.canonical_stock_codes == canonical_targets
            and current.fid_list == tuple(NXT_DISPLAY_REALTIME_FIDS)
        ):
            return {
                "ok": True,
                "changed": False,
                "active": True,
                "reason_code": "NXT_DISPLAY_UNCHANGED",
                "snapshot": current,
            }
        had_registration = bool(current.screen_batches)
        if had_registration:
            cleared = self.clear_nxt_display_registration(
                remove_from_broker=True,
                reason="NXT_DISPLAY_TARGET_REPLACED",
            )
            if cleared.get("ok") is not True:
                return {
                    "ok": False,
                    "changed": True,
                    "active": False,
                    "reason_code": "NXT_DISPLAY_REPLACEMENT_CLEAR_FAILED",
                    "errors": tuple(cleared.get("errors", ())),
                    "snapshot": self._nxt_display_registration,
                }
        if not broker_targets:
            return {
                "ok": True,
                "changed": had_registration,
                "active": False,
                "reason_code": "NXT_DISPLAY_EMPTY_TARGET",
                "snapshot": self._nxt_display_registration,
            }

        fid_text = ";".join(str(fid) for fid in NXT_DISPLAY_REALTIME_FIDS)
        registered: list[RealtimeShadowScreenBatch] = []
        try:
            for offset in range(0, len(broker_targets), 100):
                codes = broker_targets[offset:offset + 100]
                owner = f"nxt_display:{session.connection_epoch}:{offset // 100}"
                lease = self._screen_allocator.claim(REALTIME, owner)
                registered.append(
                    RealtimeShadowScreenBatch(
                        screen_no=lease.screen_no,
                        owner=owner,
                        stock_codes=codes,
                    )
                )
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
                if raw_result not in (None, 0, "0"):
                    raise RuntimeError(f"SetRealReg failed: {raw_result}")
            if (session.connection_epoch, session.login_session_id) != (
                self._connection_epoch, self._login_session_id
            ) or not self._connected:
                raise RuntimeError("NXT display broker session changed")
        except Exception as exc:
            self._clear_realtime_shadow_batches(registered, remove_from_broker=True)
            self._nxt_display_registration = self._empty_nxt_display_snapshot(
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                last_error=str(exc),
            )
            return {
                "ok": False,
                "changed": True,
                "active": False,
                "reason_code": "NXT_DISPLAY_REGISTRATION_FAILED",
                "error": str(exc),
                "snapshot": self._nxt_display_registration,
            }
        self._nxt_display_registration = NxtDisplayRegistrationSnapshot(
            active=True,
            connection_epoch=session.connection_epoch,
            login_session_id=session.login_session_id,
            canonical_stock_codes=canonical_targets,
            broker_code_identities=broker_targets,
            fid_list=tuple(NXT_DISPLAY_REALTIME_FIDS),
            screen_batches=tuple(registered),
            last_error="",
        )
        return {
            "ok": True,
            "changed": True,
            "active": True,
            "reason_code": "NXT_DISPLAY_REGISTER_CALL_RETURNED",
            "snapshot": self._nxt_display_registration,
        }

    def clear_nxt_display_registration(
        self,
        *,
        remove_from_broker: bool | None = None,
        reason: str = "NXT_DISPLAY_CLEARED",
    ) -> dict[str, Any]:
        self._ensure_nxt_display_state()
        current = self._nxt_display_registration
        batches = list(current.screen_batches)
        if remove_from_broker is None:
            remove_from_broker = bool(self._connected and self._control is not None)
        errors = self._clear_realtime_shadow_batches(
            batches,
            remove_from_broker=bool(remove_from_broker),
        )
        self._nxt_display_registration = self._empty_nxt_display_snapshot(
            connection_epoch=int(getattr(self, "_connection_epoch", 0) or 0),
            login_session_id=str(getattr(self, "_login_session_id", "") or ""),
            last_error="; ".join(errors),
        )
        return {
            "ok": not errors,
            "changed": bool(batches),
            "active": False,
            "reason_code": reason if not errors else "NXT_DISPLAY_CLEAR_FAILED",
            "errors": tuple(errors),
            "snapshot": self._nxt_display_registration,
        }

    @staticmethod
    def _empty_mock_orderbook_snapshot(
        *,
        connection_epoch: int = 0,
        login_session_id: str = "",
        last_error: str = "",
    ) -> MockOrderbookRegistrationSnapshot:
        return MockOrderbookRegistrationSnapshot(
            active=False,
            connection_epoch=int(connection_epoch or 0),
            login_session_id=str(login_session_id or ""),
            target_stock_codes=(),
            fid_list=tuple(REALTIME_ORDERBOOK_FIDS),
            screen_batches=(),
            last_error=str(last_error or ""),
        )

    def _ensure_mock_orderbook_state(self) -> None:
        state = self.__dict__
        if "_screen_allocator" not in state:
            self._screen_allocator = KiwoomScreenAllocator()
        if "_mock_orderbook_receive_sequence" not in state:
            self._mock_orderbook_receive_sequence = 0
        if "_mock_execution_receive_sequence" not in state:
            self._mock_execution_receive_sequence = 0
        if "_mock_registration_generation" not in state:
            self._mock_registration_generation = 0
        if "_mock_sor_trade_receive_sequence" not in state:
            self._mock_sor_trade_receive_sequence = 0
        if "_mock_sor_receipts" not in state:
            self._mock_sor_receipts = {}
        if "_mock_krx_receipts" not in state:
            self._mock_krx_receipts = {}
        if not isinstance(
            state.get("_mock_orderbook_registration"),
            MockOrderbookRegistrationSnapshot,
        ):
            self._mock_orderbook_registration = self._empty_mock_orderbook_snapshot()

    def _next_mock_orderbook_receive_sequence(self) -> int:
        self._ensure_mock_orderbook_state()
        self._mock_orderbook_receive_sequence += 1
        return self._mock_orderbook_receive_sequence

    def _record_mock_sor_receipt(self, code: str, kind: str, identity: str) -> None:
        identities = self._mock_sor_receipts.setdefault(code, {}).setdefault(kind, {})
        identities[identity] = None
        if len(identities) > 10000:
            del identities[next(iter(identities))]

    def mock_orderbook_registration_snapshot(
        self,
    ) -> MockOrderbookRegistrationSnapshot:
        self._ensure_mock_orderbook_state()
        return self._mock_orderbook_registration

    def commit_realtime_primary_bar(
        self,
        bar: RealtimeShadowBar,
        *,
        stock_name: str = "",
    ) -> dict[str, Any]:
        """Commit and publish a current-session realtime bar from the central API."""
        self._ensure_realtime_shadow_state()
        registration = self._realtime_shadow_registration
        session = self.broker_session_snapshot()
        if not isinstance(bar, RealtimeShadowBar):
            return {"ok": False, "changed": False, "reason_code": "INVALID_REALTIME_BAR"}
        if (
            not registration.active
            or bar.stock_code not in KiwoomApi._registration_shadow_targets(registration)
            or not session.connected
            or bar.connection_epoch != registration.connection_epoch
            or bar.login_session_id != registration.login_session_id
            or bar.connection_epoch != session.connection_epoch
            or bar.login_session_id != session.login_session_id
        ):
            return {
                "ok": False,
                "changed": False,
                "reason_code": "REALTIME_SESSION_OR_REGISTRATION_INVALID",
            }
        commit = commit_realtime_primary_bar_for_stock(
            bar.stock_code,
            stock_name,
            bar,
        )
        result = {
            "ok": commit.ok,
            "changed": commit.changed,
            "commit_verified": bool(commit.ok and commit.readback_verified),
            "canonical_content_hash": commit.canonical_content_hash,
            "canonical_path": commit.path,
            "commit_identity": commit.commit_identity,
            "bar_key": commit.bar_key,
            "bar_identity": commit.bar_identity,
            "bar_time": commit.bar_time,
            "trade_date": commit.trade_date,
            "error_kind": commit.error_kind,
            "error": commit.error,
            "reason_code": (
                "REALTIME_CANONICAL_COMMITTED"
                if commit.ok and commit.readback_verified
                else "REALTIME_CANONICAL_COMMIT_FAILED"
            ),
        }
        if commit.ok and commit.readback_verified and commit.changed and commit.notification is not None:
            self.bar_committed.emit(commit.notification.to_payload())
        return result

    def sync_realtime_monitoring_registration(
        self,
        stock_codes: object,
    ) -> dict[str, Any]:
        """Replace Broker realtime registrations for the current monitoring set."""

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
        normalized_targets = {
            normalize_stock_code(code)
            for code in candidates
            if normalize_stock_code(code)
        }
        unsupported_targets = tuple(
            sorted(code for code in normalized_targets if not is_broker_action_stock_code(code))
        )
        targets = tuple(
            sorted(code for code in normalized_targets if code not in unsupported_targets)
        )
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
                "unsupported_stock_codes": unsupported_targets,
                "snapshot": current,
            }

        preserved_shadow_targets = tuple(
            code
            for code in self._registration_shadow_targets(current)
            if code in targets
        )
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
                "unsupported_stock_codes": unsupported_targets,
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
            shadow_target_stock_codes=preserved_shadow_targets,
        )
        return {
            "ok": True,
            "changed": True,
            "active": True,
            "reason_code": "REGISTER_CALL_RETURNED",
            "unsupported_stock_codes": unsupported_targets,
            "snapshot": self._realtime_shadow_registration,
        }

    def sync_realtime_shadow_targets(self, stock_codes: object) -> dict[str, Any]:
        """Update the process-local Shadow eligibility set without Broker I/O."""

        self._ensure_realtime_shadow_state()
        candidates = (
            stock_codes
            if isinstance(stock_codes, (list, tuple, set, frozenset))
            else ()
        )
        normalized = {
            normalize_stock_code(code)
            for code in candidates
            if normalize_stock_code(code)
        }
        unsupported = tuple(
            sorted(code for code in normalized if not is_broker_action_stock_code(code))
        )
        registered = set(self._realtime_shadow_registration.target_stock_codes)
        targets = tuple(
            sorted(
                code
                for code in normalized
                if code not in unsupported and code in registered
            )
        )
        current = self._realtime_shadow_registration
        if self._registration_shadow_targets(current) == targets:
            return {
                "ok": True,
                "changed": False,
                "active": bool(current.active),
                "reason_code": "REALTIME_SHADOW_TARGETS_UNCHANGED",
                "unsupported_stock_codes": unsupported,
                "snapshot": current,
            }
        self._realtime_shadow_builder.reset()
        self._realtime_shadow_registration = replace(
            current,
            shadow_target_stock_codes=targets,
        )
        return {
            "ok": True,
            "changed": True,
            "active": bool(current.active),
            "reason_code": "REALTIME_SHADOW_TARGETS_UPDATED",
            "unsupported_stock_codes": unsupported,
            "snapshot": self._realtime_shadow_registration,
        }

    def current_realtime_shadow_bar(self, stock_code: object) -> dict[str, Any] | None:
        """Expose the current process-local forming minute without committing it."""
        self._ensure_realtime_shadow_state()
        bar = self._realtime_shadow_builder.current_bar(stock_code)
        if bar is None:
            return None
        payload = bar.to_payload()
        return {
            "time": str(payload.get("bar_time") or ""),
            "open": payload.get("open"),
            "high": payload.get("high"),
            "low": payload.get("low"),
            "close": payload.get("close"),
            "volume": payload.get("volume"),
            "is_complete": False,
        }

    def sync_realtime_shadow_registration(
        self,
        stock_codes: object,
    ) -> dict[str, Any]:
        """Compatibility wrapper for callers that still request combined sync."""

        candidates = (
            stock_codes
            if isinstance(stock_codes, (list, tuple, set, frozenset))
            else ()
        )
        normalized = {
            normalize_stock_code(code)
            for code in candidates
            if normalize_stock_code(code)
        }
        unsupported = tuple(
            sorted(code for code in normalized if not is_broker_action_stock_code(code))
        )
        if unsupported:
            return {
                "ok": False,
                "changed": False,
                "active": bool(self._realtime_shadow_registration.active),
                "reason_code": "BROKER_ALPHANUMERIC_STOCK_CODE_UNCONFIRMED",
                "unsupported_stock_codes": unsupported,
                "snapshot": self._realtime_shadow_registration,
            }
        monitoring = self.sync_realtime_monitoring_registration(tuple(sorted(normalized)))
        if monitoring.get("ok") is not True:
            return monitoring
        shadow = self.sync_realtime_shadow_targets(tuple(sorted(normalized)))
        result = dict(monitoring)
        result["snapshot"] = shadow.get("snapshot", monitoring.get("snapshot"))
        return result

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

    def sync_mock_orderbook_registration(
        self, stock_codes: object, *, sor_stock_codes: object = (),
    ) -> dict[str, Any]:
        """Synchronize Mock quotes, using broker _AL identity for SOR stocks."""

        self._ensure_mock_orderbook_state()
        session = self.broker_session_snapshot()
        if not (
            session.api_available
            and session.connected
            and session.login_session_id
        ):
            self.clear_mock_orderbook_registration(
                remove_from_broker=False,
                reason="MOCK_ORDERBOOK_BROKER_SESSION_NOT_READY",
            )
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "BROKER_SESSION_NOT_READY",
                "snapshot": self._mock_orderbook_registration,
            }

        candidates = (
            stock_codes
            if isinstance(stock_codes, (list, tuple, set, frozenset))
            else ()
        )
        normalized_targets = {
            normalize_stock_code(code)
            for code in candidates
            if normalize_stock_code(code)
        }
        unsupported_targets = tuple(
            sorted(
                code
                for code in normalized_targets
                if not is_broker_action_stock_code(code)
            )
        )
        targets = tuple(
            sorted(
                code
                for code in normalized_targets
                if code not in unsupported_targets
            )
        )
        sor_candidates = (
            sor_stock_codes if isinstance(sor_stock_codes, (list, tuple, set, frozenset)) else ()
        )
        sor_targets = tuple(sorted(
            {normalize_stock_code(code) for code in sor_candidates} & set(targets)
        ))
        broker_targets = tuple(
            f"{code}_AL" if code in sor_targets else code for code in targets
        )
        mock_fids = tuple(dict.fromkeys((*REALTIME_ORDERBOOK_FIDS, *REALTIME_SHADOW_FIDS)))
        current = self._mock_orderbook_registration
        if (
            current.active
            and current.connection_epoch == session.connection_epoch
            and current.login_session_id == session.login_session_id
            and current.target_stock_codes == targets
            and current.sor_stock_codes == sor_targets
            and current.fid_list == mock_fids
        ):
            return {
                "ok": True,
                "changed": False,
                "active": True,
                "reason_code": "MOCK_ORDERBOOK_UNCHANGED",
                "unsupported_stock_codes": unsupported_targets,
                "snapshot": current,
            }

        had_registration = bool(current.screen_batches)
        if had_registration:
            clear_result = self.clear_mock_orderbook_registration(
                remove_from_broker=True,
                reason="MOCK_ORDERBOOK_TARGET_REPLACED",
            )
            if clear_result.get("ok") is not True:
                return {
                    "ok": False,
                    "changed": True,
                    "active": False,
                    "reason_code": "MOCK_ORDERBOOK_REPLACEMENT_CLEAR_FAILED",
                    "errors": tuple(clear_result.get("errors", ())),
                    "snapshot": self._mock_orderbook_registration,
                }
        if not targets:
            return {
                "ok": True,
                "changed": had_registration,
                "active": False,
                "reason_code": "MOCK_ORDERBOOK_EMPTY_TARGET",
                "unsupported_stock_codes": unsupported_targets,
                "snapshot": self._mock_orderbook_registration,
            }

        fid_text = ";".join(str(fid) for fid in mock_fids)
        registered: list[MockOrderbookScreenBatch] = []
        try:
            for offset in range(0, len(broker_targets), 100):
                codes = broker_targets[offset:offset + 100]
                owner = f"mock_orderbook:{session.connection_epoch}:{offset // 100}"
                lease = self._screen_allocator.claim(REALTIME, owner)
                batch = MockOrderbookScreenBatch(
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
                registered[-1] = MockOrderbookScreenBatch(
                    screen_no=lease.screen_no,
                    owner=owner,
                    stock_codes=codes,
                    raw_registration_result=raw_result,
                )
        except Exception as exc:
            self._clear_mock_orderbook_batches(
                registered,
                remove_from_broker=True,
            )
            self._mock_orderbook_registration = self._empty_mock_orderbook_snapshot(
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                last_error=str(exc),
            )
            return {
                "ok": False,
                "changed": True,
                "active": False,
                "reason_code": "MOCK_ORDERBOOK_REGISTRATION_FAILED",
                "error": str(exc),
                "snapshot": self._mock_orderbook_registration,
            }

        self._mock_registration_generation += 1
        self._mock_orderbook_registration = MockOrderbookRegistrationSnapshot(
            active=True,
            connection_epoch=session.connection_epoch,
            login_session_id=session.login_session_id,
            target_stock_codes=targets,
            fid_list=mock_fids,
            screen_batches=tuple(registered),
            last_error="",
            registration_id=f"{session.connection_epoch}:{self._mock_registration_generation}",
            sor_stock_codes=sor_targets,
        )
        return {
            "ok": True,
            "changed": True,
            "active": True,
            "reason_code": "MOCK_ORDERBOOK_REGISTER_CALL_RETURNED",
            "unsupported_stock_codes": unsupported_targets,
            "snapshot": self._mock_orderbook_registration,
        }

    def clear_mock_orderbook_registration(
        self,
        *,
        remove_from_broker: bool | None = None,
        reason: str = "MOCK_ORDERBOOK_CLEARED",
    ) -> dict[str, Any]:
        self._ensure_mock_orderbook_state()
        self._mock_sor_receipts.clear()
        self._mock_krx_receipts.clear()
        current = self._mock_orderbook_registration
        batches = list(current.screen_batches)
        if remove_from_broker is None:
            remove_from_broker = bool(self._connected and self._control is not None)
        errors = self._clear_mock_orderbook_batches(
            batches,
            remove_from_broker=bool(remove_from_broker),
        )
        self._mock_orderbook_registration = self._empty_mock_orderbook_snapshot(
            connection_epoch=int(getattr(self, "_connection_epoch", 0) or 0),
            login_session_id=str(getattr(self, "_login_session_id", "") or ""),
            last_error="; ".join(errors),
        )
        return {
            "ok": not errors,
            "changed": bool(batches),
            "active": False,
            "reason_code": reason if not errors else "MOCK_ORDERBOOK_CLEAR_FAILED",
            "errors": tuple(errors),
            "snapshot": self._mock_orderbook_registration,
        }

    def _clear_mock_orderbook_batches(
        self,
        batches: list[MockOrderbookScreenBatch],
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
        evidence_store = getattr(self, "_send_order_request_evidence", None)
        if isinstance(evidence_store, dict):
            evidence_store.clear()
        else:
            self._send_order_request_evidence = {}
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
        self.clear_v5_al_realtime_targets(
            remove_from_broker=False, reason="V5_AL_SESSION_INVALIDATED",
        )
        reset_shadow = getattr(self, "clear_realtime_shadow_registration", None)
        if callable(reset_shadow):
            reset_shadow(
                remove_from_broker=False,
                reason="REALTIME_SHADOW_SESSION_INVALIDATED",
            )
        reset_nxt_display = getattr(self, "clear_nxt_display_registration", None)
        if callable(reset_nxt_display):
            reset_nxt_display(
                remove_from_broker=False,
                reason="NXT_DISPLAY_SESSION_INVALIDATED",
            )
        reset_mock_orderbook = getattr(
            self,
            "clear_mock_orderbook_registration",
            None,
        )
        if callable(reset_mock_orderbook):
            reset_mock_orderbook(
                remove_from_broker=False,
                reason="MOCK_ORDERBOOK_SESSION_INVALIDATED",
            )
        evidence_store = getattr(self, "_send_order_request_evidence", None)
        if isinstance(evidence_store, dict):
            evidence_store.clear()
        else:
            self._send_order_request_evidence = {}
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
        self._record_tr_governor_stale()
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
        if pending_type == "period_candles":
            callback = pending.get("callback")
            payload = self._stale_broker_session_error(request_name, pending)
            payload.update(
                {
                    "type": "period_candles",
                    "code": pending.get("code", ""),
                    "name": pending.get("name", ""),
                    "timeframe_key": pending.get("timeframe_key", ""),
                }
            )
            self._finish_callback(callback if callable(callback) else None, payload)
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
            return
        if pending_type == "initial_market_snapshot":
            callback = pending.get("callback")
            payload = self._stale_broker_session_error(request_name, pending)
            payload.update(
                {
                    "type": "initial_market_snapshot",
                    "stock_codes": list(pending.get("stock_codes") or ()),
                    "rows": [],
                }
            )
            self._finish_callback(callback if callable(callback) else None, payload)
            return
        if pending_type == "stock_ranking_snapshot":
            callback = pending.get("callback")
            payload = self._stale_broker_session_error(request_name, pending)
            payload.update(
                {
                    "type": "stock_ranking_snapshot",
                    "source": pending.get("source", ""),
                    "trcode": pending.get("trcode", ""),
                    "rows": [],
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
        if not hasattr(self, "_tr_governor_total_enqueued"):
            self._tr_governor_total_enqueued = 0
        if not hasattr(self, "_tr_governor_total_dispatched"):
            self._tr_governor_total_dispatched = 0
        if not hasattr(self, "_tr_governor_last_rqname"):
            self._tr_governor_last_rqname = ""
        if not hasattr(self, "_tr_governor_last_trcode"):
            self._tr_governor_last_trcode = ""
        if not hasattr(self, "_tr_governor_dispatch_history"):
            self._tr_governor_dispatch_history = deque(maxlen=4096)
        if not hasattr(self, "_candle_tr_dispatch_history"):
            self._candle_tr_dispatch_history = deque()
        if not hasattr(self, "_tr_governor_last_queue_wait_ms"):
            self._tr_governor_last_queue_wait_ms = 0.0
        if not hasattr(self, "_tr_governor_max_queue_wait_ms"):
            self._tr_governor_max_queue_wait_ms = 0.0
        if not hasattr(self, "_tr_governor_timeout_count"):
            self._tr_governor_timeout_count = 0
        if not hasattr(self, "_tr_governor_stale_count"):
            self._tr_governor_stale_count = 0
        if not hasattr(self, "_tr_governor_error_count"):
            self._tr_governor_error_count = 0
        if not hasattr(self, "_tr_governor_last_error_reason"):
            self._tr_governor_last_error_reason = ""

    def _prune_tr_governor_dispatch_history(self, now: float) -> None:
        history = self._tr_governor_dispatch_history
        cutoff = float(now) - 60.0
        while history and history[0] < cutoff:
            history.popleft()

    def _record_tr_governor_timeout(self) -> None:
        self._ensure_tr_governor_state()
        self._tr_governor_timeout_count += 1

    def _record_tr_governor_stale(self) -> None:
        self._ensure_tr_governor_state()
        self._tr_governor_stale_count += 1

    def _record_tr_governor_error(self, reason: object) -> None:
        self._ensure_tr_governor_state()
        self._tr_governor_error_count += 1
        self._tr_governor_last_error_reason = str(reason or "").strip()

    def tr_governor_metrics_snapshot(self) -> TrGovernorMetricsSnapshot:
        self._ensure_tr_governor_state()
        now = monotonic()
        self._prune_tr_governor_dispatch_history(now)
        return TrGovernorMetricsSnapshot(
            total_enqueued=self._tr_governor_total_enqueued,
            total_dispatched=self._tr_governor_total_dispatched,
            current_queue_depth=len(self._tr_request_queue),
            last_rqname=self._tr_governor_last_rqname,
            last_trcode=self._tr_governor_last_trcode,
            last_dispatch_monotonic=self._tr_last_dispatch_monotonic_ms,
            dispatch_count_last_60s=len(self._tr_governor_dispatch_history),
            last_queue_wait_ms=self._tr_governor_last_queue_wait_ms,
            max_queue_wait_ms=self._tr_governor_max_queue_wait_ms,
            timeout_count=self._tr_governor_timeout_count,
            stale_count=self._tr_governor_stale_count,
            error_count=self._tr_governor_error_count,
            last_error_reason=self._tr_governor_last_error_reason,
        )

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
        dispatch_kind: str = "COMM_RQ_DATA",
        dispatch_payload: tuple[object, ...] = (),
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
            "dispatch_kind": str(dispatch_kind or "COMM_RQ_DATA"),
            "dispatch_payload": tuple(dispatch_payload),
            "metrics_enqueued_monotonic": monotonic(),
        }
        self._tr_request_queue.append(request)
        self._tr_governor_total_enqueued += 1
        return self._drain_tr_governor()

    def _drain_tr_governor(self) -> dict[str, Any]:
        self._ensure_tr_governor_state()
        if self._tr_governor_dispatching:
            return {"ok": True, "status": "QUEUED"}
        if not self._tr_request_queue:
            return {"ok": True, "status": "IDLE"}

        rejected = None
        original_remaining = len(self._tr_request_queue)
        for _ in range(original_remaining):
            request = self._tr_request_queue[0]
            if str(request.get("trcode") or "").lower() != "opt10080":
                break
            now = monotonic()
            history = self._candle_tr_dispatch_history
            while history and history[0] <= now - 3600:
                history.popleft()
            if (len(history) < 600 and
                    sum(stamp > now - 60 for stamp in history) < 40):
                break
            self._tr_request_queue.popleft()
            original_remaining -= 1
            self._record_tr_governor_error("CANDLE_TR_BUDGET_EXCEEDED")
            on_failed = request.get("on_failed")
            self._tr_governor_dispatching = True
            try:
                if callable(on_failed):
                    rejected = on_failed(-1, "CANDLE_TR_BUDGET_EXCEEDED")
                else:
                    rqname = str(request.get("rqname") or "")
                    pending = self._pending_tr.pop(rqname, None)
                    self._release_pending_tr_screen(rqname, pending)
                    rejected = {"ok": False, "status": "REQUEST_FAILED",
                                "rqname": rqname, "error": "CANDLE_TR_BUDGET_EXCEEDED"}
            finally:
                self._tr_governor_dispatching = False
        if (rejected is not None and original_remaining == 0 and self._tr_request_queue
                and str(self._tr_request_queue[0].get("trcode") or "").lower()
                == "opt10080"):
            self._schedule_tr_governor_dispatch(self.TR_GOVERNOR_MIN_INTERVAL_MS)
            return rejected
        if not self._tr_request_queue:
            return rejected or {"ok": True, "status": "IDLE"}

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
            dispatch_kind = str(request.get("dispatch_kind") or "COMM_RQ_DATA")
            if dispatch_kind == "COMM_KW_RQ_DATA":
                stock_codes, stock_count, type_flag = request.get(
                    "dispatch_payload", ("", 0, 0)
                )
                result = self._control.dynamicCall(
                    "CommKwRqData(QString, bool, int, int, QString, QString)",
                    str(stock_codes),
                    False,
                    int(stock_count),
                    int(type_flag),
                    rqname,
                    str(request.get("screen_no") or ""),
                )
            else:
                for field, value in request.get("inputs", ()):
                    self._control.dynamicCall(
                        "SetInputValue(QString, QString)",
                        str(field),
                        str(value),
                    )
                if str(request.get("trcode") or "").lower() == "opt10080":
                    self._candle_tr_dispatch_history.append(monotonic())
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

        dispatched_at = monotonic()
        raw_enqueued_at = request.get("metrics_enqueued_monotonic")
        enqueued_at = (
            dispatched_at
            if raw_enqueued_at is None
            else float(raw_enqueued_at)
        )
        queue_wait_ms = max(0.0, (dispatched_at - enqueued_at) * 1000.0)
        self._tr_governor_total_dispatched += 1
        self._tr_governor_last_rqname = rqname
        self._tr_governor_last_trcode = str(request.get("trcode") or "")
        self._tr_governor_last_queue_wait_ms = queue_wait_ms
        self._tr_governor_max_queue_wait_ms = max(
            self._tr_governor_max_queue_wait_ms,
            queue_wait_ms,
        )
        self._tr_governor_dispatch_history.append(dispatched_at)
        self._prune_tr_governor_dispatch_history(dispatched_at)

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

        self._record_tr_governor_error(error)
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
        if bool(getattr(self, "_login_requested", False)):
            return {
                "ok": True,
                "status": "login_in_progress",
                "connected": False,
                "message": "login already requested",
            }
        if bool(getattr(self, "_connected", False)) and str(
            getattr(self, "_login_session_id", "") or ""
        ):
            return {
                "ok": True,
                "status": "already_connected",
                "connected": True,
                "message": "login already completed",
            }
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

        if not accounts:
            session_id = str(self.login_session_id() or "").strip()
            if getattr(self, "_empty_account_diagnostic_session_id", None) != session_id:
                diagnostic: dict[str, object] = {
                    "accno_present": bool(str(raw_value or "").strip()),
                    "accno_text_length": len(str(raw_value or "")),
                    "parsed_account_count": 0,
                }
                for tag, key in (
                    ("ACCOUNT_CNT", "reported_account_count"),
                    ("USER_ID", "user_id_present"),
                    ("KEY_BSECGB", "keyboard_security_state"),
                    ("FIREW_SECGB", "firewall_state"),
                    ("GetServerGubun", "server_gubun"),
                ):
                    try:
                        value = self._control.dynamicCall("GetLoginInfo(QString)", tag)
                    except Exception as exc:
                        diagnostic[f"{key}_error"] = type(exc).__name__
                        continue
                    if tag == "USER_ID":
                        diagnostic[key] = bool(str(value or "").strip())
                    else:
                        diagnostic[key] = str(value or "").strip()
                append_production_event(
                    "RUNTIME_WARNING",
                    severity="WARNING",
                    result="BLOCKED",
                    source="kiwoom_api.KiwoomApi.account_numbers",
                    template_args={"target": "Kiwoom account discovery"},
                    target_type="BROKER_CONNECTION",
                    target_id="kiwoom_openapi",
                    reason_code="LOGIN_ACCOUNT_LIST_EMPTY",
                    details=diagnostic,
                )
                self._empty_account_diagnostic_session_id = session_id
        return accounts

    @staticmethod
    def _normalize_master_code_list(raw_value: object) -> list[str]:
        codes: list[str] = []
        seen: set[str] = set()
        for item in str(raw_value or "").split(";"):
            code = normalize_stock_code(item)
            if not code or code in seen:
                continue
            codes.append(code)
            seen.add(code)
        return codes

    def _master_call_ready(self) -> tuple[bool, str]:
        readiness = self.broker_readiness_snapshot()
        return bool(readiness.broker_request_ready), str(readiness.reason or "")

    def get_market_stock_codes(self, market_code: str) -> dict[str, Any]:
        """Read one market code list through the existing connected OCX."""
        clean_market = str(market_code or "").strip()
        if clean_market not in {market_id for _name, market_id in self.MASTER_STOCK_MARKETS}:
            return {
                "ok": False,
                "value": [],
                "error": "unsupported market code",
                "reason": "UNSUPPORTED_MARKET",
            }
        ready, reason = self._master_call_ready()
        if not ready:
            return {
                "ok": False,
                "value": [],
                "error": "broker request is not ready",
                "reason": reason or "BROKER_REQUEST_NOT_READY",
            }
        try:
            raw_value = self._control.dynamicCall(
                "GetCodeListByMarket(QString)",
                clean_market,
            )
        except Exception as exc:
            return {
                "ok": False,
                "value": [],
                "error": str(exc),
                "reason": "MASTER_MARKET_CALL_FAILED",
            }
        codes = self._normalize_master_code_list(raw_value)
        diagnostic = build_master_code_diagnostic_projection(raw_value)
        if not codes:
            return {
                "ok": False,
                "value": [],
                "diagnostic": diagnostic,
                "error": "market code list is empty",
                "reason": "MASTER_MARKET_EMPTY",
            }
        return {
            "ok": True,
            "value": codes,
            "diagnostic": diagnostic,
            "error": "",
            "reason": "OK",
        }

    def get_master_stock_name(self, stock_code: str) -> dict[str, Any]:
        """Read one stock name through the existing connected OCX."""
        clean_code = normalize_stock_code(stock_code)
        if not is_valid_stock_code(clean_code):
            return {
                "ok": False,
                "value": "",
                "error": "stock code is empty",
                "reason": "INVALID_STOCK_CODE",
            }
        ready, reason = self._master_call_ready()
        if not ready:
            return {
                "ok": False,
                "value": "",
                "error": "broker request is not ready",
                "reason": reason or "BROKER_REQUEST_NOT_READY",
            }
        try:
            raw_value = self._control.dynamicCall(
                "GetMasterCodeName(QString)",
                clean_code,
            )
        except Exception as exc:
            return {
                "ok": False,
                "value": "",
                "error": str(exc),
                "reason": "MASTER_NAME_CALL_FAILED",
            }
        name = _decode_legacy_koa_text(raw_value)
        if not name:
            return {
                "ok": False,
                "value": "",
                "error": "master stock name is empty",
                "reason": "MASTER_NAME_EMPTY",
            }
        return {"ok": True, "value": name, "error": "", "reason": "OK"}

    def _get_master_stock_text(
        self,
        stock_code: str,
        *,
        signature: str,
        failure_reason: str,
    ) -> dict[str, Any]:
        clean_code = normalize_stock_code(stock_code)
        if not is_valid_stock_code(clean_code):
            return {
                "ok": False,
                "value": "",
                "error": "stock code is invalid",
                "reason": "INVALID_STOCK_CODE",
            }
        ready, reason = self._master_call_ready()
        if not ready:
            return {
                "ok": False,
                "value": "",
                "error": "broker request is not ready",
                "reason": reason or "BROKER_REQUEST_NOT_READY",
            }
        try:
            raw_value = self._control.dynamicCall(signature, clean_code)
        except Exception as exc:
            return {
                "ok": False,
                "value": "",
                "error": str(exc),
                "reason": failure_reason,
            }
        value = _decode_legacy_koa_text(raw_value)
        return {
            "ok": True,
            "value": value,
            "error": "",
            "reason": "OK" if value else "EMPTY",
        }

    def get_master_stock_state(self, stock_code: str) -> dict[str, Any]:
        """Read the official master stock-state text without issuing a TR."""
        return self._get_master_stock_text(
            stock_code,
            signature="GetMasterStockState(QString)",
            failure_reason="MASTER_STOCK_STATE_CALL_FAILED",
        )

    def get_master_construction(self, stock_code: str) -> dict[str, Any]:
        """Read the official master construction text without issuing a TR."""
        return self._get_master_stock_text(
            stock_code,
            signature="GetMasterConstruction(QString)",
            failure_reason="MASTER_CONSTRUCTION_CALL_FAILED",
        )

    def get_master_stock_info(self, stock_code: str) -> dict[str, Any]:
        """Read official market/product classification evidence without a TR."""
        clean_code = normalize_stock_code(stock_code)
        if not is_valid_stock_code(clean_code):
            return {
                "ok": False,
                "value": "",
                "error": "stock code is invalid",
                "reason": "INVALID_STOCK_CODE",
            }
        ready, reason = self._master_call_ready()
        if not ready:
            return {
                "ok": False,
                "value": "",
                "error": "broker request is not ready",
                "reason": reason or "BROKER_REQUEST_NOT_READY",
            }
        try:
            raw_value = self._control.dynamicCall(
                "KOA_Functions(QString, QString)",
                "GetMasterStockInfo",
                clean_code,
            )
        except Exception as exc:
            return {
                "ok": False,
                "value": "",
                "error": str(exc),
                "reason": "MASTER_STOCK_INFO_CALL_FAILED",
            }
        value = _decode_legacy_koa_text(raw_value)
        return {
            "ok": True,
            "value": value,
            "error": "",
            "reason": "OK" if value else "EMPTY",
        }

    def get_master_stock_market_kind(self, stock_code: str) -> dict[str, Any]:
        """Read the official per-stock market kind from the connected OCX."""
        clean_code = normalize_stock_code(stock_code)
        if not is_valid_stock_code(clean_code):
            return {
                "ok": False,
                "value": "",
                "error": "stock code is invalid",
                "reason": "INVALID_STOCK_CODE",
            }
        ready, reason = self._master_call_ready()
        if not ready:
            return {
                "ok": False,
                "value": "",
                "error": "broker request is not ready",
                "reason": reason or "BROKER_REQUEST_NOT_READY",
            }
        try:
            raw_value = self._control.dynamicCall(
                "KOA_Functions(QString, QString)",
                "GetStockMarketKind",
                clean_code,
            )
        except Exception as exc:
            return {
                "ok": False,
                "value": "",
                "error": str(exc),
                "reason": "MASTER_STOCK_MARKET_KIND_CALL_FAILED",
            }
        value = str(raw_value or "").strip()
        return {
            "ok": bool(value),
            "value": value,
            "error": "" if value else "stock market kind is empty",
            "reason": "OK" if value else "MASTER_STOCK_MARKET_KIND_EMPTY",
        }

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

    def register_send_order_reconciliation_context(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Register queue/execution identity before one SendOrder call."""
        data = dict(payload) if isinstance(payload, dict) else {}
        rqname = str(data.get("rqname") or "").strip()
        screen_no = str(data.get("screen_no") or "").strip()
        order_id = str(data.get("order_id") or "").strip()
        dispatch_claim_id = str(data.get("dispatch_claim_id") or "").strip()
        attempt_id = str(data.get("send_order_attempt_id") or "").strip()
        session_id = str(getattr(self, "_login_session_id", "") or "").strip()
        connection_epoch = getattr(self, "_connection_epoch", None)

        missing = [
            field
            for field, value in (
                ("rqname", rqname),
                ("screen_no", screen_no),
                ("order_id", order_id),
                ("dispatch_claim_id", dispatch_claim_id),
                ("send_order_attempt_id", attempt_id),
            )
            if not value
        ]
        if missing:
            return {
                "registered": False,
                "reason": "SEND_ORDER_RECONCILIATION_IDENTITY_INCOMPLETE",
                "missing_fields": missing,
            }
        if not session_id or not self.is_connected():
            return {
                "registered": False,
                "reason": "SEND_ORDER_RECONCILIATION_SESSION_UNAVAILABLE",
            }

        evidence_store = getattr(self, "_send_order_request_evidence", None)
        if not isinstance(evidence_store, dict):
            evidence_store = {}
            self._send_order_request_evidence = evidence_store
        key = (screen_no, rqname)
        existing = evidence_store.get(key)
        if isinstance(existing, dict):
            existing_identity = (
                str(existing.get("order_id") or "").strip(),
                str(existing.get("dispatch_claim_id") or "").strip(),
                str(existing.get("send_order_attempt_id") or "").strip(),
                str(existing.get("login_session_id") or "").strip(),
                existing.get("connection_epoch"),
            )
            new_identity = (
                order_id,
                dispatch_claim_id,
                attempt_id,
                session_id,
                connection_epoch,
            )
            if any(existing_identity) and existing_identity != new_identity:
                return {
                    "registered": False,
                    "reason": "SEND_ORDER_RECONCILIATION_CONTEXT_CONFLICT",
                }

        evidence = dict(existing) if isinstance(existing, dict) else {}
        evidence.update(
            {
                "reconciliation_context_registered": True,
                "reconciliation_context_source": str(
                    data.get("source") or "claimed_send_order_executor"
                ).strip(),
                "rqname": rqname,
                "screen_no": screen_no,
                "order_id": order_id,
                "dispatch_claim_id": dispatch_claim_id,
                "send_order_attempt_id": attempt_id,
                "execution_id": str(data.get("execution_id") or "").strip(),
                "signal_id": str(data.get("signal_id") or "").strip(),
                "market_route": str(data.get("market_route") or "").strip().upper(),
                "order_type": data.get("order_type"),
                "login_session_id": session_id,
                "connection_epoch": connection_epoch,
                "reconciliation_context_registered_at": (
                    datetime.now().astimezone().isoformat(
                        sep=" ",
                        timespec="milliseconds",
                    )
                ),
            }
        )
        evidence_store[key] = evidence
        while len(evidence_store) > 512:
            evidence_store.pop(next(iter(evidence_store)))
        return {
            "registered": True,
            "rqname": rqname,
            "screen_no": screen_no,
            "order_id": order_id,
            "dispatch_claim_id": dispatch_claim_id,
            "send_order_attempt_id": attempt_id,
            "login_session_id": session_id,
            "connection_epoch": connection_epoch,
        }

    def send_order(
        self,
        rqname: str,
        screen_no: str,
        account_no: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str,
        original_order_no: str,
    ) -> Any:
        """Call Kiwoom OpenAPI SendOrder once in official RQName/ScreenNo order."""
        rqname_text = str(rqname or "").strip()
        screen_no_text = str(screen_no or "").strip()
        if not rqname_text:
            raise ValueError("send order rqname is required")
        clean_code = normalize_stock_code(code)
        if not clean_code:
            raise ValueError("stock code is required")
        if not is_broker_action_stock_code(clean_code):
            raise ValueError("BROKER_ALPHANUMERIC_STOCK_CODE_UNCONFIRMED")
        if not self.is_available():
            raise RuntimeError(self._unavailable_reason or "kiwoom api unavailable")
        if not self.is_connected():
            raise RuntimeError("kiwoom api is not connected")

        evidence_store = getattr(self, "_send_order_request_evidence", None)
        if not isinstance(evidence_store, dict):
            evidence_store = {}
            self._send_order_request_evidence = evidence_store
        request_key = (screen_no_text, rqname_text)
        existing_evidence = evidence_store.get(request_key)
        request_evidence = (
            dict(existing_evidence)
            if isinstance(existing_evidence, dict)
            else {}
        )
        request_evidence.update({
            "source": "kiwoom_send_order",
            "rqname": rqname_text,
            "screen_no": screen_no_text,
            "account_no": str(account_no or "").strip(),
            "order_type": int(order_type),
            "code": clean_code,
            "quantity": int(quantity),
            "price": int(price),
            "hoga": str(hoga or "").strip(),
            "original_order_no": str(original_order_no or "").strip(),
            "login_session_id": str(getattr(self, "_login_session_id", "") or "").strip(),
            "connection_epoch": getattr(self, "_connection_epoch", None),
            "requested_at": datetime.now().astimezone().isoformat(
                sep=" ", timespec="milliseconds"
            ),
        })
        evidence_store[request_key] = request_evidence
        while len(evidence_store) > 512:
            evidence_store.pop(next(iter(evidence_store)))

        if os.environ.get("KIWOOM_SOR_CAPTURE_NO_ORDERS") == "1":
            request_evidence["send_order_blocked_reason"] = "KIWOOM_SOR_CAPTURE_NO_ORDERS"
            raise RuntimeError("KIWOOM_SOR_CAPTURE_NO_ORDERS")

        try:
            raw_result = self._control.dynamicCall(
                "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
                rqname_text,
                screen_no_text,
                str(account_no or ""),
                int(order_type),
                clean_code,
                int(quantity),
                int(price),
                str(hoga or ""),
                str(original_order_no or ""),
            )
        except Exception as exc:
            request_evidence["send_order_exception"] = str(exc)
            request_evidence["send_order_returned_at"] = (
                datetime.now().astimezone().isoformat(
                    sep=" ", timespec="milliseconds"
                )
            )
            raise
        request_evidence["send_order_raw_result"] = raw_result
        request_evidence["send_order_returned_at"] = (
            datetime.now().astimezone().isoformat(
                sep=" ", timespec="milliseconds"
            )
        )
        return raw_result

    def request_initial_market_snapshot(
        self,
        stock_codes: object,
        *,
        callback: InitialMarketSnapshotCallback | None = None,
    ) -> dict[str, Any]:
        """Enqueue one official OPTKWFID snapshot batch per 100 current Stocks."""

        raw_codes = [stock_codes] if isinstance(stock_codes, str) else list(stock_codes or ())
        targets = tuple(
            sorted(
                {
                    code
                    for item in raw_codes
                    if (code := normalize_stock_code(item))
                    and is_valid_stock_code(code)
                }
            )
        )
        if not targets:
            return self._finish_callback(
                callback,
                {
                    "ok": True,
                    "status": "EMPTY",
                    "type": "initial_market_snapshot",
                    "target_stock_codes": [],
                    "batch_count": 0,
                },
            )
        if not self.is_available() or not self.is_connected():
            result = self._broker_request_not_ready_error()
            result.update(type="initial_market_snapshot", target_stock_codes=list(targets))
            return self._finish_callback(callback, result)
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result.update(type="initial_market_snapshot", target_stock_codes=list(targets))
            return self._finish_callback(callback, result)

        batch_results: list[dict[str, Any]] = []
        for offset in range(0, len(targets), self.INITIAL_MARKET_SNAPSHOT_BATCH_SIZE):
            batch = targets[offset : offset + self.INITIAL_MARKET_SNAPSHOT_BATCH_SIZE]
            batch_index = offset // self.INITIAL_MARKET_SNAPSHOT_BATCH_SIZE
            rqname = (
                f"OPTKWFID_INITIAL_{batch_index}_"
                f"{datetime.now().strftime('%H%M%S%f')}"
            )
            screen_no, claim_error = self._claim_tr_screen(
                purpose=MARKET_TR,
                rqname=rqname,
                screen_no=None,
                callback=callback,
                failure_payload={
                    "type": "initial_market_snapshot",
                    "stock_codes": list(batch),
                    "rows": [],
                },
            )
            if screen_no is None:
                batch_results.append(claim_error or {"ok": False, "rqname": rqname})
                continue
            pending = {
                "type": "initial_market_snapshot",
                "stock_codes": batch,
                "screen_no": screen_no,
                "callback": callback,
                **request_identity,
            }
            self._pending_tr[rqname] = pending

            def start_timeout(_result: Any, request_name: str = rqname) -> None:
                QTimer.singleShot(
                    self.INITIAL_MARKET_SNAPSHOT_TIMEOUT_MS,
                    lambda name=request_name: self._expire_initial_market_snapshot(name),
                )

            def fail_request(
                result: Any,
                error: str,
                request_name: str = rqname,
            ) -> dict[str, Any]:
                failed = self._pending_tr.pop(request_name, None)
                self._release_pending_tr_screen(request_name, failed)
                payload = {
                    "ok": False,
                    "type": "initial_market_snapshot",
                    "stock_codes": list((failed or {}).get("stock_codes") or ()),
                    "rqname": request_name,
                    "trcode": "OPTKWFID",
                    "rows": [],
                    "result": result,
                    "error": error,
                }
                return self._finish_callback(callback, payload)

            batch_results.append(
                self._submit_governed_tr_request(
                    rqname=rqname,
                    trcode="OPTKWFID",
                    prev_next=0,
                    screen_no=screen_no,
                    inputs=(),
                    pending=pending,
                    on_dispatched=start_timeout,
                    on_failed=fail_request,
                    dispatch_kind="COMM_KW_RQ_DATA",
                    dispatch_payload=(";".join(batch), len(batch), 0),
                )
            )

        return {
            "ok": bool(batch_results) and all(item.get("ok") for item in batch_results),
            "status": "ENQUEUED",
            "type": "initial_market_snapshot",
            "target_stock_codes": list(targets),
            "batch_count": len(batch_results),
            "batches": batch_results,
        }

    def _expire_initial_market_snapshot(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "initial_market_snapshot":
            return
        self._record_tr_governor_timeout()
        self._release_pending_tr_screen(str(rqname), pending)
        callback = pending.get("callback")
        self._finish_callback(
            callback if callable(callback) else None,
            {
                "ok": False,
                "type": "initial_market_snapshot",
                "stock_codes": list(pending.get("stock_codes") or ()),
                "rqname": str(rqname),
                "trcode": "OPTKWFID",
                "rows": [],
                "error": "initial market snapshot request timed out",
                "error_kind": "TIMEOUT",
            },
        )

    def request_stock_ranking_snapshot(
        self,
        source: str,
        *,
        callback: StockRankingSnapshotCallback | None = None,
    ) -> dict[str, Any]:
        """Enqueue one governed KRX KOSPI+KOSDAQ ranking snapshot."""

        clean_source = str(source or "").strip().upper()
        spec = self.STOCK_RANKING_SPECS.get(clean_source)
        if not isinstance(spec, dict):
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "type": "stock_ranking_snapshot",
                    "source": clean_source,
                    "rows": [],
                    "error": "unsupported stock ranking source",
                    "error_kind": "UNSUPPORTED_STOCK_RANKING_SOURCE",
                },
            )
        if not self.is_available() or not self.is_connected():
            result = self._broker_request_not_ready_error()
            result.update(
                type="stock_ranking_snapshot",
                source=clean_source,
                rows=[],
            )
            return self._finish_callback(callback, result)
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result.update(
                type="stock_ranking_snapshot",
                source=clean_source,
                rows=[],
            )
            return self._finish_callback(callback, result)

        trcode = str(spec.get("trcode") or "").strip().upper()
        rqname = (
            f"STOCK_RANKING_{clean_source}_"
            f"{datetime.now().strftime('%H%M%S%f')}"
        )
        screen_no, claim_error = self._claim_tr_screen(
            purpose=MARKET_TR,
            rqname=rqname,
            screen_no=None,
            callback=callback,
            failure_payload={
                "type": "stock_ranking_snapshot",
                "source": clean_source,
                "trcode": trcode,
                "rows": [],
            },
        )
        if screen_no is None:
            return claim_error or {
                "ok": False,
                "type": "stock_ranking_snapshot",
                "source": clean_source,
                "trcode": trcode,
                "rows": [],
            }

        pending = {
            "type": "stock_ranking_snapshot",
            "source": clean_source,
            "trcode": trcode,
            "fields": tuple(spec.get("fields") or ()),
            "screen_no": screen_no,
            "callback": callback,
            **request_identity,
        }
        self._pending_tr[rqname] = pending

        def start_timeout(_result: Any, request_name: str = rqname) -> None:
            QTimer.singleShot(
                self.STOCK_RANKING_SNAPSHOT_TIMEOUT_MS,
                lambda name=request_name: self._expire_stock_ranking_snapshot(name),
            )

        def fail_request(
            result: Any,
            error: str,
            request_name: str = rqname,
        ) -> dict[str, Any]:
            failed = self._pending_tr.pop(request_name, None)
            self._release_pending_tr_screen(request_name, failed)
            payload = {
                "ok": False,
                "type": "stock_ranking_snapshot",
                "source": clean_source,
                "rqname": request_name,
                "trcode": trcode,
                "rows": [],
                "result": result,
                "error": error,
            }
            return self._finish_callback(callback, payload)

        submit_result = self._submit_governed_tr_request(
            rqname=rqname,
            trcode=trcode,
            prev_next=0,
            screen_no=screen_no,
            inputs=tuple(spec.get("inputs") or ()),
            pending=pending,
            on_dispatched=start_timeout,
            on_failed=fail_request,
        )
        return {
            "ok": bool(submit_result.get("ok")),
            "status": "ENQUEUED",
            "type": "stock_ranking_snapshot",
            "source": clean_source,
            "rqname": rqname,
            "trcode": trcode,
            "ranking_tr_count": 1,
            "continuation_tr_count": 0,
            "request": submit_result,
        }

    def _expire_stock_ranking_snapshot(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "stock_ranking_snapshot":
            return
        self._record_tr_governor_timeout()
        self._release_pending_tr_screen(str(rqname), pending)
        callback = pending.get("callback")
        self._finish_callback(
            callback if callable(callback) else None,
            {
                "ok": False,
                "type": "stock_ranking_snapshot",
                "source": pending.get("source", ""),
                "rqname": str(rqname),
                "trcode": pending.get("trcode", ""),
                "rows": [],
                "error": "stock ranking snapshot request timed out",
                "error_kind": "TIMEOUT",
            },
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
        return self._request_minute_candles_common(
            code,
            name,
            interval,
            count,
            max_count,
            screen_no,
            callback,
            commit_to_production=True,
        )

    def request_minute_candles_read_only(
        self,
        code: str,
        name: str = "",
        interval: int = 1,
        count: int = 300,
        max_count: int = DEFAULT_CANDLES_MAX_COUNT,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        """Request opt10080 minute candles without a Production candle commit."""
        return self._request_minute_candles_common(
            code,
            name,
            interval,
            count,
            max_count,
            screen_no,
            callback,
            commit_to_production=False,
        )

    def request_minute_candles_display_only_krx(
        self,
        code: str,
        name: str = "",
        interval: int = 1,
        count: int = 300,
        max_count: int = DEFAULT_CANDLES_MAX_COUNT,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        """Request plain-code native KRX bars with a session-bound read-only receipt."""
        clean_code = normalize_stock_code(code)
        if not is_broker_action_stock_code(clean_code):
            return self._finish_callback(callback, {
                "ok": False, "code": clean_code, "display_only": True,
                "reason_code": "DISPLAY_KRX_CODE_INVALID",
            })

        def display_callback(result: dict[str, Any]) -> None:
            payload = dict(result) if isinstance(result, dict) else {"ok": False}
            payload.update(display_only=True, market_data_identity=clean_code,
                           market_source="KRX")
            if callable(callback):
                callback(payload)

        started = self._request_minute_candles_common(
            clean_code, name, interval, count, max_count, screen_no,
            display_callback, commit_to_production=False,
            market_data_identity=clean_code, market_source="KRX", display_only=True,
        )
        result = dict(started) if isinstance(started, dict) else {"ok": False}
        result.update(display_only=True, market_data_identity=clean_code,
                      market_source="KRX")
        return result

    def request_minute_candles_display_only_nxt(
        self,
        code: str,
        name: str = "",
        interval: int = 1,
        count: int = 300,
        max_count: int = DEFAULT_CANDLES_MAX_COUNT,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
        *,
        nxt_available: bool | None = None,
    ) -> dict[str, Any]:
        """Request exact ``<code>_NX`` native bars without a Production commit."""
        clean_code = normalize_stock_code(code)
        if nxt_available is not True:
            return self._finish_callback(callback, {
                "ok": False, "code": clean_code, "display_only": True,
                "reason_code": "NXT_ELIGIBILITY_NOT_VERIFIED",
            })
        if not is_broker_action_stock_code(clean_code):
            return self._finish_callback(callback, {
                "ok": False, "code": clean_code, "display_only": True,
                "reason_code": "DISPLAY_NXT_CODE_INVALID",
            })
        identity = f"{clean_code}_NX"

        def display_callback(result: dict[str, Any]) -> None:
            payload = dict(result) if isinstance(result, dict) else {"ok": False}
            payload.update(display_only=True, market_data_identity=identity,
                           market_source="NXT")
            if callable(callback):
                callback(payload)

        started = self._request_minute_candles_common(
            clean_code, name, interval, count, max_count, screen_no,
            display_callback, commit_to_production=False,
            market_data_identity=identity, market_source="NXT", display_only=True,
        )
        result = dict(started) if isinstance(started, dict) else {"ok": False}
        result.update(display_only=True, market_data_identity=identity, market_source="NXT")
        return result

    def request_minute_candles_display_only_integrated(
        self,
        code: str,
        name: str = "",
        interval: int = 1,
        count: int = 300,
        max_count: int = DEFAULT_CANDLES_MAX_COUNT,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
        *,
        nxt_available: bool | None = None,
    ) -> dict[str, Any]:
        """Request ``<code>_AL`` history without Production commit."""
        clean_code = normalize_stock_code(code)
        if nxt_available is not True:
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "display_only": True,
                 "reason_code": "NXT_ELIGIBILITY_NOT_VERIFIED"},
            )
        if not is_broker_action_stock_code(clean_code):
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "code": clean_code,
                    "display_only": True,
                    "reason_code": "DISPLAY_INTEGRATED_CODE_INVALID",
                    "error": "canonical six-digit stock code is required",
                },
            )
        market_data_identity = f"{clean_code}_AL"

        def display_callback(result: dict[str, Any]) -> None:
            payload = dict(result) if isinstance(result, dict) else {"ok": False}
            payload.update(
                display_only=True,
                market_data_identity=market_data_identity,
                market_source="INTEGRATED",
            )
            diagnostic = getattr(self, "_al_m1_page_diagnostic", None)
            if diagnostic is not None:
                try:
                    diagnostic.finish_request(str(payload.get("rqname") or ""), payload)
                except Exception as exc:  # Diagnostic failure must not change the display callback.
                    diagnostic.last_error = type(exc).__name__
            if callable(callback):
                callback(payload)

        started = self._request_minute_candles_common(
            clean_code,
            name,
            interval,
            count,
            max_count,
            screen_no,
            display_callback,
            commit_to_production=False,
            market_data_identity=market_data_identity,
            market_source="INTEGRATED",
            display_only=True,
        )
        result = dict(started) if isinstance(started, dict) else {"ok": False}
        result.update(
            display_only=True,
            market_data_identity=market_data_identity,
            market_source="INTEGRATED",
        )
        return result

    def request_day_candles_read_only(
        self,
        code: str,
        name: str = "",
        *,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        return self.request_period_candles_read_only(
            code,
            name,
            timeframe_key="D1",
            count=count,
            screen_no=screen_no,
            callback=callback,
        )

    def request_week_candles_read_only(
        self,
        code: str,
        name: str = "",
        *,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        return self.request_period_candles_read_only(
            code,
            name,
            timeframe_key="W1",
            count=count,
            screen_no=screen_no,
            callback=callback,
        )

    def request_month_candles_read_only(
        self,
        code: str,
        name: str = "",
        *,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        return self.request_period_candles_read_only(
            code,
            name,
            timeframe_key="MO1",
            count=count,
            screen_no=screen_no,
            callback=callback,
        )

    def request_year_candles_read_only(
        self,
        code: str,
        name: str = "",
        *,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        return self.request_period_candles_read_only(
            code,
            name,
            timeframe_key="Y1",
            count=count,
            screen_no=screen_no,
            callback=callback,
        )

    def request_period_candles_read_only(
        self,
        code: str,
        name: str = "",
        *,
        timeframe_key: str,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        return self._request_period_candles_read_only_common(
            code, name, timeframe_key=timeframe_key, count=count,
            screen_no=screen_no, callback=callback,
        )

    def request_period_candles_read_only_integrated(
        self,
        code: str,
        name: str = "",
        *,
        timeframe_key: str,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
        nxt_available: bool | None = None,
    ) -> dict[str, Any]:
        clean_code = normalize_stock_code(code)
        if not is_broker_action_stock_code(clean_code):
            return self._finish_callback(callback, {
                "ok": False, "code": clean_code,
                "reason_code": "INTEGRATED_SOURCE_NOT_VERIFIED",
                "error": "canonical six-digit stock code is required",
            })
        return self._request_period_candles_read_only_common(
            clean_code, name, timeframe_key=timeframe_key, count=count,
            screen_no=screen_no, callback=callback, integrated=True,
        )

    def request_period_candles_read_only_nxt(
        self,
        code: str,
        name: str = "",
        *,
        timeframe_key: str,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
        nxt_available: bool | None = None,
    ) -> dict[str, Any]:
        """Request exact ``<code>_NX`` period bars without a Production commit."""
        clean_code = normalize_stock_code(code)
        if nxt_available is not True:
            return self._finish_callback(callback, {
                "ok": False, "code": clean_code, "display_only": True,
                "reason_code": "NXT_ELIGIBILITY_NOT_VERIFIED",
            })
        if code != clean_code or not is_broker_action_stock_code(clean_code):
            return self._finish_callback(callback, {
                "ok": False, "code": clean_code, "display_only": True,
                "reason_code": "DISPLAY_NXT_CODE_INVALID",
            })
        identity = f"{clean_code}_NX"

        def display_callback(result: dict[str, Any]) -> None:
            payload = dict(result) if isinstance(result, dict) else {"ok": False}
            payload.update(display_only=True, commit_to_production=False,
                           market_data_identity=identity, market_source="NXT")
            if callable(callback):
                callback(payload)

        started = self._request_period_candles_read_only_common(
            clean_code, name, timeframe_key=timeframe_key, count=count,
            screen_no=screen_no, callback=display_callback, nxt=True,
        )
        result = dict(started) if isinstance(started, dict) else {"ok": False}
        result.update(display_only=True, commit_to_production=False,
                      market_data_identity=identity, market_source="NXT")
        return result

    def _request_period_candles_read_only_common(
        self,
        code: str,
        name: str = "",
        *,
        timeframe_key: str,
        count: int = 300,
        screen_no: str | None = None,
        callback: Opt10080Callback | None = None,
        integrated: bool = False,
        nxt: bool = False,
    ) -> dict[str, Any]:
        """Request day/week/month/year candles for Validation without Production mutation."""
        clean_code = normalize_stock_code(code)
        market_data_identity = (
            (f"{clean_code}_NX" if nxt else f"{clean_code}_AL" if integrated else clean_code)
            if is_broker_action_stock_code(clean_code) else ""
        )
        market_source = "NXT" if nxt else "INTEGRATED" if integrated else "KRX"
        key = str(timeframe_key or "").strip().upper()
        trcode = self.VALIDATION_PERIOD_TR_CODES.get(key)
        if not clean_code:
            return self._finish_callback(
                callback,
                {"ok": False, "error": "stock code is required"},
            )
        if trcode is None:
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "error": "unsupported validation timeframe"},
            )
        if not market_data_identity:
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "code": clean_code,
                    "error": "broker support for this stock code is not confirmed",
                    "reason_code": "BROKER_ALPHANUMERIC_STOCK_CODE_UNCONFIRMED",
                },
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
        request_as_of = (
            datetime.now().astimezone().isoformat(timespec="microseconds")
            if nxt else None
        )
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result["code"] = clean_code
            return self._finish_callback(callback, result)
        if nxt:
            request_identity.update(
                request_as_of=request_as_of,
                started_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
                display_only=True,
                commit_to_production=False,
            )
        try:
            clean_count = min(max(int(count), 1), DEFAULT_CANDLES_MAX_COUNT)
        except (TypeError, ValueError):
            clean_count = 300

        rqname = f"{trcode}_{clean_code}_{datetime.now().strftime('%H%M%S%f')}"
        claimed_screen_no, claim_error = self._claim_tr_screen(
            purpose=MARKET_TR,
            rqname=rqname,
            screen_no=screen_no,
            callback=callback,
            failure_payload={"code": clean_code},
        )
        if claimed_screen_no is None:
            return claim_error or {"ok": False, "code": clean_code, "rqname": rqname}

        today = datetime.now().strftime("%Y%m%d")
        inputs = (
            ("\uc885\ubaa9\ucf54\ub4dc", market_data_identity),
            ("\uae30\uc900\uc77c\uc790", today),
            ("\uc218\uc815\uc8fc\uac00\uad6c\ubd84", "1"),
        )
        self._pending_tr[rqname] = {
            "type": "period_candles",
            "code": clean_code,
            "market_data_identity": market_data_identity,
            "market_source": market_source,
            "name": str(name or "").strip(),
            "timeframe_key": key,
            "trcode": trcode,
            "count": clean_count,
            "screen_no": claimed_screen_no,
            "callback": callback,
            "rows": [],
            "inputs": inputs,
            **request_identity,
        }

        def start_timeout(_result: Any) -> None:
            self._arm_minute_candle_timeout(rqname, self._pending_tr.get(rqname))

        def fail_request(result: Any, error: str) -> dict[str, Any]:
            pending = self._pending_tr.pop(rqname, None)
            self._release_pending_tr_screen(rqname, pending)
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "type": "period_candles",
                    "code": clean_code,
                    "timeframe_key": key,
                    "rqname": rqname,
                    "result": result,
                    "error": error,
                },
            )

        dispatched = self._submit_governed_tr_request(
            rqname=rqname,
            trcode=trcode,
            prev_next=0,
            screen_no=claimed_screen_no,
            inputs=inputs,
            pending=self._pending_tr[rqname],
            on_dispatched=start_timeout,
            on_failed=fail_request,
        )
        if not dispatched.get("ok"):
            return dispatched
        result = {
            "ok": True,
            "status": dispatched.get("status", "REQUESTED"),
            "code": clean_code,
            "market_data_identity": market_data_identity,
            "market_source": market_source,
            "timeframe_key": key,
            "rqname": rqname,
            "screen_no": claimed_screen_no,
            "result": dispatched.get("result"),
        }
        if nxt:
            result.update(
                display_only=True,
                connection_epoch=request_identity["request_connection_epoch"],
                login_session_id=request_identity["request_login_session_id"],
                request_as_of=request_identity["request_as_of"],
                started_at=request_identity["started_at"],
            )
        return result

    def _request_minute_candles_common(
        self,
        code: str,
        name: str,
        interval: int,
        count: int,
        max_count: int,
        screen_no: str | None,
        callback: Opt10080Callback | None,
        *,
        commit_to_production: bool,
        market_data_identity: str = "",
        market_source: str = "KRX",
        display_only: bool = False,
    ) -> dict[str, Any]:
        clean_code = normalize_stock_code(code)
        if not clean_code:
            return self._finish_callback(
                callback,
                {"ok": False, "error": "stock code is required"},
            )
        if not is_broker_action_stock_code(clean_code):
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "code": clean_code,
                    "error": "broker support for this stock code is not confirmed",
                    "reason_code": "BROKER_ALPHANUMERIC_STOCK_CODE_UNCONFIRMED",
                },
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
        request_as_of = (
            datetime.now().astimezone().isoformat(timespec="microseconds")
            if display_only else None
        )
        request_identity = self._capture_broker_request_identity()
        if request_identity is None:
            result = self._broker_request_not_ready_error()
            result["code"] = clean_code
            return self._finish_callback(callback, result)

        if display_only:
            request_identity["request_as_of"] = request_as_of
            request_identity["started_at"] = datetime.now().astimezone().isoformat(
                timespec="microseconds"
            )
        try:
            clean_interval = max(int(interval), 1)
        except (TypeError, ValueError):
            clean_interval = 1
        try:
            clean_count = min(max(int(count), 1), DEFAULT_CANDLES_MAX_COUNT)
        except (TypeError, ValueError):
            clean_count = 300
        try:
            clean_max_count = min(
                max(int(max_count), clean_count),
                DEFAULT_CANDLES_MAX_COUNT,
            )
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
        pending = {
            "type": "minute_candles",
            "code": clean_code,
            "name": str(name or "").strip(),
            "interval": clean_interval,
            "count": clean_count,
            "max_count": clean_max_count,
            "screen_no": claimed_screen_no,
            "callback": callback,
            "rows": [],
            "commit_to_production": bool(commit_to_production),
            **request_identity,
        }
        if not commit_to_production:
            pending["market_data_identity"] = (
                str(market_data_identity or "").strip() or clean_code
            )
            pending["market_source"] = str(market_source or "KRX").strip()
            if display_only:
                pending["display_only"] = True
                if (getattr(self, "_al_m1_page_diagnostic", None) is not None
                        and clean_interval == 1
                        and pending["market_data_identity"] == f"{clean_code}_AL"):
                    pending["al_m1_diag_requested_stock_code"] = pending["market_data_identity"]
        self._pending_tr[rqname] = pending

        def start_timeout(_result: Any) -> None:
            self._arm_minute_candle_timeout(rqname, self._pending_tr.get(rqname))

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
                ("종목코드", str(pending.get("market_data_identity") or clean_code)),
                ("틱범위", str(clean_interval)),
                ("수정주가구분", "1"),
            ),
            pending=self._pending_tr[rqname],
            on_dispatched=start_timeout,
            on_failed=fail_request,
        )
        if not dispatched.get("ok"):
            return dispatched
        result = {
            "ok": True,
            "status": dispatched.get("status", "REQUESTED"),
            "code": clean_code,
            "rqname": rqname,
            "screen_no": claimed_screen_no,
            "result": dispatched.get("result"),
        }
        if display_only:
            result.update(
                display_only=True,
                market_data_identity=str(pending["market_data_identity"]),
                market_source=str(pending["market_source"]),
                connection_epoch=pending["request_connection_epoch"],
                login_session_id=pending["request_login_session_id"],
                request_as_of=pending["request_as_of"],
                started_at=pending["started_at"],
            )
        return result

    def _arm_minute_candle_timeout(
        self,
        rqname: str,
        pending: dict[str, Any] | None,
    ) -> None:
        if (
            not isinstance(pending, dict)
            or pending.get("type") not in {"minute_candles", "period_candles"}
        ):
            return
        generation = int(pending.get("timeout_generation") or 0) + 1
        pending["timeout_generation"] = generation
        QTimer.singleShot(
            self.MINUTE_CANDLE_TR_TIMEOUT_MS,
            lambda request_name=str(rqname), expected=generation: self._expire_minute_candle_request(
                request_name,
                expected_generation=expected,
            ),
        )

    def _expire_minute_candle_request(
        self,
        rqname: str,
        *,
        expected_generation: int | None = None,
    ) -> None:
        current = self._pending_tr.get(str(rqname))
        if (
            isinstance(current, dict)
            and expected_generation is not None
            and int(current.get("timeout_generation") or 0) != int(expected_generation)
        ):
            return
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") not in {"minute_candles", "period_candles"}:
            return
        self._record_tr_governor_timeout()
        self._release_pending_tr_screen(str(rqname), pending)
        callback = pending.get("callback")
        pending_type = str(pending.get("type") or "")
        payload = {
            "ok": False,
            "type": pending_type,
            "code": pending.get("code", ""),
            "name": pending.get("name", ""),
            "rqname": str(rqname),
            "error": "historical candle request timed out",
        }
        if pending_type == "period_candles":
            payload["timeframe_key"] = str(pending.get("timeframe_key") or "")
        else:
            payload.update(_empty_candle_commit_projection())
            if pending.get("display_only") is True:
                payload.update(
                    display_only=True,
                    market_data_identity=pending.get("market_data_identity", ""),
                    market_source=pending.get("market_source", ""),
                )
        self._finish_callback(
            callback if callable(callback) else None,
            payload,
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
        self._record_tr_governor_timeout()
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
        self._record_tr_governor_timeout()
        self._release_pending_tr_screen(str(rqname), pending)
        self._finish_recovery_snapshot(
            str(rqname),
            pending,
            collection_complete=False,
            errors=("broker snapshot request timed out",),
        )

    def _on_event_connect(self, err_code: Any) -> None:
        try:
            code = int(err_code)
        except (TypeError, ValueError):
            code = -9999

        if (
            code == 0
            and bool(getattr(self, "_connected", False))
            and str(getattr(self, "_login_session_id", "") or "")
        ):
            self._login_requested = False
            self._stop_login_bootstrap_observation()
            return

        self._login_requested = False
        self._stop_login_bootstrap_observation()

        self.last_login_error = code
        if code == 0:
            self.clear_v5_al_realtime_targets(
                remove_from_broker=False, reason="V5_AL_NEW_SESSION",
            )
            self.clear_realtime_shadow_registration(
                remove_from_broker=False,
                reason="REALTIME_SHADOW_NEW_SESSION",
            )
            self.clear_nxt_display_registration(
                remove_from_broker=False,
                reason="NXT_DISPLAY_NEW_SESSION",
            )
            self.clear_mock_orderbook_registration(
                remove_from_broker=False,
                reason="MOCK_ORDERBOOK_NEW_SESSION",
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

        observed_fids = list(
            dict.fromkeys(
                [
                    *fids,
                    *TRADE_COST_DIAGNOSTIC_FIDS,
                    *ORDER_RECONCILIATION_EVIDENCE_FIDS,
                ]
            )
        )
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
            "received_at": datetime.now().astimezone().isoformat(
                sep=" ", timespec="milliseconds"
            ),
        }
        if raw_event["gubun"] == "0":
            try:
                record_trade_cost_chejan_diagnostic(raw_event)
            except Exception:
                pass
        self.raw_chejan_received.emit(raw_event)

    def clear_v5_al_realtime_targets(
        self, *, remove_from_broker: bool = True, reason: str = "V5_AL_CLEARED",
    ) -> dict[str, Any]:
        registration = getattr(self, "_v5_al_registration", {})
        batches = registration.get("batches", ())
        errors = self._clear_realtime_shadow_batches(
            list(batches), remove_from_broker=remove_from_broker,
        ) if batches else []
        self._v5_al_registration = {}
        self._v5_al_receive_sequence = 0
        self._v5_al_receive_sequence_by_code = {}
        return {"ok": not errors, "changed": bool(batches),
                "reason_code": reason if not errors else "V5_AL_CLEAR_FAILED",
                "errors": tuple(errors)}

    def sync_v5_al_realtime_targets(self, stock_codes: object) -> dict[str, Any]:
        """Own V5-only broker _AL screens, independently of Mock and shadow."""
        try:
            requested = tuple(stock_codes)
        except TypeError:
            return {"ok": False, "reason_code": "V5_AL_CODES_INVALID"}
        if any(not isinstance(code, str) or not is_broker_action_stock_code(code)
               or len(code) != 6 or not code.isascii() or not code.isdigit()
               for code in requested):
            return {"ok": False, "reason_code": "V5_AL_CODES_INVALID"}
        targets = tuple(sorted(set(requested)))
        session = self.broker_session_snapshot()
        current = getattr(self, "_v5_al_registration", {})
        identity = (session.connection_epoch, session.login_session_id)
        if (current.get("identity") == identity
                and current.get("targets") == targets):
            return {"ok": True, "changed": False, "active": bool(targets)}
        cleared = self.clear_v5_al_realtime_targets(
            remove_from_broker=bool(session.connected and current.get("identity") == identity),
        )
        if not cleared["ok"]:
            return cleared
        if not targets:
            return {"ok": True, "changed": bool(current.get("batches")), "active": False}
        if not (session.api_available and session.connected and session.login_session_id):
            return {"ok": False, "reason_code": "V5_AL_SESSION_NOT_READY"}
        batches: list[RealtimeShadowScreenBatch] = []
        try:
            for offset in range(0, len(targets), 100):
                codes = tuple(f"{code}_AL" for code in targets[offset:offset + 100])
                owner = f"v5_al:{identity[0]}:{offset // 100}"
                lease = self._screen_allocator.claim(REALTIME, owner)
                batch = RealtimeShadowScreenBatch(lease.screen_no, owner, codes)
                batches.append(batch)
                raw = self._control.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    lease.screen_no, ";".join(codes),
                    ";".join(str(fid) for fid in V5_AL_EXECUTION_FIDS), "0",
                )
                if raw not in (None, 0, "0"):
                    raise RuntimeError(f"SetRealReg failed: {raw}")
        except Exception as exc:
            self._clear_realtime_shadow_batches(batches, remove_from_broker=True)
            return {"ok": False, "reason_code": "V5_AL_REGISTRATION_FAILED",
                    "error": str(exc)}
        if identity != (self._connection_epoch, self._login_session_id):
            self._clear_realtime_shadow_batches(batches, remove_from_broker=True)
            return {"ok": False, "reason_code": "V5_AL_SESSION_CHANGED"}
        self._v5_al_registration = {"identity": identity, "targets": targets,
                                    "batches": tuple(batches)}
        return {"ok": True, "changed": True, "active": True}

    def _on_receive_v5_al_tick(self, broker_identity: str) -> None:
        identity = str(broker_identity or "").strip()
        if (len(identity) != 9 or not identity.endswith("_AL")
                or not identity[:6].isascii() or not identity[:6].isdigit()):
            return
        code = identity[:6]
        registration = getattr(self, "_v5_al_registration", {})
        session = self.broker_session_snapshot()
        if (code not in registration.get("targets", ())
                or not session.connected
                or registration.get("identity") != (session.connection_epoch, session.login_session_id)):
            return
        by_code = getattr(self, "_v5_al_receive_sequence_by_code", {})
        sequence = by_code.get(code, 0) + 1
        by_code[code] = sequence
        self._v5_al_receive_sequence_by_code = by_code
        try:
            values = {fid: self._control.dynamicCall(
                "GetCommRealData(QString, int)", identity, fid,
            ) for fid in V5_AL_EXECUTION_FIDS}
            tick = normalize_realtime_shadow_tick(
                stock_code=code, real_type=REALTIME_EXECUTION_TYPE,
                execution_time_raw=values[REALTIME_EXECUTION_TIME_FID],
                current_price_raw=values[REALTIME_CURRENT_PRICE_FID],
                cumulative_volume_raw=values[REALTIME_CUMULATIVE_VOLUME_FID],
                trade_volume_raw=values[REALTIME_TRADE_VOLUME_FID],
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                receive_sequence=sequence, received_monotonic=monotonic(),
                received_at=datetime.now().astimezone(),
            )
            if tick is None or tick.stock_code != code:
                return
            self._v5_al_receive_sequence = sequence
            self.v5_al_tick_received.emit({
                **tick.to_payload(), "broker_market_identity": identity,
                "market_source": "SOR", "v5_only": True,
            })
        except Exception:
            return  # A malformed broker callback cannot enter another consumer.

    def _on_receive_real_data(self, *args: Any) -> None:
        """Read the verified 주식체결 FIDs into process-local shadow state."""

        if len(args) < 2:
            return
        stock_code = str(args[0] or "").strip()
        real_type = str(args[1] or "").strip()
        if real_type not in {REALTIME_EXECUTION_TYPE, REALTIME_ORDERBOOK_TYPE}:
            self._ensure_realtime_shadow_state()
            self._observe_realtime_boundary(
                "REAL_CALLBACK_RECEIVED",
                stock_code,
                channel=real_type,
                real_type=real_type,
            )
        if real_type == REALTIME_ORDERBOOK_TYPE:
            self._on_receive_mock_orderbook(stock_code)
            return
        if real_type == REALTIME_EXECUTION_TYPE and stock_code.endswith("_AL"):
            self._on_receive_mock_sor_trade(stock_code)
            self._on_receive_v5_al_tick(stock_code)
            return
        if real_type == NXT_REALTIME_EXECUTION_TYPE:
            self._on_receive_nxt_display_tick(stock_code)
            return
        registration = getattr(self, "_realtime_shadow_registration", None)
        if registration is None:
            registration = self._empty_realtime_shadow_snapshot()
        if real_type != REALTIME_EXECUTION_TYPE:
            self._observe_realtime_boundary(
                "REGISTRATION_IDENTITY_REJECTED",
                stock_code,
                reason="REAL_TYPE_UNSUPPORTED",
                channel=real_type,
                real_type=real_type,
            )
            return
        self._ensure_mock_orderbook_state()
        mock_registration = self._mock_orderbook_registration
        session = self.broker_session_snapshot()
        production_target = bool(
            registration.active
            and stock_code in registration.target_stock_codes
            and session.connected
            and session.connection_epoch == registration.connection_epoch
            and session.login_session_id == registration.login_session_id
        )
        mock_target = bool(
            mock_registration.active
            and stock_code in mock_registration.target_stock_codes
            and stock_code not in mock_registration.sor_stock_codes
            and session.connected
            and session.connection_epoch == mock_registration.connection_epoch
            and session.login_session_id == mock_registration.login_session_id
        )
        if not production_target and not mock_target:
            self._observe_realtime_boundary(
                "REGISTRATION_IDENTITY_REJECTED",
                stock_code,
                reason="STOCK_OR_SESSION_NOT_EXPECTED",
                channel=real_type,
            )
            return
        native_counts = self._native_receive_sequence_by_code["KRX"]
        native_counts[stock_code] = native_counts.get(stock_code, 0) + 1
        native_sequence = native_counts[stock_code]
        if production_target:
            self._observe_realtime_boundary(
                "REAL_CALLBACK_RECEIVED", stock_code,
                channel=real_type, real_type=real_type,
            )
        try:
            values = {
                fid: self._control.dynamicCall(
                    "GetCommRealData(QString, int)",
                    stock_code,
                    fid,
                )
                for fid in REALTIME_SHADOW_FIDS
            }
            tick_args = dict(
                stock_code=stock_code,
                real_type=real_type,
                execution_time_raw=values[REALTIME_EXECUTION_TIME_FID],
                current_price_raw=values[REALTIME_CURRENT_PRICE_FID],
                cumulative_volume_raw=values[REALTIME_CUMULATIVE_VOLUME_FID],
                trade_volume_raw=values[REALTIME_TRADE_VOLUME_FID],
                open_price_raw=values[REALTIME_OPEN_PRICE_FID],
                high_price_raw=values[REALTIME_HIGH_PRICE_FID],
                low_price_raw=values[REALTIME_LOW_PRICE_FID],
                change_rate_raw=values[REALTIME_CHANGE_RATE_FID],
                previous_day_volume_rate_raw=values[
                    REALTIME_PREVIOUS_DAY_VOLUME_RATE_FID
                ],
                execution_strength_raw=values[REALTIME_EXECUTION_STRENGTH_FID],
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                received_monotonic=monotonic(),
            )
            if mock_target and not production_target:
                tick_args["received_at"] = datetime.now().astimezone()
            tick = normalize_realtime_shadow_tick(
                **tick_args,
                receive_sequence=(
                    self._next_realtime_receive_sequence()
                    if production_target
                    else self._mock_execution_receive_sequence + 1
                ),
            )
            if tick is None:
                if production_target:
                    self._observe_realtime_boundary(
                        "RAW_TICK_REJECTED", stock_code,
                        reason="PAYLOAD_UNAVAILABLE", channel=real_type,
                    )
                return
            if production_target:
                self._observe_realtime_boundary(
                    "RAW_TICK_ACCEPTED", stock_code,
                    channel=real_type,
                    current_price=tick.current_price,
                    market_datetime=tick.market_datetime,
                    receive_sequence=tick.receive_sequence,
                )
            if production_target:
                self.realtime_shadow_tick_received.emit({
                    **tick.to_payload(), "native_receive_sequence": native_sequence,
                    "market_source": "KRX", "broker_code_identity": stock_code})
                if stock_code in self._registration_shadow_targets(registration):
                    _status, completed = self._realtime_shadow_builder.accept_tick(tick)
                    if completed is not None:
                        self.realtime_shadow_bar_completed.emit(completed.to_payload())
            if mock_target:
                self._mock_execution_receive_sequence += 1
                payload = {
                    **replace(tick, receive_sequence=self._mock_execution_receive_sequence).to_payload(),
                    "mock_registration_id": mock_registration.registration_id,
                    "market_source": "KRX",
                    "broker_market_identity": stock_code,
                    "source_proof": "BROKER_KRX_REALTIME",
                }
                normalized = normalize_mock_trade_snapshot(payload)
                if normalized is not None:
                    receipts = self._mock_krx_receipts.setdefault(stock_code, {}).setdefault("trade", {})
                    receipts[normalized.snapshot_identity] = None
                    if len(receipts) > 10000:
                        del receipts[next(iter(receipts))]
                    self.mock_execution_tick_received.emit(payload)
        except Exception as exc:
            if production_target:
                self._observe_realtime_boundary(
                    "RAW_TICK_REJECTED", stock_code,
                    reason="PAYLOAD_UNAVAILABLE", channel=real_type,
                    exception_type=type(exc).__name__,
                )
                observe_production_exception(
                    type(exc), exc, exc.__traceback__,
                    component="realtime_shadow",
                    operation="receive_realtime_tick",
                    source="kiwoom_api.KiwoomApi._on_receive_real_data",
                    target_type="MARKET_DATA", target_id=stock_code,
                    target_name=stock_code,
                    reason_code="REALTIME_SHADOW_TICK_FAILED",
                    failure_scope=f"realtime_shadow_tick:{stock_code}",
                )

    def _on_receive_nxt_display_tick(self, broker_code_identity: str) -> None:
        """Emit one registered ECN execution tick to the display-only path."""

        self._ensure_nxt_display_state()
        registration = self._nxt_display_registration
        if (
            not registration.active
            or broker_code_identity not in registration.broker_code_identities
        ):
            return
        session = self.broker_session_snapshot()
        if (
            not session.connected
            or not session.login_session_id
            or session.connection_epoch != registration.connection_epoch
            or session.login_session_id != registration.login_session_id
        ):
            return
        native_code = broker_code_identity.removesuffix("_NX")
        native_counts = self._native_receive_sequence_by_code["NXT"]
        native_counts[native_code] = native_counts.get(native_code, 0) + 1
        native_sequence = native_counts[native_code]
        try:
            values = {
                fid: self._control.dynamicCall(
                    "GetCommRealData(QString, int)",
                    broker_code_identity,
                    fid,
                )
                for fid in NXT_DISPLAY_REALTIME_FIDS
            }
            tick = normalize_nxt_display_realtime_tick(
                broker_code_identity=broker_code_identity,
                real_type=NXT_REALTIME_EXECUTION_TYPE,
                execution_time_raw=values[NXT_REALTIME_EXECUTION_TIME_FID],
                current_price_raw=values[NXT_REALTIME_CURRENT_PRICE_FID],
                execution_quantity_raw=values[NXT_REALTIME_TRADE_VOLUME_FID],
                cumulative_volume_raw=values[NXT_REALTIME_CUMULATIVE_VOLUME_FID],
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                receive_sequence=self._next_nxt_display_receive_sequence(),
                received_monotonic=monotonic(),
            )
            if tick is not None:
                self.nxt_display_tick_received.emit({
                    **tick.to_payload(), "native_receive_sequence": native_sequence})
        except Exception as exc:
            self._nxt_display_registration = replace(
                registration,
                last_error=str(exc),
            )

    def _on_receive_mock_orderbook(self, stock_code: str) -> None:
        """Emit a Mock-only immutable orderbook without Production mutation."""

        self._ensure_mock_orderbook_state()
        broker_identity = normalize_stock_code(stock_code)
        sor = broker_identity.endswith("_AL")
        code = broker_identity[:-3] if sor else broker_identity
        registration = self._mock_orderbook_registration
        if (
            not registration.active
            or not code
            or code not in registration.target_stock_codes
            or (code in registration.sor_stock_codes) != sor
            or broker_identity != (f"{code}_AL" if sor else code)
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
                for fid in REALTIME_ORDERBOOK_FIDS
            }
            snapshot = normalize_mock_orderbook_snapshot(
                stock_code=code,
                raw_values=values,
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                receive_sequence=self._next_mock_orderbook_receive_sequence(),
                received_at=datetime.now().astimezone(),
                registration_id=registration.registration_id,
                market_source="SOR" if sor else "KRX",
                broker_market_identity=broker_identity,
                source_proof="BROKER_SOR_REALTIME" if sor else "BROKER_KRX_REALTIME",
            )
            if snapshot is not None:
                if sor:
                    self._record_mock_sor_receipt(code, "orderbook", snapshot.snapshot_identity)
                else:
                    receipts = self._mock_krx_receipts.setdefault(code, {}).setdefault("orderbook", {})
                    receipts[snapshot.snapshot_identity] = None
                    if len(receipts) > 10000:
                        del receipts[next(iter(receipts))]
                self.mock_orderbook_received.emit(snapshot)
        except Exception as exc:
            self._mock_orderbook_registration = replace(
                registration,
                last_error=str(exc),
            )

    def _on_receive_mock_sor_trade(self, broker_identity: str) -> None:
        self._ensure_mock_orderbook_state()
        identity = normalize_stock_code(broker_identity)
        code = identity[:-3]
        registration = self._mock_orderbook_registration
        if identity != f"{code}_AL" or not registration.active or code not in registration.sor_stock_codes:
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
                fid: self._control.dynamicCall("GetCommRealData(QString, int)", identity, fid)
                for fid in SOR_MOCK_EXECUTION_FIDS
            }
            self._mock_sor_trade_receive_sequence += 1
            tick = normalize_realtime_shadow_tick(
                stock_code=code, real_type=REALTIME_EXECUTION_TYPE,
                execution_time_raw=values[REALTIME_EXECUTION_TIME_FID],
                current_price_raw=values[REALTIME_CURRENT_PRICE_FID],
                cumulative_volume_raw=values[REALTIME_CUMULATIVE_VOLUME_FID],
                trade_volume_raw=values[REALTIME_TRADE_VOLUME_FID],
                connection_epoch=session.connection_epoch,
                login_session_id=session.login_session_id,
                receive_sequence=self._mock_sor_trade_receive_sequence,
                received_monotonic=monotonic(),
                received_at=datetime.now().astimezone(),
            )
            if tick is None:
                return
            payload = {
                **tick.to_payload(), "market_source": "SOR",
                "broker_market_identity": identity,
                "source_proof": "BROKER_SOR_REALTIME",
                "mock_registration_id": registration.registration_id,
            }
            snapshot = normalize_mock_trade_snapshot(payload)
            if snapshot is not None:
                self._record_mock_sor_receipt(code, "trade", snapshot.snapshot_identity)
                self.mock_sor_trade_received.emit(payload)
        except Exception as exc:
            self._mock_orderbook_registration = replace(registration, last_error=str(exc))

    def verify_mock_sor_market_pair(self, market: Any) -> bool:
        """Attest pairs emitted by this API's registered _AL callbacks."""
        self._ensure_mock_orderbook_state()
        registration = self._mock_orderbook_registration
        book = getattr(market, "orderbook", None)
        trade = getattr(market, "trade", None)
        code = getattr(market, "stock_code", "")
        receipt = self._mock_sor_receipts.get(code, {})
        return bool(
            registration.active and code in registration.sor_stock_codes
            and book is not None and trade is not None
            and book.registration_id == registration.registration_id
            and book.connection_epoch == trade.connection_epoch == registration.connection_epoch
            and book.login_session_id == trade.login_session_id == registration.login_session_id
            and book.market_source == trade.market_source == "SOR"
            and book.broker_market_identity == trade.broker_market_identity == f"{code}_AL"
            and book.source_proof == trade.source_proof == "BROKER_SOR_REALTIME"
            and book.snapshot_identity in receipt.get("orderbook", {})
            and trade.snapshot_identity in receipt.get("trade", {})
        )

    def verify_mock_krx_market_pair(self, market: Any) -> bool:
        """Attest a pair emitted by this API's registered plain-code callbacks."""
        self._ensure_mock_orderbook_state()
        registration = self._mock_orderbook_registration
        book = getattr(market, "orderbook", None)
        trade = getattr(market, "trade", None)
        code = getattr(market, "stock_code", "")
        receipt = self._mock_krx_receipts.get(code, {})
        return bool(
            registration.active and code in registration.target_stock_codes
            and code not in registration.sor_stock_codes
            and book is not None and trade is not None
            and book.registration_id == registration.registration_id
            and book.connection_epoch == trade.connection_epoch == registration.connection_epoch
            and book.login_session_id == trade.login_session_id == registration.login_session_id
            and book.market_source == trade.market_source == "KRX"
            and book.broker_market_identity == trade.broker_market_identity == code
            and book.source_proof == trade.source_proof == "BROKER_KRX_REALTIME"
            and book.snapshot_identity in receipt.get("orderbook", {})
            and trade.snapshot_identity in receipt.get("trade", {})
        )

    def _on_receive_msg(self, *args: Any) -> None:
        """Preserve raw broker messages, then project verified account-auth evidence."""

        if len(args) < 4:
            return
        screen_no, rqname, trcode, message = args[:4]
        request_name = str(rqname or "")
        raw_message = {
            "source": "kiwoom_message",
            "screen_no": str(screen_no or "").strip(),
            "rqname": request_name,
            "trcode": str(trcode or "").strip(),
            "message_raw": "" if message is None else str(message),
            "message": str(message or "").strip(),
            "login_session_id": str(
                getattr(self, "_login_session_id", "") or ""
            ).strip(),
            "connection_epoch": getattr(self, "_connection_epoch", None),
            "received_at": datetime.now().astimezone().isoformat(
                sep=" ", timespec="milliseconds"
            ),
        }
        evidence_store = getattr(self, "_send_order_request_evidence", None)
        request_evidence = (
            evidence_store.get(
                (str(screen_no or "").strip(), request_name)
            )
            if isinstance(evidence_store, dict)
            else None
        )
        if isinstance(request_evidence, dict):
            current_session_id = str(
                getattr(self, "_login_session_id", "") or ""
            ).strip()
            current_epoch = getattr(self, "_connection_epoch", None)
            if (
                str(request_evidence.get("login_session_id") or "").strip()
                != current_session_id
                or request_evidence.get("connection_epoch") != current_epoch
            ):
                request_evidence = None
        if isinstance(request_evidence, dict):
            raw_message["send_order_request"] = dict(request_evidence)
        raw_message_signal = getattr(self, "raw_message_received", None)
        if raw_message_signal is not None and hasattr(raw_message_signal, "emit"):
            raw_message_signal.emit(dict(raw_message))
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
        screen_no_text = str(_screen_no or "").strip()
        request_name = str(rqname or "").strip()

        evidence_store = getattr(self, "_send_order_request_evidence", None)
        request_evidence = (
            evidence_store.get((screen_no_text, request_name))
            if isinstance(evidence_store, dict)
            else None
        )
        if isinstance(request_evidence, dict):
            current_session_id = str(
                getattr(self, "_login_session_id", "") or ""
            ).strip()
            current_epoch = getattr(self, "_connection_epoch", None)
            if (
                str(request_evidence.get("login_session_id") or "").strip()
                != current_session_id
                or request_evidence.get("connection_epoch") != current_epoch
            ):
                request_evidence = None
        if isinstance(request_evidence, dict):
            broker_order_no = ""
            order_no_error = ""
            try:
                raw_order_no = self._control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    str(trcode or ""),
                    request_name,
                    0,
                    "주문번호",
                )
                broker_order_no = str(raw_order_no or "").strip()
            except Exception as exc:
                order_no_error = str(exc)
            received_at = datetime.now().astimezone().isoformat(
                sep=" ", timespec="milliseconds"
            )
            request_evidence["broker_order_no"] = broker_order_no
            request_evidence["order_tr_received_at"] = received_at
            request_evidence["order_trcode"] = str(trcode or "").strip()
            request_evidence["order_record_name"] = str(_record_name or "").strip()
            request_evidence["order_no_error"] = order_no_error
            raw_order_tr = {
                "source": "kiwoom_order_tr",
                "screen_no": screen_no_text,
                "rqname": request_name,
                "trcode": str(trcode or "").strip(),
                "record_name": str(_record_name or "").strip(),
                "prev_next": str(prev_next or "").strip(),
                "broker_order_no": broker_order_no,
                "order_no_error": order_no_error,
                "login_session_id": str(
                    getattr(self, "_login_session_id", "") or ""
                ).strip(),
                "connection_epoch": getattr(self, "_connection_epoch", None),
                "received_at": received_at,
                "send_order_request": dict(request_evidence),
            }
            raw_order_tr_signal = getattr(self, "raw_order_tr_received", None)
            if (
                raw_order_tr_signal is not None
                and hasattr(raw_order_tr_signal, "emit")
            ):
                raw_order_tr_signal.emit(raw_order_tr)

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
        if pending.get("type") == "initial_market_snapshot":
            self._on_receive_initial_market_snapshot(
                request_name,
                str(trcode),
                pending,
            )
            return
        if pending.get("type") == "stock_ranking_snapshot":
            self._on_receive_stock_ranking_snapshot(
                request_name,
                str(trcode),
                pending,
            )
            return
        if pending.get("type") == "period_candles":
            received_at = (
                datetime.now().astimezone().isoformat(timespec="microseconds")
                if pending.get("display_only") is True else None
            )
            callback = pending.get("callback")
            try:
                requested_count = int(pending.get("count") or 300)
                accumulated = pending.setdefault("rows", [])
                remaining = max(requested_count - len(accumulated), 1)
                accumulated.extend(
                    self._read_validation_period_rows(
                        str(trcode),
                        str(rqname),
                        remaining,
                    )
                )
                if str(prev_next).strip() == "2" and len(accumulated) < requested_count:
                    pending["timeout_generation"] = int(pending.get("timeout_generation") or 0) + 1
                    dispatched = self._submit_governed_tr_request(
                        rqname=request_name,
                        trcode=str(pending.get("trcode") or trcode),
                        prev_next=2,
                        screen_no=str(pending.get("screen_no") or ""),
                        inputs=tuple(pending.get("inputs") or ()),
                        pending=pending,
                        on_dispatched=lambda _result: self._arm_minute_candle_timeout(
                            request_name,
                            self._pending_tr.get(request_name),
                        ),
                    )
                    if isinstance(dispatched, dict) and dispatched.get("ok") is True:
                        return
                    raise RuntimeError(
                        str(
                            dispatched.get("error")
                            if isinstance(dispatched, dict)
                            else "period candle continuation failed"
                        )
                    )
                pending = self._pending_tr.pop(request_name, pending)
                self._release_pending_tr_screen(request_name, pending)
                rows = list(accumulated[:requested_count])
                result = {
                    "ok": True,
                    "type": "period_candles",
                    "request_id": request_name,
                    "code": pending.get("code", ""),
                    "market_data_identity": pending.get(
                        "market_data_identity",
                        pending.get("code", ""),
                    ),
                    "market_source": pending.get("market_source", "KRX"),
                    "name": pending.get("name", ""),
                    "timeframe_key": str(pending.get("timeframe_key") or ""),
                    "rows": rows,
                    "rows_count": len(rows),
                    "rqname": str(rqname),
                    "trcode": str(trcode),
                    "has_more": str(prev_next).strip() == "2",
                    "warning": "additional pages available" if str(prev_next).strip() == "2" else "",
                }
                if pending.get("display_only") is True:
                    result.update(
                        display_only=True,
                        connection_epoch=pending["request_connection_epoch"],
                        login_session_id=pending["request_login_session_id"],
                        request_as_of=pending["request_as_of"],
                        started_at=pending["started_at"],
                        received_at=received_at,
                    )
            except Exception as exc:
                self._pending_tr.pop(request_name, None)
                self._release_pending_tr_screen(request_name, pending)
                result = {
                    "ok": False,
                    "type": "period_candles",
                    "code": pending.get("code", ""),
                    "name": pending.get("name", ""),
                    "timeframe_key": str(pending.get("timeframe_key") or ""),
                    "rqname": str(rqname),
                    "trcode": str(trcode),
                    "error": str(exc),
                }
            _trace_validation_readonly_callback(result)
            self._finish_callback(callback if callable(callback) else None, result)
            return
        if pending.get("type") != "minute_candles":
            return
        received_at = (
            datetime.now().astimezone().isoformat(timespec="microseconds")
            if pending.get("display_only") is True else None
        )
        callback = pending.get("callback")
        try:
            requested_count = int(pending.get("count") or 300)
            accumulated = pending.setdefault("rows", [])
            remaining = max(requested_count - len(accumulated), 1)
            page_rows = self._read_opt10080_rows(str(trcode), str(rqname), remaining)
            accumulated.extend(page_rows)
            diagnostic = getattr(self, "_al_m1_page_diagnostic", None)
            if (diagnostic is not None and pending.get("display_only") is True
                    and pending.get("commit_to_production") is False
                    and pending.get("market_source") == "INTEGRATED"
                    and pending.get("market_data_identity") == f"{pending.get('code')}_AL"
                    and pending.get("interval") == 1
                    and str(trcode).lower() == "opt10080"):
                if "al_m1_diag_request_number" not in pending:
                    diagnostic.request_number += 1
                    pending["al_m1_diag_request_number"] = diagnostic.request_number
                pending["al_m1_diag_page_count"] = int(pending.get("al_m1_diag_page_count") or 0) + 1
                try:
                    diagnostic.capture(
                        code=pending.get("code"),
                        request_number=pending["al_m1_diag_request_number"],
                        page_number=pending["al_m1_diag_page_count"],
                        prev_next=str(prev_next).strip(),
                        requested_count=requested_count,
                        rows=page_rows,
                        timestamp_field=self.OPT10080_FIELDS[0],
                        request_id=request_name,
                        ohlcv_fields=self.OPT10080_FIELDS,
                        requested_stock_code=pending.get("al_m1_diag_requested_stock_code", ""),
                        timeframe="M1",
                        request_connection_epoch=pending.get("request_connection_epoch"),
                        request_login_session_id=pending.get("request_login_session_id", ""),
                        callback_metadata=(_screen_no, rqname, trcode, _record_name, prev_next),
                    )
                except Exception as exc:  # Diagnostic failure must not change a broker callback.
                    diagnostic.last_error = type(exc).__name__
            if str(prev_next).strip() == "2" and len(accumulated) < requested_count:
                pending["timeout_generation"] = int(pending.get("timeout_generation") or 0) + 1
                dispatched = self._submit_governed_tr_request(
                    rqname=request_name,
                    trcode="opt10080",
                    prev_next=2,
                    screen_no=str(pending.get("screen_no") or ""),
                    inputs=(
                        (
                            "종목코드",
                            str(
                                pending.get("market_data_identity")
                                or pending.get("code")
                                or ""
                            ),
                        ),
                        ("틱범위", str(pending.get("interval") or 1)),
                        ("수정주가구분", "1"),
                    ),
                    pending=pending,
                    on_dispatched=lambda _result: self._arm_minute_candle_timeout(
                        request_name,
                        self._pending_tr.get(request_name),
                    ),
                )
                if isinstance(dispatched, dict) and dispatched.get("ok") is True:
                    return
                raise RuntimeError(str(dispatched.get("error") if isinstance(dispatched, dict) else "minute candle continuation failed"))
            pending = self._pending_tr.pop(request_name, pending)
            self._release_pending_tr_screen(request_name, pending)
            rows = list(accumulated[:requested_count])
            if pending.get("commit_to_production", True) is False:
                result = {
                    "ok": True,
                    "type": "minute_candles",
                    "request_id": request_name,
                    "code": pending.get("code", ""),
                    "market_data_identity": pending.get(
                        "market_data_identity",
                        pending.get("code", ""),
                    ),
                    "market_source": pending.get("market_source", "KRX"),
                    "name": pending.get("name", ""),
                    "interval": int(pending.get("interval") or 1),
                    "rows": rows,
                    "rows_count": len(rows),
                    "rqname": str(rqname),
                    "trcode": str(trcode),
                    "has_more": str(prev_next).strip() == "2",
                    "warning": "additional pages available" if str(prev_next).strip() == "2" else "",
                }
                if pending.get("display_only") is True:
                    result["display_only"] = True
                    result["connection_epoch"] = pending["request_connection_epoch"]
                    result["login_session_id"] = pending["request_login_session_id"]
                    result["received_at"] = received_at
                    result["request_as_of"] = pending.get("request_as_of")
            else:
                accepted_rows = filter_verified_krx_opt10080_rows(
                    str(pending.get("code", "")), str(pending.get("name", "")), rows,
                )
                commit = commit_minute_candles_for_stock(
                    str(pending.get("code", "")),
                    str(pending.get("name", "")),
                    accepted_rows,
                    max_count=int(pending.get("max_count") or DEFAULT_CANDLES_MAX_COUNT),
                    rqname=str(rqname),
                    trcode=str(trcode),
                    connection_epoch=int(pending.get("request_connection_epoch") or 0),
                ) if accepted_rows else CanonicalMinuteCandleCommitResult(
                    ok=False, changed=False, readback_verified=False, path="",
                    saved_count=0, canonical_content_hash="",
                    error_kind="NO_VERIFIED_KRX_CANDLES",
                    error="opt10080 response contains no rows matching a verified KRX source plan",
                )
                result = {
                    "ok": commit.ok,
                    "type": "minute_candles",
                    "code": pending.get("code", ""),
                    "name": pending.get("name", ""),
                    "rqname": str(rqname),
                    "trcode": str(trcode),
                    "rows_count": len(accepted_rows),
                    "response_rows_count": len(rows),
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
            self._pending_tr.pop(request_name, None)
            self._release_pending_tr_screen(request_name, pending)
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

        if pending.get("commit_to_production") is False:
            _trace_validation_readonly_callback(result)
        self._finish_callback(callback if callable(callback) else None, result)

    def _on_receive_initial_market_snapshot(
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
            raw_rows = self._read_tr_rows(trcode, rqname, OPTKWFID_MARKET_FIELDS)
            rows = [
                normalized.to_payload()
                for row in raw_rows
                if (normalized := normalize_optkwfid_market_row(row)) is not None
            ]
            result = {
                "ok": True,
                "type": "initial_market_snapshot",
                "stock_codes": list(pending.get("stock_codes") or ()),
                "rqname": rqname,
                "trcode": trcode,
                "rows": rows,
                "rows_count": len(rows),
                "snapshot_received_at": datetime.now().astimezone().isoformat(
                    timespec="microseconds"
                ),
                "connection_epoch": pending.get("request_connection_epoch"),
                "login_session_id": pending.get("request_login_session_id", ""),
                "source": "SNAPSHOT",
            }
        except Exception as exc:
            result = {
                "ok": False,
                "type": "initial_market_snapshot",
                "stock_codes": list(pending.get("stock_codes") or ()),
                "rqname": rqname,
                "trcode": trcode,
                "rows": [],
                "error": f"initial market snapshot parsing failed: {exc}",
            }
        self._finish_callback(callback if callable(callback) else None, result)

    @staticmethod
    def _ranking_number(value: object, *, absolute: bool = False) -> int | float | None:
        text = str(value or "").replace(",", "").replace("%", "").strip()
        if not text:
            return None
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
        if absolute:
            number = abs(number)
        return int(number) if number.is_integer() else number

    @classmethod
    def _normalize_stock_ranking_row(
        cls,
        row: object,
        *,
        source: str,
        rank: int,
    ) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        stock_code = normalize_broker_stock_code(row.get("종목코드"))
        if not is_valid_stock_code(stock_code):
            return None
        volume_field = "거래량" if source == "VOLUME_TOP" else "현재거래량"
        trading_value_field = "거래금액" if source == "VOLUME_TOP" else "거래대금"
        return {
            "rank": int(rank),
            "source": str(source),
            "stock_code": stock_code,
            "stock_name": str(row.get("종목명") or "").strip(),
            "current_price": cls._ranking_number(row.get("현재가"), absolute=True),
            "change_rate": cls._ranking_number(row.get("등락률")),
            "execution_strength": cls._ranking_number(row.get("체결강도")),
            "cumulative_volume": cls._ranking_number(row.get(volume_field)),
            "cumulative_trading_value": cls._ranking_number(
                row.get(trading_value_field)
            ),
        }

    def _on_receive_stock_ranking_snapshot(
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
        source = str(pending.get("source") or "")
        try:
            raw_rows = self._read_tr_rows(
                trcode,
                rqname,
                tuple(pending.get("fields") or ()),
            )
            rows = [
                normalized
                for rank, row in enumerate(
                    raw_rows[: self.STOCK_RANKING_LIMIT],
                    start=1,
                )
                if (
                    normalized := self._normalize_stock_ranking_row(
                        row,
                        source=source,
                        rank=rank,
                    )
                )
                is not None
            ]
            result = {
                "ok": True,
                "type": "stock_ranking_snapshot",
                "source": source,
                "rqname": rqname,
                "trcode": trcode,
                "rows": rows,
                "rows_count": len(rows),
                "ranking_tr_count": 1,
                "continuation_tr_count": 0,
                "snapshot_received_at": datetime.now().astimezone().isoformat(
                    timespec="microseconds"
                ),
                "connection_epoch": pending.get("request_connection_epoch"),
                "login_session_id": pending.get("request_login_session_id", ""),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "type": "stock_ranking_snapshot",
                "source": source,
                "rqname": rqname,
                "trcode": trcode,
                "rows": [],
                "error": f"stock ranking snapshot parsing failed: {exc}",
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

    def _read_validation_period_rows(
        self,
        trcode: str,
        rqname: str,
        count: int,
    ) -> list[dict[str, Any]]:
        repeat_count = int(
            self._control.dynamicCall(
                "GetRepeatCnt(QString, QString)",
                trcode,
                rqname,
            )
            or 0
        )
        limit = min(max(int(count or 0), 0), repeat_count) if count else repeat_count
        rows: list[dict[str, Any]] = []
        trace_year = (
            signal_validation_trace_enabled()
            and str(trcode).lower() == "opt10094"
            and str(rqname).startswith("opt10094_005930_")
        )
        raw_dates: list[str] = []
        for index in range(limit):
            raw: dict[str, str] = {}
            for field in self.PERIOD_CANDLE_FIELDS:
                value = self._control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    trcode,
                    rqname,
                    index,
                    field,
                )
                raw[field] = str(value or "").strip()
            if trace_year:
                raw_dates.append(str(raw.get("일자") or ""))
            normalized = normalize_date_based_candle_row(raw)
            if normalized is not None:
                rows.append(normalized)
        if trace_year:
            normalized_times = [str(row.get("체결시간") or "") for row in rows]
            trace_signal_validation_event(
                "broker.validation.raw_normalized.page",
                stock_code="005930", timeframe="Y1",
                request_id=str(rqname), trcode=str(trcode),
                broker_repeat_count=repeat_count, requested_page_count=limit,
                raw_read_count=len(raw_dates), normalized_count=len(rows),
                rejected_count=len(raw_dates) - len(rows),
                raw_first_date=raw_dates[0] if raw_dates else "",
                raw_latest_date=max(raw_dates, default=""),
                normalized_latest_time=max(normalized_times, default=""),
                raw_contains_2026=any(date.startswith("2026") for date in raw_dates),
            )
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
        if signal_validation_trace_enabled() and str(rqname).startswith("opt10080_005930_"):
            pending = getattr(self, "_pending_tr", {}).get(str(rqname)) or {}
            if (
                str(trcode).lower() == "opt10080"
                and pending.get("interval") == 120
                and pending.get("commit_to_production") is False
            ):
                times = [str(row.get("체결시간") or "") for row in rows]
                trace_signal_validation_event(
                    "broker.validation.raw_normalized.page",
                    stock_code="005930", timeframe="M120",
                    request_id=str(rqname), trcode=str(trcode),
                    broker_repeat_count=repeat_count, requested_page_count=limit,
                    raw_read_count=len(rows), normalized_count=len(rows),
                    rejected_count=0,
                    raw_first_time=times[0] if times else "",
                    raw_latest_time=max(times, default=""),
                    raw_contains_2026=any(time.startswith("2026") for time in times),
                )
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
