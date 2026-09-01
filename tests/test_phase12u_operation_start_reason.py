# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gui_auto_trade_run_control as run_control
import gui_main_stock_context_menu as monitoring_context_menu
from gui_auto_trade_operation_host import AutoTradeOperationHost
from tests.participant_owner_fixture import attach_participant_owner


class _StartReasonWindow:
    def __init__(
        self,
        targets,
        *,
        recalculate_result=("changed", "STOPPED", "RUNNING"),
    ):
        self.targets = list(targets)
        self.status_messages = []
        self._operation_start_batch_active = False
        self.stock_table = SimpleNamespace(
            viewport=lambda: SimpleNamespace(
                update=MagicMock(),
                repaint=MagicMock(),
            ),
            repaint=MagicMock(),
        )
        self.recalculate_stock_status_by_operation_policy = MagicMock(
            return_value=recalculate_result
        )
        attach_participant_owner(self)

    def selected_stock_infos(self):
        return list(self.targets)

    def start_target_is_review_isolated(self, _stock_dir, _code):
        return False

    def split_start_targets(self, selected):
        return list(selected), []

    def pre_start_review_check(self, *_args):
        return {}

    def mark_review_required(self, *_args, **_kwargs):
        return False

    def statusBarMessage(self, message):
        self.status_messages.append(str(message))

    def show_auto_trade_result_dialog(self, *_args):
        return None


def _write_target(root: Path, code: str, *, start: str = "09:00:00"):
    stock_dir = root / f"{code}_테스트"
    stock_dir.mkdir()
    (stock_dir / "config.json").write_text(
        json.dumps(
            {
                "assigned_routine_instance_id": "instance-1",
                "routine_instance_name": "routine-1",
                "trade_amount_type": "QUANTITY",
                "buy_qty": 1,
                "operation_mode": "SCHEDULED",
                "start_time": start,
                "end_buy_time": "13:30:00",
            }
        ),
        encoding="utf-8",
    )
    (stock_dir / "state.json").write_text(
        json.dumps({"status": "STOPPED", "holding_qty": 0}),
        encoding="utf-8",
    )
    return stock_dir, code, "테스트"


class Phase12UOperationStartReasonTest(unittest.TestCase):
    def test_structural_no_target_keeps_generic_message(self) -> None:
        self.assertEqual(
            "현재 운영을 시작할 수 있는 종목이 없습니다.\n"
            "검토관리와 자동매매 설정을 확인하십시오.",
            run_control._start_failure_user_message([]),
        )

    def test_all_structural_targets_outside_time_are_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _write_target(Path(temp_dir), "000001")
            window = _StartReasonWindow([target])
            with (
                patch.object(
                    run_control,
                    "read_operation_state",
                    return_value={},
                ),
                patch.object(
                    run_control,
                    "current_datetime",
                    return_value=run_control.datetime(2026, 8, 25, 20, 0),
                ),
                patch.object(run_control, "refresh_auto_trade_views"),
                patch.object(run_control, "append_changelog"),
                patch.object(run_control, "_show_start_failure_once"),
                patch.object(run_control, "write_global_operation_running_state") as writer,
            ):
                result = run_control.auto_trade_start_selected_auto_trades(
                    window,
                    selected_targets=[target],
                    request_scope=run_control.START_REQUEST_MULTIPLE,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(1, result["started_count"])
            self.assertEqual((target,), result["time_eligible_targets"])
            self.assertEqual((), result["time_blocked_targets"])
            self.assertEqual(
                ("000001",),
                run_control.auto_trade_current_session_operation_participant_codes(
                    window
                ),
            )
            writer.assert_called_once_with(participant_stock_codes=["000001"])

    def test_time_eligible_target_keeps_downstream_validation_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = _write_target(Path(temp_dir), "000001", start="18:00:00")
            inside = _write_target(Path(temp_dir), "000002")
            outside_config = json.loads(
                (outside[0] / "config.json").read_text(encoding="utf-8")
            )
            outside_config["end_buy_time"] = "19:00:00"
            (outside[0] / "config.json").write_text(
                json.dumps(outside_config),
                encoding="utf-8",
            )
            window = _StartReasonWindow(
                [outside, inside],
                recalculate_result=("failed", None, None),
            )
            with (
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(
                    run_control,
                    "current_datetime",
                    return_value=run_control.datetime(2026, 8, 25, 10, 0),
                ),
                patch.object(run_control, "refresh_auto_trade_views"),
                patch.object(run_control, "append_changelog"),
                patch.object(run_control, "_show_start_failure_once"),
                patch.object(run_control, "write_global_operation_running_state") as writer,
            ):
                result = run_control.auto_trade_start_selected_auto_trades(
                    window,
                    selected_targets=[outside, inside],
                    request_scope=run_control.START_REQUEST_MULTIPLE,
                )

            self.assertFalse(result["ok"])
            self.assertIn("운영 상태를 저장하지 못했습니다", str(result["user_message"]))
            self.assertNotIn("매매 운영 시간이 아닙니다", str(result["user_message"]))
            self.assertEqual((outside, inside), result["time_eligible_targets"])
            self.assertEqual((), result["time_blocked_targets"])
            writer.assert_not_called()

    def test_schedule_helper_matches_guard_time_contract(self) -> None:
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        before = run_control.datetime(2026, 8, 25, 8, 59)
        during = run_control.datetime(2026, 8, 25, 10, 0)
        after = run_control.datetime(2026, 8, 25, 13, 30)
        self.assertFalse(run_control.auto_trade_operation_time_allowed(config, now_dt=before))
        self.assertTrue(run_control.auto_trade_operation_time_allowed(config, now_dt=during))
        self.assertFalse(run_control.auto_trade_operation_time_allowed(config, now_dt=after))

    def test_main_global_adapter_path_allows_outside_time_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _write_target(Path(temp_dir), "012210")

            class _Host(AutoTradeOperationHost):
                def __init__(self):
                    super().__init__(None)

                def split_start_targets(self, selected):
                    return list(selected), []

                def filter_start_targets_by_recovery(self, targets, *, action):
                    return {
                        "allowed": True,
                        "reason": "RECOVERY_COMPLETED",
                        "eligible": tuple(targets),
                        "excluded_review": (),
                    }

                def pre_start_review_check(self, *_args, **_kwargs):
                    return {}

                def mark_review_required(self, *_args, **_kwargs):
                    return False

                def recalculate_stock_status_by_operation_policy(
                    self, *_args, **_kwargs
                ):
                    return "changed", "STOPPED", "RUNNING"

                def rebind_startup_recovery_after_trusted_runtime_update(self):
                    return None

            class _Owner:
                def __init__(self):
                    self.routine_table = SimpleNamespace(
                        viewport=lambda: SimpleNamespace(update=MagicMock()),
                        repaint=MagicMock(),
                    )
                    self.btn_start = SimpleNamespace(
                        setText=MagicMock(),
                        setStyleSheet=MagicMock(),
                        setEnabled=MagicMock(),
                    )
                    self.refresh_all = MagicMock()
                    self._host = _Host()
                    self._main_monitoring_auto_trade_operation_host = self._host

                def main_monitoring_auto_trade_operation_host(self):
                    return self._host

                def global_operation_start_prerequisite(self, _action):
                    return {"allowed": True, "reason": "GLOBAL_PREREQUISITE_READY"}

                def statusBar(self):
                    return SimpleNamespace(showMessage=MagicMock())

            owner = _Owner()
            adapter = monitoring_context_menu.MainMonitoringStockOperationAdapter(
                owner,
                [SimpleNamespace(stock_dir=target[0], code=target[1], name=target[2], routine_instance_id="instance-1")],
            )
            result_holder = {}
            start_backend = run_control.auto_trade_start_selected_auto_trades

            def invoke_backend(window, **kwargs):
                result = start_backend(window, **kwargs)
                result_holder["result"] = result
                return result

            with (
                patch.object(monitoring_context_menu, "auto_trade_registered_operation_targets", return_value=[target]),
                patch.object(run_control, "auto_trade_registered_operation_targets", return_value=[target]),
                patch.object(run_control, "auto_trade_stock_operation_excluded", return_value=False),
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(run_control, "current_datetime", return_value=run_control.datetime(2026, 8, 25, 20, 38)),
                patch.object(run_control, "auto_trade_start_selected_auto_trades", side_effect=invoke_backend),
                patch.object(run_control, "refresh_auto_trade_views"),
                patch.object(run_control, "append_changelog"),
                patch.object(run_control, "_show_start_failure_once"),
                patch.object(run_control, "_show_operation_start_summary_toast"),
                patch.object(run_control, "write_global_operation_running_state") as writer,
            ):
                run_control.execute_operation_start_command(
                    adapter,
                    run_control.OperationStartCommandRequest(
                        intent=run_control.OperationStartIntent.FULL_START,
                        source="auto_trade_global_start_button",
                    ),
                    operation_state_reader=lambda: {},
                )

            self.assertTrue(result_holder["result"]["ok"])
            self.assertEqual(1, result_holder["result"]["started_count"])
            self.assertEqual((), result_holder["result"]["time_blocked_targets"])
            writer.assert_called_once_with(participant_stock_codes=["012210"])


if __name__ == "__main__":
    unittest.main()
