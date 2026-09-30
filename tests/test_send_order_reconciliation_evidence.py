# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_auto_trade_setting_window as setting_window
import gui_windows
from send_order_reconciliation_evidence import (
    DEFAULT_EVIDENCE_PATH,
    SCHEMA,
    build_send_order_reconciliation_evidence,
    inspect_send_order_reconciliation_evidence_storage,
    read_send_order_reconciliation_evidence,
    record_send_order_reconciliation_evidence,
)
from stock_library_diagnostics_retention import (
    ACTION_ROTATE_CANDIDATE,
    ACTION_SKIP_UNCERTAIN,
    plan_stock_library_diagnostic_retention,
)


def _request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "rqname": "BUY_005930_A1B2C3D4",
        "screen_no": "0101",
        "account_no": "12345678",
        "order_id": "ORDER_1",
        "dispatch_claim_id": "CLAIM_1",
        "send_order_attempt_id": "ATTEMPT_1",
        "execution_id": "EXEC_1",
        "signal_id": "SIG_1",
        "market_route": "KRX",
        "order_type": 1,
        "code": "005930",
        "login_session_id": "SESSION_1",
        "connection_epoch": 7,
    }
    value.update(overrides)
    return value


def _event(source: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source": source,
        "rqname": "BUY_005930_A1B2C3D4",
        "screen_no": "0101",
        "trcode": "KOA_NORMAL_BUY_KP_ORD",
        "login_session_id": "SESSION_1",
        "connection_epoch": 7,
        "received_at": "2026-09-29 18:00:00.000+09:00",
        "send_order_request": _request(),
    }
    if source == "kiwoom_order_tr":
        value.update(
            broker_order_no="987654",
            order_no_error="",
            record_name="주문",
            prev_next="0",
        )
    else:
        value.update(
            message="주문이 접수되었습니다.",
            message_raw=" 주문이 접수되었습니다. ",
        )
    value.update(overrides)
    return value




class SendOrderReconciliationEvidenceTest(unittest.TestCase):
    def test_order_tr_build_preserves_correlations_without_account_copy(self) -> None:
        result = build_send_order_reconciliation_evidence(
            _event("kiwoom_order_tr")
        )

        self.assertTrue(result["recorded"])
        record = result["record"]
        self.assertEqual(SCHEMA, record["schema"])
        self.assertEqual("kiwoom_order_tr", record["source"])
        self.assertEqual("ORDER_1", record["identity"]["order_id"])
        self.assertEqual("CLAIM_1", record["identity"]["dispatch_claim_id"])
        self.assertEqual(
            "ATTEMPT_1",
            record["identity"]["send_order_attempt_id"],
        )
        self.assertEqual(
            "987654",
            record["broker_evidence"]["broker_order_no"],
        )
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("12345678", serialized)
    def test_message_build_preserves_normalized_message(self) -> None:
        result = build_send_order_reconciliation_evidence(
            _event("kiwoom_message")
        )

        self.assertTrue(result["recorded"])
        record = result["record"]
        self.assertEqual(
            "주문이 접수되었습니다.",
            record["broker_evidence"]["message"],
        )
        self.assertEqual("SESSION_1", record["login_session_id"])
        self.assertEqual(7, record["connection_epoch"])

    def test_missing_request_and_stale_session_fail_closed(self) -> None:
        missing = _event("kiwoom_message")
        missing.pop("send_order_request")
        self.assertEqual(
            "SEND_ORDER_REQUEST_EVIDENCE_MISSING",
            build_send_order_reconciliation_evidence(missing)["reason"],
        )

        stale = _event("kiwoom_order_tr", login_session_id="OLD_SESSION")
        self.assertEqual(
            "SEND_ORDER_RECONCILIATION_SESSION_MISMATCH",
            build_send_order_reconciliation_evidence(stale)["reason"],
        )

    def test_append_and_filtered_read_are_order_identity_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            first = record_send_order_reconciliation_evidence(
                _event("kiwoom_order_tr"),
                evidence_path=path,
            )
            second_event = _event("kiwoom_message")
            second_event["send_order_request"] = _request(
                order_id="ORDER_2",
                dispatch_claim_id="CLAIM_2",
                send_order_attempt_id="ATTEMPT_2",
            )
            second = record_send_order_reconciliation_evidence(
                second_event,
                evidence_path=path,
            )

            self.assertTrue(first["recorded"])
            self.assertTrue(second["recorded"])
            self.assertEqual(
                2,
                len(read_send_order_reconciliation_evidence(path)),
            )
            filtered = read_send_order_reconciliation_evidence(
                path,
                order_id="ORDER_2",
                dispatch_claim_id="CLAIM_2",
                send_order_attempt_id="ATTEMPT_2",
            )
            self.assertEqual(1, len(filtered))
            self.assertEqual(
                "ORDER_2",
                filtered[0]["identity"]["order_id"],
            )

    def test_storage_preflight_is_read_only_and_requires_canonical_runtime_path(self) -> None:
        with patch(
            "send_order_reconciliation_evidence.os.access",
            return_value=True,
        ):
            canonical = inspect_send_order_reconciliation_evidence_storage(
                DEFAULT_EVIDENCE_PATH
            )
        self.assertTrue(canonical["ready"])
        self.assertTrue(canonical["checks"]["canonical_runtime_path"])
        self.assertTrue(canonical["checks"]["existing_ancestor_writable"])

        with tempfile.TemporaryDirectory() as temp_dir:
            external = Path(temp_dir) / "events.jsonl"
            with patch(
                "send_order_reconciliation_evidence.os.access",
                return_value=True,
            ):
                blocked = inspect_send_order_reconciliation_evidence_storage(
                    external
                )
        self.assertFalse(blocked["ready"])
        self.assertIn(
            "canonical_runtime_path",
            blocked["missing_capabilities"],
        )
        self.assertFalse(external.exists())

    def test_main_window_consumer_ignores_uncorrelated_general_message(self) -> None:
        main = SimpleNamespace()
        event = _event("kiwoom_message")
        event.pop("send_order_request")

        with patch(
            "gui_windows.record_send_order_reconciliation_evidence"
        ) as recorder:
            result = gui_windows.MainWindow._record_kiwoom_order_transport_evidence(
                main,
                event,
            )

        self.assertTrue(result["ignored"])
        recorder.assert_not_called()

    def test_main_window_handlers_delegate_correlated_events(self) -> None:
        recorder = Mock(return_value={"recorded": True})
        tr_observer = Mock(return_value={"appended": True})
        message_observer = Mock(return_value={"appended": True})
        main = SimpleNamespace(
            _record_kiwoom_order_transport_evidence=recorder,
        )
        tr_event = _event("kiwoom_order_tr")
        msg_event = _event("kiwoom_message")

        with (
            patch(
                "gui_windows.observe_broker_order_tr_evidence",
                tr_observer,
            ),
            patch(
                "gui_windows.observe_broker_message_evidence",
                message_observer,
            ),
        ):
            gui_windows.MainWindow.on_kiwoom_raw_order_tr_received(
                main,
                tr_event,
            )
            gui_windows.MainWindow.on_kiwoom_raw_message_received(
                main,
                msg_event,
            )

        self.assertEqual(
            {"recorded": True},
            main.last_order_tr_transport_evidence_result,
        )
        self.assertEqual(
            {"recorded": True},
            main.last_message_transport_evidence_result,
        )
        self.assertEqual(
            {"appended": True},
            main.last_broker_order_tr_evidence_result,
        )
        self.assertEqual(
            {"appended": True},
            main.last_broker_message_evidence_result,
        )
        self.assertEqual(
            [((tr_event,), {}), ((msg_event,), {})],
            recorder.call_args_list,
        )
        tr_observer.assert_called_once_with(tr_event)
        message_observer.assert_called_once_with(msg_event)

    def test_live_sor_reconciliation_capability_requires_full_evidence_pipeline(self) -> None:
        api = SimpleNamespace(
            is_connected=lambda: True,
            login_session_id=lambda: "SESSION_1",
            broker_session_snapshot=lambda: SimpleNamespace(
                login_session_id="SESSION_1",
                connection_epoch=7,
            ),
            register_send_order_reconciliation_context=lambda _payload: {
                "registered": True
            },
            account_server_type=lambda: "REAL",
        )
        main = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=lambda: "12345678",
            _raw_chejan_evidence_bound=True,
            _raw_order_tr_evidence_bound=True,
            _raw_message_evidence_bound=True,
        )
        certificate_patch = patch.object(
            gui_windows,
            "inspect_live_sor_validation_certificate",
            return_value={
                "valid": False,
                "certificate_hash": "",
                "blocked_reasons": [],
            },
        )
        certificate_patch.start()
        self.addCleanup(certificate_patch.stop)

        ready = gui_windows.MainWindow.live_sor_reconciliation_capability_snapshot(
            main
        )
        self.assertFalse(ready["ready"])
        self.assertTrue(ready["pipeline_ready"])
        self.assertFalse(ready["live_execution_authorized"])
        self.assertTrue(ready["controlled_validation_ready"])
        self.assertEqual(
            "READY_FOR_CONTROLLED_VALIDATION",
            ready["validation_state"],
        )
        self.assertEqual(
            "LIVE_SOR_PRODUCTION_EVIDENCE_UNVERIFIED",
            ready["authorization_reason"],
        )
        self.assertEqual([], ready["missing_capabilities"])
        self.assertEqual(
            ["LIVE_SOR_PRODUCTION_EVIDENCE_UNVERIFIED"],
            ready["blocking_reasons"],
        )
        self.assertTrue(
            ready["checks"]["transport_evidence_recorder_available"]
        )

        main._raw_message_evidence_bound = False
        blocked = (
            gui_windows.MainWindow.live_sor_reconciliation_capability_snapshot(
                main
            )
        )
        self.assertFalse(blocked["ready"])
        self.assertFalse(blocked["pipeline_ready"])
        self.assertFalse(blocked["live_execution_authorized"])
        self.assertFalse(blocked["controlled_validation_ready"])
        self.assertEqual(
            "SOR_RECONCILIATION_CAPABILITY_INCOMPLETE",
            blocked["validation_state"],
        )
        self.assertIn(
            "raw_message_consumer_bound",
            blocked["missing_capabilities"],
        )
        self.assertIn(
            "raw_message_consumer_bound",
            blocked["blocking_reasons"],
        )

    def test_live_sor_capability_authorizes_only_valid_current_session_certificate(self) -> None:
        api = SimpleNamespace(
            is_connected=lambda: True,
            login_session_id=lambda: "SESSION_1",
            broker_session_snapshot=lambda: SimpleNamespace(
                login_session_id="SESSION_1",
                connection_epoch=7,
            ),
            register_send_order_reconciliation_context=lambda _payload: {
                "registered": True
            },
            account_server_type=lambda: "REAL",
        )
        main = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=lambda: "12345678",
            _raw_chejan_evidence_bound=True,
            _raw_order_tr_evidence_bound=True,
            _raw_message_evidence_bound=True,
        )
        with patch.object(
            gui_windows,
            "inspect_live_sor_validation_certificate",
            return_value={
                "valid": True,
                "certificate_hash": "CERT_HASH",
                "blocked_reasons": [],
            },
        ) as inspector:
            ready = gui_windows.MainWindow.live_sor_reconciliation_capability_snapshot(
                main
            )

        self.assertTrue(ready["ready"])
        self.assertTrue(ready["pipeline_ready"])
        self.assertTrue(ready["live_execution_authorized"])
        self.assertTrue(ready["authorization_verified"])
        self.assertTrue(ready["validation_certificate_valid"])
        self.assertEqual("CERT_HASH", ready["validation_certificate_hash"])
        self.assertEqual("LIVE_SOR_EXECUTION_AUTHORIZED", ready["validation_state"])
        self.assertEqual([], ready["blocking_reasons"])
        inspector.assert_called_once_with(
            expected_login_session_id="SESSION_1",
            expected_connection_epoch=7,
            expected_account_no="12345678",
            expected_server_type="REAL",
        )







    def test_main_window_init_wires_raw_tr_and_message_signals(self) -> None:
        source = inspect.getsource(gui_windows.MainWindow.__init__)
        self.assertIn(
            "raw_order_tr_received.connect(",
            source,
        )
        self.assertIn(
            "self.on_kiwoom_raw_order_tr_received",
            source,
        )
        self.assertIn(
            "raw_message_received.connect(",
            source,
        )
        self.assertIn(
            "self.on_kiwoom_raw_message_received",
            source,
        )

    def test_transport_evidence_narrows_only_one_exact_chejan_candidate(self) -> None:
        first = {
            "order_id": "ORDER_1",
            "dispatch_claim_id": "CLAIM_1",
            "send_order_attempt_id": "ATTEMPT_1",
        }
        second = {
            "order_id": "ORDER_2",
            "dispatch_claim_id": "CLAIM_2",
            "send_order_attempt_id": "ATTEMPT_2",
        }
        evidence = {
            "identity": {
                "order_id": "ORDER_2",
                "dispatch_claim_id": "CLAIM_2",
                "send_order_attempt_id": "ATTEMPT_2",
            }
        }
        with patch.object(
            setting_window,
            "read_send_order_reconciliation_evidence",
            return_value=[evidence],
        ) as reader:
            narrowed = (
                setting_window._narrow_chejan_candidates_with_transport_evidence(
                    [first, second],
                    "987654",
                )
            )

        self.assertEqual([second], narrowed)
        reader.assert_called_once_with(
            setting_window.SEND_ORDER_RECONCILIATION_EVIDENCE_PATH,
            broker_order_no="987654",
        )

    def test_ambiguous_transport_evidence_keeps_chejan_fail_closed(self) -> None:
        candidates = [
            {
                "order_id": "ORDER_1",
                "dispatch_claim_id": "CLAIM_1",
                "send_order_attempt_id": "ATTEMPT_1",
            },
            {
                "order_id": "ORDER_2",
                "dispatch_claim_id": "CLAIM_2",
                "send_order_attempt_id": "ATTEMPT_2",
            },
        ]
        evidence = [
            {"identity": dict(candidates[0])},
            {"identity": dict(candidates[1])},
        ]
        with patch.object(
            setting_window,
            "read_send_order_reconciliation_evidence",
            return_value=evidence,
        ):
            narrowed = (
                setting_window._narrow_chejan_candidates_with_transport_evidence(
                    candidates,
                    "987654",
                )
            )

        self.assertEqual(candidates, narrowed)

    def test_stock_library_retention_never_rotates_reconciliation_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            evidence_path = (
                root
                / "kiwoom_send_order_reconciliation"
                / "events.jsonl"
            )
            result = record_send_order_reconciliation_evidence(
                _event("kiwoom_order_tr"),
                evidence_path=evidence_path,
            )
            self.assertTrue(result["recorded"])

            plan = plan_stock_library_diagnostic_retention(
                root,
                current_session_id="KIWOOM_LOGIN_SESSION_TEST",
                current_connection_epoch=1,
                now=datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc),
            )
            target = next(
                item
                for item in plan["entries"]
                if item["path"]
                == "kiwoom_send_order_reconciliation/events.jsonl"
            )

            self.assertEqual(ACTION_SKIP_UNCERTAIN, target["action"])
            self.assertNotEqual(ACTION_ROTATE_CANDIDATE, target["action"])
            self.assertTrue(evidence_path.exists())

    def test_recorder_has_no_order_queue_mutation_dependency(self) -> None:
        source = inspect.getsource(record_send_order_reconciliation_evidence)
        self.assertNotIn("mutate_order_queue", source)
        self.assertNotIn("order_queue.json", source)


if __name__ == "__main__":
    unittest.main()
