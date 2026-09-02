from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from routine_signal_probe import (
    _is_trade_watch_target,
    probe_all_enabled_routine_stocks_once,
)
from tests.participant_owner_fixture import participant_owner


class RoutineSignalProbeAllStocksTest(unittest.TestCase):
    def test_review_and_emergency_states_are_not_probe_targets(self) -> None:
        for status in (
            "REVIEW_REQUIRED",
            "REVIEW",
            "EMERGENCY_STOPPED",
            "EMERGENCY_STOP",
            "EMERGENCY",
        ):
            self.assertFalse(
                _is_trade_watch_target(
                    {
                        "status": status,
                        "trade_enabled": True,
                    }
                )
            )

    def test_probe_uses_stock_assignments_without_ui_routine_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_a = root / "111111_A"
            stock_b = root / "222222_B"
            stock_a.mkdir()
            stock_b.mkdir()
            for stock_dir, instance_id, definition_id, instance_name in (
                (stock_a, "inst-a", "def-a", "루틴 A"),
                (stock_b, "inst-b", "def-b", "루틴 B"),
            ):
                (stock_dir / "state.json").write_text(
                    '{"status":"MONITORING","trade_enabled":true}',
                    encoding="utf-8",
                )
                (stock_dir / "config.json").write_text(
                    (
                        '{"assigned_routine_instance_id":"%s",'
                        '"routine_definition_id":"%s",'
                        '"routine_instance_name":"%s"}'
                    )
                    % (instance_id, definition_id, instance_name),
                    encoding="utf-8",
                )

            definitions = [
                SimpleNamespace(
                    definition_id="def-a",
                    display_name="정의 A",
                    package_dir=root / "routine-a",
                    package_enabled=True,
                ),
                SimpleNamespace(
                    definition_id="def-b",
                    display_name="정의 B",
                    package_dir=root / "routine-b",
                    package_enabled=True,
                ),
            ]
            calls: list[tuple[str, Path]] = []

            def fake_probe(
                _module,
                routine_name,
                stock_dir,
                _tick_key,
                *,
                actionable_price_reader,
                **_kwargs,
            ):
                self.assertTrue(callable(actionable_price_reader))
                calls.append((routine_name, stock_dir))
                return {"signal": "NONE"}

            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner(
                    {"111111", "222222"}
                )
            )
            window.startup_recovery_session_ready = lambda refresh=False: True

            with (
                patch(
                    "execution_universe.all_registered_stock_dirs",
                    return_value=[stock_a, stock_b],
                ),
                patch(
                    "routine_signal_probe.load_routine_definitions",
                    return_value=definitions,
                ),
                patch(
                    "routine_signal_probe._load_routine_module",
                    side_effect=lambda path: path,
                ),
                patch(
                    "routine_signal_probe.routine_instance_by_id",
                    side_effect=lambda instance_id, **_kwargs: SimpleNamespace(
                        instance_id=instance_id,
                        definition_id=("def-a" if instance_id == "inst-a" else "def-b"),
                        display_name=("루틴 A" if instance_id == "inst-a" else "루틴 B"),
                        enabled=True,
                    ),
                ),
                patch(
                    "routine_signal_probe.probe_routine_for_stock",
                    side_effect=fake_probe,
                ),
            ):
                result = probe_all_enabled_routine_stocks_once(
                    window,
                    "tick",
                )

        self.assertEqual(
            [("루틴 A", stock_a), ("루틴 B", stock_b)],
            calls,
        )
        self.assertEqual(
            {"checked": 2, "logged": 2, "error": 0, "skip": 0, "queued": 0},
            result,
        )

    def test_probe_ignores_stale_trade_enabled_without_session_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "111111_A"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"MONITORING","trade_enabled":true}',
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text(
                (
                    '{"assigned_routine_instance_id":"inst-a",'
                    '"routine_definition_id":"def-a",'
                    '"routine_instance_name":"루틴 A"}'
                ),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner()
            )
            window.startup_recovery_session_ready = lambda refresh=False: True

            with (
                patch(
                    "execution_universe.all_registered_stock_dirs",
                    return_value=[stock_dir],
                ),
                patch(
                    "routine_signal_probe.load_routine_definitions",
                    return_value=[
                        SimpleNamespace(
                            definition_id="def-a",
                            display_name="정의 A",
                            package_dir=root / "routine-a",
                            package_enabled=True,
                        )
                    ],
                ),
                patch("routine_signal_probe.probe_routine_for_stock") as probe,
            ):
                result = probe_all_enabled_routine_stocks_once(window, "tick")

        probe.assert_not_called()
        self.assertEqual(
            {"checked": 0, "logged": 0, "error": 0, "skip": 0, "queued": 0},
            result,
        )


if __name__ == "__main__":
    unittest.main()
