# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import routine_signal_queue
from execution_candidate_service import build_execution_candidate
from execution_queue_pending_service import build_execution_queue_pending
from execution_queue_writer import preview_execution_queue_write
from order_execution_request import build_execution_request_preview
from order_queue import signal_to_order_candidate


class ExecutionIntentProvenancePipelineTest(unittest.TestCase):
    def test_routine_signal_queue_preserves_intent_before_order_candidate(self) -> None:
        intent = {
            "side": "BUY",
            "routine_type": "INDICATOR_FOLLOW",
            "routine_instance_id": "INSTANCE_A",
            "buy_phase": "BASE",
            "buy_round": 1,
            "budget": 1000,
            "quantity": 1,
            "price_basis": "CURRENT_PRICE",
            "price": 1000,
            "hoga": "LIMIT",
            "hoga_mode": "SINGLE",
            "source_signal_id": None,
            "cycle_identity": None,
            "confirmed_previous_round": 0,
            "unresolved": False,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = Path(tmpdir) / "routine_signals.json"
            with mock.patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                queued = routine_signal_queue.enqueue_routine_signal(
                    {
                        "signal": "BUY",
                        "reason": "test",
                        "routine_type": "INDICATOR_FOLLOW",
                        "routine_instance_id": "INSTANCE_A",
                        "execution_intent": intent,
                    },
                    routine="지표추종매매",
                    code="005930",
                    name="삼성전자",
                    tick_key="TICK_1",
                )
            signal = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]
            order = signal_to_order_candidate(signal, 0)

        self.assertEqual("queued", queued["status"])
        self.assertEqual(signal["id"], signal["execution_intent"]["source_signal_id"])
        self.assertEqual("CANDIDATE_READY", order["candidate_status"])
        self.assertEqual(signal["execution_intent"], order["execution_intent"])

    def test_intent_is_preserved_through_request_candidate_pending_and_queue(self) -> None:
        intent = {
            "side": "BUY",
            "routine_type": "INDICATOR_FOLLOW",
            "routine_instance_id": "INSTANCE_A",
            "buy_phase": "REPEAT",
            "buy_round": 2,
            "source_signal_id": "SIG_1",
            "cycle_identity": "CYCLE_1",
        }
        order = {
            "id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "execution_intent": intent,
            "order_provenance": {
                "routine_type": "INDICATOR_FOLLOW",
                "routine_instance_id": "INSTANCE_A",
                "cycle_identity": "CYCLE_1",
            },
        }
        request_preview = build_execution_request_preview(
            order,
            {"ok": True},
            {"unresolved": False, "adapter_request_preview": {"request_preview": {"code": "005930"}}},
            {"ok": True},
            {"unresolved": False, "lock_id": "LOCK_1"},
            {"unresolved": False, "request_hash": "a" * 64},
        )
        self.assertEqual(intent, request_preview["execution_request"]["execution_intent"])

        preview = {
            "ok": True,
            "summary": {"order_id": "ORDER_1", "request_hash": "a" * 64},
            "pipeline": {
                "request_hash_preview": {"request_hash": "a" * 64},
                "lock_preview": {"lock_id": "LOCK_1"},
                "execution_request_preview": request_preview,
            },
        }
        candidate = build_execution_candidate(
            preview,
            {"approved": True, "next_stage": "EXECUTION_CANDIDATE"},
        )
        pending = build_execution_queue_pending(candidate)
        queued = preview_execution_queue_write(pending)
        record = queued["order_queued_record_preview"]

        self.assertEqual(intent, candidate["execution_intent"])
        self.assertEqual(intent, pending["execution_intent"])
        self.assertEqual(intent, record["execution_intent"])
        self.assertEqual("INSTANCE_A", record["routine_provenance"]["routine_instance_id"])
        self.assertEqual("SIG_1", record["source_signal_id"])


if __name__ == "__main__":
    unittest.main()
