"""Shared fixture for the canonical assigned-Routine timer boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest import mock

import gui_auto_trade_timer


def run_assigned_routine_timer_fixture(testcase, *, code: str = "005930") -> dict:
    """Prove Main routes an assigned stock through the Routine coordinator only."""
    with tempfile.TemporaryDirectory() as temp:
        stock_dir = Path(temp) / f"{code}_Fixture"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "state.json").write_text("{}", encoding="utf-8")
        entry = SimpleNamespace(
            execution_ready=True,
            signal_probe_only=False,
            stock_code=code,
            stock_name="fixture",
            stock_dir=stock_dir,
        )
        snapshot = SimpleNamespace(entries=(entry,))
        facts = {
            "revision": "FACTS-1",
            "snapshot_hash": "HASH-1",
            "stock_configs": {code: {"assigned_routine_instance_id": "INSTANCE-1"}},
            "stock_states": {code: {}},
            "allowed_stock_codes": [code],
            "actionable_prices_by_code": {},
        }
        capture = mock.Mock(return_value=SimpleNamespace(to_payload=lambda: dict(facts)))
        evaluate = mock.Mock(return_value={"ok": True, "decisions": []})
        consumer_summary = {"signals_checked": 0, "executable_order_ids": []}
        consumer = mock.Mock(return_value={"summary": consumer_summary})
        window = SimpleNamespace(
            statusBarMessage=mock.Mock(),
            mark_review_required=mock.Mock(return_value=True),
        )
        with (
            mock.patch.object(gui_auto_trade_timer, "capture_routine_main_facts", capture),
            mock.patch.object(gui_auto_trade_timer, "evaluate_routine_lifecycle", evaluate),
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True),
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

    testcase.assertEqual(1, evaluate.call_count)
    testcase.assertEqual("INSTANCE-1", evaluate.call_args.kwargs["instance_id"])
    testcase.assertEqual(facts, evaluate.call_args.kwargs["main_facts"])
    testcase.assertEqual(1, consumer.call_count)
    testcase.assertEqual((code,), consumer.call_args.kwargs["allowed_stock_codes"])
    testcase.assertEqual([], result["lifecycle"]["executable_order_ids"])
    return result
