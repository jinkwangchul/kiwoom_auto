# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_execution_transaction_contract import build_execution_transaction_contract
from lifecycle_execution_engine_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_ENGINE_PREVIEW_READY,
    build_execution_engine_preview,
)
from lifecycle_execution_broker_adapter_contract_preview import (
    STATUS_READY,
    STATUS_BLOCKED as ADAPTER_BLOCKED,
    STATUS_INVALID as ADAPTER_INVALID,
    build_execution_broker_adapter_contract_preview,
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


def _ready_engine_preview(**overrides: object) -> dict[str, object]:
    contract = build_execution_transaction_contract(
        _ready_readiness_gate_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    engine_preview = build_execution_engine_preview(contract, {"generated_at": "2026-07-09 09:00:00"})
    engine_preview.update(overrides)
    return engine_preview


class LifecycleExecutionBrokerAdapterContractPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_engine_preview_builds_ready_adapter_contract(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_BROKER_ADAPTER_CONTRACT_PREVIEW", result["preview_type"])

    def test_blocked_engine_preview_is_blocked(self) -> None:
        result = build_execution_broker_adapter_contract_preview(
            _ready_engine_preview(status=STATUS_BLOCKED)
        )

        self.assertEqual(ADAPTER_BLOCKED, result["status"])
        self.assertFalse(result["adapter_safety_validation"]["ready"])
        self.assertTrue(result["adapter_safety_validation"]["blocked"])
        self.assertFalse(result["final_adapter_decision"]["approved"])

    def test_invalid_engine_preview_is_invalid(self) -> None:
        invalid = build_execution_broker_adapter_contract_preview(
            _ready_engine_preview(status=STATUS_INVALID)
        )
        unsupported = build_execution_broker_adapter_contract_preview(
            _ready_engine_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(ADAPTER_INVALID, invalid["status"])
        self.assertFalse(invalid["adapter_safety_validation"]["ready"])
        self.assertTrue(invalid["adapter_safety_validation"]["invalid"])
        self.assertEqual(ADAPTER_INVALID, unsupported["status"])
        self.assertFalse(unsupported["adapter_safety_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_broker_adapter_contract_preview(None)
        self.assertEqual(ADAPTER_INVALID, none_result["status"])

        empty_result = build_execution_broker_adapter_contract_preview({})
        self.assertEqual(ADAPTER_INVALID, empty_result["status"])

    def test_broker_adapter_contract_is_built(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        contract = result["broker_adapter_contract"]
        self.assertTrue(contract)
        self.assertTrue(contract.get("adapter_id"))
        self.assertTrue(contract.get("adapter_name"))
        self.assertTrue(contract["adapter_planned"])
        self.assertFalse(contract["adapter_called"])
        self.assertTrue(contract["preview_only"])

    def test_broker_connection_preview_is_built(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        connection = result["broker_connection_preview"]
        self.assertTrue(connection)
        self.assertTrue(connection["connection_planned"])
        self.assertFalse(connection["broker_connected"])
        self.assertFalse(connection["connection_established"])
        self.assertTrue(connection["preview_only"])

    def test_send_order_contract_preview_is_built(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        send_order = result["send_order_contract_preview"]
        self.assertTrue(send_order)
        self.assertTrue(send_order["send_order_planned"])
        self.assertFalse(send_order["send_order_available"])
        self.assertFalse(send_order["send_order_called"])
        self.assertTrue(send_order["send_order_blocked"])
        self.assertTrue(send_order["preview_only"])

    def test_order_route_candidate_preview_is_built(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        route = result["order_route_candidate_preview"]
        self.assertTrue(route)
        self.assertTrue(route.get("order_route_candidates"))
        self.assertFalse(route["order_route_selected"])
        self.assertFalse(route["order_routed"])
        self.assertTrue(route["preview_only"])

    def test_adapter_safety_validation_ready_true(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        validation = result["adapter_safety_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_adapter_decision_approved_true(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        decision = result["final_adapter_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["broker_connected"])
        self.assertFalse(decision["broker_adapter_called"])
        self.assertFalse(decision["send_order_available"])
        self.assertFalse(decision["send_order_called"])
        self.assertFalse(decision["order_routed"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["execution_started"])
        self.assertFalse(decision["execution_completed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_broker_adapter_contract_preview(_ready_engine_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["broker_connected"])
        self.assertFalse(result["broker_adapter_called"])
        self.assertFalse(result["send_order_available"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["order_routed"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["execution_completed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])

    def test_input_is_not_mutated(self) -> None:
        engine_preview = _ready_engine_preview()
        original = copy.deepcopy(engine_preview)

        build_execution_broker_adapter_contract_preview(engine_preview)

        self.assertEqual(original, engine_preview)

    def test_protected_files_unchanged(self) -> None:
        build_execution_broker_adapter_contract_preview(_ready_engine_preview())
        build_execution_broker_adapter_contract_preview(_ready_engine_preview(status=STATUS_INVALID))
        build_execution_broker_adapter_contract_preview(_ready_engine_preview(status=STATUS_BLOCKED))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
