# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from event_journal_contract import EVENT_TYPE_LABELS, required_template_arguments
from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter, expected_category
from gui_event_record_window import EventRecordPrototypeWindow


NOW = datetime.fromisoformat("2026-08-16T18:00:00+09:00")


class EventJournalGuiUsabilityTest(unittest.TestCase):
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

    def _template_args(self, event_type: str) -> dict[str, object]:
        values = {
            "account_display": "8129****",
            "target": "자동매매",
            "stock_name": "삼성전자",
            "filled_qty": 3,
            "order_qty": 5,
            "routine": "지표추종매매 A",
        }
        return {key: values[key] for key in required_template_arguments(event_type)}

    def _append(self, event_type: str, **fields) -> dict[str, object]:
        occurred_at = f"2026-08-16T10:00:{self._second:02d}+09:00"
        self._second += 1
        severity = fields.pop("severity", "INFO")
        result = fields.pop("result", "COMPLETED")
        appended = self.writer.append_event(
            event_type=event_type,
            occurred_at=occurred_at,
            category=expected_category(event_type),
            severity=severity,
            result=result,
            source=fields.pop("source", "focused_gui_test"),
            template_args=self._template_args(event_type),
            **fields,
        )
        self.assertTrue(appended["appended"], appended)
        return appended["event"]

    def _window(self) -> EventRecordPrototypeWindow:
        return EventRecordPrototypeWindow(reader=self.reader, now_provider=lambda: NOW)

    def test_p1_p2_p3_and_existing_events_are_all_visible(self) -> None:
        event_types = (
            "OPERATOR_SYSTEM_DECISION",
            "OPERATOR_OPERATION_DECISION",
            "OPERATOR_SETTING_DECISION",
            "OPERATOR_ORDER_DECISION",
            "SETTING_CHANGED",
            "TRADING_TIME_CHANGED",
            "ATS_CHANGED",
            "ROUTINE_CHANGED",
            "ROUTINE_INSTANCE_CREATED",
            "ROUTINE_INSTANCE_DELETED",
            "PROCESSING_ERROR",
            "RUNTIME_WARNING",
            "INTEGRITY_WARNING",
            "PNL_CYCLE_BOUNDARY_CREATED",
            "LIQUIDATION_REQUESTED",
            "BUY_SIGNAL_DETECTED",
            "BROKER_ORDER_ACCEPTED",
            "FULL_FILL",
        )
        for event_type in event_types:
            fields: dict[str, object] = {}
            if event_type.startswith("OPERATOR_"):
                fields.update(
                    result="ACCEPTED",
                    details={
                        "interaction_type": "CONFIRM",
                        "prompt_title": "운영 확인",
                        "selected_option": "진행",
                    },
                )
            if event_type in {"SETTING_CHANGED", "TRADING_TIME_CHANGED", "ATS_CHANGED", "ROUTINE_CHANGED"}:
                fields["changes"] = [{"field_key": "total_budget", "before": 30_000_000, "after": 40_000_000}]
            if event_type in {"PROCESSING_ERROR", "RUNTIME_WARNING", "INTEGRITY_WARNING"}:
                fields.update(
                    severity="ERROR" if event_type == "PROCESSING_ERROR" else "WARNING",
                    result="FAILED" if event_type == "PROCESSING_ERROR" else "BLOCKED",
                    reason_code=f"{event_type}_REASON",
                )
            if event_type in {"BROKER_ORDER_ACCEPTED", "FULL_FILL"}:
                fields.update(stock_code="005930", stock_name="삼성전자")
            self._append(event_type, **fields)

        window = self._window()
        try:
            window.select_period("오늘")
            labels = {
                window.event_table.item(row, 4).text()
                for row in range(window.event_table.rowCount())
            }
            self.assertEqual({EVENT_TYPE_LABELS[item] for item in event_types}, labels)
        finally:
            window.close()

    def test_operator_and_changes_are_human_readable_without_raw_json(self) -> None:
        self._append(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED",
            stock_code="005930",
            stock_name="삼성전자",
            details={
                "interaction_type": "CONFIRM",
                "prompt_title": "조기마감 확인",
                "prompt_summary": "시장가로 진행하시겠습니까?",
                "offered_options": ["시장가", "취소"],
                "selected_option": "시장가 진행",
                "input_value": {"quantity": 3, "price": 71_000},
            },
        )
        self._append(
            "SETTING_CHANGED",
            changes=[
                {"field_key": "total_budget", "before": 30_000_000, "after": 40_000_000},
                {"field_key": "threshold_percent", "before": 80, "after": 70},
            ],
        )
        window = self._window()
        try:
            window.search_edit.setText("시장가 진행")
            self.assertEqual(1, window.event_table.rowCount())
            self.assertIn("조기마감 확인 — 시장가 진행", window.event_table.item(0, 6).text())
            window.event_table.selectRow(0)
            detail = window.detail_text.toPlainText()
            self.assertIn("[사용자 선택]", detail)
            self.assertIn("수량=3", detail)
            self.assertNotIn("offered_options", detail)
            self.assertNotIn("{", detail)

            window.search_edit.setText("설정 변경")
            self.assertEqual(1, window.event_table.rowCount())
            self.assertIn("전체예산: 30,000,000 → 40,000,000", window.event_table.item(0, 6).text())
            window.event_table.selectRow(0)
            detail = window.detail_text.toPlainText()
            self.assertIn("[변경 내용]", detail)
            self.assertIn("구간마감 비율: 80 → 70", detail)
            self.assertNotIn("\"field_key\"", detail)
        finally:
            window.close()

    def test_search_reason_routine_and_detail_correlations(self) -> None:
        self._append(
            "PROCESSING_ERROR",
            severity="ERROR",
            result="FAILED",
            reason_code="TARGET_COLLECTION_FAILED",
            routine="지표추종매매 A",
            stock_code="005930",
            stock_name="삼성전자",
            command_id="COMMAND-1",
            signal_id="SIGNAL-1",
            order_id="ORDER-1",
            execution_id="EXECUTION-1",
            broker_order_no="BROKER-1",
            details={
                "stage": "operation_start",
                "traceback": "Traceback (most recent call last): secret",
                "file_path": "C:\\secret\\runtime.json",
            },
        )
        window = self._window()
        try:
            for query in ("TARGET_COLLECTION_FAILED", "지표추종매매 A", "처리 오류"):
                window.search_edit.setText(query)
                self.assertEqual(1, window.event_table.rowCount())
            window.event_table.selectRow(0)
            self.assertEqual("TARGET_COLLECTION_FAILED", window.detail_labels["reason_code"].text())
            for key in ("command_id", "signal_id", "order_id", "execution_id", "broker_order_no"):
                self.assertTrue(window.correlation_rows[key][1].isVisibleTo(window))
            detail = window.detail_text.toPlainText()
            self.assertIn("처리 단계: operation_start", detail)
            self.assertNotIn("Traceback", detail)
            self.assertNotIn("C:\\secret", detail)
        finally:
            window.close()

    def test_manual_refresh_local_time_sort_and_malformed_records(self) -> None:
        self._append("APP_STARTED")
        month_path = self.journal_dir / "2026-08.jsonl"
        with month_path.open("a", encoding="utf-8") as handle:
            handle.write("not-json\n")
            handle.write(json.dumps({"schema_version": "invalid"}) + "\n")
        window = self._window()
        try:
            self.assertEqual(1, window.event_table.rowCount())
            self.writer.append_event(
                event_type="APP_STOPPED",
                occurred_at="2026-08-16T01:00:02+00:00",
                category=expected_category("APP_STOPPED"),
                severity="INFO",
                result="COMPLETED",
                source="focused_gui_test",
                template_args={},
            )
            self.assertEqual(1, window.event_table.rowCount())
            window.refresh_button.click()
            self.assertEqual(2, window.event_table.rowCount())
            expected_local = datetime.fromisoformat("2026-08-16T01:00:02+00:00").astimezone().strftime("%Y-%m-%d %H:%M:%S")
            self.assertEqual(expected_local, window.event_table.item(0, 0).text())
            first = window.event_table.item(0, 0).data(Qt.UserRole + 1)
            second = window.event_table.item(1, 0).data(Qt.UserRole + 1)
            self.assertGreater(first, second)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
