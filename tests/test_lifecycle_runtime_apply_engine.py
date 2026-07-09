# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_apply_engine import (
    STATUS_BLOCKED,
    STATUS_ENGINE_READY,
    STATUS_INVALID,
    build_runtime_apply_engine_result,
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


def _ready_engine_contract(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "preview_type": "LIFECYCLE_RUNTIME_APPLY_ENGINE_CONTRACT",
        "status": "READY",
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "engine_executed": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "apply_engine_contract": {
            "contract_id": "ENGINE_CONTRACT_1",
            "source_orchestrator_id": "ORCHESTRATOR_1",
            "status": "READY",
            "ready_for_engine": True,
            "blocked_reason": "",
            "invalid_reason": "",
            "preview_only": True,
        },
        "engine_input_contract": {
            "runtime_payload_contract": {
                "contract_type": "RUNTIME_PAYLOAD_CONTRACT",
                "status": "READY",
                "ready": True,
                "payload_preview": {"runtime": {"order_id": "ORDER_1"}},
                "preview_only": True,
            },
            "position_payload_contract": {
                "contract_type": "POSITION_PAYLOAD_CONTRACT",
                "status": "READY",
                "ready": True,
                "payload_preview": {"position": {"code": "005930"}},
                "preview_only": True,
            },
            "balance_payload_contract": {
                "contract_type": "BALANCE_PAYLOAD_CONTRACT",
                "status": "READY",
                "ready": True,
                "payload_preview": {"balance": {"cash_delta": -70000}},
                "preview_only": True,
            },
            "backup_contract": {
                "contract_type": "BACKUP_CONTRACT",
                "status": "READY",
                "ready": True,
                "payload_preview": {"backup": "preview"},
                "preview_only": True,
            },
            "rollback_contract": {
                "contract_type": "ROLLBACK_CONTRACT",
                "status": "READY",
                "ready": True,
                "payload_preview": {"rollback": "preview"},
                "preview_only": True,
            },
            "preview_only": True,
        },
        "engine_gate": {
            "approval_required": True,
            "operator_review_required": True,
            "contract_token_required": True,
            "ready_to_execute": True,
            "preview_only": True,
            "engine_executed": False,
        },
        "engine_validation": {
            "ready": True,
            "blocked": False,
            "invalid": False,
            "issues": [],
            "warnings": [],
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleRuntimeApplyEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_contract_builds_engine_ready_result(self) -> None:
        result = build_runtime_apply_engine_result(_ready_engine_contract())

        self.assertEqual(STATUS_ENGINE_READY, result["status"])
        self.assertTrue(result["engine_validation"]["ready"])
        self.assertTrue(result["final_engine_decision"]["approved_for_future_engine"])

    def test_blocked_contract_blocks_engine(self) -> None:
        result = build_runtime_apply_engine_result(_ready_engine_contract(status="BLOCKED", issues=["blocked upstream"]))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertTrue(result["engine_validation"]["blocked"])
        self.assertFalse(result["final_engine_decision"]["approved_for_future_engine"])

    def test_invalid_contract_is_invalid(self) -> None:
        invalid = build_runtime_apply_engine_result(_ready_engine_contract(status="INVALID"))
        malformed = build_runtime_apply_engine_result({"status": "READY"})

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertTrue(invalid["engine_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertTrue(malformed["engine_validation"]["invalid"])

    def test_execution_plan_is_created(self) -> None:
        result = build_runtime_apply_engine_result(_ready_engine_contract())

        plan = result["engine_execution_plan"]
        self.assertEqual("ENGINE_EXECUTION_PLAN", plan["plan_type"])
        self.assertTrue(plan["ready"])
        self.assertEqual("ENGINE_CONTRACT_1", plan["source_contract_id"])
        self.assertIn("prepare_file_write_preview", plan["execution_steps"])

    def test_transaction_preview_is_created(self) -> None:
        result = build_runtime_apply_engine_result(_ready_engine_contract())

        preview = result["transaction_plan_preview"]
        self.assertEqual("TRANSACTION_PLAN_PREVIEW", preview["plan_type"])
        self.assertTrue(preview["ready"])
        self.assertFalse(preview["transaction_boundary"]["runtime_write_allowed"])
        self.assertFalse(preview["sqlite_write"])

    def test_file_write_plan_is_preview_only(self) -> None:
        result = build_runtime_apply_engine_result(_ready_engine_contract())

        preview = result["file_write_plan_preview"]
        self.assertEqual("FILE_WRITE_PLAN_PREVIEW", preview["plan_type"])
        self.assertTrue(preview["ready"])
        self.assertTrue(preview["preview_only"])
        self.assertFalse(preview["file_write_called"])
        self.assertFalse(preview["runtime_write"])
        self.assertFalse(preview["position_write"])
        self.assertFalse(preview["balance_write"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_apply_engine_result(_ready_engine_contract())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["engine_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_input_is_not_mutated(self) -> None:
        contract = _ready_engine_contract()
        original = copy.deepcopy(contract)

        build_runtime_apply_engine_result(contract)

        self.assertEqual(original, contract)

    def test_protected_files_hash_unchanged(self) -> None:
        build_runtime_apply_engine_result(_ready_engine_contract())
        build_runtime_apply_engine_result(_ready_engine_contract(status="BLOCKED"))
        build_runtime_apply_engine_result(_ready_engine_contract(status="INVALID"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
