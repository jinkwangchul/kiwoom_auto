"""V5 history-browse ownership regression coverage for replay settings hashes."""
from __future__ import annotations

from copy import deepcopy
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import gui_indicator_follow_signal_validation_v5_window as window_module
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests.test_indicator_follow_signal_validation_v5_baseline import case


def _entry(rows, index: int, side: str):
    stamp = rows[index]["time"]
    return window_module.ValidationReplayEntry(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=stamp,
        signal=side,
        reason=f"history-hash {side} {index}",
        signal_index=index,
        signal_time=stamp,
        delay_bar=0,
        matched_groups=[f"{side}:{index}"],
        details=["controlled V5 history hash ownership"],
        trace={"history_hash_ownership": index},
    )


def _snapshot(window, rows, settings_snapshot, request_id, entries):
    return window_module.ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=1,
        timeframe_key="M1",
        settings_hash=settings_snapshot.rules_hash,
        historical_request_id=request_id,
        evaluated_start_index=0,
        evaluated_end_index=len(rows) - 1,
        dropped_raw_rows_count=0,
        candles=rows,
        entries=entries,
    )


def _settings_b(settings_a):
    rules_b = deepcopy(settings_a.to_dict())
    rules_b["history_hash_ownership_probe"] = "B"
    return window_module.ValidationSettingsSnapshot.from_rules(rules_b)


def _window_with_a_baseline(case):
    window = baseline.window_for(case)
    rows = baseline.candles(40)
    settings_a = window._signal_validation_seed.settings_snapshot
    markers_a = [_entry(rows, 21, "BUY"), _entry(rows, 34, "SELL")]
    window.set_historical_candle_pool(rows, chart_candle_count=20)
    window.install_normal_base(settings_a, "M1")
    window.stage_signal_marker_entries(markers_a)
    snapshot_a = _snapshot(window, rows, settings_a, "history-hash-A", markers_a)
    window.set_replay_snapshot(snapshot_a)
    window._set_validation_range(5, 12)
    return window, rows, settings_a, markers_a, snapshot_a


def test_v5_history_capture_requires_a_base_snapshot_after_b_range_replay(case):
    window, rows, settings_a, markers_a, snapshot_a = _window_with_a_baseline(case)
    baseline_state = window.capture_history_browse_state(settings_a)
    assert baseline_state is not None
    assert baseline_state["replay_snapshot"] is snapshot_a
    assert baseline_state["signal_entries"] == tuple(markers_a)

    settings_b = _settings_b(settings_a)
    snapshot_b = _snapshot(
        window,
        rows,
        settings_b,
        "history-hash-B-range",
        [_entry(rows, 27, "BUY"), _entry(rows, 30, "SELL")],
    )
    window.apply_range_replay_snapshot(snapshot_b, settings_snapshot=settings_b)

    # B is a range-only overlay.  A remains the validated source used by a
    # later history browse, while B remains visible for the selected range.
    assert window.replay_snapshot is snapshot_a
    assert window._result_settings_snapshot is settings_a
    assert window._validation_range_times == (rows[25]["time"], rows[32]["time"])
    assert window._range_replay_entries_available
    assert [entry.evaluation_time for entry in window._range_replay_entries] == [
        rows[27]["time"], rows[30]["time"],
    ]
    captured = window.capture_history_browse_state(settings_a)
    assert captured is not None
    assert captured["replay_snapshot"] is snapshot_a
    assert captured["signal_entries"] == tuple(markers_a)


def test_v5_history_prepend_keeps_time_range_and_revalidates_stale_b_once(case):
    window, rows, settings_a, markers_a, snapshot_a = _window_with_a_baseline(case)
    settings_b = _settings_b(settings_a)
    snapshot_b = _snapshot(
        window,
        rows,
        settings_b,
        "history-hash-B-range",
        [_entry(rows, 27, "BUY"), _entry(rows, 30, "SELL")],
    )
    window.apply_range_replay_snapshot(snapshot_b, settings_snapshot=settings_b)
    assert window.completed_cycles
    requested = []
    window.validation_range_evaluation_requested.connect(
        lambda start, end: requested.append((start, end))
    )

    extended_rows = baseline.candles(50)
    prepared = window_module.prepare_signal_validation_presentation(
        extended_rows,
        markers_a,
        settings_a,
        chart_start_index=0,
        chart_count=len(extended_rows),
    )
    window.apply_history_browse_presentation(
        extended_rows,
        chart_candle_count=len(extended_rows),
        prepared_presentation=prepared,
    )

    assert window.replay_snapshot is snapshot_a
    assert window._validation_range_times == (rows[25]["time"], rows[32]["time"])
    assert window.validation_range == (35, 42)
    assert not window._range_replay_entries_available
    assert window._range_replay_entries == []
    assert window._range_replay_calculation_entries == []
    assert window._range_replay_settings_snapshot is None
    assert window.completed_cycle_table.rowCount() == 0
    assert requested == [(35, 42)]


def test_v5_history_prepend_defers_default_scrollbar_state_until_anchor_restore(case):
    """A preserved 3,500-bar viewport must never publish a new-canvas 250-bar state."""
    window = baseline.window_for(case)
    settings = window._signal_validation_seed.settings_snapshot
    rows = baseline.candles(5_000)[500:]
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    window.install_normal_base(settings, "M1")
    snapshot = _snapshot(window, rows, settings, "scrollbar-anchor-base", [])
    window.set_replay_snapshot(snapshot)
    window._set_time_view(1_000, 3_500)
    window.select_evaluation_index(1_500)
    visible_start_time = window._candles[1_000]["time"]
    selected_time = window._candles[1_500]["time"]

    extended_rows = baseline.candles(5_000)
    prepared = window_module.prepare_signal_validation_presentation(
        extended_rows,
        [],
        settings,
        chart_start_index=0,
        chart_count=len(extended_rows),
    )
    scrollbar = window.time_navigation_scrollbar
    sync_states = []
    real_sync = window._sync_time_navigation_scrollbar

    def sync_and_capture():
        real_sync()
        sync_states.append((
            scrollbar.minimum(),
            scrollbar.maximum(),
            scrollbar.pageStep(),
            scrollbar.value(),
        ))

    with patch.object(window, "_sync_time_navigation_scrollbar", side_effect=sync_and_capture):
        window.apply_history_browse_presentation(
            extended_rows,
            chart_candle_count=len(extended_rows),
            prepared_presentation=prepared,
        )

    units = window._TIME_SCROLL_UNITS_PER_CANDLE
    assert sync_states == [(0, 1_500 * units, 3_500 * units, 1_500 * units)]
    assert window.visible_candle_span == 3_500
    assert window.visible_start_index == 1_500
    assert window._candles[round(window.visible_start_index)]["time"] == visible_start_time
    assert window._candles[window._selected_index]["time"] == selected_time


def test_v5_same_pool_replay_refresh_publishes_preserved_scrollbar_once(case):
    """A same-window cache refresh must not expose an install-time default view."""
    window = baseline.window_for(case)
    settings = window._signal_validation_seed.settings_snapshot
    rows = baseline.candles(500)
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    window.install_normal_base(settings, "M1")
    snapshot = _snapshot(window, rows, settings, "scrollbar-cache-refresh", [])
    window.set_replay_snapshot(snapshot)
    window._set_time_view(100, 250)
    window.select_evaluation_index(150)
    visible_start_time = window._candles[100]["time"]
    selected_time = window._candles[150]["time"]

    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    scrollbar = window.time_navigation_scrollbar
    sync_states = []
    real_sync = window._sync_time_navigation_scrollbar

    def sync_and_capture():
        real_sync()
        sync_states.append((
            scrollbar.minimum(),
            scrollbar.maximum(),
            scrollbar.pageStep(),
            scrollbar.value(),
        ))

    with patch.object(window, "_sync_time_navigation_scrollbar", side_effect=sync_and_capture):
        window.set_replay_snapshot(snapshot, presentation_only=True)

    units = window._TIME_SCROLL_UNITS_PER_CANDLE
    assert sync_states == [(0, 250 * units, 250 * units, 100 * units)]
    assert window._candles[round(window.visible_start_index)]["time"] == visible_start_time
    assert window._candles[window._selected_index]["time"] == selected_time


def test_v5_normal_base_range_overlay_never_becomes_a_replay(case):
    window = baseline.window_for(case)
    rows = baseline.candles(40)
    settings_b = _settings_b(window._signal_validation_seed.settings_snapshot)
    window.set_historical_candle_pool(rows, chart_candle_count=20)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    window._set_validation_range(5, 12)
    snapshot_b = _snapshot(
        window,
        rows,
        settings_b,
        "history-hash-normal-base-B",
        [_entry(rows, 27, "BUY"), _entry(rows, 30, "SELL")],
    )

    window.apply_range_replay_snapshot(snapshot_b, settings_snapshot=settings_b)

    assert window.replay_snapshot is None
    assert window._range_replay_from_normal_base
    assert window._range_replay_entries_available
    assert window.capture_history_browse_state(
        window._signal_validation_seed.settings_snapshot
    ) is None


def test_v5_matching_a_baseline_capture_keeps_a_candle_signal_provenance(case):
    window, _rows, settings_a, markers_a, snapshot_a = _window_with_a_baseline(case)

    captured = window.capture_history_browse_state(settings_a)

    assert captured is not None
    assert captured["replay_snapshot"] is snapshot_a
    assert captured["source_candles"] == window._calculation_candles
    assert captured["source_candles"] is not window._calculation_candles
    assert captured["signal_entries"] == tuple(markers_a)
    assert all(entry.trace["history_hash_ownership"] in (21, 34)
               for entry in captured["signal_entries"])


def test_v5_forged_b_replay_with_a_result_fails_closed(case):
    window, rows, settings_a, markers_a, snapshot_a = _window_with_a_baseline(case)
    settings_b = _settings_b(settings_a)
    forged_b = _snapshot(
        window,
        rows,
        settings_b,
        "history-hash-forged-B",
        [_entry(rows, 22, "BUY")],
    )

    with pytest.raises(ValueError, match="range replay settings identity mismatch"):
        window.apply_range_replay_snapshot(forged_b, settings_snapshot=settings_a)

    captured = window.capture_history_browse_state(settings_a)
    assert window.replay_snapshot is snapshot_a
    assert window._result_settings_snapshot is settings_a
    assert forged_b is not window.replay_snapshot
    assert captured is not None
    assert captured["signal_entries"] == tuple(markers_a)
