# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QItemSelectionModel
from PyQt5.QtWidgets import QApplication

import gui_main_emergency_ops as emergency
import gui_review_required_window as review_window
import operation_policy_gate
from runtime_io import read_json_dict


class EmergencyScopeLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.operation_state_path = self.runtime / "operation_state.json"
        self.queue_path = self.runtime / "order_queue.json"
        self.operation_state_path.write_text(
            json.dumps({"emergency_stop": False}), encoding="utf-8"
        )
        self.queue_path.write_text(
            json.dumps({"version": 1, "orders": []}), encoding="utf-8"
        )

    def _stock(
        self,
        code: str,
        *,
        status: str = "STOPPED",
        scope: str = "",
        review: bool = False,
        holding_qty: int = 0,
    ) -> tuple[Path, str, str]:
        stock_dir = self.root / "stocks" / f"{code}_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "orders.json").write_text(
            json.dumps({"orders": []}), encoding="utf-8"
        )
        state: dict[str, object] = {
            "status": status,
            "holding_qty": holding_qty,
            "avg_price": 1000 if holding_qty else 0,
            "trade_enabled": False,
        }
        if status == "EMERGENCY_STOPPED":
            state.update(
                emergency_stopped_at="2026-08-16 09:00:00",
                emergency_reason="USER_EMERGENCY_STOP",
            )
        if scope:
            state["emergency_scope"] = scope
        if review:
            state.update(
                review_required=True,
                review_status="PENDING",
                review_reason="기존 사유",
                review_location="기존 위치",
                review_entered_at="2026-08-15 09:00:00",
            )
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        return stock_dir, code, "TEST"

    @staticmethod
    def _window(
        targets,
        *,
        recovery_ready: bool = True,
    ):
        return SimpleNamespace(
            all_runtime_stock_dirs=lambda: [target[0] for target in targets],
            refresh_auto_trade_assignment_views=Mock(),
            refresh_all=Mock(),
            statusBar=lambda: SimpleNamespace(showMessage=Mock()),
            statusBarMessage=Mock(),
            btn_emergency_stop=SimpleNamespace(setText=Mock()),
            startup_recovery_session_ready=lambda refresh=False: recovery_ready,
        )

    @classmethod
    def _preflight_window(
        cls,
        targets,
        *,
        recovery_ready: bool = True,
        connected: bool = True,
        login_session_id: str = "LOGIN-READY",
        account_no: str = "1234567890",
        authenticated: bool = True,
    ):
        window = cls._window(targets, recovery_ready=recovery_ready)
        window.kiwoom_api = SimpleNamespace(
            is_connected=lambda: connected,
            login_session_id=lambda: login_session_id,
        )
        window.selected_account_no = lambda: account_no
        window._account_authentication_states = {
            account_no: "READY" if authenticated else "FAILED"
        }
        return window

    def _quiet(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(operation_policy_gate, "OPERATION_STATE_PATH", self.operation_state_path)
        )
        stack.enter_context(patch.object(emergency, "ORDER_QUEUE_PATH", self.queue_path))
        for name in (
            "append_changelog",
            "append_stock_log",
            "append_production_event",
            "show_toast",
        ):
            stack.enter_context(patch.object(emergency, name))
        stack.enter_context(patch.object(emergency.QMessageBox, "critical"))
        return stack

    def test_global_activation_scopes_only_non_review_targets(self) -> None:
        normal = [self._stock(f"10000{i}") for i in range(3)]
        reviews = [
            self._stock(f"20000{i}", status="REVIEW_REQUIRED", review=True)
            for i in range(2)
        ]
        review_before = [read_json_dict(target[0] / "state.json") for target in reviews]
        window = self._window(normal + reviews)
        with self._quiet():
            emergency.execute_emergency_stop(window)

        self.assertTrue(read_json_dict(self.operation_state_path)["emergency_stop"])
        for target in normal:
            state = read_json_dict(target[0] / "state.json")
            self.assertEqual("EMERGENCY_STOPPED", state["status"])
            self.assertEqual("GLOBAL", state["emergency_scope"])
            self.assertFalse(state.get("review_required", False))
        self.assertEqual(
            review_before,
            [read_json_dict(target[0] / "state.json") for target in reviews],
        )

    def test_global_stop_preserves_terminal_early_close_notice(self) -> None:
        target = self._stock("300001")
        state = read_json_dict(target[0] / "state.json")
        state.update(
            {
                "operation_command_mode": "EARLY_CLOSE",
                "operation_notice": "EARLY_CLOSE_NO_TARGET",
                "operation_notice_reason": "EARLY_CLOSE_NO_TARGET",
                "operation_notice_at": "2026-08-16 08:00:00",
                "early_close_requested_at": "2026-08-16 07:00:00",
                "early_close_source": "TEST",
                "early_close_method": "AUTO",
                "early_close_policy": {"mode": "AUTO"},
                "auto_close_method": "TEST",
                "auto_close_policy": {"mode": "AUTO"},
                "liquidation_policy_forced": True,
                "liquidation_policy_reason": "TEST",
            }
        )
        (target[0] / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        window = self._window([target])
        with self._quiet():
            emergency.execute_emergency_stop(window)

        stopped = read_json_dict(target[0] / "state.json")
        self.assertEqual("EMERGENCY_STOPPED", stopped["status"])
        self.assertEqual("GLOBAL", stopped["emergency_scope"])
        self.assertEqual("EARLY_CLOSE", stopped["operation_command_mode"])
        self.assertEqual("EARLY_CLOSE_NO_TARGET", stopped["operation_notice"])
        self.assertEqual("EARLY_CLOSE_NO_TARGET", stopped["operation_notice_reason"])
        self.assertEqual("2026-08-16 08:00:00", stopped["operation_notice_at"])
        self.assertEqual("", stopped["early_close_requested_at"])
        self.assertEqual("", stopped["early_close_source"])
        self.assertEqual("", stopped["early_close_method"])
        self.assertEqual({}, stopped["early_close_policy"])
        self.assertEqual("", stopped["auto_close_method"])
        self.assertEqual({}, stopped["auto_close_policy"])
        self.assertEqual(False, stopped["liquidation_policy_forced"])
        self.assertEqual("", stopped["liquidation_policy_reason"])
        self.assertFalse(stopped["trade_enabled"])

        with self._quiet():
            release_result = emergency.release_emergency_stop(window)
        released = read_json_dict(target[0] / "state.json")
        self.assertEqual("COMPLETED", release_result["status"])
        self.assertEqual("STOPPED", released["status"])
        self.assertEqual("", released.get("emergency_scope", ""))
        self.assertEqual("EARLY_CLOSE", released["operation_command_mode"])
        self.assertEqual("EARLY_CLOSE_NO_TARGET", released["operation_notice"])
        self.assertEqual("EARLY_CLOSE_NO_TARGET", released["operation_notice_reason"])
        self.assertEqual("2026-08-16 08:00:00", released["operation_notice_at"])
        self.assertFalse(released.get("review_required", False))
        self.assertFalse(released["trade_enabled"])
        self.assertEqual(1, release_result["normal_count"])
        self.assertEqual(0, release_result["review_count"])
        self.assertEqual(0, release_result["remaining_global_count"])
        self.assertEqual(0, release_result["failed_count"])

    def test_repeated_global_stop_release_preserves_terminal_early_close_notice(self) -> None:
        target = self._stock("300003")
        state = read_json_dict(target[0] / "state.json")
        state.update(
            {
                "operation_command_mode": "EARLY_CLOSE",
                "operation_notice": "EARLY_CLOSE_NO_TARGET",
                "operation_notice_reason": "EARLY_CLOSE_NO_TARGET",
                "operation_notice_at": "2026-08-16 08:00:00",
            }
        )
        (target[0] / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        window = self._window([target])

        for cycle in range(1, 4):
            with self._quiet():
                emergency.execute_emergency_stop(window)
            stopped = read_json_dict(target[0] / "state.json")
            self.assertEqual("EMERGENCY_STOPPED", stopped["status"])
            self.assertEqual("GLOBAL", stopped["emergency_scope"])
            self.assertEqual("EARLY_CLOSE", stopped["operation_command_mode"])
            self.assertEqual("EARLY_CLOSE_NO_TARGET", stopped["operation_notice"])
            self.assertEqual("EARLY_CLOSE_NO_TARGET", stopped["operation_notice_reason"])
            self.assertEqual("2026-08-16 08:00:00", stopped["operation_notice_at"])

            with self._quiet():
                release_result = emergency.release_emergency_stop(window)
            released = read_json_dict(target[0] / "state.json")
            self.assertEqual("COMPLETED", release_result["status"], msg=f"cycle={cycle}")
            self.assertEqual("STOPPED", released["status"], msg=f"cycle={cycle}")
            self.assertEqual("", released.get("emergency_scope", ""), msg=f"cycle={cycle}")
            self.assertEqual("EARLY_CLOSE", released["operation_command_mode"], msg=f"cycle={cycle}")
            self.assertEqual(
                "EARLY_CLOSE_NO_TARGET",
                released["operation_notice"],
                msg=f"cycle={cycle}",
            )
            self.assertEqual(
                "EARLY_CLOSE_NO_TARGET",
                released["operation_notice_reason"],
                msg=f"cycle={cycle}",
            )
            self.assertEqual(
                "2026-08-16 08:00:00",
                released["operation_notice_at"],
                msg=f"cycle={cycle}",
            )
            self.assertFalse(released.get("review_required", False), msg=f"cycle={cycle}")
            self.assertEqual(1, release_result["normal_count"], msg=f"cycle={cycle}")
            self.assertEqual(0, release_result["review_count"], msg=f"cycle={cycle}")
            self.assertEqual(0, release_result["remaining_global_count"], msg=f"cycle={cycle}")
            self.assertEqual(0, release_result["failed_count"], msg=f"cycle={cycle}")

    def test_global_stop_clears_non_terminal_early_close_notice(self) -> None:
        target = self._stock("300002")
        state = read_json_dict(target[0] / "state.json")
        state.update(
            {
                "operation_command_mode": "EARLY_CLOSE",
                "operation_notice": "EARLY_CLOSE_WAITING",
                "operation_notice_reason": "WAITING",
                "operation_notice_at": "2026-08-16 08:00:00",
            }
        )
        (target[0] / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        with self._quiet():
            emergency.execute_emergency_stop(self._window([target]))

        stopped = read_json_dict(target[0] / "state.json")
        self.assertEqual("EMERGENCY_STOPPED", stopped["status"])
        self.assertEqual("GLOBAL", stopped["emergency_scope"])
        self.assertEqual("EARLY_CLOSE", stopped["operation_command_mode"])
        self.assertEqual("", stopped["operation_notice"])
        self.assertEqual("", stopped["operation_notice_reason"])
        self.assertEqual("", stopped["operation_notice_at"])

    def test_global_preflight_failure_mutates_nothing_and_keeps_latch(self) -> None:
        target = self._stock("300001", status="EMERGENCY_STOPPED", scope="GLOBAL")
        before = read_json_dict(target[0] / "state.json")
        self.operation_state_path.write_text(
            json.dumps({"emergency_stop": True}), encoding="utf-8"
        )
        with self._quiet(), patch.object(
            emergency,
            "global_emergency_release_preflight",
            return_value=(False, "RECOVERY_NOT_READY"),
        ):
            result = emergency.release_emergency_stop(self._window([target]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(before, read_json_dict(target[0] / "state.json"))
        self.assertTrue(read_json_dict(self.operation_state_path)["emergency_stop"])

    def test_pre_emergency_readiness_reasons_are_fail_closed(self) -> None:
        self.assertEqual(
            (False, "LOGIN_NOT_READY"),
            emergency._evaluate_emergency_preflight(
                self._preflight_window([], connected=False)
            ),
        )
        self.assertEqual(
            (False, "LOGIN_NOT_READY"),
            emergency._evaluate_emergency_preflight(
                self._preflight_window([], login_session_id="")
            ),
        )
        self.assertEqual(
            (False, "ACCOUNT_NOT_SELECTED"),
            emergency._evaluate_emergency_preflight(
                self._preflight_window([], account_no="")
            ),
        )
        self.assertEqual(
            (False, "ACCOUNT_NOT_AUTHENTICATED"),
            emergency._evaluate_emergency_preflight(
                self._preflight_window([], authenticated=False)
            ),
        )
        self.assertEqual(
            (False, "RECOVERY_NOT_READY"),
            emergency._evaluate_emergency_preflight(
                self._preflight_window([], recovery_ready=False)
            ),
        )
        self.assertEqual(
            (True, ""),
            emergency._evaluate_emergency_preflight(self._preflight_window([])),
        )
        owner = self._preflight_window([], connected=False)
        adapter = SimpleNamespace(
            _window=owner,
            startup_recovery_session_ready=owner.startup_recovery_session_ready,
        )
        self.assertEqual(
            (False, "LOGIN_NOT_READY"),
            emergency._evaluate_emergency_preflight(adapter),
        )

    def test_pre_connection_global_and_selected_stop_mutate_nothing(self) -> None:
        target = self._stock("300003")
        before = read_json_dict(target[0] / "state.json")
        window = self._preflight_window([target], connected=False)
        with self._quiet(), patch.object(emergency, "show_toast") as toast:
            emergency.execute_emergency_stop(window)
            selected_result = emergency.execute_selected_emergency_stop(
                window,
                [target],
            )

        self.assertFalse(read_json_dict(self.operation_state_path)["emergency_stop"])
        self.assertEqual(before, read_json_dict(target[0] / "state.json"))
        self.assertEqual(0, selected_result["changed_count"])
        self.assertFalse(
            read_json_dict(target[0] / "state.json").get("review_required", False)
        )
        self.assertEqual(2, toast.call_count)
        self.assertTrue(
            all(
                call.kwargs["message"]
                == emergency._PRE_EMERGENCY_NOT_READY_MESSAGE
                for call in toast.call_args_list
            )
        )

    def test_global_preflight_reuses_recovery_and_queue_readiness(self) -> None:
        with patch.object(emergency, "ORDER_QUEUE_PATH", self.queue_path):
            self.assertEqual(
                (False, "RECOVERY_NOT_READY"),
                emergency.global_emergency_release_preflight(
                    self._window([], recovery_ready=False)
                ),
            )
            self.queue_path.write_text("{", encoding="utf-8")
            self.assertEqual(
                (False, "RUNTIME_DAMAGED"),
                emergency.global_emergency_release_preflight(
                    self._window([], recovery_ready=True)
                ),
            )

    def test_global_release_normal_and_problem_stock_clear_global_ownership(self) -> None:
        normal = self._stock("400001", status="EMERGENCY_STOPPED", scope="GLOBAL")
        problem = self._stock(
            "400002", status="EMERGENCY_STOPPED", scope="GLOBAL", holding_qty=1
        )
        self.operation_state_path.write_text(
            json.dumps({"emergency_stop": True}), encoding="utf-8"
        )
        with self._quiet():
            result = emergency.release_emergency_stop(self._window([normal, problem]))

        normal_state = read_json_dict(normal[0] / "state.json")
        problem_state = read_json_dict(problem[0] / "state.json")
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("STOPPED", normal_state["status"])
        self.assertEqual("", normal_state["emergency_scope"])
        self.assertEqual("REVIEW_REQUIRED", problem_state["status"])
        self.assertEqual("PENDING", problem_state["review_status"])
        self.assertEqual("", problem_state["emergency_scope"])
        self.assertEqual("", problem_state["emergency_reason"])
        self.assertFalse(read_json_dict(self.operation_state_path)["emergency_stop"])

    def test_global_write_failure_keeps_scope_and_latch(self) -> None:
        target = self._stock("500001", status="EMERGENCY_STOPPED", scope="GLOBAL")
        self.operation_state_path.write_text(
            json.dumps({"emergency_stop": True}), encoding="utf-8"
        )
        with self._quiet(), patch(
            "runtime_stock_state_mutation.write_state_json", return_value=False
        ):
            result = emergency.release_emergency_stop(self._window([target]))

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual("INCOMPLETE", result["status"])
        self.assertEqual("EMERGENCY_STOPPED", state["status"])
        self.assertEqual("GLOBAL", state["emergency_scope"])
        self.assertTrue(read_json_dict(self.operation_state_path)["emergency_stop"])

    def test_selected_scope_release_contract_and_global_isolation(self) -> None:
        selected = self._stock(
            "600001", status="EMERGENCY_STOPPED", scope="SELECTED", review=True
        )
        global_target = self._stock(
            "600002", status="EMERGENCY_STOPPED", scope="GLOBAL"
        )
        unknown = self._stock("600003", status="EMERGENCY_STOPPED")
        window = self._window([selected, global_target, unknown])
        with self._quiet():
            selected_result = emergency.release_emergency_stop_target(
                window, *selected
            )
            global_result = emergency.release_emergency_stop_target(
                window, *global_target
            )
            unknown_result = emergency.release_emergency_stop_target(window, *unknown)

        self.assertEqual(emergency.RELEASED_TO_REVIEW, selected_result)
        self.assertEqual("REVIEW_REQUIRED", read_json_dict(selected[0] / "state.json")["status"])
        self.assertEqual(emergency.RELEASE_SKIPPED, global_result)
        self.assertEqual(emergency.RELEASE_SKIPPED, unknown_result)
        self.assertEqual("EMERGENCY_STOPPED", read_json_dict(global_target[0] / "state.json")["status"])
        self.assertEqual("EMERGENCY_STOPPED", read_json_dict(unknown[0] / "state.json")["status"])

    def test_global_latch_blocks_selected_release_without_mutation(self) -> None:
        target = self._stock(
            "700001", status="EMERGENCY_STOPPED", scope="SELECTED", review=True
        )
        before = read_json_dict(target[0] / "state.json")
        self.operation_state_path.write_text(
            json.dumps({"emergency_stop": True}), encoding="utf-8"
        )
        with self._quiet():
            result = emergency.execute_selected_emergency_release(
                self._window([target]), [target]
            )

        self.assertEqual(1, result["blocked_count"])
        self.assertEqual(before, read_json_dict(target[0] / "state.json"))
        self.assertTrue(read_json_dict(self.operation_state_path)["emergency_stop"])

    def test_selected_guard_failure_stays_in_emergency_and_is_not_success(self) -> None:
        target = self._stock(
            "800001",
            status="EMERGENCY_STOPPED",
            scope="SELECTED",
            review=True,
            holding_qty=1,
        )
        with self._quiet(), patch.object(emergency, "show_toast") as toast:
            result = emergency.execute_selected_emergency_release(
                self._window([target]), [target]
            )

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(1, result["blocked_count"])
        self.assertEqual("EMERGENCY_STOPPED", state["status"])
        self.assertEqual("SELECTED", state["emergency_scope"])
        self.assertEqual("PENDING", state["review_status"])
        self.assertNotIn("완료", toast.call_args.kwargs["message"])

    def test_pending_order_reason_is_operator_text_and_evidence_is_preserved(self) -> None:
        target = self._stock("900001", status="EMERGENCY_STOPPED", scope="GLOBAL")
        self.queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "orders": [
                        {
                            "stock_code": "900001",
                            "status": "QUEUED",
                            "side": "BUY",
                            "remaining_qty": 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.operation_state_path.write_text(
            json.dumps({"emergency_stop": True}), encoding="utf-8"
        )
        with self._quiet():
            emergency.release_emergency_stop(self._window([target]))

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual("미체결 주문 존재", state["review_reason"])
        self.assertNotIn("PENDING_ORDER", state["review_reason"])
        self.assertIn("PENDING_ORDER", state["review_detail"])

    def test_review_window_has_no_selected_release_entrypoint(self) -> None:
        target = self._stock(
            "910001", status="EMERGENCY_STOPPED", scope="SELECTED", review=True
        )
        row = {
            "stock_dir": target[0],
            "code": target[1],
            "name": target[2],
            "routine_name": "루틴",
            "review_location": "종목 긴급정지",
            "review_reason": "사용자 긴급정지",
            "review_entered_at": "2026-08-16 09:00:00",
            "display_status": "미해결",
            "return_availability": "BLOCKED",
            "return_block_reason": "EMERGENCY_STOP_ACTIVE",
        }
        with (
            patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
            patch.object(review_window, "read_review_policy", return_value={}),
        ):
            window = review_window.GlobalReviewRequiredWindow()
            self.addCleanup(window.close)
            window.table.selectionModel().select(
                window.table.model().index(0, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
            window.refresh_operator_guidance()
            self.assertFalse(hasattr(window, "btn_emergency_release"))
            self.assertFalse(hasattr(window, "release_selected_emergency_stopped_items"))

    def test_review_window_disables_selected_release_during_global_latch(self) -> None:
        target = self._stock(
            "920001", status="EMERGENCY_STOPPED", scope="SELECTED", review=True
        )
        row = {
            "stock_dir": target[0],
            "code": target[1],
            "name": target[2],
            "display_status": "미해결",
            "return_availability": "BLOCKED",
            "return_block_reason": "EMERGENCY_STOP_ACTIVE",
        }
        with (
            patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
            patch.object(review_window, "read_review_policy", return_value={}),
        ):
            window = review_window.GlobalReviewRequiredWindow()
            self.addCleanup(window.close)
            window.table.selectionModel().select(
                window.table.model().index(0, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
            window.refresh_operator_guidance()
            self.assertFalse(hasattr(window, "btn_emergency_release"))


if __name__ == "__main__":
    unittest.main()
