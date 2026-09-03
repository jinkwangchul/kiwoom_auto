from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gui_auto_trade_run_control import (
    OperationStartCommandRequest,
    OperationStartIntent,
    auto_trade_start_selected_auto_trades,
    execute_operation_start_command,
)
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_main_stock_context_menu import MainMonitoringStockOperationAdapter
from gui_windows import MainWindow


def _target(code: str) -> tuple[Path, str, str]:
    return Path("stocks") / f"{code}_fixture", code, f"name-{code}"


class _StartHost:
    def __init__(self, targets=(), mock_codes=(), *, membership_error=False):
        self.targets = list(targets)
        self.mock_codes = set(mock_codes)
        self.membership_error = membership_error
        self.updated = 0
        self.messages: list[str] = []

    def registered_operation_start_targets(self):
        return list(self.targets)

    def running_registered_operation_targets(self):
        return []

    def selected_stock_infos(self):
        return list(self.targets)

    def operation_start_exclusion_reason(self, target):
        if self.membership_error:
            raise RuntimeError("membership unavailable")
        return "MOCK_VALIDATION_ACTIVE" if target[1] in self.mock_codes else None

    def update_global_operation_button_state(self):
        self.updated += 1

    def statusBarMessage(self, message):
        self.messages.append(message)

    def parent(self):
        return None


class MockProductionOperationCoexistenceTests(unittest.TestCase):
    def _full_start(self, host):
        captured = Mock(
            return_value={
                "ok": True,
                "completed": tuple(f"{code} {name}" for _path, code, name in host.targets),
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
            start_backend=captured,
            operation_state_reader=lambda: {},
            summary_presenter=lambda *_args: None,
        )
        return result, captured

    def test_main_global_mixed_targets_pass_only_normal_stocks_to_backend(self):
        targets = [_target("005930"), _target("000660"), _target("035420")]
        host = _StartHost(targets, {"005930"})

        result, backend = self._full_start(host)

        selected = backend.call_args.kwargs["selected_targets"]
        self.assertEqual(["000660", "035420"], [item[1] for item in selected])
        self.assertEqual(1, result.blocked_count)
        self.assertEqual(
            "MOCK_VALIDATION_ACTIVE",
            result.payload["blocked_target_details"][0]["reason"],
        )

    def test_main_global_all_mock_never_calls_backend(self):
        host = _StartHost([_target("005930"), _target("000660")], {"005930", "000660"})

        result, backend = self._full_start(host)

        backend.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(2, result.blocked_count)

    def test_selective_mixed_targets_pass_only_normal_stock(self):
        targets = [_target("005930"), _target("000660")]
        host = _StartHost(targets, {"005930"})
        backend = Mock(return_value={"ok": True, "completed": ("000660",), "started_count": 1})

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(
                intent=OperationStartIntent.SELECTIVE_START,
                selected_targets=tuple(targets),
            ),
            selective_backend=backend,
        )

        self.assertEqual(
            ["000660"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )
        self.assertEqual(1, result.blocked_count)

    def test_selective_all_mock_never_calls_backend(self):
        target = _target("005930")
        host = _StartHost([target], {"005930"})
        backend = Mock()

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(
                intent=OperationStartIntent.SELECTIVE_START,
                selected_targets=(target,),
            ),
            selective_backend=backend,
        )

        backend.assert_not_called()
        self.assertFalse(result.ok)

    def test_selective_without_explicit_targets_filters_selected_rows(self):
        targets = [_target("005930"), _target("000660")]
        host = _StartHost(targets, {"005930"})
        backend = Mock(return_value={"ok": True, "completed": ("000660",), "started_count": 1})

        execute_operation_start_command(
            host,
            OperationStartCommandRequest(intent=OperationStartIntent.SELECTIVE_START),
            selective_backend=backend,
        )

        self.assertEqual(
            ["000660"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )

    def test_membership_exception_is_fail_closed(self):
        host = _StartHost([_target("005930")], membership_error=True)

        result, backend = self._full_start(host)

        backend.assert_not_called()
        self.assertEqual(
            "OPERATION_START_EXCLUSION_CHECK_FAILED",
            result.reason_code,
        )

    def test_legacy_host_without_optional_provider_is_unchanged(self):
        target = _target("000660")
        host = SimpleNamespace(
            registered_operation_start_targets=lambda: [target],
            running_registered_operation_targets=lambda: [],
            update_global_operation_button_state=lambda: None,
            parent=lambda: None,
        )
        backend = Mock(return_value={"ok": True, "completed": ("000660",), "started_count": 1})

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(intent=OperationStartIntent.FULL_START),
            start_backend=backend,
            operation_state_reader=lambda: {},
            summary_presenter=lambda *_args: None,
        )

        self.assertTrue(result.ok)
        self.assertEqual([target], backend.call_args.kwargs["selected_targets"])

    def test_direct_common_backend_blocks_before_production_reads_or_writes(self):
        target = _target("005930")
        host = _StartHost([target], {"005930"})
        with (
            patch("gui_auto_trade_run_control.read_operation_state") as operation_read,
            patch("gui_auto_trade_run_control.append_production_event") as event_write,
        ):
            result = auto_trade_start_selected_auto_trades(
                host,
                selected_targets=[target],
                source="direct_boundary_test",
            )

        self.assertFalse(result["ok"])
        operation_read.assert_not_called()
        event_write.assert_not_called()

    def test_main_adapter_delegates_to_window_membership_provider(self):
        target = _target("005930")
        window = SimpleNamespace(
            routine_table=object(),
            operation_start_exclusion_reason=Mock(return_value="MOCK_VALIDATION_ACTIVE"),
        )
        adapter = MainMonitoringStockOperationAdapter(window, [])

        self.assertEqual(
            "MOCK_VALIDATION_ACTIVE",
            adapter.operation_start_exclusion_reason(target),
        )
        window.operation_start_exclusion_reason.assert_called_once_with(target)

    def test_main_provider_excludes_current_session_for_all_session_states(self):
        target = _target("005930")
        for state in ("WAITING", "RUNNING", "CLOSING", "REVIEW_STOPPED"):
            with self.subTest(state=state):
                owner = SimpleNamespace(
                    mock_validation_host=SimpleNamespace(
                        current_stock_codes=lambda: frozenset({"005930"})
                    )
                )
                self.assertEqual(
                    "MOCK_VALIDATION_ACTIVE",
                    MainWindow.operation_start_exclusion_reason(owner, target),
                )

    def test_setting_window_uses_persistent_owner_membership(self):
        target = _target("005930")
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=lambda: frozenset({"005930"})
            )
        )
        with patch("gui_auto_trade_setting_window.persistent_feature_owner", return_value=owner):
            reason = AutoTradeSettingWindow.operation_start_exclusion_reason(
                SimpleNamespace(),
                target,
            )
        self.assertEqual("MOCK_VALIDATION_ACTIVE", reason)

    def test_setting_window_global_start_passes_only_normal_target(self):
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
        backend = Mock(return_value={"ok": True, "completed": ("000660",), "started_count": 1})
        with (
            patch("gui_auto_trade_setting_window.persistent_feature_owner", return_value=owner),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades", backend),
            patch("gui_auto_trade_setting_window.read_operation_state", return_value={}),
            patch("gui_auto_trade_setting_window._show_operation_start_summary_toast"),
        ):
            AutoTradeSettingWindow.start_selected_auto_trades(setting)
        self.assertEqual(
            ["000660"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )

    def test_setting_window_selected_rows_exclude_mock_target(self):
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
        backend = Mock(return_value={"ok": True, "completed": ("000660",), "started_count": 1})
        with (
            patch("gui_auto_trade_setting_window.persistent_feature_owner", return_value=owner),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_rows_auto_trades", backend),
        ):
            AutoTradeSettingWindow.start_selected_rows_auto_trades(setting)
        self.assertEqual(
            ["000660"],
            [item[1] for item in backend.call_args.kwargs["selected_targets"]],
        )

    def test_archived_session_restores_underlying_production_eligibility(self):
        target = _target("005930")
        codes = {"005930"}
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=lambda: frozenset(codes)
            )
        )
        self.assertEqual(
            "MOCK_VALIDATION_ACTIVE",
            MainWindow.operation_start_exclusion_reason(owner, target),
        )
        codes.clear()
        self.assertIsNone(MainWindow.operation_start_exclusion_reason(owner, target))

    def test_multiple_instances_share_one_stock_level_exclusion(self):
        target = _target("005930")
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=lambda: frozenset({"005930"})
            )
        )
        reasons = [
            MainWindow.operation_start_exclusion_reason(owner, target)
            for _instance_id in ("A", "B", "C")
        ]
        self.assertEqual(["MOCK_VALIDATION_ACTIVE"] * 3, reasons)


if __name__ == "__main__":
    unittest.main()
