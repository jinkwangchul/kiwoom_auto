# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_commit_contract_preview import build_queue_commit_contract_preview


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


class QueueCommitContractPreviewTest(unittest.TestCase):
    def _approval(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "APPROVED",
            "approval": {"approved": True},
            "issues": [],
            "warnings": [],
            "approval_summary": {"approved": True},
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_commit_called": False,
            "send_order_called": False,
        }
        result.update(overrides)
        return result

    def _readiness(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "READY",
            "readiness": {"queue_commit_ready": True},
            "issues": [],
            "warnings": [],
            "validation_summary": {"ready": True},
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "queue_commit_called": False,
        }
        result.update(overrides)
        return result

    def _order_contract(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "REAL_READY",
            "order_id": "ORDER_QUEUE_CONTRACT_1",
            "source_signal_id": "SIGNAL_QUEUE_CONTRACT_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "preview_only": True,
            "order_intent": {"side": "BUY", "hoga": "\uc2dc\uc7a5\uac00"},
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

    def test_ready_normal(self) -> None:
        result = build_queue_commit_contract_preview(
            self._approval(),
            self._readiness(),
            self._order_contract(),
            self._runtime_snapshot(),
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual("EXECUTION_QUEUE_COMMIT_CONTRACT_PREVIEW", result["commit_contract"]["contract_type"])
        self.assertEqual("EXECUTION_QUEUE_COMMIT_PLAN_PREVIEW", result["commit_plan"]["plan_type"])
        self.assertEqual("ORDER_QUEUE_CONTRACT_1", result["commit_contract"]["order_id"])
        self.assertEqual("QUEUE_COMMIT_SERVICE", result["commit_contract"]["required_next_service"])
        self.assertEqual("runtime/order_queue.json", result["commit_plan"]["target"])
        self.assertEqual("ORDER_QUEUED", result["commit_plan"]["would_create_status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_commit_called"])
        self.assertFalse(result["send_order_called"])

    def test_approval_denied_is_blocked(self) -> None:
        result = build_queue_commit_contract_preview(
            self._approval(status="DENIED"),
            self._readiness(),
            self._order_contract(),
            self._runtime_snapshot(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("approval_result.status is not APPROVED", result["issues"])

    def test_approval_invalid_is_invalid(self) -> None:
        result = build_queue_commit_contract_preview(
            self._approval(status="INVALID"),
            self._readiness(),
            self._order_contract(),
            self._runtime_snapshot(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("approval_result.status is INVALID", result["issues"])

    def test_missing_order_contract_is_invalid(self) -> None:
        result = build_queue_commit_contract_preview(
            self._approval(),
            self._readiness(),
            {},
            self._runtime_snapshot(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("order_contract must be a non-empty dict", result["issues"])

    def test_commit_contract_required_fields(self) -> None:
        result = build_queue_commit_contract_preview(
            self._approval(),
            self._readiness(),
            self._order_contract(),
            self._runtime_snapshot(),
        )
        contract = result["commit_contract"]

        for field in (
            "contract_type",
            "queue_contract_version",
            "order_id",
            "source_signal_id",
            "code",
            "side",
            "quantity",
            "price",
            "order_status",
            "execution_enabled",
            "approval_status",
            "readiness_status",
            "preview_only",
            "runtime_write",
            "queue_write",
            "queue_commit_called",
            "send_order_called",
        ):
            self.assertIn(field, contract)

    def test_runtime_lock_and_duplicate_block(self) -> None:
        locked = build_queue_commit_contract_preview(
            self._approval(),
            self._readiness(),
            self._order_contract(),
            self._runtime_snapshot(locks=[{"order_id": "ORDER_QUEUE_CONTRACT_1"}]),
        )
        duplicate = build_queue_commit_contract_preview(
            self._approval(),
            self._readiness(),
            self._order_contract(),
            self._runtime_snapshot(existing_orders=[{"order_id": "ORDER_QUEUE_CONTRACT_1"}]),
        )

        self.assertEqual("BLOCKED", locked["status"])
        self.assertIn("runtime lock exists for order", locked["issues"])
        self.assertEqual("BLOCKED", duplicate["status"])
        self.assertIn("duplicate order exists", duplicate["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        approval = self._approval()
        readiness = self._readiness()
        order_contract = self._order_contract()
        runtime_snapshot = self._runtime_snapshot()
        originals = (
            deepcopy(approval),
            deepcopy(readiness),
            deepcopy(order_contract),
            deepcopy(runtime_snapshot),
        )

        result = build_queue_commit_contract_preview(approval, readiness, order_contract, runtime_snapshot)
        result["commit_plan"]["order_contract"]["order_intent"]["side"] = "SELL"

        self.assertEqual(originals[0], approval)
        self.assertEqual(originals[1], readiness)
        self.assertEqual(originals[2], order_contract)
        self.assertEqual(originals[3], runtime_snapshot)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order:
            result = build_queue_commit_contract_preview(
                self._approval(),
                self._readiness(),
                self._order_contract(),
                self._runtime_snapshot(),
            )

        self.assertEqual("READY", result["status"])
        queue_commit.assert_not_called()
        queue_write.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
