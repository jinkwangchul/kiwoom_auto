"""AL cached history is presentation only; NX still owns PRE calculation/cache."""
from datetime import datetime, timedelta
import json

import pytest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from indicator_follow_signal_validation_historical_cache import IndicatorFollowSignalValidationHistoricalCache
from tests import test_phase5_nxt_native as native
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def _run_with_al(tmp_path, monkeypatch, minutes, *, corrupt=False):
    now = datetime.now(SEOUL_TIMEZONE).replace(hour=8, minute=41, second=0, microsecond=0)
    cache = IndicatorFollowSignalValidationHistoricalCache(tmp_path / "cache")
    original_flow = native._flow
    seed = original_flow()
    seed._historical_cache = cache
    seed._validation_source_by_code["005930"] = True
    stock = native.v5.ValidationStockRef("005930", "Samsung")
    al = []
    for i in range(5000):
        at = (now - timedelta(days=i // 70 + 1)).replace(hour=9, minute=0)
        at += timedelta(minutes=(i % 70) * minutes)
        al.append(dict(time=at.strftime("%Y%m%d%H%M%S"), open=500, high=501,
                       low=499, close=500, volume=20))
    # Neither an AL current-day row nor an AL value at an NX timestamp may win.
    al.extend([dict(native._forming_row(now, minutes), close=124),
               dict(time=(now - timedelta(days=1)).strftime("%Y%m%d") + "080000",
                    open=900, high=901, low=899, close=900, volume=20),
               dict(time=(now - timedelta(days=1)).strftime("%Y%m%d") + "153500",
                    open=700, high=701, low=699, close=700, volume=20)])
    al.sort(key=lambda row: row["time"])
    assert seed._persist_history(stock, minutes, f"M{minutes}", 5900, al,
                                 market_data_identity="005930_AL", market_source="INTEGRATED")
    marker = seed._validation_cache_source_marker_path(stock, minutes, f"M{minutes}", 5900)
    if corrupt:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["candle_content_sha256"] = "0" * 64
        marker.write_text(json.dumps(payload), encoding="utf-8")
    frozen_al = {path.name: path.read_bytes() for path in cache.root.glob("005930_AL*")}

    def configured_flow():
        flow = original_flow()
        flow._historical_cache = cache
        return flow

    monkeypatch.setattr(native, "_flow", configured_flow)
    result = native._run_nxt(tmp_path, monkeypatch, interval=minutes,
                             extra_candles=lambda at: [native._forming_row(at, minutes)])
    flow, calls, accepted, errors, clock, _ = result
    assert not errors and accepted, errors
    assert calls == [("005930", "Samsung", minutes, 5900, True)]
    assert frozen_al == {path.name: path.read_bytes() for path in cache.root.glob("005930_AL*")}
    session, pool = accepted[-1]["session"], accepted[-1]["pool"]
    return flow, session, pool, clock


@pytest.mark.parametrize("minutes", [3, 5])
def test_al_history_never_displaces_nx_forming_calculation_or_completed_cache(tmp_path, monkeypatch, minutes):
    flow, session, pool, clock = _run_with_al(tmp_path, monkeypatch, minutes)
    view = flow._validation_pool_for_session(session, pool, target_count=5000)
    assert view["market_source"] == "NXT"
    assert view["market_data_identity"] == "005930_NX"
    assert len(view["candles"]) == 11
    assert view["native_forming_included"] is True
    assert view["candles"][-1]["close"] == 123
    assert view["candles"][-1]["is_complete"] is False
    assert len(view["display_candles"]) == 5000
    assert view["display_candles"][0]["close"] == 500
    assert view["display_candles"][-1] == view["candles"][-1]
    assert view["display_only_integrated"] is True
    assert all(row["close"] not in (500, 700, 900) for row in view["candles"])
    assert any(row["time"].endswith("153500") and row["close"] == 700
               for row in view["display_candles"])
    replay = flow._prepare_replay_result_from_pool(
        session, pool, history_target=5000, evaluation_count=1)
    assert replay.ok
    assert replay.snapshot.candle_count == 11
    assert replay.snapshot.to_display_candles()[-1]["close"] == 123
    normal, _ = flow._normal_base_history_view(session, pool, 5000)
    assert normal["native_forming_included"] is True
    assert normal["candles"][-1]["is_complete"] is False
    assert len(normal["display_candles"]) == 5000
    prior_nx = {row["time"]: row for row in pool["candles"]}
    assert all(row == prior_nx[row["time"]] for row in view["display_candles"]
               if row["time"] in prior_nx)
    cached = flow._cached_history(session.request.stock, minutes, f"M{minutes}", 5900)
    assert list(cached.candles) == pool["candles"]
    assert len(cached.candles) == 10
    assert all(row.get("is_complete") is not False for row in cached.candles)
    # Reusing the prepared pool must not re-read thousands of disk rows per scroll.
    monkeypatch.setattr(flow._historical_cache, "load_covering",
                        lambda *_a, **_k: pytest.fail("cache read during view projection"))
    clock[0] += timedelta(minutes=1)
    expired = flow._validation_pool_for_session(session, pool, target_count=5000)
    assert not expired["native_forming_included"]
    assert expired["candles"] == pool["candles"]
    assert all(row["time"] != view["candles"][-1]["time"] for row in expired["display_candles"])
    for at in (clock[0].replace(hour=8, minute=50), clock[0].replace(hour=9, minute=0)):
        clock[0] = at
        with pytest.raises(ValueError):
            flow._validation_pool_for_session(session, pool)


def test_invalid_al_history_does_not_block_nx_or_enter_display(tmp_path, monkeypatch):
    flow, session, pool, _ = _run_with_al(tmp_path, monkeypatch, 5, corrupt=True)
    view = flow._validation_pool_for_session(session, pool, target_count=5000)
    assert view["native_forming_included"] is True
    assert len(view["candles"]) == 11
    assert view.get("display_candles", view["candles"]) == view["candles"]


def test_nx_chart_5000_al_history_scroll_and_reentry_keep_calculation_separate(case, tmp_path, monkeypatch):
    flow, session, pool, _ = _run_with_al(tmp_path, monkeypatch, 5)
    view = flow._validation_pool_for_session(session, pool, target_count=5000)
    for _ in range(2):
        window = baseline.window_for(case)
        window.set_historical_candle_pool(view["candles"], chart_candle_count=5000)
        window.set_historical_display_candles(view["display_candles"], display_only_integrated=True)
        window.install_normal_base(session.request.settings_snapshot, "M5")
        window._set_time_view(1500, 3500)
        assert len(window._candles) == 5000
        assert len(window._calculation_candles) == 11
        assert window.visible_candle_span == 3500
        assert window.time_navigation_scrollbar.maximum() >= 1_500_000
        assert window._candles[-1]["close"] == 123
        assert window._calculation_candles[-1]["is_complete"] is False
        window.close()
