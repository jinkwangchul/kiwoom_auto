"""Read-only chart projection of Mock-owned plans, orders and fill records."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from math import isfinite

from candle_timeframe_aggregation import SEOUL_TIMEZONE


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=SEOUL_TIMEZONE) if parsed.tzinfo is None else parsed.astimezone(SEOUL_TIMEZONE)
    except (TypeError, ValueError):
        return None


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _positive(value):
    try:
        return isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _option_summary(plan):
    mode = plan.get("mode")
    if mode == "SINGLE":
        return "단일 주문"
    if mode == "MULTI_HOGA":
        offsets = plan.get("hoga", {}).get("hoga_offsets")
        if isinstance(offsets, list) and offsets and all(isinstance(n, int) for n in offsets):
            return f"다중호가 ↑{max(0, max(offsets))} / ↓{max(0, -min(offsets))}"
        return "다중호가"
    if mode == "MULTI_TIME":
        schedule = plan.get("schedule", {})
        value = schedule.get("interval")
        unit = {"SECOND": "초", "SECONDS": "초", "초": "초",
                "MINUTE": "분", "MINUTES": "분", "분": "분",
                "BAR": "봉", "BARS": "봉", "봉": "봉"}.get(schedule.get("interval_unit"))
        if _positive(value) and unit:
            return f"다중시간 {value}{unit} {len(_rows(plan.get('children')))}회"
        return "다중시간"
    if mode == "MULTI_RATIO":
        return f"다중비율 {len(_rows(plan.get('children')))}회" if plan.get("ratio") else "다중비율"
    return _text(mode) or "-"


def project_mock_execution_chart(document, routine_instance_id, trade_date):
    """Project existing evidence without importing a writer or Production provenance."""
    markers, rails, diagnostics = [], [], []
    result = {"actual_fill_markers": markers, "execution_process_rails": rails, "diagnostics": diagnostics}
    session = document.get("session", {})
    operation = document.get("mock_operation_lifecycle", {}).get("instance_operations", {}).get(routine_instance_id, {})
    started = _timestamp(operation.get("started_at"))
    ended = _timestamp(operation.get("ended_at"))
    if operation and (operation.get("trading_date") != trade_date or started is None):
        diagnostics.append({"reason": "MOCK_CHART_OPERATION_SCOPE_UNAVAILABLE"})
        return result

    def issue(reason, identity):
        diagnostics.append({"reason": reason, "identity": identity})

    def in_scope_time(value):
        time = _timestamp(value)
        return time is not None and time.date().isoformat() == trade_date and (
            started is None or time >= started
        ) and (ended is None or time <= ended)

    def same_target(row):
        return all(row.get(key) == value for key, value in (
            ("routine_instance_id", routine_instance_id),
            ("stock_code", session.get("stock_code")),
            ("validation_session_id", session.get("validation_session_id")),
        ))

    orders = defaultdict(list)
    for order in _rows(document.get("orders")):
        orders[_text(order.get("mock_order_id"))].append(order)
    owners = defaultdict(list)
    all_plans = []
    for instance_id, root in document.get("progression_by_instance", {}).items():
        for plan in _rows(root.get("indicator_follow_mock_adapter", {}).get("plans")):
            all_plans.append((instance_id, plan))
            for child in _rows(plan.get("children")):
                if child.get("mock_order_id"):
                    owners[_text(child["mock_order_id"])].append((instance_id, plan, child))
    process_counts = Counter(_text(p.get("execution_process_id")) for _, p in all_plans)
    eligible = {}
    for instance_id, plan in all_plans:
        if instance_id != routine_instance_id or not in_scope_time(plan.get("plan_started_at")):
            continue
        process_id = _text(plan.get("execution_process_id"))
        children = _rows(plan.get("children"))
        sequences = [child.get("child_sequence") for child in children]
        if not process_id or process_counts[process_id] != 1 or plan.get("side") not in {"BUY", "SELL"}:
            issue("MOCK_CHART_PROCESS_IDENTITY_INVALID", process_id)
            continue
        if not children or sequences != list(range(1, len(children) + 1)):
            issue("MOCK_CHART_CHILD_SEQUENCE_INVALID", process_id)
            continue
        invalid = False
        for child in children:
            order_id = _text(child.get("mock_order_id"))
            if not order_id:
                continue
            matches = orders[order_id]
            if len(owners[order_id]) != 1 or len(matches) != 1 or not same_target(matches[0]) or matches[0].get("side") != plan["side"]:
                issue("MOCK_CHART_ORDER_OWNERSHIP_INVALID", order_id)
                invalid = True
        if not invalid:
            eligible[id(plan)] = plan

    fills = _rows(document.get("fills"))
    fill_counts = Counter(_text(fill.get("mock_fill_id")) for fill in fills)
    fill_ids = defaultdict(list)
    for fill in fills:
        if fill.get("routine_instance_id") != routine_instance_id or not in_scope_time(fill.get("filled_at")):
            continue
        fill_id, order_id = _text(fill.get("mock_fill_id")), _text(fill.get("mock_order_id"))
        if not fill_id or not order_id or fill_counts[fill_id] != 1 or not same_target(fill) or fill.get("side") not in {"BUY", "SELL"} or not _positive(fill.get("qty")) or not _positive(fill.get("price")):
            issue("MOCK_CHART_FILL_INVALID", fill_id)
            continue
        matches = orders[order_id]
        if len(matches) != 1 or not same_target(matches[0]) or matches[0].get("side") != fill["side"]:
            issue("MOCK_CHART_ORDER_IDENTITY_INVALID", order_id)
            continue
        if not in_scope_time(matches[0].get("created_at")):
            continue
        ownership = owners[order_id]
        if len(ownership) > 1:
            issue("MOCK_CHART_ORDER_OWNERSHIP_INVALID", order_id)
            continue
        plan = child = None
        if ownership:
            owner_instance, plan, child = ownership[0]
            if owner_instance != routine_instance_id or id(plan) not in eligible:
                continue
        else:
            issue("MOCK_CHART_FILL_PLAN_MISSING", fill_id)
        marker = {
            "marker_id": "MOCK_FILL:" + fill_id,
            "fill_id": fill_id,
            "side": fill["side"],
            "filled_quantity_delta": fill["qty"],
            "filled_price": fill["price"],
            "occurred_at": fill["filled_at"],
            "execution_time_source": "MOCK_FILL",
            "execution_time_quality": "SIMULATED",
            "mock_order_id": order_id,
            "execution_process_id": plan["execution_process_id"] if plan else "",
            "child_sequence_index": child["child_sequence"] if child else None,
            "child_sequence_total": len(plan["children"]) if plan else None,
            "option_summary": _option_summary(plan) if plan else "",
            "buy_round": plan.get("round") if plan and fill["side"] == "BUY" else None,
        }
        markers.append(marker)
        fill_ids[order_id].append(fill_id)

    for plan in eligible.values():
        children = []
        for child in plan["children"]:
            intent = child.get("intent", {})
            child_plan = intent.get("child_plan", {})
            status = {"FILLED": "COMPLETED", "PARTIAL_FILL": "PARTIAL", "OPEN": "ORDERED", "CANCELED": "CANCELLED"}.get(child.get("status"), child.get("status"))
            children.append({
                "child_sequence_index": child["child_sequence"],
                "child_sequence_total": len(plan["children"]),
                "child_kind": intent.get("child_kind", ""),
                "mock_order_id": child.get("mock_order_id", ""),
                "filled_quantity": child.get("filled_qty"),
                "remaining_quantity": child.get("remaining_qty"),
                "status": status,
                "fill_ids": list(fill_ids[_text(child.get("mock_order_id"))]),
                "planned_at": plan.get("plan_started_at"),
                "due_at": child.get("due_at"),
                "planned_quantity": child.get("quantity"),
                "planned_price": child_plan.get("planned_price", intent.get("price")),
            })
        rails.append({
            "execution_process_id": plan["execution_process_id"],
            "side": plan["side"], "execution_mode": plan.get("mode"),
            "buy_round": plan.get("round") if plan["side"] == "BUY" else None,
            "option_summary": _option_summary(plan), "status": plan.get("state"),
            "source_signal_id": plan.get("source_signal_id", ""),
            "child_total": len(children),
            "child_completed": sum(child["status"] == "COMPLETED" for child in children),
            "children": children,
        })
    markers.sort(key=lambda marker: (_timestamp(marker["occurred_at"]), marker["fill_id"]))
    return deepcopy(result)
