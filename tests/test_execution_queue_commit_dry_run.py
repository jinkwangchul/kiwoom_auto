# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_commit_dry_run import dry_run_queue_commit


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


class QueueCommitDryRunTest(unittest.TestCase):
    def _preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "READY",
            "commit_contract": {
                "contract_type": "EXECUTION_QUEUE_COMMIT_CONTRACT_PREVIEW",
                "order_id": "ORDER_DRY_RUN_1",
                "source_signal_id": "SIGNAL_DRY_RUN_1",
                "preview_only": True,
                "queue_write": False,
                "runtime_write": False,
                "queue_commit_called": False,
                "send_order_called": False,
            },
            "commit_plan": {
                "plan_type": "EXECUTION_QUEUE_COMMIT_PLAN_PREVIEW",
                "target": "runtime/order_queue.json",
                "preview_only": True,
                "queue_write": False,
                "runtime_write": False,
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_commit_called": False,
            "send_order_called": False,
        }
        result.update(overrides)
        return result

    def _runtime_snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "locks": [],
            "existing_orders": [],
            "duplicate": False,
            "locked": False,
        }
        result.update(overrides)
        return result

    def _queue_snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orders": [],
            "existing_orders": [],
            "duplicate": False,
        }
        result.update(overrides)
        return result

    def test_dry_run_ready_normal(self) -> None:
        result = dry_run_queue_commit(
            self._preview(),
            self._runtime_snapshot(),
            self._queue_snapshot(),
        )

        self.assertEqual("DRY_RUN_READY", result["status"])
        self.assertTrue(result["dry_run"]["queue_commit_dry_run"])
        self.assertTrue(result["dry_run"]["dry_run_ready"])
        self.assertEqual("runtime/order_queue.json", result["dry_run"]["target"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_commit_called"])
        self.assertFalse(result["send_order_called"])

    def test_preview_blocked_returns_dry_run_blocked(self) -> None:
        result = dry_run_queue_commit(
            self._preview(status="BLOCKED"),
            self._runtime_snapshot(),
            self._queue_snapshot(),
        )

        self.assertEqual("DRY_RUN_BLOCKED", result["status"])
        self.assertIn("commit_contract_preview.status is not READY", result["issues"])

    def test_preview_invalid_returns_invalid(self) -> None:
        result = dry_run_queue_commit(
            self._preview(status="INVALID"),
            self._runtime_snapshot(),
            self._queue_snapshot(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("commit_contract_preview.status is INVALID", result["issues"])

    def test_queue_snapshot_malformed_is_invalid(self) -> None:
        result = dry_run_queue_commit(
            self._preview(),
            self._runtime_snapshot(),
            {"orders": "bad"},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("queue_snapshot is malformed", result["issues"])

    def test_duplicate_order_blocks(self) -> None:
        result = dry_run_queue_commit(
            self._preview(),
            self._runtime_snapshot(),
            self._queue_snapshot(existing_orders=[{"order_id": "ORDER_DRY_RUN_1"}]),
        )

        self.assertEqual("DRY_RUN_BLOCKED", result["status"])
        self.assertIn("duplicate order exists", result["issues"])
        self.assertFalse(result["dry_run"]["duplicate_check_passed"])

    def test_runtime_lock_blocks(self) -> None:
        result = dry_run_queue_commit(
            self._preview(),
            self._runtime_snapshot(locks=[{"order_id": "ORDER_DRY_RUN_1"}]),
            self._queue_snapshot(),
        )

        self.assertEqual("DRY_RUN_BLOCKED", result["status"])
        self.assertIn("runtime lock exists", result["issues"])
        self.assertFalse(result["dry_run"]["runtime_lock_check_passed"])

    def test_inputs_are_not_mutated(self) -> None:
        preview = self._preview()
        runtime_snapshot = self._runtime_snapshot()
        queue_snapshot = self._queue_snapshot()
        originals = (deepcopy(preview), deepcopy(runtime_snapshot), deepcopy(queue_snapshot))

        result = dry_run_queue_commit(preview, runtime_snapshot, queue_snapshot)
        result["dry_run"]["commit_contract"]["order_id"] = "MUTATED"

        self.assertEqual(originals[0], preview)
        self.assertEqual(originals[1], runtime_snapshot)
        self.assertEqual(originals[2], queue_snapshot)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order:
            result = dry_run_queue_commit(
                self._preview(),
                self._runtime_snapshot(),
                self._queue_snapshot(),
            )

        self.assertEqual("DRY_RUN_READY", result["status"])
        queue_commit.assert_not_called()
        queue_write.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
