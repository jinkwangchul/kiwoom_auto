# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication, QDialog, QWidget

import gui_auto_trade_close as close
from close_liquidation_command import (
    EARLY_CLOSE_REQUEST,
    inspect_close_liquidation_availability,
)
from gui_window_policy import configure_persistent_feature_window, persistent_feature_owner
from tests.participant_owner_fixture import attach_participant_owner


class _MainOwner(QWidget):
    def __init__(self, *, connected: bool, recovery_allowed: bool = True) -> None:
        super().__init__()
        self.kiwoom_api = SimpleNamespace(
            is_connected=lambda: connected,
            login_session_id=lambda: "login-session-1" if connected else "",
        )
        self._selected_account_no = "account-1" if connected else ""
        self._account_authentication_states = {
            "account-1": "READY" if connected else ""
        }
        self._account_query_states = {
            "account-1": "READY" if connected else ""
        }
        self.recovery_decision = SimpleNamespace(
            allowed=recovery_allowed,
            reason_code=("RECOVERY_COMPLETED" if recovery_allowed else "RECOVERY_NOT_STARTED"),
            evidence=(),
        )
        self.recovery_calls: list[tuple[str, str]] = []

    def selected_account_no(self) -> str:
        return self._selected_account_no

    def startup_recovery_session_ready(self, *, refresh: bool = False) -> bool:
        del refresh
        return self.recovery_decision.allowed is True

    def production_recovery_gate_for_stock(self, code: str, *, caller_name: str):
        self.recovery_calls.append((code, caller_name))
        return self.recovery_decision

    def production_recovery_block_user_message(self, decision) -> str:
        return f"RECOVERY_BLOCK:{decision.reason_code}"


class AutoTradeCloseOwnerResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _surface(self, owner: QWidget) -> QDialog:
        surface = QDialog(None)
        configure_persistent_feature_window(surface, owner)
        self.addCleanup(surface.close)
        self.addCleanup(owner.close)
        self.assertIsNone(surface.parent())
        self.assertIs(owner, persistent_feature_owner(surface))
        return surface

    def test_connected_logical_owner_does_not_report_logged_out(self) -> None:
        owner = _MainOwner(connected=True)
        surface = self._surface(owner)

        self.assertEqual("", close._kiwoom_server_login_block_message(surface))
        self.assertTrue(owner.kiwoom_api.is_connected())
        self.assertTrue(owner.selected_account_no())
        self.assertEqual("READY", owner._account_authentication_states["account-1"])
        self.assertEqual("READY", owner._account_query_states["account-1"])
        self.assertTrue(owner.startup_recovery_session_ready(refresh=False))

    def test_disconnected_logical_owner_keeps_existing_login_block(self) -> None:
        owner = _MainOwner(connected=False)
        surface = self._surface(owner)

        self.assertEqual(
            "키움 서버에 로그인되어 있지 않습니다.",
            close._kiwoom_server_login_block_message(surface),
        )

    def test_recovery_gate_uses_logical_owner_and_blocks_when_not_ready(self) -> None:
        owner = _MainOwner(connected=True, recovery_allowed=False)
        surface = self._surface(owner)

        decision = close._production_recovery_gate(
            surface,
            "005930",
            "EARLY_CLOSE_REQUEST",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual([("005930", "EARLY_CLOSE_REQUEST")], owner.recovery_calls)
        self.assertEqual(
            "RECOVERY_BLOCK:RECOVERY_NOT_STARTED",
            close._recovery_block_user_message(surface, decision),
        )

    def test_recovery_gate_uses_logical_owner_and_allows_completed(self) -> None:
        owner = _MainOwner(connected=True, recovery_allowed=True)
        surface = self._surface(owner)

        decision = close._production_recovery_gate(
            surface,
            "005930",
            "EARLY_CLOSE_REQUEST",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual([("005930", "EARLY_CLOSE_REQUEST")], owner.recovery_calls)

    def test_operating_filter_reads_current_session_participation_from_logical_owner(self) -> None:
        owner = _MainOwner(connected=True, recovery_allowed=True)
        attach_participant_owner(owner, {"005930"})
        surface = self._surface(owner)
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Samsung"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "holding_qty": 1,
                    }
                ),
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text("{}", encoding="utf-8")

            allowed = inspect_close_liquidation_availability(
                surface,
                stock_dir,
                "005930",
                intent=EARLY_CLOSE_REQUEST,
            )
            other = inspect_close_liquidation_availability(
                surface,
                stock_dir,
                "000660",
                intent=EARLY_CLOSE_REQUEST,
            )
            state_path.write_text(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "trade_enabled": False,
                        "holding_qty": 1,
                    }
                ),
                encoding="utf-8",
            )
            stopped = inspect_close_liquidation_availability(
                surface,
                stock_dir,
                "005930",
                intent=EARLY_CLOSE_REQUEST,
            )

        self.assertTrue(allowed.allowed)
        self.assertEqual("NOT_CURRENT_PARTICIPANT", other.reason_code)
        self.assertEqual("NOT_CURRENT_PARTICIPANT", stopped.reason_code)

    def test_early_close_confirmation_uses_actual_scope_names(self) -> None:
        all_scope = SimpleNamespace(
            _all_stocks_scope_active=True,
            current_selected_routine_row_metadata=lambda: None,
            current_selected_routine_name=lambda: "",
        )
        group_scope = SimpleNamespace(
            _all_stocks_scope_active=False,
            current_selected_routine_row_metadata=lambda: {
                "row_kind": "definition",
                "definition_name": "성장주그룹",
            },
            current_selected_routine_name=lambda: "",
        )
        routine_scope = SimpleNamespace(
            _all_stocks_scope_active=False,
            current_selected_routine_row_metadata=lambda: {
                "row_kind": "instance",
                "instance_name": "오전루틴",
            },
            current_selected_routine_name=lambda: "오전루틴",
        )

        self.assertEqual(
            "전체운영 12종목을 조기마감합니다. 진행하시겠습니까?",
            close._early_close_confirmation_message(all_scope, 12),
        )
        self.assertEqual(
            "성장주그룹 4종목을 조기마감합니다. 진행하시겠습니까?",
            close._early_close_confirmation_message(group_scope, 4),
        )
        self.assertEqual(
            "오전루틴 2종목을 조기마감합니다. 진행하시겠습니까?",
            close._early_close_confirmation_message(routine_scope, 2),
        )


if __name__ == "__main__":
    unittest.main()
