from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_api import (
    KiwoomApi,
    ORDER_RECONCILIATION_EVIDENCE_FIDS,
    TRADE_COST_DIAGNOSTIC_FIDS,
)
from kiwoom_trade_cost_diagnostic import (
    build_trade_cost_chejan_diagnostic,
    format_trade_cost_chejan_table,
    read_trade_cost_chejan_diagnostics,
    record_trade_cost_chejan_diagnostic,
)


class _FakeControl:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values
        self.requested: list[str] = []
        self.calls: list[tuple[object, ...]] = []

    def dynamicCall(self, signature: str, *args: object) -> object:
        self.calls.append((signature, *args))
        if signature.startswith("GetChejanData"):
            key = str(args[0])
            self.requested.append(key)
            return self.values.get(key, "")
        if signature.startswith("GetCommData"):
            field = str(args[3])
            return self.values.get(field, "")
        if signature.startswith("SendOrder"):
            return self.values.get("__send_order_return__", 0)
        return ""


class _FakeSignal:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)


class _FakeKiwoom:
    def __init__(self, values: dict[str, object]) -> None:
        self._control = _FakeControl(values)
        self.raw_chejan_received = _FakeSignal()
        self.raw_message_received = _FakeSignal()
        self.raw_order_tr_received = _FakeSignal()
        self._account_funds_request_accounts = {}
        self._send_order_request_evidence = {}
        self._pending_tr = {}
        self._connected = True
        self._login_session_id = "SOR_EVIDENCE_SESSION"
        self._connection_epoch = 17

    def is_available(self) -> bool:
        return True

    def is_connected(self) -> bool:
        return bool(self._connected)


class KiwoomTradeCostRawCaptureTests(unittest.TestCase):
    def test_required_fids_and_exact_raw_values_are_preserved(self) -> None:
        api = _FakeKiwoom({"9203": " 12345 ", "938": " 0 ", "939": 0})
        with patch("kiwoom_api.record_trade_cost_chejan_diagnostic") as recorder:
            KiwoomApi._on_receive_chejan_data(api, "0", "1", "9203;")

        event = api.raw_chejan_received.events[0]
        self.assertEqual(["9203"], event["fid_list"])
        self.assertEqual(" 12345 ", event["fid_raw_values"]["9203"])
        self.assertEqual(" 0 ", event["fid_raw_values"]["938"])
        self.assertEqual("0", event["fid_raw_values"]["939"])
        self.assertEqual("12345", event["fid_values"]["9203"])
        self.assertEqual("0", event["fid_values"]["938"])
        self.assertTrue(set(TRADE_COST_DIAGNOSTIC_FIDS).issubset(api._control.requested))
        recorder.assert_called_once_with(event)

    def test_order_reconciliation_evidence_fids_are_forced_and_preserved(self) -> None:
        api = _FakeKiwoom(
            {
                "9203": " 12345 ",
                "904": " 12000 ",
                "909": " 777 ",
                "2134": " 0 ",
                "2135": " 통합 ",
                "2136": " Y ",
            }
        )
        with patch("kiwoom_api.record_trade_cost_chejan_diagnostic"):
            KiwoomApi._on_receive_chejan_data(api, "0", "1", "9203;")

        event = api.raw_chejan_received.events[0]
        self.assertTrue(
            set(ORDER_RECONCILIATION_EVIDENCE_FIDS).issubset(api._control.requested)
        )
        self.assertEqual(" 777 ", event["fid_raw_values"]["909"])
        self.assertEqual("777", event["fid_values"]["909"])
        self.assertEqual("0", event["fid_values"]["2134"])
        self.assertEqual("통합", event["fid_values"]["2135"])
        self.assertEqual("Y", event["fid_values"]["2136"])

    def test_general_broker_message_is_preserved_with_session_provenance(self) -> None:
        api = _FakeKiwoom({})

        KiwoomApi._on_receive_msg(
            api,
            "0101",
            "BUY_005930",
            "",
            "  SOR order accepted  ",
        )

        self.assertEqual(1, len(api.raw_message_received.events))
        event = api.raw_message_received.events[0]
        self.assertEqual("kiwoom_message", event["source"])
        self.assertEqual("0101", event["screen_no"])
        self.assertEqual("BUY_005930", event["rqname"])
        self.assertEqual("", event["trcode"])
        self.assertEqual("  SOR order accepted  ", event["message_raw"])
        self.assertEqual("SOR order accepted", event["message"])
        self.assertEqual("SOR_EVIDENCE_SESSION", event["login_session_id"])
        self.assertEqual(17, event["connection_epoch"])
        self.assertTrue(event["received_at"])

    def test_order_tr_and_message_keep_same_send_order_request_evidence(self) -> None:
        rqname = "BUY_005930_A1B2C3D4"
        api = _FakeKiwoom({"주문번호": " 987654 "})
        api._send_order_request_evidence[("0101", rqname)] = {
            "source": "kiwoom_send_order",
            "rqname": rqname,
            "screen_no": "0101",
            "code": "005930",
            "order_type": 1,
            "login_session_id": "SOR_EVIDENCE_SESSION",
            "connection_epoch": 17,
        }

        KiwoomApi._on_receive_tr_data(
            api,
            "0101",
            rqname,
            "",
            "주문",
            "0",
            0,
            "",
            "",
            "",
        )
        KiwoomApi._on_receive_msg(
            api,
            "0101",
            rqname,
            "",
            "  order accepted  ",
        )

        self.assertEqual(1, len(api.raw_order_tr_received.events))
        order_tr = api.raw_order_tr_received.events[0]
        self.assertEqual("987654", order_tr["broker_order_no"])
        self.assertEqual(rqname, order_tr["rqname"])
        self.assertEqual(
            "005930",
            order_tr["send_order_request"]["code"],
        )
        self.assertEqual(
            "987654",
            api._send_order_request_evidence[("0101", rqname)][
                "broker_order_no"
            ],
        )
        self.assertEqual(1, len(api.raw_message_received.events))
        message = api.raw_message_received.events[0]
        self.assertEqual(rqname, message["rqname"])
        self.assertEqual(
            "987654",
            message["send_order_request"]["broker_order_no"],
        )

    def test_stale_session_order_evidence_is_not_joined_to_new_session_events(self) -> None:
        rqname = "BUY_005930_A1B2C3D4"
        api = _FakeKiwoom({"주문번호": "999999"})
        api._send_order_request_evidence[("0101", rqname)] = {
            "source": "kiwoom_send_order",
            "rqname": rqname,
            "screen_no": "0101",
            "code": "005930",
            "order_type": 1,
            "login_session_id": "OLD_SESSION",
            "connection_epoch": 16,
        }

        KiwoomApi._on_receive_tr_data(
            api,
            "0101",
            rqname,
            "",
            "주문",
            "0",
            0,
            "",
            "",
            "",
        )
        KiwoomApi._on_receive_msg(
            api,
            "0101",
            rqname,
            "",
            "late old-session message",
        )

        self.assertEqual([], api.raw_order_tr_received.events)
        self.assertEqual(1, len(api.raw_message_received.events))
        self.assertNotIn(
            "send_order_request",
            api.raw_message_received.events[0],
        )
        self.assertNotIn(
            "broker_order_no",
            api._send_order_request_evidence[("0101", rqname)],
        )

    def test_login_session_transition_clears_send_order_request_evidence(self) -> None:
        api = _FakeKiwoom({})
        api._send_order_request_evidence[("0101", "OLD")] = {
            "login_session_id": api._login_session_id,
            "connection_epoch": api._connection_epoch,
        }

        KiwoomApi._invalidate_login_session(
            api,
            reason="test_disconnect",
            emit=False,
            increment_epoch=True,
        )

        self.assertEqual({}, api._send_order_request_evidence)
        self.assertEqual("", api._login_session_id)
        self.assertEqual(18, api._connection_epoch)

        api._send_order_request_evidence[("0101", "STALE")] = {
            "login_session_id": "STALE",
            "connection_epoch": 18,
        }
        new_session_id = KiwoomApi._establish_login_session(
            api,
            account_payload="12345678",
        )

        self.assertEqual({}, api._send_order_request_evidence)
        self.assertTrue(new_session_id.startswith("KIWOOM_LOGIN_SESSION_"))
        self.assertEqual(19, api._connection_epoch)

    def test_reconciliation_context_survives_send_order_merge(self) -> None:
        rqname = "BUY_005930_A1B2C3D4"
        api = _FakeKiwoom({"__send_order_return__": 0})
        registered = KiwoomApi.register_send_order_reconciliation_context(
            api,
            {
                "rqname": rqname,
                "screen_no": "0101",
                "order_id": "ORDER_1",
                "dispatch_claim_id": "CLAIM_1",
                "send_order_attempt_id": "ATTEMPT_1",
                "execution_id": "EXEC_1",
                "signal_id": "SIG_1",
                "market_route": "KRX",
                "order_type": 1,
            },
        )

        self.assertTrue(registered["registered"])
        raw_result = KiwoomApi.send_order(
            api,
            rqname,
            "0101",
            "12345678",
            1,
            "005930",
            2,
            70000,
            "00",
            "",
        )

        self.assertEqual(0, raw_result)
        evidence = api._send_order_request_evidence[("0101", rqname)]
        self.assertTrue(evidence["reconciliation_context_registered"])
        self.assertEqual("ORDER_1", evidence["order_id"])
        self.assertEqual("CLAIM_1", evidence["dispatch_claim_id"])
        self.assertEqual("ATTEMPT_1", evidence["send_order_attempt_id"])
        self.assertEqual("EXEC_1", evidence["execution_id"])
        self.assertEqual("SIG_1", evidence["signal_id"])
        self.assertEqual("005930", evidence["code"])
        self.assertEqual("12345678", evidence["account_no"])
        self.assertEqual(0, evidence["send_order_raw_result"])

    def test_reconciliation_context_rejects_identity_conflict(self) -> None:
        rqname = "BUY_005930_A1B2C3D4"
        api = _FakeKiwoom({})
        payload = {
            "rqname": rqname,
            "screen_no": "0101",
            "order_id": "ORDER_1",
            "dispatch_claim_id": "CLAIM_1",
            "send_order_attempt_id": "ATTEMPT_1",
        }
        first = KiwoomApi.register_send_order_reconciliation_context(api, payload)
        conflicting = dict(payload)
        conflicting["dispatch_claim_id"] = "CLAIM_2"
        second = KiwoomApi.register_send_order_reconciliation_context(
            api,
            conflicting,
        )

        self.assertTrue(first["registered"])
        self.assertFalse(second["registered"])
        self.assertEqual(
            "SEND_ORDER_RECONCILIATION_CONTEXT_CONFLICT",
            second["reason"],
        )
        self.assertEqual(
            "CLAIM_1",
            api._send_order_request_evidence[("0101", rqname)][
                "dispatch_claim_id"
            ],
        )

    def test_diagnostic_failure_does_not_block_raw_signal(self) -> None:
        api = _FakeKiwoom({"938": "17"})
        with patch(
            "kiwoom_api.record_trade_cost_chejan_diagnostic",
            side_effect=OSError("diagnostic unavailable"),
        ):
            KiwoomApi._on_receive_chejan_data(api, "0", "1", "938;")

        self.assertEqual(1, len(api.raw_chejan_received.events))
        self.assertEqual("17", api.raw_chejan_received.events[0]["fid_raw_values"]["938"])

    def test_balance_chejan_is_not_written_to_trade_cost_diagnostic(self) -> None:
        api = _FakeKiwoom({"938": "17"})
        with patch("kiwoom_api.record_trade_cost_chejan_diagnostic") as recorder:
            KiwoomApi._on_receive_chejan_data(api, "1", "1", "938;")

        recorder.assert_not_called()
        self.assertEqual(1, len(api.raw_chejan_received.events))


class KiwoomTradeCostDiagnosticTests(unittest.TestCase):
    def _event(self, **raw_overrides: str) -> dict[str, object]:
        raw = {
            "9201": " 12345678 ",
            "9001": "A005930",
            "302": " 삼성전자 ",
            "9203": " 777 ",
            "904": "",
            "907": "2",
            "900": "5",
            "901": "+70000",
            "910": "+70100",
            "911": "2",
            "902": "3",
            "903": "+140200",
            "913": " 체결 ",
            "908": "101503",
            "938": " 0 ",
            "939": "",
        }
        raw.update(raw_overrides)
        return {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "item_count": len(raw),
            "fid_list": list(raw),
            "observed_fid_list": list(raw),
            "fid_raw_values": raw,
            "fid_values": {key: value.strip() for key, value in raw.items()},
            "received_at": "2026-08-07 10:15:03.123",
        }

    def _queue(self, path: Path) -> None:
        payload = {
            "orders": [
                {
                    "id": "ORDER_QUEUED_1",
                    "order_id": "ORDER_1",
                    "execution_id": "EXEC_1",
                    "broker_order_no": "777",
                    "account_no": "12345678",
                    "code": "005930",
                    "side": "BUY",
                    "status": "PARTIALLY_FILLED",
                }
            ]
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_raw_cost_fields_and_internal_identity_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            self._queue(queue_path)
            record = build_trade_cost_chejan_diagnostic(
                self._event(),
                order_queue_path=queue_path,
            )

        fields = record["server_raw"]["fields"]
        self.assertEqual(" 0 ", fields["raw_938"])
        self.assertEqual("", fields["raw_939"])
        self.assertEqual("+140200", fields["cumulative_fill_amount"])
        self.assertEqual("ORDER_QUEUED_1", record["internal_identity"]["order_queued_id"])
        self.assertEqual("ORDER_1", record["internal_identity"]["order_id"])
        self.assertEqual("EXEC_1", record["internal_identity"]["execution_id"])
        self.assertNotIn("estimated_fee", record)
        self.assertNotIn("calculated_tax", record)

    def test_each_partial_fill_and_other_order_remains_a_distinct_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "events.jsonl"
            queue_path = root / "order_queue.json"
            self._queue(queue_path)
            queue_before = queue_path.read_bytes()
            first = record_trade_cost_chejan_diagnostic(
                self._event(**{"911": "2", "938": " 0 "}),
                diagnostic_path=output,
                order_queue_path=queue_path,
            )
            second = record_trade_cost_chejan_diagnostic(
                self._event(**{"911": "3", "902": "0", "938": " 75 "}),
                diagnostic_path=output,
                order_queue_path=queue_path,
            )
            third = record_trade_cost_chejan_diagnostic(
                self._event(**{"9001": "A000660", "9203": "888", "938": ""}),
                diagnostic_path=output,
                order_queue_path=queue_path,
            )
            records = read_trade_cost_chejan_diagnostics(output)
            queue_after = queue_path.read_bytes()

        self.assertTrue(first["recorded"] and second["recorded"] and third["recorded"])
        self.assertEqual(queue_before, queue_after)
        self.assertEqual(3, len(records))
        self.assertEqual(["2", "3"], [
            records[0]["server_raw"]["fields"]["filled_quantity"],
            records[1]["server_raw"]["fields"]["filled_quantity"],
        ])
        self.assertEqual("888", records[2]["server_raw"]["fields"]["broker_order_no"])
        self.assertFalse(records[2]["internal_identity"]["matched"])
        table = format_trade_cost_chejan_table(records)
        self.assertIn("time\tstock\tside\torder_no\tfill_qty\tcum_amount\t938\t939", table)
        self.assertIn("A000660\t2\t888", table)

    def test_missing_server_values_are_not_fabricated(self) -> None:
        record = build_trade_cost_chejan_diagnostic(
            {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "received_at": "2026-08-07 10:00:00.000",
                "fid_raw_values": {},
            },
            order_queue_path=Path("missing-order-queue.json"),
        )
        fields = record["server_raw"]["fields"]
        self.assertEqual("", fields["raw_938"])
        self.assertEqual("", fields["raw_939"])
        self.assertEqual("", fields["cumulative_fill_amount"])
        self.assertFalse(record["internal_identity"]["matched"])


if __name__ == "__main__":
    unittest.main()
