# -*- coding: utf-8 -*-
"""routine_signal_probe.py

STEP 6-B: 루틴 evaluate() 연결 확인용 안전 프로브 + 신호큐 저장본.

역할:
- 중앙 stocks/의 운영 대상별 routine.py를 import한다.
- 종목에 연결된 루틴 인스턴스 기준으로 evaluate(context)를 호출한다.
- 결과를 runtime/routine_signal_probe.log에 기록한다.
- BUY/SELL 신호만 runtime/routine_signals.json 큐에 저장한다.

중요 원칙:
- 주문 실행 없음.
- 예산 처리 없음.
- 청산 처리 없음.
- state.json / config.json / orders.json 수정 없음.
- GUI 상태 컬럼 변경 없음.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from candle_timeframe_aggregation import (
    candle_market_datetime,
    completed_timeframe_candles,
    parse_market_datetime,
)
from gui_auto_trade_runtime import all_registered_stock_dirs
from routine_instance_registry import load_routine_definitions, routine_instance_by_id
from order_candidate_engine import read_latest_price

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_PATH = RUNTIME_DIR / "routine_signal_probe.log"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
FILLS_PATH = RUNTIME_DIR / "fills.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
_DEFAULT_OBSERVER_SENTINEL = object()
_DEFAULT_DECISION_TRACE_OBSERVER: Any = None


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


def _load_routine_module(routine_dir: Path):
    routine_file = routine_dir / "routine.py"
    if not routine_file.exists():
        raise FileNotFoundError(f"routine.py 없음: {routine_file}")

    module_name = "runtime_probe_" + routine_dir.name.replace("-", "_").replace(" ", "_")
    spec = importlib.util.spec_from_file_location(module_name, routine_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"routine.py 로드 실패: {routine_file}")

    module = importlib.util.module_from_spec(spec)
    routine_path_text = str(routine_dir.resolve())
    added_to_path = False
    if routine_path_text not in sys.path:
        sys.path.insert(0, routine_path_text)
        added_to_path = True
    try:
        spec.loader.exec_module(module)
    finally:
        if added_to_path:
            try:
                sys.path.remove(routine_path_text)
            except ValueError:
                pass
    return module


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


def probe_routine_for_stock(
    routine_module: Any,
    routine_name: str,
    stock_dir: Path,
    tick_key: str,
    *,
    decision_trace_observer: Any = _DEFAULT_OBSERVER_SENTINEL,
) -> dict[str, Any]:
    code, name = _parse_stock_folder_name(stock_dir)
    state = _read_json_dict(stock_dir / "state.json")
    stock_config = _read_json_dict(stock_dir / "config.json")
    routine_instance_id = str(stock_config.get("assigned_routine_instance_id") or "").strip()
    routine_type = str(getattr(routine_module, "ROUTINE_TYPE", "") or "").strip()

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
            result = {
                "signal": "ERROR",
                "reason": f"거래기준봉 변환 실패: {candle_projection_error}",
                "routine": routine_name,
                "code": code,
                "name": name,
            }
        elif not callable(evaluate):
            result = {
                "signal": "ERROR",
                "reason": "evaluate 함수 없음",
                "routine": routine_name,
                "code": code,
                "name": name,
            }
        else:
            context = {
                "routine": routine_name,
                "code": code,
                "name": name,
                "stock_dir": str(stock_dir),
                "state": state,
                "stock_config": stock_config,
                "candles": candles,
                "probe_only": True,
                "tick_key": tick_key,
                "routine_instance_id": routine_instance_id,
                "routine_type": routine_type,
            }
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
                        initial_rules=instance_rules,
                    )
                    if trace_collector is not None:
                        context["decision_trace_observer"] = trace_collector
                except Exception:
                    trace_collector = None
            current_price = read_latest_price(code, name)
            if isinstance(current_price, (int, float)) and current_price > 0:
                context["current_price"] = current_price
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
                result = raw_result if isinstance(raw_result, dict) else {
                    "signal": "ERROR",
                    "reason": f"evaluate 반환 형식 오류: {type(raw_result).__name__}",
                }
                result["code"] = code
                result["name"] = name
                result["routine"] = result.get("routine", routine_name)
                result["candles"] = len(candles)

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

    queue_text = ""
    if isinstance(queue_result, dict):
        queue_text = f" queue={queue_result.get('status')}"

    _append_log(
        f"[{now_text()}] tick={tick_key} routine={routine_name} "
        f"stock={code} {name} signal={result.get('signal')} reason={result.get('reason')}{queue_text}"
    )
    return result


def probe_selected_routine_once(window: Any, tick_key: str = "") -> dict[str, int]:
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
        routine_module = _load_routine_module(Path(routine_dir))
    except Exception as exc:
        _append_log(f"[{now_text()}] tick={tick_key} routine={routine_name} ERROR routine load: {exc}")
        return {"checked": 0, "logged": 0, "error": 1, "skip": 0, "queued": 0}

    checked = 0
    logged = 0
    error = 0
    skip = 0
    queued = 0

    for stock_dir in stock_dirs:
        checked += 1
        result = probe_routine_for_stock(routine_module, routine_name, Path(stock_dir), tick_key)
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

    for stock_dir in all_registered_stock_dirs():
        state = _read_json_dict(stock_dir / "state.json")
        if not _is_trade_watch_target(state):
            continue

        checked += 1
        config = _read_json_dict(stock_dir / "config.json")
        instance_id = str(
            config.get("assigned_routine_instance_id", "") or ""
        ).strip()
        definition_id = str(
            config.get("routine_definition_id", "") or ""
        ).strip()
        routine_name = str(
            config.get("routine_instance_name")
            or config.get("routine")
            or config.get("routine_name")
            or ""
        ).strip()
        definition = definitions.get(definition_id)

        if not instance_id or definition is None:
            code, name = _parse_stock_folder_name(stock_dir)
            _append_log(
                f"[{now_text()}] tick={tick_key} routine={routine_name or definition_id} "
                f"stock={code} {name} ERROR routine assignment unresolved"
            )
            error += 1
            logged += 1
            continue

        if not routine_name:
            routine_name = definition.display_name

        try:
            routine_module = module_cache.get(definition_id)
            if routine_module is None:
                routine_module = _load_routine_module(definition.package_dir)
                module_cache[definition_id] = routine_module
        except Exception as exc:
            code, name = _parse_stock_folder_name(stock_dir)
            _append_log(
                f"[{now_text()}] tick={tick_key} routine={routine_name} "
                f"stock={code} {name} ERROR routine load: {exc}"
            )
            error += 1
            logged += 1
            continue

        result = probe_routine_for_stock(
            routine_module,
            routine_name,
            stock_dir,
            tick_key,
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
