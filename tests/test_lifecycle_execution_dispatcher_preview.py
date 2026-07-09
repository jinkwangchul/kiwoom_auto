# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_execution_final_approval_preview import (
    STATUS_READY as FINAL_APPROVAL_READY,
    STATUS_BLOCKED as FINAL_APPROVAL_BLOCKED,
    STATUS_INVALID as FINAL_APPROVAL_INVALID,
    build_execution_final_approval_preview,
)
from lifecycle_execution_sendorder_result_review_preview import (
    STATUS_READY as RESULT_REVIEW_READY,
    STATUS_BLOCKED as RESULT_REVIEW_BLOCKED,
    STATUS_INVALID as RESULT_REVIEW_INVALID,
    build_execution_sendorder_result_review_preview,
)
from lifecycle_execution_dispatcher_preview import (
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_execution_dispatcher_preview,
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
    return {
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
        **overrides,
    }


def _ready_call_preview(**overrides: object) -> dict[str, object]:
    from lifecycle_execution_transaction_contract import build_execution_transaction_contract
    from lifecycle_execution_engine_preview import build_execution_engine_preview
    from lifecycle_execution_broker_adapter_contract_preview import (
        build_execution_broker_adapter_contract_preview,
    )
    from lifecycle_execution_order_router_contract_preview import (
        build_execution_order_router_contract_preview,
    )
    from lifecycle_execution_sendorder_contract_preview import (
        build_execution_sendorder_contract_preview,
    )
    from lifecycle_execution_sendorder_call_preview import (
        build_execution_sendorder_call_preview,
    )

    generated_at = {"generated_at": "2026-07-09 09:00:00"}
    contract = build_execution_transaction_contract(_ready_readiness_gate_preview(), generated_at)
    engine_preview = build_execution_engine_preview(contract, generated_at)
    adapter_preview = build_execution_broker_adapter_contract_preview(engine_preview, generated_at)
    router_preview = build_execution_order_router_contract_preview(adapter_preview, generated_at)
    sendorder_contract_preview = build_execution_sendorder_contract_preview(
        router_preview, generated_at
    )
    call_preview = build_execution_sendorder_call_preview(sendorder_contract_preview, generated_at)
    call_preview.update(overrides)
    return call_preview


def _ready_result_review_preview(**overrides: object) -> dict[str, object]:
    result_review_preview = build_execution_sendorder_result_review_preview(
        _ready_call_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    result_review_preview.update(overrides)
    return result_review_preview


def _ready_final_approval_preview(**overrides: object) -> dict[str, object]:
    final_approval_preview = build_execution_final_approval_preview(
        _ready_result_review_preview(), {"generated_at": "2026-07-09 09:00:00"}
    )
    final_approval_preview.update(overrides)
    return final_approval_preview


class LifecycleExecutionDispatcherPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_final_approval_preview_builds_ready_dispatcher_preview(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_DISPATCHER_PREVIEW", result["preview_type"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["dispatch_candidate_preview"])
        self.assertTrue(result["dispatch_route_preview"])
        self.assertTrue(result["dispatch_queue_preview"])
        self.assertTrue(result["dispatch_safety_validation"]["ready"])
        self.assertTrue(result["final_dispatch_decision"]["dispatched"])

    def test_blocked_final_approval_preview_is_blocked(self) -> None:
        result = build_execution_dispatcher_preview(
            _ready_final_approval_preview(status=FINAL_APPROVAL_BLOCKED)
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["final_dispatch_decision"]["dispatched"])
        self.assertTrue(result["dispatch_safety_validation"]["blocked"])

    def test_invalid_final_approval_preview_is_invalid(self) -> None:
        invalid = build_execution_dispatcher_preview(
            _ready_final_approval_preview(status=FINAL_APPROVAL_INVALID)
        )
        unsupported = build_execution_dispatcher_preview(
            _ready_final_approval_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["dispatch_safety_validation"]["ready"])
        self.assertTrue(invalid["dispatch_safety_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["dispatch_safety_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_dispatcher_preview(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])

        empty_result = build_execution_dispatcher_preview({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_dispatch_candidate_preview_is_built(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        dispatch_candidate_preview = result["dispatch_candidate_preview"]
        self.assertTrue(dispatch_candidate_preview)
        self.assertTrue(dispatch_candidate_preview.get("candidates"))
        self.assertTrue(dispatch_candidate_preview["dispatch_candidate_ready"])
        self.assertFalse(dispatch_candidate_preview["dispatch_candidate_blocked"])
        self.assertTrue(dispatch_candidate_preview["preview_only"])

    def test_dispatch_route_preview_is_built(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        dispatch_route_preview = result["dispatch_route_preview"]
        self.assertTrue(dispatch_route_preview)
        self.assertTrue(dispatch_route_preview["route_ready"])
        self.assertTrue(dispatch_route_preview.get("route_target"))
        self.assertFalse(dispatch_route_preview["route_blocked"])
        self.assertTrue(dispatch_route_preview["preview_only"])

    def test_dispatch_queue_preview_is_built(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        dispatch_queue_preview = result["dispatch_queue_preview"]
        self.assertTrue(dispatch_queue_preview)
        self.assertTrue(dispatch_queue_preview["queue_ready"])
        self.assertTrue(dispatch_queue_preview.get("queue_name"))
        self.assertFalse(dispatch_queue_preview["queue_enqueued"])
        self.assertFalse(dispatch_queue_preview["queue_started"])
        self.assertTrue(dispatch_queue_preview["preview_only"])

    def test_dispatch_safety_validation_ready_true(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        validation = result["dispatch_safety_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_dispatch_decision_dispatched_true(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        decision = result["final_dispatch_decision"]
        self.assertTrue(decision["dispatched"])
        self.assertFalse(decision["dispatch_allowed"])
        self.assertFalse(decision["dispatch_started"])
        self.assertFalse(decision["dispatch_completed"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["execution_started"])
        self.assertFalse(decision["execution_completed"])
        self.assertFalse(decision["send_order_called"])
        self.assertFalse(decision["send_order_result_recorded"])
        self.assertFalse(decision["recorder_called"])
        self.assertFalse(decision["chejan_called"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_dispatcher_preview(_ready_final_approval_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["dispatch_allowed"])
        self.assertFalse(result["dispatch_started"])
        self.assertFalse(result["dispatch_completed"])
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
        final_approval_preview = _ready_final_approval_preview()
        original = copy.deepcopy(final_approval_preview)

        build_execution_dispatcher_preview(final_approval_preview)

        self.assertEqual(original, final_approval_preview)

    def test_protected_files_unchanged(self) -> None:
        build_execution_dispatcher_preview(_ready_final_approval_preview())
        build_execution_dispatcher_preview(
            _ready_final_approval_preview(status=FINAL_APPROVAL_INVALID)
        )
        build_execution_dispatcher_preview(
            _ready_final_approval_preview(status=FINAL_APPROVAL_BLOCKED)
        )

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
