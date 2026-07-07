# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_post_execution_review import review_post_execution


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


class PostExecutionReviewTest(unittest.TestCase):
    def _lock_release_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "LOCK_RELEASE_PREVIEW",
            "released": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "lock_status": "RELEASED",
            "order_id": "ORDER_POST_REVIEW_1",
            "request_hash": "HASH_POST_REVIEW_1",
            "broker_order_no": "BRK_POST_REVIEW_1",
        }
        result.update(overrides)
        return result

    def _lock_release(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orchestrator_type": "EXECUTION_LOCK_RELEASE_ORCHESTRATOR",
            "status": "LOCK_RELEASED",
            "lock_release_called": True,
            "lock_release_record": self._lock_release_record(),
            "runtime_write": False,
            "queue_write": False,
            "next_stage": "POST_EXECUTION_REVIEW_REQUIRED",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_all_valid_execution_completed(self) -> None:
        result = review_post_execution(self._lock_release())

        self.assertEqual("EXECUTION_POST_EXECUTION_REVIEW", result["review_type"])
        self.assertEqual("EXECUTION_COMPLETED", result["status"])
        self.assertTrue(result["execution_completed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["gui_update_called"])
        self.assertEqual("EXECUTION_COMPLETE", result["next_stage"])
        self.assertEqual("ORDER_POST_REVIEW_1", result["lock_release_record"]["order_id"])
        self.assertEqual([], result["issues"])

    def test_blocked_input_is_blocked(self) -> None:
        result = review_post_execution(self._lock_release(status="BLOCKED", issues=["LOCK_BLOCKED"]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_completed"])
        self.assertIn("LOCK_BLOCKED", result["issues"])
        self.assertEqual("BLOCKED", result["next_stage"])

    def test_invalid_and_malformed_input_are_invalid(self) -> None:
        invalid = review_post_execution(self._lock_release(status="INVALID", issues=["LOCK_INVALID"]))
        malformed = review_post_execution("malformed")

        self.assertEqual("INVALID", invalid["status"])
        self.assertIn("LOCK_INVALID", invalid["issues"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_LOCK_RELEASE_ORCHESTRATOR_RESULT", malformed["issues"])

    def test_missing_lock_release_record_blocks(self) -> None:
        result = review_post_execution(self._lock_release(lock_release_record=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_completed"])
        self.assertIn("LOCK_RELEASE_RECORD_REQUIRED", result["issues"])

    def test_lock_release_called_false_blocks(self) -> None:
        result = review_post_execution(self._lock_release(lock_release_called=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_completed"])
        self.assertIn("LOCK_RELEASE_CALLED_NOT_TRUE", result["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = review_post_execution(self._lock_release(next_stage="OTHER"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_completed"])
        self.assertIn("POST_EXECUTION_REVIEW_NEXT_STAGE_REQUIRED", result["issues"])

    def test_runtime_queue_and_gui_flags_remain_closed(self) -> None:
        result = review_post_execution(self._lock_release())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["gui_update_called"])

    def test_no_runtime_queue_gui_lock_update_recorder_or_broker_recall(self) -> None:
        with mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("execution_lock_release_orchestrator.orchestrate_lock_release") as lock_release, \
            mock.patch("execution_queue_status_update_orchestrator.orchestrate_queue_status_update") as queue_update, \
            mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint:
            result = review_post_execution(self._lock_release())

        self.assertEqual("EXECUTION_COMPLETED", result["status"])
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        lock_release.assert_not_called()
        queue_update.assert_not_called()
        runtime_update.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()

    def test_result_is_deepcopied(self) -> None:
        lock_release = self._lock_release()
        result = review_post_execution(lock_release)

        result["lock_release_record"]["order_id"] = "MUTATED_RESULT"

        self.assertEqual("ORDER_POST_REVIEW_1", lock_release["lock_release_record"]["order_id"])

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        review_post_execution(self._lock_release())
        review_post_execution(self._lock_release(status="BLOCKED"))
        review_post_execution(self._lock_release(lock_release_record=None))

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
