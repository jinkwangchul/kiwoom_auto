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

    def _write_positions_root(self, path: Path, positions: list[dict[str, object]]) -> None:
        path.write_text(
            json.dumps({"version": 1, "updated_at": "before", "positions": positions}, ensure_ascii=False, indent=2),
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
                self._raw_event(fid_values={"930": "0", "933": "0", "931": "0", "932": "0"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertEqual("INTERNAL_ONLY", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertEqual(0, holding["holding_quantity"])
            self.assertEqual(0, holding["available_quantity"])
            self.assertEqual(0, holding["average_price"])
            self.assertTrue(holding["manual_reconciliation_required"])

    def test_zero_holding_without_internal_position_is_consistent_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            result = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "0", "933": "0", "931": "0", "932": "0"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("CONSISTENT", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertEqual(0, holding["holding_quantity"])
            self.assertEqual(0, holding["available_quantity"])
            self.assertEqual(0, holding["average_price"])
            self.assertFalse(holding["manual_reconciliation_required"])

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

    def test_duplicate_internal_positions_are_position_source_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            first = {
                "position_id": "POSITION_KIWOOM_12345678_003550_A",
                "broker": "KIWOOM",
                "account_no": "12345678",
                "code": "003550",
                "quantity": 3,
                "average_price": 1000,
            }
            second = dict(first, position_id="POSITION_KIWOOM_12345678_003550_B")
            self._write_positions_root(positions_path, [first, second])

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("POSITION_SOURCE_INVALID", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertIn("multiple internal positions", holding["position_read_failure_reason"])

    def test_invalid_internal_position_values_are_position_source_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions_root(
                positions_path,
                [
                    {
                        "position_id": "POSITION_KIWOOM_12345678_003550",
                        "broker": "KIWOOM",
                        "account_no": "12345678",
                        "code": "003550",
                        "quantity": "bad",
                        "average_price": -1,
                    }
                ],
            )

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("POSITION_SOURCE_INVALID", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertTrue(holding["position_read_failure_reason"])

    def test_fractional_average_price_mismatch_is_not_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path, quantity=3, average_price=1000.9)

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertEqual("AVERAGE_PRICE_MISMATCH", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertEqual(1000.9, holding["internal_average_price"])

    def test_fractional_broker_average_price_is_preserved_for_compare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions(positions_path, quantity=3, average_price=1000)

            result = record_broker_holding_snapshot(
                self._raw_event(fid_values={"931": "1000.9"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertEqual("AVERAGE_PRICE_MISMATCH", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertEqual(1000.9, holding["average_price"])
            self.assertEqual(1000.9, holding["broker_average_price"])

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

    def test_older_event_does_not_replace_latest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            latest = record_broker_holding_snapshot(
                self._raw_event(received_at="2026-07-16 11:10:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )
            after_latest = holdings_path.read_text(encoding="utf-8")
            stale = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "7", "933": "7"}, received_at="2026-07-16 11:09:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertTrue(latest["holding_recorded"], latest)
            self.assertFalse(stale["holding_recorded"], stale)
            self.assertEqual("stale_broker_holding_event", stale["holding_stage"])
            self.assertEqual(after_latest, holdings_path.read_text(encoding="utf-8"))

    def test_same_received_at_different_identity_blocks_as_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            first = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())
            after_first = holdings_path.read_text(encoding="utf-8")
            ambiguous = record_broker_holding_snapshot(
                self._raw_event(fid_values={"933": "1"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertTrue(first["holding_recorded"], first)
            self.assertFalse(ambiguous["holding_recorded"], ambiguous)
            self.assertEqual("ambiguous_broker_holding_event", ambiguous["holding_stage"])
            self.assertEqual(after_first, holdings_path.read_text(encoding="utf-8"))

    def test_timezone_mixed_received_at_is_blocked_without_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            first = record_broker_holding_snapshot(
                self._raw_event(received_at="2026-07-16T02:00:00+00:00"),
                holdings_path,
                positions_path,
                context=self._context(),
            )
            stale = record_broker_holding_snapshot(
                self._raw_event(received_at="2026-07-16 10:59:59", fid_values={"933": "1"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertTrue(first["holding_recorded"], first)
            self.assertFalse(stale["holding_recorded"], stale)
            self.assertEqual("broker_holding_received_at", stale["holding_stage"])

    def test_event_identity_history_is_bounded_and_recent_duplicates_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            last_event = None
            for index in range(25):
                last_event = self._raw_event(received_at=f"2026-07-16 11:{index:02d}:00", fid_values={"933": str(index)})
                result = record_broker_holding_snapshot(last_event, holdings_path, positions_path, context=self._context())
                self.assertTrue(result["holding_recorded"], result)
            holding = self._read_holdings(holdings_path)[0]
            self.assertLessEqual(len(holding["event_identities"]), 20)

            duplicate = record_broker_holding_snapshot(last_event, holdings_path, positions_path, context=self._context())
            self.assertFalse(duplicate["holding_recorded"], duplicate)
            self.assertEqual("duplicate_broker_holding_event", duplicate["holding_stage"])

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

    def test_negative_broker_quantities_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            result = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "-3"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertFalse(result["holding_recorded"])
            self.assertIn("holding_quantity must not be negative", result["blocked_reasons"][0])
            self.assertFalse(holdings_path.exists())

    def test_fractional_nan_and_infinite_broker_quantities_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"

            fractional = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "3.5"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )
            nan_value = record_broker_holding_snapshot(
                self._raw_event(fid_values={"930": "NaN"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )
            infinite = record_broker_holding_snapshot(
                self._raw_event(fid_values={"933": "Infinity"}),
                holdings_path,
                positions_path,
                context=self._context(),
            )

            self.assertFalse(fractional["holding_recorded"])
            self.assertIn("holding_quantity must be an integer", fractional["blocked_reasons"][0])
            self.assertFalse(nan_value["holding_recorded"])
            self.assertIn("holding_quantity is required", nan_value["blocked_reasons"][0])
            self.assertFalse(infinite["holding_recorded"])
            self.assertIn("available_quantity is required", infinite["blocked_reasons"][0])
            self.assertFalse(holdings_path.exists())

    def test_nan_and_infinite_internal_position_values_are_invalid_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_positions_root(
                positions_path,
                [
                    {
                        "position_id": "POSITION_KIWOOM_12345678_003550",
                        "broker": "KIWOOM",
                        "account_no": "12345678",
                        "code": "003550",
                        "quantity": "NaN",
                        "average_price": "Infinity",
                    }
                ],
            )

            result = record_broker_holding_snapshot(self._raw_event(), holdings_path, positions_path, context=self._context())

            self.assertTrue(result["holding_recorded"], result)
            self.assertEqual("POSITION_SOURCE_INVALID", result["reconciliation_status"])
            holding = self._read_holdings(holdings_path)[0]
            self.assertIn("finite numeric", holding["position_read_failure_reason"])

    def test_duplicate_account_code_holding_records_block_without_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            duplicate = {
                "account_no": "12345678",
                "code": "003550",
                "received_at": "2026-07-16 10:00:00",
                "event_identities": ["OLD"],
                "reconciliation_status": "BROKER_ONLY",
                "manual_reconciliation_required": True,
            }
            holdings_path.write_text(
                json.dumps({"version": 1, "updated_at": "before", "holdings": [duplicate, dict(duplicate, event_identities=["OLD2"])]}, indent=2),
                encoding="utf-8",
            )
            before = holdings_path.read_text(encoding="utf-8")

            result = record_broker_holding_snapshot(self._raw_event(received_at="2026-07-16 11:00:00"), holdings_path, positions_path, context=self._context())

            self.assertFalse(result["holding_recorded"])
            self.assertEqual("broker_holdings_source_integrity", result["holding_stage"])
            self.assertEqual(before, holdings_path.read_text(encoding="utf-8"))

    def test_duplicate_account_code_records_block_even_when_one_has_incoming_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            event = self._raw_event()
            first = record_broker_holding_snapshot(event, holdings_path, positions_path, context=self._context())
            data = json.loads(holdings_path.read_text(encoding="utf-8"))
            data["holdings"].append(dict(data["holdings"][0], event_identities=["OTHER_IDENTITY"]))
            holdings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            before = holdings_path.read_text(encoding="utf-8")

            result = record_broker_holding_snapshot(event, holdings_path, positions_path, context=self._context())

            self.assertTrue(first["holding_recorded"], first)
            self.assertFalse(result["holding_recorded"], result)
            self.assertEqual("broker_holdings_source_integrity", result["holding_stage"])
            self.assertEqual(before, holdings_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
