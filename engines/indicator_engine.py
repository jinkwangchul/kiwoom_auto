# -*- coding: utf-8 -*-
"""공통 지표 계산 엔진.

역할:
- 봉데이터에서 종가/거래량 추출.
- EMA, 단순이평, RSI, MACD, OSC, Bollinger 계산.
- 루틴별 신호발생부가 사용할 series_map 생성.
"""

from __future__ import annotations

from typing import Any, Mapping
import math


DEFAULT_INDICATOR_HISTORY_TARGET_BARS = 600
INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND = {
    "MINUTE": 900,
    "DAY": 600,
    "WEEK": 300,
    "MONTH": 120,
    "YEAR": 120,
}
INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KEY = {
    "D1": 600,
    "W1": 300,
    "MO1": 120,
    "Y1": 120,
}


def indicator_history_target_bars(timeframe: Any) -> int:
    """Return the Hero4-style retained history target for one timeframe."""
    if isinstance(timeframe, Mapping):
        key = str(timeframe.get("key") or "").strip().upper()
        if key in INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KEY:
            return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KEY[key]
        kind = str(timeframe.get("kind") or "").strip().upper()
        if kind in INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND:
            return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND[kind]
        minutes = timeframe.get("minutes")
        if minutes is not None:
            return indicator_history_target_bars(minutes)

    if isinstance(timeframe, bool):
        return DEFAULT_INDICATOR_HISTORY_TARGET_BARS
    if isinstance(timeframe, (int, float)):
        return (
            INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["MINUTE"]
            if int(timeframe) > 0
            else DEFAULT_INDICATOR_HISTORY_TARGET_BARS
        )

    text = str(timeframe or "").strip().upper()
    if text in INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KEY:
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KEY[text]
    if text in INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND:
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND[text]
    if text in {"일", "DAY"}:
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["DAY"]
    if text in {"주", "WEEK"}:
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["WEEK"]
    if text in {"월", "MONTH"}:
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["MONTH"]
    if text in {"년", "YEAR"}:
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["YEAR"]
    if text.startswith("M") and text[1:].isdigit():
        return INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["MINUTE"]
    try:
        return (
            INDICATOR_HISTORY_TARGET_BARS_BY_TIMEFRAME_KIND["MINUTE"]
            if int(float(text)) > 0
            else DEFAULT_INDICATOR_HISTORY_TARGET_BARS
        )
    except (TypeError, ValueError):
        return DEFAULT_INDICATOR_HISTORY_TARGET_BARS


def indicator_history_target_bars_from_rules(
    rules: Mapping[str, Any] | None,
) -> int:
    """Resolve Validation timeframe first, otherwise the applied minute bar."""
    source = rules if isinstance(rules, Mapping) else {}
    validation_timeframe = source.get("validation_timeframe")
    if validation_timeframe not in (None, ""):
        return indicator_history_target_bars(validation_timeframe)
    bar = source.get("bar") if isinstance(source.get("bar"), Mapping) else {}
    raw_minutes = bar.get("bar_minutes", source.get("bar_minutes", 1))
    return indicator_history_target_bars(raw_minutes)


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def close_prices(candles: list[dict[str, Any]]) -> list[float | None]:
    return [safe_float(candle.get("close")) for candle in candles]


def volumes(candles: list[dict[str, Any]]) -> list[float | None]:
    return [safe_float(candle.get("volume")) for candle in candles]


def ema(values: list[float | None], period: int) -> list[float | None]:
    if period <= 0:
        return [None for _ in values]

    multiplier = 2 / (period + 1)

    # Hero4 EAVG exposes a period-warmed prefix for ordinary numeric vectors:
    # the recursive core starts from the first value, but the first period
    # output slots are backfilled with the EMA at the period-th observation.
    # A shorter vector is returned as zeroes. Keep the legacy sparse/None
    # behavior below because Hero4's missing-value semantics are not proven.
    if all(value is not None for value in values):
        numeric_values = [float(value) for value in values]
        if len(numeric_values) < period:
            return [0.0 for _ in numeric_values]
        if not numeric_values:
            return []

        numeric_result: list[float] = [numeric_values[0]]
        previous_numeric = numeric_values[0]
        for value in numeric_values[1:]:
            previous_numeric = (
                (value - previous_numeric) * multiplier
                + previous_numeric
            )
            numeric_result.append(previous_numeric)
        warmed_value = numeric_result[period - 1]
        numeric_result[:period] = [warmed_value] * period
        return numeric_result

    result: list[float | None] = []
    previous: float | None = None
    for value in values:
        if value is None:
            result.append(previous)
            continue
        if previous is None:
            previous = value
        else:
            previous = (value - previous) * multiplier + previous
        result.append(previous)
    return result


def simple_ma(values: list[float | None], period: int) -> list[float | None]:
    if period <= 0:
        return [None for _ in values]
    result: list[float | None] = []
    window: list[float] = []
    for value in values:
        if value is None:
            result.append(None)
            continue
        window.append(value)
        if len(window) > period:
            window.pop(0)
        if len(window) < period:
            result.append(None)
        else:
            result.append(sum(window) / period)
    return result


def _std_dev(values: list[float | None], period: int) -> list[float | None]:
    """Calculate simple standard deviation over a rolling window."""
    if period <= 0:
        return [None for _ in values]
    result: list[float | None] = []
    window: list[float] = []
    for value in values:
        if value is None:
            result.append(None)
            continue
        window.append(value)
        if len(window) > period:
            window.pop(0)
        if len(window) < period:
            result.append(None)
        else:
            mean = sum(window) / period
            variance = sum((x - mean) ** 2 for x in window) / period
            result.append(math.sqrt(variance))
    return result


def bollinger_band(
    values: list[float | None],
    period: int = 20,
    std_multiplier: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calculate Bollinger Bands.

    Returns:
        tuple: (lower_band, middle_band, upper_band)
        - middle_band: Simple Moving Average
        - upper_band: MA + (std * multiplier)
        - lower_band: MA - (std * multiplier)
    """
    ma = simple_ma(values, period)
    std = _std_dev(values, period)

    upper: list[float | None] = []
    lower: list[float | None] = []
    for ma_val, std_val in zip(ma, std):
        if ma_val is None or std_val is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(ma_val + std_val * std_multiplier)
            lower.append(ma_val - std_val * std_multiplier)

    return lower, ma, upper


def price_box(
    values: list[float | None],
    period: int = 24,
    history_window: int = DEFAULT_INDICATOR_HISTORY_TARGET_BARS,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calculate a causal Kiwoom-style Price Box.

    The center line is the rolling period-bar close average. The upper and
    lower offsets use AvgIf/StdevIf semantics over the valid deviation series
    inside the trailing history window. Recomputing the valid deviation series
    from each local history window excludes its first period-1 bars, matching
    the HTS loaded-window behavior while remaining causal for replay.
    """
    if period <= 0 or history_window <= 0:
        empty = [None for _ in values]
        return list(empty), list(empty), list(empty)
    history_window = max(int(history_window), int(period))

    lower: list[float | None] = [None for _ in values]
    middle: list[float | None] = [None for _ in values]
    upper: list[float | None] = [None for _ in values]
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if any(value is None for value in window):
            continue
        middle[index] = sum(float(value) for value in window) / period

    deviations: list[float | None] = [None for _ in values]
    for index, average in enumerate(middle):
        value = values[index] if index < len(values) else None
        if average is not None and value is not None:
            deviations[index] = float(value) - float(average)

    positive_rows: list[tuple[int, float]] = []
    negative_rows: list[tuple[int, float]] = []
    positive_sum = 0.0
    positive_sumsq = 0.0
    negative_sum = 0.0
    negative_sumsq = 0.0
    positive_head = 0
    negative_head = 0

    def stats(
        rows: list[tuple[int, float]],
        head: int,
        total: float,
        total_sq: float,
    ) -> tuple[float, float] | None:
        count = len(rows) - head
        if count <= 0:
            return None
        mean = total / count
        variance = max((total_sq / count) - (mean * mean), 0.0)
        return mean, math.sqrt(variance)

    for index in range(period - 1, len(values)):
        deviation = deviations[index]
        if deviation is not None:
            if deviation > 0:
                positive_rows.append((index, deviation))
                positive_sum += deviation
                positive_sumsq += deviation * deviation
            elif deviation < 0:
                negative_rows.append((index, deviation))
                negative_sum += deviation
                negative_sumsq += deviation * deviation

        local_start = max(0, index - history_window + 1)
        first_valid_deviation = local_start + period - 1

        while (
            positive_head < len(positive_rows)
            and positive_rows[positive_head][0] < first_valid_deviation
        ):
            old = positive_rows[positive_head][1]
            positive_sum -= old
            positive_sumsq -= old * old
            positive_head += 1
        while (
            negative_head < len(negative_rows)
            and negative_rows[negative_head][0] < first_valid_deviation
        ):
            old = negative_rows[negative_head][1]
            negative_sum -= old
            negative_sumsq -= old * old
            negative_head += 1

        average = middle[index]
        if average is None:
            continue
        positive_stats = stats(
            positive_rows,
            positive_head,
            positive_sum,
            positive_sumsq,
        )
        negative_stats = stats(
            negative_rows,
            negative_head,
            negative_sum,
            negative_sumsq,
        )
        if positive_stats is not None:
            upper[index] = average + positive_stats[0] + (2.0 * positive_stats[1])
        if negative_stats is not None:
            lower[index] = average + negative_stats[0] - (2.0 * negative_stats[1])
    return lower, middle, upper


def rsi(values: list[float | None], period: int = 14) -> list[float | None]:
    if period <= 0:
        return [None for _ in values]

    result: list[float | None] = [None]
    gains: list[float] = []
    losses: list[float] = []
    avg_gain: float | None = None
    avg_loss: float | None = None

    for idx in range(1, len(values)):
        current = values[idx]
        previous = values[idx - 1]
        if current is None or previous is None:
            result.append(None)
            continue

        change = current - previous
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if avg_gain is None or avg_loss is None:
            gains.append(gain)
            losses.append(loss)
            if len(gains) < period:
                result.append(None)
                continue
            if len(gains) > period:
                gains.pop(0)
                losses.pop(0)
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
        else:
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_gain == 0 and avg_loss == 0:
            result.append(0.0)
        elif avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - (100 / (1 + rs)))

    return result


def rsi_causal_history(
    values: list[float | None],
    period: int = 14,
    history_window: int = DEFAULT_INDICATOR_HISTORY_TARGET_BARS,
) -> list[float | None]:
    """Return RSI as if each point were evaluated from at most history_window bars."""
    if history_window <= 0:
        return [None for _ in values]
    history_window = max(int(history_window), int(period) + 1)
    if len(values) <= history_window:
        return rsi(values, period)

    result = rsi(values[:history_window], period)
    for index in range(history_window, len(values)):
        start = index - history_window + 1
        local = rsi(values[start:index + 1], period)
        result.append(local[-1] if local else None)
    return result


def macd_series(
    closes: list[float | None],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)

    macd_line: list[float | None] = []
    for fast_value, slow_value in zip(fast_ema, slow_ema):
        if fast_value is None or slow_value is None:
            macd_line.append(None)
        else:
            macd_line.append(fast_value - slow_value)

    signal_line = ema(macd_line, signal_period)

    osc: list[float | None] = []
    for macd_value, signal_value in zip(macd_line, signal_line):
        if macd_value is None or signal_value is None:
            osc.append(None)
        else:
            osc.append(macd_value - signal_value)

    return macd_line, signal_line, osc


def macd_series_causal_history(
    closes: list[float | None],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
    history_window: int = DEFAULT_INDICATOR_HISTORY_TARGET_BARS,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return MACD family values from at most history_window bars per point."""
    if history_window <= 0:
        empty = [None for _ in closes]
        return list(empty), list(empty), list(empty)
    history_window = max(
        int(history_window),
        int(slow) + int(signal_period),
    )

    # Hero4 EAVG backfills its initial period slots from the period-th EMA.
    # Replaying a whole future-known vector would therefore leak that backfill
    # into earlier evaluations. Preserve prefix causality while avoiding a
    # quadratic recomputation inside the initial retained-history window.
    macd_values: list[float | None] = []
    signal_values: list[float | None] = []
    osc_values: list[float | None] = []

    initial_end = min(len(closes), history_window)
    stable_count = max(int(fast), int(slow), int(signal_period))
    can_stream_initial = (
        fast > 0
        and slow > 0
        and signal_period > 0
        and all(value is not None for value in closes[:initial_end])
    )
    prefix_end = (
        min(initial_end, stable_count)
        if can_stream_initial
        else initial_end
    )

    for index in range(prefix_end):
        local_macd, local_signal, local_osc = macd_series(
            closes[: index + 1],
            fast,
            slow,
            signal_period,
        )
        macd_values.append(local_macd[-1] if local_macd else None)
        signal_values.append(local_signal[-1] if local_signal else None)
        osc_values.append(local_osc[-1] if local_osc else None)

    if can_stream_initial and initial_end > stable_count:
        seed_closes = closes[:stable_count]
        fast_state = ema(seed_closes, fast)[-1]
        slow_state = ema(seed_closes, slow)[-1]
        signal_state = signal_values[-1]
        fast_multiplier = 2 / (fast + 1)
        slow_multiplier = 2 / (slow + 1)
        signal_multiplier = 2 / (signal_period + 1)
        assert fast_state is not None
        assert slow_state is not None
        assert signal_state is not None

        for index in range(stable_count, initial_end):
            close = closes[index]
            assert close is not None
            fast_state = (
                (float(close) - fast_state) * fast_multiplier
                + fast_state
            )
            slow_state = (
                (float(close) - slow_state) * slow_multiplier
                + slow_state
            )
            macd_value = fast_state - slow_state
            signal_state = (
                (macd_value - signal_state) * signal_multiplier
                + signal_state
            )
            macd_values.append(macd_value)
            signal_values.append(signal_state)
            osc_values.append(macd_value - signal_state)

    for index in range(history_window, len(closes)):
        start = index - history_window + 1
        local_macd, local_signal, local_osc = macd_series(
            closes[start:index + 1],
            fast,
            slow,
            signal_period,
        )
        macd_values.append(local_macd[-1] if local_macd else None)
        signal_values.append(local_signal[-1] if local_signal else None)
        osc_values.append(local_osc[-1] if local_osc else None)
    return macd_values, signal_values, osc_values


def build_indicator_series(
    candles: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, list[float | None]]:
    cfg = config if isinstance(config, dict) else {}
    indicator_cfg = cfg.get("indicators") if isinstance(cfg.get("indicators"), dict) else cfg

    macd_cfg = indicator_cfg.get("macd", {}) if isinstance(indicator_cfg.get("macd"), dict) else {}
    fast = int(macd_cfg.get("fast", 12) or 12)
    slow = int(macd_cfg.get("slow", 26) or 26)
    signal_period = int(macd_cfg.get("signal", 9) or 9)

    closes = close_prices(candles)
    vols = volumes(candles)
    macd_line, signal_line, osc = macd_series(closes, fast, slow, signal_period)

    rsi_cfg = indicator_cfg.get("rsi", {}) if isinstance(indicator_cfg.get("rsi"), dict) else {}
    rsi_period = int(rsi_cfg.get("period", 14) or 14)

    bollinger_cfg = indicator_cfg.get("bollinger", {}) if isinstance(indicator_cfg.get("bollinger"), dict) else {}
    bollinger_period = int(bollinger_cfg.get("period", 20) or 20)
    bollinger_std = safe_float(bollinger_cfg.get("std", 2.0)) or 2.0

    bollinger_lower, bollinger_middle, bollinger_upper = bollinger_band(
        closes, bollinger_period, bollinger_std
    )

    price_box_cfg = indicator_cfg.get("price_box", {}) if isinstance(indicator_cfg.get("price_box"), dict) else {}
    try:
        price_box_period = int(price_box_cfg.get("period", 24) or 24)
    except (TypeError, ValueError):
        price_box_period = 24
    if price_box_period <= 0:
        price_box_period = 24
    history_window = indicator_history_target_bars_from_rules(cfg)
    price_box_lower, price_box_middle, price_box_upper = price_box(
        closes,
        price_box_period,
        history_window=history_window,
    )

    series_map: dict[str, list[float | None]] = {
        "CLOSE": closes,
        "VOLUME": vols,
        "MACD": macd_line,
        "SIGNAL": signal_line,
        "OSC": osc,
        "RSI": rsi(closes, rsi_period),
        "BOLLINGER": bollinger_lower,
        "BOLLINGER_LOWER": bollinger_lower,
        "BOLLINGER_MIDDLE": bollinger_middle,
        "BOLLINGER_UPPER": bollinger_upper,
        "PRICE_BOX_LOWER": price_box_lower,
        "PRICE_BOX_MIDDLE": price_box_middle,
        "PRICE_BOX_UPPER": price_box_upper,
    }

    ma_periods = indicator_cfg.get("moving_averages", [5, 20, 60])
    if not isinstance(ma_periods, list):
        ma_periods = [5, 20, 60]

    for period_value in ma_periods:
        try:
            period = int(period_value)
        except (TypeError, ValueError):
            continue
        if period > 0:
            series_map[f"MA{period}"] = simple_ma(closes, period)

    return series_map
