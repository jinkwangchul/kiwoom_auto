# -*- coding: utf-8 -*-
"""Isolated Indicator Follow adapter for Mock Validation.

The adapter reuses only the frozen routine's pure evaluator and intent
builders.  It never enters the Production signal, candidate, approval, queue,
runtime, broker, Chejan, position, PnL, event, or review mutation paths.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
import importlib.util
from pathlib import Path
import sys
from typing import Any, Callable

from candle_timeframe_aggregation import candle_market_datetime
from engines.signal_result import signal_to_dict
from execution_price_comparison import evaluate_percent_comparison, resolve_price_source
from mock_validation_contract import (
    MOCK_BUDGET_STATE_WAIT_SELL,
    ORDER_CANCELED,
    ORDER_FILLED,
    SESSION_RUNNING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    instance_initial_buy_adjustment,
    payload_hash,
)
from mock_validation_market_data import MockMarketSnapshot
from mock_validation_indicator_follow_continuation import (
    ACTION_EXIT,
    ACTION_RECOVERY,
    ACTION_REPLAN,
    MockIndicatorFollowContinuationCoordinator,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import (
    LIVE_STATES,
    MockExecutionPolicy,
    MockVirtualExecutionEngine,
    RESULT_BLOCKED,
)
from routine_main_facts import build_routine_main_facts_from_projection


RESULT_NO_SIGNAL = "NO_SIGNAL"
RESULT_WAIT = "WAIT"
RESULT_NOOP = "NOOP"
RESULT_PROGRESSED = "PROGRESSED"

MODE_SINGLE = "SINGLE"
MODE_MULTI_HOGA = "MULTI_HOGA"
MODE_MULTI_TIME = "MULTI_TIME"
MODE_MULTI_RATIO = "MULTI_RATIO"

_TERMINAL_ORDER_STATES = {ORDER_FILLED, ORDER_CANCELED, "REJECTED"}
_ROUTINE_DIR = Path(__file__).resolve().parent / "routines" / "지표추종매매"


@dataclass(frozen=True)
class MockRoutineEvaluationInput:
    evaluation_cycle_id: str
    candles: tuple[dict[str, Any], ...]
    evaluated_at: datetime
    market: MockMarketSnapshot | None
    policy: MockExecutionPolicy


def _load_file_module(filename: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, _ROUTINE_DIR / filename)
    if spec is None or spec.loader is None:
        raise MockValidationError(f"MOCK_ROUTINE_PURE_MODULE_UNAVAILABLE:{filename}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(_ROUTINE_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(_ROUTINE_DIR))
    return module


@lru_cache(maxsize=1)
def _pure_routine_functions() -> tuple[Callable[..., Any], Callable[..., dict[str, Any]], Callable[..., dict[str, Any]]]:
    evaluator_module = _load_file_module(
        "routine_macd_engine.py", "mock_validation_indicator_follow_pure_evaluator"
    )
    buy_module = _load_file_module(
        "routine_buy_execution.py", "mock_validation_indicator_follow_pure_buy"
    )
    sell_module = _load_file_module(
        "routine_sell_execution.py", "mock_validation_indicator_follow_pure_sell"
    )
    return (
        evaluator_module.evaluate_indicator_follow_routine,
        buy_module.build_indicator_follow_buy_intent,
        sell_module.build_indicator_follow_sell_intent,
    )


@lru_cache(maxsize=1)
def _pure_lifecycle_evaluator() -> Callable[..., dict[str, Any]]:
    lifecycle_module = _load_file_module(
        "routine_lifecycle.py", "mock_validation_indicator_follow_pure_lifecycle"
    )
    return lifecycle_module.evaluate_lifecycle


@lru_cache(maxsize=1)
def _market_projection_requester() -> Callable[[dict[str, Any] | None], dict[str, Any]]:
    routine_module = _load_file_module("routine.py", "mock_validation_indicator_follow_routine_contract")
    requester = getattr(routine_module, "market_bar_projection_request", None)
    if not callable(requester):
        raise MockValidationError("MOCK_ROUTINE_MARKET_PROJECTION_REQUEST_UNAVAILABLE")
    return requester


def _positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _aware(value: datetime | str) -> datetime | None:
    observed = value
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed)
        except ValueError:
            return None
    return observed if isinstance(observed, datetime) and observed.tzinfo is not None else None


def _signal_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    try:
        return signal_to_dict(value)
    except Exception as exc:
        raise MockValidationError("MOCK_ROUTINE_SIGNAL_RESULT_INVALID") from exc


def _signal_marker_payload(
    signal: dict[str, Any],
    candles: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    side = clean_text(signal.get("signal")).upper()
    index = signal.get("signal_activation_index", signal.get("signal_index"))
    if side not in {"BUY", "SELL"} or not isinstance(index, int) or isinstance(index, bool):
        return {}
    if not candles or not -len(candles) <= index < len(candles):
        return {}
    candle = candles[index]
    if not isinstance(candle, dict):
        return {}
    bar_time = candle_market_datetime(candle)
    close = candle.get("close")
    if bar_time is None or close in (None, ""):
        return {}
    return {
        "signal_bar_time": bar_time.isoformat(timespec="seconds"),
        "signal_bar_close": close,
        "signal_timeframe_minutes": candle.get("timeframe_minutes"),
        "signal_trade_date": clean_text(candle.get("trade_date")) or bar_time.date().isoformat(),
        "signal_input_hash": payload_hash(list(candles)),
    }


def _reference_price(candles: tuple[dict[str, Any], ...]) -> int | float | None:
    for candle in reversed(candles):
        if not isinstance(candle, dict):
            continue
        for key in ("close", "close_price", "price"):
            price = _positive_number(candle.get(key))
            if price is not None:
                return price
    return None


def _position(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
    matches = [item for item in document["positions"] if item.get("routine_instance_id") == instance_id]
    if len(matches) != 1:
        raise MockValidationError("MOCK_ROUTINE_POSITION_IDENTITY_INVALID")
    return matches[0]


def _instance_reference(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in document["reference_snapshot"]["routine_instances"]
        if item.get("routine_instance_id") == instance_id
    ]
    if len(matches) != 1:
        raise MockValidationError("MOCK_ROUTINE_REFERENCE_IDENTITY_INVALID")
    item = matches[0]
    rules = item.get("rules_snapshot")
    if not isinstance(rules, dict) or payload_hash(rules) != item.get("rules_hash"):
        raise MockValidationError("MOCK_ROUTINE_RULES_HASH_MISMATCH")
    return item


def _mock_settings(rules: dict[str, Any]) -> tuple[dict[str, Any], int | float | None]:
    settings = rules.get("mock_validation")
    settings = settings if isinstance(settings, dict) else {}
    stock_config = settings.get("stock_config")
    stock_config = deepcopy(stock_config) if isinstance(stock_config, dict) else {}
    if not stock_config:
        legacy = rules.get("stock_config")
        stock_config = deepcopy(legacy) if isinstance(legacy, dict) else {}
    budget = _positive_number(settings.get("execution_budget"))
    if budget is None:
        budget = _positive_number(rules.get("starting_budget"))
    return stock_config, budget


def _operation_rules(document: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
    lifecycle = document.get("mock_operation_lifecycle")
    operations = lifecycle.get("instance_operations") if isinstance(lifecycle, dict) else None
    operation = operations.get(instance_id) if isinstance(operations, dict) else None
    if not isinstance(operation, dict):
        operation = lifecycle.get("current") if isinstance(lifecycle, dict) else None
    snapshot = operation.get("operation_policy_snapshot") if isinstance(operation, dict) else None
    if not isinstance(snapshot, dict):
        return None
    direct = snapshot.get("mock_instance_rules_snapshot")
    if isinstance(direct, dict):
        return deepcopy(direct)
    by_instance = snapshot.get("mock_rules_snapshot_by_instance")
    if isinstance(by_instance, dict) and isinstance(by_instance.get(instance_id), dict):
        return deepcopy(by_instance[instance_id])
    return None


def _operation_effective_settings(
    document: dict[str, Any], instance_id: str
) -> dict[str, Any] | None:
    lifecycle = document.get("mock_operation_lifecycle")
    if not isinstance(lifecycle, dict):
        return None
    operations = lifecycle.get("instance_operations")
    operation = operations.get(instance_id) if isinstance(operations, dict) else None
    if not isinstance(operation, dict):
        operation = lifecycle.get("current")
    snapshot = (
        operation.get("operation_policy_snapshot")
        if isinstance(operation, dict)
        else None
    )
    if not isinstance(snapshot, dict):
        return None
    direct = snapshot.get("mock_instance_effective_settings")
    if isinstance(direct, dict):
        settings = deepcopy(direct)
    else:
        by_instance = snapshot.get("mock_effective_settings_by_instance")
        settings = (
            deepcopy(by_instance[instance_id])
            if isinstance(by_instance, dict)
            and isinstance(by_instance.get(instance_id), dict)
            else None
        )
    if not isinstance(settings, dict):
        return None
    adjustment = instance_initial_buy_adjustment(document, instance_id)
    if adjustment is None:
        return settings
    if clean_text(operation.get("operation_session_id")) != adjustment[
        "operation_session_id"
    ]:
        raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
    value_key = (
        "previous_value"
        if adjustment["state"] == MOCK_BUDGET_STATE_WAIT_SELL
        else "requested_value"
    )
    settings["initial_buy"] = {
        "mode": adjustment["mode"],
        "value": int(adjustment[value_key]),
    }
    return settings


def _apply_effective_initial_buy(
    stock_config: dict[str, Any],
    execution_budget: int | float | None,
    settings: dict[str, Any] | None,
) -> tuple[dict[str, Any], int | float | None]:
    if not isinstance(settings, dict):
        return stock_config, execution_budget
    initial = settings.get("initial_buy")
    if not isinstance(initial, dict):
        return stock_config, execution_budget
    mode = clean_text(initial.get("mode")).upper()
    try:
        value = int(initial.get("value", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    if mode not in {"QUANTITY", "AMOUNT"} or value <= 0:
        raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_INVALID")
    effective_config = deepcopy(stock_config)
    effective_config["trade_amount_type"] = mode
    if mode == "QUANTITY":
        effective_config["buy_qty"] = value
        effective_config.pop("buy_amount", None)
    else:
        effective_config["buy_amount"] = value
        effective_config.pop("buy_qty", None)
        execution_budget = value
    return effective_config, execution_budget


def _market_freshness(
    market: MockMarketSnapshot | None,
    *,
    evaluated_at: datetime,
    policy: MockExecutionPolicy,
) -> tuple[bool, bool, str]:
    if market is None:
        return False, False, "MOCK_MARKET_UNAVAILABLE"
    book = market.orderbook
    received = _aware(book.received_at)
    if received is None or evaluated_at.tzinfo is None:
        return False, False, "MOCK_MARKET_TIME_INVALID"
    if (
        book.connection_epoch != int(policy.connection_epoch)
        or book.login_session_id != clean_text(policy.login_session_id)
    ):
        return False, False, "MOCK_MARKET_SESSION_INVALID"
    age = (evaluated_at - received).total_seconds()
    if age < 0 or age > float(policy.max_orderbook_age_seconds):
        return False, False, "MOCK_ORDERBOOK_STALE"
    trade = market.trade
    if trade is None:
        return True, False, "MOCK_TRADE_UNAVAILABLE"
    trade_received = _aware(trade.received_at)
    trade_fresh = bool(
        trade_received is not None
        and trade.connection_epoch == int(policy.connection_epoch)
        and trade.login_session_id == clean_text(policy.login_session_id)
        and 0 <= (evaluated_at - trade_received).total_seconds() <= float(policy.max_trade_age_seconds)
    )
    return True, trade_fresh, "MOCK_MARKET_FRESH" if trade_fresh else "MOCK_TRADE_STALE"


def _plan_mode(intents: list[dict[str, Any]]) -> str:
    values = {clean_text(item.get("execution_mode")).upper() for item in intents}
    if values == {MODE_MULTI_HOGA}:
        return MODE_MULTI_HOGA
    if values == {MODE_MULTI_TIME}:
        return MODE_MULTI_TIME
    if values == {MODE_MULTI_RATIO}:
        return MODE_MULTI_RATIO
    if len(intents) == 1 and values <= {"", MODE_SINGLE, "SINGLE_ORDER"}:
        return MODE_SINGLE
    raise MockValidationError("MOCK_EXECUTION_PLAN_MODE_INVALID")


def _child_order_type(intent: dict[str, Any]) -> tuple[str, int | float | None]:
    hoga = clean_text(intent.get("hoga")).upper()
    if hoga == "MARKET" or clean_text(intent.get("price_basis")).upper() == "MARKET":
        return "MARKET", None
    price = _positive_number(intent.get("price"))
    if hoga not in {"", "LIMIT"} or price is None:
        raise MockValidationError("MOCK_EXECUTION_CHILD_PRICE_INVALID")
    return "LIMIT", price


class MockIndicatorFollowRoutineAdapter:
    """Drive one frozen Indicator Follow instance inside the Mock domain."""

    def __init__(
        self,
        repository: MockValidationRepository,
        execution_engine: MockVirtualExecutionEngine,
        *,
        now_factory: Callable[[], datetime] | None = None,
        evaluator: Callable[..., Any] | None = None,
        buy_intent_builder: Callable[..., dict[str, Any]] | None = None,
        sell_intent_builder: Callable[..., dict[str, Any]] | None = None,
        lifecycle_evaluator: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        defaults: tuple[Callable[..., Any], Callable[..., dict[str, Any]], Callable[..., dict[str, Any]]] | None = None
        if evaluator is None or buy_intent_builder is None or sell_intent_builder is None:
            defaults = _pure_routine_functions()
        self.repository = repository
        self.engine = execution_engine
        self.session_service = MockValidationSessionService(repository)
        self._now = now_factory or (lambda: datetime.now().astimezone())
        self._evaluator = evaluator or defaults[0]  # type: ignore[index]
        self._buy_builder = buy_intent_builder or defaults[1]  # type: ignore[index]
        self._sell_builder = sell_intent_builder or defaults[2]  # type: ignore[index]
        self._lifecycle_evaluator = lifecycle_evaluator or _pure_lifecycle_evaluator()
        self.continuation = MockIndicatorFollowContinuationCoordinator(
            repository, execution_engine, now_factory=self._now,
        )

    @staticmethod
    def _progression(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
        root = document["progression_by_instance"][instance_id]
        adapter = root.setdefault(
            "indicator_follow_mock_adapter",
            {"plans": [], "evaluation_cycles": {}, "version": 1},
        )
        if not isinstance(adapter.get("plans"), list) or not isinstance(adapter.get("evaluation_cycles"), dict):
            raise MockValidationError("MOCK_ROUTINE_PROGRESSION_CORRUPTED")
        return adapter

    def _event(
        self,
        document: dict[str, Any],
        *,
        instance_id: str,
        event_type: str,
        identity: str,
        timestamp: str,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "event_id": deterministic_mock_identity(
                "ME", document["session"]["validation_session_id"], instance_id, identity, event_type
            ),
            "validation_session_id": document["session"]["validation_session_id"],
            "stock_code": document["session"]["stock_code"],
            "routine_instance_id": instance_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "reason_code": reason,
            "payload": deepcopy(payload) if isinstance(payload, dict) else {},
        }
        self.repository.append_event(event)

    @staticmethod
    def _operation_identity(document: dict[str, Any], instance_id: str) -> str:
        lifecycle = document.get("mock_operation_lifecycle")
        operation = None
        if isinstance(lifecycle, dict):
            operations = lifecycle.get("instance_operations")
            if isinstance(operations, dict):
                operation = operations.get(instance_id)
            if not isinstance(operation, dict):
                operation = lifecycle.get("current")
        identity = (
            clean_text(operation.get("operation_session_id"))
            if isinstance(operation, dict)
            else ""
        )
        return identity or clean_text(document["session"].get("start_identity")) or clean_text(
            document["session"].get("validation_session_id")
        )

    def _clear_normal_block_episode(
        self,
        session_id: str,
        instance_id: str,
        *,
        cleared_at: str,
    ) -> None:
        before = self.repository.read_session(session_id)
        progression = self._progression(before, instance_id)
        episode = progression.get("normal_block_episode")
        if not isinstance(episode, dict) or episode.get("active") is not True:
            return

        def clear(document: dict[str, Any]) -> dict[str, Any]:
            current = self._progression(document, instance_id).get("normal_block_episode")
            if isinstance(current, dict) and current.get("active") is True:
                current.update({"active": False, "cleared_at": cleared_at})
            return document

        self.repository.mutate_session(
            session_id,
            clear,
            expected_revision=before["revision"],
        )

    def _normal_block_episode(
        self,
        session_id: str,
        instance_id: str,
        *,
        reason: str,
        timestamp: str,
    ) -> tuple[dict[str, Any], bool]:
        before = self.repository.read_session(session_id)
        progression = self._progression(before, instance_id)
        operation_identity = self._operation_identity(before, instance_id)
        current = progression.get("normal_block_episode")
        if (
            isinstance(current, dict)
            and current.get("active") is True
            and clean_text(current.get("operation_identity")) == operation_identity
            and clean_text(current.get("reason_code")) == reason
        ):
            return deepcopy(current), current.get("event_recorded") is not True

        sequence = int(progression.get("normal_block_episode_sequence", 0) or 0) + 1
        episode_id = deterministic_mock_identity(
            "MC",
            before["session"]["validation_session_id"],
            instance_id,
            operation_identity,
            sequence,
            reason,
            "NORMAL_BLOCK_EPISODE",
        )
        episode = {
            "active": True,
            "episode_id": episode_id,
            "operation_identity": operation_identity,
            "reason_code": reason,
            "started_at": timestamp,
            "cleared_at": "",
            "sequence": sequence,
            "event_recorded": False,
        }

        def begin(document: dict[str, Any]) -> dict[str, Any]:
            adapter = self._progression(document, instance_id)
            adapter["normal_block_episode_sequence"] = sequence
            adapter["normal_block_episode"] = deepcopy(episode)
            return document

        self.repository.mutate_session(
            session_id,
            begin,
            expected_revision=before["revision"],
        )
        return episode, True

    def _mark_normal_block_event_recorded(
        self,
        session_id: str,
        instance_id: str,
        *,
        episode_id: str,
    ) -> None:
        before = self.repository.read_session(session_id)
        current = self._progression(before, instance_id).get("normal_block_episode")
        if (
            not isinstance(current, dict)
            or clean_text(current.get("episode_id")) != episode_id
            or current.get("event_recorded") is True
        ):
            return

        def mark(document: dict[str, Any]) -> dict[str, Any]:
            episode = self._progression(document, instance_id).get("normal_block_episode")
            if isinstance(episode, dict) and clean_text(episode.get("episode_id")) == episode_id:
                episode["event_recorded"] = True
            return document

        self.repository.mutate_session(
            session_id,
            mark,
            expected_revision=before["revision"],
        )

    def _normal_block(
        self,
        document: dict[str, Any],
        *,
        instance_id: str,
        cycle_id: str,
        timestamp: str,
        reason: str,
        status: str = RESULT_BLOCKED,
        evaluation_completed: bool = False,
    ) -> dict[str, Any]:
        session_id = document["session"]["validation_session_id"]
        episode, should_record = self._normal_block_episode(
            session_id,
            instance_id,
            reason=reason,
            timestamp=timestamp,
        )
        if should_record:
            current = self.repository.read_session(session_id)
            self._event(
                current,
                instance_id=instance_id,
                event_type="EXECUTION_PLAN_BLOCKED",
                identity=episode["episode_id"],
                timestamp=episode["started_at"],
                reason=reason,
                payload={
                    "normal_block_episode_id": episode["episode_id"],
                    "operation_identity": episode["operation_identity"],
                    "episode_sequence": episode["sequence"],
                },
            )
            self._mark_normal_block_event_recorded(
                session_id,
                instance_id,
                episode_id=episode["episode_id"],
            )
        if evaluation_completed:
            before_mark = self.repository.read_session(session_id)

            def mark(document_value: dict[str, Any]) -> dict[str, Any]:
                self._progression(document_value, instance_id)["evaluation_cycles"][cycle_id] = {
                    "result": status,
                    "reason": reason,
                    "recorded_at": timestamp,
                }
                return document_value

            self.repository.mutate_session(
                session_id,
                mark,
                expected_revision=before_mark["revision"],
            )
        return {
            "status": status,
            "reason": reason,
            "orders": [],
            "fills": [],
            "_normal_block_episode": True,
        }

    def _integrity_stop(
        self,
        session_id: str,
        instance_id: str,
        cycle_id: str,
        error: Exception,
    ) -> dict[str, Any]:
        reason = str(error) or type(error).__name__
        result = self.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id=instance_id,
            reason_code="MOCK_ROUTINE_ADAPTER_INTEGRITY_FAILURE",
            reason=reason,
            command_id=deterministic_mock_identity("MC", session_id, instance_id, cycle_id, "INTEGRITY"),
            source_category="INDICATOR_FOLLOW_ADAPTER",
        )
        return {
            "status": "INSTANCE_ERROR",
            "reason": reason,
            "orders": [],
            "fills": [],
            "document": result["document"],
        }

    @staticmethod
    def _active_orders(document: dict[str, Any], instance_id: str) -> list[dict[str, Any]]:
        return [
            item for item in document["orders"]
            if item.get("routine_instance_id") == instance_id and item.get("state") in LIVE_STATES
        ]

    @staticmethod
    def _explicit_signal_id(signal: dict[str, Any]) -> str:
        return clean_text(signal.get("source_signal_id") or signal.get("signal_id") or signal.get("id"))

    def _set_pending_successor(
        self, session_id: str, instance_id: str, *, source_plan_id: str,
        signal: dict[str, Any], timestamp: str,
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            progression = self._progression(document, instance_id)
            value = {
                "source_plan_id": source_plan_id,
                "signal": deepcopy(signal),
                "queued_at": timestamp,
            }
            existing = progression.get("pending_successor")
            if existing not in (None, value):
                raise MockValidationError("MOCK_PENDING_SUCCESSOR_CONFLICT")
            progression["pending_successor"] = value
            return document

        return self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]

    def _clear_pending_successor(self, session_id: str, instance_id: str) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            self._progression(document, instance_id).pop("pending_successor", None)
            return document

        return self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]

    def _advance_pending_successor(
        self, session_id: str, instance_id: str, *, cycle_id: str,
        evaluated_at: datetime, rules: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        document = self.repository.read_session(session_id)
        pending = self._progression(document, instance_id).get("pending_successor")
        if not isinstance(pending, dict):
            return None, None
        source_plan_id = clean_text(pending.get("source_plan_id"))
        plans = self._progression(document, instance_id)["plans"]
        source = next((item for item in plans if item.get("plan_id") == source_plan_id), None)
        if source is None:
            raise MockValidationError("MOCK_PENDING_SUCCESSOR_SOURCE_PLAN_MISSING")
        signal = deepcopy(pending.get("signal")) if isinstance(pending.get("signal"), dict) else {}
        return self._apply_signal_arbitration(
            session_id, instance_id, cycle_id=cycle_id, evaluated_at=evaluated_at,
            rules=rules, signal=signal, active_plan=source, pending=True,
        )

    def _signal_arbitration_decision(
        self, document: dict[str, Any], instance_id: str, *, rules: dict[str, Any],
        signal: dict[str, Any], active_plan: dict[str, Any], evaluated_at: datetime,
    ) -> tuple[dict[str, Any], int]:
        """Ask the Production routine coordinator to arbitrate Mock facts.

        Mock contributes only an isolated fact projection and later executes the
        returned generic command against Mock writers.  Duplicate-signal policy
        meaning therefore has one Production authority.
        """
        code = clean_text(document["session"].get("stock_code")).lstrip("A")
        predecessor_id = clean_text(active_plan.get("source_signal_id"))
        successor_id = self._explicit_signal_id(signal)
        if not code or not predecessor_id or not successor_id:
            raise MockValidationError("MOCK_SIGNAL_ARBITRATION_IDENTITY_INVALID")
        position = _position(document, instance_id)
        signal_policy = rules.get("signal_runtime_policy")
        signal_policy = deepcopy(signal_policy) if isinstance(signal_policy, dict) else {}
        signals = [
            {
                "id": predecessor_id,
                "code": code,
                "routine_instance_id": instance_id,
                "signal": active_plan.get("side"),
                "status": "PENDING",
                "created_at": clean_text(active_plan.get("plan_started_at")) or "0000",
            },
            {
                "id": successor_id,
                "code": code,
                "routine_instance_id": instance_id,
                "signal": clean_text(signal.get("signal")).upper(),
                "status": "PENDING",
                "created_at": evaluated_at.isoformat(timespec="microseconds"),
                "signal_runtime_policy": signal_policy,
            },
        ]
        source_order_ids = {
            clean_text(child.get("mock_order_id"))
            for child in active_plan.get("children", [])
            if clean_text(child.get("mock_order_id"))
        }
        orders = [
            {
                "id": item.get("mock_order_id"),
                "code": code,
                "source_signal_id": predecessor_id,
                "routine_instance_id": instance_id,
                "status": item.get("state"),
                "broker_order_no": item.get("mock_order_id"),
                "order_action": "NEW",
                "manual_reconciliation_required": False,
            }
            for item in document.get("orders", [])
            if clean_text(item.get("mock_order_id")) in source_order_ids
        ]
        quantity = int(position.get("holding_qty", 0) or 0)
        projection = {
            "signals": signals,
            "orders": orders,
            "executions": [],
            "processes": [],
            "fills": [],
            "positions": [{"code": code, "quantity": quantity}],
            "holdings": [{
                "code": code,
                "holding_quantity": quantity,
                "reconciliation_status": "CONSISTENT",
                "manual_reconciliation_required": False,
            }],
            "stock_configs": {code: {"assigned_routine_instance_id": instance_id}},
            "stock_states": {code: {"status": "RUNNING", "review_required": False}},
            "selected_account_no": "MOCK_ONLY",
            "allowed_stock_codes": [code],
            "actionable_prices_by_code": {},
            "current_orderable_cash": document.get("session", {}).get("cash_balance"),
            "budget": {},
            "limits": {},
            "market": {},
            "review": {"by_stock_code": {code: {"review_required": False}}},
        }
        facts = build_routine_main_facts_from_projection(projection, now=evaluated_at).to_payload()
        result = self._lifecycle_evaluator(
            main_facts=facts,
            rules=deepcopy(rules),
            routine_identity={"routine_instance_id": instance_id},
            rules_identity=payload_hash(rules),
        )
        decisions = [
            item for item in result.get("decisions", [])
            if isinstance(item, dict)
            and clean_text(item.get("stock_code")).lstrip("A") == code
            and clean_text(item.get("payload", {}).get("phase")).upper() == "SIGNAL_PRIORITY"
        ]
        if not decisions:
            raise MockValidationError("MOCK_SIGNAL_ARBITRATION_DECISION_INVALID")
        selected = next(
            (item for item in decisions if clean_text(item.get("command")).upper() == "STOP_AND_REVIEW"),
            None,
        ) or next(
            (item for item in decisions if clean_text(item.get("command")).upper() != "WAIT"),
            decisions[0],
        )
        return selected, int(document.get("revision", 0) or 0)

    def _apply_signal_arbitration(
        self, session_id: str, instance_id: str, *, cycle_id: str,
        evaluated_at: datetime, rules: dict[str, Any], signal: dict[str, Any],
        active_plan: dict[str, Any], pending: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        document = self.repository.read_session(session_id)
        decision, facts_revision = self._signal_arbitration_decision(
            document, instance_id, rules=rules, signal=signal,
            active_plan=active_plan, evaluated_at=evaluated_at,
        )
        # No Mock mutation may consume a decision from a stale session snapshot.
        if int(self.repository.read_session(session_id).get("revision", 0) or 0) != facts_revision:
            return {"status": RESULT_WAIT, "reason": "MOCK_SIGNAL_FACTS_STALE", "orders": [], "fills": []}, None
        command = clean_text(decision.get("command")).upper()
        reason = clean_text(decision.get("reason")) or "MOCK_SIGNAL_ARBITRATION"
        timestamp = evaluated_at.isoformat(timespec="microseconds")
        if command == "IGNORE_SIGNAL":
            self._event(
                document, instance_id=instance_id, event_type="ROUTINE_SIGNAL_IGNORED",
                identity=f"{active_plan.get('source_signal_id')}:{self._explicit_signal_id(signal)}",
                timestamp=timestamp, reason=reason,
                payload={"successor_signal_id": self._explicit_signal_id(signal),
                         "routine_decision_identity": decision.get("decision_identity")},
            )
            if pending:
                self._clear_pending_successor(session_id, instance_id)
            return {"status": RESULT_NOOP, "reason": reason, "orders": [], "fills": [], "signal": deepcopy(signal)}, None
        if command == "REQUEST_CANCEL":
            orders = self.continuation._plan_orders(document, active_plan)
            targets = [item for item in orders if item.get("state") in LIVE_STATES]
            result = self.continuation._cancel_action(
                session_id, instance_id, cycle_id, orders=targets,
                as_of=evaluated_at, reason=reason,
            )
            return result, None
        if command in {"WAIT", "BLOCK_PROCESS_DISPATCH"}:
            return {"status": RESULT_WAIT, "reason": reason, "orders": [], "fills": []}, None
        if command == "STOP_AND_REVIEW":
            raise MockValidationError(reason)
        if command != "COMPLETE_PROCESS":
            raise MockValidationError(f"MOCK_SIGNAL_ARBITRATION_COMMAND_UNSUPPORTED:{command}")
        self._supersede_plan(
            session_id, instance_id, active_plan["plan_id"],
            reason=reason, timestamp=timestamp,
        )
        if pending:
            self._clear_pending_successor(session_id, instance_id)
        return None, deepcopy(signal)

    def _arbitrate_active_signal(
        self, session_id: str, instance_id: str, *, cycle_id: str,
        evaluated_at: datetime, rules: dict[str, Any], signal: dict[str, Any],
        active_plan: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        successor_id = self._explicit_signal_id(signal)
        if not successor_id or successor_id == clean_text(active_plan.get("source_signal_id")):
            return None, None
        timestamp = evaluated_at.isoformat(timespec="microseconds")
        self._set_pending_successor(
            session_id, instance_id, source_plan_id=active_plan["plan_id"],
            signal=signal, timestamp=timestamp,
        )
        return self._apply_signal_arbitration(
            session_id, instance_id, cycle_id=cycle_id, evaluated_at=evaluated_at,
            rules=rules, signal=signal, active_plan=active_plan, pending=True,
        )

    @staticmethod
    def _order_by_id(document: dict[str, Any], order_id: str) -> dict[str, Any] | None:
        return next((item for item in document["orders"] if item.get("mock_order_id") == order_id), None)

    def _refresh_plan(self, document: dict[str, Any], plan: dict[str, Any]) -> bool:
        changed = False
        all_emitted = True
        all_terminal = True
        filled_total = 0
        for child in plan["children"]:
            order_id = clean_text(child.get("mock_order_id"))
            if not order_id:
                if clean_text(child.get("status")).upper() == "SKIPPED":
                    continue
                all_emitted = False
                all_terminal = False
                continue
            order = self._order_by_id(document, order_id)
            if order is None:
                raise MockValidationError("MOCK_EXECUTION_CHILD_ORDER_MISSING")
            next_status = clean_text(order.get("state")).upper()
            filled_total += int(order.get("filled_qty", 0) or 0)
            if child.get("status") != next_status:
                child["status"] = next_status
                child["filled_qty"] = int(order.get("filled_qty", 0))
                child["remaining_qty"] = int(order.get("remaining_qty", 0))
                changed = True
            if next_status not in _TERMINAL_ORDER_STATES:
                all_terminal = False
        if plan.get("filled_qty") != filled_total:
            plan["filled_qty"] = filled_total
            plan["remaining_qty"] = max(0, int(plan.get("total_qty", 0) or 0) - filled_total)
            changed = True
        supplement_needed = bool(
            plan.get("side") == "SELL"
            and plan.get("mode") == MODE_MULTI_HOGA
            and all_emitted and all_terminal
            and filled_total < int(plan.get("total_qty", 0) or 0)
        )
        paused = plan.get("state") == "PAUSED_FOR_RECOVERY"
        next_state = (
            "COMPLETED" if all_emitted and all_terminal and not supplement_needed
            else "PAUSED_FOR_RECOVERY" if paused
            else "ACTIVE"
        )
        if plan.get("state") != next_state:
            plan["state"] = next_state
            changed = True
        return changed

    def _sync_cycle(self, document: dict[str, Any], instance_id: str, timestamp: str) -> None:
        position = _position(document, instance_id)
        fills = [item for item in document["fills"] if item.get("routine_instance_id") == instance_id]
        cycle = document["cycle_state_by_instance"][instance_id]
        progression = self._progression(document, instance_id)
        plans = progression["plans"]
        active_scope = clean_text(cycle.get("cycle_identity"))
        if not active_scope and int(position["holding_qty"]) > 0:
            filled_buy_order_ids = {item.get("mock_order_id") for item in fills if item.get("side") == "BUY"}
            active_scope = next((
                clean_text(plan.get("cycle_scope_identity"))
                for plan in reversed(plans)
                if plan.get("side") == "BUY"
                and any(child.get("mock_order_id") in filled_buy_order_ids for child in plan.get("children", []))
            ), "")
        scoped_plans = [item for item in plans if clean_text(item.get("cycle_scope_identity")) == active_scope] if active_scope else []
        buy_plans = [item for item in scoped_plans if item.get("side") == "BUY"]
        order_round: dict[str, int] = {}
        for plan in buy_plans:
            for child in plan.get("children", []):
                if child.get("mock_order_id"):
                    order_round[child["mock_order_id"]] = int(plan.get("round", 0) or 0)
        scoped_order_ids = {
            child.get("mock_order_id") for plan in scoped_plans for child in plan.get("children", [])
            if child.get("mock_order_id")
        }
        buy_fills = [item for item in fills if item.get("side") == "BUY" and item.get("mock_order_id") in scoped_order_ids]
        by_round: dict[int, float] = {}
        for fill in buy_fills:
            round_value = order_round.get(fill.get("mock_order_id"), 0)
            if round_value > 0:
                by_round[round_value] = by_round.get(round_value, 0.0) + float(fill["price"]) * int(fill["qty"])
        buy_cost = sum(by_round.values())
        completed_rounds = {
            int(plan.get("round", 0) or 0)
            for plan in buy_plans
            if plan.get("state") == "COMPLETED"
            and int(plan.get("round", 0) or 0) > 0
            and by_round.get(int(plan.get("round", 0) or 0), 0) > 0
        }
        confirmed_round = max(completed_rounds, default=int(cycle.get("confirmed_buy_round", 0) or 0))
        if buy_fills and not cycle.get("cycle_identity"):
            cycle["cycle_identity"] = active_scope
            cycle["started_at"] = buy_fills[0]["filled_at"]
        if buy_fills:
            cycle.update(
                {
                    "confirmed_buy_round": confirmed_round,
                    "cumulative_filled_buy_amount": buy_cost,
                    "base_filled_buy_amount": by_round.get(1, cycle.get("base_filled_buy_amount", 0)),
                    "last_filled_buy_amount": by_round.get(confirmed_round, cycle.get("last_filled_buy_amount", 0)),
                    "filled_buy_amount_by_round": {key: value for key, value in sorted(by_round.items())},
                }
            )
        cycle.update(
            {
                "status": "resolved",
                "active": bool(position["holding_qty"] > 0 and (buy_fills or cycle.get("cycle_identity"))),
                "holding_qty": int(position["holding_qty"]),
                "available_qty": int(position["available_qty"]),
                "average_price": position["average_price"],
                "confirmed_buy_round": int(cycle.get("confirmed_buy_round", 0) or 0),
                "cumulative_filled_buy_amount": cycle.get("cumulative_filled_buy_amount", 0),
                "base_filled_buy_amount": cycle.get("base_filled_buy_amount", 0),
                "last_filled_buy_amount": cycle.get("last_filled_buy_amount", 0),
                "filled_buy_amount_by_round": deepcopy(cycle.get("filled_buy_amount_by_round", {})),
                "pending_buy_rounds": sorted({
                    order_round.get(order["mock_order_id"], 0)
                    for order in self._active_orders(document, instance_id)
                    if order.get("side") == "BUY" and order_round.get(order["mock_order_id"], 0) > 0
                }),
                "pending_buy_order_identities": [
                    order["mock_order_id"]
                    for order in self._active_orders(document, instance_id)
                    if order.get("side") == "BUY"
                ],
                "updated_at": timestamp,
            }
        )
        if int(position["holding_qty"]) == 0 and cycle.get("cycle_identity") and not self._active_orders(document, instance_id):
            cycle["last_completed_cycle_identity"] = cycle["cycle_identity"]
            cycle["completed_at"] = timestamp
            cycle["cycle_identity"] = ""
            cycle["active"] = False
            cycle["confirmed_buy_round"] = 0
            cycle["cumulative_filled_buy_amount"] = 0
            cycle["base_filled_buy_amount"] = 0
            cycle["last_filled_buy_amount"] = 0
            cycle["filled_buy_amount_by_round"] = {}

    def _save_plan(
        self,
        session_id: str,
        instance_id: str,
        plan: dict[str, Any],
        cycle_id: str,
        timestamp: str,
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            progression = self._progression(document, instance_id)
            if any(item.get("plan_id") == plan["plan_id"] for item in progression["plans"]):
                return document
            progression["plans"].append(deepcopy(plan))
            progression["evaluation_cycles"][cycle_id] = {
                "decision_id": plan["decision_id"],
                "plan_id": plan["plan_id"],
                "recorded_at": timestamp,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        return result["document"]

    def _update_plan(
        self,
        session_id: str,
        instance_id: str,
        plan_id: str,
        updater: Callable[[dict[str, Any], dict[str, Any]], None],
        timestamp: str,
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            progression = self._progression(document, instance_id)
            matches = [item for item in progression["plans"] if item.get("plan_id") == plan_id]
            if len(matches) != 1:
                raise MockValidationError("MOCK_EXECUTION_PLAN_IDENTITY_INVALID")
            updater(document, matches[0])
            self._refresh_plan(document, matches[0])
            self._sync_cycle(document, instance_id, timestamp)
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        return result["document"]

    def _refresh_instance_plans(self, session_id: str, instance_id: str, timestamp: str) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            for plan in self._progression(document, instance_id)["plans"]:
                self._refresh_plan(document, plan)
            self._sync_cycle(document, instance_id, timestamp)
            return document

        return self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]

    def _supersede_plan(
        self, session_id: str, instance_id: str, plan_id: str,
        *, reason: str, timestamp: str,
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            matches = [item for item in self._progression(document, instance_id)["plans"] if item.get("plan_id") == plan_id]
            if len(matches) != 1:
                raise MockValidationError("MOCK_EXECUTION_PLAN_IDENTITY_INVALID")
            plan = matches[0]
            if any(
                child.get("mock_order_id")
                and self._order_by_id(document, child["mock_order_id"]).get("state") in LIVE_STATES
                for child in plan.get("children", [])
            ):
                raise MockValidationError("MOCK_PLAN_SUPERSEDE_ACTIVE_ORDER")
            for child in plan.get("children", []):
                if not child.get("mock_order_id"):
                    child["status"] = "SKIPPED"
                    child["remaining_qty"] = 0
            plan["state"] = "COMPLETED"
            plan["superseded_reason"] = reason
            plan["superseded_at"] = timestamp
            self._sync_cycle(document, instance_id, timestamp)
            return document

        return self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )["document"]

    def _pause_source_for_recovery(
        self, session_id: str, instance_id: str, plan_id: str,
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            plan = next(
                (item for item in self._progression(document, instance_id)["plans"] if item.get("plan_id") == plan_id),
                None,
            )
            if plan is None:
                raise MockValidationError("MOCK_RECOVERY_SOURCE_PLAN_MISSING")
            if any(not child.get("mock_order_id") and child.get("status") != "SKIPPED" for child in plan.get("children", [])):
                plan["state"] = "PAUSED_FOR_RECOVERY"
            return document

        return self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])["document"]

    def _resume_paused_source_after_recovery(
        self, session_id: str, instance_id: str,
    ) -> dict[str, Any]:
        before = self.repository.read_session(session_id)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            plans = self._progression(document, instance_id)["plans"]
            paused = [item for item in plans if item.get("state") == "PAUSED_FOR_RECOVERY"]
            active = [item for item in plans if item.get("state") == "ACTIVE"]
            if len(paused) > 1 or len(active) > 1:
                raise MockValidationError("MOCK_RECOVERY_PLAN_STACK_INVALID")
            if not paused or active:
                return document
            source = paused[0]
            recoveries = [
                item for item in plans
                if item.get("parent_plan_id") == source.get("plan_id")
                and item.get("continuation_kind") == "BUY_RECOVERY"
            ]
            if recoveries and recoveries[-1].get("state") == "COMPLETED":
                source["state"] = "ACTIVE"
            return document

        return self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])["document"]

    def _run_continuation_plan(
        self,
        *,
        session_id: str,
        instance_id: str,
        reference: dict[str, Any],
        evaluation: MockRoutineEvaluationInput,
        decision: dict[str, Any],
        execution_budget: int | float | None,
        trade_fresh: bool,
    ) -> dict[str, Any]:
        source_plan = decision.get("source_plan") if isinstance(decision.get("source_plan"), dict) else {}
        continuation_budget = _positive_number(source_plan.get("execution_budget"))
        if continuation_budget is None:
            continuation_budget = _positive_number(execution_budget)
        if decision.get("action") == ACTION_REPLAN and not isinstance(decision.get("intents"), list):
            decision = self.continuation.materialize_reset(
                session_id,
                routine_instance_id=instance_id,
                evaluation_cycle_id=evaluation.evaluation_cycle_id,
                evaluated_at=evaluation.evaluated_at,
                source_plan=decision["source_plan"],
                market=evaluation.market,
                trade_fresh=trade_fresh,
                execution_budget=float(continuation_budget) if continuation_budget is not None else None,
            )
        intents = [deepcopy(item) for item in decision.get("intents", []) if isinstance(item, dict)]
        signal = deepcopy(decision.get("signal")) if isinstance(decision.get("signal"), dict) else {}
        if not intents or clean_text(signal.get("signal")).upper() not in {"BUY", "SELL"}:
            reason = clean_text(decision.get("reason")) or "CONTINUATION_NO_PLAN"
            document = self.repository.read_session(session_id)
            self._event(
                document, instance_id=instance_id, event_type="CONTINUATION_BLOCKED",
                identity=f"{evaluation.evaluation_cycle_id}:{reason}",
                timestamp=evaluation.evaluated_at.isoformat(timespec="microseconds"),
                reason=reason,
                payload={"action": decision.get("action"), "status": decision.get("status")},
            )
            return {"status": RESULT_WAIT if decision.get("status") == "WAIT" else RESULT_NOOP,
                    "reason": reason, "orders": [], "fills": []}
        document = self.repository.read_session(session_id)
        plan = self._build_plan(
            document=document, instance_id=instance_id, reference=reference,
            signal=signal, intents=intents, evaluation=evaluation,
            execution_budget=continuation_budget,
        )
        if decision.get("action") == ACTION_REPLAN and decision.get("source_plan_id"):
            self._supersede_plan(
                session_id, instance_id, decision["source_plan_id"],
                reason="PRICE_RESET_REPLANNED",
                timestamp=evaluation.evaluated_at.isoformat(timespec="microseconds"),
            )
        elif decision.get("action") == ACTION_RECOVERY and decision.get("source_plan_id"):
            self._pause_source_for_recovery(
                session_id, instance_id, decision["source_plan_id"],
            )
        plan["continuation_kind"] = clean_text(decision.get("continuation_kind"))
        plan["parent_plan_id"] = clean_text(decision.get("source_plan_id"))
        plan["continuation_source_hash"] = clean_text(decision.get("source_hash"))
        document = self._save_plan(
            session_id, instance_id, plan, evaluation.evaluation_cycle_id,
            evaluation.evaluated_at.isoformat(timespec="microseconds"),
        )
        self.continuation.confirm_plan_created(
            session_id, routine_instance_id=instance_id,
            evaluation_cycle_id=evaluation.evaluation_cycle_id,
            action=decision["action"], source_hash=decision["source_hash"],
            plan_id=plan["plan_id"], evaluated_at=evaluation.evaluated_at,
        )
        self._event(
            document, instance_id=instance_id, event_type="EXECUTION_PLAN_CREATED",
            identity=plan["plan_id"],
            timestamp=evaluation.evaluated_at.isoformat(timespec="microseconds"),
            payload={"plan_id": plan["plan_id"], "side": plan["side"], "mode": plan["mode"],
                     "total_qty": plan["total_qty"], "continuation_kind": plan["continuation_kind"]},
        )
        result = self._progress_plan(
            session_id=session_id, instance_id=instance_id, plan=plan,
            evaluation=evaluation, execution_budget=continuation_budget,
            trade_fresh=trade_fresh,
        )
        return {**result, "continuation_action": decision["action"], "plan_id": plan["plan_id"]}

    def _build_plan(
        self,
        *,
        document: dict[str, Any],
        instance_id: str,
        reference: dict[str, Any],
        signal: dict[str, Any],
        intents: list[dict[str, Any]],
        evaluation: MockRoutineEvaluationInput,
        execution_budget: int | float | None,
    ) -> dict[str, Any]:
        side = clean_text(signal.get("signal")).upper()
        if side not in {"BUY", "SELL"} or not intents:
            raise MockValidationError("MOCK_EXECUTION_PLAN_SIGNAL_INVALID")
        mode = _plan_mode(intents)
        sequence = [int(item.get("child_sequence_index", 1)) for item in intents]
        if sequence != list(range(1, len(intents) + 1)):
            raise MockValidationError("MOCK_EXECUTION_CHILD_SEQUENCE_INVALID")
        quantities = [int(item.get("quantity", 0)) for item in intents]
        if any(value <= 0 for value in quantities):
            raise MockValidationError("MOCK_EXECUTION_CHILD_QUANTITY_INVALID")
        rules_hash = reference["rules_hash"]
        source_evaluation_identity = "MRE-" + payload_hash(
            {
                "candles": list(evaluation.candles),
                "signal": signal,
                "evaluated_at": evaluation.evaluated_at.isoformat(timespec="microseconds"),
            }
        )
        round_value = int(intents[0].get("buy_round", 0) or 0)
        generation = int(intents[0].get("plan_generation", 0) or 0)
        source_signal_id = clean_text(
            intents[0].get("source_signal_id") or signal.get("source_signal_id")
            or signal.get("signal_id") or signal.get("id")
        ) or deterministic_mock_identity(
            "MS", document["session"]["validation_session_id"], instance_id,
            source_evaluation_identity,
        )
        execution_process_id = clean_text(intents[0].get("execution_process_id")) or deterministic_mock_identity(
            "MS", document["session"]["validation_session_id"], instance_id, "PROCESS",
            source_signal_id, side,
        )
        existing_cycle_identity = clean_text(
            intents[0].get("cycle_identity")
            or document["cycle_state_by_instance"][instance_id].get("cycle_identity")
        )
        cycle_scope_identity = existing_cycle_identity or (
            "MVC-" + payload_hash([
                document["session"]["validation_session_id"], instance_id,
                source_signal_id, "BUY_CYCLE",
            ]) if side == "BUY" and round_value == 1 else ""
        )
        decision_payload = {
            "validation_session_id": document["session"]["validation_session_id"],
            "routine_instance_id": instance_id,
            "stock_code": document["session"]["stock_code"],
            "evaluation_cycle_id": evaluation.evaluation_cycle_id,
            "side": side,
            "generation": generation,
            "round": round_value,
            "source_evaluation_identity": source_evaluation_identity,
            "rules_hash": rules_hash,
            "market_evidence_identity": evaluation.market.snapshot_identity if evaluation.market else "",
        }
        decision_id = "MRD-" + payload_hash(decision_payload)
        plan_id = "MRP-" + payload_hash([decision_id, mode, quantities])
        started = evaluation.evaluated_at.isoformat(timespec="microseconds")
        children: list[dict[str, Any]] = []
        for index, intent in enumerate(intents, 1):
            intent = deepcopy(intent)
            intent["source_signal_id"] = source_signal_id
            intent["execution_process_id"] = execution_process_id
            if cycle_scope_identity:
                intent["cycle_identity"] = cycle_scope_identity
            child_plan = intent.get("child_plan") if isinstance(intent.get("child_plan"), dict) else {}
            offset = int(child_plan.get("scheduled_offset_ms", 0) or 0)
            child_id = "MRC-" + payload_hash([plan_id, generation, index])
            children.append(
                {
                    "child_id": child_id,
                    "child_sequence": index,
                    "generation": generation,
                    "round": round_value,
                    "quantity": quantities[index - 1],
                    "intent": deepcopy(intent),
                    "due_at": (evaluation.evaluated_at + timedelta(milliseconds=offset)).isoformat(timespec="microseconds"),
                    "status": "PLANNED",
                    "mock_order_id": "",
                    "filled_qty": 0,
                    "remaining_qty": quantities[index - 1],
                }
            )
        plan = {
            "plan_id": plan_id,
            "decision_id": decision_id,
            "decision_identity": decision_payload,
            "side": side,
            "mode": mode,
            "state": "ACTIVE",
            "generation": generation,
            "round": round_value,
            "total_qty": sum(quantities),
            "price_basis": clean_text(intents[0].get("price_basis")).upper(),
            "plan_started_at": started,
            "total_children": len(children),
            "next_child_index": 1,
            "emitted_qty": 0,
            "filled_qty": 0,
            "remaining_qty": sum(quantities),
            "market_evidence_identity": evaluation.market.snapshot_identity if evaluation.market else "",
            "rules_hash": rules_hash,
            "source_signal_id": source_signal_id,
            "execution_process_id": execution_process_id,
            "execution_budget": (
                float(execution_budget)
                if side == "BUY" and execution_budget is not None
                else None
            ),
            "cycle_scope_identity": cycle_scope_identity,
            "children": children,
            "supplements": [],
        }
        if mode == MODE_MULTI_TIME:
            plan["schedule"] = {
                "interval": deepcopy(intents[0].get("multi_time_plan", {})).get("time_value"),
                "interval_unit": deepcopy(intents[0].get("multi_time_plan", {})).get("time_unit"),
            }
        if mode == MODE_MULTI_RATIO:
            plan["ratio"] = deepcopy(intents[0].get("multi_ratio_plan", {}))
        if mode == MODE_MULTI_HOGA:
            plan["hoga"] = deepcopy(intents[0].get("multi_hoga_plan", {}))
        return plan

    def _ratio_eligible(
        self,
        plan: dict[str, Any],
        *,
        document: dict[str, Any],
        instance_id: str,
        market: MockMarketSnapshot | None,
        trade_fresh: bool,
    ) -> tuple[bool | None, str, dict[str, Any]]:
        ratio = plan.get("ratio") if isinstance(plan.get("ratio"), dict) else {}
        left_source = clean_text(ratio.get("ratio_left")).upper()
        right_source = clean_text(ratio.get("ratio_right")).upper()
        current_price = (
            _positive_number(market.trade.current_price)
            if market is not None and market.trade is not None and trade_fresh
            else None
        )
        if "CURRENT_PRICE" in {left_source, right_source} and current_price is None:
            return False, "RATIO_CURRENT_PRICE_UNAVAILABLE", {}
        position = _position(document, instance_id)
        average_price = _positive_number(position.get("average_price"))
        if "AVG_PRICE" in {left_source, right_source} and average_price is None:
            return None, "RATIO_AVERAGE_PRICE_UNAVAILABLE", {}
        order_price = _positive_number(ratio.get("order_price"))
        left = resolve_price_source(
            left_source,
            order_price=float(order_price) if order_price is not None else None,
            current_price=float(current_price) if current_price is not None else None,
            average_price=float(average_price) if average_price is not None else None,
        )
        right = resolve_price_source(
            right_source,
            order_price=float(order_price) if order_price is not None else None,
            current_price=float(current_price) if current_price is not None else None,
            average_price=float(average_price) if average_price is not None else None,
        )
        threshold = _positive_number(ratio.get("ratio_value"))
        if left is None or right is None or threshold is None:
            return None, "RATIO_TRIGGER_SOURCE_INVALID", {}
        eligible, observed = evaluate_percent_comparison(
            left=left,
            right=right,
            direction=clean_text(ratio.get("ratio_direction")).upper(),
            compare=clean_text(ratio.get("ratio_compare")).upper(),
            threshold=float(threshold),
        )
        evidence = {
            "left_source": left_source,
            "left_price": left,
            "right_source": right_source,
            "right_price": right,
            "threshold_percent": threshold,
            "observed_percent": observed,
        }
        if eligible is None:
            return None, "RATIO_TRIGGER_POLICY_INVALID", evidence
        return eligible, "" if eligible else "RATIO_THRESHOLD_NOT_MET", evidence

    def _submit_child(
        self,
        *,
        session_id: str,
        instance_id: str,
        plan: dict[str, Any],
        child: dict[str, Any],
        evaluation: MockRoutineEvaluationInput,
        execution_budget: int | float | None,
    ) -> dict[str, Any]:
        intent = child["intent"]
        order_type, price = _child_order_type(intent)
        command_id = deterministic_mock_identity(
            "MC", session_id, instance_id, plan["plan_id"], child["child_id"], "SUBMIT"
        )
        result = self.engine.submit_order(
            session_id,
            routine_instance_id=instance_id,
            side=plan["side"],
            order_type=order_type,
            requested_qty=child["quantity"],
            limit_price=price,
            market=evaluation.market,
            policy=evaluation.policy,
            execution_budget=execution_budget if plan["side"] == "BUY" else None,
            generation=plan["generation"],
            child_identity=child["child_id"],
            command_id=command_id,
        )
        if result.get("status") == RESULT_BLOCKED:
            return result
        order = result.get("order")
        if not isinstance(order, dict):
            raise MockValidationError("MOCK_EXECUTION_CHILD_ORDER_RESULT_INVALID")
        timestamp = evaluation.evaluated_at.isoformat(timespec="microseconds")

        def updater(document: dict[str, Any], stored: dict[str, Any]) -> None:
            matches = [item for item in stored["children"] if item.get("child_id") == child["child_id"]]
            if len(matches) != 1:
                raise MockValidationError("MOCK_EXECUTION_CHILD_IDENTITY_CONFLICT")
            target = matches[0]
            if target.get("mock_order_id") and target.get("mock_order_id") != order["mock_order_id"]:
                raise MockValidationError("MOCK_EXECUTION_CHILD_ORDER_CONFLICT")
            target["mock_order_id"] = order["mock_order_id"]
            target["status"] = order["state"]
            target["filled_qty"] = int(order["filled_qty"])
            target["remaining_qty"] = int(order["remaining_qty"])
            stored["next_child_index"] = max(int(stored.get("next_child_index", 1)), int(target["child_sequence"]) + 1)
            stored["emitted_qty"] = sum(
                int(item["quantity"]) for item in stored["children"] if item.get("mock_order_id")
            )
            plan_order_ids = {item.get("mock_order_id") for item in stored["children"] if item.get("mock_order_id")}
            stored["filled_qty"] = sum(
                int(item["qty"]) for item in document["fills"] if item.get("mock_order_id") in plan_order_ids
            )
            stored["remaining_qty"] = max(0, int(stored["total_qty"]) - int(stored["filled_qty"]))

        persisted = self._update_plan(session_id, instance_id, plan["plan_id"], updater, timestamp)
        persisted_plan = next(
            item for item in self._progression(persisted, instance_id)["plans"] if item["plan_id"] == plan["plan_id"]
        )
        self._event(
            persisted,
            instance_id=instance_id,
            event_type="EXECUTION_CHILD_CREATED",
            identity=child["child_id"],
            timestamp=timestamp,
            payload={
                "plan_id": plan["plan_id"],
                "child_id": child["child_id"],
                "mock_order_id": order["mock_order_id"],
                "mode": plan["mode"],
            },
        )
        if order["state"] in _TERMINAL_ORDER_STATES:
            self._event(
                persisted,
                instance_id=instance_id,
                event_type="EXECUTION_CHILD_COMPLETED",
                identity=f"{child['child_id']}:{order['state']}",
                timestamp=timestamp,
                payload={"plan_id": plan["plan_id"], "mock_order_id": order["mock_order_id"], "state": order["state"]},
            )
        return {**result, "plan": deepcopy(persisted_plan), "document": persisted}

    def _progress_plan(
        self,
        *,
        session_id: str,
        instance_id: str,
        plan: dict[str, Any],
        evaluation: MockRoutineEvaluationInput,
        execution_budget: int | float | None,
        trade_fresh: bool,
    ) -> dict[str, Any]:
        timestamp = evaluation.evaluated_at.isoformat(timespec="microseconds")
        document = self._update_plan(
            session_id,
            instance_id,
            plan["plan_id"],
            lambda _document, _plan: None,
            timestamp,
        )
        plan = next(
            item for item in self._progression(document, instance_id)["plans"]
            if item.get("plan_id") == plan["plan_id"]
        )
        live = self._active_orders(document, instance_id)
        uncreated = [child for child in plan["children"] if not child.get("mock_order_id")]
        if plan["mode"] == MODE_SINGLE and live:
            return {"status": RESULT_WAIT, "reason": "BLOCKED_ACTIVE_ORDER", "orders": [], "fills": [], "plan": deepcopy(plan)}
        if not uncreated:
            if plan["mode"] == MODE_MULTI_HOGA and plan["side"] == "SELL" and not live:
                return self._progress_supplement(
                    session_id=session_id,
                    instance_id=instance_id,
                    plan=plan,
                    evaluation=evaluation,
                )
            return {"status": RESULT_NOOP, "reason": "EXECUTION_PLAN_COMPLETE_OR_WAITING", "orders": [], "fills": [], "plan": deepcopy(plan)}

        selected = uncreated if plan["mode"] == MODE_MULTI_HOGA else uncreated[:1]
        if plan["mode"] == MODE_MULTI_TIME:
            due = _aware(selected[0]["due_at"])
            if due is None:
                raise MockValidationError("MOCK_MULTI_TIME_DUE_AT_INVALID")
            if evaluation.evaluated_at < due:
                return {"status": RESULT_WAIT, "reason": "TIME_CHILD_NOT_DUE", "orders": [], "fills": [], "plan": deepcopy(plan)}
        if plan["mode"] == MODE_MULTI_RATIO:
            eligible, reason, evidence = self._ratio_eligible(
                plan, document=document, instance_id=instance_id,
                market=evaluation.market, trade_fresh=trade_fresh,
            )
            if eligible is None:
                raise MockValidationError(reason)
            if not eligible:
                return {"status": RESULT_WAIT, "reason": reason, "ratio_evidence": evidence, "orders": [], "fills": [], "plan": deepcopy(plan)}
            selected[0]["ratio_trigger_evidence"] = evidence

        results: list[dict[str, Any]] = []
        for child in selected:
            result = self._submit_child(
                session_id=session_id,
                instance_id=instance_id,
                plan=plan,
                child=child,
                evaluation=evaluation,
                execution_budget=execution_budget,
            )
            if result.get("status") == RESULT_BLOCKED:
                return {**result, "orders": [item.get("order") for item in results if item.get("order")], "fills": []}
            results.append(result)
            document = result["document"]
            plan = result["plan"]
        return {
            "status": RESULT_PROGRESSED,
            "reason": "",
            "orders": [deepcopy(item["order"]) for item in results],
            "fills": [deepcopy(fill) for item in results for fill in item.get("fills", [])],
            "plan": deepcopy(plan),
            "document": document,
        }

    def _progress_supplement(
        self,
        *,
        session_id: str,
        instance_id: str,
        plan: dict[str, Any],
        evaluation: MockRoutineEvaluationInput,
    ) -> dict[str, Any]:
        document = self.repository.read_session(session_id)
        original_ids = {item.get("mock_order_id") for item in plan["children"] if item.get("mock_order_id")}
        supplement_ids = {item.get("mock_order_id") for item in plan.get("supplements", []) if item.get("mock_order_id")}
        active_supplement = [
            order for order in document["orders"]
            if order.get("mock_order_id") in supplement_ids and order.get("state") in LIVE_STATES
        ]
        if active_supplement:
            return {"status": RESULT_WAIT, "reason": "SUPPLEMENT_ORDER_ACTIVE", "orders": [], "fills": [], "plan": deepcopy(plan)}
        sold = sum(
            int(fill["qty"])
            for fill in document["fills"]
            if fill.get("side") == "SELL" and fill.get("mock_order_id") in original_ids | supplement_ids
        )
        position = _position(document, instance_id)
        shortage = min(max(0, int(plan["total_qty"]) - sold), int(position["available_qty"]))
        if shortage <= 0:
            return {"status": RESULT_NOOP, "reason": "SUPPLEMENT_NOT_REQUIRED", "orders": [], "fills": [], "plan": deepcopy(plan)}
        sequence = len(plan.get("supplements", [])) + 1
        base_price = _positive_number(deepcopy(plan.get("hoga", {})).get("base_price"))
        if base_price is None:
            raise MockValidationError("MOCK_SUPPLEMENT_PRICE_INVALID")
        child = {
            "child_id": "MRC-" + payload_hash([plan["plan_id"], "SUPPLEMENT", sequence, shortage]),
            "child_sequence": int(plan["total_children"]) + sequence,
            "generation": int(plan["generation"]),
            "round": int(plan["round"]),
            "quantity": shortage,
            "intent": {"hoga": "LIMIT", "price_basis": "ORDER_PRICE", "price": base_price},
            "due_at": evaluation.evaluated_at.isoformat(timespec="microseconds"),
            "status": "PLANNED",
            "mock_order_id": "",
            "filled_qty": 0,
            "remaining_qty": shortage,
            "child_kind": "SUPPLEMENT",
        }
        timestamp = evaluation.evaluated_at.isoformat(timespec="microseconds")
        before = self.repository.read_session(session_id)

        def add(document_value: dict[str, Any]) -> dict[str, Any]:
            stored = next(
                item for item in self._progression(document_value, instance_id)["plans"]
                if item.get("plan_id") == plan["plan_id"]
            )
            if any(item.get("child_id") == child["child_id"] for item in stored["children"] + stored["supplements"]):
                return document_value
            stored["supplements"].append(deepcopy(child))
            stored["children"].append(deepcopy(child))
            stored["total_children"] = len(stored["children"])
            stored["state"] = "ACTIVE"
            return document_value

        added = self.repository.mutate_session(session_id, add, expected_revision=before["revision"])["document"]
        stored = next(item for item in self._progression(added, instance_id)["plans"] if item["plan_id"] == plan["plan_id"])
        selected = next(item for item in stored["children"] if item["child_id"] == child["child_id"])
        return self._submit_child(
            session_id=session_id,
            instance_id=instance_id,
            plan=stored,
            child=selected,
            evaluation=evaluation,
            execution_budget=None,
        )

    def market_bar_projection_request(self, rules: dict[str, Any] | None) -> dict[str, Any]:
        return deepcopy(_market_projection_requester()(rules))

    def evaluate_cycle(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        candles: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        market: MockMarketSnapshot | None,
        policy: MockExecutionPolicy,
        evaluation_cycle_id: str,
        evaluated_at: datetime | None = None,
        new_buy_allowed: bool = True,
        candle_availability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._evaluate_cycle_impl(
            session_id,
            routine_instance_id=routine_instance_id,
            candles=candles,
            market=market,
            policy=policy,
            evaluation_cycle_id=evaluation_cycle_id,
            evaluated_at=evaluated_at,
            new_buy_allowed=new_buy_allowed,
            candle_availability=candle_availability,
        )
        normal_block = result.pop("_normal_block_episode", False) is True
        if (
            not normal_block
            and result.get("status") != "INSTANCE_ERROR"
            and result.get("reason") != "MOCK_EVALUATION_IDENTITY_INVALID"
        ):
            now = evaluated_at or self._now()
            timestamp = now.isoformat(timespec="microseconds") if now.tzinfo is not None else ""
            if timestamp:
                self._clear_normal_block_episode(
                    session_id,
                    clean_text(routine_instance_id),
                    cleared_at=timestamp,
                )
        return result

    def _evaluate_cycle_impl(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        candles: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        market: MockMarketSnapshot | None,
        policy: MockExecutionPolicy,
        evaluation_cycle_id: str,
        evaluated_at: datetime | None = None,
        new_buy_allowed: bool = True,
        candle_availability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instance_id = clean_text(routine_instance_id)
        cycle_id = clean_text(evaluation_cycle_id)
        now = evaluated_at or self._now()
        timestamp = now.isoformat(timespec="microseconds") if now.tzinfo is not None else ""
        if not cycle_id or not timestamp:
            return {"status": RESULT_BLOCKED, "reason": "MOCK_EVALUATION_IDENTITY_INVALID", "orders": [], "fills": []}
        try:
            document = self.repository.read_session(session_id)
            if instance_id not in document["instance_execution"]:
                raise MockValidationError("MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION")
            if document["session"]["state"] != SESSION_RUNNING:
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="MOCK_SESSION_NOT_RUNNING", status=RESULT_WAIT,
                )
            if document["instance_execution"][instance_id].get("progression_allowed") is not True:
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="MOCK_INSTANCE_PROGRESSION_BLOCKED", status=RESULT_WAIT,
                )
            availability = candle_availability if isinstance(candle_availability, dict) else {}
            if availability.get("reason") == "CANDLE_WARMUP_LIMIT_EXCEEDED":
                blocked = self._normal_block(
                    document,
                    instance_id=instance_id,
                    cycle_id=cycle_id,
                    timestamp=timestamp,
                    reason="CANDLE_WARMUP_LIMIT_EXCEEDED",
                    status=RESULT_WAIT,
                )
                blocked["required_minute_candles"] = availability.get("required_minute_candles")
                blocked["maximum_minute_candles"] = availability.get("maximum_minute_candles")
                return blocked
            reference = _instance_reference(document, instance_id)
            if clean_text(reference.get("routine_type")).upper() not in {"INDICATOR_FOLLOW", "지표추종매매"}:
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="MOCK_ROUTINE_TYPE_UNSUPPORTED",
                )
            progression = self._progression(document, instance_id)
            if cycle_id in progression["evaluation_cycles"]:
                return {"status": RESULT_NOOP, "reason": "MOCK_EVALUATION_CYCLE_ALREADY_PROCESSED", "orders": [], "fills": []}
            book_fresh, trade_fresh, market_reason = _market_freshness(market, evaluated_at=now, policy=policy)
            if not book_fresh:
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason=market_reason, status=RESULT_WAIT,
                )
            if market is None or market.stock_code != document["session"]["stock_code"]:
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="MOCK_MARKET_STOCK_MISMATCH", status=RESULT_WAIT,
                )
            if trade_fresh and market.trade is not None:
                mark_price = _positive_number(market.trade.current_price)
                if mark_price is not None:
                    marked = self.engine.mark_to_market(
                        session_id,
                        instance_id,
                        current_price=mark_price,
                        market_identity=market.snapshot_identity,
                        command_id=deterministic_mock_identity(
                            "MC", session_id, instance_id,
                            market.snapshot_identity, "MARK_TO_MARKET",
                        ),
                    )
                    document = marked["document"]
            rules = _operation_rules(document, instance_id) or deepcopy(reference["rules_snapshot"])
            reference = {
                **deepcopy(reference),
                "rules_snapshot": deepcopy(rules),
                "rules_hash": payload_hash(rules),
            }
            stock_config, execution_budget = _mock_settings(rules)
            stock_config, execution_budget = _apply_effective_initial_buy(
                stock_config,
                execution_budget,
                _operation_effective_settings(document, instance_id),
            )
            evaluation = MockRoutineEvaluationInput(
                evaluation_cycle_id=cycle_id,
                candles=tuple(deepcopy(list(candles))),
                evaluated_at=now,
                market=market,
                policy=policy,
            )
            document = self._refresh_instance_plans(session_id, instance_id, timestamp)
            document = self._resume_paused_source_after_recovery(session_id, instance_id)
            progression = self._progression(document, instance_id)
            active_plans = [item for item in progression["plans"] if item.get("state") == "ACTIVE"]
            if len(active_plans) > 1:
                raise MockValidationError("MOCK_MULTIPLE_ACTIVE_EXECUTION_PLANS")
            position = _position(document, instance_id)
            current_price = (
                _positive_number(market.trade.current_price)
                if market is not None and market.trade is not None and trade_fresh
                else None
            )
            reference_price = _reference_price(evaluation.candles)
            cycle = deepcopy(document["cycle_state_by_instance"][instance_id])
            cycle.setdefault("status", "resolved")
            cycle.setdefault("active", bool(position["holding_qty"] > 0 and cycle.get("cycle_identity")))
            cycle.update(
                {
                    "holding_qty": int(position["holding_qty"]),
                    "available_qty": int(position["available_qty"]),
                    "average_price": position["average_price"],
                    "confirmed_buy_round": int(cycle.get("confirmed_buy_round", 0) or 0),
                    "cumulative_filled_buy_amount": cycle.get("cumulative_filled_buy_amount", 0),
                    "pending_buy_rounds": [],
                    "pending_buy_order_identities": [],
                }
            )
            context = {
                "candles": list(evaluation.candles),
                "rules": rules,
                "routine_config": rules,
                "routine_instance_id": instance_id,
                "cycle": cycle,
                "stock_config": stock_config,
                "reference_price": reference_price,
                "current_price": current_price,
                "actionable_current_price": current_price,
                "holding_qty": int(position["holding_qty"]),
                "average_price": position["average_price"],
                "now": timestamp,
            }
            pending_result, pending_signal = self._advance_pending_successor(
                session_id, instance_id, cycle_id=cycle_id, evaluated_at=now,
                rules=rules,
            )
            if pending_result is not None:
                return pending_result
            raw_signal = None if pending_signal is not None else self._evaluator(list(evaluation.candles), rules, context)
            signal = deepcopy(pending_signal) if pending_signal is not None else _signal_payload(raw_signal)
            if active_plans:
                arbitration_result, successor = self._arbitrate_active_signal(
                    session_id, instance_id, cycle_id=cycle_id, evaluated_at=now,
                    rules=rules, signal=signal, active_plan=active_plans[0],
                )
                if arbitration_result is not None:
                    return arbitration_result
                if successor is not None:
                    signal = successor
                    document = self._refresh_instance_plans(session_id, instance_id, timestamp)
                    progression = self._progression(document, instance_id)
                    active_plans = [item for item in progression["plans"] if item.get("state") == "ACTIVE"]
            continuation_result = self.continuation.inspect_active(
                session_id, routine_instance_id=instance_id,
                evaluation_cycle_id=cycle_id, evaluated_at=now,
                market=market, trade_fresh=trade_fresh,
            )
            if continuation_result is not None:
                if continuation_result.get("action") in {ACTION_REPLAN, ACTION_RECOVERY} and continuation_result.get("status") == "READY":
                    return self._run_continuation_plan(
                        session_id=session_id, instance_id=instance_id,
                        reference=reference, evaluation=evaluation,
                        decision=continuation_result, execution_budget=execution_budget,
                        trade_fresh=trade_fresh,
                    )
                if continuation_result.get("action") == ACTION_EXIT and active_plans:
                    self._supersede_plan(
                        session_id, instance_id, active_plans[0]["plan_id"],
                        reason=clean_text(continuation_result.get("reason")) or "CONTINUATION_EXIT",
                        timestamp=timestamp,
                    )
                return continuation_result
            if active_plans:
                plan_budget = _positive_number(active_plans[0].get("execution_budget"))
                if plan_budget is None:
                    plan_budget = _positive_number(execution_budget)
                result = self._progress_plan(
                    session_id=session_id,
                    instance_id=instance_id,
                    plan=deepcopy(active_plans[0]),
                    evaluation=evaluation,
                    execution_budget=plan_budget,
                    trade_fresh=trade_fresh,
                )
                before_mark = self.repository.read_session(session_id)

                def mark(document_value: dict[str, Any]) -> dict[str, Any]:
                    self._progression(document_value, instance_id)["evaluation_cycles"][cycle_id] = {
                        "plan_id": active_plans[0]["plan_id"], "recorded_at": timestamp,
                        "result": result.get("status"),
                    }
                    return document_value

                self.repository.mutate_session(session_id, mark, expected_revision=before_mark["revision"])
                return result

            if progression["plans"]:
                terminal = self.continuation.terminal_decision(
                    session_id, routine_instance_id=instance_id,
                    evaluation_cycle_id=cycle_id, evaluated_at=now,
                    market=market, trade_fresh=trade_fresh,
                )
                if terminal is not None:
                    if terminal.get("status") == "READY":
                        return self._run_continuation_plan(
                            session_id=session_id, instance_id=instance_id,
                            reference=reference, evaluation=evaluation,
                            decision=terminal, execution_budget=execution_budget,
                            trade_fresh=trade_fresh,
                        )
                    return terminal
                if progression["plans"][-1].get("side") == "SELL" and int(_position(document, instance_id)["holding_qty"]) > 0:
                    return self._normal_block(
                        document, instance_id=instance_id, cycle_id=cycle_id,
                        timestamp=timestamp, reason="SELL_CONTINUATION_NOT_CONFIGURED", status=RESULT_NOOP,
                    )
            continuation_state = self.continuation.state(document, instance_id)
            buy_exit_evidence = continuation_state.get("buy_exit") if isinstance(continuation_state.get("buy_exit"), dict) else None
            if (
                clean_text(signal.get("signal")).upper() == "BUY"
                and buy_exit_evidence is not None
                and clean_text(cycle.get("cycle_identity"))
                and clean_text(buy_exit_evidence.get("cycle_identity")) == clean_text(cycle.get("cycle_identity"))
            ):
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="BUY_EXIT_ACTIVE", status=RESULT_WAIT,
                    evaluation_completed=True,
                )
            self._event(
                document,
                instance_id=instance_id,
                event_type="ROUTINE_EVALUATED",
                identity=cycle_id,
                timestamp=timestamp,
                payload={
                    "signal": signal.get("signal"),
                    "rules_hash": reference["rules_hash"],
                    "operation_identity": self._operation_identity(document, instance_id),
                    "evaluation_cycle_id": cycle_id,
                    **_signal_marker_payload(signal, evaluation.candles),
                },
            )
            side = clean_text(signal.get("signal")).upper()
            if side not in {"BUY", "SELL"}:
                before_mark = self.repository.read_session(session_id)

                def no_signal_mark(document_value: dict[str, Any]) -> dict[str, Any]:
                    self._progression(document_value, instance_id)["evaluation_cycles"][cycle_id] = {
                        "result": RESULT_NO_SIGNAL, "recorded_at": timestamp,
                    }
                    return document_value

                self.repository.mutate_session(session_id, no_signal_mark, expected_revision=before_mark["revision"])
                return {"status": RESULT_NO_SIGNAL, "reason": clean_text(signal.get("reason")), "orders": [], "fills": [], "signal": signal}
            if side == "BUY" and new_buy_allowed is not True:
                return self._normal_block(
                    document,
                    instance_id=instance_id,
                    cycle_id=cycle_id,
                    timestamp=timestamp,
                    reason="MOCK_SCHEDULED_END_BUY_REACHED",
                    status=RESULT_WAIT,
                    evaluation_completed=True,
                )
            explicit_signal_id = clean_text(signal.get("source_signal_id") or signal.get("signal_id") or signal.get("id"))
            if explicit_signal_id and any(item.get("source_signal_id") == explicit_signal_id for item in progression["plans"]):
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="MOCK_SOURCE_SIGNAL_ALREADY_PROCESSED", status=RESULT_WAIT,
                    evaluation_completed=True,
                )
            if side == "SELL":
                if int(position["holding_qty"]) <= 0 or int(position["available_qty"]) <= 0:
                    return self._normal_block(
                        document, instance_id=instance_id, cycle_id=cycle_id,
                        timestamp=timestamp, reason="SELL_HOLDING_QUANTITY_INVALID",
                        evaluation_completed=True,
                    )
                if cycle.get("active") is not True:
                    return self._normal_block(
                        document, instance_id=instance_id, cycle_id=cycle_id,
                        timestamp=timestamp, reason="MOCK_SELL_ACTIVE_CYCLE_REQUIRED",
                        evaluation_completed=True,
                    )
            elif self._active_orders(document, instance_id):
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason="BLOCKED_ACTIVE_ORDER", status=RESULT_WAIT,
                    evaluation_completed=True,
                )
            self._event(
                document,
                instance_id=instance_id,
                event_type="ROUTINE_SELL_DECISION" if side == "SELL" else "ROUTINE_BUY_DECISION",
                identity=cycle_id,
                timestamp=timestamp,
                payload={"reason": signal.get("reason")},
            )
            accepted_signal_id = explicit_signal_id or deterministic_mock_identity(
                "MS", session_id, instance_id, cycle_id, side, "BUDGET_TRANSITION"
            )
            self.session_service.transition_instance_initial_buy_for_signal(
                session_id,
                routine_instance_id=instance_id,
                signal=side,
                signal_id=accepted_signal_id,
                observed_at=timestamp,
            )
            build_result = (
                self._sell_builder(sell_signal_result=signal, context=context)
                if side == "SELL"
                else self._buy_builder(buy_signal_result=signal, context=context)
            )
            if build_result.get("status") != "READY":
                return self._normal_block(
                    document, instance_id=instance_id, cycle_id=cycle_id,
                    timestamp=timestamp, reason=clean_text(build_result.get("reason")) or "MOCK_EXECUTION_PLAN_BLOCKED",
                    evaluation_completed=True,
                )
            intents = build_result.get("execution_intents")
            if not isinstance(intents, list) or not intents:
                intent = build_result.get("execution_intent")
                intents = [intent] if isinstance(intent, dict) else []
            intents = [deepcopy(item) for item in intents if isinstance(item, dict)]
            if side == "SELL" and intents and clean_text(intents[0].get("execution_mode")).upper() == MODE_MULTI_HOGA:
                hoga = intents[0].get("multi_hoga_plan") if isinstance(intents[0].get("multi_hoga_plan"), dict) else {}
                configured = hoga.get("configured_child_count") or len(hoga.get("hoga_offsets", []))
                raw_setting = rules.get("sell", {}).get("method", {}) if isinstance(rules.get("sell"), dict) else {}
                selected = raw_setting.get("selected_sets", []) if isinstance(raw_setting, dict) else []
                setting = raw_setting.get(selected[0], {}) if len(selected) == 1 and isinstance(raw_setting.get(selected[0]), dict) else {}
                expected_children = 1 + int(float(setting.get("perform1_multi_up_line", 0) or 0)) + int(float(setting.get("perform1_multi_down_line", 0) or 0))
                if int(position["available_qty"]) < expected_children or (configured and int(configured) != expected_children):
                    return self._normal_block(
                        document, instance_id=instance_id, cycle_id=cycle_id,
                        timestamp=timestamp, reason="SELL_MULTI_HOGA_QUANTITY_BELOW_CHILD_COUNT",
                        evaluation_completed=True,
                    )
            plan_budget = execution_budget or _positive_number(intents[0].get("budget"))
            plan = self._build_plan(
                document=document,
                instance_id=instance_id,
                reference=reference,
                signal=signal,
                intents=intents,
                evaluation=evaluation,
                execution_budget=plan_budget,
            )
            if side == "BUY" and int(plan.get("round", 0) or 0) > 1:
                plan["continuation_kind"] = "BUY_REPEAT"
                plan["repeat_source_snapshot_hash"] = payload_hash(
                    [plan["source_signal_id"], plan["round"], cycle.get("cycle_identity"), position.get("updated_at")]
                )
            document = self._save_plan(session_id, instance_id, plan, cycle_id, timestamp)
            self._event(
                document,
                instance_id=instance_id,
                event_type="EXECUTION_PLAN_CREATED",
                identity=plan["plan_id"],
                timestamp=timestamp,
                payload={"plan_id": plan["plan_id"], "side": side, "mode": plan["mode"], "total_qty": plan["total_qty"]},
            )
            if plan.get("continuation_kind") == "BUY_REPEAT":
                self._event(
                    document, instance_id=instance_id, event_type="BUY_REPEAT_TRIGGERED",
                    identity=plan["repeat_source_snapshot_hash"], timestamp=timestamp,
                    payload={"plan_id": plan["plan_id"], "round": plan["round"],
                             "source_signal_id": plan["source_signal_id"]},
                )
                self._event(
                    document, instance_id=instance_id, event_type="BUY_REPEAT_ROUND_STARTED",
                    identity=plan["plan_id"], timestamp=timestamp,
                    payload={"plan_id": plan["plan_id"], "round": plan["round"]},
                )
            result = self._progress_plan(
                session_id=session_id,
                instance_id=instance_id,
                plan=plan,
                evaluation=evaluation,
                execution_budget=plan_budget,
                trade_fresh=trade_fresh,
            )
            return {**result, "signal": signal, "decision_id": plan["decision_id"], "plan_id": plan["plan_id"]}
        except MockValidationError as exc:
            return self._integrity_stop(session_id, instance_id, cycle_id, exc)
        except Exception as exc:
            return self._integrity_stop(
                session_id,
                instance_id,
                cycle_id,
                MockValidationError(f"MOCK_ROUTINE_ADAPTER_FAILURE:{type(exc).__name__}:{exc}"),
            )


__all__ = [
    "MODE_MULTI_HOGA",
    "MODE_MULTI_RATIO",
    "MODE_MULTI_TIME",
    "MODE_SINGLE",
    "MockIndicatorFollowRoutineAdapter",
    "MockRoutineEvaluationInput",
    "RESULT_NO_SIGNAL",
    "RESULT_NOOP",
    "RESULT_PROGRESSED",
    "RESULT_WAIT",
]
