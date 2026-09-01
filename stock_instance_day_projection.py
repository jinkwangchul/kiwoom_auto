# -*- coding: utf-8 -*-
"""Read-only daily source projection for a stock's active routine instance."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
from typing import Any

from candle_manager import load_candles
from broker_holding_recorder import _reconciliation_status as broker_holding_reconciliation_status
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    completed_timeframe_candles,
    filter_candles_by_trade_date,
    read_canonical_bar_minutes,
)
from execution_queue_writer import read_execution_queue_records
from execution_chart_read_model import project_execution_chart_read_model
from manual_ats_runtime import manual_ats_runtime_selected_keys
from market_evidence_store import market_window_hash
from realized_pnl_ledger import read_realized_pnl_ledger
from confirmable_pnl_cycle_service import (
    latest_pnl_cycle_boundary,
    project_confirmable_cumulative_pnl,
)
from gui_review_utils import current_price_from_state
from routine_instance_registry import routine_instance_by_id
from runtime_io import read_json_dict
from state_policy import (
    auto_trade_status_display,
    normalize_operation_mode,
    normalized_hhmmss_or_empty,
    operation_mode_display,
    seconds_from_hhmmss,
)
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
_PNL_SNAPSHOT_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}

CHART_PROJECTION_VALID = "VALID"
CHART_PROJECTION_NO_DAY_DATA = "NO_DAY_DATA"
CHART_PROJECTION_NOT_READY = "NOT_READY"
CHART_PROJECTION_RULES_UNAVAILABLE = "RULES_UNAVAILABLE"
CHART_PROJECTION_REFRESH_FAILED = "REFRESH_FAILED"
CHART_PROJECTION_STALE_REJECTED = "STALE_REJECTED"


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_file_malformed(path: Path, *, list_field: str | None = None) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if list_field is None:
        return not isinstance(data, dict)
    if isinstance(data, list):
        return list_field != "candles" or any(
            not isinstance(item, dict) for item in data
        )
    if not isinstance(data, dict):
        return True
    records = data.get(list_field, [])
    return not isinstance(records, list) or any(
        not isinstance(item, dict) for item in records
    )


def _effective_operation_times(
    config: dict[str, Any],
    root: Path,
) -> tuple[str, str]:
    """Resolve the existing scheduled-operation read model without mutation."""
    default_start = "09:00:00"
    default_end = "13:30:00"
    policy = read_json_dict(root / "operation_policy.json")
    scheduled = policy.get("scheduled_operation", {})
    if isinstance(scheduled, dict):
        default_start = (
            normalized_hhmmss_or_empty(scheduled.get("default_start_time"))
            or default_start
        )
        default_end = (
            normalized_hhmmss_or_empty(scheduled.get("default_end_buy_time"))
            or default_end
        )
    elif not policy:
        legacy = read_json_dict(root / "global_schedule.json")
        default_start = (
            normalized_hhmmss_or_empty(
                legacy.get("start_time", legacy.get("buy_start_time"))
            )
            or default_start
        )
        default_end = (
            normalized_hhmmss_or_empty(
                legacy.get("end_buy_time", legacy.get("buy_end_time"))
            )
            or default_end
        )

    local_start = normalized_hhmmss_or_empty(
        config.get("start_time", config.get("trade_start_time"))
    )
    local_end = normalized_hhmmss_or_empty(
        config.get("end_buy_time", config.get("buy_end_time"))
    )
    if (
        local_start
        and local_end
        and seconds_from_hhmmss(local_start, local_start)
        < seconds_from_hhmmss(local_end, local_end)
    ):
        return local_start, local_end
    if seconds_from_hhmmss(default_start, default_start) >= seconds_from_hhmmss(
        default_end, default_end
    ):
        return "09:00:00", "13:30:00"
    return default_start, default_end


def _selected_ats_session_ranges(
    config: dict[str, Any],
    state: dict[str, Any],
    root: Path,
) -> list[dict[str, str]]:
    """Return selected canonical manual ATS ranges without policy interpretation."""
    if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
        return []
    selected = set(manual_ats_runtime_selected_keys(state))
    if not selected:
        return []
    policy = read_json_dict(root / "operation_policy.json")
    sessions = policy.get("extra_sessions", []) if isinstance(policy, dict) else []
    if not isinstance(sessions, list):
        return []
    ranges: list[dict[str, str]] = []
    for index, key in enumerate(("extra1", "extra2", "extra3")):
        if key not in selected or index >= len(sessions):
            continue
        session = sessions[index]
        if not isinstance(session, dict) or not bool(session.get("enabled", True)):
            continue
        start_time = normalized_hhmmss_or_empty(session.get("start_time"))
        end_time = normalized_hhmmss_or_empty(session.get("end_time"))
        if not start_time or not end_time or start_time == end_time:
            continue
        ranges.append(
            {
                "key": key,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
    return ranges


def _operation_title_display(
    operation_mode: str,
    state: dict[str, Any],
) -> str:
    if operation_mode != "CONTINUOUS":
        return "시간운영"
    if manual_ats_runtime_selected_keys(state):
        return "수동+ATS"
    return "수동운영"


def _decimal_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _display_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _read_runtime_list(path: Path, field: str) -> tuple[list[dict[str, Any]], str]:
    if not path.exists():
        return [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{field.upper()}_DATA_MALFORMED:{exc}"
    records = data.get(field) if isinstance(data, dict) else None
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return [], f"{field.upper()}_DATA_MALFORMED"
    return [deepcopy(item) for item in records], ""


def _read_runtime_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"RUNTIME_DATA_MALFORMED:{exc}"
    if not isinstance(data, dict):
        return {}, "RUNTIME_DATA_MALFORMED"
    return deepcopy(data), ""


def _record_trade_date(record: dict[str, Any]) -> str:
    text = str(record.get("received_at") or record.get("recorded_at") or "").strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _fill_order_key(fill: dict[str, Any]) -> str:
    order_identity = str(
        fill.get("broker_order_no")
        or fill.get("order_id")
        or fill.get("order_queued_id")
        or fill.get("execution_id")
        or ""
    ).strip()
    return "|".join(
        (
            str(fill.get("account_no") or "").strip(),
            str(fill.get("code") or "").strip(),
            str(fill.get("side") or "").strip().upper(),
            order_identity,
        )
    )


def _actual_fill_deltas(
    root: Path,
    stock_code: str,
    *,
    account_no: str = "",
) -> tuple[list[dict[str, Any]], str]:
    fills, error = _read_runtime_list(root / "runtime" / "fills.json", "fills")
    if error:
        return [], error
    selected = [
        item
        for item in fills
        if str(item.get("code") or "").strip() == str(stock_code or "").strip()
        and (
            not account_no
            or str(item.get("account_no") or "").strip() == account_no
        )
    ]
    selected.sort(
        key=lambda item: (
            str(item.get("received_at") or ""),
            str(item.get("recorded_at") or ""),
            str(item.get("fill_id") or ""),
        )
    )
    seen_fill_ids: set[str] = set()
    seen_execution_ids: set[tuple[str, str]] = set()
    cumulative_by_order: dict[str, int] = {}
    deltas: list[dict[str, Any]] = []
    for fill in selected:
        fill_id = str(fill.get("fill_id") or "").strip()
        if not fill_id or fill_id in seen_fill_ids:
            continue
        execution_key = (
            str(fill.get("execution_identity_source") or "").strip(),
            str(fill.get("execution_identity") or "").strip(),
        )
        if execution_key[1] and execution_key in seen_execution_ids:
            continue
        side = str(fill.get("side") or "").strip().upper()
        quantity = _decimal_number(fill.get("filled_quantity"))
        price = _decimal_number(fill.get("filled_price"))
        if (
            side not in {"BUY", "SELL"}
            or quantity is None
            or quantity <= 0
            or quantity != quantity.to_integral_value()
            or price is None
            or price <= 0
        ):
            return [], "FILL_COST_EVIDENCE_INVALID"
        order_key = _fill_order_key(fill)
        if not order_key.rsplit("|", 1)[-1]:
            return [], "FILL_ORDER_IDENTITY_UNAVAILABLE"
        cumulative = int(quantity)
        previous = cumulative_by_order.get(order_key, 0)
        if cumulative < previous:
            return [], "FILL_CUMULATIVE_QUANTITY_OUT_OF_ORDER"
        cumulative_by_order[order_key] = cumulative
        seen_fill_ids.add(fill_id)
        if execution_key[1]:
            seen_execution_ids.add(execution_key)
        if cumulative == previous:
            continue
        deltas.append(
            {
                "fill_id": fill_id,
                "side": side,
                "quantity": cumulative - previous,
                "price": price,
                "trade_date": _record_trade_date(fill),
            }
        )
    return deltas, ""


def _open_position_projection(root: Path, stock_code: str) -> dict[str, Any]:
    unavailable = {
        "open_position_available": False,
        "holding_quantity": None,
        "average_price": None,
        "open_position_cost": None,
        "position_account_no": "",
        "open_position_unavailable_reason": "POSITION_UNAVAILABLE",
    }
    positions, error = _read_runtime_list(root / "runtime" / "positions.json", "positions")
    if error:
        return {**unavailable, "open_position_unavailable_reason": error}
    matches = [
        item
        for item in positions
        if str(item.get("code") or "").strip() == str(stock_code or "").strip()
    ]
    if len(matches) > 1:
        return {**unavailable, "open_position_unavailable_reason": "POSITION_DUPLICATE"}
    if not matches:
        holdings, holding_error = _read_runtime_list(
            root / "runtime" / "broker_holdings.json",
            "holdings",
        )
        if holding_error:
            return {**unavailable, "open_position_unavailable_reason": holding_error}
        broker_matches = [
            item
            for item in holdings
            if str(item.get("code") or "").strip() == str(stock_code or "").strip()
        ]
        if len(broker_matches) > 1:
            return {**unavailable, "open_position_unavailable_reason": "BROKER_HOLDING_DUPLICATE"}
        broker_quantity = (
            _decimal_number(broker_matches[0].get("holding_quantity"))
            if broker_matches
            else Decimal("0")
        )
        if broker_quantity is None or broker_quantity < 0:
            return {**unavailable, "open_position_unavailable_reason": "BROKER_HOLDING_INVALID"}
        if broker_quantity > 0:
            return {
                **unavailable,
                "open_position_unavailable_reason": "HOLDING_RECONCILIATION_REQUIRED",
            }
        return {
            **unavailable,
            "open_position_available": True,
            "holding_quantity": 0,
            "average_price": 0,
            "open_position_cost": 0,
            "open_position_unavailable_reason": "",
        }
    position = matches[0]
    quantity = _decimal_number(position.get("quantity"))
    average = _decimal_number(position.get("average_price"))
    if quantity is None or quantity < 0 or quantity != quantity.to_integral_value():
        return {**unavailable, "open_position_unavailable_reason": "POSITION_QUANTITY_INVALID"}
    if average is None or average < 0 or (quantity > 0 and average <= 0):
        return {**unavailable, "open_position_unavailable_reason": "POSITION_AVERAGE_PRICE_INVALID"}
    account_no = str(position.get("account_no") or "").strip()
    # Position evidence is sufficient for this read-only display.  When broker
    # holding evidence exists it becomes a mandatory reconciliation check, but
    # opening the chart must not trigger a new account TR merely to create it.
    if (root / "runtime" / "broker_holdings.json").exists():
        holdings, holding_error = _read_runtime_list(
            root / "runtime" / "broker_holdings.json",
            "holdings",
        )
        if holding_error:
            return {**unavailable, "open_position_unavailable_reason": holding_error}
        holding_matches = [
            item
            for item in holdings
            if str(item.get("code") or "").strip() == str(stock_code or "").strip()
            and (
                not account_no
                or str(item.get("account_no") or "").strip() == account_no
            )
        ]
        if len(holding_matches) != 1:
            if quantity == 0 and not holding_matches:
                holding_matches = []
            else:
                return {
                    **unavailable,
                    "open_position_unavailable_reason": (
                        "BROKER_HOLDING_DUPLICATE" if len(holding_matches) > 1 else "BROKER_HOLDING_UNAVAILABLE"
                    ),
                }
        if holding_matches:
            reconciliation = broker_holding_reconciliation_status(
                holding_matches[0],
                root / "runtime" / "positions.json",
            )
            if (
                reconciliation.get("status") != "CONSISTENT"
                or reconciliation.get("manual_reconciliation_required") is True
            ):
                return {
                    **unavailable,
                    "open_position_unavailable_reason": "HOLDING_RECONCILIATION_REQUIRED",
                }
    cost = _decimal_number(position.get("cost_basis"))
    if cost is None:
        cost = average * quantity
    if cost < 0:
        return {**unavailable, "open_position_unavailable_reason": "POSITION_COST_INVALID"}
    return {
        "open_position_available": True,
        "holding_quantity": int(quantity),
        "average_price": _display_number(average),
        "open_position_cost": _display_number(cost),
        "position_account_no": account_no,
        "open_position_unavailable_reason": "",
    }


def _completed_buy_cost_projection(
    root: Path,
    stock_code: str,
    trade_date: str,
    *,
    account_no: str,
    expected_open_quantity: int,
    expected_open_cost: Decimal,
) -> tuple[Decimal | None, str]:
    deltas, error = _actual_fill_deltas(root, stock_code, account_no=account_no)
    if error:
        return None, error
    inventory_quantity = 0
    inventory_cost = Decimal("0")
    completed_cost = Decimal("0")
    for fill in deltas:
        quantity = int(fill["quantity"])
        price = fill["price"]
        if fill["side"] == "BUY":
            inventory_quantity += quantity
            inventory_cost += price * quantity
            continue
        if quantity > inventory_quantity or inventory_quantity <= 0:
            return None, "BUY_COST_HISTORY_INCOMPLETE"
        matched_cost = inventory_cost * Decimal(quantity) / Decimal(inventory_quantity)
        if fill["trade_date"] == trade_date:
            completed_cost += matched_cost
        inventory_quantity -= quantity
        inventory_cost -= matched_cost
    if inventory_quantity != expected_open_quantity:
        return None, "FILL_POSITION_QUANTITY_MISMATCH"
    if abs(inventory_cost - expected_open_cost) > Decimal("0.01"):
        return None, "FILL_POSITION_COST_MISMATCH"
    return completed_cost, ""


def _cumulative_pnl_projection(
    root: Path,
    stock_code: str,
    trade_date: str,
    candles: list[dict[str, Any]],
    instance_id: str,
    *,
    evaluation_price: Any = None,
    allow_candle_price_fallback: bool = True,
) -> dict[str, Any]:
    unavailable = {
        "daily_realized_gross": None,
        "completed_buy_cost": None,
        "open_position_cost": None,
        "unrealized_pnl_at_bar_close": None,
        "cumulative_pnl": None,
        "cumulative_return_rate": None,
        "pnl_bar_time": None,
        "pnl_bar_close": None,
        "pnl_available": False,
        "cumulative_return_available": False,
        "pnl_unavailable_reason": "COMPLETED_CANDLE_UNAVAILABLE",
        "pnl_source": "runtime/realized_pnl.json+runtime/fills.json+runtime/positions.json+completed_candle.close",
        "pnl_basis": "GROSS",
    }
    latest = candles[-1] if candles else None
    latest_close = _decimal_number(latest.get("close")) if isinstance(latest, dict) else None
    latest_bar_time = str(latest.get("bar_time") or "").strip() if isinstance(latest, dict) else ""
    if latest_close is None or latest_close <= 0 or not latest_bar_time:
        return unavailable

    if evaluation_price is None and allow_candle_price_fallback:
        evaluation_price = latest_close
    cycle = project_confirmable_cumulative_pnl(
        stock_code,
        evaluation_price,
        project_root=root,
        ledger_path=root / "runtime" / "pnl_cycle_boundaries.json",
    )
    return {
        "daily_realized_gross": cycle.get("realized_profit"),
        "completed_buy_cost": cycle.get("completed_buy_cost"),
        "open_position_cost": cycle.get("open_cost"),
        "holding_quantity": None,
        "average_price": None,
        "unrealized_pnl_at_bar_close": cycle.get("unrealized_profit"),
        "cumulative_pnl": cycle.get("cumulative_profit"),
        "cumulative_return_rate": cycle.get("cumulative_rate"),
        "pnl_bar_time": latest_bar_time,
        "pnl_bar_close": _display_number(latest_close),
        "pnl_evaluation_price": cycle.get("evaluation_price"),
        "pnl_available": cycle.get("available") is True,
        "cumulative_return_available": cycle.get("available") is True and cycle.get("cumulative_rate") is not None,
        "pnl_unavailable_reason": str(cycle.get("reason") or ""),
        "pnl_source": "runtime/pnl_cycle_boundaries.json+runtime/realized_pnl.json+runtime/fills.json+runtime/positions.json+completed_candle.close",
        "pnl_basis": "GROSS_CYCLE",
        "pnl_cycle_boundary_id": cycle.get("boundary_id"),
    }


def _bar_scoped_cumulative_pnl_projection(
    root: Path,
    stock_code: str,
    trade_date: str,
    candles: list[dict[str, Any]],
    instance_id: str,
    *,
    evaluation_price: Any = None,
    allow_candle_price_fallback: bool = True,
) -> dict[str, Any]:
    latest_bar_time = (
        str(candles[-1].get("bar_time") or "").strip()
        if candles and isinstance(candles[-1], dict)
        else ""
    )
    boundary = latest_pnl_cycle_boundary(
        stock_code,
        root / "runtime" / "pnl_cycle_boundaries.json",
    )
    boundary_id = str((boundary or {}).get("boundary_id") or "")
    cache_key = (
        str(root.resolve()),
        str(stock_code or "").strip(),
        str(trade_date or "").strip(),
        str(instance_id or "").strip(),
        boundary_id,
    )
    cached = _PNL_SNAPSHOT_CACHE.get(cache_key)
    if (
        latest_bar_time
        and isinstance(cached, dict)
        and cached.get("pnl_available") is True
        and str(cached.get("pnl_bar_time") or "").strip() == latest_bar_time
    ):
        reused = deepcopy(cached)
        reused["pnl_snapshot_reused"] = True
        return reused
    projected = _cumulative_pnl_projection(
        root,
        stock_code,
        trade_date,
        candles,
        instance_id,
        evaluation_price=evaluation_price,
        allow_candle_price_fallback=allow_candle_price_fallback,
    )
    projected["pnl_snapshot_reused"] = False
    if latest_bar_time and projected.get("pnl_available") is True:
        _PNL_SNAPSHOT_CACHE[cache_key] = deepcopy(projected)
    return projected


def _read_signal_records(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return []
    signals = data.get("signals", []) if isinstance(data, dict) else []
    return [deepcopy(item) for item in signals if isinstance(item, dict)] if isinstance(signals, list) else []


def _projection_as_of(trade_date: str, now: datetime | None) -> datetime:
    current = now or datetime.now(SEOUL_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        current = current.astimezone(SEOUL_TIMEZONE)
    try:
        requested = date.fromisoformat(trade_date)
    except ValueError:
        return current
    if requested < current.date():
        return datetime.combine(requested + timedelta(days=1), time(0, 0), tzinfo=SEOUL_TIMEZONE)
    return current


def _marker_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    required = (
        "signal_bar_time",
        "signal_bar_close",
        "signal_timeframe_minutes",
        "signal_trade_date",
    )
    if any(record.get(field) in (None, "") for field in required):
        return None
    signal_id = str(record.get("id") or "").strip()
    if not signal_id:
        return None
    return {
        "signal_id": signal_id,
        "signal": str(record.get("signal") or "").upper(),
        "signal_bar_time": record["signal_bar_time"],
        "signal_bar_close": record["signal_bar_close"],
        "signal_timeframe_minutes": record["signal_timeframe_minutes"],
        "signal_trade_date": record["signal_trade_date"],
        "signal_input_hash": record.get("signal_input_hash"),
        "signal_index": record.get("signal_index"),
        "delay_bar": record.get("delay_bar"),
        "created_at": record.get("created_at"),
    }


def project_stock_instance_day(
    stock_code: str,
    trade_date: str,
    *,
    project_root: str | Path = PROJECT_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project completed candles, exact signal markers, and order linkage."""
    root = Path(project_root)
    repository = StockRepository(project_root=root)
    stock_dir = repository.resolve_stock_dir(stock_code)
    config = read_json_dict(stock_dir / "config.json")
    state = read_json_dict(stock_dir / "state.json")
    instance_id = str(config.get("assigned_routine_instance_id") or "").strip()
    rules: dict[str, Any] | None = None
    instance = routine_instance_by_id(instance_id, project_root=root) if instance_id else None
    if instance is not None and instance.rules_path is not None:
        rules = read_json_dict(instance.rules_path)

    diagnostics: dict[str, Any] = {
        "stock_found": stock_dir.exists(),
        "instance_rules_available": isinstance(rules, dict) and bool(rules),
        "raw_candle_count": 0,
        "completed_candle_count": 0,
        "legacy_signal_marker_unavailable_count": 0,
        "order_queue_available": False,
        "issues": [],
    }
    if not stock_dir.exists():
        diagnostics["issues"].append("STOCK_NOT_FOUND")
    if _json_file_malformed(stock_dir / "config.json"):
        diagnostics["issues"].append("CONFIG_DATA_MALFORMED")
    if _json_file_malformed(stock_dir / "state.json"):
        diagnostics["issues"].append("STATE_DATA_MALFORMED")
    if _json_file_malformed(stock_dir / "candles.json", list_field="candles"):
        diagnostics["issues"].append("CANDLE_DATA_MALFORMED")
    if _json_file_malformed(
        root / "runtime" / "routine_signals.json",
        list_field="signals",
    ):
        diagnostics["issues"].append("SIGNAL_DATA_MALFORMED")
    if instance is not None and instance.rules_path is not None and _json_file_malformed(
        instance.rules_path
    ):
        diagnostics["issues"].append("INSTANCE_RULES_DATA_MALFORMED")
    if not instance_id:
        diagnostics["issues"].append("INSTANCE_ASSIGNMENT_UNAVAILABLE")
    if not rules:
        diagnostics["issues"].append("INSTANCE_RULES_UNAVAILABLE")

    raw_day = filter_candles_by_trade_date(load_candles(stock_dir), trade_date)
    diagnostics["raw_candle_count"] = len(raw_day)
    candles: list[dict[str, Any]] = []
    bar_minutes: int | None = None
    if rules:
        try:
            bar_minutes = read_canonical_bar_minutes(rules)
            candles = completed_timeframe_candles(
                raw_day,
                rules,
                now=_projection_as_of(trade_date, now),
            )
        except ValueError as exc:
            diagnostics["issues"].append(f"BAR_PROJECTION_ERROR:{exc}")
    diagnostics["completed_candle_count"] = len(candles)
    diagnostics["completed_input_hash"] = market_window_hash(candles) if candles else ""

    malformed_or_failed = any(
        "MALFORMED" in str(issue).upper()
        or "CORRUPT" in str(issue).upper()
        or "PROJECTION_ERROR" in str(issue).upper()
        or str(issue).upper() == "STOCK_NOT_FOUND"
        for issue in diagnostics["issues"]
    )
    if malformed_or_failed:
        projection_status = CHART_PROJECTION_REFRESH_FAILED
    elif not instance_id or not rules:
        projection_status = CHART_PROJECTION_RULES_UNAVAILABLE
    elif candles:
        projection_status = CHART_PROJECTION_VALID
    elif raw_day:
        projection_status = CHART_PROJECTION_NOT_READY
    else:
        projection_status = CHART_PROJECTION_NO_DAY_DATA
    diagnostics["projection_status"] = projection_status

    signal_records = _read_signal_records(root / "runtime" / "routine_signals.json")
    markers: list[dict[str, Any]] = []
    for record in signal_records:
        if str(record.get("code") or "").strip() != str(stock_code or "").strip():
            continue
        record_instance = str(record.get("routine_instance_id") or "").strip()
        marker = _marker_from_record(record)
        if marker is None:
            if not record_instance or record_instance == instance_id:
                diagnostics["legacy_signal_marker_unavailable_count"] += 1
            continue
        if not instance_id or record_instance != instance_id:
            continue
        if str(marker["signal_trade_date"]) != str(trade_date):
            continue
        if marker["signal"] in {"BUY", "SELL"}:
            markers.append(marker)

    order_result = read_execution_queue_records(root / "runtime" / "order_queue.json")
    order_records = list(order_result.get("records", ())) if order_result.get("ok") is True else []
    diagnostics["order_queue_available"] = order_result.get("ok") is True
    if order_result.get("ok") is not True:
        diagnostics["issues"].append("ORDER_QUEUE_UNAVAILABLE")
        if (root / "runtime" / "order_queue.json").exists():
            diagnostics["issues"].append("ORDER_QUEUE_DATA_MALFORMED")
    marker_ids = {str(marker.get("signal_id") or "") for marker in markers}
    actual_orders = [
        record
        for record in order_records
        if str(record.get("source_signal_id") or "") in marker_ids
        and record.get("send_order_called") is True
    ]
    actual_count_by_signal = {
        signal_id: sum(
            1
            for record in actual_orders
            if str(record.get("source_signal_id") or "") == signal_id
        )
        for signal_id in marker_ids
    }
    for marker in markers:
        marker["actual_order_count"] = actual_count_by_signal.get(marker["signal_id"], 0)

    normalized_operation_mode = normalize_operation_mode(
        config.get("operation_mode", "SCHEDULED")
    )
    operation_title_display = _operation_title_display(
        normalized_operation_mode,
        state,
    )
    operation_start_time, operation_end_buy_time = _effective_operation_times(
        config,
        root,
    )
    ats_session_ranges = _selected_ats_session_ranges(config, state, root)
    instance_name = str(
        getattr(instance, "display_name", "")
        or config.get("routine_instance_name")
        or instance_id
        or ""
    ).strip()
    current_status = str(state.get("status") or "STOPPED").strip() or "STOPPED"
    cumulative_pnl = _bar_scoped_cumulative_pnl_projection(
        root,
        stock_code,
        trade_date,
        candles,
        instance_id,
        evaluation_price=current_price_from_state(state),
        allow_candle_price_fallback=False,
    )
    # Position evidence is intentionally re-read on every projection refresh.
    # It must not inherit the bar-scoped PnL cache after an additional buy or a
    # full sell.
    open_position = _open_position_projection(root, stock_code)
    projection_now = now or datetime.now(SEOUL_TIMEZONE)
    if projection_now.tzinfo is None:
        projection_now = projection_now.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        projection_now = projection_now.astimezone(SEOUL_TIMEZONE)
    current_day_position = str(trade_date or "") == projection_now.date().isoformat()
    average_price = _finite_number(open_position.get("average_price"))
    holding_quantity = _finite_number(open_position.get("holding_quantity"))
    average_price_visible = bool(
        current_day_position
        and open_position.get("open_position_available") is True
        and holding_quantity is not None
        and holding_quantity > 0
        and average_price is not None
        and average_price > 0
    )

    fill_records, fill_error = _read_runtime_list(
        root / "runtime" / "fills.json",
        "fills",
    )
    execution_data, execution_error = _read_runtime_object(
        root / "runtime" / "order_executions.json"
    )
    execution_chart = project_execution_chart_read_model(
        fills=fill_records,
        order_executions=execution_data,
        queue_records=order_records,
        stock_code=stock_code,
        trade_date=trade_date,
    )
    diagnostics["actual_fill_diagnostics"] = execution_chart.get("diagnostics", [])
    diagnostics["actual_fill_read_errors"] = [
        error for error in (fill_error, execution_error) if error
    ]
    diagnostics["actual_fill_marker_count"] = len(
        execution_chart.get("actual_fill_markers", [])
    )
    diagnostics["execution_process_rail_count"] = len(
        execution_chart.get("execution_process_rails", [])
    )

    return {
        "stock_code": str(stock_code or "").strip(),
        "stock_name": str(config.get("name") or stock_dir.name.split("_", 1)[-1] if stock_dir.exists() else ""),
        "trade_date": str(trade_date or ""),
        "projection_status": projection_status,
        "instance_id": instance_id,
        "instance_name": instance_name,
        "bar_minutes": bar_minutes,
        "operation_mode": normalized_operation_mode,
        "operation_mode_display": operation_mode_display(normalized_operation_mode),
        "operation_title_display": operation_title_display,
        "operation_start_time": operation_start_time,
        "operation_end_buy_time": operation_end_buy_time,
        "operation_time": (
            "수동 운영"
            if normalized_operation_mode == "CONTINUOUS"
            else f"{operation_start_time[:5]}~{operation_end_buy_time[:5]}"
        ),
        "ats_session_ranges": ats_session_ranges,
        "current_status": current_status,
        "current_status_display": auto_trade_status_display(current_status),
        **cumulative_pnl,
        "open_position_available": open_position.get("open_position_available") is True,
        "holding_quantity": open_position.get("holding_quantity"),
        "position_open_cost": open_position.get("open_position_cost"),
        "position_account_no": open_position.get("position_account_no"),
        "open_position_unavailable_reason": open_position.get("open_position_unavailable_reason"),
        "average_price": average_price if average_price_visible else None,
        "average_price_visible": average_price_visible,
        "candles": candles,
        "buy_signal_markers": [marker for marker in markers if marker["signal"] == "BUY"],
        "sell_signal_markers": [marker for marker in markers if marker["signal"] == "SELL"],
        "buy_signal_count": sum(1 for marker in markers if marker["signal"] == "BUY"),
        "sell_signal_count": sum(1 for marker in markers if marker["signal"] == "SELL"),
        "actual_order_count": len(actual_orders),
        "actual_order_source": "runtime/order_queue.json:source_signal_id+send_order_called",
        "actual_fill_markers": execution_chart.get("actual_fill_markers", []),
        "actual_buy_fill_markers": execution_chart.get("actual_buy_fill_markers", []),
        "actual_sell_fill_markers": execution_chart.get("actual_sell_fill_markers", []),
        "execution_process_rails": execution_chart.get("execution_process_rails", []),
        "actual_fill_source": "runtime/fills.json→runtime/order_executions.json→runtime/order_queue.json",
        "diagnostics": diagnostics,
    }
