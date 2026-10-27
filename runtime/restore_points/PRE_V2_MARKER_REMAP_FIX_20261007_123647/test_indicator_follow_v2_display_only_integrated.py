# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

import gui_indicator_follow_signal_validation_window as window_module
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationChartCanvas,
    _calculation_indexes_for_display,
    _calculation_indexes_for_mixed_display,
    prepare_signal_validation_presentation,
)
from indicator_follow_signal_validation_visualization import (
    FAMILY_PRICE_COMPARISON,
    PRICE_AXIS,
    ValidationFilterDescriptor,
    ValidationIndicatorSeriesCache,
)
from tests import test_indicator_follow_signal_validation_v2 as v2_tests

ValidationReplayEntry = v2_tests.ValidationReplayEntry
ValidationReplaySnapshot = v2_tests.ValidationReplaySnapshot


def _candle(timestamp: str, close: float) -> dict[str, object]:
    return {
        "time": timestamp,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1,
    }


def _entry(candle: dict[str, object], index: int, side: str) -> ValidationReplayEntry:
    timestamp = str(candle["time"])
    return ValidationReplayEntry(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=timestamp,
        signal=side,
        reason="fixture",
        signal_index=index,
        signal_time=timestamp,
        delay_bar=0,
        matched_groups=[],
        details=[],
        trace={"conditions": [], "groups": [], "aggregations": []},
    )


@pytest.fixture
def v2_case():
    case_class = v2_tests.IndicatorFollowSignalValidationV2Test
    case_class.setUpClass()
    case = case_class(methodName="runTest")
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


def test_series_line_bridges_only_marked_display_only_gaps(v2_case):
    candles = [
        _candle(f"20261007090{index}00", 100.0 + index)
        for index in range(4)
    ]
    canvas = IndicatorFollowSignalValidationChartCanvas(
        candles,
        [],
        bridgeable_display_only_indexes=frozenset({1, 2}),
    )
    v2_case.widgets.append(canvas)

    path, has_segment = canvas._series_line_data_path(
        (10.0, None, None, 20.0),
        0,
        4,
    )

    assert has_segment
    assert path.elementCount() == 2
    assert path.elementAt(0).isMoveTo()
    assert path.elementAt(1).isLineTo()
    assert (path.elementAt(0).x, path.elementAt(0).y) == (0.0, 10.0)
    assert (path.elementAt(1).x, path.elementAt(1).y) == (3.0, 20.0)


def test_series_line_unmarked_none_still_splits_segment(v2_case):
    candles = [
        _candle(f"20261007090{index}00", 100.0 + index)
        for index in range(5)
    ]
    canvas = IndicatorFollowSignalValidationChartCanvas(candles, [])
    v2_case.widgets.append(canvas)

    path, has_segment = canvas._series_line_data_path(
        (10.0, 11.0, None, 20.0, 21.0),
        0,
        5,
    )

    assert has_segment
    assert path.elementCount() == 4
    assert path.elementAt(0).isMoveTo()
    assert path.elementAt(1).isLineTo()
    assert path.elementAt(2).isMoveTo()
    assert path.elementAt(3).isLineTo()


def test_strict_display_mapping_still_rejects_display_only_candle():
    calculation = [_candle("20261007090000", 100.0)]
    display = [_candle("20261007083000", 90.0), calculation[0]]

    with pytest.raises(ValueError, match="DISPLAY_CANDLE_TIME_IDENTITY_INVALID"):
        _calculation_indexes_for_display(calculation, display)


def test_mixed_display_mapping_preserves_none_and_strict_backed_order():
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    display = [_candle("20261007083000", 90.0), *calculation]

    assert _calculation_indexes_for_mixed_display(calculation, display) == (
        None,
        0,
        1,
    )

    with pytest.raises(ValueError, match="DISPLAY_CANDLE_TIME_IDENTITY_INVALID"):
        _calculation_indexes_for_mixed_display(
            calculation,
            [calculation[1], calculation[0]],
        )
    with pytest.raises(ValueError, match="DISPLAY_CANDLE_TIME_IDENTITY_INVALID"):
        _calculation_indexes_for_mixed_display(
            calculation,
            [display[0], display[0]],
        )
    with pytest.raises(ValueError, match="CALCULATION_CANDLE_TIME_IDENTITY_INVALID"):
        _calculation_indexes_for_mixed_display(
            [calculation[0], dict(calculation[0])],
            [calculation[0]],
        )


def test_prepared_trusted_mixed_projection_has_no_unbacked_evidence(
    v2_case,
    monkeypatch,
):
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    display = [_candle("20261007083000", 90.0), *calculation]
    entries = [
        _entry(calculation[0], 0, "BUY"),
        _entry(calculation[1], 1, "SELL"),
    ]
    descriptor = ValidationFilterDescriptor(
        identity="AVG",
        family=FAMILY_PRICE_COMPARISON,
        label="AVG",
        axis=PRICE_AXIS,
        sides=("BUY", "SELL"),
        series_keys=("AVG_PRICE",),
        parameter_json=json.dumps({
            "condition": {"target": "CLOSE", "compare_target": "AVG_PRICE"}
        }),
        evidence_keys=(),
    )
    settings = v2_case._seed().settings_snapshot
    monkeypatch.setattr(
        window_module,
        "build_validation_indicator_cache",
        lambda candles, _rules, _descriptors, *, entries=None: (
            ValidationIndicatorSeriesCache(
                candle_count=len(candles),
                series=((
                    "AVG",
                    "AVG_PRICE",
                    tuple(float(candle["close"]) for candle in candles),
                ),),
            )
        ),
    )
    strict = prepare_signal_validation_presentation(
        calculation,
        entries,
        settings,
        chart_start_index=0,
        chart_count=2,
        display_candles=calculation,
        visualization_descriptors=(descriptor,),
    )
    mixed = prepare_signal_validation_presentation(
        calculation,
        entries,
        settings,
        chart_start_index=0,
        chart_count=3,
        display_candles=display,
        visualization_descriptors=(descriptor,),
        display_only_integrated=True,
    )

    assert mixed.chart_calculation_indexes == (None, 0, 1)
    assert mixed.visualization_cache.values_for("AVG", "AVG_PRICE") == (
        None,
        *strict.visualization_cache.values_for("AVG", "AVG_PRICE"),
    )
    assert [entry.evaluation_index for entry in mixed.chart_entries] == [1, 2]
    assert set(mixed.visualization_active_by_marker) == {(1, "BUY"), (2, "SELL")}
    assert set(mixed.signal_tooltips).issubset({(1, "BUY"), (2, "SELL")})


def test_window_trusted_display_keeps_calculation_and_exposes_backed_bounds(v2_case):
    window = v2_case._window()
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    display = [_candle("20261007083000", 90.0), *calculation]
    window.set_historical_candle_pool(calculation, chart_candle_count=2)
    calculation_before = tuple(window._calculation_candles)

    window.set_historical_display_candles(
        display,
        display_only_integrated=True,
    )

    assert tuple(window._calculation_candles) == calculation_before
    assert window._chart_calculation_indexes == (None, 0, 1)
    assert window._chart_pool_start_index == 0
    assert window.calculation_bounds_for_display_range(0, 0) is None
    assert window.calculation_bounds_for_display_range(0, 2) == (0, 1)


def test_window_canvas_bridges_exact_active_mixed_mapping_none_positions(v2_case):
    window = v2_case._window()
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    display = [
        _candle("20261007083000", 90.0),
        calculation[0],
        _candle("20261007090100", 95.0),
        calculation[1],
    ]
    replay = ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=5,
        settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
        historical_request_id="INTEGRATED-BRIDGE-MASK",
        evaluated_start_index=0,
        evaluated_end_index=1,
        dropped_raw_rows_count=0,
        candles=calculation,
        entries=[],
    )
    window.set_historical_candle_pool(calculation, chart_candle_count=4)
    window.set_replay_snapshot(replay)

    assert getattr(
        window.canvas,
        "_bridgeable_display_only_indexes",
        frozenset(),
    ) == frozenset()

    window.set_display_only_integrated_candles(display)

    assert window._chart_calculation_indexes == (None, 0, None, 1)
    assert getattr(
        window.canvas,
        "_bridgeable_display_only_indexes",
        frozenset(),
    ) == frozenset({0, 2})

    window.validation_regular_market_only_check.setChecked(True)
    v2_case.app.processEvents()

    assert getattr(
        window.canvas,
        "_bridgeable_display_only_indexes",
        frozenset(),
    ) == frozenset()


def test_regular_only_toggle_keeps_calculation_bytes_with_integrated_sidecar(v2_case):
    window = v2_case._window()
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    display = [
        _candle("20261007083000", 90.0),
        *calculation,
        _candle("20261007154500", 110.0),
    ]
    replay = ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=5,
        settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
        historical_request_id="INTEGRATED-TOGGLE",
        evaluated_start_index=0,
        evaluated_end_index=1,
        dropped_raw_rows_count=0,
        candles=calculation,
        entries=[],
    )
    window.set_historical_candle_pool(calculation, chart_candle_count=2)
    window.set_replay_snapshot(replay)
    window.set_display_only_integrated_candles(display)
    expected_integrated_times = [
        candle["time"] for candle in window._display_only_integrated_candles
    ]
    before = json.dumps(
        window._calculation_candles,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    runs = []
    window.validation_run_requested.connect(runs.append)

    window.validation_regular_market_only_check.setChecked(True)
    v2_case.app.processEvents()
    assert [candle["time"] for candle in window._candles] == [
        "20261007090000",
        "20261007090300",
    ]
    window.validation_regular_market_only_check.setChecked(False)
    v2_case.app.processEvents()

    after = json.dumps(
        window._calculation_candles,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert after == before
    assert runs == []
    assert [candle["time"] for candle in window._candles] == expected_integrated_times


def test_integrated_display_remaps_existing_signal_markers_without_loss(v2_case):
    window = v2_case._window()
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    entries = [
        _entry(calculation[0], 0, "BUY"),
        _entry(calculation[1], 1, "SELL"),
    ]
    replay = ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=3,
        settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
        historical_request_id="INTEGRATED-MARKER-REMAP",
        evaluated_start_index=0,
        evaluated_end_index=1,
        dropped_raw_rows_count=0,
        candles=calculation,
        entries=[],
    )
    window.set_historical_candle_pool(calculation, chart_candle_count=2)
    window.set_replay_snapshot(replay)
    window.set_signal_marker_entries(entries)
    assert {
        (marker["evaluation_time"], marker["side"])
        for marker in window.canvas.marker_records()
    } == {
        ("20261007090000", "BUY"),
        ("20261007090300", "SELL"),
    }

    window.set_display_only_integrated_candles([
        _candle("20261007083000", 90.0),
        calculation[0],
        calculation[1],
        _candle("20261007154500", 110.0),
    ])

    assert {
        (marker["evaluation_time"], marker["side"])
        for marker in window.canvas.marker_records()
    } == {
        ("20261007090000", "BUY"),
        ("20261007090300", "SELL"),
    }
    assert [
        (entry.evaluation_time, entry.evaluation_index, entry.signal)
        for entry in window._signal_marker_entries
    ] == [
        ("20261007090000", 1, "BUY"),
        ("20261007090300", 2, "SELL"),
    ]


def test_display_only_range_replay_is_benign(v2_case):
    window = v2_case._window()
    calculation = [
        _candle("20261007090000", 100.0),
        _candle("20261007090300", 101.0),
    ]
    window.set_historical_candle_pool(calculation, chart_candle_count=2)
    window.set_historical_display_candles(
        [_candle("20261007083000", 90.0)],
        display_only_integrated=True,
    )
    window._set_validation_range(0, 0)
    calculation_entries_before = tuple(window._calculation_entries)
    replay_before = window._replay_snapshot

    window.apply_range_replay_snapshot(ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=5,
        settings_hash=window._signal_validation_seed.settings_snapshot.rules_hash,
        historical_request_id="DISPLAY-ONLY-RANGE",
        evaluated_start_index=0,
        evaluated_end_index=1,
        dropped_raw_rows_count=0,
        candles=calculation,
        entries=[_entry(calculation[0], 0, "BUY")],
    ))

    assert tuple(window._calculation_entries) == calculation_entries_before
    assert window._replay_snapshot is replay_before
    assert not window._range_replay_entries_available
    assert window._visualization_data_error == ""


def test_legacy_window_display_mapping_remains_strict(v2_case):
    window = v2_case._window()
    calculation = [_candle("20261007090000", 100.0)]
    window.set_historical_candle_pool(calculation, chart_candle_count=1)

    with pytest.raises(ValueError, match="DISPLAY_CANDLE_TIME_IDENTITY_INVALID"):
        window.set_historical_display_candles([
            _candle("20261007083000", 90.0),
            calculation[0],
        ])
