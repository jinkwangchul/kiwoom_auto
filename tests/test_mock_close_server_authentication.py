# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import MethodType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_windows
import mock_validation_context_menu as context_menu
from gui_windows import MainWindow
from mock_validation_contract import MockValidationError
from mock_validation_host import MockValidationHost
from mock_validation_ui_actions import MockValidationUIActions
from tests.test_mock_validation_host_ui import NOW, _Api, _reference


class _ConnectedApi(_Api):
    def __init__(self) -> None:
        super().__init__()
        self.connected = True
        self.connection_checks = 0

    def is_connected(self):
        self.connection_checks += 1
        return self.connected


class MockCloseServerAuthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project_root = Path(self.temporary.name) / "project"
        self.project_root.mkdir()
        self.api = _ConnectedApi()
        self.host = MockValidationHost(
            self.api,
            project_root=self.project_root,
            now_factory=lambda: NOW,
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
                "review_policy": {"long_term_holding_enabled": False},
            },
            candles_provider=lambda **_kwargs: {
                "available": False,
                "candles": [],
                "availability_state": "SOURCE_UNAVAILABLE",
            },
        )
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)
        created = self.actions.create_waiting_session(_reference("005930"))
        self.session_id = created["document"]["session"]["validation_session_id"]

    def _snapshot(self):
        return (
            deepcopy(self.host.current_session("005930")),
            deepcopy(self.host.repository.read_events(self.session_id)),
        )

    def _assert_server_not_connected(self, operation) -> None:
        with self.assertRaises(MockValidationError) as raised:
            operation()
        self.assertEqual("SERVER_NOT_CONNECTED", str(raised.exception))

    def test_pre_operation_individual_liquidation_requires_server_then_keeps_zero_holding_contract(self):
        before = self._snapshot()
        self.api.connected = False

        self._assert_server_not_connected(
            lambda: self.host.request_instance_individual_liquidation(
                "005930",
                "A",
                method="현재가",
                minutes_before_regular_close="10",
                as_of=NOW,
            )
        )

        self.assertEqual(before, self._snapshot())
        self.api.connected = True
        result = self.host.request_instance_individual_liquidation(
            "005930",
            "A",
            method="현재가",
            minutes_before_regular_close="10",
            as_of=NOW,
        )
        document = self.host.current_session("005930")
        self.assertEqual("REQUESTED", result["status"])
        self.assertEqual("NEXT_OPERATION", result["reservation_scope"])
        self.assertEqual(
            "CURRENT_PRICE",
            document["individual_liquidation_reservations_by_instance"]["A"]["method"],
        )
        self.assertEqual([], document["orders"])
        self.assertEqual([], document["fills"])

    def test_active_and_legacy_early_close_fail_before_any_mock_mutation(self):
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        before = self._snapshot()
        self.api.connected = False

        self._assert_server_not_connected(
            lambda: self.host.request_instance_early_close(
                "005930", "A", method="시장가", as_of=NOW
            )
        )
        self._assert_server_not_connected(
            lambda: self.host.request_early_close(
                "005930", method="시장가", as_of=NOW
            )
        )

        self.assertEqual(before, self._snapshot())

    def test_authentication_is_fresh_read_and_fail_closed_on_checker_error(self):
        self.assertTrue(self.actions.server_authenticated())
        self.api.connected = False
        self.assertFalse(self.actions.server_authenticated())
        self.assertGreaterEqual(self.api.connection_checks, 2)

        self.api.is_connected = Mock(side_effect=RuntimeError("connection unavailable"))
        self.assertFalse(self.host.server_authenticated())

    def test_profit_loss_blocks_before_dialog_and_individual_blocks_before_dispatch(self):
        target = context_menu.MockContextTarget(
            row_kind="mock_routine_instance",
            stock_code="005930",
            stock_name="삼성전자",
            validation_session_id=self.session_id,
            routine_instance_id="A",
        )
        window = SimpleNamespace()
        window._mock_action_result = MethodType(MainWindow._mock_action_result, window)
        self.api.connected = False
        before = self._snapshot()

        with (
            patch.object(context_menu, "ProfitLossEarlyCloseDialog") as dialog,
            patch.object(context_menu, "_fresh_operation") as fresh_operation,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            context_menu._apply_mock_profit_loss_early_close(
                window, 0, target, self.actions
            )
            context_menu._apply_mock_individual_liquidation(
                window,
                0,
                target,
                self.actions,
                method="이월",
                minutes="5",
            )

        dialog.assert_not_called()
        fresh_operation.assert_not_called()
        self.assertEqual(2, toast.call_count)
        self.assertTrue(
            all(
                item.args[1] == "키움 서버에 로그인되어 있지 않습니다."
                for item in toast.call_args_list
            )
        )
        self.assertEqual(before, self._snapshot())

    def test_direct_early_close_rechecks_after_authenticated_menu_open(self):
        window = SimpleNamespace()
        window._mock_action_result = MethodType(MainWindow._mock_action_result, window)
        operation = Mock(return_value={"status": "REQUESTED"})
        self.assertTrue(self.actions.server_authenticated())
        self.api.connected = False

        with patch.object(gui_windows, "show_toast") as toast:
            admitted = context_menu._run_mock_close_edit(
                window,
                "모의 Instance 조기마감",
                self.actions,
                operation,
            )

        self.assertFalse(admitted)
        operation.assert_not_called()
        toast.assert_called_once_with(
            window,
            "키움 서버에 로그인되어 있지 않습니다.",
            duration_ms=2500,
        )

    def test_early_close_cancel_keeps_existing_no_initial_auth_gate(self):
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        self.host.session_service.set_instance_position(
            self.session_id,
            "A",
            holding_qty=1,
            available_qty=1,
            average_price=100,
            realized_cost_basis=100,
            command_id="MC-auth-cancel-position-A",
        )
        self.host.request_instance_early_close(
            "005930", "A", method="루틴", as_of=NOW
        )
        self.api.connected = False

        result = self.host.cancel_instance_early_close("005930", "A", as_of=NOW)

        self.assertEqual("CANCELLED", result["status"])


if __name__ == "__main__":
    unittest.main()
