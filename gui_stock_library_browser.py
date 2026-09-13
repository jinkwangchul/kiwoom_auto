# -*- coding: utf-8 -*-
"""Shared read-only projection helpers for stock-library browser dialogs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


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
