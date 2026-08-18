# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
import inspect
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

import gui_review_required_window as review_window
import gui_windows
from kiwoom_api import KiwoomApi
from production_recovery_contract import (
    ACCOUNT_COMPLETED,
    BrokerAccountSnapshot,
    RecoverySessionIdentity,
    recovery_request_id,
)


def _identity(*, trading_day: str | None = None) -> RecoverySessionIdentity:
    return RecoverySessionIdentity(
        recovery_session_id="RECOVERY-B0",
        login_session_id="LOGIN-B0",
        account_no="81290000",
        trading_day=trading_day or date.today().isoformat(),
        requested_at=f"{trading_day or date.today().isoformat()}T09:00:00+09:00",
    )


def _snapshot(identity: RecoverySessionIdentity, *, complete: bool = True) -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_no=identity.account_no,
        trading_day=identity.trading_day,
        requested_at=identity.requested_at,
        completed_at=f"{identity.trading_day}T09:00:03+09:00",
        request_id=recovery_request_id(identity, "ACCOUNT"),
        recovery_session_id=identity.recovery_session_id,
        is_complete=complete,
        holdings=(),
        open_orders=(),
        source="KIWOOM_OPENAPI",
        errors=() if complete else ("PARTIAL",),
    )


class RecoverySnapshotHandoffTest(unittest.TestCase):
    def owner(self) -> SimpleNamespace:
        return SimpleNamespace(
            _latest_completed_recovery_snapshot=None,
            _latest_completed_recovery_identity=None,
        )

    def test_completed_recovery_keeps_exact_immutable_reference(self) -> None:
        identity = _identity()
        snapshot = _snapshot(identity)
        owner = self.owner()
        context = SimpleNamespace(identity=identity, account_status=ACCOUNT_COMPLETED)
        with mock.patch.object(
            gui_windows.production_recovery_registry, "snapshot", return_value=context
        ):
            published = gui_windows.MainWindow._publish_completed_recovery_handoff(
                owner, identity, snapshot
            )
        self.assertTrue(published)
        self.assertIs(identity, owner._latest_completed_recovery_identity)
        self.assertIs(snapshot, owner._latest_completed_recovery_snapshot)

    def test_partial_and_failed_recovery_have_no_handoff(self) -> None:
        identity = _identity()
        owner = self.owner()
        owner._latest_completed_recovery_snapshot = _snapshot(identity)
        owner._latest_completed_recovery_identity = identity
        context = SimpleNamespace(identity=identity, account_status=ACCOUNT_COMPLETED)
        with mock.patch.object(
            gui_windows.production_recovery_registry, "snapshot", return_value=context
        ):
            published = gui_windows.MainWindow._publish_completed_recovery_handoff(
                owner, identity, _snapshot(identity, complete=False)
            )
        self.assertFalse(published)
        self.assertIsNone(owner._latest_completed_recovery_snapshot)
        self.assertIsNone(owner._latest_completed_recovery_identity)

        owner._latest_completed_recovery_snapshot = _snapshot(identity)
        owner._latest_completed_recovery_identity = identity
        failure_owner = SimpleNamespace(
            _latest_completed_recovery_snapshot=owner._latest_completed_recovery_snapshot,
            _latest_completed_recovery_identity=identity,
            _stop_production_recovery_timers=mock.Mock(),
            _record_production_recovery_review=mock.Mock(),
            _production_recovery_status_result=mock.Mock(),
        )
        with (
            mock.patch.object(gui_windows.production_recovery_registry, "fail_account"),
            mock.patch.object(gui_windows, "append_owner_event_once"),
        ):
            gui_windows.MainWindow._fail_production_recovery(
                failure_owner, identity, "TEST_FAILURE"
            )
        self.assertIsNone(failure_owner._latest_completed_recovery_snapshot)

    def test_getter_fail_closes_wrong_day_and_preserves_no_mutation(self) -> None:
        identity = _identity(trading_day="2000-01-01")
        snapshot = _snapshot(identity)
        owner = SimpleNamespace(
            _latest_completed_recovery_snapshot=snapshot,
            _latest_completed_recovery_identity=identity,
            kiwoom_api=SimpleNamespace(login_session_id=lambda: identity.login_session_id),
            selected_account_no=lambda: identity.account_no,
        )
        context = SimpleNamespace(identity=identity, account_status=ACCOUNT_COMPLETED)
        with mock.patch.object(
            gui_windows.production_recovery_registry, "snapshot", return_value=context
        ):
            handoff = gui_windows.MainWindow.latest_completed_recovery_handoff(owner)
        self.assertIsNone(handoff)
        self.assertIs(snapshot, owner._latest_completed_recovery_snapshot)

    def test_lifecycle_invalidation_points_are_connected(self) -> None:
        for method_name in (
            "start_production_recovery",
            "_fail_production_recovery",
            "on_kiwoom_login_state_changed",
            "on_kiwoom_account_changed",
            "closeEvent",
        ):
            method_source = inspect.getsource(getattr(gui_windows.MainWindow, method_name))
            self.assertIn("_clear_completed_recovery_handoff", method_source, method_name)


class _Owner(QWidget):
    def __init__(self, handoff: dict[str, object] | None) -> None:
        super().__init__()
        self._handoff = handoff

    def latest_completed_recovery_handoff(self) -> dict[str, object] | None:
        return self._handoff


class ReviewPositionReconciliationActionTest(unittest.TestCase):
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
        self.identity = _identity()
        self.snapshot = _snapshot(self.identity)
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

    def make_stock(self, code: str, name: str, *, qty: int = 1) -> Path:
        stock_dir = self.stocks / f"{code}_{name}"
        stock_dir.mkdir()
        state = {
            "status": "EMERGENCY_STOPPED",
            "holding_qty": qty,
            "avg_price": 1000 if qty else 0,
            "review_required": True,
            "review_status": "PENDING",
            "review_reason": "TEST",
            "review_location": "TEST",
            "review_entered_at": "2026-08-17 09:00:00",
            "trade_enabled": False,
            "emergency_scope": "SELECTED",
        }
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        return stock_dir

    def make_window(self, targets: list[tuple[Path, str, str]], handoff=None):
        owner = _Owner(self.handoff if handoff is None else handoff)
        with mock.patch.object(
            review_window.GlobalReviewRequiredWindow,
            "_central_review_rows",
            return_value=[],
        ):
            window = review_window.GlobalReviewRequiredWindow(owner)
        window.selected_stock_dirs = mock.Mock(return_value=targets)
        return owner, window

    def test_no_snapshot_or_selection_blocks_without_service_call(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        _owner, window = self.make_window([(stock, "000660", "SK하이닉스")], handoff={})
        with mock.patch.object(review_window, "reconcile_review_stock_position") as service:
            window.reconcile_selected_position_information()
        service.assert_not_called()
        self.assertEqual(0, window.last_position_reconciliation_result["service_calls"])
        window.close()

    def test_no_selection_keeps_action_disabled(self) -> None:
        owner, window = self.make_window([])
        self.assertFalse(hasattr(window, "btn_position_reconcile"))
        self.assertIsNotNone(owner)
        window.close()

    def test_completed_handoff_prepares_position_without_operator_button(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        state_path = stock / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update({"status": "REVIEW_REQUIRED", "emergency_scope": ""})
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        _owner, window = self.make_window([])
        row = {"stock_dir": stock, "code": "000660"}
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(review_window, "ORDER_QUEUE_PATH", self.queue),
            mock.patch.object(window, "_central_review_rows", return_value=[row]),
            mock.patch.object(
                review_window,
                "reconcile_review_stock_position",
                return_value={"status": "APPLIED", "reason": "POSITION_RECONCILED"},
            ) as service,
        ):
            window._prepare_safe_review_reconciliation()
        service.assert_called_once()
        self.assertEqual(1, window.last_position_reconciliation_result["service_calls"])
        self.assertFalse(hasattr(window, "btn_position_reconcile"))
        window.close()

    def test_selected_review_provenance_is_auto_reconciliation_eligible(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        _owner, window = self.make_window([])
        row = {"stock_dir": stock, "code": "000660"}
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(window, "_central_review_rows", return_value=[row]),
            mock.patch.object(review_window, "reconcile_review_stock_position") as service,
        ):
            window._prepare_safe_review_reconciliation()
        service.assert_called_once()
        window.close()

    def test_global_emergency_review_is_not_auto_reconciled(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        state_path = stock / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["emergency_scope"] = "GLOBAL"
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        _owner, window = self.make_window([])
        row = {"stock_dir": stock, "code": "000660"}
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(window, "_central_review_rows", return_value=[row]),
            mock.patch.object(review_window, "reconcile_review_stock_position") as service,
        ):
            window._prepare_safe_review_reconciliation()
        service.assert_not_called()
        window.close()

    def test_confirmation_cancel_calls_service_zero_times(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        _owner, window = self.make_window([(stock, "000660", "SK하이닉스")])
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No),
            mock.patch.object(review_window, "reconcile_review_stock_position") as service,
        ):
            window.reconcile_selected_position_information()
        service.assert_not_called()
        self.assertEqual("CANCELED", window.last_position_reconciliation_result["status"])
        window.close()

    def test_two_review_targets_call_service_once_each_and_aggregate(self) -> None:
        first = self.make_stock("000660", "SK하이닉스")
        second = self.make_stock("323410", "카카오뱅크", qty=0)
        targets = [(first, "000660", "SK하이닉스"), (second, "323410", "카카오뱅크")]
        _owner, window = self.make_window(targets)
        side_effects = [
            {"status": "APPLIED", "reason": "POSITION_RECONCILED"},
            {"status": "NO_CHANGE", "reason": "POSITION_ALREADY_MATCHED"},
        ]
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(review_window, "ORDER_QUEUE_PATH", self.queue),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(
                review_window, "reconcile_review_stock_position", side_effect=side_effects
            ) as service,
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_position_information()
        self.assertEqual(2, service.call_count)
        self.assertEqual(1, window.last_position_reconciliation_result["applied"])
        self.assertEqual(1, window.last_position_reconciliation_result["no_change"])
        self.assertEqual(0, window.last_position_reconciliation_result["blocked"])
        window.close()

    def test_blocked_and_failed_are_not_counted_as_success(self) -> None:
        first = self.make_stock("000660", "SK하이닉스")
        second = self.make_stock("323410", "카카오뱅크", qty=0)
        _owner, window = self.make_window(
            [(first, "000660", "SK하이닉스"), (second, "323410", "카카오뱅크")]
        )
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(
                review_window,
                "reconcile_review_stock_position",
                side_effect=[
                    {"status": "BLOCKED_EVIDENCE", "reason": "STATE_STALE"},
                    {"status": "FAILED", "reason": "ATOMIC_WRITE_FAILED"},
                ],
            ),
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_position_information()
        self.assertEqual(0, window.last_position_reconciliation_result["applied"])
        self.assertEqual(0, window.last_position_reconciliation_result["no_change"])
        self.assertEqual(1, window.last_position_reconciliation_result["blocked"])
        self.assertEqual(1, window.last_position_reconciliation_result["failed"])
        window.close()

    def test_handoff_change_during_confirmation_blocks_entire_batch(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        owner, window = self.make_window([(stock, "000660", "SK하이닉스")])

        def clear_then_accept(*_args, **_kwargs):
            owner._handoff = None
            return QMessageBox.Yes

        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", side_effect=clear_then_accept),
            mock.patch.object(review_window, "reconcile_review_stock_position") as service,
        ):
            window.reconcile_selected_position_information()
        service.assert_not_called()
        self.assertEqual(0, window.last_position_reconciliation_result["service_calls"])
        self.assertEqual(
            "RECOVERY_HANDOFF_CHANGED_DURING_CONFIRMATION",
            window.last_position_reconciliation_result["reason"],
        )
        window.close()

    def test_state_change_during_confirmation_is_blocked_by_captured_sha(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        state_path = stock / "state.json"
        _owner, window = self.make_window([(stock, "000660", "SK하이닉스")])

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
            window.reconcile_selected_position_information()
        self.assertEqual(1, window.last_position_reconciliation_result["service_calls"])
        self.assertEqual(1, window.last_position_reconciliation_result["blocked"])
        self.assertEqual(1, json.loads(state_path.read_text(encoding="utf-8"))["holding_qty"])
        window.close()

    def test_real_apply_changes_only_position_and_preserves_review(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        before = json.loads((stock / "state.json").read_text(encoding="utf-8"))
        _owner, window = self.make_window([(stock, "000660", "SK하이닉스")])
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(review_window, "ORDER_QUEUE_PATH", self.queue),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_position_information()
        after = json.loads((stock / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(1, window.last_position_reconciliation_result["applied"])
        self.assertEqual(0, after["holding_qty"])
        self.assertEqual(0, after["avg_price"])
        for key in ("status", "review_required", "review_status", "review_reason", "emergency_scope"):
            self.assertEqual(before[key], after[key], key)
        window.close()

    def test_action_never_calls_sendorder_or_broker_tr(self) -> None:
        stock = self.make_stock("000660", "SK하이닉스")
        _owner, window = self.make_window([(stock, "000660", "SK하이닉스")])
        with (
            mock.patch.object(review_window, "PROJECT_ROOT", self.root),
            mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
            mock.patch.object(
                review_window,
                "reconcile_review_stock_position",
                return_value={"status": "NO_CHANGE", "reason": "TEST"},
            ),
            mock.patch.object(KiwoomApi, "send_order") as send_order,
            mock.patch.object(
                KiwoomApi, "request_account_holdings_snapshot"
            ) as broker_tr,
            mock.patch.object(window, "load_review_items"),
        ):
            window.reconcile_selected_position_information()
        send_order.assert_not_called()
        broker_tr.assert_not_called()
        window.close()


if __name__ == "__main__":
    unittest.main()
