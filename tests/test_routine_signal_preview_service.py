from pathlib import Path
import hashlib
import unittest

from engines.signal_result import RoutineSignal, signal_to_dict
import routine_signal_preview_service


class RoutineSignalPreviewServiceTest(unittest.TestCase):
    def _runtime_queue_hash(self):
        queue_path = Path(__file__).resolve().parents[1] / "runtime" / "routine_signals.json"
        return hashlib.sha256(queue_path.read_bytes()).hexdigest().upper() if queue_path.exists() else None

    def test_buy_preview_preserves_routine_signal_fields(self):
        signal = RoutineSignal(
            "BUY",
            "buy reason",
            ["buy_group"],
            ["PASS CLOSE >= 12"],
            2,
            0,
        )

        preview = routine_signal_preview_service.build_routine_signal_preview(
            signal,
            {
                "rule_source": "rules.json",
                "matched_rule_paths": ["buy.groups[0].conditions"],
                "preview_time": "2026-07-05T12:00:00+09:00",
                "condition_summary": ["buy group passed"],
                "engine_version": "test_engine_v1",
            },
        )

        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["stage"], "ROUTINE_SIGNAL_PREVIEW")
        self.assertEqual(preview["preview_type"], "routine_signal_preview")
        self.assertEqual(preview["signal"], "BUY")
        self.assertEqual(preview["reason"], "buy reason")
        self.assertEqual(preview["matched_groups"], ["buy_group"])
        self.assertEqual(preview["details"], ["PASS CLOSE >= 12"])
        self.assertEqual(preview["signal_index"], 2)
        self.assertEqual(preview["delay_bar"], 0)
        self.assertEqual(preview["rule_source"], "rules.json")
        self.assertEqual(preview["matched_rule_paths"], ["buy.groups[0].conditions"])
        self.assertEqual(preview["preview_time"], "2026-07-05T12:00:00+09:00")
        self.assertEqual(preview["condition_summary"], ["buy group passed"])
        self.assertEqual(preview["engine_version"], "test_engine_v1")
        self.assertEqual(preview["routine_signal"], signal_to_dict(signal))

    def test_sell_preview_preserves_routine_signal_fields(self):
        signal = RoutineSignal(
            "SELL",
            "sell reason",
            ["sell_group"],
            ["PASS MACD <= -1"],
            3,
            1,
        )

        preview = routine_signal_preview_service.preview_routine_signal(
            signal,
            {
                "rule_source": "rules.json",
                "matched_rule_paths": ["sell.signals.macd_sell.groups[0].conditions"],
                "preview_time": "2026-07-05T12:01:00+09:00",
            },
        )

        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["signal"], "SELL")
        self.assertEqual(preview["reason"], "sell reason")
        self.assertEqual(preview["matched_groups"], ["sell_group"])
        self.assertEqual(preview["details"], ["PASS MACD <= -1"])
        self.assertEqual(preview["matched_rule_paths"], ["sell.signals.macd_sell.groups[0].conditions"])
        self.assertEqual(preview["routine_signal"], signal_to_dict(signal))

    def test_none_preview_preserves_non_signal_result(self):
        signal = RoutineSignal(
            None,
            "conditions not met",
            [],
            ["FAIL CLOSE > 99"],
            2,
            0,
        )

        preview = routine_signal_preview_service.build_routine_signal_preview(
            signal,
            {"preview_time": "2026-07-05T12:02:00+09:00"},
        )

        self.assertTrue(preview["ok"], preview)
        self.assertIsNone(preview["signal"])
        self.assertEqual(preview["reason"], "conditions not met")
        self.assertEqual(preview["matched_groups"], [])
        self.assertEqual(preview["details"], ["FAIL CLOSE > 99"])
        self.assertEqual(preview["routine_signal"], signal_to_dict(signal))

    def test_preview_blocks_non_routine_signal_input(self):
        preview = routine_signal_preview_service.build_routine_signal_preview({"signal": "BUY"})

        self.assertFalse(preview["ok"])
        self.assertEqual(preview["stage"], "ROUTINE_SIGNAL_PREVIEW_BLOCKED")
        self.assertIn("routine_signal must be RoutineSignal", preview["blocked_reasons"])

    def test_preview_is_not_connected_to_queue_runtime_execution_or_send_order(self):
        signal = RoutineSignal("BUY", "buy reason", ["buy_group"], ["PASS"], 2, 0)
        queue_before = self._runtime_queue_hash()

        preview = routine_signal_preview_service.build_routine_signal_preview(signal)

        self.assertTrue(preview["ok"], preview)
        self.assertFalse(preview["queue_connected"])
        self.assertFalse(preview["runtime_write"])
        self.assertFalse(preview["execution_connected"])
        self.assertFalse(preview["send_order_connected"])
        self.assertEqual(queue_before, self._runtime_queue_hash())

    def test_preview_module_does_not_import_queue_runtime_execution_or_send_order(self):
        module_text = Path(routine_signal_preview_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("routine_signal_queue", module_text)
        self.assertNotIn("runtime_io", module_text)
        self.assertNotIn("import execution", module_text)
        self.assertNotIn("from execution", module_text)
        self.assertNotIn("SendOrder", module_text)
        self.assertNotIn("import send_order", module_text)
        self.assertNotIn("from send_order", module_text)


if __name__ == "__main__":
    unittest.main()
