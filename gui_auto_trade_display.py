# -*- coding: utf-8 -*-
"""
gui_auto_trade_display.py

자동매매 설정/관제 표시 전용 유틸리티.
- 현황 점
- 상태 표시
- 상태 색상
- 방식/청산 비활성 스타일

주의:
- 상태 변경, 청산정책 계산, ATS 판정 로직은 포함하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QColor, QFont, QFontMetrics
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QTableWidgetItem, QWidget

SORT_ROLE = Qt.UserRole + 100


class SortableTableWidgetItem(QTableWidgetItem):
    """화면 표시값과 정렬 기준값을 분리하는 표 아이템."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if other is not None else None
        if left is not None and right is not None:
            try:
                return left < right
            except Exception:
                return str(left) < str(right)
        return self.text() < (other.text() if other is not None else "")


ROUTINE_PROFIT_SIGNAL_COLORS = {
    "LOSS": "#dc2626",
    "COST_NOT_RECOVERED": "#d97706",
    "NET_PROFIT": "#16a34a",
    "NEUTRAL": "#9ca3af",
}


def _format_plain_number(value: object) -> str:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return "-"

    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _format_signed_amount(value: float) -> str:
    rounded = int(round(value))
    if rounded == 0:
        return "0"
    return f"{rounded:+,}"


def _format_signed_rate(value: float) -> str:
    if abs(value) < 0.005:
        return "0.00%"
    return f"{value:+.2f}%"


STOCK_POSITION_METRIC_SAMPLES = {
    "보유": ("99999주", "999,999,999"),
    "가격": ("9,999,999", "9,999,999"),
    "손익": ("-99,999,999", "-00.00%"),
    "미체결": ("99", "99"),
}

@dataclass(frozen=True)
class RatioMetricDisplay:
    """label(value1 / value2) 형태의 공통 표시 단위."""

    label: str
    value1: str
    value2: str
    value1_sample: str
    value2_sample: str
    value1_width: int | None = None
    value2_width: int | None = None
    value1_alignment: object = Qt.AlignRight | Qt.AlignVCenter
    value2_alignment: object = Qt.AlignRight | Qt.AlignVCenter
    force_slot_render: bool = False


@dataclass(frozen=True)
class RatioMetricLayout:
    prefix_width: int
    label_width: int
    value1_width: int
    slash_width: int
    value2_width: int
    close_width: int
    total_width: int


def ratio_metric_text(metric: RatioMetricDisplay, *, prefix: str = "") -> str:
    return f"{prefix}{metric.label}({metric.value1} / {metric.value2})"


def stock_position_value_text(metric: RatioMetricDisplay, *, prefix: str = "") -> str:
    return f"{prefix}{metric.value1} / {metric.value2}"


def _split_stock_position_metric(
    text: object,
    *,
    label_hint: str | None = None,
) -> tuple[str, str, str, bool] | None:
    value = str(text or "").strip()
    has_separator = False
    if value.startswith("| "):
        has_separator = True
        value = value[2:].strip()

    for label in STOCK_POSITION_METRIC_SAMPLES:
        prefix = f"{label}("
        if value.startswith(prefix) and value.endswith(")"):
            inner = value[len(prefix) : -1]
            if " / " not in inner:
                return None
            left_value, right_value = inner.split(" / ", 1)
            return label, left_value, right_value, has_separator
    if label_hint in STOCK_POSITION_METRIC_SAMPLES and " / " in value:
        left_value, right_value = value.split(" / ", 1)
        return label_hint, left_value, right_value, has_separator
    return None


def default_stock_position_metric_value_widths(font: QFont | None = None) -> dict[str, tuple[int, int]]:
    metrics = QFontMetrics(font or QFont())
    widths: dict[str, tuple[int, int]] = {}
    for label, samples in STOCK_POSITION_METRIC_SAMPLES.items():
        left_sample, right_sample = samples
        widths[label] = (
            max(metrics.horizontalAdvance(left_sample), metrics.horizontalAdvance("-")),
            max(metrics.horizontalAdvance(right_sample), metrics.horizontalAdvance("-")),
        )
    return widths


def split_wrapped_metric_text(text: object, label: str) -> str:
    value = str(text or "").strip()
    prefix = f"{label}("
    if value.startswith(prefix) and value.endswith(")"):
        return value[len(prefix) : -1]
    return value


def split_ratio_metric_text(text: object, label: str) -> tuple[str, str]:
    inner = split_wrapped_metric_text(text, label)
    if " / " not in inner:
        return inner, ""
    left_value, right_value = inner.split(" / ", 1)
    return left_value, right_value


def ratio_metric_width(
    *,
    label: str,
    left_width: int,
    right_width: int,
    font: QFont | None = None,
    outer_padding: int = 0,
) -> int:
    metrics = QFontMetrics(font or QFont())
    return (
        metrics.horizontalAdvance(f"{label}(")
        + left_width
        + metrics.horizontalAdvance(" / ")
        + right_width
        + metrics.horizontalAdvance(")")
        + (outer_padding * 2)
    )


def ratio_metric_layout(
    metrics: QFontMetrics,
    metric: RatioMetricDisplay,
    *,
    prefix: str = "",
    outer_padding: int = 0,
) -> RatioMetricLayout:
    value1_width = (
        metric.value1_width
        if metric.value1_width is not None
        else max(
            metrics.horizontalAdvance(str(metric.value1_sample)),
            metrics.horizontalAdvance(str(metric.value1)),
            metrics.horizontalAdvance("-"),
        )
    )
    value2_width = (
        metric.value2_width
        if metric.value2_width is not None
        else max(
            metrics.horizontalAdvance(str(metric.value2_sample)),
            metrics.horizontalAdvance(str(metric.value2)),
            metrics.horizontalAdvance("-"),
        )
    )
    prefix_width = metrics.horizontalAdvance(prefix)
    label_width = metrics.horizontalAdvance(f"{metric.label}(")
    slash_width = metrics.horizontalAdvance(" / ")
    close_width = metrics.horizontalAdvance(")")
    total_width = (
        prefix_width
        + label_width
        + value1_width
        + slash_width
        + value2_width
        + close_width
        + (outer_padding * 2)
    )
    return RatioMetricLayout(
        prefix_width=prefix_width,
        label_width=label_width,
        value1_width=value1_width,
        slash_width=slash_width,
        value2_width=value2_width,
        close_width=close_width,
        total_width=total_width,
    )


def ratio_metric_display_width(
    metric: RatioMetricDisplay,
    *,
    font: QFont | None = None,
    prefix: str = "",
    outer_padding: int = 0,
) -> int:
    return ratio_metric_layout(
        QFontMetrics(font or QFont()),
        metric,
        prefix=prefix,
        outer_padding=outer_padding,
    ).total_width


def stock_position_metric_width(
    *,
    label: str,
    value_widths: dict[str, tuple[int, int]],
    font: QFont | None = None,
    outer_padding: int = 0,
) -> int:
    left_width, right_width = value_widths[label]
    return ratio_metric_width(
        label=label,
        left_width=left_width,
        right_width=right_width,
        font=font,
        outer_padding=outer_padding,
    )


def draw_ratio_metric_display(
    painter,
    rect,
    metric: RatioMetricDisplay,
    color=None,
    *,
    prefix: str = "",
    outer_padding: int = 0,
    show_label: bool = True,
) -> None:
    metrics = painter.fontMetrics()
    layout = ratio_metric_layout(
        metrics,
        metric,
        prefix=prefix,
        outer_padding=outer_padding,
    )
    draw_rect = rect.adjusted(outer_padding, 0, -outer_padding, 0)
    painter.save()
    if color is not None:
        painter.setPen(QColor(color))
    visible_label_width = layout.label_width if show_label else 0
    visible_close_width = layout.close_width if show_label else 0
    visible_total_width = (
        layout.prefix_width
        + visible_label_width
        + layout.value1_width
        + layout.slash_width
        + layout.value2_width
        + visible_close_width
    )
    display_text = (
        ratio_metric_text(metric, prefix=prefix)
        if show_label
        else stock_position_value_text(metric, prefix=prefix)
    )
    if not metric.force_slot_render and visible_total_width > draw_rect.width():
        painter.drawText(
            draw_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(
                display_text,
                Qt.ElideRight,
                draw_rect.width(),
            ),
        )
        painter.restore()
        return

    x = draw_rect.left()
    painter.drawText(x, draw_rect.top(), layout.prefix_width, draw_rect.height(), Qt.AlignLeft | Qt.AlignVCenter, prefix)
    x += layout.prefix_width
    if show_label:
        painter.drawText(x, draw_rect.top(), layout.label_width, draw_rect.height(), Qt.AlignLeft | Qt.AlignVCenter, f"{metric.label}(")
        x += layout.label_width
    painter.drawText(x, draw_rect.top(), layout.value1_width, draw_rect.height(), metric.value1_alignment, metric.value1)
    x += layout.value1_width
    painter.drawText(x, draw_rect.top(), layout.slash_width, draw_rect.height(), Qt.AlignCenter, " / ")
    x += layout.slash_width
    painter.drawText(x, draw_rect.top(), layout.value2_width, draw_rect.height(), metric.value2_alignment, metric.value2)
    x += layout.value2_width
    if show_label:
        painter.drawText(x, draw_rect.top(), layout.close_width, draw_rect.height(), Qt.AlignLeft | Qt.AlignVCenter, ")")
    painter.restore()


def draw_ratio_metric(
    painter,
    rect,
    *,
    label: str,
    left_value: str,
    right_value: str,
    left_width: int,
    right_width: int,
    color=None,
    prefix: str = "",
    outer_padding: int = 0,
) -> None:
    draw_ratio_metric_display(
        painter,
        rect,
        RatioMetricDisplay(
            label=label,
            value1=left_value,
            value2=right_value,
            value1_sample=left_value,
            value2_sample=right_value,
            value1_width=left_width,
            value2_width=right_width,
            force_slot_render=(label == "가격"),
        ),
        color=color,
        prefix=prefix,
        outer_padding=outer_padding,
    )


def draw_stock_position_metric(
    painter,
    rect,
    text: object,
    color=None,
    *,
    value_widths: dict[str, tuple[int, int]] | None = None,
    outer_padding: int = 0,
    label_hint: str | None = None,
    compact: bool = False,
    compact_margins: tuple[int, int] | None = None,
) -> bool:
    """종목 관제 묶음 텍스트의 숫자 슬롯을 우측 정렬해 그린다."""
    parsed = _split_stock_position_metric(text, label_hint=label_hint)
    if parsed is None:
        return False

    label, left_value, right_value, has_separator = parsed
    if compact and not has_separator:
        widths = value_widths or default_stock_position_metric_value_widths(painter.font())
        left_width, right_width = widths[label]
        slash_width = painter.fontMetrics().horizontalAdvance(" / ")
        left_margin, right_margin = compact_margins or (2, 2)
        left_rect, slash_rect, right_rect = compact_stock_position_metric_rects(
            rect,
            left_width,
            slash_width,
            right_width,
            left_margin=left_margin,
            right_margin=right_margin,
            outer_padding=outer_padding,
        )
        painter.save()
        if color is not None:
            painter.setPen(QColor(color))
        left_alignment = (
            Qt.AlignCenter | Qt.AlignVCenter
            if label == "가격" and left_value == "-"
            else Qt.AlignRight | Qt.AlignVCenter
        )
        right_alignment = (
            Qt.AlignCenter | Qt.AlignVCenter
            if label == "가격" and right_value == "-"
            else Qt.AlignRight | Qt.AlignVCenter
        )
        painter.drawText(left_rect, left_alignment, left_value)
        painter.drawText(slash_rect, Qt.AlignCenter | Qt.AlignVCenter, " / ")
        painter.drawText(right_rect, right_alignment, right_value)
        painter.restore()
        return True

    widths = value_widths or default_stock_position_metric_value_widths(painter.font())
    left_width, right_width = widths[label]
    draw_stock_position_metric_values(
        painter,
        rect,
        label=label,
        left_value=left_value,
        right_value=right_value,
        left_width=left_width,
        right_width=right_width,
        color=color,
        prefix="| " if has_separator else "",
        outer_padding=outer_padding,
    )
    return True


def compact_stock_position_metric_rects(
    rect,
    left_width: int,
    slash_width: int,
    right_width: int,
    *,
    left_margin: int = 2,
    right_margin: int = 2,
    outer_padding: int = 0,
) -> tuple[QRect, QRect, QRect]:
    draw_rect = rect.adjusted(outer_padding, 0, -outer_padding, 0)
    x = draw_rect.left() + left_margin
    left_rect = QRect(x, draw_rect.top(), left_width, draw_rect.height())
    x += left_width
    slash_rect = QRect(x, draw_rect.top(), slash_width, draw_rect.height())
    x += slash_width
    right_rect = QRect(x, draw_rect.top(), right_width, draw_rect.height())
    return left_rect, slash_rect, right_rect


def draw_stock_position_metric_values(
    painter,
    rect,
    *,
    label: str,
    left_value: str,
    right_value: str,
    left_width: int,
    right_width: int,
    color=None,
    prefix: str = "",
    outer_padding: int = 0,
) -> None:
    draw_ratio_metric_display(
        painter,
        rect,
        RatioMetricDisplay(
            label=label,
            value1=left_value,
            value2=right_value,
            value1_sample=left_value,
            value2_sample=right_value,
            value1_width=left_width,
            value2_width=right_width,
            force_slot_render=(label == "가격"),
            value1_alignment=(
                Qt.AlignCenter | Qt.AlignVCenter
                if left_value == "-"
                else Qt.AlignRight | Qt.AlignVCenter
            ),
            value2_alignment=(
                Qt.AlignCenter | Qt.AlignVCenter
                if right_value == "-"
                else Qt.AlignRight | Qt.AlignVCenter
            ),
        ),
        color=color,
        prefix=prefix,
        outer_padding=outer_padding,
        show_label=False,
    )


def draw_stock_position_metric_display(
    painter,
    rect,
    metric: RatioMetricDisplay,
    color=None,
    *,
    outer_padding: int = 0,
) -> bool:
    if not isinstance(metric, RatioMetricDisplay):
        return False
    draw_ratio_metric_display(
        painter,
        rect,
        metric,
        color=color,
        outer_padding=outer_padding,
        show_label=False,
    )
    return True


def draw_limit_metric(
    painter,
    rect,
    text: object,
    *,
    value_width: int,
    color=None,
    outer_padding: int = 0,
    hide_value: bool = False,
) -> bool:
    value = split_wrapped_metric_text(text, "한도")
    if value == str(text or "").strip():
        return False

    metrics = painter.fontMetrics()
    draw_rect = rect.adjusted(outer_padding, 0, -outer_padding, 0)
    label_width = metrics.horizontalAdvance("한도(")
    close_width = metrics.horizontalAdvance(")")
    total_width = label_width + value_width + close_width
    painter.save()
    if color is not None:
        painter.setPen(QColor(color))
    if total_width > draw_rect.width():
        painter.drawText(
            draw_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(str(text or ""), Qt.ElideRight, draw_rect.width()),
        )
        painter.restore()
        return True

    value_alignment = (
        Qt.AlignCenter | Qt.AlignVCenter
        if value == "미설정"
        else Qt.AlignRight | Qt.AlignVCenter
    )
    x = draw_rect.left()
    painter.drawText(x, draw_rect.top(), label_width, draw_rect.height(), Qt.AlignLeft | Qt.AlignVCenter, "한도(")
    x += label_width
    if not hide_value:
        painter.drawText(x, draw_rect.top(), value_width, draw_rect.height(), value_alignment, value)
    x += value_width
    painter.drawText(x, draw_rect.top(), close_width, draw_rect.height(), Qt.AlignLeft | Qt.AlignVCenter, ")")
    painter.restore()
    return True


def stock_position_metric_values(
    *,
    holding_qty: object,
    avg_price: object,
    current_price: object = None,
    buy_pending_qty: object = 0,
    sell_pending_qty: object = 0,
) -> tuple[RatioMetricDisplay, RatioMetricDisplay, RatioMetricDisplay, RatioMetricDisplay, float, float]:
    try:
        holding_value = int(float(str(holding_qty).replace(",", "").strip()))
    except (TypeError, ValueError):
        holding_value = 0

    try:
        avg_value = float(str(avg_price).replace(",", "").strip())
    except (TypeError, ValueError):
        avg_value = 0.0

    try:
        current_value = (
            None
            if current_price in (None, "", "-")
            else float(str(current_price).replace(",", "").strip())
        )
    except (TypeError, ValueError):
        current_value = None

    try:
        buy_pending_value = int(float(str(buy_pending_qty).replace(",", "").strip()))
    except (TypeError, ValueError):
        buy_pending_value = 0
    try:
        sell_pending_value = int(float(str(sell_pending_qty).replace(",", "").strip()))
    except (TypeError, ValueError):
        sell_pending_value = 0

    total_buy_amount = int(round(holding_value * avg_value)) if holding_value > 0 and avg_value > 0 else 0
    avg_text = _format_plain_number(avg_value) if avg_value > 0 else "-"
    current_text = _format_plain_number(current_value) if current_value is not None else "-"

    profit_amount = 0.0
    profit_rate = 0.0
    if holding_value > 0 and avg_value > 0 and current_value is not None:
        cost_basis = holding_value * avg_value
        profit_amount = (current_value - avg_value) * holding_value
        if cost_basis > 0:
            profit_rate = (profit_amount / cost_basis) * 100.0

    holding_metric = RatioMetricDisplay(
        label="보유",
        value1=f"{holding_value:,}주",
        value2=f"{total_buy_amount:,}",
        value1_sample=STOCK_POSITION_METRIC_SAMPLES["보유"][0],
        value2_sample=STOCK_POSITION_METRIC_SAMPLES["보유"][1],
    )
    price_metric = RatioMetricDisplay(
        label="가격",
        value1=avg_text,
        value2=current_text,
        value1_sample=STOCK_POSITION_METRIC_SAMPLES["가격"][0],
        value2_sample=STOCK_POSITION_METRIC_SAMPLES["가격"][1],
        force_slot_render=True,
        value1_alignment=(
            Qt.AlignCenter | Qt.AlignVCenter
            if avg_text == "-"
            else Qt.AlignRight | Qt.AlignVCenter
        ),
        value2_alignment=(
            Qt.AlignCenter | Qt.AlignVCenter
            if current_text == "-"
            else Qt.AlignRight | Qt.AlignVCenter
        ),
    )
    profit_metric = RatioMetricDisplay(
        label="손익",
        value1=_format_signed_amount(profit_amount),
        value2=_format_signed_rate(profit_rate),
        value1_sample=STOCK_POSITION_METRIC_SAMPLES["손익"][0],
        value2_sample=STOCK_POSITION_METRIC_SAMPLES["손익"][1],
    )
    pending_metric = RatioMetricDisplay(
        label="미체결",
        value1=f"{buy_pending_value:,}",
        value2=f"{sell_pending_value:,}",
        value1_sample=STOCK_POSITION_METRIC_SAMPLES["미체결"][0],
        value2_sample=STOCK_POSITION_METRIC_SAMPLES["미체결"][1],
    )
    return holding_metric, price_metric, profit_metric, pending_metric, profit_amount, profit_rate


def stock_position_display_values(
    *,
    holding_qty: object,
    avg_price: object,
    current_price: object = None,
    buy_pending_qty: object = 0,
    sell_pending_qty: object = 0,
    include_separator: bool = False,
) -> tuple[str, str, str, str, float, float]:
    """종목 관제용 보유/가격/손익/미체결 묶음 문자열을 만든다."""
    holding_metric, price_metric, profit_metric, pending_metric, profit_amount, profit_rate = (
        stock_position_metric_values(
            holding_qty=holding_qty,
            avg_price=avg_price,
            current_price=current_price,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
        )
    )
    prefix = "| " if include_separator else ""
    holding_text = stock_position_value_text(holding_metric, prefix=prefix)
    price_text = stock_position_value_text(price_metric, prefix=prefix)
    profit_text = stock_position_value_text(profit_metric, prefix=prefix)
    pending_text = stock_position_value_text(pending_metric, prefix=prefix)
    return holding_text, price_text, profit_text, pending_text, profit_amount, profit_rate


def format_routine_buy_limit(
    *,
    enabled: bool,
    amount: object = None,
) -> str:
    """루틴 매수한도를 독립 금액 셀용 문자열로 만든다."""
    if not enabled:
        return "-"

    try:
        limit_value = int(float(str(amount).replace(",", "").strip()))
    except (TypeError, ValueError):
        return "-"

    if limit_value <= 0:
        return "-"

    return f"₩{limit_value:,}"


def format_routine_used_amount(amount: object = None) -> str:
    """루틴의 공식 사용금액이 공급된 경우 원화 형식으로 표시한다."""
    if amount in (None, "", "-"):
        return "-"

    try:
        amount_value = int(float(str(amount).replace(",", "").strip()))
    except (TypeError, ValueError):
        return "-"

    return f"₩{amount_value:,}"


def format_routine_buy_limit_usage(
    *,
    enabled: bool,
    limit_amount: object = None,
    used_amount: object = None,
) -> str:
    """사용금액이 매수한도에서 차지하는 비율을 독립 셀로 표시한다."""
    if not enabled:
        return "-"

    try:
        limit_value = float(str(limit_amount).replace(",", "").strip())
        used_value = float(str(used_amount).replace(",", "").strip())
    except (TypeError, ValueError):
        return "-"

    if limit_value <= 0:
        return "-"

    usage_rate = (used_value / limit_value) * 100.0
    if usage_rate.is_integer():
        return f"{int(usage_rate)}%"
    return f"{usage_rate:.2f}%"


def routine_profit_signal(
    gross_rate: object = None,
    net_rate: object = None,
) -> tuple[str, str, str]:
    """수익률 표시값과 비용 반영 상태를 신호등 계약으로 변환한다."""
    try:
        gross_value = float(gross_rate)
    except (TypeError, ValueError):
        return "NEUTRAL", "-", ROUTINE_PROFIT_SIGNAL_COLORS["NEUTRAL"]

    display_text = f"{gross_value:+.2f}%" if gross_value != 0 else "0.00%"

    if gross_value < 0:
        signal = "LOSS"
    elif gross_value == 0:
        signal = "NEUTRAL"
    else:
        try:
            net_value = float(net_rate)
        except (TypeError, ValueError):
            signal = "NEUTRAL"
        else:
            signal = "NET_PROFIT" if net_value > 0 else "COST_NOT_RECOVERED"

    return signal, display_text, ROUTINE_PROFIT_SIGNAL_COLORS[signal]


def create_routine_profit_signal_widget(
    gross_rate: object = None,
    net_rate: object = None,
) -> QWidget:
    """숫자 글자색은 유지하고 원형 점에만 신호색을 적용한다."""
    signal, display_text, color = routine_profit_signal(gross_rate, net_rate)

    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(4, 0, 4, 0)
    layout.setSpacing(5)
    layout.setAlignment(Qt.AlignCenter)

    value_label = QLabel(display_text)
    value_label.setObjectName("routineProfitSignalValue")

    if display_text != "-":
        dot_label = QLabel("●")
        dot_label.setObjectName("routineProfitSignalDot")
        dot_label.setAlignment(Qt.AlignCenter)
        dot_label.setStyleSheet(f"color: {color}; font-size: 11pt;")
        dot_label.setProperty("signal", signal)
        layout.addWidget(dot_label)
    layout.addWidget(value_label)
    return widget


from state_policy import (
    auto_trade_status_color,
    auto_trade_status_display,
    auto_trade_status_dot,
)


def create_auto_trade_status_item(display_status: str) -> QTableWidgetItem:
    """
    상태 컬럼 표시용 아이템.
    내부 상태코드는 GUI 표시명으로 변환해 보여준다.
    SELL_ONLY도 화면에서는 감시/매도로 표시한다.
    """
    normalized_status = display_status_text_for_gui(display_status)

    item = SortableTableWidgetItem(f"{auto_trade_status_dot(normalized_status)} {normalized_status}")
    item.setToolTip(normalized_status)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setForeground(QColor(auto_trade_status_color(normalized_status)))
    return item


def auto_trade_setting_display_status(display_status: str) -> str:
    """자동매매설정창 표시용 상태명.

    이 창은 운영 가능 종목의 설정/현황을 보는 곳이므로
    기존 감시/매도 표시를 운영자 기준의 자동마감으로 보여준다.
    """
    normalized = display_status_text_for_gui(display_status)
    if normalized == "감시/매도":
        return "자동마감"
    return normalized


def auto_trade_setting_status_color(display_status: str) -> str:
    """자동매매설정창 상태 컬럼 색상."""
    normalized = str(display_status or "").strip()
    color_map = {
        "감시/대기": "#2563eb",
        "매수/매도": "#16a34a",
        "자동마감": "#7c3aed",
        "조기마감": "#ea580c",
    }
    return color_map.get(normalized, auto_trade_status_color(normalized))


def create_auto_trade_setting_status_item(display_status: str) -> QTableWidgetItem:
    """자동매매설정창 전용 상태 아이템. 상태 컬럼에는 점을 표시하지 않는다."""
    normalized = auto_trade_setting_display_status(display_status)
    item = SortableTableWidgetItem(normalized)
    item.setToolTip(normalized)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setForeground(QColor(auto_trade_setting_status_color(normalized)))
    return item




def apply_auto_trade_setting_activity_style(item: QTableWidgetItem, active: bool) -> None:
    """정보는 유지하고 현재 주도권 없는 칸만 회색으로 표시한다."""
    if active:
        item.setBackground(QColor("#FFFFFF"))
        return
    item.setBackground(QColor("#F4F5F7"))
    item.setForeground(QColor("#AFB2B9"))


def apply_auto_trade_setting_liquidation_style(
    item: QTableWidgetItem,
    active: bool,
    has_policy: bool = True,
    is_individual: bool = False,
) -> None:
    """청산정책 표시 스타일.

    - 개별 청산정책: 배경/굵게 없이 글자색만 주황
    - 환경설정 청산정책 활성: 기존 연노랑 강조
    - 환경설정 청산정책 비활성: 기존 회색
    - 청산정책 자체가 없는 종목('-'): 기본 흰색 유지
    """
    if not has_policy:
        item.setBackground(QColor("#FFFFFF"))
        return

    if is_individual:
        if active:
            item.setBackground(QColor("#FFFFFF"))
        else:
            item.setBackground(QColor("#F4F5F7"))
        item.setForeground(QColor("#D97706"))
        return

    if active:
        item.setBackground(QColor("#FFFFFF"))
        item.setForeground(QColor("#5C4300"))
        return

    item.setBackground(QColor("#F4F5F7"))
    item.setForeground(QColor("#9CA3AF"))


def yes_no_display(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"

    text_value = str(value).strip().lower()
    if text_value in ("true", "1", "yes", "y"):
        return "예"

    return "아니오"


def display_status_text_for_gui(raw_status: object) -> str:
    """GUI 표시용 상태명. state_policy 기준 6개 표시 상태로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


def routine_status_display_text(raw_status: object) -> str:
    """루틴/리포트용 운영상태 표시명을 state_policy 기준으로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


def routine_status_display_text(routine_name: str, status: str) -> str:
    """
    루틴별 상태 표시 문구를 반환한다.

    v20.9.1a:
    - 등록 루틴 컬럼에서도 감시중/운영중/매도만 등 상태 차이를 숨기지 않는다.
    - 대기 상태만 삭제보호용 표시 목적에 맞춰 등록대기로 표시한다.
    """
    normalized = str(status or "").strip()

    if normalized in ("운영", "운영중"):
        return f"{routine_name}(운영중)"

    if normalized == "대기":
        return f"{routine_name}(등록대기)"

    if normalized:
        return f"{routine_name}({normalized})"

    return f"{routine_name}(상태없음)"
