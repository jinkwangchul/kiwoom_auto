"""A selected Samsung M1 source can drive marking with its frozen proof."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from gui_indicator_follow_signal_validation_v5_flow import ValidationHistoricalSnapshot
from gui_market_data_host import MarketDataHost
from gui_indicator_follow_signal_validation_v5_window import (
    IndicatorFollowSignalValidationV5Window,
)
from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
    _registered_m1_calculation_case,
)
from tests.test_display_only_integrated_history import (
    DisplayOnlyMarketDataHostTest, _frozen_source_plan,
)
from tests.test_sor_core_m1_input import NOW, _forming_host, _opt10080_row


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


@pytest.mark.parametrize(("advance_host", "tamper"), [
    (False, None), (True, None),
    (False, "display"), (False, "source"),
    (False, "publication"), (False, "receipt"), (False, "proof"),
])
def test_published_forming_m1_marking_uses_only_bound_basis(
    monkeypatch, tmp_path, advance_host, tamper,
):
    flow, session, pool, _market, _cache = _registered_m1_calculation_case()
    host = _forming_host()
    host._sor_m1_required_by_stock = {"005930": 5}
    host.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(host, **kwargs))
    flow._fixture_owner.existing_market_data_host = lambda: host
    flow._now_factory = lambda: NOW + timedelta(seconds=12)
    pool["candles"] = [
        {"time": f"20261020090{minute}00", "open": 100 + minute,
         "high": 102 + minute, "low": 98 + minute,
         "close": 101 + minute, "volume": 10}
        for minute in range(5)
    ]
    selected = ValidationHistoricalSnapshot(
        stock=session.request.stock, timeframe_minutes=1, timeframe_key="M1",
        requested_count=6, request_id="selected-al-m1-forming-marking",
        rows=flow._historical_rows_from_candles([
            *pool["candles"],
            {"time": "20261020090500", "open": 110, "high": 112,
             "low": 108, "close": 111, "volume": 10},
        ]),
        market_data_identity="005930_AL", market_source="INTEGRATED",
        request_as_of=(NOW + timedelta(seconds=12)).isoformat(),
    )
    completed, forming, query = flow._direct_selected_history_parts(selected)
    pool = flow._pool_from_candles(
        session, completed, requested_count=6, request_id=selected.request_id,
        market_data_identity="005930_AL", market_source="INTEGRATED",
        direct_selected_query=query, selected_direct_forming_snapshot=forming,
    )
    monkeypatch.setattr(flow_module, "_SOR_AL_M1_DIAGNOSTIC_PATH",
                        tmp_path / "sor_m1.jsonl")
    monkeypatch.setattr(flow_module, "_V5_MARKING_DIAGNOSTIC_PATH",
                        tmp_path / "v5_marking_signal.jsonl")
    monkeypatch.setattr(flow_module, "_V5_M3_OPERATIONAL_PATH",
                        tmp_path / "v5_sor_m3_operational.jsonl")
    monkeypatch.setattr(flow_module, "_V5_DIRECT_OPERATIONAL_PATH",
                        tmp_path / "v5_sor_direct_operational.jsonl")
    monkeypatch.setattr(flow_module._m3_observation, "PATH",
                        tmp_path / "v5_sor_m3_native.jsonl")

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(flow_module, "Thread", InlineThread)
    monkeypatch.setattr(flow, "_ensure_integrated_display_sidecar", lambda *_a: None)
    monkeypatch.setattr(flow, "_start_pending_window_work", lambda *_a: None)
    tooltips = []
    window = SimpleNamespace(
        stock=session.request.stock, canvas=None, _marking_times=None,
        _normal_base_requested=True, _calculation_candles=[], _candles=[],
        _marking_generation=1, _marking_key="mark",
        _marking_settings=session.request.settings_snapshot,
        validation_range=(0, 4),
        _disable_marking=lambda **_kwargs: None,
        _revalidate_marking_source=lambda: None,
        _invalidate_visualization_projection_data=lambda: None,
        _refresh_marking_projection=lambda: None,
        _refresh_valid_filter_emphasis=lambda: None,
        _refresh_filter_display_projection=lambda: None,
        marking_display_check=SimpleNamespace(setToolTip=tooltips.append),
    )
    window.set_historical_candle_pool = lambda rows, **_kw: setattr(
        window, "_calculation_candles", deepcopy(rows))
    window.set_historical_display_candles = lambda rows, **_kw: setattr(
        window, "_candles", deepcopy(rows))
    window.install_normal_base = lambda *_a, **_kw: setattr(
        window, "_normal_base_ready", True)
    window.apply_marking_snapshot = MethodType(
        IndicatorFollowSignalValidationV5Window.apply_marking_snapshot, window)
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._request_generation[key] = 1
    flow._history_targets[key] = 5
    flow._pool_dispatch_snapshot(pool)
    token = object()
    flow._prepare_active[key] = token

    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        source_view = flow._validation_pool_for_session(session, pool, target_count=6)
        frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
        calculated = flow._sor_m1_calculation_view(
            session, pool, source_view, frozen)
        assert frozen[2]["projection"] == "FORMING_BASE_BAR"
        assert len(calculated["candles"]) == 6
        assert calculated["candles"][-1]["time"] == "20261020090500"
        flow._on_signal_scan_completed(dict(
            window_key=key, generation=1, prepare_token=token,
            session=session, pool=pool, evaluation_count=5,
            history_target=5, normal_base=True,
            prepared_view_pool=calculated,
            normal_base_presentation=((), {}),
            m1_marking_basis=(source_view, frozen),
        ))
        assert window._calculation_candles == calculated["candles"]
        assert window._candles == calculated["display_candles"][-5:]
        assert getattr(window, "_v5_m1_marking_basis", None) is not None, {
            "source": calculated.get("calculation_source"),
            "proof": frozen[2].get("forming_snapshot_source"),
            "snapshot_matches": calculated["snapshot"].to_rows()
            == flow._historical_rows_from_candles(calculated["candles"]),
            "identity_matches": frozen[0] == pool["_dispatch_snapshot"][3],
            "source_view_timeframe": source_view.get("timeframe_key"),
        }

        if advance_host:
            with patch("gui_market_data_host.load_stock_library_snapshot",
                       return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
                MarketDataHost, "direct_timeframe_requirements", return_value={1: 5},
            ):
                installed = MarketDataHost.install_display_timeframe_candles(
                    host, "005930", 1,
                    [_opt10080_row("20261020090500", 111),
                     _opt10080_row("20261020090400", 104)],
                    as_of=NOW, market_data_identity="005930_AL",
                    market_source="INTEGRATED", display_only=True,
                    connection_epoch=1, login_session_id="SESSION-1",
                    request_as_of=(NOW + timedelta(seconds=13)).isoformat(),
                    received_at=(NOW + timedelta(seconds=14)).isoformat(),
                )
            assert installed["ok"] is True
            flow._now_factory = lambda: NOW + timedelta(seconds=20)

        if tamper == "display":
            window._calculation_candles[-1]["close"] += 1
            window._candles[-1]["close"] += 1
        elif tamper == "source":
            pool["candles"][-1]["close"] += 1
        elif tamper == "publication":
            published = pool["_dispatch_snapshot"]
            pool["_dispatch_snapshot"] = (
                published[0], published[1], published[2], published[3])
        elif tamper == "receipt":
            pool["selected_direct_forming_snapshot"]["request_id"] = "other"
        elif tamper == "proof":
            basis = window._v5_m1_marking_basis
            window._v5_m1_marking_basis = (*basis[:-1], b"invalid")

        project = Mock(wraps=host.project_broker_sor_m1_calculation)
        host.project_broker_sor_m1_calculation = project
        projection = Mock(wraps=flow._sor_m1_calculation_view)
        monkeypatch.setattr(flow, "_sor_m1_calculation_view", projection)
        payloads = []
        flow.marking_completed.disconnect()
        flow.marking_completed.connect(flow._on_marking_completed)
        flow.marking_completed.connect(payloads.append)
        display = deepcopy(window._candles)
        request = dict(
            settings=session.request.settings_snapshot,
            calculation=deepcopy(window._calculation_candles), display=display,
            generation=1, key="mark", range=(0, 4),
            times=(display[0]["time"], display[-1]["time"]), descriptors=(),
        )
        flow._start_marking(window, request)

    if tamper:
        assert tooltips and not payloads
        assert not hasattr(window, "_marking_snapshot")
        assert projection.call_count == 0
    else:
        assert not tooltips
        assert payloads and payloads[-1]["error"] == ""
        assert payloads[-1]["snapshot"].to_candles() == calculated["candles"]
        assert window._marking_snapshot is payloads[-1]["snapshot"]
        assert projection.call_count == 1
    assert project.call_count == 0
