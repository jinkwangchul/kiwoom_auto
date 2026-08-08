# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import sys
import tempfile
from pathlib import Path
import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QAbstractItemView

from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter, expected_category
import gui_event_record_window as event_ui


NOW = datetime.fromisoformat("2026-08-08T16:00:00+09:00")


class EventRecordProductionReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        journal_dir = Path(self.temp.name) / "event_journal"
        self.reader = EventJournalReader(journal_dir)
        self.writer = EventJournalWriter(journal_dir)
        self._append(self.writer, "APP_STARTED", "2026-08-08T08:55:58+09:00", result="COMPLETED")
        self._append(
            self.writer,
            "OPERATION_HOST_STARTED",
            "2026-08-08T08:55:59+09:00",
            result="SUCCESS",
            target_type="OPERATION_HOST",
            target_id="main_operation_host",
        )
        self._append(
            self.writer,
            "RECOVERY_COMPLETED",
            "2026-08-08T08:56:00+09:00",
            result="COMPLETED",
            target_type="RECOVERY_SESSION",
            target_id="RECOVERY-COMPLETED",
        )
        self._append(
            self.writer,
            "ORDER_QUEUED",
            "2026-08-08T10:00:00+09:00",
            result="COMPLETED",
            template_args={"stock_name": "삼성전자"},
            stock_code="005930",
            stock_name="삼성전자",
        )
        self._append(
            self.writer,
            "SEND_ORDER_REQUEST_ACCEPTED",
            "2026-08-08T10:00:01+09:00",
            severity="NOTICE",
            result="ACCEPTED",
            template_args={"stock_name": "카카오게임즈"},
            stock_code="293490",
            stock_name="카카오게임즈",
        )
        self._append(
            self.writer,
            "OPERATION_EXCLUDED",
            "2026-08-08T11:05:13+09:00",
            result="COMPLETED",
            template_args={"stock_name": "삼성전자"},
            target_type="STOCK",
            target_id="005930",
            target_name="삼성전자",
            stock_code="005930",
            stock_name="삼성전자",
            details={"reason": "operator request"},
        )
        self._append(
            self.writer,
            "RECOVERY_WARNING",
            "2026-08-01T09:00:00+09:00",
            severity="WARNING",
            result="COMPLETED",
            target_type="RECOVERY_SESSION",
            target_id="RECOVERY-1",
            reason_code="REVIEW_REQUIRED",
        )
        self._append(
            self.writer,
            "RECOVERY_FAILED",
            "2026-08-01T09:00:01+09:00",
            severity="ERROR",
            result="FAILED",
            target_type="RECOVERY_SESSION",
            target_id="RECOVERY-2",
            reason_code="RECOVERY_FAILED",
        )
        self.window = event_ui.EventRecordPrototypeWindow(
            reader=self.reader,
            now_provider=lambda: NOW,
        )

    @staticmethod
    def _append(writer, event_type, occurred_at, *, severity="INFO", template_args=None, **fields):
        result = writer.append_event(
            event_type=event_type,
            occurred_at=occurred_at,
            category=expected_category(event_type),
            severity=severity,
            template_args=template_args or {},
            app_session_id="APP-SESSION-TEST",
            source="test",
            **fields,
        )
        assert result["appended"], result

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def test_window_is_read_only_and_uses_production_columns(self) -> None:
        self.assertEqual("이벤트기록", self.window.windowTitle())
        headers = [
            self.window.event_table.horizontalHeaderItem(column).text()
            for column in range(self.window.event_table.columnCount())
        ]
        self.assertEqual(list(event_ui.EVENT_RECORD_HEADERS), headers)
        self.assertEqual(QAbstractItemView.NoEditTriggers, self.window.event_table.editTriggers())
        self.assertEqual(Qt.ScrollBarAlwaysOn, self.window.event_table.verticalScrollBarPolicy())

    def test_category_and_severity_controls_match_period_badge_height(self) -> None:
        period_height = self.window.period_buttons["오늘"].sizeHint().height()
        self.assertEqual(period_height, self.window.category_combo.height())
        self.assertEqual(period_height, self.window.severity_combo.height())

    def test_category_uses_function_areas_and_severity_keeps_warning_and_error(self) -> None:
        categories = [
            self.window.category_combo.itemData(index)
            for index in range(self.window.category_combo.count())
        ]
        severities = [
            self.window.severity_combo.itemData(index)
            for index in range(self.window.severity_combo.count())
        ]
        self.assertNotIn("WARNING", categories)
        self.assertNotIn("ERROR", categories)
        self.assertIn("WARNING", severities)
        self.assertIn("ERROR", severities)

    def test_system_and_severity_filters_compose_for_recovery_events(self) -> None:
        self.window.select_period("전체")
        self.window.category_combo.setCurrentIndex(self.window.category_combo.findData("SYSTEM"))
        self.window.severity_combo.setCurrentIndex(self.window.severity_combo.findData("WARNING"))
        self.assertEqual(1, self.window.event_table.rowCount())
        self.assertEqual("Recovery 경고", self.window.event_table.item(0, 4).text())
        self.window.severity_combo.setCurrentIndex(self.window.severity_combo.findData("ERROR"))
        self.assertEqual(1, self.window.event_table.rowCount())
        self.assertEqual("Recovery 실패", self.window.event_table.item(0, 4).text())

    def test_reader_filters_are_immediate_and_search_contract_is_shared(self) -> None:
        self.window.select_period("오늘")
        self.assertEqual(2, self.window.event_table.rowCount())
        self.window.category_combo.setCurrentText("설정")
        self.assertEqual(1, self.window.event_table.rowCount())
        self.window.category_combo.setCurrentText("전체")
        self.window.search_edit.setText("005930")
        self.assertEqual(1, self.window.event_table.rowCount())
        self.assertEqual("삼성전자 (005930)", self.window.event_table.item(0, 3).text())

    def test_default_hidden_events_remain_in_reader_but_not_in_view_or_search(self) -> None:
        contracts = (
            ("ORDER_QUEUED", "주문 실행 준비", "ORDER"),
            ("OPERATION_HOST_STARTED", "Operation Host가 시작", "SYSTEM"),
            ("RECOVERY_COMPLETED", "복구가 완료", "SYSTEM"),
            ("SEND_ORDER_REQUEST_ACCEPTED", "Broker에 요청", "ORDER"),
        )
        self.assertEqual(
            {event_type for event_type, _, _ in contracts},
            set(event_ui.DEFAULT_HIDDEN_EVENT_TYPES),
        )
        self.window.select_period("전체")
        for event_type, query, category in contracts:
            with self.subTest(event_type=event_type):
                reader_events = self.reader.read_events(query=query)["events"]
                self.assertEqual([event_type], [event["event_type"] for event in reader_events])
                self.window.category_combo.setCurrentIndex(
                    self.window.category_combo.findData(category)
                )
                self.window.search_edit.setText(query)
                self.assertEqual(0, self.window.event_table.rowCount())
                self.window.search_edit.clear()
        self.window.category_combo.setCurrentIndex(0)

    def test_normal_start_shows_app_only_while_recovery_anomalies_remain_visible(self) -> None:
        self.window.select_period("전체")
        self.window.category_combo.setCurrentIndex(self.window.category_combo.findData("SYSTEM"))
        self.window.severity_combo.setCurrentIndex(self.window.severity_combo.findData("INFO"))
        self.assertEqual(1, self.window.event_table.rowCount())
        self.assertEqual("프로그램 시작", self.window.event_table.item(0, 4).text())
        self.window.severity_combo.setCurrentIndex(self.window.severity_combo.findData("WARNING"))
        self.assertEqual("Recovery 경고", self.window.event_table.item(0, 4).text())
        self.window.severity_combo.setCurrentIndex(self.window.severity_combo.findData("ERROR"))
        self.assertEqual("Recovery 실패", self.window.event_table.item(0, 4).text())

    def test_normal_order_timeline_keeps_signal_broker_acceptance_and_fill(self) -> None:
        common = {
            "template_args": {"stock_name": "카카오게임즈"},
            "stock_code": "293490",
            "stock_name": "카카오게임즈",
        }
        self._append(
            self.writer,
            "BUY_SIGNAL_DETECTED",
            "2026-08-08T10:00:00+09:00",
            result="SUCCESS",
            **common,
        )
        self._append(
            self.writer,
            "BROKER_ORDER_ACCEPTED",
            "2026-08-08T10:00:02+09:00",
            severity="NOTICE",
            result="ACCEPTED",
            **common,
        )
        self._append(
            self.writer,
            "FULL_FILL",
            "2026-08-08T10:00:03+09:00",
            severity="NOTICE",
            result="COMPLETED",
            **common,
        )
        self.window.apply_filters()
        self.window.search_edit.setText("293490")
        self.assertEqual(3, self.window.event_table.rowCount())
        self.assertCountEqual(
            ["매수 신호 발생", "Broker 주문 접수", "전량체결"],
            [self.window.event_table.item(row, 4).text() for row in range(3)],
        )

    def test_send_order_and_broker_failures_remain_visible(self) -> None:
        contracts = (
            ("SEND_ORDER_REQUEST_REJECTED", "WARNING", "REJECTED", "주문 요청 실패"),
            ("SEND_ORDER_RESULT_UNCERTAIN", "WARNING", "UNCERTAIN", "주문 결과 불확실"),
            ("BROKER_ORDER_REJECTED", "WARNING", "REJECTED", "Broker 주문 거부"),
        )
        for index, (event_type, severity, result, _) in enumerate(contracts):
            self._append(
                self.writer,
                event_type,
                f"2026-08-08T10:01:0{index}+09:00",
                severity=severity,
                result=result,
                template_args={"stock_name": "카카오게임즈"},
                stock_code="293490",
                stock_name="카카오게임즈",
            )
        self.window.apply_filters()
        for _, _, _, label in contracts:
            with self.subTest(label=label):
                self.window.search_edit.setText(label)
                self.assertEqual(1, self.window.event_table.rowCount())
                self.assertEqual(label, self.window.event_table.item(0, 4).text())

    def test_stage6_scenario_display_count_drops_from_40_to_33(self) -> None:
        stage6_event_types = (
            "APP_STARTED", "OPERATION_HOST_STARTED", "RECOVERY_COMPLETED",
            "OPERATION_STARTED", "BUY_SIGNAL_DETECTED", "ORDER_QUEUED",
            "SEND_ORDER_REQUEST_ACCEPTED", "BROKER_ORDER_ACCEPTED", "FULL_FILL",
            "BUY_SIGNAL_DETECTED", "POLICY_BLOCKED",
            "BUY_SIGNAL_DETECTED", "EXECUTION_BLOCKED",
            "BUY_SIGNAL_DETECTED", "ORDER_QUEUED", "SEND_ORDER_REQUEST_ACCEPTED",
            "BROKER_ORDER_ACCEPTED", "PARTIAL_FILL", "PARTIAL_FILL", "FULL_FILL",
            "BUY_SIGNAL_DETECTED", "ORDER_QUEUED", "SEND_ORDER_REQUEST_ACCEPTED",
            "BROKER_ORDER_REJECTED",
            "BUY_SIGNAL_DETECTED", "ORDER_QUEUED", "SEND_ORDER_RESULT_UNCERTAIN",
            "AUTO_CLOSE_STARTED", "LIQUIDATION_REQUESTED", "SEND_ORDER_REQUEST_ACCEPTED",
            "BROKER_ORDER_ACCEPTED", "PARTIAL_FILL", "FULL_FILL", "LIQUIDATION_COMPLETED",
            "EARLY_CLOSE_STARTED", "LIQUIDATION_REQUESTED", "SEND_ORDER_REQUEST_ACCEPTED",
            "BROKER_ORDER_ACCEPTED", "FULL_FILL", "LIQUIDATION_COMPLETED",
            "EMERGENCY_STOPPED", "EMERGENCY_RELEASED",
            "OPERATION_EXCLUDED", "OPERATION_EXCLUSION_RELEASED",
        )
        self.assertEqual(44, len(stage6_event_types))
        self.assertEqual(40, sum(item != "ORDER_QUEUED" for item in stage6_event_types))
        self.assertEqual(
            33,
            sum(item not in event_ui.DEFAULT_HIDDEN_EVENT_TYPES for item in stage6_event_types),
        )

    def test_period_bounds_are_timezone_aware_and_all_reads_old_event(self) -> None:
        start, end = self.window._period_bounds()
        self.assertIsNotNone(start.utcoffset())
        self.assertIsNotNone(end.utcoffset())
        self.window.select_period("전체")
        self.assertEqual(4, self.window.event_table.rowCount())

    def test_detail_uses_actual_fields_and_hides_absent_correlations(self) -> None:
        self.window.select_period("오늘")
        self.window.search_edit.setText("005930")
        self.window.event_table.selectRow(0)
        self.app.processEvents()
        self.assertEqual("운영제외", self.window.detail_labels["event"].text())
        self.assertIn("operator request", self.window.detail_text.toPlainText())
        self.assertTrue(self.window.correlation_rows["stock_code"][1].isVisibleTo(self.window))
        self.assertFalse(self.window.correlation_rows["order_id"][1].isVisibleTo(self.window))

    def test_empty_journal_is_a_normal_empty_view(self) -> None:
        empty = event_ui.EventRecordPrototypeWindow(
            reader=EventJournalReader(Path(self.temp.name) / "empty"),
            now_provider=lambda: NOW,
        )
        try:
            self.assertEqual(0, empty.event_table.rowCount())
            self.assertEqual("표시 0건", empty.result_count_label.text())
        finally:
            empty.close()

    def test_preview_source_and_fixed_rows_are_removed(self) -> None:
        self.assertFalse(hasattr(event_ui, "EVENT_RECORD_PREVIEW_SOURCE"))
        self.assertFalse(hasattr(event_ui, "EVENT_RECORD_PREVIEW_ROWS"))


if __name__ == "__main__":
    unittest.main()
