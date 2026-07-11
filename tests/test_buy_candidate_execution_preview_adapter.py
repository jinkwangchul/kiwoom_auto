from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from buy_candidate_execution_preview_adapter import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_execution_preview_input_from_buy_candidate,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BuyCandidateExecutionPreviewAdapterTest(unittest.TestCase):
    def _preview(self, *, status="READY", order_type="LIMIT", hoga_mode="SINGLE"):
        draft = {
            "candidate_version": "BUY_ORDER_CANDIDATE_DRAFT_V1",
            "candidate_id": "BUY_ORDER_CANDIDATE_ABC",
            "symbol": "005930",
            "side": "BUY",
            "order_type": order_type,
            "price": 70000.0 if order_type == "LIMIT" else None,
            "budget": 100000.0,
            "quantity_policy": "BUDGET_BASED",
            "next_buy_round": 2,
            "is_last_round": False,
            "hoga_mode": hoga_mode,
            "hoga_up": 1,
            "hoga_down": 0,
            "source_signal_id": "SIG_BUY_1",
            "policy_version": "BUY_EXECUTION_POLICY_V1",
            "execution_snapshot": {"policy_hash": "policy-hash"},
        }
        return {
            "status": status,
            "order_candidate_draft": draft if status == "READY" else None,
            "execution_policy_result": {
                "status": status,
                "issues": ["POLICY_BLOCKED"] if status == "BLOCKED" else [],
                "execution_snapshot": {"policy_hash": "policy-hash"},
            },
            "execution_snapshot": {"policy_hash": "policy-hash"},
            "evidence": {"round": 2},
            "diagnostics": [{"stage": "candidate_draft", "ok": status == "READY"}],
        }

    def test_ready_creates_execution_preview_input(self):
        result = build_execution_preview_input_from_buy_candidate(self._preview())
        order_input = result["order_candidate_input"]

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("BUY_ORDER_CANDIDATE_ABC", order_input["candidate_id"])
        self.assertEqual("005930", order_input["symbol"])
        self.assertEqual("BUY", order_input["side"])
        self.assertEqual("LIMIT", order_input["order_type"])
        self.assertEqual(70000.0, order_input["price"])
        self.assertEqual(100000.0, order_input["budget"])
        self.assertEqual(2, order_input["next_buy_round"])
        self.assertFalse(order_input["is_last_round"])
        self.assertEqual("SINGLE", order_input["hoga_mode"])
        self.assertEqual(1, order_input["hoga_up"])
        self.assertEqual(0, order_input["hoga_down"])
        self.assertEqual({"policy_hash": "policy-hash"}, order_input["execution_snapshot"])
        self.assertEqual("BUY_ORDER_CANDIDATE_ABC", result["execution_preview_context"]["candidate_id"])

    def test_blocked_preview_does_not_create_input_and_preserves_context(self):
        result = build_execution_preview_input_from_buy_candidate(self._preview(status="BLOCKED"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["order_candidate_input"])
        self.assertEqual(["POLICY_BLOCKED"], result["issues"])
        self.assertEqual({"round": 2}, result["evidence"])
        self.assertEqual([{"stage": "candidate_draft", "ok": False}], result["diagnostics"])

    def test_invalid_preview_is_blocked_as_invalid(self):
        result = build_execution_preview_input_from_buy_candidate(self._preview(status="INVALID"))

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIsNone(result["order_candidate_input"])

    def test_limit_and_market_are_transferred(self):
        limit_result = build_execution_preview_input_from_buy_candidate(self._preview(order_type="LIMIT"))
        market_result = build_execution_preview_input_from_buy_candidate(self._preview(order_type="MARKET"))

        self.assertEqual("LIMIT", limit_result["order_candidate_input"]["order_type"])
        self.assertEqual(70000.0, limit_result["order_candidate_input"]["price"])
        self.assertEqual("MARKET", market_result["order_candidate_input"]["order_type"])
        self.assertIsNone(market_result["order_candidate_input"]["price"])

    def test_multi_hoga_is_transferred(self):
        result = build_execution_preview_input_from_buy_candidate(self._preview(hoga_mode="MULTI"))

        self.assertEqual("MULTI", result["order_candidate_input"]["hoga_mode"])

    def test_snapshot_candidate_id_and_evidence_are_preserved(self):
        result = build_execution_preview_input_from_buy_candidate(self._preview())

        self.assertEqual("BUY_ORDER_CANDIDATE_ABC", result["order_candidate_input"]["candidate_id"])
        self.assertEqual({"policy_hash": "policy-hash"}, result["execution_preview_context"]["execution_snapshot"])
        self.assertEqual({"round": 2}, result["execution_preview_context"]["evidence"])

    def test_input_immutability(self):
        preview = self._preview()
        context = {"caller": "test"}
        original = (deepcopy(preview), deepcopy(context))

        build_execution_preview_input_from_buy_candidate(preview, context)

        self.assertEqual((preview, context), original)

    def test_deterministic(self):
        first = build_execution_preview_input_from_buy_candidate(self._preview())
        second = build_execution_preview_input_from_buy_candidate(self._preview())

        self.assertEqual(first, second)

    def test_runtime_order_queue_is_not_changed(self):
        order_queue = ROOT / "runtime" / "order_queue.json"
        before = _sha256(order_queue)

        build_execution_preview_input_from_buy_candidate(self._preview())

        self.assertEqual(before, _sha256(order_queue))

    def test_no_send_order_gui_broker_or_file_write(self):
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_execution_preview_input_from_buy_candidate(self._preview())

        self.assertEqual(STATUS_READY, result["status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
