"""A selected Samsung M1 source can drive marking with its frozen proof."""

from copy import deepcopy
from datetime import datetime
from types import MethodType, SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from gui_indicator_follow_signal_validation_v5_flow import ValidationHistoricalSnapshot
from gui_indicator_follow_signal_validation_v5_window import (
    IndicatorFollowSignalValidationV5Window,
)
from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
    _registered_m1_calculation_case,
)
from tests.test_display_only_integrated_history import _frozen_source_plan


def _run_current_day_m1_marking(monkeypatch, tmp_path, forming_current, tamper=None):
    flow, session, _old_pool, market, _cache = _registered_m1_calculation_case()
    if forming_current:
        market._display_timeframe_candles.pop(("005930", 1), None)
        market._display_timeframe_session_identity.pop(("005930", 1), None)
    day = 27 if forming_current else 20
    now = datetime(2026, 10, day, 9, 5, 30 if forming_current else 0,
                   tzinfo=ZoneInfo("Asia/Seoul"))
    flow._now_factory = lambda: now
    monkeypatch.setattr(flow_module, "_SOR_AL_M1_DIAGNOSTIC_PATH",
                        tmp_path / "sor_m1.jsonl")
    monkeypatch.setattr(flow_module, "_V5_MARKING_DIAGNOSTIC_PATH",
                        tmp_path / "v5_marking_signal.jsonl")
    rows = [
        {"time": f"202610{day:02d}090{minute}00", "open": 100 + minute,
         "high": 102 + minute, "low": 98 + minute,
         "close": 101 + minute, "volume": 10}
        for minute in range(6 if forming_current else 5)
    ]
    snapshot = ValidationHistoricalSnapshot(
        stock=session.request.stock, timeframe_minutes=1, timeframe_key="M1",
        requested_count=len(rows), request_id="selected-al-m1-marking",
        rows=flow._historical_rows_from_candles(rows),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=now.isoformat(),
    )
    completed, forming, query = flow._direct_selected_history_parts(snapshot)
    assert completed == (rows[:-1] if forming_current else rows)
    assert (forming is not None) == forming_current
    pool = flow._pool_from_candles(
        session, completed, requested_count=len(rows), request_id=snapshot.request_id,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming,
    )
    flow._pool_dispatch_snapshot(pool)
    if forming_current:
        frozen = flow._sor_m1_calculation_snapshot(session, pool)
        view = flow._validation_pool_for_session(session, pool,
                                                 target_count=len(rows))
        prepared = flow._sor_m1_calculation_view(session, pool, view, frozen)
        assert prepared["candles"] == rows
        assert pool["candles"] == rows[:-1]

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(flow_module, "Thread", InlineThread)
    tooltips = []
    window = SimpleNamespace(
        stock=session.request.stock, _marking_generation=1, _marking_key="mark",
        _marking_settings=session.request.settings_snapshot,
        _disable_marking=lambda **_kwargs: None,
        _revalidate_marking_source=lambda: None,
        _invalidate_visualization_projection_data=lambda: None,
        _refresh_marking_projection=lambda: None,
        _refresh_valid_filter_emphasis=lambda: None,
        _refresh_filter_display_projection=lambda: None,
        marking_display_check=SimpleNamespace(setToolTip=tooltips.append),
    )
    window.apply_marking_snapshot = MethodType(
        IndicatorFollowSignalValidationV5Window.apply_marking_snapshot, window,
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._request_generation[key] = 1
    payloads = []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(flow._on_marking_completed)
    flow.marking_completed.connect(payloads.append)
    displayed = rows[-5:]
    request = dict(
        settings=session.request.settings_snapshot, calculation=deepcopy(rows),
        display=deepcopy(displayed), generation=1, key="mark", range=(0, 4),
        times=(displayed[0]["time"], displayed[-1]["time"]), descriptors=(),
    )
    if tamper == "calculation_value":
        request["calculation"][-1]["close"] += 1
        request["display"][-1]["close"] += 1
    elif tamper == "source_value":
        pool["candles"][-1]["close"] += 1
    elif tamper == "receipt_identity":
        pool["selected_direct_forming_snapshot"]["request_id"] = "other"

    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        flow._start_marking(window, request)

    return tooltips, payloads, window, rows


@pytest.mark.parametrize("forming_current", [False, True])
def test_current_day_m1_marking_publishes_selected_al_candles(
    monkeypatch, tmp_path, forming_current,
):
    tooltips, payloads, window, rows = _run_current_day_m1_marking(
        monkeypatch, tmp_path, forming_current,
    )
    assert not tooltips
    assert payloads and payloads[-1]["error"] == ""
    assert payloads[-1]["snapshot"].to_candles() == rows
    assert payloads[-1]["comparison"][0] == tuple(row["time"] for row in rows)
    assert window._marking_snapshot is payloads[-1]["snapshot"]
    assert window._marking_cache[1].to_candles() == rows


@pytest.mark.parametrize(("tamper", "expected_error"), [
    ("calculation_value", "SOR_M1_BLOCKED:DISPLAYED_CALCULATION_CHANGED"),
    ("source_value", "SOR_M1_BLOCKED:SOR_M1_SOURCE_UNAVAILABLE"),
    ("receipt_identity", "SOR_M1_BLOCKED:SOR_M1_SOURCE_UNAVAILABLE"),
])
def test_current_day_m1_marking_rejects_changed_proof_or_values(
    monkeypatch, tmp_path, tamper, expected_error,
):
    tooltips, payloads, window, _rows = _run_current_day_m1_marking(
        monkeypatch, tmp_path, True, tamper,
    )
    assert payloads and payloads[-1]["error"] == expected_error
    assert payloads[-1]["snapshot"] is None
    assert tooltips == [payloads[-1]["error"]]
    assert not hasattr(window, "_marking_snapshot")
