"""Focused standard-library checks for the disconnected direct SOR API."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import sor_no_order_signal_probe as probe


KST = timezone(timedelta(hours=9))


class DirectSorSignalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        stock_dir = root / "stocks" / "005930_test"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(json.dumps({
            "assigned_routine_instance_id": "instance-1",
            "calculation_market_source": "SOR",
        }), encoding="utf-8")
        rules_path = root / "rules.json"
        self.rules_path = rules_path
        rules_path.write_text(json.dumps({"bar": {"bar_minutes": 3}}), encoding="utf-8")
        package = root / "routines" / "지표추종매매"
        package.mkdir(parents=True)
        self.now = datetime.now(KST) - timedelta(seconds=1)
        self.bar_time = (self.now - timedelta(minutes=6)).isoformat()
        self.bar = {"bar_time": self.bar_time, "timeframe_minutes": 3,
                    "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
                    "display_only": True, "is_complete": True,
                    "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}

        import routine_instance_registry as registry
        import routine_package_contract as contract
        import stock_repository
        import sor_m3_production_capture as capture

        patches = [
            patch.object(probe, "_ROOT", root),
            patch.object(stock_repository, "StockRepository", lambda **_kw:
                         SimpleNamespace(resolve_stock_dir=lambda _code: stock_dir)),
            patch.object(registry, "routine_instance_by_id", lambda *_a, **_kw:
                         SimpleNamespace(enabled=True, persisted=True,
                                         rules_path=rules_path, definition_id="definition-1")),
            patch.object(registry, "routine_definition_by_id", lambda *_a, **_kw:
                         SimpleNamespace(package_enabled=True, package_dir=package)),
        ]
        self.saved = []
        patches.append(patch.object(capture, "enqueue_preview",
                                    lambda record: self.saved.append(record) or True))

        test_case = self

        class MarketDataHost:
            def project_routine_candles(self, **kwargs):
                test_case.assertEqual(kwargs["consumer_scope"], "SOR_CALCULATION_ONLY")
                test_case.assertEqual(kwargs["projection_request"]["projection"],
                                      "COMPLETED_TIMEFRAME")
                source_hash = "a" * 64
                return {
                    "available": True, "stock_code": "005930", "candles": [test_case.bar],
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
                        "source_content_sha256": source_hash,
                        "connection_epoch": 7,
                        "session_sha256": hashlib.sha256(b"actual-session").hexdigest(),
                        "source_as_of": test_case.now.isoformat(),
                    },
                }

            def _current_display_session_identity(self):
                return 7, "actual-session"

        MarketDataHost.__module__ = "gui_market_data_host"
        self.host = MarketDataHost()
        patches.append(patch.dict(sys.modules, {
            "gui_market_data_host": SimpleNamespace(MarketDataHost=MarketDataHost)}))
        self.selector = SimpleNamespace(
            market_bar_projection_request=lambda _rules: {"projection": "FORMING_BASE_BAR"},
            evaluate_signal_selection=lambda *_a: {"signal": "NONE"},
            evaluate=lambda *_a: self.fail("generic evaluate called"),
        )
        patches.append(patch.object(contract, "load_routine_module",
                                    lambda *_a: self.selector))
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        self.args = {"market_host": self.host, "stock_code": "005930",
                     "routine_instance_id": "instance-1",
                     "observed_cycle": {"status": "resolved", "holding_qty": 0},
                     "observed_at": self.now}
        self.order_imports_before = {
            name for name in ("routine_signal_probe", "routine_signal_queue")
            if name in sys.modules}

    def test_buy_sell_none_source_proof_and_disconnection(self):
        for side in ("BUY", "SELL", "NONE"):
            with self.subTest(side=side):
                self.saved.clear()
                self.selector.evaluate_signal_selection = lambda *_a: {
                    "signal": side if side != "NONE" else None,
                    "signal_activation_bar_time": self.bar_time if side != "NONE" else None,
                }
                result = probe.capture_direct_sor_signal(**self.args)
                self.assertEqual(result["status"], "OBSERVED")
                self.assertEqual(result["sor_preview_signal"], side)
                self.assertEqual(result["sor_preview_evidence"]["timeframe"], "M3")
                self.assertEqual(result["sor_preview_evidence"]["source_session_sha256"],
                                 hashlib.sha256(b"actual-session").hexdigest())
                from sor_m3_production_capture import _safe_record
                record = _safe_record(result)
                self.assertIsNotNone(record)
                self.assertEqual(record["sor_preview_evidence"]["evaluation_time"],
                                 datetime.fromisoformat(self.bar_time).strftime("%Y%m%d%H%M%S"))
                self.assertEqual(record["sor_preview_activation_time"],
                                 self.bar_time if side != "NONE" else None)
                self.assertEqual(record["sor_preview_evaluated_at"],
                                 result["sor_preview_evaluated_at"])
                self.assertEqual(record["sor_preview_cycle_hash"],
                                 result["sor_preview_cycle_hash"])
                self.assertEqual(record["sor_preview_routine_instance_sha256"],
                                 hashlib.sha256(b"instance-1").hexdigest())
                self.assertEqual(self.saved, [result])
                self.assert_no_order(result)

    def test_missing_source_blocks(self):
        self.host.project_routine_candles = lambda **_kw: {"available": False}
        self.assert_blocked()

    def test_completed_m1_receipt_is_recorded(self):
        self.rules_path.write_text(json.dumps({"bar": {"bar_minutes": 1}}),
                                   encoding="utf-8")
        self.bar["timeframe_minutes"] = 1
        original = self.host.project_routine_candles

        def m1_source(**kwargs):
            source = original(**kwargs)
            source["timeframe_minutes"] = 1
            source["calculation_source"] = "BROKER_AL_CONFIRMED_M1"
            source["direct_history_source"] = ""
            source["direct_source_receipt"]["timeframe_key"] = "M1"
            return source

        self.host.project_routine_candles = m1_source
        self.selector.evaluate_signal_selection = lambda *_a: {
            "signal": "BUY", "signal_activation_bar_time": self.bar_time}
        result = probe.capture_direct_sor_signal(**self.args)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["sor_preview_evidence"]["timeframe"], "M1")
        self.assertEqual(result["sor_preview_evidence"]["direct_m1_source_proof"],
                         "BROKER_SESSION_BOUND_COMPLETED_M1")
        self.assert_no_order(result)

    def test_malformed_receipt_blocks(self):
        original = self.host.project_routine_candles
        self.host.project_routine_candles = lambda **kw: {
            **original(**kw), "direct_source_receipt": None}
        self.assert_blocked()

    def test_malformed_candle_blocks_before_selector(self):
        self.bar["close"] = float("nan")
        self.selector.evaluate_signal_selection = lambda *_a: self.fail("selector called")
        self.assert_blocked()

    def test_missing_cycle_price_blocks_before_host(self):
        self.args["observed_cycle"] = {"status": "resolved", "holding_qty": 2}
        self.host.project_routine_candles = lambda **_kw: self.fail("host called")
        self.assert_blocked()

    def test_source_exception_blocks(self):
        self.host.project_routine_candles = lambda **_kw: 1 / 0
        self.assert_blocked()

    def test_unknown_cycle_blocks(self):
        self.args["observed_cycle"] = {"status": "UNKNOWN", "holding_qty": 0}
        self.assert_blocked()

    def test_observed_unresolved_cycle_stays_unresolved(self):
        self.args["observed_cycle"] = {
            "status": "unresolved", "holding_qty": 0,
            "unresolved_reason": "PENDING_OPERATION_FACTS"}
        result = probe.capture_direct_sor_signal(**self.args)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["sor_preview_cycle_status"], "unresolved")
        self.assertEqual(result["sor_preview_signal"], "NONE")
        self.assert_no_order(result)

    def test_changed_session_blocks(self):
        with patch.object(type(self.host), "_current_display_session_identity",
                          lambda _host: (8, "other-session")):
            self.assert_blocked()

    def test_selector_exception_blocks(self):
        self.selector.evaluate_signal_selection = lambda *_a: 1 / 0
        self.assert_blocked()

    def test_stale_observation_blocks_before_host(self):
        self.args["observed_at"] -= timedelta(hours=1)
        self.host.project_routine_candles = lambda **_kw: self.fail("host called")
        result = probe.capture_direct_sor_signal(**self.args)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self.saved, [])
        self.assert_no_order(result)

    def assert_no_order(self, result):
        for key in ("execution_enabled", "queue_connected", "send_order_connected"):
            self.assertIs(result[key], False)
        self.assertEqual({
            name for name in ("routine_signal_probe", "routine_signal_queue")
            if name in sys.modules}, self.order_imports_before)

    def assert_blocked(self):
        result = probe.capture_direct_sor_signal(**self.args)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(self.saved, [])
        self.assert_no_order(result)


if __name__ == "__main__":
    unittest.main()
