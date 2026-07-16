# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from broker_holding_recorder import record_broker_holding_snapshot


class BrokerHoldingRecorderTest(unittest.TestCase):
    def _context(self) -> dict[str, object]:
        return {
            "kiwoom_api_live_event": True,
            "live_event_source": "KiwoomApi.raw_chejan_received",
        }

    def _raw_event(self, **overrides: object) -> dict[str, object]:
        fids = {
            "9201": "12345678",
            "9001": "A003550",
            "302": "LG",
            "930": "3",
            "933": "2",
            "931": "1000",
            "932": "3000",
            "10": "1100",
            "8019": "10.5",
        }
        fids.update(overrides.pop("fid_values", {}))
        event = {
            "source": "kiwoom_chejan",
            "gubun": "1",
            "fid_values": fids,
            "received_at": "2026-07-16 11:00:00",
        }
        event.update(overrides)
        return event

    def _write_positions(self, path: Path, *, account_no: str = "12345678", code: str = "003550", quantity: int = 3, average_price: int = 1000) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": "before",
                    "positions": [
                        {
                            "position_id": f"POSITION_KIWOOM_{account_no}_{code}",
                            "broker": "KIWOOM",
                            "account_no": account_no,
                            "code": code,
                            "side": "LONG",
                            "quantity": quantity,
                            "average_price": average_price,
                            "cost_basis": quantity * average_price,
                            "position_status": "OPEN" if quantity else "CLOSED",
                            "applied_fill_ids": [],
                            "applied_fill_identities": [],
                            "last_applied_cumulative_by_order": {},
                            "updated_at": "before",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _read_holdings(self, path: Path) -> list[dict[str, object]]:
        return json.loads(path.read_text(encoding="utf-8"))["holdings"]

    def test_consistent_broker_snapshot_records_without_position_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path)
            before_positions = positions_path.read_text(encoding="utf-8")

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("CONSISTENT", result["reconciliation_status"])
            self.assertFalse(result["manual_reconciliation_required"])
            self.assertEqual(before_positions, positions_path.read_text(encoding="utf-8"))
            holding = self._read_holdings(holdings_path)[0]
            self.assertEqual(3, holding["holding_quantity"])
            self.assertEqual("CONSISTENT", holding["reconciliation_status"])

    def test_broker_only_holding_requires_manual_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("BROKER_ONLY", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertTrue(holding["manual_reconciliation_required"])
            self.assertEqual(["position_missing"], holding["mismatch_fields"])

    def test_internal_only_when_broker_reports_zero_holding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path, quantity=3)

            result = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "0", "933": "0", "932": "0"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertEqual("INTERNAL_ONLY", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertEqual(0, holding["holding_quantity"])
            self.assertTrue(holding["manual_reconciliation_required"])

    def test_quantity_and_average_price_mismatch_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path, quantity=2, average_price=900)

            quantity = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())
            average = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "2"}, received_at="2026-07-16 11:01:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertEqual("QUANTITY_MISMATCH", quantity["reconciliation_status"])
            self.assertEqual("AVERAGE_PRICE_MISMATCH", average["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertTrue(holding["manual_reconciliation_required"])
            self.assertEqual(["average_price"], holding["mismatch_fields"])

    def test_positions_json_damage_records_position_source_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            positions_path.write_text("{bad json", encoding="utf-8")

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("POSITION_SOURCE_INVALID", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertTrue(holding["position_read_failure_reason"])

    def test_corrupt_broker_holdings_json_blocks_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            holdings_path.write_text("{bad json", encoding="utf-8")
            before = holdings_path.read_text(encoding="utf-8")

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertFalse(result["holding_recorded"])
            self.assertEqual("read_broker_holdings", result["holding_stage"])
            self.assertEqual(before, holdings_path.read_text(encoding="utf-8"))

    def test_duplicate_event_is_complete_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path)
            event = self._raw_event()

            first = record_broker_holding_snapshot(event, holdings_path, positions_path, context=self._context())
            after_first = holdings_path.read_text(encoding="utf-8")
            duplicate = record_broker_holding_snapshot(event, holdings_path, positions_path, context=self._context())

            self.assertTrue(first["holding_recorded"], first)
            self.assertFalse(duplicate["holding_recorded"], duplicate)
            self.assertEqual("duplicate_broker_holding_event", duplicate["holding_stage"])
            self.assertEqual(after_first, holdings_path.read_text(encoding="utf-8"))

    def test_duplicate_mismatch_event_preserves_reconciliation_status_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            event = self._raw_event()

            first = record_broker_holding_snapshot(event, holdings_path, positions_path, context=self._context())
            after_first = holdings_path.read_text(encoding="utf-8")
            duplicate = record_broker_holding_snapshot(event, holdings_path, positions_path, context=self._context())

            self.assertEqual("BROKER_ONLY", first["reconciliation_status"])
            self.assertFalse(duplicate["holding_recorded"], duplicate)
            self.assertEqual("BROKER_ONLY", duplicate["reconciliation_status"])
            self.assertTrue(duplicate["manual_reconciliation_required"])
            self.assertEqual(after_first, holdings_path.read_text(encoding="utf-8"))

    def test_different_accounts_same_code_are_separate_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            first = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())
            second = record_broker_holding_snapshot(
                self._raw_event(fid_values={"9201": "87654321"}, received_at="2026-07-16 11:02:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertTrue(first["holding_recorded"], first)
            self.assertTrue(second["holding_recorded"], second)
            self.assertEqual(2, len(self._read_holdings(holdings_path)))

    def test_mismatch_resolves_for_same_account_code_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path, quantity=3, average_price=1000)

            mismatch = record_broker_holding_snapshot(
                self._raw_event(fid_values={"9001": "A005930"}, received_at="2026-07-16 11:03:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )
            consistent = record_broker_holding_snapshot(
                self._raw_event(received_at="2026-07-16 11:04:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertEqual("BROKER_ONLY", mismatch["reconciliation_status"])
            self.assertEqual("CONSISTENT", consistent["reconciliation_status"])
            holdings = self._read_holdings(holdings_path)
            by_code = {item["code"]: item for item in holdings}
            self.assertTrue(by_code["005930"]["manual_reconciliation_required"])
            self.assertFalse(by_code["003550"]["manual_reconciliation_required"])

    def test_requires_live_context_and_required_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            no_context = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context={})
            missing_quantity = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": ""}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertFalse(no_context["holding_recorded"])
            self.assertFalse(missing_quantity["holding_recorded"])
            self.assertFalse(holdings_path.exists())


if __name__ == "__main__":
    unittest.main()
