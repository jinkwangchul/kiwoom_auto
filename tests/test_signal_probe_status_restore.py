from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gui_auto_trade_run_control as run_control
import gui_auto_trade_status_ops as status_ops


class _ProbeWindow:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, object]]] = []

    def update_stock_status(
        self,
        stock_dir,
        _code,
        _name,
        new_status,
        extra_state=None,
        _log_suffix="",
    ) -> bool:
        state_path = Path(stock_dir) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = new_status
        state.update(dict(extra_state or {}))
        state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        self.writes.append((str(new_status), dict(extra_state or {})))
        return True


class _ProbeControlWindow:
    def __init__(self, stock_dir: Path) -> None:
        self.stock_dir = stock_dir
        self.messages: list[str] = []

    def selected_stock_infos(self):
        return [(self.stock_dir, "005930", "삼성전자")]

    def statusBarMessage(self, message: str) -> None:
        self.messages.append(message)


class SignalProbeStatusRestoreTest(unittest.TestCase):
    def run_recalculation(
        self,
        state: dict[str, object],
    ) -> tuple[tuple[str, str, str], _ProbeWindow, dict[str, object]]:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            window = _ProbeWindow()
            with patch.object(status_ops, "append_stock_log"):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    "signal-probe 상태복원 테스트",
                )
            saved = json.loads(
                (stock_dir / "state.json").read_text(encoding="utf-8")
            )
        return result, window, saved

    def test_legacy_buy_sell_values_do_not_trigger_probe_restore(self) -> None:
        canonical = {
            "status": "MONITORING",
            "signal_probe_only": True,
            "trade_enabled": True,
        }
        for buy_enabled, sell_enabled in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        ):
            with self.subTest(
                buy_enabled=buy_enabled,
                sell_enabled=sell_enabled,
            ):
                state = dict(
                    canonical,
                    buy_enabled=buy_enabled,
                    sell_enabled=sell_enabled,
                )
                result, window, saved = self.run_recalculation(state)
                self.assertEqual(
                    ("protected", "MONITORING", "MONITORING"), result
                )
                self.assertEqual([], window.writes)
                self.assertEqual(state, saved)

    def test_canonical_probe_sources_restore_when_mismatched(self) -> None:
        scenarios = (
            ("trade disabled", {"trade_enabled": False}),
            ("stopped status", {"status": "STOPPED"}),
            ("running status", {"status": "RUNNING"}),
        )
        base = {
            "status": "MONITORING",
            "signal_probe_only": True,
            "trade_enabled": True,
        }
        for label, updates in scenarios:
            with self.subTest(label=label):
                state = dict(base)
                state.update(updates)
                result, window, saved = self.run_recalculation(state)
                self.assertEqual("protected", result[0])
                self.assertEqual(1, len(window.writes))
                self.assertEqual("MONITORING", saved["status"])
                self.assertIs(saved["signal_probe_only"], True)
                self.assertIs(saved["trade_enabled"], True)
                self.assertNotIn("real_trade_enabled", saved)
                self.assertNotIn("buy_enabled", saved)
                self.assertNotIn("sell_enabled", saved)

    def run_probe_control(
        self,
        action,
        state: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object]]:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            window = _ProbeControlWindow(stock_dir)
            with patch.object(run_control, "_refresh_signal_probe_only_window"):
                result = action(window)
            saved = json.loads(
                (stock_dir / "state.json").read_text(encoding="utf-8")
            )
        return result, saved

    def test_probe_start_and_stop_do_not_create_legacy_permission_keys(self) -> None:
        start_result, started = self.run_probe_control(
            run_control.start_signal_probe_only_for_selected_stocks,
            {"status": "STOPPED"},
        )
        self.assertEqual(1, start_result["count"])
        self.assertEqual("MONITORING", started["status"])
        self.assertIs(started["signal_probe_only"], True)
        self.assertIs(started["trade_enabled"], True)
        self.assertNotIn("real_trade_enabled", started)
        self.assertNotIn("buy_enabled", started)
        self.assertNotIn("sell_enabled", started)

        stop_result, stopped = self.run_probe_control(
            run_control.stop_signal_probe_only_for_selected_stocks,
            {
                "status": "MONITORING",
                "signal_probe_only": True,
                "trade_enabled": True,
            },
        )
        self.assertEqual(1, stop_result["count"])
        self.assertEqual("STOPPED", stopped["status"])
        self.assertIs(stopped["signal_probe_only"], False)
        self.assertIs(stopped["trade_enabled"], False)
        self.assertNotIn("real_trade_enabled", stopped)
        self.assertNotIn("buy_enabled", stopped)
        self.assertNotIn("sell_enabled", stopped)

    def test_probe_start_and_stop_preserve_existing_legacy_keys(self) -> None:
        legacy = {
            "buy_enabled": True,
            "sell_enabled": False,
        }
        for action, state in (
            (
                run_control.start_signal_probe_only_for_selected_stocks,
                {"status": "STOPPED", **legacy},
            ),
            (
                run_control.stop_signal_probe_only_for_selected_stocks,
                {
                    "status": "MONITORING",
                    "signal_probe_only": True,
                    "trade_enabled": True,
                    "real_trade_enabled": False,
                    **legacy,
                },
            ),
        ):
            with self.subTest(action=action.__name__):
                result, saved = self.run_probe_control(action, state)
                self.assertEqual(1, result["count"])
                self.assertIs(saved["buy_enabled"], True)
                self.assertIs(saved["sell_enabled"], False)


if __name__ == "__main__":
    unittest.main()
