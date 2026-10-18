from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication, QWidget

import gui_ats_utils as ats
import gui_auto_trade_policy as display
import gui_auto_trade_table_loader as setting_table
import gui_main_table_loader as main_table
import gui_operation_environment as environment
import state_policy
from manual_ats_runtime import manual_ats_runtime_selected_keys
from routine_order_permission import canonical_stock_trading_time_status


class GlobalAtsDisableContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy_path = self.root / "operation_policy.json"
        self.config = {"operation_mode": "CONTINUOUS"}
        self.policy = environment.default_operation_policy()
        self.policy["extra_sessions"] = [
            {"enabled": True, "name": "장전프리", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": True, "name": "장후거래", "start_time": "15:40:00", "end_time": "19:50:00"},
            {"enabled": False, "name": "추가3", "start_time": "", "end_time": ""},
        ]

    def stock(self, code: str, keys: list[str]) -> Path:
        stock_dir = self.root / "stocks" / f"{code}_test"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        (stock_dir / "state.json").write_text(json.dumps({
            "status": "RUNNING", "trade_enabled": True, "unrelated": {"keep": 1},
            "manual_ats_selection": {"selected_sessions": keys, "source": "ATS_SETTINGS"},
        }), encoding="utf-8")
        return stock_dir

    @staticmethod
    def state(stock_dir: Path) -> dict[str, object]:
        return json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

    def test_disabled_stale_selection_cannot_block_regular_or_appear_as_ats(self) -> None:
        self.policy["extra_sessions"][0]["enabled"] = False
        self.policy["extra_sessions"][1]["enabled"] = False
        state = {"manual_ats_selection": {"selected_sessions": ["extra2"]}}
        with (
            patch.object(ats, "read_operation_policy", return_value=self.policy),
            patch.object(state_policy, "read_operation_policy", return_value=self.policy),
        ):
            phase = ats.auto_trade_operation_session_phase(
                self.config, state, now_dt=datetime(2026, 10, 16, 10, 0)
            )
            regular = canonical_stock_trading_time_status(
                config=self.config, state=state, now_dt=datetime(2026, 10, 16, 10, 0)
            )
            after = canonical_stock_trading_time_status(
                config=self.config, state=state, now_dt=datetime(2026, 10, 16, 16, 30)
            )
            self.assertEqual([], ats.manual_ats_enabled_labels(self.config, state))
            self.assertEqual("수동", display.auto_trade_operation_display(self.config, state)[0])
            self.assertEqual("수동", main_table.auto_trade_operation_display(self.config, state)[0])
            self.assertEqual([], setting_table.manual_ats_enabled_labels(self.config, state))
            self.assertFalse(ats.manual_ats_active_now(self.config, state, datetime(2026, 10, 16, 16, 30)))
        self.assertEqual("ACTIVE_SESSION", phase["phase"])
        self.assertEqual((), phase["invalid_sessions"])
        self.assertEqual("ACTIVE_REGULAR", regular["reason"])
        self.assertTrue(regular["active"])
        self.assertFalse(after["active"])

    def test_partial_off_prunes_only_disabled_key_and_keeps_unrelated_state(self) -> None:
        environment.write_operation_policy(self.policy, path=self.policy_path)
        stock_dir = self.stock("111111", ["extra1", "extra2"])
        self.policy["extra_sessions"][0]["enabled"] = False
        environment.write_operation_policy(self.policy, path=self.policy_path)
        state = self.state(stock_dir)
        self.assertEqual(("extra2",), manual_ats_runtime_selected_keys(state))
        self.assertEqual("RUNNING", state["status"])
        self.assertEqual({"keep": 1}, state["unrelated"])
        self.assertEqual(self.config, json.loads((stock_dir / "config.json").read_text(encoding="utf-8")))

    def test_all_off_clears_every_stock_and_reenable_does_not_restore(self) -> None:
        environment.write_operation_policy(self.policy, path=self.policy_path)
        stocks = [self.stock("111111", ["extra1"]), self.stock("222222", ["extra2"])]
        for session in self.policy["extra_sessions"]:
            session["enabled"] = False
        environment.write_operation_policy(self.policy, path=self.policy_path)
        for stock_dir in stocks:
            state = self.state(stock_dir)
            self.assertNotIn("manual_ats_selection", state)
            self.assertEqual("RUNNING", state["status"])
            self.assertEqual({"keep": 1}, state["unrelated"])
        for session in self.policy["extra_sessions"][:2]:
            session["enabled"] = True
        environment.write_operation_policy(self.policy, path=self.policy_path)
        for stock_dir in stocks:
            self.assertNotIn("manual_ats_selection", self.state(stock_dir))

    def test_cleanup_failure_never_reports_success(self) -> None:
        environment.write_operation_policy(self.policy, path=self.policy_path)
        first = self.stock("111111", ["extra1"])
        second = self.stock("222222", ["extra1"])
        self.policy["extra_sessions"][0]["enabled"] = False
        from manual_ats_runtime import clear_manual_ats_runtime_selection

        def fail_second(stock_dir):
            return False if Path(stock_dir) == second else clear_manual_ats_runtime_selection(stock_dir)

        with patch.object(environment, "clear_manual_ats_runtime_selection", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "222222"):
                environment.write_operation_policy(self.policy, path=self.policy_path)
        self.assertNotIn("manual_ats_selection", self.state(first))
        self.assertEqual(("extra1",), manual_ats_runtime_selected_keys(self.state(second)))
        self.assertFalse(json.loads(self.policy_path.read_text(encoding="utf-8"))["extra_sessions"][0]["enabled"])

    def test_invalid_enabled_ats_still_fails_closed_and_regular_checkbox_is_separate(self) -> None:
        state = {"manual_ats_selection": {"selected_sessions": ["extra1"]}}
        self.policy["extra_sessions"][0]["start_time"] = "bad"
        self.policy["extra_sessions"][0]["end_time"] = "bad"
        with patch.object(ats, "read_operation_policy", return_value=self.policy):
            phase = ats.auto_trade_operation_session_phase(
                self.config, state, now_dt=datetime(2026, 10, 16, 10, 0)
            )
        self.assertEqual("SESSION_EVIDENCE_INVALID", phase["phase"])
        self.assertEqual(("extra1",), phase["invalid_sessions"])
        self.policy["extra_sessions"][0]["enabled"] = False
        self.policy["manual_operation"]["use_regular_market"] = False
        with patch.object(ats, "read_operation_policy", return_value=self.policy):
            phase = ats.auto_trade_operation_session_phase(
                self.config, state, now_dt=datetime(2026, 10, 16, 10, 0)
            )
        self.assertFalse(phase["active"])

    def test_unrelated_policy_write_with_existing_off_does_not_touch_stock(self) -> None:
        stock_dir = self.stock("111111", ["extra1"])
        self.policy["extra_sessions"][0]["enabled"] = False
        environment.write_operation_policy(self.policy, path=self.policy_path)
        before = (stock_dir / "state.json").read_bytes()
        saved = environment.read_operation_policy(path=self.policy_path)
        saved["system_budget"]["total_budget"] = 3000000
        environment.write_operation_policy(saved, path=self.policy_path)
        self.assertEqual(before, (stock_dir / "state.json").read_bytes())

    def test_dialog_save_clears_existing_stale_off_selection_and_refreshes_owner(self) -> None:
        stock_dir = self.stock("111111", ["extra1"])
        self.policy["extra_sessions"][0]["enabled"] = False
        environment.write_operation_policy(self.policy, path=self.policy_path)
        owner = QWidget()
        calls = []
        owner._handle_operation_environment_settings_saved = lambda dialog: calls.append(dialog)
        with patch.object(environment, "OPERATION_POLICY_PATH", self.policy_path):
            dialog = environment.OperationEnvironmentSettingsDialog(owner)
            try:
                with patch.object(environment, "append_changelog"), patch.object(environment, "show_toast"):
                    dialog.save_extra_sessions_only()
                self.assertNotIn("manual_ats_selection", self.state(stock_dir))
                self.assertEqual([dialog], calls)
            finally:
                dialog.close()
                dialog.deleteLater()
                owner.close()
                owner.deleteLater()
                self.app.processEvents()

    def test_unreadable_stock_preflight_rejects_without_saving_policy(self) -> None:
        environment.write_operation_policy(self.policy, path=self.policy_path)
        stock_dir = self.stock("111111", ["extra1"])
        before_policy = self.policy_path.read_bytes()
        (stock_dir / "state.json").write_text("{invalid", encoding="utf-8")
        self.policy["extra_sessions"][0]["enabled"] = False
        with self.assertRaisesRegex(RuntimeError, "111111"):
            environment.write_operation_policy(self.policy, path=self.policy_path)
        self.assertEqual(before_policy, self.policy_path.read_bytes())

    def test_reenable_prunes_preexisting_stale_selection_before_policy_becomes_on(self) -> None:
        stock_dir = self.stock("111111", ["extra1"])
        self.policy["extra_sessions"][0]["enabled"] = False
        environment.write_operation_policy(self.policy, path=self.policy_path)
        self.assertEqual(("extra1",), manual_ats_runtime_selected_keys(self.state(stock_dir)))
        self.policy["extra_sessions"][0]["enabled"] = True
        environment.write_operation_policy(self.policy, path=self.policy_path)
        self.assertNotIn("manual_ats_selection", self.state(stock_dir))

    def test_reenable_cleanup_failure_keeps_policy_off(self) -> None:
        stock_dir = self.stock("111111", ["extra1"])
        self.policy["extra_sessions"][0]["enabled"] = False
        environment.write_operation_policy(self.policy, path=self.policy_path)
        self.policy["extra_sessions"][0]["enabled"] = True
        with patch.object(environment, "clear_manual_ats_runtime_selection", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "111111"):
                environment.write_operation_policy(self.policy, path=self.policy_path)
        self.assertFalse(json.loads(self.policy_path.read_text(encoding="utf-8"))["extra_sessions"][0]["enabled"])
        self.assertEqual(("extra1",), manual_ats_runtime_selected_keys(self.state(stock_dir)))


if __name__ == "__main__":
    unittest.main()
