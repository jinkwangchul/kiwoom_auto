"""A direct M3 receipt never authorizes reconstructing a missing M1."""

from datetime import datetime
from unittest.mock import patch

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from tests.test_display_only_integrated_history import (
    DisplayOnlyMarketDataHostTest, _opt10080_row,
)


def test_missing_m1_blocks_even_when_direct_m3_exists():
    host = DisplayOnlyMarketDataHostTest()._host()
    as_of = datetime(2026, 10, 23, 8, 3, tzinfo=SEOUL_TIMEZONE)
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)):
        for interval, stamps in ((1, ("20261023080000", "20261023080200")),
                                 (3, ("20261023080000",))):
            result = MarketDataHost.install_display_timeframe_candles(
                host, "005930", interval,
                [_opt10080_row(stamp, 100) for stamp in stamps],
                as_of=as_of, market_data_identity="005930_AL",
                market_source="INTEGRATED", display_only=True,
                connection_epoch=1, login_session_id="SESSION-1",
            )
            assert result["ok"] is True
    projected = MarketDataHost.project_broker_sor_m1_calculation(
        host, stock_code="005930", rules={"bar": {"bar_minutes": 1}},
        projection_request={"projection": "COMPLETED_TIMEFRAME"}, as_of=as_of,
    )
    assert projected["available"] is False
    assert projected["availability_state"] == "SOR_M1_INTERNAL_COVERAGE_GAP"
    assert projected["candles"] == []
