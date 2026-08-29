# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMenu

from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
)
from execution_preview_order_service import preview_execution_for_real_ready_order
from gui_ats_utils import project_manual_ats_execution_order
from gui_auto_trade_context_menu import (
    _add_ats_settings_menu,
    _dispatch_ats_settings_action,
)
from gui_auto_trade_policy import auto_trade_setting_row_projection


class AtsExecutionMethodContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _phase(self, name: str) -> dict[str, object]:
        active_sessions = {
            "REGULAR": ("regular",),
            "ATS": ("extra1",),
            "BETWEEN": (),
            "FINAL": (),
        }[name]
        return {
            "evaluable": True,
            "mode": "CONTINUOUS",
            "phase": {
                "REGULAR": "ACTIVE_SESSION",
                "ATS": "ACTIVE_SESSION",
                "BETWEEN": "BETWEEN_SESSIONS",
                "FINAL": "FINAL_SESSION_ENDED",
            }[name],
            "active": bool(active_sessions),
            "active_sessions": active_sessions,
            "future_session_exists": name == "BETWEEN",
            "final_session_ended": name == "FINAL",
            "sessions": (),
            "invalid_sessions": (),
        }

    def _state(self, method: object = "ROUTINE") -> dict[str, object]:
        selection = {"selected_sessions": ["extra1"]}
        if method is not None:
            selection["execution_method"] = method
        return {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-27 09:00:00",
            "manual_ats_selection": selection,
        }

    def _order(self, side: str = "BUY") -> dict[str, object]:
        return {
            "id": "ORDER_ATS_1",
            "status": "REAL_READY",
            "source_signal_id": "SIGNAL_1",
            "code": "012210",
            "side": side,
            "quantity": 2,
            "price": 12765,
            "execution_enabled": True,
            "order_intent": {"side": side, "hoga": "LIMIT"},
        }

    def test_ui_has_exactly_three_execution_method_choices_and_dispatches_selection(self) -> None:
        for current, expected_label in (
            ("ROUTINE", "루틴"),
            ("MARKET", "시장가"),
            ("CURRENT_PRICE", "현재가"),
        ):
            with self.subTest(current=current):
                menu = QMenu()
                setter = Mock()
                actions = _add_ats_settings_menu(
                    menu,
                    has_selection=True,
                    state_getter=lambda: {"extra1": True},
                    toggle=None,
                    execution_method_state_getter=lambda current=current: {
                        "ok": True,
                        "execution_method": current,
                        "mixed": False,
                    },
                    execution_method_setter=setter,
                    liquidation_available_getter=lambda: False,
                )
                method_actions = actions["method_actions"]
                self.assertEqual(["루틴", "시장가", "현재가"], [item[1] for item in method_actions])
                selected = [item for item in method_actions if item[2].property("atsExecutionMethodCurrent")]
                self.assertEqual(1, len(selected))
                self.assertEqual(expected_label, selected[0][1])
                chosen = next(item for item in method_actions if item[0] == "MARKET")
                self.assertTrue(
                    _dispatch_ats_settings_action(
                        chosen[2], actions, toggle=None, liquidate=None
                    )
                )
                setter.assert_called_once_with("MARKET", "시장가")

    def test_ui_legacy_defaults_routine_and_invalid_is_not_silently_selected(self) -> None:
        menu = QMenu()
        legacy = _add_ats_settings_menu(
            menu,
            has_selection=True,
            state_getter=lambda: {},
            toggle=None,
            execution_method_state_getter=lambda: {
                "ok": True,
                "execution_method": "ROUTINE",
                "mixed": False,
            },
            execution_method_setter=Mock(),
            liquidation_available_getter=lambda: False,
        )
        self.assertTrue(legacy["method_actions"][0][2].property("atsExecutionMethodCurrent"))

        invalid_menu = QMenu()
        invalid = _add_ats_settings_menu(
            invalid_menu,
            has_selection=True,
            state_getter=lambda: {},
            toggle=None,
            execution_method_state_getter=lambda: {
                "ok": False,
                "execution_method": "",
                "reason_code": "INVALID_ATS_EXECUTION_METHOD",
            },
            execution_method_setter=Mock(),
            liquidation_available_getter=lambda: False,
        )
        self.assertFalse(any(action.property("atsExecutionMethodCurrent") for _, _, action in invalid["method_actions"]))
        self.assertEqual("INVALID_ATS_EXECUTION_METHOD", invalid["method_menu"].toolTip())

    def test_projection_uses_method_only_during_ats_active(self) -> None:
        operation_policy = {"manual_operation": {"use_liquidation_policy": False}}
        for method, expected in (
            ("ROUTINE", "루틴"),
            ("MARKET", "시장가"),
            ("CURRENT_PRICE", "현재가"),
        ):
            for phase_name, expected_text in (
                ("REGULAR", "루틴"),
                ("BETWEEN", "루틴"),
                ("ATS", expected),
            ):
                now_by_phase = {
                    "REGULAR": datetime(2026, 8, 27, 10, 0),
                    "BETWEEN": datetime(2026, 8, 27, 15, 30),
                    "ATS": datetime(2026, 8, 27, 8, 10),
                }
                with self.subTest(method=method, phase=phase_name), patch(
                    "gui_auto_trade_policy.auto_trade_operation_session_phase",
                    return_value=self._phase(phase_name),
                ), patch(
                    "gui_auto_trade_policy.read_operation_policy",
                    return_value=operation_policy,
                ):
                    result = auto_trade_setting_row_projection(
                        self._state(method),
                        {"operation_mode": "CONTINUOUS"},
                        operation_category="operation",
                        holding_qty=0,
                        current_session_trade_started=True,
                        persisted_trade_started=True,
                        now_dt=now_by_phase[phase_name],
                    )
                self.assertEqual(
                    "감시/대기" if phase_name == "BETWEEN" else "매수/매도",
                    result["display_status"],
                )
                self.assertEqual(expected_text, result["method_text"])
                self.assertEqual("-", result["liquidation_text"])
                if phase_name == "BETWEEN":
                    self.assertIs(result["method_cell_active"], False)

    def test_execution_projection_is_symmetric_and_does_not_mutate_or_generate_orders(self) -> None:
        for method, side, expected_price, expected_hoga in (
            ("ROUTINE", "BUY", 12765, "LIMIT"),
            ("ROUTINE", "SELL", 12765, "LIMIT"),
            ("MARKET", "BUY", 0, "MARKET"),
            ("MARKET", "SELL", 0, "MARKET"),
            ("CURRENT_PRICE", "BUY", 12800, "CURRENT_PRICE"),
            ("CURRENT_PRICE", "SELL", 12800, "CURRENT_PRICE"),
        ):
            with self.subTest(method=method, side=side):
                order = self._order(side)
                before = deepcopy(order)
                result = project_manual_ats_execution_order(
                    order,
                    {"operation_mode": "CONTINUOUS"},
                    self._state(method),
                    current_price=12800,
                    session_phase=self._phase("ATS"),
                )
                self.assertIs(result["ok"], True)
                self.assertEqual(before, order)
                self.assertEqual(1, len([result["order"]]))
                self.assertEqual(expected_price, result["order"]["price"])
                self.assertEqual(expected_hoga, result["order"]["order_intent"]["hoga"])
                self.assertEqual("SIGNAL_1", result["order"]["source_signal_id"])

    def test_current_price_and_invalid_method_fail_closed(self) -> None:
        for method, price, reason in (
            ("CURRENT_PRICE", None, "ATS_CURRENT_PRICE_UNAVAILABLE"),
            ("CURRENT_PRICE", 0, "ATS_CURRENT_PRICE_UNAVAILABLE"),
            ("BROKEN_VALUE", 12800, "INVALID_ATS_EXECUTION_METHOD"),
        ):
            with self.subTest(method=method, price=price):
                result = project_manual_ats_execution_order(
                    self._order(),
                    {"operation_mode": "CONTINUOUS"},
                    self._state(method),
                    current_price=price,
                    session_phase=self._phase("ATS"),
                )
                self.assertIs(result["ok"], False)
                self.assertEqual(reason, result["reason_code"])

    def test_regular_between_and_final_leave_order_unchanged(self) -> None:
        order = self._order()
        for phase_name in ("REGULAR", "BETWEEN", "FINAL"):
            result = project_manual_ats_execution_order(
                order,
                {"operation_mode": "CONTINUOUS"},
                self._state("MARKET"),
                session_phase=self._phase(phase_name),
            )
            self.assertIs(result["applied"], False)
            self.assertEqual(order, result["order"])

    def test_real_ready_preview_uses_effective_market_order_without_queue_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue_path = Path(temp) / "order_queue.json"
            order = self._order()
            queue_path.write_text(
                json.dumps({"version": 1, "updated_at": "", "orders": [order]}),
                encoding="utf-8",
            )
            before = queue_path.read_bytes()
            effective = project_manual_ats_execution_order(
                order,
                {"operation_mode": "CONTINUOUS"},
                self._state("MARKET"),
                session_phase=self._phase("ATS"),
            )["order"]
            result = preview_execution_for_real_ready_order(
                order["id"],
                {"operator_confirmed": True, "real_trade_enabled": True, "account_no": "12345678"},
                queue_path,
                order_override=effective,
            )
            self.assertIs(result["ok"], True)
            preview = result["preview_result"]["pipeline_result"]["pipeline"]["execution_preview"]
            self.assertEqual("MARKET", preview["hoga_preview"]["hoga"])
            self.assertEqual(before, queue_path.read_bytes())

    def test_boundary_reuses_fresh_market_data_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "012210_삼미금속"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "CONTINUOUS"}), encoding="utf-8"
            )
            (stock_dir / "state.json").write_text(
                json.dumps(self._state("CURRENT_PRICE")), encoding="utf-8"
            )
            callback = Mock(return_value=12900)
            context = AutoTradeOrderExecutionContext(
                kiwoom_connected=lambda: True,
                account_numbers=lambda: ["12345678"],
                selected_account_no=lambda: "12345678",
                send_order_callable=lambda: None,
                selected_stock_info=lambda: None,
                selected_routine_metadata=lambda: None,
                selected_target_instance_ids=lambda: (),
                selected_routine_dir=lambda: None,
                routine_dirs=lambda: [],
                stock_dirs_in_routine=lambda _path: [],
                base_stocks=lambda: [],
                order_queue_path=lambda: Path(temp) / "queue.json",
                order_executions_path=lambda: Path(temp) / "executions.json",
                order_locks_path=lambda: Path(temp) / "locks.json",
                all_group_stock_dirs=lambda: [stock_dir],
                fresh_current_price=callback,
            )
            boundary = AutoTradeOrderExecutionBoundary(context)
            operation_policy = {
                "manual_operation": {"use_regular_market": False},
                "extra_sessions": [
                    {"enabled": True, "start_time": "18:00:00", "end_time": "19:00:00"}
                ],
            }
            with patch("gui_ats_utils.read_operation_policy", return_value=operation_policy):
                result = boundary.project_ats_execution_order(
                    self._order(), now_dt=datetime(2026, 8, 27, 18, 30)
                )
            self.assertIs(result["ok"], True)
            self.assertEqual(12900, result["order"]["price"])
            callback.assert_called_once_with("012210")

    def test_automatic_process_passes_ats_order_to_existing_preview_without_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            send_order = Mock()
            context = AutoTradeOrderExecutionContext(
                kiwoom_connected=lambda: True,
                account_numbers=lambda: ["12345678"],
                selected_account_no=lambda: "12345678",
                send_order_callable=lambda: send_order,
                selected_stock_info=lambda: None,
                selected_routine_metadata=lambda: None,
                selected_target_instance_ids=lambda: (),
                selected_routine_dir=lambda: None,
                routine_dirs=lambda: [],
                stock_dirs_in_routine=lambda _path: [],
                base_stocks=lambda: [],
                order_queue_path=lambda: root / "queue.json",
                order_executions_path=lambda: root / "executions.json",
                order_locks_path=lambda: root / "locks.json",
            )
            boundary = AutoTradeOrderExecutionBoundary(context)
            executable = self._order()
            executable["status"] = "EXECUTABLE"
            real_ready = deepcopy(executable)
            real_ready["status"] = "REAL_READY"
            effective = deepcopy(real_ready)
            effective["price"] = 0
            effective["order_intent"]["hoga"] = "MARKET"
            reads = [
                {"ok": True, "order": executable, "blocked_reasons": []},
                {"ok": True, "order": executable, "blocked_reasons": []},
                {"ok": True, "order": real_ready, "blocked_reasons": []},
            ]
            preview = {
                "ok": False,
                "blocked_reasons": ["TEST_STOP_BEFORE_RUNTIME_COMMIT"],
                "issues": ["TEST_STOP_BEFORE_RUNTIME_COMMIT"],
            }
            with patch.object(
                boundary, "read_order_from_queue_by_id", side_effect=reads
            ), patch.object(
                boundary, "auto_trade_execution_block_reasons", return_value=[]
            ), patch.object(
                boundary, "queue_file_snapshot", return_value={"sha256": "x"}
            ), patch.object(
                boundary,
                "build_real_preflight_guard_from_gui",
                return_value={"operator_confirmed": True, "real_trade_enabled": True},
            ), patch.object(
                boundary, "real_preflight_guard_block_reasons", return_value=[]
            ), patch.object(
                boundary,
                "project_ats_execution_order",
                return_value={
                    "ok": True,
                    "applied": True,
                    "execution_method": "MARKET",
                    "order": effective,
                },
            ), patch(
                "auto_trade_order_execution_boundary.preview_execution_enable",
                return_value={"enable_preview": True, "blocked_reasons": []},
            ), patch(
                "auto_trade_order_execution_boundary.commit_execution_enable",
                return_value={"enabled": True, "blocked_reasons": []},
            ), patch(
                "auto_trade_order_execution_boundary.preview_real_order_preflight",
                return_value={"real_preflight_preview": True, "blocked_reasons": []},
            ), patch(
                "auto_trade_order_execution_boundary.commit_real_order_preflight",
                return_value={"real_preflight_committed": True, "blocked_reasons": []},
            ), patch(
                "auto_trade_order_execution_boundary.preview_execution_for_real_ready_order",
                return_value=preview,
            ) as preview_call, patch(
                "decision_trace_stage_observer.observe_execution_result"
            ):
                result = boundary.process_executable_order_for_auto_trade(
                    executable["id"]
                )

            self.assertEqual("execution_preview", result["stage"])
            preview_call.assert_called_once_with(
                executable["id"],
                {"operator_confirmed": True, "real_trade_enabled": True},
                root / "queue.json",
                order_override=effective,
            )
            send_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
