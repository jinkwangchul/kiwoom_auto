# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import gui_auto_trade_run_control as run_control
import gui_ats_utils as ats_utils
import gui_main_stock_context_menu as monitoring_context_menu
import routine_order_permission as order_permission
from tests.participant_owner_fixture import attach_participant_owner


def _target(
    root: Path,
    code: str,
    *,
    start: str = "09:00:00",
    end: str = "13:30:00",
    trade_amount_type: str = "QUANTITY",
    buy_qty: int = 1,
    buy_amount: int = 0,
    previous_close: int | None = None,
) -> tuple[Path, str, str]:
    stock_dir = root / f"{code}_테스트"
    stock_dir.mkdir()
    (stock_dir / "config.json").write_text(
        json.dumps(
            {
                "assigned_routine_instance_id": "instance-1",
                "routine_instance_name": "routine-1",
                "trade_amount_type": trade_amount_type,
                "buy_qty": buy_qty,
                "buy_amount": buy_amount,
                "operation_mode": "SCHEDULED",
                "start_time": start,
                "end_buy_time": end,
                "operation_excluded": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = {"status": "STOPPED", "holding_qty": 0, "trade_enabled": False}
    if previous_close is not None:
        state["previous_close"] = previous_close
    (stock_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )
    return stock_dir, code, "테스트"


class _PrecedenceWindow:
    def __init__(
        self,
        targets,
        *,
        global_result: dict[str, object] | None = None,
        recovery_result: dict[str, object] | None = None,
        recalculate_result=("changed", "STOPPED", "RUNNING"),
    ) -> None:
        self.targets = list(targets)
        self.global_result = global_result or {
            "allowed": True,
            "reason": "GLOBAL_PREREQUISITE_READY",
        }
        self.recovery_result = recovery_result
        self.status_messages: list[str] = []
        self.split_start_targets = Mock(side_effect=lambda selected: (list(selected), []))
        self.filter_start_targets_by_recovery = Mock(side_effect=self._recovery_filter)
        self.pre_start_review_check = Mock(return_value={})
        self.mark_review_required = Mock(return_value=False)
        self.recalculate_stock_status_by_operation_policy = Mock(
            return_value=recalculate_result
        )
        self.recalculate_routine_limits_for_new_operation_session = Mock(
            return_value={"ok": True}
        )
        self._operation_start_batch_active = False
        self.stock_table = SimpleNamespace(
            viewport=lambda: SimpleNamespace(update=MagicMock()),
            repaint=MagicMock(),
        )
        attach_participant_owner(self)

    def selected_stock_infos(self):
        return list(self.targets)

    def global_operation_start_prerequisite(self, _action):
        return dict(self.global_result)

    def start_target_is_review_isolated(self, _stock_dir, _code):
        return False

    def _recovery_filter(self, targets, *, action):
        if self.recovery_result is not None:
            return dict(self.recovery_result)
        return {
            "allowed": True,
            "reason": "RECOVERY_COMPLETED",
            "eligible": tuple(targets),
            "excluded_review": (),
        }

    def statusBarMessage(self, message, *_args):
        self.status_messages.append(str(message))

    def show_auto_trade_result_dialog(self, *_args):
        return None


class Phase12VStartGuardPrecedenceTest(unittest.TestCase):
    def _run(self, window, *, now=None, operation_state=None):
        with (
            patch.object(run_control, "read_operation_state", return_value=operation_state or {}),
            patch.object(
                run_control,
                "current_datetime",
                return_value=now or run_control.datetime(2026, 8, 25, 10, 0),
            ),
            patch.object(run_control, "refresh_auto_trade_views"),
            patch.object(run_control, "append_changelog"),
            patch.object(run_control, "append_production_event"),
            patch.object(run_control, "_show_start_failure_once"),
            patch.object(
                run_control,
                "write_global_operation_running_state",
                return_value={"ok": True},
            ) as writer,
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                window,
                selected_targets=list(window.targets),
                request_scope=run_control.START_REQUEST_MULTIPLE,
                source="phase12v_test",
            )
        return result, writer

    def test_unauthenticated_outside_time_target_exists_uses_global_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow(
                [target],
                global_result={
                    "allowed": False,
                    "reason": "RECOVERY_CONTEXT_MISSING",
                    "user_message": "키움 서버에 로그인되어 있지 않습니다.",
                },
            )

            result, writer = self._run(
                window,
                now=run_control.datetime(2026, 8, 25, 21, 0),
            )

        self.assertFalse(result["ok"])
        self.assertEqual("RECOVERY_CONTEXT_MISSING", result["reason"])
        self.assertEqual("키움 서버에 로그인되어 있지 않습니다.", result["user_message"])
        self.assertNotIn("매매 운영 시간이 아닙니다", result["user_message"])
        self.assertNotIn("검토관리와 자동매매 설정", result["user_message"])
        window.split_start_targets.assert_not_called()
        window.filter_start_targets_by_recovery.assert_not_called()
        window.recalculate_stock_status_by_operation_policy.assert_not_called()
        writer.assert_not_called()

    def test_authenticated_outside_time_target_starts_and_registers_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow([target])

            result, writer = self._run(
                window,
                now=run_control.datetime(2026, 8, 25, 21, 0),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual((target,), result["time_eligible_targets"])
        self.assertEqual((), result["time_blocked_targets"])
        window.filter_start_targets_by_recovery.assert_called_once_with(
            [target], action="운영시작"
        )
        self.assertEqual(
            ("012210",),
            run_control.auto_trade_current_session_operation_participant_codes(window),
        )
        writer.assert_called_once_with(participant_stock_codes=["012210"])
        time_status = order_permission.canonical_stock_trading_time_status(
            config={
                "operation_mode": "SCHEDULED",
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            state={},
            now_dt=run_control.datetime(2026, 8, 25, 21, 0),
        )
        self.assertIs(time_status["active"], False)
        self.assertEqual("OUTSIDE_OPERATION_TIME", time_status["reason"])

    def test_pre_market_ats_active_start_allows_ats_order_time(self) -> None:
        operation_policy = {
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
            "manual_operation": {"use_regular_market": True},
            "extra_sessions": [
                {
                    "enabled": True,
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210", start="09:30:00")
            config_path = target[0] / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["operation_mode"] = "CONTINUOUS"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            state_path = target[0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["manual_ats_selection"] = {
                "selected_sessions": ["extra1"],
                "execution_method": "MARKET",
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = _PrecedenceWindow([target])

            result, writer = self._run(
                window,
                now=run_control.datetime(2026, 8, 25, 8, 10),
            )
            with patch.object(
                ats_utils,
                "read_operation_policy",
                return_value=operation_policy,
            ):
                time_status = order_permission.canonical_stock_trading_time_status(
                    config=config,
                    state=state,
                    now_dt=run_control.datetime(2026, 8, 25, 8, 10),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual(
            ("012210",),
            run_control.auto_trade_current_session_operation_participant_codes(window),
        )
        writer.assert_called_once_with(participant_stock_codes=["012210"])
        self.assertIs(time_status["active"], True)
        self.assertEqual("ACTIVE_ATS", time_status["reason"])

    def test_future_ats_gap_start_succeeds_but_order_time_is_blocked(self) -> None:
        operation_policy = {
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
            "manual_operation": {"use_regular_market": True},
            "extra_sessions": [
                {
                    "enabled": True,
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210", start="09:30:00")
            config_path = target[0] / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["operation_mode"] = "CONTINUOUS"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            state_path = target[0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["manual_ats_selection"] = {
                "selected_sessions": ["extra1"],
                "execution_method": "MARKET",
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = _PrecedenceWindow([target])

            result, _writer = self._run(
                window,
                now=run_control.datetime(2026, 8, 25, 7, 59, 59),
            )
            with patch.object(
                ats_utils,
                "read_operation_policy",
                return_value=operation_policy,
            ):
                time_status = order_permission.canonical_stock_trading_time_status(
                    config=config,
                    state=state,
                    now_dt=run_control.datetime(2026, 8, 25, 7, 59, 59),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        self.assertIs(time_status["active"], False)
        self.assertEqual("OUTSIDE_OPERATION_TIME", time_status["reason"])

    def test_authenticated_no_structural_target_keeps_generic_message(self) -> None:
        window = _PrecedenceWindow([])

        result, writer = self._run(window)

        self.assertFalse(result["ok"])
        self.assertEqual("NO_TARGETS", result["reason"])
        self.assertEqual("운영을 시작할 종목을 1개 이상 선택하십시오.", result["user_message"])
        writer.assert_not_called()

    def test_authenticated_structural_start_target_zero_keeps_generic_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            other = _target(Path(temp_dir), "012211")
            window = _PrecedenceWindow([target, other])
            window.split_start_targets = Mock(return_value=([], []))

            result, writer = self._run(window)

        self.assertFalse(result["ok"])
        self.assertEqual("NO_STARTABLE_TARGETS", result["reason"])
        self.assertEqual(
            "현재 운영을 시작할 수 있는 종목이 없습니다.\n"
            "검토관리와 자동매매 설정을 확인하십시오.",
            result["user_message"],
        )
        writer.assert_not_called()

    def test_authenticated_in_time_recovery_not_ready_uses_recovery_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow(
                [target],
                recovery_result={
                    "allowed": False,
                    "reason": "RECOVERY_IN_PROGRESS",
                    "user_message": "Recovery가 진행 중입니다. 복구가 완료된 후 다시 시도하십시오.",
                    "eligible": (),
                    "excluded_review": (),
                },
            )

            result, writer = self._run(window)

        self.assertFalse(result["ok"])
        self.assertEqual("RECOVERY_IN_PROGRESS", result["reason"])
        self.assertIn("Recovery가 진행 중입니다", result["user_message"])
        self.assertNotIn("매매 운영 시간이 아닙니다", result["user_message"])
        window.filter_start_targets_by_recovery.assert_called_once()
        window.recalculate_stock_status_by_operation_policy.assert_not_called()
        writer.assert_not_called()

    def test_authenticated_in_time_validation_failure_uses_validation_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(
                Path(temp_dir),
                "012210",
                trade_amount_type="AMOUNT",
                buy_amount=0,
                previous_close=100_000,
            )
            window = _PrecedenceWindow([target])

            result, writer = self._run(window)

        self.assertFalse(result["ok"])
        self.assertEqual(
            "현재 세션의 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n"
            "시세 정보를 확인한 뒤 다시 시도하십시오.",
            result["user_message"],
        )
        window.recalculate_stock_status_by_operation_policy.assert_not_called()
        writer.assert_not_called()

    def test_authenticated_in_time_normal_flow_starts_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow([target])

            result, writer = self._run(window)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        window.recalculate_stock_status_by_operation_policy.assert_called_once()
        writer.assert_called_once()

    def test_normal_ended_stays_above_global_prerequisite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow(
                [target],
                global_result={
                    "allowed": False,
                    "reason": "RECOVERY_CONTEXT_MISSING",
                    "user_message": "키움 서버에 로그인되어 있지 않습니다.",
                },
            )

            result, writer = self._run(
                window,
                operation_state={
                    "operation_date": "2026-08-25",
                    "operation_status": "NORMAL_ENDED",
                },
            )

        self.assertEqual("NORMAL_ENDED", result["reason"])
        self.assertIn("오늘의 정상 운영이 이미 종료되었습니다", result["user_message"])
        window.split_start_targets.assert_not_called()
        writer.assert_not_called()

    def test_global_emergency_stays_above_global_prerequisite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow(
                [target],
                global_result={
                    "allowed": False,
                    "reason": "RECOVERY_CONTEXT_MISSING",
                    "user_message": "키움 서버에 로그인되어 있지 않습니다.",
                },
            )

            result, writer = self._run(
                window,
                operation_state={"emergency_stop": True},
            )

        self.assertEqual("GLOBAL_EMERGENCY_STOP", result["reason"])
        self.assertIn("전역 긴급정지", result["user_message"])
        window.split_start_targets.assert_not_called()
        writer.assert_not_called()

    def test_mixed_trade_windows_all_reach_recovery_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = _target(Path(temp_dir), "000001", start="18:00:00", end="19:00:00")
            inside = _target(Path(temp_dir), "000002")
            window = _PrecedenceWindow([outside, inside])

            result, _writer = self._run(window)

        self.assertTrue(result["ok"])
        self.assertEqual(
            [outside, inside],
            window.filter_start_targets_by_recovery.call_args.args[0],
        )
        self.assertEqual((outside, inside), result["time_eligible_targets"])
        self.assertEqual((), result["time_blocked_targets"])
        self.assertNotIn("매매 운영 시간이 아닙니다", result["user_message"])

    def test_all_outside_time_starts_after_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            window = _PrecedenceWindow([target])

            result, _writer = self._run(
                window,
                now=run_control.datetime(2026, 8, 25, 21, 0),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual((target,), result["time_eligible_targets"])
        self.assertEqual((), result["time_blocked_targets"])
        window.filter_start_targets_by_recovery.assert_called_once_with(
            [target], action="운영시작"
        )
        self.assertNotIn("검토관리와 자동매매 설정", result["user_message"])

    def test_main_global_caller_blocks_global_prerequisite_before_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = _target(Path(temp_dir), "012210")
            host = SimpleNamespace(
                split_start_targets=Mock(return_value=([target], [])),
                filter_start_targets_by_recovery=Mock(),
            )
            owner = SimpleNamespace(
                routine_table=SimpleNamespace(),
                btn_start=SimpleNamespace(
                    setText=MagicMock(),
                    setStyleSheet=MagicMock(),
                    setEnabled=MagicMock(),
                ),
                refresh_all=MagicMock(),
                main_monitoring_auto_trade_operation_host=Mock(return_value=host),
                statusBar=Mock(return_value=SimpleNamespace(showMessage=MagicMock())),
                global_operation_start_prerequisite=Mock(
                    return_value={
                        "allowed": False,
                        "reason": "RECOVERY_CONTEXT_MISSING",
                        "user_message": "키움 서버에 로그인되어 있지 않습니다.",
                    }
                ),
            )
            adapter = monitoring_context_menu.MainMonitoringStockOperationAdapter(
                owner,
                [],
            )
            result_holder = {}
            start_backend = run_control.auto_trade_start_selected_auto_trades

            def invoke_backend(window, **kwargs):
                result = start_backend(window, **kwargs)
                result_holder["result"] = result
                return result

            with (
                patch.object(
                    monitoring_context_menu,
                    "auto_trade_registered_operation_targets",
                    return_value=[target],
                ),
                patch.object(run_control, "auto_trade_registered_operation_targets", return_value=[target]),
                patch.object(run_control, "auto_trade_stock_operation_excluded", return_value=False),
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(
                    run_control,
                    "current_datetime",
                    return_value=run_control.datetime(2026, 8, 25, 21, 0),
                ),
                patch.object(
                    run_control,
                    "auto_trade_start_selected_auto_trades",
                    side_effect=invoke_backend,
                ),
                patch.object(run_control, "refresh_auto_trade_views"),
                patch.object(run_control, "append_changelog"),
                patch.object(run_control, "_show_start_failure_once"),
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

        result = result_holder["result"]
        self.assertEqual("RECOVERY_CONTEXT_MISSING", result["reason"])
        self.assertEqual("키움 서버에 로그인되어 있지 않습니다.", result["user_message"])
        self.assertNotIn("매매 운영 시간이 아닙니다", result["user_message"])
        host.split_start_targets.assert_not_called()
        host.filter_start_targets_by_recovery.assert_not_called()
        writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
