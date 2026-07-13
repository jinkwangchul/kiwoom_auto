import copy
import inspect
import unittest

import sell_execution_contract_preview as subject
from sell_execution_contract_preview import build_sell_execution_contract_preview


def _normalized_candidate(
    action_source="METHOD",
    *,
    signal_id="sell_signal_1",
    symbol="005930",
    method_set="setting_a",
    quantity=10,
    price=None,
    hoga="MARKET",
    order_type="SELL",
    side="SELL",
    order_id=None,
):
    return {
        "action_source": action_source,
        "symbol": symbol,
        "side": side,
        "signal_id": signal_id,
        "method_set": method_set,
        "order_id": order_id,
        "quantity": quantity,
        "price": price,
        "hoga": hoga,
        "order_type": order_type,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "candidate_created": False,
        "send_order": False,
        "source_previews": {action_source.lower(): {"preview_only": True}},
    }


def _authorized_candidate(action_source="METHOD", **kwargs):
    return {
        "action_source": action_source,
        "status": "AUTHORIZED",
        "authorized": kwargs.pop("authorized", True),
        "real_ready_eligible": True,
        "normalized_candidate": _normalized_candidate(action_source, **kwargs),
        "reasons": [],
        "warnings": [],
    }


def _authorization_preview(candidates):
    return {
        "preview_type": "SELL_REAL_READY_AUTHORIZATION_PREVIEW",
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "status": "READY",
        "authorized_candidates": candidates,
        "warnings": ["source_warning"],
        "reasons": [],
    }


class SellExecutionContractPreviewTests(unittest.TestCase):
    def test_method_authorized_contract_creation(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["contracts"]), 1)
        contract = result["contracts"][0]
        self.assertEqual(contract["contract_status"], "READY")
        self.assertEqual(contract["action_source"], "METHOD")

    def test_completion_authorized_contract_creation(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("COMPLETION")])
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["contracts"][0]["action_source"], "COMPLETION")

    def test_method_and_completion_multiple_contracts_kept(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview(
                [
                    _authorized_candidate("METHOD", signal_id="m1"),
                    _authorized_candidate("COMPLETION", signal_id="c1"),
                ]
            )
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual([c["action_source"] for c in result["contracts"]], ["METHOD", "COMPLETION"])
        self.assertEqual(result["summary"]["ready_contract_count"], 2)

    def test_candidate_order_preserved(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview(
                [
                    _authorized_candidate("COMPLETION", signal_id="c1"),
                    _authorized_candidate("METHOD", signal_id="m1"),
                ]
            )
        )

        self.assertEqual([c["source_signal_id"] for c in result["contracts"]], ["c1", "m1"])

    def test_no_priority_decision(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview(
                [
                    _authorized_candidate("METHOD"),
                    _authorized_candidate("COMPLETION"),
                ]
            )
        )

        self.assertFalse(result["summary"]["priority_selected"])

    def test_no_auto_select(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )

        self.assertFalse(result["summary"]["auto_selected"])

    def test_symbol_maps_to_code(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", symbol="A005930")])
        )

        contract = result["contracts"][0]
        self.assertEqual(contract["symbol"], "A005930")
        self.assertEqual(contract["code"], "A005930")

    def test_side_fixed_sell(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )

        self.assertEqual(result["contracts"][0]["side"], "SELL")
        self.assertEqual(result["contracts"][0]["order_intent"]["side"], "SELL")

    def test_source_signal_id_preserved(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", signal_id="signal-x")])
        )

        self.assertEqual(result["contracts"][0]["source_signal_id"], "signal-x")

    def test_existing_order_id_reused(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", order_id="existing-id")])
        )

        self.assertEqual(result["contracts"][0]["id"], "existing-id")
        self.assertEqual(result["contracts"][0]["order_id"], "existing-id")

    def test_deterministic_id_without_order_id(self):
        auth = _authorization_preview([_authorized_candidate("METHOD", order_id=None)])

        first = build_sell_execution_contract_preview(auth)
        second = build_sell_execution_contract_preview(auth)

        self.assertEqual(first["contracts"][0]["id"], second["contracts"][0]["id"])
        self.assertIn("SELL_EXEC_CONTRACT", first["contracts"][0]["id"])

    def test_market_price_none_preserved(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=None, hoga="MARKET")]),
            market_context={"current_price": 70000},
        )

        self.assertIsNone(result["contracts"][0]["price"])

    def test_market_price_not_required(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=None, hoga="MARKET")])
        )

        self.assertFalse(result["contracts"][0]["price_required"])
        self.assertFalse(result["contracts"][0]["order_intent"]["price_required"])

    def test_market_current_price_not_substituted(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=None, hoga="MARKET")]),
            market_context={"current_price": 12345},
        )

        self.assertIsNone(result["contracts"][0]["price"])

    def test_market_validator_incompatibility_warning(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=None, hoga="MARKET")])
        )

        self.assertTrue(
            any("execution readiness validators" in warning for warning in result["contracts"][0]["warnings"])
        )

    def test_limit_positive_price_ready(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=50000, hoga="LIMIT")])
        )

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["contracts"][0]["price_required"])

    def test_limit_missing_price_blocked(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=None, hoga="LIMIT")])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["contracts"][0]["contract_status"], "BLOCKED")

    def test_limit_zero_price_blocked(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", price=0, hoga="LIMIT")])
        )

        self.assertEqual(result["contracts"][0]["contract_status"], "BLOCKED")

    def test_pending_does_not_become_normal_sell_contract(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("PENDING", order_id="pending-1")])
        )

        contract = result["contracts"][0]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(contract["action_source"], "PENDING")
        self.assertFalse(contract["normal_sell_order_contract"])
        self.assertIsNone(contract["target_status"])

    def test_pending_has_separate_cancel_path_reason(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("PENDING", order_id="pending-1")])
        )

        self.assertIn("separate cancel execution path", result["contracts"][0]["reasons"][0])

    def test_unauthorized_candidate_omitted(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", authorized=False)])
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["contracts"], [])
        self.assertEqual(result["summary"]["unauthorized_skipped_count"], 1)

    def test_malformed_authorization_input_invalid(self):
        result = build_sell_execution_contract_preview(None)

        self.assertEqual(result["status"], "INVALID")

    def test_invalid_preview_type_invalid(self):
        result = build_sell_execution_contract_preview({"authorized_candidates": []})

        self.assertEqual(result["status"], "INVALID")

    def test_candidates_must_be_list(self):
        result = build_sell_execution_contract_preview(
            {
                "preview_type": "SELL_REAL_READY_AUTHORIZATION_PREVIEW",
                "authorized_candidates": {},
            }
        )

        self.assertEqual(result["status"], "INVALID")

    def test_empty_candidates_blocked(self):
        result = build_sell_execution_contract_preview(_authorization_preview([]))

        self.assertEqual(result["status"], "BLOCKED")

    def test_unknown_action_source_invalid(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("UNKNOWN")])
        )

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_item_type_invalid(self):
        result = build_sell_execution_contract_preview(_authorization_preview(["bad"]))

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_side_not_sell_invalid(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", side="BUY")])
        )

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_safety_flag_invalid(self):
        candidate = _authorized_candidate("METHOD")
        candidate["normalized_candidate"]["queue_write"] = True

        result = build_sell_execution_contract_preview(_authorization_preview([candidate]))

        self.assertEqual(result["status"], "INVALID")

    def test_missing_symbol_blocked(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", symbol=None)])
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_signal_id_blocked(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", signal_id=None)])
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_quantity_must_be_positive(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD", quantity=0)])
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_output_snapshots_are_deepcopy(self):
        auth = _authorization_preview([_authorized_candidate("METHOD")])
        result = build_sell_execution_contract_preview(auth)

        auth["authorized_candidates"][0]["normalized_candidate"]["symbol"] = "CHANGED"
        result["contracts"][0]["source_candidate"]["symbol"] = "OTHER"

        self.assertEqual(result["authorization_snapshot"]["authorized_candidates"][0]["normalized_candidate"]["symbol"], "005930")
        self.assertEqual(auth["authorized_candidates"][0]["normalized_candidate"]["symbol"], "CHANGED")

    def test_input_object_immutable(self):
        auth = _authorization_preview([_authorized_candidate("METHOD")])
        original = copy.deepcopy(auth)

        build_sell_execution_contract_preview(auth)

        self.assertEqual(auth, original)

    def test_repeated_results_are_deterministic(self):
        auth = _authorization_preview([_authorized_candidate("METHOD")])

        first = build_sell_execution_contract_preview(auth)
        second = build_sell_execution_contract_preview(auth)

        self.assertEqual(first, second)

    def test_top_level_safety_flags(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_contract_safety_flags(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )
        contract = result["contracts"][0]

        self.assertTrue(contract["preview_only"])
        self.assertFalse(contract["execution_connected"])
        self.assertFalse(contract["runtime_write"])
        self.assertFalse(contract["queue_write"])
        self.assertFalse(contract["order_request_created"])
        self.assertFalse(contract["send_order"])
        self.assertFalse(contract["real_ready_state_changed"])

    def test_real_ready_status_is_preview_only(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )
        contract = result["contracts"][0]

        self.assertEqual(contract["target_status"], "REAL_READY")
        self.assertEqual(contract["intended_status"], "REAL_READY")
        self.assertEqual(contract["status"], "REAL_READY")
        self.assertFalse(contract["real_ready_state_changed"])

    def test_source_warning_preserved(self):
        result = build_sell_execution_contract_preview(
            _authorization_preview([_authorized_candidate("METHOD")])
        )

        self.assertIn("source_warning", result["warnings"])

    def test_forbidden_execution_modules_not_imported(self):
        source = inspect.getsource(subject)

        self.assertNotIn("execution_pipeline_controller", source)
        self.assertNotIn("signal_gate_execution_queue_bridge", source)
        self.assertNotIn("order_execution_request", source)
        self.assertNotIn("SendOrder", source)


if __name__ == "__main__":
    unittest.main()
