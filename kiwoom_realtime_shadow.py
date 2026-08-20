# -*- coding: utf-8 -*-
"""Widget-free realtime shadow tick, one-minute bar, and comparison helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Any

from candle_timeframe_aggregation import SEOUL_TIMEZONE, candle_market_datetime
from kiwoom_candle_adapter import normalize_kiwoom_price


MATCH = "MATCH"
MISMATCH = "MISMATCH"
PARTIAL_VOLUME_UNVERIFIED = "PARTIAL_VOLUME_UNVERIFIED"
NO_CANONICAL_BAR = "NO_CANONICAL_BAR"


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _positive_number(value: Any) -> int | float | None:
    number = normalize_kiwoom_price(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


@dataclass(frozen=True)
class RealtimeShadowTick:
    stock_code: str
    real_type: str
    execution_time_raw: str
    current_price: int | float
    cumulative_volume: int | float | None
    received_at: str
    market_datetime: str
    minute_key: str
    connection_epoch: int
    login_session_id: str

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RealtimeShadowBar:
    stock_code: str
    timeframe_minutes: int
    trade_date: str
    bar_time: str
    open: int | float
    high: int | float
    low: int | float
    close: int | float
    volume: int | float | None
    volume_complete: bool
    first_tick_time: str
    last_tick_time: str
    tick_count: int
    connection_epoch: int
    login_session_id: str

    @property
    def minute_key(self) -> str:
        parsed = datetime.fromisoformat(self.bar_time)
        return parsed.strftime("%Y-%m-%d %H:%M")

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RealtimeShadowComparison:
    stock_code: str
    minute_key: str
    canonical_content_hash: str
    shadow_ohlcv: dict[str, int | float | None]
    canonical_ohlcv: dict[str, int | float | None]
    price_match: bool
    volume_compared: bool
    volume_match: bool | None
    status: str

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def normalize_realtime_shadow_tick(
    *,
    stock_code: object,
    real_type: object,
    execution_time_raw: object,
    current_price_raw: object,
    cumulative_volume_raw: object,
    connection_epoch: object,
    login_session_id: object,
    received_at: datetime | None = None,
) -> RealtimeShadowTick | None:
    """Normalize one official 주식체결 event without mutating external state."""

    code = str(stock_code or "").strip()
    execution_time = str(execution_time_raw or "").strip()
    price = normalize_kiwoom_price(current_price_raw)
    if not code or len(execution_time) != 6 or not execution_time.isdigit() or price is None:
        return None

    observed_at = received_at or datetime.now(SEOUL_TIMEZONE)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        observed_at = observed_at.astimezone(SEOUL_TIMEZONE)
    try:
        tick_clock = datetime.strptime(execution_time, "%H%M%S").time()
    except ValueError:
        return None
    market_time = datetime.combine(
        observed_at.date(),
        tick_clock,
        tzinfo=SEOUL_TIMEZONE,
    )
    try:
        epoch = int(connection_epoch)
    except (TypeError, ValueError):
        return None
    session_id = str(login_session_id or "").strip()
    if epoch < 0 or not session_id:
        return None

    normalized_price = int(price) if price.is_integer() else price
    return RealtimeShadowTick(
        stock_code=code,
        real_type=str(real_type or "").strip(),
        execution_time_raw=execution_time,
        current_price=normalized_price,
        cumulative_volume=_positive_number(cumulative_volume_raw),
        received_at=observed_at.isoformat(timespec="microseconds"),
        market_datetime=market_time.isoformat(timespec="seconds"),
        minute_key=market_time.strftime("%Y-%m-%d %H:%M"),
        connection_epoch=epoch,
        login_session_id=session_id,
    )


@dataclass
class _CurrentShadowBar:
    tick: RealtimeShadowTick
    open: int | float
    high: int | float
    low: int | float
    close: int | float
    first_tick_time: str
    last_tick_time: str
    tick_count: int
    volume_baseline: int | float | None
    last_cumulative_volume: int | float | None
    volume_reliable: bool


class RealtimeShadowBarBuilder:
    """Build process-local bars; only a later valid tick finalizes a minute."""

    def __init__(self) -> None:
        self._current_by_stock: dict[str, _CurrentShadowBar] = {}

    def reset(self) -> None:
        self._current_by_stock.clear()

    def accept_tick(
        self,
        tick: RealtimeShadowTick,
    ) -> tuple[str, RealtimeShadowBar | None]:
        current = self._current_by_stock.get(tick.stock_code)
        if current is None:
            self._current_by_stock[tick.stock_code] = self._start_bar(
                tick,
                volume_baseline=None,
                volume_reliable=False,
            )
            return "STARTED", None

        current_minute = datetime.fromisoformat(current.tick.market_datetime).replace(
            second=0,
            microsecond=0,
        )
        tick_minute = datetime.fromisoformat(tick.market_datetime).replace(
            second=0,
            microsecond=0,
        )
        if tick_minute < current_minute:
            return "OUT_OF_ORDER", None
        if tick_minute == current_minute:
            self._update_bar(current, tick)
            return "UPDATED", None

        completed = self._finalize(current)
        consecutive = (tick_minute - current_minute).total_seconds() == 60
        baseline = current.last_cumulative_volume if consecutive else None
        reliable = bool(
            baseline is not None
            and tick.cumulative_volume is not None
            and tick.cumulative_volume >= baseline
        )
        self._current_by_stock[tick.stock_code] = self._start_bar(
            tick,
            volume_baseline=baseline if reliable else None,
            volume_reliable=reliable,
        )
        return "ROLLED_OVER", completed

    @staticmethod
    def _start_bar(
        tick: RealtimeShadowTick,
        *,
        volume_baseline: int | float | None,
        volume_reliable: bool,
    ) -> _CurrentShadowBar:
        return _CurrentShadowBar(
            tick=tick,
            open=tick.current_price,
            high=tick.current_price,
            low=tick.current_price,
            close=tick.current_price,
            first_tick_time=tick.market_datetime,
            last_tick_time=tick.market_datetime,
            tick_count=1,
            volume_baseline=volume_baseline,
            last_cumulative_volume=tick.cumulative_volume,
            volume_reliable=bool(volume_reliable),
        )

    @staticmethod
    def _update_bar(current: _CurrentShadowBar, tick: RealtimeShadowTick) -> None:
        current.high = max(current.high, tick.current_price)
        current.low = min(current.low, tick.current_price)
        current.close = tick.current_price
        current.last_tick_time = tick.market_datetime
        current.tick_count += 1
        if tick.cumulative_volume is None:
            current.volume_reliable = False
            return
        if (
            current.last_cumulative_volume is not None
            and tick.cumulative_volume < current.last_cumulative_volume
        ):
            current.volume_reliable = False
        if (
            current.volume_baseline is not None
            and tick.cumulative_volume < current.volume_baseline
        ):
            current.volume_reliable = False
        current.last_cumulative_volume = tick.cumulative_volume

    @staticmethod
    def _finalize(current: _CurrentShadowBar) -> RealtimeShadowBar:
        bar_time = datetime.fromisoformat(current.tick.market_datetime).replace(
            second=0,
            microsecond=0,
        )
        volume_complete = bool(
            current.volume_reliable
            and current.volume_baseline is not None
            and current.last_cumulative_volume is not None
            and current.last_cumulative_volume >= current.volume_baseline
        )
        volume = (
            current.last_cumulative_volume - current.volume_baseline
            if volume_complete
            else None
        )
        return RealtimeShadowBar(
            stock_code=current.tick.stock_code,
            timeframe_minutes=1,
            trade_date=bar_time.date().isoformat(),
            bar_time=bar_time.isoformat(timespec="seconds"),
            open=current.open,
            high=current.high,
            low=current.low,
            close=current.close,
            volume=volume,
            volume_complete=volume_complete,
            first_tick_time=current.first_tick_time,
            last_tick_time=current.last_tick_time,
            tick_count=current.tick_count,
            connection_epoch=current.tick.connection_epoch,
            login_session_id=current.tick.login_session_id,
        )


def compare_shadow_bar_to_canonical(
    shadow_bar: RealtimeShadowBar,
    canonical_candles: object,
    *,
    canonical_content_hash: str,
) -> RealtimeShadowComparison:
    shadow_values = {
        field: getattr(shadow_bar, field)
        for field in ("open", "high", "low", "close", "volume")
    }
    shadow_minute = datetime.fromisoformat(shadow_bar.bar_time).replace(
        second=0,
        microsecond=0,
    )
    canonical = None
    if isinstance(canonical_candles, list):
        for candle in canonical_candles:
            parsed = candle_market_datetime(candle)
            if parsed is not None and parsed == shadow_minute:
                canonical = candle
                break

    if not isinstance(canonical, dict):
        return RealtimeShadowComparison(
            stock_code=shadow_bar.stock_code,
            minute_key=shadow_bar.minute_key,
            canonical_content_hash=str(canonical_content_hash or ""),
            shadow_ohlcv=shadow_values,
            canonical_ohlcv={},
            price_match=False,
            volume_compared=False,
            volume_match=None,
            status=NO_CANONICAL_BAR,
        )

    canonical_values = {
        field: _number(canonical.get(field))
        for field in ("open", "high", "low", "close", "volume")
    }
    price_match = all(
        canonical_values[field] == shadow_values[field]
        for field in ("open", "high", "low", "close")
    )
    volume_compared = bool(shadow_bar.volume_complete)
    volume_match = (
        canonical_values["volume"] == shadow_values["volume"]
        if volume_compared
        else None
    )
    if not price_match or (volume_compared and volume_match is not True):
        status = MISMATCH
    elif not volume_compared:
        status = PARTIAL_VOLUME_UNVERIFIED
    else:
        status = MATCH
    return RealtimeShadowComparison(
        stock_code=shadow_bar.stock_code,
        minute_key=shadow_bar.minute_key,
        canonical_content_hash=str(canonical_content_hash or ""),
        shadow_ohlcv=shadow_values,
        canonical_ohlcv=canonical_values,
        price_match=price_match,
        volume_compared=volume_compared,
        volume_match=volume_match,
        status=status,
    )
