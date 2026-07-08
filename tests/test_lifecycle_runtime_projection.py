# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_projection import project_lifecycle_commit_to_runtime_view


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


class LifecycleRuntimeProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _committed_result(self, event: str = "ORDER_RECEIVED") -> dict[str, object]:
        return {
            "status": "COMMITTED",
            "commit_token": "TOKEN_1",
            "commit_contract": {
                "candidate_lifecycle_event": event,
                "order_id": "ORDER_1",
                "dispatch_id": "DISPATCH_1",
                "source_signal_id": "SIGNAL_1",
                "order_queued_id": "QUEUED_1",
                "evidence_id": "EVIDENCE_1",
                "code": "005930",
                "account_no": "12345678",
                "side": "BUY",
                "quantity": 3,
                "price": 71000,
            },
            "issues": [],
            "warnings": [],
        }

    def _snapshot(self) -> dict[str, object]:
        return {
            "snapshot_valid": True,
            "orders": {
                "ORDER_OLD": {
                    "order_id": "ORDER_OLD",
                    "runtime_state": "ORDER_RECEIVED",
                }
            },
            "lifecycle_events": [],
        }

    def test_committed_lifecycle_event_is_projected(self) -> None:
        result = project_lifecycle_commit_to_runtime_view(self._committed_result(), self._snapshot())

        self.assertEqual("PROJECTED", result["status"])
        self.assertEqual("ORDER_RECEIVED", result["lifecycle_event"])
        self.assertEqual("ORDER_RECEIVED", result["runtime_projection"]["runtime_state"])

    def test_non_committed_result_is_blocked_without_projection(self) -> None:
        result = project_lifecycle_commit_to_runtime_view({"status": "ABORTED", "issues": ["external failed"]}, self._snapshot())

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual({}, result["runtime_projection"])
        self.assertIn("lifecycle commit is not committed", result["issues"])

    def test_fill_event_builds_position_projection(self) -> None:
        result = project_lifecycle_commit_to_runtime_view(self._committed_result("PARTIAL_FILL"), self._snapshot())

        position = result["position_projection"]
        self.assertEqual("POSITION_PROJECTION", position["projection_kind"])
        self.assertTrue(position["position_update_preview"])
        self.assertEqual("POSITION_12345678_005930", position["position_id"])
        self.assertEqual(3, position["quantity_delta"])
        self.assertFalse(position["position_write"])

    def test_fill_event_builds_balance_projection(self) -> None:
        result = project_lifecycle_commit_to_runtime_view(self._committed_result("FULL_FILL"), self._snapshot())

        balance = result["balance_projection"]
        self.assertEqual("BALANCE_PROJECTION", balance["projection_kind"])
        self.assertTrue(balance["balance_update_preview"])
        self.assertEqual(-213000, balance["cash_delta_preview"])
        self.assertFalse(balance["balance_write"])

    def test_runtime_snapshot_projection_is_deepcopy(self) -> None:
        snapshot = self._snapshot()

        result = project_lifecycle_commit_to_runtime_view(self._committed_result(), snapshot)

        result["runtime_snapshot_projection"]["orders"]["ORDER_OLD"]["runtime_state"] = "MUTATED"
        self.assertEqual("ORDER_RECEIVED", snapshot["orders"]["ORDER_OLD"]["runtime_state"])
        snapshot["orders"]["ORDER_OLD"]["runtime_state"] = "SNAPSHOT_MUTATED"
        self.assertEqual("MUTATED", result["runtime_snapshot_projection"]["orders"]["ORDER_OLD"]["runtime_state"])

    def test_preview_only_and_no_write_or_external_call_flags_are_fixed(self) -> None:
        result = project_lifecycle_commit_to_runtime_view(self._committed_result("PARTIAL_FILL"), self._snapshot())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])


if __name__ == "__main__":
    unittest.main()
