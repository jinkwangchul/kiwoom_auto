"""Deep V5 regressions for label handoff, range replay, and completed cycles."""
from __future__ import annotations

from copy import deepcopy
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import gui_indicator_follow_signal_validation_v5_window as window_module
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests.test_indicator_follow_signal_validation_v5_baseline import case


def _cycle(number: int, buy_indexes: tuple[int, ...], sell_index: int, price: float):
    return window_module.IndicatorFollowValidationCompletedCycle(
        cycle_number=number,
        buy_indexes=buy_indexes,
        buy_start_index=buy_indexes[0],
        buy_end_index=buy_indexes[-1],
        buy_count=len(buy_indexes),
        average_buy_price=price,
        sell_index=sell_index,
        sell_price=price + 2.0,
        estimated_return_percent=2.0,
    )


def _entry(rows, index: int, side: str, *, signal: bool = True):
    stamp = rows[index]["time"]
    return window_module.ValidationReplayEntry(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=stamp,
        signal=side if signal else None,
        reason=f"fixture {side} {index}",
        signal_index=index if signal else None,
        signal_time=stamp if signal else None,
        delay_bar=0,
        matched_groups=[f"{side}:{index}"],
        details=["controlled V5 range replay"],
        trace={"fixture_index": index},
    )


def _handoff_pair(case):
    rows = baseline.candles(36)
    prefix, old_rows = rows[:4], rows[4:]
    descriptor = baseline.descriptor(
        "handoff-ma5",
        window_module.FAMILY_MOVING_AVERAGE,
        "MA5",
        window_module.PRICE_AXIS,
        "BUY",
        "MA5",
    )

    def make_canvas(candles, markers):
        canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
            candles,
            markers,
            visualization_descriptors=(descriptor,),
            visualization_cache=window_module.ValidationIndicatorSeriesCache(
                len(candles),
                ((descriptor.identity, "MA5", tuple(row["close"] for row in candles)),),
            ),
        )
        case.widgets.append(canvas)
        return canvas

    old_markers = [
        {
            "evaluation_index": 8,
            "evaluation_time": old_rows[8]["time"],
            "side": "BUY",
            "identity": "buy-8",
            "tooltip": "BUY evidence",
        },
        {
            "evaluation_index": 20,
            "evaluation_time": old_rows[20]["time"],
            "side": "SELL",
            "identity": "sell-20",
            "tooltip": "SELL evidence",
        },
    ]
    new_markers = [
        {**marker, "evaluation_index": marker["evaluation_index"] + len(prefix)}
        for marker in old_markers
    ]
    old = make_canvas(old_rows, old_markers)
    new = make_canvas(prefix + old_rows, new_markers)
    cycles = (
        _cycle(1, (5, 7), 15, 100_012.0),
        _cycle(2, (9,), 22, 100_018.0),
    )
    for canvas, offset in ((old, 0), (new, len(prefix))):
        canvas.resize(741, 463)
        canvas.set_time_view(offset + 2.25, 24.0)
        canvas.set_price_view(99_990.0, 100_050.0)
        canvas.set_validation_range(offset + 2, offset + 28)
        canvas.set_completed_cycle_average_lines(tuple(
            window_module.IndicatorFollowSignalValidationV5Window._offset_completed_cycle(
                cycle, offset,
            )
            for cycle in cycles
        ))
    return old, new, descriptor, len(prefix)


def test_v5_handoff_reuses_remapped_two_cycle_layout_once_and_detaches_records(case):
    old, new, _descriptor, offset = _handoff_pair(case)
    old_records = old._cycle_label_records_for_paint(old.price_scale())
    handoff = old.capture_cycle_label_handoff()
    oracle = new.completed_cycle_average_label_records()
    new._completed_cycle_label_cache_key = None
    new._completed_cycle_label_cache_records = ()

    assert old_records and handoff is not None
    assert [cycle.buy_start_index for cycle in new._completed_cycle_average_lines] == [
        cycle.buy_start_index + offset for cycle in old._completed_cycle_average_lines
    ]
    assert [marker["evaluation_index"] for marker in new._markers] == [12, 24]
    assert new.install_cycle_label_handoff(handoff)

    expected_texts = tuple(record["texts"] for record in oracle)
    old._cycle_label_last_records[0]["texts"] = ("old mutated",)
    handoff[1][0]["texts"] = ("handoff mutated",)
    with patch.object(
        new,
        "_completed_cycle_label_blockers",
        wraps=new._completed_cycle_label_blockers,
    ) as blockers:
        reused = new._cycle_label_records_for_paint(new.price_scale())

    assert reused == oracle
    assert tuple(record["texts"] for record in reused) == expected_texts
    assert blockers.call_count == 1, "handoff fell through to exact collision scoring"
    assert new._cycle_label_handoff is None


def test_v5_handoff_rejects_each_material_visual_domain_change(case):
    def price(canvas, _descriptor, _offset):
        canvas.set_price_view(99_991.0, 100_050.0)

    def ohlc(canvas, _descriptor, offset):
        canvas._candles[offset + 8]["high"] += 1.0

    def marker(canvas, _descriptor, _offset):
        markers = deepcopy(canvas._markers)
        markers[0]["identity"] = "different-marker"
        canvas.set_marker_records(markers)

    def selected_range(canvas, _descriptor, offset):
        canvas.set_validation_range(offset + 3, offset + 28)

    def legend(canvas, descriptor, _offset):
        canvas._toggle_price_legend_identities("BUY", (descriptor.identity,))

    for name, change in (
        ("price", price),
        ("visible OHLC", ohlc),
        ("marker identity", marker),
        ("selected range", selected_range),
        ("legend state", legend),
    ):
        old, new, descriptor, offset = _handoff_pair(case)
        assert old._cycle_label_records_for_paint(old.price_scale())
        handoff = old.capture_cycle_label_handoff()
        assert handoff is not None
        change(new, descriptor, offset)
        assert not new.install_cycle_label_handoff(handoff), name
        assert new._cycle_label_handoff is None, name


@pytest.fixture
def ranged_window(case):
    window = baseline.window_for(case)
    rows = baseline.candles(40)
    source_copy = deepcopy(rows)
    with patch.object(window, "_refresh_filter_display_projection"):
        window.set_historical_candle_pool(rows, chart_candle_count=20)
        window.install_normal_base(
            window._signal_validation_seed.settings_snapshot,
            "M1",
        )
        window.stage_signal_marker_entries([
            _entry(rows, 21, "BUY"),
            _entry(rows, 34, "SELL"),
        ])
        window._set_validation_range(5, 12)

    range_entries = [
        _entry(rows, 22, "BUY"),
        _entry(rows, 25, "BUY"),
        _entry(rows, 27, "BUY", signal=False),
        _entry(rows, 31, "SELL"),
        _entry(rows, 33, "BUY"),
    ]
    snapshot = window_module.ValidationReplaySnapshot(
        stock=window.stock,
        timeframe_minutes=1,
        timeframe_key="M1",
        settings_hash=window._result_settings_snapshot.rules_hash,
        historical_request_id="v5-deep-range",
        evaluated_start_index=22,
        evaluated_end_index=33,
        dropped_raw_rows_count=0,
        candles=rows,
        entries=range_entries,
    )
    window.apply_range_replay_snapshot(
        snapshot,
        settings_snapshot=window._result_settings_snapshot,
    )
    return window, rows, source_copy, snapshot


def test_v5_range_projection_keeps_warmup_outside_display_and_maps_once(ranged_window):
    window, rows, source_copy, snapshot = ranged_window

    assert window._chart_pool_start_index == 20
    assert window.calculation_bounds_for_display_range(5, 12) == (25, 32)
    assert [entry.evaluation_index for entry in window._range_replay_calculation_entries] == [
        22, 25, 27, 31, 33
    ]
    assert [entry.evaluation_index for entry in window._range_replay_entries] == [
        2, 5, 7, 11, 13
    ]

    projected_rows, projected_entries, offset = (
        window._validation_range_visualization_projection()
    )
    assert offset == 25
    assert projected_rows == rows[25:33]
    assert [entry.evaluation_index for entry in projected_entries] == [0, 2, 6]
    assert [entry.signal_index for entry in projected_entries] == [0, None, 6]

    projected_rows[0]["close"] = -1.0
    assert rows == source_copy
    assert window._calculation_candles[25]["close"] != -1.0
    assert [entry.evaluation_index for entry in snapshot.to_entries()] == [22, 25, 27, 31, 33]


def test_v5_range_publishes_only_completed_selected_buy_sell_then_clears(ranged_window):
    window, _rows, _source_copy, _snapshot = ranged_window

    displayed = window._marker_entries_for_display()
    assert [(entry.evaluation_index, entry.evaluation_side, entry.signal) for entry in displayed] == [
        (5, "BUY", "BUY"),
        (11, "SELL", "SELL"),
    ]
    assert {
        (record["evaluation_index"], record["side"], record["provenance"])
        for record in window._chart_marker_records()
    } == {(5, "BUY", "manual"), (11, "SELL", "manual")}

    with patch.object(window, "_refresh_filter_display_projection"):
        window._clear_validation_range_interaction()

    assert window.validation_range is None
    assert not window._range_replay_entries_available
    assert window._marker_entries_for_display() == []
    assert window.canvas.marker_records() == []


def test_v5_range_overlay_keeps_base_calculation_evidence_independent(ranged_window):
    window, _rows, _source_copy, _snapshot = ranged_window

    assert [entry.evaluation_index for entry in window._marker_entries_for_calculation()] == [
        21, 34
    ]
    authoritative = window._authoritative_calculation_marker_entries()
    assert [
        (entry.evaluation_index, entry.signal)
        for entry in authoritative
    ] == [(21, "BUY"), (25, "BUY"), (31, "SELL"), (34, "SELL")]
    assert [entry.evaluation_index for entry in window._marker_entries_for_calculation()] == [
        21, 34
    ]


def test_v5_completed_cycle_uses_display_indexes_once_for_table_line_and_label(ranged_window):
    window, rows, _source_copy, _snapshot = ranged_window

    cycle, = window.completed_cycles
    assert cycle.buy_indexes == (5,)
    assert (cycle.buy_start_index, cycle.buy_end_index, cycle.sell_index) == (5, 5, 11)
    assert rows[25]["time"] == window._candles[cycle.buy_start_index]["time"]
    assert rows[31]["time"] == window._candles[cycle.sell_index]["time"]
    assert window.completed_cycle_table.item(0, 5).data(window_module.Qt.UserRole) == 11

    line, = window.canvas.completed_cycle_average_line_records()
    label, = window.canvas.completed_cycle_average_label_records()
    assert (line["buy_start_index"], line["sell_index"]) == (5, 11)
    assert line["x1"] == pytest.approx(window.canvas._x_for_index(5))
    assert line["x2"] == pytest.approx(window.canvas._x_for_index(11))
    assert label["cycle_number"] == cycle.cycle_number
    assert label["x2"] == pytest.approx(line["x2"])
