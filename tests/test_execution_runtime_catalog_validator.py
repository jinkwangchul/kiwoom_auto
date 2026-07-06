import copy
import hashlib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from execution_runtime_catalog_preview import build_execution_runtime_catalog_preview
from execution_runtime_catalog_validator import validate_execution_runtime_catalog_preview


ROOT = Path(__file__).resolve().parents[1]


def _file_hash(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCatalogValidatorTests(TestCase):
    def _execution_request_preview(self):
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

    def _lock_preview(self):
        return {
            "ok": True,
            "stage": "ORDER_LOCK_PREVIEW",
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

    def _queue_write_preview(self):
        return {
            "stage": "EXECUTION_QUEUE_WRITE_PREVIEW",
            "write_preview": True,
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

    def _ready_catalog(self):
        return build_execution_runtime_catalog_preview(
            execution_request_preview=self._execution_request_preview(),
            lock_preview=self._lock_preview(),
            request_hash_preview=self._request_hash_preview(),
            queue_write_preview_result=self._queue_write_preview(),
            order_candidate=self._order_candidate(),
        )

    def _blocked_catalog(self):
        catalog = self._ready_catalog()
        catalog["status"] = "BLOCKED"
        catalog["issues"] = ["QUEUE_PREVIEW_UNAVAILABLE"]
        catalog["checks"]["QueuePreview"] = "FAIL"
        return catalog

    def test_valid_ready_catalog(self):
        result = validate_execution_runtime_catalog_preview(self._ready_catalog())

        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["warnings"], [])

    def test_valid_blocked_catalog(self):
        result = validate_execution_runtime_catalog_preview(self._blocked_catalog())

        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["issues"], [])

    def test_invalid_catalog_type(self):
        catalog = self._ready_catalog()
        catalog["catalog_type"] = "WRONG"

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_CATALOG_TYPE", result["issues"])

    def test_invalid_preview_only_and_runtime_write(self):
        catalog = self._ready_catalog()
        catalog["preview_only"] = False
        catalog["runtime_write"] = True

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("PREVIEW_ONLY_REQUIRED", result["issues"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_invalid_status(self):
        catalog = self._ready_catalog()
        catalog["status"] = "WAITING"

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("INVALID_STATUS", result["issues"])

    def test_ready_with_issues_is_invalid(self):
        catalog = self._ready_catalog()
        catalog["issues"] = ["SHOULD_NOT_BE_READY"]

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("READY_WITH_ISSUES", result["issues"])

    def test_invalid_without_issues_is_invalid(self):
        catalog = self._ready_catalog()
        catalog["status"] = "INVALID"
        catalog["issues"] = []

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_WITHOUT_ISSUES", result["issues"])

    def test_missing_runtime_targets(self):
        catalog = self._ready_catalog()
        catalog.pop("runtime_targets")

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_RUNTIME_TARGETS", result["issues"])

    def test_malformed_checks_warnings_issues(self):
        catalog = self._ready_catalog()
        catalog["checks"] = []
        catalog["warnings"] = {}
        catalog["issues"] = {}

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("CHECKS_MUST_BE_DICT", result["issues"])
        self.assertIn("WARNINGS_MUST_BE_LIST", result["issues"])
        self.assertIn("ISSUES_MUST_BE_LIST", result["issues"])

    def test_missing_identity_fields(self):
        catalog = self._blocked_catalog()
        catalog["execution_id"] = None
        catalog["order_id"] = ""
        catalog["request_hash"] = None
        catalog["lock_id"] = ""

        result = validate_execution_runtime_catalog_preview(catalog)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])
        self.assertIn("MISSING_ORDER_ID", result["issues"])
        self.assertIn("MISSING_REQUEST_HASH", result["issues"])
        self.assertIn("MISSING_LOCK_ID", result["issues"])

    def test_malformed_catalog_preview(self):
        result = validate_execution_runtime_catalog_preview("bad")

        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MALFORMED_CATALOG_PREVIEW", result["issues"])

    def test_input_immutability(self):
        catalog = self._ready_catalog()
        before = copy.deepcopy(catalog)

        result = validate_execution_runtime_catalog_preview(catalog)
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(catalog, before)

    def test_validator_does_not_perform_file_io_or_side_effects(self):
        runtime_hash_before = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_before = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")
        catalog = self._ready_catalog()

        with patch("builtins.open") as mocked_open, patch.object(
            Path, "write_text"
        ) as mocked_write_text, patch.object(Path, "mkdir") as mocked_mkdir:
            result = validate_execution_runtime_catalog_preview(catalog)

        runtime_hash_after = _file_hash(ROOT / "runtime" / "order_queue.json")
        rules_hash_after = _file_hash(ROOT / "routines" / "지표추종매매" / "rules.json")

        self.assertTrue(result["valid"])
        mocked_open.assert_not_called()
        mocked_write_text.assert_not_called()
        mocked_mkdir.assert_not_called()
        self.assertEqual(runtime_hash_before, runtime_hash_after)
        self.assertEqual(rules_hash_before, rules_hash_after)
