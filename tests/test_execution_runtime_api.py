from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_lifecycle import ExecutionLifecycle
from execution_runtime_api import API_TYPE, ExecutionRuntimeAPI
from execution_runtime_coordinator import ExecutionRuntimeCoordinator
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data
from execution_runtime_manager import ExecutionRuntimeManager
from execution_runtime_storage import ExecutionRuntimeStorage
from execution_runtime_supervisor import ExecutionRuntimeSupervisor


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class ExecutionRuntimeAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.executions_path = root / "order_executions.json"
        self.locks_path = root / "order_locks.json"
        _write_json(self.executions_path, default_order_executions_data())
        _write_json(self.locks_path, default_order_locks_data())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _manager(self) -> ExecutionRuntimeManager:
        return ExecutionRuntimeManager(
            lifecycle=ExecutionLifecycle(),
            storage=ExecutionRuntimeStorage(self.executions_path, self.locks_path),
            metadata={"manager": {"name": "unit-test"}},
        )

    def _coordinator(self) -> ExecutionRuntimeCoordinator:
        supervisor = ExecutionRuntimeSupervisor(
            runtime_manager=self._manager(),
            metadata={"supervisor": {"name": "unit-test"}},
        )
        return ExecutionRuntimeCoordinator(
            supervisor=supervisor,
            metadata={"coordinator": {"name": "unit-test"}},
        )

    def _api(self) -> ExecutionRuntimeAPI:
        return ExecutionRuntimeAPI(
            coordinator=self._coordinator(),
            metadata={"api": {"name": "unit-test"}},
        )

    def _order(self) -> dict:
        return {
            "id": "ORDER_API_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_API_1",
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

    def test_submit_dry_run_ready(self) -> None:
        api = self._api()

        result = api.submit_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual(API_TYPE, result["api_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("READY", result["coordinator_result"]["status"])
        self.assertEqual("READY", api.list_sessions()[0].status)

    def test_submit_dry_run_blocked(self) -> None:
        api = self._api()

        result = api.submit_dry_run(self._order(), self._guard(), confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("BLOCKED", api.list_sessions()[0].status)

    def test_submit_dry_run_invalid(self) -> None:
        api = self._api()
        with mock.patch.object(
            api.coordinator,
            "submit",
            return_value={
                "status": "INVALID",
                "issues": ["FORCED_INVALID"],
                "warnings": [],
            },
        ):
            result = api.submit_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("FORCED_INVALID", result["issues"])

    def test_get_last_result(self) -> None:
        api = self._api()
        result = api.submit_dry_run(self._order(), self._guard(), self._confirmations())
        result["status"] = "MUTATED_RESULT"

        last = api.get_last_result()
        self.assertEqual("READY", last["status"])
        self.assertEqual("READY", last["coordinator_result"]["status"])

    def test_clear(self) -> None:
        api = self._api()
        api.submit_dry_run(self._order(), self._guard(), self._confirmations())

        api.clear()

        self.assertIsNone(api.get_last_result())
        self.assertIsNone(api.coordinator.last_result())

    def test_snapshot_restore(self) -> None:
        api = self._api()
        api.submit_dry_run(self._order(), self._guard(), self._confirmations())
        snapshot = api.snapshot()

        restored = self._api()
        restored.restore(snapshot)
        snapshot["context"]["state"]["sessions"][0]["status"] = "MUTATED"

        self.assertEqual("READY", restored.list_sessions()[0].status)
        self.assertEqual(
            ["SESSION_CREATED", "SESSION_READY"],
            [event["event_type"] for event in restored.list_events()],
        )

    def test_list_sessions(self) -> None:
        api = self._api()
        api.submit_dry_run(self._order(), self._guard(), self._confirmations())

        sessions = api.list_sessions()
        sessions[0].metadata["source"] = "mutated"

        self.assertEqual(1, len(sessions))
        self.assertNotEqual("mutated", api.list_sessions()[0].metadata.get("source"))

    def test_list_events(self) -> None:
        api = self._api()
        api.submit_dry_run(self._order(), self._guard(), self._confirmations())
        session_id = api.list_sessions()[0].session_id

        events = api.list_events(session_id)
        events[0]["payload"]["status"] = "MUTATED"

        self.assertEqual(["SESSION_CREATED", "SESSION_READY"], [event["event_type"] for event in api.list_events(session_id)])
        self.assertEqual("CREATED", api.list_events(session_id)[0]["payload"]["status"])

    def test_summary(self) -> None:
        api = self._api()

        empty = api.summary()
        api.submit_dry_run(self._order(), self._guard(), self._confirmations())
        result = api.summary()

        self.assertFalse(empty["has_last_result"])
        self.assertTrue(result["has_last_result"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("unit-test", result["metadata"]["api"]["name"])

    def test_deepcopy(self) -> None:
        metadata = {"api": {"name": "source"}}
        api = ExecutionRuntimeAPI(coordinator=self._coordinator(), metadata=metadata)
        metadata["api"]["name"] = "mutated"

        result = api.submit_dry_run(self._order(), self._guard(), self._confirmations())
        last = api.get_last_result()
        last["coordinator_result"]["status"] = "MUTATED_LAST"

        self.assertEqual("source", api.metadata["api"]["name"])
        self.assertEqual("READY", api.get_last_result()["coordinator_result"]["status"])
        self.assertEqual("READY", result["coordinator_result"]["status"])

    def test_constructor_validation(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionRuntimeAPI(coordinator="bad")
        with self.assertRaises(ValueError):
            ExecutionRuntimeAPI(coordinator=self._coordinator(), metadata="bad")

    def test_commit_and_send_paths_not_called(self) -> None:
        api = self._api()
        with (
            mock.patch.object(api.coordinator.supervisor.runtime_manager.storage, "commit") as storage_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = api.submit_dry_run(self._order(), self._guard(), self._confirmations())

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()

    def test_gui_preview_controller_not_connected(self) -> None:
        import execution_runtime_api

        module_text = execution_runtime_api.__loader__.get_source(execution_runtime_api.__name__)

        self.assertNotIn("execution_readiness_preview_controller", module_text)
        self.assertNotIn("gui_auto_trade_setting_window", module_text)
        self.assertNotIn("commit_execution_runtime", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        api = self._api()
        api.submit_dry_run(self._order(), self._guard(), self._confirmations())
        snapshot = api.snapshot()
        api.restore(snapshot)
        api.summary()
        api.clear()

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
