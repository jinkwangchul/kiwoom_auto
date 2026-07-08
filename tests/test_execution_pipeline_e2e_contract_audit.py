# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from broker_dispatch_preview import preview_broker_dispatch
from execution_approval_gate import evaluate_execution_approval
from execution_order_dispatch_builder import build_order_dispatch_contract
from execution_queue_commit_contract_preview import build_queue_commit_contract_preview
from execution_queue_commit_dry_run import dry_run_queue_commit
from execution_queue_commit_executor import execute_queue_commit_from_dry_run
from execution_queue_commit_result_review import review_queue_commit_result
from execution_readiness_validator import validate_execution_readiness
from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract
from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call
from kiwoom_send_order_executor import execute_kiwoom_send_order
from kiwoom_send_order_executor_result_review import review_kiwoom_send_order_executor_result
from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety
from rule_apply_preview_execution_order_adapter import build_rule_apply_preview_execution_order_contract
from rule_apply_preview_execution_preview_controller import preview_execution_from_rule_apply_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeSendOrderAdapter:
    def __init__(self, result: object = 0) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.result


class ExecutionPipelineE2EContractAuditTest(unittest.TestCase):
    def _apply_preview(self) -> dict[str, object]:
        return {
            "mode": "approved_rule_apply_preview",
            "stage": "RULE_APPLY_PREVIEW",
            "applied_rules_preview": {
                "bar": {"bar_minutes": 5},
                "buy": {"enabled": True},
                "sell": {"enabled": True},
            },
            "applied_patches": [{"operation": "set_value", "target_path": "bar.bar_minutes"}],
            "skipped_patches": [],
            "summary": {"patches": 1, "applied": 1, "skipped": 0},
            "warnings": [],
        }

    def _signal_context(self) -> dict[str, object]:
        return {
            "order_id": "ORDER_PIPELINE_AUDIT_1",
            "source_signal_id": "SIGNAL_PIPELINE_AUDIT_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "시장가",
        }

    def _guard(self) -> dict[str, object]:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "account_no": "12345678",
        }

    def _order_defaults(self) -> dict[str, object]:
        return {
            "request_hash": "HASH_PIPELINE_AUDIT_1",
            "lock_id": "LOCK_PIPELINE_AUDIT_1",
            "execution_id": "EXECUTION_PIPELINE_AUDIT_1",
        }

    def _runtime_snapshot(self) -> dict[str, object]:
        return {
            "locks": [],
            "existing_orders": [],
            "duplicate": False,
            "locked": False,
            "emergency_stop": False,
        }

    def _operation_state(self) -> dict[str, object]:
        return {
            "status": "READY",
            "emergency_stop": False,
            "operation_allowed": True,
        }

    def _make_temp_queue(self, root: Path) -> Path:
        runtime = root / "runtime"
        runtime.mkdir()
        queue_path = runtime / "order_queue.json"
        _write_json(queue_path, {"version": 1, "updated_at": "", "orders": []})
        return queue_path

    def _run_pipeline(self, queue_path: Path, fake_adapter: FakeSendOrderAdapter) -> dict[str, object]:
        apply_preview = self._apply_preview()
        signal_context = self._signal_context()
        guard = self._guard()
        runtime_snapshot = self._runtime_snapshot()
        order_defaults = self._order_defaults()

        order_contract = build_rule_apply_preview_execution_order_contract(
            apply_preview,
            signal_context,
            guard_defaults=guard,
            order_defaults=order_defaults,
        )
        self.assertEqual("REAL_READY", order_contract["status"])

        preview_controller = preview_execution_from_rule_apply_preview(
            apply_preview,
            signal_context,
            guard=guard,
            guard_defaults=guard,
            order_defaults=order_defaults,
        )
        self.assertEqual("READY", preview_controller["status"])
        self.assertEqual(order_contract["request_hash"], preview_controller["order_contract"]["request_hash"])

        readiness = validate_execution_readiness(
            preview_controller,
            guard,
            runtime_snapshot,
            self._operation_state(),
        )
        self.assertEqual("READY", readiness["status"])

        approval = evaluate_execution_approval(
            readiness,
            {
                "operator_confirmed": True,
                "real_trade_enabled": True,
                "real_trade_guard_ok": True,
                "emergency_stop": False,
            },
            {
                "approved": True,
                "order_id": signal_context["order_id"],
                "source_signal_id": signal_context["source_signal_id"],
            },
            runtime_snapshot,
        )
        self.assertEqual("APPROVED", approval["status"])

        commit_preview = build_queue_commit_contract_preview(
            approval,
            readiness,
            preview_controller["order_contract"],
            runtime_snapshot,
        )
        self.assertEqual("READY", commit_preview["status"])

        queue_snapshot = _read_json(queue_path)
        dry_run = dry_run_queue_commit(commit_preview, runtime_snapshot, queue_snapshot)
        self.assertEqual("DRY_RUN_READY", dry_run["status"])

        queue_commit = execute_queue_commit_from_dry_run(
            dry_run,
            queue_path,
            manual_confirmation=True,
        )
        self.assertEqual("COMMITTED", queue_commit["status"])
        self.assertTrue(queue_commit["queue_write"])
        self.assertFalse(queue_commit["runtime_write"])

        queue_review = review_queue_commit_result(
            queue_commit,
            queue_path,
            signal_context["order_id"],
        )
        self.assertEqual("REVIEW_OK", queue_review["status"])
        self.assertTrue(queue_review["send_order_ready"])

        dispatch = build_order_dispatch_contract(
            queue_review,
            {"account_no": "12345678"},
            {"broker_type": "KIWOOM", "default_hoga": "시장가"},
        )
        self.assertEqual("DISPATCH_READY", dispatch["status"])

        broker_preview = preview_broker_dispatch(
            dispatch,
            {
                "supported_brokers": ["KIWOOM"],
                "supported_sides": ["BUY", "SELL"],
                "supported_hogas": ["MARKET", "LIMIT"],
            },
            {"market_open": True, "session_open": True, "status": "OPEN"},
        )
        self.assertEqual("BROKER_DISPATCH_READY", broker_preview["status"])

        adapter_contract = build_kiwoom_send_order_adapter_contract(
            broker_preview,
            {"account_no": "12345678"},
            {"screen_no": "0101"},
        )
        self.assertEqual("SEND_ORDER_CONTRACT_READY", adapter_contract["status"])

        safety = evaluate_kiwoom_send_order_safety(
            adapter_contract,
            {"locks": [], "existing_dispatches": [], "emergency_stop": False},
            {"kiwoom_connected": True, "account_no": "12345678"},
            {"operator_final_send_confirmed": True, "emergency_stop": False},
        )
        self.assertEqual("SEND_ORDER_SAFE", safety["status"])

        call_preview = preview_kiwoom_send_order_call(
            safety,
            adapter_contract,
            {"final_call_token": "FINAL_CALL_TOKEN_PIPELINE_AUDIT_1"},
        )
        self.assertEqual("SEND_ORDER_CALL_READY", call_preview["status"])

        executor = execute_kiwoom_send_order(
            call_preview,
            fake_adapter,
            {
                "final_confirmation": True,
                "environment_send_order_enabled": True,
            },
        )
        self.assertEqual("SEND_ORDER_SENT", executor["status"])

        executor["adapter_call_count"] = len(fake_adapter.calls)
        executor["send_order_result"]["adapter_call_count"] = len(fake_adapter.calls)
        executor["send_order_result"]["dispatch_id"] = call_preview["send_order_call_preview"]["dispatch_id"]
        executor["send_order_result"]["order_id"] = call_preview["send_order_call_preview"]["order_id"]

        review = review_kiwoom_send_order_executor_result(
            executor,
            call_preview,
            {"review_enabled": True},
        )

        return {
            "order_contract": order_contract,
            "preview_controller": preview_controller,
            "readiness": readiness,
            "approval": approval,
            "commit_preview": commit_preview,
            "dry_run": dry_run,
            "queue_commit": queue_commit,
            "queue_review": queue_review,
            "dispatch": dispatch,
            "broker_preview": broker_preview,
            "adapter_contract": adapter_contract,
            "safety": safety,
            "call_preview": call_preview,
            "executor": executor,
            "executor_review": review,
        }

    def test_current_implemented_pipeline_reaches_send_order_review_ok(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        fake_adapter = FakeSendOrderAdapter(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._make_temp_queue(Path(temp_dir))
            with mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
                mock.patch("chejan_event_recorder.record_chejan_event") as chejan_recorder, \
                mock.patch("kiwoom_order_adapter.send_order_stub") as real_kiwoom_stub, \
                mock.patch("send_order_entrypoint.execute_send_order") as send_order_entrypoint, \
                mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
                results = self._run_pipeline(queue_path, fake_adapter)

        self.assertEqual("SEND_ORDER_REVIEW_OK", results["executor_review"]["status"])
        self.assertTrue(results["executor_review"]["record_ready"])
        self.assertTrue(results["executor_review"]["chejan_wait_required"])
        self.assertEqual(1, len(fake_adapter.calls))

        self.assertTrue(results["queue_commit"]["queue_write"])
        self.assertFalse(results["queue_commit"]["runtime_write"])
        for key in (
            "preview_controller",
            "readiness",
            "approval",
            "commit_preview",
            "dry_run",
            "queue_review",
            "broker_preview",
            "adapter_contract",
            "safety",
            "call_preview",
            "executor_review",
        ):
            self.assertFalse(results[key]["runtime_write"], key)
            self.assertFalse(results[key]["queue_write"], key)

        result_recorder.assert_not_called()
        chejan_recorder.assert_not_called()
        real_kiwoom_stub.assert_not_called()
        send_order_entrypoint.assert_not_called()
        broker_dispatch.assert_not_called()

        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_concept_guard_future_concepts_are_not_created_or_called(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        fake_adapter = FakeSendOrderAdapter(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = self._make_temp_queue(Path(temp_dir))
            with mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
                mock.patch("chejan_event_recorder.record_chejan_event") as chejan_recorder:
                results = self._run_pipeline(queue_path, fake_adapter)

        flattened = json.dumps(results, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("ORDER_LIFECYCLE_MANAGER", flattened)
        self.assertNotIn("POSITION_UPDATED", flattened)
        self.assertNotIn("BALANCE_UPDATED", flattened)
        self.assertNotIn("AUTO_RETRY", flattened)
        self.assertNotIn("PARTIAL_FILL", flattened)
        self.assertNotIn("FULL_FILL", flattened)
        self.assertNotIn("ORDER_REJECTED", flattened)

        result_recorder.assert_not_called()
        chejan_recorder.assert_not_called()
        self.assertEqual(1, len(fake_adapter.calls))
        self.assertEqual("SEND_ORDER_REVIEW_OK", results["executor_review"]["status"])
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
