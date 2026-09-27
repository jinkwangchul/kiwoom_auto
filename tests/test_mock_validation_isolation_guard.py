# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import MockValidationError
from mock_validation_isolation_guard import (
    assert_mock_dependency_isolation,
    audit_mock_dependency_graph,
    mock_foundation_module_paths,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MockValidationIsolationGuardTest(unittest.TestCase):
    def test_mock_foundation_has_no_production_mutation_dependency(self) -> None:
        paths = mock_foundation_module_paths(PROJECT_ROOT)
        self.assertGreaterEqual(len(paths), 5)
        result = assert_mock_dependency_isolation(paths)
        self.assertTrue(result["ok"])
        self.assertEqual([], result["violations"])

    def test_forbidden_import_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mock_validation_bad.py"
            path.write_text(
                "import order_queue\n"
                "from execution_runtime_commit_service import commit_execution_runtime\n",
                encoding="utf-8",
            )
            result = audit_mock_dependency_graph([path])
            self.assertFalse(result["ok"])
            self.assertEqual(
                {"order_queue", "execution_runtime_commit_service"},
                {item["name"] for item in result["violations"]},
            )
            with self.assertRaisesRegex(MockValidationError, "MOCK_PRODUCTION_DEPENDENCY_FORBIDDEN"):
                assert_mock_dependency_isolation([path])

    def test_forbidden_send_call_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mock_validation_bad_call.py"
            path.write_text("def bad(api):\n    return api.SendOrder()\n", encoding="utf-8")
            result = audit_mock_dependency_graph([path])
            self.assertFalse(result["ok"])
            self.assertEqual("SendOrder", result["violations"][0]["name"])

    def test_production_market_data_host_import_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mock_validation_bad_market_data.py"
            path.write_text("from gui_market_data_host import MarketDataHost\n", encoding="utf-8")
            result = audit_mock_dependency_graph([path])
            self.assertFalse(result["ok"])
            self.assertEqual("gui_market_data_host", result["violations"][0]["name"])

    def test_dynamic_forbidden_import_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mock_validation_bad_dynamic.py"
            path.write_text(
                "import importlib\n"
                "value = importlib.import_module('kiwoom_send_order_executor')\n",
                encoding="utf-8",
            )
            result = audit_mock_dependency_graph([path])
            self.assertFalse(result["ok"])
            self.assertEqual("DYNAMIC_IMPORT", result["violations"][0]["kind"])

    def test_dynamic_routine_source_is_audited(self) -> None:
        result = audit_mock_dependency_graph(
            [PROJECT_ROOT / "mock_validation_indicator_follow_adapter.py"]
        )
        checked_names = {Path(value).name for value in result["files_checked"]}
        self.assertIn("routine_buy_execution.py", checked_names)
        self.assertIn("routine_sell_execution.py", checked_names)
        self.assertIn("routine_lifecycle.py", checked_names)
        self.assertTrue(result["ok"], result["violations"])


if __name__ == "__main__":
    unittest.main()
