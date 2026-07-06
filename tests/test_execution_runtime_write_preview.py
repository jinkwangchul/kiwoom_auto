from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_catalog_orchestrator import (
    run_execution_runtime_catalog_orchestrator_preview,
)
from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)
from execution_runtime_write_preview import (
    WRITE_PREVIEW_TYPE,
    build_execution_runtime_write_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeWritePreviewTest(unittest.TestCase):
    def _catalog(self, *, status: str = "READY", missing_field: str | None = None) -> dict:
        catalog = {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "catalog_type": "EXECUTION_RUNTIME_CATALOG_PREVIEW",
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "runtime_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
            "checks": {},
            "warnings": ["Preview mode"],
            "issues": [] if status == "READY" else [f"CATALOG_{status}"],
        }
        if missing_field is not None:
            catalog[missing_field] = ""
        return catalog

    def _orchestrator_result(self) -> dict:
        return {
            "status": "READY",
            "preview_only": True,
            "runtime_write": False,
            "orchestrator_type": "EXECUTION_RUNTIME_CATALOG_ORCHESTRATOR_PREVIEW",
            "catalog_preview": self._catalog(),
            "validation": {"valid": True},
            "issues": [],
            "warnings": ["Preview mode"],
        }

    def _execution_request_preview(self) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_REQUEST_PREVIEW",
            "execution_request": {
                "execution_id": "EXEC_1",
                "order_id": "ORDER_1",
                "request_hash": "HASH_1",
                "lock_id": "LOCK_1",
            },
        }

    def _lock_preview(self) -> dict:
        return {
            "ok": True,
            "stage": "ORDER_LOCK_PREVIEW",
            "lock_id": "LOCK_1",
            "preview_only": True,
        }

    def _request_hash_preview(self) -> dict:
        return {
            "ok": True,
            "stage": "REQUEST_HASH_PREVIEW",
            "request_hash": "HASH_1",
            "preview_only": True,
        }

    def _queue_write_preview(self) -> dict:
        return {
            "stage": "EXECUTION_QUEUE_WRITE_PREVIEW",
            "write_preview": True,
            "preview_only": True,
            "no_write": True,
            "runtime_write": False,
        }

    def _order_candidate(self) -> dict:
        return {
            "id": "ORDER_1",
            "candidate_state": "REAL_READY",
            "signal": "BUY",
            "price": 50000,
            "qty": 1,
            "order_type": "BUY",
            "hoga": "00",
        }

    def _existing_executions(self) -> dict:
        return default_order_executions_data()

    def _existing_locks(self) -> dict:
        return default_order_locks_data()

    def _preview(self, catalog: object | None = None, *, executions: dict | None = None, locks: dict | None = None) -> dict:
        return build_execution_runtime_write_preview(
            catalog if catalog is not None else self._catalog(),
            existing_order_executions_data=self._existing_executions() if executions is None else executions,
            existing_order_locks_data=self._existing_locks() if locks is None else locks,
        )

    def test_ready_write_preview_from_catalog_preview(self) -> None:
        result = self._preview()

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(result["write_preview_type"], WRITE_PREVIEW_TYPE)
        self.assertEqual("EXEC_1", result["execution_record_preview"]["execution_id"])
        self.assertEqual("LOCK_1", result["lock_record_preview"]["lock_id"])
        self.assertEqual("ORDER_1", result["duplicate_checks"]["order_id"])
        self.assertEqual(
            "runtime/order_executions.json",
            result["would_write_targets"]["order_executions"],
        )
        self.assertEqual([], result["issues"])

    def test_ready_write_preview_from_catalog_orchestrator_result(self) -> None:
        result = build_execution_runtime_write_preview(
            catalog_orchestrator_result=self._orchestrator_result(),
            existing_order_executions_data=self._existing_executions(),
            existing_order_locks_data=self._existing_locks(),
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual("EXEC_1", result["execution_record_preview"]["execution_id"])

    def test_ready_write_preview_from_real_orchestrator_result(self) -> None:
        orchestrator_result = run_execution_runtime_catalog_orchestrator_preview(
            execution_request_preview=self._execution_request_preview(),
            lock_preview=self._lock_preview(),
            request_hash_preview=self._request_hash_preview(),
            queue_write_preview_result=self._queue_write_preview(),
            order_candidate=self._order_candidate(),
        )

        result = build_execution_runtime_write_preview(
            catalog_orchestrator_result=orchestrator_result,
            existing_order_executions_data=self._existing_executions(),
            existing_order_locks_data=self._existing_locks(),
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual("EXEC_1", result["execution_record_preview"]["execution_id"])

    def test_blocked_duplicate_execution_id(self) -> None:
        executions = self._existing_executions()
        executions["executions"].append({"execution_id": "EXEC_1"})

        result = self._preview(executions=executions)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_EXECUTION_ID", result["issues"])
        self.assertIsNone(result["execution_record_preview"])

    def test_blocked_duplicate_request_hash_in_executions(self) -> None:
        executions = self._existing_executions()
        executions["executions"].append({"request_hash": "HASH_1"})

        result = self._preview(executions=executions)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_REQUEST_HASH", result["issues"])

    def test_blocked_duplicate_order_id_in_executions(self) -> None:
        executions = self._existing_executions()
        executions["executions"].append({"order_id": "ORDER_1"})

        result = self._preview(executions=executions)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_ORDER_ID", result["issues"])

    def test_blocked_duplicate_lock_id(self) -> None:
        locks = self._existing_locks()
        locks["locks"].append({"lock_id": "LOCK_1"})

        result = self._preview(locks=locks)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_LOCK_ID", result["issues"])

    def test_blocked_duplicate_request_hash_in_locks(self) -> None:
        locks = self._existing_locks()
        locks["locks"].append({"request_hash": "HASH_1"})

        result = self._preview(locks=locks)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_REQUEST_HASH", result["issues"])

    def test_blocked_duplicate_order_id_in_locks(self) -> None:
        locks = self._existing_locks()
        locks["locks"].append({"order_id": "ORDER_1"})

        result = self._preview(locks=locks)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DUPLICATE_ORDER_ID", result["issues"])

    def test_blocked_catalog_not_ready(self) -> None:
        result = self._preview(self._catalog(status="BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("CATALOG_BLOCKED", result["issues"])

    def test_invalid_catalog_invalid(self) -> None:
        result = self._preview(self._catalog(status="INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("CATALOG_INVALID", result["issues"])

    def test_invalid_missing_fields(self) -> None:
        cases = [
            ("execution_id", "MISSING_EXECUTION_ID"),
            ("order_id", "MISSING_ORDER_ID"),
            ("request_hash", "MISSING_REQUEST_HASH"),
            ("lock_id", "MISSING_LOCK_ID"),
        ]
        for field, issue in cases:
            with self.subTest(field=field):
                result = self._preview(self._catalog(missing_field=field))
                self.assertEqual(result["status"], "INVALID")
                self.assertIn(issue, result["issues"])

    def test_invalid_malformed_catalog_input(self) -> None:
        result = self._preview("bad-catalog")

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MALFORMED_CATALOG_INPUT", result["issues"])

    def test_invalid_malformed_existing_data(self) -> None:
        result = build_execution_runtime_write_preview(
            self._catalog(),
            existing_order_executions_data="bad-executions",
            existing_order_locks_data=self._existing_locks(),
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MALFORMED_EXISTING_DATA", result["issues"])

    def test_invalid_malformed_existing_list_fields(self) -> None:
        result = build_execution_runtime_write_preview(
            self._catalog(),
            existing_order_executions_data={"executions": {}},
            existing_order_locks_data={"locks": []},
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MALFORMED_EXECUTIONS_FIELD", result["issues"])

    def test_input_immutability(self) -> None:
        catalog = self._catalog()
        executions = self._existing_executions()
        locks = self._existing_locks()
        before = (deepcopy(catalog), deepcopy(executions), deepcopy(locks))

        result = build_execution_runtime_write_preview(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=locks,
        )
        result["execution_record_preview"]["execution_id"] = "MUTATED_RESULT_ONLY"
        result["would_write_targets"]["order_locks"] = "changed"

        self.assertEqual(before[0], catalog)
        self.assertEqual(before[1], executions)
        self.assertEqual(before[2], locks)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._preview()

        self.assertEqual(result["status"], "READY")
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        self._preview()

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_write_preview

        module_text = execution_runtime_write_preview.__loader__.get_source(
            execution_runtime_write_preview.__name__
        )

        self.assertNotIn("write_text", module_text)
        self.assertNotIn("mkdir", module_text)
        self.assertNotIn("os.replace", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text)
        self.assertNotIn("ExecutionController", module_text)
        self.assertNotIn("QWidget", module_text)


if __name__ == "__main__":
    unittest.main()
