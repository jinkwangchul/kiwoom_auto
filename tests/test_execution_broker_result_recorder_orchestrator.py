# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
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


class RecordingCallable:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, review: dict[str, object]) -> dict[str, object]:
        self.calls.append(review)
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


class RaisingRecorder:
    def __call__(self, review: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("record failed")


class ExecutionBrokerResultRecorderOrchestratorTest(unittest.TestCase):
    def _broker_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "broker_status": "SUBMITTED",
            "broker_order_no": "BRK_RECORD_ORCH_1",
            "order_id": "ORDER_RECORD_ORCH_1",
            "request_hash": "HASH_RECORD_ORCH_1",
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

    def test_ready_and_review_ready_records_result(self) -> None:
        recorder = RecordingCallable()

        result = orchestrate_broker_result_recording(self._policy(), self._review(), recorder)

        self.assertEqual("BROKER_RESULT_RECORDED", result["status"])
        self.assertTrue(result["result_record_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])
        self.assertEqual("RUNTIME_STATUS_UPDATE_REQUIRED", result["next_stage"])
        self.assertEqual(1, len(recorder.calls))
        self.assertEqual("ORDER_RECORD_ORCH_1", result["broker_result_record"]["order_id"])
        self.assertEqual("HASH_RECORD_ORCH_1", result["broker_result_record"]["request_hash"])

    def test_default_preview_recorder_records_without_file_side_effects(self) -> None:
        result = orchestrate_broker_result_recording(self._policy(), self._review())

        self.assertEqual("BROKER_RESULT_RECORDED", result["status"])
        self.assertTrue(result["result_record_called"])
        self.assertEqual("BROKER_RESULT_RECORD_PREVIEW", result["broker_result_record"]["record_type"])
        self.assertFalse(result["broker_result_record"]["runtime_write"])
        self.assertFalse(result["broker_result_record"]["queue_write"])

    def test_readiness_blocked_invalid_and_disallowed_do_not_call_recorder(self) -> None:
        cases = [
            (self._policy(status="BLOCKED", issues=["NOT_READY"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["BAD_POLICY"]), "INVALID"),
            (self._policy(result_record_allowed=False), "BLOCKED"),
            ("malformed", "INVALID"),
        ]
        for policy, expected in cases:
            with self.subTest(policy=policy):
                recorder = RecordingCallable()
                result = orchestrate_broker_result_recording(policy, self._review(), recorder)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["result_record_called"])
                self.assertEqual([], recorder.calls)

    def test_review_blocked_invalid_and_malformed_do_not_call_recorder(self) -> None:
        cases = [
            (self._review(status="BLOCKED", issues=["REVIEW_BLOCKED"]), "BLOCKED"),
            (self._review(status="INVALID", issues=["REVIEW_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for review, expected in cases:
            with self.subTest(review=review):
                recorder = RecordingCallable()
                result = orchestrate_broker_result_recording(self._policy(), review, recorder)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["result_record_called"])
                self.assertEqual([], recorder.calls)

    def test_missing_broker_result_blocks(self) -> None:
        recorder = RecordingCallable()
        result = orchestrate_broker_result_recording(self._policy(), self._review(broker_result=None), recorder)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_RESULT_REQUIRED", result["issues"])
        self.assertFalse(result["result_record_called"])
        self.assertEqual([], recorder.calls)

    def test_next_stage_and_broker_called_blockers(self) -> None:
        cases = [
            self._review(next_stage="OTHER"),
            self._review(broker_called=False),
        ]
        for review in cases:
            with self.subTest(review=review):
                recorder = RecordingCallable()
                result = orchestrate_broker_result_recording(self._policy(), review, recorder)
                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["result_record_called"])
                self.assertEqual([], recorder.calls)

    def test_recorder_exception_blocks_without_runtime_or_queue_write(self) -> None:
        result = orchestrate_broker_result_recording(self._policy(), self._review(), RaisingRecorder())

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["result_record_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])
        self.assertIn("BROKER_RESULT_RECORDER_EXCEPTION: record failed", result["issues"])

    def test_malformed_or_unrecorded_recorder_result(self) -> None:
        malformed = orchestrate_broker_result_recording(
            self._policy(),
            self._review(),
            result_recorder=lambda review: "not a dict",
        )
        self.assertEqual("INVALID", malformed["status"])
        self.assertTrue(malformed["result_record_called"])

        unrecorded = orchestrate_broker_result_recording(
            self._policy(),
            self._review(),
            result_recorder=lambda review: {"recorded": False, "issues": ["NO_RECORD"]},
        )
        self.assertEqual("BLOCKED", unrecorded["status"])
        self.assertTrue(unrecorded["result_record_called"])
        self.assertIn("NO_RECORD", unrecorded["issues"])

    def test_inputs_are_deepcopied_before_recorder_call_and_result_return(self) -> None:
        recorder = RecordingCallable()
        review = self._review()
        policy = self._policy()
        review_before = copy.deepcopy(review)
        policy_before = copy.deepcopy(policy)

        result = orchestrate_broker_result_recording(policy, review, recorder)

        recorder.calls[0]["broker_result"]["order_id"] = "MUTATED_CALL"
        result["broker_result_record"]["broker_result"]["order_id"] = "MUTATED_RESULT"
        self.assertEqual(review_before, review)
        self.assertEqual(policy_before, policy)
        self.assertEqual("ORDER_RECORD_ORCH_1", review["broker_result"]["order_id"])

    def test_runtime_queue_lock_broker_and_kiwoom_are_not_called(self) -> None:
        recorder = RecordingCallable()
        with mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order:
            result = orchestrate_broker_result_recording(self._policy(), self._review(), recorder)

        self.assertEqual("BROKER_RESULT_RECORDED", result["status"])
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        result_recorder.assert_not_called()
        send_order.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            ROOT / "routines" / "지표추종매매" / "rules.json",
        ]
        before = {path: _sha256(path) for path in protected}

        orchestrate_broker_result_recording(self._policy(), self._review(), RecordingCallable())
        orchestrate_broker_result_recording(self._policy(status="BLOCKED"), self._review(), RecordingCallable())

        after = {path: _sha256(path) for path in protected}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
