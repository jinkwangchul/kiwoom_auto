"""V5 direct _AL history keeps its 5,000-bar navigation span."""
from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from stock_market_source_plan import initialize_stock_market_source_plan
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests import test_indicator_follow_v2_display_only_integrated_flow as contracts


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def _timestamps(rows):
    return [row["time"] for row in rows]


def _session():
    stock = contracts.ValidationStockRef("005930", "Samsung")
    settings = contracts.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 1},
        "validation_timeframe": {"key": "M1"},
    })
    return contracts.ValidationSession(
        contracts.ValidationRequest(stock, settings, 1),
        operation_active_reader=lambda: False,
    )


def _flow():
    return flow_module.IndicatorFollowSignalValidationV5Flow(
        object(),
        host=contracts._Host(),
        recent_stock_store=contracts._RecentStore(),
        preference_store=SimpleNamespace(),
        historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_args: {
            "status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False,
        },
    )


def test_v5_unmatched_sidecar_keeps_5000_direct_al_scroll_history(case):
    """An unverified sidecar cannot replace or extend the direct _AL axis."""
    window = baseline.window_for(case)
    direct = baseline.candles(5000)
    window.set_historical_candle_pool(direct, chart_candle_count=5000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    integrated = [dict(row) for row in direct[-3000:]]
    pre_index = next(index for index, row in enumerate(integrated)
                     if row["time"].endswith("090000"))
    integrated[pre_index] = {
        **integrated[pre_index], "time": integrated[pre_index]["time"][:8] + "083000",
        "close": 99999.0,
    }
    integrated[-1] = {**integrated[-1], "time": integrated[-1]["time"][:8] + "154000"}
    pre_time = integrated[pre_index]["time"]
    after_time = integrated[-1]["time"]
    selected_time = direct[-250]["time"]
    window._validation_range_anchor_time = selected_time
    window._set_time_view(1500, 3500)

    window.set_display_only_integrated_candles(integrated)

    assert len(window._candles) == 5000
    assert window.visible_candle_span == 3500
    assert window.time_navigation_scrollbar.maximum() >= 1_500_000
    assert len(_timestamps(window._candles)) == len(set(_timestamps(window._candles)))
    assert selected_time in _timestamps(window._candles)
    assert window._validation_range_anchor_time == selected_time
    assert direct[0]["time"] == window._candles[0]["time"]
    assert pre_time not in _timestamps(window._candles)
    assert after_time not in _timestamps(window._candles)


def test_v5_cached_direct_al_projection_keeps_full_5000_bar_axis(tmp_path, monkeypatch):
    """A shorter sidecar cannot displace the selected direct _AL history."""
    flow = _flow()
    session = _session()
    now = datetime.now(SEOUL_TIMEZONE)
    initialize_stock_market_source_plan(tmp_path, "005930", "Samsung",
        trading_day=now.date(), decided_at=now, nxt_available=True)
    monkeypatch.setattr(flow_module, "PROJECT_ROOT", tmp_path)
    flow._validation_source_by_code["005930"] = flow._verified_nxt_validation_stock(session.request.stock)
    direct = baseline.candles(5000)
    integrated = [dict(row) for row in direct[-3000:]]
    flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)] = {
        "candles": integrated,
        "verified_nxt_eligible": True,
    }
    pool = {
        "stock": session.request.stock,
        "candles": direct,
        "market_source": "INTEGRATED",
        "market_data_identity": "005930_AL",
        "timeframe_key": "M1",
        "timeframe_minutes": 1,
        "requested_count": 5000,
    }

    view = flow._validation_pool_for_session(session, pool, target_count=5000)

    times = _timestamps(view.get("display_candles", view["candles"]))
    assert len(times) == 5000
    assert times[0] == direct[0]["time"]
    assert times[-3000:] == _timestamps(integrated)
    assert len(times) == len(set(times))
    assert view["market_source"] == "INTEGRATED"
    assert view["market_data_identity"] == "005930_AL"
