# -*- coding: utf-8 -*-

from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import ats_session_contract as ats_contract
import gui_auto_trade_close as production_close
import gui_auto_trade_context_menu as production_menu
import gui_operation_ui_primitives as operation_ui
import manual_ats_runtime
import mock_validation_context_menu as mock_menu
from mock_validation_isolation_guard import (
    FORBIDDEN_IMPORT_ROOTS,
    audit_mock_dependency_graph,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add(str(node.module or "").split(".", 1)[0])
    return roots


class SharedPureUiBoundaryTest(unittest.TestCase):
    def test_manual_ats_runtime_reexports_one_neutral_contract(self) -> None:
        self.assertEqual(ats_contract.PROGRAM_SESSION_ID, manual_ats_runtime.PROGRAM_SESSION_ID)
        self.assertEqual(ats_contract.VALID_SESSION_KEYS, manual_ats_runtime.VALID_SESSION_KEYS)
        self.assertIs(
            ats_contract.normalized_manual_ats_session_keys,
            manual_ats_runtime.normalized_manual_ats_session_keys,
        )
        self.assertIs(
            ats_contract.normalize_manual_ats_execution_method,
            manual_ats_runtime.normalize_manual_ats_execution_method,
        )
        self.assertEqual(
            ("extra1", "extra3"),
            ats_contract.normalized_manual_ats_session_keys(
                {"extra1": True, "extra2": False, "extra3": 1}
            ),
        )
        self.assertEqual(
            "CURRENT_PRICE",
            ats_contract.normalize_manual_ats_execution_method("current price"),
        )
        self.assertIsNone(
            ats_contract.normalize_manual_ats_execution_method("unsupported")
        )

    def test_production_and_mock_reference_the_same_neutral_ui_primitives(self) -> None:
        self.assertIs(
            operation_ui.ProfitLossEarlyCloseDialog,
            production_close.ProfitLossEarlyCloseDialog,
        )
        self.assertIs(
            operation_ui.ProfitLossEarlyCloseDialog,
            mock_menu.ProfitLossEarlyCloseDialog,
        )
        for name in (
            "PersistentContextMenu",
            "_add_early_close_menu",
            "_add_individual_liquidation_menu",
            "_dispatch_early_close_action",
            "_dispatch_ats_settings_action",
            "_individual_liquidation_action_applied",
            "_refresh_individual_liquidation_menu_state",
        ):
            shared = getattr(operation_ui, name)
            self.assertIs(shared, getattr(production_menu, name), name)
            self.assertIs(shared, getattr(mock_menu, name), name)

    def test_production_ats_adapter_injects_display_inputs_into_shared_builder(self) -> None:
        built = object()
        menu = MagicMock()
        with (
            patch.object(production_menu, "manual_ats_visible_session_keys", return_value=("extra2",)),
            patch.object(production_menu, "manual_ats_session_labels", return_value={"extra2": "야간"}),
            patch.object(production_menu, "_build_ats_settings_menu", return_value=built) as builder,
        ):
            result = production_menu._add_ats_settings_menu(
                menu,
                has_selection=True,
                state_getter=None,
                toggle=None,
            )
        self.assertIs(built, result)
        builder.assert_called_once_with(
            menu,
            has_selection=True,
            visible_keys=("extra2",),
            labels={"extra2": "야간"},
            state_getter=None,
            toggle=None,
            liquidation_available_getter=None,
        )

    def test_mock_sources_do_not_import_production_heavy_boundary_modules(self) -> None:
        forbidden = {
            "manual_ats_runtime",
            "gui_auto_trade_close",
            "gui_auto_trade_context_menu",
        }
        self.assertTrue(forbidden.issubset(FORBIDDEN_IMPORT_ROOTS))
        violations = {
            path.name: sorted(_import_roots(path) & forbidden)
            for path in PROJECT_ROOT.glob("mock_validation_*.py")
            if _import_roots(path) & forbidden
        }
        self.assertEqual({}, violations)

    def test_isolation_guard_rejects_each_new_production_heavy_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mock_validation_bad_shared_boundary.py"
            path.write_text(
                "import manual_ats_runtime\n"
                "from gui_auto_trade_close import ProfitLossEarlyCloseDialog\n"
                "from gui_auto_trade_context_menu import PersistentContextMenu\n",
                encoding="utf-8",
            )
            result = audit_mock_dependency_graph([path])
        self.assertIs(result["ok"], False)
        self.assertEqual(
            {
                "manual_ats_runtime",
                "gui_auto_trade_close",
                "gui_auto_trade_context_menu",
            },
            {item["name"] for item in result["violations"]},
        )

    def test_neutral_modules_do_not_import_either_execution_domain(self) -> None:
        forbidden = {
            "runtime_atomic_writer",
            "execution_queue_writer",
            "order_queue",
            "close_liquidation_command",
            "close_liquidation_execution_pipeline",
            "event_journal_production",
            "mock_validation_repository",
            "mock_validation_host",
            "mock_validation_virtual_execution",
        }
        violations = {
            path.name: sorted(_import_roots(path) & forbidden)
            for path in (
                PROJECT_ROOT / "ats_session_contract.py",
                PROJECT_ROOT / "gui_operation_ui_primitives.py",
            )
            if _import_roots(path) & forbidden
        }
        self.assertEqual({}, violations)

    def test_mock_ats_ui_options_are_projected_from_injected_policy(self) -> None:
        visible, labels = operation_ui.ats_session_ui_options(
            {
                "extra_sessions": [
                    {"enabled": True, "name": "장전"},
                    {"enabled": False, "name": "비활성"},
                    {"name": "장후"},
                ]
            }
        )
        self.assertEqual(("extra1", "extra3"), visible)
        self.assertEqual("장전", labels["extra1"])
        self.assertEqual("비활성", labels["extra2"])
        self.assertEqual("장후", labels["extra3"])


if __name__ == "__main__":
    unittest.main()
