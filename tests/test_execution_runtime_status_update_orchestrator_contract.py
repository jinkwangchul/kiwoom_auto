# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_status_update_orchestrator import orchestrate_runtime_status_update


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


class UpdaterSpy:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.raises = raises

    def __call__(self, recorder_result: dict[str, object]) -> dict[str, object]:
        self.calls.append(recorder_result)
        if self.raises:
            raise RuntimeError("update failed")
        broker_result_record = dict(recorder_result["broker_result_record"])
        return {
            "record_type": "RUNTIME_STATUS_UPDATE_PREVIEW",
            "updated": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "execution_status": "BROKER_RESULT_RECORDED",
            "order_id": broker_result_record.get("order_id"),
            "request_hash": broker_result_record.get("request_hash"),
            "broker_order_no": broker_result_record.get("broker_order_no"),
            "broker_result_record": broker_result_record,
            "issues": [],
            "warnings": [],
        }


class RuntimeStatusUpdateOrchestratorContractTest(unittest.TestCase):
    def _broker_result_record(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "record_type": "BROKER_RESULT_RECORD_PREVIEW",
            "recorded": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "order_id": "ORDER_RUNTIME_UPDATE_CONTRACT_1",
            "request_hash": "HASH_RUNTIME_UPDATE_CONTRACT_1",
            "broker_order_no": "BRK_RUNTIME_UPDATE_CONTRACT_1",
            "broker_result": {
                "order_id": "ORDER_RUNTIME_UPDATE_CONTRACT_1",
                "request_hash": "HASH_RUNTIME_UPDATE_CONTRACT_1",
                "broker_order_no": "BRK_RUNTIME_UPDATE_CONTRACT_1",
            },
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

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "policy_type": "EXECUTION_RUNTIME_STATUS_UPDATE_READINESS_POLICY",
            "status": "READY_TO_UPDATE_RUNTIME_STATUS",
            "runtime_status_update_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "runtime_status_update_called": False,
            "queue_update_called": False,
            "lock_release_called": False,
            "required_confirmations": {"manual_runtime_status_update_confirmed": True},
            "environment_checks": {
                "runtime_status_update_enabled": True,
                "runtime_execution_state_enabled": True,
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_policy_and_recorder_ready_updates_runtime_status(self) -> None:
        updater = UpdaterSpy()

        result = orchestrate_runtime_status_update(self._policy(), self._recorder(), updater)

        self.assertEqual("RUNTIME_STATUS_UPDATED", result["status"])
        self.assertEqual("QUEUE_STATUS_UPDATE_REQUIRED", result["next_stage"])
        self.assertTrue(result["runtime_status_update_called"])
        self.assertEqual(1, len(updater.calls))
        self.assertEqual("ORDER_RUNTIME_UPDATE_CONTRACT_1", result["runtime_status_record"]["order_id"])
        self.assertEqual("HASH_RUNTIME_UPDATE_CONTRACT_1", result["runtime_status_record"]["request_hash"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])

    def test_readiness_blocked_invalid_malformed_and_disallowed_do_not_call_updater(self) -> None:
        cases = [
            (self._policy(status="BLOCKED", issues=["POLICY_BLOCKED"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (self._policy(runtime_status_update_allowed=False), "BLOCKED"),
        ]
        for policy, expected in cases:
            with self.subTest(policy=policy):
                updater = UpdaterSpy()
                result = orchestrate_runtime_status_update(policy, self._recorder(), updater)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["runtime_status_update_called"])
                self.assertEqual([], updater.calls)

    def test_recorder_blocked_invalid_and_malformed_do_not_call_updater(self) -> None:
        cases = [
            (self._recorder(status="BLOCKED", issues=["RECORDER_BLOCKED"]), "BLOCKED"),
            (self._recorder(status="INVALID", issues=["RECORDER_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for recorder, expected in cases:
            with self.subTest(recorder=recorder):
                updater = UpdaterSpy()
                result = orchestrate_runtime_status_update(self._policy(), recorder, updater)
                self.assertEqual(expected, result["status"])
                self.assertFalse(result["runtime_status_update_called"])
                self.assertEqual([], updater.calls)

    def test_missing_broker_result_record_blocks_without_updater_call(self) -> None:
        updater = UpdaterSpy()

        result = orchestrate_runtime_status_update(self._policy(), self._recorder(broker_result_record={}), updater)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_RESULT_RECORD_REQUIRED", result["issues"])
        self.assertFalse(result["runtime_status_update_called"])
        self.assertEqual([], updater.calls)

    def test_updater_exception_blocks_and_keeps_boundaries_closed(self) -> None:
        result = orchestrate_runtime_status_update(self._policy(), self._recorder(), UpdaterSpy(raises=True))

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["runtime_status_update_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lock_release_called"])
        self.assertIn("RUNTIME_STATUS_UPDATER_EXCEPTION: update failed", result["issues"])

    def test_queue_lock_broker_result_recorder_and_runtime_write_are_not_called(self) -> None:
        with mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit, \
            mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("send_order_entrypoint.execute_send_order") as broker_entrypoint, \
            mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit:
            result = orchestrate_runtime_status_update(self._policy(), self._recorder(), UpdaterSpy())

        self.assertEqual("RUNTIME_STATUS_UPDATED", result["status"])
        queue_commit.assert_not_called()
        result_recorder.assert_not_called()
        broker_entrypoint.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        orchestrate_runtime_status_update(self._policy(), self._recorder(), UpdaterSpy())
        orchestrate_runtime_status_update(self._policy(status="BLOCKED"), self._recorder(), UpdaterSpy())

        after = {path: _sha256(path) for path in _protected_paths()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
