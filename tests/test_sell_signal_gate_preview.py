from __future__ import annotations

from copy import deepcopy
import inspect
import unittest
from unittest import mock

import sell_signal_gate_preview as subject
from sell_signal_gate_preview import build_sell_signal_gate_preview


def _order_candidate(
    *,
    order_id: str = "ORDER_1",
    source_signal_id: str = "SIG_1",
    side: str = "SELL",
    hoga: str = "LIMIT",
) -> dict:
    return {
        "id": order_id,
        "source_signal_id": source_signal_id,
        "code": "005930",
        "symbol": "005930",
        "side": side,
        "order_type": side,
        "hoga": hoga,
        "price": None if hoga == "MARKET" else 80000,
        "quantity": 10,
        "method_set": "setting_a",
    }


def _readiness_candidate(
    *,
    index: int = 0,
    status: str = "READY",
    readiness_ready: bool = True,
    action_source: str = "METHOD",
    order_candidate: dict | None = None,
) -> dict:
    return {
        "status": status,
        "readiness_ready": readiness_ready,
        "candidate_index": index,
        "action_source": action_source,
        "candidate_snapshot": {
            "status": "READY",
            "candidate_index": index,
            "action_source": action_source,
            "candidate_snapshot": deepcopy(order_candidate or _order_candidate()),
            "method_set": "setting_a",
        },
        "stage_checks": {
            "execution_preview": True,
            "final_guard": True,
            "lock_preview": True,
            "request_hash_preview": True,
            "execution_request_preview": True,
        },
        "reasons": [],
        "warnings": ["candidate_warning"],
    }


def _readiness_preview(*candidates: dict, status: str = "READY") -> dict:
    return {
        "preview_type": "SELL_EXECUTION_READINESS_PREVIEW",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Preview",
        "routine_dependency": None,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "status": status,
        "readiness_ready": status == "READY",
        "ready_candidates": list(candidates),
        "candidate_readiness": list(candidates),
        "blocked_candidate_readiness": [],
        "blocked_candidates": [],
        "summary": {
            "candidate_count": len(candidates),
            "ready_candidate_count": len(candidates) if status == "READY" else 0,
            "blocked_candidate_count": 0,
            "invalid_candidate_count": 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": ["upstream_warning"],
        "reasons": [],
    }


class SellSignalGatePreviewTests(unittest.TestCase):
    def test_single_sell_ready_candidate_opens_gate_and_overall_ready(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["signal_gate_ready"])
        self.assertEqual(result["summary"]["opened_gate_count"], 1)
        self.assertEqual(result["opened_gates"][0]["gate_result"], "OPEN")

    def test_multiple_ready_candidates_all_preserved_without_selection(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(
                _readiness_candidate(index=0, action_source="METHOD"),
                _readiness_candidate(index=1, action_source="COMPLETION"),
            )
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["opened_gates"]), 2)
        self.assertEqual([item["action_source"] for item in result["candidate_gates"]], ["METHOD", "COMPLETION"])
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_common_gate_allows_sell_signal_to_open(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        gate = result["opened_gates"][0]["gate_preview"]
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["stage"], "SIGNAL_QUEUE_GATE")
        self.assertEqual(gate["gate_result"], "OPEN")
        self.assertEqual(gate["signal"], "SELL")

    def test_source_candidate_index_preserved(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate(index=7)))

        self.assertEqual(result["opened_gates"][0]["source_candidate_index"], 7)
        self.assertEqual(result["opened_gates"][0]["signal_queue_candidate"]["signal_index"], 7)

    def test_source_signal_id_preserved(self):
        order = _order_candidate(source_signal_id="SIG_SELL_7")

        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate(order_candidate=order)))

        self.assertEqual(result["opened_gates"][0]["source_signal_id"], "SIG_SELL_7")

    def test_source_order_id_preserved(self):
        order = _order_candidate(order_id="ORDER_SELL_7")

        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate(order_candidate=order)))

        self.assertEqual(result["opened_gates"][0]["source_order_id"], "ORDER_SELL_7")

    def test_action_source_preserved(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(action_source="COMPLETION"))
        )

        self.assertEqual(result["opened_gates"][0]["action_source"], "COMPLETION")
        self.assertEqual(result["opened_gates"][0]["signal_queue_candidate"]["rule_source"], "COMPLETION")

    def test_adapter_preserves_identity_not_returned_by_common_gate(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        opened = result["opened_gates"][0]
        self.assertEqual(opened["source_signal_id"], "SIG_1")
        self.assertEqual(opened["source_order_id"], "ORDER_1")
        self.assertEqual(opened["action_source"], "METHOD")
        self.assertIsNotNone(opened["source_readiness_candidate"])

    def test_input_must_be_dict(self):
        result = build_sell_signal_gate_preview(None)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("execution_readiness_preview must be a dict", result["reasons"])

    def test_preview_type_mismatch_invalid(self):
        preview = _readiness_preview(_readiness_candidate())
        preview["preview_type"] = "OTHER"

        result = build_sell_signal_gate_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_preview_only_false_invalid(self):
        preview = _readiness_preview(_readiness_candidate())
        preview["preview_only"] = False

        result = build_sell_signal_gate_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_top_safety_flag_violation_invalid(self):
        preview = _readiness_preview(_readiness_candidate())
        preview["queue_write"] = True

        result = build_sell_signal_gate_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_ready_candidates_must_be_list(self):
        preview = _readiness_preview(_readiness_candidate())
        preview["ready_candidates"] = {}

        result = build_sell_signal_gate_preview(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("ready_candidates must be a list", result["reasons"])

    def test_candidate_must_be_dict(self):
        result = build_sell_signal_gate_preview(_readiness_preview("bad"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["candidate_gates"][0]["status"], "INVALID")

    def test_candidate_status_not_ready_blocked(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(status="BLOCKED"))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("readiness candidate status is not READY", result["candidate_gates"][0]["reasons"])

    def test_readiness_ready_false_blocked(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(readiness_ready=False))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("readiness candidate readiness_ready must be True", result["candidate_gates"][0]["reasons"])

    def test_candidate_snapshot_missing_invalid(self):
        candidate = _readiness_candidate()
        candidate.pop("candidate_snapshot")

        result = build_sell_signal_gate_preview(_readiness_preview(candidate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("candidate_snapshot must be a dict", result["candidate_gates"][0]["reasons"])

    def test_nested_order_candidate_missing_invalid(self):
        candidate = _readiness_candidate()
        candidate["candidate_snapshot"].pop("candidate_snapshot")

        result = build_sell_signal_gate_preview(_readiness_preview(candidate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("nested order candidate_snapshot must be a dict", result["candidate_gates"][0]["reasons"])

    def test_side_not_sell_invalid(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(order_candidate=_order_candidate(side="BUY")))
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("order candidate side must be SELL", result["candidate_gates"][0]["reasons"])

    def test_missing_source_signal_id_blocks(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(order_candidate=_order_candidate(source_signal_id="")))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("source_signal_id is required", result["candidate_gates"][0]["reasons"])

    def test_missing_order_id_blocks(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(order_candidate=_order_candidate(order_id="")))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("order id is required", result["candidate_gates"][0]["reasons"])

    def test_market_candidate_blocks(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(order_candidate=_order_candidate(hoga="MARKET")))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MARKET candidates stay blocked", result["candidate_gates"][0]["reasons"][0])

    def test_pending_candidate_blocks(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(action_source="PENDING"))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["candidate_gates"][0]["reasons"][0])

    def test_cancel_pending_order_candidate_blocks(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(action_source="CANCEL_PENDING_ORDER"))
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["candidate_gates"][0]["reasons"][0])

    def test_upstream_overall_blocked_blocks_and_preserves_blocked_candidates(self):
        preview = _readiness_preview(status="BLOCKED")
        preview["blocked_candidate_readiness"] = [{"status": "BLOCKED", "reasons": ["upstream"]}]

        result = build_sell_signal_gate_preview(preview)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["upstream_blocked_candidates"], [{"status": "BLOCKED", "reasons": ["upstream"]}])

    def test_upstream_overall_invalid_invalidates(self):
        result = build_sell_signal_gate_preview(_readiness_preview(status="INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("execution_readiness_preview status is INVALID", result["reasons"])

    def test_upstream_blocked_candidates_preserved(self):
        preview = _readiness_preview(_readiness_candidate())
        preview["blocked_candidate_readiness"] = [{"status": "BLOCKED", "reasons": ["market"]}]

        result = build_sell_signal_gate_preview(preview)

        self.assertEqual(result["summary"]["upstream_blocked_count"], 1)
        self.assertEqual(result["upstream_blocked_candidates"][0]["reasons"], ["market"])

    def test_common_gate_blocked_goes_to_blocked_gates(self):
        with mock.patch("sell_signal_gate_preview.build_signal_queue_gate") as gate:
            gate.return_value = {
                "ok": False,
                "stage": "SIGNAL_QUEUE_GATE",
                "gate_result": "BLOCKED",
                "signal": "SELL",
                "blocked_reasons": ["gate blocked"],
            }

            result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["blocked_gates"][0]["gate_result"], "BLOCKED")
        self.assertIn("gate blocked", result["blocked_gates"][0]["reasons"])

    def test_common_gate_ignore_goes_to_ignored_gates(self):
        with mock.patch("sell_signal_gate_preview.build_signal_queue_gate") as gate:
            gate.return_value = {
                "ok": True,
                "stage": "SIGNAL_QUEUE_GATE",
                "gate_result": "IGNORE",
                "signal": "SELL",
                "blocked_reasons": [],
            }

            result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["ignored_gates"]), 1)
        self.assertEqual(result["ignored_gates"][0]["status"], "IGNORE")

    def test_gate_stage_mismatch_invalid(self):
        with mock.patch("sell_signal_gate_preview.build_signal_queue_gate") as gate:
            gate.return_value = {"ok": True, "stage": "OTHER", "gate_result": "OPEN", "signal": "SELL"}

            result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("gate_preview stage is invalid", result["candidate_gates"][0]["reasons"])

    def test_gate_signal_mismatch_invalid(self):
        with mock.patch("sell_signal_gate_preview.build_signal_queue_gate") as gate:
            gate.return_value = {"ok": True, "stage": "SIGNAL_QUEUE_GATE", "gate_result": "OPEN", "signal": "BUY"}

            result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("gate_preview signal is invalid", result["candidate_gates"][0]["reasons"])

    def test_gate_result_must_be_dict(self):
        with mock.patch("sell_signal_gate_preview.build_signal_queue_gate", return_value="bad"):
            result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("gate_preview must be a dict", result["candidate_gates"][0]["reasons"])

    def test_multiple_candidates_are_not_auto_selected(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(_readiness_candidate(index=0), _readiness_candidate(index=1))
        )

        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])
        self.assertFalse(result["candidate_gates"][0]["priority_selected"])
        self.assertFalse(result["candidate_gates"][1]["auto_selected"])

    def test_priority_selected_false(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertFalse(result["summary"]["priority_selected"])

    def test_auto_selected_false(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertFalse(result["summary"]["auto_selected"])

    def test_queue_preview_called_false(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertFalse(result["queue_writer_preview_called"])
        self.assertFalse(result["summary"]["queue_preview_called"])

    def test_safety_flags_remain_false(self):
        result = build_sell_signal_gate_preview(_readiness_preview(_readiness_candidate()))

        self.assertTrue(result["preview_only"])
        for flag in (
            "execution_connected",
            "runtime_write",
            "queue_write",
            "file_write",
            "send_order",
            "broker_api_called",
            "real_ready_state_changed",
            "order_request_created",
            "queue_writer_preview_called",
        ):
            self.assertFalse(result[flag])

    def test_input_object_not_mutated(self):
        preview = _readiness_preview(_readiness_candidate())
        original = deepcopy(preview)

        result = build_sell_signal_gate_preview(preview)
        result["candidate_gates"][0]["source_readiness_candidate"]["candidate_snapshot"]["candidate_snapshot"]["id"] = "MUTATED"

        self.assertEqual(preview, original)

    def test_warnings_and_reasons_preserved(self):
        candidate = _readiness_candidate()
        candidate["warnings"] = ["local_warning"]
        preview = _readiness_preview(candidate)
        preview["warnings"] = ["top_warning"]

        result = build_sell_signal_gate_preview(preview)

        self.assertIn("top_warning", result["warnings"])
        self.assertIn("local_warning", result["candidate_gates"][0]["warnings"])

    def test_does_not_import_or_call_queue_bridge_or_writer(self):
        source = inspect.getsource(subject)

        self.assertNotIn("signal_gate_execution_queue_bridge", source)
        self.assertNotIn("execution_queue_writer", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("OrderRequest(", source)
        self.assertNotIn("ORDER_QUEUED", source)

    def test_summary_counts_mixed_candidates(self):
        result = build_sell_signal_gate_preview(
            _readiness_preview(
                _readiness_candidate(index=0),
                _readiness_candidate(index=1, order_candidate=_order_candidate(hoga="MARKET")),
            )
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["summary"]["candidate_count"], 2)
        self.assertEqual(result["summary"]["opened_gate_count"], 1)
        self.assertEqual(result["summary"]["blocked_gate_count"], 1)


if __name__ == "__main__":
    unittest.main()
