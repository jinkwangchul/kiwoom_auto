# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path

from close_liquidation_transition_service import DOMAIN_CLOSE
from transition_evidence_reader import (
    COMMAND_REQUEST_SCOPE,
    TIME_POLICY_SCOPE,
    TransitionEvidenceScope,
)
from transition_production_guard import (
    EVIDENCE_COMPLETE,
    EVIDENCE_UNKNOWN,
    REASON_INSUFFICIENT_EVIDENCE,
    evaluate_production_transition,
)


class TransitionProductionGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.queue_path = root / "order_queue.json"
        self.fills_path = root / "fills.json"
        self.queue_path.write_text(
            json.dumps({"version": 1, "orders": []}),
            encoding="utf-8",
        )
        self.fills_path.write_text(
            json.dumps({"version": 1, "fills": []}),
            encoding="utf-8",
        )
        self.state = {"close_routine_final_sell_ordered": False}

    def tearDown(self):
        self.temp_dir.cleanup()

    def _evaluate(self, scope, current="루틴매도신호", requested="시장가"):
        return evaluate_production_transition(
            policy_domain=DOMAIN_CLOSE,
            current_policy=current,
            requested_policy=requested,
            queue_path=self.queue_path,
            fills_path=self.fills_path,
            runtime_state=self.state,
            runtime_routine_instance_id="routine-instance-1",
            scope=scope,
        )

    def test_command_request_without_command_id_is_allowed(self):
        result = self._evaluate(
            TransitionEvidenceScope(
                scope_type=COMMAND_REQUEST_SCOPE,
                stock_code="005930",
                trade_date="2026-07-27",
                routine_instance_id="routine-instance-1",
                transition_requested_at="2026-07-27 13:30:00",
            )
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.evidence_status, EVIDENCE_COMPLETE)

    def test_time_policy_without_command_id_is_allowed(self):
        result = self._evaluate(
            TransitionEvidenceScope(
                scope_type=TIME_POLICY_SCOPE,
                stock_code="005930",
                trade_date="2026-07-27",
                routine_instance_id="routine-instance-1",
                auto_close_requested_at="2026-07-27 13:30:00",
                source="TIME_POLICY",
            ),
            current="현재가",
            requested="현재가",
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.evidence_status, EVIDENCE_COMPLETE)

    def test_unknown_is_blocked_before_decision(self):
        self.queue_path.write_text("{", encoding="utf-8")
        result = self._evaluate(
            TransitionEvidenceScope(
                scope_type=COMMAND_REQUEST_SCOPE,
                stock_code="005930",
                trade_date="2026-07-27",
                routine_instance_id="routine-instance-1",
                transition_requested_at="2026-07-27 13:30:00",
            )
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, REASON_INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.evidence_status, EVIDENCE_UNKNOWN)
        self.assertIsNone(result.decision)

    def test_complete_evidence_can_be_rejected_by_transition_service(self):
        result = self._evaluate(
            TransitionEvidenceScope(
                scope_type=COMMAND_REQUEST_SCOPE,
                stock_code="005930",
                trade_date="2026-07-27",
                routine_instance_id="routine-instance-1",
                transition_requested_at="2026-07-27 13:30:00",
            ),
            current="시장가",
            requested="현재가",
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.evidence_status, EVIDENCE_COMPLETE)
        self.assertIsNotNone(result.decision)


if __name__ == "__main__":
    unittest.main()
