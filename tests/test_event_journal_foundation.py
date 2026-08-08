from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from event_journal_contract import (
    CATEGORIES,
    EVENT_TYPE_LABELS,
    SCHEMA_VERSION,
    new_app_session_id,
    render_summary,
)
from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter, expected_category


class EventJournalFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "event_journal"
        self.writer = EventJournalWriter(self.root)
        self.reader = EventJournalReader(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _append(
        self,
        *,
        event_type: str = "APP_STARTED",
        occurred_at: str = "2026-08-08T09:00:00.000+09:00",
        severity: str = "INFO",
        event_id: str | None = None,
        template_args: dict[str, object] | None = None,
        **fields: object,
    ) -> dict[str, object]:
        return self.writer.append_event(
            event_type=event_type,
            occurred_at=occurred_at,
            category=expected_category(event_type),
            severity=severity,
            event_id=event_id,
            template_args=template_args,
            **fields,
        )

    def test_01_normal_event_append(self) -> None:
        result = self._append(event_id="event-1")
        self.assertTrue(result["appended"])
        self.assertEqual(SCHEMA_VERSION, result["event"]["schema_version"])

    def test_02_utf8_korean_summary_is_preserved(self) -> None:
        result = self._append(
            event_type="OPERATION_EXCLUDED",
            event_id="event-2",
            template_args={"stock_name": "삼성전자"},
            stock_name="삼성전자",
        )
        path = Path(result["path"])
        self.assertIn("삼성전자", path.read_text(encoding="utf-8"))

    def test_03_timezone_aware_occurred_at_is_accepted(self) -> None:
        self.assertTrue(self._append(event_id="event-3")["appended"])

    def test_04_timezone_naive_occurred_at_is_rejected(self) -> None:
        result = self._append(event_id="event-4", occurred_at="2026-08-08T09:00:00")
        self.assertTrue(result["invalid"])

    def test_05_events_are_split_into_monthly_files(self) -> None:
        self._append(event_id="july", occurred_at="2026-07-31T23:59:59+09:00")
        self._append(event_id="august", occurred_at="2026-08-01T00:00:00+09:00")
        self.assertTrue((self.root / "2026-07.jsonl").exists())
        self.assertTrue((self.root / "2026-08.jsonl").exists())

    def test_06_duplicate_event_id_is_not_appended(self) -> None:
        self.assertTrue(self._append(event_id="duplicate")["appended"])
        result = self._append(event_id="duplicate")
        self.assertTrue(result["duplicate"])
        self.assertEqual(1, len((self.root / "2026-08.jsonl").read_text(encoding="utf-8").splitlines()))

    def test_07_same_content_with_different_ids_is_appended(self) -> None:
        self._append(event_id="same-1")
        self._append(event_id="same-2")
        self.assertEqual(2, len(self.reader.read_events()["events"]))

    def test_08_invalid_category_is_rejected(self) -> None:
        result = self.writer.append_event(
            event_type="APP_STARTED",
            occurred_at="2026-08-08T09:00:00+09:00",
            category="INVALID",
            severity="INFO",
        )
        self.assertTrue(result["invalid"])

    def test_08b_new_writer_rejects_legacy_warning_and_error_categories(self) -> None:
        warning = self.writer.append_event(
            event_type="RECOVERY_WARNING",
            occurred_at="2026-08-08T09:00:00+09:00",
            category="WARNING",
            severity="WARNING",
        )
        error = self.writer.append_event(
            event_type="RECOVERY_FAILED",
            occurred_at="2026-08-08T09:00:01+09:00",
            category="ERROR",
            severity="ERROR",
        )
        self.assertTrue(warning["invalid"])
        self.assertTrue(error["invalid"])

    def test_08c_new_categories_and_recovery_mapping_use_system(self) -> None:
        self.assertEqual(
            {"SYSTEM", "OPERATION", "SETTING", "SIGNAL", "ORDER", "FILL"},
            set(CATEGORIES),
        )
        completed = self._append(event_type="RECOVERY_COMPLETED", severity="INFO")
        warning = self._append(event_type="RECOVERY_WARNING", severity="WARNING")
        failed = self._append(event_type="RECOVERY_FAILED", severity="ERROR")
        self.assertTrue(all(item["appended"] for item in (completed, warning, failed)))
        self.assertEqual(["SYSTEM", "SYSTEM", "SYSTEM"], [
            item["event"]["category"] for item in (completed, warning, failed)
        ])

    def test_08d_reader_normalizes_legacy_warning_and_error_without_invalid_rows(self) -> None:
        self.root.mkdir(parents=True)
        records = [
            {
                "schema_version": SCHEMA_VERSION,
                "event_id": "legacy-warning",
                "occurred_at": "2026-08-08T09:00:00+09:00",
                "category": "WARNING",
                "severity": "WARNING",
                "event_type": "RECOVERY_WARNING",
                "summary": "legacy warning",
            },
            {
                "schema_version": SCHEMA_VERSION,
                "event_id": "legacy-error",
                "occurred_at": "2026-08-08T09:00:01+09:00",
                "category": "ERROR",
                "severity": "ERROR",
                "event_type": "RECOVERY_FAILED",
                "summary": "legacy error",
            },
        ]
        (self.root / "2026-08.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        result = self.reader.read_events(category="SYSTEM", descending=False)
        self.assertEqual(0, result["invalid_count"])
        self.assertEqual(["SYSTEM", "SYSTEM"], [event["category"] for event in result["events"]])
        self.assertEqual(
            ["RECOVERY_WARNING", "RECOVERY_FAILED"],
            [event["event_type"] for event in result["events"]],
        )
        self.assertEqual(1, len(self.reader.read_events(category="SYSTEM", severity="WARNING")["events"]))
        self.assertEqual(1, len(self.reader.read_events(category="SYSTEM", severity="ERROR")["events"]))

    def test_09_invalid_severity_is_rejected(self) -> None:
        result = self._append(event_id="bad-severity", severity="CRITICAL")
        self.assertTrue(result["invalid"])

    def test_10_invalid_result_is_rejected(self) -> None:
        result = self._append(event_id="bad-result", result="DONE")
        self.assertTrue(result["invalid"])

    def test_11_missing_required_field_is_rejected(self) -> None:
        result = self.writer.append_event(
            event_type="APP_STARTED",
            category="SYSTEM",
            severity="INFO",
            occurred_at=None,
        )
        self.assertTrue(result["invalid"])

    def test_12_template_renders_normally(self) -> None:
        result = render_summary("OPERATION_EXCLUDED", {"stock_name": "삼성전자"})
        self.assertTrue(result["rendered"])
        self.assertIn("삼성전자", result["summary"])

    def test_13_missing_template_argument_is_rejected(self) -> None:
        result = self._append(event_type="PARTIAL_FILL", event_id="missing-template")
        self.assertTrue(result["invalid"])

    def test_13b_partial_fill_summary_uses_cumulative_progress(self) -> None:
        with_total = render_summary(
            "PARTIAL_FILL",
            {"stock_name": "삼성전자", "filled_qty": 20, "order_qty": 100},
        )
        without_total = render_summary(
            "PARTIAL_FILL",
            {"stock_name": "삼성전자", "filled_qty": 20},
        )
        self.assertEqual("삼성전자 주문이 누적 20/100주 체결되었습니다.", with_total["summary"])
        self.assertEqual("삼성전자 주문이 누적 20주 체결되었습니다.", without_total["summary"])

    def test_14_optional_identities_are_preserved(self) -> None:
        result = self._append(
            event_id="identity",
            signal_id="signal-1",
            order_id="order-1",
            execution_id="execution-1",
            broker_order_no="broker-1",
            command_id="command-1",
        )
        event = result["event"]
        self.assertEqual("order-1", event["order_id"])
        self.assertEqual("execution-1", event["execution_id"])

    def test_15_app_session_id_is_preserved(self) -> None:
        session_id = new_app_session_id()
        result = self._append(event_id="session", app_session_id=session_id)
        self.assertEqual(session_id, result["event"]["app_session_id"])

    def test_16_changes_array_is_preserved(self) -> None:
        changes = [{"field_key": "start_time", "before": "09:00", "after": "09:10"}]
        result = self._append(
            event_type="SETTING_CHANGED",
            event_id="changes",
            template_args={"target": "환경설정"},
            changes=changes,
        )
        self.assertEqual(changes, result["event"]["changes"])

    def test_17_raw_account_number_is_rejected(self) -> None:
        result = self._append(
            event_type="ACCOUNT_CHANGED",
            event_id="raw-account",
            template_args={"account_display": "1234567890"},
            target_type="ACCOUNT",
            target_name="1234567890",
        )
        self.assertTrue(result["invalid"])

    def test_18_masked_account_display_is_allowed(self) -> None:
        result = self._append(
            event_type="ACCOUNT_CHANGED",
            event_id="masked-account",
            template_args={"account_display": "1234-****-5678"},
            target_type="ACCOUNT",
            target_name="1234-****-5678",
        )
        self.assertTrue(result["appended"])

    def test_18b_raw_account_in_changes_is_rejected(self) -> None:
        result = self._append(
            event_type="SETTING_CHANGED",
            event_id="raw-account-change",
            template_args={"target": "환경설정"},
            changes=[
                {
                    "field_key": "account_no",
                    "before": "1234567890",
                    "after": "9876543210",
                }
            ],
        )
        self.assertTrue(result["invalid"])

    def test_19_concurrent_appends_remain_one_json_object_per_line(self) -> None:
        def append(index: int) -> dict[str, object]:
            return self._append(event_id=f"thread-{index}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(append, range(40)))
        self.assertTrue(all(result["appended"] for result in results))
        lines = (self.root / "2026-08.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(40, len(lines))
        self.assertTrue(all(isinstance(json.loads(line), dict) for line in lines))

    def test_20_reader_filters_by_period(self) -> None:
        self._append(event_id="inside", occurred_at="2026-08-08T09:00:00+09:00")
        self._append(event_id="outside", occurred_at="2026-08-01T09:00:00+09:00")
        result = self.reader.read_events(
            start_at="2026-08-07T00:00:00+09:00",
            end_at="2026-08-08T23:59:59+09:00",
        )
        self.assertEqual(["inside"], [event["event_id"] for event in result["events"]])

    def test_21_reader_handles_month_boundary(self) -> None:
        self._append(event_id="july", occurred_at="2026-07-31T23:00:00+09:00")
        self._append(event_id="august", occurred_at="2026-08-01T01:00:00+09:00")
        result = self.reader.read_events(
            start_at="2026-07-31T00:00:00+09:00",
            end_at="2026-08-01T23:59:59+09:00",
        )
        self.assertEqual(2, len(result["files_read"]))
        self.assertEqual(2, len(result["events"]))

    def test_22_reader_filters_category(self) -> None:
        self._append(event_id="system")
        self._append(
            event_type="OPERATION_EXCLUDED",
            event_id="setting",
            template_args={"stock_name": "삼성전자"},
        )
        result = self.reader.read_events(category="SETTING")
        self.assertEqual(["setting"], [event["event_id"] for event in result["events"]])

    def test_23_reader_filters_severity(self) -> None:
        self._append(event_id="info")
        self._append(event_id="warning", severity="WARNING")
        result = self.reader.read_events(severity="WARNING")
        self.assertEqual(["warning"], [event["event_id"] for event in result["events"]])

    def test_24_search_matches_target_display(self) -> None:
        self._append(event_id="target", target_name="전체 운영")
        self.assertEqual(1, len(self.reader.read_events(query="전체 운영")["events"]))

    def test_25_search_matches_event_label(self) -> None:
        self._append(event_id="label")
        label = EVENT_TYPE_LABELS["APP_STARTED"]
        self.assertEqual(1, len(self.reader.read_events(query=label)["events"]))

    def test_26_search_matches_summary(self) -> None:
        self._append(event_id="summary")
        self.assertEqual(1, len(self.reader.read_events(query="자동매매 프로그램")["events"]))

    def test_27_search_matches_stock_code(self) -> None:
        self._append(event_id="stock-code", stock_code="005930", stock_name="삼성전자")
        self.assertEqual(1, len(self.reader.read_events(query="005930")["events"]))

    def test_28_reader_sorts_ascending(self) -> None:
        self._append(event_id="later", occurred_at="2026-08-08T10:00:00+09:00")
        self._append(event_id="earlier", occurred_at="2026-08-08T09:00:00+09:00")
        result = self.reader.read_events(descending=False)
        self.assertEqual(["earlier", "later"], [event["event_id"] for event in result["events"]])

    def test_29_reader_sorts_descending(self) -> None:
        self._append(event_id="later", occurred_at="2026-08-08T10:00:00+09:00")
        self._append(event_id="earlier", occurred_at="2026-08-08T09:00:00+09:00")
        result = self.reader.read_events(descending=True)
        self.assertEqual(["later", "earlier"], [event["event_id"] for event in result["events"]])

    def test_30_reader_skips_malformed_json_line(self) -> None:
        self._append(event_id="valid")
        with (self.root / "2026-08.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{broken json\n")
        result = self.reader.read_events()
        self.assertEqual(1, result["malformed_count"])
        self.assertEqual(1, len(result["events"]))

    def test_31_reader_skips_schema_invalid_line(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "2026-08.jsonl").write_text(
            json.dumps({"schema_version": SCHEMA_VERSION, "event_id": "invalid"}) + "\n",
            encoding="utf-8",
        )
        result = self.reader.read_events()
        self.assertEqual(1, result["invalid_count"])
        self.assertEqual([], result["events"])

    def test_32_missing_month_file_is_an_empty_success(self) -> None:
        result = self.reader.read_events(
            start_at="2025-01-01T00:00:00+09:00",
            end_at="2025-01-31T23:59:59+09:00",
        )
        self.assertEqual([], result["events"])
        self.assertEqual([], result["errors"])

    def test_33_reader_reports_file_open_failure(self) -> None:
        self._append(event_id="read-failure")
        with patch("pathlib.Path.open", side_effect=OSError("read denied")):
            result = self.reader.read_events()
        self.assertEqual(1, len(result["errors"]))
        self.assertEqual([], result["events"])

    def test_34_writer_reports_write_failure(self) -> None:
        blocked = Path(self._temp.name) / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        writer = EventJournalWriter(blocked)
        result = writer.append_event(
            event_type="APP_STARTED",
            occurred_at="2026-08-08T09:00:00+09:00",
            category="SYSTEM",
            severity="INFO",
        )
        self.assertTrue(result["write_failed"])
        self.assertFalse(result["appended"])


if __name__ == "__main__":
    unittest.main()
