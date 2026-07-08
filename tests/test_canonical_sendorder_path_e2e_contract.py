# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from broker_dispatch_preview import preview_broker_dispatch
from execution_order_dispatch_builder import build_order_dispatch_contract
from execution_queue_commit_result_review import review_queue_commit_result
from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract
from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call
from kiwoom_send_order_executor import execute_kiwoom_send_order
from kiwoom_send_order_executor_result_review import review_kiwoom_send_order_executor_result
from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety


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


class FakeSendOrderAdapter:
    def __init__(self, result: object = 0) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.result


class CanonicalSendOrderPathE2EContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _queue_item(self, commit_id: str) -> dict[str, object]:
        return {
            "status": "ORDER_QUEUED",
            "commit_id": commit_id,
            "order_id": "ORDER_CANONICAL_SEND_1",
            "source_order_id": "ORDER_CANONICAL_SEND_1",
            "source_signal_id": "SIGNAL_CANONICAL_SEND_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "MARKET",
            "request_hash": "HASH_CANONICAL_SEND_1",
            "send_order_called": False,
        }

    def _make_queue_and_commit_result(self, root: Path) -> tuple[Path, dict[str, object]]:
        commit_id = "COMMIT_CANONICAL_SEND_1"
        queue_item = self._queue_item(commit_id)
        queue_path = root / "runtime" / "order_queue.json"
        queue_path.parent.mkdir()
        _write_json(queue_path, {"version": 1, "updated_at": "", "orders": [queue_item]})
        commit_report = {
            "before_hash": "BEFORE_HASH_CANONICAL_SEND_1",
            "after_hash": "AFTER_HASH_CANONICAL_SEND_1",
            "committed_record": queue_item,
            "rollback_attempted": False,
            "rollback_succeeded": False,
            "restored_from_backup": False,
        }
        commit_result = {
            "status": "COMMITTED",
            "commit_id": commit_id,
            "commit_report": commit_report,
            "issues": [],
            "warnings": [],
            "runtime_write": False,
            "queue_write": True,
            "queue_commit_called": True,
            "send_order_called": False,
        }
        return queue_path, commit_result

    def _run_canonical_path(
        self,
        queue_path: Path,
        commit_result: dict[str, object],
        adapter: FakeSendOrderAdapter,
        *,
        expected_executor_status: str = "SEND_ORDER_SENT",
    ) -> dict[str, object]:
        queue_review = review_queue_commit_result(commit_result, queue_path, "ORDER_CANONICAL_SEND_1")
        self.assertEqual("REVIEW_OK", queue_review["status"])

        dispatch = build_order_dispatch_contract(
            queue_review,
            {"account_no": "12345678"},
            {"broker_type": "KIWOOM", "default_hoga": "MARKET"},
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
            {"final_call_token": "FINAL_CALL_TOKEN_CANONICAL_SEND_1"},
        )
        self.assertEqual("SEND_ORDER_CALL_READY", call_preview["status"])

        executor = execute_kiwoom_send_order(
            call_preview,
            adapter,
            {"final_confirmation": True, "environment_send_order_enabled": True},
        )
        self.assertEqual(expected_executor_status, executor["status"])

        executor["adapter_call_count"] = len(adapter.calls)
        executor["send_order_result"]["adapter_call_count"] = len(adapter.calls)
        executor["send_order_result"]["dispatch_id"] = call_preview["send_order_call_preview"]["dispatch_id"]
        executor["send_order_result"]["order_id"] = call_preview["send_order_call_preview"]["order_id"]

        executor_review = review_kiwoom_send_order_executor_result(
            executor,
            call_preview,
            {"review_enabled": True},
        )

        return {
            "queue_review": queue_review,
            "dispatch": dispatch,
            "broker_preview": broker_preview,
            "adapter_contract": adapter_contract,
            "safety": safety,
            "call_preview": call_preview,
            "executor": executor,
            "executor_review": executor_review,
        }

    def test_canonical_sendorder_path_reaches_review_ok_with_fake_adapter_once(self) -> None:
        adapter = FakeSendOrderAdapter(0)
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path, commit_result = self._make_queue_and_commit_result(Path(temp_dir))
            with mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
                mock.patch("chejan_event_recorder.record_chejan_event") as chejan_recorder, \
                mock.patch("send_order_entrypoint.execute_send_order") as send_order_entrypoint, \
                mock.patch("execution_final_send_gate_call_orchestrator.call_final_send_gate_after_open_policy") as final_gate_chain, \
                mock.patch("execution_send_order_entrypoint_orchestrator.orchestrate_send_order_entrypoint") as entrypoint_chain, \
                mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch_chain, \
                mock.patch("execution_broker_result_review.review_broker_dispatch_result") as broker_result_review, \
                mock.patch("lifecycle_commit_dry_run.dry_run_lifecycle_commit") as lifecycle_dry_run:
                results = self._run_canonical_path(queue_path, commit_result, adapter)

        self.assertEqual("SEND_ORDER_REVIEW_OK", results["executor_review"]["status"])
        self.assertEqual(1, len(adapter.calls))
        self.assertTrue(results["executor_review"]["record_ready"])
        self.assertTrue(results["executor_review"]["chejan_wait_required"])

        for key in ("queue_review", "broker_preview", "adapter_contract", "safety", "call_preview", "executor_review"):
            self.assertFalse(results[key]["runtime_write"], key)
            self.assertFalse(results[key]["queue_write"], key)

        result_recorder.assert_not_called()
        chejan_recorder.assert_not_called()
        send_order_entrypoint.assert_not_called()
        final_gate_chain.assert_not_called()
        entrypoint_chain.assert_not_called()
        broker_dispatch_chain.assert_not_called()
        broker_result_review.assert_not_called()
        lifecycle_dry_run.assert_not_called()

        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_alternate_chains_are_not_called_when_executor_fails(self) -> None:
        adapter = FakeSendOrderAdapter(-1)
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path, commit_result = self._make_queue_and_commit_result(Path(temp_dir))
            with mock.patch("send_order_entrypoint.execute_send_order") as send_order_entrypoint, \
                mock.patch("execution_final_send_gate_call_orchestrator.call_final_send_gate_after_open_policy") as final_gate_chain, \
                mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch_chain, \
                mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder:
                results = self._run_canonical_path(
                    queue_path,
                    commit_result,
                    adapter,
                    expected_executor_status="SEND_ORDER_FAILED",
                )

        self.assertEqual("SEND_ORDER_REVIEW_FAILED", results["executor_review"]["status"])
        self.assertEqual(1, len(adapter.calls))
        send_order_entrypoint.assert_not_called()
        final_gate_chain.assert_not_called()
        broker_dispatch_chain.assert_not_called()
        result_recorder.assert_not_called()
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
