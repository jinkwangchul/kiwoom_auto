# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from chejan_event_normalizer import normalize_kiwoom_chejan_event
from execution_candidate_service import build_execution_candidate
from execution_fill_recorder import record_execution_fill
from execution_provenance_contract import (
    option_snapshot_hash,
    validate_child_set,
)
from execution_queue_pending_service import build_execution_queue_pending
from execution_queue_writer import preview_execution_queue_write
from execution_runtime_catalog_preview import build_execution_runtime_catalog_preview
from execution_runtime_commit_plan_orchestrator import (
    run_execution_runtime_commit_plan_orchestrator,
)
from execution_runtime_commit_readiness_gate import (
    evaluate_execution_runtime_commit_readiness,
)
from execution_runtime_commit_service import commit_execution_runtime_plan
from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)
from execution_runtime_reader import read_order_executions
from execution_runtime_write_preview_orchestrator import (
    run_execution_runtime_write_preview_orchestrator,
)
from order_execution_request import build_execution_request_preview


class ExecutionProvenancePersistenceTest(unittest.TestCase):
    def _preview(self) -> dict:
        execution_request = {
            "execution_id": "EXEC_PREVIEW_ORDER_1",
            "order_id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "source_kind": "ROUTINE_SIGNAL",
            "lock_id": "LOCK_1",
            "request_hash": "a" * 64,
            "execution_trade_date": "2026-09-01",
            "request_preview": {
                "side": "BUY",
                "quantity": 10,
                "price": 40250,
                "order_action": "NEW",
            },
            "routine_provenance": {
                "routine_id": "INDICATOR_FOLLOW",
                "routine_name": "지표추종매매",
                "routine_instance_id": "INSTANCE_1",
            },
            "execution_intent": {
                "side": "BUY",
                "buy_phase": "BASE",
                "buy_round": 1,
                "budget": 402500,
                "quantity": 10,
                "price_basis": "ORDER_PRICE",
                "policy_name": "BUY_EXECUTION_POLICY",
                "policy_version": "BUY_EXECUTION_POLICY_V1",
                "execution_trade_date": "2026-09-01",
                "execution_snapshot": {
                    "policy_hash": "POLICY_HASH",
                    "approved_rule_hash": "RULE_HASH",
                    "runtime_state_hash": "STATE_HASH",
                    "calculation_hash": "CALC_HASH",
                },
                "approved_execution_options": {
                    "hoga_mode": "SINGLE",
                    "order_price_basis": "ORDER_PRICE",
                    "hoga_up": 0,
                    "hoga_down": 0,
                    "point_mode": "WITHIN",
                    "point_value": 1,
                    "point_unit": "MINUTE",
                    "point_count": 3,
                },
            },
        }
        return {
            "ok": True,
            "summary": {"ok": True, "order_id": "ORDER_1", "request_hash": "a" * 64},
            "pipeline_result": {
                "pipeline": {
                    "lock_preview": {"ok": True, "lock_id": "LOCK_1"},
                    "request_hash_preview": {"ok": True, "request_hash": "a" * 64},
                    "execution_request_preview": {
                        "ok": True,
                        "execution_request": execution_request,
                    },
                }
            },
        }

    @staticmethod
    def _approval() -> dict:
        return {
            "approved": True,
            "approval_stage": "approved",
            "blocked_reasons": [],
            "next_stage": "EXECUTION_CANDIDATE",
        }

    def _candidate_and_catalog(self) -> tuple[dict, dict, dict]:
        preview = self._preview()
        candidate = build_execution_candidate(preview, self._approval())
        pending = build_execution_queue_pending(candidate)
        queue_preview = preview_execution_queue_write(pending)
        pipeline = preview["pipeline_result"]["pipeline"]
        catalog = build_execution_runtime_catalog_preview(
            execution_request_preview=candidate["execution_request_preview"],
            lock_preview=pipeline["lock_preview"],
            request_hash_preview=pipeline["request_hash_preview"],
            queue_write_preview_result=queue_preview,
            order_candidate={"id": "ORDER_1"},
        )
        return candidate, queue_preview, catalog

    def test_single_buy_freezes_one_process_and_one_child(self) -> None:
        candidate, queue_preview, catalog = self._candidate_and_catalog()
        process = candidate["process_record"]
        child = candidate["child_contract"]

        self.assertTrue(candidate["execution_process_id"].startswith("EXEC_PROCESS_"))
        self.assertEqual(candidate["execution_process_id"], child["execution_process_id"])
        self.assertEqual(1, child["child_sequence_index"])
        self.assertEqual(1, child["child_sequence_total"])
        self.assertEqual("SINGLE_ORDER", child["child_kind"])
        self.assertEqual(process["option_snapshot_hash"], option_snapshot_hash(process["option_snapshot"]))
        self.assertEqual("WITHIN", process["option_snapshot"]["point"]["point_mode"])
        self.assertEqual(3, process["option_snapshot"]["point"]["point_count"])
        self.assertNotIn("process_record", queue_preview["order_queued_record_preview"]["execution_request"])
        self.assertEqual(candidate["execution_process_id"], catalog["provenance"]["execution_process_id"])

    def test_snapshot_is_immutable_after_source_options_change(self) -> None:
        preview = self._preview()
        candidate = build_execution_candidate(preview, self._approval())
        preview["pipeline_result"]["pipeline"]["execution_request_preview"]["execution_request"][
            "execution_intent"
        ]["approved_execution_options"].update({"point_value": 3, "point_count": 5})

        point = candidate["process_record"]["option_snapshot"]["point"]
        self.assertEqual(1, point["point_value"])
        self.assertEqual(3, point["point_count"])

    def test_process_and_child_commit_together_and_replay_is_idempotent(self) -> None:
        candidate, _queue_preview, catalog = self._candidate_and_catalog()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executions_path = root / "order_executions.json"
            locks_path = root / "order_locks.json"
            executions_path.write_text(json.dumps(default_order_executions_data()), encoding="utf-8")
            locks_path.write_text(json.dumps(default_order_locks_data()), encoding="utf-8")

            def plan() -> dict:
                current_executions = json.loads(executions_path.read_text(encoding="utf-8"))
                current_locks = json.loads(locks_path.read_text(encoding="utf-8"))
                write = run_execution_runtime_write_preview_orchestrator(
                    catalog_preview=catalog,
                    existing_order_executions_data=current_executions,
                    existing_order_locks_data=current_locks,
                )
                gate = evaluate_execution_runtime_commit_readiness(
                    write,
                    manual_execution_runtime_commit_confirmed=True,
                    manual_runtime_file_write_confirmed=True,
                )
                return run_execution_runtime_commit_plan_orchestrator(write, gate)

            first = commit_execution_runtime_plan(
                plan(),
                executions_path,
                locks_path,
                context={
                    "manual_execution_runtime_commit_confirmed": True,
                    "manual_runtime_file_write_confirmed": True,
                },
            )
            self.assertTrue(first["committed"])
            stored = json.loads(executions_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(stored["processes"]))
            self.assertEqual(1, len(stored["executions"]))
            self.assertEqual(candidate["execution_process_id"], stored["executions"][0]["execution_process_id"])

            second = commit_execution_runtime_plan(
                plan(),
                executions_path,
                locks_path,
                context={
                    "manual_execution_runtime_commit_confirmed": True,
                    "manual_runtime_file_write_confirmed": True,
                },
            )
            self.assertTrue(second["committed"])
            self.assertTrue(second["idempotent"])
            self.assertFalse(second["runtime_write"])
            stored = json.loads(executions_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(stored["processes"]))
            self.assertEqual(1, len(stored["executions"]))

    def test_legacy_and_broken_new_reference_are_distinguished(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "order_executions.json"
            path.write_text(json.dumps({"version": 1, "executions": []}), encoding="utf-8")
            legacy = read_order_executions(path)
            self.assertTrue(legacy["ok"])
            self.assertEqual("LEGACY_MISSING", legacy["provenance_status"])

            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "processes": [],
                        "executions": [
                            {
                                "execution_id": "EXEC_1",
                                "order_id": "ORDER_1",
                                "execution_process_id": "MISSING_PROCESS",
                                "option_snapshot_hash": "HASH",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            broken = read_order_executions(path)
            self.assertFalse(broken["ok"])
            self.assertEqual("INVALID", broken["status"])

    def test_cancel_reuses_parent_process_without_snapshot_copy(self) -> None:
        original = build_execution_candidate(self._preview(), self._approval())
        preview = self._preview()
        request = preview["pipeline_result"]["pipeline"]["execution_request_preview"]["execution_request"]
        request.update(
            {
                "execution_id": "EXEC_CANCEL_1",
                "execution_process_id": original["execution_process_id"],
                "option_snapshot_hash": original["option_snapshot_hash"],
            }
        )
        request["request_preview"]["order_action"] = "CANCEL"
        cancel = build_execution_candidate(preview, self._approval())
        self.assertEqual(original["execution_process_id"], cancel["execution_process_id"])
        self.assertEqual("CANCEL", cancel["child_contract"]["child_kind"])
        self.assertIsNone(cancel["process_record"])

    def test_operation_command_uses_separate_source_command_identity(self) -> None:
        request_preview = build_execution_request_preview(
            {
                "id": "CLOSE_LIQUIDATION_COMMAND_1",
                "source": "operation_command",
                "source_signal_id": "COMMAND_1",
                "command_id": "COMMAND_1",
                "side": "SELL",
            },
            {"operator_confirmed": True, "account_no": "12345678"},
            {
                "unresolved": False,
                "adapter_request_preview": {
                    "request_preview": {"side": "SELL", "quantity": 10, "price": 0}
                },
            },
            {"ok": True},
            {"unresolved": False, "lock_id": "LOCK_COMMAND_1"},
            {
                "unresolved": False,
                "request_hash": "b" * 64,
                "hash_source": {
                    "order_id": "CLOSE_LIQUIDATION_COMMAND_1",
                    "source_signal_id": "COMMAND_1",
                },
            },
        )
        self.assertTrue(request_preview["ok"])
        request = request_preview["execution_request"]
        self.assertEqual("OPERATION_COMMAND", request["source_kind"])
        self.assertEqual("COMMAND_1", request["source_command_id"])
        self.assertEqual("COMMAND_1", request["source_signal_id"])

    def test_same_process_id_with_different_payload_is_invalid(self) -> None:
        candidate, _queue_preview, catalog = self._candidate_and_catalog()
        existing_process = deepcopy(candidate["process_record"])
        existing_process["option_snapshot"]["point"]["point_count"] = 9
        existing = default_order_executions_data()
        existing["processes"] = [existing_process]
        result = run_execution_runtime_write_preview_orchestrator(
            catalog_preview=catalog,
            existing_order_executions_data=existing,
            existing_order_locks_data=default_order_locks_data(),
        )
        self.assertEqual("INVALID", result["status"])
        self.assertIn("EXECUTION_PROCESS_PAYLOAD_CONFLICT", result["issues"])

    def test_contract_only_multi_time_and_multi_hoga_children(self) -> None:
        time_children = [
            {
                "execution_process_id": "PROCESS_TIME",
                "execution_id": f"EXEC_TIME_{index}",
                "child_sequence_index": index,
                "child_sequence_total": 3,
                "child_kind": "TIME_SLICE",
                "child_plan": {"scheduled_offset_ms": (index - 1) * 30000},
            }
            for index in (1, 2, 3)
        ]
        hoga_children = [
            {
                "execution_process_id": "PROCESS_HOGA",
                "execution_id": f"EXEC_HOGA_{index}",
                "child_sequence_index": index,
                "child_sequence_total": 3,
                "child_kind": "HOGA_LEVEL",
                "child_plan": {"hoga_offset_ticks": offset},
            }
            for index, offset in enumerate((0, -1, -2), start=1)
        ]
        self.assertEqual([], validate_child_set(time_children))
        self.assertEqual([], validate_child_set(hoga_children))

    def test_fid_908_exact_and_fill_process_reference(self) -> None:
        raw = {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "received_at": "2026-09-01T09:30:01+09:00",
            "fid_raw_values": {"908": "09300012"},
            "fid_values": {
                "9201": "12345678",
                "9203": "BROKER_1",
                "9001": "A005070",
                "302": "코스모신소재",
                "913": "체결",
                "907": "2",
                "900": "10",
                "911": "3",
                "902": "7",
                "910": "40250",
                "901": "40250",
                "908": "09300012",
                "909": "FILL_NO_1",
            },
        }
        normalized = normalize_kiwoom_chejan_event(
            raw,
            context={"execution_trade_date": "2026-09-01"},
        )
        self.assertEqual("09300012", normalized["broker_execution_time_raw"])
        self.assertEqual("2026-09-01T09:30:00+09:00", normalized["broker_execution_datetime"])
        self.assertEqual("BROKER_FID_908", normalized["execution_time_source"])
        self.assertEqual("FILL_NO_1", normalized["broker_execution_no"])

        with tempfile.TemporaryDirectory() as temp_dir:
            fill_path = Path(temp_dir) / "fills.json"
            result = record_execution_fill(
                {
                    "recorded": True,
                    "next_stage": "FILL_RECORD_REQUIRED",
                    "order_id": "ORDER_1",
                    "order_queued_id": "ORDER_QUEUED_1",
                    "execution_id": "EXEC_1",
                    "execution_process_id": "PROCESS_1",
                    "execution_trade_date": "2026-09-01",
                    "request_hash": "HASH_1",
                    "lock_id": "LOCK_1",
                },
                normalized,
                fill_path,
                context={"manual_fill_record_confirmed": True},
            )
            self.assertTrue(result["fill_recorded"])
            self.assertEqual("PROCESS_1", result["fill_record"]["execution_process_id"])
            self.assertEqual("EXACT", result["fill_record"]["execution_time_quality"])

    def test_fid_908_malformed_uses_received_at_approximation(self) -> None:
        raw = {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "received_at": "2026-09-01T09:30:01+09:00",
            "fid_values": {
                "9201": "12345678", "9203": "BROKER_1", "9001": "A005070",
                "302": "코스모신소재", "913": "체결", "907": "2", "900": "10",
                "911": "3", "902": "7", "910": "40250", "901": "40250", "908": "bad",
            },
        }
        normalized = normalize_kiwoom_chejan_event(raw)
        self.assertIsNone(normalized["broker_execution_datetime"])
        self.assertEqual("LOCAL_RECEIVED_AT", normalized["execution_time_source"])
        self.assertEqual("APPROXIMATE", normalized["execution_time_quality"])

    def test_three_partial_fills_keep_process_and_independent_fill_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fill_path = Path(temp_dir) / "fills.json"
            fill_ids: list[str] = []
            for index, (cumulative, remaining) in enumerate(((3, 7), (5, 5), (10, 0)), start=1):
                event_type = "FULL_FILL" if remaining == 0 else "PARTIAL_FILL"
                event = {
                    "normalized": True,
                    "event_type": event_type,
                    "broker": "KIWOOM",
                    "source": "kiwoom_chejan",
                    "gubun": "0",
                    "broker_order_no": "BROKER_1",
                    "account_no": "12345678",
                    "code": "005070",
                    "side": "BUY",
                    "order_quantity": 10,
                    "filled_quantity": cumulative,
                    "filled_price": 40250 + index,
                    "remaining_quantity": remaining,
                    "order_price": 40250,
                    "received_at": f"2026-09-01T09:30:0{index}+09:00",
                    "broker_execution_time_raw": f"09300{index}",
                    "execution_no": f"FILL_NO_{index}",
                    "raw_event": {},
                }
                result = record_execution_fill(
                    {
                        "recorded": True,
                        "next_stage": "FILL_RECORD_REQUIRED",
                        "order_id": "ORDER_1",
                        "order_queued_id": "ORDER_QUEUED_1",
                        "execution_id": "EXEC_1",
                        "execution_process_id": "PROCESS_1",
                        "execution_trade_date": "2026-09-01",
                        "request_hash": "HASH_1",
                        "lock_id": "LOCK_1",
                    },
                    event,
                    fill_path,
                    context={"manual_fill_record_confirmed": True},
                )
                self.assertTrue(result["fill_recorded"])
                self.assertEqual("PROCESS_1", result["execution_process_id"])
                fill_ids.append(result["fill_id"])
            self.assertEqual(3, len(set(fill_ids)))


if __name__ == "__main__":
    unittest.main()
