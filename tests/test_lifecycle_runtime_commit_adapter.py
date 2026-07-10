# -*- coding: utf-8 -*-
"""Tests for Lifecycle -> M6 runtime commit adapter."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lifecycle_runtime_commit_adapter import (
    ADAPTER_TYPE,
    adapt_and_execute_lifecycle_runtime_commit,
)


def _lifecycle_request(**overrides):
    request = {
        "lifecycle_id": "life-001",
        "commit_id": "commit-001",
        "transaction_id": "tx-001",
        "requested_action": "RUNTIME_COMMIT",
        "source_stage": "LIFECYCLE_COMMIT",
        "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
        "preview_only": True,
        "metadata": {"operator": "tester"},
    }
    request.update(overrides)
    return request


def _adapter_inputs(**overrides):
    values = {
        "lifecycle_commit_request": _lifecycle_request(),
        "gate_result": {"gate_status": "APPROVED", "commit_id": "commit-001"},
        "transaction_manifest": {
            "commit_id": "commit-001",
            "transaction_id": "tx-001",
            "execution_plan_hash": "plan-hash-001",
        },
        "storage_plan": {"storage_status": "READY", "storage_root": "unused", "commit_id": "commit-001"},
        "guard_plan": {"guard_status": "READY", "owner_id": "consumer-001"},
        "token_storage_plan": {"storage_status": "READY", "token_id": "token-001"},
        "expected_targets": {"target.json": {"old": "value"}},
        "new_targets": {"target.json": {"new": "value"}},
        "consumer_id": "consumer-001",
    }
    values.update(overrides)
    return values


def _runtime_result(status):
    return {
        "execute_status": status,
        "transaction_status": status,
        "commit_id": "commit-001",
        "transaction_id": "tx-001",
        "issues": [],
        "warnings": [],
    }


class LifecycleRuntimeCommitAdapterTests(unittest.TestCase):
    def _run_with_status(self, status):
        calls = []

        def executor(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            return _runtime_result(status)

        result = adapt_and_execute_lifecycle_runtime_commit(
            **_adapter_inputs(),
            executor=executor,
        )
        self.assertEqual(1, len(calls))
        return result, calls[0]

    def assert_adapter_safety_flags(self, result):
        self.assertFalse(result["direct_write_called"])
        self.assertFalse(result["backup_called_by_adapter"])
        self.assertFalse(result["rollback_called_by_adapter"])
        self.assertFalse(result["verify_called_by_adapter"])
        self.assertFalse(result["lock_called_by_adapter"])
        self.assertFalse(result["token_called_by_adapter"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["sqlite_write"])
        self.assertFalse(result["rules_write"])

    def test_committed_mapping(self):
        result, _ = self._run_with_status("COMMITTED")

        self.assertEqual(ADAPTER_TYPE, result["adapter_type"])
        self.assertEqual("COMMITTED", result["adapter_status"])
        self.assertEqual("COMMITTED", result["lifecycle_commit_result"]["status"])
        self.assertTrue(result["executor_called"])
        self.assertFalse(result["legacy_executor_called"])
        self.assert_adapter_safety_flags(result)

    def test_blocked_mapping(self):
        result, _ = self._run_with_status("BLOCKED")

        self.assertEqual("BLOCKED", result["adapter_status"])
        self.assertEqual("BLOCKED", result["lifecycle_commit_result"]["status"])

    def test_invalid_mapping(self):
        result, _ = self._run_with_status("INVALID")

        self.assertEqual("INVALID", result["adapter_status"])
        self.assertEqual("INVALID", result["lifecycle_commit_result"]["status"])

    def test_aborted_mapping(self):
        result, _ = self._run_with_status("ABORTED")

        self.assertEqual("ABORTED", result["adapter_status"])
        self.assertEqual("ABORTED", result["lifecycle_commit_result"]["status"])

    def test_rolled_back_mapping(self):
        result, _ = self._run_with_status("ROLLED_BACK")

        self.assertEqual("ROLLED_BACK", result["adapter_status"])
        self.assertEqual("ROLLED_BACK", result["lifecycle_commit_result"]["status"])

    def test_manual_restore_required_maps_to_review_required(self):
        result, _ = self._run_with_status("MANUAL_RESTORE_REQUIRED")

        self.assertEqual("REVIEW_REQUIRED", result["adapter_status"])
        self.assertEqual("REVIEW_REQUIRED", result["lifecycle_commit_result"]["status"])
        self.assertEqual("MANUAL_RESTORE_REQUIRED", result["runtime_commit_result"]["execute_status"])

    def test_unknown_runtime_status_maps_to_invalid(self):
        result, _ = self._run_with_status("SOMETHING_NEW")

        self.assertEqual("INVALID", result["adapter_status"])
        self.assertTrue(any("unknown runtime execute_status" in issue for issue in result["issues"]))

    def test_executor_called_once_and_receives_expected_inputs(self):
        result, call = self._run_with_status("COMMITTED")
        inputs = _adapter_inputs()

        self.assertTrue(result["executor_called"])
        self.assertEqual(inputs["gate_result"], call["gate_result"])
        self.assertEqual(inputs["transaction_manifest"], call["transaction_manifest"])
        self.assertEqual(inputs["storage_plan"], call["storage_plan"])
        self.assertEqual(inputs["guard_plan"], call["guard_plan"])
        self.assertEqual(inputs["token_storage_plan"], call["token_storage_plan"])
        self.assertEqual(inputs["expected_targets"], call["expected_targets"])
        self.assertEqual(inputs["new_targets"], call["new_targets"])
        self.assertEqual(inputs["consumer_id"], call["consumer_id"])

    def test_input_dicts_are_not_mutated_even_when_executor_mutates_arguments(self):
        inputs = _adapter_inputs()
        before = copy.deepcopy(inputs)

        def mutating_executor(**kwargs):
            kwargs["gate_result"]["gate_status"] = "MUTATED"
            kwargs["transaction_manifest"]["commit_id"] = "mutated"
            kwargs["storage_plan"]["storage_root"] = "mutated"
            kwargs["guard_plan"]["guard_status"] = "MUTATED"
            kwargs["token_storage_plan"]["token_id"] = "mutated"
            kwargs["expected_targets"]["target.json"]["old"] = "mutated"
            kwargs["new_targets"]["target.json"]["new"] = "mutated"
            return _runtime_result("COMMITTED")

        adapt_and_execute_lifecycle_runtime_commit(**inputs, executor=mutating_executor)

        self.assertEqual(before, inputs)

    def test_missing_required_lifecycle_field_does_not_call_executor(self):
        calls = []
        request = _lifecycle_request()
        request.pop("transaction_id")

        result = adapt_and_execute_lifecycle_runtime_commit(
            **_adapter_inputs(lifecycle_commit_request=request),
            executor=lambda **kwargs: calls.append(kwargs) or _runtime_result("COMMITTED"),
        )

        self.assertEqual("INVALID", result["adapter_status"])
        self.assertFalse(result["executor_called"])
        self.assertEqual([], calls)
        self.assertTrue(any("missing lifecycle field: transaction_id" in issue for issue in result["issues"]))

    def test_malformed_lifecycle_request_does_not_call_executor(self):
        calls = []

        result = adapt_and_execute_lifecycle_runtime_commit(
            **_adapter_inputs(lifecycle_commit_request="bad"),
            executor=lambda **kwargs: calls.append(kwargs) or _runtime_result("COMMITTED"),
        )

        self.assertEqual("INVALID", result["adapter_status"])
        self.assertFalse(result["executor_called"])
        self.assertEqual([], calls)

    def test_executor_exception_is_sanitized(self):
        def executor(**kwargs):
            raise RuntimeError("secret details that should not leak as traceback")

        result = adapt_and_execute_lifecycle_runtime_commit(
            **_adapter_inputs(),
            executor=executor,
        )

        self.assertEqual("ABORTED", result["adapter_status"])
        self.assertTrue(result["executor_called"])
        self.assertEqual(["executor exception: RuntimeError"], result["issues"])
        self.assertNotIn("Traceback", repr(result))
        self.assertNotIn("secret details", repr(result))

    def test_adapter_does_not_create_files_when_stub_executor_is_used(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            before = sorted(root.rglob("*"))

            result = adapt_and_execute_lifecycle_runtime_commit(
                **_adapter_inputs(
                    storage_plan={"storage_status": "READY", "storage_root": str(root)},
                    token_storage_plan={"storage_status": "READY", "storage_root": str(root), "token_id": "token-001"},
                    guard_plan={"guard_status": "READY", "storage_root": str(root)},
                ),
                executor=lambda **kwargs: _runtime_result("COMMITTED"),
            )

            self.assertEqual("COMMITTED", result["adapter_status"])
            self.assertEqual(before, sorted(root.rglob("*")))
            self.assert_adapter_safety_flags(result)

    def test_legacy_executor_is_not_called_when_default_executor_is_resolved(self):
        with mock.patch("runtime_commit_real_executor.execute_runtime_commit", return_value=_runtime_result("COMMITTED")) as m6_executor, \
             mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as legacy_executor:
            result = adapt_and_execute_lifecycle_runtime_commit(**_adapter_inputs())

        self.assertEqual("COMMITTED", result["adapter_status"])
        self.assertEqual(1, m6_executor.call_count)
        legacy_executor.assert_not_called()
        self.assertFalse(result["legacy_executor_called"])

    def test_default_executor_import_does_not_call_legacy_executor(self):
        with mock.patch("runtime_commit_real_executor.execute_runtime_commit", return_value=_runtime_result("BLOCKED")) as m6_executor:
            result = adapt_and_execute_lifecycle_runtime_commit(**_adapter_inputs())

        self.assertEqual("BLOCKED", result["adapter_status"])
        self.assertEqual(1, m6_executor.call_count)
        self.assertFalse(result["legacy_executor_called"])


if __name__ == "__main__":
    unittest.main()
