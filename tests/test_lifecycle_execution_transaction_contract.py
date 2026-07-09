# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_execution_transaction_contract import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_execution_transaction_contract,
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
        "preview_type": "LIFECYCLE_RUNTIME_EXECUTION_READINESS_GATE_PREVIEW",
        "status": "EXECUTION_READINESS_GATE_READY",
        "preview_only": True,
        "execution_allowed": False,
        "execution_started": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "commit_executed": False,
        "sync_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "readiness_check_preview": {
            "readiness_required": True,
            "readiness_checked": False,
            "ready_for_execution_layer": True,
            "preview_only": True,
        },
        "execution_gate_preview": {
            "gate_type": "RUNTIME_EXECUTION_READINESS_GATE",
            "execution_allowed": False,
            "execution_started": False,
            "preview_only": True,
        },
        "approval_requirement_preview": {
            "operator_approval_required": True,
            "runtime_review_required": True,
            "execution_token_required": True,
            "preview_only": True,
        },
        "blocking_reason_preview": {
            "blocked": False,
            "invalid": False,
            "blocking_reasons": [],
            "invalid_reasons": [],
            "preview_only": True,
        },
        "final_readiness_decision": {
            "approved": True,
            "blocked": False,
            "invalid": False,
            "execution_allowed": False,
            "execution_started": False,
            "preview_only": True,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleExecutionTransactionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_readiness_gate_builds_ready_contract(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["execution_validation_contract"]["validation_ready"])
        self.assertTrue(result["final_execution_contract"]["approved"])

    def test_blocked_readiness_gate_builds_blocked_contract(self) -> None:
        result = build_execution_transaction_contract(
            _ready_readiness_gate_preview(status="BLOCKED", issues=["blocked upstream"])
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["execution_validation_contract"]["validation_ready"])
        self.assertTrue(result["final_execution_contract"]["blocked"])

    def test_invalid_and_malformed_readiness_gate_are_invalid(self) -> None:
        invalid = build_execution_transaction_contract(_ready_readiness_gate_preview(status="INVALID"))
        malformed = build_execution_transaction_contract({"status": "EXECUTION_READINESS_GATE_READY"})

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertTrue(invalid["final_execution_contract"]["invalid"])
        self.assertTrue(malformed["final_execution_contract"]["invalid"])

    def test_execution_transaction_contract_shape(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())
        contract = result["execution_transaction_contract"]

        self.assertEqual("v1", contract["contract_version"])
        self.assertEqual("RUNTIME_TO_EXECUTION_TRANSACTION", contract["transaction_type"])
        self.assertEqual("PREVIEW_ONLY", contract["execution_mode"])
        self.assertTrue(contract["preview_only"])

    def test_execution_input_contract_shape(self) -> None:
        result = build_execution_transaction_contract(
            _ready_readiness_gate_preview(),
            {
                "runtime_payload": {"runtime_id": "RUNTIME_1"},
                "position_payload": {"position_id": "POSITION_1"},
                "balance_payload": {"balance_id": "BALANCE_1"},
                "audit_payload": {"audit_id": "AUDIT_1"},
            },
        )
        contract = result["execution_input_contract"]

        self.assertEqual("RUNTIME_1", contract["runtime_payload"]["runtime_id"])
        self.assertEqual("POSITION_1", contract["position_payload"]["position_id"])
        self.assertEqual("BALANCE_1", contract["balance_payload"]["balance_id"])
        self.assertEqual("AUDIT_1", contract["audit_payload"]["audit_id"])
        self.assertTrue(contract["preview_only"])

    def test_execution_gate_contract_shape(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())
        gate = result["execution_gate_contract"]

        self.assertFalse(gate["execution_gate_open"])
        self.assertTrue(gate["operator_review_required"])
        self.assertTrue(gate["execution_token_required"])
        self.assertTrue(gate["approval_required"])
        self.assertTrue(gate["preview_only"])

    def test_execution_validation_contract_shape(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())
        validation = result["execution_validation_contract"]

        self.assertTrue(validation["validation_ready"])
        self.assertTrue(validation["validation_items"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_execution_route_contract_shape(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())
        route = result["execution_route_contract"]

        for key in ("execution_engine", "broker_adapter", "order_router"):
            self.assertTrue(route[key]["planned"])
            self.assertFalse(route[key]["connected"])
            self.assertTrue(route[key]["preview_only"])
        self.assertTrue(route["preview_only"])

    def test_final_execution_contract_shape(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())
        final = result["final_execution_contract"]

        self.assertTrue(final["approved"])
        self.assertFalse(final["execution_allowed"])
        self.assertFalse(final["execution_started"])
        self.assertFalse(final["execution_completed"])
        self.assertTrue(final["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_transaction_contract(_ready_readiness_gate_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["execution_completed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["broker_connected"])
        self.assertFalse(result["order_router_connected"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])

    def test_input_is_not_mutated(self) -> None:
        readiness = _ready_readiness_gate_preview()
        original = copy.deepcopy(readiness)

        build_execution_transaction_contract(readiness)

        self.assertEqual(original, readiness)

    def test_protected_files_hash_unchanged(self) -> None:
        build_execution_transaction_contract(_ready_readiness_gate_preview())
        build_execution_transaction_contract(_ready_readiness_gate_preview(status="BLOCKED"))
        build_execution_transaction_contract(_ready_readiness_gate_preview(status="INVALID"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
