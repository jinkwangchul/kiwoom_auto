# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import routine_signal_probe
from routine_main_facts import build_routine_main_facts_from_projection


class _RoutineModule:
    ROUTINE_TYPE = "INDICATOR_FOLLOW"

    def __init__(self) -> None:
        self.context = None

    @staticmethod
    def market_bar_projection_request(_rules):
        return {"projection": "COMPLETED_TIMEFRAME"}

    @staticmethod
    def project_cycle_context(**kwargs):
        return {
            "status": "resolved",
            "active": False,
            "confirmed_buy_round": 0,
            "cumulative_filled_buy_amount": 0.0,
            "holding_qty": 0,
            "avg_price": 0.0,
            "last_buy_order_identity": None,
            "partial_sell": False,
            "cycle_ended": False,
            "unresolved_reason": "",
            "projection_code": kwargs["code"],
            "projection_instance": kwargs["routine_instance_id"],
        }

    def evaluate(self, context):
        self.context = context
        return {"signal": "HOLD", "reason": "test"}


class RoutineCycleContextBridgeTest(unittest.TestCase):
    def test_probe_passes_read_only_cycle_projection_to_routine(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stock_dir = root / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"trade_enabled": True, "status": "WATCHING"}),
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text(
                json.dumps({"assigned_routine_instance_id": "INSTANCE_A"}),
                encoding="utf-8",
            )
            main_facts = build_routine_main_facts_from_projection(
                {
                    "signals": [],
                    "orders": [],
                    "executions": [],
                    "processes": [],
                    "fills": [],
                    "positions": [],
                    "holdings": [],
                    "stock_configs": {
                        "005930": {"assigned_routine_instance_id": "INSTANCE_A"}
                    },
                    "stock_states": {
                        "005930": {"trade_enabled": True, "status": "WATCHING"}
                    },
                    "selected_account_no": "",
                    "allowed_stock_codes": ["005930"],
                    "actionable_prices_by_code": {"005930": 71_000},
                    "current_orderable_cash": None,
                    "budget": {},
                    "limits": {},
                    "market": {
                        "raw_candles_by_code": {"005930": []},
                        "reference_prices_by_code": {"005930": 70_000},
                    },
                    "review": {},
                }
            ).to_payload()
            module = _RoutineModule()

            with (
                mock.patch.object(routine_signal_probe, "_load_candles_from_stock_dir", return_value=[]),
                mock.patch.object(routine_signal_probe, "read_reference_price", return_value=70_000),
                mock.patch.object(routine_signal_probe, "_append_log"),
            ):
                result = routine_signal_probe.probe_routine_for_stock(
                    module,
                    "지표추종매매",
                    stock_dir,
                    "TICK_1",
                    main_facts=main_facts,
                    fresh_main_facts_provider=lambda: main_facts,
                )

            self.assertEqual("HOLD", result["signal"])
            self.assertIsNotNone(module.context)
            self.assertEqual("resolved", module.context["cycle"]["status"])
            self.assertEqual("005930", module.context["cycle"]["projection_code"])
            self.assertEqual("INSTANCE_A", module.context["cycle"]["projection_instance"])
            self.assertEqual(70_000, module.context["reference_price"])
            self.assertEqual(71_000, module.context["actionable_current_price"])
            self.assertNotIn("current_price", module.context)

    def test_indicator_follow_routine_blocks_buy_when_cycle_is_unresolved(self) -> None:
        routine_dir = (
            Path(__file__).resolve().parents[1] / "routines" / "지표추종매매"
        )
        spec = importlib.util.spec_from_file_location(
            "indicator_follow_routine_cycle_bridge_test",
            routine_dir / "routine.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(routine_dir))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(routine_dir))
        module.evaluate_indicator_follow_routine = lambda candles, config, context: {"raw": True}
        module.signal_to_dict = lambda signal: {"signal": "BUY", "reason": "buy"}

        result = module.evaluate({
            "candles": [],
            "rules": {},
            "cycle": {
                "status": "unresolved",
                "unresolved_reason": "FOREIGN_ORDER_MIXED_IN_ACTIVE_CYCLE",
            },
        })

        self.assertIsNone(result["signal"])
        self.assertTrue(result["buy_execution_blocked"])
        self.assertEqual(
            "FOREIGN_ORDER_MIXED_IN_ACTIVE_CYCLE",
            result["buy_execution_blocked_reason"],
        )


if __name__ == "__main__":
    unittest.main()
