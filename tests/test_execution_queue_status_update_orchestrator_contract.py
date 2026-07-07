# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_status_update_orchestrator import orchestrate_queue_status_update


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


class QueueUpdaterSpy:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.raises = raises

    def __call__(self, runtime_update: dict[str, object]) -> dict[str, object]:
        self.calls.append(runtime_update)
        if self.raises:
            raise RuntimeError("queue update failed")
        runtime_status_record = dict(runtime_update["runtime_status_record"])
        return {
            "record_type": "QUEUE_STATUS_UPDATE_PREVIEW",
            "updated": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_status": "BROKER_RESULT_RECORDED",
            "order_id": runtime_status_record.get("order_id"),
            "request_hash": runtime_status_record.get("request_hash"),
            "broker_order_no": runtime_status_record.get("broker_order_no"),
            "runtime_status_record": runtime_status_record,
            "issues": [],
            "warnings": [],
        }


class QueueStatusUpdateOrchestratorContractTest(unittest.TestCase):
    def _runtime_status_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "RUNTIME_STATUS_UPDATE_PREVIEW",
            "updated": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "execution_status": "BROKER_RESULT_RECORDED",
            "order_id": "ORDER_QUEUE_UPDATE_CONTRACT_1",
            "request_hash": "HASH_QUEUE_UPDATE_CONTRACT_1",
            "broker_order_no": "BRK_QUEUE_UPDATE_CONTRACT_1",
        }
        result.update(overrides)
        return result

    def _runtime_update(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orchestrator_type": "EXECUTION_RUNTIME_STATUS_UPDATE_ORCHESTRATOR",
            "status": "RUNTIME_STATUS_UPDATED",
            "runtime_status_update_called": True,
            "runtime_status_record": self._runtime_status_record(),
            "runtime_write": False,
            "queue_write": False,
            "lock_release_called": False,
            "next_stage": "QUEUE_STATUS_UPDATE_REQUIRED",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "policy_type": "EXECUTION_QUEUE_STATUS_UPDATE_READINESS_POLICY",
            "status": "READY_TO_UPDATE_QUEUE_STATUS",
            "queue_status_update_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_status_update_called": False,
            "lock_release_called": False,
            "required_confirmations": {"manual_queue_status_update_confirmed": True},
            "environment_checks": {
                "queue_status_update_enabled": True,
                "queue_execution_state_enabled": True,
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_policy_and_runtime_update_ready_updates_queue_status(self) -> None:
        updater = QueueUpdaterSpy()

        result = orchestrate_queue_status_update(self._policy(), self._runtime_update(), updater)

        self.assertEqual("QUEUE_STATUS_UPDATED", result["status"])
        self.assertEqual("LOCK_RELEASE_REQUIRED", result["next_stage"])
        self.assertTrue(result["queue_status_update_called"])
        self.assertEqual(1, len(updater.calls))
        self.assertEqual("ORDER_QUEUE_UPDATE_CONTRACT_1", result["queue_status_record"]["order_id"])
        self.assertEqual("HASH_QUEUE_UPDATE_CONTRACT_1", result["queue_status_record"]["request_hash"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])

    def test_readiness_blocked_invalid_malformed_and_disallowed_do_not_call_updater(self) -> None:
        cases = [
            (self._policy(status="BLOCKED", issues=["POLICY_BLOCKED"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (self._policy(queue_status_update_allowed=False), "BLOCKED"),
        ]
        for policy, expected in cases:
            with self.subTest(policy=policy):
                updater = QueueUpdaterSpy()
                result = orchestrate_queue_status_update(policy, self._runtime_update(), updater)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["queue_status_update_called"])
                self.assertEqual([], updater.calls)

    def test_runtime_update_blocked_invalid_and_malformed_do_not_call_updater(self) -> None:
        cases = [
            (self._runtime_update(status="BLOCKED", issues=["RUNTIME_BLOCKED"]), "BLOCKED"),
            (self._runtime_update(status="INVALID", issues=["RUNTIME_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for runtime_update, expected in cases:
            with self.subTest(runtime_update=runtime_update):
                updater = QueueUpdaterSpy()
                result = orchestrate_queue_status_update(self._policy(), runtime_update, updater)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["queue_status_update_called"])
                self.assertEqual([], updater.calls)

    def test_missing_runtime_status_record_blocks_without_updater_call(self) -> None:
        updater = QueueUpdaterSpy()

        result = orchestrate_queue_status_update(self._policy(), self._runtime_update(runtime_status_record={}), updater)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_STATUS_RECORD_REQUIRED", result["issues"])
        self.assertFalse(result["queue_status_update_called"])
        self.assertEqual([], updater.calls)

    def test_updater_exception_blocks_and_keeps_boundaries_closed(self) -> None:
        result = orchestrate_queue_status_update(self._policy(), self._runtime_update(), QueueUpdaterSpy(raises=True))

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["queue_status_update_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])
        self.assertIn("QUEUE_STATUS_UPDATER_EXCEPTION: queue update failed", result["issues"])

    def test_lock_runtime_recorder_broker_and_file_writes_are_not_called(self) -> None:
        with mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint, \
            mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit:
            result = orchestrate_queue_status_update(self._policy(), self._runtime_update(), QueueUpdaterSpy())

        self.assertEqual("QUEUE_STATUS_UPDATED", result["status"])
        runtime_update.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        orchestrate_queue_status_update(self._policy(), self._runtime_update(), QueueUpdaterSpy())
        orchestrate_queue_status_update(self._policy(status="BLOCKED"), self._runtime_update(), QueueUpdaterSpy())

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
