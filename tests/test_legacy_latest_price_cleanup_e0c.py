from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import order_candidate_engine as candidate_engine
import routine_signal_probe


class _ContextCaptureRoutine:
    ROUTINE_TYPE = "TEST"

    def __init__(self) -> None:
        self.context = None

    def evaluate(self, context):
        self.context = context
        return {"signal": "HOLD", "reason": "reference captured"}


class LegacyLatestPriceCleanupE0cTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stocks_dir = self.root / "stocks"
        self.stock_dir = self.stocks_dir / "002810_TEST"
        self.stock_dir.mkdir(parents=True)
        (self.stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (self.stock_dir / "state.json").write_text(
            json.dumps({"trade_enabled": True, "status": "WATCHING"}),
            encoding="utf-8",
        )
        self.stocks_patch = patch.object(candidate_engine, "STOCKS_DIR", self.stocks_dir)
        self.stocks_patch.start()

    def tearDown(self) -> None:
        self.stocks_patch.stop()
        self.temp.cleanup()

    def _write_candles(self, candles) -> None:
        (self.stock_dir / "candles.json").write_text(
            json.dumps(candles),
            encoding="utf-8",
        )

    def test_reference_price_ignores_legacy_json_and_uses_canonical_close(self) -> None:
        (self.stock_dir / "latest_price.json").write_text(
            json.dumps({"price": 99_999}),
            encoding="utf-8",
        )
        self._write_candles([{"close": 10_000}])

        self.assertEqual(10_000, candidate_engine.read_reference_price("002810", "TEST"))

    def test_legacy_json_only_does_not_supply_reference_price(self) -> None:
        (self.stock_dir / "latest_price.json").write_text(
            json.dumps({"price": 99_999}),
            encoding="utf-8",
        )

        self.assertIsNone(candidate_engine.read_reference_price("002810", "TEST"))

    def test_malformed_canonical_candle_file_fails_without_legacy_fallback(self) -> None:
        (self.stock_dir / "latest_price.json").write_text(
            json.dumps({"price": 99_999}),
            encoding="utf-8",
        )
        (self.stock_dir / "candles.json").write_text("{broken", encoding="utf-8")

        self.assertIsNone(candidate_engine.read_reference_price("002810", "TEST"))

    def test_previous_day_close_remains_valid_reference_evidence(self) -> None:
        self._write_candles(
            [{"trade_date": "2026-08-28", "datetime": "2026-08-28 15:30:00", "close": 10_000}]
        )

        self.assertEqual(10_000, candidate_engine.read_reference_price("002810", "TEST"))

    def test_candidate_consumer_uses_canonical_reference_close(self) -> None:
        (self.stock_dir / "latest_price.json").write_text(
            json.dumps({"price": 99_999}),
            encoding="utf-8",
        )
        (self.stock_dir / "config.json").write_text(
            json.dumps({"buy_qty": 2}),
            encoding="utf-8",
        )
        self._write_candles([{"close": 10_000}])

        candidate = candidate_engine.build_order_candidate(
            {"signal": "BUY", "code": "002810", "name": "TEST"}
        )

        self.assertEqual("CANDIDATE_READY", candidate["candidate_status"])
        self.assertEqual(10_000, candidate["price"])
        self.assertEqual("REFERENCE_PRICE", candidate["price_basis"])

    def test_signal_probe_consumer_uses_same_canonical_reference_close(self) -> None:
        (self.stock_dir / "latest_price.json").write_text(
            json.dumps({"price": 99_999}),
            encoding="utf-8",
        )
        self._write_candles([{"close": 10_000}])
        routine = _ContextCaptureRoutine()

        with patch.object(routine_signal_probe, "_load_candles_from_stock_dir", return_value=[]), patch.object(
            routine_signal_probe, "_append_log"
        ), patch.object(routine_signal_probe, "observe_owner_failure_transition"):
            result = routine_signal_probe.probe_routine_for_stock(
                routine,
                "test",
                self.stock_dir,
                "2026-08-28 15:30:00",
                decision_trace_observer=None,
            )

        self.assertEqual("HOLD", result["signal"])
        self.assertEqual(10_000, routine.context["reference_price"])
        self.assertNotIn("current_price", routine.context)


if __name__ == "__main__":
    unittest.main()
