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
    STATUS_READY as ROUTER_READY,
    STATUS_BLOCKED as ROUTER_BLOCKED,
    STATUS_INVALID as ROUTER_INVALID,
    build_execution_order_router_contract_preview,
)
from lifecycle_execution_sendorder_contract_preview import (
    STATUS_READY as SENDORDER_READY,
    STATUS_BLOCKED as SENDORDER_BLOCKED,
    STATUS_INVALID as SENDORDER_INVALID,
    build_execution_sendorder_contract_preview,
)
from lifecycle_execution_sendorder_call_preview import (
    STATUS_READY as CALL_READY,
    STATUS_BLOCKED as CALL_BLOCKED,
    STATUS_INVALID as CALL_INVALID,
    build_execution_sendorder_call_preview,
)
from lifecycle_execution_sendorder_result_review_preview import (
    STATUS_READY as RESULT_REVIEW_READY,
    STATUS_BLOCKED as RESULT_REVIEW_BLOCKED,
    STATUS_INVALID as RESULT_REVIEW_INVALID,
    build_execution_sendorder_result_review_preview,
)
from lifecycle_execution_final_approval_preview import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_execution_final_approval_preview,
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


def _ready_call_preview(**overrides: object) -> dict[str, object]:
    contract = build_execution_transaction_contract(
        _ready_readiness_gate_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    engine_preview = build_execution_engine_preview(contract, {"generated_at": "2026-07-09 09:00:00"})
    adapter_preview = build_execution_broker_adapter_contract_preview(
        engine_preview, {"generated_at": "2026-07-09 09:00:00"}
    )
    router_preview = build_execution_order_router_contract_preview(
        adapter_preview, {"generated_at": "2026-07-09 09:00:00"}
    )
    sendorder_contract_preview = build_execution_sendorder_contract_preview(
        router_preview, {"generated_at": "2026-07-09 09:00:00"}
    )
    call_preview = build_execution_sendorder_call_preview(
        sendorder_contract_preview, {"generated_at": "2026-07-09 09:00:00"}
    )
    call_preview.update(overrides)
    return call_preview


def _ready_result_review_preview(**overrides: object) -> dict[str, object]:
    result_review_preview = build_execution_sendorder_result_review_preview(
        _ready_call_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    result_review_preview.update(overrides)
    return result_review_preview


class LifecycleExecutionFinalApprovalPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_result_review_preview_builds_ready_final_approval_preview(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_FINAL_APPROVAL_PREVIEW", result["preview_type"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["approval_requirement_preview"])
        self.assertTrue(result["approval_requirement_preview"].get("approval_source"))
        self.assertTrue(result["operator_review_preview"])
        self.assertTrue(result["operator_review_preview"]["preview_only"])
        self.assertTrue(result["execution_blocking_preview"])
        self.assertTrue(result["execution_blocking_preview"]["preview_only"])
        self.assertTrue(result["approval_token_preview"])
        self.assertTrue(result["approval_token_preview"]["preview_only"])
        self.assertTrue(result["approval_safety_validation"]["ready"])
        self.assertTrue(result["final_approval_decision"]["approved"])

    def test_blocked_result_review_preview_is_blocked(self) -> None:
        result = build_execution_final_approval_preview(
            _ready_result_review_preview(status=RESULT_REVIEW_BLOCKED)
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["final_approval_decision"]["approved"])
        self.assertTrue(result["approval_safety_validation"]["blocked"])

    def test_invalid_result_review_preview_is_invalid(self) -> None:
        invalid = build_execution_final_approval_preview(
            _ready_result_review_preview(status=RESULT_REVIEW_INVALID)
        )
        unsupported = build_execution_final_approval_preview(
            _ready_result_review_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["approval_safety_validation"]["ready"])
        self.assertTrue(invalid["approval_safety_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["approval_safety_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_final_approval_preview(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])

        empty_result = build_execution_final_approval_preview({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_approval_requirement_preview_is_built(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        approval_requirement_preview = result["approval_requirement_preview"]
        self.assertTrue(approval_requirement_preview)
        self.assertTrue(approval_requirement_preview["approval_required"])
        self.assertFalse(approval_requirement_preview["approval_granted"])
        self.assertTrue(approval_requirement_preview.get("approval_source"))
        self.assertTrue(approval_requirement_preview["preview_only"])

    def test_operator_review_preview_is_built(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        operator_review_preview = result["operator_review_preview"]
        self.assertTrue(operator_review_preview)
        self.assertTrue(operator_review_preview["operator_review_required"])
        self.assertFalse(operator_review_preview["operator_review_completed"])
        self.assertTrue(operator_review_preview.get("review_items"))
        self.assertTrue(operator_review_preview["preview_only"])

    def test_execution_blocking_preview_is_built(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        execution_blocking_preview = result["execution_blocking_preview"]
        self.assertTrue(execution_blocking_preview)
        self.assertTrue(execution_blocking_preview.get("blocking_reasons"))
        self.assertTrue(execution_blocking_preview["execution_blocked"])
        self.assertTrue(execution_blocking_preview["preview_only"])

    def test_approval_token_preview_is_built(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        approval_token_preview = result["approval_token_preview"]
        self.assertTrue(approval_token_preview)
        self.assertTrue(approval_token_preview.get("token_id"))
        self.assertTrue(approval_token_preview["token_required"])
        self.assertFalse(approval_token_preview["token_issued"])
        self.assertFalse(approval_token_preview["token_consumed"])
        self.assertTrue(approval_token_preview["preview_only"])

    def test_approval_safety_validation_ready_true(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        validation = result["approval_safety_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_approval_decision_approved_true(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        decision = result["final_approval_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["approval_granted"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["execution_started"])
        self.assertFalse(decision["execution_completed"])
        self.assertFalse(decision["send_order_called"])
        self.assertFalse(decision["send_order_result_recorded"])
        self.assertFalse(decision["recorder_called"])
        self.assertFalse(decision["chejan_called"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_final_approval_preview(_ready_result_review_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["approval_granted"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["execution_completed"])
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
        result_review_preview = _ready_result_review_preview()
        original = copy.deepcopy(result_review_preview)

        build_execution_final_approval_preview(result_review_preview)

        self.assertEqual(original, result_review_preview)

    def test_protected_files_unchanged(self) -> None:
        build_execution_final_approval_preview(_ready_result_review_preview())
        build_execution_final_approval_preview(_ready_result_review_preview(status=RESULT_REVIEW_INVALID))
        build_execution_final_approval_preview(_ready_result_review_preview(status=RESULT_REVIEW_BLOCKED))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
