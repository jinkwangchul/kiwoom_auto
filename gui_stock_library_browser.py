# -*- coding: utf-8 -*-
"""Shared read-only projection helpers for stock-library browser dialogs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHeaderView, QStyle


STOCK_BROWSER_HEADERS = (
    "종목코드",
    "종목명",
    "시장",
    "등록상태",
    "분류",
    "비고",
    "현재주가",
    "등락률",
    "체결강도",
    "전일대비",
    "거래대금",
    "거래량",
    "시총",
    "상태",
)

CODE_COLUMN = 0
NAME_COLUMN = 1
MARKET_COLUMN = 2
REGISTRATION_STATUS_COLUMN = 3
INSTRUMENT_CLASSIFICATION_COLUMN = 4
AFTER_MARKET_COLUMN = 5
CURRENT_PRICE_COLUMN = 6
CHANGE_RATE_COLUMN = 7
EXECUTION_STRENGTH_COLUMN = 8
PREVIOUS_DAY_VOLUME_RATE_COLUMN = 9
TRADING_VALUE_COLUMN = 10
VOLUME_COLUMN = 11
MARKET_CAP_COLUMN = 12
STOCK_STATUS_COLUMN = 13

NUMERIC_SNAPSHOT_COLUMNS = frozenset(
    {
        CURRENT_PRICE_COLUMN,
        CHANGE_RATE_COLUMN,
        EXECUTION_STRENGTH_COLUMN,
        PREVIOUS_DAY_VOLUME_RATE_COLUMN,
        TRADING_VALUE_COLUMN,
        VOLUME_COLUMN,
        MARKET_CAP_COLUMN,
    }
)

RANKING_BADGES = (
    ("VOLUME_TOP", "거래량"),
    ("VALUE_TOP", "거래대금"),
    ("RISE_TOP", "급상승"),
    ("FALL_TOP", "급하락"),
)

RANKING_HIGHLIGHT_COLUMNS = {
    "VOLUME_TOP": VOLUME_COLUMN,
    "VALUE_TOP": TRADING_VALUE_COLUMN,
    "RISE_TOP": CHANGE_RATE_COLUMN,
    "FALL_TOP": CHANGE_RATE_COLUMN,
}

STOCK_BROWSER_DIALOG_HEIGHT = 420
STOCK_BROWSER_STOCK_NAME_DISPLAY_CHARACTERS = 14
STOCK_BROWSER_SEARCH_DISPLAY_CHARACTERS = 12
STOCK_BROWSER_ROW_NUMBER_HORIZONTAL_PADDING = 1
STOCK_BROWSER_BADGE_HEIGHT = 22
STOCK_BROWSER_RANKING_BADGE_HORIZONTAL_PADDING = 3
STOCK_BROWSER_STATUS_SINGLE_VALUES = (
    "정상",
    "관리",
    "관리종목",
    "거래정지",
    "증거금100%",
    "감리종목",
    "투자유의종목",
    "담보대출",
    "액면분할",
    "신용가능",
    "투자주의",
    "투자경고",
    "투자위험",
    "투자주의환기",
    "투자주의환기종목",
)


def _text_column_width(table, *samples: str) -> int:
    style = table.style()
    item_margin = style.pixelMetric(QStyle.PM_FocusFrameHMargin, None, table) + 1
    header = table.horizontalHeader()
    header_margin = style.pixelMetric(QStyle.PM_HeaderMargin, None, header)
    sort_indicator_width = style.pixelMetric(QStyle.PM_SmallIconSize, None, header)
    metrics = table.fontMetrics()
    item_width = max(metrics.horizontalAdvance(sample) for sample in samples) + (
        item_margin * 2
    )
    header_width = (
        metrics.horizontalAdvance(samples[0])
        + (header_margin * 2)
        + sort_indicator_width
    )
    return max(item_width, header_width)


def _symmetric_text_column_width(table, *samples: str) -> int:
    item_margin = (
        table.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None, table) + 1
    )
    section_border_width = (
        table.style().pixelMetric(QStyle.PM_DefaultFrameWidth, None, table)
        if table.showGrid()
        else 0
    )
    text_width = max(table.fontMetrics().horizontalAdvance(sample) for sample in samples)
    return text_width + (item_margin * 2) + section_border_width


def _text_column_width_with_margin(
    table,
    horizontal_margin: int,
    *samples: str,
) -> int:
    section_border_width = (
        table.style().pixelMetric(QStyle.PM_DefaultFrameWidth, None, table)
        if table.showGrid()
        else 0
    )
    text_width = max(table.fontMetrics().horizontalAdvance(sample) for sample in samples)
    return text_width + (max(0, horizontal_margin) * 2) + section_border_width


def configure_stock_browser_table_geometry(table) -> None:
    """Apply the shared fixed-column, scrollbar, and row-number geometry."""
    table.setColumnCount(len(STOCK_BROWSER_HEADERS))
    table.setHorizontalHeaderLabels(STOCK_BROWSER_HEADERS)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Fixed)
    header.setStretchLastSection(False)
    header.setSectionsMovable(False)
    header.setDefaultAlignment(Qt.AlignCenter)
    vertical_header = table.verticalHeader()
    vertical_header.setSectionResizeMode(QHeaderView.Fixed)
    vertical_header.setSectionsMovable(False)
    vertical_header.setDefaultAlignment(Qt.AlignCenter)

    item_margin = table.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None, table) + 1
    section_border_width = (
        table.style().pixelMetric(QStyle.PM_DefaultFrameWidth, None, table)
        if table.showGrid()
        else 0
    )
    name_width = (
        table.fontMetrics().horizontalAdvance(
            "한" * STOCK_BROWSER_STOCK_NAME_DISPLAY_CHARACTERS
        )
        + (item_margin * 2)
        + section_border_width
    )
    code_width = _symmetric_text_column_width(table, "0000000000")
    code_text_width = table.fontMetrics().horizontalAdvance("000000")
    code_horizontal_margin = max(
        item_margin,
        (code_width - section_border_width - code_text_width) // 2,
    )

    table.setColumnWidth(CODE_COLUMN, code_width)
    table.setColumnWidth(NAME_COLUMN, name_width)
    table.setColumnWidth(
        MARKET_COLUMN,
        _text_column_width_with_margin(table, code_horizontal_margin, "시장", "KOSPI", "코스닥"),
    )
    table.setColumnWidth(
        REGISTRATION_STATUS_COLUMN,
        _text_column_width_with_margin(
            table,
            code_horizontal_margin,
            "등록상태",
            "등록대기",
            "검토관리",
        ),
    )
    table.setColumnWidth(
        INSTRUMENT_CLASSIFICATION_COLUMN,
        _text_column_width_with_margin(
            table,
            code_horizontal_margin,
            "분류",
            "일반종목",
            "SPAC",
            "REIT",
        ),
    )
    table.setColumnWidth(
        AFTER_MARKET_COLUMN,
        _text_column_width_with_margin(table, code_horizontal_margin, "비고", "NXT"),
    )
    numeric_samples = {
        CURRENT_PRICE_COLUMN: ("현재주가", "999,999,999"),
        CHANGE_RATE_COLUMN: ("등락률", "+999.99%"),
        EXECUTION_STRENGTH_COLUMN: ("체결강도", "999.99"),
        PREVIOUS_DAY_VOLUME_RATE_COLUMN: ("전일대비", "+999.99%"),
        TRADING_VALUE_COLUMN: ("거래대금", "99,999,999억", "9,999만원"),
        VOLUME_COLUMN: ("거래량", "99,999,999주", "999.9억주"),
        MARKET_CAP_COLUMN: ("시총", "99,999,999억"),
    }
    for column, samples in numeric_samples.items():
        table.setColumnWidth(column, _text_column_width(table, *samples))
    table.setColumnWidth(
        STOCK_STATUS_COLUMN,
        _text_column_width(table, "상태", *STOCK_BROWSER_STATUS_SINGLE_VALUES)
        + section_border_width,
    )
    for column in range(table.columnCount()):
        header.setSectionResizeMode(column, QHeaderView.Fixed)

    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    normalize_stock_browser_row_number_width(table)


def configure_stock_browser_search_geometry(
    search_input,
    general_stock_button,
    ranking_title_label,
    ranking_buttons,
) -> None:
    ranking_buttons = tuple(ranking_buttons)
    if ranking_buttons:
        ranking_width = (
            ranking_buttons[0].fontMetrics().horizontalAdvance("거래대금")
            + (STOCK_BROWSER_RANKING_BADGE_HORIZONTAL_PADDING * 2)
            + 2
        )
        for button in ranking_buttons:
            button.setFixedWidth(ranking_width)
            button.setFixedHeight(STOCK_BROWSER_BADGE_HEIGHT)
            button.setCursor(Qt.PointingHandCursor)
    general_width = (
        general_stock_button.fontMetrics().horizontalAdvance("일반종목")
        + (STOCK_BROWSER_RANKING_BADGE_HORIZONTAL_PADDING * 2)
        + 2
    )
    general_stock_button.setFixedWidth(general_width)
    general_stock_button.setFixedHeight(STOCK_BROWSER_BADGE_HEIGHT)
    general_stock_button.setCursor(Qt.PointingHandCursor)
    ranking_title_label.setFixedWidth(ranking_title_label.sizeHint().width())
    search_margin = (
        search_input.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None, search_input)
        + 1
    )
    search_input.setFixedWidth(
        search_input.fontMetrics().horizontalAdvance(
            "한" * STOCK_BROWSER_SEARCH_DISPLAY_CHARACTERS
        )
        + (search_margin * 2)
    )
    search_input.setMinimumHeight(search_input.sizeHint().height() + 6)


def normalize_stock_browser_row_number_width(table) -> int:
    text_width = table.verticalHeader().fontMetrics().horizontalAdvance("999")
    section_border_width = table.style().pixelMetric(
        QStyle.PM_DefaultFrameWidth,
        None,
        table.verticalHeader(),
    )
    fixed_width = (
        text_width
        + (STOCK_BROWSER_ROW_NUMBER_HORIZONTAL_PADDING * 2)
        + section_border_width
    )
    table.verticalHeader().setFixedWidth(fixed_width)
    return fixed_width


def stock_browser_table_required_width(table) -> int:
    return (
        sum(
            table.horizontalHeader().sectionSize(column)
            for column in range(table.columnCount())
        )
        + table.verticalHeader().width()
        + table.verticalScrollBar().sizeHint().width()
        + (table.frameWidth() * 2)
    )


def normalize_stock_browser_dialog_width(dialog, table) -> int:
    fixed_width = getattr(dialog, "_fixed_result_dialog_width", None)
    if not isinstance(fixed_width, int) or fixed_width <= 0:
        margins = dialog.layout().contentsMargins()
        fixed_width = (
            stock_browser_table_required_width(table)
            + margins.left()
            + margins.right()
        )
        dialog._fixed_result_dialog_width = fixed_width
    dialog.setFixedWidth(fixed_width)
    return fixed_width


def normalize_browser_code(value: object) -> str:
    return str(value or "").strip().upper()


def market_display_text(value: object) -> str:
    market = str(value or "").strip().upper()
    return {"KOSPI": "KOSPI", "KOSDAQ": "코스닥"}.get(market, "")


def instrument_classification_display_text(value: object) -> str:
    text = str(value or "-").strip() or "-"
    return "일반" if text == "일반종목" else text


def stock_status_full_text(value: object) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        parts = [str(item or "").strip() for item in value]
        text = " | ".join(part for part in parts if part)
    else:
        text = str(value or "").strip()
    return text if text else "-"


def filter_stock_library_records(
    records: Iterable[Mapping[str, object]],
    query: object,
    *,
    include_all_when_empty: bool = False,
) -> list[dict[str, object]]:
    """Return stable, deduplicated local matches without mutating source rows."""
    keywords = [
        part.strip().casefold()
        for part in re.split(r"[,，、]+", str(query or ""))
        if part.strip()
    ]
    source = [dict(record) for record in records]
    if not keywords:
        return source if include_all_when_empty else []

    matches: list[dict[str, object]] = []
    seen_codes: set[str] = set()
    for keyword in keywords:
        for stock in source:
            code = normalize_browser_code(stock.get("code"))
            name = str(stock.get("name", "") or "").strip()
            if not code or not name or code in seen_codes:
                continue
            searchable_values = (
                code.casefold(),
                name.casefold(),
                str(stock.get("chosung", "") or "").strip().casefold(),
            )
            market_values = {
                str(stock.get("market", "") or "").strip().casefold(),
                market_display_text(stock.get("market", "")).casefold(),
            }
            if (
                any(keyword in value for value in searchable_values)
                or keyword in market_values
            ):
                matches.append(dict(stock))
                seen_codes.add(code)
    return matches


def stock_browser_display_values(
    stock: Mapping[str, object],
    *,
    registration_status: object = "-",
) -> tuple[object, ...]:
    classification = str(stock.get("classification", "") or "-").strip() or "-"
    return (
        normalize_browser_code(stock.get("code")),
        str(stock.get("name", "") or "").strip(),
        market_display_text(stock.get("market", "")),
        str(registration_status or "-").strip() or "-",
        instrument_classification_display_text(classification),
        "NXT" if stock.get("nxt_available") is True else "",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        stock_status_full_text(stock.get("status")),
    )


def snapshot_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _compact_one_decimal(value: int | float) -> str:
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def format_snapshot_integer(value: object) -> str:
    number = snapshot_number(value)
    return "-" if number is None else f"{int(number):,}"


def format_snapshot_volume(value: object) -> str:
    number = snapshot_number(value)
    if number is None:
        return "-"
    if number >= 100_000_000:
        return f"{_compact_one_decimal(number / 100_000_000)}억주"
    return f"{int(number):,}주"


def format_snapshot_trading_value(value: object) -> str:
    number = snapshot_number(value)
    if number is None:
        return "-"
    if number >= 100:
        return f"{int(number // 100):,}억"
    return f"{int(number * 100):,}만원"


def format_snapshot_market_cap(value: object) -> str:
    number = snapshot_number(value)
    if number is None:
        return "-"
    if number >= 1:
        return f"{int(number):,}억"
    return f"{int(number * 10_000):,}만원"


def format_snapshot_decimal(value: object) -> str:
    number = snapshot_number(value)
    return "-" if number is None else f"{float(number):.2f}"


def format_snapshot_percent(value: object) -> str:
    number = snapshot_number(value)
    if number is None:
        return "-"
    rounded = round(float(number), 2)
    if rounded > 0:
        return f"+{rounded:.2f}%"
    if rounded < 0:
        return f"-{abs(rounded):.2f}%"
    return "0.00%"


def market_snapshot_display_values(
    snapshot: Mapping[str, object],
) -> dict[int, tuple[object, str]]:
    return {
        CURRENT_PRICE_COLUMN: (
            snapshot.get("current_price"),
            format_snapshot_integer(snapshot.get("current_price")),
        ),
        CHANGE_RATE_COLUMN: (
            snapshot.get("change_rate"),
            format_snapshot_percent(snapshot.get("change_rate")),
        ),
        EXECUTION_STRENGTH_COLUMN: (
            snapshot.get("execution_strength"),
            format_snapshot_decimal(snapshot.get("execution_strength")),
        ),
        PREVIOUS_DAY_VOLUME_RATE_COLUMN: (
            snapshot.get("previous_day_volume_rate"),
            format_snapshot_percent(snapshot.get("previous_day_volume_rate")),
        ),
        TRADING_VALUE_COLUMN: (
            snapshot.get("cumulative_trading_value"),
            format_snapshot_trading_value(snapshot.get("cumulative_trading_value")),
        ),
        VOLUME_COLUMN: (
            snapshot.get("cumulative_volume"),
            format_snapshot_volume(snapshot.get("cumulative_volume")),
        ),
        MARKET_CAP_COLUMN: (
            snapshot.get("market_capitalization"),
            format_snapshot_market_cap(snapshot.get("market_capitalization")),
        ),
    }
