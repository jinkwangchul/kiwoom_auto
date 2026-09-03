from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_unfilled_cancel_eligibility import inspect_unfilled_cancel_eligibility
from tests.test_execution_unfilled_cancel_eligibility import _order, _cancel, ACCOUNT, CODE
from tests.test_execution_unfilled_cancel_eligibility import ExistingCancelProductionPathTest


class BuyUnfilledCancelEligibilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "order_queue.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, orders):
        for order in orders:
            order.setdefault("side", "BUY")
            order.setdefault("execution_intent", {}).update(side="BUY")
        self.path.write_text(json.dumps({"version": 1, "orders": orders}), encoding="utf-8")

    def inspect(self, at="2026-09-03T10:00:20"):
        return inspect_unfilled_cancel_eligibility(selected_account_no=ACCOUNT,
            allowed_stock_codes=[CODE], now=datetime.fromisoformat(at), order_queue_path=self.path)

    def order(self, identity="BUY_1", **kwargs):
        value = _order(identity, **kwargs)
        value["side"] = "BUY"
        value["execution_intent"]["side"] = "BUY"
        value["execution_intent"]["unfilled_timeout_policy"]["enabled"] = True
        return value

    def test_before_exact_after_boundary_and_anchor(self):
        self.write([self.order()])
        self.assertFalse(self.inspect("2026-09-03T10:00:19.999")["proposals"])
        result = self.inspect()
        self.assertEqual(1, len(result["proposals"]))
        self.assertEqual("BUY", result["proposals"][0]["side"])
        self.assertEqual("BROKER_ACCEPTED_AT", result["proposals"][0]["timeout_anchor"])

    def test_filled_zero_and_rejected_are_not_candidates(self):
        self.write([self.order(status="FILLED", remaining=0), self.order("REJ", status="BROKER_REJECTED")])
        self.assertFalse(self.inspect()["proposals"])

    def test_partial_fill_uses_latest_remaining(self):
        self.write([self.order(status="PARTIALLY_FILLED", remaining=6)])
        self.assertEqual(6, self.inspect()["proposals"][0]["remaining_quantity"])

    def test_missing_broker_or_anchor_is_reconciliation(self):
        self.write([self.order("NO", broker_order_no=""), self.order("ANCHOR", accepted_at=None)])
        result = self.inspect()
        self.assertFalse(result["proposals"])
        reasons = " ".join(r for item in result["reviews"] for r in item["review_reasons"])
        self.assertIn("UNFILLED_TIMEOUT_BROKER_ORDER_NO_MISSING", reasons)
        self.assertIn("UNFILLED_TIMEOUT_BROKER_ACCEPTED_AT_MISSING", reasons)

    def test_each_scope_and_batch_scope(self):
        first = self.order("B1", scope="BATCH")
        second = self.order("B2", scope="BATCH", accepted_at="2026-09-03T10:00:15")
        self.write([first, second])
        result = self.inspect("2026-09-03T10:01:00")
        self.assertEqual({"B1", "B2"}, {p["order_id"] for p in result["proposals"]})
        each = self.order("E1", scope="EACH")
        self.write([each, self.order("E2", scope="EACH")])
        self.assertEqual(2, len(self.inspect()["proposals"]))

    def test_active_and_uncertain_cancel_are_never_duplicated(self):
        source = self.order()
        self.write([source, _cancel(source)])
        self.assertFalse(self.inspect()["proposals"])
        uncertain = _cancel(source, status="SEND_UNCERTAIN")
        self.write([source, uncertain])
        result = self.inspect()
        self.assertFalse(result["proposals"])
        self.assertTrue(result["reviews"])

    def test_restart_timeout_open_and_confirmed_cancel_are_idempotent(self):
        source = self.order()
        self.write([source])
        self.assertEqual(1, len(self.inspect()["proposals"]))
        done = self.order(status="CANCELLED", remaining=0)
        self.write([done, _cancel(done, status="CANCELLED", confirmed=True)])
        self.assertFalse(self.inspect()["proposals"])

    def test_policy_disabled_or_invalid_fails_closed(self):
        disabled = self.order()
        disabled["execution_intent"]["unfilled_timeout_policy"]["enabled"] = False
        self.write([disabled])
        self.assertFalse(self.inspect()["proposals"])
        invalid = self.order()
        invalid["execution_intent"]["unfilled_timeout_policy"]["scope"] = "OTHER"
        self.write([invalid])
        self.assertTrue(self.inspect()["reviews"])

    def test_chejan_anchor_is_first_and_send_uncertain_isolated(self):
        value = self.order(accepted_at=None)
        value["chejan_events"] = [{"event_type": "ORDER_ACCEPTED", "received_at": "2026-09-03T10:00:00"},
                                   {"event_type": "ORDER_OPEN", "received_at": "2026-09-03T10:00:10"}]
        self.write([value])
        self.assertEqual("CHEJAN_ORDER_RECEIVED_AT", self.inspect()["proposals"][0]["timeout_anchor"])
        uncertain = self.order(status="SEND_UNCERTAIN")
        self.write([uncertain])
        self.assertTrue(self.inspect()["reviews"])

    def test_identity_and_scope_mismatch_review(self):
        value = self.order()
        value["execution_intent"]["source_signal_id"] = "OTHER"
        self.write([value])
        result = self.inspect()
        self.assertFalse(result["proposals"])
        self.assertIn("UNFILLED_TIMEOUT_ORDER_IDENTITY_MISMATCH", result["reviews"][0]["review_reasons"])


class BuyTimeoutMapperAndRoundContractTest(unittest.TestCase):
    def test_buy_situation_maps_generic_policy(self):
        from routines.지표추종매매 import routine_rule_mapper as mapper
        result = mapper.build_engine_rules_preview_from_ui_state({"buy_ui": {
            "base": {"hoga_combo": "단일호가", "order_combo": "주문가"},
            "situation": {"type_combo": "미체결", "unfilled_scope_combo": "매회",
                           "unfilled_time_line": "10", "unfilled_unit_combo": "초"},
        }}, {"buy": {"execution": {"base": {}}}})
        policy = result["preview_rules"]["buy"]["execution"]["base"]["unfilled_timeout_policy"]
        self.assertEqual({"policy", "enabled", "action", "scope", "configured_value", "configured_unit", "anchor"}, set(policy))
        self.assertEqual((True, "EACH", 10.0, "SECOND"), (policy["enabled"], policy["scope"], policy["configured_value"], policy["configured_unit"]))
        self.assertNotIn("situation response mapping is postponed", result["postponed"])

    def test_buy_round_is_not_changed_by_cancel_policy(self):
        from routines.지표추종매매 import routine_buy_execution as buy
        base = {"unfilled_timeout_policy": {"policy": "CANCEL_PENDING_ORDER", "enabled": True,
            "action": "CANCEL", "scope": "EACH", "configured_value": 10, "configured_unit": "SECOND"}}
        policy, reason = buy._buy_unfilled_timeout_policy(base, {})
        self.assertEqual("", reason)
        self.assertEqual(10000, policy["timeout_ms"])


class BuyCancelProductionPathTest(ExistingCancelProductionPathTest):
    def test_buy_cancel_reuses_existing_identity_pinned_path(self):
        source = _order("BUY", status="PARTIALLY_FILLED", remaining=6)
        source["side"] = "BUY"
        source["execution_intent"]["side"] = "BUY"
        source["execution_request"]["execution_intent"]["side"] = "BUY"
        source["execution_request"]["request_preview"] = {
            "account_no": ACCOUNT, "code": CODE, "side": "BUY", "order_action": "NEW"
        }
        self._write([source])
        result = self.boundary.queue_open_order_cancel_automatically(
            source["id"], expected_account_no=ACCOUNT, expected_code=CODE,
            expected_side="BUY", expected_broker_order_no=source["broker_order_no"],
            cancel_evidence={"trigger": "UNFILLED_TIMEOUT", "scope": "EACH"},
        )
        self.assertTrue(result["ok"], result)
        saved = json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"]
        cancel = next(item for item in saved if item.get("order_action") == "CANCEL")
        self.assertEqual("BUY", cancel["side"])
        self.assertEqual(6, cancel["quantity"])
        self.assertEqual(source["execution_process_id"], cancel["execution_process_id"])
        self.assertEqual("CANCEL", cancel["child_kind"])


class BuyUnfilledCancelTimerRoutingTest(unittest.TestCase):
    def test_operation_cycle_routes_buy_cancel_through_generic_path(self):
        import gui_auto_trade_timer
        from types import SimpleNamespace

        entry = SimpleNamespace(
            stock_code=CODE,
            stock_name="테스트",
            stock_dir=Path("unused"),
            execution_ready=True,
            real_trade_enabled=True,
            signal_probe_only=False,
        )
        snapshot = SimpleNamespace(entries=(entry,))
        requester = mock.Mock(return_value={"ok": True, "cancel_requested": 1, "cancel_pending": 0})
        window = SimpleNamespace(
            current_selected_account_no=lambda: ACCOUNT,
            queue_open_order_cancel_automatically=requester,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        inspected = {
            "proposals": [{
                "order_queued_id": "BUY_ORDER_1",
                "account_no": ACCOUNT,
                "code": CODE,
                "side": "BUY",
                "broker_order_no": "BUY_BROKER_1",
                "remaining_quantity": 3,
                "scope": "EACH",
                "timeout_ms": 20_000,
                "timeout_anchor": "BROKER_ACCEPTED_AT",
            }],
            "reviews": [], "waiting": [], "errors": [],
        }
        empty = {"proposals": [], "reviews": [], "waiting": [], "errors": []}
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0,
                                  "errors": 0, "orders_created": 0, "approval_checked": 0,
                                  "approved": 0, "executable_order_ids": []}}
        with mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=inspected), \
             mock.patch.object(gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty), \
             mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty), \
             mock.patch.object(gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty), \
             mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        self.assertEqual(1, result["unfilled_cancel"]["cancel_requested"])
        requester.assert_called_once()
        self.assertEqual("BUY", requester.call_args.kwargs["expected_side"])


if __name__ == "__main__":
    unittest.main()
