"""Prepare durable buffer-owned IMMEDIATE liquidation without sending SELL.

The preparer is intentionally bounded to BUY-only cancellation readiness and
the existing reconciled holding resolver.  It never selects a new candidate,
changes ownership, completes an event, or invokes a liquidation executor.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Callable, Mapping

from auto_trade_order_execution_boundary import (
    CANCEL_SIDE_SCOPE_BUY_ONLY,
    project_buy_only_cancel_readiness,
)
from buffer_response_candidate_selector import (
    _active_sell_order_reason,
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
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_OWNED,
    BufferResponseOwnershipService,
    validate_batch_ownership_event,
)
from operation_close_completion_evaluator import resolve_liquidation_holding_quantity
from close_liquidation_transition_service import (
    POLICY_MARKET,
    POLICY_ROUTINE_CLOSE,
    normalize_direct_close_policy_alias,
)
from production_recovery_state_registry import production_recovery_registry
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"

STATE_BLOCKED = "BLOCKED"
STATE_WAITING_BUY_CANCEL = "WAITING_BUY_CANCEL"
STATE_READY = "READY_FOR_IMMEDIATE_LIQUIDATION"
STATE_ALREADY_FLAT = "ALREADY_FLAT"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _stock_code(value: object) -> str:
    return _text(value).lstrip("A")


def _main_window_cancel_requester(window: object) -> Callable[..., object] | None:
    requester = getattr(
        window,
        "queue_pending_order_cancellations_for_stock_automatically",
        None,
    )
    if callable(requester):
        return requester
    host_reader = getattr(window, "main_monitoring_auto_trade_operation_host", None)
    if not callable(host_reader):
        return None
    host = host_reader()
    requester = getattr(
        host,
        "queue_pending_order_cancellations_for_stock_automatically",
        None,
    )
    return requester if callable(requester) else None


def _result(reason: str = "", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": False,
        "blocked": True,
        "state": STATE_BLOCKED,
        "event_id": "",
        "stock_code": "",
        "response_intent": "",
        "cancel_requested": 0,
        "cancel_already_pending": 0,
        "cancel_complete": False,
        "remaining_buy_pending_qty": None,
        "holding_confirmed": False,
        "holding_quantity": None,
        "ready_for_liquidation": False,
        "reason": _text(reason),
    }
    result.update(updates)
    return result


def _ingress_contains_event(
    ingress_service: BufferResponseIngressStateService,
    event: Mapping[str, object],
) -> tuple[bool, str]:
    read_result = ingress_service.read_snapshot()
    snapshot = read_result.get("snapshot")
    if read_result.get("ok") is not True or not isinstance(snapshot, Mapping):
        return False, _text(read_result.get("reason")) or "INGRESS_UNAVAILABLE"
    checkpoints = snapshot.get("checkpoints")
    if not isinstance(checkpoints, Mapping):
        return False, "INGRESS_CHECKPOINTS_INVALID"
    matches = [
        checkpoint
        for checkpoint in checkpoints.values()
        if isinstance(checkpoint, Mapping)
        and checkpoint.get("account_no") == event.get("account_no")
        and checkpoint.get("trading_day") == event.get("trading_day")
    ]
    if len(matches) != 1:
        return False, "INGRESS_ACCOUNT_DAY_EVIDENCE_UNAVAILABLE"
    checkpoint = matches[0]
    try:
        committed_sequence = int(checkpoint.get("last_event_sequence"))
        event_sequence = int(event.get("event_sequence"))
    except (TypeError, ValueError):
        return False, "INGRESS_EVENT_SEQUENCE_INVALID"
    if committed_sequence < event_sequence:
        return False, "INGRESS_EVENT_NOT_COMMITTED"
    source_evidence = event.get("source_evidence")
    source_dict = source_evidence if isinstance(source_evidence, Mapping) else {}
    new_ids = {_text(item) for item in source_dict.get("new_contributing_buy_ids", ())}
    seen_ids = {_text(item) for item in checkpoint.get("seen_contributing_buy_ids", ())}
    if not new_ids or not new_ids.issubset(seen_ids):
        return False, "INGRESS_CONTRIBUTOR_EVIDENCE_MISMATCH"
    return True, ""


class BufferResponseImmediateLiquidationPreparer:
    """Resume one OWNED IMMEDIATE event up to reconciled holding readiness."""

    def __init__(
        self,
        *,
        ownership_service: BufferResponseOwnershipService | None = None,
        ingress_service: BufferResponseIngressStateService | None = None,
        project_root: str | Path = PROJECT_ROOT,
        order_queue_path: str | Path = ORDER_QUEUE_PATH,
        positions_path: str | Path = POSITIONS_PATH,
        broker_holdings_path: str | Path = BROKER_HOLDINGS_PATH,
        cancel_requester: Callable[..., Mapping[str, object]] | None = None,
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
        self._cancel_requester = cancel_requester
        self._holding_resolver = holding_resolver

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
        base = {"event_id": identity}
        if not identity or not account or not day:
            return _result("EVENT_OR_ACCOUNT_IDENTITY_UNAVAILABLE", **base)

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
        if intent == RESPONSE_INTENT_EARLY_CLOSE:
            return _result("EARLY_CLOSE_NOT_HANDLED_BY_IMMEDIATE_PREPARER", **base)
        if intent != RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED:
            return _result("OWNERSHIP_RESPONSE_INTENT_UNSUPPORTED", **base)
        if not stock_code:
            return _result("OWNERSHIP_SELECTED_STOCK_UNAVAILABLE", **base)

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
            state = _read_json_object(stock_dir / "state.json")
            config = _read_json_object(stock_dir / "config.json")
            queue_document = _read_json_object(self.order_queue_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "RUNTIME_READ_FAILED", **base)

        stock_orders, orders_reason = _stock_orders(
            queue_document,
            account_no=account,
            stock_code=stock_code,
        )
        if stock_orders is None:
            return _result(orders_reason, **base)

        # A same-event routine EARLY_CLOSE may be promoted by the interval
        # contract.  Every other close/SELL conflict remains fail-closed.
        same_event_market_close = (
            _text(state.get("operation_command_mode")).upper() == "EARLY_CLOSE"
            and _text(state.get("operation_command_source"))
            == buffer_response_command_source(identity)
            and _text(state.get("early_close_source"))
            == buffer_response_command_source(identity)
            and normalize_direct_close_policy_alias(
                state.get("early_close_method")
            )
            == POLICY_MARKET
        )
        conflict = ""
        same_event_routine_close = (
            _text(state.get("operation_command_mode")).upper() == "EARLY_CLOSE"
            and _text(state.get("operation_command_source"))
            == buffer_response_command_source(identity)
            and _text(state.get("early_close_source"))
            == buffer_response_command_source(identity)
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
                expected_source=buffer_response_command_source(identity),
                expected_command_id=deterministic_buffer_early_close_command_id(identity),
            )
        elif not same_event_market_close:
            conflict = existing_close_or_sell_exclusion_reason(
                stock_code=stock_code,
                state=state,
                config=config,
                orders=stock_orders,
            )
        else:
            conflict = _active_sell_order_reason(stock_code, stock_orders)
        if conflict:
            return _result(f"EXISTING_CLOSE_CONFLICT:{conflict}", **base)

        readiness = project_buy_only_cancel_readiness(
            queue_document,
            account_no=account,
            stock_code=stock_code,
        )
        if readiness.get("available") is not True:
            return _result(
                _text(readiness.get("reason")) or "BUY_CANCEL_READINESS_UNCERTAIN",
                **base,
                remaining_buy_pending_qty=readiness.get("pending_buy_quantity"),
            )

        cancel_requested = 0
        cancel_pending = 0
        if readiness.get("ready") is not True:
            if not callable(self._cancel_requester):
                return _result(
                    "BUY_CANCEL_REQUESTER_UNAVAILABLE",
                    **base,
                    remaining_buy_pending_qty=readiness.get("pending_buy_quantity"),
                )
            routine_instance_id = _text(config.get("assigned_routine_instance_id"))
            started_at = _text(state.get("trade_started_at"))
            if not routine_instance_id or not started_at:
                return _result(
                    "BUY_CANCEL_SCOPE_IDENTITY_UNAVAILABLE",
                    **base,
                    remaining_buy_pending_qty=readiness.get("pending_buy_quantity"),
                )
            cancel_result = self._cancel_requester(
                stock_code,
                routine_instance_id,
                trading_day=day,
                started_at=started_at,
                side_scope=CANCEL_SIDE_SCOPE_BUY_ONLY,
                account_no=account,
            )
            if not isinstance(cancel_result, Mapping) or cancel_result.get("ok") is not True:
                reason = "BUY_ONLY_CANCEL_REQUEST_BLOCKED"
                if isinstance(cancel_result, Mapping):
                    blocked_reasons = cancel_result.get("blocked_reasons")
                    if isinstance(blocked_reasons, (list, tuple)) and blocked_reasons:
                        reason = "; ".join(_text(item) for item in blocked_reasons)
                return _result(reason, **base)
            cancel_requested = int(cancel_result.get("cancel_requested") or 0)
            cancel_pending = int(cancel_result.get("cancel_pending") or 0)
            try:
                queue_document = _read_json_object(self.order_queue_path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return _result(
                    _text(exc) or "ORDER_QUEUE_READ_BACK_FAILED",
                    **base,
                    cancel_requested=cancel_requested,
                    cancel_already_pending=cancel_pending,
                )
            readiness = project_buy_only_cancel_readiness(
                queue_document,
                account_no=account,
                stock_code=stock_code,
            )
            if readiness.get("available") is not True:
                return _result(
                    _text(readiness.get("reason"))
                    or "BUY_CANCEL_READINESS_UNCERTAIN",
                    **base,
                    cancel_requested=cancel_requested,
                    cancel_already_pending=cancel_pending,
                    remaining_buy_pending_qty=readiness.get("pending_buy_quantity"),
                )

        common = {
            **base,
            "cancel_requested": cancel_requested,
            "cancel_already_pending": cancel_pending,
            "remaining_buy_pending_qty": readiness.get("pending_buy_quantity"),
        }
        if readiness.get("ready") is not True:
            return _result(
                _text(readiness.get("reason")) or STATE_WAITING_BUY_CANCEL,
                **common,
                ok=True,
                blocked=False,
                state=STATE_WAITING_BUY_CANCEL,
            )

        holding = self._holding_resolver(
            stock_code,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
        )
        if not isinstance(holding, Mapping) or holding.get("ok") is not True:
            reasons = holding.get("blocked_reasons") if isinstance(holding, Mapping) else ()
            reason = "; ".join(_text(item) for item in reasons or ())
            return _result(
                reason or "BLOCKED_HOLDING_UNCERTAIN",
                **common,
                cancel_complete=True,
            )
        quantity = holding.get("resolved_liquidation_qty")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            return _result(
                "BLOCKED_HOLDING_UNCERTAIN",
                **common,
                cancel_complete=True,
            )
        if quantity == 0:
            return _result(
                "",
                **common,
                ok=True,
                blocked=False,
                state=STATE_ALREADY_FLAT,
                cancel_complete=True,
                holding_confirmed=True,
                holding_quantity=0,
            )
        return _result(
            "",
            **common,
            ok=True,
            blocked=False,
            state=STATE_READY,
            cancel_complete=True,
            holding_confirmed=True,
            holding_quantity=quantity,
            ready_for_liquidation=True,
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
            return {
                "ok": False,
                "attempted": 0,
                "results": (),
                "reason": "OWNERSHIP_EVENTS_INVALID",
            }
        event_ids = sorted(
            _text(event_id)
            for event_id, event in events.items()
            if isinstance(event, Mapping)
            and event.get("account_no") == account
            and event.get("trading_day") == day
            and event.get("status") == STATUS_OWNED
            and event.get("response_intent")
            == RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
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
                (
                    _text(result.get("reason"))
                    for result in results
                    if result.get("ok") is not True
                ),
                "",
            ),
        }


def prepare_main_window_buffer_immediate_liquidation(
    window: object,
    *,
    event_id: object,
    ownership_service: BufferResponseOwnershipService | None = None,
    ingress_service: BufferResponseIngressStateService | None = None,
) -> dict[str, object]:
    try:
        account_reader = getattr(window, "selected_account_no", None)
        account = _text(account_reader()) if callable(account_reader) else ""
        context = production_recovery_registry.snapshot()
        day = _text(getattr(getattr(context, "identity", None), "trading_day", ""))
        requester = _main_window_cancel_requester(window)
        preparer = BufferResponseImmediateLiquidationPreparer(
            ownership_service=ownership_service,
            ingress_service=ingress_service,
            cancel_requester=requester,
        )
        return preparer.dispatch_event(
            event_id=event_id,
            account_no=account,
            trading_day=day,
            recovery_context=context,
        )
    except Exception as exc:
        return _result(
            _text(exc) or "MAIN_WINDOW_IMMEDIATE_LIQUIDATION_PREPARATION_FAILED"
        )


def resume_main_window_buffer_immediate_liquidation_events(
    window: object,
    *,
    ownership_service: BufferResponseOwnershipService | None = None,
    ingress_service: BufferResponseIngressStateService | None = None,
) -> dict[str, object]:
    """Re-read active IMMEDIATE ownership after a committed Chejan cycle."""

    try:
        account_reader = getattr(window, "selected_account_no", None)
        account = _text(account_reader()) if callable(account_reader) else ""
        context = production_recovery_registry.snapshot()
        day = _text(getattr(getattr(context, "identity", None), "trading_day", ""))
        requester = _main_window_cancel_requester(window)
        return BufferResponseImmediateLiquidationPreparer(
            ownership_service=ownership_service,
            ingress_service=ingress_service,
            cancel_requester=requester,
        ).resume_owned_events(
            account_no=account,
            trading_day=day,
            recovery_context=context,
        )
    except Exception as exc:
        return {
            "ok": False,
            "attempted": 0,
            "results": (),
            "reason": _text(exc) or "MAIN_WINDOW_IMMEDIATE_PREPARATION_RESUME_FAILED",
        }


def resume_main_window_buffer_immediate_liquidation_preparation(
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
            return {
                "ok": False,
                "attempted": 0,
                "results": (),
                "reason": "RECOVERY_IDENTITY_STALE",
            }
        requester = _main_window_cancel_requester(window)
        return BufferResponseImmediateLiquidationPreparer(
            cancel_requester=requester,
        ).resume_owned_events(
            account_no=account,
            trading_day=day,
            recovery_context=production_recovery_registry.snapshot(),
        )
    except Exception as exc:
        return {
            "ok": False,
            "attempted": 0,
            "results": (),
            "reason": _text(exc) or "RECOVERY_IMMEDIATE_PREPARATION_RESUME_FAILED",
        }


__all__ = [
    "BufferResponseImmediateLiquidationPreparer",
    "STATE_ALREADY_FLAT",
    "STATE_BLOCKED",
    "STATE_READY",
    "STATE_WAITING_BUY_CANCEL",
    "prepare_main_window_buffer_immediate_liquidation",
    "resume_main_window_buffer_immediate_liquidation_events",
    "resume_main_window_buffer_immediate_liquidation_preparation",
]
