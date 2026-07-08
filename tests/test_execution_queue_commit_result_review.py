# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_queue_commit_executor import execute_queue_commit_from_dry_run
from execution_queue_commit_result_review import review_queue_commit_result


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class QueueCommitResultReviewTest(unittest.TestCase):
    def _queue_path(self, root: Path) -> Path:
        runtime = root / "runtime"
        runtime.mkdir()
        queue_path = runtime / "order_queue.json"
        _write_json(queue_path, {"version": 1, "updated_at": "", "orders": []})
        return queue_path

    def _dry_run(self) -> dict[str, object]:
        return {
            "status": "DRY_RUN_READY",
            "dry_run": {
                "queue_commit_dry_run": True,
                "commit_contract": {
                    "order_id": "ORDER_REVIEW_1",
                    "source_order_id": "ORDER_REVIEW_1",
                    "source_signal_id": "SIGNAL_REVIEW_1",
                    "code": "003550",
                    "side": "BUY",
                    "quantity": 10,
                    "price": 85000,
                    "request_hash": "HASH_REVIEW_1",
                    "lock_id": "LOCK_REVIEW_1",
                    "execution_id": "EXECUTION_REVIEW_1",
                },
                "commit_plan": {
                    "target": "runtime/order_queue.json",
                    "order_contract": {"order_id": "ORDER_REVIEW_1"},
                },
            },
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_commit_called": False,
            "send_order_called": False,
            "issues": [],
            "warnings": [],
        }

    def _committed(self, queue_path: Path) -> dict[str, object]:
        result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)
        self.assertEqual("COMMITTED", result["status"])
        return result

    def test_review_ok_normal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            commit = self._committed(queue_path)

            result = review_queue_commit_result(commit, queue_path, "ORDER_REVIEW_1")

        self.assertEqual("REVIEW_OK", result["status"])
        self.assertTrue(result["send_order_ready"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertEqual(commit["commit_id"], result["review"]["commit_id"])

    def test_commit_blocked_is_review_blocked(self) -> None:
        result = review_queue_commit_result(
            {"status": "BLOCKED", "issues": ["blocked"]},
            "runtime/order_queue.json",
            "ORDER_REVIEW_1",
        )

        self.assertEqual("REVIEW_BLOCKED", result["status"])
        self.assertFalse(result["send_order_ready"])
        self.assertIn("commit_result.status is not COMMITTED", result["issues"])

    def test_commit_invalid_is_invalid(self) -> None:
        result = review_queue_commit_result(
            {"status": "INVALID", "issues": ["bad"]},
            "runtime/order_queue.json",
            "ORDER_REVIEW_1",
        )

        self.assertEqual("INVALID", result["status"])

    def test_commit_error_is_error(self) -> None:
        result = review_queue_commit_result(
            {"status": "ERROR", "issues": ["write failed"]},
            "runtime/order_queue.json",
            "ORDER_REVIEW_1",
        )

        self.assertEqual("ERROR", result["status"])

    def test_expected_order_id_missing_is_invalid(self) -> None:
        result = review_queue_commit_result(
            {"status": "COMMITTED"},
            "runtime/order_queue.json",
            "",
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("expected_order_id is required", result["issues"])

    def test_queue_item_missing_is_review_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            commit = self._committed(queue_path)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            data["orders"] = []
            _write_json(queue_path, data)

            result = review_queue_commit_result(commit, queue_path, "ORDER_REVIEW_1")

        self.assertEqual("REVIEW_BLOCKED", result["status"])
        self.assertIn("queue item not found", result["issues"])

    def test_malformed_queue_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            commit = self._committed(queue_path)
            queue_path.write_text("[]", encoding="utf-8")

            result = review_queue_commit_result(commit, queue_path, "ORDER_REVIEW_1")

        self.assertEqual("ERROR", result["status"])
        self.assertIn("order_queue root must be an object", result["issues"])

    def test_send_order_not_called_and_hashes_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            commit = self._committed(queue_path)
            with mock.patch("send_order_entrypoint.execute_send_order") as send_order:
                result = review_queue_commit_result(commit, queue_path, "ORDER_REVIEW_1")

        self.assertEqual("REVIEW_OK", result["status"])
        send_order.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
