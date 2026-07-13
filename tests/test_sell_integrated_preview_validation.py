from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_common_execution_preview_adapter as common_adapter
import sell_execution_full_preview_orchestrator as full_orchestrator
from sell_execution_full_preview_orchestrator import build_sell_execution_full_preview
from sell_real_ready_adapter_preview import build_sell_real_ready_adapter_preview


def _guard() -> dict:
    return {
        "operator_confirmed": True,
        "real_trade_enabled": True,
        "account_no": "12345678",
    }


def _contract(
    *,
    order_id: str = "ORDER_1",
    source_signal_id: str = "SIG_1",
    action_source: str = "METHOD",
    hoga: str = "LIMIT",
    price: int | None = 80000,
    contract_status: str = "READY",
) -> dict:
    return {
        "id": order_id,
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "code": "003550",
        "symbol": "003550",
        "side": "SELL",
        "quantity": 10,
        "price": price,
        "hoga": hoga,
        "order_type": "SELL",
        "action_source": action_source,
        "contract_status": contract_status,
        "price_required": hoga == "LIMIT",
        "order_intent": {
            "side": "SELL",
            "hoga": hoga,
            "action_source": action_source,
        },
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "warnings": [],
        "reasons": [],
    }


def _contract_preview(*contracts: dict, warnings: list[str] | None = None) -> dict:
    return {
        "preview_type": "SELL_EXECUTION_CONTRACT_PREVIEW",
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "status": "READY",
        "contracts": list(contracts),
        "warnings": list(warnings or []),
        "reasons": [],
        "summary": {},
    }


def _adapter_from_contracts(*contracts: dict, warnings: list[str] | None = None) -> dict:
    return build_sell_real_ready_adapter_preview(_contract_preview(*contracts, warnings=warnings))


def _common_with_ready_flag(adapter_preview: dict, guard_context: dict | None = None) -> dict:
    result = common_adapter.build_sell_common_execution_preview(adapter_preview, guard_context)
    result["common_execution_ready"] = result.get("status") == "READY"
    return result


class SellIntegratedPreviewValidationTests(unittest.TestCase):
    def _run_full(self, adapter_preview: dict, *, existing_orders: list[dict] | None = None) -> dict:
        return build_sell_execution_full_preview(
            adapter_preview,
            guard_context=_guard(),
            existing_orders=existing_orders,
        )

    def _ready_result(self, *contracts: dict) -> dict:
        return self._run_full(_adapter_from_contracts(*contracts))

    def test_limit_sell_single_candidate_ready_flow(self):
        result = self._ready_result(_contract())

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["completed"])
        self.assertEqual(result["preview_steps"]["CommonExecutionPreview"], "PASS")
        self.assertEqual(result["preview_steps"]["ExecutionReadinessPreview"], "PASS")
        self.assertEqual(result["preview_steps"]["SignalGatePreview"], "PASS")
        self.assertEqual(result["preview_steps"]["ExecutionQueuePreview"], "PASS")
        self.assertEqual(result["summary"]["queue_ready_count"], 1)

    def test_unpatched_limit_flow_is_ready_after_common_ready_flag_contract_fix(self):
        adapter = _adapter_from_contracts(_contract())

        result = build_sell_execution_full_preview(adapter, guard_context=_guard())

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["common_execution_preview"]["common_execution_ready"])

    def test_limit_sell_multiple_candidates_are_preserved(self):
        result = self._ready_result(
            _contract(order_id="ORDER_1", source_signal_id="SIG_1"),
            _contract(order_id="ORDER_2", source_signal_id="SIG_2", action_source="COMPLETION"),
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["summary"]["ready_candidate_count"], 2)
        self.assertEqual(result["summary"]["opened_gate_count"], 2)
        self.assertEqual(result["summary"]["queue_ready_count"], 2)
        self.assertEqual(len(result["execution_queue_preview"]["queue_ready_candidates"]), 2)

    def test_some_ready_some_blocked_preserves_ready_candidate(self):
        adapter = _adapter_from_contracts(
            _contract(order_id="ORDER_1", source_signal_id="SIG_1"),
            _contract(order_id="ORDER_MARKET", source_signal_id="SIG_MARKET", hoga="MARKET", price=None),
        )

        result = self._run_full(adapter)

        self.assertEqual(result["status"], "READY")
        self.assertEqual(adapter["summary"]["ready_candidate_count"], 1)
        self.assertEqual(len(adapter["blocked_candidates"]), 1)
        self.assertEqual(result["summary"]["queue_ready_count"], 1)

    def test_market_candidate_remains_blocked(self):
        adapter = _adapter_from_contracts(_contract(hoga="MARKET", price=None))

        result = self._run_full(adapter)

        self.assertEqual(adapter["status"], "BLOCKED")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("no eligible SELL common execution preview candidates", result["common_execution_preview"]["reasons"])

    def test_pending_cancel_is_separated_from_normal_sell_order_path(self):
        adapter = _adapter_from_contracts(_contract(action_source="PENDING", hoga="LIMIT"))

        result = self._run_full(adapter)

        self.assertEqual(adapter["status"], "BLOCKED")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", adapter["blocked_candidates"][0]["reasons"][0])

    def test_duplicate_request_hash_blocks_queue_candidate(self):
        first = self._ready_result(_contract())
        record = deepcopy(first["execution_queue_preview"]["queue_ready_candidates"][0]["order_queued_record_preview"])

        result = self._run_full(_adapter_from_contracts(_contract()), existing_orders=[record])

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate request_hash", result["execution_queue_preview"]["candidate_queue_results"][0]["reasons"])

    def test_duplicate_lock_id_blocks_queue_candidate(self):
        first = self._ready_result(_contract())
        record = deepcopy(first["execution_queue_preview"]["queue_ready_candidates"][0]["order_queued_record_preview"])
        record["request_hash"] = "different-request-hash"
        record["order_id"] = "DIFFERENT_ORDER"

        result = self._run_full(_adapter_from_contracts(_contract()), existing_orders=[record])

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate lock_id", result["execution_queue_preview"]["candidate_queue_results"][0]["reasons"])

    def test_duplicate_order_id_blocks_queue_candidate(self):
        first = self._ready_result(_contract())
        record = deepcopy(first["execution_queue_preview"]["queue_ready_candidates"][0]["order_queued_record_preview"])
        record["request_hash"] = "different-request-hash"
        record["lock_id"] = "DIFFERENT_LOCK"

        result = self._run_full(_adapter_from_contracts(_contract()), existing_orders=[record])

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate order_id", result["execution_queue_preview"]["candidate_queue_results"][0]["reasons"])

    def test_common_invalid_stops_chain(self):
        common = _common_with_ready_flag(_adapter_from_contracts(_contract()), _guard())
        common["status"] = "INVALID"
        common["common_execution_ready"] = False
        common["reasons"] = ["common invalid"]

        with mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", return_value=common):
            result = build_sell_execution_full_preview(_adapter_from_contracts(_contract()), guard_context=_guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["preview_steps"]["CommonExecutionPreview"], "FAIL")
        self.assertEqual(result["preview_steps"]["ExecutionReadinessPreview"], "SKIP")

    def test_readiness_invalid_stops_chain(self):
        readiness = {"status": "INVALID", "preview_type": "SELL_EXECUTION_READINESS_PREVIEW", "preview_only": True, "readiness_ready": False, "reasons": ["readiness invalid"], "warnings": [], "summary": {}}

        with (
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", side_effect=_common_with_ready_flag),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_readiness_preview", return_value=readiness),
        ):
            result = build_sell_execution_full_preview(_adapter_from_contracts(_contract()), guard_context=_guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["preview_steps"]["SignalGatePreview"], "SKIP")

    def test_gate_invalid_stops_chain(self):
        gate = {"status": "INVALID", "preview_type": "SELL_SIGNAL_GATE_PREVIEW", "preview_only": True, "signal_gate_ready": False, "reasons": ["gate invalid"], "warnings": [], "summary": {}}

        with (
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", side_effect=_common_with_ready_flag),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_signal_gate_preview", return_value=gate),
        ):
            result = build_sell_execution_full_preview(_adapter_from_contracts(_contract()), guard_context=_guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["preview_steps"]["ExecutionQueuePreview"], "SKIP")

    def test_queue_invalid_is_overall_invalid(self):
        queue = {"status": "INVALID", "preview_type": "SELL_EXECUTION_QUEUE_PREVIEW", "preview_only": True, "execution_queue_ready": False, "reasons": ["queue invalid"], "warnings": [], "summary": {}}

        with (
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", side_effect=_common_with_ready_flag),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_queue_preview", return_value=queue),
        ):
            result = build_sell_execution_full_preview(_adapter_from_contracts(_contract()), guard_context=_guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["preview_steps"]["ExecutionQueuePreview"], "FAIL")

    def test_stage_safety_flag_violation_invalid(self):
        common = _common_with_ready_flag(_adapter_from_contracts(_contract()), _guard())
        common["runtime_write"] = True

        with mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", return_value=common):
            result = build_sell_execution_full_preview(_adapter_from_contracts(_contract()), guard_context=_guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("CommonExecutionPreview safety flag violation", result["reasons"])

    def test_ready_flag_missing_invalid(self):
        common = _common_with_ready_flag(_adapter_from_contracts(_contract()), _guard())
        common.pop("common_execution_ready")

        with mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", return_value=common):
            result = build_sell_execution_full_preview(_adapter_from_contracts(_contract()), guard_context=_guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("CommonExecutionPreview ready flag is not True", result["reasons"])

    def test_input_mutation_does_not_occur(self):
        preview = _contract_preview(_contract())
        original = deepcopy(preview)

        adapter = build_sell_real_ready_adapter_preview(preview)
        self._run_full(adapter)

        self.assertEqual(preview, original)

    def test_queue_commit_runtime_sendorder_and_broker_flags_remain_false(self):
        result = self._ready_result(_contract())

        self.assertFalse(result["queue_committed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])

    def test_file_runtime_queue_sendorder_functions_are_not_called(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._ready_result(_contract())

        self.assertEqual(result["status"], "READY")
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_candidate_identity_source_signal_and_order_id_are_preserved(self):
        result = self._ready_result(_contract(order_id="ORDER_IDENTITY", source_signal_id="SIG_IDENTITY"))

        queue_candidate = result["execution_queue_preview"]["queue_ready_candidates"][0]
        record = queue_candidate["order_queued_record_preview"]

        self.assertEqual(queue_candidate["source_signal_id"], "SIG_IDENTITY")
        self.assertEqual(queue_candidate["source_order_id"], "ORDER_IDENTITY")
        self.assertEqual(record["source_signal_id"], "SIG_IDENTITY")
        self.assertEqual(record["order_id"], "ORDER_IDENTITY")

    def test_summary_counts_match_candidate_lists(self):
        result = self._ready_result(
            _contract(order_id="ORDER_1", source_signal_id="SIG_1"),
            _contract(order_id="ORDER_2", source_signal_id="SIG_2", action_source="COMPLETION"),
        )
        queue = result["execution_queue_preview"]

        self.assertEqual(result["summary"]["queue_ready_count"], len(queue["queue_ready_candidates"]))
        self.assertEqual(queue["summary"]["queue_ready_count"], len(queue["queue_ready_candidates"]))
        self.assertEqual(queue["summary"]["candidate_count"], len(queue["candidate_queue_results"]))

    def test_warnings_and_reasons_propagate(self):
        adapter = _adapter_from_contracts(_contract(), warnings=["source-warning"])
        result = self._run_full(adapter, existing_orders=[{"request_hash": "x", "lock_id": "y", "order_id": "ORDER_1"}])

        self.assertIn("source-warning", result["warnings"])
        self.assertIn("duplicate order_id", result["execution_queue_preview"]["candidate_queue_results"][0]["reasons"])

    def test_no_priority_or_auto_selection_for_multiple_candidates(self):
        result = self._ready_result(
            _contract(order_id="ORDER_1", source_signal_id="SIG_1"),
            _contract(order_id="ORDER_2", source_signal_id="SIG_2", action_source="COMPLETION"),
        )

        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])
        self.assertFalse(result["execution_queue_preview"]["summary"]["priority_selected"])
        self.assertFalse(result["execution_queue_preview"]["summary"]["auto_selected"])

    def test_new_validation_does_not_reference_protected_files(self):
        source = Path(__file__).read_text(encoding="utf-8")

        self.assertNotIn("rules" + ".json", source)
        self.assertNotIn("state" + ".json", source)


if __name__ == "__main__":
    unittest.main()
