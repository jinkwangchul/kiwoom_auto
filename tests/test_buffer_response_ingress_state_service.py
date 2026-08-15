# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from buffer_response_ingress_state_service import (
    BufferResponseIngressStateService,
    build_stable_buffer_observation,
    collect_confirmed_contributing_buy_ids,
)
from buffer_response_ownership_service import BufferResponseOwnershipService


ACCOUNT = "81291234"
DAY = "2026-08-15"
SELECTED_A = "000001"
SELECTED_B = "000002"
HASH_A = "A" * 64
HASH_B = "B" * 64
HASH_C = "C" * 64


def _evidence(revision: int = 1, suffix: str = "A") -> dict[str, object]:
    digest = suffix * 64
    return {
        "recovery_session_id": "RECOVERY-SESSION-1",
        "queue_revision": revision,
        "order_queue_sha256": digest,
        "positions_sha256": HASH_B,
        "fills_sha256": HASH_C,
    }


def _observation(
    amount: int,
    contributors: tuple[str, ...] = (),
    *,
    revision: int = 1,
    suffix: str = "A",
    observed_at: str = "2026-08-15T10:00:00+09:00",
) -> dict[str, object]:
    evidence = _evidence(revision, suffix)
    result = build_stable_buffer_observation(
        account_no=ACCOUNT,
        trading_day=DAY,
        confirmed_entry_amount=amount,
        contributing_buy_ids=contributors,
        evidence_before=evidence,
        evidence_after=deepcopy(evidence),
        observed_at=observed_at,
    )
    assert result["available"] is True, result
    return result["observation"]


def _batch_source(preview: dict[str, object]) -> dict[str, object]:
    observation = preview["observation"]
    return {
        "observation_id": observation["observation_id"],
        "observed_at": observation["observed_at"],
        "previous_entry_amount": preview["previous_entry_amount"],
        "current_entry_amount": observation["confirmed_entry_amount"],
        "new_contributing_buy_ids": preview["new_contributing_buy_ids"],
        "contributing_buy_ids": observation["contributing_buy_ids"],
        "confirmed_evidence": observation["evidence"],
    }


class BufferResponseIngressStateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        runtime = Path(self.temp.name) / "runtime"
        runtime.mkdir()
        self.ingress_path = runtime / "buffer_response_ingress_state.json"
        self.ownership_path = runtime / "buffer_response_ownership.json"
        self.ingress = BufferResponseIngressStateService(
            self.ingress_path,
            now_factory=lambda: "2026-08-15T10:05:00+09:00",
        )
        self.ownership = BufferResponseOwnershipService(
            self.ownership_path,
            now_factory=lambda: "2026-08-15T10:05:00+09:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _commit_baseline(self, amount: int = 0, contributors: tuple[str, ...] = ()) -> dict[str, object]:
        observation = _observation(amount, contributors)
        preview = self.ingress.preview_observation(observation)
        self.assertFalse(preview["event_required"], preview)
        result = self.ingress.commit_stable_observation(
            observation=observation,
            expected_revision=preview["expected_revision"],
        )
        self.assertTrue(result["ok"], result)
        return result

    def _claim_preview(
        self,
        preview: dict[str, object],
        *,
        selected: str = SELECTED_A,
        expected_revision: int = 0,
    ) -> dict[str, object]:
        observation = preview["observation"]
        result = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=preview["event_sequence"],
            source_evidence=_batch_source(preview),
            selected_stock_code=selected,
            response_intent="EARLY_CLOSE",
            detected_at=observation["observed_at"],
            expected_revision=expected_revision,
        )
        self.assertTrue(result["ok"], result)
        return result

    def _commit_event(
        self,
        observation: dict[str, object],
        *,
        ownership_revision: int,
    ) -> tuple[dict[str, object], dict[str, object]]:
        preview = self.ingress.preview_observation(observation)
        self.assertTrue(preview["event_required"], preview)
        claimed = self._claim_preview(preview, expected_revision=ownership_revision)
        committed = self.ingress.commit_event_observation(
            observation=observation,
            claimed_event=claimed["event"],
            expected_revision=preview["expected_revision"],
        )
        self.assertTrue(committed["ok"], committed)
        return claimed, committed

    def test_missing_file_reads_empty_without_creating_file(self) -> None:
        result = self.ingress.read_snapshot()
        self.assertTrue(result["ok"])
        self.assertFalse(result["exists"])
        self.assertEqual({}, result["snapshot"]["checkpoints"])
        self.assertFalse(self.ingress_path.exists())

    def test_first_stable_zero_creates_only_baseline_and_same_value_is_no_event(self) -> None:
        self._commit_baseline()
        preview = self.ingress.preview_observation(_observation(0))
        self.assertFalse(preview["event_required"])
        self.assertEqual(0, preview["event_sequence"])

    def test_sequential_contributors_create_e1_and_e2(self) -> None:
        self._commit_baseline()
        e1, _ = self._commit_event(_observation(100, ("O1",), revision=2, suffix="D"), ownership_revision=0)
        e2, _ = self._commit_event(_observation(200, ("O1", "O2"), revision=3, suffix="E"), ownership_revision=1)
        self.assertEqual(1, e1["event"]["event_sequence"])
        self.assertEqual(2, e2["event"]["event_sequence"])

    def test_same_buy_lifecycle_increase_has_no_event_but_updates_checkpoint(self) -> None:
        self._commit_baseline()
        self._commit_event(_observation(100, ("O1",), revision=2, suffix="D"), ownership_revision=0)
        observation = _observation(101, ("O1",), revision=3, suffix="E")
        preview = self.ingress.preview_observation(observation)
        self.assertFalse(preview["event_required"])
        result = self.ingress.commit_stable_observation(
            observation=observation, expected_revision=preview["expected_revision"]
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(101, result["checkpoint"]["last_confirmed_entry_amount"])
        self.assertEqual(1, result["checkpoint"]["last_event_sequence"])

    def test_unclaimed_confirmed_event_is_consumed_once_and_identical_replay_is_noop(self) -> None:
        self._commit_baseline()
        observation = _observation(100, ("O1",), revision=2, suffix="D")
        preview = self.ingress.preview_observation(observation)
        self.assertTrue(preview["event_required"], preview)
        consumed = self.ingress.commit_unclaimed_event_observation(
            observation=observation,
            expected_revision=preview["expected_revision"],
        )
        self.assertTrue(consumed["ok"], consumed)
        self.assertEqual(1, consumed["checkpoint"]["last_event_sequence"])
        revision = consumed["revision_after"]

        later_tick = _observation(
            100,
            ("O1",),
            revision=2,
            suffix="D",
            observed_at="2026-08-15T10:01:00+09:00",
        )
        self.assertEqual(observation["observation_id"], later_tick["observation_id"])
        replay = self.ingress.preview_observation(later_tick)
        self.assertFalse(replay["event_required"], replay)
        noop = self.ingress.commit_stable_observation(
            observation=later_tick,
            expected_revision=replay["expected_revision"],
        )
        self.assertTrue(noop["ok"], noop)
        self.assertFalse(noop["changed"], noop)
        self.assertEqual("OBSERVATION_ALREADY_COMMITTED", noop["operation"])
        self.assertEqual(revision, noop["revision_after"])

    def test_uncontributing_queue_id_is_not_seen_then_contribution_can_create_event(self) -> None:
        empty = {"positions": []}
        queue = {
            "orders": [
                {
                    "id": "O2",
                    "status": "ORDER_QUEUED",
                    "account_no": ACCOUNT,
                    "side": "BUY",
                    "code": "005930",
                    "source_signal_id": "SIGNAL-O2",
                }
            ]
        }
        contributors = collect_confirmed_contributing_buy_ids(
            account_no=ACCOUNT,
            positions_snapshot=empty,
            order_queue_snapshot=queue,
            fills_snapshot={"fills": []},
            reconciled_stock_codes=("005930",),
        )
        self.assertTrue(contributors["available"], contributors)
        self.assertEqual([], contributors["contributing_buy_ids"])
        self._commit_baseline()
        queue["orders"][0].update(
            {
                "status": "BROKER_ACCEPTED",
                "broker_order_no": "12345",
                "remaining_quantity": 10,
                "order_price": 1000,
            }
        )
        contributors = collect_confirmed_contributing_buy_ids(
            account_no=ACCOUNT,
            positions_snapshot=empty,
            order_queue_snapshot=queue,
            fills_snapshot={"fills": []},
            reconciled_stock_codes=("005930",),
        )
        self.assertEqual(["O2"], contributors["contributing_buy_ids"])
        preview = self.ingress.preview_observation(_observation(100, tuple(contributors["contributing_buy_ids"]), revision=2, suffix="D"))
        self.assertTrue(preview["event_required"])
        self.assertEqual(["O2"], preview["new_contributing_buy_ids"])

    def test_position_contributor_requires_buy_fill_applied_to_position(self) -> None:
        positions = {
            "positions": [
                {
                    "account_no": ACCOUNT,
                    "code": "005930",
                    "quantity": 10,
                    "cost_basis": 10000,
                    "last_applied_cumulative_by_order": {"order_queued_id:O1": 10},
                }
            ]
        }
        fills = {
            "fills": [
                {
                    "account_no": ACCOUNT,
                    "code": "005930",
                    "side": "BUY",
                    "order_queued_id": "O1",
                }
            ]
        }
        result = collect_confirmed_contributing_buy_ids(
            account_no=ACCOUNT,
            positions_snapshot=positions,
            order_queue_snapshot={"orders": []},
            fills_snapshot=fills,
            reconciled_stock_codes=("005930",),
        )
        self.assertTrue(result["available"], result)
        self.assertEqual(["O1"], result["position_contributing_buy_ids"])
        missing_fill = collect_confirmed_contributing_buy_ids(
            account_no=ACCOUNT,
            positions_snapshot=positions,
            order_queue_snapshot={"orders": []},
            fills_snapshot={"fills": []},
            reconciled_stock_codes=("005930",),
        )
        self.assertFalse(missing_fill["available"])

    def test_unresolved_send_or_missing_contributor_evidence_is_fail_closed(self) -> None:
        unresolved = collect_confirmed_contributing_buy_ids(
            account_no=ACCOUNT,
            positions_snapshot={"positions": []},
            order_queue_snapshot={
                "orders": [
                    {
                        "id": "O1",
                        "status": "SEND_CALL_ACCEPTED",
                        "account_no": ACCOUNT,
                        "side": "BUY",
                        "code": "005930",
                    }
                ]
            },
            fills_snapshot={"fills": []},
            reconciled_stock_codes=("005930",),
        )
        self.assertFalse(unresolved["available"])
        missing_account = collect_confirmed_contributing_buy_ids(
            account_no=ACCOUNT,
            positions_snapshot={"positions": []},
            order_queue_snapshot={
                "orders": [
                    {
                        "id": "O1",
                        "status": "BROKER_ACCEPTED",
                        "side": "BUY",
                        "code": "005930",
                    }
                ]
            },
            fills_snapshot={"fills": []},
            reconciled_stock_codes=("005930",),
        )
        self.assertFalse(missing_account["available"])

    def test_simultaneous_buy_ids_create_one_batch_with_both_sources(self) -> None:
        self._commit_baseline()
        observation = _observation(300, ("O1", "O2"), revision=2, suffix="D")
        preview = self.ingress.preview_observation(observation)
        self.assertEqual(1, preview["event_sequence"])
        self.assertEqual(["O1", "O2"], preview["new_contributing_buy_ids"])
        claimed = self._claim_preview(preview)
        self.assertEqual(["O1", "O2"], claimed["event"]["source_evidence"]["new_contributing_buy_ids"])
        self.assertEqual(1, len(self.ownership.read_snapshot()["snapshot"]["events"]))

    def test_decreases_commit_without_new_events(self) -> None:
        self._commit_baseline()
        self._commit_event(_observation(300, ("O1",), revision=2, suffix="D"), ownership_revision=0)
        for amount, revision, suffix in ((200, 3, "E"), (0, 4, "F")):
            observation = _observation(amount, ("O1",), revision=revision, suffix=suffix)
            preview = self.ingress.preview_observation(observation)
            self.assertFalse(preview["event_required"])
            committed = self.ingress.commit_stable_observation(
                observation=observation, expected_revision=preview["expected_revision"]
            )
            self.assertEqual(1, committed["checkpoint"]["last_event_sequence"])

    def test_restart_same_value_no_event_and_new_contributor_advances_sequence(self) -> None:
        self._commit_baseline()
        self._commit_event(_observation(300, ("O1",), revision=2, suffix="D"), ownership_revision=0)
        restarted = BufferResponseIngressStateService(self.ingress_path)
        same = restarted.preview_observation(_observation(300, ("O1",), revision=2, suffix="D"))
        self.assertFalse(same["event_required"])
        later = restarted.preview_observation(_observation(500, ("O1", "O3"), revision=3, suffix="E"))
        self.assertTrue(later["event_required"])
        self.assertEqual(2, later["event_sequence"])

    def test_crash_bridge_recovers_captured_e1_before_current_e2(self) -> None:
        self._commit_baseline()
        e1_observation = _observation(300, ("O1", "O2"), revision=2, suffix="D")
        e1_preview = self.ingress.preview_observation(e1_observation)
        claimed = self._claim_preview(e1_preview)
        restarted = BufferResponseIngressStateService(
            self.ingress_path,
            now_factory=lambda: "2026-08-15T10:06:00+09:00",
        )
        recovered = restarted.recover_claimed_event_checkpoint(
            claimed_event=claimed["event"], expected_revision=1
        )
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(300, recovered["checkpoint"]["last_confirmed_entry_amount"])
        current = restarted.preview_observation(
            _observation(500, ("O1", "O2", "O3"), revision=3, suffix="E")
        )
        self.assertTrue(current["event_required"])
        self.assertEqual(2, current["event_sequence"])
        self.assertEqual(["O3"], current["new_contributing_buy_ids"])

    def test_crash_bridge_gap_corruption_and_revision_mismatch_fail_closed(self) -> None:
        self._commit_baseline()
        observation = _observation(300, ("O1",), revision=2, suffix="D")
        preview = self.ingress.preview_observation(observation)
        gap = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=2,
            source_evidence=_batch_source({**preview, "event_sequence": 2}),
            selected_stock_code=SELECTED_A,
            response_intent="EARLY_CLOSE",
            detected_at=observation["observed_at"],
            expected_revision=0,
        )
        self.assertTrue(gap["ok"], gap)
        blocked_gap = self.ingress.recover_claimed_event_checkpoint(
            claimed_event=gap["event"], expected_revision=1
        )
        self.assertFalse(blocked_gap["ok"])
        corrupted = deepcopy(gap["event"])
        corrupted["source_evidence"]["observation_id"] = "0" * 64
        blocked_corrupt = self.ingress.recover_claimed_event_checkpoint(
            claimed_event=corrupted, expected_revision=1
        )
        self.assertFalse(blocked_corrupt["ok"])
        blocked_revision = self.ingress.recover_claimed_event_checkpoint(
            claimed_event=gap["event"], expected_revision=9
        )
        self.assertFalse(blocked_revision["ok"])

    def test_double_snapshot_change_is_fail_closed(self) -> None:
        result = build_stable_buffer_observation(
            account_no=ACCOUNT,
            trading_day=DAY,
            confirmed_entry_amount=100,
            contributing_buy_ids=("O1",),
            evidence_before=_evidence(1, "A"),
            evidence_after=_evidence(2, "D"),
            observed_at="2026-08-15T10:00:00+09:00",
        )
        self.assertFalse(result["available"])
        self.assertTrue(result["blocked"])

    def test_v1_v2_read_and_v3_intent_batch_claim_compatibility(self) -> None:
        legacy_path = Path(self.temp.name) / "runtime" / "legacy_ownership.json"
        legacy = BufferResponseOwnershipService(legacy_path)
        legacy_claim = legacy.claim_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            source_order_id="BUY-1",
            source_stock_code="005930",
            selected_stock_code=SELECTED_A,
            detected_at="2026-08-15T10:00:00+09:00",
            expected_revision=0,
        )
        self.assertTrue(legacy_claim["ok"], legacy_claim)
        self.assertEqual(1, legacy.read_snapshot()["snapshot"]["schema_version"])

        blocked_batch_migration = legacy.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence={
                "observation_id": HASH_A,
                "observed_at": "2026-08-15T10:00:00+09:00",
                "previous_entry_amount": 0,
                "current_entry_amount": 100,
                "new_contributing_buy_ids": ["O1"],
                "contributing_buy_ids": ["O1"],
                "confirmed_evidence": _evidence(),
            },
            selected_stock_code=SELECTED_A,
            response_intent="EARLY_CLOSE",
            detected_at="2026-08-15T10:00:00+09:00",
            expected_revision=1,
        )
        self.assertFalse(blocked_batch_migration["ok"])
        self.assertEqual(1, legacy.read_snapshot()["snapshot"]["schema_version"])

        self._commit_baseline()
        preview = self.ingress.preview_observation(
            _observation(100, ("O1",), revision=2, suffix="D")
        )
        first = self._claim_preview(preview)
        current_snapshot = self.ownership.read_snapshot()["snapshot"]
        self.assertEqual(3, current_snapshot["schema_version"])
        again = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence=_batch_source(preview),
            selected_stock_code=SELECTED_B,
            response_intent="IMMEDIATE_LIQUIDATION_REQUIRED",
            detected_at=preview["observation"]["observed_at"],
            expected_revision=0,
        )
        self.assertTrue(again["ok"], again)
        self.assertFalse(again["changed"])
        self.assertEqual(first["selected_stock_code"], again["selected_stock_code"])
        conflicting_source = _batch_source(preview)
        conflicting_source["current_entry_amount"] = 101
        conflict = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence=conflicting_source,
            selected_stock_code=SELECTED_A,
            response_intent="EARLY_CLOSE",
            detected_at=preview["observation"]["observed_at"],
            expected_revision=1,
        )
        self.assertFalse(conflict["ok"])

        legacy_batch_path = Path(self.temp.name) / "runtime" / "legacy_batch.json"
        legacy_batch_document = deepcopy(current_snapshot)
        legacy_batch_document["schema_version"] = 2
        for event in legacy_batch_document["events"].values():
            event.pop("response_intent")
        legacy_batch_path.write_text(
            json.dumps(legacy_batch_document), encoding="utf-8"
        )
        legacy_batch = BufferResponseOwnershipService(legacy_batch_path)
        legacy_read = legacy_batch.read_snapshot()
        self.assertTrue(legacy_read["ok"], legacy_read)
        self.assertEqual(2, legacy_read["snapshot"]["schema_version"])
        legacy_event = next(iter(legacy_read["snapshot"]["events"].values()))
        self.assertNotIn("response_intent", legacy_event)
        blocked_legacy_batch_write = legacy_batch.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=2,
            source_evidence=_batch_source({**preview, "event_sequence": 2}),
            selected_stock_code=SELECTED_B,
            response_intent="EARLY_CLOSE",
            detected_at=preview["observation"]["observed_at"],
            expected_revision=legacy_batch_document["revision"],
        )
        self.assertFalse(blocked_legacy_batch_write["ok"])
        self.assertEqual(
            legacy_batch_document,
            json.loads(legacy_batch_path.read_text(encoding="utf-8")),
        )

    def test_ingress_schema_contains_only_checkpoint_state(self) -> None:
        self._commit_baseline()
        document = json.loads(self.ingress_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema_version", "revision", "updated_at", "checkpoints"}, set(document)
        )
        checkpoint = next(iter(document["checkpoints"].values()))
        self.assertEqual(
            {
                "account_no",
                "trading_day",
                "last_confirmed_entry_amount",
                "last_confirmed_observation_id",
                "last_confirmed_observed_at",
                "last_confirmed_evidence",
                "seen_contributing_buy_ids",
                "last_event_sequence",
            },
            set(checkpoint),
        )
        forbidden = {"candidate", "selected_stock_code", "pnl", "policy", "positions", "orders"}
        self.assertTrue(forbidden.isdisjoint(checkpoint))


if __name__ == "__main__":
    unittest.main()
