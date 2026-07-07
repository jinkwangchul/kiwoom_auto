# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_lock_release_orchestrator import orchestrate_lock_release


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


class LockReleaserSpy:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.raises = raises

    def __call__(self, queue_update: dict[str, object]) -> dict[str, object]:
        self.calls.append(queue_update)
        if self.raises:
            raise RuntimeError("contract lock release failed")
        queue_status_record = dict(queue_update["queue_status_record"])
        return {
            "record_type": "LOCK_RELEASE_PREVIEW",
            "released": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "lock_status": "RELEASED",
            "order_id": queue_status_record.get("order_id"),
            "request_hash": queue_status_record.get("request_hash"),
            "broker_order_no": queue_status_record.get("broker_order_no"),
            "queue_status_record": queue_status_record,
            "issues": [],
            "warnings": [],
        }


class LockReleaseOrchestratorContractTest(unittest.TestCase):
    def _queue_status_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "QUEUE_STATUS_UPDATE_PREVIEW",
            "updated": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_status": "BROKER_RESULT_RECORDED",
            "order_id": "ORDER_LOCK_RELEASE_CONTRACT_1",
            "request_hash": "HASH_LOCK_RELEASE_CONTRACT_1",
            "broker_order_no": "BRK_LOCK_RELEASE_CONTRACT_1",
        }
        result.update(overrides)
        return result

    def _queue_update(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orchestrator_type": "EXECUTION_QUEUE_STATUS_UPDATE_ORCHESTRATOR",
            "status": "QUEUE_STATUS_UPDATED",
            "queue_status_update_called": True,
            "queue_status_record": self._queue_status_record(),
            "runtime_write": False,
            "queue_write": False,
            "lock_release_called": False,
            "next_stage": "LOCK_RELEASE_REQUIRED",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "policy_type": "EXECUTION_LOCK_RELEASE_READINESS_POLICY",
            "status": "READY_TO_RELEASE_LOCK",
            "lock_release_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "lock_release_called": False,
            "required_confirmations": {"manual_lock_release_confirmed": True},
            "environment_checks": {
                "lock_release_enabled": True,
                "runtime_lock_state_enabled": True,
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_queue_update_releases_lock_and_preserves_record_contract(self) -> None:
        releaser = LockReleaserSpy()

        result = orchestrate_lock_release(self._policy(), self._queue_update(), releaser)

        self.assertEqual("LOCK_RELEASED", result["status"])
        self.assertEqual("POST_EXECUTION_REVIEW_REQUIRED", result["next_stage"])
        self.assertTrue(result["lock_release_called"])
        self.assertEqual(1, len(releaser.calls))
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertEqual("LOCK_RELEASE_PREVIEW", result["lock_release_record"]["record_type"])
        self.assertEqual("ORDER_LOCK_RELEASE_CONTRACT_1", result["lock_release_record"]["order_id"])
        self.assertEqual("HASH_LOCK_RELEASE_CONTRACT_1", result["lock_release_record"]["request_hash"])

    def test_readiness_blocked_invalid_malformed_and_disallowed_remain_closed(self) -> None:
        cases = [
            (self._policy(status="BLOCKED", issues=["POLICY_BLOCKED"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (self._policy(lock_release_allowed=False), "BLOCKED"),
        ]
        for policy, expected in cases:
            with self.subTest(policy=policy):
                releaser = LockReleaserSpy()
                result = orchestrate_lock_release(policy, self._queue_update(), releaser)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["lock_release_called"])
                self.assertEqual([], releaser.calls)
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["queue_write"])

    def test_queue_update_blocked_invalid_malformed_and_missing_record_remain_closed(self) -> None:
        cases = [
            (self._queue_update(status="BLOCKED", issues=["QUEUE_BLOCKED"]), "BLOCKED"),
            (self._queue_update(status="INVALID", issues=["QUEUE_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (self._queue_update(queue_status_record=None), "BLOCKED"),
        ]
        for queue_update, expected in cases:
            with self.subTest(queue_update=queue_update):
                releaser = LockReleaserSpy()
                result = orchestrate_lock_release(self._policy(), queue_update, releaser)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["lock_release_called"])
                self.assertEqual([], releaser.calls)
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["queue_write"])

    def test_releaser_exception_blocks_without_file_write_flags(self) -> None:
        result = orchestrate_lock_release(self._policy(), self._queue_update(), LockReleaserSpy(raises=True))

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["lock_release_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertIn("LOCK_RELEASER_EXCEPTION: contract lock release failed", result["issues"])

    def test_no_queue_runtime_recorder_broker_kiwoom_recall_contract(self) -> None:
        releaser = LockReleaserSpy()
        with mock.patch("execution_queue_status_update_orchestrator.orchestrate_queue_status_update") as queue_update, \
            mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = orchestrate_lock_release(self._policy(), self._queue_update(), releaser)

        self.assertEqual("LOCK_RELEASED", result["status"])
        queue_update.assert_not_called()
        runtime_update.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()
        broker_dispatch.assert_not_called()

    def test_runtime_order_locks_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        orchestrate_lock_release(self._policy(), self._queue_update(), LockReleaserSpy())
        orchestrate_lock_release(self._policy(status="BLOCKED"), self._queue_update(), LockReleaserSpy())
        orchestrate_lock_release(self._policy(), self._queue_update(queue_status_record=None), LockReleaserSpy())

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
