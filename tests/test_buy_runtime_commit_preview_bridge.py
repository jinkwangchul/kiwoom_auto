from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_preview_bridge import (
    PREVIEW_VERSION,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitPreviewBridgeTest(unittest.TestCase):
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

    def _projection(self):
        runtime = self._runtime()
        return build_buy_runtime_projection_preview(
            buy_candidate_preview=self._candidate(snapshot=self._snapshot(runtime)),
            runtime_state_snapshot=runtime,
            execution_policy_snapshot={"policy_hash": "policy-hash-1"},
            projection_context={"preview_timestamp": "2026-07-11T10:00:00+09:00"},
        )

    def test_ready_projection_builds_commit_preview(self):
        result = build_buy_runtime_commit_preview(self._projection())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        preview = result["runtime_commit_preview"]
        self.assertEqual(PREVIEW_VERSION, preview["preview_version"])
        self.assertEqual("buy_execution_state", preview["runtime_target"])
        self.assertEqual("BUY_ORDER_CANDIDATE_1", preview["candidate_id"])
        self.assertEqual("SIG_BUY_2", preview["signal_id"])
        self.assertEqual("policy-hash-1", preview["policy_hash"])
        self.assertEqual("approved-rule-hash-1", preview["approved_rule_hash"])
        self.assertFalse(preview["commit_allowed"])
        self.assertFalse(preview["commit_executed"])

    def test_blocked_projection_has_no_commit_preview(self):
        projection = self._projection()
        projection["status"] = "BLOCKED"
        projection["runtime_projection"] = None
        projection["runtime_patch_preview"] = None

        result = build_buy_runtime_commit_preview(projection)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["runtime_commit_preview"])
        self.assertIsNone(result["runtime_commit_preview_summary"])

    def test_invalid_projection_has_no_commit_preview(self):
        projection = self._projection()
        projection["status"] = "INVALID"

        result = build_buy_runtime_commit_preview(projection)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIsNone(result["runtime_commit_preview"])

    def test_preview_id_is_deterministic(self):
        first = build_buy_runtime_commit_preview(self._projection())
        second = build_buy_runtime_commit_preview(self._projection())

        first_id = first["runtime_commit_preview"]["preview_id"]
        second_id = second["runtime_commit_preview"]["preview_id"]
        self.assertEqual(first_id, second_id)
        self.assertTrue(first_id.startswith("BUY_RUNTIME_COMMIT_PREVIEW_"))
        self.assertEqual(len("BUY_RUNTIME_COMMIT_PREVIEW_") + 24, len(first_id))

    def test_projection_hash_and_execution_snapshot_are_preserved(self):
        result = build_buy_runtime_commit_preview(self._projection())
        preview = result["runtime_commit_preview"]

        self.assertTrue(preview["projection_hash"])
        self.assertEqual(preview["projection_hash"], result["runtime_commit_preview_summary"]["projection_hash"])
        self.assertEqual({"policy_hash", "approved_rule_hash", "runtime_state_hash", "calculation_hash"}, set(preview["execution_snapshot"]))
        self.assertEqual(preview["execution_snapshot"], result["execution_snapshot"])

    def test_summary_is_created(self):
        projection = self._projection()
        result = build_buy_runtime_commit_preview(projection)
        summary = result["runtime_commit_preview_summary"]

        self.assertEqual(len(projection["runtime_patch_preview"]["changes"]), summary["changed_fields_count"])
        self.assertEqual(2, summary["current_buy_round"])
        self.assertEqual(2, summary["executed_buy_rounds"])
        self.assertEqual(250000.0, summary["cumulative_budget"])
        self.assertFalse(summary["is_last_round"])
        self.assertEqual("BUY_EXECUTION_POLICY_V1", summary["policy_version"])
        self.assertTrue(summary["projection_hash"])

    def test_report_is_created(self):
        result = build_buy_runtime_commit_preview(self._projection())
        report = result["runtime_commit_preview_report"]

        self.assertEqual("BUY Runtime Commit Preview", report["title"])
        self.assertEqual(result["runtime_commit_preview"]["preview_id"], report["preview_id"])
        self.assertEqual(
            ["Candidate", "Projection", "Changed Fields", "Before", "After", "Hash", "Warnings", "Diagnostics"],
            [section["title"] for section in report["sections"]],
        )

    def test_changed_fields_and_patch_count_are_exact(self):
        projection = self._projection()
        result = build_buy_runtime_commit_preview(projection)
        preview = result["runtime_commit_preview"]
        patch_changes = projection["runtime_patch_preview"]["changes"]

        self.assertEqual(sorted(patch_changes), sorted(preview["changed_fields"]))
        self.assertEqual(len(patch_changes), preview["patch_count"])
        self.assertEqual(patch_changes, result["runtime_patch_preview"]["changes"])

    def test_input_immutability(self):
        projection = self._projection()
        context = {"warnings": ["preview only"]}
        original = (deepcopy(projection), deepcopy(context))

        build_buy_runtime_commit_preview(projection, context)

        self.assertEqual(original, (projection, context))

    def test_deterministic(self):
        self.assertEqual(
            build_buy_runtime_commit_preview(self._projection()),
            build_buy_runtime_commit_preview(self._projection()),
        )

    def test_missing_projection_hash_source_is_invalid(self):
        projection = self._projection()
        projection["runtime_projection"] = {}

        result = build_buy_runtime_commit_preview(projection)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("MALFORMED_PROJECTION", result["issues"])
        self.assertIn("PROJECTION_HASH_MISSING", result["issues"])

    def test_missing_runtime_hashes_are_invalid(self):
        projection = self._projection()
        projection["runtime_projection"].pop("before_state_hash")
        projection["runtime_projection"].pop("after_state_candidate_hash")
        projection["runtime_patch_preview"].pop("runtime_state_hash_before")
        projection["runtime_patch_preview"].pop("runtime_state_hash_after_candidate")

        result = build_buy_runtime_commit_preview(projection)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("RUNTIME_BEFORE_HASH_MISSING", result["issues"])
        self.assertIn("RUNTIME_AFTER_HASH_MISSING", result["issues"])

    def test_missing_candidate_execution_snapshot_or_patch_is_invalid(self):
        projection = self._projection()
        projection["runtime_projection"].pop("candidate_id")
        projection["runtime_patch_preview"] = None
        projection["execution_snapshot"] = {}

        result = build_buy_runtime_commit_preview(projection)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CANDIDATE_ID_MISSING", result["issues"])
        self.assertIn("EXECUTION_SNAPSHOT_MISSING", result["issues"])
        self.assertIn("RUNTIME_PATCH_PREVIEW_MISSING", result["issues"])

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        build_buy_runtime_commit_preview(self._projection())

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_no_queue_runtime_broker_gui_calls(self):
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_buy_runtime_commit_preview(self._projection())

        self.assertEqual(STATUS_READY, result["status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["runtime_commit_core_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
