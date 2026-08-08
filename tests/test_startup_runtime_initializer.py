from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from operator_reconciliation_service import assess_startup_recovery
from startup_runtime_initializer import (
    STATUS_BLOCKED_INVALID,
    STATUS_BLOCKED_PARTIAL,
    STATUS_INITIALIZED,
    initialize_pristine_startup_runtime,
    startup_runtime_paths,
)


class StartupRuntimeInitializerTest(unittest.TestCase):
    def _assessment_arguments(self, runtime_dir: Path) -> dict[str, Path]:
        paths = startup_runtime_paths(runtime_dir)
        return {
            "queue_path": paths["order_queue.json"],
            "fills_path": paths["fills.json"],
            "positions_path": paths["positions.json"],
            "broker_holdings_path": paths["broker_holdings.json"],
            "order_executions_path": paths["order_executions.json"],
            "order_locks_path": paths["order_locks.json"],
            "routine_signals_path": paths["routine_signals.json"],
        }

    def test_pristine_runtime_is_initialized_and_reassesses_resume_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"

            initialized = initialize_pristine_startup_runtime(root)
            assessment = assess_startup_recovery(
                **self._assessment_arguments(root),
            )

            self.assertEqual(STATUS_INITIALIZED, initialized["status"])
            self.assertTrue(initialized["initialized"])
            self.assertTrue(initialized["read_back_verified"])
            self.assertEqual(7, len(initialized["created_files"]))
            self.assertEqual("RESUME_READY", assessment["status"])
            self.assertTrue(assessment["operator_approval_allowed"])

    def test_partial_runtime_is_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            root.mkdir()
            queue = root / "order_queue.json"
            queue.write_text(
                json.dumps({"version": 1, "revision": 0, "orders": []}),
                encoding="utf-8",
            )
            before = queue.read_bytes()

            result = initialize_pristine_startup_runtime(root)

            self.assertEqual(STATUS_BLOCKED_PARTIAL, result["status"])
            self.assertFalse(result["runtime_write"])
            self.assertEqual(before, queue.read_bytes())
            self.assertEqual(
                ["order_queue.json"],
                sorted(path.name for path in root.iterdir()),
            )

    def test_damaged_complete_runtime_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            initialized = initialize_pristine_startup_runtime(root)
            self.assertEqual(STATUS_INITIALIZED, initialized["status"])
            damaged = root / "positions.json"
            damaged.write_text("{broken", encoding="utf-8")
            before = {
                path: path.read_bytes()
                for path in startup_runtime_paths(root).values()
            }

            result = initialize_pristine_startup_runtime(root)

            self.assertEqual(STATUS_BLOCKED_INVALID, result["status"])
            self.assertFalse(result["runtime_write"])
            self.assertIn("positions.json", " ".join(result["issues"]))
            self.assertEqual(
                before,
                {
                    path: path.read_bytes()
                    for path in startup_runtime_paths(root).values()
                },
            )

    def test_active_stock_state_is_preserved_for_recovery_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime_dir = root / "runtime"
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )

            initialized = initialize_pristine_startup_runtime(runtime_dir)
            assessment = assess_startup_recovery(
                **self._assessment_arguments(runtime_dir),
                stock_state_paths=[state_path],
            )

            self.assertEqual(STATUS_INITIALIZED, initialized["status"])
            self.assertEqual("REVIEW_REQUIRED", assessment["status"])
            self.assertTrue(assessment["operator_approval_allowed"])
            self.assertEqual(
                {"status": "RUNNING", "trade_enabled": True},
                json.loads(state_path.read_text(encoding="utf-8")),
            )


if __name__ == "__main__":
    unittest.main()
