# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_status_update_readiness_policy import (
    evaluate_execution_runtime_status_update_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rules_path() -> Path:
    matches = sorted((ROOT / "routines").glob("*/rules.json"))
    for path in matches:
        if path.parent.name == "지표추종매매":
            return path
    return matches[0]


class RuntimeStatusUpdateReadinessPolicyTest(unittest.TestCase):
    def _broker_result_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "BROKER_RESULT_RECORD_PREVIEW",
            "recorded": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "order_id": "ORDER_RUNTIME_STATUS_1",
            "request_hash": "HASH_RUNTIME_STATUS_1",
            "broker_order_no": "BRK_RUNTIME_STATUS_1",
        }
        result.update(overrides)
        return result

    def _recorder(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orchestrator_type": "EXECUTION_BROKER_RESULT_RECORDER_ORCHESTRATOR",
            "status": "BROKER_RESULT_RECORDED",
            "result_record_called": True,
            "runtime_write": False,
            "queue_write": False,
            "lock_release_called": False,
            "broker_result_record": self._broker_result_record(),
            "next_stage": "RUNTIME_STATUS_UPDATE_REQUIRED",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"manual_runtime_status_update_confirmed": True}
        result.update(overrides)
        return result

    def _environment(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "runtime_status_update_enabled": True,
            "runtime_execution_state_enabled": True,
        }
        result.update(overrides)
        return result

    def _evaluate(
        self,
        recorder_result: object | None = None,
        confirmations: object | None = None,
        environment_flags: object | None = None,
    ) -> dict[str, object]:
        return evaluate_execution_runtime_status_update_readiness(
            self._recorder() if recorder_result is None else recorder_result,
            self._confirmations() if confirmations is None else confirmations,
            self._environment() if environment_flags is None else environment_flags,
        )

    def test_all_valid_ready_to_update_runtime_status(self) -> None:
        result = self._evaluate()

        self.assertEqual("EXECUTION_RUNTIME_STATUS_UPDATE_READINESS_POLICY", result["policy_type"])
        self.assertEqual("READY_TO_UPDATE_RUNTIME_STATUS", result["status"])
        self.assertTrue(result["runtime_status_update_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_status_update_called"])
        self.assertFalse(result["queue_update_called"])
        self.assertFalse(result["lock_release_called"])
        self.assertEqual([], result["issues"])

    def test_runtime_status_update_allowed_only_when_ready(self) -> None:
        ready = self._evaluate()
        blocked = self._evaluate(confirmations={})

        self.assertTrue(ready["runtime_status_update_allowed"])
        self.assertFalse(blocked["runtime_status_update_allowed"])

    def test_recorder_blocked_is_blocked(self) -> None:
        result = self._evaluate(self._recorder(status="BLOCKED", issues=["RECORDER_BLOCKED"]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_status_update_allowed"])
        self.assertIn("RECORDER_BLOCKED", result["issues"])

    def test_recorder_invalid_or_malformed_is_invalid(self) -> None:
        invalid = self._evaluate(self._recorder(status="INVALID", issues=["RECORDER_INVALID"]))
        malformed = self._evaluate("malformed")

        self.assertEqual("INVALID", invalid["status"])
        self.assertIn("RECORDER_INVALID", invalid["issues"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_BROKER_RESULT_RECORDER_ORCHESTRATOR_RESULT", malformed["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = self._evaluate(self._recorder(next_stage="OTHER"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_STATUS_UPDATE_NEXT_STAGE_REQUIRED", result["issues"])

    def test_result_record_called_false_blocks(self) -> None:
        result = self._evaluate(self._recorder(result_record_called=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RESULT_RECORD_CALLED_NOT_TRUE", result["issues"])

    def test_missing_broker_result_record_blocks(self) -> None:
        result = self._evaluate(self._recorder(broker_result_record=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_RESULT_RECORD_REQUIRED", result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_RUNTIME_STATUS_UPDATE_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(result["required_confirmations"]["manual_runtime_status_update_confirmed"])

    def test_runtime_status_update_enabled_false_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(runtime_status_update_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_STATUS_UPDATE_ENVIRONMENT_DISABLED", result["issues"])
        self.assertFalse(result["environment_checks"]["runtime_status_update_enabled"])

    def test_runtime_execution_state_enabled_false_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(runtime_execution_state_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_EXECUTION_STATE_ENVIRONMENT_DISABLED", result["issues"])
        self.assertFalse(result["environment_checks"]["runtime_execution_state_enabled"])

    def test_preview_flags_and_called_flags_remain_closed_when_blocked(self) -> None:
        result = self._evaluate(confirmations={})

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_status_update_called"])
        self.assertFalse(result["queue_update_called"])
        self.assertFalse(result["lock_release_called"])

    def test_runtime_queue_lock_broker_and_recorder_are_not_called(self) -> None:
        with mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint:
            result = self._evaluate()

        self.assertEqual("READY_TO_UPDATE_RUNTIME_STATUS", result["status"])
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            _rules_path(),
        ]
        before = {path: _sha256(path) for path in protected}

        self._evaluate()
        self._evaluate(confirmations={})

        after = {path: _sha256(path) for path in protected}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
