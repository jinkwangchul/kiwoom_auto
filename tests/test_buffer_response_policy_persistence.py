# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

import buffer_response_policy_projection as projection
import buffer_response_coordinator as coordinator
import gui_operation_environment as environment
import gui_main_budget_panel as budget_panel
import gui_windows


def _pnl() -> dict[str, dict[str, object]]:
    return {
        "005930": {
            "available": True,
            "cumulative_profit": 100,
            "cumulative_rate": "5.0",
            "open_cost": 1000,
        }
    }


def _activity(ratio: int = 50) -> dict[str, object]:
    return {"available": True, "entry_amount": 1, "entry_ratio": ratio}


class BufferResponsePolicyPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy_path = Path(self.temp.name) / "operation_policy.json"

    def _surface(self) -> tuple[QWidget, gui_windows._BufferResponseSettingsSurface]:
        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        surface = gui_windows._BufferResponseSettingsSurface(
            owner,
            policy_path=self.policy_path,
        )
        self.addCleanup(surface.close)
        return owner, surface

    def test_missing_policy_shows_defaults_but_remains_unconfigured_until_save(self) -> None:
        _owner, surface = self._surface()
        unavailable = environment.read_buffer_response_policy(path=self.policy_path)
        self.assertFalse(unavailable["available"])
        self.assertEqual("BUFFER_RESPONSE_POLICY_NOT_CONFIGURED", unavailable["reason"])
        self.assertEqual("UNIFIED", surface.application_mode())
        self.assertEqual("80%", surface.buffer_close_ratio_combo.currentText())
        self.assertTrue(surface.save_button.isEnabled())

        surface.accept()
        saved = environment.read_buffer_response_policy(path=self.policy_path)
        self.assertTrue(saved["available"])
        self.assertEqual("UNIFIED", saved["application_mode"])
        self.assertEqual(80, saved["threshold_percent"])
        self.assertFalse(surface.save_button.isEnabled())

    def test_writer_preserves_every_existing_operation_policy_section(self) -> None:
        original = environment.default_operation_policy()
        original["custom_existing_section"] = {"keep": "yes"}
        environment.write_operation_policy(original, path=self.policy_path)
        environment.write_buffer_response_policy(
            environment.default_buffer_response_policy(),
            path=self.policy_path,
        )
        saved = environment.read_operation_policy(path=self.policy_path)
        self.assertEqual({"keep": "yes"}, saved["custom_existing_section"])
        self.assertEqual(original["system_budget"], saved["system_budget"])
        self.assertEqual(original["early_close"], saved["early_close"])

        without_buffer = environment.default_operation_policy()
        without_buffer["custom_existing_section"] = {"keep": "updated"}
        environment.write_operation_policy(without_buffer, path=self.policy_path)
        reread = environment.read_buffer_response_policy(path=self.policy_path)
        self.assertTrue(reread["available"])
        self.assertEqual(
            {"keep": "updated"},
            environment.read_operation_policy(path=self.policy_path)[
                "custom_existing_section"
            ],
        )

        environment.write_operation_policy(
            environment.default_operation_policy(),
            path=self.policy_path,
            preserve_buffer_response=False,
        )
        reset = environment.read_buffer_response_policy(path=self.policy_path)
        self.assertFalse(reset["available"])
        self.assertEqual("BUFFER_RESPONSE_POLICY_NOT_CONFIGURED", reset["reason"])

    def test_dirty_detection_tracks_values_and_can_return_to_baseline(self) -> None:
        baseline = environment.default_buffer_response_policy()
        environment.write_buffer_response_policy(baseline, path=self.policy_path)
        _owner, surface = self._surface()
        self.assertFalse(surface.save_button.isEnabled())

        unified_factor, unified_direction = surface.strategy_rows["unified"][0]
        unified_factor.setCurrentText("투입금액")
        self.assertTrue(surface.save_button.isEnabled())
        unified_factor.setCurrentText("손익금액")
        self.assertFalse(surface.save_button.isEnabled())

        unified_direction.setCurrentText("높은순")
        self.assertTrue(surface.save_button.isEnabled())
        unified_direction.setCurrentText("낮은순")
        self.assertFalse(surface.save_button.isEnabled())

        surface._cycle_strategy_action_badge("unified")
        self.assertTrue(surface.save_button.isEnabled())
        surface.strategy_action_badges["unified"].setText("조기마감")
        surface._refresh_save_enabled()
        self.assertFalse(surface.save_button.isEnabled())

        surface.buffer_close_ratio_combo.setCurrentText("60%")
        self.assertTrue(surface.save_button.isEnabled())
        surface.buffer_close_ratio_combo.setCurrentText("80%")
        self.assertFalse(surface.save_button.isEnabled())

        surface.segmented_checkbox.click()
        self.assertTrue(surface.save_button.isEnabled())
        surface.unified_checkbox.click()
        self.assertFalse(surface.save_button.isEnabled())

    def test_cancel_and_window_close_discard_unsaved_editor_state(self) -> None:
        saved = environment.default_buffer_response_policy()
        environment.write_buffer_response_policy(saved, path=self.policy_path)
        _owner, surface = self._surface()
        factor = surface.strategy_rows["unified"][0][0]
        factor.setCurrentText("투입금액")
        surface.reject()
        self.assertEqual("손익금액", factor.currentText())
        self.assertEqual(
            "손익금액",
            environment.read_buffer_response_policy(path=self.policy_path)[
                "strategies"
            ]["unified"]["evaluation_factor"],
        )

        factor.setCurrentText("투입금액")
        surface.close()
        surface.reload_from_persisted()
        self.assertEqual("손익금액", factor.currentText())

    def test_saved_values_restore_in_a_fresh_surface(self) -> None:
        configured = environment.default_buffer_response_policy()
        configured["application_mode"] = "SEGMENTED"
        configured["threshold_percent"] = 60
        configured["strategies"]["profit"] = {
            "evaluation_factor": "손익비율",
            "direction": "낮은순",
            "response_mode": "구간마감",
        }
        configured["strategies"]["loss"] = {
            "evaluation_factor": "투입금액",
            "direction": "높은순",
            "response_mode": "즉시청산",
        }
        environment.write_buffer_response_policy(configured, path=self.policy_path)
        _owner, surface = self._surface()
        self.assertEqual("SEGMENTED", surface.application_mode())
        self.assertEqual("60%", surface.buffer_close_ratio_combo.currentText())
        self.assertEqual(
            ("손익비율", "낮은순", "구간마감"),
            (
                surface.strategy_rows["profit"][0][0].currentText(),
                surface.strategy_rows["profit"][0][1].currentText(),
                surface.strategy_action_badges["profit"].text(),
            ),
        )
        self.assertFalse(surface.save_button.isEnabled())

    def test_saved_values_restore_in_a_fresh_python_process(self) -> None:
        configured = environment.default_buffer_response_policy()
        configured["application_mode"] = "SEGMENTED"
        configured["threshold_percent"] = 40
        configured["strategies"]["loss"] = {
            "evaluation_factor": "투입금액",
            "direction": "높은순",
            "response_mode": "구간마감",
        }
        environment.write_buffer_response_policy(configured, path=self.policy_path)
        script = textwrap.dedent(
            """
            import json
            import sys
            from pathlib import Path
            from PyQt5.QtWidgets import QApplication, QWidget
            from gui_windows import _BufferResponseSettingsSurface

            app = QApplication([])
            owner = QWidget()
            surface = _BufferResponseSettingsSurface(
                owner,
                policy_path=Path(sys.argv[1]),
            )
            loss_factor, loss_direction = surface.strategy_rows["loss"][0]
            print(json.dumps({
                "mode": surface.application_mode(),
                "threshold": surface.buffer_close_ratio_combo.currentText(),
                "factor": loss_factor.currentText(),
                "direction": loss_direction.currentText(),
                "response": surface.strategy_action_badges["loss"].text(),
                "save_enabled": surface.save_button.isEnabled(),
            }, ensure_ascii=False))
            surface.close()
            """
        )
        child_env = dict(os.environ)
        child_env["QT_QPA_PLATFORM"] = "offscreen"
        child_env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.policy_path)],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        restored = json.loads(completed.stdout.strip())
        self.assertEqual(
            {
                "mode": "SEGMENTED",
                "threshold": "40%",
                "factor": "투입금액",
                "direction": "높은순",
                "response": "구간마감",
                "save_enabled": False,
            },
            restored,
        )

    def test_production_projection_ignores_unsaved_open_editor_changes(self) -> None:
        saved = environment.default_buffer_response_policy()
        saved["strategies"]["unified"]["response_mode"] = "구간마감"
        environment.write_buffer_response_policy(saved, path=self.policy_path)
        _owner, surface = self._surface()
        surface.strategy_rows["unified"][0][0].setCurrentText("투입금액")
        surface.strategy_rows["unified"][0][1].setCurrentText("높은순")
        surface.strategy_action_badges["unified"].setText("즉시청산")
        surface.buffer_close_ratio_combo.setCurrentText("50%")

        persisted = environment.read_buffer_response_policy(path=self.policy_path)
        result = projection.project_buffer_response_policy(
            settings_policy=persisted,
            pnl_by_stock=_pnl(),
            budget_activity=_activity(70),
        )
        self.assertEqual("손익금액", result["evaluation_factor"])
        self.assertEqual("ASCENDING", result["sort_direction"])
        self.assertEqual(80, result["configured_threshold"])
        self.assertEqual("BUFFER_ENTRY_THRESHOLD", result["configured_response_mode"])

        surface.accept()
        updated = projection.project_buffer_response_policy(
            settings_policy=environment.read_buffer_response_policy(
                path=self.policy_path
            ),
            pnl_by_stock=_pnl(),
            budget_activity=_activity(70),
        )
        self.assertEqual("투입금액", updated["evaluation_factor"])
        self.assertEqual("DESCENDING", updated["sort_direction"])
        self.assertEqual(50, updated["configured_threshold"])
        self.assertEqual("IMMEDIATE_LIQUIDATION", updated["configured_response_mode"])

    def test_production_coordinator_has_no_settings_widget_dependency(self) -> None:
        source = inspect.getsource(coordinator)
        self.assertNotIn("_main_buffer_response_settings_surface", source)
        self.assertNotIn("settings_surface", source)
        self.assertIn("read_buffer_response_policy", source)

    def test_absent_and_malformed_persisted_policy_fail_closed(self) -> None:
        absent = projection.project_buffer_response_policy(
            settings_policy=environment.read_buffer_response_policy(
                path=self.policy_path
            ),
            pnl_by_stock=_pnl(),
            budget_activity=_activity(),
        )
        self.assertFalse(absent["applicable"])
        self.assertEqual("BUFFER_RESPONSE_POLICY_NOT_CONFIGURED", absent["reason"])

        malformed = environment.default_operation_policy()
        malformed["buffer_response"] = {
            "application_mode": "UNIFIED",
            "threshold_percent": 75,
            "strategies": {},
        }
        self.policy_path.write_text(
            json.dumps(malformed, ensure_ascii=False),
            encoding="utf-8",
        )
        loaded = environment.read_buffer_response_policy(path=self.policy_path)
        self.assertFalse(loaded["available"])
        self.assertEqual("BUFFER_RESPONSE_POLICY_MALFORMED", loaded["reason"])
        projected = projection.project_buffer_response_policy(
            settings_policy=loaded,
            pnl_by_stock=_pnl(),
            budget_activity=_activity(),
        )
        self.assertFalse(projected["applicable"])
        self.assertEqual("BUFFER_RESPONSE_POLICY_MALFORMED", projected["reason"])

    def test_persisted_setting_does_not_enable_a_disabled_buffer(self) -> None:
        environment.write_buffer_response_policy(
            environment.default_buffer_response_policy(),
            path=self.policy_path,
        )
        activity = budget_panel.project_main_budget_activity(
            {
                "total_budget": 400_000_000,
                "available_budget": 400_000_000,
                "buffer_budget": 0,
                "buffer_enabled": False,
            },
            {"available": True, "consumed_amount": 100_000_000},
        )
        self.assertIsNone(activity["entry_amount"])
        self.assertIsNone(activity["entry_ratio"])
        result = projection.project_buffer_response_policy(
            settings_policy=environment.read_buffer_response_policy(
                path=self.policy_path
            ),
            pnl_by_stock=_pnl(),
            budget_activity=activity,
        )
        self.assertFalse(result["applicable"])
        self.assertEqual("BUFFER_ENTRY_PROJECTION_UNAVAILABLE", result["reason"])

    def test_all_allowed_thresholds_round_trip_and_invalid_values_do_not_write(self) -> None:
        for threshold in range(10, 100, 10):
            configured = environment.default_buffer_response_policy()
            configured["threshold_percent"] = threshold
            environment.write_buffer_response_policy(
                configured,
                path=self.policy_path,
            )
            self.assertEqual(
                threshold,
                environment.read_buffer_response_policy(path=self.policy_path)[
                    "threshold_percent"
                ],
            )
        before = self.policy_path.read_bytes()
        invalid = environment.default_buffer_response_policy()
        invalid["threshold_percent"] = 75
        with self.assertRaises(ValueError):
            environment.write_buffer_response_policy(invalid, path=self.policy_path)
        self.assertEqual(before, self.policy_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
