# -*- coding: utf-8 -*-
"""
gui_order_utils.py

주문 데이터 해석 / 미체결 수량 계산 유틸리티.
UI에 의존하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from gui_common_utils import safe_int_value
from runtime_io import read_orders_data


def order_value(order: dict[str, object], keys: list[str], default: object = "") -> object:
    """
    주문 dict 에서 여러 후보 키 중 먼저 발견되는 값을 반환한다.
    """
    for key in keys:
        if key in order:
            return order.get(key, default)
    return default


PENDING_ORDER_STATUSES = {
    "OPEN",
    "PARTIAL",
    "PARTIAL_FILLED",
    "PARTIALLY_FILLED",
    "ORDER_ACCEPTED",
    "ORDER_REQUESTED",
    "RECEIVED",
    "ACCEPTED",
    "SUBMITTED",
    "CANCEL_REQUESTED",
}

CLOSED_ORDER_STATUSES = {
    "FILLED",
    "COMPLETED",
    "COMPLETE",
    "CANCELED",
    "CANCELLED",
    "CANCEL_COMPLETE",
    "REJECTED",
    "EXPIRED",
    "FAILED",
    "LOCAL_RESET",
    "전량체결",
    "체결완료",
    "완료",
    "취소",
    "취소완료",
    "거부",
    "실패",
}


def normalized_order_status(order: dict[str, object]) -> str:
    return str(order_value(order, ["status", "order_status", "상태"], "")).strip().upper()


def order_current_pending_qty(order: dict[str, object]) -> tuple[int, bool]:
    """
    주문 1건의 현재 미체결 수량과 확인 필요 여부를 반환한다.
    """
    status = normalized_order_status(order)

    if status in CLOSED_ORDER_STATUSES:
        return 0, False

    raw_pending = order_value(order, ["pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"], None)
    if raw_pending not in (None, ""):
        pending_qty = max(safe_int_value(raw_pending, 0), 0)
        return pending_qty, False

    if status in PENDING_ORDER_STATUSES:
        order_qty_raw = order_value(order, ["order_qty", "qty", "주문수량"], None)
        filled_qty_raw = order_value(order, ["filled_qty", "executed_qty", "체결수량"], None)
        if order_qty_raw not in (None, ""):
            order_qty = safe_int_value(order_qty_raw, 0)
            filled_qty = safe_int_value(filled_qty_raw, 0) if filled_qty_raw not in (None, "") else 0
            return max(order_qty - filled_qty, 0), False
        return 0, True

    return 0, False


def pending_order_side_quantities(stock_dir: Path, state: dict[str, object]) -> tuple[object, object]:
    """
    매수/매도 현재 미체결 수량을 반환한다.

    반환값은 숫자 또는 계산 불가를 뜻하는 "?" 이다.
    과거 매결/도결 누적값은 현재 미체결 판단에 사용하지 않는다.
    """
    buy_qty = 0
    sell_qty = 0
    unknown_buy = False
    unknown_sell = False

    for order in read_orders_data(stock_dir / "orders.json"):
        pending_qty, unknown = order_current_pending_qty(order)
        if pending_qty <= 0 and not unknown:
            continue

        side_raw = str(order_value(order, ["side", "order_side", "구분", "매매구분"], "")).strip().upper()
        is_buy = side_raw in ("BUY", "매수", "B")
        is_sell = side_raw in ("SELL", "매도", "S")
        if not is_buy and not is_sell:
            unknown_buy = True
            continue

        if unknown:
            if is_buy:
                unknown_buy = True
            else:
                unknown_sell = True
            continue

        if is_buy:
            buy_qty += pending_qty
        else:
            sell_qty += pending_qty

    state_pending_qty = safe_int_value(state.get("pending_qty"), 0)
    if bool(state.get("pending_order", False)) and state_pending_qty > 0 and buy_qty == 0 and sell_qty == 0:
        unknown_buy = True

    return ("?" if unknown_buy else buy_qty), ("?" if unknown_sell else sell_qty)

# ===== 주문 표시 / 결산 / 내보내기 유틸리티 =====

def order_status_display(raw_status: object) -> str:
    """
    주문 상태 표시값을 한글로 정리한다.
    """
    status = str(raw_status or "").strip().upper()

    mapping = {
        "": "-",
        "RECEIVED": "접수",
        "ACCEPTED": "접수",
        "SUBMITTED": "접수",
        "OPEN": "미체결",
        "PARTIAL": "일부체결",
        "PARTIALLY_FILLED": "일부체결",
        "FILLED": "체결완료",
        "COMPLETED": "완료",
        "CANCELED": "취소완료",
        "CANCELLED": "취소완료",
        "REJECTED": "거부",
        "EXPIRED": "만료",
    }

    return mapping.get(status, str(raw_status or "-"))


def order_side_display(raw_side: object) -> str:
    """
    주문 구분 표시값.
    """
    side = str(raw_side or "").strip().upper()

    mapping = {
        "BUY": "매수",
        "B": "매수",
        "SELL": "매도",
        "S": "매도",
    }

    return mapping.get(side, str(raw_side or "-"))


def format_number_value(value: object) -> str:
    """
    숫자는 천 단위 콤마로 표시하고, 그 외는 문자열로 표시한다.
    """
    if value in ("", None):
        return "-"

    try:
        if isinstance(value, str) and not value.strip():
            return "-"
        number = float(value)
        if number.is_integer():
            return f"{int(number):,}"
        return f"{number:,.2f}"
    except Exception:
        return str(value)


def build_order_rows(orders: list[dict[str, object]]) -> list[list[str]]:
    """
    주문 목록을 표 표시용 행으로 변환한다.
    """
    rows: list[list[str]] = []

    for order in orders:
        order_time = order_value(
            order,
            ["order_time", "time", "created_at", "timestamp", "주문시간"],
            "-",
        )
        order_no = order_value(
            order,
            ["order_no", "order_id", "order_number", "주문번호"],
            "-",
        )
        side = order_side_display(
            order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-")
        )
        order_qty = format_number_value(
            order_value(order, ["order_qty", "qty", "quantity", "주문수량"], 0)
        )
        filled_qty = format_number_value(
            order_value(order, ["filled_qty", "filled", "체결수량"], 0)
        )
        pending_qty = format_number_value(
            order_value(order, ["pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"], 0)
        )
        price = format_number_value(
            order_value(order, ["price", "order_price", "주문가격"], "")
        )
        filled_price = format_number_value(
            order_value(order, ["filled_price", "avg_filled_price", "체결가격"], "")
        )
        status = order_status_display(
            order_value(order, ["status", "order_status", "상태"], "-")
        )

        fee = format_number_value(order_total_fee(order))

        rows.append([
            str(order_time),
            str(order_no),
            side,
            order_qty,
            filled_qty,
            pending_qty,
            price,
            filled_price,
            fee,
            status,
        ])

    return rows


def build_order_timeline_text(orders: list[dict[str, object]], limit: int = 20) -> str:
    """
    주문 목록을 간단한 매매 타임라인 텍스트로 변환한다.
    """
    if not orders:
        return "주문 내역 없음"

    lines: list[str] = []
    for order in orders[-limit:]:
        order_time = str(order_value(order, ["order_time", "time", "created_at", "timestamp", "주문시간"], "-"))
        side = order_side_display(order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-"))
        qty = format_number_value(order_value(order, ["order_qty", "qty", "quantity", "주문수량"], 0))
        price = format_number_value(order_value(order, ["filled_price", "avg_filled_price", "price", "order_price", "체결가격", "주문가격"], ""))
        status = order_status_display(order_value(order, ["status", "order_status", "상태"], "-"))
        lines.append(f"{order_time} | {side} | {qty}주 | {price} | {status}")

    if len(orders) > limit:
        lines.insert(0, f"... 최근 {limit}건 표시 / 전체 {len(orders)}건")

    return "\n".join(lines)


def parse_order_datetime_value(value: object) -> datetime | None:
    """
    주문시간 문자열을 datetime 으로 변환한다.
    """
    if value in ("", None):
        return None

    text_value = str(value).strip()
    if not text_value or text_value == "-":
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y%m%d%H%M%S",
        "%Y%m%d %H%M%S",
        "%H:%M:%S",
        "%H:%M",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(text_value, fmt)
            if fmt in ("%H:%M:%S", "%H:%M"):
                today = date.today()
                return datetime.combine(today, parsed.time())
            return parsed
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text_value)
        return parsed
    except ValueError:
        return None


def order_datetime(order: dict[str, object]) -> datetime | None:
    raw_time = order_value(
        order,
        ["order_time", "time", "created_at", "timestamp", "주문시간"],
        "",
    )
    return parse_order_datetime_value(raw_time)


def filter_orders_by_range(
    orders: list[dict[str, object]],
    filter_mode: str,
) -> list[dict[str, object]]:
    """
    주문 목록을 오늘/최근 7일/전체 기준으로 필터링한다.
    """
    if filter_mode == "전체":
        return orders

    today = date.today()

    if filter_mode == "오늘":
        return [
            order for order in orders
            if order_datetime(order) is not None and order_datetime(order).date() == today
        ]

    if filter_mode == "최근 7일":
        start_date = today - timedelta(days=6)
        return [
            order for order in orders
            if order_datetime(order) is not None and start_date <= order_datetime(order).date() <= today
        ]

    return orders


def order_sort_key(order: dict[str, object]) -> tuple[int, str]:
    parsed = order_datetime(order)
    if parsed is None:
        return (1, str(order_value(order, ["order_time", "time", "created_at", "timestamp", "주문시간"], "")))
    return (0, parsed.isoformat())


def build_grouped_order_timeline_text(
    orders: list[dict[str, object]],
    limit: int = 200,
) -> str:
    """
    주문 목록을 날짜별 그룹 타임라인으로 변환한다.
    날짜 그룹 끝에는 단순 결산을 표시한다.
    """
    if not orders:
        return "주문 내역 없음"

    sorted_orders = sorted(orders, key=order_sort_key)
    visible_orders = sorted_orders[-limit:]

    grouped: dict[str, list[dict[str, object]]] = {}
    for order in visible_orders:
        parsed = order_datetime(order)
        group_key = parsed.strftime("%Y-%m-%d") if parsed is not None else "날짜 없음"
        grouped.setdefault(group_key, []).append(order)

    lines: list[str] = []
    if len(sorted_orders) > limit:
        lines.append(f"... 최근 {limit}건 표시 / 선택 범위 {len(sorted_orders)}건")
        lines.append("")

    for group_key in sorted(grouped.keys(), reverse=True):
        group_orders = sorted(grouped[group_key], key=order_sort_key)
        lines.append(f"[{group_key}]")
        for order in group_orders:
            parsed = order_datetime(order)
            raw_time = str(order_value(order, ["order_time", "time", "created_at", "timestamp", "주문시간"], "-"))
            display_time = parsed.strftime("%H:%M:%S") if parsed is not None else raw_time
            order_no = str(order_value(order, ["order_no", "order_id", "order_number", "주문번호"], "-"))
            side = order_side_display(order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-"))
            qty = format_number_value(order_value(order, ["order_qty", "qty", "quantity", "주문수량"], 0))
            price = format_number_value(
                order_value(
                    order,
                    ["filled_price", "avg_filled_price", "price", "order_price", "체결가격", "주문가격"],
                    "",
                )
            )
            status = order_status_display(order_value(order, ["status", "order_status", "상태"], "-"))
            lines.append(f"{display_time} | {order_no} | {side} | {qty}주 | {price} | {status}")

        lines.append(daily_settlement_line(group_orders))
        lines.append("")

    return "\n".join(lines).rstrip()


def numeric_order_value(
    order: dict[str, object],
    keys: list[str],
    default: float = 0.0,
) -> float:
    value = order_value(order, keys, default)

    if value in ("", None, "-"):
        return default

    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def order_total_fee(order: dict[str, object]) -> float:
    explicit_total = order_value(order, ["total_fee", "fee_total", "총비용"], None)
    if explicit_total not in (None, ""):
        return numeric_order_value(order, ["total_fee", "fee_total", "총비용"], 0.0)

    commission = numeric_order_value(order, ["commission", "수수료"], 0.0)
    tax = numeric_order_value(order, ["tax", "거래세", "세금"], 0.0)
    other_fee = numeric_order_value(order, ["other_fee", "기타비용"], 0.0)
    return commission + tax + other_fee


def order_filled_amount(order: dict[str, object]) -> float:
    explicit_amount = order_value(
        order,
        ["gross_amount", "filled_amount", "체결금액"],
        None,
    )
    if explicit_amount not in (None, ""):
        return numeric_order_value(order, ["gross_amount", "filled_amount", "체결금액"], 0.0)

    filled_qty = numeric_order_value(order, ["filled_qty", "filled", "체결수량"], 0.0)
    filled_price = numeric_order_value(
        order,
        ["filled_price", "avg_filled_price", "체결가격"],
        0.0,
    )

    if filled_qty > 0 and filled_price > 0:
        return filled_qty * filled_price

    order_qty = numeric_order_value(order, ["order_qty", "qty", "quantity", "주문수량"], 0.0)
    order_price = numeric_order_value(order, ["price", "order_price", "주문가격"], 0.0)
    return order_qty * order_price


def calculate_fifo_realized_pnl(orders: list[dict[str, object]]) -> float:
    """
    주문 목록 기준 실현손익을 FIFO 방식으로 계산한다.

    1차 기준:
    - 매수 체결은 보유 lot 으로 적재
    - 매도 체결은 가장 오래된 매수 lot 부터 차감
    - 전체 주문 비용(total_fee 또는 commission/tax/other_fee)은 손익에서 차감
    - 매수 원가 이월 정보가 없는 경우, 주문 목록 안에서 확인 가능한 lot 기준으로만 계산한다.
    """
    lots: list[list[float]] = []
    gross_pnl = 0.0
    fee_total = 0.0

    for order in sorted(orders, key=order_sort_key):
        side = order_side_display(
            order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-")
        )
        filled_qty = numeric_order_value(order, ["filled_qty", "filled", "체결수량"], 0.0)
        filled_price = numeric_order_value(
            order,
            ["filled_price", "avg_filled_price", "체결가격"],
            0.0,
        )

        fee_total += order_total_fee(order)

        if filled_qty <= 0 or filled_price <= 0:
            continue

        if side == "매수":
            lots.append([filled_qty, filled_price])
            continue

        if side != "매도":
            continue

        sell_remaining = filled_qty

        while sell_remaining > 0 and lots:
            lot_qty, lot_price = lots[0]
            matched_qty = min(sell_remaining, lot_qty)

            gross_pnl += (filled_price - lot_price) * matched_qty

            lot_qty -= matched_qty
            sell_remaining -= matched_qty

            if lot_qty <= 0:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty

        # 주문 목록 안에 매수 원가가 없는 초과 매도 물량은 손익 계산에서 제외한다.
        # 실제 자동매매에서는 전일 이월 보유 원가를 state/history에서 보강해야 한다.

    return gross_pnl - fee_total


def summarize_orders(orders: list[dict[str, object]]) -> dict[str, float]:
    summary = {
        "buy_qty": 0.0,
        "sell_qty": 0.0,
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "pending_qty": 0.0,
        "fee_total": 0.0,
        "realized_pnl": 0.0,
    }

    for order in orders:
        side = order_side_display(
            order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-")
        )
        filled_qty = numeric_order_value(order, ["filled_qty", "filled", "체결수량"], 0.0)
        pending_qty = numeric_order_value(
            order,
            ["pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"],
            0.0,
        )
        amount = order_filled_amount(order)
        fee = order_total_fee(order)

        if side == "매수":
            summary["buy_qty"] += filled_qty
            summary["buy_amount"] += amount
        elif side == "매도":
            summary["sell_qty"] += filled_qty
            summary["sell_amount"] += amount

        summary["pending_qty"] += pending_qty
        summary["fee_total"] += fee

    summary["realized_pnl"] = calculate_fifo_realized_pnl(orders)

    return summary


def format_quantity(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_money(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_signed_money(value: float) -> str:
    if value > 0:
        return f"+{format_money(value)}"
    if value < 0:
        return f"-{format_money(abs(value))}"
    return "0"


def settlement_summary_text(orders: list[dict[str, object]]) -> str:
    summary = summarize_orders(orders)

    return (
        f"매수 {format_quantity(summary['buy_qty'])}주 / "
        f"매도 {format_quantity(summary['sell_qty'])}주 / "
        f"미체결 {format_quantity(summary['pending_qty'])}주 / "
        f"비용 {format_money(summary['fee_total'])} / "
        f"손익 {format_signed_money(summary['realized_pnl'])}"
    )


def daily_settlement_line(orders: list[dict[str, object]]) -> str:
    summary = summarize_orders(orders)

    return (
        "결산 | "
        f"매수 {format_quantity(summary['buy_qty'])}주 / "
        f"매도 {format_quantity(summary['sell_qty'])}주 / "
        f"미체결 {format_quantity(summary['pending_qty'])}주 / "
        f"비용 {format_money(summary['fee_total'])} / "
        f"손익 {format_signed_money(summary['realized_pnl'])}"
    )


def date_range_for_mode(filter_mode: str) -> tuple[date | None, date | None]:
    today = date.today()

    if filter_mode == "이번주":
        start_date = today - timedelta(days=today.weekday())
        return start_date, today

    if filter_mode == "이번달":
        return today.replace(day=1), today

    if filter_mode == "3개월":
        return today - timedelta(days=89), today

    return None, None


def filter_orders_by_dates(
    orders: list[dict[str, object]],
    start_date: date | None,
    end_date: date | None,
) -> list[dict[str, object]]:
    if start_date is None or end_date is None:
        return orders

    result: list[dict[str, object]] = []
    for order in orders:
        parsed = order_datetime(order)
        if parsed is None:
            continue

        order_date = parsed.date()
        if start_date <= order_date <= end_date:
            result.append(order)

    return result


def today_orders(orders: list[dict[str, object]]) -> list[dict[str, object]]:
    return filter_orders_by_dates(orders, date.today(), date.today())


def build_current_status_rows(orders: list[dict[str, object]]) -> list[list[str]]:
    rows: list[list[str]] = []

    for order in sorted(orders, key=order_sort_key):
        parsed = order_datetime(order)
        order_time = parsed.strftime("%H:%M:%S") if parsed is not None else str(
            order_value(order, ["order_time", "time", "created_at", "timestamp", "주문시간"], "-")
        )
        side = order_side_display(
            order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-")
        )
        order_qty = format_number_value(order_value(order, ["order_qty", "qty", "quantity", "주문수량"], 0))
        filled_qty = format_number_value(order_value(order, ["filled_qty", "filled", "체결수량"], 0))
        pending_qty = format_number_value(order_value(order, ["pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"], 0))
        price = format_number_value(order_value(order, ["price", "order_price", "주문가격"], ""))
        filled_price = format_number_value(order_value(order, ["filled_price", "avg_filled_price", "체결가격"], ""))
        fee = format_number_value(order_total_fee(order))
        status = order_status_display(order_value(order, ["status", "order_status", "상태"], "-"))

        rows.append([order_time, side, order_qty, filled_qty, pending_qty, price, filled_price, fee, status])

    return rows


def build_full_trade_export_text(
    orders: list[dict[str, object]],
    routine_name: str,
    stock_code: str,
    stock_name: str,
    orders_path: Path,
) -> str:
    sorted_orders = sorted(orders, key=order_sort_key)
    grouped: dict[str, list[dict[str, object]]] = {}

    for order in sorted_orders:
        parsed = order_datetime(order)
        group_key = parsed.strftime("%Y-%m-%d") if parsed is not None else "날짜 없음"
        grouped.setdefault(group_key, []).append(order)

    lines: list[str] = []
    lines.append("매매거래 전체 내역")
    lines.append("")
    lines.append(f"루틴: {routine_name}")
    lines.append(f"종목: {stock_code} {stock_name}")
    lines.append(f"원본파일: {orders_path}")
    lines.append(f"저장시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"전체 주문건수: {len(sorted_orders)}건")
    lines.append("")
    lines.append(f"전체 결산: {settlement_summary_text(sorted_orders)}")
    lines.append("")

    header = (
        "주문시간\t주문번호\t구분\t주문수량\t체결수량\t미체결수량\t"
        "주문가격\t체결가격\t수수료\t거래세\t기타비용\t총비용\t상태"
    )

    for group_key in sorted(grouped.keys(), reverse=True):
        group_orders = sorted(grouped[group_key], key=order_sort_key)
        lines.append(f"[{group_key}]")
        lines.append(header)

        for order in group_orders:
            order_time = str(order_value(order, ["order_time", "time", "created_at", "timestamp", "주문시간"], "-"))
            order_no = str(order_value(order, ["order_no", "order_id", "order_number", "주문번호"], "-"))
            side = order_side_display(order_value(order, ["side", "order_type", "type", "구분", "주문구분"], "-"))
            order_qty = format_number_value(order_value(order, ["order_qty", "qty", "quantity", "주문수량"], 0))
            filled_qty = format_number_value(order_value(order, ["filled_qty", "filled", "체결수량"], 0))
            pending_qty = format_number_value(order_value(order, ["pending_qty", "remaining_qty", "unfilled_qty", "미체결수량"], 0))
            price = format_number_value(order_value(order, ["price", "order_price", "주문가격"], ""))
            filled_price = format_number_value(order_value(order, ["filled_price", "avg_filled_price", "체결가격"], ""))
            commission = format_number_value(numeric_order_value(order, ["commission", "수수료"], 0.0))
            tax = format_number_value(numeric_order_value(order, ["tax", "거래세", "세금"], 0.0))
            other_fee = format_number_value(numeric_order_value(order, ["other_fee", "기타비용"], 0.0))
            total_fee = format_number_value(order_total_fee(order))
            status = order_status_display(order_value(order, ["status", "order_status", "상태"], "-"))

            lines.append("\t".join([
                order_time, order_no, side, order_qty, filled_qty, pending_qty,
                price, filled_price, commission, tax, other_fee, total_fee, status,
            ]))

        lines.append(daily_settlement_line(group_orders))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

