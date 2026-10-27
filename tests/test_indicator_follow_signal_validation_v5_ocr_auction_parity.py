"""V5 keeps genuine KRX closing-auction bars beside verified NXT AFTER."""
from __future__ import annotations

from datetime import datetime, timedelta
import math

import pytest

from gui_indicator_follow_signal_validation_v5_window import (
    ValidationFilterDescriptor,
    _build_validation_projection_data,
    _calculation_indexes_for_mixed_display,
)
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import (
    _flow,
)
from tests import test_indicator_follow_v2_display_only_integrated_flow as contracts


def _row(stamp: str, index: int) -> dict[str, object]:
    close = 100.0 + index * 0.07 + math.sin(index / 4) * 2
    return {
        "time": stamp, "open": close - 0.2, "high": close + 1,
        "low": close - 1, "close": close, "volume": 100 + index,
    }


def _source(verified: bool):
    flow = _flow()
    stock = contracts.ValidationStockRef("005930", "Samsung")
    settings = contracts.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"},
    })
    session = contracts.ValidationSession(
        contracts.ValidationRequest(stock, settings, 3),
        operation_active_reader=lambda: False,
    )
    start = datetime(2026, 9, 22, 10, 27)
    krx = [
        _row((start + timedelta(minutes=3 * index)).strftime("%Y%m%d%H%M%S"), index)
        for index in range(104)
    ]
    after = [_row(stamp, 104 + index) for index, stamp in enumerate((
        "20260922153900", "20260922154200",
    ))]
    omitted = {
        "20260922145700", "20260922152700",  # Outside the auction.
        "20260922153000", "20260922153300",
    }
    integrated = [row.copy() for row in krx if row["time"] not in omitted] + after
    sidecar = {
        "stock_code": session.request.stock.code,
        "timeframe_key": "M3", "timeframe_minutes": 3,
        "market_data_identity": f"{session.request.stock.code}_AL",
        "market_source": "INTEGRATED", "display_only": True,
        "verified_nxt_eligible": verified,
        "request_as_of": "2026-09-22T16:20:00+09:00",
        "candles": integrated,
    }
    pool = {
        "stock": session.request.stock, "candles": krx,
        "market_source": "KRX", "market_data_identity": session.request.stock.code,
        "timeframe_key": "M3", "timeframe_minutes": 3,
        "requested_count": len(krx), "request_id": "KRX",
    }
    return flow, session, pool, sidecar


def test_verified_after_restores_only_missing_krx_auction_with_price_and_osc():
    flow, session, pool, sidecar = _source(True)
    view = flow._validation_pool_for_session(
        session, pool, target_count=106, sidecar_override=sidecar,
    )
    calculation = view["candles"]
    display = view["display_candles"]
    times = [row["time"] for row in display]
    assert view["market_source"] == "VALIDATION_COMPOSITE"
    assert len(calculation) == 106
    assert len(times) == len(set(times))
    assert {"20260922153000", "20260922153300"} <= set(times)
    assert not {"20260922145700", "20260922152700"} & set(times)
    assert [row["time"] for row in pool["candles"]] == [
        row["time"] for row in calculation if row["time"] < "20260922153900"
    ]

    indexes = _calculation_indexes_for_mixed_display(calculation, display)
    rules = session.request.settings_snapshot.to_dict()
    descriptor = ValidationFilterDescriptor(
        "auction-osc", "OCR_OSC", "OCR", "LOWER", ("BUY",),
        ("OSC",), '{"fast":12,"slow":26,"signal":9}', (),
    )
    projected, _ = _build_validation_projection_data(
        calculation, [], [], indexes, rules, (descriptor,),
    )
    full, _ = _build_validation_projection_data(
        calculation, [], [], tuple(range(len(calculation))), rules, (descriptor,),
    )
    values = projected.values_for(descriptor.identity, "OSC")
    full_values = full.values_for(descriptor.identity, "OSC")
    by_time = {row["time"]: row for row in calculation}
    for stamp in (
        "20260922153000", "20260922153300", "20260922153600",
        "20260922153900", "20260922154200",
    ):
        display_index = times.index(stamp)
        calculation_index = indexes[display_index]
        assert calculation_index is not None
        assert display[display_index]["close"] == by_time[stamp]["close"]
        assert math.isfinite(values[display_index])
        assert values[display_index] == pytest.approx(full_values[calculation_index])


def test_unverified_after_does_not_restore_missing_auction():
    flow, session, pool, sidecar = _source(False)
    view = flow._validation_pool_for_session(
        session, pool, target_count=106, sidecar_override=sidecar,
    )
    times = {row["time"] for row in view["display_candles"]}
    assert view["market_source"] == "KRX"
    assert "20260922153000" not in times
    assert "20260922153300" not in times
