# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_lock_release_readiness_policy import evaluate_execution_lock_release_readiness


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


class LockReleaseReadinessPolicyContractTest(unittest.TestCase):
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

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"manual_lock_release_confirmed": True}
        result.update(overrides)
        return result

    def _environment(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lock_release_enabled": True,
            "runtime_lock_state_enabled": True,
        }
        result.update(overrides)
        return result

    def _evaluate(
        self,
        queue_update_result: object | None = None,
        confirmations: object | None = None,
        environment_flags: object | None = None,
    ) -> dict[str, object]:
        return evaluate_execution_lock_release_readiness(
            self._queue_update() if queue_update_result is None else queue_update_result,
            self._confirmations() if confirmations is None else confirmations,
            self._environment() if environment_flags is None else environment_flags,
        )

    def test_all_valid_ready_to_release_lock(self) -> None:
        result = self._evaluate()

        self.assertEqual("READY_TO_RELEASE_LOCK", result["status"])
        self.assertTrue(result["lock_release_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])

    def test_lock_release_allowed_only_when_ready(self) -> None:
        cases = [
            self._evaluate(),
            self._evaluate(self._queue_update(status="BLOCKED")),
            self._evaluate(self._queue_update(status="INVALID")),
            self._evaluate(confirmations={}),
        ]

        self.assertTrue(cases[0]["lock_release_allowed"])
        for result in cases[1:]:
            self.assertFalse(result["lock_release_allowed"])

    def test_queue_status_update_blocked_invalid_and_malformed_statuses(self) -> None:
        blocked = self._evaluate(self._queue_update(status="BLOCKED", issues=["QUEUE_BLOCKED"]))
        invalid = self._evaluate(self._queue_update(status="INVALID", issues=["QUEUE_INVALID"]))
        malformed = self._evaluate("malformed")

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("QUEUE_BLOCKED", blocked["issues"])
        self.assertIn("QUEUE_INVALID", invalid["issues"])
        self.assertIn("MALFORMED_QUEUE_STATUS_UPDATE_ORCHESTRATOR_RESULT", malformed["issues"])

    def test_required_queue_update_payload_failures_block(self) -> None:
        cases = [
            (self._queue_update(next_stage="OTHER"), "LOCK_RELEASE_NEXT_STAGE_REQUIRED"),
            (self._queue_update(queue_status_update_called=False), "QUEUE_STATUS_UPDATE_CALLED_NOT_TRUE"),
            (self._queue_update(queue_status_record=None), "QUEUE_STATUS_RECORD_REQUIRED"),
        ]

        for queue_update_result, issue in cases:
            with self.subTest(issue=issue):
                result = self._evaluate(queue_update_result)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_confirmation_and_environment_failures_block(self) -> None:
        cases = [
            ({}, self._environment(), "MANUAL_LOCK_RELEASE_CONFIRMATION_REQUIRED"),
            (
                self._confirmations(),
                self._environment(lock_release_enabled=False),
                "LOCK_RELEASE_ENVIRONMENT_DISABLED",
            ),
            (
                self._confirmations(),
                self._environment(runtime_lock_state_enabled=False),
                "RUNTIME_LOCK_STATE_ENVIRONMENT_DISABLED",
            ),
        ]

        for confirmations, environment_flags, issue in cases:
            with self.subTest(issue=issue):
                result = self._evaluate(confirmations=confirmations, environment_flags=environment_flags)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_preview_and_closed_call_flags_are_preserved_for_ready_and_blocked(self) -> None:
        for result in [self._evaluate(), self._evaluate(confirmations={})]:
            with self.subTest(status=result["status"]):
                self.assertTrue(result["preview_only"])
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["queue_write"])
                self.assertFalse(result["lock_release_called"])

    def test_lock_queue_runtime_recorder_and_broker_are_not_called(self) -> None:
        with mock.patch("execution_queue_status_update_orchestrator.orchestrate_queue_status_update") as queue_update, \
            mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint:
            result = self._evaluate()

        self.assertEqual("READY_TO_RELEASE_LOCK", result["status"])
        queue_update.assert_not_called()
        runtime_update.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        self._evaluate()
        self._evaluate(confirmations={})
        self._evaluate(self._queue_update(status="BLOCKED"))

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
