# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import execution_queue_commit_service
from execution_queue_commit_service import commit_execution_queue_manually


class ExecutionQueueCommitServiceTest(unittest.TestCase):
    def _write_preview_result(self) -> dict:
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": {
                "id": "ORDER_QUEUED_ORDER_1",
                "status": "ORDER_QUEUED",
            },
        }

    def _success_commit_result(self) -> dict:
        return {
            "committed": True,
            "write_stage": "order_queued_record_committed",
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "changed": True,
            "blocked_reasons": [],
            "warnings": [],
        }

    def _failed_commit_result(self) -> dict:
        return {
            "committed": False,
            "write_stage": "duplicate",
            "next_stage": "BLOCKED",
            "changed": False,
            "blocked_reasons": ["duplicate request_hash"],
            "warnings": [],
        }

    def _ready_policy(self) -> dict:
        return {
            "policy_type": "EXECUTION_QUEUE_COMMIT_READINESS_POLICY",
            "status": "READY_TO_COMMIT_QUEUE",
            "queue_commit_allowed": True,
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "identity_checks": {},
            "required_confirmations": {},
            "issues": [],
            "warnings": [],
        }

    def _blocked_policy(self) -> dict:
        policy = self._ready_policy()
        policy.update(
            {
                "status": "BLOCKED",
                "queue_commit_allowed": False,
                "issues": ["RUNTIME_COMMIT_NOT_COMMITTED"],
            }
        )
        return policy

    def _invalid_policy(self) -> dict:
        policy = self._ready_policy()
        policy.update(
            {
                "status": "INVALID",
                "queue_commit_allowed": False,
                "issues": ["MALFORMED_RUNTIME_COMMIT_RESULT"],
            }
        )
        return policy

    def test_without_confirmation_is_blocked(self) -> None:
        result = commit_execution_queue_manually(
            self._write_preview_result(),
            "temp_order_queue.json",
            context={},
        )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("operator_confirmation", result["commit_stage"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertIsNone(result["commit_result"])
        self.assertIn("manual queue write confirmation is required", result["blocked_reasons"])

    def test_without_confirmation_does_not_call_writer(self) -> None:
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context={},
            )

        self.assertFalse(result["manual_commit"])
        writer.assert_not_called()

    def test_manual_queue_write_confirmed_calls_writer(self) -> None:
        context = {"manual_queue_write_confirmed": True}

        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ) as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context=context,
                backup=False,
            )

        self.assertTrue(result["manual_commit"])
        self.assertEqual("committed", result["commit_stage"])
        self.assertEqual("QUEUE_COMMITTED_REVIEW_REQUIRED", result["next_stage"])
        writer.assert_called_once_with(
            self._write_preview_result(),
            "temp_order_queue.json",
            backup=False,
            context=context,
        )

    def test_operator_confirmed_for_queue_write_calls_writer(self) -> None:
        context = {"operator_confirmed_for_queue_write": True}

        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ) as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context=context,
            )

        self.assertTrue(result["manual_commit"])
        writer.assert_called_once()

    def test_existing_operator_confirmed_alone_is_blocked(self) -> None:
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context={"operator_confirmed": True},
            )

        self.assertFalse(result["manual_commit"])
        writer.assert_not_called()

    def test_runtime_path_requires_runtime_confirmation(self) -> None:
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "runtime/order_queue.json",
                context={"manual_queue_write_confirmed": True},
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("runtime_operator_confirmation", result["commit_stage"])
        self.assertIn("manual runtime queue write confirmation is required", result["blocked_reasons"])
        writer.assert_not_called()

    def test_runtime_path_with_runtime_confirmation_without_policy_is_blocked(self) -> None:
        context = {
            "manual_queue_write_confirmed": True,
            "manual_runtime_queue_write_confirmed": True,
        }
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "runtime/order_queue.json",
                context=context,
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("queue_commit_readiness_policy", result["commit_stage"])
        self.assertIn("queue commit readiness policy is required", result["blocked_reasons"])
        writer.assert_not_called()

    def test_runtime_path_with_ready_policy_and_final_confirmation_calls_writer(self) -> None:
        context = {
            "manual_queue_write_confirmed": True,
            "manual_runtime_queue_write_confirmed": True,
        }
        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ) as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "runtime/order_queue.json",
                context=context,
                queue_commit_readiness_policy_result=self._ready_policy(),
                manual_queue_commit_after_runtime_confirmed=True,
            )

        self.assertTrue(result["manual_commit"])
        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual("QUEUE_COMMITTED_REVIEW_REQUIRED", result["next_stage"])
        writer.assert_called_once()

    def test_runtime_path_ready_policy_without_final_confirmation_blocked(self) -> None:
        context = {
            "manual_queue_write_confirmed": True,
            "manual_runtime_queue_write_confirmed": True,
        }
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "runtime/order_queue.json",
                context=context,
                queue_commit_readiness_policy_result=self._ready_policy(),
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("queue_commit_after_runtime_confirmation", result["commit_stage"])
        self.assertIn("manual queue commit after runtime confirmation is required", result["blocked_reasons"])
        writer.assert_not_called()

    def test_runtime_path_policy_blocked_invalid_or_malformed_blocks(self) -> None:
        context = {
            "manual_queue_write_confirmed": True,
            "manual_runtime_queue_write_confirmed": True,
        }
        cases = [
            (self._blocked_policy(), "BLOCKED", "RUNTIME_COMMIT_NOT_COMMITTED"),
            (self._invalid_policy(), "INVALID", "MALFORMED_RUNTIME_COMMIT_RESULT"),
            ({"bad": True}, "INVALID", "queue commit readiness policy type is invalid"),
        ]

        for policy, status, reason in cases:
            with self.subTest(status=status), mock.patch.object(
                execution_queue_commit_service, "commit_execution_queue_write"
            ) as writer:
                result = commit_execution_queue_manually(
                    self._write_preview_result(),
                    "runtime/order_queue.json",
                    context=context,
                    queue_commit_readiness_policy_result=policy,
                    manual_queue_commit_after_runtime_confirmed=True,
                )

                self.assertFalse(result["manual_commit"])
                self.assertEqual(status, result["status"])
                self.assertIn(reason, result["blocked_reasons"][0])
                writer.assert_not_called()

    def test_runtime_path_queue_commit_failure_marks_after_runtime_issue(self) -> None:
        context = {
            "manual_queue_write_confirmed": True,
            "manual_runtime_queue_write_confirmed": True,
        }
        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._failed_commit_result(),
        ) as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "runtime/order_queue.json",
                context=context,
                queue_commit_readiness_policy_result=self._ready_policy(),
                manual_queue_commit_after_runtime_confirmed=True,
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("duplicate request_hash", result["blocked_reasons"])
        self.assertIn("QUEUE_COMMIT_FAILED_AFTER_RUNTIME_COMMIT", result["blocked_reasons"])
        writer.assert_called_once()

    def test_missing_queue_path_is_blocked(self) -> None:
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                None,
                context={"manual_queue_write_confirmed": True},
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("queue_path", result["commit_stage"])
        self.assertIn("queue_path is required", result["blocked_reasons"])
        writer.assert_not_called()

    def test_successful_commit_result_is_wrapped(self) -> None:
        commit_result = self._success_commit_result()
        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=commit_result,
        ):
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context={"manual_queue_write_confirmed": True},
            )

        self.assertTrue(result["manual_commit"])
        self.assertEqual("committed", result["commit_stage"])
        self.assertEqual(commit_result, result["commit_result"])
        self.assertEqual([], result["blocked_reasons"])

    def test_failed_commit_result_is_wrapped(self) -> None:
        commit_result = self._failed_commit_result()
        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=commit_result,
        ):
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context={"manual_queue_write_confirmed": True},
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("duplicate", result["commit_stage"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertEqual(commit_result, result["commit_result"])
        self.assertIn("duplicate request_hash", result["blocked_reasons"])

    def test_send_order_is_not_called(self) -> None:
        with (
            mock.patch.object(
                execution_queue_commit_service,
                "commit_execution_queue_write",
                return_value=self._success_commit_result(),
            ),
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = commit_execution_queue_manually(
                self._write_preview_result(),
                "temp_order_queue.json",
                context={"manual_queue_write_confirmed": True},
            )

        self.assertTrue(result["manual_commit"])
        send_order_stub.assert_not_called()

    def test_gui_and_timer_are_not_referenced_by_service_module(self) -> None:
        module_text = execution_queue_commit_service.__loader__.get_source(
            execution_queue_commit_service.__name__
        )

        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("preview_execution_for_real_ready_order_manual", module_text)

    def test_input_dict_is_not_mutated(self) -> None:
        write_preview = self._write_preview_result()
        context = {"manual_queue_write_confirmed": True}
        original_preview = deepcopy(write_preview)
        original_context = deepcopy(context)

        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ):
            commit_execution_queue_manually(
                write_preview,
                "temp_order_queue.json",
                context=context,
            )

        self.assertEqual(original_preview, write_preview)
        self.assertEqual(original_context, context)


if __name__ == "__main__":
    unittest.main()
