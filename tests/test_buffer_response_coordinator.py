# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import buffer_response_coordinator as coordinator_module
from buffer_response_coordinator import BufferResponseCoordinator
from buffer_response_early_close_dispatcher import (
    buffer_response_command_source,
    deterministic_buffer_early_close_command_id,
)
from buffer_response_ingress_state_service import (
    BufferResponseIngressStateService,
    build_stable_buffer_observation,
)
from buffer_response_ownership_service import (
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_COMPLETED,
    STATUS_OWNED,
    BufferResponseOwnershipService,
)
from close_liquidation_transition_service import POLICY_ROUTINE_CLOSE


ACCOUNT = "81291234"
DAY = "2026-08-15"


class _Control:
    def __init__(self, text: str) -> None:
        self.value = text

    def currentText(self) -> str:
        return self.value

    def text(self) -> str:
        return self.value


class _Surface:
    def __init__(self, threshold: int = 80, response_label: str | None = None) -> None:
        self.buffer_close_ratio_combo = _Control(f"{threshold}%")
        self.strategy_rows = {
            "unified": [(_Control("손익금액"), _Control("높은순"))]
        }
        self.strategy_action_badges = {"unified": _Control("구간마감")}

        if response_label is not None:
            self.strategy_action_badges["unified"].value = response_label

    def isVisible(self) -> bool:
        return True

    def application_mode(self) -> str:
        return "UNIFIED"


def _evidence(revision: int, marker: str) -> dict[str, object]:
    return {
        "recovery_session_id": "RECOVERY-1",
        "queue_revision": revision,
        "order_queue_sha256": marker * 64,
        "positions_sha256": "B" * 64,
        "fills_sha256": "C" * 64,
    }


def _observation(
    amount: int,
    contributors: tuple[str, ...],
    *,
    revision: int,
    marker: str,
    observed_at: str | None = None,
) -> dict[str, object]:
    evidence = _evidence(revision, marker)
    result = build_stable_buffer_observation(
        account_no=ACCOUNT,
        trading_day=DAY,
        confirmed_entry_amount=amount,
        contributing_buy_ids=contributors,
        evidence_before=evidence,
        evidence_after=deepcopy(evidence),
        observed_at=observed_at or f"2026-08-15T10:{revision:02d}:00+09:00",
    )
    assert result["available"] is True, result
    return result["observation"]


def _pnl(profit: int, rate: float, open_cost: int) -> dict[str, object]:
    return {
        "available": True,
        "cumulative_profit": profit,
        "cumulative_rate": rate,
        "open_cost": open_cost,
    }


def _candidate(code: str) -> dict[str, object]:
    return {
        "stock_code": code,
        "stock_dir": f"stocks/{code}",
        "routine_instance_id": f"routine-{code}",
        "is_auto_trade_target": True,
        "position": {
            "code": code,
            "position_status": "OPEN",
            "quantity": 10,
            "cost_basis": 1000,
        },
        "state": {"status": "RUNNING"},
        "config": {"operation_excluded": False},
        "orders": [],
    }


def _completion_projection(
    statuses: dict[str, str],
) -> dict[str, object]:
    return {
        "evaluated": True,
        "blocked": False,
        "operation_date": DAY,
        "evidence_errors": [],
        "stock_results": [
            {
                "stock_code": code,
                "status": status,
                "reasons": [] if status in {"DONE", "CARRYOVER_DONE"} else ["not complete"],
            }
            for code, status in statuses.items()
        ],
    }


def _owned_early_candidate(code: str, event_id: str) -> dict[str, object]:
    candidate = _candidate(code)
    source = buffer_response_command_source(event_id)
    candidate["state"] = {
        "status": "EARLY_CLOSE",
        "operation_command_mode": "EARLY_CLOSE",
        "operation_command_id": deterministic_buffer_early_close_command_id(event_id),
        "operation_command_source": source,
        "early_close_source": source,
        "early_close_method": POLICY_ROUTINE_CLOSE,
        "review_required": False,
        "emergency_stopped": False,
        "liquidation_policy_forced": False,
        "close_routine_final_sell_ordered": False,
    }
    return candidate


class BufferResponseCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        runtime = Path(self.temp.name) / "runtime"
        runtime.mkdir()
        self.ingress_path = runtime / "buffer_response_ingress_state.json"
        self.ownership_path = runtime / "buffer_response_ownership.json"
        self.ingress = BufferResponseIngressStateService(
            self.ingress_path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )
        self.ownership = BufferResponseOwnershipService(
            self.ownership_path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )
        self.coordinator = BufferResponseCoordinator(
            ingress_service=self.ingress,
            ownership_service=self.ownership,
        )
        self.surface = _Surface()
        self.pnl = {
            "000001": _pnl(300, 3.0, 1000),
            "000002": _pnl(200, 2.0, 2000),
            "000003": _pnl(100, 1.0, 3000),
        }
        self.candidates = [_candidate(code) for code in self.pnl]

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _activity(amount: int, ratio: float = 50.0) -> dict[str, object]:
        return {
            "available": True,
            "entry_amount": amount,
            "entry_ratio": ratio,
        }

    def _process(
        self,
        observation: dict[str, object],
        *,
        surface: object | None = None,
        candidates: object | None = None,
        ratio: float = 50.0,
    ) -> dict[str, object]:
        return self.coordinator.process_stable_observation(
            observation=observation,
            budget_activity=self._activity(
                int(observation["confirmed_entry_amount"]), ratio=ratio
            ),
            settings_surface=self.surface if surface is None else surface,
            pnl_by_stock=self.pnl,
            candidates=self.candidates if candidates is None else candidates,
        )

    def _baseline(self, amount: int = 0) -> dict[str, object]:
        result = self._process(
            _observation(amount, (), revision=1, marker="A")
        )
        self.assertTrue(result["ingress_committed"], result)
        self.assertFalse(result["event_created"], result)
        return result

    def _claim_owned_early(self, sequence: int, code: str) -> str:
        previous = (sequence - 1) * 100
        current = sequence * 100
        evidence = {
            "observation_id": chr(64 + sequence) * 64,
            "observed_at": f"2026-08-15T10:0{sequence}:00+09:00",
            "previous_entry_amount": previous,
            "current_entry_amount": current,
            "new_contributing_buy_ids": [f"O{sequence}"],
            "contributing_buy_ids": [f"O{i}" for i in range(1, sequence + 1)],
            "confirmed_evidence": _evidence(sequence, chr(67 + sequence)),
        }
        claimed = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=sequence,
            source_evidence=evidence,
            selected_stock_code=code,
            response_intent=RESPONSE_INTENT_EARLY_CLOSE,
            detected_at=evidence["observed_at"],
            expected_revision=sequence - 1,
        )
        self.assertTrue(claimed["ok"], claimed)
        return str(claimed["event_id"])

    def test_first_observation_is_baseline_only_even_when_already_entered(self) -> None:
        result = self._baseline(300)
        self.assertTrue(result["baseline_initialized"])
        self.assertFalse(self.ownership_path.exists())

    def test_sequential_increases_claim_e1_a_then_e2_excludes_a_and_claims_b(self) -> None:
        self._baseline()
        e1 = self._process(_observation(100, ("O1",), revision=2, marker="D"))
        self.assertEqual((1, "000001"), (e1["event_sequence"], e1["selected_stock_code"]))
        self.assertEqual("EARLY_CLOSE", e1["claimed_response_intent"])
        self.assertTrue(e1["ownership_claimed"], e1)
        self.surface.strategy_action_badges["unified"].value = "\uc989\uc2dc\uccad\uc0b0"
        e2 = self._process(
            _observation(200, ("O1", "O2"), revision=3, marker="E")
        )
        self.assertEqual((2, "000002"), (e2["event_sequence"], e2["selected_stock_code"]))
        self.assertEqual(
            "IMMEDIATE_LIQUIDATION_REQUIRED", e2["claimed_response_intent"]
        )
        self.assertTrue(e2["ownership_claimed"], e2)

    def test_simultaneous_buy_ids_create_one_event_and_one_owner(self) -> None:
        self._baseline()
        result = self._process(
            _observation(300, ("O1", "O2"), revision=2, marker="D")
        )
        snapshot = self.ownership.read_snapshot()["snapshot"]
        self.assertEqual(1, len(snapshot["events"]))
        event = next(iter(snapshot["events"].values()))
        self.assertEqual(["O1", "O2"], event["source_evidence"]["new_contributing_buy_ids"])
        self.assertEqual(1, result["event_sequence"])

    def test_same_lifecycle_decrease_and_zero_create_no_new_event(self) -> None:
        self._baseline()
        self._process(_observation(300, ("O1",), revision=2, marker="D"))
        for amount, revision, marker in ((301, 3, "E"), (200, 4, "F"), (0, 5, "A")):
            result = self._process(
                _observation(amount, ("O1",), revision=revision, marker=marker)
            )
            self.assertFalse(result["event_created"], result)
        snapshot = self.ingress.read_snapshot()["snapshot"]
        checkpoint = next(iter(snapshot["checkpoints"].values()))
        self.assertEqual(1, checkpoint["last_event_sequence"])
        self.assertEqual(0, checkpoint["last_confirmed_entry_amount"])

    def test_repeated_identical_observation_is_noop_without_revision_or_selector(self) -> None:
        self._baseline()
        observation = _observation(100, ("O1",), revision=2, marker="D")
        first = self._process(observation)
        revision = self.ingress.read_snapshot()["snapshot"]["revision"]
        replayed_at_later_tick = _observation(
            100,
            ("O1",),
            revision=2,
            marker="D",
            observed_at="2026-08-15T10:59:00+09:00",
        )
        self.assertEqual(
            observation["observation_id"],
            replayed_at_later_tick["observation_id"],
        )
        with mock.patch.object(
            self.coordinator,
            "_candidate_selector",
            side_effect=AssertionError("selector must not rerun"),
        ):
            repeated = self._process(replayed_at_later_tick)
        self.assertTrue(first["event_created"])
        self.assertFalse(repeated["event_created"])
        self.assertEqual(revision, self.ingress.read_snapshot()["snapshot"]["revision"])

    def test_no_candidate_consumes_event_once_without_ownership(self) -> None:
        self._baseline()
        observation = _observation(100, ("O1",), revision=2, marker="D")
        first = self._process(observation, candidates=[])
        repeated = self._process(observation, candidates=[])
        self.assertTrue(first["event_created"])
        self.assertTrue(first["ingress_committed"])
        self.assertFalse(first["ownership_claimed"])
        self.assertFalse(repeated["event_created"])
        self.assertFalse(self.ownership_path.exists())

    def test_missing_settings_consumes_event_once_without_ownership(self) -> None:
        self._baseline()
        observation = _observation(100, ("O1",), revision=2, marker="D")
        first = self.coordinator.process_stable_observation(
            observation=observation,
            budget_activity=self._activity(100),
            settings_surface=None,
            pnl_by_stock=self.pnl,
            candidates=self.candidates,
        )
        repeated = self.coordinator.process_stable_observation(
            observation=observation,
            budget_activity=self._activity(100),
            settings_surface=None,
            pnl_by_stock=self.pnl,
            candidates=self.candidates,
        )
        self.assertTrue(first["event_created"])
        self.assertFalse(first["policy_projected"])
        self.assertTrue(first["ingress_committed"])
        self.assertFalse(repeated["event_created"])
        self.assertFalse(self.ownership_path.exists())

    def test_claim_then_crash_recovers_e1_without_selecting_b(self) -> None:
        self._baseline()
        observation = _observation(300, ("O1",), revision=2, marker="D")
        preview = self.ingress.preview_observation(observation)
        source = coordinator_module._source_evidence(observation, preview)
        claim = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence=source,
            selected_stock_code="000001",
            response_intent="EARLY_CLOSE",
            detected_at=observation["observed_at"],
            expected_revision=0,
        )
        self.assertTrue(claim["ok"], claim)
        self.surface.strategy_action_badges["unified"].value = "\uc989\uc2dc\uccad\uc0b0"
        with mock.patch.object(
            self.coordinator,
            "_candidate_selector",
            side_effect=AssertionError("same E1 must not select a new candidate"),
        ):
            recovered = self._process(observation)
        self.assertEqual(1, recovered["crash_bridge_recovered"])
        self.assertFalse(recovered["event_created"])
        events = self.ownership.read_snapshot()["snapshot"]["events"]
        self.assertEqual(1, len(events))
        restored = next(iter(events.values()))
        self.assertEqual("000001", restored["selected_stock_code"])
        self.assertEqual("EARLY_CLOSE", restored["response_intent"])

    def test_crash_with_new_increase_recovers_e1_then_creates_e2_for_b(self) -> None:
        self._baseline()
        e1_observation = _observation(300, ("O1",), revision=2, marker="D")
        preview = self.ingress.preview_observation(e1_observation)
        claim = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence=coordinator_module._source_evidence(e1_observation, preview),
            selected_stock_code="000001",
            response_intent="EARLY_CLOSE",
            detected_at=e1_observation["observed_at"],
            expected_revision=0,
        )
        self.assertTrue(claim["ok"], claim)
        current = self._process(
            _observation(500, ("O1", "O2"), revision=3, marker="E")
        )
        self.assertEqual(1, current["crash_bridge_recovered"])
        self.assertEqual(2, current["event_sequence"])
        self.assertEqual("000002", current["selected_stock_code"])

    def test_dynamic_thresholds_remain_owned_by_policy_projection(self) -> None:
        for threshold in (10, 20, 30, 40, 50, 60, 70, 80, 90):
            policy = coordinator_module.project_buffer_response_policy(
                settings_surface=_Surface(threshold),
                pnl_by_stock=self.pnl,
                budget_activity=self._activity(1, ratio=float(threshold)),
            )
            self.assertEqual(threshold, policy["configured_threshold"])
            self.assertEqual(
                "IMMEDIATE_LIQUIDATION_REQUIRED",
                policy["effective_response"],
            )

    def test_dynamic_threshold_intent_is_frozen_by_each_atomic_claim(self) -> None:
        for threshold in (10, 40, 60, 80, 90):
            with self.subTest(threshold=threshold), tempfile.TemporaryDirectory() as temp:
                runtime = Path(temp) / "runtime"
                runtime.mkdir()
                ingress = BufferResponseIngressStateService(
                    runtime / "ingress.json",
                    now_factory=lambda: "2026-08-15T11:00:00+09:00",
                )
                ownership = BufferResponseOwnershipService(
                    runtime / "ownership.json",
                    now_factory=lambda: "2026-08-15T11:00:00+09:00",
                )
                coordinator = BufferResponseCoordinator(
                    ingress_service=ingress,
                    ownership_service=ownership,
                )
                surface = _Surface(threshold)

                baseline = coordinator.process_stable_observation(
                    observation=_observation(0, (), revision=1, marker="A"),
                    budget_activity=self._activity(0, ratio=0.0),
                    settings_surface=surface,
                    pnl_by_stock=self.pnl,
                    candidates=self.candidates,
                )
                self.assertTrue(baseline["ingress_committed"], baseline)
                below = coordinator.process_stable_observation(
                    observation=_observation(100, ("O1",), revision=2, marker="D"),
                    budget_activity=self._activity(100, ratio=threshold - 0.001),
                    settings_surface=surface,
                    pnl_by_stock=self.pnl,
                    candidates=self.candidates,
                )
                at_threshold = coordinator.process_stable_observation(
                    observation=_observation(
                        200, ("O1", "O2"), revision=3, marker="E"
                    ),
                    budget_activity=self._activity(200, ratio=float(threshold)),
                    settings_surface=surface,
                    pnl_by_stock=self.pnl,
                    candidates=self.candidates,
                )
                self.assertEqual("EARLY_CLOSE", below["claimed_response_intent"])
                self.assertEqual("", at_threshold["claimed_response_intent"])
                self.assertEqual(
                    "INTERVAL_ESCALATION_REUSES_EXISTING_OWNERSHIP",
                    at_threshold["reason"],
                )
                snapshot = ownership.read_snapshot()["snapshot"]
                self.assertEqual(1, len(snapshot["events"]))

    def test_completion_requires_done_and_is_exactly_once(self) -> None:
        event_id = self._claim_owned_early(1, "000001")
        candidates = [_owned_early_candidate("000001", event_id)]
        for status in (
            "REQUESTED",
            "ORDER_QUEUED",
            "PARTIALLY_FILLED",
            "HOLDING_REMAINS",
        ):
            before = self.ownership.read_snapshot()["snapshot"]["revision"]
            result = self.coordinator.reconcile_completion_and_escalate(
                account_no=ACCOUNT,
                trading_day=DAY,
                budget_activity=self._activity(0, ratio=0.0),
                settings_surface=self.surface,
                pnl_by_stock=self.pnl,
                candidates=candidates,
                completion_projection=_completion_projection({"000001": status}),
            )
            self.assertEqual(0, result["completion_changed_count"], result)
            self.assertEqual(
                before, self.ownership.read_snapshot()["snapshot"]["revision"]
            )

        completed = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(0, ratio=0.0),
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=candidates,
            completion_projection=_completion_projection({"000001": "DONE"}),
        )
        self.assertEqual(1, completed["completion_changed_count"], completed)
        snapshot = self.ownership.read_snapshot()["snapshot"]
        self.assertEqual(STATUS_COMPLETED, snapshot["events"][event_id]["status"])
        revision = snapshot["revision"]

        restarted = BufferResponseCoordinator(
            ingress_service=self.ingress,
            ownership_service=BufferResponseOwnershipService(self.ownership_path),
        )
        repeated = restarted.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(90, ratio=90.0),
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=self.candidates,
            completion_projection=_completion_projection({"000001": "DONE"}),
        )
        self.assertEqual(0, repeated["completion_changed_count"], repeated)
        self.assertEqual(revision, self.ownership.read_snapshot()["snapshot"]["revision"])
        self.assertEqual("NO_PENDING_BUFFER_OWNED_EARLY_CLOSE", repeated["reason"])

    def test_interval_close_promotes_one_then_waits_for_actual_completion(self) -> None:
        event_ids = {
            code: self._claim_owned_early(index, code)
            for index, code in enumerate(("000001", "000002", "000003"), start=1)
        }
        candidates = [
            _owned_early_candidate(code, event_ids[code]) for code in event_ids
        ]
        not_complete = _completion_projection(
            {code: "HOLDING_REMAINS" for code in event_ids}
        )

        first = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity={
                **self._activity(90, ratio=90.0),
                "predicted_entry_ratio": 0.0,
                "estimated_recovery_amount": 999999999,
            },
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=candidates,
            completion_projection=not_complete,
        )
        self.assertTrue(first["escalation_changed"], first)
        self.assertEqual(event_ids["000001"], first["selected_event_id"])
        snapshot = self.ownership.read_snapshot()["snapshot"]
        immediate = [
            event
            for event in snapshot["events"].values()
            if event["response_intent"]
            == RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
        ]
        self.assertEqual(1, len(immediate))

        before = snapshot["revision"]
        repeated = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(90, ratio=90.0),
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=candidates,
            completion_projection=not_complete,
        )
        self.assertEqual("ACTIVE_IMMEDIATE_OWNERSHIP_PENDING", repeated["reason"])
        self.assertFalse(repeated["escalation_changed"])
        self.assertEqual(before, self.ownership.read_snapshot()["snapshot"]["revision"])

        after_a = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(85, ratio=85.0),
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=candidates,
            completion_projection=_completion_projection(
                {
                    "000001": "DONE",
                    "000002": "HOLDING_REMAINS",
                    "000003": "HOLDING_REMAINS",
                }
            ),
        )
        self.assertEqual(1, after_a["completion_changed_count"], after_a)
        self.assertEqual(event_ids["000002"], after_a["selected_event_id"])
        self.assertTrue(after_a["escalation_changed"], after_a)
        snapshot = self.ownership.read_snapshot()["snapshot"]
        self.assertEqual(STATUS_COMPLETED, snapshot["events"][event_ids["000001"]]["status"])
        self.assertEqual(
            RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
            snapshot["events"][event_ids["000002"]]["response_intent"],
        )
        self.assertEqual(
            RESPONSE_INTENT_EARLY_CLOSE,
            snapshot["events"][event_ids["000003"]]["response_intent"],
        )

        after_b = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(70, ratio=70.0),
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=candidates,
            completion_projection=_completion_projection(
                {
                    "000001": "DONE",
                    "000002": "CARRYOVER_DONE",
                    "000003": "HOLDING_REMAINS",
                }
            ),
        )
        self.assertEqual("BUFFER_BELOW_CONFIGURED_THRESHOLD", after_b["reason"])
        self.assertFalse(after_b["escalation_changed"])
        snapshot = self.ownership.read_snapshot()["snapshot"]
        self.assertEqual(STATUS_COMPLETED, snapshot["events"][event_ids["000002"]]["status"])
        self.assertEqual(STATUS_OWNED, snapshot["events"][event_ids["000003"]]["status"])
        self.assertEqual(
            RESPONSE_INTENT_EARLY_CLOSE,
            snapshot["events"][event_ids["000003"]]["response_intent"],
        )

    def test_interval_close_reapplies_filter_and_never_expands_to_ordinary_holding(self) -> None:
        event_a = self._claim_owned_early(1, "000001")
        event_b = self._claim_owned_early(2, "000002")
        event_c = self._claim_owned_early(3, "000003")
        candidates = [
            _owned_early_candidate("000001", event_a),
            _owned_early_candidate("000002", event_b),
            _owned_early_candidate("000003", event_c),
            _candidate("999999"),
        ]
        first = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(90, ratio=90.0),
            settings_surface=self.surface,
            pnl_by_stock={**self.pnl, "999999": _pnl(999999, 999.0, 999999)},
            candidates=candidates,
            completion_projection=_completion_projection(
                {code: "HOLDING_REMAINS" for code in ("000001", "000002", "000003")}
            ),
        )
        self.assertEqual(event_a, first["selected_event_id"])

        repriced_pnl = {
            **self.pnl,
            "000002": _pnl(1, 1.0, 2000),
            "000003": _pnl(999, 9.0, 3000),
            "999999": _pnl(999999, 999.0, 999999),
        }
        second = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(90, ratio=90.0),
            settings_surface=self.surface,
            pnl_by_stock=repriced_pnl,
            candidates=candidates,
            completion_projection=_completion_projection(
                {
                    "000001": "DONE",
                    "000002": "HOLDING_REMAINS",
                    "000003": "HOLDING_REMAINS",
                }
            ),
        )
        self.assertEqual(event_c, second["selected_event_id"])
        self.assertNotEqual("999999", second["selected_stock_code"])

    def test_zero_no_pending_and_conflicts_fail_closed_without_new_ownership(self) -> None:
        event_id = self._claim_owned_early(1, "000001")
        base = _owned_early_candidate("000001", event_id)
        not_complete = _completion_projection({"000001": "HOLDING_REMAINS"})
        zero = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(0, ratio=0.0),
            settings_surface=self.surface,
            pnl_by_stock=self.pnl,
            candidates=[base],
            completion_projection=not_complete,
        )
        self.assertEqual("BUFFER_NOT_ENTERED", zero["reason"])

        for conflict_name, mutate in (
            ("SELL", lambda item: item.update({"orders": [{"code": "000001", "side": "SELL", "status": "ORDER_QUEUED", "remaining_quantity": 1}]})),
            ("REVIEW", lambda item: item["state"].update({"review_required": True})),
            ("EMERGENCY", lambda item: item["state"].update({"status": "EMERGENCY_STOPPED"})),
        ):
            with self.subTest(conflict=conflict_name):
                candidate = deepcopy(base)
                mutate(candidate)
                result = self.coordinator.reconcile_completion_and_escalate(
                    account_no=ACCOUNT,
                    trading_day=DAY,
                    budget_activity=self._activity(90, ratio=90.0),
                    settings_surface=self.surface,
                    pnl_by_stock=self.pnl,
                    candidates=[candidate, _candidate("999999")],
                    completion_projection=not_complete,
                )
                self.assertFalse(result["escalation_changed"], result)
                self.assertEqual("NO_ELIGIBLE_BUFFER_OWNED_EARLY_CLOSE", result["reason"])

        completed = self.coordinator.reconcile_completion_and_escalate(
            account_no=ACCOUNT,
            trading_day=DAY,
            budget_activity=self._activity(90, ratio=90.0),
            settings_surface=self.surface,
            pnl_by_stock={**self.pnl, "999999": _pnl(999999, 999.0, 999999)},
            candidates=[base, _candidate("999999")],
            completion_projection=_completion_projection({"000001": "DONE"}),
        )
        self.assertEqual("NO_PENDING_BUFFER_OWNED_EARLY_CLOSE", completed["reason"])
        self.assertEqual(1, len(self.ownership.read_snapshot()["snapshot"]["events"]))

    def test_operation_cycle_resumes_same_active_immediate_ownership_after_crash(self) -> None:
        window = type("Window", (), {})()
        window._main_buffer_response_settings_surface = self.surface
        collected = {
            "observation": {"account_no": ACCOUNT, "trading_day": DAY},
            "budget_activity": self._activity(90, ratio=90.0),
            "pnl_by_stock": self.pnl,
            "candidates": self.candidates,
        }
        with (
            mock.patch.object(
                self.coordinator,
                "reconcile_completion_and_escalate",
                return_value={
                    "ok": True,
                    "reason": "ACTIVE_IMMEDIATE_OWNERSHIP_PENDING",
                    "escalation_changed": False,
                    "selected_event_id": "",
                },
            ),
            mock.patch.object(
                coordinator_module,
                "resume_main_window_buffer_immediate_liquidation_events",
                return_value={"ok": True, "preparations": ()},
            ) as resume,
            mock.patch.object(
                coordinator_module,
                "dispatch_ready_main_window_buffer_immediate_preparations",
                return_value={"ok": True, "dispatches": ()},
            ) as dispatch,
            mock.patch.object(
                coordinator_module,
                "prepare_main_window_buffer_immediate_liquidation",
            ) as direct_prepare,
        ):
            result = coordinator_module._reconcile_collected_main_window_buffer_response(
                window,
                coordinator=self.coordinator,
                collected=collected,
            )
        resume.assert_called_once()
        dispatch.assert_called_once()
        direct_prepare.assert_not_called()
        self.assertEqual(
            "ACTIVE_IMMEDIATE_OWNERSHIP_PENDING", result["reason"]
        )

    def test_main_window_dispatches_early_close_only_after_claim_and_ingress_commit(self) -> None:
        window = type("Window", (), {})()
        window._buffer_response_coordinator = self.coordinator
        window._main_buffer_response_settings_surface = self.surface
        collected = {
            "available": True,
            "reason": "",
            "budget_activity": self._activity(0),
            "pnl_by_stock": self.pnl,
            "candidates": self.candidates,
        }
        dispatch_result = {"ok": True, "dispatched": True}
        with mock.patch.object(
            coordinator_module,
            "collect_main_window_stable_buffer_context",
        ) as collect, mock.patch.object(
            coordinator_module,
            "dispatch_main_window_buffer_early_close",
            return_value=dispatch_result,
        ) as dispatch:
            collect.return_value = {
                **collected,
                "observation": _observation(0, (), revision=1, marker="A"),
            }
            baseline = coordinator_module.coordinate_main_window_buffer_response(
                window,
                chejan_result={"recorded": True, "stage": "chejan_record"},
            )
            dispatch.assert_not_called()
            self.assertTrue(baseline["ingress_committed"], baseline)

            collect.return_value = {
                **collected,
                "observation": _observation(100, ("O1",), revision=2, marker="D"),
                "budget_activity": self._activity(100),
            }
            claimed = coordinator_module.coordinate_main_window_buffer_response(
                window,
                chejan_result={"recorded": True, "stage": "chejan_record"},
            )
        self.assertTrue(claimed["ownership_claimed"], claimed)
        self.assertTrue(claimed["ingress_committed"], claimed)
        self.assertEqual("EARLY_CLOSE", claimed["claimed_response_intent"])
        self.assertIs(dispatch_result, claimed["early_close_dispatch"])
        dispatch.assert_called_once_with(
            window,
            event_id=claimed["event_id"],
            ownership_service=self.ownership,
        )

    def test_main_window_prepares_immediate_only_after_claim_and_ingress_commit(self) -> None:
        surface = _Surface(response_label="\uc989\uc2dc\uccad\uc0b0")
        window = type("Window", (), {})()
        window._buffer_response_coordinator = self.coordinator
        window._main_buffer_response_settings_surface = surface
        common = {
            "available": True,
            "reason": "",
            "pnl_by_stock": self.pnl,
            "candidates": self.candidates,
        }
        with mock.patch.object(
            coordinator_module,
            "collect_main_window_stable_buffer_context",
        ) as collect, mock.patch.object(
            coordinator_module,
            "dispatch_main_window_buffer_early_close",
        ) as dispatch, mock.patch.object(
            coordinator_module,
            "prepare_main_window_buffer_immediate_liquidation",
            return_value={
                "ok": True,
                "state": "READY_FOR_IMMEDIATE_LIQUIDATION",
                "ready_for_liquidation": True,
            },
        ) as prepare, mock.patch.object(
            coordinator_module,
            "resume_main_window_buffer_immediate_liquidation_events",
            return_value={"ok": True, "attempted": 1, "results": ()},
        ) as resume, mock.patch.object(
            coordinator_module,
            "dispatch_main_window_buffer_immediate_market_close",
            return_value={"ok": True, "read_back_verified": True},
        ) as immediate_dispatch, mock.patch.object(
            coordinator_module,
            "dispatch_ready_main_window_buffer_immediate_preparations",
            return_value={"ok": True, "attempted": 0, "results": ()},
        ) as resume_dispatch:
            collect.return_value = {
                **common,
                "observation": _observation(0, (), revision=1, marker="A"),
                "budget_activity": self._activity(0, ratio=80.0),
            }
            coordinator_module.coordinate_main_window_buffer_response(
                window,
                chejan_result={"recorded": True, "stage": "chejan_record"},
            )
            collect.return_value = {
                **common,
                "observation": _observation(100, ("O1",), revision=2, marker="D"),
                "budget_activity": self._activity(100, ratio=80.0),
            }
            result = coordinator_module.coordinate_main_window_buffer_response(
                window,
                chejan_result={"recorded": True, "stage": "chejan_record"},
            )
            repeated = coordinator_module.coordinate_main_window_buffer_response(
                window,
                chejan_result={"recorded": True, "stage": "cancel_chejan_record"},
            )
        self.assertEqual(
            "IMMEDIATE_LIQUIDATION_REQUIRED",
            result["claimed_response_intent"],
        )
        self.assertIsNone(result["early_close_dispatch"])
        dispatch.assert_not_called()
        self.assertEqual(
            "READY_FOR_IMMEDIATE_LIQUIDATION",
            result["immediate_liquidation_preparation"]["state"],
        )
        prepare.assert_called_once_with(
            window,
            event_id=result["event_id"],
            ownership_service=self.ownership,
            ingress_service=self.ingress,
        )
        immediate_dispatch.assert_called_once_with(
            window,
            event_id=result["event_id"],
            preparation_result=result["immediate_liquidation_preparation"],
            ownership_service=self.ownership,
            ingress_service=self.ingress,
        )
        self.assertTrue(result["immediate_liquidation_dispatch"]["read_back_verified"])
        self.assertEqual(
            {"ok": True, "attempted": 1, "results": ()},
            repeated["immediate_liquidation_resume"],
        )
        self.assertEqual(2, resume.call_count)
        resume.assert_called_with(
            window,
            ownership_service=self.ownership,
            ingress_service=self.ingress,
        )
        self.assertEqual(2, resume_dispatch.call_count)

    def test_coordinator_has_no_close_cancel_or_order_execution_dependency(self) -> None:
        source = inspect.getsource(coordinator_module)
        for forbidden in (
            "apply_close_intent",
            "apply_early_close_intent",
            "OperationCommand",
            "cancel_queue",
            "SendOrder",
            "kiwoom_send_order_executor",
        ):
            self.assertNotIn(forbidden, source)


class MainWindowBufferResponseIntegrationTests(unittest.TestCase):
    def test_main_window_calls_coordinator_after_successful_chejan_pipeline(self) -> None:
        import gui_windows

        call_order: list[str] = []

        def handle(*_args, **_kwargs):
            call_order.append("chejan-finished")
            return {"recorded": True, "stage": "chejan_record"}

        def coordinate(window, *, chejan_result):
            self.assertEqual("chejan-finished", call_order[-1])
            self.assertIs(window.last_chejan_record_result, chejan_result)
            call_order.append("coordinator")
            return {"observed": True}

        main = type("Main", (), {"auto_trade_setting_window": None})()
        main.kiwoom_api = None
        main.account_combo = None
        main._production_recovery_identity = None
        main._main_budget_orderable_valid = False
        coordinator_module.register_main_window_buffer_response_integration(main)
        self.addCleanup(
            coordinator_module._INTEGRATION_READY_WINDOW_IDS.discard,
            id(main),
        )
        with mock.patch.object(gui_windows, "handle_kiwoom_raw_chejan_event", side_effect=handle), mock.patch.object(
            gui_windows,
            "coordinate_main_window_buffer_response",
            side_effect=coordinate,
        ):
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(main, {"gubun": "0"})
        self.assertEqual(["chejan-finished", "coordinator"], call_order)

    def test_main_window_does_not_call_coordinator_before_success_or_after_reconciliation_failure(self) -> None:
        import gui_windows

        main = type("Main", (), {"auto_trade_setting_window": None})()
        blocked_results = (
            {"recorded": False, "stage": "normalize"},
            {
                "recorded": True,
                "stage": "chejan_record",
                "manual_reconciliation_required": True,
            },
        )
        for result in blocked_results:
            with self.subTest(result=result), mock.patch.object(
                gui_windows,
                "handle_kiwoom_raw_chejan_event",
                return_value=result,
            ), mock.patch.object(
                gui_windows,
                "coordinate_main_window_buffer_response",
            ) as coordinate:
                gui_windows.MainWindow.on_kiwoom_raw_chejan_received(main, {"gubun": "0"})
                coordinate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
