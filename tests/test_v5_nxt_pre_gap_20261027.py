"""The gap after NXT PRE must permit direct historical AL validation."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as v5
from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationRunRequest
from stock_market_source_plan import _build
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import _flow


@pytest.mark.parametrize("clock,source", [
    ("08:49", "NXT"), ("08:50", "INTEGRATED"),
    ("08:59", "INTEGRATED"), ("09:00", "INTEGRATED"),
])
def test_requester_uses_nxt_only_during_actual_pre_session(clock, source):
    now = datetime.fromisoformat(f"2026-10-27T{clock}:00+09:00")
    stock = v5.ValidationStockRef("005930", "Samsung")
    plan = _build(stock.code, stock.name, now.date(), now.isoformat(), True)
    with patch.object(v5, "read_stock_market_source_plan", return_value=plan):
        requester = v5._V5SelectedSourceRequester(
            object(), stock, "M5", True, now_factory=lambda: now,
        )
    assert requester.market_source == source


def test_requester_rejects_missing_nxt_plan_at_0849():
    now = datetime.fromisoformat("2026-10-27T08:49:00+09:00")
    stock = v5.ValidationStockRef("005930", "Samsung")
    with patch.object(v5, "read_stock_market_source_plan", return_value=None):
        with pytest.raises(ValueError, match="NXT_PRE_SOURCE_UNAVAILABLE"):
            v5._V5SelectedSourceRequester(
                object(), stock, "M5", True, now_factory=lambda: now,
            )


@pytest.mark.parametrize("clock,pre_selected", [
    ("08:49", True), ("08:50", False),
    ("08:59", False), ("09:00", False),
])
def test_flow_precheck_only_requires_nxt_during_actual_pre_session(clock, pre_selected):
    now = datetime.fromisoformat(f"2026-10-27T{clock}:00+09:00")
    stock = v5.ValidationStockRef("005930", "Samsung")
    plan = _build(stock.code, stock.name, now.date(), now.isoformat(), True)
    flow = _flow()
    flow._now_factory = lambda: now
    window = type("Window", (), {"stock": stock})()
    flow._open_windows[id(window)] = window
    request = IndicatorFollowSignalValidationRunRequest(
        v5.ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}}), 10,
    )
    with (patch.object(flow, "_verified_nxt_validation_stock", return_value=True),
          patch.object(flow, "_sync_v5_observation_lease"),
          patch.object(v5, "read_stock_market_source_plan", return_value=plan),
          patch.object(v5, "read_json_dict", return_value={}),
          patch.object(v5.StockRepository, "resolve_stock_dir", return_value=Path(".")),
          patch.object(flow, "_validation_fetch_contract",
                       side_effect=RuntimeError("PRECHECK_PASSED")) as reached,
          patch.object(flow, "_fail_window") as fail):
        flow._run_validation(window, request)
    reached.assert_called_once()
    fail.assert_called_once_with(window, "HISTORICAL_PROVIDER_ERROR: PRECHECK_PASSED")
    assert (stock.code in flow._validation_nxt_pre_codes) is pre_selected


def test_flow_rejects_missing_nxt_plan_at_0849():
    now = datetime.fromisoformat("2026-10-27T08:49:00+09:00")
    stock = v5.ValidationStockRef("005930", "Samsung")
    flow = _flow()
    flow._now_factory = lambda: now
    window = type("Window", (), {"stock": stock})()
    flow._open_windows[id(window)] = window
    request = IndicatorFollowSignalValidationRunRequest(
        v5.ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}}), 10,
    )
    with (patch.object(flow, "_verified_nxt_validation_stock", return_value=True),
          patch.object(flow, "_sync_v5_observation_lease"),
          patch.object(v5, "read_stock_market_source_plan", return_value=None),
          patch.object(flow, "_validation_fetch_contract") as reached,
          patch.object(flow, "_fail_window") as fail):
        flow._run_validation(window, request)
    reached.assert_not_called()
    fail.assert_called_once_with(window, "NXT_PRE_SOURCE_UNAVAILABLE")
