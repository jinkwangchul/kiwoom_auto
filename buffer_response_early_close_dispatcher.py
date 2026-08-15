# -*- coding: utf-8 -*-
"""Dispatch durable buffer-owned EARLY_CLOSE intents through Production close."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, uuid5

from buffer_response_candidate_selector import existing_close_exclusion_reason
from buffer_response_ownership_service import (
    BATCH_SCHEMA_VERSION,
    STATUS_OWNED,
    BufferResponseOwnershipService,
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    validate_batch_ownership_event,
)
from close_intent_service import CLOSE_INTENT_EARLY_CLOSE, apply_close_intent
from operation_command_service import MODE_EARLY_CLOSE, SCOPE_STOCK
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import production_recovery_registry
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
ROUTINE_EARLY_CLOSE_METHOD = "루틴"
SOURCE_PREFIX = "BUFFER_RESPONSE:"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _stock_code(value: object) -> str:
    return _text(value).lstrip("A")


def deterministic_buffer_early_close_command_id(event_id: object) -> str:
    identity = _text(event_id)
    if not identity:
        return ""
    return str(uuid5(NAMESPACE_URL, f"kiwoom-auto:buffer-response:{identity}:EARLY_CLOSE"))


def buffer_response_command_source(event_id: object) -> str:
    identity = _text(event_id)
    return f"{SOURCE_PREFIX}{identity}" if identity else ""


def _result(reason: str = "", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": False,
        "blocked": True,
        "dispatched": False,
        "backend_called": False,
        "already_applied": False,
        "event_id": "",
        "selected_stock_code": "",
        "response_intent": "",
        "command_id": "",
        "source": "",
        "reason": _text(reason),
    }
    result.update(updates)
    return result


def _read_json_object(path: Path) -> dict[str, object]:
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} root must be an object")
    return parsed


def _recovery_allows_event(
    recovery_context: object,
    *,
    account_no: str,
    trading_day: str,
    stock_code: str,
) -> tuple[bool, str]:
    if recovery_context is None:
        return False, "RECOVERY_UNAVAILABLE"
    identity = getattr(recovery_context, "identity", None)
    if (
        getattr(recovery_context, "account_status", None) != ACCOUNT_COMPLETED
        or identity is None
        or _text(getattr(identity, "account_no", "")) != account_no
        or _text(getattr(identity, "trading_day", "")) != trading_day
    ):
        return False, "RECOVERY_INCOMPLETE_OR_IDENTITY_MISMATCH"
    stock_results = tuple(getattr(recovery_context, "stocks", ()) or ())
    matching = tuple(
        item
        for item in stock_results
        if _stock_code(getattr(item, "stock_code", "")) == stock_code
    )
    if len(matching) != 1:
        return False, "RECOVERY_STOCK_EVIDENCE_UNAVAILABLE"
    stock_result = matching[0]
    if (
        getattr(stock_result, "stock_status", None) != STOCK_RESTORED
        or bool(getattr(stock_result, "review_required", False))
    ):
        return False, "RECOVERY_STOCK_NOT_RESTORED"
    return True, ""


def _matching_open_position(
    positions_document: Mapping[str, object],
    *,
    account_no: str,
    stock_code: str,
) -> tuple[dict[str, object] | None, str]:
    positions = positions_document.get("positions")
    if not isinstance(positions, list) or any(
        not isinstance(item, dict) for item in positions
    ):
        return None, "POSITIONS_RUNTIME_INVALID"
    matches = [
        dict(item)
        for item in positions
        if _text(item.get("account_no")) == account_no
        and _stock_code(item.get("code") or item.get("stock_code")) == stock_code
    ]
    if len(matches) != 1:
        return None, "OPEN_POSITION_IDENTITY_UNAVAILABLE"
    position = matches[0]
    quantity = position.get("quantity")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
        or _text(position.get("position_status")).upper() != "OPEN"
    ):
        return None, "NO_OPEN_HOLDING"
    return position, ""


def _stock_orders(
    queue_document: Mapping[str, object],
    *,
    account_no: str,
    stock_code: str,
) -> tuple[list[dict[str, object]] | None, str]:
    orders = queue_document.get("orders")
    if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
        return None, "ORDER_QUEUE_RUNTIME_INVALID"
    return [
        dict(item)
        for item in orders
        if _stock_code(item.get("code") or item.get("stock_code")) == stock_code
        and _text(item.get("account_no")) in {"", account_no}
    ], ""


def _read_back_matches(
    state: Mapping[str, object],
    *,
    command_id: str,
    source: str,
) -> bool:
    return (
        _text(state.get("operation_command_mode")).upper() == MODE_EARLY_CLOSE
        and _text(state.get("operation_command_id")) == command_id
        and _text(state.get("operation_command_source")) == source
        and _text(state.get("early_close_source")) == source
        and _text(state.get("early_close_method")) == ROUTINE_EARLY_CLOSE_METHOD
        and _text(state.get("status")).upper()
        in {MODE_EARLY_CLOSE, "EARLY_CLOSING", "EARLY_CLOSED"}
    )


class BufferResponseEarlyCloseDispatcher:
    """Read ownership, dispatch only EARLY_CLOSE, and verify stock state."""

    def __init__(
        self,
        *,
        ownership_service: BufferResponseOwnershipService | None = None,
        project_root: str | Path = PROJECT_ROOT,
        positions_path: str | Path = POSITIONS_PATH,
        order_queue_path: str | Path = ORDER_QUEUE_PATH,
        fills_path: str | Path = FILLS_PATH,
        close_backend: Callable[..., dict[str, Any]] = apply_close_intent,
    ) -> None:
        self.ownership = ownership_service or BufferResponseOwnershipService()
        self.project_root = Path(project_root)
        self.positions_path = Path(positions_path)
        self.order_queue_path = Path(order_queue_path)
        self.fills_path = Path(fills_path)
        self._close_backend = close_backend

    def dispatch_event(
        self,
        *,
        event_id: object,
        account_no: object,
        trading_day: object,
        recovery_context: object,
    ) -> dict[str, object]:
        identity = _text(event_id)
        account = _text(account_no)
        day = _text(trading_day)
        command_id = deterministic_buffer_early_close_command_id(identity)
        source = buffer_response_command_source(identity)
        base = {
            "event_id": identity,
            "command_id": command_id,
            "source": source,
        }
        if not identity or not account or not day or not command_id or not source:
            return _result("EVENT_OR_ACCOUNT_IDENTITY_UNAVAILABLE", **base)

        ownership_read = self.ownership.read_snapshot()
        snapshot = ownership_read.get("snapshot")
        if ownership_read.get("ok") is not True or not isinstance(snapshot, Mapping):
            return _result(_text(ownership_read.get("reason")) or "OWNERSHIP_UNAVAILABLE", **base)
        events = snapshot.get("events")
        event_value = events.get(identity) if isinstance(events, Mapping) else None
        if not isinstance(event_value, Mapping):
            return _result("OWNERSHIP_EVENT_UNAVAILABLE", **base)
        if snapshot.get("schema_version") != BATCH_SCHEMA_VERSION:
            return _result("LEGACY_OWNERSHIP_INTENT_NOT_EXECUTABLE", **base)
        try:
            event = validate_batch_ownership_event(event_value)
        except ValueError as exc:
            return _result(str(exc), **base)

        stock_code = _stock_code(event.get("selected_stock_code"))
        intent = _text(event.get("response_intent")).upper()
        base.update(selected_stock_code=stock_code, response_intent=intent)
        if event.get("status") != STATUS_OWNED:
            return _result("OWNERSHIP_NOT_ACTIVE", **base)
        if event.get("account_no") != account or event.get("trading_day") != day:
            return _result("OWNERSHIP_ACCOUNT_OR_DAY_MISMATCH", **base)
        if intent == RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED:
            return _result("IMMEDIATE_LIQUIDATION_NOT_CONNECTED", **base)
        if intent != RESPONSE_INTENT_EARLY_CLOSE:
            return _result("OWNERSHIP_RESPONSE_INTENT_UNSUPPORTED", **base)

        recovery_ok, recovery_reason = _recovery_allows_event(
            recovery_context,
            account_no=account,
            trading_day=day,
            stock_code=stock_code,
        )
        if not recovery_ok:
            return _result(recovery_reason, **base)
        try:
            positions_document = _read_json_object(self.positions_path)
            queue_document = _read_json_object(self.order_queue_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "RUNTIME_READ_FAILED", **base)
        position, position_reason = _matching_open_position(
            positions_document,
            account_no=account,
            stock_code=stock_code,
        )
        if position is None:
            return _result(position_reason, **base)
        stock_orders, orders_reason = _stock_orders(
            queue_document,
            account_no=account,
            stock_code=stock_code,
        )
        if stock_orders is None:
            return _result(orders_reason, **base)

        repository = StockRepository(project_root=self.project_root)
        stock_record = repository.find_by_code(stock_code)
        if stock_record is None:
            return _result("STOCK_RUNTIME_UNAVAILABLE", **base)
        stock_dir = self.project_root / stock_record.stock_path
        try:
            state = _read_json_object(stock_dir / "state.json")
            config = _read_json_object(stock_dir / "config.json")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "STOCK_RUNTIME_READ_FAILED", **base)

        if _read_back_matches(state, command_id=command_id, source=source):
            return _result(
                "",
                **base,
                ok=True,
                blocked=False,
                already_applied=True,
            )
        if _text(state.get("operation_command_id")) == command_id:
            return _result("DETERMINISTIC_COMMAND_EVIDENCE_CONFLICT", **base)
        conflict = existing_close_exclusion_reason(
            {
                "stock_code": stock_code,
                "is_auto_trade_target": True,
                "position": position,
                "state": state,
                "config": config,
                "orders": stock_orders,
            }
        )
        if conflict:
            return _result(f"EXISTING_CLOSE_CONFLICT:{conflict}", **base)

        routine_instance_id = _text(config.get("assigned_routine_instance_id"))
        requested_at = _text(event.get("detected_at"))
        backend_result = self._close_backend(
            intent=CLOSE_INTENT_EARLY_CLOSE,
            target_scope=SCOPE_STOCK,
            target_id=str(stock_dir.resolve()),
            source=source,
            requested_policy=ROUTINE_EARLY_CLOSE_METHOD,
            has_close_progress_quantity=True,
            extra_policy={},
            stock_code=stock_code,
            runtime_state=dict(state),
            runtime_routine_instance_id=routine_instance_id,
            current_policy="",
            current_started_at="",
            current_command_id=_text(state.get("operation_command_id")),
            command_id=command_id,
            requested_at=requested_at,
            project_root=self.project_root,
            queue_path=self.order_queue_path,
            fills_path=self.fills_path,
        )
        try:
            saved_state = _read_json_object(stock_dir / "state.json")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(
                _text(exc) or "EARLY_CLOSE_READ_BACK_FAILED",
                **base,
                backend_called=True,
            )
        read_back_ok = _read_back_matches(
            saved_state,
            command_id=command_id,
            source=source,
        )
        if not isinstance(backend_result, Mapping) or backend_result.get("ok") is not True:
            return _result(
                _text(
                    backend_result.get("reason")
                    if isinstance(backend_result, Mapping)
                    else ""
                )
                or "EARLY_CLOSE_BACKEND_FAILED",
                **base,
                backend_called=True,
            )
        if not read_back_ok:
            return _result(
                "EARLY_CLOSE_READ_BACK_MISMATCH",
                **base,
                backend_called=True,
            )
        return _result(
            "",
            **base,
            ok=True,
            blocked=False,
            dispatched=True,
            backend_called=True,
        )

    def resume_owned_events(
        self,
        *,
        account_no: object,
        trading_day: object,
        recovery_context: object,
    ) -> dict[str, object]:
        account = _text(account_no)
        day = _text(trading_day)
        ownership_read = self.ownership.read_snapshot()
        snapshot = ownership_read.get("snapshot")
        if ownership_read.get("ok") is not True or not isinstance(snapshot, Mapping):
            return {
                "ok": False,
                "attempted": 0,
                "results": (),
                "reason": _text(ownership_read.get("reason")) or "OWNERSHIP_UNAVAILABLE",
            }
        if snapshot.get("schema_version") != BATCH_SCHEMA_VERSION:
            return {"ok": True, "attempted": 0, "results": (), "reason": ""}
        events = snapshot.get("events")
        if not isinstance(events, Mapping):
            return {"ok": False, "attempted": 0, "results": (), "reason": "OWNERSHIP_EVENTS_INVALID"}
        event_ids = sorted(
            _text(event_id)
            for event_id, event in events.items()
            if isinstance(event, Mapping)
            and event.get("account_no") == account
            and event.get("trading_day") == day
            and event.get("status") == STATUS_OWNED
            and event.get("response_intent") == RESPONSE_INTENT_EARLY_CLOSE
        )
        results = tuple(
            self.dispatch_event(
                event_id=event_id,
                account_no=account,
                trading_day=day,
                recovery_context=recovery_context,
            )
            for event_id in event_ids
        )
        return {
            "ok": all(result.get("ok") is True for result in results),
            "attempted": len(results),
            "results": results,
            "reason": next(
                (_text(result.get("reason")) for result in results if result.get("ok") is not True),
                "",
            ),
        }


def dispatch_main_window_buffer_early_close(
    window: object,
    *,
    event_id: object,
    ownership_service: BufferResponseOwnershipService | None = None,
) -> dict[str, object]:
    try:
        account_reader = getattr(window, "selected_account_no", None)
        account = _text(account_reader()) if callable(account_reader) else ""
        context = production_recovery_registry.snapshot()
        day = _text(getattr(getattr(context, "identity", None), "trading_day", ""))
        dispatcher = BufferResponseEarlyCloseDispatcher(
            ownership_service=ownership_service,
        )
        return dispatcher.dispatch_event(
            event_id=event_id,
            account_no=account,
            trading_day=day,
            recovery_context=context,
        )
    except Exception as exc:
        return _result(_text(exc) or "MAIN_WINDOW_EARLY_CLOSE_DISPATCH_FAILED")


def resume_main_window_buffer_early_close(
    window: object,
    *,
    recovery_identity: object,
) -> dict[str, object]:
    try:
        account_reader = getattr(window, "selected_account_no", None)
        account = _text(account_reader()) if callable(account_reader) else ""
        identity_account = _text(getattr(recovery_identity, "account_no", ""))
        day = _text(getattr(recovery_identity, "trading_day", ""))
        if not account or account != identity_account or day != date.today().isoformat():
            return {"ok": False, "attempted": 0, "results": (), "reason": "RECOVERY_IDENTITY_STALE"}
        return BufferResponseEarlyCloseDispatcher().resume_owned_events(
            account_no=account,
            trading_day=day,
            recovery_context=production_recovery_registry.snapshot(),
        )
    except Exception as exc:
        return {
            "ok": False,
            "attempted": 0,
            "results": (),
            "reason": _text(exc) or "RECOVERY_EARLY_CLOSE_RESUME_FAILED",
        }


__all__ = [
    "BufferResponseEarlyCloseDispatcher",
    "buffer_response_command_source",
    "deterministic_buffer_early_close_command_id",
    "dispatch_main_window_buffer_early_close",
    "resume_main_window_buffer_early_close",
]
