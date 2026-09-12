# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import patch

from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_session import (
    REASON_OPERATION_ACTIVE,
    REASON_OPERATION_ACTIVE_READER_ERROR,
    REASON_OPERATION_ACTIVE_READER_UNAVAILABLE,
    ValidationSession,
)
from routines.지표추종매매.routine_validation_trace import (
    ValidationTraceObserver,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_MODULES = (
    PROJECT_ROOT
    / "routines"
    / "지표추종매매"
    / "routine_validation_contract.py",
    PROJECT_ROOT
    / "routines"
    / "지표추종매매"
    / "routine_validation_trace.py",
    PROJECT_ROOT
    / "routines"
    / "지표추종매매"
    / "routine_validation_session.py",
)


def _request(reader=None) -> ValidationSession:
    stock = ValidationStockRef("005930", "삼성전자")
    snapshot = ValidationSettingsSnapshot({"buy": {"enabled": True}})
    request = ValidationRequest(stock, snapshot, 3)
    return ValidationSession(request, operation_active_reader=reader)


class ValidationContractTest(unittest.TestCase):
    def test_stock_ref_is_frozen(self):
        stock = ValidationStockRef("005930", "삼성전자")
        with self.assertRaises(FrozenInstanceError):
            stock.code = "000000"

    def test_settings_snapshot_is_canonical_and_detached(self):
        first_source = {"sell": {"enabled": False}, "buy": {"levels": [1, 2]}}
        second_source = {"buy": {"levels": [1, 2]}, "sell": {"enabled": False}}
        first = ValidationSettingsSnapshot(first_source)
        second = ValidationSettingsSnapshot(second_source)

        self.assertEqual(first.canonical_json, second.canonical_json)
        self.assertEqual(first.rules_hash, second.rules_hash)
        first_source["buy"]["levels"].append(3)
        self.assertEqual([1, 2], first.to_dict()["buy"]["levels"])

        returned = first.to_dict()
        returned["buy"]["levels"].append(4)
        self.assertEqual([1, 2], first.to_dict()["buy"]["levels"])


class ValidationTraceObserverTest(unittest.TestCase):
    def test_trace_is_ordered_and_detached(self):
        observer = ValidationTraceObserver()
        condition = {"name": "first", "values": [1]}
        group = {"name": "group", "matched": True}
        buy = {"matched": True}
        sell = {"matched": False}

        observer.observe_condition(condition)
        observer.observe_condition({"name": "second"})
        observer.observe_group(group)
        observer.observe_aggregation("BUY", buy)
        observer.observe_aggregation("SELL", sell)
        condition["values"].append(2)
        group["matched"] = False
        buy["matched"] = False

        snapshot = observer.snapshot()
        self.assertEqual(
            ["first", "second"],
            [item["name"] for item in snapshot["conditions"]],
        )
        self.assertEqual(True, snapshot["groups"][0]["matched"])
        self.assertEqual(
            ["BUY", "SELL"],
            [item["side"] for item in snapshot["aggregations"]],
        )
        self.assertEqual(True, snapshot["aggregations"][0]["payload"]["matched"])

        snapshot["conditions"][0]["values"].append(99)
        snapshot["aggregations"][0]["payload"]["matched"] = False
        fresh = observer.snapshot()
        self.assertEqual([1], fresh["conditions"][0]["values"])
        self.assertEqual(True, fresh["aggregations"][0]["payload"]["matched"])

    def test_clear_and_malformed_payloads_never_escape(self):
        class BrokenCopy:
            def __deepcopy__(self, _memo):
                raise ValueError("malformed")

        observer = ValidationTraceObserver()
        observer.observe_condition(None)
        observer.observe_group(["not", "a", "mapping"])
        observer.observe_aggregation(None, BrokenCopy())
        observer.observe_condition(BrokenCopy())
        self.assertEqual([None], observer.snapshot()["conditions"])
        self.assertEqual([["not", "a", "mapping"]], observer.snapshot()["groups"])
        self.assertEqual([], observer.snapshot()["aggregations"])
        observer.clear()
        self.assertEqual(
            {"conditions": [], "groups": [], "aggregations": []},
            observer.snapshot(),
        )


class ValidationAvailabilityTest(unittest.TestCase):
    def test_missing_reader_is_fail_closed(self):
        readiness = _request().readiness()
        self.assertFalse(readiness.allowed)
        self.assertEqual(REASON_OPERATION_ACTIVE_READER_UNAVAILABLE, readiness.reason)

    def test_reader_error_is_fail_closed(self):
        def broken_reader():
            raise RuntimeError("unavailable")

        readiness = _request(broken_reader).readiness()
        self.assertFalse(readiness.allowed)
        self.assertEqual(REASON_OPERATION_ACTIVE_READER_ERROR, readiness.reason)

    def test_active_operation_is_blocked(self):
        readiness = _request(lambda: True).readiness()
        self.assertFalse(readiness.allowed)
        self.assertEqual(REASON_OPERATION_ACTIVE, readiness.reason)

    def test_inactive_operation_is_allowed(self):
        readiness = _request(lambda: False).readiness()
        self.assertTrue(readiness.allowed)
        self.assertIsNone(readiness.reason)


class ValidationIndependenceTest(unittest.TestCase):
    def test_imports_stay_inside_stdlib_and_validation_foundation(self):
        allowed_roots = {
            "__future__",
            "collections",
            "copy",
            "dataclasses",
            "hashlib",
            "json",
            "typing",
        }
        forbidden_fragments = {
            "gui_market_data_host",
            "gui_auto_trade_run_control",
            "gui_auto_trade_setting_window",
            "routine_signal_queue",
            "routine_signal_consumer",
            "order_queue",
            "execution_queue_writer",
            "SendOrder",
            "Chejan",
            "kiwoom_api",
            "mock_validation_",
            "gui_search_stock_register_dialog",
            "operation_policy_gate",
        }
        for module_path in VALIDATION_MODULES:
            source = module_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            with self.subTest(module=module_path.name):
                for fragment in forbidden_fragments:
                    self.assertNotIn(fragment, source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertIn(alias.name.split(".", 1)[0], allowed_roots)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        root = (node.module or "").split(".", 1)[0]
                        self.assertIn(root, allowed_roots)

    def test_foundation_api_does_not_call_filesystem_writers(self):
        session = _request(lambda: False)
        observer = session.trace_observer
        with patch("builtins.open", side_effect=AssertionError("write")), \
             patch.object(Path, "open", side_effect=AssertionError("write")), \
             patch.object(Path, "write_text", side_effect=AssertionError("write")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("write")), \
             patch.object(Path, "touch", side_effect=AssertionError("write")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("write")):
            self.assertTrue(session.readiness().allowed)
            observer.observe_condition({"matched": True})
            self.assertEqual(1, len(observer.snapshot()["conditions"]))
            self.assertEqual(
                {"buy": {"enabled": True}},
                session.request.settings_snapshot.to_dict(),
            )


if __name__ == "__main__":
    unittest.main()
