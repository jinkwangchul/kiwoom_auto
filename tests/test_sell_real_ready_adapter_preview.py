import copy
import inspect
import unittest

import sell_real_ready_adapter_preview as subject
from sell_real_ready_adapter_preview import build_sell_real_ready_adapter_preview


def _contract(
    action_source="METHOD",
    *,
    contract_status="READY",
    order_id="order-1",
    source_signal_id="signal-1",
    code="005930",
    symbol="005930",
    side="SELL",
    quantity=10,
    price=50000,
    hoga="LIMIT",
    order_type="SELL",
    price_required=True,
):
    return {
        "contract_type": "SELL_EXECUTION_CONTRACT",
        "contract_status": contract_status,
        "authorized": True,
        "action_source": action_source,
        "id": order_id,
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "code": code,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "price_required": price_required,
        "hoga": hoga,
        "order_type": order_type,
        "target_status": "REAL_READY",
        "intended_status": "REAL_READY",
        "status": "REAL_READY",
        "execution_enabled": True,
        "order_intent": {
            "side": "SELL",
            "hoga": hoga,
            "action_source": action_source,
            "price_required": price_required,
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


def _preview(contracts):
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
        "contracts": contracts,
        "summary": {},
        "warnings": ["contract_warning"],
        "reasons": [],
    }


class SellRealReadyAdapterPreviewTests(unittest.TestCase):
    def test_method_ready_contract_converted(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["order_candidates"][0]["action_source"], "METHOD")

    def test_completion_ready_contract_converted(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("COMPLETION")]))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["order_candidates"][0]["action_source"], "COMPLETION")

    def test_multiple_contracts_all_preserved(self):
        result = build_sell_real_ready_adapter_preview(
            _preview(
                [
                    _contract("METHOD", order_id="m1", source_signal_id="s1"),
                    _contract("COMPLETION", order_id="c1", source_signal_id="s2"),
                ]
            )
        )

        self.assertEqual(len(result["order_candidates"]), 2)

    def test_input_order_preserved(self):
        result = build_sell_real_ready_adapter_preview(
            _preview(
                [
                    _contract("COMPLETION", order_id="c1", source_signal_id="s2"),
                    _contract("METHOD", order_id="m1", source_signal_id="s1"),
                ]
            )
        )

        self.assertEqual([item["order_id"] for item in result["order_candidates"]], ["c1", "m1"])

    def test_priority_not_selected(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["order_candidates"][0]["priority_selected"])

    def test_auto_select_not_performed(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertFalse(result["summary"]["auto_selected"])
        self.assertFalse(result["order_candidates"][0]["auto_selected"])

    def test_status_real_ready_preview_expression(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertEqual(result["order_candidates"][0]["status"], "REAL_READY")

    def test_execution_enabled_true(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertTrue(result["order_candidates"][0]["execution_enabled"])

    def test_real_ready_state_not_changed(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertFalse(result["real_ready_state_changed"])
        self.assertFalse(result["order_candidates"][0]["real_ready_state_changed"])

    def test_id_and_order_id_preserved(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", order_id="keep-id")])
        )

        candidate = result["order_candidates"][0]
        self.assertEqual(candidate["id"], "keep-id")
        self.assertEqual(candidate["order_id"], "keep-id")

    def test_source_signal_id_preserved(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", source_signal_id="signal-x")])
        )

        self.assertEqual(result["order_candidates"][0]["source_signal_id"], "signal-x")

    def test_code_preserved(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD", code="A0001")]))

        self.assertEqual(result["order_candidates"][0]["code"], "A0001")

    def test_side_sell(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertEqual(result["order_candidates"][0]["side"], "SELL")

    def test_quantity_preserved(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD", quantity=77)]))

        self.assertEqual(result["order_candidates"][0]["quantity"], 77)

    def test_action_source_preserved(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("COMPLETION")]))

        self.assertEqual(result["order_candidates"][0]["action_source"], "COMPLETION")

    def test_order_intent_preserved(self):
        contract = _contract("METHOD")

        result = build_sell_real_ready_adapter_preview(_preview([contract]))

        self.assertEqual(result["order_candidates"][0]["order_intent"], contract["order_intent"])

    def test_hoga_preserved(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD", hoga="LIMIT")]))

        self.assertEqual(result["order_candidates"][0]["hoga"], "LIMIT")

    def test_market_price_none_preserved(self):
        result = build_sell_real_ready_adapter_preview(
            _preview(
                [
                    _contract(
                        "METHOD",
                        price=None,
                        hoga="MARKET",
                        price_required=False,
                    )
                ]
            )
        )

        self.assertIsNone(result["blocked_candidates"][0]["price"])

    def test_market_price_required_false_preserved(self):
        result = build_sell_real_ready_adapter_preview(
            _preview(
                [
                    _contract(
                        "METHOD",
                        price=None,
                        hoga="MARKET",
                        price_required=False,
                    )
                ]
            )
        )

        self.assertFalse(result["blocked_candidates"][0]["price_required"])

    def test_market_current_price_not_substituted(self):
        result = build_sell_real_ready_adapter_preview(
            _preview(
                [
                    _contract(
                        "METHOD",
                        price=None,
                        hoga="MARKET",
                        price_required=False,
                    )
                ]
            ),
            market_context={"current_price": 99999},
        )

        self.assertIsNone(result["blocked_candidates"][0]["price"])

    def test_market_common_validator_conflict_blocks(self):
        result = build_sell_real_ready_adapter_preview(
            _preview(
                [
                    _contract(
                        "METHOD",
                        price=None,
                        hoga="MARKET",
                        price_required=False,
                    )
                ]
            )
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("price=None", result["blocked_candidates"][0]["warnings"][0])

    def test_limit_positive_price_ready(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", price=51000, hoga="LIMIT")])
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["order_candidates"][0]["price"], 51000)

    def test_limit_missing_price_blocked(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", price=None, hoga="LIMIT")])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blocked_candidates"][0]["candidate_status"], "BLOCKED")

    def test_limit_zero_price_blocked(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", price=0, hoga="LIMIT")])
        )

        self.assertEqual(result["blocked_candidates"][0]["candidate_status"], "BLOCKED")

    def test_pending_excluded(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("PENDING", price=None, hoga=None, order_type=None)])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["order_candidates"], [])

    def test_cancel_pending_order_excluded(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("CANCEL_PENDING_ORDER", price=None, hoga=None, order_type=None)])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["blocked_candidates"][0]["reasons"][0])

    def test_blocked_contract_excluded(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", contract_status="BLOCKED")])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["order_candidates"], [])

    def test_invalid_contract_excluded(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", contract_status="INVALID")])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["order_candidates"], [])

    def test_empty_contracts_blocked(self):
        result = build_sell_real_ready_adapter_preview(_preview([]))

        self.assertEqual(result["status"], "BLOCKED")

    def test_malformed_top_input_invalid(self):
        result = build_sell_real_ready_adapter_preview(None)

        self.assertEqual(result["status"], "INVALID")

    def test_wrong_preview_type_invalid(self):
        result = build_sell_real_ready_adapter_preview({"contracts": []})

        self.assertEqual(result["status"], "INVALID")

    def test_contracts_not_list_invalid(self):
        result = build_sell_real_ready_adapter_preview(
            {"preview_type": "SELL_EXECUTION_CONTRACT_PREVIEW", "contracts": {}}
        )

        self.assertEqual(result["status"], "INVALID")

    def test_missing_required_id_blocked(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", order_id=None)])
        )

        self.assertEqual(result["blocked_candidates"][0]["candidate_status"], "BLOCKED")

    def test_missing_source_signal_id_blocked(self):
        result = build_sell_real_ready_adapter_preview(
            _preview([_contract("METHOD", source_signal_id=None)])
        )

        self.assertEqual(result["blocked_candidates"][0]["candidate_status"], "BLOCKED")

    def test_missing_code_blocked(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD", code=None)]))

        self.assertEqual(result["blocked_candidates"][0]["candidate_status"], "BLOCKED")

    def test_invalid_side_invalid(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD", side="BUY")]))

        self.assertEqual(result["status"], "INVALID")

    def test_input_object_immutable(self):
        preview = _preview([_contract("METHOD")])
        original = copy.deepcopy(preview)

        build_sell_real_ready_adapter_preview(preview)

        self.assertEqual(preview, original)

    def test_output_deepcopy(self):
        preview = _preview([_contract("METHOD")])
        result = build_sell_real_ready_adapter_preview(preview)

        preview["contracts"][0]["code"] = "CHANGED"
        result["order_candidates"][0]["source_contract"]["code"] = "OTHER"

        self.assertEqual(result["execution_contract_snapshot"]["contracts"][0]["code"], "005930")

    def test_repeated_execution_deterministic(self):
        preview = _preview([_contract("METHOD")])

        first = build_sell_real_ready_adapter_preview(preview)
        second = build_sell_real_ready_adapter_preview(preview)

        self.assertEqual(first, second)

    def test_top_level_safety_flags(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["pipeline_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["send_order"])

    def test_candidate_safety_flags(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))
        candidate = result["order_candidates"][0]

        self.assertTrue(candidate["preview_only"])
        self.assertFalse(candidate["execution_connected"])
        self.assertFalse(candidate["pipeline_called"])
        self.assertFalse(candidate["runtime_write"])
        self.assertFalse(candidate["queue_write"])
        self.assertFalse(candidate["order_request_created"])
        self.assertFalse(candidate["send_order"])

    def test_common_execution_functions_not_imported(self):
        source = inspect.getsource(subject)

        self.assertNotIn("execution_pipeline_controller", source)
        self.assertNotIn("execution_controller", source)
        self.assertNotIn("final_execution_guard", source)
        self.assertNotIn("signal_gate_execution_queue_bridge", source)
        self.assertNotIn("execution_readiness_input_builder", source)
        self.assertNotIn("order_execution_request", source)

    def test_runtime_queue_files_not_referenced(self):
        source = inspect.getsource(subject)

        self.assertNotIn("state.json", source)
        self.assertNotIn("order_queue.json", source)

    def test_source_warning_preserved(self):
        result = build_sell_real_ready_adapter_preview(_preview([_contract("METHOD")]))

        self.assertIn("contract_warning", result["warnings"])


if __name__ == "__main__":
    unittest.main()
