# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_queue_commit_executor import execute_queue_commit_from_dry_run


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class QueueCommitExecutorTest(unittest.TestCase):
    def _queue_path(self, root: Path) -> Path:
        runtime = root / "runtime"
        runtime.mkdir()
        queue_path = runtime / "order_queue.json"
        _write_json(queue_path, {"version": 1, "updated_at": "", "orders": []})
        return queue_path

    def _dry_run(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "DRY_RUN_READY",
            "dry_run": {
                "queue_commit_dry_run": True,
                "commit_contract": {
                    "contract_type": "EXECUTION_QUEUE_COMMIT_CONTRACT_PREVIEW",
                    "queue_contract_version": "preview-1",
                    "order_id": "ORDER_EXECUTOR_1",
                    "source_order_id": "ORDER_EXECUTOR_1",
                    "source_signal_id": "SIGNAL_EXECUTOR_1",
                    "code": "003550",
                    "side": "BUY",
                    "quantity": 10,
                    "price": 85000,
                    "request_hash": "HASH_EXECUTOR_1",
                    "lock_id": "LOCK_EXECUTOR_1",
                    "execution_id": "EXECUTOR_EXECUTION_1",
                },
                "commit_plan": {
                    "plan_type": "EXECUTION_QUEUE_COMMIT_PLAN_PREVIEW",
                    "target": "runtime/order_queue.json",
                    "order_contract": {
                        "status": "REAL_READY",
                        "order_id": "ORDER_EXECUTOR_1",
                        "source_signal_id": "SIGNAL_EXECUTOR_1",
                        "code": "003550",
                        "side": "BUY",
                        "quantity": 10,
                        "price": 85000,
                        "execution_enabled": True,
                        "preview_only": True,
                    },
                },
                "target": "runtime/order_queue.json",
                "order_id": "ORDER_EXECUTOR_1",
                "source_signal_id": "SIGNAL_EXECUTOR_1",
                "dry_run_ready": True,
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "queue_commit_called": False,
            "send_order_called": False,
        }
        result.update(overrides)
        return result

    def test_committed_normal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))

            result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["queue_write"])
            self.assertTrue(result["queue_commit_called"])
            self.assertFalse(result["runtime_write"])
            self.assertFalse(result["send_order_called"])
            self.assertEqual(result["commit_id"], result["commit_report"]["commit_id"])
            self.assertEqual("QUEUE_COMMITTED_REVIEW_REQUIRED", result["commit_report"]["next_stage"])
            self.assertTrue(Path(result["commit_report"]["backup_path"]).exists())
            data = _read_json(queue_path)
            self.assertEqual(1, len(data["orders"]))
            self.assertEqual("ORDER_EXECUTOR_1", data["orders"][0]["order_id"])
            self.assertEqual("ORDER_QUEUED", data["orders"][0]["status"])
            self.assertFalse(data["orders"][0]["send_order_called"])

    def test_manual_confirmation_false_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=False)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("manual_confirmation is required", result["issues"])
        self.assertFalse(result["queue_write"])

    def test_dry_run_blocked_returns_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            result = execute_queue_commit_from_dry_run(
                self._dry_run(status="DRY_RUN_BLOCKED"),
                queue_path,
                manual_confirmation=True,
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("dry_run_result.status is not DRY_RUN_READY", result["issues"])

    def test_dry_run_invalid_returns_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            result = execute_queue_commit_from_dry_run(
                self._dry_run(status="INVALID"),
                queue_path,
                manual_confirmation=True,
            )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("dry_run_result.status is INVALID", result["issues"])

    def test_non_real_ready_order_contract_is_invalid_before_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            dry_run = self._dry_run()
            dry_run["dry_run"]["commit_plan"]["order_contract"]["status"] = "EXECUTABLE"
            with mock.patch("execution_queue_commit_executor.commit_legacy_order_queued_record") as writer:
                result = execute_queue_commit_from_dry_run(dry_run, queue_path, manual_confirmation=True)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("commit_plan.order_contract.status is not REAL_READY", result["issues"])
        writer.assert_not_called()

    def test_real_ready_execution_disabled_is_invalid_before_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            dry_run = self._dry_run()
            dry_run["dry_run"]["commit_plan"]["order_contract"]["execution_enabled"] = False
            with mock.patch("execution_queue_commit_executor.commit_legacy_order_queued_record") as writer:
                result = execute_queue_commit_from_dry_run(dry_run, queue_path, manual_confirmation=True)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("commit_plan.order_contract.execution_enabled is not true", result["issues"])
        writer.assert_not_called()

    def test_real_ready_identity_mismatch_is_invalid_before_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            dry_run = self._dry_run()
            dry_run["dry_run"]["commit_plan"]["order_contract"]["source_signal_id"] = "OTHER_SIGNAL"
            with mock.patch("execution_queue_commit_executor.commit_legacy_order_queued_record") as writer:
                result = execute_queue_commit_from_dry_run(dry_run, queue_path, manual_confirmation=True)

        self.assertEqual("INVALID", result["status"])
        self.assertIn(
            "commit_contract.source_signal_id does not match REAL_READY order contract",
            result["issues"],
        )
        writer.assert_not_called()

    def test_duplicate_order_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            _write_json(
                queue_path,
                {
                    "version": 1,
                    "updated_at": "",
                    "orders": [{"order_id": "ORDER_EXECUTOR_1"}],
                },
            )
            result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("duplicate order_id", result["issues"])

    def test_malformed_queue_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            queue_path.write_text("[]", encoding="utf-8")
            result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("order_queue root must be an object", result["issues"])

    def test_queue_path_guard_violation_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "order_queue.json"
            _write_json(queue_path, {"version": 1, "updated_at": "", "orders": []})
            result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("queue_path must resolve to runtime/order_queue.json", result["issues"])

    def test_legacy_executor_uses_canonical_writer_post_write_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            with mock.patch("execution_queue_commit_executor._verify_queue_item") as verify_queue_item:
                result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["commit_report"]["writer_result"]["post_write_verified"])
            verify_queue_item.assert_not_called()

    def test_post_write_failure_preserves_canonical_side_effects(self) -> None:
        writer_result = {
            "committed": True,
            "changed": True,
            "file_write": True,
            "queue_write": True,
            "queue_committed": True,
            "post_write_verified": False,
            "revision_before": 0,
            "revision_after": 1,
            "lock_acquired": True,
            "cas_checked": True,
            "write_stage": "post_write_verify",
            "blocked_reasons": ["forced post-write failure"],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            with mock.patch(
                "execution_queue_commit_executor.commit_legacy_order_queued_record",
                return_value=writer_result,
            ):
                result = execute_queue_commit_from_dry_run(
                    self._dry_run(), queue_path, manual_confirmation=True
                )

        self.assertEqual("ERROR", result["status"])
        for field in ("committed", "changed", "file_write", "queue_write", "queue_committed", "lock_acquired", "cas_checked"):
            self.assertTrue(result[field], field)
            self.assertTrue(result["commit_report"][field], field)
        self.assertFalse(result["post_write_verified"])
        self.assertTrue(result["commit_report"]["manual_restore_required"])

    def test_send_order_not_called_and_protected_files_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            with mock.patch("send_order_entrypoint.execute_send_order") as send_order:
                result = execute_queue_commit_from_dry_run(self._dry_run(), queue_path, manual_confirmation=True)

        self.assertEqual("COMMITTED", result["status"])
        send_order.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_inputs_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._queue_path(Path(temp_dir))
            dry_run = self._dry_run()
            original = deepcopy(dry_run)
            result = execute_queue_commit_from_dry_run(dry_run, queue_path, manual_confirmation=True)

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(original, dry_run)


if __name__ == "__main__":
    unittest.main()
