from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

import gui_windows
from routine_instance_registry import (
    default_routine_limit_response_policy,
    validate_routine_limit_response_policy,
)
from routine_instance_repository import RoutineInstanceRepository


INSTANCE_IDS = (
    UUID("b8f46f9c-4243-4a0e-8593-0a27772c86b1"),
    UUID("0df6bb89-f613-484e-b2ae-772e953f9d7f"),
    UUID("50579222-f70f-4e41-a6d0-e094f8bc38ac"),
)


class RoutineLimitResponseSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _repository(self, root: Path) -> RoutineInstanceRepository:
        routine_dir = root / "routines" / "indicator_follow"
        routine_dir.mkdir(parents=True)
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "definition_id": "indicator_follow",
                    "name": "지표추종매매",
                    "settings_ui": "indicator_follow",
                    "module_name": "indicator_follow_routine",
                    "rules_file": "rules.json",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return RoutineInstanceRepository(
            root,
            now_factory=lambda: datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        )

    def _write_instance(
        self,
        root: Path,
        instance_id: UUID,
        *,
        enabled: bool = False,
        amount: int | None = None,
        ratio: str | None = None,
        policy: dict[str, object] | None = None,
    ) -> Path:
        instance_dir = root / "routine_instances" / str(instance_id)
        instance_dir.mkdir(parents=True)
        metadata: dict[str, object] = {
            "schema_version": "1.0",
            "instance_id": str(instance_id),
            "definition_id": "indicator_follow",
            "display_name": f"루틴 {str(instance_id)[:8]}",
            "description": "",
            "enabled": False,
            "buy_limit_enabled": enabled,
            "buy_limit_amount": amount,
            "rules_file": "rules.json",
            "created_at": "2026-08-20T08:00:00+00:00",
            "updated_at": "2026-08-20T08:00:00+00:00",
        }
        if ratio is not None:
            metadata["buy_limit_adjustment_ratio"] = ratio
        if policy is not None:
            metadata["buy_limit_response_policy"] = deepcopy(policy)
        metadata_path = instance_dir / "instance.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (instance_dir / "rules.json").write_text("{}\n", encoding="utf-8")
        return metadata_path

    @staticmethod
    def _combo_texts(combo) -> list[str]:
        return [combo.itemText(index) for index in range(combo.count())]

    def _surface(
        self,
        repository: RoutineInstanceRepository,
        instance_id: UUID = INSTANCE_IDS[0],
    ) -> tuple[QWidget, gui_windows._RoutineLimitResponseSettingsSurface]:
        owner = QWidget()
        surface = gui_windows._RoutineLimitResponseSettingsSurface(
            owner,
            str(instance_id),
            repository=repository,
        )
        return owner, surface

    def test_default_policy_projection_is_complete_and_not_persisted(self) -> None:
        expected = default_routine_limit_response_policy()
        self.assertEqual("UNIFIED", expected["application_mode"])
        self.assertEqual(
            {
                "evaluation_factor": "손익금액",
                "direction": "낮은순",
                "response_mode": "조기마감",
            },
            expected["strategies"]["unified"],
        )
        self.assertEqual(
            {"early_close_percent": 90, "immediate_liquidation_percent": 100},
            expected["segment_close"],
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            metadata_path = self._write_instance(root, INSTANCE_IDS[0])
            before = metadata_path.read_bytes()
            owner, surface = self._surface(repository)

            self.assertEqual(expected, surface._editor_policy())
            surface.reject()
            self.assertEqual(before, metadata_path.read_bytes())
            self.assertNotIn(
                "buy_limit_response_policy",
                json.loads(metadata_path.read_text(encoding="utf-8")),
            )
            owner.deleteLater()

    def test_mode_rows_and_saved_segmented_mode_round_trip(self) -> None:
        policy = default_routine_limit_response_policy()
        policy["application_mode"] = "SEGMENTED"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(root, INSTANCE_IDS[0], policy=policy)
            owner, surface = self._surface(repository)

            self.assertFalse(surface.unified_checkbox.isChecked())
            self.assertTrue(surface.segmented_checkbox.isChecked())
            self.assertFalse(surface.unified_strategy_row.isEnabled())
            self.assertTrue(surface.profit_strategy_row.isEnabled())
            self.assertTrue(surface.loss_strategy_row.isEnabled())
            surface.set_application_mode(surface.MODE_UNIFIED)
            self.assertTrue(surface.unified_checkbox.isChecked())
            self.assertFalse(surface.segmented_checkbox.isChecked())
            self.assertTrue(surface.unified_strategy_row.isEnabled())
            self.assertFalse(surface.profit_strategy_row.isEnabled())
            owner.deleteLater()

    def test_action_cycle_and_color_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(root, INSTANCE_IDS[0])
            owner, surface = self._surface(repository)
            badge = surface.strategy_action_badges["unified"]

            self.assertIn("#2563EB", badge.styleSheet())
            self.assertIn("#DC2626", surface.segment_early_close_label.styleSheet())
            self.assertNotIn("green", surface.styleSheet().lower())
            self.assertEqual("※구간마감:", surface.segment_close_title_label.text())
            self.assertEqual("조기마감", surface.segment_early_close_label.text())
            self.assertEqual(
                "즉시청산",
                surface.segment_immediate_liquidation_label.text(),
            )
            surface._cycle_strategy_action_badge("unified")
            self.assertEqual("즉시청산", badge.text())
            surface._cycle_strategy_action_badge("unified")
            self.assertEqual("구간마감", badge.text())
            self.assertIn("#DC2626", badge.styleSheet())
            surface._cycle_strategy_action_badge("unified")
            self.assertEqual("조기마감", badge.text())
            self.assertIn("#2563EB", badge.styleSheet())
            owner.deleteLater()

    def test_threshold_options_preserve_or_raise_immediate_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(root, INSTANCE_IDS[0])
            owner, surface = self._surface(repository)

            self.assertEqual(
                ["50%", "60%", "70%", "80%", "90%"],
                self._combo_texts(surface.early_close_percent_combo),
            )
            self.assertEqual("100%", surface.immediate_liquidation_percent_combo.currentText())
            surface.early_close_percent_combo.setCurrentText("50%")
            self.assertEqual(
                ["60%", "70%", "80%", "90%", "100%"],
                self._combo_texts(surface.immediate_liquidation_percent_combo),
            )
            surface.immediate_liquidation_percent_combo.setCurrentText("80%")
            surface.early_close_percent_combo.setCurrentText("70%")
            self.assertEqual("80%", surface.immediate_liquidation_percent_combo.currentText())
            surface.early_close_percent_combo.setCurrentText("80%")
            self.assertEqual("90%", surface.immediate_liquidation_percent_combo.currentText())
            surface.early_close_percent_combo.setCurrentText("90%")
            self.assertEqual(
                ["100%"],
                self._combo_texts(surface.immediate_liquidation_percent_combo),
            )
            surface.immediate_liquidation_percent_combo.addItem("90%")
            surface.immediate_liquidation_percent_combo.setCurrentText("90%")
            surface._refresh_save_enabled()
            self.assertFalse(surface.save_button.isEnabled())
            owner.deleteLater()

    def test_save_round_trip_preserves_amount_ratio_and_other_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            first_path = self._write_instance(
                root,
                INSTANCE_IDS[0],
                enabled=True,
                amount=12_000_000,
                ratio="1.2",
            )
            second_path = self._write_instance(root, INSTANCE_IDS[1], enabled=True)
            second_before = second_path.read_bytes()
            owner, surface = self._surface(repository)
            surface.set_application_mode(surface.MODE_SEGMENTED)
            surface.strategy_rows["profit"][0][0].setCurrentText("투입금액")

            with patch("routine_instance_repository.append_production_event"):
                surface.accept()

            saved = json.loads(first_path.read_text(encoding="utf-8"))
            loaded = repository.get_instance(str(INSTANCE_IDS[0]))
            self.assertEqual("SEGMENTED", saved["buy_limit_response_policy"]["application_mode"])
            self.assertEqual(12_000_000, saved["buy_limit_amount"])
            self.assertEqual("1.2", saved["buy_limit_adjustment_ratio"])
            self.assertEqual(
                validate_routine_limit_response_policy(
                    saved["buy_limit_response_policy"]
                ),
                loaded.buy_limit_response_policy,
            )
            self.assertEqual(second_before, second_path.read_bytes())
            owner.deleteLater()

    def test_limit_updates_preserve_policy_for_amount_and_disable_paths(self) -> None:
        policy = default_routine_limit_response_policy()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(
                root,
                INSTANCE_IDS[0],
                enabled=True,
                amount=10_000_000,
                ratio="1.25",
                policy=policy,
            )
            with patch("routine_instance_repository.append_production_event"):
                amount_result = repository.update_buy_limit(
                    str(INSTANCE_IDS[0]),
                    enabled=True,
                    amount=11_000_000,
                    adjustment_ratio=Decimal("1.1"),
                )
                disabled_result = repository.update_buy_limit(
                    str(INSTANCE_IDS[0]),
                    enabled=False,
                    amount=None,
                )

            self.assertTrue(amount_result.success)
            self.assertEqual(policy, amount_result.instance.buy_limit_response_policy)
            self.assertTrue(disabled_result.success)
            self.assertEqual(policy, disabled_result.instance.buy_limit_response_policy)
            self.assertFalse(disabled_result.instance.buy_limit_enabled)
            self.assertIsNone(disabled_result.instance.buy_limit_amount)
            self.assertIsNone(disabled_result.instance.buy_limit_adjustment_ratio)

    def test_invalid_policies_are_rejected_without_overwrite(self) -> None:
        mutations = (
            ("policy", None),
            ("application_mode", "INVALID"),
            ("strategies.unified.evaluation_factor", "INVALID"),
            ("strategies.unified.direction", "INVALID"),
            ("strategies.unified.response_mode", "INVALID"),
            ("segment_close.early_close_percent", 40),
            ("segment_close.immediate_liquidation_percent", 110),
            ("segment_close.immediate_liquidation_percent", 90),
        )
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                policy = default_routine_limit_response_policy()
                if path == "policy":
                    policy = value
                else:
                    target = policy
                    parts = path.split(".")
                    for part in parts[:-1]:
                        target = target[part]
                    target[parts[-1]] = value
                with self.assertRaises(ValueError):
                    validate_routine_limit_response_policy(policy)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            valid = default_routine_limit_response_policy()
            metadata_path = self._write_instance(root, INSTANCE_IDS[0], policy=valid)
            before = metadata_path.read_bytes()
            invalid = deepcopy(valid)
            invalid["segment_close"]["immediate_liquidation_percent"] = 90
            result = repository.update_buy_limit_response_policy(
                str(INSTANCE_IDS[0]), invalid
            )
            self.assertFalse(result.success)
            self.assertEqual("BUY_LIMIT_RESPONSE_POLICY_INVALID", result.error_code)
            self.assertEqual(before, metadata_path.read_bytes())

    def test_settings_boundary_opens_for_unset_waiting_and_numeric_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(root, INSTANCE_IDS[0], enabled=False)
            self._write_instance(root, INSTANCE_IDS[1], enabled=True)
            self._write_instance(root, INSTANCE_IDS[2], enabled=True, amount=12_000_000)
            owner = QWidget()

            with patch.object(gui_windows, "RoutineInstanceRepository", return_value=repository):
                for instance_id in INSTANCE_IDS:
                    self.assertTrue(
                        gui_windows.MainWindow.open_routine_instance_buy_limit_settings(
                            owner,
                            str(instance_id),
                        )
                    )
            surfaces = owner._routine_limit_response_settings_surfaces
            self.assertEqual(3, len(surfaces))
            for surface in surfaces.values():
                self.assertEqual("루틴 한도대응 설정", surface.windowTitle())
                surface.close()
            owner.deleteLater()


if __name__ == "__main__":
    unittest.main()
