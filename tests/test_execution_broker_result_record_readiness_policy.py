# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_broker_result_record_readiness_policy import (
    evaluate_execution_broker_result_record_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionBrokerResultRecordReadinessPolicyTest(unittest.TestCase):
    def _broker_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "broker_status": "SUBMITTED",
            "broker_order_no": "BRK_RECORD_1",
            "order_id": "ORDER_RECORD_1",
            "request_hash": "HASH_RECORD_1",
        }
        result.update(overrides)
        return result

    def _review(self, **overrides: object) -> dict[str, object]:
        result = {
            "review_type": "EXECUTION_BROKER_RESULT_REVIEW",
            "status": "READY_FOR_RESULT_RECORD",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "broker_called": True,
            "kiwoom_called": False,
            "next_stage": "BROKER_RESULT_RECORD_REQUIRED",
            "broker_result": self._broker_result(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        result = {"manual_result_record_confirmed": True}
        result.update(overrides)
        return result

    def _environment(self, **overrides: object) -> dict[str, object]:
        result = {
            "result_record_enabled": True,
            "runtime_recording_enabled": True,
        }
        result.update(overrides)
        return result

    def test_all_valid_ready_to_record_broker_result(self) -> None:
        result = evaluate_execution_broker_result_record_readiness(
            self._review(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("READY_TO_RECORD_BROKER_RESULT", result["status"])
        self.assertTrue(result["result_record_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(result["broker_called"])
        self.assertFalse(result["result_record_called"])
        self.assertFalse(result["lock_release_called"])

    def test_result_record_allowed_only_when_ready(self) -> None:
        ready = evaluate_execution_broker_result_record_readiness(
            self._review(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        blocked = evaluate_execution_broker_result_record_readiness(
            self._review(status="BLOCKED", issues=["REVIEW_BLOCKED"]),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        invalid = evaluate_execution_broker_result_record_readiness(
            self._review(status="INVALID", issues=["REVIEW_INVALID"]),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertTrue(ready["result_record_allowed"])
        self.assertFalse(blocked["result_record_allowed"])
        self.assertFalse(invalid["result_record_allowed"])

    def test_review_blocked_invalid_and_malformed(self) -> None:
        cases = [
            (self._review(status="BLOCKED", issues=["REVIEW_BLOCKED"]), "BLOCKED"),
            (self._review(status="INVALID", issues=["REVIEW_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for review, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_execution_broker_result_record_readiness(
                    review,
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(),
                )

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["result_record_allowed"])

    def test_next_stage_broker_called_and_broker_result_required(self) -> None:
        cases = [
            (self._review(next_stage="BLOCKED"), "BROKER_RESULT_RECORD_NEXT_STAGE_REQUIRED"),
            (self._review(broker_called=False), "BROKER_CALLED_NOT_TRUE"),
            (self._review(broker_result=None), "BROKER_RESULT_REQUIRED"),
        ]
        for review, issue in cases:
            with self.subTest(issue=issue):
                result = evaluate_execution_broker_result_record_readiness(
                    review,
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = evaluate_execution_broker_result_record_readiness(
            self._review(),
            confirmations={},
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_RESULT_RECORD_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(result["required_confirmations"]["manual_result_record_confirmed"])

    def test_environment_flags_false_block(self) -> None:
        cases = [
            ("result_record_enabled", "RESULT_RECORD_ENVIRONMENT_DISABLED"),
            ("runtime_recording_enabled", "RUNTIME_RECORDING_ENVIRONMENT_DISABLED"),
        ]
        for flag, expected_issue in cases:
            with self.subTest(flag=flag):
                result = evaluate_execution_broker_result_record_readiness(
                    self._review(),
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(**{flag: False}),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_issue, result["issues"])

    def test_recorder_lock_release_runtime_queue_and_broker_are_not_called(self) -> None:
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = evaluate_execution_broker_result_record_readiness(
                self._review(),
                confirmations=self._confirmations(),
                environment_flags=self._environment(),
            )

        self.assertEqual("READY_TO_RECORD_BROKER_RESULT", result["status"])
        self.assertFalse(result["result_record_called"])
        self.assertFalse(result["lock_release_called"])
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        evaluate_execution_broker_result_record_readiness(
            self._review(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        evaluate_execution_broker_result_record_readiness(
            self._review(status="BLOCKED"),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
