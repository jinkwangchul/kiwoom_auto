# -*- coding: utf-8 -*-
"""Read-only daily source projection for a stock's active routine instance."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
from typing import Any, Callable

from broker_holding_recorder import _reconciliation_status as broker_holding_reconciliation_status
from candle_manager import canonical_candle_content_hash
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    candle_market_datetime,
    candle_session_windows,
    read_canonical_bar_minutes,
)
from execution_queue_writer import read_execution_queue_records
from event_journal_reader import EventJournalReader
from execution_chart_read_model import project_execution_chart_read_model
from gui_stock_data import STOCK_LIBRARY_READY, load_stock_library_snapshot
from manual_ats_runtime import manual_ats_runtime_selected_keys
from market_evidence_store import market_window_hash
from realized_pnl_ledger import read_realized_pnl_ledger
from confirmable_pnl_cycle_service import (
    latest_pnl_cycle_boundary,
    project_confirmable_cumulative_pnl,
)
from gui_review_utils import current_price_from_state
from routine_instance_registry import routine_definition_by_id, routine_instance_by_id
from routine_package_contract import EVALUATION_ROLE, load_routine_callable
from runtime_io import read_json_dict
from state_policy import (
    actual_exchange_session_ranges,
    auto_trade_status_display,
    normalize_operation_mode,
    normalized_hhmmss_or_empty,
    operation_mode_display,
    seconds_from_hhmmss,
)
from stock_repository import StockRepository
from stock_code_contract import normalize_broker_stock_code
from stock_market_source_plan import read_stock_market_source_plan, source_for_time
from mock_validation_candle_provider import verified_native_calculation_supply


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


def _stock_nxt_availability(stock_code: object, root: Path) -> bool | None:
    """Read the frozen trading-day decision without a late library lookup."""
    try:
        plan = read_stock_market_source_plan(
            root, normalize_broker_stock_code(stock_code),
            datetime.now(SEOUL_TIMEZONE).date(),
        )
    except ValueError:
        return None
    return plan["nxt_available"] if plan is not None else None


def chart_market_session_projection(
    stock_code: object,
    root: Path,
    *,
    trading_day: date | None = None,
) -> dict[str, Any]:
    """Project the frozen source decision; an absent plan admits no session."""

    try:
        plan = read_stock_market_source_plan(
            root, normalize_broker_stock_code(stock_code),
            trading_day or datetime.now(SEOUL_TIMEZONE).date(),
        )
        plan_status = "READY" if plan is not None else "MARKET_SOURCE_PLAN_MISSING"
    except ValueError:
        plan = None
        plan_status = "MARKET_SOURCE_PLAN_INVALID"
    nxt_available = plan["nxt_available"] if plan is not None else None
    return {
        "source_plan": plan,
        "source_plan_status": plan_status,
        "nxt_available": nxt_available,
        "market_eligibility": {
            "krx": plan is not None,
            "nxt": nxt_available,
        },
        "market_sessions": [
            dict(item) for item in (plan["sessions"] if plan is not None else ())
        ],
    }


def _chart_calculation_scope(root: Path, code: str, at: datetime) -> str:
    try:
        plan = read_stock_market_source_plan(root, normalize_broker_stock_code(code), at.date())
        active = source_for_time(plan, at) if plan is not None else None
    except ValueError:
        return ""
    source = active.get("source") if active is not None else ""
    return f"{source}_CALCULATION_ONLY" if source in {"SOR", "KRX", "NXT"} else ""


def _verified_krx_chart_supply(
    supply: dict[str, Any], code: str, interval: int, *, plan: dict[str, Any], as_of: datetime,
) -> bool:
    return verified_native_calculation_supply(supply, stock_code=code, interval=interval,
                                              as_of=as_of, plan=plan)



def chart_operation_method_badge_label(
    operation_mode: object,
    *,
    ats_selected: bool,
) -> str:
    """Return the shared compact operation-method badge literal."""

    if normalize_operation_mode(operation_mode) != "CONTINUOUS":
        return "시간"
    return "ATS" if ats_selected else "수동"


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


def project_operation_timeline(
    start_at: object,
    end_at: object,
    *,
    now: datetime,
    source_dates: set[str],
    active: bool = False,
) -> dict[str, Any]:
    """Share only the read-only chart DTO, never lifecycle ownership."""
    def exact_timestamp(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return (parsed.replace(tzinfo=SEOUL_TIMEZONE) if parsed.tzinfo is None
                else parsed.astimezone(SEOUL_TIMEZONE))

    start = exact_timestamp(start_at)
    end = exact_timestamp(end_at) if end_at else None
    if start is None or start > now or (end_at and end is None) or (end is not None and (end < start or end > now)):
        return {"status": "NOT_PROVEN", "start_at": "", "end_at": "", "trade_dates": []}
    first_day = start.date().isoformat()
    target_day = (end or now).date()
    if end is None:
        while target_day.weekday() >= 5:
            target_day -= timedelta(days=1)
    last_day = max(first_day, target_day.isoformat())
    witnessed_source_days = sorted(day for day in source_dates
                                   if first_day <= day <= last_day)
    event_days = {start.date().isoformat()}
    if end is not None:
        event_days.add(end.date().isoformat())
    days = sorted(
        day for day in source_dates | event_days
        if first_day <= day <= last_day
    )
    cursor = start.date()
    display_days = set(days)
    unverified_days = []
    while cursor <= date.fromisoformat(last_day):
        day = cursor.isoformat()
        if cursor.weekday() < 5:
            display_days.add(day)
            if day not in witnessed_source_days:
                unverified_days.append(day)
        cursor += timedelta(days=1)
    return {
        "status": "ENDED" if end is not None else "ONGOING" if active else "END_UNKNOWN",
        "start_at": start.isoformat(),
        "end_at": end.isoformat() if end is not None else "",
        "trade_dates": days,
        "source_dates": witnessed_source_days,
        "display_dates": sorted(display_days),
        "unverified_calendar_dates": unverified_days,
        "date_coverage": ("CALENDAR_WEEKDAYS_UNVERIFIED" if unverified_days
                          else "SOURCE_DATES_WITNESSED"),
    }


def _production_operation_timeline(root: Path, stock_code: str, now: datetime,
                                   source_dates: set[str]) -> dict[str, Any]:
    journal = EventJournalReader(root / "runtime" / "event_journal").read_events(
        category="OPERATION", end_at=now, descending=False,
    )
    if journal["errors"] or journal["malformed_count"] or journal["invalid_count"]:
        return project_operation_timeline(None, None, now=now, source_dates=set())
    code = normalize_broker_stock_code(stock_code)
    starts = [event for event in journal["events"]
              if event.get("event_type") == "OPERATION_STARTED"
              and event.get("result") == "SUCCESS"
              and event.get("target_type") == "STOCK"
              and normalize_broker_stock_code(event.get("stock_code")) == code]
    if not starts:
        return project_operation_timeline(None, None, now=now, source_dates=set())
    start = starts[-1]
    start_at = start["occurred_at"]
    start_time = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
    endings = [event for event in journal["events"]
               if event.get("event_type") == "LIQUIDATION_COMPLETED"
               and event.get("result") == "COMPLETED"
               and event.get("source") == "operation_close_completion_check_service.check_global_close_completion_after_durable_update"
               and event.get("target_type") == "STOCK"
               and normalize_broker_stock_code(event.get("stock_code")) == code
               and datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")) >= start_time
               and isinstance(event.get("details"), dict)
               and event["details"].get("completion_status") == "DONE"]
    operation_state = read_json_dict(root / "runtime" / "operation_state.json")
    participants = operation_state.get("operation_participant_stock_codes")
    try:
        global_start = datetime.fromisoformat(
            str(operation_state.get("operation_started_at") or "").replace("Z", "+00:00")
        )
        if global_start.tzinfo is None:
            global_start = global_start.replace(tzinfo=SEOUL_TIMEZONE)
    except ValueError:
        global_start = None
    stock_state = read_json_dict(StockRepository(project_root=root).resolve_stock_dir(code) / "state.json")
    stock_status = str(stock_state.get("status") or "").upper()
    active = (not endings and operation_state.get("operation_status") == "RUNNING"
              and global_start is not None and global_start <= start_time
              and isinstance(participants, list) and code in participants
              and stock_status in {"MONITORING", "RUNNING", "SELL_ONLY", "AUTO_CLOSE",
                                   "AUTO_CLOSING", "EARLY_CLOSE", "EARLY_CLOSING"}
              and stock_state.get("review_required") is not True)
    if not active and not endings:
        return {"status": "WAITING", "start_at": "", "end_at": "", "trade_dates": []}
    return project_operation_timeline(
        start_at, endings[0]["occurred_at"] if endings else None,
        now=now, source_dates=source_dates, active=active,
    )


def project_stock_instance_day(
    stock_code: str,
    trade_date: str,
    *,
    project_root: str | Path = PROJECT_ROOT,
    now: datetime | None = None,
    candle_provider: Callable[..., dict[str, Any]] | None = None,
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

    candles: list[dict[str, Any]] = []
    bar_minutes: int | None = None
    candle_supply: dict[str, Any] = {
        "available": False,
        "candles": [],
        "availability_state": "SOURCE_UNAVAILABLE",
    }
    if rules:
        try:
            bar_minutes = read_canonical_bar_minutes(rules)
        except ValueError as exc:
            diagnostics["issues"].append(f"BAR_PROJECTION_ERROR:{exc}")
        if callable(candle_provider) and instance is not None and bar_minutes is not None:
            definition = routine_definition_by_id(
                instance.definition_id,
                project_root=root,
            )
            if definition is None:
                raise ValueError("ROUTINE_DEFINITION_UNAVAILABLE")
            request_reader = load_routine_callable(
                definition,
                EVALUATION_ROLE,
                callable_key="market_bar_projection_callable",
            )
            routine_projection_request = request_reader(rules)
            if not isinstance(routine_projection_request, dict):
                raise ValueError("ROUTINE_MARKET_PROJECTION_REQUEST_INVALID")
            projection_as_of = _projection_as_of(trade_date, now)
            market_projection = chart_market_session_projection(
                stock_code, root, trading_day=date.fromisoformat(trade_date),
            )
            plan = market_projection["source_plan"]
            if plan is not None and projection_as_of.date().isoformat() != trade_date:
                last_end = plan["sessions"][-1]["end_time"]
                projection_as_of = (datetime.combine(date.fromisoformat(trade_date),
                                  time.fromisoformat(last_end), tzinfo=SEOUL_TIMEZONE)
                                  - timedelta(seconds=1))
            consumer_scope = _chart_calculation_scope(root, stock_code, projection_as_of)
            projection_request = chart_candle_projection_request(
                routine_projection_request,
                krx_native=consumer_scope == "KRX_CALCULATION_ONLY",
            )
            candle_supply = candle_provider(
                stock_code=stock_code,
                rules=rules,
                projection_request=projection_request,
                as_of=projection_as_of,
                session_windows=candle_session_windows(
                    read_json_dict(root / "operation_policy.json"),
                    manual_ats_runtime_selected_keys(state),
                ),
                consumer_scope=consumer_scope,
            )
            if not isinstance(candle_supply, dict):
                raise TypeError("Production Candle provider returned a non-object")
            supplied = candle_supply.get("candles", [])
            if not isinstance(supplied, list):
                raise TypeError("Production Candle provider returned invalid candles")
            candles = [deepcopy(item) for item in supplied if isinstance(item, dict)]
            supplied_interval = candle_supply.get("timeframe_minutes")
            if isinstance(supplied_interval, int) and supplied_interval > 0:
                bar_minutes = supplied_interval
            availability_state = str(
                candle_supply.get("availability_state") or ""
            ).upper()
            if "MALFORMED" in availability_state or "CORRUPT" in availability_state:
                diagnostics["issues"].append(availability_state)
    diagnostics["raw_candle_count"] = int(
        candle_supply.get("available_minute_candles", 0) or 0
    )
    diagnostics["completed_candle_count"] = len(candles)
    diagnostics["completed_input_hash"] = market_window_hash(candles) if candles else ""
    diagnostics["candle_availability_state"] = str(
        candle_supply.get("availability_state") or ""
    ).strip()
    market_projection = chart_market_session_projection(
        stock_code, root, trading_day=date.fromisoformat(trade_date),
    )
    nxt_available = market_projection["nxt_available"]
    display_metadata = candle_supply.get("display_metadata", {})
    display_metadata = (
        deepcopy(display_metadata)
        if isinstance(display_metadata, dict)
        else {}
    )
    supplied_display = candle_supply.get("display_candles", [])
    display_candles = (
        [
            deepcopy(item)
            for item in supplied_display
            if isinstance(item, dict)
            and (bar_time := candle_market_datetime(item)) is not None
            and bar_time.date().isoformat() == str(trade_date or "")
        ]
        if isinstance(supplied_display, list)
        else []
    )
    display_evidence_valid = bool(
        display_candles
        and nxt_available is True
        and display_metadata.get("display_only") is True
        and str(display_metadata.get("market_source") or "") == "INTEGRATED"
        and str(display_metadata.get("market_data_identity") or "")
        == f"{normalize_broker_stock_code(stock_code)}_AL"
    )
    sor_verified = (
        nxt_available is True
        and candle_supply.get("available") is True
        and candle_supply.get("market_source") == "INTEGRATED"
        and candle_supply.get("market_data_identity") == f"{normalize_broker_stock_code(stock_code)}_AL"
        and candle_supply.get("calculation_only") is True
        and candle_supply.get("execution_enabled") is False
        and candle_supply.get("queue_connected") is False
        and candle_supply.get("send_order_connected") is False
        and (
            candle_supply.get("calculation_source") == "BROKER_AL_CONFIRMED_M1"
            if bar_minutes == 1 else
            candle_supply.get("calculation_source") == "BROKER_AL_CONFIRMED_DIRECT"
            and candle_supply.get("direct_history_source") == "BROKER_AL_CONFIRMED_DIRECT"
            and candle_supply.get("direct_timeframe_source") is True
            and isinstance(candle_supply.get("direct_source_receipt"), dict)
            and candle_supply["direct_source_receipt"].get("stock_code") == normalize_broker_stock_code(stock_code)
            and candle_supply["direct_source_receipt"].get("timeframe_key") == f"M{bar_minutes}"
            and isinstance(candle_supply.get("source_identity"), str)
            and len(candle_supply["source_identity"]) == 64
            and candle_supply["direct_source_receipt"].get("source_content_sha256")
            == candle_supply.get("source_identity")
        )
    )
    krx_verified = (nxt_available is False and bar_minutes is not None
                    and candle_supply.get("available") is True
                    and _verified_krx_chart_supply(
                        candle_supply, normalize_broker_stock_code(stock_code), bar_minutes,
                        plan=market_projection["source_plan"], as_of=projection_as_of))
    nxt_verified = (nxt_available is True and bar_minutes is not None
                    and candle_supply.get("market_source") == "NXT"
                    and candle_supply.get("available") is True
                    and _verified_krx_chart_supply(
                        candle_supply, normalize_broker_stock_code(stock_code), bar_minutes,
                        plan=market_projection["source_plan"], as_of=projection_as_of))
    calculation_verified = sor_verified or krx_verified or nxt_verified
    chart_candles = (
        [item for item in candles
         if (bar_time := candle_market_datetime(item)) is not None
         and bar_time.date().isoformat() != str(trade_date or "")] + display_candles
        if display_evidence_valid and sor_verified else candles if calculation_verified else []
    )
    projection_now = now or datetime.now(SEOUL_TIMEZONE)
    if projection_now.tzinfo is None:
        projection_now = projection_now.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        projection_now = projection_now.astimezone(SEOUL_TIMEZONE)
    source_dates = {
        bar_time.date().isoformat()
        for item in chart_candles
        if (bar_time := candle_market_datetime(item)) is not None
    }
    operation_timeline = _production_operation_timeline(
        root, stock_code, projection_now, source_dates,
    )
    episode_start = episode_end = None
    if operation_timeline["status"] in {"ONGOING", "ENDED", "END_UNKNOWN"}:
        episode_start = datetime.fromisoformat(operation_timeline["start_at"])
        episode_end = (datetime.fromisoformat(operation_timeline["end_at"])
                       if operation_timeline["end_at"] else projection_now)
        chart_candles = [item for item in chart_candles
                         if (bar_time := candle_market_datetime(item)) is not None
                         and episode_start <= bar_time <= episode_end]
        in_range_source_dates = {
            bar_time.date().isoformat()
            for item in chart_candles
            if (bar_time := candle_market_datetime(item)) is not None
        }
        operation_timeline = project_operation_timeline(
            operation_timeline["start_at"], operation_timeline["end_at"] or None,
            now=projection_now, source_dates=in_range_source_dates,
            active=operation_timeline["status"] == "ONGOING",
        )
    timeline_dates = (
        set(operation_timeline["trade_dates"])
        if operation_timeline["status"] in {"ONGOING", "ENDED", "END_UNKNOWN"}
        else {str(trade_date)}
    )
    if operation_timeline["status"] in {"ONGOING", "ENDED", "END_UNKNOWN"}:
        chart_candles = [item for item in chart_candles
                         if (bar_time := candle_market_datetime(item)) is not None
                         and bar_time.date().isoformat() in timeline_dates]
    diagnostics["calculation_candle_count"] = len(candles)
    diagnostics["chart_candle_count"] = len(chart_candles)
    diagnostics["chart_display_only_integrated"] = display_evidence_valid and sor_verified
    diagnostics["chart_candle_source"] = (
        "INTEGRATED_DISPLAY_ONLY"
        if display_evidence_valid and sor_verified
        else "CALCULATION_SOR_AL" if sor_verified
        else "CALCULATION_KRX_NATIVE" if krx_verified
        else "CALCULATION_NXT_NATIVE" if nxt_verified else "SOURCE_UNAVAILABLE"
    )

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
    elif calculation_verified and candles:
        projection_status = CHART_PROJECTION_VALID
    elif diagnostics["raw_candle_count"] > 0:
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
        if str(marker["signal_trade_date"]) not in timeline_dates:
            continue
        marker_time = candle_market_datetime({"bar_time": marker["signal_bar_time"]})
        if (episode_start is not None and
                (marker_time is None or not episode_start <= marker_time <= episode_end)):
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
    execution_charts = [project_execution_chart_read_model(
        fills=fill_records,
        order_executions=execution_data,
        queue_records=order_records,
        stock_code=stock_code,
        trade_date=day,
    ) for day in sorted(timeline_dates)]
    execution_chart = {
        key: [item for result in execution_charts for item in result.get(key, [])]
        for key in ("actual_fill_markers", "actual_buy_fill_markers",
                    "actual_sell_fill_markers", "execution_process_rails", "diagnostics")
    }
    if operation_timeline["status"] in {"ONGOING", "ENDED", "END_UNKNOWN"}:
        started = datetime.fromisoformat(operation_timeline["start_at"])
        finished = datetime.fromisoformat(operation_timeline["end_at"]) if operation_timeline["end_at"] else projection_now
        for key in ("actual_fill_markers", "actual_buy_fill_markers", "actual_sell_fill_markers"):
            execution_chart[key] = [item for item in execution_chart[key]
                                    if isinstance(item, dict)
                                    and isinstance(item.get("occurred_at"), str)
                                    and (occurred := datetime.fromisoformat(item["occurred_at"]))
                                    and started <= occurred <= finished]
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
        "operation_timeline": operation_timeline,
        "chart_domain_label": "SOR _AL" if sor_verified else "KRX" if krx_verified else "NXT _NX" if nxt_verified else "-",
        "chart_operation_method_label": chart_operation_method_badge_label(
            normalized_operation_mode,
            ats_selected=bool(ats_session_ranges),
        ),
        "chart_bar_label": f"{bar_minutes}분봉" if bar_minutes else "-",
        "operation_start_time": operation_start_time,
        "operation_end_buy_time": operation_end_buy_time,
        "operation_time": (
            "수동 운영"
            if normalized_operation_mode == "CONTINUOUS"
            else f"{operation_start_time[:5]}~{operation_end_buy_time[:5]}"
        ),
        "ats_session_ranges": ats_session_ranges,
        **market_projection,
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
        "candles": chart_candles,
        "calculation_candle_source": ("SOR_AL" if sor_verified else
                                      "KRX_NATIVE" if krx_verified else
                                      "NXT_NATIVE" if nxt_verified else "SOURCE_UNAVAILABLE"),
        "chart_candle_source": diagnostics["chart_candle_source"],
        "chart_display_only_integrated": display_evidence_valid and sor_verified,
        "display_metadata": display_metadata if display_evidence_valid and sor_verified else {},
        "candle_availability": deepcopy(candle_supply),
        "completeness": deepcopy(candle_supply.get("completeness", {})),
        "freshness": deepcopy(candle_supply.get("freshness", {})),
        "source_identity": str(candle_supply.get("source_identity") or "").strip(),
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


def chart_candle_projection_request(
    routine_projection_request: dict[str, Any],
    *, krx_native: bool = False,
) -> dict[str, Any]:
    """Keep the Routine projection kind without trimming chart history to evaluation needs."""

    if not isinstance(routine_projection_request, dict):
        raise ValueError("ROUTINE_MARKET_PROJECTION_REQUEST_INVALID")
    request = deepcopy(routine_projection_request)
    request.pop("warmup_bars", None)
    request.pop("history_target_bars", None)
    return request
