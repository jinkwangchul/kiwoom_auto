from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from sell_order_candidate_inspector import inspect_sell_order_candidates


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellOrderCandidateInspectorTest(unittest.TestCase):
    def _candidate(self, action_source="METHOD", **overrides):
        source_previews = {"method_preview": {"status": "READY", "nested": {"value": 1}}}
        candidate = {
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "send_order": False,
            "status": "READY",
            "symbol": "005930",
            "side": "SELL",
            "signal_id": "SIG_SELL_1",
            "method_set": "setting_a",
            "action_source": action_source,
            "order_id": None,
            "quantity": 10,
            "price": None,
            "hoga": "MARKET",
            "order_type": "SELL",
            "order_request_created": False,
            "candidate_created": False,
            "source_previews": source_previews,
            "reasons": [],
            "warnings": [],
        }
        if action_source == "COMPLETION":
            candidate["source_previews"] = {"completion": {"status": "READY"}}
        if action_source == "PENDING":
            candidate.update(
                {
                    "order_id": "ORD-1",
                    "hoga": None,
                    "order_type": None,
                    "source_previews": {"pending": {"status": "READY"}},
                }
            )
        candidate.update(overrides)
        return candidate

    def _preview(self, *candidates, **overrides):
        preview = {
            "preview_type": "SELL_ORDER_CANDIDATE_PREVIEW",
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "send_order": False,
            "status": "READY",
            "candidates": list(candidates),
            "warnings": [],
            "reasons": [],
        }
        preview.update(overrides)
        return preview

    def _single_inspection(self, result):
        self.assertEqual(1, len(result["inspected_candidates"]))
        return result["inspected_candidates"][0]

    def test_method_market_is_real_ready_eligible(self):
        result = inspect_sell_order_candidates(self._preview(self._candidate()))

        inspected = self._single_inspection(result)
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["real_ready_eligible"])
        self.assertEqual("METHOD", inspected["action_source"])
        self.assertTrue(inspected["real_ready_eligible"])

    def test_method_limit_is_real_ready_eligible(self):
        candidate = self._candidate(hoga="LIMIT", price=70000)
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("READY", inspected["status"])
        self.assertTrue(inspected["real_ready_eligible"])

    def test_method_limit_missing_price_is_blocked(self):
        candidate = self._candidate(hoga="LIMIT", price=None)
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", inspected["status"])
        self.assertIn("LIMIT price is required", inspected["reasons"])

    def test_method_invalid_hoga_is_invalid(self):
        candidate = self._candidate(hoga="AFTER_HOURS")
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("INVALID", result["status"])
        self.assertIn("hoga must be MARKET or LIMIT", inspected["reasons"])

    def test_completion_market_is_real_ready_eligible(self):
        candidate = self._candidate("COMPLETION")
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("READY", inspected["status"])
        self.assertEqual("COMPLETION", inspected["action_source"])
        self.assertTrue(inspected["real_ready_eligible"])

    def test_completion_price_present_is_invalid(self):
        candidate = self._candidate("COMPLETION", price=1)
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("INVALID", inspected["status"])
        self.assertIn("COMPLETION price must be None", inspected["reasons"])

    def test_pending_is_real_ready_eligible(self):
        candidate = self._candidate("PENDING")
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("READY", inspected["status"])
        self.assertEqual("PENDING", inspected["action_source"])
        self.assertTrue(inspected["real_ready_eligible"])

    def test_pending_missing_order_id_is_blocked(self):
        candidate = self._candidate("PENDING", order_id="")
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("BLOCKED", inspected["status"])
        self.assertIn("order_id is required", inspected["reasons"])

    def test_pending_allows_none_price_hoga_order_type(self):
        candidate = self._candidate("PENDING", price=None, hoga=None, order_type=None)
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("READY", inspected["status"])
        self.assertIsNone(inspected["normalized_candidate"]["price"])
        self.assertIsNone(inspected["normalized_candidate"]["hoga"])
        self.assertIsNone(inspected["normalized_candidate"]["order_type"])

    def test_safety_flag_violation_is_invalid(self):
        candidate = self._candidate(runtime_write=True)
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("INVALID", result["status"])
        self.assertIn("runtime_write must be False", inspected["reasons"])

    def test_missing_source_previews_is_blocked(self):
        candidate = self._candidate(source_previews={})
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("BLOCKED", inspected["status"])
        self.assertIn("source_previews.method_preview is required", inspected["reasons"])

    def test_unknown_action_source_is_invalid(self):
        candidate = self._candidate(action_source="UNKNOWN")
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("INVALID", result["status"])
        self.assertIn("unknown action_source: UNKNOWN", inspected["reasons"])

    def test_candidate_preview_type_error_is_invalid(self):
        result = inspect_sell_order_candidates([])

        self.assertEqual("INVALID", result["status"])
        self.assertIn("candidate_preview must be a dict", result["reasons"])

    def test_candidates_type_error_is_invalid(self):
        result = inspect_sell_order_candidates({"candidates": {}})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("candidates must be a list", result["reasons"])

    def test_candidate_item_type_error_is_invalid(self):
        result = inspect_sell_order_candidates(self._preview("not-a-candidate"))

        inspected = self._single_inspection(result)
        self.assertEqual("INVALID", result["status"])
        self.assertIn("candidate must be a dict", inspected["reasons"])

    def test_method_completion_pending_can_all_be_eligible(self):
        result = inspect_sell_order_candidates(
            self._preview(
                self._candidate(),
                self._candidate("COMPLETION"),
                self._candidate("PENDING"),
            )
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(3, result["candidate_count"])
        self.assertEqual(
            ["METHOD", "COMPLETION", "PENDING"],
            [item["action_source"] for item in result["inspected_candidates"]],
        )
        self.assertTrue(all(item["real_ready_eligible"] for item in result["inspected_candidates"]))

    def test_overall_real_ready_eligible_true_when_one_candidate_eligible(self):
        blocked = self._candidate(status="BLOCKED")
        ready = self._candidate("PENDING")
        result = inspect_sell_order_candidates(self._preview(blocked, ready))

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["real_ready_eligible"])

    def test_real_ready_status_is_never_created(self):
        result = inspect_sell_order_candidates(self._preview(self._candidate()))

        self.assertNotIn("REAL_READY", json.dumps(result, ensure_ascii=False))

    def test_input_warning_is_preserved(self):
        result = inspect_sell_order_candidates(
            self._preview(self._candidate(warnings=["candidate_warning"]), warnings=["multiple_ready_action_sources"])
        )

        self.assertIn("multiple_ready_action_sources", result["warnings"])
        self.assertIn("candidate_warning", result["warnings"])

    def test_normalized_candidate_is_deepcopy(self):
        candidate = self._candidate()
        result = inspect_sell_order_candidates(self._preview(candidate))

        normalized = result["inspected_candidates"][0]["normalized_candidate"]
        normalized["source_previews"]["method_preview"]["nested"]["value"] = 99

        self.assertEqual(1, candidate["source_previews"]["method_preview"]["nested"]["value"])

    def test_input_original_is_not_mutated(self):
        preview = self._preview(self._candidate())
        original = deepcopy(preview)

        inspect_sell_order_candidates(preview)

        self.assertEqual(original, preview)

    def test_runtime_queue_sendorder_are_not_called_or_written(self):
        runtime_file = ROOT / "runtime" / "order_queue.json"
        before = _sha256(runtime_file)

        result = inspect_sell_order_candidates(self._preview(self._candidate()))

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order"])
        self.assertEqual(before, _sha256(runtime_file))

    def test_numeric_format_error_is_invalid(self):
        candidate = self._candidate(quantity="ten")
        result = inspect_sell_order_candidates(self._preview(candidate))

        inspected = self._single_inspection(result)
        self.assertEqual("INVALID", result["status"])
        self.assertIn("quantity must be numeric", inspected["reasons"])

    def test_not_applicable_candidate_is_skipped(self):
        candidate = self._candidate(status="NOT_APPLICABLE")
        result = inspect_sell_order_candidates(self._preview(candidate))

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(0, result["candidate_count"])
        self.assertIn("no inspectable candidates", result["reasons"])


if __name__ == "__main__":
    unittest.main()
