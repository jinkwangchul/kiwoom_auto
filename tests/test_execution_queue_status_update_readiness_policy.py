# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_status_update_readiness_policy import (
    evaluate_execution_queue_status_update_readiness,
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


class QueueStatusUpdateReadinessPolicyTest(unittest.TestCase):
    def _runtime_status_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "RUNTIME_STATUS_UPDATE_PREVIEW",
            "updated": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "execution_status": "BROKER_RESULT_RECORDED",
            "order_id": "ORDER_QUEUE_STATUS_1",
            "request_hash": "HASH_QUEUE_STATUS_1",
            "broker_order_no": "BRK_QUEUE_STATUS_1",
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

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"manual_queue_status_update_confirmed": True}
        result.update(overrides)
        return result

    def _environment(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "queue_status_update_enabled": True,
            "queue_execution_state_enabled": True,
        }
        result.update(overrides)
        return result

    def _evaluate(
        self,
        runtime_update_result: object | None = None,
        confirmations: object | None = None,
        environment_flags: object | None = None,
    ) -> dict[str, object]:
        return evaluate_execution_queue_status_update_readiness(
            self._runtime_update() if runtime_update_result is None else runtime_update_result,
            self._confirmations() if confirmations is None else confirmations,
            self._environment() if environment_flags is None else environment_flags,
        )

    def test_all_valid_ready_to_update_queue_status(self) -> None:
        result = self._evaluate()

        self.assertEqual("EXECUTION_QUEUE_STATUS_UPDATE_READINESS_POLICY", result["policy_type"])
        self.assertEqual("READY_TO_UPDATE_QUEUE_STATUS", result["status"])
        self.assertTrue(result["queue_status_update_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_status_update_called"])
        self.assertFalse(result["lock_release_called"])
        self.assertEqual([], result["issues"])

    def test_queue_status_update_allowed_only_when_ready(self) -> None:
        ready = self._evaluate()
        blocked = self._evaluate(confirmations={})

        self.assertTrue(ready["queue_status_update_allowed"])
        self.assertFalse(blocked["queue_status_update_allowed"])

    def test_runtime_status_update_blocked_is_blocked(self) -> None:
        result = self._evaluate(self._runtime_update(status="BLOCKED", issues=["RUNTIME_BLOCKED"]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["queue_status_update_allowed"])
        self.assertIn("RUNTIME_BLOCKED", result["issues"])

    def test_runtime_status_update_invalid_or_malformed_is_invalid(self) -> None:
        invalid = self._evaluate(self._runtime_update(status="INVALID", issues=["RUNTIME_INVALID"]))
        malformed = self._evaluate("malformed")

        self.assertEqual("INVALID", invalid["status"])
        self.assertIn("RUNTIME_INVALID", invalid["issues"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_RUNTIME_STATUS_UPDATE_ORCHESTRATOR_RESULT", malformed["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = self._evaluate(self._runtime_update(next_stage="OTHER"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_STATUS_UPDATE_NEXT_STAGE_REQUIRED", result["issues"])

    def test_runtime_status_update_called_false_blocks(self) -> None:
        result = self._evaluate(self._runtime_update(runtime_status_update_called=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_STATUS_UPDATE_CALLED_NOT_TRUE", result["issues"])

    def test_missing_runtime_status_record_blocks(self) -> None:
        result = self._evaluate(self._runtime_update(runtime_status_record=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_STATUS_RECORD_REQUIRED", result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_QUEUE_STATUS_UPDATE_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(result["required_confirmations"]["manual_queue_status_update_confirmed"])

    def test_queue_status_update_enabled_false_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(queue_status_update_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_STATUS_UPDATE_ENVIRONMENT_DISABLED", result["issues"])
        self.assertFalse(result["environment_checks"]["queue_status_update_enabled"])

    def test_queue_execution_state_enabled_false_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(queue_execution_state_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_EXECUTION_STATE_ENVIRONMENT_DISABLED", result["issues"])
        self.assertFalse(result["environment_checks"]["queue_execution_state_enabled"])

    def test_preview_flags_and_called_flags_remain_closed_when_blocked(self) -> None:
        result = self._evaluate(confirmations={})

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_status_update_called"])
        self.assertFalse(result["lock_release_called"])

    def test_queue_lock_runtime_recorder_and_broker_are_not_called(self) -> None:
        with mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit, \
            mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint:
            result = self._evaluate()

        self.assertEqual("READY_TO_UPDATE_QUEUE_STATUS", result["status"])
        queue_commit.assert_not_called()
        runtime_update.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        self._evaluate()
        self._evaluate(confirmations={})

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
