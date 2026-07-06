import copy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from execution_runtime_catalog_adapter import adapt_execution_runtime_catalog_for_readiness
from execution_runtime_catalog_orchestrator import (
    run_execution_runtime_catalog_orchestrator_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _file_hash(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCatalogContractTests(TestCase):
    def _execution_request_preview(
        self,
        *,
        execution_id="EXEC_1",
        order_id="ORDER_1",
        request_hash="HASH_1",
        lock_id="LOCK_1",
        unresolved=False,
    ):
        return {
            "ok": not unresolved,
            "stage": "EXECUTION_REQUEST_PREVIEW",
            "unresolved": unresolved,
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

    def _request_hash_preview(self, *, unresolved=False):
        return {
            "ok": not unresolved,
            "stage": "REQUEST_HASH_PREVIEW",
            "unresolved": unresolved,
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

    def _inputs(self, *, blocked=False, invalid=False):
        return {
            "execution_request_preview": self._execution_request_preview(
                execution_id="" if invalid else "EXEC_1"
            ),
            "lock_preview": self._lock_preview(),
            "request_hash_preview": self._request_hash_preview(),
            "queue_write_preview_result": self._queue_write_preview(available=not blocked),
            "order_candidate": self._order_candidate(),
        }

    def _adapted_result(self, **kwargs):
        orchestrator_result = run_execution_runtime_catalog_orchestrator_preview(
            **self._inputs(**kwargs)
        )
        adapter_result = adapt_execution_runtime_catalog_for_readiness(orchestrator_result)
        return orchestrator_result, adapter_result

    def test_ready_status_and_flags_are_preserved_to_adapter(self):
        orchestrator_result, adapter_result = self._adapted_result()

        self.assertEqual(orchestrator_result["status"], "READY")
        self.assertEqual(adapter_result["status"], "READY")
        self.assertTrue(adapter_result["preview_only"])
        self.assertFalse(adapter_result["runtime_write"])

    def test_blocked_status_and_issues_are_preserved_to_adapter(self):
        orchestrator_result, adapter_result = self._adapted_result(blocked=True)

        self.assertEqual(orchestrator_result["status"], "BLOCKED")
        self.assertEqual(adapter_result["status"], "BLOCKED")
        self.assertIn("QUEUE_PREVIEW_UNAVAILABLE", orchestrator_result["issues"])
        self.assertIn("QUEUE_PREVIEW_UNAVAILABLE", adapter_result["issues"])

    def test_invalid_status_and_issues_are_preserved_to_adapter(self):
        orchestrator_result, adapter_result = self._adapted_result(invalid=True)

        self.assertEqual(orchestrator_result["status"], "INVALID")
        self.assertEqual(adapter_result["status"], "INVALID")
        self.assertIn("MISSING_EXECUTION_ID", orchestrator_result["issues"])
        self.assertIn("MISSING_EXECUTION_ID", adapter_result["issues"])

    def test_runtime_targets_are_preserved_to_adapter(self):
        orchestrator_result, adapter_result = self._adapted_result()

        self.assertEqual(
            adapter_result["runtime_targets"],
            orchestrator_result["catalog_preview"]["runtime_targets"],
        )

    def test_identity_fields_are_preserved_to_adapter(self):
        orchestrator_result, adapter_result = self._adapted_result()
        catalog = orchestrator_result["catalog_preview"]

        self.assertEqual(adapter_result["execution_id"], catalog["execution_id"])
        self.assertEqual(adapter_result["order_id"], catalog["order_id"])
        self.assertEqual(adapter_result["request_hash"], catalog["request_hash"])
        self.assertEqual(adapter_result["lock_id"], catalog["lock_id"])

    def test_warnings_are_preserved_to_adapter(self):
        orchestrator_result, adapter_result = self._adapted_result()

        self.assertEqual(adapter_result["warnings"], orchestrator_result["warnings"])

    def test_malformed_input_becomes_safe_invalid_payload(self):
        adapter_result = adapt_execution_runtime_catalog_for_readiness("malformed")

        self.assertEqual(adapter_result["status"], "INVALID")
        self.assertTrue(adapter_result["preview_only"])
        self.assertFalse(adapter_result["runtime_write"])
        self.assertIn("MALFORMED_ORCHESTRATOR_RESULT", adapter_result["issues"])
        self.assertEqual(adapter_result["runtime_targets"], {})

    def test_inputs_remain_unchanged_across_contract_flow(self):
        inputs = self._inputs(blocked=True)
        before = copy.deepcopy(inputs)

        orchestrator_result = run_execution_runtime_catalog_orchestrator_preview(**inputs)
        adapter_result = adapt_execution_runtime_catalog_for_readiness(orchestrator_result)
        adapter_result["issues"].append("MUTATED_RESULT_ONLY")
        adapter_result["runtime_targets"]["order_locks"] = "changed"

        self.assertEqual(inputs, before)

    def test_contract_flow_does_not_perform_file_io_or_side_effects(self):
        runtime_hash_before = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_before = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        with patch("builtins.open") as mocked_open, patch.object(
            Path, "write_text"
        ) as mocked_write_text, patch.object(Path, "mkdir") as mocked_mkdir:
            orchestrator_result, adapter_result = self._adapted_result()

        runtime_hash_after = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_after = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        self.assertEqual(orchestrator_result["status"], "READY")
        self.assertEqual(adapter_result["status"], "READY")
        mocked_open.assert_not_called()
        mocked_write_text.assert_not_called()
        mocked_mkdir.assert_not_called()
        self.assertEqual(runtime_hash_before, runtime_hash_after)
        self.assertEqual(rules_hash_before, rules_hash_after)
