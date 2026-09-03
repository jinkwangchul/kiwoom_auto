# -*- coding: utf-8 -*-
"""Durable continuation policy for the isolated Indicator Follow Mock domain.

This module deliberately owns no Production mutation dependency.  It inspects
Mock ledgers, records deterministic continuation evidence, and uses only the
Phase-3 virtual cancel transitions.  Plan materialization remains in the
Phase-4 adapter so every new generation re-enters the same Mock execution path.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable

from execution_buy_exit import evaluate_buy_exit_policy
from execution_price_comparison import evaluate_percent_comparison, resolve_price_source
from execution_price_reset import build_buy_generation_intents, build_sell_generation_intents
from mock_validation_contract import (
    ORDER_CANCELED,
    ORDER_CANCEL_PENDING,
    ORDER_FILLED,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    payload_hash,
)
from mock_validation_market_data import MockMarketSnapshot
from mock_validation_repository import MockValidationRepository
from mock_validation_virtual_execution import MockVirtualExecutionEngine


ACTION_CANCEL_EFFECT = "CANCEL_EFFECT"
ACTION_CANCEL_REQUEST = "CANCEL_REQUEST"
ACTION_EXIT = "EXIT"
ACTION_FINAL_RESIDUAL = "FINAL_RESIDUAL"
ACTION_NONE = "NONE"
ACTION_REPLAN = "REPLAN"
ACTION_REPEAT = "REPEAT"

_LIVE = {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}
_TERMINAL = {ORDER_FILLED, ORDER_CANCELED, "REJECTED"}


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    try:
        parsed = datetime.fromisoformat(clean_text(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _intent(plan: dict[str, Any]) -> dict[str, Any]:
    children = plan.get("children")
    if not isinstance(children, list) or not children:
        return {}
    value = children[0].get("intent") if isinstance(children[0], dict) else None
    return deepcopy(value) if isinstance(value, dict) else {}


def _position(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
    matches = [item for item in document["positions"] if item.get("routine_instance_id") == instance_id]
    if len(matches) != 1:
        raise MockValidationError("MOCK_CONTINUATION_POSITION_IDENTITY_INVALID")
    return matches[0]


def _order_price(plan: dict[str, Any]) -> float | None:
    intent = _intent(plan)
    for source in (
        intent,
        intent.get("child_plan") if isinstance(intent.get("child_plan"), dict) else {},
        plan.get("hoga") if isinstance(plan.get("hoga"), dict) else {},
        plan.get("ratio") if isinstance(plan.get("ratio"), dict) else {},
    ):
        for key in ("price", "planned_price", "order_price", "base_price"):
            value = _positive(source.get(key))
            if value is not None:
                return value
    return None


def _current_price(market: MockMarketSnapshot | None, trade_fresh: bool) -> float | None:
    if market is None or market.trade is None or not trade_fresh:
        return None
    return _positive(market.trade.current_price)


def evaluate_price_reset_policy(
    policy: dict[str, Any], *, order_price: float | None,
    current_price: float | None, average_price: float | None,
) -> dict[str, Any]:
    """Pure reset evaluation matching Production left-to-right semantics."""
    if not policy or policy.get("enabled") is False:
        return {"active": False, "triggered": False, "reason": "POLICY_DISABLED"}
    left_source = clean_text(policy.get("left_source")).upper()
    right_source = clean_text(policy.get("right_source")).upper()
    if "CURRENT_PRICE" in {left_source, right_source} and current_price is None:
        return {"active": True, "triggered": False, "reason": "CURRENT_PRICE_UNAVAILABLE"}
    left = resolve_price_source(
        left_source, order_price=order_price, current_price=current_price,
        average_price=average_price,
    )
    right = resolve_price_source(
        right_source, order_price=order_price, current_price=current_price,
        average_price=average_price,
    )
    threshold = _positive(policy.get("threshold_percent"))
    if left is None or right is None or threshold is None:
        raise MockValidationError("MOCK_PRICE_RESET_SOURCE_INVALID")
    triggered, observed = evaluate_percent_comparison(
        left=left, right=right,
        direction=clean_text(policy.get("direction")).upper(),
        compare=clean_text(policy.get("compare")).upper(),
        threshold=threshold,
    )
    if triggered is None or observed is None:
        raise MockValidationError("MOCK_PRICE_RESET_POLICY_INVALID")
    evidence = {
        "left_source": left_source, "right_source": right_source,
        "left_price": left, "right_price": right,
        "threshold_percent": threshold, "observed_percent": observed,
        "direction": clean_text(policy.get("direction")).upper(),
        "compare": clean_text(policy.get("compare")).upper(),
    }
    evidence["snapshot_hash"] = payload_hash(evidence)
    return {"active": True, "triggered": bool(triggered), "reason": "MATCHED" if triggered else "NOT_MATCHED", **evidence}


def timeout_due(order: dict[str, Any], policy: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
    """Return deterministic timeout evidence using the Mock OPEN acceptance time."""
    if order.get("state") not in {ORDER_OPEN, ORDER_PARTIAL_FILL}:
        return {"eligible": False, "reason": "ORDER_NOT_OPEN"}
    if int(order.get("remaining_qty", 0) or 0) <= 0:
        return {"eligible": False, "reason": "NO_REMAINING_QUANTITY"}
    timeout_ms = policy.get("timeout_ms")
    if clean_text(policy.get("scope")).upper() not in {"EACH", "BATCH"}:
        raise MockValidationError("MOCK_TIMEOUT_SCOPE_INVALID")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, (int, float)) or timeout_ms < 0:
        raise MockValidationError("MOCK_TIMEOUT_POLICY_INVALID")
    anchor = _aware(order.get("mock_opened_at") or order.get("accepted_at"))
    if anchor is None or as_of.tzinfo is None:
        raise MockValidationError("MOCK_TIMEOUT_ANCHOR_INVALID")
    due = anchor + timedelta(milliseconds=float(timeout_ms))
    return {
        "eligible": as_of >= due,
        "reason": "TIMEOUT_REACHED" if as_of >= due else "TIMEOUT_NOT_REACHED",
        "anchor": "MOCK_OPENED_AT", "anchor_at": anchor.isoformat(timespec="microseconds"),
        "due_at": due.isoformat(timespec="microseconds"),
        "remaining_qty": int(order["remaining_qty"]),
    }


class MockIndicatorFollowContinuationCoordinator:
    """Inspect and apply exactly one durable continuation decision per cycle."""

    def __init__(
        self, repository: MockValidationRepository, engine: MockVirtualExecutionEngine,
        *, now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self._now = now_factory or (lambda: datetime.now().astimezone())

    @staticmethod
    def state(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
        root = document["progression_by_instance"][instance_id]
        value = root.setdefault(
            "indicator_follow_mock_continuation",
            {
                "version": 1, "cycles": {}, "pending_reset": None,
                "buy_exit": None, "sell_repeat_exit": None,
                "reset_snapshots": [], "repeat_snapshots": [],
                "final_residual_snapshots": [], "historical_evidence": [],
            },
        )
        if not isinstance(value.get("cycles"), dict):
            raise MockValidationError("MOCK_CONTINUATION_STATE_CORRUPTED")
        return value

    @staticmethod
    def _plan_orders(document: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
        ids = {
            child.get("mock_order_id") for child in plan.get("children", [])
            if isinstance(child, dict) and child.get("mock_order_id")
        }
        orders = [item for item in document["orders"] if item.get("mock_order_id") in ids]
        if len(orders) != len(ids):
            raise MockValidationError("MOCK_CONTINUATION_ORDER_IDENTITY_MISSING")
        return orders

    @staticmethod
    def _all_instance_orders(document: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
        return [item for item in document["orders"] if item.get("routine_instance_id") == instance_id]

    @staticmethod
    def validate_integrity(document: dict[str, Any], instance_id: str) -> None:
        adapter = document["progression_by_instance"][instance_id].get("indicator_follow_mock_adapter", {})
        plans = adapter.get("plans") if isinstance(adapter, dict) else []
        if not isinstance(plans, list):
            raise MockValidationError("MOCK_CONTINUATION_PLAN_LEDGER_INVALID")
        plan_ids = [clean_text(item.get("plan_id")) for item in plans if isinstance(item, dict)]
        if len(plan_ids) != len(plans) or any(not value for value in plan_ids) or len(plan_ids) != len(set(plan_ids)):
            raise MockValidationError("MOCK_CONTINUATION_PLAN_IDENTITY_CONFLICT")
        child_ids: set[str] = set()
        order_owners: dict[str, str] = {}
        generations: set[tuple[str, int, int]] = set()
        for plan in plans:
            process_id = clean_text(plan.get("execution_process_id"))
            generation = plan.get("generation")
            round_value = plan.get("round")
            if not process_id or isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise MockValidationError("MOCK_CONTINUATION_GENERATION_IDENTITY_INVALID")
            if plan.get("side") == "BUY" and (isinstance(round_value, bool) or not isinstance(round_value, int) or round_value <= 0):
                raise MockValidationError("MOCK_CONTINUATION_BUY_ROUND_INVALID")
            identity = (process_id, int(round_value or 0), generation)
            if identity in generations:
                raise MockValidationError("MOCK_CONTINUATION_GENERATION_DUPLICATE")
            generations.add(identity)
            for child in plan.get("children", []):
                child_id = clean_text(child.get("child_id"))
                if not child_id or child_id in child_ids:
                    raise MockValidationError("MOCK_CONTINUATION_CHILD_IDENTITY_CONFLICT")
                child_ids.add(child_id)
                order_id = clean_text(child.get("mock_order_id"))
                if order_id:
                    if order_id in order_owners and order_owners[order_id] != plan["plan_id"]:
                        raise MockValidationError("MOCK_CONTINUATION_ORDER_OWNER_CONFLICT")
                    order_owners[order_id] = plan["plan_id"]
        instance_order_ids = {
            clean_text(item.get("mock_order_id")) for item in document["orders"]
            if item.get("routine_instance_id") == instance_id
        }
        if not set(order_owners).issubset(instance_order_ids):
            raise MockValidationError("MOCK_CONTINUATION_ORDER_IDENTITY_MISSING")

    def _record(
        self, session_id: str, instance_id: str, cycle_id: str,
        *, action: str, timestamp: str, evidence: dict[str, Any],
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = self.state(document, instance_id)
            existing = state["cycles"].get(cycle_id)
            record = {"action": action, "recorded_at": timestamp, "evidence": deepcopy(evidence)}
            if existing is not None and existing != record:
                raise MockValidationError("MOCK_CONTINUATION_CYCLE_CONFLICT")
            state["cycles"][cycle_id] = record
            return document

        return self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]

    def _event(
        self, document: dict[str, Any], instance_id: str, event_type: str,
        identity: str, timestamp: str, evidence: dict[str, Any], reason: str = "",
    ) -> None:
        self.repository.append_event({
            "event_id": deterministic_mock_identity(
                "ME", document["session"]["validation_session_id"], instance_id,
                "CONTINUATION", identity, event_type,
            ),
            "validation_session_id": document["session"]["validation_session_id"],
            "stock_code": document["session"]["stock_code"],
            "routine_instance_id": instance_id,
            "event_type": event_type, "timestamp": timestamp,
            "reason_code": reason, "payload": deepcopy(evidence),
        })

    def _set_evidence(
        self, session_id: str, instance_id: str, key: str, value: dict[str, Any],
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = self.state(document, instance_id)
            existing = state.get(key)
            if existing is not None and existing != value:
                existing_cycle = clean_text(existing.get("cycle_identity")) if isinstance(existing, dict) else ""
                next_cycle = clean_text(value.get("cycle_identity"))
                if key in {"buy_exit", "sell_repeat_exit"} and existing_cycle and next_cycle and existing_cycle != next_cycle:
                    state["historical_evidence"].append({"kind": key, "evidence": deepcopy(existing)})
                else:
                    raise MockValidationError(f"MOCK_CONTINUATION_{key.upper()}_CONFLICT")
            state[key] = deepcopy(value)
            return document

        return self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]

    def _cancel_action(
        self, session_id: str, instance_id: str, cycle_id: str,
        *, orders: list[dict[str, Any]], as_of: datetime, reason: str,
    ) -> dict[str, Any]:
        timestamp = as_of.isoformat(timespec="microseconds")
        pending = [item for item in orders if item.get("state") == ORDER_CANCEL_PENDING]
        if pending:
            order = pending[0]
            command = deterministic_mock_identity("MC", session_id, instance_id, order["mock_order_id"], "CANCEL_EFFECT")
            result = self.engine.finalize_cancel(session_id, order["mock_order_id"], command_id=command)
            evidence = {"mock_order_id": order["mock_order_id"], "remaining_qty": order["remaining_qty"], "reason": reason}
            document = self._record(session_id, instance_id, cycle_id, action=ACTION_CANCEL_EFFECT, timestamp=timestamp, evidence=evidence)
            self._event(document, instance_id, "VIRTUAL_CANCEL_EFFECT_CONFIRMED", command, timestamp, evidence)
            return {"status": "PROGRESSED", "reason": "CANCEL_EFFECT_CONFIRMED", "action": ACTION_CANCEL_EFFECT,
                    "orders": [deepcopy(result.get("order"))], "fills": [], "document": document}
        open_orders = [item for item in orders if item.get("state") in {ORDER_OPEN, ORDER_PARTIAL_FILL}]
        if not open_orders:
            return {"status": "NOOP", "reason": "NO_CANCEL_TARGET", "action": ACTION_NONE, "orders": [], "fills": []}
        affected: list[dict[str, Any]] = []
        for order in open_orders:
            command = deterministic_mock_identity("MC", session_id, instance_id, order["mock_order_id"], "CANCEL_REQUEST")
            result = self.engine.request_cancel(session_id, order["mock_order_id"], command_id=command)
            if isinstance(result.get("order"), dict):
                affected.append(deepcopy(result["order"]))
        evidence = {
            "reason": reason,
            "orders": [{"mock_order_id": item["mock_order_id"], "remaining_qty": item["remaining_qty"]} for item in affected],
        }
        document = self._record(session_id, instance_id, cycle_id, action=ACTION_CANCEL_REQUEST, timestamp=timestamp, evidence=evidence)
        self._event(document, instance_id, "VIRTUAL_CANCEL_REQUESTED", cycle_id, timestamp, evidence, reason)
        return {"status": "PROGRESSED", "reason": reason, "action": ACTION_CANCEL_REQUEST,
                "orders": affected, "fills": [], "document": document}

    @staticmethod
    def _completed_buy_repeats(plans: list[dict[str, Any]]) -> tuple[int, datetime | None]:
        rounds: dict[int, list[dict[str, Any]]] = {}
        for plan in plans:
            if plan.get("side") == "BUY" and int(plan.get("round", 0) or 0) > 1:
                rounds.setdefault(int(plan["round"]), []).append(plan)
        completed = 0
        started: list[datetime] = []
        for _, group in rounds.items():
            if all(item.get("state") == "COMPLETED" for item in group) and any(int(item.get("filled_qty", 0) or 0) > 0 for item in group):
                completed += 1
            for item in group:
                parsed = _aware(item.get("plan_started_at"))
                if parsed is not None:
                    started.append(parsed)
        return completed, min(started) if started else None

    def inspect_active(
        self, session_id: str, *, routine_instance_id: str,
        evaluation_cycle_id: str, evaluated_at: datetime,
        market: MockMarketSnapshot | None, trade_fresh: bool,
    ) -> dict[str, Any] | None:
        """Apply the highest-priority active-plan continuation action, if any."""
        document = self.repository.read_session(session_id)
        instance_id = clean_text(routine_instance_id)
        self.validate_integrity(document, instance_id)
        state = self.state(document, instance_id)
        if evaluation_cycle_id in state["cycles"]:
            return {"status": "NOOP", "reason": "MOCK_CONTINUATION_CYCLE_ALREADY_PROCESSED", "action": ACTION_NONE, "orders": [], "fills": []}
        adapter = document["progression_by_instance"][instance_id].get("indicator_follow_mock_adapter", {})
        plans = adapter.get("plans") if isinstance(adapter, dict) else []
        plans = plans if isinstance(plans, list) else []
        active = [item for item in plans if item.get("state") != "COMPLETED"]
        if len(active) > 1:
            raise MockValidationError("MOCK_MULTIPLE_ACTIVE_EXECUTION_PLANS")
        plan = active[0] if active else None
        orders = self._plan_orders(document, plan) if plan else []
        all_orders = self._all_instance_orders(document, instance_id)
        pending = [item for item in all_orders if item.get("state") == ORDER_CANCEL_PENDING]
        if pending:
            return self._cancel_action(
                session_id, instance_id, evaluation_cycle_id, orders=pending,
                as_of=evaluated_at, reason="ACTIVE_CANCEL_EFFECT_PENDING",
            )
        if plan is None:
            return None
        intent = _intent(plan)
        side = clean_text(plan.get("side")).upper()
        cycle_identity = clean_text(plan.get("cycle_scope_identity"))
        cycle_plans = [
            item for item in plans
            if clean_text(item.get("cycle_scope_identity")) == cycle_identity
        ]
        current = _current_price(market, trade_fresh)
        position = _position(document, instance_id)
        average = _positive(position.get("average_price"))

        # Highest semantic precedence: BUY Exit / SELL Repeat Exit.
        if side == "BUY":
            policy = intent.get("buy_exit_policy") if isinstance(intent.get("buy_exit_policy"), dict) else {}
            existing_buy_exit = state.get("buy_exit") if isinstance(state.get("buy_exit"), dict) else None
            if existing_buy_exit is not None and clean_text(existing_buy_exit.get("cycle_identity")) == cycle_identity:
                live_orders = [item for item in orders if item.get("state") in _LIVE]
                return self._cancel_action(session_id, instance_id, evaluation_cycle_id, orders=live_orders,
                                           as_of=evaluated_at, reason="BUY_EXIT_ACTIVE") if live_orders else {
                    "status": "WAIT", "reason": "BUY_EXIT_ACTIVE", "action": ACTION_EXIT, "orders": [], "fills": []}
            if policy:
                completed, repeat_started = self._completed_buy_repeats(cycle_plans)
                result = evaluate_buy_exit_policy(
                    policy=policy, completed_repeat_count=completed,
                    repeat_started_at=repeat_started, order_price=_order_price(plan),
                    current_price=current, average_price=average, now=evaluated_at,
                )
                if result.get("triggered") is True:
                    evidence = {**result, "cycle_identity": cycle_identity,
                                "plan_id": plan["plan_id"], "triggered_at": evaluated_at.isoformat(timespec="microseconds")}
                    updated = self._set_evidence(session_id, instance_id, "buy_exit", evidence)
                    self._event(updated, instance_id, "BUY_EXIT_TRIGGERED", result["snapshot_hash"], evidence["triggered_at"], evidence)
                    if any(item.get("state") in {ORDER_OPEN, ORDER_PARTIAL_FILL} for item in orders):
                        return self._cancel_action(session_id, instance_id, evaluation_cycle_id, orders=orders,
                                                   as_of=evaluated_at, reason="BUY_EXIT_CANCEL_REQUIRED")
                    document = self._record(session_id, instance_id, evaluation_cycle_id, action=ACTION_EXIT,
                                            timestamp=evidence["triggered_at"], evidence=evidence)
                    self._event(document, instance_id, "BUY_EXIT_CONFIRMED", result["snapshot_hash"], evidence["triggered_at"], evidence)
                    return {"status": "PROGRESSED", "reason": "BUY_EXIT_TRIGGERED", "action": ACTION_EXIT, "orders": [], "fills": [], "document": document}
        else:
            repeat = intent.get("sell_repeat_policy") if isinstance(intent.get("sell_repeat_policy"), dict) else {}
            exit_policy = repeat.get("exit_policy") if isinstance(repeat.get("exit_policy"), dict) else {}
            existing_sell_exit = state.get("sell_repeat_exit") if isinstance(state.get("sell_repeat_exit"), dict) else None
            current_sell_exited = existing_sell_exit is not None and clean_text(existing_sell_exit.get("cycle_identity")) == cycle_identity
            if not current_sell_exited and exit_policy:
                repeat_plans = [
                    item for item in cycle_plans
                    if item.get("side") == "SELL" and item.get("continuation_kind") == "SELL_REPEAT"
                ]
                repeat_times = [_aware(item.get("plan_started_at")) for item in repeat_plans]
                repeat_started = min((item for item in repeat_times if item is not None), default=None)
                result = evaluate_buy_exit_policy(
                    policy=exit_policy, completed_repeat_count=sum(item.get("state") == "COMPLETED" for item in repeat_plans),
                    repeat_started_at=repeat_started, order_price=_order_price(plan),
                    current_price=current, average_price=average, now=evaluated_at,
                )
                if result.get("triggered") is True:
                    evidence = {**result, "cycle_identity": cycle_identity, "plan_id": plan["plan_id"], "triggered_at": evaluated_at.isoformat(timespec="microseconds")}
                    updated = self._set_evidence(session_id, instance_id, "sell_repeat_exit", evidence)
                    self._event(updated, instance_id, "SELL_REPEAT_EXIT_TRIGGERED", result["snapshot_hash"], evidence["triggered_at"], evidence)
                    document = self._record(session_id, instance_id, evaluation_cycle_id, action=ACTION_EXIT,
                                            timestamp=evidence["triggered_at"], evidence=evidence)
                    return {"status": "PROGRESSED", "reason": "SELL_REPEAT_EXIT_TRIGGERED", "action": ACTION_EXIT, "orders": [], "fills": [], "document": document}

        # Price reset precedes timeout and slice progression.
        reset_key = "buy_price_reset_policy" if side == "BUY" else "sell_price_reset_policy"
        reset_policy = intent.get(reset_key) if isinstance(intent.get(reset_key), dict) else {}
        pending_reset = state.get("pending_reset")
        reset_evidence: dict[str, Any] | None = None
        if isinstance(pending_reset, dict) and pending_reset.get("source_plan_id") == plan.get("plan_id"):
            reset_evidence = pending_reset
        elif reset_policy:
            evaluated = evaluate_price_reset_policy(
                reset_policy, order_price=_order_price(plan), current_price=current,
                average_price=average,
            )
            trigger_hash = payload_hash([
                plan.get("execution_process_id"), plan.get("generation"),
                market.snapshot_identity if market else "", evaluated.get("snapshot_hash"),
            ])
            if evaluated.get("triggered") is True and trigger_hash not in state["reset_snapshots"]:
                reset_evidence = {
                    **evaluated, "comparison_snapshot_hash": evaluated["snapshot_hash"],
                    "snapshot_hash": trigger_hash, "side": side, "source_plan_id": plan["plan_id"],
                    "source_generation": int(plan.get("generation", 0)),
                    "round": int(plan.get("round", 0)),
                    "market_identity": market.snapshot_identity if market else "",
                    "triggered_at": evaluated_at.isoformat(timespec="microseconds"),
                }
                updated = self._set_evidence(session_id, instance_id, "pending_reset", reset_evidence)
                self._event(updated, instance_id, "PRICE_RESET_TRIGGERED", trigger_hash, reset_evidence["triggered_at"], reset_evidence)
        if reset_evidence is not None:
            if any(item.get("state") in {ORDER_OPEN, ORDER_PARTIAL_FILL} for item in orders):
                return self._cancel_action(session_id, instance_id, evaluation_cycle_id, orders=orders,
                                           as_of=evaluated_at, reason=f"{side}_PRICE_RESET_CANCEL_REQUIRED")
            return {"status": "READY", "reason": f"{side}_PRICE_RESET_REPLAN_READY", "action": ACTION_REPLAN,
                    "evidence": deepcopy(reset_evidence), "source_plan": deepcopy(plan), "orders": [], "fills": []}

        timeout_policy = intent.get("unfilled_timeout_policy") if isinstance(intent.get("unfilled_timeout_policy"), dict) else {}
        if timeout_policy:
            due_orders = []
            for order in orders:
                evidence = timeout_due(order, timeout_policy, as_of=evaluated_at)
                if evidence.get("eligible") is True:
                    due_orders.append(order)
            if due_orders:
                scope = clean_text(timeout_policy.get("scope")).upper()
                targets = [item for item in orders if item.get("state") in {ORDER_OPEN, ORDER_PARTIAL_FILL}] if scope == "BATCH" else due_orders[:1]
                timeout_identity = payload_hash([plan["plan_id"], [item["mock_order_id"] for item in targets], timeout_policy.get("timeout_ms")])
                self._event(document, instance_id, "ORDER_TIMEOUT_DETECTED", timeout_identity,
                            evaluated_at.isoformat(timespec="microseconds"), {"plan_id": plan["plan_id"], "orders": [item["mock_order_id"] for item in targets]})
                return self._cancel_action(session_id, instance_id, evaluation_cycle_id, orders=targets,
                                           as_of=evaluated_at, reason="ORDER_TIMEOUT_DETECTED")
        return None

    def materialize_reset(
        self, session_id: str, *, routine_instance_id: str,
        evaluation_cycle_id: str, evaluated_at: datetime,
        source_plan: dict[str, Any], market: MockMarketSnapshot | None,
        trade_fresh: bool, execution_budget: float | None,
    ) -> dict[str, Any]:
        document = self.repository.read_session(session_id)
        instance_id = clean_text(routine_instance_id)
        self.validate_integrity(document, instance_id)
        state = self.state(document, instance_id)
        evidence = state.get("pending_reset")
        if not isinstance(evidence, dict) or evidence.get("source_plan_id") != source_plan.get("plan_id"):
            raise MockValidationError("MOCK_RESET_EVIDENCE_MISSING")
        source_hash = clean_text(evidence.get("snapshot_hash"))
        if source_hash in state["reset_snapshots"]:
            return {"status": "NOOP", "reason": "MOCK_RESET_SNAPSHOT_ALREADY_USED", "action": ACTION_NONE}
        intent = _intent(source_plan)
        generation = int(source_plan.get("generation", 0)) + 1
        current = _current_price(market, trade_fresh)
        process_id = clean_text(source_plan.get("execution_process_id"))
        signal_id = clean_text(source_plan.get("source_signal_id"))
        if not process_id or not signal_id:
            raise MockValidationError("MOCK_RESET_PROCESS_IDENTITY_INVALID")
        if source_plan.get("side") == "BUY":
            plan_budget = sum(float(child.get("intent", {}).get("budget", 0) or 0) for child in source_plan.get("children", []))
            filled = sum(float(item["price"]) * int(item["qty"]) for item in document["fills"]
                         if item.get("mock_order_id") in {child.get("mock_order_id") for child in source_plan.get("children", [])})
            remaining_budget = max(0.0, plan_budget - filled)
            if execution_budget is not None:
                remaining_budget = min(remaining_budget, max(0.0, float(execution_budget) - float(_position(document, instance_id).get("realized_cost_basis", 0))))
            try:
                intents = build_buy_generation_intents(
                    template=intent, source_signal_id=signal_id, process_id=process_id,
                    option_snapshot_hash=clean_text(intent.get("option_snapshot_hash")) or payload_hash(intent),
                    generation=generation, buy_round=int(source_plan.get("round", 1) or 1),
                    remaining_budget=remaining_budget, current_price=current,
                    source_snapshot_hash=source_hash, generated_at=evaluated_at,
                )
            except ValueError as exc:
                reason = clean_text(exc)
                if reason in {
                    "BUY_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE",
                    "BUY_PRICE_RESET_REMAINING_BUDGET_BELOW_ONE_SHARE",
                    "BUY_PRICE_RESET_QUANTITY_BELOW_CHILD_COUNT",
                }:
                    return {"status": "WAIT", "reason": reason, "action": ACTION_NONE,
                            "orders": [], "fills": []}
                raise MockValidationError(reason or "MOCK_BUY_RESET_PLAN_INVALID") from exc
        else:
            qty = int(_position(document, instance_id)["available_qty"])
            if qty <= 0:
                return {"status": "NOOP", "reason": "MOCK_RESET_NO_SELLABLE_QUANTITY", "action": ACTION_NONE}
            if source_plan.get("mode") == "MULTI_HOGA":
                offsets = (source_plan.get("hoga") or {}).get("hoga_offsets") if isinstance(source_plan.get("hoga"), dict) else None
                if isinstance(offsets, list) and qty < len(offsets):
                    return {"status": "WAIT", "reason": "SELL_MULTI_HOGA_QUANTITY_BELOW_CHILD_COUNT",
                            "action": ACTION_NONE, "orders": [], "fills": []}
            try:
                intents = build_sell_generation_intents(
                    template=intent, source_signal_id=signal_id, process_id=process_id,
                    option_snapshot_hash=clean_text(intent.get("option_snapshot_hash")) or payload_hash(intent),
                    generation=generation, quantity=qty, current_price=current or _order_price(source_plan),
                    source_snapshot_hash=source_hash, generated_at=evaluated_at,
                )
            except ValueError as exc:
                reason = clean_text(exc)
                if reason.endswith("CURRENT_PRICE_UNAVAILABLE"):
                    return {"status": "WAIT", "reason": reason, "action": ACTION_NONE,
                            "orders": [], "fills": []}
                raise MockValidationError(reason or "MOCK_SELL_RESET_PLAN_INVALID") from exc
        return {"status": "READY", "reason": "", "action": ACTION_REPLAN,
                "intents": intents, "signal": {"signal": source_plan["side"], "source_signal_id": signal_id},
                "continuation_kind": "PRICE_RESET", "source_hash": source_hash,
                "source_plan_id": source_plan["plan_id"]}

    def terminal_decision(
        self, session_id: str, *, routine_instance_id: str,
        evaluation_cycle_id: str, evaluated_at: datetime,
        market: MockMarketSnapshot | None, trade_fresh: bool,
    ) -> dict[str, Any] | None:
        """Choose SELL repeat/exit/residual after all current orders are terminal."""
        document = self.repository.read_session(session_id)
        instance_id = clean_text(routine_instance_id)
        state = self.state(document, instance_id)
        if evaluation_cycle_id in state["cycles"]:
            return {"status": "NOOP", "reason": "MOCK_CONTINUATION_CYCLE_ALREADY_PROCESSED", "action": ACTION_NONE}
        adapter = document["progression_by_instance"][instance_id].get("indicator_follow_mock_adapter", {})
        plans = adapter.get("plans") if isinstance(adapter, dict) else []
        plans = plans if isinstance(plans, list) else []
        if not plans or any(item.get("state") != "COMPLETED" for item in plans):
            return None
        latest = plans[-1]
        cycle_identity = clean_text(latest.get("cycle_scope_identity"))
        cycle_plans = [
            item for item in plans
            if clean_text(item.get("cycle_scope_identity")) == cycle_identity
        ]
        position = _position(document, instance_id)
        qty = int(position["available_qty"])
        intent = _intent(latest)
        timestamp = evaluated_at.isoformat(timespec="microseconds")

        pending_reset = state.get("pending_reset")
        if isinstance(pending_reset, dict) and pending_reset.get("source_plan_id") == latest.get("plan_id"):
            return {"status": "READY", "reason": f"{latest.get('side')}_PRICE_RESET_REPLAN_READY",
                    "action": ACTION_REPLAN, "evidence": deepcopy(pending_reset),
                    "source_plan": deepcopy(latest), "orders": [], "fills": []}

        if latest.get("side") == "BUY":
            existing_buy_exit = state.get("buy_exit") if isinstance(state.get("buy_exit"), dict) else None
            if existing_buy_exit is not None and clean_text(existing_buy_exit.get("cycle_identity")) == cycle_identity:
                # The exit closes only BUY repeat progression for this cycle.  A
                # later routine evaluation must still be able to emit the SELL
                # that closes the holding; the adapter rejects another BUY by
                # consulting this same cycle-scoped evidence.
                return None
            exit_policy = intent.get("buy_exit_policy") if isinstance(intent.get("buy_exit_policy"), dict) else {}
            if not exit_policy:
                return None
            completed, repeat_started = self._completed_buy_repeats(cycle_plans)
            result = evaluate_buy_exit_policy(
                policy=exit_policy, completed_repeat_count=completed,
                repeat_started_at=repeat_started, order_price=_order_price(latest),
                current_price=_current_price(market, trade_fresh),
                average_price=_positive(position.get("average_price")), now=evaluated_at,
            )
            if result.get("triggered") is not True:
                return None
            evidence = {**result, "cycle_identity": cycle_identity,
                        "plan_id": latest["plan_id"], "triggered_at": timestamp}
            updated = self._set_evidence(session_id, instance_id, "buy_exit", evidence)
            self._event(updated, instance_id, "BUY_EXIT_TRIGGERED", result["snapshot_hash"], timestamp, evidence)
            recorded = self._record(session_id, instance_id, evaluation_cycle_id, action=ACTION_EXIT,
                                    timestamp=timestamp, evidence=evidence)
            self._event(recorded, instance_id, "BUY_EXIT_CONFIRMED", result["snapshot_hash"], timestamp, evidence)
            return {"status": "PROGRESSED", "reason": "BUY_EXIT_TRIGGERED", "action": ACTION_EXIT,
                    "orders": [], "fills": [], "document": recorded}

        if latest.get("side") != "SELL":
            return None
        repeat_policy = intent.get("sell_repeat_policy") if isinstance(intent.get("sell_repeat_policy"), dict) else {}

        existing_sell_exit = state.get("sell_repeat_exit") if isinstance(state.get("sell_repeat_exit"), dict) else None
        current_sell_exited = existing_sell_exit is not None and clean_text(existing_sell_exit.get("cycle_identity")) == cycle_identity
        if not current_sell_exited and repeat_policy:
            exit_policy = repeat_policy.get("exit_policy") if isinstance(repeat_policy.get("exit_policy"), dict) else {}
            if exit_policy:
                repeat_plans = [
                    item for item in cycle_plans
                    if item.get("side") == "SELL" and item.get("continuation_kind") == "SELL_REPEAT"
                ]
                times = [_aware(item.get("plan_started_at")) for item in repeat_plans]
                result = evaluate_buy_exit_policy(
                    policy=exit_policy,
                    completed_repeat_count=sum(item.get("state") == "COMPLETED" for item in repeat_plans),
                    repeat_started_at=min((item for item in times if item is not None), default=None),
                    order_price=_order_price(latest), current_price=_current_price(market, trade_fresh),
                    average_price=_positive(position.get("average_price")), now=evaluated_at,
                )
                if result.get("triggered") is True:
                    evidence = {**result, "cycle_identity": cycle_identity, "plan_id": latest["plan_id"], "triggered_at": timestamp}
                    updated = self._set_evidence(session_id, instance_id, "sell_repeat_exit", evidence)
                    self._event(updated, instance_id, "SELL_REPEAT_EXIT_TRIGGERED", result["snapshot_hash"], timestamp, evidence)
                    recorded = self._record(session_id, instance_id, evaluation_cycle_id, action=ACTION_EXIT,
                                            timestamp=timestamp, evidence=evidence)
                    return {"status": "PROGRESSED", "reason": "SELL_REPEAT_EXIT_TRIGGERED", "action": ACTION_EXIT,
                            "orders": [], "fills": [], "document": recorded}

        state = self.state(self.repository.read_session(session_id), instance_id)
        existing_sell_exit = state.get("sell_repeat_exit") if isinstance(state.get("sell_repeat_exit"), dict) else None
        if existing_sell_exit is not None and clean_text(existing_sell_exit.get("cycle_identity")) == cycle_identity:
            if qty <= 0:
                return None
            snapshot = payload_hash([latest["plan_id"], qty, existing_sell_exit.get("snapshot_hash")])
            if snapshot in state["final_residual_snapshots"]:
                return {"status": "NOOP", "reason": "FINAL_RESIDUAL_ALREADY_STARTED", "action": ACTION_NONE}
            final_intent = {
                **intent, "side": "SELL", "execution_mode": "SINGLE_ORDER",
                "hoga": "MARKET", "price_basis": "MARKET", "price": None,
                "quantity": qty, "planned_total_quantity": qty,
                "plan_generation": int(latest.get("generation", 0)) + 1,
                "child_sequence_index": 1, "child_sequence_total": 1,
                "child_kind": "SINGLE_ORDER", "final_residual_exit": True,
                "final_residual_exit_action_hash": snapshot,
                "child_plan": {"planned_quantity": qty, "planned_price": None,
                               "plan_generation": int(latest.get("generation", 0)) + 1,
                               "final_residual_exit_action_hash": snapshot},
            }
            for key in ("sell_repeat_policy", "sell_price_reset_policy", "unfilled_timeout_policy"):
                final_intent.pop(key, None)
            return {"status": "READY", "reason": "", "action": ACTION_FINAL_RESIDUAL,
                    "intents": [final_intent], "signal": {"signal": "SELL", "source_signal_id": latest["source_signal_id"]},
                    "continuation_kind": "FINAL_RESIDUAL", "source_hash": snapshot,
                    "source_plan_id": latest["plan_id"]}

        if not repeat_policy or qty <= 0:
            return None
        current = _current_price(market, trade_fresh)
        generation = int(latest.get("generation", 0)) + 1
        snapshot = payload_hash([latest["plan_id"], generation, qty, position.get("updated_at"), repeat_policy.get("plan_snapshot_hash")])
        if snapshot in state["repeat_snapshots"]:
            return {"status": "NOOP", "reason": "SELL_REPEAT_SNAPSHOT_ALREADY_USED", "action": ACTION_NONE}
        template = self._sell_repeat_template(repeat_policy, intent, qty, current or _order_price(latest), evaluated_at)
        intents = build_sell_generation_intents(
            template=template, source_signal_id=latest["source_signal_id"],
            process_id=latest["execution_process_id"],
            option_snapshot_hash=clean_text(intent.get("option_snapshot_hash")) or payload_hash(intent),
            generation=generation, quantity=qty, current_price=current or _order_price(latest),
            source_snapshot_hash=snapshot, generated_at=evaluated_at,
            source_snapshot_field="repeat_source_snapshot_hash",
        )
        return {"status": "READY", "reason": "", "action": ACTION_REPEAT,
                "intents": intents, "signal": {"signal": "SELL", "source_signal_id": latest["source_signal_id"]},
                "continuation_kind": "SELL_REPEAT", "source_hash": snapshot,
                "source_plan_id": latest["plan_id"]}

    @staticmethod
    def _sell_repeat_template(
        policy: dict[str, Any], base: dict[str, Any], quantity: int,
        price: float | None, generated_at: datetime,
    ) -> dict[str, Any]:
        configured = deepcopy(policy.get("execution_template")) if isinstance(policy.get("execution_template"), dict) else {}
        mode = clean_text(configured.get("execution_mode")).upper()
        template = {key: deepcopy(value) for key, value in base.items() if key not in {
            "execution_id", "quantity", "price", "child_plan", "multi_hoga_plan",
            "multi_time_plan", "multi_ratio_plan", "child_sequence_index", "child_sequence_total",
        }}
        template.update(configured)
        template["sell_repeat_policy"] = deepcopy(policy)
        if isinstance(policy.get("unfilled_timeout_policy"), dict):
            template["unfilled_timeout_policy"] = deepcopy(policy["unfilled_timeout_policy"])
        if isinstance(policy.get("sell_price_reset_policy"), dict):
            template["sell_price_reset_policy"] = deepcopy(policy["sell_price_reset_policy"])
        if mode == "MULTI_HOGA":
            offsets = configured.get("hoga_offsets")
            if not isinstance(offsets, list) or not offsets or quantity < len(offsets):
                raise MockValidationError("SELL_MULTI_HOGA_QUANTITY_BELOW_CHILD_COUNT")
            template["multi_hoga_plan"] = {"base_price": price, "hoga_offsets": offsets,
                "planned_child_count": len(offsets), "planned_total_quantity": quantity,
                "instrument_type": configured.get("instrument_type") or "STOCK"}
        elif mode == "MULTI_TIME":
            count = int(configured.get("configured_child_count", 0) or 0)
            if count <= 0 or quantity < count:
                raise MockValidationError("MOCK_SELL_REPEAT_TIME_PLAN_INVALID")
            value = int(configured.get("time_value", 0) or 0)
            unit = int(configured.get("time_unit_milliseconds", 0) or 0)
            range_mode = clean_text(configured.get("time_range")).upper()
            duration = value * unit
            offsets = ([0] if count == 1 else [round(i * duration / (count - 1)) for i in range(count)]) if range_mode in {"WITHIN", "이내"} else [i * duration for i in range(count)]
            template["multi_time_plan"] = {"configured_child_count": count, "planned_child_count": count,
                "planned_total_quantity": quantity, "scheduled_offsets_ms": offsets,
                "price_basis": configured.get("price_basis") or "ORDER_PRICE"}
        elif mode == "MULTI_RATIO":
            count = int(configured.get("configured_child_count", 0) or 0)
            if count <= 0 or quantity < count:
                raise MockValidationError("MOCK_SELL_REPEAT_RATIO_PLAN_INVALID")
            template["multi_ratio_plan"] = {"configured_child_count": count, "planned_child_count": count,
                "planned_total_quantity": quantity, "ratio_left": configured.get("ratio_left"),
                "ratio_right": configured.get("ratio_right"), "ratio_direction": configured.get("ratio_direction"),
                "ratio_value": configured.get("ratio_value"), "ratio_compare": configured.get("ratio_compare"),
                "ratio_unit": "PERCENT", "order_price": price}
        elif mode not in {"SINGLE", "SINGLE_ORDER"}:
            raise MockValidationError("MOCK_SELL_REPEAT_MODE_UNSUPPORTED")
        return template

    def confirm_plan_created(
        self, session_id: str, *, routine_instance_id: str,
        evaluation_cycle_id: str, action: str, source_hash: str,
        plan_id: str, evaluated_at: datetime,
    ) -> dict[str, Any]:
        instance_id = clean_text(routine_instance_id)
        timestamp = evaluated_at.isoformat(timespec="microseconds")
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            state = self.state(document, instance_id)
            collection = "reset_snapshots" if action == ACTION_REPLAN else "repeat_snapshots" if action == ACTION_REPEAT else "final_residual_snapshots"
            if source_hash not in state[collection]:
                state[collection].append(source_hash)
            if action == ACTION_REPLAN:
                state["pending_reset"] = None
            state["cycles"][evaluation_cycle_id] = {"action": action, "recorded_at": timestamp,
                "evidence": {"source_hash": source_hash, "plan_id": plan_id}}
            return document

        document = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])["document"]
        evidence = {"source_hash": source_hash, "plan_id": plan_id}
        if action == ACTION_REPEAT:
            self._event(document, instance_id, "SELL_REPEAT_TRIGGERED", source_hash, timestamp, evidence)
            event_type = "SELL_REPEAT_GENERATION_STARTED"
        else:
            event_type = "PRICE_RESET_REPLANNED" if action == ACTION_REPLAN else "FINAL_RESIDUAL_MARKET_STARTED"
        self._event(document, instance_id, event_type, source_hash, timestamp, evidence)
        return document


__all__ = [
    "ACTION_CANCEL_EFFECT", "ACTION_CANCEL_REQUEST", "ACTION_EXIT",
    "ACTION_FINAL_RESIDUAL", "ACTION_NONE", "ACTION_REPLAN", "ACTION_REPEAT",
    "MockIndicatorFollowContinuationCoordinator", "evaluate_price_reset_policy",
    "timeout_due",
]
