from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_controller import run_execution_runtime_dry_run
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data
from execution_runtime_storage import ExecutionRuntimeStorage


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class ExecutionRuntimeControllerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"
        _write_json(self.executions_path, default_order_executions_data())
        _write_json(self.locks_path, default_order_locks_data())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _storage(self) -> ExecutionRuntimeStorage:
        return ExecutionRuntimeStorage(self.executions_path, self.locks_path)

    def _order(self) -> dict:
        return {
            "id": "ORDER_CONTRACT_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_CONTRACT_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {
                "side": "BUY",
                "hoga": "MARKET",
            },
        }

    def _guard(self) -> dict:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }

    def _confirmations(self) -> dict:
        return {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }

    def _assert_preview_only_boundary(self, result: dict, *, status: str) -> None:
        self.assertEqual(status, result["status"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["queue_commit_called"])
        self.assertFalse(result["runtime_commit_called"])

    def test_ready_input_confirmed_temp_valid_storage_returns_ready(self) -> None:
        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self._assert_preview_only_boundary(result, status="READY")
        self.assertEqual("READY", result["commit_plan"]["status"])

    def test_missing_confirmations_returns_blocked(self) -> None:
        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations={},
        )

        self._assert_preview_only_boundary(result, status="BLOCKED")
        self.assertIn("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_preview_pipeline_blocked_returns_blocked(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "UNKNOWN"

        result = run_execution_runtime_dry_run(
            order,
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self._assert_preview_only_boundary(result, status="BLOCKED")
        self.assertIn("EXECUTION_PREVIEW_BLOCKED", result["issues"])

    def test_storage_preview_commit_plan_blocked_returns_blocked(self) -> None:
        executions = default_order_executions_data()
        executions["executions"].append({"order_id": "ORDER_CONTRACT_1"})
        _write_json(self.executions_path, executions)

        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self._assert_preview_only_boundary(result, status="BLOCKED")
        self.assertIn("DUPLICATE_ORDER_ID", result["issues"])

    def test_malformed_order_guard_storage_return_invalid(self) -> None:
        invalid_order = run_execution_runtime_dry_run("bad", self._guard(), self._storage())
        invalid_guard = run_execution_runtime_dry_run(self._order(), "bad", self._storage())
        invalid_storage = run_execution_runtime_dry_run(self._order(), self._guard(), object())

        self._assert_preview_only_boundary(invalid_order, status="INVALID")
        self._assert_preview_only_boundary(invalid_guard, status="INVALID")
        self._assert_preview_only_boundary(invalid_storage, status="INVALID")

    def test_storage_commit_send_order_queue_commit_and_runtime_commit_are_not_called(self) -> None:
        storage = self._storage()
        with (
            mock.patch.object(storage, "commit") as storage_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = run_execution_runtime_dry_run(
                self._order(),
                self._guard(),
                storage,
                confirmations=self._confirmations(),
            )

        self._assert_preview_only_boundary(result, status="READY")
        storage_commit.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_actual_runtime_files_are_not_created_and_rules_are_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self._assert_preview_only_boundary(result, status="READY")
        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_inputs_are_not_mutated(self) -> None:
        order = self._order()
        guard = self._guard()
        confirmations = self._confirmations()
        before = (deepcopy(order), deepcopy(guard), deepcopy(confirmations))

        result = run_execution_runtime_dry_run(
            order,
            guard,
            self._storage(),
            confirmations=confirmations,
        )
        result["execution_preview"]["pipeline"]["execution_preview"]["status"] = "MUTATED_RESULT_ONLY"
        result["commit_plan"]["commit_plan"]["planned_records"]["execution"]["order_id"] = "MUTATED_RESULT_ONLY"

        self.assertEqual(before[0], order)
        self.assertEqual(before[1], guard)
        self.assertEqual(before[2], confirmations)


if __name__ == "__main__":
    unittest.main()
