from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_projection_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyExecutionRuntimeProjectionPreviewTest(unittest.TestCase):
    def _runtime(self, **overrides):
        state = {
            "current_buy_round": 1,
            "executed_buy_rounds": 1,
            "cumulative_buy_budget": 100000.0,
            "last_buy_order_price": 69000.0,
            "last_buy_budget": 100000.0,
            "last_buy_signal_id": "SIG_PREV",
            "last_buy_candidate_id": "BUY_ORDER_CANDIDATE_PREV",
            "last_buy_created_at": "2026-07-11T09:00:00+09:00",
            "is_last_buy_round": False,
        }
        state.update(overrides)
        return state

    def _snapshot(self, runtime=None, **overrides):
        snapshot = {
            "policy_hash": "policy-hash-1",
            "approved_rule_hash": "approved-rule-hash-1",
            "runtime_state_hash": _stable_hash(self._runtime() if runtime is None else runtime),
            "calculation_hash": "calc-hash-1",
        }
        snapshot.update(overrides)
        return snapshot

    def _candidate(self, *, status="READY", draft_overrides=None, snapshot=None):
        draft = {
            "candidate_version": "BUY_ORDER_CANDIDATE_DRAFT_V1",
            "candidate_id": "BUY_ORDER_CANDIDATE_1",
            "symbol": "005930",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 70000.0,
            "budget": 150000.0,
            "quantity_policy": "BUDGET_BASED",
            "next_buy_round": 2,
            "is_last_round": False,
            "hoga_mode": "SINGLE",
            "hoga_up": 1,
            "hoga_down": 0,
            "source_signal_id": "SIG_BUY_2",
            "policy_version": "BUY_EXECUTION_POLICY_V1",
            "execution_snapshot": snapshot or self._snapshot(),
        }
        if draft_overrides:
            draft.update(draft_overrides)
        return {
            "status": status,
            "order_candidate_draft": draft if status == "READY" else None,
            "execution_snapshot": deepcopy(draft["execution_snapshot"]),
            "evidence": {"candidate": "ready"},
            "diagnostics": [{"stage": "candidate_draft", "ok": status == "READY"}],
        }

    def _build(self, **overrides):
        runtime = overrides.pop("runtime_state_snapshot", self._runtime())
        kwargs = {
            "buy_candidate_preview": self._candidate(snapshot=self._snapshot(runtime)),
            "runtime_state_snapshot": runtime,
            "execution_policy_snapshot": {"policy_hash": "policy-hash-1"},
            "projection_context": {"preview_timestamp": "2026-07-11T10:00:00+09:00"},
        }
        kwargs.update(overrides)
        return build_buy_runtime_projection_preview(**kwargs)

    def test_ready_candidate_creates_projection(self):
        result = self._build()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        after = result["after_state_candidate"]
        self.assertEqual(2, after["current_buy_round"])
        self.assertEqual(2, after["executed_buy_rounds"])
        self.assertEqual(250000.0, after["cumulative_buy_budget"])
        self.assertEqual(70000.0, after["last_buy_order_price"])
        self.assertEqual(150000.0, after["last_buy_budget"])
        self.assertEqual("SIG_BUY_2", after["last_buy_signal_id"])
        self.assertEqual("BUY_ORDER_CANDIDATE_1", after["last_buy_candidate_id"])
        self.assertEqual("2026-07-11T10:00:00+09:00", after["last_buy_created_at"])
        self.assertFalse(after["is_last_buy_round"])
        self.assertEqual("BUY_EXECUTION_POLICY_V1", after["execution_policy_version"])
        self.assertEqual("policy-hash-1", after["execution_policy_hash"])
        self.assertEqual("approved-rule-hash-1", after["multi_point_policy_hash"])

    def test_blocked_candidate_has_no_projection(self):
        result = self._build(buy_candidate_preview=self._candidate(status="BLOCKED"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["runtime_projection"])
        self.assertIsNone(result["runtime_patch_preview"])

    def test_invalid_candidate_has_no_projection(self):
        result = self._build(buy_candidate_preview=self._candidate(status="INVALID"))

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIsNone(result["runtime_projection"])

    def test_round_executed_and_budget_increment(self):
        result = self._build()

        self.assertEqual(2, result["after_state_candidate"]["current_buy_round"])
        self.assertEqual(2, result["after_state_candidate"]["executed_buy_rounds"])
        self.assertEqual(250000.0, result["after_state_candidate"]["cumulative_buy_budget"])

    def test_last_order_budget_signal_candidate_reflected(self):
        result = self._build()
        after = result["after_state_candidate"]

        self.assertEqual(70000.0, after["last_buy_order_price"])
        self.assertEqual(150000.0, after["last_buy_budget"])
        self.assertEqual("SIG_BUY_2", after["last_buy_signal_id"])
        self.assertEqual("BUY_ORDER_CANDIDATE_1", after["last_buy_candidate_id"])

    def test_is_last_round_reflected(self):
        candidate = self._candidate(draft_overrides={"is_last_round": True})

        result = self._build(buy_candidate_preview=candidate)

        self.assertTrue(result["after_state_candidate"]["is_last_buy_round"])

    def test_policy_version_and_hash_reflected(self):
        result = self._build()
        after = result["after_state_candidate"]

        self.assertEqual("BUY_EXECUTION_POLICY_V1", after["execution_policy_version"])
        self.assertEqual("policy-hash-1", after["execution_policy_hash"])
        self.assertEqual("approved-rule-hash-1", after["multi_point_policy_hash"])

    def test_patch_contains_only_changed_fields(self):
        result = self._build()
        changes = result["runtime_patch_preview"]["changes"]

        self.assertEqual(set(changes), set(result["runtime_projection"]["changed_fields"]))
        self.assertNotIn("unrelated_state", changes)
        self.assertEqual("preview_runtime_state_patch", result["runtime_patch_preview"]["operation"])
        self.assertEqual("buy_execution_state", result["runtime_patch_preview"]["target"])

    def test_before_after_hash_are_stable(self):
        first = self._build()
        second = self._build()

        self.assertEqual(first["runtime_patch_preview"]["runtime_state_hash_before"], second["runtime_patch_preview"]["runtime_state_hash_before"])
        self.assertEqual(
            first["runtime_patch_preview"]["runtime_state_hash_after_candidate"],
            second["runtime_patch_preview"]["runtime_state_hash_after_candidate"],
        )

    def test_input_immutability(self):
        runtime = self._runtime()
        candidate = self._candidate(snapshot=self._snapshot(runtime))
        policy = {"policy_hash": "policy-hash-1"}
        context = {"preview_timestamp": "2026-07-11T10:00:00+09:00"}
        original = (deepcopy(candidate), deepcopy(runtime), deepcopy(policy), deepcopy(context))

        build_buy_runtime_projection_preview(
            buy_candidate_preview=candidate,
            runtime_state_snapshot=runtime,
            execution_policy_snapshot=policy,
            projection_context=context,
        )

        self.assertEqual((candidate, runtime, policy, context), original)

    def test_deterministic(self):
        self.assertEqual(self._build(), self._build())

    def test_malformed_candidate_blocked(self):
        result = self._build(buy_candidate_preview={"status": "READY"})

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CANDIDATE_DRAFT_MISSING", result["issues"])

    def test_invalid_runtime_state_blocked(self):
        runtime = self._runtime(executed_buy_rounds="bad")
        candidate = self._candidate(snapshot=self._snapshot(runtime))

        result = self._build(buy_candidate_preview=candidate, runtime_state_snapshot=runtime)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("INVALID_RUNTIME_EXECUTED_ROUNDS", result["issues"])

    def test_policy_hash_mismatch_blocked(self):
        result = self._build(execution_policy_snapshot={"policy_hash": "other"})

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("POLICY_HASH_MISMATCH", result["issues"])

    def test_before_state_hash_mismatch_blocked(self):
        result = self._build(projection_context={"expected_runtime_state_hash": "other"})

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("BEFORE_STATE_HASH_MISMATCH", result["issues"])

    def test_candidate_round_before_current_blocked(self):
        candidate = self._candidate(draft_overrides={"next_buy_round": 1})

        result = self._build(runtime_state_snapshot=self._runtime(current_buy_round=2), buy_candidate_preview=candidate)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CANDIDATE_ROUND_BEFORE_CURRENT_ROUND", result["issues"])

    def test_runtime_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        self._build()

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_no_queue_runtime_broker_gui_calls(self):
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._build()

        self.assertEqual(STATUS_READY, result["status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
