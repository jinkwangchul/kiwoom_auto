from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_gate_adapter import (
    GATE_VERSION,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_gate_preview,
)
from buy_runtime_commit_preview_bridge import build_buy_runtime_commit_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitGateAdapterTest(unittest.TestCase):
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

    def _snapshot(self, runtime=None):
        return {
            "policy_hash": "policy-hash-1",
            "approved_rule_hash": "approved-rule-hash-1",
            "runtime_state_hash": _stable_hash(self._runtime() if runtime is None else runtime),
            "calculation_hash": "calc-hash-1",
        }

    def _candidate(self, *, status="READY", snapshot=None):
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
        return {
            "status": status,
            "order_candidate_draft": draft if status == "READY" else None,
            "execution_snapshot": deepcopy(draft["execution_snapshot"]),
            "evidence": {"candidate": "ready"},
            "diagnostics": [{"stage": "candidate_draft", "ok": status == "READY"}],
        }

    def _commit_preview_result(self):
        runtime = self._runtime()
        projection = build_buy_runtime_projection_preview(
            buy_candidate_preview=self._candidate(snapshot=self._snapshot(runtime)),
            runtime_state_snapshot=runtime,
            execution_policy_snapshot={"policy_hash": "policy-hash-1"},
            projection_context={"preview_timestamp": "2026-07-11T10:00:00+09:00"},
        )
        return build_buy_runtime_commit_preview(projection)

    def test_ready_commit_preview_builds_gate_preview(self):
        result = build_buy_runtime_commit_gate_preview(self._commit_preview_result())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_commit_core_called"])
        gate = result["runtime_commit_gate_preview"]
        self.assertEqual(GATE_VERSION, gate["gate_version"])
        self.assertTrue(gate["commit_allowed"])
        self.assertFalse(gate["commit_execute"])
        self.assertEqual("BUY_ORDER_CANDIDATE_1", gate["candidate_id"])
        self.assertEqual("SIG_BUY_2", gate["signal_id"])
        self.assertEqual("policy-hash-1", gate["policy_hash"])
        self.assertEqual("approved-rule-hash-1", gate["approved_rule_hash"])

    def test_blocked_commit_preview_builds_blocked_gate(self):
        commit_preview = self._commit_preview_result()
        commit_preview["status"] = "BLOCKED"
        commit_preview["issues"] = ["blocked upstream"]

        result = build_buy_runtime_commit_gate_preview(commit_preview)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        gate = result["runtime_commit_gate_preview"]
        self.assertFalse(gate["commit_allowed"])
        self.assertFalse(gate["commit_execute"])
        self.assertIn("blocked upstream", gate["blocking_reason"])

    def test_invalid_commit_preview_builds_invalid_gate(self):
        commit_preview = self._commit_preview_result()
        commit_preview["status"] = "INVALID"
        commit_preview["issues"] = ["invalid upstream"]

        result = build_buy_runtime_commit_gate_preview(commit_preview)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertFalse(result["runtime_commit_gate_preview"]["commit_allowed"])
        self.assertIn("invalid upstream", result["runtime_commit_gate_preview"]["blocking_reason"])

    def test_gate_id_is_deterministic(self):
        first = build_buy_runtime_commit_gate_preview(self._commit_preview_result())
        second = build_buy_runtime_commit_gate_preview(self._commit_preview_result())

        first_id = first["runtime_commit_gate_preview"]["gate_id"]
        second_id = second["runtime_commit_gate_preview"]["gate_id"]
        self.assertEqual(first_id, second_id)
        self.assertTrue(first_id.startswith("BUY_RUNTIME_COMMIT_GATE_"))
        self.assertEqual(len("BUY_RUNTIME_COMMIT_GATE_") + 24, len(first_id))

    def test_summary_is_created(self):
        result = build_buy_runtime_commit_gate_preview(self._commit_preview_result())
        summary = result["gate_summary"]

        self.assertTrue(summary["commit_allowed"])
        self.assertEqual(2, summary["current_buy_round"])
        self.assertEqual(2, summary["executed_buy_rounds"])
        self.assertEqual(250000.0, summary["cumulative_budget"])
        self.assertEqual("BUY_EXECUTION_POLICY_V1", summary["policy_version"])
        self.assertTrue(summary["projection_hash"])
        self.assertEqual(
            len(result["runtime_commit_gate_preview"]["changed_fields"]),
            summary["changed_fields_count"],
        )

    def test_report_is_created(self):
        result = build_buy_runtime_commit_gate_preview(self._commit_preview_result())
        report = result["gate_report"]

        self.assertEqual("Runtime Commit Gate Preview", report["title"])
        self.assertEqual(result["runtime_commit_gate_preview"]["gate_id"], report["gate_id"])
        self.assertEqual(
            ["Commit Decision", "Candidate", "Projection", "Changed Fields", "Hashes", "Warnings", "Diagnostics"],
            [section["title"] for section in report["sections"]],
        )

    def test_hashes_and_execution_snapshot_are_preserved(self):
        commit_result = self._commit_preview_result()
        result = build_buy_runtime_commit_gate_preview(commit_result)
        source = commit_result["runtime_commit_preview"]
        gate = result["runtime_commit_gate_preview"]

        self.assertEqual(source["projection_hash"], gate["projection_hash"])
        self.assertEqual(source["runtime_before_hash"], gate["runtime_before_hash"])
        self.assertEqual(source["runtime_after_hash"], gate["runtime_after_hash"])
        self.assertEqual(source["execution_snapshot"], gate["execution_snapshot"])
        self.assertEqual(source["execution_snapshot"], result["execution_snapshot"])

    def test_input_immutability(self):
        commit_result = self._commit_preview_result()
        context = {"warnings": ["preview only"]}
        original = (deepcopy(commit_result), deepcopy(context))

        build_buy_runtime_commit_gate_preview(commit_result, context)

        self.assertEqual(original, (commit_result, context))

    def test_deterministic(self):
        self.assertEqual(
            build_buy_runtime_commit_gate_preview(self._commit_preview_result()),
            build_buy_runtime_commit_gate_preview(self._commit_preview_result()),
        )

    def test_missing_preview_fields_are_invalid(self):
        commit_result = self._commit_preview_result()
        preview = commit_result["runtime_commit_preview"]
        preview.pop("preview_id")
        preview.pop("projection_hash")
        preview.pop("candidate_id")
        preview.pop("policy_hash")
        commit_result["execution_snapshot"] = {}
        preview["execution_snapshot"] = {}

        result = build_buy_runtime_commit_gate_preview(commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertFalse(result["runtime_commit_gate_preview"]["commit_allowed"])
        self.assertIn("PREVIEW_ID_MISSING", result["issues"])
        self.assertIn("PROJECTION_HASH_MISSING", result["issues"])
        self.assertIn("CANDIDATE_ID_MISSING", result["issues"])
        self.assertIn("EXECUTION_SNAPSHOT_MISSING", result["issues"])
        self.assertIn("POLICY_HASH_MISSING", result["issues"])

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        build_buy_runtime_commit_gate_preview(self._commit_preview_result())

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_no_runtime_commit_core_queue_broker_or_gui_calls(self):
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_buy_runtime_commit_gate_preview(self._commit_preview_result())

        self.assertEqual(STATUS_READY, result["status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["runtime_commit_core_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
