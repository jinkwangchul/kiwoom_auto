# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_reconciliation import build_runtime_reconciliation_preview


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


class LifecycleRuntimeReconciliationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _recovery_preview(self) -> dict[str, object]:
        return {
            "status": "RECOVERY_PREVIEW_READY",
            "preview_only": True,
            "recovery_summary": {
                "persistence_id": "PERSISTENCE_RECON_1",
                "order_id": "ORDER_STATE_MISMATCH",
            },
            "issues": [],
            "warnings": [],
        }

    def _runtime_snapshot(self) -> dict[str, object]:
        return {
            "orders": {
                "ORDER_STATE_MISMATCH": {
                    "order_id": "ORDER_STATE_MISMATCH",
                    "runtime_state": "FILLED",
                    "quantity": 10,
                    "price": 70000,
                },
                "ORDER_RUNTIME_ONLY": {
                    "order_id": "ORDER_RUNTIME_ONLY",
                    "runtime_state": "ORDER_RECEIVED",
                    "quantity": 1,
                },
            }
        }

    def _broker_snapshot(self) -> dict[str, object]:
        return {
            "orders": {
                "ORDER_STATE_MISMATCH": {
                    "order_id": "ORDER_STATE_MISMATCH",
                    "runtime_state": "PARTIALLY_FILLED",
                    "quantity": 10,
                    "price": 70000,
                },
                "ORDER_BROKER_ONLY": {
                    "order_id": "ORDER_BROKER_ONLY",
                    "runtime_state": "ORDER_RECEIVED",
                    "quantity": 2,
                },
            }
        }

    def _result(self) -> dict[str, object]:
        return build_runtime_reconciliation_preview(
            self._recovery_preview(),
            self._runtime_snapshot(),
            self._broker_snapshot(),
            {"generated_at": "2026-07-08 11:00:00", "compare_fields": ["runtime_state", "quantity", "price"]},
        )

    def test_valid_recovery_preview_builds_reconciliation_preview(self) -> None:
        result = self._result()

        self.assertEqual("RECONCILIATION_PREVIEW_READY", result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_RECONCILIATION_PREVIEW", result["reconciliation_type"])
        self.assertTrue(result["validation_result"]["valid"])

    def test_runtime_view_is_deepcopy(self) -> None:
        runtime = self._runtime_snapshot()

        result = build_runtime_reconciliation_preview(self._recovery_preview(), runtime, self._broker_snapshot())

        result["runtime_view"]["orders"]["ORDER_STATE_MISMATCH"]["runtime_state"] = "MUTATED"
        self.assertEqual("FILLED", runtime["orders"]["ORDER_STATE_MISMATCH"]["runtime_state"])

    def test_broker_view_is_deepcopy(self) -> None:
        broker = self._broker_snapshot()

        result = build_runtime_reconciliation_preview(self._recovery_preview(), self._runtime_snapshot(), broker)

        result["broker_view"]["orders"]["ORDER_STATE_MISMATCH"]["runtime_state"] = "MUTATED"
        self.assertEqual("PARTIALLY_FILLED", broker["orders"]["ORDER_STATE_MISMATCH"]["runtime_state"])

    def test_runtime_broker_order_state_mismatch_is_detected(self) -> None:
        result = self._result()

        mismatch = [
            item
            for item in result["mismatch_candidates"]
            if item["order_id"] == "ORDER_STATE_MISMATCH" and item["field"] == "runtime_state"
        ]
        self.assertEqual(1, len(mismatch))
        self.assertEqual("FILLED", mismatch[0]["runtime_value"])
        self.assertEqual("PARTIALLY_FILLED", mismatch[0]["broker_value"])

    def test_missing_runtime_order_is_detected(self) -> None:
        result = self._result()

        missing = [
            item
            for item in result["mismatch_candidates"]
            if item["order_id"] == "ORDER_BROKER_ONLY"
        ]
        self.assertEqual(1, len(missing))
        self.assertEqual("ORDER_MISSING_FROM_RUNTIME_VIEW", missing[0]["candidate_type"])

    def test_missing_broker_order_is_detected(self) -> None:
        result = self._result()

        missing = [
            item
            for item in result["mismatch_candidates"]
            if item["order_id"] == "ORDER_RUNTIME_ONLY"
        ]
        self.assertEqual(1, len(missing))
        self.assertEqual("ORDER_MISSING_FROM_BROKER_VIEW", missing[0]["candidate_type"])

    def test_reconciliation_actions_are_manual_review_preview(self) -> None:
        result = self._result()

        self.assertEqual(len(result["mismatch_candidates"]), len(result["reconciliation_actions"]))
        self.assertTrue(result["reconciliation_actions"])
        for action in result["reconciliation_actions"]:
            self.assertEqual("MANUAL_REVIEW_REQUIRED", action["action_type"])
            self.assertFalse(action["runtime_write"])
            self.assertFalse(action["broker_write"])
            self.assertFalse(action["reconciliation_executed"])

    def test_review_required_items_and_summary_counts_are_created(self) -> None:
        result = self._result()

        self.assertEqual(result["mismatch_candidates"], result["review_required_items"])
        self.assertEqual(3, result["reconciliation_summary"]["mismatch_count"])
        self.assertEqual(3, result["reconciliation_summary"]["action_count"])
        self.assertEqual(3, result["reconciliation_summary"]["review_required_count"])

    def test_invalid_or_blocked_recovery_preview_is_not_ready(self) -> None:
        invalid = build_runtime_reconciliation_preview({"status": "INVALID", "preview_only": True}, self._runtime_snapshot(), self._broker_snapshot())
        blocked = build_runtime_reconciliation_preview({"status": "BLOCKED", "preview_only": True}, self._runtime_snapshot(), self._broker_snapshot())

        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertFalse(invalid["validation_result"]["valid"])
        self.assertFalse(blocked["validation_result"]["valid"])

    def test_preview_only_and_no_write_or_external_call_flags_are_fixed(self) -> None:
        result = self._result()

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["reconciliation_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["broker_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])


if __name__ == "__main__":
    unittest.main()
