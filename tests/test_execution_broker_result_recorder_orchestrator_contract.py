# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_broker_result_recorder_orchestrator import orchestrate_broker_result_recording


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


class RecorderSpy:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.raises = raises

    def __call__(self, review: dict[str, object]) -> dict[str, object]:
        self.calls.append(review)
        if self.raises:
            raise RuntimeError("recorder failed")
        broker_result = dict(review["broker_result"])
        return {
            "record_type": "BROKER_RESULT_RECORD_PREVIEW",
            "recorded": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "broker_result": broker_result,
            "order_id": broker_result.get("order_id"),
            "request_hash": broker_result.get("request_hash"),
            "broker_order_no": broker_result.get("broker_order_no"),
            "issues": [],
            "warnings": [],
        }


class BrokerResultRecorderOrchestratorContractTest(unittest.TestCase):
    def _broker_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "broker_status": "SUBMITTED",
            "broker_order_no": "BRK_RECORD_CONTRACT_1",
            "order_id": "ORDER_RECORD_CONTRACT_1",
            "request_hash": "HASH_RECORD_CONTRACT_1",
        }
        result.update(overrides)
        return result

    def _review(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
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

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "policy_type": "EXECUTION_BROKER_RESULT_RECORD_READINESS_POLICY",
            "status": "READY_TO_RECORD_BROKER_RESULT",
            "result_record_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "broker_called": True,
            "result_record_called": False,
            "lock_release_called": False,
            "required_confirmations": {"manual_result_record_confirmed": True},
            "environment_checks": {
                "result_record_enabled": True,
                "runtime_recording_enabled": True,
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_review_ready_records_and_preserves_contract_fields(self) -> None:
        recorder = RecorderSpy()

        result = orchestrate_broker_result_recording(self._policy(), self._review(), recorder)

        self.assertEqual("BROKER_RESULT_RECORDED", result["status"])
        self.assertEqual("RUNTIME_STATUS_UPDATE_REQUIRED", result["next_stage"])
        self.assertTrue(result["result_record_called"])
        self.assertEqual(1, len(recorder.calls))
        self.assertEqual("ORDER_RECORD_CONTRACT_1", result["broker_result_record"]["order_id"])
        self.assertEqual("HASH_RECORD_CONTRACT_1", result["broker_result_record"]["request_hash"])
        self.assertEqual("BRK_RECORD_CONTRACT_1", result["broker_result_record"]["broker_order_no"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])

    def test_readiness_blocked_invalid_malformed_and_disallowed_are_blocked_or_invalid(self) -> None:
        cases = [
            (self._policy(status="BLOCKED", issues=["POLICY_BLOCKED"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (self._policy(result_record_allowed=False), "BLOCKED"),
        ]
        for policy, expected in cases:
            with self.subTest(policy=policy):
                recorder = RecorderSpy()
                result = orchestrate_broker_result_recording(policy, self._review(), recorder)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["result_record_called"])
                self.assertEqual([], recorder.calls)

    def test_review_blocked_invalid_and_malformed_are_blocked_or_invalid(self) -> None:
        cases = [
            (self._review(status="BLOCKED", issues=["REVIEW_BLOCKED"]), "BLOCKED"),
            (self._review(status="INVALID", issues=["REVIEW_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for review, expected in cases:
            with self.subTest(review=review):
                recorder = RecorderSpy()
                result = orchestrate_broker_result_recording(self._policy(), review, recorder)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["result_record_called"])
                self.assertEqual([], recorder.calls)

    def test_missing_broker_result_blocks_without_recorder_call(self) -> None:
        recorder = RecorderSpy()

        result = orchestrate_broker_result_recording(self._policy(), self._review(broker_result={}), recorder)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_RESULT_REQUIRED", result["issues"])
        self.assertFalse(result["result_record_called"])
        self.assertEqual([], recorder.calls)

    def test_recorder_exception_blocks_and_keeps_write_boundaries_closed(self) -> None:
        result = orchestrate_broker_result_recording(self._policy(), self._review(), RecorderSpy(raises=True))

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["result_record_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])
        self.assertIn("BROKER_RESULT_RECORDER_EXCEPTION: recorder failed", result["issues"])

    def test_runtime_queue_lock_status_update_and_broker_are_not_called(self) -> None:
        with mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint:
            result = orchestrate_broker_result_recording(self._policy(), self._review(), RecorderSpy())

        self.assertEqual("BROKER_RESULT_RECORDED", result["status"])
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

        orchestrate_broker_result_recording(self._policy(), self._review(), RecorderSpy())
        orchestrate_broker_result_recording(self._policy(status="BLOCKED"), self._review(), RecorderSpy())

        after = {path: _sha256(path) for path in protected}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
