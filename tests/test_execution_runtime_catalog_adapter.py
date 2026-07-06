import copy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from execution_runtime_catalog_adapter import (
    ADAPTER_TYPE,
    adapt_execution_runtime_catalog_for_readiness,
)
from execution_runtime_catalog_orchestrator import (
    run_execution_runtime_catalog_orchestrator_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _file_hash(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCatalogAdapterTests(TestCase):
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

    def _orchestrator_result(self, *, blocked=False, invalid=False):
        return run_execution_runtime_catalog_orchestrator_preview(
            execution_request_preview=self._execution_request_preview(
                execution_id="" if invalid else "EXEC_1"
            ),
            lock_preview=self._lock_preview(),
            request_hash_preview=self._request_hash_preview(),
            queue_write_preview_result=self._queue_write_preview(available=not blocked),
            order_candidate=self._order_candidate(),
        )

    def test_ready_conversion(self):
        result = adapt_execution_runtime_catalog_for_readiness(self._orchestrator_result())

        self.assertEqual(result["adapter_type"], ADAPTER_TYPE)
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["display_status"], "Execution runtime catalog ready")
        self.assertEqual(result["execution_id"], "EXEC_1")
        self.assertEqual(result["order_id"], "ORDER_1")
        self.assertEqual(result["request_hash"], "HASH_1")
        self.assertEqual(result["lock_id"], "LOCK_1")
        self.assertEqual(
            result["runtime_targets"]["order_executions"],
            "runtime/order_executions.json",
        )
        self.assertEqual(result["issues"], [])
        self.assertIn("Status: READY", result["summary_lines"])

    def test_blocked_conversion(self):
        result = adapt_execution_runtime_catalog_for_readiness(
            self._orchestrator_result(blocked=True)
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["display_status"], "Execution runtime catalog blocked")
        self.assertIn("QUEUE_PREVIEW_UNAVAILABLE", result["issues"])
        self.assertTrue(any("QUEUE_PREVIEW_UNAVAILABLE" in line for line in result["summary_lines"]))

    def test_invalid_conversion(self):
        result = adapt_execution_runtime_catalog_for_readiness(
            self._orchestrator_result(invalid=True)
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["display_status"], "Execution runtime catalog invalid")
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])

    def test_malformed_orchestrator_result(self):
        result = adapt_execution_runtime_catalog_for_readiness("not-a-dict")

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["display_status"], "Execution runtime catalog invalid")
        self.assertIn("MALFORMED_ORCHESTRATOR_RESULT", result["issues"])
        self.assertEqual(result["runtime_targets"], {})

    def test_missing_catalog_preview(self):
        orchestrator_result = self._orchestrator_result()
        orchestrator_result.pop("catalog_preview")

        result = adapt_execution_runtime_catalog_for_readiness(orchestrator_result)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MISSING_CATALOG_PREVIEW", result["issues"])
        self.assertIsNone(result["execution_id"])
        self.assertEqual(result["runtime_targets"], {})

    def test_input_immutability(self):
        orchestrator_result = self._orchestrator_result()
        before = copy.deepcopy(orchestrator_result)

        result = adapt_execution_runtime_catalog_for_readiness(orchestrator_result)
        result["runtime_targets"]["order_executions"] = "changed"
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(orchestrator_result, before)

    def test_adapter_does_not_perform_file_io_or_side_effects(self):
        runtime_hash_before = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_before = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")
        orchestrator_result = self._orchestrator_result()

        with patch("builtins.open") as mocked_open, patch.object(
            Path, "write_text"
        ) as mocked_write_text, patch.object(Path, "mkdir") as mocked_mkdir:
            result = adapt_execution_runtime_catalog_for_readiness(orchestrator_result)

        runtime_hash_after = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_after = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        self.assertEqual(result["status"], "READY")
        mocked_open.assert_not_called()
        mocked_write_text.assert_not_called()
        mocked_mkdir.assert_not_called()
        self.assertEqual(runtime_hash_before, runtime_hash_after)
        self.assertEqual(rules_hash_before, rules_hash_after)
