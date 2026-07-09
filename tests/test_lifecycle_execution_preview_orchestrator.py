# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from lifecycle_execution_preview_orchestrator import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_execution_preview_orchestrator,
)


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


def _ready_readiness_gate_preview(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "EXECUTION_READINESS_GATE_READY",
        "preview_only": True,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "readiness_check_preview": {"ready": True, "preview_only": True},
        "execution_gate_preview": {"gate_open": False, "preview_only": True},
        "final_readiness_decision": {
            "approved": True,
            "execution_allowed": False,
            "execution_started": False,
            "execution_completed": False,
            "preview_only": True,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _blocked_result(step_name: str) -> dict[str, object]:
    return {
        "status": STATUS_BLOCKED,
        "preview_only": True,
        "issues": ["patched {} blocked".format(step_name)],
        "warnings": [],
    }


def _invalid_result(step_name: str) -> dict[str, object]:
    return {
        "status": STATUS_INVALID,
        "preview_only": True,
        "issues": ["patched {} invalid".format(step_name)],
        "warnings": [],
    }


class LifecycleExecutionPreviewOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_full_ready_chain_builds_orchestrator_ready(self) -> None:
        result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_PREVIEW_ORCHESTRATOR", result["preview_type"])
        self.assertTrue(result["preview_only"])
        self.assertEqual(11, len(result["orchestrator_steps"]))
        self.assertEqual("", result["failed_step"])
        self.assertTrue(result["final_orchestrator_decision"]["approved"])
        self.assertTrue(result["orchestrator_summary"]["completed_steps"], 11)

    def test_intermediate_blocked_stops_subsequent_steps(self) -> None:
        with mock.patch(
            "lifecycle_execution_preview_orchestrator.build_execution_sendorder_result_review_preview",
            return_value=_blocked_result("sendorder_result_review_preview"),
        ):
            result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        self.assertEqual(STATUS_BLOCKED, result["status"])
        # Steps 1-7 executed, step 7 blocked, steps 8-11 not called.
        self.assertEqual(7, len(result["orchestrator_steps"]))
        self.assertEqual("sendorder_result_review_preview", result["failed_step"])
        self.assertFalse(result["final_orchestrator_decision"]["approved"])
        self.assertEqual(
            "sendorder_result_review_preview", result["orchestrator_summary"]["blocked_step"]
        )
        step_names = [step["step_name"] for step in result["orchestrator_steps"]]
        self.assertNotIn("execution_dispatcher_preview", step_names)

    def test_intermediate_invalid_stops_subsequent_steps(self) -> None:
        with mock.patch(
            "lifecycle_execution_preview_orchestrator.build_execution_sendorder_contract_preview",
            return_value=_invalid_result("sendorder_contract_preview"),
        ):
            result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        self.assertEqual(STATUS_INVALID, result["status"])
        # Steps 1-5 executed, step 5 invalid, steps 6-11 not called.
        self.assertEqual(5, len(result["orchestrator_steps"]))
        self.assertEqual("sendorder_contract_preview", result["failed_step"])
        self.assertFalse(result["final_orchestrator_decision"]["approved"])
        self.assertEqual(
            "sendorder_contract_preview", result["orchestrator_summary"]["invalid_step"]
        )
        step_names = [step["step_name"] for step in result["orchestrator_steps"]]
        self.assertNotIn("sendorder_call_preview", step_names)

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_preview_orchestrator(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])
        self.assertEqual("execution_transaction_contract", none_result["failed_step"])

        empty_result = build_execution_preview_orchestrator({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_orchestrator_steps_structure(self) -> None:
        result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        for index, step in enumerate(result["orchestrator_steps"], start=1):
            self.assertEqual(index, step["step_index"])
            self.assertTrue(step["step_name"])
            self.assertTrue(step["status"])
            self.assertTrue(step["preview_only"])
            self.assertTrue(step["completed"])
            self.assertFalse(step["blocked"])
            self.assertFalse(step["invalid"])

    def test_orchestrator_summary_structure(self) -> None:
        result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        summary = result["orchestrator_summary"]
        self.assertEqual(11, summary["total_steps"])
        self.assertEqual(11, summary["completed_steps"])
        self.assertEqual("", summary["blocked_step"])
        self.assertEqual("", summary["invalid_step"])
        self.assertEqual(STATUS_READY, summary["final_status"])
        self.assertTrue(summary["preview_only"])

    def test_final_orchestrator_decision_structure(self) -> None:
        result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        decision = result["final_orchestrator_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["runtime_write"])
        self.assertFalse(decision["send_order_called"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["execution_completed"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["dispatch_started"])
        self.assertFalse(result["dispatch_completed"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["send_order_result_recorded"])
        self.assertFalse(result["recorder_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])

    def test_input_is_not_mutated(self) -> None:
        readiness_gate = _ready_readiness_gate_preview()
        original = copy.deepcopy(readiness_gate)

        build_execution_preview_orchestrator(readiness_gate)

        self.assertEqual(original, readiness_gate)

    def test_protected_files_unchanged(self) -> None:
        build_execution_preview_orchestrator(_ready_readiness_gate_preview())
        with mock.patch(
            "lifecycle_execution_preview_orchestrator.build_execution_sendorder_result_review_preview",
            return_value=_blocked_result("sendorder_result_review_preview"),
        ):
            build_execution_preview_orchestrator(_ready_readiness_gate_preview())
        with mock.patch(
            "lifecycle_execution_preview_orchestrator.build_execution_sendorder_contract_preview",
            return_value=_invalid_result("sendorder_contract_preview"),
        ):
            build_execution_preview_orchestrator(_ready_readiness_gate_preview())

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
