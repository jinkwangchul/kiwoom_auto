from __future__ import annotations

from copy import deepcopy
import inspect
import unittest
from unittest import mock

import sell_common_execution_preview_adapter as subject
from sell_common_execution_preview_adapter import build_sell_common_execution_preview


def _guard(**overrides):
    guard = {
        "operator_confirmed": True,
        "real_trade_enabled": True,
        "account_no": "12345678",
    }
    guard.update(overrides)
    return guard


def _candidate(
    action_source="METHOD",
    *,
    order_id="ORDER_1",
    source_signal_id="SIG_1",
    code="003550",
    symbol="003550",
    status="REAL_READY",
    execution_enabled=True,
    side="SELL",
    quantity=10,
    price=85000,
    hoga="LIMIT",
    order_type="SELL",
):
    return {
        "candidate_type": "SELL_REAL_READY_ORDER_CANDIDATE_PREVIEW",
        "candidate_status": "READY",
        "action_source": action_source,
        "id": order_id,
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "code": code,
        "symbol": symbol,
        "status": status,
        "execution_enabled": execution_enabled,
        "side": side,
        "quantity": quantity,
        "price": price,
        "hoga": hoga,
        "order_type": order_type,
        "order_intent": {
            "side": side,
            "hoga": hoga,
            "action_source": action_source,
        },
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "warnings": [],
        "reasons": [],
    }


def _adapter_preview(candidates):
    return {
        "preview_type": "SELL_REAL_READY_ADAPTER_PREVIEW",
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "status": "READY",
        "order_candidates": candidates,
        "blocked_candidates": [],
        "summary": {},
        "warnings": ["adapter_warning"],
        "reasons": [],
    }


class SellCommonExecutionPreviewAdapterTests(unittest.TestCase):
    def test_method_limit_ready(self):
        result = build_sell_common_execution_preview(_adapter_preview([_candidate("METHOD")]), _guard())

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["candidate_results"][0]["action_source"], "METHOD")
        self.assertTrue(result["common_execution_ready"])

    def test_completion_limit_ready(self):
        result = build_sell_common_execution_preview(_adapter_preview([_candidate("COMPLETION")]), _guard())

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["candidate_results"][0]["action_source"], "COMPLETION")

    def test_multiple_candidates_are_processed_independently(self):
        result = build_sell_common_execution_preview(
            _adapter_preview(
                [
                    _candidate("METHOD", order_id="M1", source_signal_id="S1"),
                    _candidate("COMPLETION", order_id="C1", source_signal_id="S2"),
                ]
            ),
            _guard(),
        )

        self.assertEqual(len(result["candidate_results"]), 2)
        self.assertEqual(result["summary"]["ready_candidate_count"], 2)

    def test_input_order_preserved(self):
        result = build_sell_common_execution_preview(
            _adapter_preview(
                [
                    _candidate("COMPLETION", order_id="C1", source_signal_id="S2"),
                    _candidate("METHOD", order_id="M1", source_signal_id="S1"),
                ]
            ),
            _guard(),
        )

        self.assertEqual(
            [item["candidate_snapshot"]["id"] for item in result["candidate_results"]],
            ["C1", "M1"],
        )

    def test_guard_missing_blocks_without_pipeline_call(self):
        with mock.patch.object(subject, "run_execution_preview_pipeline") as pipeline:
            result = build_sell_common_execution_preview(_adapter_preview([_candidate("METHOD")]))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["common_execution_ready"])
        self.assertFalse(result["pipeline_preview_called"])
        pipeline.assert_not_called()

    def test_operator_confirmed_false_blocks_without_pipeline_call(self):
        with mock.patch.object(subject, "run_execution_preview_pipeline") as pipeline:
            result = build_sell_common_execution_preview(
                _adapter_preview([_candidate("METHOD")]),
                _guard(operator_confirmed=False),
            )

        self.assertEqual(result["status"], "BLOCKED")
        pipeline.assert_not_called()

    def test_real_trade_enabled_false_blocks_without_pipeline_call(self):
        with mock.patch.object(subject, "run_execution_preview_pipeline") as pipeline:
            result = build_sell_common_execution_preview(
                _adapter_preview([_candidate("METHOD")]),
                _guard(real_trade_enabled=False),
            )

        self.assertEqual(result["status"], "BLOCKED")
        pipeline.assert_not_called()

    def test_market_blocked_without_price_substitution(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", hoga="MARKET", price=None)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["blocked_candidates"][0]["candidate_snapshot"]["price"])

    def test_pending_excluded_from_order_path(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("PENDING", hoga=None, price=None, order_type=None)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["blocked_candidates"][0]["reasons"][0])

    def test_cancel_pending_order_excluded(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("CANCEL_PENDING_ORDER", hoga=None, price=None, order_type=None)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["candidate_results"], [])

    def test_limit_missing_price_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", price=None)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("LIMIT price", result["blocked_candidates"][0]["reasons"][0])

    def test_limit_zero_price_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", price=0)]),
            _guard(),
        )

        self.assertEqual(result["blocked_candidates"][0]["status"], "BLOCKED")

    def test_limit_negative_price_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", price=-1)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_pipeline_result_preserved(self):
        result = build_sell_common_execution_preview(_adapter_preview([_candidate("METHOD")]), _guard())

        item = result["candidate_results"][0]
        self.assertIn("pipeline_result", item)
        self.assertTrue(item["pipeline_result"]["ok"])

    def test_stage_results_preserved(self):
        result = build_sell_common_execution_preview(_adapter_preview([_candidate("METHOD")]), _guard())
        item = result["candidate_results"][0]

        self.assertEqual(item["execution_preview"]["stage"], "EXECUTION_PREVIEW")
        self.assertEqual(item["final_guard"]["stage"], "FINAL_EXECUTION_GUARD")
        self.assertEqual(item["lock_preview"]["stage"], "ORDER_LOCK_PREVIEW")
        self.assertEqual(item["request_hash_preview"]["stage"], "REQUEST_HASH_PREVIEW")
        self.assertEqual(item["execution_request_preview"]["stage"], "EXECUTION_REQUEST_PREVIEW")

    def test_pipeline_blocked_candidate_does_not_block_later_candidate(self):
        first = _candidate("METHOD", order_id="BAD", source_signal_id="BAD")
        first["order_intent"]["hoga"] = "UNKNOWN"
        second = _candidate("COMPLETION", order_id="GOOD", source_signal_id="GOOD")

        result = build_sell_common_execution_preview(_adapter_preview([first, second]), _guard())

        self.assertEqual(result["status"], "READY")
        self.assertEqual([item["status"] for item in result["candidate_results"]], ["BLOCKED", "READY"])

    def test_pipeline_called_for_each_eligible_candidate(self):
        with mock.patch.object(
            subject,
            "run_execution_preview_pipeline",
            return_value={"ok": True, "pipeline": {}, "warnings": []},
        ) as pipeline:
            result = build_sell_common_execution_preview(
                _adapter_preview(
                    [
                        _candidate("METHOD", order_id="M1", source_signal_id="S1"),
                        _candidate("COMPLETION", order_id="C1", source_signal_id="S2"),
                    ]
                ),
                _guard(),
            )

        self.assertEqual(pipeline.call_count, 2)
        self.assertTrue(result["pipeline_preview_called"])

    def test_candidate_with_status_not_real_ready_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", status="READY")]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_execution_enabled_false_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", execution_enabled=False)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_side_buy_invalid(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", side="BUY", order_type="BUY")]),
            _guard(),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_missing_id_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", order_id="")]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_source_signal_id_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", source_signal_id="")]),
            _guard(),
        )

        self.assertEqual(result["blocked_candidates"][0]["status"], "BLOCKED")

    def test_missing_code_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", code="")]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_quantity_blocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", quantity=None)]),
            _guard(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_order_intent_blocked(self):
        candidate = _candidate("METHOD")
        candidate.pop("order_intent")

        result = build_sell_common_execution_preview(_adapter_preview([candidate]), _guard())

        self.assertEqual(result["status"], "BLOCKED")

    def test_wrong_preview_type_invalid(self):
        result = build_sell_common_execution_preview({"preview_type": "OTHER", "order_candidates": []}, _guard())

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["common_execution_ready"])

    def test_adapter_preview_must_be_dict(self):
        result = build_sell_common_execution_preview(None, _guard())

        self.assertEqual(result["status"], "INVALID")

    def test_order_candidates_must_be_list(self):
        result = build_sell_common_execution_preview(
            {"preview_type": "SELL_REAL_READY_ADAPTER_PREVIEW", "order_candidates": {}},
            _guard(),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_must_be_dict(self):
        result = build_sell_common_execution_preview(
            _adapter_preview(["bad"]),
            _guard(),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_top_safety_flag_invalid(self):
        preview = _adapter_preview([_candidate("METHOD")])
        preview["runtime_write"] = True

        result = build_sell_common_execution_preview(preview, _guard())

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_safety_flag_invalid(self):
        candidate = _candidate("METHOD")
        candidate["queue_write"] = True

        result = build_sell_common_execution_preview(_adapter_preview([candidate]), _guard())

        self.assertEqual(result["status"], "INVALID")

    def test_input_object_immutable(self):
        preview = _adapter_preview([_candidate("METHOD")])
        guard = _guard()
        original_preview = deepcopy(preview)
        original_guard = deepcopy(guard)

        build_sell_common_execution_preview(preview, guard)

        self.assertEqual(preview, original_preview)
        self.assertEqual(guard, original_guard)

    def test_output_snapshots_are_deepcopy(self):
        preview = _adapter_preview([_candidate("METHOD")])
        result = build_sell_common_execution_preview(preview, _guard())

        preview["order_candidates"][0]["code"] = "CHANGED"
        result["candidate_results"][0]["candidate_snapshot"]["code"] = "OTHER"

        self.assertEqual(result["adapter_preview_snapshot"]["order_candidates"][0]["code"], "003550")

    def test_repeated_execution_deterministic(self):
        preview = _adapter_preview([_candidate("METHOD")])
        guard = _guard()

        first = build_sell_common_execution_preview(preview, guard)
        second = build_sell_common_execution_preview(preview, guard)

        self.assertEqual(first, second)

    def test_top_level_safety_flags(self):
        result = build_sell_common_execution_preview(_adapter_preview([_candidate("METHOD")]), _guard())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["file_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_no_priority_or_auto_selection(self):
        result = build_sell_common_execution_preview(
            _adapter_preview(
                [
                    _candidate("METHOD", order_id="M1", source_signal_id="S1"),
                    _candidate("COMPLETION", order_id="C1", source_signal_id="S2"),
                ]
            ),
            _guard(),
        )

        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_source_warning_preserved(self):
        result = build_sell_common_execution_preview(_adapter_preview([_candidate("METHOD")]), _guard())

        self.assertIn("adapter_warning", result["warnings"])

    def test_empty_candidates_blocked(self):
        result = build_sell_common_execution_preview(_adapter_preview([]), _guard())

        self.assertEqual(result["status"], "BLOCKED")

    def test_pipeline_preview_called_false_when_all_preblocked(self):
        result = build_sell_common_execution_preview(
            _adapter_preview([_candidate("METHOD", hoga="MARKET", price=None)]),
            _guard(),
        )

        self.assertFalse(result["pipeline_preview_called"])

    def test_pipeline_block_reason_is_preserved(self):
        bad = _candidate("METHOD")
        bad["order_intent"]["side"] = "UNKNOWN"

        result = build_sell_common_execution_preview(_adapter_preview([bad]), _guard())

        self.assertEqual(result["candidate_results"][0]["status"], "BLOCKED")
        self.assertTrue(result["candidate_results"][0]["reasons"])

    def test_queue_gate_runtime_writers_not_referenced(self):
        source = inspect.getsource(subject)

        self.assertNotIn("signal_gate_execution_queue_bridge", source)
        self.assertNotIn("execution_queue_writer", source)
        self.assertNotIn("runtime_writer", source)
        self.assertNotIn("full_readiness", source)

    def test_file_paths_not_referenced(self):
        source = inspect.getsource(subject)

        self.assertNotIn("order_queue.json", source)
        self.assertNotIn("state.json", source)


if __name__ == "__main__":
    unittest.main()
