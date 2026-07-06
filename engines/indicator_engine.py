# -*- coding: utf-8 -*-
"""공통 지표 계산 엔진.

역할:
- 봉데이터에서 종가/거래량 추출.
- EMA, 단순이평, RSI, MACD, OSC 계산.
- 루틴별 신호발생부가 사용할 series_map 생성.
"""

from __future__ import annotations

from typing import Any


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
    result: list[float | None] = []
    multiplier = 2 / (period + 1)
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

        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - (100 / (1 + rs)))

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


def build_indicator_series(
    candles: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, list[float | None]]:
    cfg = config if isinstance(config, dict) else {}

    macd_cfg = cfg.get("macd", {}) if isinstance(cfg.get("macd"), dict) else {}
    fast = int(macd_cfg.get("fast", 12) or 12)
    slow = int(macd_cfg.get("slow", 26) or 26)
    signal_period = int(macd_cfg.get("signal", 9) or 9)

    closes = close_prices(candles)
    vols = volumes(candles)
    macd_line, signal_line, osc = macd_series(closes, fast, slow, signal_period)

    rsi_cfg = cfg.get("rsi", {}) if isinstance(cfg.get("rsi"), dict) else {}
    rsi_period = int(rsi_cfg.get("period", 14) or 14)

    series_map: dict[str, list[float | None]] = {
        "CLOSE": closes,
        "VOLUME": vols,
        "MACD": macd_line,
        "SIGNAL": signal_line,
        "OSC": osc,
        "RSI": rsi(closes, rsi_period),
    }

    ma_periods = cfg.get("moving_averages", [5, 20, 60])
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
