from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from buy_order_candidate_preview_service import (
    CANDIDATE_VERSION,
    POLICY_VERSION,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_order_candidate_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BuyOrderCandidatePreviewServiceTest(unittest.TestCase):
    def _signal(self, **overrides):
        signal = {
            "signal_type": "BUY",
            "signal_id": "SIG_BUY_1",
            "symbol": "005930",
            "order_price": 70000,
            "current_price": 70100,
            "market_price": 70200,
        }
        signal.update(overrides)
        return signal

    def _rules(self, *, order_price_basis="ORDER_PRICE", hoga_mode="SINGLE", pending=None):
        rules = {
            "buy": {
                "execution": {
                    "base": {
                        "hoga_mode": hoga_mode,
                        "order_price_basis": order_price_basis,
                        "hoga_up": 1,
                        "hoga_down": 0,
                        "point_count": 3,
                        "ratio_count": 3,
                    },
                    "repeat": {
                        "apply_all": True,
                        "detail_mode": "ROUND",
                        "round_operator": "ADD",
                        "round_budget_value": 100000,
                        "budget_ratio": 0,
                    },
                }
            }
        }
        if pending is not None:
            rules["indicator_follow_rule_pending"] = pending
        return rules

    def _runtime(self, **overrides):
        runtime = {"current_buy_round": 0, "used_budget": 0}
        runtime.update(overrides)
        return runtime

    def _budget(self, **overrides):
        budget = {"total_budget": 500000, "remaining_budget": 500000, "max_buy_rounds": 3}
        budget.update(overrides)
        return budget

    def _build(self, **overrides):
        kwargs = {
            "buy_signal_result": self._signal(),
            "approved_rules": self._rules(),
            "runtime_state_snapshot": self._runtime(),
            "budget_context": self._budget(),
        }
        kwargs.update(overrides)
        return build_buy_order_candidate_preview(**kwargs)

    def test_buy_ready_creates_candidate(self):
        result = self._build()
        draft = result["order_candidate_draft"]

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(CANDIDATE_VERSION, draft["candidate_version"])
        self.assertTrue(draft["candidate_id"].startswith("BUY_ORDER_CANDIDATE_"))
        self.assertEqual("005930", draft["symbol"])
        self.assertEqual("BUY", draft["side"])
        self.assertEqual("LIMIT", draft["order_type"])
        self.assertEqual(70000.0, draft["price"])
        self.assertEqual(100000.0, draft["budget"])
        self.assertEqual("BUDGET_BASED", draft["quantity_policy"])
        self.assertEqual(1, draft["next_buy_round"])
        self.assertFalse(draft["is_last_round"])
        self.assertEqual("SINGLE", draft["hoga_mode"])
        self.assertEqual(1, draft["hoga_up"])
        self.assertEqual(0, draft["hoga_down"])
        self.assertEqual("SIG_BUY_1", draft["source_signal_id"])
        self.assertEqual(POLICY_VERSION, draft["policy_version"])
        self.assertEqual(draft["execution_snapshot"], result["execution_snapshot"])

    def test_evaluator_blocked_preserves_evidence_without_candidate(self):
        def blocked_evaluator(**_kwargs):
            return {
                "status": "BLOCKED",
                "issues": ["ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET"],
                "evidence": {"why": "budget"},
                "execution_snapshot": {"policy_hash": "blocked"},
            }

        result = self._build(evaluator=blocked_evaluator)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["order_candidate_draft"])
        self.assertEqual(["ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET"], result["execution_policy_result"]["issues"])
        self.assertEqual({"why": "budget"}, result["evidence"])
        self.assertEqual({"policy_hash": "blocked"}, result["execution_snapshot"])

    def test_non_buy_signal_blocked_before_candidate(self):
        result = self._build(buy_signal_result=self._signal(signal_type="SELL"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["order_candidate_draft"])
        self.assertEqual("SELL", result["evidence"]["signal_side"])

    def test_limit_and_market_conversion(self):
        limit_result = self._build()
        market_result = self._build(approved_rules=self._rules(order_price_basis="MARKET"))

        self.assertEqual("LIMIT", limit_result["order_candidate_draft"]["order_type"])
        self.assertEqual(70000.0, limit_result["order_candidate_draft"]["price"])
        self.assertEqual("MARKET", market_result["order_candidate_draft"]["order_type"])
        self.assertIsNone(market_result["order_candidate_draft"]["price"])

    def test_single_and_multi_are_preserved(self):
        single = self._build()
        multi = self._build(approved_rules=self._rules(hoga_mode="MULTI"))

        self.assertEqual("SINGLE", single["order_candidate_draft"]["hoga_mode"])
        self.assertEqual("MULTI", multi["order_candidate_draft"]["hoga_mode"])

    def test_budget_price_round_are_preserved(self):
        result = self._build(runtime_state_snapshot=self._runtime(current_buy_round=1))
        draft = result["order_candidate_draft"]

        self.assertEqual(2, draft["next_buy_round"])
        self.assertEqual(100000.0, draft["budget"])
        self.assertEqual(70000.0, draft["price"])

    def test_candidate_id_is_deterministic(self):
        first = self._build()["order_candidate_draft"]["candidate_id"]
        second = self._build()["order_candidate_draft"]["candidate_id"]

        self.assertEqual(first, second)

    def test_execution_snapshot_is_preserved(self):
        result = self._build()

        self.assertEqual(
            result["execution_policy_result"]["execution_snapshot"],
            result["order_candidate_draft"]["execution_snapshot"],
        )

    def test_evidence_is_preserved(self):
        result = self._build()

        self.assertEqual(
            result["execution_policy_result"]["evidence"],
            result["evidence"],
        )

    def test_input_immutability(self):
        signal = self._signal()
        rules = self._rules()
        runtime = self._runtime()
        budget = self._budget()
        original = (deepcopy(signal), deepcopy(rules), deepcopy(runtime), deepcopy(budget))

        self._build(
            buy_signal_result=signal,
            approved_rules=rules,
            runtime_state_snapshot=runtime,
            budget_context=budget,
        )

        self.assertEqual((signal, rules, runtime, budget), original)

    def test_pending_namespace_is_not_passed_to_evaluator(self):
        pending = {"candidates": {"execution": {"base": {"value": {"hoga_mode": "BROKEN"}}}}}
        captured = {}

        def evaluator(**kwargs):
            captured.update(kwargs)
            return {
                "status": "READY",
                "next_buy_round": 1,
                "order_price_basis": "ORDER_PRICE",
                "order_price": 70000.0,
                "hoga_mode": "SINGLE",
                "hoga_up": 1,
                "hoga_down": 0,
                "round_budget": 100000.0,
                "is_last_round": False,
                "evidence": {"ok": True},
                "execution_snapshot": {"policy_hash": "ready"},
            }

        result = self._build(approved_rules=self._rules(pending=pending), evaluator=evaluator)

        self.assertEqual(STATUS_READY, result["status"])
        self.assertNotIn("indicator_follow_rule_pending", captured["approved_rules"])
        self.assertEqual("SINGLE", captured["approved_rules"]["buy"]["execution"]["base"]["hoga_mode"])

    def test_invalid_evaluator_result_blocks(self):
        result = self._build(evaluator=lambda **_kwargs: {"status": "WEIRD", "evidence": {"raw": True}})

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIsNone(result["order_candidate_draft"])
        self.assertEqual({"raw": True}, result["evidence"])

    def test_non_dict_evaluator_result_invalid(self):
        result = self._build(evaluator=lambda **_kwargs: None)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIsNone(result["order_candidate_draft"])

    def test_runtime_order_queue_files_are_not_changed(self):
        order_queue = ROOT / "runtime" / "order_queue.json"
        before = _sha256(order_queue)

        self._build()

        self.assertEqual(before, _sha256(order_queue))

    def test_no_broker_sendorder_gui_or_file_write(self):
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._build()

        self.assertEqual(STATUS_READY, result["status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["gui_updated"])

    def test_diagnostics_are_compatible_with_followup_preview_chain(self):
        ready = self._build()
        blocked = self._build(buy_signal_result=self._signal(signal_type="SELL"))

        self.assertEqual("candidate_draft", ready["diagnostics"][0]["stage"])
        self.assertTrue(ready["diagnostics"][0]["ok"])
        self.assertEqual("signal", blocked["diagnostics"][0]["stage"])
        self.assertFalse(blocked["diagnostics"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
