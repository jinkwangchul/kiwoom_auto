# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_broker_dispatch_open_policy import evaluate_execution_broker_dispatch_open_policy
from execution_broker_dispatch_orchestrator import orchestrate_broker_dispatch
from execution_broker_result_record_readiness_policy import evaluate_execution_broker_result_record_readiness
from execution_broker_result_recorder_orchestrator import orchestrate_broker_result_recording
from execution_broker_result_review import review_broker_dispatch_result
from execution_final_send_gate_call_orchestrator import call_final_send_gate_after_open_policy
from execution_final_send_gate_input_adapter import adapt_final_send_gate_readiness_to_input
from execution_final_send_gate_open_policy import evaluate_execution_final_send_gate_open_policy
from execution_final_send_gate_orchestrator import orchestrate_final_send_gate_preview
from execution_final_send_gate_readiness_policy import evaluate_execution_final_send_gate_readiness
from execution_lock_release_orchestrator import orchestrate_lock_release
from execution_lock_release_readiness_policy import evaluate_execution_lock_release_readiness
from execution_post_execution_review import review_post_execution
from execution_queue_committed_review import review_execution_queue_committed
from execution_queue_review_to_send_order_preview_adapter import adapt_queue_review_to_send_order_preview
from execution_queue_status_update_orchestrator import orchestrate_queue_status_update
from execution_queue_status_update_readiness_policy import evaluate_execution_queue_status_update_readiness
from execution_runtime_status_update_orchestrator import orchestrate_runtime_status_update
from execution_runtime_status_update_readiness_policy import evaluate_execution_runtime_status_update_readiness
from execution_send_order_entrypoint_open_policy import evaluate_execution_send_order_entrypoint_open_policy
from execution_send_order_entrypoint_orchestrator import orchestrate_send_order_entrypoint


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


class MockBrokerAdapter:
    def __init__(self, *, raises: bool = False) -> None:
        self.requests: list[dict[str, object]] = []
        self.raises = raises

    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("broker dispatch failed")
        return {
            "broker_status": "SUBMITTED",
            "broker_order_no": "BRK_E2E_1",
            "order_id": request.get("order_id"),
            "request_hash": request.get("request_hash"),
        }


class PreviewOnlyExecutionPipelineE2EContractTest(unittest.TestCase):
    def _request_preview(self) -> dict[str, object]:
        return {
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
        }

    def _execution_request(self) -> dict[str, object]:
        return {
            "execution_id": "EXEC_E2E_1",
            "order_id": "ORDER_E2E_1",
            "source_signal_id": "SIGNAL_E2E_1",
            "lock_id": "LOCK_E2E_1",
            "request_hash": "HASH_E2E_1",
            "guard_snapshot": {"account_no": "12345678", "operator_confirmed": True},
            "request_preview": self._request_preview(),
        }

    def _order_queued_record(self) -> dict[str, object]:
        return {
            "id": "ORDER_QUEUED_E2E_1",
            "status": "ORDER_QUEUED",
            "source_signal_id": "SIGNAL_E2E_1",
            "order_id": "ORDER_E2E_1",
            "request_hash": "HASH_E2E_1",
            "lock_id": "LOCK_E2E_1",
            "execution_id": "EXEC_E2E_1",
            "execution_request": self._execution_request(),
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        }

    def _queue_commit_result(self) -> dict[str, object]:
        return {
            "status": "COMMITTED",
            "manual_commit": True,
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "commit_result": {
                "status": "ORDER_QUEUED",
                "committed": True,
                "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
                "order_queued_record": self._order_queued_record(),
                "blocked_reasons": [],
                "warnings": [],
            },
            "blocked_reasons": [],
            "warnings": [],
        }

    def _guard(self, **overrides: object) -> dict[str, object]:
        guard: dict[str, object] = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }
        guard.update(overrides)
        return guard

    def _context(self) -> dict[str, object]:
        return {"manual_final_send_confirmed": True}

    def _run_until_final_send_gate_readiness(self, *, guard: dict[str, object] | None = None) -> dict[str, object]:
        queue_review = review_execution_queue_committed(self._queue_commit_result())
        adapter = adapt_queue_review_to_send_order_preview(queue_review)
        return evaluate_execution_final_send_gate_readiness(adapter, guard or self._guard(), self._context())

    def _run_complete_pipeline(
        self,
        *,
        broker_adapter: MockBrokerAdapter | None = None,
        lock_releaser=None,
        queue_commit_result: dict[str, object] | None = None,
    ) -> dict[str, object]:
        queue_review = review_execution_queue_committed(queue_commit_result or self._queue_commit_result())
        send_preview = adapt_queue_review_to_send_order_preview(queue_review)
        final_readiness = evaluate_execution_final_send_gate_readiness(send_preview, self._guard(), self._context())
        final_input = adapt_final_send_gate_readiness_to_input(final_readiness, self._guard(), self._context())
        final_orchestrator = orchestrate_final_send_gate_preview(final_input)
        final_open = evaluate_execution_final_send_gate_open_policy(
            final_orchestrator,
            confirmations={"manual_final_send_gate_call_confirmed": True},
            environment_flags={"final_send_gate_call_enabled": True},
        )
        final_call = call_final_send_gate_after_open_policy(final_open, final_orchestrator)
        final_call["final_send_gate_input"] = copy.deepcopy(final_orchestrator["final_send_gate_input"])
        entrypoint_open = evaluate_execution_send_order_entrypoint_open_policy(
            final_call,
            confirmations={"manual_send_order_entrypoint_confirmed": True},
            environment_flags={"send_order_entrypoint_enabled": True, "real_send_order_enabled": True},
        )
        entrypoint = orchestrate_send_order_entrypoint(entrypoint_open, final_call)
        broker_open = evaluate_execution_broker_dispatch_open_policy(
            entrypoint,
            confirmations={"manual_broker_dispatch_confirmed": True},
            environment_flags={
                "broker_dispatch_enabled": True,
                "real_broker_dispatch_enabled": True,
                "kiwoom_connected": True,
                "account_selected": True,
                "real_trade_enabled": True,
            },
        )
        broker = broker_adapter or MockBrokerAdapter()
        broker_dispatch = orchestrate_broker_dispatch(broker_open, entrypoint, broker)
        broker_review = review_broker_dispatch_result(broker_dispatch)
        result_record_policy = evaluate_execution_broker_result_record_readiness(
            broker_review,
            confirmations={"manual_result_record_confirmed": True},
            environment_flags={"result_record_enabled": True, "runtime_recording_enabled": True},
        )
        result_record = orchestrate_broker_result_recording(result_record_policy, broker_review)
        runtime_policy = evaluate_execution_runtime_status_update_readiness(
            result_record,
            confirmations={"manual_runtime_status_update_confirmed": True},
            environment_flags={"runtime_status_update_enabled": True, "runtime_execution_state_enabled": True},
        )
        runtime_update = orchestrate_runtime_status_update(runtime_policy, result_record)
        queue_policy = evaluate_execution_queue_status_update_readiness(
            runtime_update,
            confirmations={"manual_queue_status_update_confirmed": True},
            environment_flags={"queue_status_update_enabled": True, "queue_execution_state_enabled": True},
        )
        queue_update = orchestrate_queue_status_update(queue_policy, runtime_update)
        lock_policy = evaluate_execution_lock_release_readiness(
            queue_update,
            confirmations={"manual_lock_release_confirmed": True},
            environment_flags={"lock_release_enabled": True, "runtime_lock_state_enabled": True},
        )
        lock_release = orchestrate_lock_release(lock_policy, queue_update, lock_releaser)
        return review_post_execution(lock_release)

    def test_normal_preview_only_pipeline_reaches_execution_complete(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        result = self._run_complete_pipeline()

        self.assertEqual("EXECUTION_COMPLETED", result["status"])
        self.assertTrue(result["execution_completed"])
        self.assertEqual("EXECUTION_COMPLETE", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["gui_update_called"])
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})

    def test_final_send_gate_blocked_stops_entrypoint_broker_and_result_steps(self) -> None:
        final_readiness = self._run_until_final_send_gate_readiness(guard=self._guard(operator_confirmed=False))

        self.assertEqual("BLOCKED", final_readiness["status"])
        with mock.patch("execution_send_order_entrypoint_orchestrator.orchestrate_send_order_entrypoint") as entrypoint, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker, \
            mock.patch("execution_broker_result_recorder_orchestrator.orchestrate_broker_result_recording") as recorder:
            self.assertEqual("BLOCKED", final_readiness["status"])

        entrypoint.assert_not_called()
        broker.assert_not_called()
        recorder.assert_not_called()

    def test_broker_dispatch_failure_stops_result_record_runtime_queue_and_lock_steps(self) -> None:
        broker = MockBrokerAdapter(raises=True)
        queue_review = review_execution_queue_committed(self._queue_commit_result())
        send_preview = adapt_queue_review_to_send_order_preview(queue_review)
        final_readiness = evaluate_execution_final_send_gate_readiness(send_preview, self._guard(), self._context())
        final_input = adapt_final_send_gate_readiness_to_input(final_readiness, self._guard(), self._context())
        final_orchestrator = orchestrate_final_send_gate_preview(final_input)
        final_open = evaluate_execution_final_send_gate_open_policy(
            final_orchestrator,
            {"manual_final_send_gate_call_confirmed": True},
            {"final_send_gate_call_enabled": True},
        )
        final_call = call_final_send_gate_after_open_policy(final_open, final_orchestrator)
        final_call["final_send_gate_input"] = copy.deepcopy(final_orchestrator["final_send_gate_input"])
        entrypoint_open = evaluate_execution_send_order_entrypoint_open_policy(
            final_call,
            {"manual_send_order_entrypoint_confirmed": True},
            {"send_order_entrypoint_enabled": True, "real_send_order_enabled": True},
        )
        entrypoint = orchestrate_send_order_entrypoint(entrypoint_open, final_call)
        broker_open = evaluate_execution_broker_dispatch_open_policy(
            entrypoint,
            {"manual_broker_dispatch_confirmed": True},
            {
                "broker_dispatch_enabled": True,
                "real_broker_dispatch_enabled": True,
                "kiwoom_connected": True,
                "account_selected": True,
                "real_trade_enabled": True,
            },
        )
        broker_dispatch = orchestrate_broker_dispatch(broker_open, entrypoint, broker)
        broker_review = review_broker_dispatch_result(broker_dispatch)

        self.assertEqual("BLOCKED", broker_dispatch["status"])
        self.assertEqual("BLOCKED", broker_review["status"])
        with mock.patch("execution_broker_result_recorder_orchestrator.orchestrate_broker_result_recording") as recorder, \
            mock.patch("execution_runtime_status_update_orchestrator.orchestrate_runtime_status_update") as runtime_update, \
            mock.patch("execution_queue_status_update_orchestrator.orchestrate_queue_status_update") as queue_update, \
            mock.patch("execution_lock_release_orchestrator.orchestrate_lock_release") as lock_release:
            self.assertEqual("BLOCKED", broker_review["status"])

        recorder.assert_not_called()
        runtime_update.assert_not_called()
        queue_update.assert_not_called()
        lock_release.assert_not_called()

    def test_lock_release_failure_does_not_complete_post_execution_review(self) -> None:
        result = self._run_complete_pipeline(lock_releaser=lambda queue_update: {"released": False, "issues": ["LOCK_HELD"]})

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_completed"])
        self.assertNotEqual("EXECUTION_COMPLETE", result["next_stage"])

    def test_input_objects_are_not_mutated_by_preview_pipeline(self) -> None:
        queue_commit = self._queue_commit_result()
        original = copy.deepcopy(queue_commit)

        result = self._run_complete_pipeline(queue_commit_result=queue_commit)
        result["lock_release_record"]["order_id"] = "MUTATED_RESULT"

        self.assertEqual("EXECUTION_COMPLETED", result["status"])
        self.assertEqual(original, queue_commit)

    def test_no_runtime_queue_rules_gui_commit_or_direct_kiwoom_side_effects(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        with mock.patch("execution_runtime_commit_service.commit_execution_runtime", create=True) as runtime_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("execution_controller.build_kiwoom_order_request") as kiwoom_request:
            result = self._run_complete_pipeline()

        self.assertEqual("EXECUTION_COMPLETED", result["status"])
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        kiwoom_request.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
