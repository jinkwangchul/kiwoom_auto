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


def _broker_result(**overrides: object) -> dict[str, object]:
    result = {
        "broker_status": "SUBMITTED",
        "broker_order_no": "BRK_RECORD_CONTRACT_1",
        "order_id": "ORDER_RECORD_CONTRACT_1",
        "request_hash": "HASH_RECORD_CONTRACT_1",
    }
    result.update(overrides)
    return result


def _review(**overrides: object) -> dict[str, object]:
    result = {
        "review_type": "EXECUTION_BROKER_RESULT_REVIEW",
        "status": "READY_FOR_RESULT_RECORD",
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "broker_called": True,
        "kiwoom_called": False,
        "next_stage": "BROKER_RESULT_RECORD_REQUIRED",
        "broker_result": _broker_result(),
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _confirmations(**overrides: object) -> dict[str, object]:
    result = {"manual_result_record_confirmed": True}
    result.update(overrides)
    return result


def _environment(**overrides: object) -> dict[str, object]:
    result = {
        "result_record_enabled": True,
        "runtime_recording_enabled": True,
    }
    result.update(overrides)
    return result


class ExecutionBrokerResultRecordReadinessPolicyContractTest(unittest.TestCase):
    def test_all_valid_ready_to_record_broker_result(self) -> None:
        result = evaluate_execution_broker_result_record_readiness(
            _review(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertEqual("READY_TO_RECORD_BROKER_RESULT", result["status"])
        self.assertTrue(result["result_record_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["result_record_called"])
        self.assertFalse(result["lock_release_called"])

    def test_result_record_allowed_only_when_ready(self) -> None:
        ready = evaluate_execution_broker_result_record_readiness(
            _review(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        blocked = evaluate_execution_broker_result_record_readiness(
            _review(status="BLOCKED", issues=["REVIEW_BLOCKED"]),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        invalid = evaluate_execution_broker_result_record_readiness(
            _review(status="INVALID", issues=["REVIEW_INVALID"]),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertTrue(ready["result_record_allowed"])
        self.assertFalse(blocked["result_record_allowed"])
        self.assertFalse(invalid["result_record_allowed"])

    def test_review_blocked_invalid_and_malformed_are_safe(self) -> None:
        cases = [
            (_review(status="BLOCKED", issues=["REVIEW_BLOCKED"]), "BLOCKED"),
            (_review(status="INVALID", issues=["REVIEW_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for review, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_execution_broker_result_record_readiness(
                    review,
                    confirmations=_confirmations(),
                    environment_flags=_environment(),
                )

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["result_record_allowed"])

    def test_review_payload_blockers(self) -> None:
        cases = [
            (_review(next_stage="BLOCKED"), "BROKER_RESULT_RECORD_NEXT_STAGE_REQUIRED"),
            (_review(broker_called=False), "BROKER_CALLED_NOT_TRUE"),
            (_review(broker_result=None), "BROKER_RESULT_REQUIRED"),
        ]
        for review, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                result = evaluate_execution_broker_result_record_readiness(
                    review,
                    confirmations=_confirmations(),
                    environment_flags=_environment(),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["result_record_allowed"])
                self.assertIn(expected_issue, result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = evaluate_execution_broker_result_record_readiness(
            _review(),
            confirmations={},
            environment_flags=_environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["result_record_allowed"])
        self.assertFalse(result["required_confirmations"]["manual_result_record_confirmed"])
        self.assertIn("MANUAL_RESULT_RECORD_CONFIRMATION_REQUIRED", result["issues"])

    def test_environment_false_flags_block(self) -> None:
        cases = [
            ("result_record_enabled", "RESULT_RECORD_ENVIRONMENT_DISABLED"),
            ("runtime_recording_enabled", "RUNTIME_RECORDING_ENVIRONMENT_DISABLED"),
        ]
        for flag, expected_issue in cases:
            with self.subTest(flag=flag):
                result = evaluate_execution_broker_result_record_readiness(
                    _review(),
                    confirmations=_confirmations(),
                    environment_flags=_environment(**{flag: False}),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["result_record_allowed"])
                self.assertIn(expected_issue, result["issues"])

    def test_recorder_lock_release_commit_and_broker_are_not_called(self) -> None:
        external_broker = mock.Mock()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_controller.build_kiwoom_order_request") as kiwoom_request,
        ):
            result = evaluate_execution_broker_result_record_readiness(
                _review(),
                confirmations=_confirmations(),
                environment_flags=_environment(),
            )

        self.assertEqual("READY_TO_RECORD_BROKER_RESULT", result["status"])
        self.assertFalse(result["result_record_called"])
        self.assertFalse(result["lock_release_called"])
        external_broker.send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()
        kiwoom_request.assert_not_called()

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        evaluate_execution_broker_result_record_readiness(
            _review(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        evaluate_execution_broker_result_record_readiness(
            _review(status="BLOCKED"),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
