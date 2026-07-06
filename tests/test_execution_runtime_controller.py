from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_controller import (
    CONTROLLER_TYPE,
    run_execution_runtime_dry_run,
)
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


class ExecutionRuntimeControllerTest(unittest.TestCase):
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
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
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

    def test_ready_dry_run(self) -> None:
        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual(CONTROLLER_TYPE, result["controller_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["queue_commit_called"])
        self.assertFalse(result["runtime_commit_called"])
        self.assertEqual("READY", result["catalog_orchestrator"]["status"])
        self.assertEqual("READY", result["commit_plan"]["status"])

    def test_preview_blocked_returns_blocked(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "UNKNOWN"

        result = run_execution_runtime_dry_run(
            order,
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("EXECUTION_PREVIEW_BLOCKED", result["issues"])
        self.assertEqual("execution_preview", result["execution_preview"]["blocked_stage"])

    def test_final_guard_blocked_returns_blocked(self) -> None:
        guard = self._guard()
        guard["operator_confirmed"] = False

        result = run_execution_runtime_dry_run(
            self._order(),
            guard,
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_GUARD_BLOCKED", result["issues"])
        self.assertEqual("final_guard", result["execution_preview"]["blocked_stage"])

    def test_catalog_invalid_returns_invalid(self) -> None:
        invalid_catalog = {
            "status": "INVALID",
            "preview_only": True,
            "runtime_write": False,
            "issues": ["FORCED_CATALOG_INVALID"],
            "warnings": [],
        }

        with mock.patch(
            "execution_runtime_controller.run_execution_runtime_catalog_orchestrator_preview",
            return_value=invalid_catalog,
        ):
            result = run_execution_runtime_dry_run(
                self._order(),
                self._guard(),
                self._storage(),
                confirmations=self._confirmations(),
            )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("FORCED_CATALOG_INVALID", result["issues"])

    def test_storage_commit_plan_blocked_returns_blocked(self) -> None:
        data = default_order_executions_data()
        data["executions"].append({"execution_id": mock.ANY})
        _write_json(
            self.executions_path,
            {
                "version": 1,
                "updated_at": None,
                "executions": [{"order_id": "ORDER_1"}],
            },
        )

        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("DUPLICATE_ORDER_ID", result["issues"])

    def test_confirmations_missing_returns_blocked(self) -> None:
        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations={},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_plan"]["commit_ready"])
        self.assertIn("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_confirmations_with_temp_valid_files_returns_ready(self) -> None:
        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["commit_plan"]["commit_ready"])

    def test_send_order_queue_commit_and_storage_commit_are_not_called(self) -> None:
        storage = self._storage()
        with (
            mock.patch.object(storage, "commit") as storage_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
        ):
            result = run_execution_runtime_dry_run(
                self._order(),
                self._guard(),
                storage,
                confirmations=self._confirmations(),
            )

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()

    def test_runtime_files_are_not_created(self) -> None:
        result = run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual("READY", result["status"])
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
        result["execution_preview"]["pipeline"]["execution_preview"]["order_id"] = "MUTATED_RESULT_ONLY"

        self.assertEqual(before[0], order)
        self.assertEqual(before[1], guard)
        self.assertEqual(before[2], confirmations)

    def test_malformed_input_returns_invalid(self) -> None:
        self.assertEqual(
            "INVALID",
            run_execution_runtime_dry_run("bad", self._guard(), self._storage())["status"],
        )
        self.assertEqual(
            "INVALID",
            run_execution_runtime_dry_run(self._order(), "bad", self._storage())["status"],
        )
        self.assertEqual(
            "INVALID",
            run_execution_runtime_dry_run(self._order(), self._guard(), object())["status"],
        )

    def test_no_mkdir_or_file_write_calls(self) -> None:
        storage = self._storage()
        with (
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = run_execution_runtime_dry_run(
                self._order(),
                self._guard(),
                storage,
                confirmations=self._confirmations(),
            )

        self.assertEqual("READY", result["status"])
        mkdir.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        run_execution_runtime_dry_run(
            self._order(),
            self._guard(),
            self._storage(),
            confirmations=self._confirmations(),
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
