import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QMenu

import gui_auto_trade_ats_ops as ats_ops
import gui_auto_trade_context_menu as context_menu
import gui_ats_utils as ats_utils
from gui_operation_ui_primitives import (
    _PersistentAtsToggleFilter, _add_ats_settings_menu, _dispatch_ats_settings_action,
    ats_session_ui_options,
)
from gui_stock_data import stock_krx_after_structural_candidate, stock_nxt_availability
from manual_ats_runtime import manual_ats_runtime_selected_keys
from routine_order_permission import (
    canonical_routine_order_permission,
    canonical_stock_trading_time_status,
)


class CommonAtsWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_verified_library_structural_candidates(self):
        for code in ("122350", "012210"):
            self.assertIs(stock_nxt_availability(code), False)
            self.assertTrue(stock_krx_after_structural_candidate(code))
        self.assertFalse(stock_krx_after_structural_candidate("999999"))
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(stock_krx_after_structural_candidate("122350", Path(temp)))

    def test_krx_only_interval_and_final_order_guard(self):
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "status": "RUNNING", "trade_enabled": True,
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        gap = ats_utils.auto_trade_operation_session_phase(
            config, state, now_dt=datetime(2026, 10, 16, 15, 50),
            nxt_available=False, krx_after_candidate=True,
        )
        self.assertEqual("BETWEEN_SESSIONS", gap["phase"])
        self.assertTrue(gap["future_session_exists"])
        for hour, minute, active in ((15, 40, False), (15, 59, False),
                                     (16, 0, True), (19, 49, True), (19, 50, False)):
            now = datetime(2026, 10, 16, hour, minute)
            status = canonical_stock_trading_time_status(
                config=config, state=state, now_dt=now, stock_code="122350"
            )
            self.assertIs(status["active"], active, (hour, minute, status))
        blocked = canonical_routine_order_permission(
            state=state, config=config, signal_type="BUY", stock_code="122350",
            now_dt=datetime(2026, 10, 16, 16, 0),
        )
        self.assertFalse(blocked["allowed"])
        self.assertEqual("KRX_AFTER_NEW_ORDER_UNVERIFIED", blocked["reason"])
        blocked_sell = canonical_routine_order_permission(
            state=state, config=config, signal_type="SELL", stock_code="122350",
            now_dt=datetime(2026, 10, 16, 16, 0),
        )
        self.assertEqual("KRX_AFTER_NEW_ORDER_UNVERIFIED", blocked_sell["reason"])
        regular = canonical_routine_order_permission(
            state=state, config=config, signal_type="BUY", stock_code="122350",
            now_dt=datetime(2026, 10, 16, 10, 0),
        )
        self.assertTrue(regular["allowed"])

    def test_nxt_pre_and_post_remain_available(self):
        self.assertTrue(stock_nxt_availability("005930"))
        self.assertTrue(ats_utils.manual_ats_session_has_effective_range(
            "extra1", nxt_available=True
        ))
        self.assertTrue(ats_utils.manual_ats_session_has_effective_range(
            "extra2", nxt_available=True
        ))
        config = {"operation_mode": "CONTINUOUS"}
        for key, hour, minute in (("extra1", 8, 20), ("extra2", 15, 50)):
            state = {"manual_ats_selection": {"selected_sessions": [key]}}
            self.assertTrue(ats_utils.manual_ats_active_now(
                config, state, datetime(2026, 10, 16, hour, minute),
                nxt_available=True,
            ))
        live_nxt_state = {
            "status": "RUNNING", "trade_enabled": True,
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        self.assertTrue(canonical_routine_order_permission(
            state=live_nxt_state, config=config, signal_type="BUY", stock_code="005930",
            now_dt=datetime(2026, 10, 16, 16, 0),
        )["allowed"])

    def test_restricted_and_unknown_library_records_fail_closed(self):
        ordinary = {
            "market": "KOSDAQ", "classification": "일반종목",
            "master_construction": "정상", "master_stock_state": "증거금100%",
            "status": "정상 | 증거금100%",
        }
        from gui_stock_data import _krx_after_structural_record
        self.assertTrue(_krx_after_structural_record(ordinary))
        for change in (
            {"classification": "ETF"}, {"classification": "ETN"},
            {"master_construction": "투자경고"},
            {"status": "투자경고 | 증거금100%"},
            {"master_stock_state": "증거금100%|관리종목"},
            {"classification": "-"}, {"market": ""},
        ):
            self.assertFalse(_krx_after_structural_record({**ordinary, **change}))

    def test_no_overlap_and_exact_display_alias(self):
        self.assertEqual("장후거래", ats_utils.manual_ats_session_labels()["extra2"])
        sessions = {"extra_sessions": [
            {"name": "장마감NTX"}, {"name": "장마감NTX"}, {"name": "사용자 지정"},
        ]}
        with patch.object(ats_utils, "read_operation_policy", return_value=sessions):
            labels = ats_utils.manual_ats_session_labels()
        self.assertEqual("장마감NTX", labels["extra1"])
        self.assertEqual("장후거래", labels["extra2"])
        self.assertEqual("사용자 지정", labels["extra3"])
        self.assertEqual("장후거래", ats_session_ui_options(sessions)[1]["extra2"])
        targets = [(Path("stocks/122350_삼기"), "122350", "삼기")]
        self.assertFalse(ats_ops.auto_trade_selected_manual_ats_enable_availability(
            targets, "extra1"
        )[0])
        self.assertTrue(ats_ops.auto_trade_selected_manual_ats_enable_availability(
            targets, "extra2"
        )[0])
        self.assertFalse(ats_ops.auto_trade_selected_manual_ats_enable_availability(
            [*targets, (Path("stocks/999999_미확인"), "999999", "미확인")], "extra2"
        )[0])
        self.assertFalse(ats_utils.manual_ats_session_has_effective_range(
            "extra1", nxt_available=False, krx_after_candidate=True
        ))
        with patch.object(ats_utils, "manual_ats_session_definition", return_value={
            "enabled": True, "start_time": "15:40:00", "end_time": "15:50:00"
        }):
            self.assertFalse(ats_utils.manual_ats_session_has_effective_range(
                "extra2", nxt_available=False, krx_after_candidate=True
            ))
        with patch.object(ats_utils, "manual_ats_session_definition", return_value={
            "enabled": True, "start_time": "", "end_time": ""
        }):
            self.assertFalse(ats_utils.manual_ats_session_has_effective_range(
                "extra2", nxt_available=False, krx_after_candidate=True
            ))

    def test_menu_disabled_click_and_selected_clear_route(self):
        state = {"extra1": False, "extra2": False}
        toggles = []
        parent_menu = QMenu()
        menu = _add_ats_settings_menu(
            parent_menu, has_selection=True, visible_keys=("extra1", "extra2"),
            labels={"extra1": "장전거래", "extra2": "장후거래"},
            state_getter=lambda: state,
            toggle=lambda key, enabled, label: (toggles.append((key, enabled)), state.__setitem__(key, enabled)),
            enable_availability_getter=lambda key: (key == "extra2", "시간 겹침 없음"),
        )
        actions = {key: action for key, _label, action in menu["session_actions"]}
        self.assertFalse(actions["extra1"].isEnabled())
        self.assertIn("시간 겹침 없음", actions["extra1"].toolTip())
        _dispatch_ats_settings_action(actions["extra1"], menu, toggle=None, liquidate=None)
        self.assertEqual([], toggles)
        state["extra1"] = True
        menu["refresh"]()
        self.assertTrue(actions["extra1"].isEnabled())
        _dispatch_ats_settings_action(actions["extra1"], menu, toggle=None, liquidate=None)
        self.assertEqual([("extra1", False)], toggles)

    def test_persistent_filter_does_not_dispatch_disabled_action(self):
        menu = QMenu()
        action = menu.addAction("장전거래")
        action.setEnabled(False)
        calls = []
        event_filter = _PersistentAtsToggleFilter(menu)
        event_filter.register(action, lambda: calls.append("toggled"))
        watched = MagicMock()
        watched.actionAt.return_value = action
        event = QMouseEvent(
            QEvent.MouseButtonRelease, QPoint(1, 1),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )
        self.assertTrue(event_filter.eventFilter(watched, event))
        self.assertEqual([], calls)

    def test_hidden_old_selection_remains_clearable_in_context_menu(self):
        parent_menu = QMenu()
        with patch.object(context_menu, "manual_ats_visible_session_keys", return_value=()):
            menu = context_menu._add_ats_settings_menu(
                parent_menu, has_selection=True,
                state_getter=lambda: {"extra2": True}, toggle=lambda *_args: None,
                enable_availability_getter=lambda _key: (False, "시간 겹침 없음"),
            )
        self.assertEqual(("extra2",), menu["visible_keys"])
        self.assertTrue(menu["session_actions"][0][2].isEnabled())

    def test_writer_preserves_old_invalid_and_rejects_mixed_enable_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            targets = []
            for code in ("122350", "999999"):
                stock_dir = Path(temp) / code
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps({"operation_mode": "CONTINUOUS"}), encoding="utf-8"
                )
                (stock_dir / "state.json").write_text(json.dumps({
                    "status": "RUNNING",
                    "manual_ats_selection": {"selected_sessions": ["extra1"]},
                }), encoding="utf-8")
                targets.append((stock_dir, code, code))
            with (patch.object(ats_ops, "append_production_event"),
                  patch.object(ats_ops, "append_stock_log"),
                  patch.object(ats_ops, "refresh_auto_trade_views")):
                result = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                    MagicMock(), targets, {"extra2": True}, editable_keys=("extra2",)
                )
            self.assertEqual((1, 1), (result["succeeded"], result["excluded"]))
            good = json.loads((targets[0][0] / "state.json").read_text(encoding="utf-8"))
            bad = json.loads((targets[1][0] / "state.json").read_text(encoding="utf-8"))
            self.assertEqual({"extra1", "extra2"}, set(manual_ats_runtime_selected_keys(good)))
            self.assertEqual(("extra1",), manual_ats_runtime_selected_keys(bad))
            with (patch.object(ats_ops, "append_production_event"),
                  patch.object(ats_ops, "append_stock_log"),
                  patch.object(ats_ops, "refresh_auto_trade_views")):
                cleared = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                    MagicMock(), targets[:1], {"extra1": False}, editable_keys=("extra1",)
                )
            self.assertEqual(1, cleared["succeeded"])


if __name__ == "__main__":
    unittest.main()
