# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from gui_routine_service import apply_default_operation_exclusion_for_new_running_assignment


class RoutineAssignmentOperationExclusionTest(unittest.TestCase):
    def _fixture(
        self,
        root: str,
        *,
        running: bool,
        config: dict[str, object] | None = None,
    ) -> tuple[SimpleNamespace, Path]:
        stock_dir = Path(root) / "000001_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps(config or {}, ensure_ascii=False),
            encoding="utf-8",
        )
        host = SimpleNamespace(
            running_registered_operation_targets=Mock(
                return_value=[(stock_dir, "000001", "테스트")] if running else []
            )
        )
        return SimpleNamespace(parent=lambda: host), stock_dir

    def test_new_assignment_during_operation_is_excluded_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window, stock_dir = self._fixture(root, running=True)
            with patch("gui_routine_service.now_text", return_value="2026-08-06 13:00:00"):
                changed = apply_default_operation_exclusion_for_new_running_assignment(
                    window,
                    stock_dir,
                    {},
                )
            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertTrue(changed)
        self.assertIs(saved["operation_excluded"], True)
        self.assertEqual("2026-08-06 13:00:00", saved["updated_at"])

    def test_new_assignment_while_stopped_does_not_change_config(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window, stock_dir = self._fixture(root, running=False, config={"marker": "kept"})
            before = (stock_dir / "config.json").read_bytes()

            changed = apply_default_operation_exclusion_for_new_running_assignment(
                window,
                stock_dir,
                {},
            )

            after = (stock_dir / "config.json").read_bytes()

        self.assertFalse(changed)
        self.assertEqual(before, after)

    def test_existing_assignment_does_not_change_config(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window, stock_dir = self._fixture(root, running=True, config={"marker": "kept"})
            before = (stock_dir / "config.json").read_bytes()

            changed = apply_default_operation_exclusion_for_new_running_assignment(
                window,
                stock_dir,
                {"assigned_routine_instance_id": "existing-instance"},
            )

            after = (stock_dir / "config.json").read_bytes()

        self.assertFalse(changed)
        self.assertEqual(before, after)

    def test_existing_operation_excluded_value_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            window, stock_dir = self._fixture(
                root,
                running=True,
                config={"operation_excluded": False},
            )

            changed = apply_default_operation_exclusion_for_new_running_assignment(
                window,
                stock_dir,
                {},
            )
            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertFalse(changed)
        self.assertIs(saved["operation_excluded"], False)

    def test_read_back_mismatch_returns_false(self) -> None:
        window = SimpleNamespace(
            parent=lambda: SimpleNamespace(
                running_registered_operation_targets=lambda: [(Path("stock"), "000001", "테스트")]
            )
        )
        with (
            patch("gui_routine_service.read_json_dict", side_effect=[{}, {}]),
            patch("gui_routine_service.write_stock_config") as writer,
        ):
            changed = apply_default_operation_exclusion_for_new_running_assignment(
                window,
                Path("stock"),
                {},
            )

        self.assertFalse(changed)
        writer.assert_called_once()

    def test_write_failure_returns_false(self) -> None:
        window = SimpleNamespace(
            parent=lambda: SimpleNamespace(
                running_registered_operation_targets=lambda: [(Path("stock"), "000001", "테스트")]
            )
        )
        with (
            patch("gui_routine_service.read_json_dict", return_value={}),
            patch("gui_routine_service.write_stock_config", side_effect=OSError("write failed")),
        ):
            changed = apply_default_operation_exclusion_for_new_running_assignment(
                window,
                Path("stock"),
                {},
            )

        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
