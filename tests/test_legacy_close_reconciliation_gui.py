# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox, QTableWidgetItem, QWidget

import gui_review_required_window as review_window
from kiwoom_api import KiwoomApi
from production_recovery_contract import (
    ACCOUNT_COMPLETED,
    BrokerAccountSnapshot,
    RecoverySessionIdentity,
    recovery_request_id,
)


def identity(**changes: str) -> RecoverySessionIdentity:
    values = {
        "recovery_session_id": "RECOVERY-7C",
        "login_session_id": "LOGIN-7C",
        "account_no": "81290000",
        "trading_day": date.today().isoformat(),
        "requested_at": f"{date.today().isoformat()}T09:00:00+09:00",
    }
    values.update(changes)
    return RecoverySessionIdentity(**values)


def snapshot(item: RecoverySessionIdentity) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_no=item.account_no,
        trading_day=item.trading_day,
        requested_at=item.requested_at,
        completed_at=f"{item.trading_day}T09:00:03+09:00",
        request_id=recovery_request_id(item, "ACCOUNT"),
        recovery_session_id=item.recovery_session_id,
        is_complete=True,
        holdings=(),
        open_orders=(),
        source="KIWOOM_OPENAPI",
        errors=(),
    )


class Owner(QWidget):
    def __init__(
        self,
        handoff: dict[str, object] | None,
        *,
        account_no: str = "81290000",
        login_session_id: str = "LOGIN-7C",
    ) -> None:
        super().__init__()
        self.handoff = handoff
        self.account_no = account_no
        self.kiwoom_api = SimpleNamespace(login_session_id=lambda: login_session_id)

    def latest_completed_recovery_handoff(self) -> dict[str, object] | None:
        return self.handoff

    def selected_account_no(self) -> str:
        return self.account_no


class LegacyCloseReconciliationGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stocks = self.root / "stocks"
        self.stocks.mkdir()
        self.queue = self.root / "order_queue.json"
        self.queue.write_text('{"version":1,"revision":0,"orders":[]}', encoding="utf-8")
        self.identity = identity()
        self.snapshot = snapshot(self.identity)
        self.handoff = {
            "identity": self.identity,
            "snapshot": self.snapshot,
            "recovery_status": ACCOUNT_COMPLETED,
            "holdings_complete": True,
            "open_orders_complete": True,
            "account_display": "8129****",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_stock(
        self,
        code: str,
        name: str,
        *,
        mode: str = "EARLY_CLOSE",
        notice: str = "",
    ) -> Path:
        stock_dir = self.stocks / f"{code}_{name}"
        stock_dir.mkdir()
        state = {
            "status": "EMERGENCY_STOPPED",
            "holding_qty": 0,
            "avg_price": 0,
            "holding_amount": 0,
            "review_required": True,
            "review_status": "PENDING",
            "review_reason": "legacy",
            "review_location": "운영 데이터 불일치",
            "review_entered_at": "2026-08-02 14:17:20",
            "trade_enabled": False,
            "emergency_reason": "legacy",
            "emergency_scope": "",
            "operation_command_mode": mode,
            "operation_notice": notice,
            "operation_notice_reason": "",
            "operation_notice_at": "",
            "liquidation_policy_forced": False,
            "close_routine_final_sell_ordered": False,
            "close_routine_final_sell_ordered_at": "",
            "routine_instance_id": "INSTANCE-1",
            "updated_at": "2026-08-02 14:17:20",
        }
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stock_dir / "orders.json").write_text('{"orders":[]}', encoding="utf-8")
        return stock_dir

    def make_window(self, owner: Owner | None = None):
        actual_owner = owner or Owner(self.handoff)
        with mock.patch.object(
            review_window.GlobalReviewRequiredWindow, "_central_review_rows", return_value=[]
        ):
            window = review_window.GlobalReviewRequiredWindow(actual_owner)
        return actual_owner, window

    def select(self, window, targets: list[tuple[Path, str, str]]) -> None:
        window.table.setRowCount(len(targets))
        window._review_rows_by_stock_dir = {}
        for row_index, (stock_dir, code, name) in enumerate(targets):
            item = QTableWidgetItem(code)
            item.setData(Qt.UserRole, str(stock_dir))
            item.setData(Qt.UserRole + 1, code)
            item.setData(Qt.UserRole + 2, name)
            window.table.setItem(row_index, 0, item)
            window._review_rows_by_stock_dir[str(stock_dir)] = {
                "stock_dir": str(stock_dir),
                "return_availability": "BLOCKED",
            }
        window.table.selectAll()

    def test_legacy_reconciliation_is_not_an_operator_button(self) -> None:
        _owner, window = self.make_window()
        self.assertFalse(hasattr(window, "btn_legacy_close_reconcile"))
        self.assertTrue(callable(window.reconcile_selected_legacy_early_close))
        window.close()

    def test_normal_virtual_missing_and_corrupt_rows_are_not_targets(self) -> None:
        normal = self.make_stock("000660", "SK하이닉스", mode="NORMAL")
        missing = self.stocks / "111111_누락"
        missing.mkdir()
        corrupt = self.stocks / "222222_손상"
        corrupt.mkdir()
        (corrupt / "state.json").write_text("{", encoding="utf-8")
        _owner, window = self.make_window()
        targets = [
            (normal, "000660", "SK하이닉스"),
            (missing, "111111", "누락"),
            (corrupt, "222222", "손상"),
            (Path("virtual://review"), "333333", "가상"),
        ]
        window.selected_stock_dirs = mock.Mock(return_value=targets)
        with mock.patch.object(review_window, "PROJECT_ROOT", self.root):
            self.assertEqual([], window._legacy_close_reconciliation_targets())
        window.close()

    def test_handoff_missing_or_current_identity_mismatch_calls_service_zero(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        owners = (
            Owner(None),
            Owner(self.handoff, account_no="99990000"),
            Owner(self.handoff, login_session_id="OTHER"),
            Owner(
                {
                    **self.handoff,
                    "identity": identity(trading_day="2000-01-01"),
                    "snapshot": snapshot(identity(trading_day="2000-01-01")),
                }
            ),
        )
        for owner in owners:
            _owner, window = self.make_window(owner)
            window.selected_stock_dirs = mock.Mock(
                return_value=[(stock, "323410", "카카오뱅크")]
            )
            with (
                mock.patch.object(review_window, "PROJECT_ROOT", self.root),
                mock.patch.object(
                    review_window, "reconcile_legacy_early_close_no_target"
                ) as service,
            ):
                window.reconcile_selected_legacy_early_close()
            service.assert_not_called()
            self.assertEqual(0, window.last_legacy_close_reconciliation_result["service_calls"])
            window.close()

    def test_valid_selection_keeps_backend_available_without_operator_button(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        _owner, window = self.make_window()
        self.select(window, [(stock, "323410", "카카오뱅크")])
        with mock.patch.object(review_window, "PROJECT_ROOT", self.root):
            window.refresh_operator_guidance()
        self.assertFalse(hasattr(window, "btn_legacy_close_reconcile"))
        self.assertTrue(callable(window.reconcile_selected_legacy_early_close))
        window.close()

    def test_completed_handoff_prepares_legacy_close_without_operator_button(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        state_path = stock / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({
            "status": "REVIEW_REQUIRED",
            "emergency_reason": "",
            "emergency_scope": "",
        })
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        _owner, window = self.make_window()
        row = {"stock_dir": stock, "code": "323410"}
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(review_window, "ORDER_QUEUE_PATH", self.queue),
            mock.patch.object(window, "_central_review_rows", return_value=[row]),
            mock.patch.object(
                review_window,
                "reconcile_review_stock_position",
                return_value={"status": "NO_CHANGE", "reason": "POSITION_ALREADY_MATCHED"},
            ),
            mock.patch.object(
                review_window,
                "reconcile_legacy_early_close_no_target",
                return_value={"status": "COMPLETED", "reason": "LEGACY_EARLY_CLOSE_NO_TARGET_RECONCILED"},
            ) as service,
        ):
            window._prepare_safe_review_reconciliation()
        service.assert_called_once()
        self.assertEqual(1, window.last_legacy_close_reconciliation_result["service_calls"])
        self.assertFalse(hasattr(window, "btn_legacy_close_reconcile"))
        window.close()

    def test_offline_local_terminal_evidence_is_a_no_write_preparation(self) -> None:
        stock = self.make_stock(
            "323410", "카카오뱅크", notice="EARLY_CLOSE_NO_TARGET"
        )
        state_path = stock / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({
            "status": "REVIEW_REQUIRED",
            "emergency_reason": "",
            "emergency_scope": "",
        })
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        before = state_path.read_bytes()
        _owner, window = self.make_window(Owner(None))
        row = {"stock_dir": stock, "code": "323410"}
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(window, "_central_review_rows", return_value=[row]),
            mock.patch.object(review_window, "reconcile_review_stock_position") as position,
            mock.patch.object(review_window, "reconcile_legacy_early_close_no_target") as legacy,
        ):
            window._prepare_safe_review_reconciliation()
        position.assert_not_called()
        legacy.assert_not_called()
        self.assertEqual(before, state_path.read_bytes())
        self.assertEqual("LOCAL_SAFE_NO_WRITER_REQUIRED", window.last_position_reconciliation_result["reason"])
        window.close()

    def test_confirmation_cancel_calls_service_zero(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        _owner, window = self.make_window()
        window.selected_stock_dirs = mock.Mock(return_value=[(stock, "323410", "카카오뱅크")])
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No),
            mock.patch.object(review_window, "reconcile_legacy_early_close_no_target") as service,
        ):
            window.reconcile_selected_legacy_early_close()
        service.assert_not_called()
        self.assertEqual("CANCELED", window.last_legacy_close_reconciliation_result["status"])
        window.close()

    def test_one_and_two_targets_call_service_once_each(self) -> None:
        first = self.make_stock("323410", "카카오뱅크")
        second = self.make_stock("086520", "에코프로")
        _owner, window = self.make_window()
        window.selected_stock_dirs = mock.Mock(
            return_value=[(first, "323410", "카카오뱅크"), (second, "086520", "에코프로")]
        )
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(
                review_window,
                "reconcile_legacy_early_close_no_target",
                side_effect=[
                    {"status": "COMPLETED", "reason": "OK"},
                    {"status": "NO_CHANGE", "reason": "DONE"},
                ],
            ) as service,
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_legacy_early_close()
        self.assertEqual(2, service.call_count)
        result = window.last_legacy_close_reconciliation_result
        self.assertEqual(1, result["completed"])
        self.assertEqual(1, result["no_change"])
        window.close()

    def test_blocked_failed_and_skipped_are_not_success(self) -> None:
        eligible = self.make_stock("323410", "카카오뱅크")
        failed_target = self.make_stock("086520", "에코프로")
        normal = self.make_stock("000660", "SK하이닉스", mode="NORMAL")
        _owner, window = self.make_window()
        window.selected_stock_dirs = mock.Mock(
            return_value=[
                (eligible, "323410", "카카오뱅크"),
                (failed_target, "086520", "에코프로"),
                (normal, "000660", "SK하이닉스"),
            ]
        )
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(
                review_window,
                "reconcile_legacy_early_close_no_target",
                side_effect=[
                    {"status": "BLOCKED_EVIDENCE", "reason": "STATE_STALE"},
                    {"status": "FAILED", "reason": "ATOMIC_WRITE_FAILED"},
                ],
            ),
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_legacy_early_close()
        result = window.last_legacy_close_reconciliation_result
        self.assertEqual(0, result["completed"])
        self.assertEqual(1, result["blocked"])
        self.assertEqual(1, result["failed"])
        self.assertEqual(1, result["skipped"])
        window.close()

    def test_state_change_during_confirmation_is_blocked_by_captured_sha(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        state_path = stock / "state.json"
        _owner, window = self.make_window()
        window.selected_stock_dirs = mock.Mock(return_value=[(stock, "323410", "카카오뱅크")])

        def mutate_then_accept(*_args, **_kwargs):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["review_checked_at"] = "CHANGED_DURING_DIALOG"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            return QMessageBox.Yes

        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(review_window, "ORDER_QUEUE_PATH", self.queue),
            mock.patch.object(QMessageBox, "question", side_effect=mutate_then_accept),
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_legacy_early_close()
        self.assertEqual(1, window.last_legacy_close_reconciliation_result["blocked"])
        self.assertEqual("", json.loads(state_path.read_text(encoding="utf-8"))["operation_notice"])
        window.close()

    def test_323410_temp_roundtrip_preserves_lifecycle_and_position(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        before = json.loads((stock / "state.json").read_text(encoding="utf-8"))
        _owner, window = self.make_window()
        window.selected_stock_dirs = mock.Mock(return_value=[(stock, "323410", "카카오뱅크")])
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(review_window, "ORDER_QUEUE_PATH", self.queue),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_legacy_early_close()
        after = json.loads((stock / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(1, window.last_legacy_close_reconciliation_result["completed"])
        self.assertEqual("EARLY_CLOSE_NO_TARGET", after["operation_notice"])
        for key in (
            "status", "review_required", "review_status", "review_reason",
            "trade_enabled", "operation_command_mode", "holding_qty", "avg_price",
            "routine_instance_id", "emergency_reason", "emergency_scope",
        ):
            self.assertEqual(before[key], after[key], key)
        window.close()

    def test_no_change_aggregate_and_no_lifecycle_side_effects(self) -> None:
        stock = self.make_stock("323410", "카카오뱅크")
        _owner, window = self.make_window()
        window.selected_stock_dirs = mock.Mock(return_value=[(stock, "323410", "카카오뱅크")])
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(
                review_window,
                "reconcile_legacy_early_close_no_target",
                return_value={"status": "NO_CHANGE", "reason": "ALREADY_TERMINAL"},
            ),
            mock.patch.object(window, "load_review_items"),
            mock.patch.object(window, "return_selected_items_to_auto_list") as review_return,
            mock.patch.object(KiwoomApi, "send_order") as send_order,
            mock.patch.object(KiwoomApi, "request_account_holdings_snapshot") as broker_tr,
        ):
            window.reconcile_selected_legacy_early_close()
        self.assertEqual(1, window.last_legacy_close_reconciliation_result["no_change"])
        review_return.assert_not_called()
        send_order.assert_not_called()
        broker_tr.assert_not_called()
        window.close()


if __name__ == "__main__":
    unittest.main()
