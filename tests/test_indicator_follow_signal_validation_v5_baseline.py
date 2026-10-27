"""Focused V5 behavior baseline with all persistence dependencies isolated."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QRectF, QSignalBlocker

import gui_indicator_follow_signal_validation_flow as v2_flow_module
import gui_indicator_follow_signal_validation_v5_flow as flow_module
import gui_indicator_follow_signal_validation_v5_window as window_module
from tests import test_indicator_follow_signal_validation_v2 as legacy
from tests.qt_test_support import ensure_qapplication


@pytest.fixture
def case():
    cls = legacy.IndicatorFollowSignalValidationV2Test
    cls.setUpClass()
    instance = cls(methodName="runTest")
    instance.setUp()
    try:
        yield instance
    finally:
        instance.tearDown()


def window_for(case):
    with patch.object(legacy.dialog_module.QTimer, "singleShot"):
        window = window_module.IndicatorFollowSignalValidationV5Window(
            case.stock,
            case._seed(),
        )
    case.widgets.append(window)
    return window


def candles(count=600):
    rows = []
    stamp = datetime(2026, 10, 7, 15, 30)
    while len(rows) < count:
        value = 100_000.0 + len(rows)
        rows.append({
            "time": stamp.strftime("%Y%m%d%H%M%S"),
            "open": value,
            "high": value + 10.0,
            "low": value - 10.0,
            "close": value + 1.0,
            "volume": 100,
        })
        stamp -= timedelta(minutes=1)
        if stamp.hour < 9:
            stamp = (stamp - timedelta(days=1)).replace(hour=15, minute=30)
    return list(reversed(rows))


def install(window, rows=None):
    rows = candles() if rows is None else rows
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")


def descriptor(identity, family, label, axis, side, channel, parameters=None):
    return window_module.ValidationFilterDescriptor(
        identity,
        family,
        label,
        axis,
        (side,),
        (channel,),
        json.dumps(parameters or {}, sort_keys=True),
        (),
    )


def canvas_for(case, *, lower_presentation=False):
    rows = candles(8)
    price = descriptor(
        "price-ma5",
        window_module.FAMILY_MOVING_AVERAGE,
        "MA5",
        window_module.PRICE_AXIS,
        "BUY",
        "MA5",
    )
    lower = descriptor(
        "lower-rsi",
        window_module.FAMILY_RSI,
        "RSI",
        window_module.LOWER_AXIS,
        "BUY",
        "RSI",
        {
            "period": 14,
            "criterion_label": "RSI >= 50",
            "condition": {"operator": "GREATER_THAN_OR_EQUAL", "threshold": 50},
        },
    )
    presentations = ()
    if lower_presentation:
        presentations = (window_module.ValidationLowerFilterPresentation(
            key="BUY:rsi",
            side="BUY",
            source_path="buy.rsi",
            filter_token="RSI",
            pane_family=window_module.FAMILY_RSI,
            display_order=(0,),
            descriptor_identities=(lower.identity,),
            component_descriptor_identities=(lower.identity,),
            component_labels=("RSI",),
        ),)
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows,
        [],
        visualization_descriptors=(price, lower),
        visualization_cache=window_module.ValidationIndicatorSeriesCache(
            len(rows),
            (
                (price.identity, "MA5", tuple(row["close"] for row in rows)),
                (lower.identity, "RSI", tuple(45.0 + index for index in range(len(rows)))),
            ),
        ),
        lower_filter_presentations=presentations,
    )
    case.widgets.append(canvas)
    return canvas, price, lower


def test_v5_window_defaults_to_250_visible_candles_with_3500_cap(case):
    window = window_for(case)
    install(window)

    assert window.visible_candle_span == 250
    assert window.visible_start_index == len(window._candles) - 250
    assert window._MAX_VISIBLE_CANDLE_SPAN == 3500


def test_v5_negative_zero_is_display_only_and_preserves_underlying_value(case):
    threshold = -0.00001
    lower = descriptor(
        "ocr-negative-zero",
        window_module.FAMILY_OCR_OSC,
        "OCR",
        window_module.LOWER_AXIS,
        "BUY",
        "CRITERION",
        {"condition": {
            "operator": "LESS_THAN_OR_EQUAL",
            "target": "OSC",
            "threshold": threshold,
        }},
    )
    rows = candles(4)
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows,
        [],
        visualization_descriptors=(lower,),
        visualization_cache=window_module.ValidationIndicatorSeriesCache(
            len(rows),
            ((lower.identity, "CRITERION", (threshold,) * len(rows)),),
        ),
    )
    case.widgets.append(canvas)
    pane, = canvas.lower_pane_records()
    scale = canvas._lower_scale(
        window_module.FAMILY_OCR_OSC,
        int(pane["top"]),
        int(pane["bottom"]),
    )
    record, = canvas.lower_reference_line_records(
        window_module.FAMILY_OCR_OSC,
        scale,
    )

    assert canvas._crosshair_number_text(threshold) == "0"
    assert record["text"].endswith(" 0")
    assert record["value"] == threshold
    assert record["value"] < 0


def test_v5_unified_price_lower_multiselect_and_valid_zero_mode(case):
    canvas, price, lower = canvas_for(case)
    both = {("BUY", price.identity), ("BUY", lower.identity)}

    canvas._toggle_price_legend_identities(
        "BUY",
        (price.identity, lower.identity),
    )
    assert set(canvas.highlighted_price_filter_identities) == {("BUY", price.identity)}
    assert set(canvas.highlighted_lower_filter_identities) == {("BUY", lower.identity)}
    assert canvas.hide_unhighlighted_price_filters

    canvas.set_valid_filter_identities((), valid_mode_enabled=True)
    assert canvas.valid_filter_identities == ()
    assert canvas._price_visibility_filter_active()
    assert canvas._effective_price_filter_identities() == set()
    assert canvas._effective_lower_filter_identities() == set()
    assert set(canvas.hidden_price_filter_identities) == {price.identity}

    canvas.set_valid_filter_identities((), valid_mode_enabled=False)
    assert canvas._effective_price_filter_identities() | canvas._effective_lower_filter_identities() == both
    assert canvas.hidden_price_filter_identities == ()


def test_v5_not_styles_outer_red_inner_yellow_in_legend_only(case):
    canvas, price, lower = canvas_for(case, lower_presentation=True)
    series_before = (canvas._price_series_groups, canvas._lower_series_groups)
    line_legend_before = canvas.legend_records()
    canvas.set_not_filter_identities(
        {("BUY", price.identity), ("BUY", lower.identity)},
        {("BUY", lower.identity)},
    )

    blocks = next(blocks for side, _label, blocks in canvas._price_legend_rows()
                  if side == "BUY")
    price_block = next(block for block in blocks if price.identity in block["descriptor_identities"])
    lower_block = next(block for block in blocks if block["legend_role"] == "LOWER_FILTER")
    rect = QRectF(0.0, 0.0, 1000.0, 24.0)
    red_runs = canvas._layout_price_legend_blocks([price_block], rect, canvas.font())[0]["caption_runs"]
    yellow_runs = canvas._layout_price_legend_blocks([lower_block], rect, canvas.font())[0]["caption_runs"]

    assert {run["color"].getRgb() for run in red_runs} == {
        window_module._NOT_FILTER_TEXT.getRgb()
    }
    assert window_module._INNER_NOT_FILTER_TEXT.getRgb() in {
        run["color"].getRgb() for run in yellow_runs
    }
    assert window_module._NOT_FILTER_TEXT.getRgb() not in {
        run["color"].getRgb() for run in yellow_runs
    }
    assert (canvas._price_series_groups, canvas._lower_series_groups) == series_before
    assert canvas.legend_records() == line_legend_before


def test_v5_vertical_navigation_preserves_marking_horizontal_change_disables_it(case):
    window = window_for(case)
    install(window)
    with QSignalBlocker(window.marking_display_check):
        window.marking_display_check.setChecked(True)
    period = (window._candles[350]["time"], window._candles[599]["time"])
    frozen = object()
    window._marking_times = period
    window._marking_snapshot = frozen
    generation = window._marking_generation

    window._pan_chart_view(0.0, 20.0)
    assert window.marking_display_check.isChecked()
    assert window._marking_times == period
    assert window._marking_snapshot is frozen
    assert window._marking_generation == generation

    with patch.object(window_module, "signal_validation_trace_enabled", return_value=False):
        window._set_time_view(
            window.visible_start_index - 10,
            window.visible_candle_span,
            user_horizontal_navigation=True,
        )
    assert not window.marking_display_check.isChecked()
    assert window._marking_times is None
    assert window._marking_snapshot is None
    assert window._marking_generation == generation + 1


def test_v5_flow_owns_injected_stores_and_purges_startup_evictions(case):
    class RecentStore:
        recent_stocks = (case.stock,)

        @staticmethod
        def take_startup_evicted_codes():
            return ("000660",)

    class HistoricalCache:
        def __init__(self):
            self.deleted = []

        def delete_stock(self, code):
            self.deleted.append(code)
            return {"ok": True}

    recent = RecentStore()
    preferences = object()
    history = HistoricalCache()
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(),
        host=legacy._FakeHost(case.stock),
        recent_stock_store=recent,
        preference_store=preferences,
        historical_cache=history,
    )

    assert flow._recent_stock_store is recent
    assert flow._preference_store is preferences
    assert flow._historical_cache is history
    assert flow.recent_stocks == (case.stock,)
    assert history.deleted == ["000660"]
    assert (
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS
        is not v2_flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS
    )


def test_v5_range_liquidation_toggle_updates_only_selected_results(case):
    window = window_for(case)
    rows = candles()
    for offset, row in enumerate(rows[-5:]):
        row["time"] = f"2026102709{offset:02d}00"
    rows[-1]["close"] = 150_000.0
    install(window, rows)
    window._result_settings_snapshot = None  # Exercise the legacy completed-cycle branch.

    def entry(side, index):
        return window_module.ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=index,
            evaluation_time=rows[index]["time"],
            signal=side,
            reason="fixture",
            signal_index=index,
            signal_time=rows[index]["time"],
            delay_bar=0,
            matched_groups=[],
            details=[],
            trace={"conditions": [], "groups": [], "aggregations": []},
        )

    entries = [entry("BUY", 595), entry("BUY", 596),
               entry("SELL", 597), entry("BUY", 598)]
    window._set_validation_range(596, 599)
    window._range_replay_entries_available = True
    with patch.object(window, "_marker_entries_for_display", return_value=entries), \
            patch.object(window, "_refresh_signal_tooltips_only") as tooltips, \
            patch.object(window_module.QTimer, "singleShot"), \
            patch.object(window, "_on_signal_validation_ui_changed") as revalidate:
        window._refresh_validation_range_results()
        original_cycles = window.completed_cycles
        original_summary = window.estimated_return_label.text()
        assert not window.validation_range_liquidation_check.isChecked()
        assert len(original_cycles) == 1
        assert window.canvas._liquidation_marker_records() == []
        end_label = window.canvas.selection_end_time_record()
        assert end_label["label"] == "10/27 09:04"
        assert window.completed_cycle_table.item(0, 0).text() == "1"

        tooltip_calls = tooltips.call_count
        window.validation_range_liquidation_check.setChecked(True)
        assert tooltips.call_count == tooltip_calls
        assert window.validation_range == (596, 599)
        assert len(window.completed_cycles) == 2
        assert window.completed_cycles[0] == original_cycles[0]
        assert tuple(window.canvas._completed_cycle_average_lines) == window.completed_cycles
        forced = window.completed_cycles[-1]
        assert forced.is_liquidation
        assert forced.buy_indexes == (598,)
        assert forced.sell_index == 599
        assert forced.sell_price == 150_000.0
        assert window.canvas._liquidation_marker_records() == [
            {"evaluation_index": 599, "side": "SELL", "virtual_liquidation": True}]
        assert window.canvas.selection_end_time_record() == end_label
        assert window.completed_cycle_table.item(1, 0).text() == "청산"
        assert window.completed_cycle_table.item(1, 5).text() == "10/27 09:04"
        assert window.completed_cycle_table.item(1, 6).text() == "150,000"
        assert window.estimated_return_label.text() != original_summary
        assert [(item.signal, item.evaluation_index) for item in entries] == [
            ("BUY", 595), ("BUY", 596), ("SELL", 597), ("BUY", 598),
        ]

        window._validation_range = (596, 598)
        window._refresh_validation_range_results()
        assert window.completed_cycles[-1].is_liquidation
        assert window.completed_cycles[-1].sell_index == 598
        assert window.completed_cycles[-1].sell_price == rows[598]["close"]
        window._validation_range = (596, 597)
        window._refresh_validation_range_results()
        assert len(window.completed_cycles) == 1
        assert not window.completed_cycles[0].is_liquidation
        assert window.canvas._liquidation_marker_records() == []
        window._validation_range = (596, 599)
        window._refresh_validation_range_results()

        tooltip_calls = tooltips.call_count
        window.validation_range_liquidation_check.setChecked(False)
        assert tooltips.call_count == tooltip_calls
        assert window.completed_cycles == original_cycles
        assert tuple(window.canvas._completed_cycle_average_lines) == original_cycles
        assert window.canvas._liquidation_marker_records() == []
        assert window.canvas.selection_end_time_record() == end_label
        assert window.estimated_return_label.text() == original_summary
        window.validation_range_liquidation_check.setChecked(True)
        window._validation_range = None
        window._refresh_validation_range_results()
        assert window.completed_cycles == ()
        assert window.canvas._completed_cycle_average_lines == ()
        assert window.completed_cycle_table.rowCount() == 0

        rules = case._seed().settings_snapshot.to_dict()
        rules["validation_execution"] = {
            "enabled": True, "first_buy_quantity": 3,
        }
        window._result_settings_snapshot = window_module.ValidationSettingsSnapshot(rules)
        window._validation_range = (596, 599)
        window._refresh_validation_range_results()
        assert window.completed_cycles[-1].is_liquidation
        assert window.completed_cycles[-1].buy_quantity == 3
        assert window.completed_cycles[-1].buy_cost == rows[598]["close"] * 3
        revalidate.assert_not_called()


def test_v5_execution_cycle_projection_marks_only_forced_last_cycle(case):
    rows = candles(3)
    entries = [window_module.ValidationReplayEntry(
        evaluation_side="BUY", evaluation_index=0,
        evaluation_time=rows[0]["time"], signal="BUY", reason="fixture",
        signal_index=0, signal_time=rows[0]["time"], delay_bar=0,
        matched_groups=[], details=[],
        trace={"conditions": [], "groups": [], "aggregations": []},
    )]
    cycles = window_module.validation_execution_cycles(
        rows, entries, {"enabled": True, "first_buy_quantity": 3},
        force_liquidation_at_end=True,
    )
    assert len(cycles) == 1
    assert cycles[0].is_liquidation
    assert cycles[0].buy_quantity == 3


def test_v5_header_pipes_share_spacing_and_separate_liquidation(case):
    window = window_for(case)
    layout = window.basic_header_row
    widgets = [layout.itemAt(index).widget() for index in range(layout.count())]
    pipes = [widget for widget in widgets
             if isinstance(widget, window_module.QLabel) and widget.text() == "|"]
    assert layout.spacing() == 8
    assert len(pipes) == 6
    window.basic_header_widget.resize(2000, 40)
    assert layout.activate()
    first_pipe = pipes[0]
    for pipe in pipes:
        assert pipe.contentsMargins() == first_pipe.contentsMargins()
        assert pipe.styleSheet() == first_pipe.styleSheet()
        assert pipe.minimumWidth() == pipe.maximumWidth() == 18
        assert pipe.minimumHeight() == pipe.maximumHeight() == 30
        assert pipe.font().pointSize() == 10
        assert not pipe.font().bold()
        assert pipe.font() == first_pipe.font()
        assert "padding: 0px;" in pipe.styleSheet()
        assert pipe.fontMetrics().horizontalAdvance("|") <= 18
        index = widgets.index(pipe)
        left, right = widgets[index - 1], widgets[index + 1]
        assert pipe.geometry().left() - left.geometry().right() - 1 == 8
        assert right.geometry().left() - pipe.geometry().right() - 1 == 8
        assert pipe.alignment() & window_module.Qt.AlignHCenter
        glyph_margin = (pipe.width() - pipe.fontMetrics().horizontalAdvance("|")) / 2
        assert glyph_margin >= 0
    assert widgets.index(window.validation_regular_market_separator) + 1 == widgets.index(
        window.validation_range_liquidation_check)
    assert widgets.index(window.validation_range_liquidation_check) + 1 == widgets.index(
        window.validation_liquidation_separator)
    assert widgets.index(window.validation_liquidation_separator) + 1 == widgets.index(
        window.validation_regular_market_only_check)


def test_v5_liquidation_sell_marker_is_visual_only_and_viewport_bound(case):
    from PyQt5.QtGui import QPainter, QPixmap

    rows = candles(8)
    markers = [{"evaluation_index": 2, "side": "BUY"},
               {"evaluation_index": 4, "side": "SELL"}]
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows, markers, timeframe_key="M1")
    case.widgets.append(canvas)
    canvas.resize(800, 400)
    canvas.set_validation_range(2, 7)
    assert canvas._liquidation_marker_records() == []
    cycle = window_module.IndicatorFollowValidationCompletedCycle(
        cycle_number=1, buy_indexes=(6,), buy_start_index=6,
        buy_end_index=6, buy_count=1, average_buy_price=100_006.0,
        sell_index=7, sell_price=100_007.0, estimated_return_percent=0.0,
        is_liquidation=True)
    canvas.set_completed_cycle_average_lines((cycle,))
    original = canvas.marker_records()
    assert canvas._liquidation_marker_records() == [
        {"evaluation_index": 7, "side": "SELL", "virtual_liquidation": True}]
    assert canvas.marker_count("SELL") == 1
    assert canvas.marker_records() == original
    pixmap = QPixmap(canvas.size())
    painter = QPainter(pixmap)
    try:
        canvas._paint_static_chart(painter, canvas.price_scale())
    finally:
        painter.end()
    assert not pixmap.isNull()
    canvas.set_time_view(0, 5)
    assert canvas._liquidation_marker_records() == []
    canvas.set_time_view(3, 5)
    assert len(canvas._liquidation_marker_records()) == 1
    canvas.set_completed_cycle_average_lines((
        window_module.IndicatorFollowValidationCompletedCycle(
            cycle_number=1, buy_indexes=(6,), buy_start_index=6,
            buy_end_index=6, buy_count=1, average_buy_price=100_006.0,
            sell_index=7, sell_price=100_007.0, estimated_return_percent=0.0),))
    assert canvas._liquidation_marker_records() == []
    canvas.set_completed_cycle_average_lines((cycle,))
    canvas.set_validation_range(None, None)
    assert canvas._liquidation_marker_records() == []
    assert canvas.marker_records() == original


def test_v5_selected_end_time_label_uses_exact_bar_and_follows_view(case):
    rows = candles(8)
    rows[7]["time"] = "20261027163300"
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows, [], timeframe_key="M1")
    case.widgets.append(canvas)
    canvas.resize(800, 400)
    assert canvas.selection_end_time_record() is None
    canvas.set_validation_range(2, 7)
    record = canvas.selection_end_time_record()
    assert record == {"index": 7, "x": canvas._x_for_index(7),
                      "label": "10/27 16:33"}
    canvas.set_time_view(0, 5)
    assert canvas.selection_end_time_record() is None
    canvas.set_validation_range(2, 4)
    left_x = canvas.selection_end_time_record()["x"]
    canvas.set_time_view(3, 5)
    assert canvas.selection_end_time_record()["x"] != left_x
    assert canvas.selection_end_time_record()["x"] == canvas._x_for_index(4)
    canvas._candles[4]["time"] = "bad"
    assert canvas.selection_end_time_record() is None
    canvas.set_validation_range(None, None)
    assert canvas.selection_end_time_record() is None
