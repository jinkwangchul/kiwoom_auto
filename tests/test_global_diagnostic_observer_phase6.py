from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter
from gui_event_record_window import (
    EVENT_AUTO_REFRESH_INTERVAL_MS,
    EVENT_RECORD_HEADERS,
    EventRecordPrototypeWindow,
)


NOW = datetime.fromisoformat("2026-08-16T18:00:00+09:00")


class GlobalDiagnosticObserverPhase6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.temp.name) / "event_journal"
        self.writer = EventJournalWriter(self.journal_dir)
        self.reader = EventJournalReader(self.journal_dir)
        self._second = 0

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _append(self, **fields) -> dict[str, object]:
        occurred_at = f"2026-08-16T10:00:{self._second:02d}+09:00"
        self._second += 1
        result = self.writer.append_event(
            event_type="PROCESSING_ERROR",
            occurred_at=occurred_at,
            category="SYSTEM",
            severity="ERROR",
            result="FAILED",
            source=fields.pop("source", "phase6_test"),
            template_args={"target": fields.pop("target", "자동매매")},
            **fields,
        )
        self.assertTrue(result["appended"], result)
        return result["event"]

    def _window(self) -> EventRecordPrototypeWindow:
        return EventRecordPrototypeWindow(
            reader=self.reader,
            now_provider=lambda: NOW,
        )

    def test_operator_list_is_unchanged_and_empty_diagnostics_are_not_shown(self) -> None:
        self._append(reason_code="OPERATION_FAILED")
        window = self._window()
        try:
            headers = [
                window.event_table.horizontalHeaderItem(column).text()
                for column in range(window.event_table.columnCount())
            ]
            self.assertEqual(list(EVENT_RECORD_HEADERS), headers)
            self.assertEqual("이벤트", window.windowTitle())
            self.assertEqual("처리 오류", window.detail_labels["event"].text())
            self.assertEqual("OPERATION_FAILED", window.detail_labels["reason_code"].text())
            self.assertTrue(window.developer_diagnostic_button.isHidden())
            self.assertTrue(window.developer_diagnostic_group.isHidden())
        finally:
            window.close()

    def test_developer_diagnostics_are_collapsed_optional_and_sanitized(self) -> None:
        self._append(
            reason_code="CALLBACK_FAILED",
            component="kiwoom_callback",
            operation="receive_chejan",
            exception_type="RuntimeError",
            exception_message=(
                "password=hunter2 token=secret-token "
                "Traceback (most recent call last): C:\\Users\\private\\app.py"
            ),
            stack_fingerprint="stack-abc",
            build_version="build-123",
        )
        window = self._window()
        try:
            window.show()
            self.app.processEvents()
            self.assertTrue(window.developer_diagnostic_button.isVisibleTo(window))
            self.assertFalse(window.developer_diagnostic_group.isVisibleTo(window))
            window.developer_diagnostic_button.click()
            self.app.processEvents()
            self.assertTrue(window.developer_diagnostic_group.isVisibleTo(window))
            self.assertEqual(
                "kiwoom_callback",
                window.developer_diagnostic_rows["component"][1].text(),
            )
            message = window.developer_diagnostic_rows["exception_message"][1].text()
            self.assertNotIn("hunter2", message)
            self.assertNotIn("secret-token", message)
            self.assertNotIn("C:\\Users\\private", message)
            self.assertNotIn("Traceback (most recent call last)", message)
            for key in (
                "broker_payload",
                "broker_raw",
                "chejan_fid_map",
                "raw_payload",
                "token",
                "password",
            ):
                self.assertTrue(window._sensitive_key(key), key)
            self.assertTrue(
                window.developer_diagnostic_rows["build_version"][1].isVisibleTo(window)
            )
        finally:
            window.close()

    def test_search_supports_diagnostics_and_all_existing_correlation_ids(self) -> None:
        self._append(
            component="operation_host",
            operation="run_operation_cycle",
            correlation_id="CORRELATION-1",
            parent_event_id="PARENT-1",
            signal_id="SIGNAL-1",
            order_id="ORDER-1",
            execution_id="EXECUTION-1",
            broker_order_no="BROKER-1",
            command_id="COMMAND-1",
        )
        window = self._window()
        try:
            for query in (
                "operation_host",
                "run_operation_cycle",
                "CORRELATION-1",
                "PARENT-1",
                "SIGNAL-1",
                "ORDER-1",
                "EXECUTION-1",
                "BROKER-1",
                "COMMAND-1",
            ):
                window.search_edit.setText(query)
                self.assertEqual(1, window.event_table.rowCount(), query)
            window.search_edit.setText("PARENT-1")
            self.assertEqual(
                "PARENT-1",
                window.correlation_rows["parent_event_id"][1].text(),
            )
        finally:
            window.close()

    def test_visible_timer_refresh_preserves_filter_and_selected_event(self) -> None:
        older = self._append(
            reason_code="FIRST_FAILURE",
            component="operation_host",
        )
        window = self._window()
        try:
            window.show()
            self.app.processEvents()
            self.assertTrue(window._refresh_timer.isActive())
            self.assertEqual(EVENT_AUTO_REFRESH_INTERVAL_MS, window._refresh_timer.interval())
            window.search_edit.setText("operation_host")
            self.assertEqual(1, window.event_table.rowCount())
            self.assertEqual(older["event_id"], window._selected_event_id())

            self._append(
                reason_code="SECOND_FAILURE",
                component="operation_host",
            )
            window._auto_refresh()
            self.assertEqual("operation_host", window.search_edit.text())
            self.assertEqual(2, window.event_table.rowCount())
            self.assertEqual(older["event_id"], window._selected_event_id())

            window.hide()
            self.app.processEvents()
            self.assertFalse(window._refresh_timer.isActive())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
