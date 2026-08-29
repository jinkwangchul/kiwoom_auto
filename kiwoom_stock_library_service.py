# -*- coding: utf-8 -*-
"""Session-scoped Kiwoom master data synchronization for the local stock library."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from event_journal_production import append_production_event
from stock_library_master_diagnostics import (
    DIAGNOSTIC_SCHEMA_VERSION,
    build_master_code_diagnostic_projection,
    invalid_stock_library_code_reason,
    master_code_token_diagnostic,
    valid_stock_library_code as diagnostic_valid_stock_library_code,
)
from stock_code_contract import is_numeric_stock_code, normalize_stock_code


LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = "1.4"
SOURCE_NAME = "KIWOOM_OPENAPI_MASTER"
EVENT_SOURCE_NAME = "KIWOOM_MASTER"
MARKETS = (("KOSPI", "0"), ("KOSDAQ", "10"))
NXT_MARKET = ("NXT", "NXT")
DEFAULT_BATCH_SIZE = 100
MINIMUM_LIBRARY_COUNT = 1_000
MINIMUM_NAME_SUCCESS_RATIO = 0.95
MINIMUM_VALID_CODE_RATIO = 0.95
VALID_SYNC_STATES = frozenset({"IDLE", "RUNNING", "SUCCEEDED", "FAILED"})
UNKNOWN_INSTRUMENT_CLASSIFICATION = "-"

_INSTRUMENT_PRODUCT_MARKERS = (
    ("ETF", ("ETF", "상장지수펀드")),
    ("ETN", ("ETN", "상장지수증권")),
    ("SPAC", ("SPAC", "스팩", "기업인수목적회사")),
    ("REIT", ("REIT", "리츠", "부동산투자회사")),
)
_OTHER_INSTRUMENT_MARKERS = (
    "ELW",
    "뮤추얼펀드",
    "신주인수권",
    "수익증권",
    "선박투자회사",
    "인프라투융자회사",
    "인프라투자금융",
    "하이일드펀드",
)
_GENERAL_EQUITY_MARKERS = frozenset(
    {
        "보통주",
        "우선주",
        "일반주",
        "일반기업",
        "대형주",
        "중형주",
        "소형주",
        "우량기업",
        "벤처기업",
        "중견기업",
        "기술성장기업",
        "신성장기업",
        "외국기업",
    }
)

_HANGUL_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def stock_name_chosung(value: object) -> str:
    """Return deterministic Hangul initials while preserving non-Hangul text."""
    result: list[str] = []
    for char in str(value or ""):
        codepoint = ord(char)
        if 0xAC00 <= codepoint <= 0xD7A3:
            result.append(_HANGUL_INITIALS[(codepoint - 0xAC00) // 588])
        else:
            result.append(char)
    return "".join(result)


def valid_stock_library_code(value: object) -> bool:
    return diagnostic_valid_stock_library_code(value)


def master_status_tokens(value: object) -> list[str]:
    """Split an observed pipe-delimited value without interpreting its labels."""
    return [token.strip() for token in str(value or "").split("|") if token.strip()]


def build_master_status_text(stock_state: object, construction: object) -> str:
    """Combine raw master evidence in stable construction/state order."""
    combined: list[str] = []
    seen: set[str] = set()
    for raw_value in (construction, stock_state):
        for token in master_status_tokens(raw_value):
            if token in seen:
                continue
            seen.add(token)
            combined.append(token)
    return " | ".join(combined)


def master_stock_info_fields(value: object) -> tuple[tuple[str, str], ...]:
    """Parse the documented key|value; master format without inferring missing data."""
    fields: list[tuple[str, str]] = []
    for raw_field in str(value or "").split(";"):
        field = raw_field.strip()
        if not field or "|" not in field:
            continue
        key, raw_value = field.split("|", 1)
        key = key.strip()
        field_value = raw_value.strip()
        if key and field_value:
            fields.append((key, field_value))
    return tuple(fields)


def classify_master_stock_info(
    value: object,
    *,
    market_kind: object = "",
) -> str:
    """Project only classifications explicitly supported by Master evidence."""
    fields = master_stock_info_fields(value)
    evidence_values = tuple(field_value for _key, field_value in fields)
    normalized_values = tuple(
        "".join(field_value.upper().split()) for field_value in evidence_values
    )
    for classification, markers in _INSTRUMENT_PRODUCT_MARKERS:
        if any(
            marker.upper() in evidence
            for evidence in normalized_values
            for marker in markers
        ):
            return classification
    if any(
        marker.upper() in evidence
        for evidence in normalized_values
        for marker in _OTHER_INSTRUMENT_MARKERS
    ):
        return "기타"
    if str(market_kind or "").strip() == "60":
        return "ETN"

    field_map = {key.strip(): field_value.strip() for key, field_value in fields}
    primary_parts = tuple(
        "".join(part.upper().split())
        for part in field_map.get("시장구분0", "").split("|")
        if part.strip()
    )
    primary_market = primary_parts[0] if primary_parts else ""
    secondary_market = "".join(field_map.get("시장구분1", "").split())
    industry_parts = tuple(
        part.strip()
        for part in field_map.get("업종구분", "").split("|")
        if part.strip()
    )
    if (
        primary_market in {"코스피", "KOSPI", "코스닥", "KOSDAQ"}
        and (
            bool(industry_parts)
            or any(
                marker in _GENERAL_EQUITY_MARKERS
                for marker in (*primary_parts[1:], secondary_market)
            )
        )
    ):
        return "일반종목"
    return UNKNOWN_INSTRUMENT_CLASSIFICATION


@dataclass
class StockLibrarySyncState:
    state: str = "IDLE"
    session_id: str = ""
    connection_epoch: int = 0
    started_at: str = ""
    finished_at: str = ""
    total_raw_codes: int = 0
    valid_codes: int = 0
    numeric_code_count: int = 0
    alphanumeric_code_count: int = 0
    failed_name_count: int = 0
    master_stock_state_call_count: int = 0
    master_construction_call_count: int = 0
    failed_master_stock_state_count: int = 0
    failed_master_construction_count: int = 0
    status_evidence_count: int = 0
    master_status_duration_ms: int = 0
    master_stock_info_call_count: int = 0
    failed_master_stock_info_count: int = 0
    master_stock_market_kind_call_count: int = 0
    failed_master_stock_market_kind_count: int = 0
    classification_evidence_count: int = 0
    master_stock_info_duration_ms: int = 0
    duplicate_count: int = 0
    invalid_count: int = 0
    conflict_count: int = 0
    markets: tuple[str, ...] = ()
    duration_ms: int = 0
    error: str = ""
    reason: str = ""
    stage: str = ""
    content_sha256: str = ""
    diagnostic_file_written: bool = False
    diagnostic_file_name: str = ""
    diagnostic_summary: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        result = asdict(self)
        result["markets"] = list(self.markets)
        return result


class StockLibraryValidationError(RuntimeError):
    def __init__(self, reason_code: str, reason: str) -> None:
        super().__init__(reason)
        self.reason_code = reason_code
        self.reason = reason


def validate_stock_library_records(
    records: list[dict[str, Any]],
    *,
    market_raw_counts: dict[str, int],
    raw_code_count: int,
    name_lookup_count: int,
    failed_name_count: int,
    invalid_code_count: int = 0,
    minimum_count: int = MINIMUM_LIBRARY_COUNT,
    minimum_name_success_ratio: float = MINIMUM_NAME_SUCCESS_RATIO,
    minimum_valid_code_ratio: float = MINIMUM_VALID_CODE_RATIO,
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    """Normalize and validate a complete in-memory collection before commit."""
    for market_name, _market_code in MARKETS:
        if int(market_raw_counts.get(market_name, 0) or 0) <= 0:
            raise StockLibraryValidationError(
                "MARKET_EMPTY",
                f"{market_name} market code list is empty",
            )

    normalized_by_code: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    conflict_count = 0
    invalid_count = 0
    for item in records:
        code = normalize_stock_code(item.get("code", ""))
        name = str(item.get("name", "") or "").strip()
        market = str(item.get("market", "") or "").strip()
        nxt_available = item.get("nxt_available")
        master_stock_state = str(item.get("master_stock_state", "") or "").strip()
        master_construction = str(item.get("master_construction", "") or "").strip()
        master_stock_info = str(item.get("master_stock_info", "") or "").strip()
        master_stock_market_kind = str(
            item.get("master_stock_market_kind", "") or ""
        ).strip()
        classification = classify_master_stock_info(
            master_stock_info,
            market_kind=master_stock_market_kind,
        )
        status = build_master_status_text(master_stock_state, master_construction)
        if not status:
            status = str(item.get("status", "") or "").strip()
        if nxt_available is not True and nxt_available is not False and nxt_available is not None:
            nxt_available = None
        if not valid_stock_library_code(code) or not name or market not in {"KOSPI", "KOSDAQ"}:
            invalid_count += 1
            continue
        existing = normalized_by_code.get(code)
        if existing is not None:
            duplicate_count += 1
            if existing["name"] != name:
                conflict_count += 1
            continue
        normalized_by_code[code] = {
            "code": code,
            "name": name,
            "market": market,
            "chosung": stock_name_chosung(name),
            "nxt_available": nxt_available,
            "status": status,
            "master_stock_state": master_stock_state,
            "master_construction": master_construction,
            "master_stock_info": master_stock_info,
            "master_stock_market_kind": master_stock_market_kind,
            "classification": classification,
        }

    if conflict_count:
        raise StockLibraryValidationError(
            "DUPLICATE_NAME_CONFLICT",
            f"conflicting stock names: {conflict_count}",
        )
    raw_count = max(0, int(raw_code_count or 0))
    valid_code_ratio = (
        max(0, raw_count - int(invalid_code_count or 0)) / raw_count
        if raw_count
        else 0.0
    )
    if valid_code_ratio < float(minimum_valid_code_ratio):
        raise StockLibraryValidationError(
            "VALID_CODE_RATIO_TOO_LOW",
            f"valid code ratio {valid_code_ratio:.4f} is below {minimum_valid_code_ratio:.4f}",
        )
    lookup_count = max(0, int(name_lookup_count or 0))
    successful_names = max(0, lookup_count - int(failed_name_count or 0))
    success_ratio = (successful_names / lookup_count) if lookup_count else 0.0
    if success_ratio < float(minimum_name_success_ratio):
        raise StockLibraryValidationError(
            "NAME_SUCCESS_RATIO_TOO_LOW",
            f"name success ratio {success_ratio:.4f} is below {minimum_name_success_ratio:.4f}",
        )
    normalized = sorted(normalized_by_code.values(), key=lambda item: item["code"])
    final_markets = {item["market"] for item in normalized}
    if not {"KOSPI", "KOSDAQ"}.issubset(final_markets):
        raise StockLibraryValidationError(
            "FINAL_MARKET_EMPTY",
            "validated library must contain both KOSPI and KOSDAQ records",
        )
    if len(normalized) < int(minimum_count):
        raise StockLibraryValidationError(
            "LIBRARY_COUNT_TOO_SMALL",
            f"valid library count {len(normalized)} is below {minimum_count}",
        )
    return normalized, {
        "raw_code_count": int(raw_code_count or 0),
        "final_count": len(normalized),
        "duplicate_count": duplicate_count,
        "conflict_count": conflict_count,
        "invalid_record_count": invalid_count,
        "name_success_ratio": success_ratio,
        "valid_code_ratio": valid_code_ratio,
    }


class KiwoomStockLibrarySyncService(QObject):
    """Collect Master data in bounded UI-thread batches and atomically cache it."""

    state_changed = pyqtSignal(dict)
    sync_finished = pyqtSignal(dict)

    def __init__(
        self,
        api: object,
        *,
        project_root: Path | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        minimum_count: int = MINIMUM_LIBRARY_COUNT,
        minimum_name_success_ratio: float = MINIMUM_NAME_SUCCESS_RATIO,
        event_writer: Callable[..., object] = append_production_event,
        scheduler: Callable[[Callable[[], None]], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.api = api
        self.project_root = Path(project_root or Path(__file__).resolve().parent)
        self.batch_size = max(1, int(batch_size or DEFAULT_BATCH_SIZE))
        self.minimum_count = max(1, int(minimum_count or MINIMUM_LIBRARY_COUNT))
        self.minimum_name_success_ratio = float(minimum_name_success_ratio)
        self.event_writer = event_writer
        self.scheduler = scheduler or (lambda callback: QTimer.singleShot(0, callback))
        self.state = StockLibrarySyncState()
        self._attempted_session_keys: set[tuple[int, str]] = set()
        self._queue: list[tuple[str, str]] = []
        self._records: list[dict[str, Any]] = []
        self._market_raw_counts: dict[str, int] = {}
        self._name_lookup_count = 0
        self._started_perf = 0.0
        self._master_status_duration_seconds = 0.0
        self._master_stock_info_duration_seconds = 0.0
        self._market_diagnostics: dict[str, dict[str, Any]] = {}
        self._nxt_codes: set[str] | None = None
        self._invalid_items: list[dict[str, Any]] = []
        self._invalid_name_queue: list[str] = []
        self._invalid_master_names: dict[str, str] = {}

    @property
    def runtime_library_path(self) -> Path:
        return self.project_root / "runtime" / "stock_library.json"

    @property
    def runtime_metadata_path(self) -> Path:
        return self.project_root / "runtime" / "stock_library_meta.json"

    def start_for_current_session(self, *, explicit_retry: bool = False) -> bool:
        """Schedule at most one automatic attempt for the current login session."""
        readiness_reader = getattr(self.api, "broker_readiness_snapshot", None)
        session_reader = getattr(self.api, "broker_session_snapshot", None)
        if not callable(readiness_reader) or not callable(session_reader):
            self._fail("READINESS", "API_UNAVAILABLE", "Kiwoom API readiness is unavailable")
            return False
        readiness = readiness_reader()
        session = session_reader()
        if not bool(getattr(readiness, "broker_request_ready", False)):
            self._fail(
                "READINESS",
                "BROKER_REQUEST_NOT_READY",
                str(getattr(readiness, "reason", "broker request is not ready") or "broker request is not ready"),
            )
            return False
        session_id = str(getattr(session, "login_session_id", "") or "")
        connection_epoch = int(getattr(session, "connection_epoch", 0) or 0)
        if not session_id:
            self._fail("READINESS", "LOGIN_SESSION_MISSING", "login session is missing")
            return False
        session_key = (connection_epoch, session_id)
        if not explicit_retry and session_key in self._attempted_session_keys:
            return False
        if self.state.state == "RUNNING":
            return False

        self._attempted_session_keys.add(session_key)
        self._started_perf = perf_counter()
        self.state = StockLibrarySyncState(
            state="RUNNING",
            session_id=session_id,
            connection_epoch=connection_epoch,
            started_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
            markets=tuple(name for name, _code in MARKETS),
            stage="SCHEDULED",
        )
        self._queue = []
        self._records = []
        self._market_raw_counts = {}
        self._name_lookup_count = 0
        self._master_status_duration_seconds = 0.0
        self._master_stock_info_duration_seconds = 0.0
        self._market_diagnostics = {}
        self._nxt_codes = None
        self._invalid_items = []
        self._invalid_name_queue = []
        self._invalid_master_names = {}
        self.state_changed.emit(self.state.payload())
        try:
            self._write_sync_state_metadata("SYNCING", "SYNC_STARTED")
        except Exception as exc:
            self._fail("SYNC_STATE", "SYNC_STATE_WRITE_FAILED", str(exc))
            return False
        self.scheduler(self._collect_market_codes)
        return True

    @staticmethod
    def _market_diagnostic(
        result: dict[str, Any],
        codes: list[object],
        *,
        market_name: str,
        market_code: str,
    ) -> dict[str, Any]:
        projection = result.get("diagnostic")
        if not isinstance(projection, dict):
            projection = build_master_code_diagnostic_projection(
                ";".join(str(value or "") for value in codes)
            )
        tokens = projection.get("tokens", [])
        safe_tokens = [dict(token) for token in tokens if isinstance(token, dict)]
        return {
            "market_code": market_code,
            "market_name": market_name,
            "original_return_length": int(projection.get("original_return_length", 0) or 0),
            "original_return_sha256": str(projection.get("original_return_sha256", "") or ""),
            "split_token_count": int(projection.get("split_token_count", len(safe_tokens)) or 0),
            "normalized_unique_count": int(projection.get("normalized_unique_count", len(codes)) or 0),
            "raw_invalid_token_count": int(projection.get("raw_invalid_token_count", 0) or 0),
            "raw_invalid_by_reason": dict(projection.get("raw_invalid_by_reason", {}) or {}),
            "tokens": safe_tokens,
        }

    @staticmethod
    def _invalid_item_for_code(
        code: str,
        market_diagnostic: dict[str, Any],
        *,
        market_name: str,
        market_code: str,
    ) -> dict[str, Any]:
        source_token = None
        for token in market_diagnostic.get("tokens", []):
            if normalize_stock_code(token.get("normalized_token", "")) == code:
                source_token = token
                break
        item = dict(source_token) if isinstance(source_token, dict) else master_code_token_diagnostic(code)
        item["market_code"] = market_code
        item["market_name"] = market_name
        item["stripped_token"] = code
        item["normalized_token"] = code
        item["current_validator_valid"] = False
        item["invalid_reason"] = invalid_stock_library_code_reason(code)
        item["master_name"] = ""
        item["master_name_found"] = False
        return item

    def _collect_market_codes(self) -> None:
        try:
            code_market: dict[str, str] = {}
            total_raw = 0
            duplicate_count = 0
            invalid_count = 0
            market_reader = getattr(self.api, "get_market_stock_codes", None)
            if not callable(market_reader):
                raise StockLibraryValidationError(
                    "MASTER_MARKET_API_UNAVAILABLE",
                    "market code API is unavailable",
                )
            for market_name, market_code in MARKETS:
                result = market_reader(market_code)
                if not isinstance(result, dict) or not bool(result.get("ok")):
                    reason = str(result.get("reason", "MASTER_MARKET_CALL_FAILED") if isinstance(result, dict) else "MASTER_MARKET_CALL_FAILED")
                    error = str(result.get("error", "market code lookup failed") if isinstance(result, dict) else "market code lookup failed")
                    raise StockLibraryValidationError(reason, error)
                values = result.get("value", [])
                codes = list(values) if isinstance(values, list) else []
                market_diagnostic = self._market_diagnostic(
                    result,
                    codes,
                    market_name=market_name,
                    market_code=market_code,
                )
                self._market_diagnostics[market_name] = market_diagnostic
                self._market_raw_counts[market_name] = len(codes)
                total_raw += len(codes)
                market_invalid_reasons: Counter[str] = Counter()
                market_valid_count = 0
                for value in codes:
                    code = normalize_stock_code(value)
                    if not valid_stock_library_code(code):
                        invalid_count += 1
                        invalid_item = self._invalid_item_for_code(
                            code,
                            market_diagnostic,
                            market_name=market_name,
                            market_code=market_code,
                        )
                        self._invalid_items.append(invalid_item)
                        market_invalid_reasons[str(invalid_item["invalid_reason"])] += 1
                        continue
                    market_valid_count += 1
                    if code in code_market:
                        duplicate_count += 1
                        continue
                    code_market[code] = market_name
                market_diagnostic["valid_count"] = market_valid_count
                market_diagnostic["invalid_count"] = sum(market_invalid_reasons.values())
                market_diagnostic["invalid_by_reason"] = dict(sorted(market_invalid_reasons.items()))
            nxt_name, nxt_market_code = NXT_MARKET
            try:
                nxt_result = market_reader(nxt_market_code)
            except Exception:
                LOGGER.exception("NXT master-code lookup failed")
                nxt_result = {}
            if isinstance(nxt_result, dict) and bool(nxt_result.get("ok")):
                nxt_values = nxt_result.get("value", [])
                nxt_codes = {
                    normalize_stock_code(value)
                    for value in (nxt_values if isinstance(nxt_values, list) else [])
                    if valid_stock_library_code(normalize_stock_code(value))
                }
                self._nxt_codes = nxt_codes
                self._market_diagnostics[nxt_name] = self._market_diagnostic(
                    nxt_result,
                    list(nxt_values) if isinstance(nxt_values, list) else [],
                    market_name=nxt_name,
                    market_code=nxt_market_code,
                )
                self._market_diagnostics[nxt_name]["valid_count"] = len(nxt_codes)
            else:
                self._nxt_codes = None
            self.state.total_raw_codes = total_raw
            self.state.duplicate_count = duplicate_count
            self.state.invalid_count = invalid_count
            self.state.stage = "MASTER_NAMES"
            self._queue = list(code_market.items())
            self._invalid_name_queue = list(
                dict.fromkeys(str(item.get("stripped_token", "") or "") for item in self._invalid_items)
            )
            self.scheduler(self._process_name_batch)
        except StockLibraryValidationError as exc:
            self._fail("MARKET_CODES", exc.reason_code, exc.reason)
        except Exception as exc:
            LOGGER.exception("Stock Library market collection failed")
            self._fail("MARKET_CODES", "UNEXPECTED_ERROR", str(exc))

    def _process_name_batch(self) -> None:
        name_reader = getattr(self.api, "get_master_stock_name", None)
        if not callable(name_reader):
            self._fail("MASTER_NAMES", "MASTER_NAME_API_UNAVAILABLE", "master name API is unavailable")
            return
        stock_state_reader = getattr(self.api, "get_master_stock_state", None)
        construction_reader = getattr(self.api, "get_master_construction", None)
        stock_info_reader = getattr(self.api, "get_master_stock_info", None)
        stock_market_kind_reader = getattr(
            self.api,
            "get_master_stock_market_kind",
            None,
        )
        batch = self._queue[: self.batch_size]
        del self._queue[: self.batch_size]
        for code, market_name in batch:
            self._name_lookup_count += 1
            try:
                result = name_reader(code)
            except Exception as exc:
                result = {"ok": False, "error": str(exc), "reason": "MASTER_NAME_CALL_FAILED"}
            if not isinstance(result, dict) or not bool(result.get("ok")):
                self.state.failed_name_count += 1
                continue
            name = str(result.get("value", "") or "").strip()
            if not name:
                self.state.failed_name_count += 1
                continue
            status_started = perf_counter()
            master_stock_state = self._read_optional_master_status(
                stock_state_reader,
                code,
                call_counter="master_stock_state_call_count",
                failure_counter="failed_master_stock_state_count",
            )
            master_construction = self._read_optional_master_status(
                construction_reader,
                code,
                call_counter="master_construction_call_count",
                failure_counter="failed_master_construction_count",
            )
            self._master_status_duration_seconds += perf_counter() - status_started
            self.state.master_status_duration_ms = max(
                0,
                round(self._master_status_duration_seconds * 1000),
            )
            status = build_master_status_text(master_stock_state, master_construction)
            if status:
                self.state.status_evidence_count += 1
            stock_info_started = perf_counter()
            master_stock_info = self._read_optional_master_status(
                stock_info_reader,
                code,
                call_counter="master_stock_info_call_count",
                failure_counter="failed_master_stock_info_count",
            )
            self._master_stock_info_duration_seconds += perf_counter() - stock_info_started
            self.state.master_stock_info_duration_ms = max(
                0,
                round(self._master_stock_info_duration_seconds * 1000),
            )
            master_stock_market_kind = ""
            classification = classify_master_stock_info(master_stock_info)
            if classification == UNKNOWN_INSTRUMENT_CLASSIFICATION:
                master_stock_market_kind = self._read_optional_master_status(
                    stock_market_kind_reader,
                    code,
                    call_counter="master_stock_market_kind_call_count",
                    failure_counter="failed_master_stock_market_kind_count",
                )
                classification = classify_master_stock_info(
                    master_stock_info,
                    market_kind=master_stock_market_kind,
                )
            if classification != UNKNOWN_INSTRUMENT_CLASSIFICATION:
                self.state.classification_evidence_count += 1
            self._records.append(
                {
                    "code": code,
                    "name": name,
                    "market": market_name,
                    "nxt_available": (
                        code in self._nxt_codes
                        if self._nxt_codes is not None
                        else None
                    ),
                    "status": status,
                    "master_stock_state": master_stock_state,
                    "master_construction": master_construction,
                    "master_stock_info": master_stock_info,
                    "master_stock_market_kind": master_stock_market_kind,
                    "classification": classification,
                }
            )
        if self._queue:
            self.scheduler(self._process_name_batch)
            return
        self.state.stage = "INVALID_MASTER_NAMES"
        if self._invalid_name_queue:
            self.scheduler(self._process_invalid_name_batch)
            return
        self._complete_diagnostic_capture()

    def _read_optional_master_status(
        self,
        reader: object,
        stock_code: str,
        *,
        call_counter: str,
        failure_counter: str,
    ) -> str:
        if not callable(reader):
            setattr(self.state, failure_counter, getattr(self.state, failure_counter) + 1)
            return ""
        setattr(self.state, call_counter, getattr(self.state, call_counter) + 1)
        try:
            result = reader(stock_code)
        except Exception:
            LOGGER.debug("Master status lookup failed: %s", stock_code, exc_info=True)
            result = {"ok": False}
        if not isinstance(result, dict) or not bool(result.get("ok")):
            setattr(self.state, failure_counter, getattr(self.state, failure_counter) + 1)
            return ""
        return str(result.get("value", "") or "").strip()

    def _process_invalid_name_batch(self) -> None:
        name_reader = getattr(self.api, "get_master_stock_name", None)
        batch = self._invalid_name_queue[: self.batch_size]
        del self._invalid_name_queue[: self.batch_size]
        for code in batch:
            result: object = {}
            if callable(name_reader):
                try:
                    result = name_reader(code)
                except Exception:
                    LOGGER.exception("Invalid Master code name diagnostic failed: %r", code)
            name = ""
            if isinstance(result, dict) and bool(result.get("ok")):
                name = str(result.get("value", "") or "").strip()
            self._invalid_master_names[code] = name
        if self._invalid_name_queue:
            self.scheduler(self._process_invalid_name_batch)
            return
        self._complete_diagnostic_capture()

    def _diagnostic_summary(self) -> dict[str, Any]:
        invalid_reasons = Counter(
            str(item.get("invalid_reason", "OTHER") or "OTHER")
            for item in self._invalid_items
        )
        unique_invalid_codes = list(
            dict.fromkeys(str(item.get("stripped_token", "") or "") for item in self._invalid_items)
        )
        found_count = sum(bool(self._invalid_master_names.get(code, "")) for code in unique_invalid_codes)
        raw_invalid_reasons: Counter[str] = Counter()
        for market in self._market_diagnostics.values():
            for reason, count in dict(market.get("raw_invalid_by_reason", {}) or {}).items():
                raw_invalid_reasons[str(reason)] += int(count or 0)
        return {
            "raw_count": sum(
                int(market.get("split_token_count", 0) or 0)
                for market in self._market_diagnostics.values()
            ),
            "normalized_count": sum(
                int(market.get("normalized_unique_count", 0) or 0)
                for market in self._market_diagnostics.values()
            ),
            "valid_count": sum(
                int(market.get("valid_count", 0) or 0)
                for market in self._market_diagnostics.values()
            ),
            "invalid_count": len(self._invalid_items),
            "invalid_by_reason": dict(sorted(invalid_reasons.items())),
            "invalid_unique_count": len(unique_invalid_codes),
            "invalid_master_name_found": found_count,
            "invalid_master_name_missing": len(unique_invalid_codes) - found_count,
            "raw_invalid_token_count": sum(raw_invalid_reasons.values()),
            "raw_invalid_by_reason": dict(sorted(raw_invalid_reasons.items())),
            "duplicate_count": self.state.duplicate_count,
        }

    def _diagnostic_file_path(self) -> Path:
        session_hash = hashlib.sha256(
            str(self.state.session_id or "missing-session").encode("utf-8")
        ).hexdigest()[:10]
        return (
            self.project_root
            / "runtime"
            / "diagnostics"
            / (
                "stock_library_invalid_codes_"
                f"e{int(self.state.connection_epoch or 0)}_{session_hash}.json"
            )
        )

    def _write_diagnostic_file(self, payload: dict[str, Any]) -> Path:
        final_path = self._diagnostic_file_path()
        temp_path: Path | None = None
        try:
            temp_path = self._write_temp(final_path, self._json_bytes(payload))
            if self._read_json(temp_path) != payload:
                raise StockLibraryValidationError(
                    "DIAGNOSTIC_READBACK_MISMATCH",
                    "Stock Library diagnostic temp read-back mismatch",
                )
            self._promote_temp(temp_path, final_path)
            temp_path = None
            if self._read_json(final_path) != payload:
                raise StockLibraryValidationError(
                    "DIAGNOSTIC_READBACK_MISMATCH",
                    "Stock Library diagnostic final read-back mismatch",
                )
            return final_path
        finally:
            if temp_path is not None:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    LOGGER.warning("Failed to remove Stock Library diagnostic temp file: %s", temp_path)

    def _complete_diagnostic_capture(self) -> None:
        for item in self._invalid_items:
            code = str(item.get("stripped_token", "") or "")
            master_name = self._invalid_master_names.get(code, "")
            item["master_name"] = master_name
            item["master_name_found"] = bool(master_name)

        summary = self._diagnostic_summary()
        self.state.diagnostic_summary = summary
        payload = {
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "source": SOURCE_NAME,
            "login_session_id": self.state.session_id,
            "connection_epoch": self.state.connection_epoch,
            "captured_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "markets": self._market_diagnostics,
            "summary": summary,
            "invalid_items": self._invalid_items,
        }
        try:
            diagnostic_path = self._write_diagnostic_file(payload)
        except Exception:
            LOGGER.exception("Stock Library invalid-code diagnostic write failed")
            self.state.diagnostic_file_written = False
            self.state.diagnostic_file_name = ""
        else:
            self.state.diagnostic_file_written = True
            self.state.diagnostic_file_name = diagnostic_path.name
        self._finalize_collection()

    def _finalize_collection(self) -> None:
        self.state.stage = "VALIDATION"
        try:
            records, diagnostics = validate_stock_library_records(
                self._records,
                market_raw_counts=self._market_raw_counts,
                raw_code_count=self.state.total_raw_codes,
                name_lookup_count=self._name_lookup_count,
                failed_name_count=self.state.failed_name_count,
                invalid_code_count=self.state.invalid_count,
                minimum_count=self.minimum_count,
                minimum_name_success_ratio=self.minimum_name_success_ratio,
            )
            self.state.duplicate_count += int(diagnostics["duplicate_count"])
            self.state.invalid_count += int(diagnostics["invalid_record_count"])
            self.state.conflict_count = int(diagnostics["conflict_count"])
            self.state.valid_codes = len(records)
            self.state.numeric_code_count = sum(
                1 for item in records if is_numeric_stock_code(item.get("code", ""))
            )
            self.state.alphanumeric_code_count = (
                len(records) - self.state.numeric_code_count
            )
            self.state.stage = "ATOMIC_COMMIT"
            duration_ms = max(0, round((perf_counter() - self._started_perf) * 1000))
            content_hash = self._commit_cache(records, duration_ms=duration_ms)
        except StockLibraryValidationError as exc:
            self._fail("VALIDATION", exc.reason_code, exc.reason)
            return
        except Exception as exc:
            LOGGER.exception("Stock Library atomic commit failed")
            self._fail("ATOMIC_COMMIT", "CACHE_COMMIT_FAILED", str(exc))
            return

        self.state.state = "SUCCEEDED"
        self.state.finished_at = datetime.now().astimezone().isoformat(timespec="microseconds")
        self.state.duration_ms = max(0, round((perf_counter() - self._started_perf) * 1000))
        self.state.content_sha256 = content_hash
        self.state.error = ""
        self.state.reason = "OK"
        self.state.stage = "COMPLETED"
        payload = self.state.payload()
        self.state_changed.emit(payload)
        self.sync_finished.emit(payload)
        self._write_result_event(success=True)

    @staticmethod
    def _json_bytes(payload: object) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def _write_temp(path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        try:
            with temp_path.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                LOGGER.warning("Failed to remove incomplete temp file: %s", temp_path)
            raise
        return temp_path

    @staticmethod
    def _read_json(path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _promote_temp(temp_path: Path, final_path: Path) -> None:
        os.replace(temp_path, final_path)

    def _commit_cache(self, records: list[dict[str, Any]], *, duration_ms: int) -> str:
        library_path = self.runtime_library_path
        metadata_path = self.runtime_metadata_path
        library_bytes = self._json_bytes(records)
        content_hash = hashlib.sha256(library_bytes).hexdigest()
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "sync_state": "READY",
            "reason_code": "OK",
            "source": SOURCE_NAME,
            "markets": list(self.state.markets),
            "nxt_eligibility_state": (
                "VERIFIED" if self._nxt_codes is not None else "UNAVAILABLE"
            ),
            "nxt_available_count": len(self._nxt_codes or ()),
            "master_stock_market_kind_call_count": (
                self.state.master_stock_market_kind_call_count
            ),
            "failed_master_stock_market_kind_count": (
                self.state.failed_master_stock_market_kind_count
            ),
            "etn_market_member_count": sum(
                item.get("master_stock_market_kind") == "60" for item in records
            ),
            "login_session_id": self.state.session_id,
            "connection_epoch": self.state.connection_epoch,
            "collected_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
            "raw_code_count": self.state.total_raw_codes,
            "final_count": len(records),
            "numeric_code_count": self.state.numeric_code_count,
            "alphanumeric_code_count": self.state.alphanumeric_code_count,
            "failed_name_count": self.state.failed_name_count,
            "master_stock_state_call_count": self.state.master_stock_state_call_count,
            "master_construction_call_count": self.state.master_construction_call_count,
            "failed_master_stock_state_count": self.state.failed_master_stock_state_count,
            "failed_master_construction_count": self.state.failed_master_construction_count,
            "status_evidence_count": self.state.status_evidence_count,
            "master_status_duration_ms": self.state.master_status_duration_ms,
            "master_stock_info_call_count": self.state.master_stock_info_call_count,
            "failed_master_stock_info_count": self.state.failed_master_stock_info_count,
            "classification_evidence_count": self.state.classification_evidence_count,
            "master_stock_info_duration_ms": self.state.master_stock_info_duration_ms,
            "duplicate_count": self.state.duplicate_count,
            "invalid_count": self.state.invalid_count,
            "content_sha256": content_hash,
            "duration_ms": int(duration_ms),
        }
        metadata_bytes = self._json_bytes(metadata)
        library_temp: Path | None = None
        metadata_temp: Path | None = None
        try:
            library_temp = self._write_temp(library_path, library_bytes)
            metadata_temp = self._write_temp(metadata_path, metadata_bytes)
            staged_library = self._read_json(library_temp)
            staged_metadata = self._read_json(metadata_temp)
            if staged_library != records or not isinstance(staged_metadata, dict):
                raise StockLibraryValidationError("CACHE_READBACK_MISMATCH", "staged cache read-back mismatch")
            if int(staged_metadata.get("final_count", -1)) != len(records):
                raise StockLibraryValidationError("CACHE_READBACK_MISMATCH", "staged metadata count mismatch")
            if str(staged_metadata.get("content_sha256", "")) != content_hash:
                raise StockLibraryValidationError("CACHE_READBACK_MISMATCH", "staged metadata hash mismatch")

            previous_library = library_path.read_bytes() if library_path.exists() else None
            previous_metadata = metadata_path.read_bytes() if metadata_path.exists() else None
            try:
                self._promote_temp(library_temp, library_path)
                self._promote_temp(metadata_temp, metadata_path)
                if self._read_json(library_path) != records:
                    raise StockLibraryValidationError("CACHE_READBACK_MISMATCH", "final cache read-back mismatch")
                final_metadata = self._read_json(metadata_path)
                if not isinstance(final_metadata, dict) or final_metadata.get("content_sha256") != content_hash:
                    raise StockLibraryValidationError("CACHE_READBACK_MISMATCH", "final metadata read-back mismatch")
            except Exception:
                self._restore_previous(library_path, previous_library)
                self._restore_previous(metadata_path, previous_metadata)
                raise
        finally:
            for temp_path in (library_temp, metadata_temp):
                if temp_path is None:
                    continue
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    LOGGER.warning("Failed to remove Stock Library temp file: %s", temp_path)
        return content_hash

    def _write_sync_state_metadata(self, sync_state: str, reason_code: str) -> None:
        """Publish search readiness without changing the last library payload."""
        state = str(sync_state or "").strip().upper()
        if state not in {"SYNCING", "FAILED"}:
            raise ValueError(f"unsupported stock library sync state: {state}")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "sync_state": state,
            "reason_code": str(reason_code or state),
            "source": SOURCE_NAME,
            "login_session_id": self.state.session_id,
            "connection_epoch": self.state.connection_epoch,
            "updated_at": datetime.now().astimezone().isoformat(timespec="microseconds"),
        }
        path = self.runtime_metadata_path
        temp_path: Path | None = None
        try:
            temp_path = self._write_temp(path, self._json_bytes(payload))
            if self._read_json(temp_path) != payload:
                raise StockLibraryValidationError(
                    "SYNC_STATE_READBACK_MISMATCH",
                    "Stock Library sync state temp read-back mismatch",
                )
            self._promote_temp(temp_path, path)
            temp_path = None
            if self._read_json(path) != payload:
                raise StockLibraryValidationError(
                    "SYNC_STATE_READBACK_MISMATCH",
                    "Stock Library sync state final read-back mismatch",
                )
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    @classmethod
    def _restore_previous(cls, path: Path, previous: bytes | None) -> None:
        if previous is None:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                LOGGER.exception("Failed to remove incomplete Stock Library cache: %s", path)
            return
        try:
            restore_temp = cls._write_temp(path, previous)
            os.replace(restore_temp, path)
        except OSError:
            LOGGER.exception("Failed to restore previous Stock Library cache: %s", path)

    def _fail(self, stage: str, reason_code: str, reason: str) -> None:
        if self.state.state not in VALID_SYNC_STATES or self.state.state == "IDLE":
            self.state = StockLibrarySyncState()
        self.state.state = "FAILED"
        self.state.stage = str(stage or "UNKNOWN")
        self.state.reason = str(reason_code or "UNKNOWN")
        self.state.error = str(reason or "stock library sync failed")
        self.state.finished_at = datetime.now().astimezone().isoformat(timespec="microseconds")
        if self._started_perf:
            self.state.duration_ms = max(0, round((perf_counter() - self._started_perf) * 1000))
        try:
            self._write_sync_state_metadata("FAILED", self.state.reason)
        except Exception:
            LOGGER.exception("Stock Library FAILED state metadata write failed")
        payload = self.state.payload()
        self.state_changed.emit(payload)
        self.sync_finished.emit(payload)
        self._write_result_event(success=False)

    def _write_result_event(self, *, success: bool) -> None:
        event_type = "STOCK_LIBRARY_SYNC_SUCCEEDED" if success else "STOCK_LIBRARY_SYNC_FAILED"
        diagnostic_summary = dict(self.state.diagnostic_summary or {})
        fields: dict[str, Any] = {
            "source": "kiwoom_stock_library_service.KiwoomStockLibrarySyncService",
            "target_type": "STOCK_LIBRARY",
            "target_id": "runtime/stock_library.json",
            "target_name": "종목 검색 Library",
            "reason_code": self.state.reason,
            "details": {
                "source": EVENT_SOURCE_NAME,
                "markets": list(self.state.markets),
                "raw_code_count": self.state.total_raw_codes,
                "final_count": self.state.valid_codes,
                "numeric_code_count": self.state.numeric_code_count,
                "alphanumeric_code_count": self.state.alphanumeric_code_count,
                "failed_name_count": self.state.failed_name_count,
                "master_stock_info_call_count": self.state.master_stock_info_call_count,
                "failed_master_stock_info_count": self.state.failed_master_stock_info_count,
                "classification_evidence_count": self.state.classification_evidence_count,
                "master_stock_info_duration_ms": self.state.master_stock_info_duration_ms,
                "duplicate_count": self.state.duplicate_count,
                "invalid_count": self.state.invalid_count,
                "conflict_count": self.state.conflict_count,
                "duration_ms": self.state.duration_ms,
                "content_sha256": self.state.content_sha256,
                "stage": self.state.stage,
                "reason_code": self.state.reason,
                "reason": self.state.error,
                "login_session_id": self.state.session_id,
                "connection_epoch": self.state.connection_epoch,
                "raw_count": int(diagnostic_summary.get("raw_count", 0) or 0),
                "valid_count": int(diagnostic_summary.get("valid_count", 0) or 0),
                "invalid_by_reason": dict(
                    diagnostic_summary.get("invalid_by_reason", {}) or {}
                ),
                "invalid_master_name_found": int(
                    diagnostic_summary.get("invalid_master_name_found", 0) or 0
                ),
                "diagnostic_file_written": self.state.diagnostic_file_written,
                "diagnostic_file_name": self.state.diagnostic_file_name,
            },
        }
        try:
            self.event_writer(
                event_type,
                result="SUCCESS" if success else "FAILED",
                severity="INFO" if success else "WARNING",
                **fields,
            )
        except Exception:
            LOGGER.exception("Stock Library result Event write failed")
