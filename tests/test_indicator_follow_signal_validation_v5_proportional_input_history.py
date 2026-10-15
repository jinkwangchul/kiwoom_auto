"""Red contracts for proportional V5 history gestures; no broker or GUI launch."""
from __future__ import annotations

from threading import Event
from unittest.mock import patch

import pytest
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests import test_indicator_follow_signal_validation_v5_wheel_history_boundary as cases


@pytest.fixture
def case():
    yield from cases.case.__wrapped__()


@pytest.mark.parametrize("steps,expected", [(1, 4000), (4, 4500)])
def test_cap_wheel_request_reflects_accumulated_delta(case, steps, expected):
    window = cases.window_with_rows(case, 3500)
    flow, key = cases.bound_flow(window)
    with patch.object(flow, "_run_validation") as run:
        for _ in range(steps):
            cases.wheel(window.canvas, -120)
        QApplication.processEvents()
        QTest.qWait(400)
        assert flow._history_targets[key] == expected
        run.assert_called_once()
    assert window.visible_candle_span == 3500


@pytest.mark.parametrize("pixels", [100, 500, 1000])
def test_drag_over_oldest_edge_preserves_requested_distance(case, pixels):
    window = cases.window_with_rows(case, 3500)
    flow, key = cases.bound_flow(window)
    canvas = window.canvas
    wanted_bars = pixels / canvas.pixels_per_candle
    canvas._queue_time_pan_delta(float(pixels))
    QApplication.processEvents()
    assert window.visible_start_index == 0
    assert window.desired_history_target >= min(5000, 3500 + wanted_bars)
    with patch.object(flow, "_run_validation") as run:
        flow._request_history_extension(window)
        assert flow._history_targets[key] >= min(5000, 3500 + wanted_bars)
        run.assert_called_once()


def test_burst_during_fetch_updates_pending_logical_target(case):
    window = cases.window_with_rows(case, 3500)
    flow, key = cases.bound_flow(window)
    with patch.object(flow, "_run_validation") as run:
        cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        assert run.call_count == 1
        for _ in range(4):
            cases.wheel(window.canvas, -120)
        QApplication.processEvents()
        QTest.qWait(400)
        assert flow._history_extension_inflight[key]["requested_target"] == 4000
        assert window.desired_history_target >= 4200


def test_burst_continues_after_same_source_prepend(case):
    window = cases.window_with_rows(case, 3500)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M5")
    window._set_time_view(0, 3500)
    flow, key = cases.bound_flow(window)
    old_time = window._candles[0]["time"]
    with patch.object(flow, "_run_validation") as run:
        cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        for _ in range(4):
            cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        assert run.call_count == 1
        rows = cases.baseline.candles(4000)
        window.set_historical_candle_pool(rows, chart_candle_count=4000)
        window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M5")
        assert window.desired_history_target > 4000
        flow._complete_history_extension(key, success=True)
        QApplication.processEvents()
        assert flow._history_targets[key] == 4500
        assert run.call_count == 2
    assert window._candles[round(window.visible_start_index)]["time"] == old_time
    assert window.time_navigation_scrollbar.value() == 500_000


def test_reverse_wheel_cancels_followup(case):
    window = cases.window_with_rows(case, 3500)
    flow, key = cases.bound_flow(window)
    with patch.object(flow, "_run_validation") as run:
        cases.wheel(window.canvas, -120)
        QTest.qWait(400)
        for _ in range(4):
            cases.wheel(window.canvas, -120)
        cases.wheel(window.canvas, 120)
        QApplication.processEvents()
        assert window.desired_history_target == 0
        rows = cases.baseline.candles(4000)
        window.set_historical_candle_pool(rows, chart_candle_count=4000)
        window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
        flow._complete_history_extension(key, success=True)
        QApplication.processEvents()
        assert flow._history_targets[key] == 4000
        assert run.call_count == 1


def test_drag_reversal_cancels_unfetched_distance(case):
    window = cases.window_with_rows(case, 3500)
    canvas = window.canvas
    canvas._queue_time_pan_delta(500.0)
    assert window.desired_history_target > 3500
    canvas._queue_time_pan_delta(-500.0)
    assert window.desired_history_target == 0


@pytest.mark.parametrize("warmup,expected", [(0, 5000), (700, 4300)])
def test_logical_target_respects_chart_cap_and_hidden_warmup(case, warmup, expected):
    window = cases.window_with_rows(case, 3500)
    flow, key = cases.bound_flow(window)
    window.desired_history_target = 5000
    with (patch.object(flow, "_run_validation") as run,
          patch.object(cases.flow_module, "DEFAULT_CANDLES_MAX_COUNT", 5000),
          patch.object(cases.flow_module, "required_validation_history_context_bars",
                       return_value=warmup)):
        flow._request_history_extension(window)
        assert flow._history_targets[key] == expected
        assert flow._history_targets[key] + warmup <= 5000
        run.assert_called_once()


def test_changed_source_drops_unfetched_distance(case):
    window = cases.window_with_rows(case, 3500)
    window.desired_history_target = 4500
    rows = cases.baseline.candles(4000)
    rows[500] = {**rows[500], "close": "1"}
    window.set_historical_candle_pool(rows, chart_candle_count=4000)
    assert window.desired_history_target == 0
    assert window._pending_history_extension_view_anchor is None


@pytest.mark.parametrize("invalidated", ["generation", "settings", "timeframe"])
def test_queued_followup_requires_same_validated_context(case, invalidated):
    window = cases.window_with_rows(case, 3500)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M5")
    flow, key = cases.bound_flow(window)
    window.desired_history_target = 4500
    window._historical_candle_count = 4000
    flow._history_targets[key] = 4000
    flow._history_extension_inflight[key] = {
        "previous_target": 3500,
        "requested_target": 4000,
        "cancel_event": Event(),
    }
    with patch.object(flow, "_run_validation") as run:
        flow._complete_history_extension(key, success=True)
        if invalidated == "generation":
            flow._request_generation[key] += 1
        elif invalidated == "settings":
            window._result_settings_snapshot = None
        else:
            window._normal_base_timeframe_key = "M15"
        QApplication.processEvents()
        run.assert_not_called()


def test_validated_replay_history_prepend_preserves_dynamic_target(case):
    from gui_indicator_follow_signal_validation_v5_window import prepare_signal_validation_presentation
    from tests import test_indicator_follow_signal_validation_v5_history_hash_ownership as snapshots

    window = cases.window_with_rows(case, 40)
    settings = window._signal_validation_seed.settings_snapshot
    candles = cases.baseline.candles(50)
    window.set_replay_snapshot(snapshots._snapshot(
        window, candles[-40:], settings, "dynamic-replay-prepend", [],
    ))
    window.desired_history_target = 80.0
    prepared = prepare_signal_validation_presentation(
        candles, [], settings, chart_start_index=0, chart_count=50,
    )
    window.apply_history_browse_presentation(
        candles, chart_candle_count=50, prepared_presentation=prepared,
    )
    assert window.desired_history_target == 80.0
    assert len(window._candles) == 50


def test_new_unrelated_replay_cancels_dynamic_target(case):
    from tests import test_indicator_follow_signal_validation_v5_history_hash_ownership as snapshots

    window = cases.window_with_rows(case, 40)
    settings = window._signal_validation_seed.settings_snapshot
    candles = cases.baseline.candles(40)
    window.desired_history_target = 80.0
    window.set_replay_snapshot(snapshots._snapshot(
        window, candles, settings, "unrelated-user-replay", [],
    ))
    assert window.desired_history_target == 0.0
