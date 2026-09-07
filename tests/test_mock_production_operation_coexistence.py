from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gui_auto_trade_run_control import (
    OperationStartCommandRequest,
    OperationStartIntent,
    execute_operation_start_command,
)
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_main_stock_context_menu import MainMonitoringStockOperationAdapter
from gui_windows import MainWindow
from mock_validation_ui_projection import mock_operation_start_exclusion_reason


def _target(code: str) -> tuple[Path, str, str]:
    return Path("stocks") / f"{code}_fixture", code, f"name-{code}"


class _StartHost:
    def __init__(self, targets=()):
        self.targets = list(targets)
        self.updated = 0
        self.messages: list[str] = []

    def registered_operation_start_targets(self):
        return list(self.targets)

    def running_registered_operation_targets(self):
        return []

    def selected_stock_infos(self):
        return list(self.targets)

    def operation_start_exclusion_reason(self, target):
        return None

    def update_global_operation_button_state(self):
        self.updated += 1

    def statusBarMessage(self, message):
        self.messages.append(message)

    def parent(self):
        return None


class MockProductionOperationCoexistenceTests(unittest.TestCase):
    def _full_start(self, host):
        backend = Mock(
            return_value={
                "ok": True,
                "completed": tuple(
                    f"{code} {name}" for _path, code, name in host.targets
                ),
                "started_count": len(host.targets),
                "blocked_count": 0,
            }
        )
        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(
                intent=OperationStartIntent.FULL_START,
                source="coexistence_test",
            ),
            start_backend=backend,
            operation_state_reader=lambda: {},
            summary_presenter=lambda *_args: None,
        )
        return result, backend

    def test_global_start_keeps_mock_registered_stock_in_production_targets(self):
        targets = [_target("005930"), _target("000660"), _target("035420")]
        host = _StartHost(targets)

        result, backend = self._full_start(host)

        self.assertTrue(result.ok)
        self.assertEqual(0, result.blocked_count)
        self.assertEqual(
            ["005930", "000660", "035420"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )

    def test_selective_start_keeps_mock_registered_stock_in_production_targets(self):
        targets = [_target("005930"), _target("000660")]
        host = _StartHost(targets)
        backend = Mock(
            return_value={
                "ok": True,
                "completed": ("005930", "000660"),
                "started_count": 2,
            }
        )

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(
                intent=OperationStartIntent.SELECTIVE_START,
                selected_targets=tuple(targets),
            ),
            selective_backend=backend,
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            ["005930", "000660"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )

    def test_main_provider_never_reads_mock_membership_for_valid_target(self):
        target = _target("005930")
        current_codes = Mock(side_effect=AssertionError("membership must be irrelevant"))
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(current_stock_codes=current_codes)
        )

        self.assertIsNone(MainWindow.operation_start_exclusion_reason(owner, target))
        current_codes.assert_not_called()

    def test_setting_provider_never_reads_persistent_mock_membership(self):
        target = _target("005930")
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=Mock(
                    side_effect=AssertionError("membership must be irrelevant")
                )
            )
        )
        with patch(
            "gui_auto_trade_setting_window.persistent_feature_owner",
            return_value=owner,
        ):
            reason = AutoTradeSettingWindow.operation_start_exclusion_reason(
                SimpleNamespace(),
                target,
            )
        self.assertIsNone(reason)

    def test_setting_global_start_passes_all_targets_with_mock_overlap(self):
        targets = [_target("005930"), _target("000660")]
        setting = _StartHost(targets)
        setting.operation_start_exclusion_reason = MethodType(
            AutoTradeSettingWindow.operation_start_exclusion_reason,
            setting,
        )
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=lambda: frozenset({"005930"})
            )
        )
        backend = Mock(
            return_value={
                "ok": True,
                "completed": ("005930", "000660"),
                "started_count": 2,
            }
        )
        with (
            patch(
                "gui_auto_trade_setting_window.persistent_feature_owner",
                return_value=owner,
            ),
            patch(
                "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades",
                backend,
            ),
            patch("gui_auto_trade_setting_window.read_operation_state", return_value={}),
            patch("gui_auto_trade_setting_window._show_operation_start_summary_toast"),
        ):
            AutoTradeSettingWindow.start_selected_auto_trades(setting)
        self.assertEqual(
            ["005930", "000660"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )

    def test_main_adapter_preserves_generic_non_mock_exclusion_provider(self):
        target = _target("005930")
        window = SimpleNamespace(
            routine_table=object(),
            operation_start_exclusion_reason=Mock(return_value="OTHER_SAFETY_REASON"),
        )
        adapter = MainMonitoringStockOperationAdapter(window, [])

        self.assertEqual(
            "OTHER_SAFETY_REASON",
            adapter.operation_start_exclusion_reason(target),
        )

    def test_mock_overlay_never_becomes_a_production_exclusion_reason(self):
        self.assertIsNone(
            mock_operation_start_exclusion_reason(
                object(), (Path("stocks"), "", "name")
            )
        )


if __name__ == "__main__":
    unittest.main()
