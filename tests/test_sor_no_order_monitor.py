"""The direct SOR monitor is event driven and never enters an order path."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject, pyqtSignal

import sor_no_order_monitor as monitor_module


KST = timezone(timedelta(hours=9))


class FakeMarketHost(QObject):
    direct_sor_completed = pyqtSignal(object)

    def __init__(self, bar_time):
        super().__init__()
        self._shutting_down = False
        self._monitoring_target_stock_codes = ("005930",)
        self.session = (7, "broker-session")
        self.bar_time = bar_time
        self.projections = []

    def _current_display_session_identity(self):
        return self.session

    def project_routine_candles(self, **kwargs):
        assert threading.current_thread() is threading.main_thread()
        self.projections.append(kwargs)
        return {"candles": [{"bar_time": self.bar_time}]}


def _drain(app, predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert predicate()


def test_accepted_direct_bar_autonomously_invokes_no_order_capture_once(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime.now(KST)
    bar = (now - timedelta(minutes=3)).replace(second=0, microsecond=0).isoformat()
    host = FakeMarketHost(bar)
    config = tmp_path / "config.json"
    rules_path = tmp_path / "rules.json"
    config.write_text(json.dumps({"assigned_routine_instance_id": "RI-1"}))
    rules_path.write_text("{}")
    prepared = {
        "instance_id": "RI-1", "rules": {"bar": {"bar_minutes": 3}},
        "facts_revision": "R-1",
        "rules_bytes": b"{}", "rules_path": rules_path, "config_path": config,
        "request": {"projection": "COMPLETED_TIMEFRAME"},
        "selector": lambda *_: None, "cycle": {"status": "resolved", "holding_qty": 0},
    }
    monkeypatch.setattr(monitor_module, "_prepare", lambda event, _facts:
                        {**prepared, "event": event})
    calls = []
    monkeypatch.setattr(monitor_module, "capture_prepared_direct_sor_signal",
                        lambda **kwargs: calls.append(kwargs) or {"status": "OBSERVED"})
    import sor_m3_production_capture as writer
    saved = []
    monkeypatch.setattr(writer, "enqueue_preview", lambda result: saved.append(result) or True)
    monitor = monitor_module.NoOrderSorMonitor(host, host)
    monitor.accept_main_facts({"captured_at": now.isoformat(), "revision": "R-1"})
    event = {"stock_code": "005930", "timeframe_minutes": 3,
             "bar_time": bar, "connection_epoch": 7,
             "login_session_id": "broker-session", "observed_at": now.isoformat()}
    host.direct_sor_completed.emit(event)
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: len(saved) == 1)
    assert len(host.projections) == len(calls) == 1
    assert calls[0]["source"]["candles"][-1]["bar_time"] == bar
    assert calls[0]["enqueue"] is False
    assert host.projections[0]["consumer_scope"] == "SOR_CALCULATION_ONLY"
    monitor.shutdown()


def test_completed_direct_bar_deduplicates_only_identical_source_and_rules(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime.now(KST)
    bar = (now - timedelta(minutes=3)).replace(second=0, microsecond=0).isoformat()
    host = FakeMarketHost(bar)
    config = tmp_path / "config.json"
    rules_path = tmp_path / "rules.json"
    config.write_text(json.dumps({"assigned_routine_instance_id": "RI-1"}))
    rules_path.write_text(json.dumps({"bar": {"bar_minutes": 3}}))
    row = {"bar_time": bar, "timeframe_minutes": 3,
           "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
           "display_only": True, "is_complete": True,
           "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}

    def project(**kwargs):
        host.projections.append(kwargs)
        source_hash = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        return {
            "available": True, "stock_code": "005930", "candles": [dict(row)],
            "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
            "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
            "calculation_only": True, "execution_enabled": False,
            "queue_connected": False, "send_order_connected": False,
            "timeframe_minutes": 3, "projection": "COMPLETED_TIMEFRAME",
            "complete": True, "forming_included": False,
            "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
            "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
            "source_identity": source_hash,
            "direct_source_receipt": {
                "stock_code": "005930", "timeframe_key": "M3",
                "source_content_sha256": source_hash, "connection_epoch": 7,
                "session_sha256": hashlib.sha256(b"broker-session").hexdigest(),
                "source_as_of": now.isoformat(),
            },
        }

    host.project_routine_candles = project

    def prepare(event, facts):
        rules_bytes = rules_path.read_bytes()
        return {
            "event": event, "instance_id": "RI-1", "rules": json.loads(rules_bytes),
            "facts_revision": facts["revision"], "rules_bytes": rules_bytes,
            "rules_path": rules_path, "config_path": config,
            "request": {"projection": "COMPLETED_TIMEFRAME"},
            "selector": lambda *_: {"signal": "NONE"},
            "cycle": {"status": "resolved", "holding_qty": 0},
        }

    monkeypatch.setattr(monitor_module, "_prepare", prepare)
    import sor_m3_production_capture as writer

    saved = []
    reject_next = [False]

    def enqueue(result):
        record = writer._safe_record(result)
        assert record is not None
        if reject_next[0]:
            reject_next[0] = False
            return False
        saved.append(record)
        return True

    monkeypatch.setattr(writer, "enqueue_preview", enqueue)
    monitor = monitor_module.NoOrderSorMonitor(host, host)
    monitor.accept_main_facts({"captured_at": now.isoformat(), "revision": "R-1"})
    event = {"stock_code": "005930", "timeframe_minutes": 3,
             "bar_time": bar, "connection_epoch": 7,
             "login_session_id": "broker-session", "observed_at": now.isoformat()}
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: len(saved) == 1 and not monitor._pending)
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: not monitor._pending)
    assert len(saved) == 1

    row["close"] = 101
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: len(saved) == 2 and not monitor._pending)
    assert saved[0]["sor_preview_evidence"]["input_hash"] != saved[1]["sor_preview_evidence"]["input_hash"]

    rules_path.write_text(json.dumps({"bar": {"bar_minutes": 3}, "threshold": 1}))
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: len(saved) == 3 and not monitor._pending)
    assert saved[1]["sor_preview_evidence"]["rules_hash"] != saved[2]["sor_preview_evidence"]["rules_hash"]

    row["close"] = 102
    reject_next[0] = True
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: monitor.last_result.get("reason") == "EVIDENCE_CAPTURE_UNAVAILABLE"
           and not monitor._pending)
    assert len(saved) == 3
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: len(saved) == 4 and not monitor._pending)
    monitor.shutdown()
    assert not monitor._enqueued


def test_missing_cycle_stale_or_cancelled_event_never_evaluates(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime.now(KST)
    bar = (now - timedelta(minutes=3)).replace(second=0, microsecond=0).isoformat()
    host = FakeMarketHost(bar)
    monitor = monitor_module.NoOrderSorMonitor(host, host)
    event = {"stock_code": "005930", "timeframe_minutes": 3,
             "bar_time": bar, "connection_epoch": 7,
             "login_session_id": "broker-session", "observed_at": now.isoformat()}
    host.direct_sor_completed.emit(event)
    assert host.projections == []
    monitor.accept_main_facts({"captured_at": now.isoformat(), "revision": "R-1"})
    monkeypatch.setattr(monitor_module, "_prepare", lambda *_: None)
    host.direct_sor_completed.emit(event)
    _drain(app, lambda: monitor.last_result["reason"] ==
           "ACTUAL_CYCLE_OR_ASSIGNMENT_UNPROVEN")
    assert host.projections == []
    host.direct_sor_completed.emit({**event, "observed_at":
                                    (now - timedelta(minutes=1)).isoformat()})
    assert host.projections == []
    monitor.shutdown()


def test_session_change_cancels_prepared_event_before_projection(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime.now(KST)
    bar = (now - timedelta(minutes=3)).replace(second=0, microsecond=0).isoformat()
    host = FakeMarketHost(bar)
    monitor = monitor_module.NoOrderSorMonitor(host, host)
    monitor.accept_main_facts({"captured_at": now.isoformat()})
    monkeypatch.setattr(monitor_module, "_prepare", lambda event, _facts: {
        "event": event, "rules": {"bar": {"bar_minutes": 3}},
        "facts_revision": "R-1",
        "request": {"projection": "COMPLETED_TIMEFRAME"},
    })
    host.direct_sor_completed.emit({
        "stock_code": "005930", "timeframe_minutes": 3, "bar_time": bar,
        "connection_epoch": 7, "login_session_id": "broker-session",
        "observed_at": now.isoformat(),
    })
    host.session = (8, "new-session")
    _drain(app, lambda: monitor.last_result["reason"] == "SOURCE_OR_SESSION_CHANGED")
    assert host.projections == []
    monitor.shutdown()


def test_replaced_cycle_facts_cancel_prepared_event(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    now = datetime.now(KST)
    bar = (now - timedelta(minutes=3)).replace(second=0, microsecond=0).isoformat()
    host = FakeMarketHost(bar)
    monitor = monitor_module.NoOrderSorMonitor(host, host)
    monitor.accept_main_facts({"revision": "old"})
    monkeypatch.setattr(monitor_module, "_prepare", lambda event, _facts: {
        "event": event, "facts_revision": "old", "rules": {}, "request": {},
    })
    host.direct_sor_completed.emit({
        "stock_code": "005930", "timeframe_minutes": 3, "bar_time": bar,
        "connection_epoch": 7, "login_session_id": "broker-session",
        "observed_at": now.isoformat(),
    })
    monitor.accept_main_facts({"revision": "new"})
    _drain(app, lambda: monitor.last_result["reason"] == "SOURCE_OR_SESSION_CHANGED")
    assert host.projections == []
    monitor.shutdown()


def test_prepared_capture_rejects_unresolved_cycle_and_bad_receipt():
    from sor_no_order_signal_probe import capture_prepared_direct_sor_signal

    now = datetime.now(KST)
    called = []
    arguments = dict(
        stock_code="005930", routine_instance_id="RI-1",
        observed_cycle={"status": "unresolved", "holding_qty": 0},
        observed_at=now, rules={"bar": {"bar_minutes": 3}},
        selector=lambda *_: called.append(True),
        source={"candles": [{"bar_time": now.isoformat()}],
                "direct_source_receipt": {}},
        session_identity=(7, "broker-session"), enqueue=False,
    )
    assert capture_prepared_direct_sor_signal(**arguments)["reason"] == "ACTUAL_CYCLE_UNRESOLVED"
    arguments["observed_cycle"] = {"status": "resolved", "holding_qty": 0}
    assert capture_prepared_direct_sor_signal(**arguments)["reason"] == "DIRECT_SOR_PROOF_UNAVAILABLE"
    arguments["observed_at"] = now - timedelta(minutes=1)
    assert capture_prepared_direct_sor_signal(**arguments)["reason"] == "OBSERVATION_TIME_INVALID"
    assert called == []


def test_monitor_has_no_order_probe_or_send_order_dependency():
    source = Path(monitor_module.__file__).read_text(encoding="utf-8")
    assert "from routine_signal_probe" not in source
    assert "import routine_signal_probe" not in source
    assert "send_order(" not in source
    assert "queue_open_order" not in source


def test_actual_market_host_emits_only_fresh_monitored_direct_completion():
    from gui_market_data_host import MarketDataHost
    from tests.test_display_only_integrated_history import (
        DisplayOnlyMarketDataHostTest, _opt10080_row,
    )

    host = DisplayOnlyMarketDataHostTest()._host()
    host._monitoring_target_stock_codes = ("005930",)
    host.direct_sor_completed = Mock()
    now = datetime(2026, 10, 24, 9, 3, 10, tzinfo=KST)
    row = _opt10080_row("20261024090000", 100)
    with patch("gui_market_data_host.load_stock_library_snapshot",
               return_value=DisplayOnlyMarketDataHostTest._snapshot(True)), patch.object(
                   MarketDataHost, "direct_timeframe_requirements", return_value={3: 1}):
        result = MarketDataHost.install_display_timeframe_candles(
            host, "005930", 3, [row], as_of=now,
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1")
        assert result["ok"] is True
        host.direct_sor_completed.emit.assert_called_once()
        event = host.direct_sor_completed.emit.call_args.args[0]
        assert event["bar_time"] == "2026-10-24T09:00:00+09:00"
        assert event["timeframe_minutes"] == 3
        host.direct_sor_completed.reset_mock()
        MarketDataHost.install_display_timeframe_candles(
            host, "005930", 3, [row], as_of=now + timedelta(minutes=2),
            market_data_identity="005930_AL", market_source="INTEGRATED",
            display_only=True, connection_epoch=1, login_session_id="SESSION-1")
        host.direct_sor_completed.emit.assert_not_called()


def test_preparation_uses_sealed_actual_facts_and_committed_assignment(tmp_path, monkeypatch):
    from routine_main_facts import build_routine_main_facts_from_projection
    import routine_instance_registry as registry
    import routine_package_contract as contract
    import stock_repository

    stock_dir = tmp_path / "stock"
    stock_dir.mkdir()
    (stock_dir / "config.json").write_text(json.dumps({
        "assigned_routine_instance_id": "RI-1", "calculation_market_source": "SOR"}))
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"bar": {"bar_minutes": 3}}))
    package = tmp_path / "routines" / "지표추종매매"
    package.mkdir(parents=True)
    projection = {
        "stock_configs": {"005930": {"assigned_routine_instance_id": "RI-1"}},
        "orders": [{"id": "observed-order-fact"}], "fills": [],
        "positions": [], "signals": [],
    }
    now = datetime.now(KST)
    facts = build_routine_main_facts_from_projection(
        projection, now=now - timedelta(seconds=1)).to_payload()
    event = {"stock_code": "005930", "timeframe_minutes": 3,
             "observed_at": now.isoformat()}
    observed = []
    module = SimpleNamespace(
        evaluate_signal_selection=lambda *_: {"signal": "NONE"},
        market_bar_projection_request=lambda _rules: {"warmup_bars": 1},
        project_cycle_context=lambda **kwargs: observed.append(kwargs) or
            {"status": "resolved", "holding_qty": 0},
    )
    monkeypatch.setattr(monitor_module, "_ROOT", tmp_path)
    monkeypatch.setattr(stock_repository, "StockRepository", lambda **_:
                        SimpleNamespace(resolve_stock_dir=lambda _code: stock_dir))
    monkeypatch.setattr(registry, "routine_instance_by_id", lambda *_a, **_kw:
                        SimpleNamespace(enabled=True, persisted=True,
                                        rules_path=rules_path, definition_id="D-1"))
    monkeypatch.setattr(registry, "routine_definition_by_id", lambda *_a, **_kw:
                        SimpleNamespace(package_enabled=True, package_dir=package))
    monkeypatch.setattr(contract, "load_routine_module", lambda *_: module)
    prepared = monitor_module._prepare(event, facts)
    assert prepared is not None
    assert prepared["cycle"] == {"status": "resolved", "holding_qty": 0}
    assert observed[0]["order_queue"] == projection["orders"]
    module.project_cycle_context = lambda **_: {"status": "unresolved", "holding_qty": 0}
    assert monitor_module._prepare(event, facts) is None
    (stock_dir / "config.json").write_text(json.dumps({
        "assigned_routine_instance_id": "different", "calculation_market_source": "SOR"}))
    assert monitor_module._prepare(event, facts) is None
