# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import unittest
from types import MethodType

from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import account_funds_foundation as funds
import gui_windows
import send_order_entrypoint


class _Label:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, value: str) -> None:
        self.value = str(value)


class _DeferredAdapter:
    def __init__(self) -> None:
        self.account_id = ""
        self.request_id = 0
        self.callback = None
        self.active_account = ""

    def set_active_account(self, account_id):
        self.active_account = str(account_id or "")

    def request_account_funds(self, account_id, *, request_id, callback):
        self.account_id = account_id
        self.request_id = request_id
        self.callback = callback
        return {"ok": True, "status": "REQUESTED"}


def _window(account_ref: list[str], *, connected: bool = True):
    window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
    window._account_funds_projection = funds.AccountFundsProjection()
    window.account_funds_adapter = None
    window.account_label = _Label()
    window.account_type_label = _Label()
    window.account_total_deposit_label = _Label()
    window.account_order_available_label = _Label()
    window.buy_time_status_label = _Label()
    window.selected_account_no = lambda: account_ref[0]
    window.kiwoom_api = type("Api", (), {"is_connected": lambda self: connected})()
    window._append_account_query_journal_event = lambda *args, **kwargs: {
        "appended": True
    }
    return window


class AccountFundsProjectionTests(unittest.TestCase):
    def test_initial_snapshot_is_unrequested_and_memory_only(self) -> None:
        projection = funds.AccountFundsProjection()

        self.assertEqual(funds.UNREQUESTED, projection.snapshot.status)
        self.assertNotIn("runtime", inspect.getsource(funds.AccountFundsProjection).lower())

    def test_selection_masks_account_and_does_not_create_values(self) -> None:
        projection = funds.AccountFundsProjection()

        snapshot = projection.select_account("1234567890", connected=True)

        self.assertEqual("1234567890", snapshot.account_id)
        self.assertEqual("1234-****", snapshot.account_display)
        self.assertEqual(funds.UNREQUESTED, snapshot.status)
        self.assertIsNone(snapshot.deposit)
        self.assertIsNone(snapshot.orderable_cash)

    def test_ready_result_normalizes_and_formats_money(self) -> None:
        projection = funds.AccountFundsProjection()
        projection.select_account("12345678", connected=True)
        request = projection.begin_request()

        self.assertIsNotNone(request)
        applied = projection.apply_result(
            request,
            {
                "ok": True,
                "deposit": "1,250,000",
                "orderable_cash": 900000,
                "account_type": "",
            },
        )

        self.assertTrue(applied)
        self.assertEqual(funds.READY, projection.snapshot.status)
        self.assertEqual(1_250_000, projection.snapshot.deposit)
        self.assertEqual(900_000, projection.snapshot.orderable_cash)
        self.assertEqual("1,250,000원", funds.format_money(projection.snapshot.deposit))

    def test_invalid_money_changes_result_to_failed(self) -> None:
        projection = funds.AccountFundsProjection()
        projection.select_account("12345678", connected=True)
        request = projection.begin_request()

        self.assertTrue(
            projection.apply_result(
                request,
                {"ok": True, "deposit": "not-money", "orderable_cash": "100"},
            )
        )
        self.assertEqual(funds.FAILED, projection.snapshot.status)
        self.assertIsNone(projection.snapshot.deposit)

    def test_account_change_marks_stale_and_clears_previous_values(self) -> None:
        projection = funds.AccountFundsProjection()
        projection.select_account("11111111", connected=True)
        request = projection.begin_request()
        projection.apply_result(
            request,
            {"ok": True, "deposit": 1000, "orderable_cash": 900},
        )

        snapshot = projection.select_account("22222222", connected=True)

        self.assertEqual(funds.STALE, snapshot.status)
        self.assertEqual("22222222", snapshot.account_id)
        self.assertIsNone(snapshot.deposit)
        self.assertIsNone(snapshot.orderable_cash)

    def test_late_response_for_previous_account_is_ignored(self) -> None:
        projection = funds.AccountFundsProjection()
        projection.select_account("11111111", connected=True)
        old_request = projection.begin_request()
        projection.select_account("22222222", connected=True)

        applied = projection.apply_result(
            old_request,
            {"ok": True, "deposit": 1000, "orderable_cash": 900},
        )

        self.assertFalse(applied)
        self.assertEqual("22222222", projection.snapshot.account_id)
        self.assertEqual(funds.STALE, projection.snapshot.status)

    def test_payload_for_different_account_is_ignored(self) -> None:
        projection = funds.AccountFundsProjection()
        projection.select_account("11111111", connected=True)
        request = projection.begin_request()

        applied = projection.apply_result(
            request,
            {
                "ok": True,
                "account_id": "22222222",
                "deposit": 1000,
                "orderable_cash": 900,
            },
        )

        self.assertFalse(applied)
        self.assertEqual(funds.LOADING, projection.snapshot.status)

    def test_disconnected_invalidates_active_request(self) -> None:
        projection = funds.AccountFundsProjection()
        projection.select_account("11111111", connected=True)
        request = projection.begin_request()

        snapshot = projection.select_account("", connected=False)

        self.assertEqual(funds.DISCONNECTED, snapshot.status)
        self.assertFalse(
            projection.apply_result(
                request,
                {"ok": True, "deposit": 1000, "orderable_cash": 900},
            )
        )


class AccountFundsMainWindowBindingTests(unittest.TestCase):
    @staticmethod
    def _capture_account_events(window):
        events: list[tuple[str, dict[str, object]]] = []

        def capture(_self, event_type, **kwargs):
            events.append((event_type, kwargs))
            return {"appended": True}

        window._append_account_query_journal_event = MethodType(capture, window)
        return events

    def test_selected_account_updates_masked_label_and_unrequested_ui(self) -> None:
        account = ["12345678"]
        window = _window(account)

        snapshot = gui_windows.MainWindow.sync_account_funds_selection(window)

        self.assertEqual(funds.UNREQUESTED, snapshot.status)
        self.assertEqual("계좌정보 :", window.account_label.value)
        self.assertEqual("계좌 구분: -", window.account_type_label.value)
        self.assertEqual("-", window.account_total_deposit_label.value)
        self.assertEqual("매수 가능 상태: 확인 전", window.buy_time_status_label.value)

    def test_mock_adapter_ready_binds_values_without_declaring_buy_allowed(self) -> None:
        account = ["12345678"]
        window = _window(account)
        adapter = _DeferredAdapter()
        window.account_funds_adapter = adapter
        gui_windows.MainWindow.sync_account_funds_selection(window)

        started = gui_windows.MainWindow.request_account_funds(window)
        self.assertTrue(started["ok"])
        self.assertEqual("조회 중", window.account_total_deposit_label.value)

        adapter.callback(
            {"ok": True, "deposit": "1,250,000", "orderable_cash": "800000"}
        )

        self.assertEqual("1,250,000", window.account_total_deposit_label.value)
        self.assertEqual("800,000", window.account_order_available_label.value)
        self.assertEqual("계좌 구분: 확인 필요", window.account_type_label.value)
        self.assertEqual("매수 가능 상태: 확인 필요", window.buy_time_status_label.value)

    def test_query_request_and_success_are_recorded_once(self) -> None:
        account = ["12345678"]
        window = _window(account)
        adapter = _DeferredAdapter()
        window.account_funds_adapter = adapter
        events = self._capture_account_events(window)
        gui_windows.MainWindow.sync_account_funds_selection(window)

        gui_windows.MainWindow.request_account_funds(window)
        adapter.callback({"ok": True, "deposit": 1000, "orderable_cash": 900})

        self.assertEqual(
            ["ACCOUNT_QUERY_REQUESTED", "ACCOUNT_QUERY_SUCCEEDED"],
            [event_type for event_type, _kwargs in events],
        )

    def test_authentication_failure_records_auth_required_once(self) -> None:
        account = ["12345678"]
        window = _window(account)
        events = self._capture_account_events(window)
        gui_windows.MainWindow.sync_account_funds_selection(window)

        class ImmediateFailureAdapter:
            def request_account_funds(self, account_id, *, request_id, callback):
                result = {
                    "ok": False,
                    "account_id": account_id,
                    "request_id": request_id,
                    "error": "account authentication required",
                    "error_kind": gui_windows.ACCOUNT_AUTHENTICATION_REQUIRED,
                    "error_code": "55",
                }
                callback(result)
                return result

        window.account_funds_adapter = ImmediateFailureAdapter()
        gui_windows.MainWindow.request_account_funds(window)

        self.assertEqual(
            ["ACCOUNT_QUERY_REQUESTED", "ACCOUNT_AUTH_REQUIRED"],
            [event_type for event_type, _kwargs in events],
        )

    def test_manual_requery_failure_records_one_result(self) -> None:
        account = ["12345678"]
        window = _window(account)
        adapter = _DeferredAdapter()
        window.account_funds_adapter = adapter
        events = self._capture_account_events(window)
        gui_windows.MainWindow.sync_account_funds_selection(window)

        gui_windows.MainWindow.request_account_funds(
            window,
            query_reason="MANUAL_REQUERY",
        )
        adapter.callback({"ok": False, "error": "server unavailable"})

        self.assertEqual(
            ["ACCOUNT_REQUERY_REQUESTED", "ACCOUNT_REQUERY_FAILED"],
            [event_type for event_type, _kwargs in events],
        )

    def test_failed_and_disconnected_ui_are_not_zero(self) -> None:
        account = ["12345678"]
        window = _window(account)
        adapter = _DeferredAdapter()
        window.account_funds_adapter = adapter
        gui_windows.MainWindow.sync_account_funds_selection(window)
        gui_windows.MainWindow.request_account_funds(window)

        adapter.callback({"ok": False, "error": "server unavailable"})

        self.assertEqual("조회 실패", window.account_total_deposit_label.value)
        self.assertEqual("조회 실패", window.account_order_available_label.value)
        self.assertNotIn("server unavailable", window.account_total_deposit_label.value)

        account[0] = ""
        gui_windows.MainWindow.sync_account_funds_selection(window, connected=False)
        self.assertEqual("미연결", window.account_total_deposit_label.value)
        self.assertEqual("미연결", window.account_order_available_label.value)
        self.assertEqual("매수 가능 상태: 미연결", window.buy_time_status_label.value)

    def test_old_callback_does_not_overwrite_new_account_ui(self) -> None:
        account = ["11111111"]
        window = _window(account)
        adapter = _DeferredAdapter()
        window.account_funds_adapter = adapter
        gui_windows.MainWindow.sync_account_funds_selection(window)
        gui_windows.MainWindow.request_account_funds(window)
        old_callback = adapter.callback

        account[0] = "22222222"
        gui_windows.MainWindow.sync_account_funds_selection(window)
        old_callback({"ok": True, "deposit": 1000, "orderable_cash": 900})

        self.assertEqual("계좌정보 :", window.account_label.value)
        self.assertEqual("-", window.account_total_deposit_label.value)
        self.assertEqual(funds.STALE, window._account_funds_projection.snapshot.status)

    def test_send_order_guard_does_not_depend_on_funds_snapshot(self) -> None:
        source = inspect.getsource(send_order_entrypoint._validate_guard)

        self.assertNotIn("account_funds", source)
        self.assertNotIn("deposit", source)
        self.assertNotIn("orderable_cash", source)

    def test_recovery_completion_requests_only_its_current_account(self) -> None:
        window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
        window.selected_account_no = lambda: "1234567890"
        calls: list[str] = []
        window.request_account_funds = lambda: calls.append("requested") or {"ok": True}
        matching = type("Identity", (), {"account_no": "1234567890"})()
        stale = type("Identity", (), {"account_no": "9999999999"})()

        matched = gui_windows.MainWindow._request_account_funds_after_recovery(window, matching)
        rejected = gui_windows.MainWindow._request_account_funds_after_recovery(window, stale)

        self.assertTrue(matched["ok"])
        self.assertEqual(["requested"], calls)
        self.assertEqual("STALE_RECOVERY_ACCOUNT", rejected["status"])

    def test_selection_notifies_adapter_of_current_account(self) -> None:
        account = ["1234567890"]
        window = _window(account)
        adapter = _DeferredAdapter()
        window.account_funds_adapter = adapter

        gui_windows.MainWindow.sync_account_funds_selection(window)

        self.assertEqual("1234567890", adapter.active_account)

    def test_actual_qt_labels_render_all_account_funds_states(self) -> None:
        app = QApplication.instance() or QApplication([])
        panel = QWidget()
        layout = QVBoxLayout(panel)
        window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
        window.account_label = QLabel()
        window.account_type_label = QLabel()
        window.account_total_deposit_label = QLabel()
        window.account_order_available_label = QLabel()
        window.buy_time_status_label = QLabel()
        for label in (
            window.account_label,
            window.account_type_label,
            window.account_total_deposit_label,
            window.account_order_available_label,
            window.buy_time_status_label,
        ):
            layout.addWidget(label)

        snapshots = (
            funds.AccountFundsSnapshot(
                account_display="1234-****", status=funds.READY,
                deposit=1_250_000, orderable_cash=920_000, account_type="실계좌",
            ),
            funds.AccountFundsSnapshot(
                account_display="1234-****", status=funds.READY,
                deposit=1_250_000, orderable_cash=920_000, account_type="모의투자",
            ),
            funds.AccountFundsSnapshot(
                account_display="1234-****", status=funds.READY,
                deposit=0, orderable_cash=0, account_type="실계좌",
            ),
            funds.AccountFundsSnapshot(account_display="1234-****", status=funds.FAILED),
            funds.AccountFundsSnapshot(account_display="5678-****", status=funds.STALE),
        )
        rendered_texts: list[tuple[str, str, str]] = []
        for snapshot in snapshots:
            gui_windows.MainWindow.render_account_funds_snapshot(window, snapshot)
            panel.adjustSize()
            pixmap = panel.grab()
            self.assertFalse(pixmap.isNull())
            rendered_texts.append(
                (
                    window.account_type_label.text(),
                    window.account_total_deposit_label.text(),
                    window.account_order_available_label.text(),
                )
            )
            app.processEvents()

        self.assertIn(("계좌 구분: 실계좌", "1,250,000", "920,000"), rendered_texts)
        self.assertIn(("계좌 구분: 모의투자", "1,250,000", "920,000"), rendered_texts)
        self.assertIn(("계좌 구분: 실계좌", "0", "0"), rendered_texts)
        self.assertIn(("계좌 구분: 확인 필요", "조회 실패", "조회 실패"), rendered_texts)
        self.assertIn(("계좌 구분: -", "-", "-"), rendered_texts)


if __name__ == "__main__":
    unittest.main()
