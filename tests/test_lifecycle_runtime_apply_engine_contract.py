# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_apply_engine_contract import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_runtime_apply_engine_contract,
)
from lifecycle_runtime_apply_orchestrator_preview import build_runtime_apply_orchestrator_preview
from lifecycle_runtime_atomic_apply_preview import build_runtime_atomic_apply_preview
from lifecycle_runtime_commit_executor_preview import build_runtime_commit_executor_preview
from lifecycle_runtime_state_apply_controller_preview import build_runtime_state_apply_controller_preview
from lifecycle_runtime_state_writer_preview import build_runtime_state_writer_preview
from lifecycle_runtime_state_validator_preview import build_runtime_state_validator_preview


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


def _ready_reconciliation(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "RECONCILIATION_PREVIEW_READY",
        "preview_only": True,
        "mismatch_candidates": [
            {
                "mismatch_id": "MISMATCH_1",
                "order_id": "ORDER_EXECUTOR_1",
                "field": "quantity",
                "runtime_value": 1,
                "broker_value": 2,
                "review_required": False,
            },
            {
                "mismatch_id": "MISMATCH_2",
                "order_id": "ORDER_EXECUTOR_1",
                "field": "balance",
                "runtime_value": 1000,
                "broker_value": 900,
                "review_required": False,
            },
        ],
        "reconciliation_actions": [
            {
                "action_id": "ACTION_1",
                "order_id": "ORDER_EXECUTOR_1",
                "action_type": "APPLY_RECONCILIATION_PREVIEW",
                "runtime_write": False,
                "broker_write": False,
                "reconciliation_executed": False,
            }
        ],
        "review_required_items": [],
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _ready_orchestrator_preview(**overrides: object) -> dict[str, object]:
    executor = build_runtime_commit_executor_preview(
        _ready_reconciliation(), {"generated_at": "2026-07-09 09:00:00"}
    )
    atomic = build_runtime_atomic_apply_preview(executor, {"generated_at": "2026-07-09 09:00:00"})
    controller = build_runtime_state_apply_controller_preview(atomic, {"generated_at": "2026-07-09 09:00:00"})
    writer = build_runtime_state_writer_preview(controller, {"generated_at": "2026-07-09 09:00:00"})
    validator = build_runtime_state_validator_preview(writer, {"generated_at": "2026-07-09 09:00:00"})
    result = build_runtime_apply_orchestrator_preview(validator, {"generated_at": "2026-07-09 09:00:00"})
    result.update(overrides)
    return result


class LifecycleRuntimeApplyEngineContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_orchestrator_preview_builds_engine_contract(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_RUNTIME_APPLY_ENGINE_CONTRACT", result["preview_type"])

    def test_apply_engine_contract_is_built(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview())

        contract = result["apply_engine_contract"]
        self.assertTrue(contract)
        self.assertEqual(STATUS_READY, contract["status"])
        self.assertTrue(contract["ready_for_engine"])
        self.assertEqual("", contract["blocked_reason"])
        self.assertEqual("", contract["invalid_reason"])
        self.assertTrue(contract["preview_only"])

    def test_engine_input_contract_is_built(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview())

        input_contract = result["engine_input_contract"]
        self.assertTrue(input_contract)
        self.assertTrue(input_contract["runtime_payload_contract"])
        self.assertTrue(input_contract["position_payload_contract"])
        self.assertTrue(input_contract["balance_payload_contract"])
        self.assertTrue(input_contract["backup_contract"])
        self.assertTrue(input_contract["rollback_contract"])
        self.assertTrue(input_contract["preview_only"])

    def test_engine_gate_is_built(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview())

        gate = result["engine_gate"]
        self.assertTrue(gate)
        self.assertTrue(gate["approval_required"])
        self.assertTrue(gate["operator_review_required"])
        self.assertTrue(gate["contract_token_required"])
        self.assertTrue(gate["ready_to_execute"])
        self.assertTrue(gate["preview_only"])

    def test_engine_validation_ready_true(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview())

        validation = result["engine_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["blocked"])
        self.assertFalse(validation["invalid"])
        self.assertFalse(validation["issues"])

    def test_invalid_orchestrator_preview_is_invalid(self) -> None:
        invalid = build_runtime_apply_engine_contract(_ready_orchestrator_preview(status="INVALID"))
        unsupported = build_runtime_apply_engine_contract(_ready_orchestrator_preview(status="SOMETHING_ELSE"))

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["engine_validation"]["ready"])
        self.assertTrue(invalid["engine_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["engine_validation"]["ready"])

    def test_blocked_orchestrator_preview_is_blocked(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview(status="BLOCKED"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["engine_validation"]["ready"])
        self.assertTrue(result["engine_validation"]["blocked"])
        self.assertFalse(result["apply_engine_contract"]["ready_for_engine"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_apply_engine_contract(_ready_orchestrator_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["engine_executed"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_protected_files_unchanged(self) -> None:
        build_runtime_apply_engine_contract(_ready_orchestrator_preview())
        build_runtime_apply_engine_contract(_ready_orchestrator_preview(status="INVALID"))
        build_runtime_apply_engine_contract(_ready_orchestrator_preview(status="BLOCKED"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()