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
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    build_execution_sendorder_call_preview,
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


def _ready_sendorder_contract_preview(**overrides: object) -> dict[str, object]:
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
    sendorder_contract_preview.update(overrides)
    return sendorder_contract_preview


class LifecycleExecutionSendorderCallPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_sendorder_contract_preview_builds_ready_call_preview(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("LIFECYCLE_EXECUTION_SENDORDER_CALL_PREVIEW", result["preview_type"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["sendorder_call_preview"])
        self.assertTrue(result["sendorder_call_preview"].get("call_id"))
        self.assertTrue(result["sendorder_call_preview"]["preview_only"])
        self.assertTrue(result["sendorder_parameter_preview"])
        self.assertTrue(result["sendorder_parameter_preview"]["preview_only"])
        self.assertTrue(result["sendorder_call_sequence_preview"])
        self.assertTrue(result["sendorder_call_sequence_preview"]["preview_only"])
        self.assertTrue(result["sendorder_result_candidate_preview"])
        self.assertTrue(result["sendorder_result_candidate_preview"]["preview_only"])
        self.assertTrue(result["call_safety_validation"]["ready"])
        self.assertTrue(result["final_call_decision"]["approved"])

    def test_blocked_sendorder_contract_preview_is_blocked(self) -> None:
        result = build_execution_sendorder_call_preview(
            _ready_sendorder_contract_preview(status=SENDORDER_BLOCKED)
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["final_call_decision"]["approved"])
        self.assertTrue(result["call_safety_validation"]["blocked"])

    def test_invalid_sendorder_contract_preview_is_invalid(self) -> None:
        invalid = build_execution_sendorder_call_preview(
            _ready_sendorder_contract_preview(status=SENDORDER_INVALID)
        )
        unsupported = build_execution_sendorder_call_preview(
            _ready_sendorder_contract_preview(status="SOMETHING_ELSE")
        )

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertFalse(invalid["call_safety_validation"]["ready"])
        self.assertTrue(invalid["call_safety_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, unsupported["status"])
        self.assertFalse(unsupported["call_safety_validation"]["ready"])

    def test_malformed_input_is_invalid(self) -> None:
        none_result = build_execution_sendorder_call_preview(None)
        self.assertEqual(STATUS_INVALID, none_result["status"])

        empty_result = build_execution_sendorder_call_preview({})
        self.assertEqual(STATUS_INVALID, empty_result["status"])

    def test_sendorder_call_preview_is_built(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        call_preview = result["sendorder_call_preview"]
        self.assertTrue(call_preview)
        self.assertTrue(call_preview.get("call_id"))
        self.assertTrue(call_preview.get("sendorder_id"))
        self.assertTrue(call_preview.get("broker_adapter_name"))
        self.assertTrue(call_preview.get("account"))
        self.assertTrue(call_preview.get("stock_code"))
        self.assertTrue(call_preview.get("order_type"))
        self.assertTrue(call_preview.get("price"))
        self.assertTrue(call_preview.get("quantity"))
        self.assertTrue(call_preview["call_planned"])
        self.assertFalse(call_preview["call_executed"])
        self.assertTrue(call_preview["preview_only"])

    def test_sendorder_parameter_preview_is_built(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        parameter_preview = result["sendorder_parameter_preview"]
        self.assertTrue(parameter_preview)
        self.assertTrue(parameter_preview.get("parameter_set_id"))
        self.assertTrue(parameter_preview.get("parameters"))
        self.assertTrue(parameter_preview.get("broker_api_parameters"))
        self.assertTrue(parameter_preview["preview_only"])

    def test_sendorder_call_sequence_preview_is_built(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        sequence_preview = result["sendorder_call_sequence_preview"]
        self.assertTrue(sequence_preview)
        self.assertTrue(sequence_preview.get("sequence_id"))
        self.assertTrue(sequence_preview.get("sequence_name"))
        self.assertTrue(sequence_preview["sequence_planned"])
        self.assertFalse(sequence_preview["sequence_executed"])
        self.assertTrue(sequence_preview.get("steps"))
        self.assertEqual(4, sequence_preview["total_steps"])
        self.assertFalse(sequence_preview["sequence_completed"])
        self.assertTrue(sequence_preview["preview_only"])

    def test_sendorder_result_candidate_preview_is_built(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        result_candidate_preview = result["sendorder_result_candidate_preview"]
        self.assertTrue(result_candidate_preview)
        self.assertTrue(result_candidate_preview.get("result_candidate_set_id"))
        self.assertTrue(result_candidate_preview.get("candidates"))
        self.assertEqual(3, len(result_candidate_preview["candidates"]))
        self.assertFalse(result_candidate_preview["result_selected"])
        self.assertTrue(result_candidate_preview["preview_only"])

    def test_call_safety_validation_ready_true(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        validation = result["call_safety_validation"]
        self.assertTrue(validation["ready"])
        self.assertFalse(validation["issues"])
        self.assertTrue(validation["preview_only"])

    def test_final_call_decision_approved_true(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        decision = result["final_call_decision"]
        self.assertTrue(decision["approved"])
        self.assertFalse(decision["send_order_called"])
        self.assertFalse(decision["broker_connected"])
        self.assertFalse(decision["broker_api_called"])
        self.assertFalse(decision["execution_allowed"])
        self.assertTrue(decision["preview_only"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["send_order_available"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_connected"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["order_router_connected"])
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
        sendorder_contract_preview = _ready_sendorder_contract_preview()
        original = copy.deepcopy(sendorder_contract_preview)

        build_execution_sendorder_call_preview(sendorder_contract_preview)

        self.assertEqual(original, sendorder_contract_preview)

    def test_protected_files_unchanged(self) -> None:
        build_execution_sendorder_call_preview(_ready_sendorder_contract_preview())
        build_execution_sendorder_call_preview(_ready_sendorder_contract_preview(status=SENDORDER_INVALID))
        build_execution_sendorder_call_preview(_ready_sendorder_contract_preview(status=SENDORDER_BLOCKED))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
