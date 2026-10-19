"""Opt-in V5 range replay evidence, with no broker or runtime trace file."""
from __future__ import annotations

import hashlib
import json

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


def _with_candles(snapshot, candles):
    return window_module.ValidationReplaySnapshot(
        stock=snapshot.stock,
        timeframe_minutes=snapshot.timeframe_minutes,
        timeframe_key=snapshot.timeframe_key,
        settings_hash=snapshot.settings_hash,
        historical_request_id=snapshot.historical_request_id,
        evaluated_start_index=snapshot.evaluated_start_index,
        evaluated_end_index=snapshot.evaluated_end_index,
        dropped_raw_rows_count=snapshot.dropped_raw_rows_count,
        candles=candles,
        entries=snapshot.to_entries(),
    )


def _parity(events):
    return [payload for event, payload in events if event == "window.range_replay.parity_evidence_v1"]


def test_disabled_never_builds_or_emits_parity(case, monkeypatch):
    window, _, settings, snapshot = _prepared(case)
    events = []
    monkeypatch.setattr(window_module, "signal_validation_trace_enabled", lambda: False)
    monkeypatch.setattr(window_module, "trace_signal_validation_event", lambda event, **payload: events.append((event, payload)))
    monkeypatch.setattr(window, "_trace_range_replay_parity_evidence", lambda *_: pytest.fail("disabled diagnostic evaluated"))

    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)

    assert _parity(events) == []
    assert [entry.signal for entry in window._marker_entries_for_display()] == ["SELL", "BUY"]


def test_enabled_emits_exact_source_rules_ranges_and_sorted_signals(case, monkeypatch):
    window, rows, settings, snapshot = _prepared(case)
    events = []
    monkeypatch.setattr(window_module, "signal_validation_trace_enabled", lambda: True)
    monkeypatch.setattr(window_module, "trace_signal_validation_event", lambda event, **payload: events.append((event, payload)))

    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)

    [evidence] = _parity(events)
    assert evidence["schema_version"] == 1
    assert evidence["stock_code"] == window.stock.code
    assert evidence["timeframe_key"] == "M1"
    assert evidence["window_key"] == id(window)
    assert evidence["historical_request_id"] == "selected-al-request"
    assert evidence["rules_hash"] == settings.rules_hash
    assert evidence["rules_canonical_json"] == settings.canonical_json
    assert evidence["source_calculation_candles"] == {
        "count": 40,
        "first_time": rows[0]["time"],
        "last_time": rows[-1]["time"],
        "sha256": hashlib.sha256(json.dumps(
            window._calculation_candles, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest(),
    }
    assert evidence["display_range"] == {
        "start_index": 5, "end_index": 12,
        "start_time": rows[25]["time"], "end_time": rows[32]["time"],
    }
    assert evidence["calculation_range"] == {
        "start_index": 25, "end_index": 32,
        "start_time": rows[25]["time"], "end_time": rows[32]["time"],
    }
    assert evidence["signals"] == [
        {"side": "BUY", "evaluation_index": 6, "evaluation_time": rows[26]["time"],
         "signal_index": 6, "signal_time": rows[26]["time"], "delay_bar": 0},
        {"side": "SELL", "evaluation_index": 11, "evaluation_time": rows[31]["time"],
         "signal_index": 11, "signal_time": rows[31]["time"], "delay_bar": 0},
    ]
    assert evidence["signal_counts"] == {"BUY": 1, "SELL": 1}


@pytest.mark.parametrize("invalid", ["hash", "time", "range_time", "pending", "canceled", "duplicate", "evaluation_start", "evaluation_end"])
def test_bad_identity_or_duplicate_suppresses_success(case, monkeypatch, invalid):
    window, rows, settings, snapshot = _prepared(case)
    events = []
    monkeypatch.setattr(window_module, "signal_validation_trace_enabled", lambda: True)
    monkeypatch.setattr(window_module, "trace_signal_validation_event", lambda event, **payload: events.append((event, payload)))
    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)
    assert len(_parity(events)) == 1
    events.clear()

    if invalid == "hash":
        object.__setattr__(settings, "rules_hash", "invalid")
    elif invalid == "time":
        window._range_replay_entries[0] = _entry(rows, 30, "SELL")
        window._range_replay_entries[0] = window._range_replay_entries[0].remap_indexes(11, 11)
    elif invalid == "range_time":
        window._validation_range_times = (rows[24]["time"], rows[32]["time"])
    elif invalid == "pending":
        window._range_replay_revalidation_pending = True
    elif invalid == "canceled":
        window._clear_range_replay_override()
    elif invalid == "evaluation_start":
        object.__setattr__(snapshot, "evaluated_start_index", 24)
    elif invalid == "evaluation_end":
        object.__setattr__(snapshot, "evaluated_end_index", 31)
    else:
        window._range_replay_entries.append(window._range_replay_entries[0])
    window._trace_range_replay_parity_evidence(snapshot, (25, 32))
    assert _parity(events) == []


@pytest.mark.parametrize("invalid", ["ohlcv", "timestamp", "short_tail", "empty"])
def test_snapshot_prefix_mismatch_suppresses_success(case, monkeypatch, invalid):
    window, rows, settings, snapshot = _prepared(case)
    events = []
    monkeypatch.setattr(window_module, "signal_validation_trace_enabled", lambda: True)
    monkeypatch.setattr(window_module, "trace_signal_validation_event", lambda event, **payload: events.append((event, payload)))
    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)
    assert len(_parity(events)) == 1
    events.clear()

    altered = snapshot.to_candles()
    if invalid == "ohlcv":
        altered[10]["close"] += 1
    elif invalid == "timestamp":
        altered[10]["time"] = "20000101000000"
    elif invalid == "short_tail":
        altered.pop()
    else:
        altered.clear()
    window._trace_range_replay_parity_evidence(
        _with_candles(snapshot, altered), (25, 32)
    )
    assert _parity(events) == []


def test_trace_exception_does_not_change_accepted_ui_result(case, monkeypatch):
    window, _, settings, snapshot = _prepared(case)
    monkeypatch.setattr(window_module, "signal_validation_trace_enabled", lambda: True)

    def broken_trace(event, **payload):
        if event == "window.range_replay.parity_evidence_v1":
            raise RuntimeError("diagnostic sink failed")

    monkeypatch.setattr(window_module, "trace_signal_validation_event", broken_trace)
    window.apply_range_replay_snapshot(snapshot, settings_snapshot=settings)
    assert window._range_replay_entries_available
    assert [(entry.evaluation_index, entry.signal) for entry in window._marker_entries_for_display()] == [
        (11, "SELL"), (6, "BUY"),
    ]
