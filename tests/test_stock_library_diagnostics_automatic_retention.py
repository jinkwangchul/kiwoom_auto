# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import stock_library_diagnostics_retention as retention
from event_journal_contract import EVENT_TYPE_CATEGORIES, SUMMARY_TEMPLATES


class StockLibraryDiagnosticsAutomaticRetentionTest(unittest.TestCase):
    NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "diagnostics"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _suffix(session_id: str) -> str:
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:10]

    def _write_incident(
        self,
        session_id: str,
        captured_at: datetime,
        *,
        epoch: int = 1,
        invalid_count: int = 1,
    ) -> Path:
        path = self.root / (
            f"stock_library_invalid_codes_e{epoch}_{self._suffix(session_id)}.json"
        )
        summary = {
            "raw_count": 10,
            "normalized_count": 9,
            "valid_count": 9,
            "invalid_count": invalid_count,
            "invalid_by_reason": {"OTHER": invalid_count},
            "invalid_unique_count": invalid_count,
            "invalid_master_name_found": 0,
            "invalid_master_name_missing": invalid_count,
            "raw_invalid_token_count": invalid_count,
            "raw_invalid_by_reason": {"OTHER": invalid_count},
            "duplicate_count": 0,
        }
        payload = {
            "schema_version": "stock_library_invalid_codes_v1",
            "source": "KIWOOM_OPENAPI_MASTER",
            "login_session_id": session_id,
            "connection_epoch": epoch,
            "captured_at": captured_at.isoformat(),
            "summary": summary,
            "invalid_items": [
                {"stripped_token": f"BAD-{index}", "invalid_reason": "OTHER"}
                for index in range(invalid_count)
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _plan(
        self,
        *,
        session_id: str = "current-session",
        epoch: int = 99,
    ) -> dict[str, object]:
        return retention.plan_stock_library_diagnostic_retention(
            self.root,
            current_session_id=session_id,
            current_connection_epoch=epoch,
            now=self.NOW,
        )

    @staticmethod
    def _entry(plan: dict[str, object], path: Path) -> dict[str, object]:
        entries = plan["entries"]
        assert isinstance(entries, list)
        return next(entry for entry in entries if entry["path"] == path.name)

    def _run(
        self,
        runner: retention.StockLibraryDiagnosticsAutomaticRetention,
        *,
        session_id: str = "current-session",
        epoch: int = 99,
    ) -> dict[str, object]:
        with mock.patch.object(
            retention,
            "_is_production_diagnostics_root",
            return_value=True,
        ):
            return runner.run_for_session(
                current_session_id=session_id,
                current_connection_epoch=epoch,
                now=self.NOW,
            )

    def test_incident_age_boundary_uses_exact_seven_day_contract(self) -> None:
        under = self._write_incident(
            "incident-under",
            self.NOW - timedelta(days=6, hours=23, minutes=59),
            epoch=1,
        )
        exact = self._write_incident(
            "incident-exact",
            self.NOW - timedelta(days=7),
            epoch=2,
        )
        over = self._write_incident(
            "incident-over",
            self.NOW - timedelta(days=7, microseconds=1),
            epoch=3,
        )
        old = self._write_incident(
            "incident-old",
            self.NOW - timedelta(days=30),
            epoch=4,
        )

        plan = self._plan()

        self.assertEqual(retention.ACTION_KEEP, self._entry(plan, under)["action"])
        self.assertEqual(retention.ACTION_KEEP, self._entry(plan, exact)["action"])
        for path in (over, old):
            self.assertEqual(
                retention.ACTION_ROTATE_CANDIDATE,
                self._entry(plan, path)["action"],
            )
            self.assertEqual(
                "CLOSED_INCIDENT_OUTSIDE_RETENTION",
                self._entry(plan, path)["reason"],
            )

    def test_old_current_session_incident_remains_protected(self) -> None:
        path = self._write_incident(
            "current-incident",
            self.NOW - timedelta(days=30),
            epoch=7,
        )

        plan = self._plan(session_id="current-incident", epoch=7)

        self.assertEqual(retention.ACTION_PROTECTED, self._entry(plan, path)["action"])
        self.assertEqual("CURRENT_SESSION", self._entry(plan, path)["reason"])

    def test_empty_root_is_no_op_and_same_session_is_attempted_once(self) -> None:
        event_writer = mock.Mock()
        runner = retention.StockLibraryDiagnosticsAutomaticRetention(
            self.root,
            event_writer=event_writer,
        )

        results = [self._run(runner, session_id="session-a", epoch=1) for _ in range(10)]
        relogin = self._run(runner, session_id="session-b", epoch=2)

        self.assertEqual(retention.AUTOMATIC_RUN_NO_CANDIDATES, results[0]["status"])
        self.assertTrue(results[0]["attempted"])
        self.assertTrue(
            all(
                result["status"] == retention.AUTOMATIC_RUN_ALREADY_ATTEMPTED
                and not result["attempted"]
                for result in results[1:]
            )
        )
        self.assertEqual(retention.AUTOMATIC_RUN_NO_CANDIDATES, relogin["status"])
        self.assertEqual(2, event_writer.call_count)

    def test_three_old_incidents_are_deleted_exactly_once(self) -> None:
        paths = [
            self._write_incident(
                f"old-incident-{index}",
                self.NOW - timedelta(days=30 + index),
                epoch=index + 1,
            )
            for index in range(3)
        ]
        runner = retention.StockLibraryDiagnosticsAutomaticRetention(self.root)

        result = self._run(runner)

        self.assertEqual(retention.AUTOMATIC_RUN_COMPLETED, result["status"])
        self.assertEqual(3, result["candidate_count"])
        self.assertEqual(3, result["deleted_count"])
        self.assertTrue(all(not path.exists() for path in paths))
        replay = self._run(runner)
        self.assertEqual(retention.AUTOMATIC_RUN_ALREADY_ATTEMPTED, replay["status"])

    def test_recent_incident_is_kept_while_old_incident_is_deleted(self) -> None:
        recent = self._write_incident(
            "recent-incident",
            self.NOW - timedelta(days=1),
            epoch=1,
        )
        old = self._write_incident(
            "old-incident",
            self.NOW - timedelta(days=30),
            epoch=2,
        )
        runner = retention.StockLibraryDiagnosticsAutomaticRetention(self.root)

        result = self._run(runner)

        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(1, result["deleted_count"])
        self.assertTrue(recent.exists())
        self.assertFalse(old.exists())

    def test_permission_error_is_partial_and_other_candidates_continue(self) -> None:
        blocked = self._write_incident(
            "blocked-incident",
            self.NOW - timedelta(days=31),
            epoch=1,
        )
        deleted = [
            self._write_incident(
                f"deletable-incident-{index}",
                self.NOW - timedelta(days=30 + index),
                epoch=index + 2,
            )
            for index in range(2)
        ]
        event_writer = mock.Mock()
        runner = retention.StockLibraryDiagnosticsAutomaticRetention(
            self.root,
            event_writer=event_writer,
        )
        original_unlink = retention._unlink_file

        def controlled_unlink(path: Path) -> None:
            if path == blocked:
                raise PermissionError(5, "file in use", str(path))
            original_unlink(path)

        with mock.patch.object(retention, "_unlink_file", side_effect=controlled_unlink):
            result = self._run(runner)

        self.assertEqual(retention.AUTOMATIC_RUN_COMPLETED, result["status"])
        self.assertEqual(1, result["failed_io"])
        self.assertEqual(2, result["deleted_count"])
        self.assertTrue(blocked.exists())
        self.assertTrue(all(not path.exists() for path in deleted))
        call = event_writer.call_args
        self.assertEqual(
            "STOCK_LIBRARY_DIAGNOSTICS_RETENTION_COMPLETED",
            call.args[0],
        )
        self.assertEqual("WARNING", call.kwargs["severity"])
        self.assertEqual(1, call.kwargs["details"]["failed_io"])

    def test_automatic_authority_is_plan_bound_one_shot_and_manual_contract_is_separate(self) -> None:
        path = self._write_incident(
            "authority-incident",
            self.NOW - timedelta(days=30),
        )
        plan = self._plan()
        with mock.patch.object(
            retention,
            "_is_production_diagnostics_root",
            return_value=True,
        ):
            authority = (
                retention.create_automatic_stock_library_diagnostics_retention_authority(
                    plan,
                    root=self.root,
                    current_session_id="current-session",
                    current_connection_epoch=99,
                )
            )
            result = retention.execute_stock_library_diagnostic_retention(
                plan,
                root=self.root,
                current_session_id="current-session",
                current_connection_epoch=99,
                automatic_authority=authority,
            )
            with self.assertRaisesRegex(
                PermissionError,
                "AUTOMATIC_RETENTION_AUTHORITY_CONSUMED",
            ):
                retention.execute_stock_library_diagnostic_retention(
                    plan,
                    root=self.root,
                    current_session_id="current-session",
                    current_connection_epoch=99,
                    automatic_authority=authority,
                )

        self.assertEqual(1, result["deleted_files"])
        self.assertFalse(path.exists())
        self.assertNotEqual(
            retention.AUTHORIZATION_PURPOSE,
            authority.purpose,
        )

    def test_changed_incident_signature_is_skipped_without_consuming_authority(self) -> None:
        path = self._write_incident(
            "changed-incident",
            self.NOW - timedelta(days=30),
            invalid_count=1,
        )
        plan = self._plan()
        with mock.patch.object(
            retention,
            "_is_production_diagnostics_root",
            return_value=True,
        ):
            authority = (
                retention.create_automatic_stock_library_diagnostics_retention_authority(
                    plan,
                    root=self.root,
                    current_session_id="current-session",
                    current_connection_epoch=99,
                )
            )
            self._write_incident(
                "changed-incident",
                self.NOW - timedelta(days=30),
                invalid_count=2,
            )
            result = retention.execute_stock_library_diagnostic_retention(
                plan,
                root=self.root,
                current_session_id="current-session",
                current_connection_epoch=99,
                automatic_authority=authority,
            )

        self.assertEqual(1, result["skipped_changed"])
        self.assertEqual(0, result["deleted_files"])
        self.assertTrue(path.exists())

    def test_event_payload_is_compact_and_contains_no_file_list(self) -> None:
        path = self._write_incident(
            "event-incident",
            self.NOW - timedelta(days=30),
        )
        event_writer = mock.Mock()
        runner = retention.StockLibraryDiagnosticsAutomaticRetention(
            self.root,
            event_writer=event_writer,
        )

        result = self._run(runner)

        self.assertEqual(1, result["deleted_count"])
        call = event_writer.call_args
        details = call.kwargs["details"]
        self.assertNotIn("entries", details)
        self.assertNotIn("files", details)
        self.assertNotIn(path.name, json.dumps(details, ensure_ascii=False))
        self.assertEqual(7, details["retention_days"])
        self.assertEqual(1, details["deleted_count"])

    def test_retention_event_types_are_system_events(self) -> None:
        for event_type in (
            "STOCK_LIBRARY_DIAGNOSTICS_RETENTION_COMPLETED",
            "STOCK_LIBRARY_DIAGNOSTICS_RETENTION_FAILED",
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual("SYSTEM", EVENT_TYPE_CATEGORIES[event_type])
                self.assertIn("진단", SUMMARY_TEMPLATES[event_type])

    def test_runner_failure_and_event_writer_failure_are_nonblocking(self) -> None:
        runner = retention.StockLibraryDiagnosticsAutomaticRetention(
            self.root,
            event_writer=mock.Mock(side_effect=RuntimeError("event failed")),
        )
        with mock.patch.object(
            retention,
            "plan_stock_library_diagnostic_retention",
            side_effect=RuntimeError("scan failed"),
        ):
            result = self._run(runner, session_id="failure-session", epoch=5)

        self.assertEqual(retention.AUTOMATIC_RUN_FAILED, result["status"])
        self.assertTrue(result["attempted"])
        self.assertFalse(result["event_recorded"])

    def test_automatic_authority_rejects_non_seven_day_plan(self) -> None:
        self._write_incident("wrong-policy", self.NOW - timedelta(days=30))
        plan = retention.plan_stock_library_diagnostic_retention(
            self.root,
            policy=retention.StockLibraryDiagnosticRetentionPolicy(
                retention_age_days=0,
            ),
            current_session_id="current-session",
            current_connection_epoch=99,
            now=self.NOW,
        )
        with mock.patch.object(
            retention,
            "_is_production_diagnostics_root",
            return_value=True,
        ):
            with self.assertRaisesRegex(
                PermissionError,
                "AUTOMATIC_RETENTION_POLICY_MISMATCH",
            ):
                retention.create_automatic_stock_library_diagnostics_retention_authority(
                    plan,
                    root=self.root,
                    current_session_id="current-session",
                    current_connection_epoch=99,
                )


if __name__ == "__main__":
    unittest.main()
