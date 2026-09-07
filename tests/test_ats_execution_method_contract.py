# -*- coding: utf-8 -*-
from __future__ import annotations

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
from gui_auto_trade_context_menu import _add_ats_settings_menu


class AtsExecutionMethodRemovalContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _boundary(root: Path) -> AutoTradeOrderExecutionBoundary:
        return AutoTradeOrderExecutionBoundary(
            AutoTradeOrderExecutionContext(
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
                order_queue_path=lambda: root / "queue.json",
                order_executions_path=lambda: root / "executions.json",
                order_locks_path=lambda: root / "locks.json",
            )
        )

    def test_production_ats_menu_omits_unauthorized_order_method(self) -> None:
        menu = QMenu()
        with patch(
            "gui_auto_trade_context_menu.manual_ats_visible_session_keys",
            return_value=("extra1",),
        ):
            actions = _add_ats_settings_menu(
                menu,
                has_selection=True,
                state_getter=lambda: {"extra1": True},
                toggle=Mock(),
                liquidation_available_getter=lambda: True,
                include_execution_method=False,
            )

        self.assertIsNone(actions["method_menu"])
        self.assertEqual((), actions["method_actions"])
        self.assertNotIn("주문방식", [action.text() for action in actions["menu"].actions()])
        self.assertTrue(actions["session_actions"])
        self.assertEqual("시장가", actions["market"].text())
        self.assertEqual("현재가", actions["current"].text())

    def test_shared_mock_menu_contract_is_unchanged(self) -> None:
        menu = QMenu()
        setter = Mock()
        actions = _add_ats_settings_menu(
            menu,
            has_selection=True,
            state_getter=lambda: {"extra1": True},
            toggle=Mock(),
            execution_method_state_getter=lambda: {
                "ok": True,
                "execution_method": "ROUTINE",
                "mixed": False,
            },
            execution_method_setter=setter,
            liquidation_available_getter=None,
        )

        self.assertEqual(
            ["루틴", "시장가", "현재가"],
            [label for _key, label, _action in actions["method_actions"]],
        )

    def test_execution_boundary_has_no_generic_ats_order_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            boundary = self._boundary(Path(temp))
            self.assertFalse(hasattr(boundary, "project_ats_execution_order"))

            order = {
                "id": "ORDER_1",
                "code": "005930",
                "side": "BUY",
                "quantity": 1,
                "price": 70000,
                "hoga": "LIMIT",
                "order_intent": {"side": "BUY", "hoga": "LIMIT"},
            }
            result = boundary.finalize_current_price_before_hash(
                order,
                queue_path=Path(temp) / "queue.json",
            )
            self.assertIs(result["ok"], True)
            self.assertIs(result["applied"], False)
            self.assertEqual(order, result["order"])


if __name__ == "__main__":
    unittest.main()
