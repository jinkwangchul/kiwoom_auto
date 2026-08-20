# -*- coding: utf-8 -*-
"""Production Routine invested-principal response coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from auto_trade_order_execution_boundary import CANCEL_SIDE_SCOPE_BUY_ONLY, project_buy_only_cancel_readiness
from buffer_response_candidate_selector import existing_close_exclusion_reason
from buffer_response_ownership_service import BufferResponseOwnershipService
from close_intent_service import CLOSE_INTENT_EARLY_CLOSE, apply_close_intent
from close_liquidation_transition_service import POLICY_MARKET, POLICY_ROUTINE_CLOSE, is_routine_close_policy, normalize_direct_close_policy_alias
from gui_auto_trade_policy import auto_trade_current_session_operation_participant_codes
from gui_operation_environment import read_operation_policy
from operation_close_completion_evaluator import evaluate_operation_close_completion, resolve_liquidation_holding_quantity
from operation_command_service import MODE_EARLY_CLOSE, SCOPE_STOCK
from pnl_ui_refresh import project_current_stock_pnl_snapshot
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import production_recovery_registry
from routine_instance_registry import default_routine_limit_response_policy, routine_instance_by_id, validate_routine_limit_response_policy
from routine_limit_response_ownership_service import (
    INTENT_EARLY_CLOSE, INTENT_IMMEDIATE, STATUS_OWNED,
    RoutineLimitResponseOwnershipService,
)
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"
SOURCE_PREFIX = "ROUTINE_LIMIT_RESPONSE:"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _code(value: object) -> str:
    return _text(value).lstrip("A")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _result(reason: str = "", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "evaluated": False, "settled": False, "owns_response": False,
        "routine_instance_id": "", "invested_principal": None,
        "buy_limit_amount": None, "usage_percent": None,
        "policy_projected": False, "selected_strategy": "",
        "configured_response_mode": "", "effective_response_intent": "",
        "candidate_selected": False, "selected_stock_code": "",
        "ownership_claimed": False, "ownership_existing": False,
        "ownership_completed": False, "ownership_promoted": False,
        "higher_priority_blocked": False, "early_close_requested": False,
        "immediate_preparation_state": "", "immediate_dispatch_requested": False,
        "reason": _text(reason), "event_id": "",
    }
    result.update(updates)
    return result


def routine_limit_source(event_id: object) -> str:
    identity = _text(event_id)
    return f"{SOURCE_PREFIX}{identity}" if identity else ""


def routine_limit_early_command_id(event_id: object) -> str:
    identity = _text(event_id)
    return str(uuid5(NAMESPACE_URL, f"kiwoom-auto:routine-limit:{identity}:EARLY_CLOSE")) if identity else ""


def routine_limit_immediate_command_id(event_id: object) -> str:
    identity = _text(event_id)
    return str(uuid5(NAMESPACE_URL, f"kiwoom-auto:routine-limit:{identity}:IMMEDIATE_MARKET_CLOSE")) if identity else ""


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} root must be an object")
    return value


def project_routine_limit_policy(
    *, policy: object, invested_principal: object, buy_limit_amount: object,
    pnl_by_stock: object,
) -> dict[str, object]:
    principal = _decimal(invested_principal)
    limit = _positive_int(buy_limit_amount)
    if principal is None or principal < 0 or limit is None:
        return {"available": False, "reason": "ROUTINE_PRINCIPAL_OR_LIMIT_INVALID"}
    try:
        normalized = validate_routine_limit_response_policy(policy)
    except ValueError as exc:
        return {"available": False, "reason": str(exc)}
    if not isinstance(pnl_by_stock, Mapping):
        return {"available": False, "reason": "CONFIRMABLE_PNL_SNAPSHOT_UNAVAILABLE"}
    total_profit = Decimal("0")
    normalized_pnl: dict[str, Mapping[str, object]] = {}
    for raw_code, raw_value in pnl_by_stock.items():
        if not isinstance(raw_value, Mapping) or raw_value.get("available") is not True:
            return {"available": False, "reason": "CONFIRMABLE_PNL_SNAPSHOT_UNAVAILABLE"}
        profit = _decimal(raw_value.get("cumulative_profit"))
        if profit is None:
            return {"available": False, "reason": "CONFIRMABLE_PNL_SNAPSHOT_UNAVAILABLE"}
        total_profit += profit
        normalized_pnl[_code(raw_code)] = raw_value
    application = normalized["application_mode"]
    strategy_key = "unified" if application == "UNIFIED" else ("profit" if total_profit >= 0 else "loss")
    strategy = normalized["strategies"][strategy_key]
    response_mode = strategy["response_mode"]
    usage = principal / Decimal(limit) * Decimal("100")
    intent = ""
    if response_mode in {"조기마감", "즉시청산"}:
        if principal > Decimal(limit):
            intent = INTENT_EARLY_CLOSE if response_mode == "조기마감" else INTENT_IMMEDIATE
    else:
        early = Decimal(normalized["segment_close"]["early_close_percent"])
        immediate = Decimal(normalized["segment_close"]["immediate_liquidation_percent"])
        if usage > immediate:
            intent = INTENT_IMMEDIATE
        elif usage > early:
            intent = INTENT_EARLY_CLOSE
    factor_field = {"손익금액": "cumulative_profit", "손익비율": "cumulative_rate", "투입금액": "open_cost"}[strategy["evaluation_factor"]]
    factors: dict[str, Decimal] = {}
    for code, projection in normalized_pnl.items():
        value = _decimal(projection.get(factor_field))
        if value is None:
            return {"available": False, "reason": "ROUTINE_FACTOR_VALUE_UNAVAILABLE"}
        factors[code] = value
    return {
        "available": True, "application_mode": application,
        "selected_strategy": strategy_key, "configured_response_mode": response_mode,
        "effective_response_intent": intent, "usage_percent": usage,
        "evaluation_factor": strategy["evaluation_factor"], "direction": strategy["direction"],
        "candidate_factor_values": factors, "segment_close": normalized["segment_close"],
        "reason": "",
    }


def select_routine_limit_candidate(*, projection: object, candidates: object) -> dict[str, object]:
    if not isinstance(projection, Mapping) or projection.get("available") is not True:
        return {"selectable": False, "reason": "POLICY_PROJECTION_UNAVAILABLE"}
    try:
        values = list(candidates)
    except TypeError:
        return {"selectable": False, "reason": "CANDIDATE_INPUT_UNAVAILABLE"}
    factor_values = projection.get("candidate_factor_values")
    if not isinstance(factor_values, Mapping):
        return {"selectable": False, "reason": "FACTOR_SNAPSHOT_UNAVAILABLE"}
    seen: set[str] = set(); eligible = []; excluded = {}
    for item in values:
        if not isinstance(item, Mapping):
            continue
        code = _code(item.get("stock_code"))
        if code in seen:
            return {"selectable": False, "reason": "DUPLICATE_STOCK_IDENTITY"}
        seen.add(code)
        reason = existing_close_exclusion_reason(item)
        factor = _decimal(factor_values.get(code))
        if reason or factor is None:
            excluded[code] = reason or "EVALUATION_VALUE_UNAVAILABLE"
            continue
        eligible.append((code, factor, item))
    if not eligible:
        return {"selectable": False, "reason": "NO_ELIGIBLE_CANDIDATE", "excluded": excluded}
    if projection.get("direction") == "높은순":
        eligible.sort(key=lambda item: (-item[1], item[0]))
    else:
        eligible.sort(key=lambda item: (item[1], item[0]))
    code, factor, item = eligible[0]
    return {"selectable": True, "selected_stock_code": code, "selected_stock": dict(item), "selected_value": factor, "reason": ""}


def _buffer_clear(
    buffer_result: object,
    *,
    account_no: str,
    trading_day: str,
    ownership: BufferResponseOwnershipService | None = None,
) -> tuple[bool, str]:
    if not isinstance(buffer_result, Mapping) or buffer_result.get("stable") is not True or buffer_result.get("ingress_committed") is not True:
        return False, "BUFFER_RESPONSE_NOT_SETTLED"
    if buffer_result.get("ownership_claimed") is True or buffer_result.get("ownership_existing") is True:
        return False, "BUFFER_RESPONSE_OWNS_CURRENT_CYCLE"
    if buffer_result.get("event_created") is True and buffer_result.get("policy_projected") is not True:
        return False, "BUFFER_RESPONSE_POLICY_UNCERTAIN"
    active = (ownership or BufferResponseOwnershipService()).active_owned_stock_codes(account_no=account_no, trading_day=trading_day)
    if active.get("ok") is not True:
        return False, "BUFFER_OWNERSHIP_UNAVAILABLE"
    if active.get("stock_codes"):
        return False, "BUFFER_RESPONSE_ACTIVE_OWNERSHIP"
    return True, ""


class RoutineLimitResponseCoordinator:
    def __init__(self, *, project_root: str | Path = PROJECT_ROOT, positions_path: str | Path = POSITIONS_PATH,
                 fills_path: str | Path = FILLS_PATH, order_queue_path: str | Path = ORDER_QUEUE_PATH,
                  broker_holdings_path: str | Path = BROKER_HOLDINGS_PATH,
                  ownership: RoutineLimitResponseOwnershipService | None = None,
                  buffer_ownership: BufferResponseOwnershipService | None = None,
                  close_backend=apply_close_intent, pnl_projector=project_current_stock_pnl_snapshot,
                  completion_projector=None, instance_reader=routine_instance_by_id,
                  participant_reader=auto_trade_current_session_operation_participant_codes,
                  operation_policy_reader=read_operation_policy,
                  recovery_snapshot_reader=production_recovery_registry.snapshot) -> None:
        self.root = Path(project_root); self.positions_path = Path(positions_path); self.fills_path = Path(fills_path)
        self.queue_path = Path(order_queue_path); self.holdings_path = Path(broker_holdings_path)
        self.ownership = ownership or RoutineLimitResponseOwnershipService(self.root / "runtime" / "routine_limit_response_ownership.json")
        self.buffer_ownership = buffer_ownership or BufferResponseOwnershipService(self.root / "runtime" / "buffer_response_ownership.json")
        self.close_backend = close_backend; self.pnl_projector = pnl_projector
        self.instance_reader = instance_reader; self.participant_reader = participant_reader
        self.operation_policy_reader = operation_policy_reader; self.recovery_snapshot_reader = recovery_snapshot_reader
        self.completion_projector = completion_projector or self._project_completion

    def _project_completion(self):
        return evaluate_operation_close_completion(
            operation_state_path=self.root / "runtime" / "operation_state.json",
            stocks_dir=self.root / "stocks",
            order_queue_path=self.queue_path,
            positions_path=self.positions_path,
            broker_holdings_path=self.holdings_path,
            operation_policy_path=self.root / "operation_policy.json",
        )

    def run(self, window: object, *, buffer_result: object, trigger: Mapping[str, object] | None = None, recovery: bool = False) -> dict[str, object]:
        context = self.recovery_snapshot_reader()
        identity = getattr(context, "identity", None)
        account = _text(getattr(identity, "account_no", "")); day = _text(getattr(identity, "trading_day", ""))
        if context is None or context.account_status != ACCOUNT_COMPLETED or not account or not day:
            return _result("RECOVERY_INCOMPLETE")
        clear, reason = _buffer_clear(
            buffer_result,
            account_no=account,
            trading_day=day,
            ownership=self.buffer_ownership,
        )
        if not clear:
            return _result(reason, evaluated=True, settled=False, higher_priority_blocked=True)
        routine_ids = set(self._active_routine_ids(account=account, day=day))
        if recovery:
            routine_ids.update(self._routine_ids(trigger, context))
        else:
            routine_ids.update(self._trigger_routine_ids(trigger))
        routine_ids = sorted(routine_ids)
        if not routine_ids:
            return _result("NO_ROUTINE_TRIGGER", evaluated=True, settled=True)
        results = [self._run_routine(window, context=context, account=account, day=day, routine_id=routine_id, trigger=trigger, recovery=recovery) for routine_id in routine_ids]
        uncertain = next((item for item in results if item.get("settled") is not True), None)
        owns = next((item for item in results if item.get("owns_response") is True), None)
        completed = next((item for item in results if item.get("ownership_completed") is True), None)
        if uncertain: return uncertain
        if owns: return owns
        if completed: return completed
        return _result("ROUTINE_LIMIT_CLEAR", evaluated=True, settled=True)

    def _trigger_routine_ids(self, trigger):
        if not isinstance(trigger, Mapping) or _text(trigger.get("side")).upper() != "BUY" or trigger.get("position_committed") is not True:
            return []
        record = StockRepository(project_root=self.root).find_by_code(_code(trigger.get("code")))
        if record is None: return []
        config = _read(self.root / record.stock_path / "config.json")
        routine_id = _text(config.get("assigned_routine_instance_id"))
        return [routine_id] if routine_id else []

    def _active_routine_ids(self, *, account, day):
        read = self.ownership.read_snapshot()
        snapshot = read.get("snapshot")
        if read.get("ok") is not True or not isinstance(snapshot, Mapping):
            return []
        return sorted(
            {
                _text(event.get("routine_instance_id"))
                for event in snapshot.get("events", {}).values()
                if isinstance(event, Mapping)
                and event.get("account_no") == account
                and event.get("trading_day") == day
                and event.get("status") == STATUS_OWNED
                and _text(event.get("routine_instance_id"))
            }
        )

    def _routine_ids(self, trigger, context):
        routine_ids = {_text(getattr(instance, "instance_id", "")) for code in [_code(getattr(stock, "stock_code", "")) for stock in context.stocks if stock.stock_status == STOCK_RESTORED and not stock.review_required] for instance in [self._instance_for_stock(code)] if instance is not None}
        read = self.ownership.read_snapshot()
        snapshot = read.get("snapshot")
        if isinstance(snapshot, Mapping):
            routine_ids.update(
                _text(event.get("routine_instance_id"))
                for event in snapshot.get("events", {}).values()
                if isinstance(event, Mapping)
                and event.get("account_no") == context.identity.account_no
                and event.get("trading_day") == context.identity.trading_day
                and event.get("status") == STATUS_OWNED
            )
        return sorted(item for item in routine_ids if item)

    def _instance_for_stock(self, code):
        record = StockRepository(project_root=self.root).find_by_code(code)
        if record is None: return None
        routine_id = _text(_read(self.root / record.stock_path / "config.json").get("assigned_routine_instance_id"))
        return self.instance_reader(routine_id) if routine_id else None

    def _run_routine(self, window, *, context, account, day, routine_id, trigger, recovery):
        active = self.ownership.active_event(account_no=account, trading_day=day, routine_instance_id=routine_id)
        if active.get("ok") is not True: return _result(active.get("reason"), evaluated=True, routine_instance_id=routine_id)
        event = active.get("event")
        instance = self.instance_reader(routine_id)
        if not isinstance(event, Mapping) and (
            instance is None
            or not instance.buy_limit_enabled
            or _positive_int(instance.buy_limit_amount) is None
        ):
            return _result("ROUTINE_LIMIT_INACTIVE", evaluated=True, routine_instance_id=routine_id, settled=True)
        if isinstance(event, Mapping):
            completed = self._complete_event_if_ready(dict(event), active["revision"])
            if completed is not None:
                return completed
        snapshot = self._snapshot(window, context=context, account=account, routine_id=routine_id)
        if snapshot.get("ok") is not True:
            if isinstance(event, Mapping):
                action = self._dispatch_event(window, dict(event), {})
                return _result(
                    action.get("reason"),
                    evaluated=True,
                    settled=action.get("settled", False),
                    owns_response=True,
                    routine_instance_id=routine_id,
                    configured_response_mode=event["configured_response_mode"],
                    effective_response_intent=event["response_intent"],
                    ownership_existing=True,
                    selected_stock_code=event["selected_stock_code"],
                    event_id=event["event_id"],
                    early_close_requested=action.get("early_close_requested", False),
                    immediate_preparation_state=action.get("preparation_state", ""),
                    immediate_dispatch_requested=action.get("immediate_dispatch_requested", False),
                )
            return _result(snapshot.get("reason"), evaluated=True, routine_instance_id=routine_id)
        base = snapshot["base"]
        if isinstance(event, Mapping):
            return self._resume_event(window, event=dict(event), revision=active["revision"], snapshot=snapshot, base=base)
        projection = snapshot["projection"]
        if not projection.get("effective_response_intent"):
            return _result("ROUTINE_LIMIT_NOT_TRIGGERED", **base, settled=True)
        trigger_evidence = self._reconstruct_trigger(routine_id, snapshot, projection) if recovery else self._live_trigger(trigger, routine_id)
        if trigger_evidence.get("ok") is not True:
            return _result(trigger_evidence.get("reason"), **base)
        selected = select_routine_limit_candidate(projection=projection, candidates=snapshot["candidates"])
        if selected.get("selectable") is not True:
            return _result(selected.get("reason"), **base)
        claimed = self.ownership.claim(
            account_no=account, trading_day=day, routine_instance_id=routine_id,
            trigger_identity_source=trigger_evidence["source"], trigger_identity=trigger_evidence["identity"],
            trigger_stock_code=trigger_evidence["stock_code"], detected_at=trigger_evidence["detected_at"],
            selected_stock_code=selected["selected_stock_code"], configured_response_mode=projection["configured_response_mode"],
            response_intent=projection["effective_response_intent"], expected_revision=active["revision"],
        )
        if claimed.get("ok") is not True: return _result(claimed.get("reason"), **base)
        event = claimed.get("event")
        if not isinstance(event, Mapping): return _result("OWNERSHIP_READ_BACK_UNAVAILABLE", **base)
        action = self._dispatch_event(window, dict(event), snapshot)
        return _result(action.get("reason"), **base, settled=action.get("settled", False), owns_response=True,
                       candidate_selected=True, selected_stock_code=event["selected_stock_code"], event_id=event["event_id"],
                       ownership_claimed=claimed.get("changed") is True, ownership_existing=claimed.get("changed") is not True,
                       early_close_requested=action.get("early_close_requested", False), immediate_preparation_state=action.get("preparation_state", ""),
                       immediate_dispatch_requested=action.get("immediate_dispatch_requested", False))

    def _snapshot(self, window, *, context, account, routine_id):
        instance = self.instance_reader(routine_id)
        if instance is None or _positive_int(instance.buy_limit_amount) is None:
            return {"ok": False, "reason": "ROUTINE_LIMIT_INACTIVE"}
        try: positions_doc, queue = _read(self.positions_path), _read(self.queue_path)
        except Exception as exc: return {"ok": False, "reason": str(exc)}
        positions = positions_doc.get("positions")
        if not isinstance(positions, list): return {"ok": False, "reason": "POSITIONS_RUNTIME_INVALID"}
        if any(not isinstance(position, Mapping) for position in positions):
            return {"ok": False, "reason": "POSITIONS_RUNTIME_INVALID"}
        orders = queue.get("orders")
        if not isinstance(orders, list) or any(not isinstance(order, Mapping) for order in orders):
            return {"ok": False, "reason": "ORDER_QUEUE_RUNTIME_INVALID"}
        restored_codes = [
            _code(stock.stock_code)
            for stock in context.stocks
            if stock.stock_status == STOCK_RESTORED and not stock.review_required
        ]
        if len(restored_codes) != len(set(restored_codes)):
            return {"ok": False, "reason": "DUPLICATE_STOCK_IDENTITY"}
        restored = set(restored_codes)
        participants = {_code(code) for code in self.participant_reader(window)}
        repository = StockRepository(project_root=self.root); assigned = {}; principal = Decimal("0"); candidates = []
        for record in repository.list_stocks():
            stock_dir = self.root / record.stock_path
            try:
                config, state = _read(stock_dir / "config.json"), _read(stock_dir / "state.json")
            except Exception:
                return {"ok": False, "reason": "ROUTINE_STOCK_EVIDENCE_INVALID"}
            if _text(config.get("assigned_routine_instance_id")) == routine_id:
                if record.code in assigned:
                    return {"ok": False, "reason": "DUPLICATE_STOCK_IDENTITY"}
                assigned[record.code] = (stock_dir, config, state)
        matching_positions = []
        position_codes: set[str] = set()
        for position in positions:
            if _text(position.get("account_no")) != account or _text(position.get("position_status")).upper() != "OPEN": continue
            code = _code(position.get("code")); cost = _decimal(position.get("cost_basis")); qty = _positive_int(position.get("quantity"))
            if code not in assigned: continue
            if code in position_codes:
                return {"ok": False, "reason": "DUPLICATE_STOCK_IDENTITY"}
            position_codes.add(code)
            if cost is None or cost < 0 or qty is None:
                return {"ok": False, "reason": "ROUTINE_POSITION_EVIDENCE_INVALID"}
            principal += cost; matching_positions.append(dict(position))
        candidate_codes = [
            _code(position.get("code"))
            for position in matching_positions
            if _code(position.get("code")) in participants
            and _code(position.get("code")) in restored
        ]
        try:
            pnl = self.pnl_projector(candidate_codes, project_root=self.root)
        except Exception:
            return {"ok": False, "reason": "CONFIRMABLE_PNL_SNAPSHOT_UNAVAILABLE"}
        for position in matching_positions:
            code = _code(position.get("code"))
            if code not in participants or code not in restored: continue
            stock_dir, config, state = assigned[code]
            candidates.append({"stock_code": code, "stock_dir": str(stock_dir), "routine_instance_id": routine_id, "is_auto_trade_target": True,
                               "position": position, "state": state, "config": config,
                               "orders": [dict(order) for order in orders if isinstance(order, Mapping) and _code(order.get("code")) == code and _text(order.get("account_no")) in {"", account}]})
        raw_policy = instance.buy_limit_response_policy
        if raw_policy is None: raw_policy = default_routine_limit_response_policy()
        projection = project_routine_limit_policy(policy=raw_policy, invested_principal=principal, buy_limit_amount=instance.buy_limit_amount, pnl_by_stock=pnl)
        if projection.get("available") is not True: return {"ok": False, "reason": projection.get("reason")}
        base = {"evaluated": True, "routine_instance_id": routine_id, "invested_principal": _json_number(principal), "buy_limit_amount": instance.buy_limit_amount,
                "usage_percent": _json_number(projection["usage_percent"]), "policy_projected": True, "selected_strategy": projection["selected_strategy"],
                "configured_response_mode": projection["configured_response_mode"], "effective_response_intent": projection["effective_response_intent"]}
        return {"ok": True, "base": base, "principal": principal, "projection": projection, "candidates": candidates, "positions": matching_positions}

    def _live_trigger(self, trigger, routine_id):
        if not isinstance(trigger, Mapping) or trigger.get("position_committed") is not True or _text(trigger.get("side")).upper() != "BUY": return {"ok": False, "reason": "BUY_POSITION_COMMIT_NOT_CONFIRMED"}
        source, identity = _text(trigger.get("execution_identity_source")), _text(trigger.get("execution_identity"))
        code = _code(trigger.get("code")); detected = datetime.now().isoformat(timespec="seconds")
        if not source or not identity or not code: return {"ok": False, "reason": "TRIGGER_IDENTITY_UNAVAILABLE"}
        return {"ok": True, "source": source, "identity": identity, "stock_code": code, "detected_at": detected}

    def _reconstruct_trigger(self, routine_id, snapshot, projection):
        try: fills = _read(self.fills_path).get("fills")
        except Exception as exc: return {"ok": False, "reason": str(exc)}
        if not isinstance(fills, list): return {"ok": False, "reason": "FILLS_RUNTIME_INVALID"}
        position_last = {(_text(p.get("last_fill_identity_source")), _text(p.get("last_fill_identity"))): p for p in snapshot["positions"]}
        matches = [fill for fill in fills if isinstance(fill, Mapping) and _text(fill.get("routine_instance_id")) == routine_id and _text(fill.get("side")).upper() == "BUY" and (_text(fill.get("execution_identity_source")), _text(fill.get("execution_identity"))) in position_last]
        matches.sort(key=lambda fill: (_text(fill.get("received_at")), _text(fill.get("fill_id"))), reverse=True)
        if not matches: return {"ok": False, "reason": "RECOVERY_TRIGGER_RECONSTRUCTION_UNAVAILABLE"}
        fill = matches[0]; position = position_last[(_text(fill.get("execution_identity_source")), _text(fill.get("execution_identity")))]
        delta = _positive_int(position.get("last_applied_fill_delta")); price = _decimal(fill.get("filled_price"))
        if delta is None or price is None or price <= 0: return {"ok": False, "reason": "RECOVERY_TRIGGER_DELTA_UNAVAILABLE"}
        before = snapshot["principal"] - Decimal(delta) * price
        mode = projection["configured_response_mode"]
        if mode == "구간마감": boundary = Decimal(snapshot["base"]["buy_limit_amount"]) * Decimal(projection["segment_close"]["early_close_percent"]) / Decimal("100")
        else: boundary = Decimal(snapshot["base"]["buy_limit_amount"])
        if before > boundary or snapshot["principal"] <= boundary: return {"ok": False, "reason": "RECOVERY_TRIGGER_CROSSING_NOT_PROVEN"}
        return {"ok": True, "source": _text(fill.get("execution_identity_source")), "identity": _text(fill.get("execution_identity")), "stock_code": _code(fill.get("code")), "detected_at": _text(fill.get("received_at"))}

    def _resume_event(self, window, *, event, revision, snapshot, base):
        # Completion is checked before snapshot construction in _run_routine so
        # an owned event remains finishable after settings are changed.
        promoted = False
        if event["configured_response_mode"] == "구간마감" and event["response_intent"] == INTENT_EARLY_CLOSE:
            immediate = Decimal(snapshot["projection"]["segment_close"]["immediate_liquidation_percent"])
            if snapshot["projection"]["usage_percent"] > immediate:
                state = self._selected_state(event["selected_stock_code"])
                if not self._same_event_early(state, event["event_id"]): return _result("SAME_EVENT_EARLY_CLOSE_EVIDENCE_MISMATCH", **base, owns_response=True)
                changed = self.ownership.promote_to_immediate(event_id=event["event_id"], expected_revision=revision)
                if changed.get("ok") is not True: return _result(changed.get("reason"), **base, owns_response=True)
                event = dict(changed["event"]); promoted = changed.get("changed") is True
        action = self._dispatch_event(window, event, snapshot)
        return _result(action.get("reason"), **base, settled=action.get("settled", False), owns_response=True, ownership_existing=True,
                       ownership_promoted=promoted, selected_stock_code=event["selected_stock_code"], event_id=event["event_id"],
                       early_close_requested=action.get("early_close_requested", False), immediate_preparation_state=action.get("preparation_state", ""), immediate_dispatch_requested=action.get("immediate_dispatch_requested", False))

    def _complete_event_if_ready(self, event, revision):
        completion = self.completion_projector()
        if not isinstance(completion, Mapping):
            return None
        stock_results = completion.get("stock_results") if isinstance(completion, Mapping) else None
        matches = [item for item in stock_results or () if isinstance(item, Mapping) and _code(item.get("stock_code")) == event["selected_stock_code"]]
        if completion.get("evaluated") is True and completion.get("blocked") is not True and not completion.get("evidence_errors") and len(matches) == 1 and matches[0].get("status") in {"DONE", "CARRYOVER_DONE"} and not matches[0].get("reasons"):
            completed = self.ownership.mark_completed(event_id=event["event_id"], evaluator_status=matches[0]["status"], observed_at=datetime.now().isoformat(timespec="seconds"), expected_revision=revision)
            return _result(
                completed.get("reason"),
                evaluated=True,
                settled=completed.get("ok") is True,
                routine_instance_id=event["routine_instance_id"],
                configured_response_mode=event["configured_response_mode"],
                effective_response_intent=event["response_intent"],
                ownership_completed=completed.get("changed") is True,
                event_id=event["event_id"],
            )
        return None

    def _selected_runtime(self, code):
        record = StockRepository(project_root=self.root).find_by_code(code)
        if record is None: raise ValueError("STOCK_RUNTIME_UNAVAILABLE")
        stock_dir = self.root / record.stock_path
        return stock_dir, _read(stock_dir / "config.json"), _read(stock_dir / "state.json")

    def _selected_state(self, code):
        try: return self._selected_runtime(code)[2]
        except Exception: return {}

    def _same_event_early(self, state, event_id):
        return _text(state.get("operation_command_mode")).upper() == MODE_EARLY_CLOSE and _text(state.get("operation_command_source")) == routine_limit_source(event_id) and _text(state.get("operation_command_id")) == routine_limit_early_command_id(event_id) and is_routine_close_policy(state.get("early_close_method"))

    def _dispatch_event(self, window, event, snapshot):
        if event["response_intent"] == INTENT_EARLY_CLOSE:
            method, extra = self._early_policy(event["configured_response_mode"])
            return self._early(event, method, extra)
        return self._immediate(window, event)

    def _early_policy(self, configured_mode):
        if configured_mode == "구간마감": return POLICY_ROUTINE_CLOSE, {}
        policy = self.operation_policy_reader().get("early_close")
        if not isinstance(policy, Mapping): return "", {}
        method = normalize_direct_close_policy_alias(policy.get("method"))
        return method, {key: value for key, value in policy.items() if key != "method" and _text(value)}

    def _early(self, event, method, extra):
        if not method: return {"settled": False, "reason": "EARLY_CLOSE_POLICY_UNAVAILABLE"}
        try: stock_dir, config, state = self._selected_runtime(event["selected_stock_code"])
        except Exception as exc: return {"settled": False, "reason": str(exc)}
        command_id, source = routine_limit_early_command_id(event["event_id"]), routine_limit_source(event["event_id"])
        existing_matches = (
            _text(state.get("operation_command_id")) == command_id
            and _text(state.get("operation_command_source")) == source
            and (
                is_routine_close_policy(state.get("early_close_method"))
                if method == POLICY_ROUTINE_CLOSE
                else normalize_direct_close_policy_alias(state.get("early_close_method")) == method
            )
        )
        if existing_matches:
            return {"settled": True, "reason": "", "early_close_requested": False}
        result = self.close_backend(intent=CLOSE_INTENT_EARLY_CLOSE, target_scope=SCOPE_STOCK, target_id=str(stock_dir.resolve()), source=source,
            requested_policy=method, has_close_progress_quantity=True, extra_policy=extra, stock_code=event["selected_stock_code"], runtime_state=state,
            runtime_routine_instance_id=event["routine_instance_id"], current_policy="", current_started_at="", current_command_id=_text(state.get("operation_command_id")),
            command_id=command_id, requested_at=event["detected_at"], project_root=self.root, queue_path=self.queue_path, fills_path=self.fills_path)
        saved = self._selected_state(event["selected_stock_code"])
        policy_matches = (
            is_routine_close_policy(saved.get("early_close_method"))
            if method == POLICY_ROUTINE_CLOSE
            else normalize_direct_close_policy_alias(saved.get("early_close_method")) == method
        )
        ok = isinstance(result, Mapping) and result.get("ok") is True and _text(saved.get("operation_command_id")) == command_id and _text(saved.get("operation_command_source")) == source and policy_matches
        return {"settled": ok, "reason": "" if ok else _text(result.get("reason") if isinstance(result, Mapping) else "") or "EARLY_CLOSE_FAILED", "early_close_requested": ok}

    def _cancel_requester(self, window):
        direct = getattr(window, "queue_pending_order_cancellations_for_stock_automatically", None)
        if callable(direct): return direct
        host_reader = getattr(window, "main_monitoring_auto_trade_operation_host", None)
        host = host_reader() if callable(host_reader) else None
        requester = getattr(host, "queue_pending_order_cancellations_for_stock_automatically", None)
        return requester if callable(requester) else None

    def _immediate(self, window, event):
        try: queue = _read(self.queue_path); stock_dir, config, state = self._selected_runtime(event["selected_stock_code"])
        except Exception as exc: return {"settled": False, "reason": str(exc), "preparation_state": "BLOCKED"}
        readiness = project_buy_only_cancel_readiness(queue, account_no=event["account_no"], stock_code=event["selected_stock_code"])
        if readiness.get("available") is not True: return {"settled": False, "reason": readiness.get("reason"), "preparation_state": "BLOCKED"}
        if readiness.get("ready") is not True:
            requester = self._cancel_requester(window)
            if not callable(requester): return {"settled": False, "reason": "BUY_CANCEL_REQUESTER_UNAVAILABLE", "preparation_state": "BLOCKED"}
            started_at = _text(state.get("trade_started_at"))
            if not event["routine_instance_id"] or not started_at:
                return {"settled": False, "reason": "BUY_CANCEL_SCOPE_IDENTITY_UNAVAILABLE", "preparation_state": "BLOCKED"}
            cancel = requester(event["selected_stock_code"], event["routine_instance_id"], trading_day=event["trading_day"], started_at=started_at, side_scope=CANCEL_SIDE_SCOPE_BUY_ONLY, account_no=event["account_no"])
            if not isinstance(cancel, Mapping) or cancel.get("ok") is not True: return {"settled": False, "reason": "BUY_ONLY_CANCEL_REQUEST_BLOCKED", "preparation_state": "BLOCKED"}
            readiness = project_buy_only_cancel_readiness(_read(self.queue_path), account_no=event["account_no"], stock_code=event["selected_stock_code"])
        if readiness.get("ready") is not True: return {"settled": True, "reason": "WAITING_BUY_CANCEL", "preparation_state": "WAITING_BUY_CANCEL"}
        holding = resolve_liquidation_holding_quantity(event["selected_stock_code"], positions_path=self.positions_path, broker_holdings_path=self.holdings_path)
        qty = holding.get("resolved_liquidation_qty") if isinstance(holding, Mapping) and holding.get("ok") is True else None
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0: return {"settled": False, "reason": "BLOCKED_HOLDING_UNCERTAIN", "preparation_state": "BLOCKED"}
        if qty == 0: return {"settled": True, "reason": "ALREADY_FLAT", "preparation_state": "ALREADY_FLAT"}
        if state.get("holding_qty") != qty: return {"settled": False, "reason": "STOCK_STATE_HOLDING_QUANTITY_MISMATCH", "preparation_state": "BLOCKED"}
        command_id, source = routine_limit_immediate_command_id(event["event_id"]), routine_limit_source(event["event_id"])
        if _text(state.get("operation_command_id")) == command_id and _text(state.get("operation_command_source")) == source and normalize_direct_close_policy_alias(state.get("early_close_method")) == POLICY_MARKET:
            return {"settled": True, "reason": "", "preparation_state": "READY_FOR_IMMEDIATE_LIQUIDATION"}
        current_policy = POLICY_ROUTINE_CLOSE if self._same_event_early(state, event["event_id"]) else ""
        result = self.close_backend(intent=CLOSE_INTENT_EARLY_CLOSE, target_scope=SCOPE_STOCK, target_id=str(stock_dir.resolve()), source=source,
            requested_policy=POLICY_MARKET, has_close_progress_quantity=True, extra_policy={}, stock_code=event["selected_stock_code"], runtime_state=state,
            runtime_routine_instance_id=event["routine_instance_id"], current_policy=current_policy, current_started_at=_text(state.get("early_close_requested_at")),
            current_command_id=_text(state.get("operation_command_id")), command_id=command_id, requested_at=event["detected_at"], project_root=self.root,
            queue_path=self.queue_path, fills_path=self.fills_path)
        saved = self._selected_state(event["selected_stock_code"])
        ok = isinstance(result, Mapping) and result.get("ok") is True and _text(saved.get("operation_command_id")) == command_id and _text(saved.get("operation_command_source")) == source and normalize_direct_close_policy_alias(saved.get("early_close_method")) == POLICY_MARKET
        return {"settled": ok, "reason": "" if ok else _text(result.get("reason") if isinstance(result, Mapping) else "") or "IMMEDIATE_MARKET_CLOSE_FAILED", "preparation_state": "READY_FOR_IMMEDIATE_LIQUIDATION", "immediate_dispatch_requested": ok}


def evaluate_main_window_routine_limit_after_chejan(window: object, *, chejan_result: object, buffer_result: object) -> dict[str, object]:
    trigger = chejan_result.get("position_result") if isinstance(chejan_result, Mapping) else None
    return RoutineLimitResponseCoordinator().run(window, buffer_result=buffer_result, trigger=trigger if isinstance(trigger, Mapping) else None)


def resume_main_window_routine_limit_responses(window: object, *, buffer_result: object) -> dict[str, object]:
    return RoutineLimitResponseCoordinator().run(window, buffer_result=buffer_result, recovery=True)


def routine_layer_allows_stock(result: object) -> bool:
    return bool(isinstance(result, Mapping) and result.get("settled") is True and result.get("owns_response") is not True)


__all__ = ["RoutineLimitResponseCoordinator", "evaluate_main_window_routine_limit_after_chejan", "project_routine_limit_policy", "resume_main_window_routine_limit_responses", "routine_layer_allows_stock", "routine_limit_early_command_id", "routine_limit_immediate_command_id", "routine_limit_source", "select_routine_limit_candidate"]
