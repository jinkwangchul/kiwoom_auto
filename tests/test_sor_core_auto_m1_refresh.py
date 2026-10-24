"""Actual SOR participant setup -> native _AL M1 refresh requirements.

All tests use mocked brokers and do not request a TR or submit an order.
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json

import pytest
from PyQt5.QtCore import QCoreApplication, QObject, QTimer

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_market_data_host import MarketDataHost
from stock_repository import StockRecord


CODE = "005930"
_QT_APP = None


def _requirement(*, sor=True, timeframe=5, diagnostic=False):
    result = {
        "stock_code": CODE,
        "rules": {"bar": {"bar_minutes": timeframe}},
        "projection_request": {
            "projection": "FORMING_BASE_BAR",
            "warmup_bars": 35,
            "history_target_bars": 900,
        },
    }
    result["calculation_market_source"] = "SOR" if sor else "KRX"
    if diagnostic:
        result["diagnostic_sor_m1"] = True
    return result


def _host():
    return SimpleNamespace(
        kiwoom_api=SimpleNamespace(
            broker_session_snapshot=lambda: SimpleNamespace(
                connected=True, connection_epoch=7,
                login_session_id="SESSION-7",
            )
        ),
        _candle_standby_required_by_stock={},
        _candle_standby_stock_codes=(),
        _candle_standby_timeframe_requirements_by_stock={},
        _candle_execution_timeframe_requirements_by_stock={},
        _candle_observation_timeframe_requirements_by_stock={},
        _display_timeframe_candles={},
        _display_timeframe_session_identity={},
        _sor_forming_m1_by_stock={},
    )


def test_legacy_krx_observation_requirement_does_not_register_raw_source():
    host = _host()
    host._candle_observation_stock_codes = ()
    host._candle_observation_required_by_stock = {}
    host._sor_observation_m1_required_by_stock = {}
    host._production_monitoring_stock_codes = ()
    host._execution_shadow_stock_codes = ()
    host.sync_monitoring_targets = lambda _codes: {"shadow_sync": {"ok": True}}
    MarketDataHost.sync_candle_observation_targets(
        host, [_requirement(sor=False)]
    )
    assert host._candle_observation_stock_codes == ()
    assert host._candle_observation_required_by_stock == {}
    assert host._sor_m1_required_by_stock == {}


def _sync(host, *, sor=True, diagnostic=False):
    return MarketDataHost.sync_candle_standby_requirements(
        host, [_requirement(sor=sor, diagnostic=diagnostic)]
    )


def _specs(host, minute_key):
    with patch.object(
        MarketDataHost, "_nxt_eligible_targets",
        return_value=(CODE,),
    ):
        return MarketDataHost.display_timeframe_refresh_specs(
            host, CODE, minute_key,
        )


def test_m5_sor_registration_adds_m1_to_governed_display_trs():
    host = _host()
    outcome = _sync(host)
    assert outcome["ok"] is True
    assert host._sor_m1_required_by_stock == {CODE: 6}
    assert MarketDataHost.direct_timeframe_requirements(
        host, CODE,
    ) == {1: 6, 5: 900}

    specs = _specs(host, "2026-10-20 09:05")
    by_tf = {item["timeframe_minutes"]: item for item in specs}
    assert set(by_tf) == {1, 5}
    assert by_tf[1]["required_count"] == 6
    assert by_tf[1]["request_count"] == 7
    assert by_tf[1]["nxt_available"] is True
    assert by_tf[5]["required_count"] == 900
    # SOR standby refreshes its M1 and M5 display sources without KRX direct TRs.
    direct = MarketDataHost.direct_timeframe_refresh_specs(
        host, CODE, "2026-10-20 09:05"
    )
    assert direct == ()
    assert MarketDataHost.operation_candle_request_cost(
        host, CODE, "2026-10-20 09:05"
    ) == 3


def test_sor_standby_does_not_schedule_krx_direct_m5_but_observation_can():
    host = _host()
    _sync(host, sor=True)
    assert MarketDataHost.direct_timeframe_refresh_specs(
        host, CODE, "2026-10-20 09:05",
    ) == ()
    assert {spec["timeframe_minutes"] for spec in _specs(
        host, "2026-10-20 09:05",
    )} == {1, 5}
    host._candle_observation_timeframe_requirements_by_stock[CODE] = {5: 10}
    assert [spec["timeframe_minutes"] for spec in MarketDataHost.direct_timeframe_refresh_specs(
        host, CODE, "2026-10-20 09:05",
    )] == [5]


def test_sor_standby_skips_legacy_canonical_m1_refresh_decision():
    from kiwoom_market_data_authority import REALTIME_PRIMARY_SKIP

    host = _host()
    _sync(host, sor=True)
    host._market_data_authority = SimpleNamespace(
        mode=lambda code: (_ for _ in ()).throw(AssertionError("KRX authority used")),
    )
    decision = MarketDataHost.market_data_refresh_decision(
        host, CODE, "2026-10-20 09:05",
    )
    assert decision["decision"] == REALTIME_PRIMARY_SKIP


def test_missing_requirement_source_defaults_to_sor():
    host = _host()
    requirement = _requirement()
    requirement.pop("calculation_market_source")
    MarketDataHost.sync_candle_standby_requirements(host, [requirement])
    assert host._sor_m1_required_by_stock == {CODE: 6}


def test_legacy_krx_standby_requirement_is_rejected_without_direct_refresh():
    host = _host()
    result = _sync(host, sor=False)
    assert result["ok"] is False
    assert result["standby_stock_codes"] == ()
    assert host._sor_m1_required_by_stock == {}
    assert MarketDataHost.direct_timeframe_requirements(host, CODE) == {}


def test_legacy_krx_requirement_cannot_preserve_previous_sor_standby():
    host = _host()
    _sync(host, sor=True)
    result = MarketDataHost.sync_candle_standby_requirements(
        host, [_requirement(sor=False)], preserve_stock_codes=(CODE,),
    )
    assert result["ok"] is False
    assert result["preserved_stock_codes"] == ()
    assert host._sor_standby_m1_required_by_stock == {}
    assert host._candle_standby_stock_codes == ()


def test_non_sor_registration_and_switch_back_have_no_added_m1():
    host = _host()
    result = _sync(host, sor=False)
    assert result["ok"] is False
    assert host._sor_m1_required_by_stock == {}
    assert [row["timeframe_minutes"] for row in _specs(
        host, "2026-10-20 09:05"
    )] == []
    assert MarketDataHost.operation_candle_request_cost(
        host, CODE, "2026-10-20 09:05"
    ) == 1

    _sync(host, sor=True)
    assert host._sor_m1_required_by_stock == {CODE: 6}
    _sync(host, sor=False)
    assert host._sor_m1_required_by_stock == {}
    assert [row["timeframe_minutes"] for row in _specs(
        host, "2026-10-20 09:05"
    )] == []


def test_diagnostic_requirement_uses_sor_m1_formula_and_clears_on_next_sync():
    host = _host()
    _sync(host, sor=True, diagnostic=True)
    assert host._sor_m1_required_by_stock == {CODE: 6}
    assert MarketDataHost.direct_timeframe_requirements(host, CODE) == {
        1: 6, 5: 900,
    }
    assert [row["timeframe_minutes"] for row in _specs(
        host, "2026-10-20 09:05"
    )] == [1, 5]
    host._sor_forming_m1_by_stock[CODE] = {"candle": "old"}
    MarketDataHost.sync_candle_standby_requirements(host, [])
    assert host._sor_m1_required_by_stock == {}
    assert host._sor_forming_m1_by_stock == {}
    assert [row["timeframe_minutes"] for row in _specs(
        host, "2026-10-20 09:05"
    )] == []


@pytest.mark.parametrize("failed_current_item", [False, True])
def test_failed_preserved_stock_keeps_sor_m1_requirement(failed_current_item):
    host = _host()
    _sync(host)
    requirements = []
    preserve_codes = (CODE,)
    if failed_current_item:
        invalid = _requirement()
        invalid["rules"] = {"bar": {"bar_minutes": 0}}
        requirements = [invalid]
        preserve_codes = ()

    result = MarketDataHost.sync_candle_standby_requirements(
        host, requirements, preserve_stock_codes=preserve_codes,
    )

    assert result["ok"] is not failed_current_item
    assert result["preserved_stock_codes"] == (CODE,)
    assert host._sor_m1_required_by_stock == {CODE: 6}
    assert MarketDataHost.direct_timeframe_requirements(host, CODE) == {
        1: 6, 5: 900,
    }


@pytest.mark.parametrize("unregister", [False, True])
def test_removed_sor_stock_clears_only_its_forming_snapshot(unregister):
    other_code = "000660"
    host = _host()
    other = _requirement()
    other["stock_code"] = other_code
    MarketDataHost.sync_candle_standby_requirements(
        host, [_requirement(), other],
    )
    forming = {CODE: {"candle": "removed"}, other_code: {"candle": "kept"}}
    history = {
        (CODE, 1): ({"bar_time": "completed"},),
        (other_code, 1): ({"bar_time": "other completed"},),
    }
    host._sor_forming_m1_by_stock = dict(forming)
    host._display_timeframe_candles = dict(history)
    requirements = [other] if unregister else [_requirement(sor=False), other]
    preserve_codes = ()

    MarketDataHost.sync_candle_standby_requirements(
        host, requirements, preserve_stock_codes=preserve_codes,
    )

    assert host._sor_m1_required_by_stock == {other_code: 6}
    assert host._sor_forming_m1_by_stock == {other_code: forming[other_code]}
    assert host._display_timeframe_candles == history
    assert CODE not in host._candle_standby_stock_codes


def test_invalid_explicit_krx_requirement_does_not_preserve_sor_m1():
    host = _host()
    _sync(host)
    invalid_krx = _requirement(sor=False)
    invalid_krx["rules"] = {"bar": {"bar_minutes": 0}}
    host._sor_forming_m1_by_stock[CODE] = {"candle": "removed"}

    result = MarketDataHost.sync_candle_standby_requirements(
        host, [invalid_krx],
    )

    assert result["ok"] is False
    assert result["preserved_stock_codes"] == ()
    assert host._sor_m1_required_by_stock == {}
    assert host._sor_forming_m1_by_stock == {}


def test_already_cached_complete_m1_still_fetches_current_forming_minute_once():
    host = _host()
    _sync(host)
    old = {
        "bar_time": "2026-10-19T15:25:00+09:00",
        "is_complete": True,
    }
    newest = {
        "bar_time": "2026-10-20T09:04:00+09:00",
        "is_complete": True,
    }
    host._display_timeframe_candles[(CODE, 1)] = (
        *(dict(old) for _ in range(699)), newest,
    )
    host._display_timeframe_session_identity[(CODE, 1)] = (
        7, "SESSION-7",
    )
    assert [spec["timeframe_minutes"] for spec in _specs(
        host, "2026-10-20 09:05"
    ) if spec["timeframe_minutes"] == 1] == [1]

    host._sor_forming_m1_by_stock[CODE] = {
        "session_identity": (7, "SESSION-7"),
        "candle": {
            "bar_time": "2026-10-20T09:05:00+09:00",
            "is_complete": False,
        },
    }
    assert not [spec for spec in _specs(
        host, "2026-10-20 09:05"
    ) if spec["timeframe_minutes"] == 1]

    assert [spec["timeframe_minutes"] for spec in _specs(
        host, "2026-10-20 09:06"
    ) if spec["timeframe_minutes"] == 1] == [1]


@pytest.mark.parametrize("diagnostic", [False, True])
def test_session_break_and_non_nxt_stock_do_not_force_new_sor_tr(diagnostic):
    host = _host()
    _sync(host, sor=True, diagnostic=diagnostic)
    host._display_timeframe_candles[(CODE, 1)] = tuple(
        {"bar_time": "2026-10-20T08:49:00+09:00"}
        for _ in range(700)
    )
    host._display_timeframe_session_identity[(CODE, 1)] = (
        7, "SESSION-7",
    )
    assert not [spec for spec in _specs(
        host, "2026-10-20 08:55"
    ) if spec["timeframe_minutes"] == 1]
    with patch.object(
        MarketDataHost, "_nxt_eligible_targets",
        return_value=(),
    ):
        assert MarketDataHost.display_timeframe_refresh_specs(
            host, CODE, "2026-10-20 09:05"
        ) == ()


@pytest.mark.parametrize("selected_source", ["SOR", "KRX", "", None])
@pytest.mark.parametrize(
    "tick_flag, diagnostic_code, registered_code, expected_diagnostic",
    [
        ("0", "", CODE, False),
        ("0", CODE, CODE, False),
        ("1", "", CODE, False),
        ("1", "000660", CODE, False),
        ("1", CODE, "000660", False),
        ("1", CODE, CODE, True),
    ],
)
def test_login_entry_defaults_to_sor_and_preserves_explicit_krx(
    selected_source, tick_flag, diagnostic_code, registered_code,
    expected_diagnostic,
):
    record = StockRecord(
        code=registered_code, name="Test", routine="", enabled=True,
        stock_path=f"stocks/{registered_code}_Test",
        assigned_routine_instance_id="INSTANCE-CANDLE",
    )
    projection = SimpleNamespace(
        target_stock_codes=(registered_code,),
        initial_snapshot_target_stock_codes=(registered_code,),
        unsupported_stock_codes=(),
        source_record_count=1,
    )
    repository = Mock()
    repository.realtime_monitoring_universe.return_value = projection
    repository.list_current_registered_stocks.return_value = [record]
    market = SimpleNamespace(
        sync_monitoring_targets=Mock(return_value={"ok": True}),
        sync_candle_standby_requirements=Mock(
            return_value={"ok": True, "standby_stock_codes": (registered_code,)}
        ),
        refresh_operation_candles=Mock(return_value={"accepted": True}),
    )
    host = SimpleNamespace(_market_data_host=market)
    stock_config = ({"calculation_market_source": selected_source}
                    if selected_source is not None else {})
    with (
        patch.dict("os.environ", {
            "KIWOOM_V5_SOR_TICK_EVIDENCE": tick_flag,
            "KIWOOM_V5_SOR_DIAGNOSTIC_STOCK_CODE": diagnostic_code,
        }),
        patch(
            "gui_auto_trade_operation_host.StockRepository",
            return_value=repository,
        ),
        patch(
            "routine_instance_registry.routine_instance_by_id",
            return_value=SimpleNamespace(
                definition_id="candle",
                rules_path=Path("read-only-rules.json"),
            ),
        ),
        patch(
            "routine_instance_registry.routine_definition_by_id",
            return_value=SimpleNamespace(
                locators={
                    "evaluation": {
                        "market_bar_projection_callable": "declare",
                    }
                }
            ),
        ),
        patch(
            "routine_package_contract.load_routine_callable",
            return_value=lambda _rules: {
                "projection": "FORMING_BASE_BAR",
                "warmup_bars": 35,
            },
        ),
        patch.object(
            Path, "read_text",
            return_value=json.dumps({"bar": {"bar_minutes": 5}}),
        ),
        patch(
            "gui_auto_trade_operation_host.read_json_dict",
            return_value=stock_config,
        ),
    ):
        result = AutoTradeOperationHost.sync_monitoring_universe_for_current_session(
            host
        )
    assert "standby_requirement_errors" in result
    assert result["standby_requirement_errors"] == ()
    kwargs = market.sync_candle_standby_requirements.call_args
    expected = {
        "stock_code": registered_code,
        "rules": {"bar": {"bar_minutes": 5}},
        "projection_request": {
            "projection": "FORMING_BASE_BAR",
            "warmup_bars": 35,
        },
    }
    expected["calculation_market_source"] = (
        selected_source if selected_source is not None else "SOR"
    )
    if expected_diagnostic:
        expected["diagnostic_sor_m1"] = True
    assert kwargs.args[0] == [expected]
    assert stock_config == ({"calculation_market_source": selected_source}
                            if selected_source is not None else {})
    market.refresh_operation_candles.assert_called_once()
    assert market.refresh_operation_candles.call_args.kwargs["drain_all"] is True


@pytest.mark.parametrize("diagnostic", [False, True])
def test_existing_refresh_coordinator_dispatches_m1_for_m5_sor(
    tmp_path, diagnostic,
):
    """Exercise the existing production refresh scheduler, not just specs."""
    import auto_candle_refresh

    stock_dir = tmp_path / f"{CODE}_Test"
    stock_dir.mkdir()
    (stock_dir / "state.json").write_text(
        '{"status":"RUNNING","trade_enabled":true}', encoding="utf-8"
    )
    host = _host()
    _sync(host, sor=True, diagnostic=diagnostic)
    calls = {"al": [], "krx_direct": []}

    def al_request(code, _name, *, interval, count, callback, nxt_available):
        calls["al"].append((interval, count))
        callback({
            "ok": True, "code": code, "interval": interval,
            "rows": [{
                "체결시간": "20261020090000", "시가": "100",
                "고가": "102", "저가": "99",
                "현재가": "101", "거래량": "10",
            }],
            "market_data_identity": f"{code}_AL",
            "market_source": "INTEGRATED", "display_only": True,
            "connection_epoch": 7,
            "login_session_id": "SESSION-7",
        })
        return {"ok": True, "rqname": f"AL-{interval}"}

    def direct_request(code, _name, *, interval, count, callback):
        calls["krx_direct"].append((interval, count))
        callback({
            "ok": True, "code": code, "interval": interval,
            "rows": [], "market_data_identity": code,
            "market_source": "KRX",
        })
        return {"ok": True}

    host.kiwoom_api.request_minute_candles_display_only_integrated = al_request
    host.kiwoom_api.request_minute_candles_read_only = direct_request
    host.kiwoom_api.is_available = lambda: True
    host.kiwoom_api.is_connected = lambda: True
    host.kiwoom_api.request_minute_candles = Mock(
        side_effect=AssertionError("canonical M1 must be skipped")
    )
    host.kiwoom_api.sync_realtime_shadow_targets = Mock(
        side_effect=AssertionError("no new broker registration")
    )
    host.kiwoom_api.send_order = Mock(
        side_effect=AssertionError("no order submission")
    )
    host.registered_operation_targets = lambda: [(stock_dir, CODE, "Test")]
    host.direct_timeframe_refresh_specs = lambda code, key: (
        MarketDataHost.direct_timeframe_refresh_specs(host, code, key)
    )
    host.display_timeframe_refresh_specs = lambda code, key: (
        MarketDataHost.display_timeframe_refresh_specs(host, code, key)
    )
    host.operation_candle_request_cost = lambda code, key: (
        MarketDataHost.operation_candle_request_cost(host, code, key)
    )
    host.install_direct_timeframe_candles = lambda *_a, **_kw: {"ok": True}
    host.install_display_timeframe_candles = lambda *_a, **_kw: {"ok": True}
    host._market_data_authority = SimpleNamespace(
        mode=Mock(side_effect=AssertionError("KRX authority used")),
    )
    host.market_data_refresh_decision = lambda code, key: (
        MarketDataHost.market_data_refresh_decision(host, code, key)
    )
    done = []
    with (
        patch.object(
            MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)
        ),
        patch.object(
            auto_candle_refresh.QTimer, "singleShot",
            side_effect=lambda _delay, callback: callback()
        ),
    ):
        result = auto_candle_refresh.refresh_operation_candles(
            host, "2026-10-20 09:05", on_complete=done.append,
        )
    assert calls["krx_direct"] == []
    assert calls["al"] == [(1, 7), (5, 901)]
    assert result["requested"] == 2
    assert result["display_requested"] == 2
    assert result["display_succeeded"] == 2
    assert done[0]["completed"] is True
    assert host.kiwoom_api.request_minute_candles.call_count == 0
    host.kiwoom_api.sync_realtime_shadow_targets.assert_not_called()
    host.kiwoom_api.send_order.assert_not_called()


def test_broker_tr_budget_remains_original():
    from auto_candle_refresh import (
        MAX_REQUESTS_PER_CYCLE,
        REQUEST_SPACING_MS,
    )
    assert MAX_REQUESTS_PER_CYCLE == 15
    assert REQUEST_SPACING_MS >= 1000


def _idle_host(monkeypatch, *, tick="1", code=CODE):
    global _QT_APP
    _QT_APP = QCoreApplication.instance() or QCoreApplication([])
    if tick is None:
        monkeypatch.delenv("KIWOOM_V5_SOR_TICK_EVIDENCE", raising=False)
    else:
        monkeypatch.setenv("KIWOOM_V5_SOR_TICK_EVIDENCE", tick)
    if code is None:
        monkeypatch.delenv("KIWOOM_V5_SOR_DIAGNOSTIC_STOCK_CODE", raising=False)
    else:
        monkeypatch.setenv("KIWOOM_V5_SOR_DIAGNOSTIC_STOCK_CODE", code)
    owner = QObject()
    operation_timer = QTimer(owner)
    owner.operation_timer = lambda: operation_timer
    api = SimpleNamespace(
        broker_session_snapshot=lambda: SimpleNamespace(
            connected=True, connection_epoch=7, login_session_id="SESSION-7",
        ),
        request_minute_candles_display_only_integrated=Mock(
            return_value={"ok": True, "rqname": "AL-M1"},
        ),
        request_minute_candles=Mock(side_effect=AssertionError("canonical M1")),
        send_order=Mock(side_effect=AssertionError("order")),
        sync_realtime_shadow_targets=Mock(
            side_effect=AssertionError("registration"),
        ),
    )
    with patch("sor_tick_evidence.TickEvidenceSampler", return_value=Mock()):
        host = MarketDataHost(owner, api, lambda _: None)
    host._sor_forming_m1_by_stock = {}
    return host, api, operation_timer


@pytest.mark.parametrize("tick,code", [
    (None, CODE), ("0", CODE), ("1", None), ("1", "000660"),
])
def test_idle_diagnostic_timer_requires_both_process_flags(monkeypatch, tick, code):
    host, api, _ = _idle_host(monkeypatch, tick=tick, code=code)
    _sync(host, sor=True, diagnostic=True)
    assert host._sor_diagnostic_idle_timer is None
    host._clear_session_state()
    assert host._sor_diagnostic_idle_timer is None
    assert not api.request_minute_candles_display_only_integrated.called


@pytest.mark.parametrize("stop", ["clear", "shutdown"])
def test_idle_diagnostic_rearms_after_login_session_reset_until_stop(monkeypatch, stop):
    host, api, operation_timer = _idle_host(monkeypatch)
    _sync(host, sor=True, diagnostic=True)
    assert host._sor_diagnostic_idle_timer.isActive()
    host._sor_diagnostic_idle_inflight = True
    host._sor_diagnostic_idle_last_attempt_minute = "old-session"
    generation = host._sor_diagnostic_idle_request_generation

    host._clear_session_state()

    assert host._sor_diagnostic_idle_required is True
    assert host._sor_diagnostic_idle_timer.isActive()
    assert host._sor_diagnostic_idle_inflight is False
    assert host._sor_diagnostic_idle_request_generation == generation + 1
    assert host._sor_diagnostic_idle_last_attempt_minute == ""
    assert not operation_timer.isActive()
    api.request_minute_candles_display_only_integrated.assert_not_called()
    api.send_order.assert_not_called()
    api.sync_realtime_shadow_targets.assert_not_called()

    getattr(host, stop)()
    assert host._sor_diagnostic_idle_required is False
    assert not host._sor_diagnostic_idle_timer.isActive()


def test_idle_diagnostic_timer_requires_latest_registered_diagnostic_requirement(
    monkeypatch,
):
    host, api, _ = _idle_host(monkeypatch)
    _sync(host, sor=True, diagnostic=False)
    assert not host._sor_diagnostic_idle_timer.isActive()
    _sync(host, sor=True, diagnostic=True)
    assert host._sor_diagnostic_idle_timer.isActive()
    _sync(host, sor=False, diagnostic=False)
    assert not host._sor_diagnostic_idle_timer.isActive()
    assert not api.request_minute_candles_display_only_integrated.called


def test_idle_diagnostic_dispatches_one_governed_al_m1_per_minute(monkeypatch):
    host, api, operation_timer = _idle_host(monkeypatch)
    _sync(host, sor=True, diagnostic=True)
    now = datetime(2026, 10, 22, 9, 5, 10, tzinfo=SEOUL_TIMEZONE)
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
        host._on_sor_diagnostic_idle_timeout(now)
    api.request_minute_candles_display_only_integrated.assert_called_once()
    args, kwargs = api.request_minute_candles_display_only_integrated.call_args
    assert args == (CODE, "")
    assert kwargs["interval"] == 1
    assert kwargs["count"] == 7
    assert kwargs["nxt_available"] is True
    assert host._sor_diagnostic_idle_inflight is True
    api.request_minute_candles.assert_not_called()
    api.send_order.assert_not_called()
    api.sync_realtime_shadow_targets.assert_not_called()
    assert not operation_timer.isActive()
    assert host._sor_diagnostic_idle_timer.isSingleShot()
    assert host._sor_diagnostic_idle_timer.isActive()
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now.replace(minute=6))
    assert api.request_minute_candles_display_only_integrated.call_count == 1
    kwargs["callback"]({"ok": False, "reason_code": "OFFLINE_TEST"})
    assert host._sor_diagnostic_idle_inflight is False
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
        host._on_sor_diagnostic_idle_timeout(now.replace(minute=6))
    assert api.request_minute_candles_display_only_integrated.call_count == 2
    second_callback = api.request_minute_candles_display_only_integrated.call_args.kwargs[
        "callback"
    ]
    host.shutdown()
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        second_callback({
            "ok": True, "code": CODE, "interval": 1,
            "rows": [], "market_data_identity": f"{CODE}_AL",
            "market_source": "INTEGRATED", "display_only": True,
            "connection_epoch": 7, "login_session_id": "SESSION-7",
        })
    assert host._sor_diagnostic_idle_inflight is False
    assert not host._sor_diagnostic_idle_timer.isActive()
    assert (CODE, 1) not in host._display_timeframe_candles


def test_idle_diagnostic_gates_and_lifecycle(monkeypatch):
    host, api, operation_timer = _idle_host(monkeypatch)
    _sync(host, sor=True, diagnostic=True)
    now = datetime(2026, 10, 22, 9, 5, 10, tzinfo=SEOUL_TIMEZONE)
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=()):
        host._on_sor_diagnostic_idle_timeout(now)
    assert not api.request_minute_candles_display_only_integrated.called
    operation_timer.start(10_000)
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
    operation_timer.stop()
    host._automatic_candle_refresh_inflight = True
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
    host._automatic_candle_refresh_inflight = False
    host._sor_forming_m1_by_stock[CODE] = {
        "session_identity": (7, "SESSION-7"),
        "candle": {"bar_time": "2026-10-22T09:05:00+09:00"},
    }
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
    host._sor_forming_m1_by_stock.clear()
    with (
        patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)),
        patch.object(MarketDataHost, "display_timeframe_refresh_specs", return_value=()),
    ):
        host._on_sor_diagnostic_idle_timeout(now)
    for blocked in (
        now.replace(hour=7), now.replace(hour=15, minute=35),
        now.replace(hour=20),
    ):
        with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
            host._on_sor_diagnostic_idle_timeout(blocked)
    with patch.object(host.kiwoom_api, "broker_session_snapshot", return_value=None):
        with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
            host._on_sor_diagnostic_idle_timeout(now)
    assert not api.request_minute_candles_display_only_integrated.called
    _sync(host, sor=False, diagnostic=False)
    assert not host._sor_diagnostic_idle_timer.isActive()
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
    host.shutdown()
    assert not host._sor_diagnostic_idle_timer.isActive()
    assert not api.request_minute_candles_display_only_integrated.called


def test_idle_diagnostic_rearms_for_next_wall_clock_minute(monkeypatch):
    host, _, _ = _idle_host(monkeypatch)
    _sync(host, sor=True, diagnostic=True)
    host._arm_sor_diagnostic_idle_timer(
        datetime(2026, 10, 22, 9, 5, 9, 500000, tzinfo=SEOUL_TIMEZONE),
    )
    assert host._sor_diagnostic_idle_timer.interval() == 500
    host._arm_sor_diagnostic_idle_timer(
        datetime(2026, 10, 22, 9, 5, 10, tzinfo=SEOUL_TIMEZONE),
    )
    assert host._sor_diagnostic_idle_timer.interval() == 60_000
    host.shutdown()


def test_idle_diagnostic_synchronous_error_callback_clears_inflight(monkeypatch):
    host, api, _ = _idle_host(monkeypatch)
    _sync(host, sor=True, diagnostic=True)

    def failed_request(_code, _name, *, callback, **_kwargs):
        callback({"ok": False, "reason_code": "BROKER_REJECTED"})
        return {"ok": False, "reason_code": "BROKER_REJECTED"}

    api.request_minute_candles_display_only_integrated.side_effect = failed_request
    now = datetime(2026, 10, 22, 9, 5, 10, tzinfo=SEOUL_TIMEZONE)
    with patch.object(MarketDataHost, "_nxt_eligible_targets", return_value=(CODE,)):
        host._on_sor_diagnostic_idle_timeout(now)
    assert host._sor_diagnostic_idle_inflight is False
    assert api.request_minute_candles_display_only_integrated.call_count == 1
    host.shutdown()
