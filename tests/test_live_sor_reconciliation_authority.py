# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from live_sor_reconciliation_authority import (
    AUTHORIZATION_CONTRACT,
    DEFAULT_CERTIFICATE_PATH,
    inspect_live_sor_validation_candidate,
    inspect_live_sor_validation_certificate,
    issue_live_sor_validation_certificate,
)


class LiveSorReconciliationAuthorityTest(unittest.TestCase):
    def _paths(self) -> tuple[tempfile.TemporaryDirectory, Path, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        return (
            tmp,
            root / "order_queue.json",
            root / "events.jsonl",
            root / "certificate.json",
        )

    @staticmethod
    def _identity() -> dict[str, str]:
        return {
            "order_id": "ORDER_1",
            "dispatch_claim_id": "CLAIM_1",
            "send_order_attempt_id": "ATTEMPT_1",
        }

    def _write_ready_queue(self, path: Path, *, is_sor: bool = True) -> None:
        identity = self._identity()
        payload = {
            "version": 1,
            "revision": 9,
            "orders": [
                {
                    **identity,
                    "status": "FILLED",
                    "market_route": "SOR",
                    "code": "005930",
                    "account_no": "12345678",
                    "actual_order_sent": True,
                    "manual_reconciliation_required": False,
                    "chejan_reconciliation_required": False,
                    "broker_order_no": "987654",
                    "chejan_events": [
                        {
                            "event_identity": "CHEJAN_1",
                            "broker_order_no": "987654",
                            "event_type": "FULL_FILL",
                            "normalized_event": {
                                "broker_order_no": "987654",
                                "account_no": "12345678",
                                "market_route": "SOR",
                                "event_type": "FULL_FILL",
                                "code": "005930",
                                "filled_quantity": 10,
                                "remaining_quantity": 0,
                                "is_sor": is_sor,
                            },
                        }
                    ],
                }
            ],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_transport_evidence(
        self,
        path: Path,
        *,
        market_route: str = "SOR",
    ) -> None:
        identity = self._identity()
        base = {
            "schema": "KIWOOM_SEND_ORDER_RECONCILIATION_EVIDENCE_V1",
            "login_session_id": "SESSION_1",
            "connection_epoch": 7,
            "market_route": market_route,
            "order_type": 11,
            "code": "005930",
            "identity": identity,
            "request_context": {
                **identity,
                "market_route": "SOR",
            },
        }
        records = [
            {
                **base,
                "source": "kiwoom_order_tr",
                "broker_evidence": {
                    "broker_order_no": "987654",
                },
            },
            {
                **base,
                "source": "kiwoom_message",
                "broker_evidence": {
                    "broker_order_no": "",
                },
            },
        ]
        path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False) + "\n"
                for item in records
            ),
            encoding="utf-8",
        )

    def test_candidate_requires_transport_and_chejan_sor_evidence(self) -> None:
        _tmp, queue_path, evidence_path, _certificate_path = self._paths()
        self._write_ready_queue(queue_path)
        self._write_transport_evidence(evidence_path)

        result = inspect_live_sor_validation_candidate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
        )

        self.assertTrue(result["ready"], result)
        self.assertEqual("VALIDATION_EVIDENCE_READY", result["state"])
        self.assertEqual("987654", result["broker_order_no"])
        self.assertEqual(2, result["transport_record_count"])
        self.assertEqual(1, result["chejan_event_count"])
        self.assertTrue(result["evidence_digest"])
        self.assertFalse(result["write_performed"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_api_called"])

        self._write_ready_queue(queue_path, is_sor=False)
        blocked = inspect_live_sor_validation_candidate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
        )
        self.assertFalse(blocked["ready"])
        self.assertIn(
            "matching Chejan is_sor=true evidence is missing",
            blocked["blocked_reasons"],
        )

        self._write_ready_queue(queue_path, is_sor=True)
        self._write_transport_evidence(evidence_path, market_route="KRX")
        wrong_route = inspect_live_sor_validation_candidate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
        )
        self.assertFalse(wrong_route["ready"])
        self.assertIn(
            "correlated transport market_route is not exclusively SOR",
            wrong_route["blocked_reasons"],
        )

    def test_certificate_issue_requires_explicit_controlled_confirmation(self) -> None:
        _tmp, queue_path, evidence_path, certificate_path = self._paths()
        self._write_ready_queue(queue_path)
        self._write_transport_evidence(evidence_path)

        blocked = issue_live_sor_validation_certificate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            certificate_path=certificate_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
            controlled_validation_confirmed=False,
            approved_by="operator",
        )

        self.assertFalse(blocked["issued"])
        self.assertFalse(certificate_path.exists())

    def test_production_certificate_rejects_noncanonical_input_paths(self) -> None:
        _tmp, queue_path, evidence_path, _certificate_path = self._paths()
        self._write_ready_queue(queue_path)
        self._write_transport_evidence(evidence_path)

        blocked = issue_live_sor_validation_certificate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            certificate_path=DEFAULT_CERTIFICATE_PATH,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
            controlled_validation_confirmed=True,
            approved_by="controlled-validation-test",
        )

        self.assertFalse(blocked["issued"])
        self.assertEqual(
            [
                "Production SOR certificate requires canonical queue and evidence paths"
            ],
            blocked["blocked_reasons"],
        )

    def test_certificate_is_session_bound_and_hash_verified(self) -> None:
        _tmp, queue_path, evidence_path, certificate_path = self._paths()
        self._write_ready_queue(queue_path)
        self._write_transport_evidence(evidence_path)

        issued = issue_live_sor_validation_certificate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            certificate_path=certificate_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
            controlled_validation_confirmed=True,
            approved_by="controlled-validation-test",
        )

        self.assertTrue(issued["issued"], issued)
        certificate = issued["certificate"]
        self.assertEqual(AUTHORIZATION_CONTRACT, certificate["authorization_contract"])
        self.assertTrue(certificate["certificate_hash"])

        valid = inspect_live_sor_validation_certificate(
            certificate_path,
            queue_path=queue_path,
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
        )
        self.assertTrue(valid["valid"], valid)
        self.assertEqual("AUTHORIZED", valid["state"])

        wrong_epoch = inspect_live_sor_validation_certificate(
            certificate_path,
            queue_path=queue_path,
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=8,
            expected_account_no="12345678",
            expected_server_type="REAL",
        )
        self.assertFalse(wrong_epoch["valid"])
        self.assertIn(
            "live SOR certificate connection epoch mismatch",
            wrong_epoch["blocked_reasons"],
        )

        payload = json.loads(certificate_path.read_text(encoding="utf-8"))
        payload["broker_order_no"] = "TAMPERED"
        certificate_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tampered = inspect_live_sor_validation_certificate(
            certificate_path,
            queue_path=queue_path,
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
        )
        self.assertFalse(tampered["valid"])
        self.assertIn(
            "live SOR validation certificate hash mismatch",
            tampered["blocked_reasons"],
        )

    def test_certificate_is_bound_to_account_and_real_server(self) -> None:
        _tmp, queue_path, evidence_path, certificate_path = self._paths()
        self._write_ready_queue(queue_path)
        self._write_transport_evidence(evidence_path)

        issued = issue_live_sor_validation_certificate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            certificate_path=certificate_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
            controlled_validation_confirmed=True,
            approved_by="controlled-validation-test",
        )
        self.assertTrue(issued["issued"], issued)

        wrong_account = inspect_live_sor_validation_certificate(
            certificate_path,
            queue_path=queue_path,
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="87654321",
            expected_server_type="REAL",
        )
        self.assertFalse(wrong_account["valid"])
        self.assertIn(
            "live SOR certificate account mismatch",
            wrong_account["blocked_reasons"],
        )

        simulation = inspect_live_sor_validation_certificate(
            certificate_path,
            queue_path=queue_path,
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="SIMULATION",
        )
        self.assertFalse(simulation["valid"])
        self.assertIn(
            "current server type is not REAL",
            simulation["blocked_reasons"],
        )

        sim_path = certificate_path.with_name("simulation-certificate.json")
        sim_issue = issue_live_sor_validation_certificate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            certificate_path=sim_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="SIMULATION",
            controlled_validation_confirmed=True,
            approved_by="controlled-validation-test",
        )
        self.assertFalse(sim_issue["issued"])
        self.assertFalse(sim_path.exists())
        self.assertIn(
            "Production live SOR validation requires REAL server",
            sim_issue["blocked_reasons"],
        )

    def test_certificate_is_invalid_when_source_evidence_changes(self) -> None:
        _tmp, queue_path, evidence_path, certificate_path = self._paths()
        self._write_ready_queue(queue_path)
        self._write_transport_evidence(evidence_path)

        issued = issue_live_sor_validation_certificate(
            queue_path,
            self._identity(),
            evidence_path=evidence_path,
            certificate_path=certificate_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
            controlled_validation_confirmed=True,
            approved_by="controlled-validation-test",
        )
        self.assertTrue(issued["issued"], issued)

        first_line = evidence_path.read_text(encoding="utf-8").splitlines()[0]
        evidence_path.write_text(first_line + "\n", encoding="utf-8")
        invalid = inspect_live_sor_validation_certificate(
            certificate_path,
            queue_path=queue_path,
            evidence_path=evidence_path,
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
        )
        self.assertFalse(invalid["valid"])
        self.assertIn(
            "live SOR source reconciliation evidence is no longer valid",
            invalid["blocked_reasons"],
        )
        self.assertIn(
            "kiwoom_message evidence is missing",
            invalid["blocked_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
