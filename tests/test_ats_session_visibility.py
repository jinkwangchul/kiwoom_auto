from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt5.QtWidgets import QApplication, QPushButton

import gui_ats_utils
import gui_auto_trade_ats_ops as ats_ops
import gui_operation_environment as environment
from manual_ats_runtime import (
    manual_ats_runtime_selected_keys,
    write_manual_ats_runtime_selection,
)


class AtsSessionVisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_enabled_is_visible_but_explicit_false_is_hidden(self) -> None:
        cases = (
            ([{}, {}, {}], ("extra1", "extra2", "extra3")),
            ([{}, {"enabled": False}, {"enabled": True}], ("extra1", "extra3")),
            ([{"enabled": False}, {"enabled": True}, {"enabled": False}], ("extra2",)),
            ([{"enabled": False}, {"enabled": False}, {"enabled": False}], ()),
        )
        for sessions, expected in cases:
            with self.subTest(expected=expected), patch.object(
                gui_ats_utils,
                "read_operation_policy",
                return_value={"extra_sessions": sessions},
            ):
                self.assertEqual(expected, gui_ats_utils.manual_ats_visible_session_keys())

    def test_environment_checkbox_load_and_policy_build(self) -> None:
        policy = environment.default_operation_policy()
        policy["extra_sessions"] = [
            {"name": "장전프리", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": False, "name": "마감후NTX", "start_time": "15:30:00", "end_time": "19:50:00"},
            {"enabled": True, "name": "미지정", "start_time": "00:00:00", "end_time": "00:00:00"},
        ]
        with patch.object(environment, "read_operation_policy", return_value=policy):
            dialog = environment.OperationEnvironmentSettingsDialog()
        try:
            self.assertEqual([True, False, True], [item.isChecked() for item in dialog.extra_enabled])
            dialog.extra_enabled[0].setChecked(False)
            built = dialog.build_policy_from_widgets()
            self.assertEqual([False, False, True], [item["enabled"] for item in built["extra_sessions"]])
            with tempfile.TemporaryDirectory() as temp, patch.object(
                environment,
                "OPERATION_POLICY_PATH",
                Path(temp) / "operation_policy.json",
            ):
                environment.write_operation_policy(built)
                saved = json.loads(environment.OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
                self.assertEqual(
                    [False, False, True],
                    [item["enabled"] for item in saved["extra_sessions"]],
                )
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_ats_rows_have_no_individual_save_buttons(self) -> None:
        with patch.object(
            environment,
            "read_operation_policy",
            return_value=environment.default_operation_policy(),
        ):
            dialog = environment.OperationEnvironmentSettingsDialog()
        try:
            save_buttons = [
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == "저장"
            ]
            self.assertEqual(1, len(save_buttons))
            self.assertIs(
                dialog.settings_button_box.button(dialog.settings_button_box.Save),
                save_buttons[0],
            )
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_bottom_save_persists_ats_name_time_and_enabled_state(self) -> None:
        policy = environment.default_operation_policy()
        with patch.object(environment, "read_operation_policy", return_value=policy):
            dialog = environment.OperationEnvironmentSettingsDialog()
        try:
            dialog.regular_start.set_time("10:00:00")
            dialog.extra_enabled[0].setChecked(True)
            dialog.extra_name[0].setText("장전프리장")
            dialog.extra_start[0].set_time("07:30:00")
            dialog.extra_end[0].set_time("08:40:00")
            expected_policy = dialog.build_policy_from_widgets(
                dialog._validated_starting_budget_defaults()
            )

            with (
                patch.object(
                    environment,
                    "read_operation_policy",
                    side_effect=[dict(expected_policy), dict(expected_policy)],
                ),
                patch.object(environment, "write_operation_policy") as writer,
                patch.object(environment, "append_changelog"),
                patch.object(environment, "show_toast") as toast,
            ):
                dialog.settings_button_box.button(dialog.settings_button_box.Save).click()

                writer.assert_called_once()
                saved_policy = writer.call_args.args[0]
                self.assertEqual("10:00:00", saved_policy["regular_market"]["start_time"])
                self.assertEqual(
                    {
                        "enabled": True,
                        "name": "장전프리장",
                        "start_time": "07:30:00",
                        "end_time": "08:40:00",
                    },
                    saved_policy["extra_sessions"][0],
                )
                toast.assert_called_once_with(
                    parent=dialog,
                    message="환경설정을 저장했습니다.",
                    duration_ms=2000,
                    position="center",
                )
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_saving_visible_key_preserves_each_targets_hidden_runtime_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            targets = []
            expected_hidden = (("extra2",), ("extra3",))
            for index, hidden_keys in enumerate(expected_hidden, start=1):
                stock_dir = root / f"00000{index}_대상{index}"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps({"operation_mode": "CONTINUOUS"}),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "RUNNING"}),
                    encoding="utf-8",
                )
                self.assertTrue(
                    write_manual_ats_runtime_selection(
                        stock_dir,
                        {key: key in hidden_keys for key in ("extra1", "extra2", "extra3")},
                    )
                )
                targets.append((stock_dir, f"00000{index}", f"대상{index}"))

            window = MagicMock()
            window.capture_stock_table_view_state.return_value = (set(), 0)
            result = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                window,
                targets,
                {"extra1": True, "extra2": False, "extra3": False},
                editable_keys=("extra1",),
            )

            self.assertEqual(2, result["succeeded"])
            for (stock_dir, _code, _name), hidden_keys in zip(targets, expected_hidden):
                state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
                self.assertEqual(
                    {"extra1", *hidden_keys},
                    set(manual_ats_runtime_selected_keys(state)),
                )


if __name__ == "__main__":
    unittest.main()
