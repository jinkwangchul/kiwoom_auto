# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_execution_transaction_contract import build_execution_transaction_contract
from lifecycle_execution_engine_preview import (
    STATUS_BLOCKED as ENGINE_BLOCKED,
    STATUS_INVALID as ENGINE_INVALID,
    build_execution_engine_preview,
)
from lifecycle_execution_broker_adapter_contract_preview import (
    STATUS_READY as ADAPTER_READY,
    STATUS_BLOCKED as ADAPTER_BLOCKED,
    STATUS_INVALID as ADAPTER_INVALID,
    build_execution_broker_adapter_contract_preview,
)
from lifecycle_execution_order_router_contract_preview import (
    STATUS_READY,
    STATUS_BLOCKED as ROUTER_BLOCKED,
    STATUS_INVALID as ROUTER_INVALID,
    build_execution_order_router_contract_preview,
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


def _ready_adapter_preview(**overrides: object) -> dict[str, object]:
    contract = build_execution_transaction_contract(
        _ready_readiness_gate_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    engine_preview = build_execution_engine_preview(contract, {"generated_at": "2026-07-09 09:00:00"})
    adapter_preview = build_execution_broker_adapter_contract_preview(
        engine_preview, {"generated_at": "2026-07-09 09:00:00"}
    )
    adapter_preview.update(overrides)
    return adapter_preview


class LifecycleExecutionOrderRouterContractPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_adapter_preview_builds_ready_router_contract(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_ORDER_ROUTER_CONTRACT_PREVIEW", result["preview_type"])

    def test_blocked_adapter_preview_is_blocked(self) -> None:
        result = build_execution_order_router_contract_preview(
            _ready_adapter_preview(status=ADAPTER_BLOCKED)
        )

        self.assertEqual(ROUTER_BLOCKED, result["status"])
        self.assertFalse(result["route_safety_validation"]["ready"])
        self.assertTrue(result["route_safety_validation"]["blocked"])
        self.assertFalse(result["final_router_decision"]["approved"])

    def test_invalid_adapter_preview_is_invalid(self) -> None:
        invalid = build_execution_order_router_contract_preview(
            _ready_adapter_preview(status=ADAPTER_INVALID)
        )
        unsupported = build_execution_order_router_contract_preview(
            _ready_adapter_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(ROUTER_INVALID, invalid["status"])
        self.assertFalse(invalid["route_safety_validation"]["ready"])
        self.assertTrue(invalid["route_safety_validation"]["invalid"])
        self.assertEqual(ROUTER_INVALID, unsupported["status"])
        self.assertFalse(unsupported["route_safety_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_order_router_contract_preview(None)
        self.assertEqual(ROUTER_INVALID, none_result["status"])

        empty_result = build_execution_order_router_contract_preview({})
        self.assertEqual(ROUTER_INVALID, empty_result["status"])

    def test_order_router_contract_is_built(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        contract = result["order_router_contract"]
        self.assertTrue(contract)
        self.assertTrue(contract.get("router_id"))
        self.assertTrue(contract.get("router_name"))
        self.assertTrue(contract["router_planned"])
        self.assertFalse(contract["router_connected"])
        self.assertTrue(contract["preview_only"])

    def test_routing_candidate_preview_is_built(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        routing = result["routing_candidate_preview"]
        self.assertTrue(routing)
        self.assertTrue(routing.get("routing_candidates"))
        self.assertFalse(routing["routing_selected"])
        self.assertFalse(routing["order_routed"])
        self.assertTrue(routing["preview_only"])

    def test_send_order_route_preview_is_built(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        send_order_route = result["send_order_route_preview"]
        self.assertTrue(send_order_route)
        self.assertTrue(send_order_route["send_order_route_planned"])
        self.assertFalse(send_order_route["send_order_available"])
        self.assertFalse(send_order_route["send_order_called"])
        self.assertFalse(send_order_route["order_router_connected"])
        self.assertTrue(send_order_route["preview_only"])

    def test_route_safety_validation_ready_true(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        validation = result["route_safety_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_router_decision_approved_true(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        decision = result["final_router_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["order_router_connected"])
        self.assertFalse(decision["order_routed"])
        self.assertFalse(decision["send_order_available"])
        self.assertFalse(decision["send_order_called"])
        self.assertFalse(decision["broker_connected"])
        self.assertFalse(decision["broker_adapter_called"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["execution_started"])
        self.assertFalse(decision["execution_completed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_order_router_contract_preview(_ready_adapter_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["order_router_connected"])
        self.assertFalse(result["order_routed"])
        self.assertFalse(result["send_order_available"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_connected"])
        self.assertFalse(result["broker_adapter_called"])
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
        adapter_preview = _ready_adapter_preview()
        original = copy.deepcopy(adapter_preview)

        build_execution_order_router_contract_preview(adapter_preview)

        self.assertEqual(original, adapter_preview)

    def test_protected_files_unchanged(self) -> None:
        build_execution_order_router_contract_preview(_ready_adapter_preview())
        build_execution_order_router_contract_preview(_ready_adapter_preview(status=ADAPTER_INVALID))
        build_execution_order_router_contract_preview(_ready_adapter_preview(status=ADAPTER_BLOCKED))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
