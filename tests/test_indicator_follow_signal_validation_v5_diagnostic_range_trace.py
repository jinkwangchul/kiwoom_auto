"""V5 replay results remain intact without retired parity diagnostics."""
from __future__ import annotations

import pytest

import gui_indicator_follow_signal_validation_v5_window as window_module
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests.test_indicator_follow_signal_validation_v5_baseline import case


def _entry(rows, index, side, *, signal=True):
    return window_module.ValidationReplayEntry(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=rows[index]["time"],
        signal=side if signal else None,
        reason="diagnostic fixture",
        signal_index=index if signal else None,
        signal_time=rows[index]["time"] if signal else None,
        delay_bar=0,
        matched_groups=[],
        details=[],
        trace={},
    )


def _prepared(case):
    window = baseline.window_for(case)
    rows = baseline.candles(40)
    window.set_historical_candle_pool(rows, chart_candle_count=20)
    settings = window._signal_validation_seed.settings_snapshot
    window.install_normal_base(settings, "M1")
    window._set_validation_range(5, 12)
    entries = [
        _entry(rows, 31, "SELL"),
        _entry(rows, 27, "BUY", signal=False),
        _entry(rows, 26, "BUY"),
    ]
    snapshot = window_module.ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=1,
        timeframe_key="M1",
        settings_hash=settings.rules_hash,
        historical_request_id="selected-al-request",
        evaluated_start_index=25,
        evaluated_end_index=32,
        dropped_raw_rows_count=0,
        candles=rows[:33],
        entries=entries,
    )
    return window, rows, settings, snapshot


def _parity(events):
    return [payload for event, payload in events if event == "window.range_replay.parity_evidence_v1"]


@pytest.mark.parametrize("trace_enabled", [False, True])
def test_replay_keeps_signals_without_parity_work(case, monkeypatch, trace_enabled):
    window, rows, settings, snapshot = _prepared(case)
    events = []
    monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_TRACE", "1" if trace_enabled else "0")
    monkeypatch.setattr(window_module, "trace_signal_validation_event",
                        lambda event, **payload: events.append((event, payload)))

    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)

    assert window._range_replay_entries_available
    assert [(entry.evaluation_index, entry.signal, entry.signal_time)
            for entry in window._marker_entries_for_display()] == [
        (11, "SELL", rows[31]["time"]), (6, "BUY", rows[26]["time"]),
    ]
    assert window.validation_range == (5, 12)
    assert window._validation_range_times == (rows[25]["time"], rows[32]["time"])
    assert _parity(events) == []


def test_trace_exception_does_not_change_accepted_ui_result(case, monkeypatch):
    window, _, settings, snapshot = _prepared(case)
    monkeypatch.setenv("KIWOOM_SIGNAL_VALIDATION_TRACE", "1")

    def broken_trace(event, **payload):
        if event == "window.range_replay.parity_evidence_v1":
            raise RuntimeError("diagnostic sink failed")

    monkeypatch.setattr(window_module, "trace_signal_validation_event", broken_trace)
    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)
    assert window._range_replay_entries_available
    assert [(entry.evaluation_index, entry.signal) for entry in window._marker_entries_for_display()] == [
        (11, "SELL"), (6, "BUY"),
    ]
