from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from sell_real_ready_authorization_preview import build_sell_real_ready_authorization_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellRealReadyAuthorizationPreviewTest(unittest.TestCase):
    def _normalized(self, action_source="METHOD", **overrides):
        source_previews = {"method_preview": {"status": "READY", "nested": {"value": 1}}}
        candidate = {
            "action_source": action_source,
            "symbol": "005930",
            "side": "SELL",
            "signal_id": "SIG_SELL_1",
            "method_set": "setting_a",
            "order_id": None,
            "quantity": 10,
            "price": None,
            "hoga": "MARKET",
            "order_type": "SELL",
            "source_previews": source_previews,
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "send_order": False,
            "order_request_created": False,
            "candidate_created": False,
        }
        if action_source == "COMPLETION":
            candidate["source_previews"] = {"completion": {"status": "READY"}}
        if action_source == "PENDING":
            candidate.update(
                {
                    "order_id": "ORD-1",
                    "price": None,
                    "hoga": None,
                    "order_type": None,
                    "source_previews": {"pending": {"status": "READY"}},
                }
            )
        candidate.update(overrides)
        return candidate

    def _inspected(self, action_source="METHOD", **overrides):
        inspected = {
            "action_source": action_source,
            "status": "READY",
            "real_ready_eligible": True,
            "normalized_candidate": self._normalized(action_source),
            "reasons": [],
            "warnings": [],
        }
        inspected.update(overrides)
        return inspected

    def _inspection(self, *candidates, **overrides):
        inspection = {
            "inspection_type": "SELL_ORDER_CANDIDATE_INSPECTION",
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "send_order": False,
            "status": "READY",
            "real_ready_eligible": any(
                isinstance(candidate, dict) and candidate.get("real_ready_eligible") for candidate in candidates
            ),
            "candidate_count": len(candidates),
            "inspected_candidates": list(candidates),
            "reasons": [],
            "warnings": [],
        }
        inspection.update(overrides)
        return inspection

    def _single_authorization(self, result):
        self.assertEqual(1, len(result["authorized_candidates"]))
        return result["authorized_candidates"][0]

    def test_eligible_method_candidate_is_authorized(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected("METHOD")))

        candidate = self._single_authorization(result)
        self.assertEqual("READY", result["status"])
        self.assertTrue(candidate["authorized"])
        self.assertEqual("METHOD", candidate["action_source"])

    def test_eligible_completion_candidate_is_authorized(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected("COMPLETION")))

        candidate = self._single_authorization(result)
        self.assertEqual("READY", candidate["status"])
        self.assertTrue(candidate["authorized"])
        self.assertEqual("COMPLETION", candidate["action_source"])

    def test_eligible_pending_candidate_is_authorized(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected("PENDING")))

        candidate = self._single_authorization(result)
        self.assertEqual("READY", candidate["status"])
        self.assertTrue(candidate["authorized"])
        self.assertEqual("PENDING", candidate["action_source"])

    def test_not_real_ready_eligible_candidate_is_not_authorized(self):
        inspected = self._inspected(real_ready_eligible=False)
        result = build_sell_real_ready_authorization_preview(self._inspection(inspected))

        candidate = self._single_authorization(result)
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(candidate["authorized"])
        self.assertIn("real_ready_eligible is False", candidate["reasons"])

    def test_blocked_candidate_is_preserved_as_blocked(self):
        inspected = self._inspected(status="BLOCKED", real_ready_eligible=False, reasons=["blocked upstream"])
        result = build_sell_real_ready_authorization_preview(self._inspection(inspected))

        candidate = self._single_authorization(result)
        self.assertEqual("BLOCKED", candidate["status"])
        self.assertFalse(candidate["authorized"])
        self.assertIn("blocked upstream", candidate["reasons"])

    def test_invalid_candidate_is_preserved_as_invalid(self):
        inspected = self._inspected(status="INVALID", real_ready_eligible=False, reasons=["invalid upstream"])
        result = build_sell_real_ready_authorization_preview(self._inspection(inspected))

        candidate = self._single_authorization(result)
        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INVALID", candidate["status"])
        self.assertFalse(candidate["authorized"])
        self.assertIn("invalid upstream", candidate["reasons"])

    def test_malformed_input_is_invalid(self):
        result = build_sell_real_ready_authorization_preview([])

        self.assertEqual("INVALID", result["status"])
        self.assertIn("inspection_result must be a dict", result["reasons"])

    def test_empty_candidates_are_blocked(self):
        result = build_sell_real_ready_authorization_preview(self._inspection())

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual([], result["authorized_candidates"])
        self.assertIn("no inspected candidates", result["reasons"])

    def test_multiple_candidate_order_is_preserved(self):
        result = build_sell_real_ready_authorization_preview(
            self._inspection(
                self._inspected("METHOD"),
                self._inspected("COMPLETION"),
                self._inspected("PENDING"),
            )
        )

        self.assertEqual(
            ["METHOD", "COMPLETION", "PENDING"],
            [candidate["action_source"] for candidate in result["authorized_candidates"]],
        )

    def test_multiple_candidates_do_not_select_priority(self):
        result = build_sell_real_ready_authorization_preview(
            self._inspection(self._inspected("METHOD"), self._inspected("COMPLETION"))
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(2, result["authorization_summary"]["authorized_count"])
        self.assertFalse(result["authorization_summary"]["priority_selected"])
        self.assertIn("multiple_authorized_candidates_priority_not_selected", result["warnings"])

    def test_input_object_is_not_mutated(self):
        inspection = self._inspection(self._inspected("METHOD"))
        original = deepcopy(inspection)

        build_sell_real_ready_authorization_preview(inspection)

        self.assertEqual(original, inspection)

    def test_deepcopy_snapshots_are_used(self):
        inspection = self._inspection(self._inspected("METHOD"))
        market_context = {"symbol": "005930", "nested": {"value": 1}}
        result = build_sell_real_ready_authorization_preview(inspection, market_context=market_context)

        result["inspection_snapshot"]["inspected_candidates"][0]["normalized_candidate"]["source_previews"][
            "method_preview"
        ]["nested"]["value"] = 99
        result["market_context_snapshot"]["nested"]["value"] = 99

        self.assertEqual(1, inspection["inspected_candidates"][0]["normalized_candidate"]["source_previews"]["method_preview"]["nested"]["value"])
        self.assertEqual(1, market_context["nested"]["value"])

    def test_output_preview_only_true(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertTrue(result["preview_only"])

    def test_output_execution_connected_false(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertFalse(result["execution_connected"])

    def test_output_runtime_write_false(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertFalse(result["runtime_write"])

    def test_output_queue_write_false(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertFalse(result["queue_write"])

    def test_output_send_order_false(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertFalse(result["send_order"])

    def test_runtime_files_are_not_changed(self):
        runtime_files = [ROOT / "runtime" / "order_executions.json", ROOT / "runtime" / "order_queue.json"]
        before = {path: _sha256(path) for path in runtime_files}

        build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertEqual(before, {path: _sha256(path) for path in runtime_files})

    def test_queue_file_is_not_changed(self):
        queue_file = ROOT / "runtime" / "order_queue.json"
        before = _sha256(queue_file)

        build_sell_real_ready_authorization_preview(self._inspection(self._inspected("PENDING")))

        self.assertEqual(before, _sha256(queue_file))

    def test_sendorder_is_not_called(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertFalse(result["send_order"])
        self.assertNotIn("SendOrder", json.dumps(result, ensure_ascii=False))

    def test_order_request_is_not_created(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertFalse(result["authorization_summary"]["order_request_created"])
        self.assertNotIn("ORDER_REQUEST", json.dumps(result, ensure_ascii=False))

    def test_unsupported_action_source_is_invalid(self):
        inspected = self._inspected(action_source="OTHER", normalized_candidate=self._normalized(action_source="OTHER"))
        result = build_sell_real_ready_authorization_preview(self._inspection(inspected))

        candidate = self._single_authorization(result)
        self.assertEqual("INVALID", result["status"])
        self.assertIn("unsupported action_source: OTHER", candidate["reasons"])

    def test_warnings_and_reasons_are_deterministic(self):
        inspected = self._inspected(
            status="BLOCKED",
            real_ready_eligible=False,
            reasons=["upstream reason"],
            warnings=["upstream warning"],
        )
        result = build_sell_real_ready_authorization_preview(
            self._inspection(inspected, warnings=["inspection warning"])
        )

        candidate = self._single_authorization(result)
        self.assertIn("inspection warning", result["warnings"])
        self.assertIn("upstream warning", result["warnings"])
        self.assertIn("upstream warning", candidate["warnings"])
        self.assertIn("upstream reason", candidate["reasons"])

    def test_same_input_is_deterministic(self):
        inspection = self._inspection(self._inspected("METHOD"), self._inspected("PENDING"))

        first = build_sell_real_ready_authorization_preview(inspection)
        second = build_sell_real_ready_authorization_preview(inspection)

        self.assertEqual(first, second)

    def test_real_ready_status_is_never_created(self):
        result = build_sell_real_ready_authorization_preview(self._inspection(self._inspected()))

        self.assertNotIn("REAL_READY", json.dumps(result["status"], ensure_ascii=False))
        self.assertFalse(result["authorization_summary"]["real_ready_status_created"])

    def test_safety_flag_violation_blocks_authorization_as_invalid(self):
        normalized = self._normalized(runtime_write=True)
        inspected = self._inspected(normalized_candidate=normalized)
        result = build_sell_real_ready_authorization_preview(self._inspection(inspected))

        candidate = self._single_authorization(result)
        self.assertEqual("INVALID", result["status"])
        self.assertFalse(candidate["authorized"])
        self.assertIn("runtime_write must be False", candidate["reasons"])


if __name__ == "__main__":
    unittest.main()
