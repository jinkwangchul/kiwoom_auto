# -*- coding: utf-8 -*-

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest

from buffer_response_ownership_service import (
    BATCH_SCHEMA_VERSION,
    BufferResponseOwnershipService,
    LEGACY_BATCH_SCHEMA_VERSION,
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_COMPLETED,
    STATUS_OWNED,
    buffer_response_event_id,
)


ACCOUNT = "81291234"
DAY = "2026-08-15"
SOURCE = "005930"
SELECTED_A = "000001"
SELECTED_B = "000002"
HASH_A = "A" * 64
HASH_B = "B" * 64
HASH_C = "C" * 64


def _batch_source(sequence: int) -> dict[str, object]:
    previous = 0 if sequence == 1 else 100
    current = sequence * 100
    contributors = [f"O{index}" for index in range(1, sequence + 1)]
    return {
        "observation_id": chr(64 + sequence) * 64,
        "observed_at": f"2026-08-15T10:0{sequence}:00+09:00",
        "previous_entry_amount": previous,
        "current_entry_amount": current,
        "new_contributing_buy_ids": [f"O{sequence}"],
        "contributing_buy_ids": contributors,
        "confirmed_evidence": {
            "recovery_session_id": "RECOVERY-1",
            "queue_revision": sequence,
            "order_queue_sha256": chr(67 + sequence) * 64,
            "positions_sha256": HASH_B,
            "fills_sha256": HASH_C,
        },
    }


def _complete_projection(code: str, status: str = "DONE") -> dict[str, object]:
    return {
        "evaluated": True,
        "blocked": False,
        "operation_date": DAY,
        "evidence_errors": [],
        "stock_results": [
            {
                "stock_code": code,
                "status": status,
                "reasons": [],
            }
        ],
    }


class BufferResponseOwnershipServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        runtime = Path(self.temp.name) / "runtime"
        runtime.mkdir()
        self.path = runtime / "buffer_response_ownership.json"
        self.service = BufferResponseOwnershipService(
            self.path,
            now_factory=lambda: "2026-08-15T10:00:00+09:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _claim(
        self,
        *,
        order_id: str = "BUY-ORDER-1",
        selected: str = SELECTED_A,
        expected_revision: int = 0,
        service: BufferResponseOwnershipService | None = None,
    ) -> dict[str, object]:
        target = service or self.service
        return target.claim_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            source_order_id=order_id,
            source_stock_code=SOURCE,
            selected_stock_code=selected,
            detected_at="2026-08-15T09:59:00+09:00",
            expected_revision=expected_revision,
        )

    def _snapshot(self) -> dict[str, object]:
        result = self.service.read_snapshot()
        self.assertTrue(result["ok"], result)
        return result["snapshot"]

    def test_missing_file_reads_empty_without_creating_file(self) -> None:
        result = self.service.read_snapshot()
        self.assertTrue(result["ok"])
        self.assertFalse(result["exists"])
        self.assertEqual(0, result["snapshot"]["revision"])
        self.assertEqual({}, result["snapshot"]["events"])
        self.assertFalse(self.path.exists())

    def test_first_claim_atomically_creates_owned_event_without_pending(self) -> None:
        result = self._claim()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["changed"])
        self.assertEqual("CLAIMED", result["operation"])
        snapshot = self._snapshot()
        self.assertEqual(1, snapshot["revision"])
        event = next(iter(snapshot["events"].values()))
        self.assertEqual(STATUS_OWNED, event["status"])
        self.assertEqual(SELECTED_A, event["selected_stock_code"])
        self.assertNotIn("PENDING", self.path.read_text(encoding="utf-8"))

    def test_same_event_same_candidate_is_idempotent(self) -> None:
        first = self._claim()
        before = self.path.read_bytes()
        second = self._claim(selected=SELECTED_A, expected_revision=0)
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertFalse(second["changed"])
        self.assertEqual("ALREADY_OWNED", second["operation"])
        self.assertEqual(SELECTED_A, second["selected_stock_code"])
        self.assertEqual(before, self.path.read_bytes())

    def test_same_event_different_candidate_cannot_reassign_to_b(self) -> None:
        self._claim(selected=SELECTED_A)
        result = self._claim(selected=SELECTED_B, expected_revision=0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertTrue(result["proposal_ignored"])
        self.assertEqual(SELECTED_A, result["selected_stock_code"])
        self.assertEqual(SELECTED_A, next(iter(self._snapshot()["events"].values()))["selected_stock_code"])

    def test_new_source_order_is_a_distinct_event(self) -> None:
        e1 = self._claim(order_id="BUY-ORDER-1", selected=SELECTED_A)
        e2 = self._claim(order_id="BUY-ORDER-2", selected=SELECTED_B, expected_revision=1)
        self.assertNotEqual(e1["event_id"], e2["event_id"])
        snapshot = self._snapshot()
        self.assertEqual(2, snapshot["revision"])
        self.assertEqual(2, len(snapshot["events"]))

    def test_same_order_lifecycle_has_one_deterministic_identity(self) -> None:
        reserved = buffer_response_event_id(
            account_no=ACCOUNT, trading_day=DAY, source_order_id="BUY-ORDER-1"
        )
        partial_fill = buffer_response_event_id(
            account_no=f" {ACCOUNT} ", trading_day=DAY, source_order_id="BUY-ORDER-1"
        )
        full_fill = buffer_response_event_id(
            account_no=ACCOUNT, trading_day=DAY, source_order_id=" BUY-ORDER-1 "
        )
        other_order = buffer_response_event_id(
            account_no=ACCOUNT, trading_day=DAY, source_order_id="BUY-ORDER-2"
        )
        self.assertEqual(reserved, partial_fill)
        self.assertEqual(reserved, full_fill)
        self.assertNotEqual(reserved, other_order)

    def test_revision_mismatch_blocks_without_creating_file(self) -> None:
        result = self._claim(expected_revision=7)
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["changed"])
        self.assertFalse(self.path.exists())

    def test_missing_selected_candidate_blocks_without_creating_event(self) -> None:
        result = self._claim(selected="")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertFalse(self.path.exists())

    def test_corrupt_and_duplicate_key_documents_fail_closed_without_rewrite(self) -> None:
        invalid_documents = (
            b"{not-json",
            b'{"schema_version":1,"schema_version":1,"revision":0,"updated_at":null,"events":{}}',
            b'{"schema_version":4,"revision":0,"updated_at":null,"events":{}}',
        )
        for content in invalid_documents:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                result = self.service.read_snapshot()
                self.assertFalse(result["ok"])
                self.assertTrue(result["blocked"])
                claim = self._claim()
                self.assertFalse(claim["ok"])
                self.assertEqual(content, self.path.read_bytes())

    def test_inconsistent_event_and_status_schemas_fail_closed(self) -> None:
        claimed = self._claim()
        valid = json.loads(self.path.read_text(encoding="utf-8"))
        event_id = claimed["event_id"]
        variants = []

        invalid_revision = json.loads(json.dumps(valid))
        invalid_revision["revision"] = -1
        variants.append(invalid_revision)

        identity_mismatch = json.loads(json.dumps(valid))
        identity_mismatch["events"][event_id]["source_order_id"] = "OTHER-ORDER"
        variants.append(identity_mismatch)

        missing_owner = json.loads(json.dumps(valid))
        missing_owner["events"][event_id]["selected_stock_code"] = ""
        variants.append(missing_owner)

        owned_with_completion = json.loads(json.dumps(valid))
        owned_with_completion["events"][event_id]["completion"] = {
            "evaluator_status": "DONE",
            "observed_at": "2026-08-15T10:10:00+09:00",
        }
        variants.append(owned_with_completion)

        completed_without_evidence = json.loads(json.dumps(valid))
        completed_without_evidence["events"][event_id]["status"] = STATUS_COMPLETED
        variants.append(completed_without_evidence)

        for document in variants:
            with self.subTest(document=document):
                content = json.dumps(document).encode("utf-8")
                self.path.write_bytes(content)
                result = self.service.read_snapshot()
                self.assertFalse(result["ok"])
                self.assertTrue(result["blocked"])
                self.assertEqual(content, self.path.read_bytes())

    def test_restart_restores_same_owned_target_and_active_set(self) -> None:
        first = self._claim()
        restarted = BufferResponseOwnershipService(
            self.path, now_factory=lambda: "2026-08-15T10:01:00+09:00"
        )
        snapshot = restarted.read_snapshot()
        self.assertTrue(snapshot["ok"])
        event = snapshot["snapshot"]["events"][first["event_id"]]
        self.assertEqual(SELECTED_A, event["selected_stock_code"])
        active = restarted.active_owned_stock_codes(account_no=ACCOUNT, trading_day=DAY)
        self.assertEqual((SELECTED_A,), active["stock_codes"])
        other_day = restarted.active_owned_stock_codes(
            account_no=ACCOUNT, trading_day="2026-08-14"
        )
        self.assertEqual((), other_day["stock_codes"])

    def test_mark_completed_requires_canonical_completion_evidence(self) -> None:
        claimed = self._claim()
        bad_cases = (
            {},
            {**_complete_projection(SELECTED_A), "evaluated": False},
            {**_complete_projection(SELECTED_A), "blocked": True},
            {**_complete_projection(SELECTED_A), "operation_date": "2026-08-14"},
            {**_complete_projection(SELECTED_A), "evidence_errors": ["positions unavailable"]},
            _complete_projection(SELECTED_A, status="REQUESTED"),
            _complete_projection(SELECTED_B),
        )
        for projection in bad_cases:
            with self.subTest(projection=projection):
                result = self.service.mark_completed(
                    event_id=claimed["event_id"],
                    completion_projection=projection,
                    expected_revision=1,
                )
                self.assertFalse(result["ok"])
                self.assertTrue(result["blocked"])
                self.assertEqual(STATUS_OWNED, self._snapshot()["events"][claimed["event_id"]]["status"])

    def test_done_and_carryover_done_are_canonical_completion_statuses(self) -> None:
        for status in ("DONE", "CARRYOVER_DONE"):
            with self.subTest(status=status):
                if self.path.exists():
                    self.path.unlink()
                claimed = self._claim()
                result = self.service.mark_completed(
                    event_id=claimed["event_id"],
                    completion_projection=_complete_projection(SELECTED_A, status=status),
                    expected_revision=1,
                    observed_at="2026-08-15T10:10:00+09:00",
                )
                self.assertTrue(result["ok"], result)
                self.assertEqual(STATUS_COMPLETED, result["status"])
                self.assertEqual(2, result["revision_after"])
                active = self.service.active_owned_stock_codes(account_no=ACCOUNT, trading_day=DAY)
                self.assertEqual((), active["stock_codes"])

    def test_early_close_promotion_preserves_identity_and_is_idempotent(self) -> None:
        claimed = self.service.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence=_batch_source(1),
            selected_stock_code=SELECTED_A,
            response_intent=RESPONSE_INTENT_EARLY_CLOSE,
            detected_at="2026-08-15T10:01:00+09:00",
            expected_revision=0,
        )
        self.assertTrue(claimed["ok"], claimed)
        event_id = claimed["event_id"]
        promoted = self.service.promote_owned_early_close_to_immediate(
            event_id=event_id,
            expected_revision=1,
        )
        self.assertTrue(promoted["changed"], promoted)
        self.assertEqual(SELECTED_A, promoted["selected_stock_code"])
        self.assertEqual(
            RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
            promoted["response_intent"],
        )
        repeated = self.service.promote_owned_early_close_to_immediate(
            event_id=event_id,
            expected_revision=2,
        )
        self.assertFalse(repeated["changed"], repeated)
        self.assertEqual("ALREADY_PROMOTED", repeated["operation"])
        snapshot = self._snapshot()
        self.assertEqual(1, len(snapshot["events"]))
        self.assertEqual(event_id, snapshot["events"][event_id]["event_id"])
        self.assertEqual(SELECTED_A, snapshot["events"][event_id]["selected_stock_code"])

    def test_completed_event_cannot_be_reassigned(self) -> None:
        claimed = self._claim()
        self.service.mark_completed(
            event_id=claimed["event_id"],
            completion_projection=_complete_projection(SELECTED_A),
            expected_revision=1,
        )
        result = self._claim(selected=SELECTED_B, expected_revision=0)
        self.assertEqual("ALREADY_COMPLETED", result["operation"])
        self.assertEqual(SELECTED_A, result["selected_stock_code"])
        self.assertFalse(result["changed"])

    def test_concurrent_claims_create_one_event_with_one_stable_owner(self) -> None:
        barrier = threading.Barrier(2)

        def run(selected: str) -> dict[str, object]:
            service = BufferResponseOwnershipService(
                self.path, now_factory=lambda: "2026-08-15T10:00:00+09:00"
            )
            barrier.wait()
            return self._claim(selected=selected, service=service)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (SELECTED_A, SELECTED_B)))
        self.assertTrue(all(result["ok"] for result in results), results)
        selected = {result["selected_stock_code"] for result in results}
        self.assertEqual(1, len(selected))
        snapshot = self._snapshot()
        self.assertEqual(1, snapshot["revision"])
        self.assertEqual(1, len(snapshot["events"]))
        self.assertEqual(selected.pop(), next(iter(snapshot["events"].values()))["selected_stock_code"])

    def test_schema_is_exact_and_contains_no_extra_runtime_state(self) -> None:
        claimed = self._claim()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema_version", "revision", "updated_at", "events"}, set(document)
        )
        event = document["events"][claimed["event_id"]]
        self.assertEqual(
            {
                "event_id", "account_no", "trading_day", "source_order_id",
                "source_stock_code", "detected_at", "status",
                "selected_stock_code", "selected_at", "completion",
            },
            set(event),
        )


class BufferResponseIntentOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime" / "buffer_response_ownership.json"
        self.path.parent.mkdir()
        self.service = BufferResponseOwnershipService(
            self.path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _claim(
        self,
        *,
        sequence: int = 1,
        selected: str = SELECTED_A,
        intent: str = RESPONSE_INTENT_EARLY_CLOSE,
        expected_revision: int = 0,
        service: BufferResponseOwnershipService | None = None,
    ) -> dict[str, object]:
        target = service or self.service
        return target.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=sequence,
            source_evidence=_batch_source(sequence),
            selected_stock_code=selected,
            response_intent=intent,
            detected_at=f"2026-08-15T10:0{sequence}:00+09:00",
            expected_revision=expected_revision,
        )

    def test_target_and_intent_are_one_immutable_restart_safe_claim(self) -> None:
        first = self._claim()
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["changed"])
        self.assertEqual(SELECTED_A, first["selected_stock_code"])
        self.assertEqual(RESPONSE_INTENT_EARLY_CLOSE, first["response_intent"])
        before = self.path.read_bytes()

        same = self._claim()
        changed_intent = self._claim(
            intent=RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
        )
        changed_target = self._claim(selected=SELECTED_B)
        changed_both = self._claim(
            selected=SELECTED_B,
            intent=RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
        )
        for result in (same, changed_intent, changed_target, changed_both):
            self.assertTrue(result["ok"], result)
            self.assertFalse(result["changed"])
            self.assertEqual(SELECTED_A, result["selected_stock_code"])
            self.assertEqual(RESPONSE_INTENT_EARLY_CLOSE, result["response_intent"])
        self.assertTrue(changed_intent["intent_proposal_ignored"])
        self.assertTrue(changed_target["proposal_ignored"])
        self.assertTrue(changed_both["proposal_ignored"])
        self.assertTrue(changed_both["intent_proposal_ignored"])
        self.assertEqual(before, self.path.read_bytes())

        restarted = BufferResponseOwnershipService(self.path)
        snapshot = restarted.read_snapshot()
        self.assertTrue(snapshot["ok"], snapshot)
        event = next(iter(snapshot["snapshot"]["events"].values()))
        self.assertEqual(SELECTED_A, event["selected_stock_code"])
        self.assertEqual(RESPONSE_INTENT_EARLY_CLOSE, event["response_intent"])

    def test_new_event_uses_new_current_intent(self) -> None:
        e1 = self._claim()
        e2 = self._claim(
            sequence=2,
            selected=SELECTED_B,
            intent=RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
            expected_revision=1,
        )
        self.assertTrue(e1["ok"], e1)
        self.assertTrue(e2["ok"], e2)
        self.assertEqual(RESPONSE_INTENT_EARLY_CLOSE, e1["response_intent"])
        self.assertEqual(
            RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
            e2["response_intent"],
        )
        snapshot = self.service.read_snapshot()["snapshot"]
        self.assertEqual(BATCH_SCHEMA_VERSION, snapshot["schema_version"])
        self.assertEqual(2, len(snapshot["events"]))

    def test_concurrent_target_and_intent_proposals_return_one_atomic_pair(self) -> None:
        barrier = threading.Barrier(2)

        def run(proposal: tuple[str, str]) -> dict[str, object]:
            selected, intent = proposal
            service = BufferResponseOwnershipService(
                self.path,
                now_factory=lambda: "2026-08-15T10:30:00+09:00",
            )
            barrier.wait()
            return self._claim(selected=selected, intent=intent, service=service)

        proposals = (
            (SELECTED_A, RESPONSE_INTENT_EARLY_CLOSE),
            (SELECTED_B, RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, proposals))
        self.assertTrue(all(result["ok"] for result in results), results)
        pairs = {
            (result["selected_stock_code"], result["response_intent"])
            for result in results
        }
        self.assertEqual(1, len(pairs))
        event = next(iter(self.service.read_snapshot()["snapshot"]["events"].values()))
        self.assertEqual(pairs.pop(), (event["selected_stock_code"], event["response_intent"]))

    def test_legacy_v2_event_reads_without_inferred_intent(self) -> None:
        current = self._claim()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["schema_version"] = LEGACY_BATCH_SCHEMA_VERSION
        document["events"][current["event_id"]].pop("response_intent")
        self.path.write_text(json.dumps(document), encoding="utf-8")

        read = self.service.read_snapshot()
        self.assertTrue(read["ok"], read)
        event = read["snapshot"]["events"][current["event_id"]]
        self.assertNotIn("response_intent", event)
        blocked = self._claim(sequence=2, expected_revision=1)
        self.assertFalse(blocked["ok"])
        self.assertNotIn(
            "response_intent",
            self.service.read_snapshot()["snapshot"]["events"][current["event_id"]],
        )


if __name__ == "__main__":
    unittest.main()
