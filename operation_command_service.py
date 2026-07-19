"""Persistent operation commands for stocks and routine instances.

This service owns persistent operation-mode sequencing.  It deliberately does
not call GUI code, order queues, SendOrder, or broker APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any, Callable
from uuid import uuid4

from runtime_atomic_writer import STATUS_OK, write_json_atomic


SCOPE_STOCK = "STOCK"
SCOPE_ROUTINE_INSTANCE = "ROUTINE_INSTANCE"

MODE_NORMAL = "NORMAL"
MODE_EARLY_CLOSE = "EARLY_CLOSE"
MODE_CARRY_OVER = "CARRY_OVER"
PERSISTENT_MODES = frozenset({MODE_NORMAL, MODE_EARLY_CLOSE, MODE_CARRY_OVER})
COMMAND_IMMEDIATE_LIQUIDATION = "IMMEDIATE_LIQUIDATION"
IMMEDIATE_LIQUIDATION_REQUEST_KEY = "immediate_liquidation_request"
IMMEDIATE_LIQUIDATION_STATUS_REQUESTED = "REQUESTED"

STOCK_APPLIED = "APPLIED"
STOCK_IGNORED_STALE = "IGNORED_STALE_COMMAND"
STOCK_IGNORED_DUPLICATE = "IGNORED_DUPLICATE_COMMAND"
STOCK_FAILED = "FAILED"

RESULT_SUCCESS = "SUCCESS"
RESULT_PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
RESULT_FAILED = "FAILED"

_LOCKS_GUARD = threading.RLock()
_STOCK_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class OperationCommandRequest:
    target_scope: str
    target_id: str
    command: str
    source: str
    occurred_at: str = ""
    command_id: str = ""


@dataclass(frozen=True)
class EarlyCloseCompatibility:
    method: str = "루틴"
    policy: dict[str, Any] = field(default_factory=dict)
    has_close_progress_quantity: bool = True


@dataclass(frozen=True)
class StockOperationCommandResult:
    stock_id: str
    stock_path: str
    status: str
    sequence: int | None = None
    error: str = ""


@dataclass(frozen=True)
class OperationCommandResult:
    status: str
    command_id: str
    stock_results: tuple[StockOperationCommandResult, ...] = field(default_factory=tuple)
    error: str = ""

    @property
    def applied(self) -> tuple[StockOperationCommandResult, ...]:
        return tuple(item for item in self.stock_results if item.status == STOCK_APPLIED)

    @property
    def failed(self) -> tuple[StockOperationCommandResult, ...]:
        return tuple(item for item in self.stock_results if item.status == STOCK_FAILED)

    @property
    def ignored(self) -> tuple[StockOperationCommandResult, ...]:
        return tuple(
            item
            for item in self.stock_results
            if item.status in {STOCK_IGNORED_STALE, STOCK_IGNORED_DUPLICATE}
        )


class OperationCommandService:
    def __init__(
        self,
        project_root: str | Path,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], Any] = uuid4,
        atomic_writer: Callable[[str | Path, dict[str, Any]], dict[str, Any]] = write_json_atomic,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.stocks_root = (self.project_root / "stocks").resolve()
        self._now_factory = now_factory or (lambda: datetime.now().astimezone())
        self._id_factory = id_factory
        self._atomic_writer = atomic_writer

    def apply(self, request: OperationCommandRequest) -> OperationCommandResult:
        return self._apply(request, early_close_compatibility=None)

    def apply_early_close(
        self,
        request: OperationCommandRequest,
        compatibility: EarlyCloseCompatibility,
    ) -> OperationCommandResult:
        if str(request.command or "").strip().upper() != MODE_EARLY_CLOSE:
            command_id = str(request.command_id or "").strip() or str(self._id_factory()).lower()
            return OperationCommandResult(
                RESULT_FAILED,
                command_id,
                error="early-close compatibility requires an EARLY_CLOSE command",
            )
        return self._apply(request, early_close_compatibility=compatibility)

    def _apply(
        self,
        request: OperationCommandRequest,
        *,
        early_close_compatibility: EarlyCloseCompatibility | None,
    ) -> OperationCommandResult:
        error = self._validate_request(request)
        command_id = str(request.command_id or "").strip() or str(self._id_factory()).lower()
        if error:
            return OperationCommandResult(RESULT_FAILED, command_id, error=error)

        targets, resolve_error = self._resolve_targets(request.target_scope, request.target_id)
        if resolve_error:
            return OperationCommandResult(RESULT_FAILED, command_id, error=resolve_error)

        results: list[StockOperationCommandResult] = []
        for stock_dir in targets:
            # Hold exactly one stock lock at a time.  Routine commands therefore
            # cannot create a multi-lock deadlock.
            results.append(
                self._apply_to_stock(
                    stock_dir,
                    request,
                    command_id,
                    early_close_compatibility=early_close_compatibility,
                )
            )

        return OperationCommandResult(
            self._aggregate_status(results),
            command_id,
            tuple(results),
        )

    def _validate_request(self, request: OperationCommandRequest) -> str:
        if str(request.target_scope or "").strip().upper() not in {
            SCOPE_STOCK,
            SCOPE_ROUTINE_INSTANCE,
        }:
            return "target_scope must be STOCK or ROUTINE_INSTANCE"
        if not str(request.target_id or "").strip():
            return "target_id is required"
        if str(request.command or "").strip().upper() not in {
            *PERSISTENT_MODES,
            COMMAND_IMMEDIATE_LIQUIDATION,
        }:
            return "command must be NORMAL, EARLY_CLOSE, CARRY_OVER, or IMMEDIATE_LIQUIDATION"
        if not str(request.source or "").strip():
            return "source is required"
        return ""

    def _resolve_targets(self, target_scope: str, target_id: str) -> tuple[list[Path], str]:
        scope = str(target_scope).strip().upper()
        target = str(target_id).strip()
        if not self.stocks_root.exists():
            return [], "stocks repository does not exist"

        stock_dirs = sorted(
            (path.resolve() for path in self.stocks_root.iterdir() if path.is_dir()),
            key=lambda path: (path.name.casefold(), str(path)),
        )
        if scope == SCOPE_STOCK:
            matches = [
                path
                for path in stock_dirs
                if path.name == target or self._stock_code(path) == target or str(path) == target
            ]
            if len(matches) != 1:
                return [], "stock target was not resolved uniquely"
            return matches, ""

        matches: list[Path] = []
        for path in stock_dirs:
            config = self._read_json(path / "config.json")
            if str(config.get("assigned_routine_instance_id", "") or "").strip() == target:
                matches.append(path)
        if not matches:
            return [], "routine instance has no assigned stocks"
        return matches, ""

    def _apply_to_stock(
        self,
        stock_dir: Path,
        request: OperationCommandRequest,
        command_id: str,
        *,
        early_close_compatibility: EarlyCloseCompatibility | None,
    ) -> StockOperationCommandResult:
        lock = self._stock_lock(stock_dir)
        with lock:
            state_path = stock_dir / "state.json"
            state = self._read_state(state_path)
            if state is None:
                return self._stock_failure(stock_dir, "state.json is missing or invalid")

            command = str(request.command).strip().upper()
            is_immediate_liquidation = command == COMMAND_IMMEDIATE_LIQUIDATION
            if is_immediate_liquidation:
                current_request = state.get(IMMEDIATE_LIQUIDATION_REQUEST_KEY)
                current_request = current_request if isinstance(current_request, dict) else {}
                current_command_id = str(current_request.get("command_id", "") or "").strip()
            else:
                current_command_id = str(state.get("operation_command_id", "") or "").strip()
            current_sequence = self._nonnegative_int(state.get("operation_sequence"))
            if current_sequence is None:
                return self._stock_failure(stock_dir, "operation_sequence is invalid")
            if current_command_id == command_id:
                return StockOperationCommandResult(
                    self._stock_code(stock_dir),
                    str(stock_dir),
                    STOCK_IGNORED_DUPLICATE,
                    current_sequence,
                )

            next_sequence = current_sequence + 1
            applied_at = self._now_factory().isoformat(timespec="seconds")
            next_state = dict(state)
            next_state["operation_sequence"] = next_sequence
            next_state["updated_at"] = applied_at
            if is_immediate_liquidation:
                next_state[IMMEDIATE_LIQUIDATION_REQUEST_KEY] = {
                    "command_id": command_id,
                    "operation_sequence": next_sequence,
                    "requested_at": applied_at,
                    "source": str(request.source).strip(),
                    "target": {
                        "scope": str(request.target_scope).strip().upper(),
                        "id": str(request.target_id).strip(),
                    },
                    "status": IMMEDIATE_LIQUIDATION_STATUS_REQUESTED,
                }
            else:
                next_state.update(
                    {
                        "operation_command_mode": command,
                        "operation_command_id": command_id,
                        "operation_command_source": str(request.source).strip(),
                        "operation_command_occurred_at": str(request.occurred_at or "").strip(),
                        "operation_command_updated_at": applied_at,
                        "operation_command_scope": str(request.target_scope).strip().upper(),
                        "operation_command_target_id": str(request.target_id).strip(),
                    }
                )
                self._apply_legacy_early_close_fields(
                    next_state,
                    request,
                    applied_at,
                    early_close_compatibility=early_close_compatibility,
                )

            try:
                write_result = self._atomic_writer(state_path, next_state)
            except Exception as exc:
                return self._stock_failure(
                    stock_dir,
                    f"atomic state writer raised: {exc}",
                    sequence=next_sequence,
                )
            if not isinstance(write_result, dict) or write_result.get("status") != STATUS_OK:
                error = write_result.get("error", "atomic state write failed") if isinstance(write_result, dict) else "atomic state write failed"
                return self._stock_failure(stock_dir, str(error), sequence=next_sequence)

            saved = self._read_state(state_path)
            if saved is None:
                return self._stock_failure(
                    stock_dir,
                    "read-back verification failed: state.json is missing or invalid",
                    sequence=next_sequence,
                )
            saved_sequence = self._nonnegative_int(saved.get("operation_sequence"))
            if is_immediate_liquidation:
                saved_request = saved.get(IMMEDIATE_LIQUIDATION_REQUEST_KEY)
                saved_request = saved_request if isinstance(saved_request, dict) else {}
                saved_command_id = str(saved_request.get("command_id", "") or "").strip()
            else:
                saved_command_id = str(saved.get("operation_command_id", "") or "").strip()
            if (
                saved_sequence is not None
                and saved_sequence > next_sequence
                and saved_command_id != command_id
            ):
                return StockOperationCommandResult(
                    self._stock_code(stock_dir),
                    str(stock_dir),
                    STOCK_IGNORED_STALE,
                    saved_sequence,
                )
            if is_immediate_liquidation:
                expected_request = next_state[IMMEDIATE_LIQUIDATION_REQUEST_KEY]
                saved_request = saved.get(IMMEDIATE_LIQUIDATION_REQUEST_KEY)
                mismatches = []
                if saved.get("operation_sequence") != next_sequence:
                    mismatches.append("operation_sequence")
                if saved_request != expected_request:
                    mismatches.append(IMMEDIATE_LIQUIDATION_REQUEST_KEY)
                if saved.get("operation_command_mode") != state.get("operation_command_mode"):
                    mismatches.append("operation_command_mode")
            else:
                expected = {
                    "operation_command_mode": next_state["operation_command_mode"],
                    "operation_sequence": next_sequence,
                    "operation_command_id": command_id,
                    "operation_command_source": next_state["operation_command_source"],
                    "operation_command_updated_at": applied_at,
                }
                mismatches = [key for key, value in expected.items() if saved.get(key) != value]
            if mismatches:
                return self._stock_failure(
                    stock_dir,
                    "read-back verification failed: " + ", ".join(mismatches),
                    sequence=next_sequence,
                )

            return StockOperationCommandResult(
                self._stock_code(stock_dir),
                str(stock_dir),
                STOCK_APPLIED,
                next_sequence,
            )

    @staticmethod
    def _apply_legacy_early_close_fields(
        state: dict[str, Any],
        request: OperationCommandRequest,
        applied_at: str,
        *,
        early_close_compatibility: EarlyCloseCompatibility | None,
    ) -> None:
        mode = str(request.command).strip().upper()
        source = str(request.source).strip()
        if mode == MODE_EARLY_CLOSE:
            compatibility = early_close_compatibility or EarlyCloseCompatibility()
            method = str(compatibility.method or "").strip() or "루틴"
            policy = {"method": method, **dict(compatibility.policy or {})}
            if not compatibility.has_close_progress_quantity:
                state.update(
                    {
                        "status": "WAIT_BUY",
                        "review_required": False,
                        "review_status": "",
                        "review_location": "",
                        "review_reason": "",
                        "review_detail": "",
                        "trade_enabled": True,
                        "buy_enabled": False,
                        "sell_enabled": False,
                        "early_close_requested_at": "",
                        "early_close_source": "",
                        "early_close_method": "",
                        "early_close_policy": {},
                        "liquidation_policy_forced": False,
                        "liquidation_policy_reason": "",
                        "operation_notice": "EARLY_CLOSE_NO_TARGET",
                        "operation_notice_reason": "조기마감 대상 없음",
                        "operation_notice_at": applied_at,
                        "close_routine_final_sell_ordered": False,
                        "close_routine_final_sell_ordered_at": "",
                        "close_routine_final_sell_source": "",
                        "close_routine_final_sell_reason": "",
                    }
                )
                return
            state.update(
                {
                    "review_required": False,
                    "review_status": "",
                    "review_location": "",
                    "review_reason": "",
                    "review_detail": "",
                    "trade_enabled": True,
                    "startup_reset_reason": "",
                    "startup_reset_cleared_at": "",
                    "buy_enabled": True,
                    "sell_enabled": True,
                    "early_close_requested_at": applied_at,
                    "early_close_source": source,
                    "early_close_method": method,
                    "early_close_policy": policy,
                    "liquidation_policy_forced": True,
                    "liquidation_policy_reason": "EARLY_CLOSE",
                    "operation_notice": "",
                    "operation_notice_reason": "",
                    "operation_notice_at": "",
                    "close_routine_final_sell_ordered": False,
                    "close_routine_final_sell_ordered_at": "",
                    "close_routine_final_sell_source": "",
                    "close_routine_final_sell_reason": "",
                    "review_returned_at": "",
                    "resumed_at": "",
                    "trade_started_at": "",
                    "startup_reset_at": "",
                    "trade_stopped_at": "",
                }
            )
            if str(state.get("status", "")).strip().upper() not in {
                "EMERGENCY_STOPPED",
                "EMERGENCY_STOP",
                "EMERGENCY",
                "REVIEW_REQUIRED",
                "REVIEW",
            }:
                state["status"] = "EARLY_CLOSE"
            return

        if mode == MODE_CARRY_OVER:
            state.update(
                {
                    "early_close_requested_at": applied_at,
                    "early_close_source": source,
                    "early_close_method": "이월",
                    "early_close_policy": {"method": "이월"},
                    "liquidation_policy_forced": False,
                    "liquidation_policy_reason": "",
                }
            )
            return

        state.update(
            {
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "liquidation_policy_forced": False,
                "liquidation_policy_reason": "",
            }
        )
        # NORMAL affects future policy only.  Existing order/fill/final-sell
        # evidence is intentionally preserved.
        if str(state.get("status", "")).strip().upper() in {
            "EARLY_CLOSE",
            "EARLY_CLOSING",
            "EARLY_CLOSED",
        }:
            state["status"] = "RUNNING" if bool(state.get("trade_enabled")) else "WAIT_BUY"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _read_state(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            return None
        try:
            result = int(value)
        except (TypeError, ValueError):
            return None
        return result if result >= 0 else None

    @staticmethod
    def _stock_code(stock_dir: Path) -> str:
        return stock_dir.name.split("_", 1)[0].strip()

    @staticmethod
    def _stock_lock(stock_dir: Path) -> threading.RLock:
        key = str(stock_dir.resolve()).casefold()
        with _LOCKS_GUARD:
            return _STOCK_LOCKS.setdefault(key, threading.RLock())

    @staticmethod
    def _aggregate_status(results: list[StockOperationCommandResult]) -> str:
        if not results:
            return RESULT_FAILED
        failed = sum(item.status == STOCK_FAILED for item in results)
        if failed == 0:
            return RESULT_SUCCESS
        if failed == len(results):
            return RESULT_FAILED
        return RESULT_PARTIAL_SUCCESS

    def _stock_failure(
        self,
        stock_dir: Path,
        error: str,
        *,
        sequence: int | None = None,
    ) -> StockOperationCommandResult:
        return StockOperationCommandResult(
            self._stock_code(stock_dir),
            str(stock_dir),
            STOCK_FAILED,
            sequence,
            error,
        )
