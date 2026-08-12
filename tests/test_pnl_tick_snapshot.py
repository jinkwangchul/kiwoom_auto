# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pnl_ui_refresh
from pnl_ui_refresh import (
    project_current_stock_pnl,
    project_current_stock_pnl_snapshot,
)


class PnlTickSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        (self.root / "stocks").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def _write_common_runtime(
        self,
        codes: list[str],
        *,
        fills: list[dict[str, object]] | None = None,
        realized: list[dict[str, object]] | None = None,
        positions: list[dict[str, object]] | None = None,
        holdings: list[dict[str, object]] | None = None,
    ) -> None:
        runtime = self.root / "runtime"
        self._write_json(
            runtime / "pnl_cycle_boundaries.json",
            {
                "boundaries": [
                    {
                        "stock_code": code,
                        "boundary_id": f"B-{code}",
                        "boundary_at": "2026-08-11T09:00:00+09:00",
                    }
                    for code in codes
                ]
            },
        )
        self._write_json(runtime / "fills.json", {"fills": fills or []})
        self._write_json(runtime / "realized_pnl.json", {"records": realized or []})
        self._write_json(runtime / "positions.json", {"positions": positions or []})
        self._write_json(runtime / "broker_holdings.json", {"holdings": holdings or []})

    def _write_stock(self, code: str, state: dict[str, object]) -> None:
        stock_dir = self.root / "stocks" / f"{code}_Test"
        stock_dir.mkdir()
        self._write_json(stock_dir / "state.json", state)

    def test_single_and_batch_results_are_identical_for_representative_states(self) -> None:
        codes = [f"{index:06d}" for index in range(1, 9)]
        fills = [
            {"fill_id": "2B", "broker_order_no": "2B", "code": codes[1], "side": "BUY", "filled_quantity": 2, "filled_price": 100, "received_at": "2026-08-11T09:10:00+09:00"},
            {"fill_id": "3B", "broker_order_no": "3B", "code": codes[2], "side": "BUY", "filled_quantity": 1, "filled_price": 100, "received_at": "2026-08-11T09:10:00+09:00"},
            {"fill_id": "3S", "broker_order_no": "3S", "code": codes[2], "side": "SELL", "filled_quantity": 1, "filled_price": 115, "received_at": "2026-08-11T10:00:00+09:00"},
            {"fill_id": "4B", "broker_order_no": "4B", "code": codes[3], "side": "BUY", "filled_quantity": 2, "filled_price": 100, "received_at": "2026-08-11T09:10:00+09:00"},
            {"fill_id": "4S", "broker_order_no": "4S", "code": codes[3], "side": "SELL", "filled_quantity": 1, "filled_price": 110, "received_at": "2026-08-11T10:00:00+09:00"},
        ]
        realized = [
            {"stock_code": codes[2], "gross_realized_profit": 15, "realized_at": "2026-08-11T10:00:00+09:00"},
            {"stock_code": codes[3], "gross_realized_profit": 10, "realized_at": "2026-08-11T10:00:00+09:00"},
        ]
        positions = [
            {"code": codes[1], "quantity": 2, "average_price": 100, "cost_basis": 200},
            {"code": codes[3], "quantity": 1, "average_price": 100, "cost_basis": 100},
        ]
        holdings = [
            {"code": codes[1], "quantity": 2},
            {"code": codes[3], "quantity": 1},
        ]
        self._write_common_runtime(
            codes,
            fills=fills,
            realized=realized,
            positions=positions,
            holdings=holdings,
        )
        states = [
            {"current_price": 100, "current_price_updated_at": "T1"},
            {"current_price": 110, "current_price_updated_at": "T2"},
            {"current_price": 120, "current_price_updated_at": "T3"},
            {"current_price": 105, "current_price_updated_at": "T4"},
            {"last_checked_price": 101, "last_checked_at": "T5"},
            {"updated_at": "T6"},
            {"current_price": 100, "routine_instance_id": "INSTANCE-A"},
            {"current_price": 100},
        ]
        for code, state in zip(codes, states):
            self._write_stock(code, state)

        singles = {
            code: project_current_stock_pnl(code, project_root=self.root)
            for code in codes
        }
        batch = project_current_stock_pnl_snapshot(codes, project_root=self.root)
        self.assertEqual(singles, batch)
        self.assertTrue(batch[codes[1]]["available"])
        self.assertEqual(20, batch[codes[1]]["unrealized_profit"])
        self.assertEqual(15, batch[codes[2]]["realized_profit"])
        self.assertEqual(15, batch[codes[3]]["cumulative_profit"])
        self.assertEqual(101, batch[codes[4]]["evaluation_price"])
        self.assertEqual("EVALUATION_PRICE_UNAVAILABLE", batch[codes[5]]["reason"])

    def test_batch_reuses_five_common_reads_and_reads_each_stock_once(self) -> None:
        codes = [f"{index:06d}" for index in range(1, 31)]
        self._write_common_runtime(codes)
        for code in codes:
            self._write_stock(code, {"current_price": 100})
        common_names = {
            "pnl_cycle_boundaries.json",
            "fills.json",
            "realized_pnl.json",
            "positions.json",
            "broker_holdings.json",
        }
        original_read_text = Path.read_text

        def measured_read(path: Path, *args, **kwargs):
            name = path.name
            if name in common_names:
                counts["common"] += 1
            elif name == "state.json":
                counts["state"] += 1
            return original_read_text(path, *args, **kwargs)

        counts = {"common": 0, "state": 0}
        with patch.object(Path, "read_text", measured_read):
            for code in codes:
                project_current_stock_pnl(code, project_root=self.root)
        before = dict(counts)

        counts = {"common": 0, "state": 0}
        with patch.object(Path, "read_text", measured_read):
            result = project_current_stock_pnl_snapshot(codes, project_root=self.root)
        after = dict(counts)

        self.assertEqual(30, len(result))
        self.assertEqual({"common": 150, "state": 30}, before)
        self.assertEqual({"common": 5, "state": 30}, after)
        self.assertEqual(180, sum(before.values()))
        self.assertEqual(35, sum(after.values()))

    def test_one_stock_failure_is_isolated_from_other_batch_results(self) -> None:
        codes = ["000001", "000002"]
        self._write_common_runtime(codes)
        for code in codes:
            self._write_stock(code, {"current_price": 100})
        original_read = pnl_ui_refresh.read_json_dict

        def selective_failure(path: Path):
            if path.parent.name.startswith("000002_"):
                raise OSError("state unavailable")
            return original_read(path)

        with patch.object(
            pnl_ui_refresh,
            "read_json_dict",
            side_effect=selective_failure,
        ):
            result = project_current_stock_pnl_snapshot(codes, project_root=self.root)
        self.assertTrue(result["000001"]["available"])
        self.assertFalse(result["000002"]["available"])
        self.assertIn("PNL_STOCK_PROJECTION_ERROR", result["000002"]["reason"])


if __name__ == "__main__":
    unittest.main()
