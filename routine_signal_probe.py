# -*- coding: utf-8 -*-
"""routine_signal_probe.py

STEP 6-B: 루틴 evaluate() 연결 확인용 안전 프로브 + 신호큐 저장본.

역할:
- 중앙 stocks/의 운영 대상별 metadata-declared evaluation entry를 import한다.
- 종목에 연결된 루틴 인스턴스 기준으로 evaluate(context)를 호출한다.
- 결과를 runtime/routine_signal_probe.log에 기록한다.
- BUY/SELL 신호만 runtime/routine_signals.json 큐에 저장한다.

중요 원칙:
- 주문 실행 없음.
- 루틴별 예산 계산 없음.
- 청산 처리 없음.
- 표준 BUY/SELL에 따른 Runtime 예산조정 상태 전이만 허용.
- config.json / orders.json 수정 없음.
- GUI 상태 컬럼 변경 없음.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from candle_timeframe_aggregation import (
    candle_market_datetime,
    completed_timeframe_candles,
    parse_market_datetime,
)
from execution_universe import (
    ExecutionUniverseSnapshot,
    execution_ready_entries,
    project_execution_universe,
)
from event_journal_production import (
    append_production_event,
    observe_owner_failure_transition,
    observe_production_exception,
)
from routine_instance_registry import load_routine_definitions, routine_instance_by_id
from routine_package_contract import EVALUATION_ROLE, load_routine_module
from gui_stock_data import find_library_stock_by_code
from gui_operation_ui_context import actionable_current_price
from order_candidate_engine import read_reference_price
from running_budget_adjustment import (
    project_running_budget_adjustment_config,
    transition_running_budget_adjustment_for_signal,
)

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_PATH = RUNTIME_DIR / "routine_signal_probe.log"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
FILLS_PATH = RUNTIME_DIR / "fills.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
_DEFAULT_OBSERVER_SENTINEL = object()
TRIGGER_PROVENANCE_FIELDS = (
    "trigger_commit_identity",
    "trigger_bar_key",
    "trigger_bar_identity",
    "trigger_canonical_content_hash",
)
_DEFAULT_DECISION_TRACE_OBSERVER: Any = None


def _routine_observer_owner() -> Any:
    return sys.modules[__name__]


def _observe_routine_contract_failure(
    *,
    scope: str,
    reason_code: str,
    routine_name: str,
    stock_code: str = "",
    stock_name: str = "",
    result_type: str = "",
) -> dict[str, Any]:
    target_name = stock_name or stock_code or routine_name or "루틴 평가"
    return observe_owner_failure_transition(
        _routine_observer_owner(),
        scope,
        active=True,
        signature=f"{reason_code}:{result_type}",
        event_type="INTEGRITY_WARNING",
        severity="ERROR",
        result="FAILED",
        source="routine_signal_probe",
        template_args={"target": target_name},
        target_type="STOCK" if stock_code else "ROUTINE",
        target_id=stock_code or routine_name,
        target_name=target_name,
        stock_code=stock_code or None,
        stock_name=stock_name or None,
        routine=routine_name or None,
        reason_code=reason_code,
        component="routine_signal_probe",
        operation="evaluate",
        details={"result_type": result_type} if result_type else {},
    )


try:
    from routine_signal_queue import enqueue_routine_signal
except Exception:  # pragma: no cover
    enqueue_routine_signal = None


def _default_decision_trace_observer() -> Any:
    global _DEFAULT_DECISION_TRACE_OBSERVER
    if _DEFAULT_DECISION_TRACE_OBSERVER is None:
        try:
            from decision_trace_production_observer import ProductionDecisionTraceObserver

            _DEFAULT_DECISION_TRACE_OBSERVER = ProductionDecisionTraceObserver(project_root=PROJECT_ROOT)
        except Exception:
            return None
    return _DEFAULT_DECISION_TRACE_OBSERVER


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _production_account_budget_context(window: Any) -> dict[str, Any]:
    """Collect fail-closed system-budget evidence from the current Recovery session."""
    try:
        from gui_main_budget_panel import collect_main_account_budget_consumption
        from gui_operation_environment import read_system_total_budget_for_recalculation

        selected_account = getattr(window, "selected_account_no", None)
        account_no = str(selected_account() or "").strip() if callable(selected_account) else ""
        total_budget = read_system_total_budget_for_recalculation()
        consumption = collect_main_account_budget_consumption(window)
        return {
            "account_no": account_no or None,
            "system_total_budget": total_budget,
            "account_consumed_amount": (
                consumption.get("consumed_amount")
                if consumption.get("available") is True
                else None
            ),
            "available": bool(
                account_no
                and total_budget is not None
                and consumption.get("available") is True
            ),
            "reason": str(consumption.get("reason") or ""),
        }
    except Exception as exc:
        return {
            "account_no": None,
            "system_total_budget": None,
            "account_consumed_amount": None,
            "available": False,
            "reason": f"account budget evidence unavailable: {exc}",
        }


def _read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_json_dict(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _read_runtime_ledger(path: Path, list_key: str) -> dict[str, Any] | None:
    if not path.exists():
        return {"version": 1, list_key: []}
    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get(list_key), list):
        return None
    return data


def _append_log(line: str) -> None:
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(line.rstrip() + "\n")
    except Exception:
        pass


def _parse_stock_folder_name(stock_dir: Path) -> tuple[str, str]:
    parts = stock_dir.name.split("_", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", stock_dir.name


def _load_candles_from_stock_dir(stock_dir: Path) -> list[dict[str, Any]]:
    for filename in ("candles.json", "bars.json", "ohlcv.json"):
        data = _read_json(stock_dir / filename)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("candles", "bars", "ohlcv"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def _load_instance_rules(routine_instance_id: str) -> dict[str, Any] | None:
    if not routine_instance_id:
        return None
    instance = routine_instance_by_id(routine_instance_id, project_root=PROJECT_ROOT)
    if instance is None or instance.rules_path is None:
        return None
    data = _read_json(instance.rules_path)
    return data if isinstance(data, dict) else None


def _load_routine_module(definition: Any):
    return load_routine_module(definition, EVALUATION_ROLE)


def _definition_for_package_dir(routine_dir: Path):
    target = Path(routine_dir).resolve()
    for definition in load_routine_definitions():
        try:
            if definition.package_dir.resolve() == target:
                return definition
        except OSError:
            continue
    raise RuntimeError("routine definition is unavailable for package directory")


def _is_trade_watch_target(state: dict[str, Any]) -> bool:
    if bool(state.get("review_required", False)):
        return False
    if not bool(state.get("trade_enabled", False)):
        return False

    status = str(state.get("status", "") or "").upper()
    if status in {
        "REVIEW_REQUIRED",
        "REVIEW",
        "EMERGENCY_STOPPED",
        "EMERGENCY_STOP",
        "EMERGENCY",
        "STOPPED",
        "UNREGISTERED",
    }:
        return False

    return True


def _maybe_enqueue_signal(
    result: dict[str, Any],
    *,
    routine_name: str,
    code: str,
    name: str,
    tick_key: str,
    routine_type: str = "",
    routine_instance_id: str = "",
    candles: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not callable(enqueue_routine_signal):
        return None

    try:
        queue_payload = dict(result)
        if routine_type:
            queue_payload.setdefault("routine_type", routine_type)
        if routine_instance_id:
            queue_payload.setdefault("routine_instance_id", routine_instance_id)
        marker = _signal_marker_snapshot(result, candles or [])
        queue_payload.update(marker)
        return enqueue_routine_signal(
            queue_payload,
            routine=routine_name,
            code=code,
            name=name,
            tick_key=tick_key,
            source="routine_signal_probe",
        )
    except Exception as exc:
        return {
            "status": "error",
            "reason": f"신호큐 저장 예외: {exc}",
        }


def _signal_marker_snapshot(
    result: dict[str, Any],
    candles: list[dict[str, Any]],
) -> dict[str, Any]:
    index = result.get("signal_index")
    if not isinstance(index, int) or isinstance(index, bool):
        return {}
    if not candles or not -len(candles) <= index < len(candles):
        return {}
    candle = candles[index]
    bar_time = candle_market_datetime(candle)
    close = candle.get("close") if isinstance(candle, dict) else None
    timeframe = candle.get("timeframe_minutes") if isinstance(candle, dict) else None
    trade_date = str(candle.get("trade_date") or "").strip() if isinstance(candle, dict) else ""
    if bar_time is None or close in (None, "") or timeframe in (None, ""):
        return {}
    marker: dict[str, Any] = {
        "signal_bar_time": bar_time.isoformat(timespec="seconds"),
        "signal_bar_close": close,
        "signal_timeframe_minutes": timeframe,
        "signal_trade_date": trade_date or bar_time.date().isoformat(),
    }
    try:
        from market_evidence_store import market_window_hash

        marker["signal_input_hash"] = market_window_hash(candles)
    except Exception:
        pass
    return marker


def apply_signal_runtime_error_policy(
    result: dict[str, Any],
    runtime_policy: dict[str, Any] | None,
    *,
    stock_dir: Path,
    code: str,
    name: str,
    routine_name: str,
    tick_key: str,
    event_appender: Callable[..., Any] = append_production_event,
    review_marker: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Apply an explicitly committed signal error policy at the probe boundary.

    Absence of the new policy preserves the legacy probe behavior.  This keeps
    the policy additive and prevents an Indicator Follow default from changing
    unrelated routine types.
    """
    if not isinstance(runtime_policy, dict):
        return result
    if "signal" not in result:
        result["signal"] = "ERROR"
        result.setdefault("reason", "routine result omitted BUY/SELL signal field")
    if str(result.get("signal") or "").strip().upper() != "ERROR":
        return result

    policy = str(runtime_policy.get("error_policy") or "STOP_AND_REVIEW").strip().upper()
    if policy not in {"STOP_AND_REVIEW", "CONTINUE_NEXT_CYCLE"}:
        policy = "STOP_AND_REVIEW"
    reason_code = "INDICATOR_FOLLOW_ABNORMAL_SIGNAL_FLOW"
    event_result = event_appender(
        "PROCESSING_ERROR",
        severity="ERROR",
        result="FAILED",
        source="routine_signal_probe.probe_routine_for_stock",
        template_args={"target": name or code},
        target_type="STOCK",
        target_id=code,
        target_name=name or code,
        stock_code=code,
        stock_name=name,
        routine=routine_name,
        reason_code=reason_code,
        component="routine_signal_probe",
        operation="evaluate_indicator_follow",
        details={"error_policy": policy, "reason": result.get("reason")},
    )
    result["error_policy_event"] = event_result
    result["error_policy_action"] = policy
    if policy == "STOP_AND_REVIEW":
        review_payload = {
            "review_reasons": [reason_code],
            "review_location": "INDICATOR_FOLLOW_ERROR_POLICY",
            "routine": routine_name,
            "detail": result.get("reason"),
        }
        if callable(review_marker):
            try:
                result["error_policy_review_created"] = bool(
                    review_marker(stock_dir, code, name, review_payload, source="INDICATOR_FOLLOW_ERROR_POLICY")
                )
            except Exception:
                result["error_policy_review_created"] = False
    else:
        result["cycle_failure_evidence"] = {
            "reason_code": reason_code,
            "action": "SKIP_ABNORMAL_FLOW_CONTINUE_NEXT_CYCLE",
            "tick_key": tick_key,
        }
    return result


def probe_routine_for_stock(
    routine_module: Any,
    routine_name: str,
    stock_dir: Path,
    tick_key: str,
    *,
    decision_trace_observer: Any = _DEFAULT_OBSERVER_SENTINEL,
    trigger_provenance: dict[str, Any] | None = None,
    account_budget_context: dict[str, Any] | None = None,
    actionable_price_reader: Callable[[str, str], Any] | None = None,
    review_marker: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    code, name = _parse_stock_folder_name(stock_dir)
    state = _read_json_dict(stock_dir / "state.json")
    stock_config = _read_json_dict(stock_dir / "config.json")
    routine_instance_id = str(stock_config.get("assigned_routine_instance_id") or "").strip()
    routine_type = str(getattr(routine_module, "ROUTINE_TYPE", "") or "").strip()
    instance_rules: dict[str, Any] | None = None

    queue_result = None
    trace_observer = (
        _default_decision_trace_observer()
        if decision_trace_observer is _DEFAULT_OBSERVER_SENTINEL
        else decision_trace_observer
    )

    if not _is_trade_watch_target(state):
        result = {
            "signal": "SKIP",
            "reason": "감시 대상 아님",
            "routine": routine_name,
            "code": code,
            "name": name,
        }
    else:
        raw_candles = _load_candles_from_stock_dir(stock_dir)
        instance_rules = _load_instance_rules(routine_instance_id)
        try:
            candles = completed_timeframe_candles(
                raw_candles,
                instance_rules,
                now=parse_market_datetime(tick_key),
            )
            candle_projection_error = ""
        except ValueError as exc:
            candles = []
            candle_projection_error = str(exc)
        evaluate = getattr(routine_module, "evaluate", None)
        if candle_projection_error:
            _observe_routine_contract_failure(
                scope=f"routine_candle_projection:{routine_name}:{code}",
                reason_code="ROUTINE_CANDLE_PROJECTION_FAILED",
                routine_name=routine_name,
                stock_code=code,
                stock_name=name,
                result_type="ValueError",
            )
            result = {
                "signal": "ERROR",
                "reason": f"거래기준봉 변환 실패: {candle_projection_error}",
                "routine": routine_name,
                "code": code,
                "name": name,
            }
        elif not callable(evaluate):
            _observe_routine_contract_failure(
                scope=f"routine_evaluate_contract:{routine_name}:{code}",
                reason_code="ROUTINE_EVALUATE_MISSING",
                routine_name=routine_name,
                stock_code=code,
                stock_name=name,
            )
            result = {
                "signal": "ERROR",
                "reason": "evaluate 함수 없음",
                "routine": routine_name,
                "code": code,
                "name": name,
            }
        else:
            effective_stock_config, budget_adjustment_projection = (
                project_running_budget_adjustment_config(stock_config, state)
            )
            context = {
                "routine": routine_name,
                "code": code,
                "name": name,
                "stock_dir": str(stock_dir),
                "state": state,
                "stock_config": effective_stock_config,
                "running_budget_adjustment": budget_adjustment_projection,
                "candles": candles,
                "probe_only": True,
                "tick_key": tick_key,
                "routine_instance_id": routine_instance_id,
                "routine_type": routine_type,
            }
            library_stock = find_library_stock_by_code(code)
            if isinstance(library_stock, dict):
                classification = str(library_stock.get("classification") or "").strip()
                market = str(library_stock.get("market") or "").strip()
                if classification:
                    context["instrument_classification"] = classification
                if market:
                    context["market"] = market
            if isinstance(account_budget_context, dict):
                context["account_budget"] = dict(account_budget_context)
            provenance = {
                field: trigger_provenance.get(field)
                for field in TRIGGER_PROVENANCE_FIELDS
                if isinstance(trigger_provenance, dict)
                and trigger_provenance.get(field) not in (None, "")
            }
            context.update(provenance)
            if isinstance(instance_rules, dict):
                context["rules"] = instance_rules
                context["rules_source"] = "ROUTINE_INSTANCE_RULES"
            trace_collector = None
            begin_trace = getattr(trace_observer, "begin", None)
            if callable(begin_trace):
                try:
                    trace_collector = begin_trace(
                        stock_code=code,
                        routine_instance_id=routine_instance_id,
                        routine_name=routine_name,
                        definition_id=str(stock_config.get("routine_definition_id") or "").strip(),
                        initial_rules=instance_rules,
                    )
                    if trace_collector is not None:
                        context["decision_trace_observer"] = trace_collector
                except Exception:
                    trace_collector = None
            reference_price = read_reference_price(code, name)
            if isinstance(reference_price, (int, float)) and reference_price > 0:
                context["reference_price"] = reference_price
            if callable(actionable_price_reader):
                try:
                    current_price = actionable_price_reader(code, name)
                except Exception:
                    current_price = None
                if isinstance(current_price, (int, float)) and current_price > 0:
                    context["actionable_current_price"] = current_price
            cycle_projector = getattr(routine_module, "project_cycle_context", None)
            if callable(cycle_projector):
                try:
                    context["cycle"] = cycle_projector(
                        code=code,
                        routine_instance_id=routine_instance_id,
                        order_queue=_read_runtime_ledger(ORDER_QUEUE_PATH, "orders"),
                        fills=_read_runtime_ledger(FILLS_PATH, "fills"),
                        positions=_read_runtime_ledger(POSITIONS_PATH, "positions"),
                    )
                except Exception as exc:
                    context["cycle"] = {
                        "status": "unresolved",
                        "active": False,
                        "confirmed_buy_round": None,
                        "cumulative_filled_buy_amount": None,
                        "holding_qty": 0,
                        "avg_price": 0.0,
                        "last_buy_order_identity": None,
                        "partial_sell": False,
                        "cycle_ended": False,
                        "unresolved_reason": f"CYCLE_PROJECTION_ERROR:{type(exc).__name__}",
                    }
            try:
                raw_result = evaluate(context)
                if isinstance(raw_result, dict):
                    signal_kind = str(raw_result.get("signal") or "").strip().upper()
                    if signal_kind not in {"BUY", "SELL", "NONE", "SKIP", "ERROR"}:
                        _observe_routine_contract_failure(
                            scope=f"routine_evaluate_contract:{routine_name}:{code}",
                            reason_code="ROUTINE_EVALUATE_SIGNAL_MALFORMED",
                            routine_name=routine_name,
                            stock_code=code,
                            stock_name=name,
                            result_type=signal_kind or "MISSING",
                        )
                    else:
                        observe_owner_failure_transition(
                            _routine_observer_owner(),
                            f"routine_evaluate_contract:{routine_name}:{code}",
                            active=False,
                        )
                    observe_owner_failure_transition(
                        _routine_observer_owner(),
                        f"routine_candle_projection:{routine_name}:{code}",
                        active=False,
                    )
                    observe_owner_failure_transition(
                        _routine_observer_owner(),
                        f"routine_evaluate_exception:{routine_name}:{code}",
                        active=False,
                    )
                    result = raw_result
                else:
                    _observe_routine_contract_failure(
                        scope=f"routine_evaluate_contract:{routine_name}:{code}",
                        reason_code="ROUTINE_EVALUATE_RESULT_MALFORMED",
                        routine_name=routine_name,
                        stock_code=code,
                        stock_name=name,
                        result_type=type(raw_result).__name__,
                    )
                    result = {
                        "signal": "ERROR",
                        "reason": f"evaluate 반환 형식 오류: {type(raw_result).__name__}",
                    }
                result["code"] = code
                result["name"] = name
                result["routine"] = result.get("routine", routine_name)
                result["candles"] = len(candles)
                for field, value in provenance.items():
                    result.setdefault(field, value)

                queue_result = _maybe_enqueue_signal(
                    result,
                    routine_name=routine_name,
                    code=code,
                    name=name,
                    tick_key=tick_key,
                    routine_type=routine_type,
                    routine_instance_id=routine_instance_id,
                    candles=candles,
                )
                if isinstance(queue_result, dict):
                    result["queue_status"] = queue_result.get("status")
                    result["queue_id"] = queue_result.get("id", "")
                    if queue_result.get("status") == "queued":
                        result["running_budget_adjustment_transition"] = (
                            transition_running_budget_adjustment_for_signal(
                                stock_dir,
                                signal=result.get("signal"),
                                signal_id=queue_result.get("id"),
                            )
                        )

                try:
                    from event_journal_trade_observer import observe_signal_created

                    observe_signal_created(
                        result,
                        queue_result,
                        routine_name=routine_name,
                        stock_code=code,
                        stock_name=name,
                        routine_instance_id=routine_instance_id,
                    )
                except Exception:
                    pass

                # Bind only a newly queued signal. A duplicate result points at an
                # older signal and must never be attached to this decision trace.
                signal_id = ""
                if isinstance(queue_result, dict) and queue_result.get("status") == "queued":
                    signal_id = str(queue_result.get("id") or "").strip()
                append_trace = getattr(trace_observer, "append_decision", None)
                if trace_collector is not None and callable(append_trace):
                    try:
                        append_trace(
                            trace_collector,
                            result=result,
                            candles=candles,
                            context=context,
                            routine_name=routine_name,
                            code=code,
                            name=name,
                            signal_id=signal_id,
                        )
                    except Exception:
                        pass

            except Exception as exc:
                observe_production_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    component="routine_signal_probe",
                    operation="evaluate",
                    source="routine_signal_probe.probe_routine_for_stock",
                    target_type="STOCK",
                    target_id=code,
                    target_name=name or code,
                    reason_code="ROUTINE_EVALUATE_EXCEPTION",
                    owner=_routine_observer_owner(),
                    failure_scope=f"routine_evaluate_exception:{routine_name}:{code}",
                    details={"routine": routine_name},
                )
                result = {
                    "signal": "ERROR",
                    "reason": f"evaluate 예외: {exc}",
                    "routine": routine_name,
                    "code": code,
                    "name": name,
                }
                append_trace = getattr(trace_observer, "append_decision", None)
                if trace_collector is not None and callable(append_trace):
                    try:
                        append_trace(
                            trace_collector,
                            result=result,
                            candles=candles,
                            context=context,
                            routine_name=routine_name,
                            code=code,
                            name=name,
                        )
                    except Exception:
                        pass

    runtime_policy = instance_rules.get("signal_runtime_policy") if isinstance(instance_rules, dict) else None
    if isinstance(runtime_policy, dict):
        result["signal_runtime_policy"] = dict(runtime_policy)
    result = apply_signal_runtime_error_policy(
        result,
        runtime_policy,
        stock_dir=stock_dir,
        code=code,
        name=name,
        routine_name=routine_name,
        tick_key=tick_key,
        review_marker=review_marker,
    )

    queue_text = ""
    if isinstance(queue_result, dict):
        queue_text = f" queue={queue_result.get('status')}"

    _append_log(
        f"[{now_text()}] tick={tick_key} routine={routine_name} "
        f"stock={code} {name} signal={result.get('signal')} reason={result.get('reason')}{queue_text}"
    )
    return result


def probe_selected_routine_once(
    window: Any,
    tick_key: str = "",
    *,
    execution_universe_snapshot: ExecutionUniverseSnapshot | None = None,
) -> dict[str, int]:
    routine_dir_func = getattr(window, "current_selected_routine_dir", None)
    routine_name_func = getattr(window, "current_selected_routine_name", None)

    if not callable(routine_dir_func) or not callable(routine_name_func):
        return {"checked": 0, "logged": 0, "error": 1, "skip": 0, "queued": 0}

    routine_dir = routine_dir_func()
    routine_name = str(routine_name_func() or "").strip()

    if routine_dir is None or not routine_name:
        return {"checked": 0, "logged": 0, "error": 0, "skip": 0, "queued": 0}

    try:
        from gui_auto_trade_runtime import get_stock_dirs_in_routine
        stock_dirs = get_stock_dirs_in_routine(routine_dir)
    except Exception:
        stock_dirs = []

    try:
        routine_module = _load_routine_module(
            _definition_for_package_dir(Path(routine_dir))
        )
    except Exception as exc:
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="routine_signal_probe",
            operation="load_routine_module",
            source="routine_signal_probe.probe_selected_routine_once",
            target_type="ROUTINE",
            target_id=routine_name,
            target_name=routine_name,
            reason_code="ROUTINE_IMPORT_FAILED",
            owner=_routine_observer_owner(),
            failure_scope=f"routine_import:{routine_name}",
        )
        return {"checked": 0, "logged": 0, "error": 1, "skip": 0, "queued": 0}
    observe_owner_failure_transition(
        _routine_observer_owner(),
        f"routine_import:{routine_name}",
        active=False,
    )

    checked = 0
    logged = 0
    error = 0
    skip = 0
    queued = 0

    snapshot = execution_universe_snapshot or project_execution_universe(
        window,
        stock_dirs=stock_dirs,
    )
    ready_stock_dirs = {
        str(entry.stock_dir.resolve())
        for entry in execution_ready_entries(snapshot)
    }

    for stock_dir in stock_dirs:
        if str(Path(stock_dir).resolve()) not in ready_stock_dirs:
            skip += 1
            continue
        checked += 1
        probe_kwargs = {}
        if callable(getattr(window, "selected_account_no", None)):
            probe_kwargs["account_budget_context"] = _production_account_budget_context(window)
        probe_kwargs["actionable_price_reader"] = (
            lambda stock_code, _stock_name="": actionable_current_price(
                window,
                stock_code,
            )
        )
        probe_kwargs["review_marker"] = getattr(window, "mark_review_required", None)
        result = probe_routine_for_stock(
            routine_module,
            routine_name,
            Path(stock_dir),
            tick_key,
            **probe_kwargs,
        )
        signal = str(result.get("signal", "") or "").upper()
        if signal == "SKIP":
            skip += 1
        elif signal == "ERROR":
            error += 1
            logged += 1
        else:
            logged += 1

        if result.get("queue_status") == "queued":
            queued += 1

    return {"checked": checked, "logged": logged, "error": error, "skip": skip, "queued": queued}


def probe_all_enabled_routine_stocks_once(
    _window: Any = None,
    tick_key: str = "",
    *,
    execution_universe_snapshot: ExecutionUniverseSnapshot | None = None,
) -> dict[str, int]:
    """Probe all enabled central stocks using each stock's own routine."""
    definitions = {
        definition.definition_id: definition
        for definition in load_routine_definitions()
        if definition.package_enabled
    }
    module_cache: dict[str, Any] = {}
    checked = 0
    logged = 0
    error = 0
    skip = 0
    queued = 0

    snapshot = execution_universe_snapshot or project_execution_universe(_window)
    for entry in execution_ready_entries(snapshot):
        checked += 1
        result = probe_execution_stock_for_committed_bar(
            _window,
            entry.stock_dir,
            tick_key,
            execution_universe_snapshot=snapshot,
            _definitions=definitions,
            _module_cache=module_cache,
        )
        signal = str(result.get("signal", "") or "").upper()
        if signal == "SKIP":
            skip += 1
        elif signal == "ERROR":
            error += 1
            logged += 1
        else:
            logged += 1
        if result.get("queue_status") == "queued":
            queued += 1

    return {
        "checked": checked,
        "logged": logged,
        "error": error,
        "skip": skip,
        "queued": queued,
    }


def probe_execution_stock_for_committed_bar(
    window: Any,
    stock_dir: Path,
    tick_key: str,
    *,
    trigger_provenance: dict[str, Any] | None = None,
    execution_universe_snapshot: ExecutionUniverseSnapshot | None = None,
    _definitions: dict[str, Any] | None = None,
    _module_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate exactly one currently execution-ready stock."""
    target_dir = Path(stock_dir)
    snapshot = execution_universe_snapshot or project_execution_universe(
        window,
        stock_dirs=[target_dir],
    )
    target_resolved = str(target_dir.resolve())
    entry = next(
        (
            candidate
            for candidate in execution_ready_entries(snapshot)
            if str(candidate.stock_dir.resolve()) == target_resolved
        ),
        None,
    )
    code, name = _parse_stock_folder_name(target_dir)
    if entry is None:
        return {
            "signal": "SKIP",
            "reason": "EXECUTION_NOT_READY",
            "code": code,
            "name": name,
        }

    state = _read_json_dict(target_dir / "state.json")
    if not _is_trade_watch_target(state):
        return {
            "signal": "SKIP",
            "reason": "EXECUTION_NOT_READY",
            "code": code,
            "name": name,
        }

    definitions = _definitions if _definitions is not None else {
        definition.definition_id: definition
        for definition in load_routine_definitions()
        if definition.package_enabled
    }
    module_cache = _module_cache if _module_cache is not None else {}
    config = _read_json_dict(target_dir / "config.json")
    instance_id = str(config.get("assigned_routine_instance_id", "") or "").strip()
    definition_id = str(config.get("routine_definition_id", "") or "").strip()
    routine_name = str(
        config.get("routine_instance_name")
        or config.get("routine")
        or config.get("routine_name")
        or ""
    ).strip()
    definition = definitions.get(definition_id)
    if not instance_id or definition is None:
        _observe_routine_contract_failure(
            scope=f"routine_assignment:{code}",
            reason_code="ROUTINE_ASSIGNMENT_UNRESOLVED",
            routine_name=routine_name or definition_id,
            stock_code=code,
            stock_name=name,
        )
        return {
            "signal": "ERROR",
            "reason": "ROUTINE_ASSIGNMENT_UNRESOLVED",
            "routine": routine_name,
            "code": code,
            "name": name,
        }

    routine_instance = routine_instance_by_id(
        instance_id,
        project_root=PROJECT_ROOT,
    )
    if (
        routine_instance is None
        or str(routine_instance.definition_id or "").strip() != definition_id
    ):
        _observe_routine_contract_failure(
            scope=f"routine_assignment:{code}",
            reason_code="ROUTINE_INSTANCE_UNRESOLVED",
            routine_name=routine_name or definition_id,
            stock_code=code,
            stock_name=name,
        )
        return {
            "signal": "ERROR",
            "reason": "ROUTINE_INSTANCE_UNRESOLVED",
            "routine": routine_name,
            "code": code,
            "name": name,
        }
    if routine_instance.enabled is not True:
        return {
            "signal": "SKIP",
            "reason": "ROUTINE_INSTANCE_DISABLED",
            "routine": routine_name or routine_instance.display_name,
            "code": code,
            "name": name,
        }

    if not routine_name:
        routine_name = definition.display_name
    try:
        routine_module = module_cache.get(definition_id)
        if routine_module is None:
            routine_module = _load_routine_module(definition)
            module_cache[definition_id] = routine_module
    except Exception as exc:
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="routine_signal_probe",
            operation="load_routine_module",
            source="routine_signal_probe.probe_execution_stock_for_committed_bar",
            target_type="ROUTINE",
            target_id=definition_id,
            target_name=routine_name,
            reason_code="ROUTINE_IMPORT_FAILED",
            owner=_routine_observer_owner(),
            failure_scope=f"routine_import:{definition_id}",
            details={"stock_code": code},
        )
        return {
            "signal": "ERROR",
            "reason": "ROUTINE_IMPORT_FAILED",
            "routine": routine_name,
            "code": code,
            "name": name,
        }

    observe_owner_failure_transition(
        _routine_observer_owner(),
        f"routine_import:{definition_id}",
        active=False,
    )
    observe_owner_failure_transition(
        _routine_observer_owner(),
        f"routine_assignment:{code}",
        active=False,
    )
    probe_kwargs = (
        {"trigger_provenance": trigger_provenance}
        if isinstance(trigger_provenance, dict) and trigger_provenance
        else {}
    )
    if callable(getattr(window, "selected_account_no", None)):
        probe_kwargs["account_budget_context"] = _production_account_budget_context(window)
    probe_kwargs["actionable_price_reader"] = (
        lambda stock_code, _stock_name="": actionable_current_price(
            window,
            stock_code,
        )
    )
    probe_kwargs["review_marker"] = getattr(window, "mark_review_required", None)
    return probe_routine_for_stock(
        routine_module,
        routine_name,
        target_dir,
        tick_key,
        **probe_kwargs,
    )
