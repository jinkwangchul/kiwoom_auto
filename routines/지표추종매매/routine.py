# -*- coding: utf-8 -*-
"""지표추종매매 routine.py

STEP 5-E 설정 충돌 보강본.

범위:
- routine_macd_engine.py 사용.
- BUY / SELL 신호만 반환.
- 주문, 예산, 체결, 청산, 검토관리 이동은 처리하지 않는다.

수정 핵심:
- context["config"]를 루틴 설정으로 자동 사용하지 않는다.
- 루틴 설정은 context["routine_config"] 또는 context["rules"]만 사용한다.
- 없으면 DEFAULT_MACD_ROUTINE_CONFIG를 사용한다.
"""

from __future__ import annotations

from typing import Any
import json
from pathlib import Path

from engines.indicator_engine import indicator_history_target_bars_from_rules

try:
    from routine_cycle_projection import project_indicator_follow_cycle  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .routine_cycle_projection import project_indicator_follow_cycle  # type: ignore
    except Exception:  # pragma: no cover
        project_indicator_follow_cycle = None

try:
    from routine_buy_execution import build_indicator_follow_buy_intent, inspect_buy_time_slice_continuation, inspect_buy_execution_support  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .routine_buy_execution import build_indicator_follow_buy_intent, inspect_buy_time_slice_continuation, inspect_buy_execution_support  # type: ignore
    except Exception:  # pragma: no cover
        build_indicator_follow_buy_intent = None
        inspect_buy_time_slice_continuation = None
        inspect_buy_execution_support = None

try:
    from routine_sell_execution import build_indicator_follow_sell_intent  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .routine_sell_execution import build_indicator_follow_sell_intent  # type: ignore
    except Exception:  # pragma: no cover
        build_indicator_follow_sell_intent = None


try:
    from routine_macd_engine import (  # type: ignore
        DEFAULT_INDICATOR_FOLLOW_CONFIG,
        evaluate_indicator_follow_routine,
        signal_to_dict,
    )
    DEFAULT_MACD_ROUTINE_CONFIG = DEFAULT_INDICATOR_FOLLOW_CONFIG
    evaluate_macd_routine = evaluate_indicator_follow_routine
    _ENGINE_SOURCE = "routine_macd_engine"
    _IMPORT_ERROR = None
except Exception as first_exc:  # pragma: no cover
    try:
        from .routine_macd_engine import (  # type: ignore
            DEFAULT_INDICATOR_FOLLOW_CONFIG,
            evaluate_indicator_follow_routine,
            signal_to_dict,
        )
        DEFAULT_MACD_ROUTINE_CONFIG = DEFAULT_INDICATOR_FOLLOW_CONFIG
        evaluate_macd_routine = evaluate_indicator_follow_routine
        _ENGINE_SOURCE = "routine_macd_engine"
        _IMPORT_ERROR = None
    except Exception as second_exc:  # pragma: no cover
        try:
            from routine_macd_engine import (  # type: ignore
                DEFAULT_MACD_ROUTINE_CONFIG,
                evaluate_macd_routine,
                signal_to_dict,
            )
            DEFAULT_INDICATOR_FOLLOW_CONFIG = DEFAULT_MACD_ROUTINE_CONFIG
            evaluate_indicator_follow_routine = evaluate_macd_routine
            _ENGINE_SOURCE = "routine_macd_engine"
            _IMPORT_ERROR = None
        except Exception as third_exc:  # pragma: no cover
            try:
                from .routine_macd_engine import (  # type: ignore
                    DEFAULT_MACD_ROUTINE_CONFIG,
                    evaluate_macd_routine,
                    signal_to_dict,
                )
                DEFAULT_INDICATOR_FOLLOW_CONFIG = DEFAULT_MACD_ROUTINE_CONFIG
                evaluate_indicator_follow_routine = evaluate_macd_routine
                _ENGINE_SOURCE = "routine_macd_engine"
                _IMPORT_ERROR = None
            except Exception as fourth_exc:  # pragma: no cover
                DEFAULT_INDICATOR_FOLLOW_CONFIG = None
                DEFAULT_MACD_ROUTINE_CONFIG = None
                evaluate_indicator_follow_routine = None
                evaluate_macd_routine = None
                signal_to_dict = None
                _ENGINE_SOURCE = "IMPORT_FAILED"
                _IMPORT_ERROR = (first_exc, second_exc, third_exc, fourth_exc)


ROUTINE_NAME = "지표추종매매"
ROUTINE_API_VERSION = "0.2"
EXECUTION_ENABLED = False
ROUTINE_TYPE = "INDICATOR_FOLLOW"


def _gate_result(
    allowed: bool,
    reason: str,
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "reasons": [] if allowed else [reason],
        "routine_identity": dict(routine_identity),
        "rules_identity": rules_identity,
    }


def evaluate_execution_admission(
    *,
    subject: dict[str, Any],
    rules: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    """Apply this routine's candidate-admission rule."""
    principle = rules.get("principle") if isinstance(rules, dict) else None
    allowed = (
        isinstance(principle, dict)
        and principle.get("execution_enabled") is True
    )
    if allowed:
        reason = (inspect_buy_execution_support(subject=subject, rules=rules)
                  if callable(inspect_buy_execution_support) else "BUY_EXECUTION_SUPPORT_UNAVAILABLE")
        if reason:
            return _gate_result(False, reason, routine_identity, rules_identity)
    if allowed and callable(inspect_buy_time_slice_continuation):
        reason = inspect_buy_time_slice_continuation(
            subject=subject,
            rules=rules,
            main_facts=subject.get("main_facts"),
        )
        if reason:
            return _gate_result(False, reason, routine_identity, rules_identity)
    return _gate_result(
        allowed,
        "ROUTINE_EXECUTION_ENABLED" if allowed else "ROUTINE_EXECUTION_DISABLED",
        routine_identity,
        rules_identity,
    )


def evaluate_final_real_order_safety(
    *,
    subject: dict[str, Any],
    rules: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    """Re-evaluate this routine's real-order rule from current effective rules."""
    safety = rules.get("safety") if isinstance(rules, dict) else None
    allowed = (
        isinstance(safety, dict)
        and safety.get("real_order_allowed") is True
    )
    if allowed:
        reason = (inspect_buy_execution_support(subject=subject, rules=rules)
                  if callable(inspect_buy_execution_support) else "BUY_EXECUTION_SUPPORT_UNAVAILABLE")
        if reason:
            return _gate_result(False, reason, routine_identity, rules_identity)
    if allowed and callable(inspect_buy_time_slice_continuation):
        reason = inspect_buy_time_slice_continuation(
            subject=subject,
            rules=rules,
            main_facts=subject.get("main_facts"),
        )
        if reason:
            return _gate_result(False, reason, routine_identity, rules_identity)
    return _gate_result(
        allowed,
        "ROUTINE_REAL_ORDER_ALLOWED" if allowed else "ROUTINE_REAL_ORDER_NOT_ALLOWED",
        routine_identity,
        rules_identity,
    )


def _matching_buy_exit_evidence(
    records: Any,
    *,
    code: str,
    routine_instance_id: str,
    cycle_identity: str,
) -> list[dict[str, Any]]:
    """Return only completion evidence owned by this exact BUY cycle."""
    if not isinstance(records, list) or not str(cycle_identity or "").strip():
        return []
    expected_code = str(code or "").strip()
    expected_routine = str(routine_instance_id or "").strip()
    expected_cycle = str(cycle_identity or "").strip()
    candidates: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or str(record.get("code") or "").strip() != expected_code:
            continue
        evidence = record.get("buy_exit_evidence")
        if not isinstance(evidence, dict) or evidence.get("buy_phase_completed") is not True:
            continue
        record_id = str(record.get("id") or "").strip()
        intents = [item for item in record.get("execution_intents", []) if isinstance(item, dict)]
        direct_intent = record.get("execution_intent")
        if isinstance(direct_intent, dict) and direct_intent not in intents:
            intents.append(direct_intent)
        record_routine_ids = {
            str(record.get("routine_instance_id") or "").strip(),
            *(str(item.get("routine_instance_id") or "").strip() for item in intents),
        }
        record_routine_ids.discard("")
        record_cycle_ids = {
            str(record.get("cycle_identity") or "").strip(),
            *(str(item.get("cycle_identity") or "").strip() for item in intents),
        }
        record_cycle_ids.discard("")
        record_process_ids = {
            str(item.get("execution_process_id") or "").strip()
            for item in intents
            if str(item.get("execution_process_id") or "").strip()
        }
        if (
            record_routine_ids != {expected_routine}
            or record_cycle_ids != {expected_cycle}
            or str(evidence.get("routine_instance_id") or "").strip() != expected_routine
            or str(evidence.get("cycle_identity") or "").strip() != expected_cycle
            or str(evidence.get("source_signal_id") or "").strip() != record_id
            or str(evidence.get("execution_process_id") or "").strip() not in record_process_ids
        ):
            continue
        candidates.append(evidence)
    return candidates


def project_cycle_context(
    *,
    code: str,
    routine_instance_id: str,
    order_queue: Any,
    fills: Any,
    positions: Any,
    signals: Any,
) -> dict[str, Any]:
    if not callable(project_indicator_follow_cycle):
        return {
            "status": "unresolved",
            "active": False,
            "confirmed_buy_round": None,
            "cumulative_filled_buy_amount": None,
            "holding_qty": 0,
            "avg_price": 0.0,
            "last_buy_order_identity": None,
            "partial_sell": False,
            "cycle_ended": False,
            "unresolved_reason": "CYCLE_PROJECTION_IMPORT_FAILED",
        }
    projection = project_indicator_follow_cycle(
        code=code,
        routine_instance_id=routine_instance_id,
        order_queue=order_queue,
        fills=fills,
        positions=positions,
    )
    # BUY phase completion is canonical signal evidence written by the
    # existing consumer.  Project it read-only into the cycle so a future BUY
    # signal is blocked without introducing another runtime writer/state file.
    try:
        root = signals if isinstance(signals, dict) else {}
        records = root.get("signals")
        current_cycle_identity = str(projection.get("cycle_identity") or "").strip()
        if isinstance(records, list) and current_cycle_identity:
            candidates = _matching_buy_exit_evidence(
                records,
                code=code,
                routine_instance_id=routine_instance_id,
                cycle_identity=current_cycle_identity,
            )
            if candidates:
                evidence = candidates[-1]
                projection["buy_phase_completed"] = True
                projection["buy_exit_evidence"] = evidence
    except Exception:
        pass
    return projection


def get_routine_info() -> dict[str, Any]:
    return {
        "name": ROUTINE_NAME,
        "api_version": ROUTINE_API_VERSION,
        "execution_enabled": EXECUTION_ENABLED,
        "signal_only": True,
        "engine": _ENGINE_SOURCE,
    }


def market_bar_projection_request(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Declare the active Indicator Follow market-bar requirement.

    Main owns acquisition and retention.  This routine only describes the
    forming-bar projection and the number of timeframe bars its current,
    applied evaluator paths can consume.
    """
    rules = rules if isinstance(rules, dict) else {}
    indicators = (
        rules.get("indicators")
        if isinstance(rules.get("indicators"), dict)
        else {}
    )

    def positive_int(value: Any, default: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return result if result > 0 else default

    def nonnegative_int(value: Any, default: int = 0) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return max(result, 0)

    macd = indicators.get("macd") if isinstance(indicators.get("macd"), dict) else {}
    macd_readiness = positive_int(macd.get("slow"), 26) + positive_int(
        macd.get("signal"), 9
    )
    rsi_cfg = indicators.get("rsi") if isinstance(indicators.get("rsi"), dict) else {}
    bollinger_cfg = (
        indicators.get("bollinger")
        if isinstance(indicators.get("bollinger"), dict)
        else {}
    )
    price_box_cfg = (
        indicators.get("price_box")
        if isinstance(indicators.get("price_box"), dict)
        else {}
    )

    def target_requirement(
        target: Any,
        condition: dict[str, Any],
        *,
        compare: bool = False,
        rsi_condition_period: bool = False,
    ) -> int:
        name = str(target or "").strip().upper()
        period_key = "compare_period" if compare else "period"
        raw_period = condition.get(period_key)
        if compare and raw_period in (None, ""):
            raw_period = condition.get("period")
        if name == "MA" or (name.startswith("MA") and name[2:].isdigit()):
            suffix = name[2:] if name.startswith("MA") else ""
            return positive_int(raw_period if raw_period not in (None, "") else suffix, 1)
        if name in {"MACD", "SIGNAL", "OSC"}:
            return macd_readiness
        if name == "RSI":
            period = (
                raw_period
                if rsi_condition_period and raw_period not in (None, "")
                else rsi_cfg.get("period")
            )
            return positive_int(period, 14) + 1
        if name == "BOLLINGER" or name.startswith("BOLLINGER_"):
            return positive_int(bollinger_cfg.get("period"), 20)
        if name == "PRICE_BOX" or name.startswith("PRICE_BOX_"):
            return positive_int(price_box_cfg.get("period"), 24)
        return 1

    lookback_by_operator = {
        "TURN_UP": 2,
        "TURN_DOWN": 2,
        "TREND_UP": 1,
        "TREND_DOWN": 1,
        "CROSS_UP": 1,
        "CROSS_DOWN": 1,
        "ZERO_CROSS_UP": 1,
        "ZERO_CROSS_DOWN": 1,
    }
    candidates: list[int] = [3]

    def add_condition(
        condition: Any,
        delay: int,
        *,
        rsi_condition_period: bool = False,
    ) -> None:
        if not isinstance(condition, dict) or condition.get("enabled", True) is False:
            return
        base = target_requirement(
            condition.get("target", "OSC"),
            condition,
            rsi_condition_period=rsi_condition_period,
        )
        compare_target = condition.get("compare_target")
        if str(compare_target or "").strip():
            base = max(
                base,
                target_requirement(compare_target, condition, compare=True),
            )
        operator = str(condition.get("operator") or "").strip().upper()
        candidates.append(
            base
            + lookback_by_operator.get(operator, 0)
            + nonnegative_int(condition.get("bar_offset"))
            + nonnegative_int(delay)
        )

    def add_groups(
        groups: Any,
        delay: int,
        *,
        rsi_condition_period: bool = False,
    ) -> None:
        for group in groups if isinstance(groups, list) else ():
            if not isinstance(group, dict) or group.get("enabled", True) is False:
                continue
            for condition in (
                group.get("conditions")
                if isinstance(group.get("conditions"), list)
                else ()
            ):
                add_condition(
                    condition,
                    delay,
                    rsi_condition_period=rsi_condition_period,
                )

    buy = rules.get("buy") if isinstance(rules.get("buy"), dict) else {}
    filters = buy.get("filters") if isinstance(buy.get("filters"), dict) else {}
    ocr = filters.get("ocr") if isinstance(filters.get("ocr"), dict) else {}
    buy_delay = nonnegative_int(
        ocr.get("order_delay_bars")
        if "order_delay_bars" in ocr
        else buy.get("delay_bar", 1)
    )
    composite = (
        filters.get("composite")
        if isinstance(filters.get("composite"), dict)
        else {}
    )
    expression = composite.get("expression") if isinstance(composite.get("expression"), dict) else {}
    identifier_map = expression.get("identifier_map") if isinstance(expression.get("identifier_map"), dict) else {}
    identifiers = expression.get("identifiers") if isinstance(expression.get("identifiers"), list) else []
    expression_filters = {
        str(identifier_map.get(str(identifier or "").strip().upper()) or "").strip().lower()
        for identifier in identifiers
    }
    expression_mode = False
    expression_filter_names = {"ocr", "bollinger", "moving_average", "rsi"}
    if (
        composite.get("enabled", False)
        and expression
        and expression_filters
        and expression_filters <= expression_filter_names
    ):
        try:
            from engines.condition_engine import evaluate_condition_expression

            expression_mode = bool(
                evaluate_condition_expression(
                    expression.get("ast"),
                    {
                        str(identifier or "").strip().upper(): True
                        for identifier in identifiers
                    },
                ).get("ok")
            )
        except Exception:
            expression_mode = False

    buy_groups = buy.get("groups") if isinstance(buy.get("groups"), list) else []
    legacy_filters_reachable = any(
        isinstance(group, dict)
        and group.get("enabled", True) is not False
        and isinstance(group.get("conditions"), list)
        and bool(group.get("conditions"))
        for group in buy_groups
    )
    if rules.get("enabled", True) is not False and buy.get("enabled", True) is not False:
        if not expression_mode:
            add_groups(buy_groups, buy_delay)
        filter_names = (
            expression_filters
            | {
                name
                for name in expression_filter_names
                if isinstance(filters.get(name), dict)
                and bool(filters.get(name))
                and bool(filters[name].get("enabled", True))
            }
            if expression_mode
            else (
                {"rsi", "moving_average", "price_compare", "bollinger", "ocr"}
                if legacy_filters_reachable
                else set()
            )
        )
        for name in filter_names:
            filter_cfg = filters.get(name)
            if not isinstance(filter_cfg, dict) or not bool(
                filter_cfg.get("enabled", True)
            ):
                continue
            conditions = filter_cfg.get("conditions")
            configured_conditions = (
                [item for item in conditions if isinstance(item, dict)]
                if isinstance(conditions, list)
                else []
            )
            if name in {"rsi", "moving_average"}:
                condition = dict(filter_cfg)
                if configured_conditions:
                    condition.update(configured_conditions[0])
                if name == "rsi":
                    condition.setdefault("target", "RSI")
                else:
                    condition.setdefault("target", "CLOSE")
                    condition.setdefault("compare_target", "MA")
                    condition.setdefault("period", 60)
                active_conditions = [condition]
            elif name == "bollinger":
                if configured_conditions:
                    condition = dict(configured_conditions[0])
                    condition.setdefault("target", "CLOSE")
                    active_conditions = [condition]
                else:
                    active_conditions = []
            else:
                active_conditions = configured_conditions or [filter_cfg]
            for condition in active_conditions:
                if name == "ocr" and "target" not in condition:
                    condition = {**condition, "target": "OSC"}
                add_condition(
                    condition,
                    buy_delay,
                    rsi_condition_period=name == "rsi",
                )

    sell = rules.get("sell") if isinstance(rules.get("sell"), dict) else {}
    signals = sell.get("signals") if isinstance(sell.get("signals"), dict) else {}
    macd_sell = signals.get("macd_sell") if isinstance(signals.get("macd_sell"), dict) else sell
    sell_delay = nonnegative_int(macd_sell.get("delay_bar", sell.get("delay_bar", 1)))
    if rules.get("enabled", True) is not False and sell.get("enabled", True) is not False:
        if signals:
            for signal_name, signal in signals.items():
                if not isinstance(signal, dict) or signal.get("enabled", True) is False:
                    continue
                signal_delay = nonnegative_int(
                    signal.get("order_delay_bars", sell_delay)
                )
                if signal_name == "profit_rate_sell":
                    candidates.append(1 + sell_delay)
                else:
                    add_groups(
                        signal.get("groups"),
                        signal_delay,
                        rsi_condition_period=True,
                    )
        else:
            add_groups(
                sell.get("groups"),
                sell_delay,
                rsi_condition_period=True,
            )

    has_ocr_delay_contract = "order_delay_bars" in ocr or any(
        isinstance(signal, dict) and "order_delay_bars" in signal
        for signal in signals.values()
    )
    warmup_bars = max(candidates)
    return {
        "projection": "FORMING_BASE_BAR",
        "ocr_delay_semantics": (
            "COMPLETED_TRANSITION_CONFIRMATION"
            if has_ocr_delay_contract
            else "ROUTINE_OWNED"
        ),
        "ocr_zero_bar_mode": (
            "COMPLETED_TRANSITION_CONFIRMATION"
            if has_ocr_delay_contract
            else "NOT_CONFIGURED"
        ),
        "warmup_bars": warmup_bars,
        "history_target_bars": max(
            indicator_history_target_bars_from_rules(rules),
            warmup_bars,
        ),
    }


def _extract_candles(context: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("candles", "bars", "ohlcv"):
        value = context.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _load_rules_json() -> dict[str, Any] | None:
    """루틴 폴더의 rules.json을 읽는다.

    원칙:
    - rules.json은 루틴 전략 설정 파일이다.
    - 종목 config.json과 혼용하지 않는다.
    - 읽기 실패 시 기본 설정으로 후퇴한다.
    """
    rules_path = Path(__file__).resolve().parent / "rules.json"
    try:
        if not rules_path.exists():
            return None
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_config(context: dict[str, Any]) -> dict[str, Any] | None:
    """루틴 설정만 추출한다.

    허용:
    - routine_config
    - rules

    금지:
    - config
      종목 config.json과 이름이 충돌하므로 루틴 설정으로 사용하지 않는다.
    """
    for key in ("routine_config", "rules"):
        value = context.get(key)
        if isinstance(value, dict):
            return value

    rules = _load_rules_json()
    if isinstance(rules, dict):
        return rules

    return DEFAULT_INDICATOR_FOLLOW_CONFIG if isinstance(DEFAULT_INDICATOR_FOLLOW_CONFIG, dict) else None


def evaluate_signal_selection(candles, config, context, *, evaluator=None, converter=None):
    """Pure dual-side selection and activation projection; no execution or persistence."""
    evaluator = evaluator or evaluate_indicator_follow_routine
    converter = converter or signal_to_dict
    sell_context = dict(context)
    sell_context["_indicator_follow_evaluate_side"] = "SELL"
    buy_context = dict(context)
    buy_context["_indicator_follow_evaluate_side"] = "BUY"
    sell_result = converter(
        evaluator(candles, config, sell_context)
    )
    buy_result = converter(
        evaluator(candles, config, buy_context)
    )
    sell_true = str(sell_result.get("signal") or "").strip().upper() == "SELL"
    buy_true = str(buy_result.get("signal") or "").strip().upper() == "BUY"
    cycle_for_conflict = context.get("cycle") if isinstance(context.get("cycle"), dict) else {}
    holding_qty = cycle_for_conflict.get("holding_qty", cycle_for_conflict.get("confirmed_holding_quantity", 0))
    try:
        has_holding = int(holding_qty or 0) > 0
    except (TypeError, ValueError):
        has_holding = False
    if sell_true and buy_true:
        result = sell_result if has_holding else buy_result
        result["signal_conflict_evidence"] = {
            "conflict": "BUY_AND_SELL_TRUE",
            "holding_quantity": holding_qty,
            "fallback": "SELL_WHEN_HOLDING_ELSE_BUY",
            "selected_side": result.get("signal"),
            "buy_signal_index": buy_result.get("signal_index"),
            "sell_signal_index": sell_result.get("signal_index"),
        }
    elif sell_true:
        result = sell_result
    else:
        result = buy_result
    if (
        str(result.get("signal") or "").strip().upper() in {"BUY", "SELL"}
        and candles
    ):
        source_index = result.get("signal_index")
        activation_index = len(candles) - 1
        if (
            isinstance(source_index, int)
            and not isinstance(source_index, bool)
            and 0 <= source_index <= activation_index
        ):
            result["signal_source_index"] = source_index
            result["signal_activation_index"] = activation_index
            result["signal_index"] = activation_index
            activation = (
                candles[activation_index].get("bar_time")
                if isinstance(candles[activation_index], dict)
                else None
            )
            if activation:
                result["signal_activation_bar_time"] = activation
            result["signal_delay_anchor"] = "FOLLOWING_COMPLETED_BASE_BAR_ENTRY"
    return result


def evaluate(context: dict[str, Any] | None = None) -> dict[str, Any]:
    if _IMPORT_ERROR is not None or evaluate_indicator_follow_routine is None or signal_to_dict is None:
        return {
            "signal": None,
            "reason": f"루틴 엔진 import 실패: {_IMPORT_ERROR}",
            "matched_groups": [],
            "details": [],
            "signal_index": -1,
            "delay_bar": 0,
            "routine": ROUTINE_NAME,
            "execution_enabled": EXECUTION_ENABLED,
            "engine": _ENGINE_SOURCE,
        }

    if context is None:
        context = {}

    if not isinstance(context, dict):
        return {
            "signal": None,
            "reason": "context 형식 오류",
            "matched_groups": [],
            "details": [],
            "signal_index": -1,
            "delay_bar": 0,
            "routine": ROUTINE_NAME,
            "execution_enabled": EXECUTION_ENABLED,
            "engine": _ENGINE_SOURCE,
        }

    candles = _extract_candles(context)
    config = _extract_config(context)
    observer = context.get("decision_trace_observer")
    set_effective_rules = getattr(observer, "set_effective_rules", None)
    if callable(set_effective_rules):
        try:
            set_effective_rules(config)
        except Exception:
            pass

    result = evaluate_signal_selection(candles, config, context)
    signal_runtime_policy = config.get("signal_runtime_policy") if isinstance(config, dict) else None
    if isinstance(signal_runtime_policy, dict):
        result["signal_runtime_policy"] = dict(signal_runtime_policy)
    cycle = context.get("cycle")
    signal_side = str(result.get("signal") or "").strip().upper()
    if isinstance(cycle, dict):
        result["cycle"] = dict(cycle)
        if signal_side == "BUY":
            if str(cycle.get("status") or "").strip().lower() == "unresolved":
                result["signal"] = None
                result["reason"] = "매매사이클 체결 상태를 확인할 수 없어 BUY를 차단합니다."
                result["buy_execution_blocked"] = True
                result["buy_execution_blocked_reason"] = cycle.get("unresolved_reason")
            elif cycle.get("buy_phase_completed") is True or cycle.get("buy_exit_evidence"):
                result["signal"] = None
                result["buy_execution_blocked"] = True
                result["buy_execution_blocked_reason"] = "BUY_PHASE_COMPLETED"
                result["buy_execution_policy_status"] = "BLOCKED"
            else:
                confirmed_round = cycle.get("confirmed_buy_round")
                cumulative_amount = cycle.get("cumulative_filled_buy_amount")
                result["buy_execution_runtime_state"] = {
                    "confirmed_current_buy_round": confirmed_round,
                    "confirmed_cumulative_buy_budget": cumulative_amount,
                }
                result["next_buy_round"] = (
                    confirmed_round + 1 if isinstance(confirmed_round, int) else None
                )
                result["buy_phase"] = "BASE" if confirmed_round == 0 else "REPEAT"
                if not callable(build_indicator_follow_buy_intent):
                    result["signal"] = None
                    result["buy_execution_blocked"] = True
                    result["buy_execution_blocked_reason"] = "BUY_EXECUTION_BRIDGE_IMPORT_FAILED"
                else:
                    execution = build_indicator_follow_buy_intent(
                        buy_signal_result=result,
                        context=context,
                    )
                    if execution.get("status") == "READY":
                        result["execution_intent"] = execution.get("execution_intent")
                        execution_intents = execution.get("execution_intents")
                        if isinstance(execution_intents, list) and execution_intents:
                            result["execution_intents"] = execution_intents
                        result["buy_execution_policy_status"] = "READY"
                    elif execution.get("status") in {"NO_BUY", "WAIT"}:
                        result["signal"] = None
                        result["buy_execution_policy_status"] = execution.get("status")
                        result["buy_execution_no_order_reason"] = execution.get("reason")
                    else:
                        result["signal"] = None
                        result["buy_execution_blocked"] = True
                        result["buy_execution_blocked_reason"] = execution.get("reason")
                        result["buy_execution_policy_status"] = "BLOCKED"
        elif signal_side == "SELL":
            if not callable(build_indicator_follow_sell_intent):
                result["signal"] = None
                result["sell_execution_blocked"] = True
                result["sell_execution_blocked_reason"] = "SELL_EXECUTION_BRIDGE_IMPORT_FAILED"
                result["sell_execution_policy_status"] = "BLOCKED"
            else:
                execution = build_indicator_follow_sell_intent(
                    sell_signal_result=result,
                    context=context,
                )
                if execution.get("status") == "READY":
                    result["execution_intent"] = execution.get("execution_intent")
                    execution_intents = execution.get("execution_intents")
                    if isinstance(execution_intents, list) and execution_intents:
                        result["execution_intents"] = execution_intents
                    result["sell_execution_policy_status"] = "READY"
                else:
                    result["signal"] = None
                    result["sell_execution_blocked"] = True
                    result["sell_execution_blocked_reason"] = execution.get("reason")
                    result["sell_execution_policy_status"] = "BLOCKED"
    elif signal_side == "SELL":
        result["signal"] = None
        result["sell_execution_blocked"] = True
        result["sell_execution_blocked_reason"] = "CYCLE_PROJECTION_UNRESOLVED"
        result["sell_execution_policy_status"] = "BLOCKED"
    result["routine"] = ROUTINE_NAME
    result["execution_enabled"] = EXECUTION_ENABLED
    result["engine"] = _ENGINE_SOURCE
    return result
