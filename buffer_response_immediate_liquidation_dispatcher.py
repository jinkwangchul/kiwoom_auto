"""Dispatch one READY buffer IMMEDIATE event as existing market EARLY_CLOSE.

This module does not implement a new liquidation command or order builder.  It
revalidates BUY cancellation and holding evidence, then applies the existing
EARLY_CLOSE command with the canonical market policy and verifies stock state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, uuid5

from auto_trade_order_execution_boundary import project_buy_only_cancel_readiness
from buffer_response_candidate_selector import (
    buffer_owned_early_close_escalation_exclusion_reason,
    existing_close_or_sell_exclusion_reason,
)
from buffer_response_early_close_dispatcher import (
    _read_json_object,
    _recovery_allows_event,
    _stock_orders,
    buffer_response_command_source,
    deterministic_buffer_early_close_command_id,
)
from buffer_response_ingress_state_service import BufferResponseIngressStateService
from buffer_response_ownership_service import (
    BATCH_SCHEMA_VERSION,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_OWNED,
    BufferResponseOwnershipService,
    validate_batch_ownership_event,
)
from buffer_response_immediate_liquidation_preparer import (
    STATE_READY,
    _ingress_contains_event,
)
from close_intent_service import CLOSE_INTENT_EARLY_CLOSE, apply_close_intent
from close_liquidation_transition_service import (
    POLICY_MARKET,
    POLICY_ROUTINE_CLOSE,
    normalize_direct_close_policy_alias,
)
from operation_close_completion_evaluator import resolve_liquidation_holding_quantity
from operation_command_service import MODE_EARLY_CLOSE, SCOPE_STOCK
from production_recovery_state_registry import production_recovery_registry
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
MARKET_CLOSE_MODE = POLICY_MARKET


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _stock_code(value: object) -> str:
    return _text(value).lstrip("A")


def deterministic_buffer_immediate_market_close_command_id(event_id: object) -> str:
    identity = _text(event_id)
    if not identity:
        return ""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"kiwoom-auto:buffer-response:{identity}:BUFFER_IMMEDIATE_MARKET_CLOSE",
        )
    )


def _result(reason: str = "", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": False,
        "blocked": True,
        "event_id": "",
        "stock_code": "",
        "response_intent": "",
        "ready_verified": False,
        "buy_pending_qty": None,
        "holding_quantity": None,
        "dispatch_requested": False,
        "already_applied": False,
        "command_id": "",
        "source": "",
        "close_mode": MARKET_CLOSE_MODE,
        "read_back_verified": False,
        "reason": _text(reason),
    }
    result.update(updates)
    return result


def _read_back_matches(
    state: Mapping[str, object],
    *,
    command_id: str,
    source: str,
) -> bool:
    policy = state.get("early_close_policy")
    policy_dict = policy if isinstance(policy, Mapping) else {}
    return (
        _text(state.get("operation_command_mode")).upper() == MODE_EARLY_CLOSE
        and _text(state.get("operation_command_id")) == command_id
        and _text(state.get("operation_command_source")) == source
        and _text(state.get("early_close_source")) == source
        and normalize_direct_close_policy_alias(state.get("early_close_method"))
        == MARKET_CLOSE_MODE
        and normalize_direct_close_policy_alias(policy_dict.get("method"))
        == MARKET_CLOSE_MODE
        and state.get("liquidation_policy_forced") is True
        and _text(state.get("status")).upper()
        in {MODE_EARLY_CLOSE, "EARLY_CLOSING", "EARLY_CLOSED"}
    )


def _current_close_policy(state: Mapping[str, object], requested_at: str) -> tuple[str, str]:
    method = normalize_direct_close_policy_alias(state.get("early_close_method"))
    policy = state.get("early_close_policy")
    if not method and isinstance(policy, Mapping):
        method = normalize_direct_close_policy_alias(policy.get("method"))
    started_at = _text(state.get("early_close_requested_at"))
    if method or started_at:
        return method or POLICY_ROUTINE_CLOSE, started_at or requested_at
    auto_method = _text(state.get("auto_close_method"))
    auto_policy = state.get("auto_close_policy")
    if not auto_method and isinstance(auto_policy, Mapping):
        auto_method = _text(auto_policy.get("method"))
    auto_started_at = _text(state.get("auto_close_requested_at"))
    if auto_method or auto_started_at:
        return auto_method, auto_started_at or requested_at
    return POLICY_ROUTINE_CLOSE, requested_at


class BufferResponseImmediateLiquidationDispatcher:
    """Apply only an already prepared OWNED IMMEDIATE event."""

    def __init__(
        self,
        *,
        ownership_service: BufferResponseOwnershipService | None = None,
        ingress_service: BufferResponseIngressStateService | None = None,
        project_root: str | Path = PROJECT_ROOT,
        order_queue_path: str | Path = ORDER_QUEUE_PATH,
        positions_path: str | Path = POSITIONS_PATH,
        broker_holdings_path: str | Path = BROKER_HOLDINGS_PATH,
        fills_path: str | Path = FILLS_PATH,
        close_backend: Callable[..., Mapping[str, Any]] = apply_close_intent,
        holding_resolver: Callable[..., Mapping[str, object]] = (
            resolve_liquidation_holding_quantity
        ),
    ) -> None:
        self.ownership = ownership_service or BufferResponseOwnershipService()
        self.ingress = ingress_service or BufferResponseIngressStateService()
        self.project_root = Path(project_root)
        self.order_queue_path = Path(order_queue_path)
        self.positions_path = Path(positions_path)
        self.broker_holdings_path = Path(broker_holdings_path)
        self.fills_path = Path(fills_path)
        self._close_backend = close_backend
        self._holding_resolver = holding_resolver

    def dispatch_event(
        self,
        *,
        event_id: object,
        account_no: object,
        trading_day: object,
        recovery_context: object,
        preparation_result: Mapping[str, object] | object,
    ) -> dict[str, object]:
        identity = _text(event_id)
        account = _text(account_no)
        day = _text(trading_day)
        command_id = deterministic_buffer_immediate_market_close_command_id(identity)
        source = buffer_response_command_source(identity)
        base = {
            "event_id": identity,
            "command_id": command_id,
            "source": source,
        }
        if not identity or not account or not day or not command_id or not source:
            return _result("EVENT_OR_ACCOUNT_IDENTITY_UNAVAILABLE", **base)
        if not isinstance(preparation_result, Mapping):
            return _result("PREPARATION_RESULT_UNAVAILABLE", **base)
        prepared_stock = _stock_code(preparation_result.get("stock_code"))
        prepared_intent = _text(preparation_result.get("response_intent")).upper()
        prepared_quantity = preparation_result.get("holding_quantity")
        if (
            preparation_result.get("state") != STATE_READY
            or preparation_result.get("ready_for_liquidation") is not True
            or preparation_result.get("cancel_complete") is not True
            or preparation_result.get("holding_confirmed") is not True
            or preparation_result.get("event_id") != identity
            or prepared_intent != RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
            or isinstance(prepared_quantity, bool)
            or not isinstance(prepared_quantity, int)
            or prepared_quantity <= 0
        ):
            return _result("PREPARATION_NOT_READY", **base)

        ownership_read = self.ownership.read_snapshot()
        snapshot = ownership_read.get("snapshot")
        if ownership_read.get("ok") is not True or not isinstance(snapshot, Mapping):
            return _result(
                _text(ownership_read.get("reason")) or "OWNERSHIP_UNAVAILABLE",
                **base,
            )
        if snapshot.get("schema_version") != BATCH_SCHEMA_VERSION:
            return _result("LEGACY_OWNERSHIP_INTENT_NOT_EXECUTABLE", **base)
        events = snapshot.get("events")
        raw_event = events.get(identity) if isinstance(events, Mapping) else None
        if not isinstance(raw_event, Mapping):
            return _result("OWNERSHIP_EVENT_UNAVAILABLE", **base)
        try:
            event = validate_batch_ownership_event(raw_event)
        except ValueError as exc:
            return _result(str(exc), **base)

        stock_code = _stock_code(event.get("selected_stock_code"))
        intent = _text(event.get("response_intent")).upper()
        base.update(stock_code=stock_code, response_intent=intent)
        if event.get("status") != STATUS_OWNED:
            return _result("OWNERSHIP_NOT_ACTIVE", **base)
        if event.get("account_no") != account or event.get("trading_day") != day:
            return _result("OWNERSHIP_ACCOUNT_OR_DAY_MISMATCH", **base)
        if intent != RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED:
            return _result("OWNERSHIP_RESPONSE_INTENT_NOT_IMMEDIATE", **base)
        if not stock_code or stock_code != prepared_stock:
            return _result("PREPARATION_STOCK_IDENTITY_MISMATCH", **base)
        ingress_ok, ingress_reason = _ingress_contains_event(self.ingress, event)
        if not ingress_ok:
            return _result(ingress_reason, **base)
        recovery_ok, recovery_reason = _recovery_allows_event(
            recovery_context,
            account_no=account,
            trading_day=day,
            stock_code=stock_code,
        )
        if not recovery_ok:
            return _result(recovery_reason, **base)

        repository = StockRepository(project_root=self.project_root)
        stock_record = repository.find_by_code(stock_code)
        if stock_record is None:
            return _result("STOCK_RUNTIME_UNAVAILABLE", **base)
        stock_dir = self.project_root / stock_record.stock_path
        try:
            queue_document = _read_json_object(self.order_queue_path)
            state = _read_json_object(stock_dir / "state.json")
            config = _read_json_object(stock_dir / "config.json")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "RUNTIME_READ_FAILED", **base)

        readiness = project_buy_only_cancel_readiness(
            queue_document,
            account_no=account,
            stock_code=stock_code,
        )
        base["buy_pending_qty"] = readiness.get("pending_buy_quantity")
        if readiness.get("available") is not True or readiness.get("ready") is not True:
            return _result(
                _text(readiness.get("reason")) or "BUY_CANCEL_NOT_COMPLETE",
                **base,
            )

        holding = self._holding_resolver(
            stock_code,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
        )
        if not isinstance(holding, Mapping) or holding.get("ok") is not True:
            reasons = holding.get("blocked_reasons") if isinstance(holding, Mapping) else ()
            return _result(
                "; ".join(_text(item) for item in reasons or ())
                or "BLOCKED_HOLDING_UNCERTAIN",
                **base,
            )
        quantity = holding.get("resolved_liquidation_qty")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            return _result("BLOCKED_HOLDING_UNCERTAIN", **base)
        base.update(ready_verified=True, holding_quantity=quantity)
        if quantity == 0:
            return _result(
                "ALREADY_FLAT",
                **base,
                ok=True,
                blocked=False,
            )
        if quantity != prepared_quantity:
            return _result("PREPARED_HOLDING_QUANTITY_CHANGED", **base)
        state_holding = state.get("holding_qty")
        if (
            isinstance(state_holding, bool)
            or not isinstance(state_holding, int)
            or state_holding != quantity
        ):
            return _result("STOCK_STATE_HOLDING_QUANTITY_MISMATCH", **base)

        if _read_back_matches(state, command_id=command_id, source=source):
            return _result(
                "",
                **base,
                ok=True,
                blocked=False,
                already_applied=True,
                read_back_verified=True,
            )
        if _text(state.get("operation_command_id")) == command_id:
            return _result("DETERMINISTIC_COMMAND_EVIDENCE_CONFLICT", **base)
        stock_orders, orders_reason = _stock_orders(
            queue_document,
            account_no=account,
            stock_code=stock_code,
        )
        if stock_orders is None:
            return _result(orders_reason, **base)
        same_event_routine_close = (
            _text(state.get("operation_command_mode")).upper() == MODE_EARLY_CLOSE
            and _text(state.get("operation_command_source")) == source
            and _text(state.get("early_close_source")) == source
            and _text(state.get("operation_command_id"))
            == deterministic_buffer_early_close_command_id(identity)
            and normalize_direct_close_policy_alias(state.get("early_close_method"))
            == POLICY_ROUTINE_CLOSE
        )
        if same_event_routine_close:
            conflict = buffer_owned_early_close_escalation_exclusion_reason(
                stock_code=stock_code,
                state=state,
                config=config,
                orders=stock_orders,
                expected_source=source,
                expected_command_id=deterministic_buffer_early_close_command_id(identity),
            )
        else:
            conflict = existing_close_or_sell_exclusion_reason(
                stock_code=stock_code,
                state=state,
                config=config,
                orders=stock_orders,
            )
        if conflict:
            return _result(f"EXISTING_CLOSE_CONFLICT:{conflict}", **base)

        requested_at = _text(event.get("detected_at"))
        current_policy, current_started_at = _current_close_policy(
            state,
            requested_at,
        )
        backend_result = self._close_backend(
            intent=CLOSE_INTENT_EARLY_CLOSE,
            target_scope=SCOPE_STOCK,
            target_id=str(stock_dir.resolve()),
            source=source,
            requested_policy=MARKET_CLOSE_MODE,
            has_close_progress_quantity=True,
            extra_policy={},
            stock_code=stock_code,
            runtime_state=dict(state),
            runtime_routine_instance_id=_text(
                config.get("assigned_routine_instance_id")
            ),
            current_policy=current_policy,
            current_started_at=current_started_at,
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
                _text(exc) or "MARKET_CLOSE_READ_BACK_FAILED",
                **base,
                dispatch_requested=True,
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
                or "MARKET_CLOSE_BACKEND_FAILED",
                **base,
                dispatch_requested=True,
            )
        if not read_back_ok:
            return _result(
                "MARKET_CLOSE_READ_BACK_MISMATCH",
                **base,
                dispatch_requested=True,
            )
        return _result(
            "",
            **base,
            ok=True,
            blocked=False,
            dispatch_requested=True,
            read_back_verified=True,
        )


def dispatch_main_window_buffer_immediate_market_close(
    window: object,
    *,
    event_id: object,
    preparation_result: Mapping[str, object] | object,
    ownership_service: BufferResponseOwnershipService | None = None,
    ingress_service: BufferResponseIngressStateService | None = None,
) -> dict[str, object]:
    try:
        account_reader = getattr(window, "selected_account_no", None)
        account = _text(account_reader()) if callable(account_reader) else ""
        context = production_recovery_registry.snapshot()
        day = _text(getattr(getattr(context, "identity", None), "trading_day", ""))
        return BufferResponseImmediateLiquidationDispatcher(
            ownership_service=ownership_service,
            ingress_service=ingress_service,
        ).dispatch_event(
            event_id=event_id,
            account_no=account,
            trading_day=day,
            recovery_context=context,
            preparation_result=preparation_result,
        )
    except Exception as exc:
        return _result(_text(exc) or "MAIN_WINDOW_IMMEDIATE_MARKET_CLOSE_FAILED")


def dispatch_ready_main_window_buffer_immediate_preparations(
    window: object,
    *,
    preparation_resume_result: Mapping[str, object] | object,
    ownership_service: BufferResponseOwnershipService | None = None,
    ingress_service: BufferResponseIngressStateService | None = None,
) -> dict[str, object]:
    if not isinstance(preparation_resume_result, Mapping):
        return {"ok": False, "attempted": 0, "results": (), "reason": "PREPARATION_RESUME_UNAVAILABLE"}
    raw_results = preparation_resume_result.get("results")
    if not isinstance(raw_results, (list, tuple)):
        return {"ok": False, "attempted": 0, "results": (), "reason": "PREPARATION_RESULTS_INVALID"}
    ready_results = tuple(
        result
        for result in raw_results
        if isinstance(result, Mapping)
        and result.get("state") == STATE_READY
        and result.get("ready_for_liquidation") is True
    )
    results = tuple(
        dispatch_main_window_buffer_immediate_market_close(
            window,
            event_id=result.get("event_id"),
            preparation_result=result,
            ownership_service=ownership_service,
            ingress_service=ingress_service,
        )
        for result in ready_results
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


__all__ = [
    "BufferResponseImmediateLiquidationDispatcher",
    "MARKET_CLOSE_MODE",
    "deterministic_buffer_immediate_market_close_command_id",
    "dispatch_main_window_buffer_immediate_market_close",
    "dispatch_ready_main_window_buffer_immediate_preparations",
]
