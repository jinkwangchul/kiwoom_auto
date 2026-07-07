# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
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
            raise RuntimeError("lock release failed")
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


class LockReleaseOrchestratorTest(unittest.TestCase):
    def _queue_status_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "QUEUE_STATUS_UPDATE_PREVIEW",
            "updated": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_status": "BROKER_RESULT_RECORDED",
            "order_id": "ORDER_LOCK_RELEASE_ORCH_1",
            "request_hash": "HASH_LOCK_RELEASE_ORCH_1",
            "broker_order_no": "BRK_LOCK_RELEASE_ORCH_1",
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

    def test_ready_policy_and_queue_update_ready_releases_lock(self) -> None:
        releaser = LockReleaserSpy()

        result = orchestrate_lock_release(self._policy(), self._queue_update(), releaser)

        self.assertEqual("LOCK_RELEASED", result["status"])
        self.assertTrue(result["lock_release_called"])
        self.assertEqual(1, len(releaser.calls))
        self.assertEqual("POST_EXECUTION_REVIEW_REQUIRED", result["next_stage"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertEqual("ORDER_LOCK_RELEASE_ORCH_1", result["lock_release_record"]["order_id"])
        self.assertEqual("HASH_LOCK_RELEASE_ORCH_1", result["lock_release_record"]["request_hash"])

    def test_default_preview_releaser_releases_without_file_side_effects(self) -> None:
        result = orchestrate_lock_release(self._policy(), self._queue_update())

        self.assertEqual("LOCK_RELEASED", result["status"])
        self.assertTrue(result["lock_release_called"])
        self.assertEqual("LOCK_RELEASE_PREVIEW", result["lock_release_record"]["record_type"])
        self.assertFalse(result["lock_release_record"]["runtime_write"])
        self.assertFalse(result["lock_release_record"]["queue_write"])

    def test_readiness_blocked_invalid_and_disallowed_do_not_call_releaser(self) -> None:
        cases = [
            (self._policy(status="BLOCKED", issues=["POLICY_BLOCKED"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["POLICY_INVALID"]), "INVALID"),
            (self._policy(lock_release_allowed=False), "BLOCKED"),
            ("malformed", "INVALID"),
        ]
        for policy, expected in cases:
            with self.subTest(policy=policy):
                releaser = LockReleaserSpy()
                result = orchestrate_lock_release(policy, self._queue_update(), releaser)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["lock_release_called"])
                self.assertEqual([], releaser.calls)

    def test_queue_update_blocked_invalid_and_malformed_do_not_call_releaser(self) -> None:
        cases = [
            (self._queue_update(status="BLOCKED", issues=["QUEUE_BLOCKED"]), "BLOCKED"),
            (self._queue_update(status="INVALID", issues=["QUEUE_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for queue_update, expected in cases:
            with self.subTest(queue_update=queue_update):
                releaser = LockReleaserSpy()
                result = orchestrate_lock_release(self._policy(), queue_update, releaser)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["lock_release_called"])
                self.assertEqual([], releaser.calls)

    def test_missing_queue_status_record_blocks(self) -> None:
        releaser = LockReleaserSpy()
        result = orchestrate_lock_release(self._policy(), self._queue_update(queue_status_record=None), releaser)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_STATUS_RECORD_REQUIRED", result["issues"])
        self.assertFalse(result["lock_release_called"])
        self.assertEqual([], releaser.calls)

    def test_next_stage_and_queue_status_update_called_blockers(self) -> None:
        cases = [
            self._queue_update(next_stage="OTHER"),
            self._queue_update(queue_status_update_called=False),
        ]
        for queue_update in cases:
            with self.subTest(queue_update=queue_update):
                releaser = LockReleaserSpy()
                result = orchestrate_lock_release(self._policy(), queue_update, releaser)
                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["lock_release_called"])
                self.assertEqual([], releaser.calls)

    def test_releaser_exception_blocks_without_runtime_or_queue_write(self) -> None:
        result = orchestrate_lock_release(
            self._policy(),
            self._queue_update(),
            LockReleaserSpy(raises=True),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["lock_release_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertIn("LOCK_RELEASER_EXCEPTION: lock release failed", result["issues"])

    def test_malformed_or_unreleased_releaser_result(self) -> None:
        malformed = orchestrate_lock_release(
            self._policy(),
            self._queue_update(),
            lock_releaser=lambda queue_update: "not a dict",
        )
        self.assertEqual("INVALID", malformed["status"])
        self.assertTrue(malformed["lock_release_called"])

        unreleased = orchestrate_lock_release(
            self._policy(),
            self._queue_update(),
            lock_releaser=lambda queue_update: {"released": False, "issues": ["NO_LOCK_RELEASE"]},
        )
        self.assertEqual("BLOCKED", unreleased["status"])
        self.assertTrue(unreleased["lock_release_called"])
        self.assertIn("NO_LOCK_RELEASE", unreleased["issues"])

    def test_inputs_are_deepcopied_before_releaser_call_and_result_return(self) -> None:
        releaser = LockReleaserSpy()
        policy = self._policy()
        queue_update = self._queue_update()
        policy_before = copy.deepcopy(policy)
        queue_update_before = copy.deepcopy(queue_update)

        result = orchestrate_lock_release(policy, queue_update, releaser)

        releaser.calls[0]["queue_status_record"]["order_id"] = "MUTATED_CALL"
        result["lock_release_record"]["queue_status_record"]["order_id"] = "MUTATED_RESULT"
        self.assertEqual(policy_before, policy)
        self.assertEqual(queue_update_before, queue_update)
        self.assertEqual("ORDER_LOCK_RELEASE_ORCH_1", queue_update["queue_status_record"]["order_id"])

    def test_queue_runtime_recorder_broker_and_runtime_file_write_are_not_called(self) -> None:
        releaser = LockReleaserSpy()
        with mock.patch("execution_queue_status_update_orchestrator.orchestrate_queue_status_update") as queue_update, \
            mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint, \
            mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit:
            result = orchestrate_lock_release(self._policy(), self._queue_update(), releaser)

        self.assertEqual("LOCK_RELEASED", result["status"])
        queue_update.assert_not_called()
        runtime_update.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        orchestrate_lock_release(self._policy(), self._queue_update(), LockReleaserSpy())
        orchestrate_lock_release(self._policy(status="BLOCKED"), self._queue_update(), LockReleaserSpy())

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
