from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import routine_signal_probe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GlobalDiagnosticObserverPhase5Tests(unittest.TestCase):
    def test_signal_probe_keeps_only_the_generic_file_log_call(self) -> None:
        source = (PROJECT_ROOT / "routine_signal_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_append_log"
        ]

        self.assertEqual(1, len(calls))
        self.assertIn("signal={result.get('signal')}", source)
        self.assertNotIn("ERROR routine assignment unresolved", source)
        self.assertNotIn("ERROR routine load:", source)

    def test_import_failure_is_event_observed_without_duplicate_probe_log(self) -> None:
        window = SimpleNamespace(
            current_selected_routine_dir=Mock(return_value=Path("missing-routine")),
            current_selected_routine_name=Mock(return_value="test-routine"),
        )
        with patch.object(
            routine_signal_probe,
            "_load_routine_module",
            side_effect=RuntimeError("import failed"),
        ), patch.object(
            routine_signal_probe, "observe_production_exception"
        ) as observe, patch.object(routine_signal_probe, "_append_log") as append_log:
            result = routine_signal_probe.probe_selected_routine_once(window)

        self.assertEqual(1, result["error"])
        observe.assert_called_once()
        append_log.assert_not_called()

    def test_generic_none_result_remains_available_to_probe_log_reader(self) -> None:
        class NoneRoutine:
            ROUTINE_TYPE = "test"

            @staticmethod
            def evaluate(_context):
                return {"signal": "NONE", "reason": "no signal"}

        state = {"trade_enabled": True, "status": "RUNNING"}

        def read_dict(path: Path):
            return state if Path(path).name == "state.json" else {}

        with tempfile.TemporaryDirectory() as temp, patch.object(
            routine_signal_probe, "_read_json_dict", side_effect=read_dict
        ), patch.object(
            routine_signal_probe, "_load_candles_from_stock_dir", return_value=[]
        ), patch.object(
            routine_signal_probe, "_load_instance_rules", return_value={}
        ), patch.object(
            routine_signal_probe, "completed_timeframe_candles", return_value=[]
        ), patch.object(
            routine_signal_probe, "read_reference_price", return_value=None
        ), patch.object(
            routine_signal_probe, "_default_decision_trace_observer", return_value=None
        ), patch.object(routine_signal_probe, "_append_log") as append_log:
            result = routine_signal_probe.probe_routine_for_stock(
                NoneRoutine,
                "test-routine",
                Path(temp) / "005930_test",
                "2026-08-16 09:00",
            )

        self.assertEqual("NONE", result["signal"])
        append_log.assert_called_once()
        line = append_log.call_args.args[0]
        self.assertIn("signal=NONE", line)
        self.assertIn("reason=no signal", line)


if __name__ == "__main__":
    unittest.main()
