"""V5 initial full-history contract."""
from __future__ import annotations

import inspect

import gui_indicator_follow_signal_validation_v5_flow as flow_module
import gui_indicator_follow_signal_validation_v5_window as window_module


def test_initial_defaults_fetch_and_retain_5000_bars():
    parameter = inspect.signature(
        flow_module.IndicatorFollowSignalValidationV5Flow.__init__
    ).parameters["historical_count"]

    assert flow_module.DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT == 5_000
    assert parameter.default == 5_000
    assert flow_module.MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT == 5_000
    assert window_module.IndicatorFollowSignalValidationV5Window._INITIAL_HISTORICAL_CANDLE_COUNT == 5_000


def test_first_install_remains_a_250_candle_viewport_with_existing_caps():
    source = inspect.getsource(
        window_module.IndicatorFollowSignalValidationV5Window
    )

    assert window_module.IndicatorFollowSignalValidationV5Window._REFERENCE_CANDLE_COUNT == 250.0
    assert window_module.IndicatorFollowSignalValidationV5Window._INITIAL_EVALUATION_CANDLE_COUNT == 100
    assert window_module.IndicatorFollowSignalValidationV5Window._MAX_VISIBLE_CANDLE_SPAN == 3_500
    assert "self._REFERENCE_CANDLE_COUNT" in source


def test_selected_timeframe_provider_request_includes_hidden_context_and_metadata():
    source = inspect.getsource(flow_module.IndicatorFollowSignalValidationV5Flow._run_validation)

    assert "target_fetch_count = historical_target + warmup_bars" in source
    assert "target_fetch_count > DEFAULT_CANDLES_MAX_COUNT" in source
    assert "historical_target = self._history_targets.get" in source


def test_source_switch_resets_to_full_initial_target_without_background_scheduler():
    source = inspect.getsource(flow_module.IndicatorFollowSignalValidationV5Flow)

    assert "previous_timeframe[\"key\"] != current_timeframe[\"key\"]" in source
    assert "self._history_targets[window_key] = self._historical_count" in source
    assert "_start_background_history_warmup" not in source
    assert "_queue_background_history_warmup" not in source
    assert "_background_warmup_started" not in source
