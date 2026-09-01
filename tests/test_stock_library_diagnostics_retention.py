# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import stock_library_diagnostics_retention as retention


class StockLibraryDiagnosticsRetentionTest(unittest.TestCase):
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

    def _payload(
        self,
        session_id: str,
        captured_at: datetime,
        *,
        epoch: int = 1,
        invalid_count: int = 0,
        marker: str = "same-market-payload",
    ) -> dict[str, object]:
        return {
            "schema_version": "stock_library_invalid_codes_v1",
            "source": "KIWOOM_OPENAPI_MASTER",
            "login_session_id": session_id,
            "connection_epoch": epoch,
            "captured_at": captured_at.isoformat(),
            "markets": {"KOSPI": {"marker": marker}},
            "summary": {
                "raw_count": 10,
                "normalized_count": 10,
                "valid_count": 10,
                "invalid_count": invalid_count,
                "invalid_by_reason": {"OTHER": invalid_count} if invalid_count else {},
                "invalid_unique_count": invalid_count,
                "invalid_master_name_found": 0,
                "invalid_master_name_missing": invalid_count,
                "raw_invalid_token_count": invalid_count,
                "raw_invalid_by_reason": {"OTHER": invalid_count} if invalid_count else {},
                "duplicate_count": 0,
            },
            "invalid_items": [
                {"stripped_token": f"BAD-{index}", "invalid_reason": "OTHER"}
                for index in range(invalid_count)
            ],
        }

    def _write(
        self,
        session_id: str,
        captured_at: datetime,
        *,
        epoch: int = 1,
        invalid_count: int = 0,
        marker: str = "same-market-payload",
    ) -> Path:
        path = self.root / (
            f"stock_library_invalid_codes_e{epoch}_{self._suffix(session_id)}.json"
        )
        path.write_text(
            json.dumps(
                self._payload(
                    session_id,
                    captured_at,
                    epoch=epoch,
                    invalid_count=invalid_count,
                    marker=marker,
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _entry(plan: dict[str, object], path: Path) -> dict[str, object]:
        entries = plan["entries"]
        assert isinstance(entries, list)
        return next(entry for entry in entries if entry["path"] == path.name)

    def _plan(
        self,
        *,
        policy: retention.StockLibraryDiagnosticRetentionPolicy | None = None,
        current_session_id: str | None = "",
        current_connection_epoch: int | None = None,
        protected_paths: tuple[Path, ...] = (),
    ) -> dict[str, object]:
        return retention.plan_stock_library_diagnostic_retention(
            self.root,
            policy=policy or retention.DEFAULT_RETENTION_POLICY,
            current_session_id=current_session_id,
            current_connection_epoch=current_connection_epoch,
            protected_paths=protected_paths,
            now=self.NOW,
        )

    def _purge_plan(
        self,
        *,
        current_session_id: str | None = "",
        current_connection_epoch: int | None = None,
        protected_paths: tuple[Path, ...] = (),
        writer_active: bool | None = False,
    ) -> dict[str, object]:
        return retention.plan_existing_normal_stock_library_diagnostics_purge(
            self.root,
            current_session_id=current_session_id,
            current_connection_epoch=current_connection_epoch,
            protected_paths=protected_paths,
            writer_active=writer_active,
        )

    def _candidate_plan(self, *paths: Path) -> dict[str, object]:
        if not paths:
            paths = (
                self._write("old-candidate", self.NOW - timedelta(days=30)),
            )
        return self._plan(
            policy=retention.StockLibraryDiagnosticRetentionPolicy(
                retention_age_days=0,
            )
        )

    def _execute(
        self,
        plan: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        options: dict[str, object] = {
            "root": self.root,
            "current_session_id": "",
        }
        options.update(kwargs)
        return retention.execute_stock_library_diagnostic_retention(plan, **options)

    def _authorize(
        self,
        plan: dict[str, object],
        **kwargs: object,
    ) -> retention.ProductionDiagnosticsRetentionAuthorization:
        options: dict[str, object] = {
            "root": self.root,
            "current_session_id": "",
        }
        options.update(kwargs)
        return retention.create_production_diagnostics_retention_authorization(
            plan,
            **options,
        )

    @staticmethod
    def _execution_entry(
        result: dict[str, object],
        path: Path,
    ) -> dict[str, object]:
        entries = result["entries"]
        assert isinstance(entries, list)
        return next(entry for entry in entries if entry["path"] == path.name)

    def test_empty_directory_returns_empty_dry_run(self) -> None:
        plan = self._plan()

        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["mutation_supported"])
        self.assertEqual(0, plan["total_files"])
        self.assertEqual([], plan["entries"])

    def test_current_session_is_protected(self) -> None:
        session_id = "current-session"
        path = self._write(session_id, self.NOW - timedelta(days=30), epoch=7)

        plan = self._plan(
            current_session_id=session_id,
            current_connection_epoch=7,
        )

        entry = self._entry(plan, path)
        self.assertEqual(retention.ACTION_PROTECTED, entry["action"])
        self.assertEqual("CURRENT_SESSION", entry["reason"])
        self.assertTrue(entry["current"])

    def test_old_incident_is_retention_candidate(self) -> None:
        path = self._write(
            "incident-session",
            self.NOW - timedelta(days=30),
            invalid_count=1,
        )
        policy = retention.StockLibraryDiagnosticRetentionPolicy(
            retention_age_days=0,
        )

        entry = self._entry(self._plan(policy=policy), path)

        self.assertEqual(retention.ACTION_ROTATE_CANDIDATE, entry["action"])
        self.assertEqual("CLOSED_INCIDENT_OUTSIDE_RETENTION", entry["reason"])
        self.assertEqual(1, entry["invalid_count"])

    def test_compact_recent_incident_without_markets_is_recognized_and_kept(self) -> None:
        path = self._write(
            "compact-incident",
            self.NOW - timedelta(days=1),
            invalid_count=3,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("markets")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        plan = self._plan()
        entry = self._entry(plan, path)

        self.assertEqual(
            "stock_library_invalid_codes_v1",
            entry["parsed_payload_version"],
        )
        self.assertEqual(3, entry["invalid_count"])
        self.assertEqual(retention.ACTION_KEEP, entry["action"])
        self.assertEqual("RECENT_AGE", entry["reason"])

    def test_existing_normal_purge_plan_selects_only_recognized_normal_files(self) -> None:
        normal = self._write("purge-normal", self.NOW - timedelta(hours=1))
        incident = self._write(
            "purge-incident",
            self.NOW - timedelta(days=30),
            invalid_count=2,
        )
        malformed = self.root / "stock_library_invalid_codes_e1_deadbeef00.json"
        malformed.write_text("{broken", encoding="utf-8")

        plan = self._purge_plan()

        self.assertEqual(retention.PURGE_EXISTING_NORMAL_DIAGNOSTICS, plan["operation"])
        self.assertEqual(retention.ACTION_ROTATE_CANDIDATE, self._entry(plan, normal)["action"])
        self.assertEqual(retention.ACTION_PROTECTED, self._entry(plan, incident)["action"])
        self.assertEqual(retention.ACTION_SKIP_UNCERTAIN, self._entry(plan, malformed)["action"])
        self.assertEqual(1, plan["rotate_candidate_files"])
        self.assertEqual(1, plan["protected_files"])
        self.assertEqual(1, plan["skip_uncertain_files"])

    def test_existing_normal_purge_requires_confirmed_inactive_writer(self) -> None:
        self._write("purge-writer-state", self.NOW - timedelta(days=30))
        for writer_active in (None, True):
            with self.subTest(writer_active=writer_active):
                with self.assertRaisesRegex(
                    ValueError,
                    "PURGE_REQUIRES_CONFIRMED_INACTIVE_WRITER",
                ):
                    self._purge_plan(writer_active=writer_active)

    def test_existing_normal_purge_reprotects_current_session_and_active_target(self) -> None:
        current = self._write("purge-current", self.NOW, epoch=4)
        active = self._write("purge-active", self.NOW - timedelta(days=30), epoch=5)
        candidate = self._write("purge-candidate", self.NOW - timedelta(days=30), epoch=6)

        plan = self._purge_plan(
            current_session_id="purge-current",
            current_connection_epoch=4,
            protected_paths=(active,),
        )

        self.assertEqual(retention.ACTION_PROTECTED, self._entry(plan, current)["action"])
        self.assertEqual(retention.ACTION_PROTECTED, self._entry(plan, active)["action"])
        self.assertEqual(
            retention.ACTION_ROTATE_CANDIDATE,
            self._entry(plan, candidate)["action"],
        )

    def test_authorized_existing_normal_purge_deletes_normal_only_once(self) -> None:
        normal = self._write("purge-authorized-normal", self.NOW - timedelta(days=30))
        incident = self._write(
            "purge-authorized-incident",
            self.NOW - timedelta(days=30),
            invalid_count=1,
        )
        plan = self._purge_plan()
        authorization = self._authorize(plan)

        preview = self._execute(plan, dry_run=True, authorization=authorization)
        result = self._execute(plan, authorization=authorization)

        self.assertEqual(1, preview["dry_run_only"])
        self.assertEqual(0, preview["deleted_files"])
        self.assertEqual(1, result["deleted_files"])
        self.assertFalse(normal.exists())
        self.assertTrue(incident.exists())
        self.assertIn(
            "STATE: CONSUMED",
            retention.format_production_diagnostics_retention_authorization_report(
                authorization
            ),
        )
        with self.assertRaisesRegex(
            PermissionError,
            "PRODUCTION_DIAGNOSTICS_AUTHORIZATION_CONSUMED",
        ):
            self._execute(plan, authorization=authorization)

    def test_recent_age_is_kept_and_old_normal_is_candidate(self) -> None:
        recent = self._write("recent-session", self.NOW - timedelta(days=1))
        old = self._write("old-session", self.NOW - timedelta(days=30))
        policy = retention.StockLibraryDiagnosticRetentionPolicy(
            retention_age_days=7,
        )

        plan = self._plan(policy=policy)

        self.assertEqual(retention.ACTION_KEEP, self._entry(plan, recent)["action"])
        self.assertEqual(
            retention.ACTION_ROTATE_CANDIDATE,
            self._entry(plan, old)["action"],
        )

    def test_old_normal_files_are_candidates_regardless_of_session_rank(self) -> None:
        paths = [
            self._write(
                f"old-session-{index}",
                self.NOW - timedelta(days=30 - index),
                marker=f"marker-{index}",
            )
            for index in range(11)
        ]

        plan = self._plan()

        self.assertEqual(0, plan["keep_files"])
        self.assertEqual(11, plan["rotate_candidate_files"])
        self.assertTrue(
            all(
                self._entry(plan, path)["action"]
                == retention.ACTION_ROTATE_CANDIDATE
                for path in paths
            )
        )

    def test_multiple_session_count_is_irrelevant_within_retention_age(self) -> None:
        paths: list[Path] = []
        for session_count in (1, 10, 100):
            paths.extend(
                self._write(
                    f"recent-session-{index}",
                    self.NOW - timedelta(days=6, hours=23),
                    marker=f"recent-marker-{index}",
                )
                for index in range(len(paths), session_count)
            )

            with self.subTest(session_count=session_count):
                plan = self._plan()
                self.assertEqual(session_count, plan["keep_files"])
                self.assertEqual(0, plan["rotate_candidate_files"])
                self.assertTrue(
                    all(
                        self._entry(plan, path)["reason"] == "RECENT_AGE"
                        for path in paths
                    )
                )

    def test_exact_seven_day_age_boundary_is_timezone_aware(self) -> None:
        seoul = timezone(timedelta(hours=9))
        now_in_seoul = self.NOW.astimezone(seoul)
        under = self._write(
            "boundary-under",
            now_in_seoul - timedelta(days=6, hours=23, minutes=59),
        )
        exact = self._write("boundary-exact", now_in_seoul - timedelta(days=7))
        over = self._write(
            "boundary-over",
            now_in_seoul - timedelta(days=7, microseconds=1),
        )

        plan = self._plan()

        self.assertEqual(retention.ACTION_KEEP, self._entry(plan, under)["action"])
        self.assertEqual(retention.ACTION_KEEP, self._entry(plan, exact)["action"])
        self.assertEqual(
            retention.ACTION_ROTATE_CANDIDATE,
            self._entry(plan, over)["action"],
        )

    def test_malformed_unknown_and_temp_files_never_become_candidates(self) -> None:
        malformed_session = "malformed-session"
        malformed = self.root / (
            f"stock_library_invalid_codes_e1_{self._suffix(malformed_session)}.json"
        )
        malformed.write_text("{", encoding="utf-8")
        old_timestamp_ns = int((self.NOW - timedelta(days=30)).timestamp() * 1_000_000_000)
        os.utime(malformed, ns=(old_timestamp_ns, old_timestamp_ns))
        unknown = self.root / "decision_trace.json"
        unknown.write_text("{}", encoding="utf-8")
        temporary = self.root / "stock_library_invalid_codes_e1_deadbeef00.json.tmp"
        temporary.write_text("temporary", encoding="utf-8")

        plan = self._plan()

        self.assertEqual(retention.ACTION_SKIP_UNCERTAIN, self._entry(plan, malformed)["action"])
        self.assertEqual(retention.ACTION_SKIP_UNCERTAIN, self._entry(plan, unknown)["action"])
        self.assertEqual(retention.ACTION_PROTECTED, self._entry(plan, temporary)["action"])
        self.assertEqual(0, plan["rotate_candidate_files"])

    def test_unknown_current_session_state_fails_closed(self) -> None:
        path = self._write("closed-but-unconfirmed", self.NOW - timedelta(days=30))
        policy = retention.StockLibraryDiagnosticRetentionPolicy(
            retention_age_days=0,
        )

        entry = self._entry(
            self._plan(policy=policy, current_session_id=None),
            path,
        )

        self.assertEqual(retention.ACTION_SKIP_UNCERTAIN, entry["action"])
        self.assertEqual("CURRENT_SESSION_UNKNOWN", entry["reason"])

    def test_semantic_duplicate_is_metadata_not_action_authority(self) -> None:
        first = self._write("semantic-one", self.NOW - timedelta(days=30))
        second = self._write("semantic-two", self.NOW - timedelta(days=29))
        policy = retention.StockLibraryDiagnosticRetentionPolicy(
            retention_age_days=0,
        )

        plan = self._plan(policy=policy)

        self.assertEqual(1, plan["semantic_payload_count"])
        self.assertEqual(1, plan["semantic_duplicate_files"])
        self.assertEqual(
            self._entry(plan, first)["semantic_hash"],
            self._entry(plan, second)["semantic_hash"],
        )
        self.assertEqual(retention.ACTION_ROTATE_CANDIDATE, self._entry(plan, first)["action"])
        self.assertEqual(retention.ACTION_ROTATE_CANDIDATE, self._entry(plan, second)["action"])

    def test_signature_determinism_and_read_only_behavior(self) -> None:
        path = self._write("stable-session", self.NOW - timedelta(days=30))
        before = (path.read_bytes(), path.stat().st_mtime_ns)

        first = self._plan()
        second = self._plan()
        after = (path.read_bytes(), path.stat().st_mtime_ns)

        self.assertEqual(first, second)
        self.assertEqual(before, after)
        signature = self._entry(first, path)["signature"]
        self.assertTrue(signature["stable"])
        self.assertEqual(hashlib.sha256(before[0]).hexdigest(), signature["sha256"])

    def test_same_mtime_uses_filename_as_deterministic_tiebreaker(self) -> None:
        first = self._write("tie-one", self.NOW - timedelta(days=30))
        second = self._write("tie-two", self.NOW - timedelta(days=30))
        timestamp = 1_700_000_000_000_000_000
        os.utime(first, ns=(timestamp, timestamp))
        os.utime(second, ns=(timestamp, timestamp))

        plan = self._plan()
        paths = [entry["path"] for entry in plan["entries"]]

        self.assertEqual(sorted((first.name, second.name)), paths)

    def test_explicit_active_target_is_protected_and_outside_path_is_rejected(self) -> None:
        path = self._write("active-target", self.NOW - timedelta(days=30))
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")

        plan = self._plan(protected_paths=(path, outside))

        entry = self._entry(plan, path)
        self.assertEqual(retention.ACTION_PROTECTED, entry["action"])
        self.assertEqual("EXPLICIT_ACTIVE_TARGET", entry["reason"])
        self.assertIn("PROTECTED_PATH_OUTSIDE_ROOT", plan["scan_issues"])

    def test_outside_symlink_is_not_followed(self) -> None:
        outside = Path(self.temporary.name) / "outside-payload.json"
        outside.write_text("outside", encoding="utf-8")
        link = self.root / "stock_library_invalid_codes_e1_deadbeef00.json"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        plan = self._plan()
        entry = self._entry(plan, link)

        self.assertEqual(retention.ACTION_SKIP_UNCERTAIN, entry["action"])
        self.assertEqual("SYMLINK_NOT_FOLLOWED", entry["reason"])
        self.assertEqual("outside", outside.read_text(encoding="utf-8"))

    def test_size_cap_is_advisory_only(self) -> None:
        path = self._write("size-cap-session", self.NOW - timedelta(days=1))
        policy = retention.StockLibraryDiagnosticRetentionPolicy(
            retention_age_days=7,
            max_total_bytes=1,
        )

        plan = self._plan(policy=policy)

        self.assertTrue(plan["size_cap_exceeded"])
        self.assertEqual(retention.ACTION_KEEP, self._entry(plan, path)["action"])

    def test_report_contains_only_dry_run_categories(self) -> None:
        self._write("report-session", self.NOW - timedelta(days=30))

        report = retention.format_stock_library_diagnostic_retention_report(self._plan())

        for heading in (
            "RETENTION_AGE: 7 days",
            "SCAN:",
            "KEEP:",
            "PROTECTED:",
            "ROTATE_CANDIDATE:",
            "SKIP_UNCERTAIN:",
            "ESTIMATED_RECLAIMABLE:",
        ):
            self.assertIn(heading, report)
        self.assertNotIn("RECENT_SESSION", report)

    def test_executor_deletes_only_temp_root_candidates_in_plan_order(self) -> None:
        paths = [
            self._write(
                f"delete-candidate-{index}",
                self.NOW - timedelta(days=30 + index),
                marker=f"delete-{index}",
            )
            for index in range(3)
        ]
        plan = self._candidate_plan(*paths)
        planned_order = [
            entry["path"]
            for entry in plan["entries"]
            if entry["action"] == retention.ACTION_ROTATE_CANDIDATE
        ]

        result = self._execute(plan)

        self.assertEqual(3, result["planned_candidates"])
        self.assertEqual(3, result["deleted_files"])
        self.assertEqual(0, result["failed_files"])
        self.assertEqual(
            planned_order,
            [entry["path"] for entry in result["entries"]],
        )
        self.assertTrue(
            all(
                entry["execution_status"] == retention.EXECUTION_DELETED
                for entry in result["entries"]
            )
        )
        self.assertTrue(all(not path.exists() for path in paths))

    def test_executor_never_touches_non_candidate_actions(self) -> None:
        keep = self._write("recent-keep", self.NOW - timedelta(days=1))
        incident = self._write(
            "recent-incident-keep",
            self.NOW - timedelta(days=1),
            invalid_count=1,
        )
        malformed = self.root / "decision_trace.json"
        malformed.write_text("{}", encoding="utf-8")
        temporary = self.root / "stock_library_invalid_codes_e1_deadbeef00.json.tmp"
        temporary.write_text("temporary", encoding="utf-8")
        plan = self._plan()

        result = self._execute(plan)

        self.assertEqual(0, result["planned_candidates"])
        self.assertEqual([], result["entries"])
        self.assertTrue(all(path.exists() for path in (keep, incident, malformed, temporary)))

    def test_executor_dry_run_revalidates_without_unlink(self) -> None:
        path = self._write("dry-run-candidate", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)

        result = self._execute(plan, dry_run=True)

        self.assertEqual(0, result["deleted_files"])
        self.assertEqual(1, result["dry_run_only"])
        self.assertEqual(
            retention.EXECUTION_DRY_RUN_ONLY,
            self._execution_entry(result, path)["execution_status"],
        )
        self.assertTrue(path.exists())

    def test_executor_skips_changed_content_signature(self) -> None:
        session_id = "changed-content"
        path = self._write(session_id, self.NOW - timedelta(days=30), marker="before")
        plan = self._candidate_plan(path)
        payload = self._payload(
            session_id,
            self.NOW - timedelta(days=30),
            marker="after-value",
        )
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        result = self._execute(plan)

        self.assertEqual(1, result["skipped_changed"])
        self.assertEqual(
            "TARGET_SIGNATURE_CHANGED",
            self._execution_entry(result, path)["reason"],
        )
        self.assertTrue(path.exists())

    def test_executor_skips_mtime_only_change(self) -> None:
        path = self._write("changed-mtime", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        result = self._execute(plan)

        self.assertEqual(1, result["skipped_changed"])
        self.assertTrue(path.exists())

    def test_executor_skips_size_change(self) -> None:
        path = self._write("changed-size", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

        result = self._execute(plan)

        self.assertEqual(1, result["skipped_changed"])
        self.assertTrue(path.exists())

    def test_executor_reprotects_new_current_session(self) -> None:
        session_id = "became-current"
        path = self._write(session_id, self.NOW - timedelta(days=30), epoch=9)
        plan = self._candidate_plan(path)

        result = self._execute(
            plan,
            current_session_id=session_id,
            current_connection_epoch=9,
        )

        entry = self._execution_entry(result, path)
        self.assertEqual(retention.EXECUTION_SKIPPED_PROTECTED, entry["execution_status"])
        self.assertEqual("CURRENT_SESSION", entry["reason"])
        self.assertTrue(path.exists())

    def test_executor_reprotects_file_that_became_incident(self) -> None:
        session_id = "became-incident"
        captured_at = self.NOW - timedelta(days=30)
        path = self._write(session_id, captured_at)
        plan = self._candidate_plan(path)
        path.write_text(
            json.dumps(
                self._payload(session_id, captured_at, invalid_count=1),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self._execute(plan)

        entry = self._execution_entry(result, path)
        self.assertEqual(retention.EXECUTION_SKIPPED_PROTECTED, entry["execution_status"])
        self.assertEqual("INCIDENT_INVALID_CODES", entry["reason"])
        self.assertTrue(path.exists())

    def test_executor_reprotects_explicit_active_target(self) -> None:
        path = self._write("active-at-execute", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)

        result = self._execute(plan, protected_paths=(path,))

        self.assertEqual(1, result["skipped_protected"])
        self.assertEqual(
            "EXPLICIT_ACTIVE_TARGET",
            self._execution_entry(result, path)["reason"],
        )
        self.assertTrue(path.exists())

    def test_executor_permission_error_is_file_local_and_next_candidate_continues(self) -> None:
        first = self._write(
            "permission-failure",
            self.NOW - timedelta(days=31),
            marker="permission-failure",
        )
        second = self._write(
            "permission-success",
            self.NOW - timedelta(days=30),
            marker="permission-success",
        )
        plan = self._candidate_plan(first, second)
        original_unlink = retention._unlink_file

        def controlled_unlink(path: Path) -> None:
            if path == first:
                raise PermissionError(5, "file in use", str(path))
            original_unlink(path)

        with mock.patch.object(retention, "_unlink_file", side_effect=controlled_unlink):
            result = self._execute(plan)

        self.assertEqual(1, result["failed_files"])
        self.assertEqual(1, result["deleted_files"])
        failed = self._execution_entry(result, first)
        self.assertEqual(retention.EXECUTION_FAILED_IO, failed["execution_status"])
        self.assertEqual("PermissionError", failed["error_type"])
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())

    def test_executor_rejects_symlink_replacement_without_touching_outside_file(self) -> None:
        path = self._write("symlink-candidate", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("outside", encoding="utf-8")
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        result = self._execute(plan)

        self.assertEqual(1, result["skipped_unsafe"])
        self.assertEqual(
            retention.EXECUTION_SKIPPED_UNSAFE,
            self._execution_entry(result, path)["execution_status"],
        )
        self.assertEqual("outside", outside.read_text(encoding="utf-8"))

    def test_executor_rejects_invalid_plans_before_any_unlink(self) -> None:
        candidate = self._write("invalid-plan-candidate", self.NOW - timedelta(days=30))
        candidate_plan = self._candidate_plan(candidate)
        keep = self._write("invalid-plan-keep", self.NOW - timedelta(days=1))
        keep_plan = self._plan()
        other_root = Path(self.temporary.name) / "other-diagnostics"
        other_root.mkdir()

        duplicate = deepcopy(candidate_plan)
        duplicate["entries"].append(deepcopy(duplicate["entries"][0]))
        missing_signature = deepcopy(candidate_plan)
        missing_signature["entries"][0].pop("signature")
        forged_action = deepcopy(keep_plan)
        self._entry(forged_action, keep)["action"] = retention.ACTION_ROTATE_CANDIDATE
        invalid_action = deepcopy(candidate_plan)
        invalid_action["entries"][0]["action"] = "DELETE_NOW"
        root_escape = deepcopy(candidate_plan)
        root_escape["entries"][0]["path"] = "../outside.json"
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text("outside", encoding="utf-8")

        cases = (
            (duplicate, self.root, "DUPLICATE_PLAN_PATH"),
            (missing_signature, self.root, "PLAN_ENTRY_SIGNATURE_MISSING"),
            (forged_action, self.root, "PLAN_CANDIDATE_COUNT_MISMATCH"),
            (invalid_action, self.root, "PLAN_ENTRY_ACTION_INVALID"),
            (root_escape, self.root, "PLAN_ENTRY_PATH_UNSAFE"),
            (candidate_plan, other_root, "PLAN_ROOT_MISMATCH"),
        )
        for plan, root, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    retention.execute_stock_library_diagnostic_retention(
                        plan,
                        root=root,
                        current_session_id="",
                    )
                self.assertTrue(candidate.exists())
                self.assertEqual("outside", outside.read_text(encoding="utf-8"))

    def test_production_mode_non_dry_run_without_authorization_is_hard_blocked(self) -> None:
        path = self._write("production-mode-no-auth", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            with self.assertRaisesRegex(
                PermissionError,
                "PRODUCTION_DIAGNOSTICS_EXECUTION_FORBIDDEN",
            ):
                self._execute(plan)

        self.assertTrue(path.exists())

    def test_authorization_schema_report_and_default_ttl(self) -> None:
        path = self._write("authorization-report", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)

        authorization = self._authorize(plan)
        report = retention.format_production_diagnostics_retention_authorization_report(
            authorization
        )

        self.assertEqual(retention.AUTHORIZATION_SCHEMA_VERSION, authorization.version)
        self.assertEqual(str(self.root.resolve()), authorization.diagnostics_root)
        self.assertEqual(plan["plan_signature"], authorization.plan_signature)
        self.assertEqual(1, authorization.candidate_count)
        self.assertEqual(plan["rotate_candidate_bytes"], authorization.candidate_bytes)
        self.assertTrue(authorization.one_shot)
        issued = datetime.fromisoformat(authorization.issued_at)
        expires = datetime.fromisoformat(authorization.expires_at)
        self.assertEqual(
            retention.DEFAULT_AUTHORIZATION_TTL_SECONDS,
            int((expires - issued).total_seconds()),
        )
        for heading in (
            "AUTHORIZATION_ID:",
            "ROOT:",
            "PLAN_SIGNATURE:",
            "CANDIDATES:",
            "BYTES:",
            "ISSUED_AT:",
            "EXPIRES_AT:",
            "ONE_SHOT:",
            "STATE: ISSUED",
        ):
            self.assertIn(heading, report)

    def test_valid_authorization_dry_run_does_not_consume_then_deletes_once(self) -> None:
        paths = [
            self._write(
                f"authorized-delete-{index}",
                self.NOW - timedelta(days=30 + index),
                marker=f"authorized-{index}",
            )
            for index in range(3)
        ]
        plan = self._candidate_plan(*paths)
        authorization = self._authorize(plan)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            preview = self._execute(plan, dry_run=True, authorization=authorization)
            self.assertEqual(3, preview["dry_run_only"])
            self.assertIn(
                "STATE: ISSUED",
                retention.format_production_diagnostics_retention_authorization_report(
                    authorization
                ),
            )
            result = self._execute(plan, authorization=authorization)

        self.assertEqual(3, result["deleted_files"])
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertIn(
            "STATE: CONSUMED",
            retention.format_production_diagnostics_retention_authorization_report(
                authorization
            ),
        )

    def test_authorization_wrong_root_is_denied(self) -> None:
        first = self._write("authorization-root-a", self.NOW - timedelta(days=30))
        first_plan = self._candidate_plan(first)
        authorization = self._authorize(first_plan)
        other_root = Path(self.temporary.name) / "other-authorized-root"
        other_root.mkdir()
        session_id = "authorization-root-b"
        other = other_root / (
            f"stock_library_invalid_codes_e1_{self._suffix(session_id)}.json"
        )
        other.write_text(
            json.dumps(
                self._payload(session_id, self.NOW - timedelta(days=30)),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        other_plan = retention.plan_stock_library_diagnostic_retention(
            other_root,
            policy=retention.StockLibraryDiagnosticRetentionPolicy(
                retention_age_days=0,
            ),
            current_session_id="",
            now=self.NOW,
        )

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_ROOT_MISMATCH"):
                retention.execute_stock_library_diagnostic_retention(
                    other_plan,
                    root=other_root,
                    current_session_id="",
                    authorization=authorization,
                )

        self.assertTrue(first.exists())
        self.assertTrue(other.exists())

    def test_authorization_wrong_plan_is_denied(self) -> None:
        first = self._write("authorization-plan-a", self.NOW - timedelta(days=31))
        first_plan = self._candidate_plan(first)
        authorization = self._authorize(first_plan)
        second = self._write("authorization-plan-b", self.NOW - timedelta(days=30))
        second_plan = self._candidate_plan(first, second)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_PLAN_MISMATCH"):
                self._execute(second_plan, authorization=authorization)

        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_expired_authorization_is_denied_without_consumption(self) -> None:
        path = self._write("authorization-expired", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        authorization = self._authorize(plan)
        after_expiry = datetime.fromisoformat(authorization.expires_at) + timedelta(seconds=1)

        with mock.patch.object(retention, "_utc_now", return_value=after_expiry):
            with mock.patch.object(
                retention,
                "_is_production_diagnostics_root",
                return_value=True,
            ):
                with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_EXPIRED"):
                    self._execute(plan, authorization=authorization)

        self.assertTrue(path.exists())

    def test_consumed_authorization_replay_is_denied(self) -> None:
        path = self._write("authorization-replay", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        authorization = self._authorize(plan)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            result = self._execute(plan, authorization=authorization)
            with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_CONSUMED"):
                self._execute(plan, authorization=authorization)

        self.assertEqual(1, result["deleted_files"])
        self.assertFalse(path.exists())

    def test_partial_failure_consumes_authorization_and_denies_replay(self) -> None:
        blocked = self._write(
            "authorization-partial-blocked",
            self.NOW - timedelta(days=31),
            marker="partial-blocked",
        )
        deleted = self._write(
            "authorization-partial-deleted",
            self.NOW - timedelta(days=30),
            marker="partial-deleted",
        )
        plan = self._candidate_plan(blocked, deleted)
        authorization = self._authorize(plan)
        original_unlink = retention._unlink_file

        def controlled_unlink(path: Path) -> None:
            if path == blocked:
                raise PermissionError(5, "file in use", str(path))
            original_unlink(path)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            with mock.patch.object(retention, "_unlink_file", side_effect=controlled_unlink):
                result = self._execute(plan, authorization=authorization)
            with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_CONSUMED"):
                self._execute(plan, authorization=authorization)

        self.assertEqual(1, result["failed_files"])
        self.assertEqual(1, result["deleted_files"])
        self.assertTrue(blocked.exists())
        self.assertFalse(deleted.exists())

    def test_authorization_session_change_is_denied_before_execution(self) -> None:
        path = self._write("authorization-session-change", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        authorization = self._authorize(plan)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_SESSION_MISMATCH"):
                self._execute(
                    plan,
                    current_session_id="new-current-session",
                    current_connection_epoch=1,
                    authorization=authorization,
                )

        self.assertTrue(path.exists())

    def test_authorized_execution_reprotects_incident_change_without_consuming(self) -> None:
        session_id = "authorization-incident-change"
        captured_at = self.NOW - timedelta(days=30)
        path = self._write(session_id, captured_at)
        plan = self._candidate_plan(path)
        authorization = self._authorize(plan)
        path.write_text(
            json.dumps(
                self._payload(session_id, captured_at, invalid_count=1),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            result = self._execute(plan, authorization=authorization)

        self.assertEqual(1, result["skipped_protected"])
        self.assertEqual(
            "INCIDENT_INVALID_CODES",
            self._execution_entry(result, path)["reason"],
        )
        self.assertTrue(path.exists())
        self.assertIn(
            "STATE: ISSUED",
            retention.format_production_diagnostics_retention_authorization_report(
                authorization
            ),
        )

    def test_authorized_execution_revalidates_signature_without_consuming(self) -> None:
        session_id = "authorization-signature-change"
        captured_at = self.NOW - timedelta(days=30)
        path = self._write(session_id, captured_at, marker="before")
        plan = self._candidate_plan(path)
        authorization = self._authorize(plan)
        path.write_text(
            json.dumps(
                self._payload(session_id, captured_at, marker="after"),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            result = self._execute(plan, authorization=authorization)

        self.assertEqual(1, result["skipped_changed"])
        self.assertTrue(path.exists())
        self.assertIn(
            "STATE: ISSUED",
            retention.format_production_diagnostics_retention_authorization_report(
                authorization
            ),
        )

    def test_forged_authorization_object_is_denied(self) -> None:
        path = self._write("authorization-forged", self.NOW - timedelta(days=30))
        plan = self._candidate_plan(path)
        authorization = self._authorize(plan)
        forged = replace(authorization, candidate_count=authorization.candidate_count + 1)

        with mock.patch.object(retention, "_is_production_diagnostics_root", return_value=True):
            with self.assertRaisesRegex(PermissionError, "AUTHORIZATION_UNKNOWN"):
                self._execute(plan, authorization=forged)

        self.assertTrue(path.exists())

    def test_authorization_candidate_count_and_bytes_are_bound(self) -> None:
        for field, reason in (
            ("candidate_count", "AUTHORIZATION_COUNT_MISMATCH"),
            ("candidate_bytes", "AUTHORIZATION_BYTES_MISMATCH"),
        ):
            with self.subTest(field=field):
                session_id = f"authorization-{field}"
                path = self._write(session_id, self.NOW - timedelta(days=30))
                plan = self._candidate_plan(path)
                authorization = self._authorize(plan)
                object.__setattr__(
                    authorization,
                    field,
                    getattr(authorization, field) + 1,
                )
                object.__setattr__(
                    authorization,
                    "authorization_signature",
                    retention._expected_authorization_signature(authorization),
                )

                with mock.patch.object(
                    retention,
                    "_is_production_diagnostics_root",
                    return_value=True,
                ):
                    with self.assertRaisesRegex(PermissionError, reason):
                        self._execute(plan, authorization=authorization)
                self.assertTrue(path.exists())

    def test_execution_report_contains_aggregate_outcomes(self) -> None:
        path = self._write("execute-report", self.NOW - timedelta(days=30))
        result = self._execute(self._candidate_plan(path), dry_run=True)

        report = retention.format_stock_library_diagnostic_retention_execution_report(result)

        for heading in (
            "PLANNED:",
            "DELETED:",
            "DRY_RUN_ONLY:",
            "SKIPPED_CHANGED:",
            "SKIPPED_PROTECTED:",
            "SKIPPED_UNSAFE:",
            "FAILED_IO:",
            "RECLAIMED_BYTES:",
        ):
            self.assertIn(heading, report)

    def test_production_module_has_only_single_file_unlink_mutation(self) -> None:
        source = Path(retention.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {
            "remove",
            "rmdir",
            "rmtree",
            "replace",
            "rename",
            "write_text",
            "write_bytes",
        }
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        unlink_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "unlink"
        ]
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        self.assertTrue(forbidden.isdisjoint(calls))
        self.assertEqual(1, len(unlink_calls))
        self.assertTrue({"gzip", "zipfile", "shutil"}.isdisjoint(imported_modules))


if __name__ == "__main__":
    unittest.main()
