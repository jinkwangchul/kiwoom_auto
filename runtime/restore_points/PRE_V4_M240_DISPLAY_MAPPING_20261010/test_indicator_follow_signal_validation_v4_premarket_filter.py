"""2026-10-09 contract: verified morning NXT bars participate in Validation filters.

These are isolated V4 validation tests. Production ordering and MarketDataHost are out of scope.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy

from tests.test_indicator_follow_signal_validation_v4_normal_base import case, legacy
from tests.test_indicator_follow_signal_validation_v4_warmup_legend import flow_case


def _premarket_rows(regular):
    base = [
        {"time": "20261007090000", "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1},
        {"time": "20261007090300", "open": 101., "high": 102., "low": 100., "close": 101., "volume": 1},
        {"time": "20261008090000", "open": 102., "high": 103., "low": 101., "close": 102., "volume": 1},
        {"time": "20261008090300", "open": 103., "high": 104., "low": 102., "close": 103., "volume": 1},
    ]
    pre = [
        {"time": "20261008080000", "open": 110., "high": 112., "low": 109., "close": 111., "volume": 1},
        {"time": "20261008080300", "open": 111., "high": 115., "low": 110., "close": 114., "volume": 1},
        {"time": "20261008080600", "open": 114., "high": 116., "low": 112., "close": 115., "volume": 1},
    ]
    return base, pre


def test_verified_nxt_premarket_is_calculation_input_independent_of_display_toggle(case):
    base, pre = _premarket_rows(False)
    window, flow, session, pool, rules, _ = flow_case(case, base, count=4, regular=False)
    before = deepcopy(pool["candles"])
    key = flow._integrated_display_sidecar_key(session)
    flow._integrated_display_sidecars[key] = {
        "stock_code": session.request.stock.code,
        "timeframe_key": session.request.timeframe_key,
        "timeframe_minutes": session.request.timeframe_minutes,
        "market_data_identity": f"{session.request.stock.code}_AL",
        "market_source": "INTEGRATED",
        "verified_nxt_eligible": True,
        "display_only": True,
        "candles": [*pre, *base],
        "coverage_count": 7,
    }
    all_view = flow._validation_pool_for_session(session, pool, target_count=20)
    all_times = [r["time"] for r in all_view["candles"]]
    assert all(t in all_times for t in (x["time"] for x in pre))
    assert pool["candles"] == before
    assert all_view["market_source"] != "KRX", "composite calculation source must not masquerade as KRX"
    rules["validation_market_scope"]["regular_market_only"] = True
    # Display-only setting must not alter the calculation source.
    settings = legacy.ValidationSettingsSnapshot(rules)
    other_session = legacy.ValidationSession(legacy.ValidationRequest(case.stock, settings, 3), operation_active_reader=lambda: False)
    regular_view = flow._validation_pool_for_session(other_session, pool, target_count=20)
    assert [r["time"] for r in regular_view["candles"]] == all_times


def test_missing_untrusted_premarket_cannot_become_filter_source(case):
    base, pre = _premarket_rows(False)
    window, flow, session, pool, rules, _ = flow_case(case, base, count=4, regular=False)
    key = flow._integrated_display_sidecar_key(session)
    flow._integrated_display_sidecars[key] = {
        "stock_code": case.stock.code, "timeframe_key": "M3", "timeframe_minutes": 3,
        "market_data_identity": "005930_AL", "market_source": "KRX",
        "display_only": True, "candles": pre, "coverage_count": 3,
    }
    view = flow._validation_pool_for_session(session, pool, target_count=20)
    assert view["candles"] == base


def test_filter_values_exist_for_verified_premarket_only_on_derived_source(case):
    base, pre = _premarket_rows(False)
    window, flow, session, pool, rules, _ = flow_case(case, base, count=4, regular=False)
    key = flow._integrated_display_sidecar_key(session)
    flow._integrated_display_sidecars[key] = {
        "stock_code": case.stock.code, "timeframe_key": "M3", "timeframe_minutes": 3,
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "verified_nxt_eligible": True,
        "display_only": True, "candles": [*pre, *base], "coverage_count": 7,
    }
    merged = flow._validation_pool_for_session(session, pool, target_count=20)
    assert any(str(c["time"]).endswith("080300") for c in merged["candles"])


def test_real_sized_normal_base_promotes_verified_premarket_without_losing_view(case):
    import time
    from tests.test_indicator_follow_signal_validation_v4_warmup_legend import archive_rows
    rows = archive_rows()
    window, flow, session, pool, rules, _ = flow_case(
        case, rows, count=500, regular=False,
    )
    key = id(window)
    flow._historical_pools[key] = pool
    flow._validation_sessions[key] = session
    flow._history_targets[key] = 500
    window.set_historical_candle_pool(rows[-1400:], chart_candle_count=500)
    window.install_normal_base(session.request.settings_snapshot, "M3")
    window._set_time_view(160, 270)
    anchor = window._capture_history_extension_view_anchor()
    sidecar = [dict(c) for c in rows[-480:]]
    pre = [
        dict(rows[-1], time="20261008080000", close=269100.),
        dict(rows[-1], time="20261008080300", close=269500.),
        dict(rows[-1], time="20261008080600", close=270100.),
    ]
    sidecar.extend(pre)
    sidecar.sort(key=lambda c: c["time"])
    flow._integrated_display_sidecars[
        flow._integrated_display_sidecar_key(session)
    ] = {
        "stock_code": case.stock.code, "timeframe_key": "M3",
        "timeframe_minutes": 3, "market_data_identity": "005930_AL",
        "market_source": "INTEGRATED", "verified_nxt_eligible": True,
        "display_only": True, "candles": sidecar,
        "coverage_count": len(sidecar),
    }
    original = deepcopy(pool["candles"])
    flow._apply_integrated_display_sidecar(window, session)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not all(
        any(c["time"] == row["time"] for c in window._calculation_candles)
        for row in pre
    ):
        case.app.processEvents()
        time.sleep(0.015)
    case.app.processEvents()
    assert all(any(c["time"] == row["time"] for c in window._calculation_candles)
               for row in pre)
    assert window._normal_base_ready
    assert not window.marking_display_check.isChecked()
    assert window.replay_snapshot is None
    assert pool["candles"] == original
    assert window.visible_candle_span == 270.
    assert window._candles[int(window.visible_start_index)]["time"] == anchor.visible_start_time
    indexes = [i for i, c in enumerate(window._candles)
               if c["time"] in {row["time"] for row in pre}]
    assert len(indexes) == len(pre)
    assert all(window._chart_calculation_indexes[i] is not None for i in indexes)
    assert any(channel == "RSI" and
               all(values[i] is not None for i in indexes)
               for _identity, channel, values in window.visualization_cache.series)
    assert any(channel == "OSC" and
               all(values[i] is not None for i in indexes)
               for _identity, channel, values in window.visualization_cache.series)


def test_validated_premarket_local_replay_has_calculation_ranges(case):
    base, pre = _premarket_rows(False)
    window, flow, session, pool, rules, _ = flow_case(
        case, base, count=4, regular=False,
    )
    key = flow._integrated_display_sidecar_key(session)
    flow._integrated_display_sidecars[key] = {
        "stock_code": case.stock.code, "timeframe_key": "M3",
        "timeframe_minutes": 3, "market_data_identity": "005930_AL",
        "market_source": "INTEGRATED", "verified_nxt_eligible": True,
        "display_only": True,
        "candles": sorted([*base, *pre], key=lambda c: c["time"]),
    }
    view, deficit = flow._normal_base_history_view(session, pool, 7)
    calculated = view["candles"]
    timestamps = [row["time"] for row in calculated]
    for row in pre:
        assert row["time"] in timestamps
    assert view["market_source"] == "VALIDATION_COMPOSITE"
    assert view["snapshot"].market_source == "VALIDATION_COMPOSITE"
    from gui_indicator_follow_signal_validation_v4_window import (
        _calculation_bounds_for_display_range,
    )
    display = view["display_candles"]
    pre_indexes = [
        i for i, row in enumerate(display)
        if row["time"] in {c["time"] for c in pre}
    ]
    assert len(pre_indexes) == len(pre)
    assert _calculation_bounds_for_display_range(
        calculated, display, pre_indexes[0], pre_indexes[-1],
    ) is not None
    bounds = _calculation_bounds_for_display_range(
        calculated, display, pre_indexes[0], pre_indexes[-1],
    )
    flow._replay_factory = legacy.ValidationHistoricalReplay
    replay = flow._prepare_replay_result_from_pool(
        session, pool, history_target=7,
        chart_range=(pre_indexes[0], pre_indexes[-1]),
        chart_range_times=(pre[0]["time"], pre[-1]["time"]),
    )
    assert replay.ok and replay.snapshot is not None
    assert replay.snapshot.evaluated_start_index == bounds[0]
    assert replay.snapshot.evaluated_end_index == bounds[1]
    replay_times = {c["time"] for c in replay.snapshot.to_candles()}
    assert all(row["time"] in replay_times for row in pre)
