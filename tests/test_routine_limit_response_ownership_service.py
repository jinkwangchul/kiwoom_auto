# -*- coding: utf-8 -*-

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from routine_limit_response_ownership_service import (
    INTENT_EARLY_CLOSE,
    INTENT_IMMEDIATE,
    RoutineLimitResponseOwnershipService,
    STATUS_COMPLETED,
    routine_limit_response_event_id,
)


ACCOUNT = "81291234"
DAY = "2026-08-21"
ROUTINE = "routine-1"


class RoutineLimitResponseOwnershipServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        runtime = Path(self.temp.name) / "runtime"
        runtime.mkdir()
        self.path = runtime / "routine_limit_response_ownership.json"
        self.service = RoutineLimitResponseOwnershipService(
            self.path,
            now_factory=lambda: "2026-08-21T10:00:00+09:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def claim(self, *, identity="EXEC-1", selected="005930", expected_revision=0):
        return self.service.claim(
            account_no=ACCOUNT,
            trading_day=DAY,
            routine_instance_id=ROUTINE,
            trigger_identity_source="BROKER_EXECUTION_ID",
            trigger_identity=identity,
            trigger_stock_code="005930",
            detected_at="2026-08-21T09:59:59+09:00",
            selected_stock_code=selected,
            configured_response_mode="구간마감",
            response_intent=INTENT_EARLY_CLOSE,
            expected_revision=expected_revision,
        )

    def test_missing_file_reads_as_empty_schema(self) -> None:
        result = self.service.read_snapshot()
        self.assertTrue(result["ok"])
        self.assertEqual(0, result["snapshot"]["revision"])
        self.assertEqual({}, result["snapshot"]["events"])

    def test_deterministic_identity_and_exactly_once_candidate(self) -> None:
        first = self.claim()
        duplicate = self.claim(selected="000660", expected_revision=1)
        self.assertTrue(first["changed"])
        self.assertFalse(duplicate["changed"])
        self.assertEqual(first["event_id"], duplicate["event_id"])
        self.assertEqual("005930", duplicate["event"]["selected_stock_code"])
        expected = routine_limit_response_event_id(
            account_no=ACCOUNT,
            trading_day=DAY,
            routine_instance_id=ROUTINE,
            trigger_identity_source="BROKER_EXECUTION_ID",
            trigger_identity="EXEC-1",
        )
        self.assertEqual(expected, first["event_id"])

    def test_one_active_event_per_routine_and_revision_cas(self) -> None:
        first = self.claim()
        competing = self.claim(identity="EXEC-2", selected="000660", expected_revision=1)
        stale = self.service.promote_to_immediate(
            event_id=first["event_id"], expected_revision=0
        )
        self.assertFalse(competing["changed"])
        self.assertEqual("ACTIVE_ROUTINE_EVENT_EXISTS", competing["operation"])
        self.assertFalse(stale["ok"])
        self.assertEqual("OWNERSHIP_REVISION_CONFLICT", stale["reason"])

    def test_concurrent_writers_commit_only_one_revision(self) -> None:
        other = RoutineLimitResponseOwnershipService(
            self.path,
            now_factory=lambda: "2026-08-21T10:00:00+09:00",
        )

        def claim(service, identity, selected):
            return service.claim(
                account_no=ACCOUNT,
                trading_day=DAY,
                routine_instance_id=ROUTINE,
                trigger_identity_source="BROKER_EXECUTION_ID",
                trigger_identity=identity,
                trigger_stock_code=selected,
                detected_at="2026-08-21T09:59:59+09:00",
                selected_stock_code=selected,
                configured_response_mode="조기마감",
                response_intent=INTENT_EARLY_CLOSE,
                expected_revision=0,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: claim(*args),
                    ((self.service, "EXEC-A", "005930"), (other, "EXEC-B", "000660")),
                )
            )
        self.assertEqual(1, sum(result.get("changed") is True for result in results))
        self.assertEqual(1, sum(result.get("reason") == "OWNERSHIP_REVISION_CONFLICT" for result in results))
        snapshot = self.service.read_snapshot()["snapshot"]
        self.assertEqual(1, snapshot["revision"])
        self.assertEqual(1, len(snapshot["events"]))

    def test_atomic_promotion_completion_and_completed_no_recreate(self) -> None:
        first = self.claim()
        promoted = self.service.promote_to_immediate(
            event_id=first["event_id"], expected_revision=1
        )
        completed = self.service.mark_completed(
            event_id=first["event_id"],
            evaluator_status="DONE",
            observed_at="2026-08-21T10:01:00+09:00",
            expected_revision=2,
        )
        duplicate = self.claim(expected_revision=3)
        self.assertEqual(INTENT_IMMEDIATE, promoted["event"]["response_intent"])
        self.assertEqual(STATUS_COMPLETED, completed["event"]["status"])
        self.assertFalse(duplicate["changed"])
        self.assertEqual("ALREADY_COMPLETED", duplicate["operation"])
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(3, persisted["revision"])
        self.assertEqual(completed["event"], persisted["events"][first["event_id"]])

    def test_strict_schema_rejects_unknown_root_and_event_fields(self) -> None:
        first = self.claim()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["unexpected"] = True
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(self.service.read_snapshot()["ok"])

        document.pop("unexpected")
        document["events"][first["event_id"]]["unexpected"] = True
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(self.service.read_snapshot()["ok"])

    def test_duplicate_json_keys_fail_closed_without_rewrite(self) -> None:
        content = b'{"schema_version":1,"schema_version":1,"revision":0,"updated_at":null,"events":{}}'
        self.path.write_bytes(content)
        result = self.service.read_snapshot()
        self.assertFalse(result["ok"])
        self.assertEqual(content, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
