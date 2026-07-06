import copy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from execution_runtime_catalog_preview import (
    CATALOG_TYPE,
    build_execution_runtime_catalog_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _file_hash(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCatalogPreviewTests(TestCase):
    def _execution_request_preview(
        self,
        *,
        execution_id="EXEC_1",
        order_id="ORDER_1",
        request_hash="HASH_1",
        lock_id="LOCK_1",
        ok=True,
        unresolved=False,
    ):
        return {
            "ok": ok,
            "stage": "EXECUTION_REQUEST_PREVIEW",
            "unresolved": unresolved,
            "execution_request": {
                "execution_id": execution_id,
                "order_id": order_id,
                "source_signal_id": "SIG_1",
                "request_hash": request_hash,
                "lock_id": lock_id,
            },
        }

    def _lock_preview(self, *, lock_id="LOCK_1", ok=True, unresolved=False):
        return {
            "ok": ok,
            "stage": "ORDER_LOCK_PREVIEW",
            "unresolved": unresolved,
            "lock_id": lock_id,
            "preview_only": True,
        }

    def _request_hash_preview(self, *, request_hash="HASH_1", ok=True, unresolved=False):
        return {
            "ok": ok,
            "stage": "REQUEST_HASH_PREVIEW",
            "unresolved": unresolved,
            "request_hash": request_hash,
            "preview_only": True,
        }

    def _queue_write_preview(self, *, available=True):
        return {
            "stage": "EXECUTION_QUEUE_WRITE_PREVIEW",
            "write_preview": available,
            "preview_only": True,
            "no_write": True,
            "runtime_write": False,
            "order_queued_record_preview": {"id": "ORDER_QUEUED_ORDER_1"} if available else None,
        }

    def _order_candidate(self):
        return {
            "id": "ORDER_1",
            "order_id": "ORDER_1",
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

    def test_ready_catalog_preview(self):
        result = build_execution_runtime_catalog_preview(**self._ready_inputs())

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(result["catalog_type"], CATALOG_TYPE)
        self.assertEqual(result["execution_id"], "EXEC_1")
        self.assertEqual(result["order_id"], "ORDER_1")
        self.assertEqual(result["request_hash"], "HASH_1")
        self.assertEqual(result["lock_id"], "LOCK_1")
        self.assertEqual(
            result["runtime_targets"],
            {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
        )
        self.assertTrue(all(value == "PASS" for value in result["checks"].values()))
        self.assertIn("Runtime write disabled", result["warnings"])
        self.assertEqual(result["issues"], [])

    def test_blocked_when_hash_unresolved(self):
        inputs = self._ready_inputs()
        inputs["request_hash_preview"] = self._request_hash_preview(ok=False, unresolved=True)

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("REQUEST_HASH_UNRESOLVED", result["issues"])
        self.assertEqual(result["checks"]["RequestHashPreview"], "FAIL")

    def test_blocked_when_lock_unresolved(self):
        inputs = self._ready_inputs()
        inputs["lock_preview"] = self._lock_preview(ok=False, unresolved=True)

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("LOCK_UNRESOLVED", result["issues"])
        self.assertEqual(result["checks"]["LockPreview"], "FAIL")

    def test_blocked_when_queue_preview_unavailable(self):
        inputs = self._ready_inputs()
        inputs["queue_write_preview_result"] = self._queue_write_preview(available=False)

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("QUEUE_PREVIEW_UNAVAILABLE", result["issues"])
        self.assertEqual(result["checks"]["QueuePreview"], "FAIL")

    def test_invalid_when_execution_id_missing(self):
        inputs = self._ready_inputs()
        inputs["execution_request_preview"] = self._execution_request_preview(execution_id="")

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])

    def test_invalid_when_order_id_missing(self):
        inputs = self._ready_inputs()
        inputs["execution_request_preview"] = self._execution_request_preview(order_id="")
        inputs["order_candidate"] = {"candidate_state": "REAL_READY"}

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MISSING_ORDER_ID", result["issues"])

    def test_invalid_when_request_hash_missing(self):
        inputs = self._ready_inputs()
        inputs["execution_request_preview"] = self._execution_request_preview(request_hash="")
        inputs["request_hash_preview"] = self._request_hash_preview(request_hash="")

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MISSING_REQUEST_HASH", result["issues"])

    def test_invalid_when_lock_id_missing(self):
        inputs = self._ready_inputs()
        inputs["execution_request_preview"] = self._execution_request_preview(lock_id="")
        inputs["lock_preview"] = self._lock_preview(lock_id="")

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MISSING_LOCK_ID", result["issues"])

    def test_invalid_when_input_is_malformed(self):
        inputs = self._ready_inputs()
        inputs["execution_request_preview"] = "not-a-dict"

        result = build_execution_runtime_catalog_preview(**inputs)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MALFORMED_INPUT", result["issues"])

    def test_inputs_remain_unchanged(self):
        inputs = self._ready_inputs()
        before = copy.deepcopy(inputs)

        result = build_execution_runtime_catalog_preview(**inputs)
        result["runtime_targets"]["order_executions"] = "changed"
        result["checks"]["ExecutionRequest"] = "FAIL"

        self.assertEqual(inputs, before)

    def test_build_does_not_perform_file_io_or_side_effects(self):
        runtime_hash_before = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_before = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        with patch("builtins.open") as mocked_open, patch.object(
            Path, "write_text"
        ) as mocked_write_text, patch.object(Path, "mkdir") as mocked_mkdir:
            result = build_execution_runtime_catalog_preview(**self._ready_inputs())

        runtime_hash_after = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_after = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        self.assertEqual(result["status"], "READY")
        mocked_open.assert_not_called()
        mocked_write_text.assert_not_called()
        mocked_mkdir.assert_not_called()
        self.assertEqual(runtime_hash_before, runtime_hash_after)
        self.assertEqual(rules_hash_before, rules_hash_after)
