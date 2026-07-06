import copy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from execution_runtime_catalog_orchestrator import (
    ORCHESTRATOR_TYPE,
    run_execution_runtime_catalog_orchestrator_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _file_hash(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCatalogOrchestratorTests(TestCase):
    def _execution_request_preview(
        self,
        *,
        execution_id="EXEC_1",
        order_id="ORDER_1",
        request_hash="HASH_1",
        lock_id="LOCK_1",
    ):
        return {
            "ok": True,
            "stage": "EXECUTION_REQUEST_PREVIEW",
            "execution_request": {
                "execution_id": execution_id,
                "order_id": order_id,
                "request_hash": request_hash,
                "lock_id": lock_id,
            },
        }

    def _lock_preview(self, *, unresolved=False):
        return {
            "ok": not unresolved,
            "stage": "ORDER_LOCK_PREVIEW",
            "unresolved": unresolved,
            "lock_id": "LOCK_1",
            "preview_only": True,
        }

    def _request_hash_preview(self):
        return {
            "ok": True,
            "stage": "REQUEST_HASH_PREVIEW",
            "request_hash": "HASH_1",
            "preview_only": True,
        }

    def _queue_write_preview(self, *, available=True):
        return {
            "stage": "EXECUTION_QUEUE_WRITE_PREVIEW",
            "write_preview": available,
            "preview_only": True,
            "no_write": True,
            "runtime_write": False,
        }

    def _order_candidate(self):
        return {
            "id": "ORDER_1",
            "candidate_state": "REAL_READY",
            "signal": "BUY",
            "price": 50000,
            "qty": 1,
            "order_type": "BUY",
            "hoga": "00",
        }

    def _ready_inputs(self):
        return {
            "execution_request_preview": self._execution_request_preview(),
            "lock_preview": self._lock_preview(),
            "request_hash_preview": self._request_hash_preview(),
            "queue_write_preview_result": self._queue_write_preview(),
            "order_candidate": self._order_candidate(),
        }

    def test_ready_flow(self):
        result = run_execution_runtime_catalog_orchestrator_preview(**self._ready_inputs())

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(result["orchestrator_type"], ORCHESTRATOR_TYPE)
        self.assertEqual(result["catalog_preview"]["status"], "READY")
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["issues"], [])
        self.assertIn("Runtime write disabled", result["warnings"])

    def test_blocked_flow(self):
        inputs = self._ready_inputs()
        inputs["queue_write_preview_result"] = self._queue_write_preview(available=False)

        result = run_execution_runtime_catalog_orchestrator_preview(**inputs)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["catalog_preview"]["status"], "BLOCKED")
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("QUEUE_PREVIEW_UNAVAILABLE", result["issues"])

    def test_invalid_flow(self):
        inputs = self._ready_inputs()
        inputs["execution_request_preview"] = self._execution_request_preview(execution_id="")

        result = run_execution_runtime_catalog_orchestrator_preview(**inputs)

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["catalog_preview"]["status"], "INVALID")
        self.assertFalse(result["validation"]["valid"])
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])

    def test_validator_invalid_flow(self):
        invalid_validation = {
            "valid": False,
            "status": "INVALID",
            "preview_only": True,
            "runtime_write": False,
            "issues": ["VALIDATOR_FORCED_INVALID"],
            "warnings": [],
        }

        with patch(
            "execution_runtime_catalog_orchestrator.validate_execution_runtime_catalog_preview",
            return_value=invalid_validation,
        ):
            result = run_execution_runtime_catalog_orchestrator_preview(**self._ready_inputs())

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["catalog_preview"]["status"], "READY")
        self.assertEqual(result["validation"], invalid_validation)
        self.assertIn("VALIDATOR_FORCED_INVALID", result["issues"])

    def test_inputs_remain_unchanged(self):
        inputs = self._ready_inputs()
        before = copy.deepcopy(inputs)

        result = run_execution_runtime_catalog_orchestrator_preview(**inputs)
        result["catalog_preview"]["execution_id"] = "MUTATED_RESULT_ONLY"
        result["validation"]["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(inputs, before)

    def test_returned_nested_results_are_copied(self):
        result = run_execution_runtime_catalog_orchestrator_preview(**self._ready_inputs())

        result["catalog_preview"]["runtime_targets"]["order_locks"] = "changed"

        fresh_result = run_execution_runtime_catalog_orchestrator_preview(**self._ready_inputs())
        self.assertEqual(
            fresh_result["catalog_preview"]["runtime_targets"]["order_locks"],
            "runtime/order_locks.json",
        )

    def test_orchestrator_does_not_perform_file_io_or_side_effects(self):
        runtime_hash_before = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_before = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        with patch("builtins.open") as mocked_open, patch.object(
            Path, "write_text"
        ) as mocked_write_text, patch.object(Path, "mkdir") as mocked_mkdir:
            result = run_execution_runtime_catalog_orchestrator_preview(**self._ready_inputs())

        runtime_hash_after = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_after = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        self.assertEqual(result["status"], "READY")
        mocked_open.assert_not_called()
        mocked_write_text.assert_not_called()
        mocked_mkdir.assert_not_called()
        self.assertEqual(runtime_hash_before, runtime_hash_after)
        self.assertEqual(rules_hash_before, rules_hash_after)
