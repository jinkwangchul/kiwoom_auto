from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from event_journal_contract import (
    DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH,
    DIAGNOSTIC_RAW_JSON_REDACTED,
    DIAGNOSTIC_REDACTED,
    DIAGNOSTIC_TRACEBACK_REDACTED,
    SCHEMA_VERSION,
    make_stack_fingerprint,
    sanitize_event_record,
    validate_event_record,
)
from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter


class EventJournalObserverContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "event_journal"
        self.writer = EventJournalWriter(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _append(self, **fields: object) -> dict[str, object]:
        return self.writer.append_event(
            event_type="PROCESSING_ERROR",
            occurred_at="2026-08-16T09:00:00+09:00",
            category="SYSTEM",
            severity="ERROR",
            template_args={"target": "Operation Host"},
            **fields,
        )

    def test_01_existing_v1_record_remains_readable(self) -> None:
        self.root.mkdir(parents=True)
        legacy = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "legacy-v1",
            "occurred_at": "2026-08-16T09:00:00+09:00",
            "category": "SYSTEM",
            "severity": "INFO",
            "event_type": "APP_STARTED",
            "summary": "legacy event",
        }
        (self.root / "2026-08.jsonl").write_text(
            json.dumps(legacy, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = EventJournalReader(self.root).read_events()
        self.assertEqual(0, result["invalid_count"])
        self.assertEqual(["legacy-v1"], [event["event_id"] for event in result["events"]])

    def test_02_optional_diagnostic_fields_round_trip_under_v1(self) -> None:
        result = self._append(
            component="operation_host",
            operation="run_operation_cycle",
            exception_type="RuntimeError",
            exception_message="cycle failed",
            correlation_id="cycle-1",
            parent_event_id="parent-1",
            stack_fingerprint="abc123",
            build_version="d94f129",
            details={"module": "gui_auto_trade_operation_host", "line": 155},
        )
        self.assertTrue(result["appended"])
        self.assertEqual(SCHEMA_VERSION, result["event"]["schema_version"])
        read = EventJournalReader(self.root).read_events()
        self.assertEqual("operation_host", read["events"][0]["component"])

    def test_03_raw_account_number_remains_rejected(self) -> None:
        result = self._append(details={"account_no": "8129123456"})
        self.assertTrue(result["invalid"])
        self.assertFalse(result["appended"])

    def test_04_credentials_and_broker_raw_payload_are_redacted(self) -> None:
        result = self._append(
            exception_message="password=hunter2 token:abc api_key=key-1",
            details={
                "password": "hunter2",
                "nested": {"token": "abc"},
                "broker_raw": {"raw": "payload"},
            },
        )
        self.assertTrue(result["appended"])
        event = result["event"]
        self.assertNotIn("hunter2", event["exception_message"])
        self.assertNotIn("key-1", event["exception_message"])
        self.assertEqual(DIAGNOSTIC_REDACTED, event["details"]["password"])
        self.assertEqual(DIAGNOSTIC_REDACTED, event["details"]["nested"]["token"])
        self.assertEqual(DIAGNOSTIC_REDACTED, event["details"]["broker_raw"])

    def test_04b_sensitive_setting_change_values_are_redacted(self) -> None:
        result = self._append(
            changes=[
                {
                    "field_key": "api_key",
                    "before": "old-key",
                    "after": "new-key",
                }
            ]
        )
        self.assertTrue(result["appended"])
        change = result["event"]["changes"][0]
        self.assertEqual("api_key", change["field_key"])
        self.assertEqual(DIAGNOSTIC_REDACTED, change["before"])
        self.assertEqual(DIAGNOSTIC_REDACTED, change["after"])

    def test_05_absolute_user_path_is_sanitized(self) -> None:
        result = self._append(
            exception_message=(
                "failed at C:\\Users\\JIN KWANG CHUL\\Documents\\kiwoom_auto\\gui_main.py"
            )
        )
        self.assertTrue(result["appended"])
        message = result["event"]["exception_message"]
        self.assertNotIn("JIN KWANG CHUL", message)
        self.assertIn("<USER_HOME>", message)

    def test_06_exception_message_is_bounded(self) -> None:
        result = self._append(exception_message="x" * 5000)
        self.assertTrue(result["appended"])
        message = result["event"]["exception_message"]
        self.assertLessEqual(len(message), DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH)
        self.assertTrue(message.endswith("...[TRUNCATED]"))

    def test_07_raw_traceback_and_raw_json_string_are_not_stored(self) -> None:
        traceback_result = self._append(
            event_id="traceback",
            exception_message=(
                "failed\nTraceback (most recent call last):\n"
                "  File C:\\Users\\name\\app.py, line 1\nRuntimeError: secret"
            ),
        )
        json_result = self._append(
            event_id="raw-json",
            details={"diagnostic_dump": '{"account":"8129123456","token":"abc"}'},
        )
        self.assertTrue(traceback_result["appended"])
        self.assertEqual(
            f"failed {DIAGNOSTIC_TRACEBACK_REDACTED}",
            traceback_result["event"]["exception_message"],
        )
        self.assertTrue(json_result["appended"])
        self.assertEqual(
            DIAGNOSTIC_RAW_JSON_REDACTED,
            json_result["event"]["details"]["diagnostic_dump"],
        )

    def test_08_sanitizer_failure_never_escapes_writer(self) -> None:
        production_state = {"mutated": False}
        with patch("event_journal_writer.sanitize_event_record", side_effect=RuntimeError("boom")):
            result = self._append(exception_message="failure")
        self.assertTrue(result["write_failed"])
        self.assertFalse(result["appended"])
        self.assertEqual({"mutated": False}, production_state)

    def test_09_unknown_field_is_still_rejected(self) -> None:
        result = self._append(unapproved_diagnostic_field="value")
        self.assertTrue(result["invalid"])
        self.assertIn("unsupported fields", " ".join(result["issues"]))

    def test_10_sanitizer_and_stack_fingerprint_are_fail_open_and_path_free(self) -> None:
        class BrokenText:
            def __str__(self) -> str:
                raise RuntimeError("no repr")

        sanitized = sanitize_event_record({"details": {"value": BrokenText()}})
        self.assertEqual("[UNAVAILABLE]", sanitized["details"]["value"])
        first = make_stack_fingerprint(
            exception_type="RuntimeError",
            module="C:\\Users\\one\\project\\worker.py",
            function="run",
            line=10,
        )
        second = make_stack_fingerprint(
            exception_type="RuntimeError",
            module="D:\\Users\\two\\other\\worker.py",
            function="run",
            line=10,
        )
        self.assertEqual(first, second)
        self.assertEqual(24, len(first))

    def test_11_direct_validation_rejects_unbounded_or_non_string_diagnostics(self) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "invalid-diagnostics",
            "occurred_at": "2026-08-16T09:00:00+09:00",
            "category": "SYSTEM",
            "severity": "ERROR",
            "event_type": "PROCESSING_ERROR",
            "summary": "failure",
            "component": 123,
            "exception_message": "x" * (DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH + 1),
        }
        issues = validate_event_record(record)
        self.assertIn("component must be a string", issues)
        self.assertTrue(any("exception_message exceeds" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
