# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from send_order_record_review_to_chejan_entry_adapter import (
    build_chejan_entry_contract_from_send_order_record_review,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class SendOrderRecordReviewToChejanEntryAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _recorder_review(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "RECORD_REVIEW_OK",
            "review": {
                "review_type": "SEND_ORDER_RESULT_RECORDER_REVIEW",
                "record_id": "SEND_ORDER_RECORD_CHEJAN_1",
                "record_path": "temp/runtime/order_executions.json",
                "dispatch_id": "DISPATCH_CHEJAN_1",
                "order_id": "ORDER_CHEJAN_1",
                "source_order_id": "SOURCE_ORDER_CHEJAN_1",
                "source_signal_id": "SIGNAL_CHEJAN_1",
                "code": "003550",
                "side": "BUY",
                "quantity": 10,
                "price": 85000,
                "hoga": "03",
                "send_order_return_code": 0,
                "send_order_status": "SEND_ORDER_SENT",
                "review_status": "SEND_ORDER_REVIEW_OK",
                "record_status": "SEND_ORDER_RESULT_RECORDED",
                "record_called": True,
                "chejan_deferred": True,
            },
            "issues": [],
            "warnings": [],
            "record_verified": True,
            "chejan_ready": False,
            "chejan_called": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _queue_lookup(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "QUEUE_RECORD_LOOKUP_OK",
            "lookup_ok": True,
            "order_queued_record": {
                "id": "ORDER_QUEUED_CHEJAN_1",
                "status": "ORDER_QUEUED",
                "order_id": "ORDER_CHEJAN_1",
                "dispatch_id": "DISPATCH_CHEJAN_1",
                "source_signal_id": "SIGNAL_CHEJAN_1",
                "request_hash": "REQUEST_HASH_CHEJAN_1",
                "lock_id": "LOCK_CHEJAN_1",
                "execution_id": "EXEC_CHEJAN_1",
                "send_order_called": True,
                "send_order_result_status": "SEND_ORDER_CALLED",
            },
            "identity": {
                "order_queued_id": "ORDER_QUEUED_CHEJAN_1",
                "order_id": "ORDER_CHEJAN_1",
                "dispatch_id": "DISPATCH_CHEJAN_1",
                "source_signal_id": "SIGNAL_CHEJAN_1",
                "request_hash": "REQUEST_HASH_CHEJAN_1",
                "lock_id": "LOCK_CHEJAN_1",
                "execution_id": "EXEC_CHEJAN_1",
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "chejan_entry_enabled": True,
            "chejan_live_connected": False,
            "operator_review_required": True,
        }
        result.update(overrides)
        return result

    def test_chejan_entry_ready_normal(self) -> None:
        result = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(),
            self._queue_lookup(),
            self._context(),
        )

        self.assertEqual("CHEJAN_ENTRY_READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lifecycle_created"])

        contract = result["chejan_entry_contract"]
        self.assertEqual("CHEJAN_ENTRY_OPEN_POLICY_REQUIRED", contract["next_stage"])
        self.assertFalse(contract["chejan_live_connected"])
        self.assertEqual("ORDER_CHEJAN_1", contract["identity"]["order_id"])
        self.assertEqual("DISPATCH_CHEJAN_1", contract["identity"]["dispatch_id"])
        self.assertEqual("SIGNAL_CHEJAN_1", contract["identity"]["source_signal_id"])
        self.assertEqual("ORDER_QUEUED_CHEJAN_1", contract["identity"]["order_queued_id"])

    def test_recorder_review_blocked_is_blocked(self) -> None:
        result = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(status="RECORD_REVIEW_BLOCKED", record_verified=False, issues=["blocked"]),
            self._queue_lookup(),
            self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("recorder_review_result.status is RECORD_REVIEW_BLOCKED", result["issues"])

    def test_recorder_review_invalid_or_error_is_invalid(self) -> None:
        invalid = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(status="INVALID", record_verified=False, issues=["bad"]),
            self._queue_lookup(),
            self._context(),
        )
        error = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(status="ERROR", record_verified=False, issues=["boom"]),
            self._queue_lookup(),
            self._context(),
        )

        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", error["status"])

    def test_queue_lookup_blocked_is_blocked(self) -> None:
        result = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(),
            self._queue_lookup(status="BLOCKED", lookup_ok=False, issues=["lookup blocked"]),
            self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("queue_record_lookup_preview is blocked", result["issues"])

    def test_identity_mismatch_is_invalid(self) -> None:
        for field, value in (
            ("order_id", "OTHER_ORDER"),
            ("dispatch_id", "OTHER_DISPATCH"),
            ("source_signal_id", "OTHER_SIGNAL"),
        ):
            lookup = self._queue_lookup()
            lookup["identity"][field] = value

            result = build_chejan_entry_contract_from_send_order_record_review(
                self._recorder_review(),
                lookup,
                self._context(),
            )

            self.assertEqual("INVALID", result["status"])
            self.assertIn(f"{field} mismatch", result["issues"])

    def test_chejan_entry_disabled_is_blocked(self) -> None:
        result = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(),
            self._queue_lookup(),
            self._context(chejan_entry_enabled=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("chejan_entry_context.chejan_entry_enabled is not true", result["issues"])

    def test_malformed_input_is_invalid(self) -> None:
        self.assertEqual(
            "INVALID",
            build_chejan_entry_contract_from_send_order_record_review(None, self._queue_lookup(), self._context())["status"],
        )
        self.assertEqual(
            "INVALID",
            build_chejan_entry_contract_from_send_order_record_review(self._recorder_review(), "bad", self._context())["status"],
        )
        self.assertEqual(
            "INVALID",
            build_chejan_entry_contract_from_send_order_record_review(self._recorder_review(), self._queue_lookup(), {})["status"],
        )

    def test_missing_required_field_is_invalid(self) -> None:
        review = self._recorder_review()
        review["review"]["source_signal_id"] = ""

        result = build_chejan_entry_contract_from_send_order_record_review(
            review,
            self._queue_lookup(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("recorder review source_signal_id is required", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        recorder_review = self._recorder_review()
        queue_lookup = self._queue_lookup()
        context = self._context()
        before = (deepcopy(recorder_review), deepcopy(queue_lookup), deepcopy(context))

        result = build_chejan_entry_contract_from_send_order_record_review(
            recorder_review,
            queue_lookup,
            context,
        )
        result["chejan_entry_contract"]["identity"]["order_id"] = "MUTATED_ORDER"
        result["chejan_entry_contract"]["order_queued_record"]["order_id"] = "MUTATED_RECORD"

        self.assertEqual(before, (recorder_review, queue_lookup, context))
        fresh = build_chejan_entry_contract_from_send_order_record_review(
            recorder_review,
            queue_lookup,
            context,
        )
        self.assertEqual("ORDER_CHEJAN_1", fresh["chejan_entry_contract"]["identity"]["order_id"])
        self.assertEqual("ORDER_CHEJAN_1", fresh["chejan_entry_contract"]["order_queued_record"]["order_id"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = build_chejan_entry_contract_from_send_order_record_review(
            self._recorder_review(),
            self._queue_lookup(),
            self._context(),
        )

        self.assertEqual("CHEJAN_ENTRY_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
