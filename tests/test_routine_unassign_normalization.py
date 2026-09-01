# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_routine_policy as policy
import stock_repository
from state_policy import canonical_auto_trade_status


class RoutineUnassignNormalizationTests(unittest.TestCase):
    INSTANCE_ID = "11111111-1111-4111-8111-111111111111"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stock_dir = self.root / "stocks" / "005930_삼성전자"
        self.stock_dir.mkdir(parents=True)
        self.config_path = self.stock_dir / "config.json"
        self.state_path = self.stock_dir / "state.json"
        self.orders_path = self.stock_dir / "orders.json"
        self.write_routine_instance_fixture()
        self.write_config()
        self.write_state("STOPPED")
        self.orders_path.write_text('{"orders": []}', encoding="utf-8")

    def write_routine_instance_fixture(self) -> None:
        routine_dir = self.root / "routines" / "지표추종매매"
        routine_dir.mkdir(parents=True)
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "name": "지표추종매매",
                    "enabled": True,
                    "version": "1.0",
                    "routine_type": "auto_trade",
                    "entry_file": "routine.py",
                    "module_name": "indicator_follow_routine",
                    "settings_ui": "indicator_follow",
                    "rules_file": "rules.json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (routine_dir / "routine.py").write_text("", encoding="utf-8")
        (routine_dir / "rules.json").write_text("{}", encoding="utf-8")

        instance_dir = self.root / "routine_instances" / self.INSTANCE_ID
        instance_dir.mkdir(parents=True)
        (instance_dir / "instance.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "instance_id": self.INSTANCE_ID,
                    "definition_id": "indicator_follow",
                    "display_name": "동전주",
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (instance_dir / "rules.json").write_text("{}", encoding="utf-8")

    def write_config(self, **changes) -> None:
        config = {
            "routine": "지표추종매매",
            "routine_name": "지표추종매매",
            "assigned_routine": "지표추종매매",
            "active_routine": "지표추종매매",
            "routine_type": "지표추종매매",
            "routines": ["지표추종매매"],
            "assigned_routine_instance_id": self.INSTANCE_ID,
            "routine_instance_name": "동전주",
            "routine_definition_id": "indicator_follow",
        }
        config.update(changes)
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_state(self, status: str, **changes) -> None:
        state = {"status": status, "holding_qty": 0}
        state.update(changes)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )

    def decision(self, **kwargs):
        return policy.routine_unassign_decision(
            "005930",
            "삼성전자",
            row_instance_id=self.INSTANCE_ID,
            project_root=self.root,
            **kwargs,
        )

    def test_canonical_classifier_recognizes_close_and_unknown_statuses(self) -> None:
        early = canonical_auto_trade_status("EARLY_CLOSE")
        auto = canonical_auto_trade_status("AUTO_CLOSE")
        unknown = canonical_auto_trade_status("UNKNOWN_X")
        self.assertTrue(early.known)
        self.assertEqual("CLOSE_OPERATION", early.status_class)
        self.assertTrue(auto.known)
        self.assertEqual("자동마감", auto.display_status)
        self.assertFalse(unknown.known)
        self.assertEqual("UNKNOWN", unknown.status_class)

    def test_early_close_without_safety_evidence_is_allowed_and_read_only(self) -> None:
        self.write_state("EARLY_CLOSE", holding_qty=0)
        before = self.state_path.read_bytes()
        decision = self.decision()
        self.assertTrue(decision.applicable)
        self.assertTrue(decision.allowed)
        self.assertEqual((), decision.reason_codes)
        self.assertEqual("CLOSE_OPERATION", decision.status_info.status_class)
        self.assertEqual(before, self.state_path.read_bytes())

    def test_auto_close_pending_and_early_close_holding_remain_blocked(self) -> None:
        self.write_state("EARLY_CLOSE", holding_qty=1)
        holding = self.decision()
        self.assertFalse(holding.allowed)
        self.assertIn("HOLDING_QTY", holding.reason_codes)
        self.assertEqual("SAFETY_BLOCK", holding.diagnostic_class)
        self.assertFalse(holding.review_required)

        self.write_state("AUTO_CLOSE", holding_qty=0)
        self.orders_path.write_text(
            json.dumps({"orders": [{"status": "OPEN", "side": "SELL", "pending_qty": 2}]}),
            encoding="utf-8",
        )
        pending = self.decision()
        self.assertFalse(pending.allowed)
        self.assertIn("SELL_PENDING", pending.reason_codes)
        self.assertFalse(pending.review_required)

    def test_buy_pending_emergency_review_and_pending_integrity_stay_blocked(self) -> None:
        self.orders_path.write_text(
            json.dumps({"orders": [{"status": "OPEN", "side": "BUY", "pending_qty": 2}]}),
            encoding="utf-8",
        )
        buy_pending = self.decision()
        self.assertIn("BUY_PENDING", buy_pending.reason_codes)
        self.assertEqual("SAFETY_BLOCK", buy_pending.diagnostic_class)
        self.assertFalse(buy_pending.review_required)

        self.orders_path.write_text('{"orders": []}', encoding="utf-8")
        self.write_state("EMERGENCY_STOP")
        emergency = self.decision()
        self.assertIn("EMERGENCY_STOP", emergency.reason_codes)
        self.assertFalse(emergency.review_required)

        self.write_state("REVIEW_REQUIRED", review_required=True)
        review = self.decision()
        self.assertIn("REVIEW_REQUIRED", review.reason_codes)
        self.assertTrue(review.evidence["review_required"])
        self.assertFalse(review.review_required)

        self.write_state("STOPPED", pending_order=True, pending_qty=1)
        integrity = self.decision()
        self.assertIn("PENDING_INTEGRITY_UNKNOWN", integrity.reason_codes)
        self.assertEqual("INTEGRITY_BLOCK", integrity.diagnostic_class)
        self.assertTrue(integrity.review_required)

    def test_unknown_status_is_integrity_block_without_evaluation_write(self) -> None:
        self.write_state("UNKNOWN_X")
        before = self.state_path.read_bytes()
        decision = self.decision()
        self.assertFalse(decision.allowed)
        self.assertIn("UNKNOWN_STATUS", decision.reason_codes)
        self.assertEqual("INTEGRITY_BLOCK", decision.diagnostic_class)
        self.assertTrue(decision.review_required)
        self.assertEqual(before, self.state_path.read_bytes())

    def test_current_instance_mismatch_blocks_but_display_alias_drift_does_not(self) -> None:
        mismatch = policy.routine_unassign_decision(
            "005930",
            "삼성전자",
            row_instance_id="22222222-2222-4222-8222-222222222222",
            project_root=self.root,
        )
        self.assertIn("CURRENT_INSTANCE_MISMATCH", mismatch.reason_codes)
        self.assertTrue(mismatch.review_required)

        self.write_config(active_routine="다른루틴")
        legacy = self.decision()
        self.assertTrue(legacy.allowed)
        self.assertNotIn("ROUTINE_RELATION_MISMATCH", legacy.reason_codes)
        self.assertIn("legacy routine fields disagree", legacy.evidence["relation_issues"])

        self.write_config(
            routine="다른루틴",
            routine_name="다른루틴",
            assigned_routine="다른루틴",
            active_routine="다른루틴",
            routines=["다른루틴"],
        )
        consistent_but_wrong = self.decision()
        self.assertTrue(consistent_but_wrong.allowed)
        self.assertNotIn("ROUTINE_RELATION_MISMATCH", consistent_but_wrong.reason_codes)
        self.assertIn(
            "legacy routine value does not match assigned instance definition",
            consistent_but_wrong.evidence["relation_issues"],
        )

    def test_missing_assigned_instance_is_runtime_relation_integrity_block(self) -> None:
        missing_id = "33333333-3333-4333-8333-333333333333"
        self.write_config(assigned_routine_instance_id=missing_id)
        decision = policy.routine_unassign_decision(
            "005930",
            "삼성전자",
            row_instance_id=missing_id,
            project_root=self.root,
        )
        self.assertIn("STOCK_RUNTIME_RELATION_BROKEN", decision.reason_codes)
        self.assertEqual("INTEGRITY_BLOCK", decision.diagnostic_class)
        self.assertTrue(decision.review_required)

    def test_runtime_missing_is_fail_closed_and_historical_is_not_applicable(self) -> None:
        self.state_path.unlink()
        missing = self.decision()
        self.assertFalse(missing.allowed)
        self.assertIn("STOCK_RUNTIME_MISSING", missing.reason_codes)
        self.assertTrue(missing.review_required)

        self.write_state("STOPPED")
        historical = self.decision(row_relation_kind=policy.HISTORICAL_STOCK_RELATION)
        self.assertFalse(historical.applicable)
        self.assertFalse(historical.allowed)
        self.assertEqual(("NOT_CURRENT_ROW",), historical.reason_codes)

    def test_compatibility_wrapper_keeps_tuple_contract(self) -> None:
        self.write_state("EARLY_CLOSE")
        with patch.object(policy, "PROJECT_ROOT", self.root):
            allowed, routine_name, reasons = policy.can_unassign_active_routine_from_stock(
                "005930",
                "삼성전자",
            )
        self.assertTrue(allowed)
        self.assertEqual("지표추종매매", routine_name)
        self.assertEqual([], reasons)

    def test_success_event_enriches_existing_routine_changed_contract(self) -> None:
        with patch.object(stock_repository, "append_production_event") as event:
            stock_repository._append_routine_changed(
                code="005930",
                name="삼성전자",
                before={"routine": "동전주", "routine_instance_id": "instance-a"},
                after={"routine": "", "routine_instance_id": ""},
            )
        event.assert_called_once()
        kwargs = event.call_args.kwargs
        self.assertEqual("ROUTINE_CHANGED", event.call_args.args[0])
        self.assertEqual("UNASSIGN", kwargs["operation"])
        self.assertEqual("OPERATOR_REQUEST", kwargs["details"]["reason"])
        self.assertEqual("instance-a", kwargs["details"]["before_instance_id"])
        self.assertIsNone(kwargs["details"]["after_instance_id"])
        self.assertEqual("동전주", kwargs["details"]["before_routine"])
        self.assertIsNone(kwargs["details"]["after_routine"])


if __name__ == "__main__":
    unittest.main()
