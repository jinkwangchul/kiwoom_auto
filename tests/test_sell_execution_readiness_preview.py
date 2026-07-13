from __future__ import annotations

from copy import deepcopy
import inspect
import unittest

import sell_execution_readiness_preview as subject
from sell_execution_readiness_preview import build_sell_execution_readiness_preview


def _stage(stage: str, *, ok: bool = True, unresolved: bool = False) -> dict:
    return {
        "stage": stage,
        "ok": ok,
        "unresolved": unresolved,
        "warnings": [],
    }


def _candidate(
    *,
    status: str = "READY",
    action_source: str = "METHOD",
    hoga: str = "LIMIT",
    pipeline_ok: bool = True,
    final_guard_ok: bool = True,
    include_execution_preview: bool = True,
    include_final_guard: bool = True,
    include_lock: bool = True,
    include_hash: bool = True,
    include_request: bool = True,
) -> dict:
    candidate = {
        "status": status,
        "candidate_index": 0,
        "action_source": action_source,
        "candidate_snapshot": {
            "id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "side": "SELL",
            "order_type": "SELL",
            "hoga": hoga,
            "price": None if hoga == "MARKET" else 85000,
            "quantity": 10,
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "send_order": False,
            "broker_api_called": False,
            "real_ready_state_changed": False,
            "order_request_created": False,
        },
        "pipeline_result": {
            "ok": pipeline_ok,
            "stage": "EXECUTION_PREVIEW_PIPELINE",
            "blocked_stage": None if pipeline_ok else "final_guard",
            "blocked_reason": None if pipeline_ok else "guard blocked",
            "pipeline": {},
            "warnings": [],
        },
        "execution_preview": _stage("EXECUTION_PREVIEW"),
        "final_guard": {"stage": "FINAL_EXECUTION_GUARD", "ok": final_guard_ok, "blocked_reasons": []},
        "lock_preview": _stage("ORDER_LOCK_PREVIEW"),
        "request_hash_preview": _stage("REQUEST_HASH_PREVIEW"),
        "execution_request_preview": _stage("EXECUTION_REQUEST_PREVIEW"),
        "warnings": ["candidate_warning"],
        "reasons": [],
    }
    if not include_execution_preview:
        candidate.pop("execution_preview")
    if not include_final_guard:
        candidate.pop("final_guard")
    if not include_lock:
        candidate.pop("lock_preview")
    if not include_hash:
        candidate.pop("request_hash_preview")
    if not include_request:
        candidate.pop("execution_request_preview")
    return candidate


def _common_preview(*candidates: dict, status: str = "READY") -> dict:
    return {
        "preview_type": "SELL_COMMON_EXECUTION_PREVIEW_ADAPTER",
        "preview_only": True,
        "execution_connected": False,
        "pipeline_preview_called": bool(candidates),
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "status": status,
        "candidate_results": list(candidates),
        "blocked_candidates": [],
        "summary": {
            "candidate_count": len(candidates),
            "ready_candidate_count": len(candidates) if status == "READY" else 0,
            "blocked_candidate_count": 0,
            "invalid_candidate_count": 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": ["common_warning"],
        "reasons": [],
    }


class SellExecutionReadinessPreviewTests(unittest.TestCase):
    def test_single_limit_candidate_ready(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate()))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Preview")
        self.assertIsNone(result["routine_dependency"])
        self.assertTrue(result["readiness_ready"])
        self.assertEqual(result["summary"]["ready_candidate_count"], 1)
        self.assertEqual(result["candidate_readiness"][0]["status"], "READY")
        self.assertEqual(len(result["ready_candidates"]), 1)

    def test_multiple_limit_candidates_preserved_without_selection(self):
        first = _candidate()
        second = _candidate(action_source="COMPLETION")

        result = build_sell_execution_readiness_preview(_common_preview(first, second))

        self.assertEqual(len(result["candidate_readiness"]), 2)
        self.assertEqual([item["action_source"] for item in result["candidate_readiness"]], ["METHOD", "COMPLETION"])
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])
        self.assertFalse(result["candidate_readiness"][0]["priority_selected"])
        self.assertFalse(result["candidate_readiness"][1]["auto_selected"])

    def test_common_preview_blocked_blocks_result(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(), status="BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["candidate_readiness"], [])
        self.assertIn("common_execution_preview status is BLOCKED", result["reasons"])

    def test_common_preview_invalid_invalidates_result(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(), status="INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("common_execution_preview status is INVALID", result["reasons"])

    def test_input_must_be_dict(self):
        result = build_sell_execution_readiness_preview(None)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("common_execution_preview must be a dict", result["reasons"])

    def test_wrong_preview_type_invalid(self):
        preview = _common_preview(_candidate())
        preview["preview_type"] = "OTHER"

        result = build_sell_execution_readiness_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_top_safety_flag_invalid(self):
        preview = _common_preview(_candidate())
        preview["runtime_write"] = True

        result = build_sell_execution_readiness_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_results_must_be_list(self):
        preview = _common_preview(_candidate())
        preview["candidate_results"] = {}

        result = build_sell_execution_readiness_preview(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("candidate_results must be a list", result["reasons"])

    def test_candidate_must_be_dict(self):
        result = build_sell_execution_readiness_preview(_common_preview("bad"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["candidate_readiness"][0]["status"], "INVALID")

    def test_candidate_status_blocked(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(status="BLOCKED")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("candidate status is not READY", result["candidate_readiness"][0]["reasons"])

    def test_candidate_status_invalid(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(status="INVALID")))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("candidate status is INVALID", result["candidate_readiness"][0]["reasons"])

    def test_execution_preview_required(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(include_execution_preview=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is required", result["candidate_readiness"][0]["reasons"])

    def test_execution_preview_failure_blocks(self):
        candidate = _candidate()
        candidate["execution_preview"]["ok"] = False

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_execution_preview_empty_dict_blocks(self):
        candidate = _candidate()
        candidate["execution_preview"] = {}

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_stage_ok_missing_blocks(self):
        candidate = _candidate()
        candidate["execution_preview"].pop("ok")

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_stage_ok_none_blocks(self):
        candidate = _candidate()
        candidate["execution_preview"]["ok"] = None

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_wrong_stage_name_blocks(self):
        candidate = _candidate()
        candidate["execution_preview"]["stage"] = "WRONG_STAGE"

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_unresolved_true_blocks(self):
        candidate = _candidate()
        candidate["execution_preview"]["unresolved"] = True

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_final_guard_required(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(include_final_guard=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("final_guard is required", result["candidate_readiness"][0]["reasons"])

    def test_final_guard_failure_blocks(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(final_guard_ok=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("final_guard is not ready", result["candidate_readiness"][0]["reasons"])

    def test_lock_preview_required(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(include_lock=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("lock_preview is required", result["candidate_readiness"][0]["reasons"])

    def test_lock_preview_empty_dict_blocks(self):
        candidate = _candidate()
        candidate["lock_preview"] = {}

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("lock_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_request_hash_preview_required(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(include_hash=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("request_hash_preview is required", result["candidate_readiness"][0]["reasons"])

    def test_request_hash_preview_empty_dict_blocks(self):
        candidate = _candidate()
        candidate["request_hash_preview"] = {}

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("request_hash_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_request_hash_preview_failure_blocks(self):
        candidate = _candidate()
        candidate["request_hash_preview"]["unresolved"] = True

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("request_hash_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_execution_request_preview_required(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(include_request=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_request_preview is required", result["candidate_readiness"][0]["reasons"])

    def test_execution_request_preview_empty_dict_blocks(self):
        candidate = _candidate()
        candidate["execution_request_preview"] = {}

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_request_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_execution_request_preview_failure_blocks(self):
        candidate = _candidate()
        candidate["execution_request_preview"]["ok"] = False

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_request_preview is not ready", result["candidate_readiness"][0]["reasons"])

    def test_market_candidate_remains_blocked_without_price_substitution(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(hoga="MARKET")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["candidate_readiness"][0]["candidate_snapshot"]["candidate_snapshot"]["price"])
        self.assertIn("MARKET candidates stay blocked", result["candidate_readiness"][0]["reasons"][0])

    def test_pending_candidate_blocked_on_cancel_path(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(action_source="PENDING")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["candidate_readiness"][0]["reasons"][0])

    def test_cancel_pending_order_candidate_blocked_on_cancel_path(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(action_source="CANCEL_PENDING_ORDER")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["candidate_readiness"][0]["reasons"][0])

    def test_pipeline_result_not_ok_blocks(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate(pipeline_ok=False)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("guard blocked", result["candidate_readiness"][0]["reasons"])

    def test_candidate_safety_flag_invalid(self):
        candidate = _candidate()
        candidate["queue_write"] = True

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["candidate_readiness"][0]["status"], "INVALID")

    def test_candidate_side_contract_violation_invalid(self):
        candidate = _candidate()
        candidate["candidate_snapshot"]["side"] = "BUY"

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("candidate_snapshot side must be SELL", result["candidate_readiness"][0]["reasons"])

    def test_candidate_order_type_contract_violation_invalid(self):
        candidate = _candidate()
        candidate["candidate_snapshot"]["order_type"] = "BUY"

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("candidate_snapshot order_type must be SELL", result["candidate_readiness"][0]["reasons"])

    def test_candidate_identity_missing_blocks(self):
        candidate = _candidate()
        candidate["candidate_snapshot"]["source_signal_id"] = ""

        result = build_sell_execution_readiness_preview(_common_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("candidate_snapshot source_signal_id is required", result["candidate_readiness"][0]["reasons"])

    def test_summary_stage_counts(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate()))

        self.assertEqual(result["summary"]["final_guard_pass_count"], 1)
        self.assertEqual(result["summary"]["lock_preview_confirmed_count"], 1)
        self.assertEqual(result["summary"]["request_hash_preview_confirmed_count"], 1)
        self.assertEqual(result["summary"]["execution_request_preview_confirmed_count"], 1)
        self.assertEqual(result["blocked_candidate_readiness"], [])

    def test_safety_flags_remain_false(self):
        result = build_sell_execution_readiness_preview(_common_preview(_candidate()))

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
        ):
            self.assertFalse(result[flag])

    def test_input_snapshot_deepcopy_and_input_immutable(self):
        preview = _common_preview(_candidate())
        original = deepcopy(preview)

        result = build_sell_execution_readiness_preview(preview)
        preview["candidate_results"][0]["candidate_snapshot"]["id"] = "CHANGED"
        result["candidate_readiness"][0]["candidate_snapshot"]["candidate_snapshot"]["id"] = "MUTATED"

        self.assertEqual(original, _common_preview(_candidate()))
        self.assertEqual(result["common_execution_preview_snapshot"]["candidate_results"][0]["candidate_snapshot"]["id"], "ORDER_1")

    def test_source_warnings_and_blocked_candidates_preserved(self):
        preview = _common_preview(_candidate())
        preview["blocked_candidates"] = [{"status": "BLOCKED", "reasons": ["preblocked"]}]

        result = build_sell_execution_readiness_preview(preview)

        self.assertIn("common_warning", result["warnings"])
        self.assertEqual(result["blocked_candidates"], [{"status": "BLOCKED", "reasons": ["preblocked"]}])

    def test_no_runtime_queue_sendorder_or_order_request_references(self):
        source = inspect.getsource(subject)

        self.assertNotIn("signal_gate_execution_queue_bridge", source)
        self.assertNotIn("execution_queue_writer", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("OrderRequest(", source)
        self.assertNotIn("order_queue.json", source)


if __name__ == "__main__":
    unittest.main()
