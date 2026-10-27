"""Explicit V5 selection freezes one shared plan; offline broker only."""
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch
import weakref

import pytest
from PyQt5.QtWidgets import QDialog, QWidget

import gui_indicator_follow_signal_validation_v5_flow as v5
import gui_stock_data
import routine_signal_probe as probe
import stock_market_source_plan as plans
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from quick_chart_native_history import native_history_source
from stock_instance_day_projection import chart_market_session_projection
from tests.v5_validation_test_support import (
    _FakeHost, _FakePicker, _FakeWindow, _MemoryRecentStockStore,
)
from tests.qt_test_support import ensure_qapplication


STOCK = v5.ValidationStockRef("005930", "삼성전자")
NOW = datetime.now(SEOUL_TIMEZONE).replace(hour=12, minute=0, second=0, microsecond=0)


def _library(root, eligible=True, *, state="READY", name=STOCK.name):
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    raw = json.dumps([dict(code=STOCK.code, name=name, market="KOSPI",
                           chosung="ㅅㅅㅈㅈ", nxt_available=eligible)],
                     ensure_ascii=False).encode("utf-8")
    (runtime / "stock_library.json").write_bytes(raw)
    (runtime / "stock_library_meta.json").write_text(json.dumps({
        "sync_state": state, "final_count": 1,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }), encoding="utf-8")


class _Window(_FakeWindow):
    def request_normal_base(self):
        self.validation_requests += 1
        self.validation_run_requested.emit(v5.IndicatorFollowSignalValidationRunRequest(
            self.seed.settings_snapshot, 5, force_historical_refresh=True,
        ))

    def request_initial_validation(self):
        self.initial_requests += 1
        self.request_normal_base()


@pytest.fixture
def selection(tmp_path, monkeypatch):
    app = ensure_qapplication()
    monkeypatch.setattr(v5, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(probe, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(v5, "_trace_sor_al_m1", lambda *a, **kw: None)
    host = _FakeHost(STOCK)
    calls, pools, scheduled, failures = [], [], [], []

    def native_request(source, identity, code, name, **kwargs):
        calls.append((source, identity, code, kwargs["interval"]))
        rows = v5.IndicatorFollowSignalValidationV5Flow._historical_rows_from_candles([
            dict(time=(NOW - timedelta(days=1)).replace(hour=9, minute=i * 5).strftime("%Y%m%d%H%M%S"),
                 open=100+i, high=102+i, low=99+i, close=101+i, volume=10)
            for i in range(10)
        ])
        result = dict(ok=True, code=code, rqname="selection-history", request_id="selection-history",
                      market_source=source, market_data_identity=identity, display_only=True,
                      interval=kwargs["interval"], type="minute_candles", rows=rows,
                      rows_count=len(rows), trcode="opt10080")
        kwargs["callback"](result)
        return result

    broker = SimpleNamespace(
        request_minute_candles_display_only_integrated=lambda code, name, **kw:
            native_request("INTEGRATED", f"{code}_AL", code, name, **kw),
        request_minute_candles_display_only_krx=lambda code, name, **kw:
            native_request("KRX", code, code, name, **kw),
        SendOrder=lambda *a, **kw: pytest.fail("real orders forbidden"),
    )
    flow = v5.IndicatorFollowSignalValidationV5Flow(
        broker, host=host, window_factory=_Window,
        recent_stock_store=_MemoryRecentStockStore(),
        preference_store=SimpleNamespace(), historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_: {"ready": False, "status": "PRODUCTION_SESSION_UNAVAILABLE"},
    )
    flow._now_factory = lambda: NOW
    flow.validation_failed.connect(failures.append)
    monkeypatch.setattr(flow, "_start_pool_prepare", pools.append)
    monkeypatch.setattr(v5.QTimer, "singleShot", lambda _delay, callback: scheduled.append(callback))
    seed = v5.IndicatorFollowSignalValidationSeed(
        v5.ValidationSettingsSnapshot({"bar": {"bar_minutes": 5}}), {},
    )
    owner = QWidget()

    def open_window(stock=None):
        flow._last_selected_stock = stock
        window = flow._open_signal_window(seed, weakref.ref(owner))
        assert window is not None, failures
        while scheduled:
            scheduled.pop(0)()
        return window

    yield SimpleNamespace(root=tmp_path, flow=flow, host=host, calls=calls,
                          pools=pools, open=open_window, app=app)
    for window in tuple(flow._open_windows.values()):
        window.destroyed.disconnect()
        flow._release_window(id(window))
        window.close()
    owner.close()


@pytest.mark.parametrize("eligible,source,identity", [
    (True, "INTEGRATED", "005930_AL"), (False, "KRX", "005930"),
])
@pytest.mark.parametrize("route", ["picker", "recent", "reentry"])
def test_selection_freezes_before_direct_chart_history(selection, eligible, source, identity, route):
    _library(selection.root, eligible)
    assert gui_stock_data.load_stock_library_snapshot(selection.root).state == "READY"
    assert plans.read_stock_market_source_plan(selection.root, STOCK.code, NOW.date()) is None
    window = selection.open(STOCK if route == "reentry" else None)
    if route == "picker":
        selection.host.pickers.append(_FakePicker(QDialog.Accepted, STOCK))
        window.stock_selection_requested.emit()
    elif route == "recent":
        window.recent_stock_selected.emit(STOCK)
    plan = plans.read_stock_market_source_plan(selection.root, STOCK.code, NOW.date())
    assert plan is not None, window.errors
    assert plan["nxt_available"] is eligible
    assert not window.errors
    assert selection.calls == [(source, identity, "005930", 5)]
    assert len(selection.pools) == 1
    assert selection.pools[0]["pool"]["market_data_identity"] == identity
    assert selection.pools[0]["pool"]["market_source"] == source


@pytest.mark.parametrize("route", ["picker", "recent", "reentry"])
@pytest.mark.parametrize("eligible,state,name", [
    (None, "READY", STOCK.name), (True, "SYNCING", STOCK.name),
    (True, "READY", "다른종목"),
])
def test_unverified_selection_blocks_before_history_and_plan_write(selection, route, eligible, state, name):
    _library(selection.root, eligible, state=state, name=name)
    window = selection.open(STOCK if route == "reentry" else None)
    if route == "picker":
        selection.host.pickers.append(_FakePicker(QDialog.Accepted, STOCK))
        window.stock_selection_requested.emit()
    elif route == "recent":
        window.recent_stock_selected.emit(STOCK)
    assert window.errors
    assert not selection.calls and not selection.pools
    assert not (selection.root / "runtime/market_source_plans").exists()


@pytest.mark.parametrize("eligible", [True, False])
def test_existing_plan_and_healthy_reselection_do_not_lookup_rewrite_or_reload(selection, eligible):
    plans.initialize_stock_market_source_plan(
        selection.root, STOCK.code, STOCK.name, trading_day=NOW.date(),
        decided_at=NOW, nxt_available=eligible,
    )
    paths = list((selection.root / "runtime/market_source_plans").rglob("*.json"))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    with patch.object(gui_stock_data, "load_stock_library_snapshot", side_effect=AssertionError("late eligibility lookup")):
        window = selection.open(STOCK)
        window.recent_stock_selected.emit(STOCK)
        window.recent_stock_selected.emit(STOCK)
        assert selection.flow._verified_nxt_validation_stock(STOCK) is eligible
    assert not window.errors
    assert len(selection.calls) == 1
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths} == before


def test_same_stock_retry_recovers_missing_plan_without_reopening(selection):
    window = selection.open(STOCK)
    assert window.errors == ["NXT_ELIGIBILITY_NOT_VERIFIED"]
    _library(selection.root)
    window.errors.clear()
    window.recent_stock_selected.emit(STOCK)
    assert plans.read_stock_market_source_plan(selection.root, STOCK.code, NOW.date()) is not None
    assert not window.errors
    assert selection.calls == [("INTEGRATED", "005930_AL", "005930", 5)]


@pytest.mark.parametrize("eligible", [True, False])
def test_selected_shared_plan_preserves_consumers_and_session_boundaries(selection, eligible):
    _library(selection.root, eligible)
    selection.open(STOCK)
    projection = chart_market_session_projection(STOCK.code, selection.root, trading_day=NOW.date())
    plan = projection["source_plan"]
    assert plan is not None
    checks = [
        ("08:49:59", "NXT" if eligible else None), ("08:50:00", None),
        ("08:59:59", None), ("09:00:00", "SOR" if eligible else "KRX"),
        ("15:20:00", "SOR" if eligible else "KRX"),
        ("15:30:00", "SOR" if eligible else "KRX"),
        ("15:30:01", "SOR" if eligible else None),
        ("15:35:00", "SOR" if eligible else None),
        ("15:40:00", "SOR" if eligible else "KRX"),
    ]
    before = {p: p.read_bytes() for p in (selection.root / "runtime/market_source_plans").rglob("*.json")}
    with patch.object(gui_stock_data, "load_stock_library_snapshot", side_effect=AssertionError("periodic lookup")):
        assert selection.flow._verified_nxt_validation_stock(STOCK) is eligible
        for clock, expected in checks:
            at = datetime.fromisoformat(f"{NOW.date()}T{clock}+09:00")
            selected = plans.source_for_time(plan, at)
            assert (selected["source"] if selected else None) == expected
            assert native_history_source(plan, at) == selected
            assert probe._calculation_consumer_scope(STOCK.code, at, "SOR") == (
                f"{expected}_CALCULATION_ONLY" if expected else ""
            )
            # Mock reads the same persisted plan, without its own eligibility decision.
            from mock_validation_candle_provider import read_stock_market_source_plan, source_for_time
            assert source_for_time(read_stock_market_source_plan(selection.root, STOCK.code, at.date()), at) == selected
    assert {p: p.read_bytes() for p in before} == before


def test_corrupt_existing_plan_is_not_replaced_from_ready_library(selection):
    _library(selection.root)
    anchor = selection.root / "runtime/market_source_plans/anchors/005930.json"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("{}", encoding="utf-8")
    window = selection.open(STOCK)
    assert window.errors and not selection.calls
    assert anchor.read_text(encoding="utf-8") == "{}"


def test_same_stock_unreadable_plan_blocks_without_escaping_or_rewriting(selection, monkeypatch):
    _library(selection.root)
    window = selection.open(STOCK)
    before = {p: p.read_bytes() for p in (selection.root / "runtime/market_source_plans").rglob("*.json")}
    monkeypatch.setattr(plans, "_read_anchor", lambda *a: (_ for _ in ()).throw(PermissionError("plan unreadable")))
    selection.flow._activate_stock_for_window(window, STOCK)
    assert window.errors == ["plan unreadable"]
    assert len(selection.calls) == 1
    assert {p: p.read_bytes() for p in before} == before


def test_blocked_window_retries_when_another_window_already_created_shared_plan(selection):
    blocked = selection.open(STOCK)
    assert blocked.errors == ["NXT_ELIGIBILITY_NOT_VERIFIED"]
    _library(selection.root)
    healthy = selection.open(STOCK)
    assert not healthy.errors and len(selection.calls) == 1
    before = {p: p.read_bytes() for p in (selection.root / "runtime/market_source_plans").rglob("*.json")}
    with patch.object(gui_stock_data, "load_stock_library_snapshot", side_effect=AssertionError("rejudged eligibility")):
        blocked.recent_stock_selected.emit(STOCK)
    assert blocked.validation_requests == 1
    assert len(selection.calls) == 2
    assert {p: p.read_bytes() for p in before} == before
