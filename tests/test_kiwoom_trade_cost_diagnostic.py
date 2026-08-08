from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_api import KiwoomApi, TRADE_COST_DIAGNOSTIC_FIDS
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

    def dynamicCall(self, _signature: str, fid: int) -> object:
        key = str(fid)
        self.requested.append(key)
        return self.values.get(key, "")


class _FakeSignal:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)


class _FakeKiwoom:
    def __init__(self, values: dict[str, object]) -> None:
        self._control = _FakeControl(values)
        self.raw_chejan_received = _FakeSignal()

    def is_available(self) -> bool:
        return True


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
