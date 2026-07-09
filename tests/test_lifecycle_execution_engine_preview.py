# -*- coding: utf-8 -*-
from __future__ import annotations

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


def _ready_execution_contract(**overrides: object) -> dict[str, object]:
    contract = build_execution_transaction_contract(
        _ready_readiness_gate_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    contract.update(overrides)
    return contract


class LifecycleExecutionEnginePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_contract_builds_engine_preview(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        self.assertEqual(STATUS_ENGINE_PREVIEW_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_ENGINE_PREVIEW", result["preview_type"])

    def test_blocked_contract_is_blocked(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract(status="BLOCKED"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["execution_safety_review"]["ready"])
        self.assertTrue(result["execution_safety_review"]["blocked"])
        self.assertFalse(result["final_engine_decision"]["approved"])

    def test_invalid_contract_is_invalid(self) -> None:
        invalid = build_execution_engine_preview(_ready_execution_contract(status="INVALID"))
        unsupported = build_execution_engine_preview(_ready_execution_contract(status="SOMETHING_ELSE"))

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["execution_safety_review"]["ready"])
        self.assertTrue(invalid["execution_safety_review"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["execution_safety_review"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_engine_preview(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])

        empty_result = build_execution_engine_preview({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_execution_engine_plan_is_built(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        plan = result["execution_engine_plan"]
        self.assertTrue(plan)
        self.assertTrue(plan.get("engine_type"))
        self.assertTrue(plan.get("execution_mode"))
        self.assertTrue(plan.get("planned_steps"))
        self.assertTrue(plan["preview_only"])

    def test_execution_preflight_preview_is_built(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        preflight = result["execution_preflight_preview"]
        self.assertTrue(preflight)
        self.assertTrue(preflight["preflight_required"])
        self.assertFalse(preflight["preflight_executed"])
        self.assertTrue(preflight.get("preflight_items"))
        self.assertTrue(preflight["preview_only"])

    def test_broker_adapter_preview_is_built(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        broker = result["broker_adapter_preview"]
        self.assertTrue(broker)
        self.assertTrue(broker["broker_adapter_planned"])
        self.assertFalse(broker["broker_connected"])
        self.assertFalse(broker["send_order_available"])
        self.assertTrue(broker["preview_only"])

    def test_order_router_preview_is_built(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        router = result["order_router_preview"]
        self.assertTrue(router)
        self.assertTrue(router["order_router_planned"])
        self.assertFalse(router["order_router_connected"])
        self.assertFalse(router["order_routed"])
        self.assertTrue(router["preview_only"])

    def test_execution_safety_review_ready_true(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        review = result["execution_safety_review"]
        self.assertTrue(review["ready"])
        self.assertFalse(review["issues"])
        self.assertTrue(review["preview_only"])

    def test_final_engine_decision_approved_true(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        decision = result["final_engine_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["execution_started"])
        self.assertFalse(decision["execution_completed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_engine_preview(_ready_execution_contract())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["execution_completed"])
        self.assertFalse(result["broker_connected"])
        self.assertFalse(result["order_router_connected"])
        self.assertFalse(result["order_routed"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])

    def test_protected_files_unchanged(self) -> None:
        build_execution_engine_preview(_ready_execution_contract())
        build_execution_engine_preview(_ready_execution_contract(status="INVALID"))
        build_execution_engine_preview(_ready_execution_contract(status="BLOCKED"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()