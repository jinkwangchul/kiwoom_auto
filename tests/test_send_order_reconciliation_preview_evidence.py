# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from chejan_event_recorder import (
    build_order_reconciliation_preview,
    inspect_incomplete_order_reconciliation,
)
from send_order_reconciliation_evidence import (
    record_send_order_reconciliation_evidence,
)


def _queue_record(*, broker_order_no: str = "") -> dict[str, object]:
    return {
        "id": "ORDER_QUEUED_1",
        "order_id": "ORDER_1",
        "request_hash": "a" * 64,
        "lock_id": "LOCK_1",
        "execution_id": "EXEC_1",
        "source_signal_id": "SIG_1",
        "status": "SEND_UNCERTAIN",
        "dispatch_claim_id": "CLAIM_1",
        "send_order_attempt_id": "ATTEMPT_1",
        "broker_order_no": broker_order_no,
        "quantity": 10,
        "original_order_quantity": 10,
        "cumulative_filled_quantity": 0,
        "remaining_quantity": 10,
        "fill_count": 0,
        "manual_reconciliation_required": True,
        "chejan_events": [],
    }


def _event(broker_order_no: str) -> dict[str, object]:
    request = {
        "rqname": "BUY_005930_A1B2C3D4",
        "screen_no": "0101",
        "order_id": "ORDER_1",
        "dispatch_claim_id": "CLAIM_1",
        "send_order_attempt_id": "ATTEMPT_1",
        "execution_id": "EXEC_1",
        "signal_id": "SIG_1",
        "market_route": "SOR",
        "order_type": 11,
        "code": "005930",
        "login_session_id": "SESSION_1",
        "connection_epoch": 3,
    }
    return {
        "source": "kiwoom_order_tr",
        "rqname": request["rqname"],
        "screen_no": request["screen_no"],
        "trcode": "KOA_NORMAL_BUY_KP_ORD",
        "record_name": "주문",
        "prev_next": "0",
        "broker_order_no": broker_order_no,
        "order_no_error": "",
        "login_session_id": "SESSION_1",
        "connection_epoch": 3,
        "received_at": "2026-09-29 18:10:00.000+09:00",
        "send_order_request": request,
    }


class SendOrderReconciliationPreviewEvidenceTest(unittest.TestCase):
    def _paths(
        self,
        temp_dir: str,
        record: dict[str, object],
    ) -> tuple[Path, Path, Path]:
        root = Path(temp_dir)
        queue_path = root / "order_queue.json"
        fills_path = root / "fills.json"
        evidence_path = root / "evidence.jsonl"
        queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "revision": 7,
                    "updated_at": "before",
                    "orders": [record],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        fills_path.write_text(
            json.dumps({"fills": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        return queue_path, fills_path, evidence_path

    def test_transport_broker_order_no_becomes_read_only_preview_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record = _queue_record()
            queue_path, fills_path, evidence_path = self._paths(
                temp_dir,
                record,
            )
            before = queue_path.read_bytes()
            recorded = record_send_order_reconciliation_evidence(
                _event("987654"),
                evidence_path=evidence_path,
            )

            self.assertTrue(recorded["recorded"])
            inspection = inspect_incomplete_order_reconciliation(
                queue_path,
                record,
                fills_path=fills_path,
                transport_evidence_path=evidence_path,
            )
            preview = build_order_reconciliation_preview(
                queue_path,
                record,
                fills_path=fills_path,
                transport_evidence_path=evidence_path,
            )

            self.assertTrue(inspection["inspection_ok"])
            self.assertEqual(
                "987654",
                inspection["transport_broker_order_no"],
            )
            self.assertEqual(
                1,
                inspection["send_order_transport_evidence_count"],
            )
            self.assertEqual(
                "RECONCILIATION_CANDIDATE",
                inspection["reconciliation_candidate_status"],
            )
            self.assertTrue(preview["preview_ready"])
            self.assertEqual("987654", preview["proposed_broker_order_no"])
            self.assertEqual(before, queue_path.read_bytes())

    def test_transport_broker_order_no_conflict_blocks_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record = _queue_record(broker_order_no="111111")
            queue_path, fills_path, evidence_path = self._paths(
                temp_dir,
                record,
            )
            record_send_order_reconciliation_evidence(
                _event("222222"),
                evidence_path=evidence_path,
            )

            inspection = inspect_incomplete_order_reconciliation(
                queue_path,
                record,
                fills_path=fills_path,
                transport_evidence_path=evidence_path,
            )
            preview = build_order_reconciliation_preview(
                queue_path,
                record,
                fills_path=fills_path,
                transport_evidence_path=evidence_path,
            )

            self.assertEqual(
                "BLOCKED",
                inspection["reconciliation_candidate_status"],
            )
            self.assertIn(
                "broker_order_no mismatch between queue and transport evidence",
                inspection["blocked_reasons"],
            )
            self.assertFalse(preview["preview_ready"])

    def test_multiple_transport_order_numbers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record = _queue_record()
            queue_path, fills_path, evidence_path = self._paths(
                temp_dir,
                record,
            )
            record_send_order_reconciliation_evidence(
                _event("111111"),
                evidence_path=evidence_path,
            )
            record_send_order_reconciliation_evidence(
                _event("222222"),
                evidence_path=evidence_path,
            )

            inspection = inspect_incomplete_order_reconciliation(
                queue_path,
                record,
                fills_path=fills_path,
                transport_evidence_path=evidence_path,
            )

            self.assertEqual(
                "BLOCKED",
                inspection["reconciliation_candidate_status"],
            )
            self.assertEqual("", inspection["transport_broker_order_no"])
            self.assertIn(
                "broker_order_no mismatch across transport evidence",
                inspection["blocked_reasons"],
            )

    def test_without_explicit_evidence_path_legacy_inspection_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record = _queue_record()
            queue_path, fills_path, _evidence_path = self._paths(
                temp_dir,
                record,
            )
            inspection = inspect_incomplete_order_reconciliation(
                queue_path,
                record,
                fills_path=fills_path,
            )
            self.assertEqual(
                0,
                inspection["send_order_transport_evidence_count"],
            )
            self.assertEqual("", inspection["transport_broker_order_no"])


if __name__ == "__main__":
    unittest.main()
