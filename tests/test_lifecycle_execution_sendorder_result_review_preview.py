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
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_execution_sendorder_result_review_preview,
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


class LifecycleExecutionSendorderResultReviewPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_call_preview_builds_ready_result_review_preview(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_SENDORDER_RESULT_REVIEW_PREVIEW", result["preview_type"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["sendorder_result_review_preview"])
        self.assertTrue(result["sendorder_result_review_preview"].get("review_id"))
        self.assertTrue(result["sendorder_result_review_preview"]["preview_only"])
        self.assertTrue(result["result_classification_preview"])
        self.assertTrue(result["result_classification_preview"]["preview_only"])
        self.assertTrue(result["recorder_handoff_preview"])
        self.assertTrue(result["recorder_handoff_preview"]["preview_only"])
        self.assertTrue(result["failure_handling_preview"])
        self.assertTrue(result["failure_handling_preview"]["preview_only"])
        self.assertTrue(result["result_safety_validation"]["ready"])
        self.assertTrue(result["final_result_review_decision"]["approved"])

    def test_blocked_call_preview_is_blocked(self) -> None:
        result = build_execution_sendorder_result_review_preview(
            _ready_call_preview(status=CALL_BLOCKED)
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["final_result_review_decision"]["approved"])
        self.assertTrue(result["result_safety_validation"]["blocked"])

    def test_invalid_call_preview_is_invalid(self) -> None:
        invalid = build_execution_sendorder_result_review_preview(
            _ready_call_preview(status=CALL_INVALID)
        )
        unsupported = build_execution_sendorder_result_review_preview(
            _ready_call_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["result_safety_validation"]["ready"])
        self.assertTrue(invalid["result_safety_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["result_safety_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_sendorder_result_review_preview(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])

        empty_result = build_execution_sendorder_result_review_preview({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_sendorder_result_review_preview_is_built(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        review_preview = result["sendorder_result_review_preview"]
        self.assertTrue(review_preview)
        self.assertTrue(review_preview.get("review_id"))
        self.assertTrue(review_preview.get("call_id"))
        self.assertTrue(review_preview.get("sendorder_id"))
        self.assertTrue(review_preview.get("broker_adapter_name"))
        self.assertTrue(review_preview.get("account"))
        self.assertTrue(review_preview.get("stock_code"))
        self.assertTrue(review_preview.get("order_type"))
        self.assertTrue(review_preview.get("price"))
        self.assertTrue(review_preview.get("quantity"))
        self.assertTrue(review_preview["review_planned"])
        self.assertFalse(review_preview["review_completed"])
        self.assertTrue(review_preview["preview_only"])

    def test_result_classification_preview_is_built(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        classification_preview = result["result_classification_preview"]
        self.assertTrue(classification_preview)
        self.assertTrue(classification_preview.get("classification_id"))
        self.assertTrue(classification_preview.get("classifications"))
        self.assertFalse(classification_preview["classification_selected"])
        self.assertTrue(classification_preview["preview_only"])

    def test_recorder_handoff_preview_is_built(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        handoff_preview = result["recorder_handoff_preview"]
        self.assertTrue(handoff_preview)
        self.assertTrue(handoff_preview.get("handoff_id"))
        self.assertTrue(handoff_preview["handoff_required"])
        self.assertFalse(handoff_preview["handoff_completed"])
        self.assertFalse(handoff_preview["send_order_result_recorded"])
        self.assertFalse(handoff_preview["recorder_called"])
        self.assertFalse(handoff_preview["chejan_called"])
        self.assertTrue(handoff_preview["preview_only"])

    def test_failure_handling_preview_is_built(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        failure_preview = result["failure_handling_preview"]
        self.assertTrue(failure_preview)
        self.assertTrue(failure_preview.get("handling_id"))
        self.assertTrue(failure_preview.get("failure_candidates"))
        self.assertTrue(failure_preview.get("handling_steps"))
        self.assertEqual(3, failure_preview["total_steps"])
        self.assertFalse(failure_preview["handling_completed"])
        self.assertFalse(failure_preview["retry_planned"])
        self.assertFalse(failure_preview["rollback_planned"])
        self.assertTrue(failure_preview["preview_only"])

    def test_result_safety_validation_ready_true(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        validation = result["result_safety_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_result_review_decision_approved_true(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        decision = result["final_result_review_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["send_order_result_recorded"])
        self.assertFalse(decision["recorder_called"])
        self.assertFalse(decision["chejan_called"])
        self.assertFalse(decision["execution_completed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_sendorder_result_review_preview(_ready_call_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["send_order_result_recorded"])
        self.assertFalse(result["recorder_called"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["execution_completed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["audit_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])

    def test_input_is_not_mutated(self) -> None:
        call_preview = _ready_call_preview()
        original = copy.deepcopy(call_preview)

        build_execution_sendorder_result_review_preview(call_preview)

        self.assertEqual(original, call_preview)

    def test_protected_files_unchanged(self) -> None:
        build_execution_sendorder_result_review_preview(_ready_call_preview())
        build_execution_sendorder_result_review_preview(_ready_call_preview(status=CALL_INVALID))
        build_execution_sendorder_result_review_preview(_ready_call_preview(status=CALL_BLOCKED))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
