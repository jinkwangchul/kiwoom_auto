# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path

import signal_decision_policy_service
from gui_config_utils import default_config, default_state
from gui_review_utils import build_review_required_item, review_reason_summary
from state_policy import auto_trade_status_display


class PausedLegacyResidueTests(unittest.TestCase):
    def _review_item(self, state: dict[str, object]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir)
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "orders.json").write_text(
                json.dumps({"orders": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            return build_review_required_item("루틴", stock_dir, "005930", "삼성전자")

    def test_current_defaults_do_not_create_pause_contract_fields(self) -> None:
        config = default_config()
        state = default_state()

        self.assertNotIn("pause_resume_policy", config)
        for key in (
            "pause_signal_check_status",
            "paused_at",
            "missed_buy_signal_count",
            "missed_sell_signal_count",
        ):
            self.assertNotIn(key, state)

    def test_legacy_pause_metadata_does_not_create_review_reason(self) -> None:
        item = self._review_item(
            {
                "status": "STOPPED",
                "holding_qty": 0,
                "pause_signal_check_status": "FAILED",
                "paused_at": "2026-08-16 09:00:00",
                "missed_buy_signal_count": 3,
                "missed_sell_signal_count": 2,
            }
        )

        self.assertEqual([], item["review_reasons"])
        self.assertEqual("-", item["review_reason_text"])
        self.assertNotIn("pause_signal_check_status", item)
        self.assertNotIn("paused_at", item)
        self.assertNotIn("missed_buy_signal_count", item)
        self.assertNotIn("missed_sell_signal_count", item)
        self.assertEqual("미체결X / 현재가X", review_reason_summary(item))

    def test_pending_order_review_is_unchanged(self) -> None:
        item = self._review_item(
            {
                "status": "STOPPED",
                "holding_qty": 0,
                "pending_order": True,
                "pending_qty": 1,
                "missed_buy_signal_count": 5,
            }
        )

        self.assertEqual(["미체결 주문 있음"], item["review_reasons"])

    def test_legacy_paused_status_is_not_a_current_display_status(self) -> None:
        self.assertEqual("검토종목", auto_trade_status_display("PAUSED"))

    def test_legacy_paused_policy_input_is_fail_closed_as_invalid(self) -> None:
        preview = {
            "ok": True,
            "stage": "SIGNAL_POLICY_PREVIEW",
            "decision": "ACCEPT",
            "signal": "BUY",
            "reason": "test",
            "decision_reason": "test",
            "rule_source": "test",
            "matched_rule_paths": [],
            "condition_summary": [],
            "policy_result": "PASS",
        }
        result = signal_decision_policy_service.apply_operation_state_policy(
            preview,
            {
                "enabled": True,
                "emergency_stop": False,
                "operation_status": "PAUSED",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            "operation_state.operation_status is invalid",
            result["operation_policy_reason"],
        )


if __name__ == "__main__":
    unittest.main()
