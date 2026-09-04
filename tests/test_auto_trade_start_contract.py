from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gui_auto_trade_run_control as run_control
import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_context_menu as context_menu
import gui_ats_utils
import operation_policy_gate
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_auto_trade_policy import auto_trade_setting_start_target_decision
from tests.participant_owner_fixture import (
    attach_participant_owner,
    participant_codes,
)
from gui_main_stock_context_menu import (
    MainMonitoringStockOperationAdapter,
    MainMonitoringStockTarget,
    execute_main_monitoring_selective_start,
)
from gui_auto_trade_run_control import (
    START_REQUEST_MULTIPLE,
    START_REQUEST_SINGLE,
    auto_trade_start_selected_auto_trades,
    initial_buy_start_validation,
    operation_start_result_summary_toast_text,
    startup_recovery_operation_block_message,
)
from routine_order_permission import canonical_stock_trading_time_status


class _Viewport:
    def update(self) -> None:
        return None


class _StockTable:
    def viewport(self) -> _Viewport:
        return _Viewport()

    def repaint(self) -> None:
        return None


class _StartWindow:
    def __init__(self, selected: list[tuple[Path, str, str]]) -> None:
        self._selected = selected
        attach_participant_owner(self)
        self.stock_table = _StockTable()
        self.statusBarMessage = Mock()
        self.show_auto_trade_result_dialog = Mock()
        self.refresh_all = Mock()
        self.open_review_required_window = Mock()
        self.rebind_startup_recovery_after_trusted_runtime_update = Mock(
            return_value=True
        )
        self.recalculate_calls: list[tuple[Path, str, str, str, dict]] = []

    def require_startup_recovery_session(self, _action: str) -> bool:
        return True

    def selected_stock_infos(self):
        return list(self._selected)

    def current_selected_routine_name(self) -> str:
        return ""

    def split_start_targets(self, selected):
        return list(selected), []

    def pre_start_review_check(self, routine_name, stock_dir, code, name):
        return {"routine_name": routine_name, "review_reasons": []}

    def mark_review_required(self, *_args, **_kwargs) -> bool:
        return True

    def recalculate_stock_status_by_operation_policy(
        self,
        stock_dir,
        code,
        name,
        source,
        metadata,
    ):
        self.recalculate_calls.append(
            (stock_dir, code, name, source, dict(metadata))
        )
        return "changed", "STOPPED", "MONITORING"


class _ReviewWindow:
    def __init__(self) -> None:
        self.status_updates: list[str] = []

    def mark_review_required(self, stock_dir, code, name, item, source="") -> bool:
        return AutoTradeSettingWindow.mark_review_required(
            self,
            stock_dir,
            code,
            name,
            item,
            source=source,
        )

    def update_stock_status(
        self,
        stock_dir,
        code,
        name,
        new_status,
        extra_state=None,
        log_suffix="",
    ):
        self.status_updates.append(str(new_status))
        return status_ops.auto_trade_update_stock_status(
            self,
            stock_dir,
            code,
            name,
            new_status,
            extra_state,
            log_suffix,
        )


class AutoTradeStartContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._start_clock_patcher = patch.object(
            run_control,
            "current_datetime",
            return_value=datetime(2026, 8, 10, 10, 0, 0),
        )
        self._start_clock_patcher.start()
        self.addCleanup(self._start_clock_patcher.stop)
        self._runtime_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._runtime_temp.cleanup)
        operation_state_path = Path(self._runtime_temp.name) / "operation_state.json"
        operation_state_path.write_text("{}", encoding="utf-8")
        self._operation_state_patcher = patch.object(
            operation_policy_gate,
            "OPERATION_STATE_PATH",
            operation_state_path,
        )
        self._operation_state_patcher.start()
        self.addCleanup(self._operation_state_patcher.stop)
        self._event_journal_patcher = patch(
            "gui_auto_trade_run_control.append_production_event"
        )
        self._event_journal_patcher.start()
        self.addCleanup(self._event_journal_patcher.stop)
        self._changelog_patcher = patch(
            "gui_auto_trade_run_control.append_changelog"
        )
        self._changelog_patcher.start()
        self.addCleanup(self._changelog_patcher.stop)

    def test_running_data_mismatch_enters_review_with_stock_emergency_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "holding_qty": 0,
                        "avg_price": 1000,
                        "trade_enabled": True,
                        "buy_enabled": True,
                        "sell_enabled": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            item = {
                "review_reasons": ["보유 0인데 평단 존재"],
                "current_price": 0,
                "pnl_rate_text": "-",
            }
            window = _ReviewWindow()

            with patch.object(status_ops, "append_stock_log"):
                ok = AutoTradeSettingWindow.mark_review_required(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    item,
                    source="운영시작",
                )

            saved_state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual("REVIEW_REQUIRED", saved_state["status"])
        self.assertTrue(saved_state["review_required"])
        self.assertEqual("PENDING", saved_state["review_status"])
        self.assertEqual("운영 데이터 불일치", saved_state["review_reason"])
        self.assertEqual("보유 0인데 평단 존재", saved_state["review_detail"])
        self.assertEqual("운영 시작", saved_state["review_location"])
        self.assertEqual("운영 데이터 불일치", saved_state["emergency_reason"])
        self.assertEqual(["EMERGENCY_STOPPED", "REVIEW_REQUIRED"], window.status_updates)
        self.assertIn("emergency_stopped_at", saved_state)
        self.assertFalse(saved_state["trade_enabled"])
        self.assertIs(saved_state["buy_enabled"], True)
        self.assertIs(saved_state["sell_enabled"], True)

    def test_running_policy_recalculation_data_mismatch_uses_running_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "operation_mode": "CONTINUOUS",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "holding_qty": 0,
                        "avg_price": 1000,
                        "trade_enabled": True,
                        "buy_enabled": True,
                        "sell_enabled": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = _ReviewWindow()

            with patch.object(status_ops, "append_stock_log"):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    "시간 경과 자동 재판정",
                    silent_unchanged=True,
                )

            saved_state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(("protected", "RUNNING", "REVIEW_REQUIRED"), result)
        self.assertEqual("REVIEW_REQUIRED", saved_state["status"])
        self.assertEqual("운영 데이터 불일치", saved_state["review_reason"])
        self.assertEqual("보유 0인데 평단 존재", saved_state["review_detail"])
        self.assertEqual("운영 중", saved_state["review_location"])
        self.assertEqual("운영 데이터 불일치", saved_state["emergency_reason"])
        self.assertEqual(["EMERGENCY_STOPPED", "REVIEW_REQUIRED"], window.status_updates)
        self.assertFalse(saved_state["trade_enabled"])
        self.assertIs(saved_state["buy_enabled"], True)
        self.assertIs(saved_state["sell_enabled"], True)

    def test_start_requested_recalculation_does_not_relabel_as_running_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "operation_mode": "CONTINUOUS",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "holding_qty": 0,
                        "avg_price": 1000,
                        "trade_enabled": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = _ReviewWindow()

            with patch.object(status_ops, "append_stock_log"):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    "운영시작",
                    {"trade_enabled": True},
                    silent_unchanged=True,
                )

            saved_state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertIn(result[0], {"changed", "unchanged"})
        self.assertNotEqual("REVIEW_REQUIRED", saved_state["status"])
        self.assertNotIn("review_location", saved_state)
        self.assertNotIn("REVIEW_REQUIRED", window.status_updates)
        self.assertNotIn("EMERGENCY_STOPPED", window.status_updates)

    def test_stopped_data_mismatch_review_does_not_add_emergency_stop_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "holding_qty": 0,
                        "avg_price": 1000,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            item = {
                "review_reasons": ["보유 0인데 평단 존재"],
                "current_price": 0,
                "pnl_rate_text": "-",
            }
            window = _ReviewWindow()

            with patch.object(status_ops, "append_stock_log"):
                ok = AutoTradeSettingWindow.mark_review_required(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    item,
                    source="운영시작",
                )

            saved_state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual("REVIEW_REQUIRED", saved_state["status"])
        self.assertEqual("운영 데이터 불일치", saved_state["review_reason"])
        self.assertEqual("보유 0인데 평단 존재", saved_state["review_detail"])
        self.assertEqual(["REVIEW_REQUIRED"], window.status_updates)
        self.assertNotIn("emergency_stopped_at", saved_state)
        self.assertNotIn("emergency_reason", saved_state)

    def test_recovery_block_message_contract_is_shared(self) -> None:
        self.assertEqual(
            "운영시작할 수 없습니다. "
            "로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오.",
            startup_recovery_operation_block_message(
                "운영시작",
                "INVALID_RUNTIME",
            ),
        )

    def _stock(
        self,
        root: Path,
        code: str,
        name: str,
        instance_id: str,
        instance_name: str,
    ) -> tuple[Path, str, str]:
        stock_dir = root / f"{code}_{name}"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "operation_mode": "CONTINUOUS",
                    "assigned_routine_instance_id": instance_id,
                    "routine_definition_id": f"definition-{instance_id}",
                    "routine_instance_name": instance_name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "STOPPED", "trade_enabled": False}),
            encoding="utf-8",
        )
        return stock_dir, code, name

    def test_all_stocks_scope_starts_each_stock_with_its_own_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._stock(root, "111111", "첫종목", "inst-a", "루틴 A")
            second = self._stock(root, "222222", "둘종목", "inst-b", "루틴 B")
            window = _StartWindow([first, second])

            with patch(
                "gui_auto_trade_run_control.append_changelog"
            ) as append_changelog:
                result = auto_trade_start_selected_auto_trades(window)

        self.assertEqual(2, len(window.recalculate_calls))
        self.assertEqual(
            ["운영시작", "운영시작"],
            [call[3] for call in window.recalculate_calls],
        )
        for _stock_dir, _code, _name, _source, metadata in window.recalculate_calls:
            self.assertTrue(metadata["trade_enabled"])
            self.assertNotIn("buy_enabled", metadata)
            self.assertNotIn("sell_enabled", metadata)
            self.assertEqual(
                metadata["trade_started_at"],
                metadata["ignore_signals_before"],
            )
        append_changelog.assert_called_once()
        window.rebind_startup_recovery_after_trusted_runtime_update.assert_called_once_with()
        self.assertEqual(
            "대상종목 2  |  기운영중 0  |  운영시작 2  |  운영불가 0",
            result["summary_toast_message"],
        )
        window.show_auto_trade_result_dialog.assert_not_called()

    def test_new_session_starts_stale_running_and_monitoring_targets_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            targets = [
                self._stock(
                    root,
                    f"{index:06d}",
                    f"종목{index}",
                    "inst-a",
                    "루틴 A",
                )
                for index in range(1, 6)
            ]
            for index, target in enumerate(targets):
                (target[0] / "state.json").write_text(
                    json.dumps(
                        {
                            "status": "MONITORING" if index == 2 else "RUNNING",
                            "trade_enabled": True,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            window = _StartWindow(targets)
            attach_participant_owner(window)
            window.startup_recovery_session_ready = lambda refresh=False: True
            window.start_target_is_review_isolated = (
                lambda _stock_dir, _code: False
            )
            window.split_start_targets = lambda selected: (
                AutoTradeOperationHost.split_start_targets(window, selected)
            )

            with patch(
                "gui_auto_trade_policy.auto_trade_operation_session_phase",
                return_value={
                    "evaluable": True,
                    "phase": "ACTIVE_SESSION",
                    "mode": "CONTINUOUS",
                },
            ):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertTrue(result["ok"], result)
        self.assertEqual(5, result["requested_count"])
        self.assertEqual(5, len(result["eligible"]))
        self.assertEqual(5, result["started_count"])
        self.assertEqual(
            [target[1] for target in targets],
            [call[1] for call in window.recalculate_calls],
        )
        self.assertEqual(
            {target[1] for target in targets},
            set(participant_codes(window)),
        )

    def test_scheduled_start_admission_distinguishes_pre_active_and_final_end(self) -> None:
        window = _StartWindow([])
        attach_participant_owner(window)
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        state = {"status": "STOPPED", "trade_enabled": False}

        operation_policy = {
            "scheduled_operation": {
                "default_start_time": "09:00:00",
                "default_end_buy_time": "13:30:00",
            },
            "manual_operation": {"use_regular_market": True},
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
        }

        def ats_session(key: str) -> dict[str, object]:
            return {
                "extra1": {
                    "enabled": True,
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                },
                "extra2": {
                    "enabled": True,
                    "start_time": "15:40:00",
                    "end_time": "19:50:00",
                },
            }.get(key, {})

        with (
            patch.object(
                gui_ats_utils,
                "read_operation_policy",
                return_value=operation_policy,
            ),
            patch.object(
                gui_ats_utils,
                "manual_ats_session_definition",
                side_effect=ats_session,
            ),
        ):
            pre_start = auto_trade_setting_start_target_decision(
                window,
                state,
                "002810",
                config=config,
                now_dt=datetime(2026, 8, 10, 8, 30, 0),
            )
            active = auto_trade_setting_start_target_decision(
                window,
                state,
                "002810",
                config=config,
                now_dt=datetime(2026, 8, 10, 10, 0, 0),
            )
            final_end = auto_trade_setting_start_target_decision(
                window,
                state,
                "002810",
                config=config,
                now_dt=datetime(2026, 8, 10, 13, 30, 0),
            )
            continuous_pre = auto_trade_setting_start_target_decision(
                window,
                state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 8, 30, 0),
            )
            continuous_active = auto_trade_setting_start_target_decision(
                window,
                state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 10, 0, 0),
            )
            continuous_final = auto_trade_setting_start_target_decision(
                window,
                state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 15, 21, 0),
            )
            ats_state = {
                "status": "STOPPED",
                "trade_enabled": False,
                "manual_ats_selection": {"selected_sessions": ["extra2"]},
            }
            ats_gap = auto_trade_setting_start_target_decision(
                window,
                ats_state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 15, 30, 0),
            )
            ats_active = auto_trade_setting_start_target_decision(
                window,
                ats_state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 16, 0, 0),
            )
            ats_final = auto_trade_setting_start_target_decision(
                window,
                ats_state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 19, 51, 0),
            )
            premarket_state = {
                "status": "STOPPED",
                "trade_enabled": False,
                "manual_ats_selection": {"selected_sessions": ["extra1"]},
            }
            premarket_gap = auto_trade_setting_start_target_decision(
                window,
                premarket_state,
                "012210",
                config={"operation_mode": "CONTINUOUS"},
                now_dt=datetime(2026, 8, 10, 8, 55, 0),
            )
            gap_order_time = canonical_stock_trading_time_status(
                config={"operation_mode": "CONTINUOUS"},
                state=ats_state,
                now_dt=datetime(2026, 8, 10, 15, 30, 0),
            )

        self.assertTrue(pre_start["allowed"])
        self.assertEqual("BEFORE_FIRST_SESSION", pre_start["session_phase"]["phase"])
        self.assertTrue(active["allowed"])
        self.assertEqual("ACTIVE_SESSION", active["session_phase"]["phase"])
        self.assertFalse(final_end["allowed"])
        self.assertEqual("FINAL_SESSION_ENDED", final_end["reason"])
        self.assertTrue(continuous_pre["allowed"])
        self.assertEqual("BEFORE_FIRST_SESSION", continuous_pre["session_phase"]["phase"])
        self.assertTrue(continuous_active["allowed"])
        self.assertEqual("ACTIVE_SESSION", continuous_active["session_phase"]["phase"])
        self.assertFalse(continuous_final["allowed"])
        self.assertEqual("FINAL_SESSION_ENDED", continuous_final["reason"])
        self.assertTrue(ats_gap["allowed"])
        self.assertEqual("BETWEEN_SESSIONS", ats_gap["session_phase"]["phase"])
        self.assertTrue(ats_active["allowed"])
        self.assertEqual("ACTIVE_SESSION", ats_active["session_phase"]["phase"])
        self.assertFalse(ats_final["allowed"])
        self.assertEqual("FINAL_SESSION_ENDED", ats_final["reason"])
        self.assertTrue(premarket_gap["allowed"])
        self.assertEqual("BETWEEN_SESSIONS", premarket_gap["session_phase"]["phase"])
        self.assertFalse(gap_order_time["active"])
        self.assertEqual("OUTSIDE_OPERATION_TIME", gap_order_time["reason"])

    def test_scheduled_final_end_partial_start_refreshes_before_result_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            specs = (
                ("012210", "삼미금속", "CONTINUOUS"),
                ("002810", "삼영무역", "SCHEDULED"),
                ("005070", "코스모신소재", "SCHEDULED"),
                ("063440", "SM Life Design", "SCHEDULED"),
                ("130500", "GH신소재", "SCHEDULED"),
            )
            targets = []
            for code, name, mode in specs:
                target = self._stock(root, code, name, "inst-a", "루틴 A")
                config_path = target[0] / "config.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                config.update(
                    {
                        "operation_mode": mode,
                        "start_time": "09:00:00",
                        "end_buy_time": "13:30:00",
                    }
                )
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False),
                    encoding="utf-8",
                )
                targets.append(target)

            events: list[str] = []
            window = _StartWindow(targets)
            attach_participant_owner(window)
            window.start_target_is_review_isolated = lambda _stock_dir, _code: False
            window.split_start_targets = lambda selected: (
                AutoTradeOperationHost.split_start_targets(window, selected)
            )
            window.start_target_block_details = lambda: (
                AutoTradeOperationHost.start_target_block_details(window)
            )
            window.refresh_auto_trade_assignment_views = Mock(
                side_effect=lambda: events.append("refresh")
            )
            original_recalculate = window.recalculate_stock_status_by_operation_policy

            def traced_recalculate(stock_dir, code, name, source, metadata):
                events.append(f"state:{code}")
                return original_recalculate(
                    stock_dir,
                    code,
                    name,
                    source,
                    metadata,
                )

            window.recalculate_stock_status_by_operation_policy = traced_recalculate
            original_register = (
                run_control.auto_trade_register_current_session_operation_participants
            )

            def traced_register(owner, stock_codes):
                events.append(f"participant:{','.join(stock_codes)}")
                return original_register(owner, stock_codes)

            def phase_for_mode(config, _state, *, now_dt=None):
                mode = str(config.get("operation_mode") or "SCHEDULED").upper()
                return {
                    "evaluable": True,
                    "phase": (
                        "BETWEEN_SESSIONS"
                        if mode == "CONTINUOUS"
                        else "FINAL_SESSION_ENDED"
                    ),
                    "mode": mode,
                    "future_session_exists": mode == "CONTINUOUS",
                }

            with (
                patch(
                    "gui_auto_trade_policy.auto_trade_operation_session_phase",
                    side_effect=phase_for_mode,
                ),
                patch.object(
                    run_control,
                    "auto_trade_register_current_session_operation_participants",
                    side_effect=traced_register,
                ),
                patch.object(
                    run_control,
                    "write_global_operation_running_state",
                    side_effect=lambda **_kwargs: (
                        events.append("global-state")
                        or {"ok": True, "started_new_session": False}
                    ),
                ),
                patch.object(
                    run_control,
                    "_start_operation_host_after_explicit_operation_start",
                    side_effect=lambda _window: (
                        events.append("operation-host")
                        or {"started": True, "reason_code": "RECOVERY_TIMER_STARTED"}
                    ),
                ),
                patch.object(
                    run_control,
                    "_show_operation_start_summary_toast",
                    side_effect=lambda *_args: events.append("toast"),
                ),
            ):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_MULTIPLE,
                    selected_targets=targets,
                    source="scheduled-final-end-test",
                )

        self.assertEqual(5, result["requested_count"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual(4, result["blocked_count"])
        self.assertEqual(0, result["failed_count"])
        self.assertEqual(
            ("FINAL_SESSION_ENDED",) * 4,
            tuple(item["reason"] for item in result["blocked_target_details"]),
        )
        self.assertEqual(
            {"012210"},
            set(participant_codes(window)),
        )
        self.assertEqual(["012210"], [call[1] for call in window.recalculate_calls])
        self.assertLess(events.index("state:012210"), events.index("participant:012210"))
        self.assertLess(events.index("participant:012210"), events.index("operation-host"))
        self.assertLess(events.index("operation-host"), events.index("refresh"))
        self.assertLess(events.index("refresh"), events.index("toast"))
        window.refresh_auto_trade_assignment_views.assert_called_once_with()
        window.show_auto_trade_result_dialog.assert_not_called()
        self.assertEqual(
            "대상종목 5  |  기운영중 0  |  운영시작 1  |  운영불가 4\n시간운영 종료 4",
            operation_start_result_summary_toast_text(result),
        )

    def test_main_production_adapter_blocks_all_final_ended_targets_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            specs = (
                ("012210", "삼미금속", "CONTINUOUS"),
                ("002810", "삼영무역", ""),
                ("005070", "코스모신소재", ""),
                ("063440", "SM Life Design", ""),
                ("130500", "GH신소재", ""),
            )
            stock_targets: list[tuple[Path, str, str]] = []
            adapter_targets: list[MainMonitoringStockTarget] = []
            for code, name, raw_mode in specs:
                stock_dir, _code, _name = self._stock(
                    root,
                    code,
                    name,
                    "inst-a",
                    "루틴 A",
                )
                config_path = stock_dir / "config.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if raw_mode:
                    config["operation_mode"] = raw_mode
                else:
                    config.pop("operation_mode", None)
                    config.update(
                        {
                            "start_time": "09:00:00",
                            "end_buy_time": "13:30:00",
                        }
                    )
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False),
                    encoding="utf-8",
                )
                stock_targets.append((stock_dir, code, name))
                adapter_targets.append(
                    MainMonitoringStockTarget(
                        stock_dir=stock_dir,
                        code=code,
                        name=name,
                        routine_instance_id="inst-a",
                    )
                )

            status_bar = SimpleNamespace(showMessage=Mock())
            main = SimpleNamespace(
                routine_table=_StockTable(),
                btn_start=Mock(),
                statusBar=lambda: status_bar,
                startup_recovery_session_ready=lambda refresh=True: True,
                global_operation_start_prerequisite=lambda _action: {
                    "allowed": True,
                    "reason": "READY",
                },
            )
            host = AutoTradeOperationHost(main)
            self.assertEqual((), host.current_session_operation_participant_stock_codes())
            main.main_monitoring_auto_trade_operation_host = lambda: host
            adapter = MainMonitoringStockOperationAdapter(main, adapter_targets)
            events: list[str] = []
            adapter.running_registered_operation_targets = Mock(return_value=[])
            adapter.refresh_auto_trade_assignment_views = Mock(
                side_effect=lambda: events.append("refresh")
            )
            adapter.update_global_operation_button_state = Mock(
                side_effect=lambda: events.append("button")
            )
            adapter.statusBarMessage = Mock()
            host.recalculate_stock_status_by_operation_policy = Mock()
            state_before = {
                stock_dir: (stock_dir / "state.json").read_bytes()
                for stock_dir, _code, _name in stock_targets
            }
            operation_policy = {
                "scheduled_operation": {
                    "default_start_time": "09:00:00",
                    "default_end_buy_time": "13:30:00",
                },
                "manual_operation": {"use_regular_market": True},
                "regular_market": {
                    "start_time": "09:00:00",
                    "end_time": "15:20:00",
                },
            }

            def fixed_production_phase(config, state, *, now_dt=None):
                return gui_ats_utils.auto_trade_operation_session_phase(
                    config,
                    state,
                    now_dt=datetime(2026, 8, 28, 16, 0, 0),
                    operation_policy_reader=lambda: operation_policy,
                    ats_session_reader=lambda _key: {},
                )

            with (
                patch.object(run_control, "read_operation_state", return_value={}),
                patch(
                    "gui_auto_trade_policy.auto_trade_operation_session_phase",
                    side_effect=fixed_production_phase,
                ),
                patch.object(
                    run_control,
                    "auto_trade_register_current_session_operation_participants",
                ) as participant_writer,
                patch.object(
                    run_control,
                    "write_global_operation_running_state",
                ) as global_state_writer,
                patch.object(
                    run_control,
                    "_start_operation_host_after_explicit_operation_start",
                ) as operation_host_start,
                patch.object(
                    run_control,
                    "_show_operation_start_summary_toast",
                    side_effect=lambda *_args: events.append("toast"),
                ),
            ):
                result = execute_main_monitoring_selective_start(adapter)

            state_after = {
                stock_dir: (stock_dir / "state.json").read_bytes()
                for stock_dir, _code, _name in stock_targets
            }

        self.assertFalse(result["ok"])
        self.assertEqual(5, result["requested_count"])
        self.assertEqual(0, result["eligible_count"])
        self.assertEqual(0, result["started_count"])
        self.assertEqual(5, result["blocked_count"])
        self.assertEqual(0, result["failed_count"])
        self.assertEqual(
            ("FINAL_SESSION_ENDED",) * 5,
            tuple(item["reason"] for item in result["blocked_target_details"]),
        )
        self.assertEqual(
            ("CONTINUOUS", "SCHEDULED", "SCHEDULED", "SCHEDULED", "SCHEDULED"),
            tuple(item["operation_mode"] for item in result["blocked_target_details"]),
        )
        self.assertEqual(state_before, state_after)
        self.assertEqual((), host.current_session_operation_participant_stock_codes())
        host.recalculate_stock_status_by_operation_policy.assert_not_called()
        participant_writer.assert_not_called()
        global_state_writer.assert_not_called()
        operation_host_start.assert_not_called()
        self.assertLess(events.index("refresh"), events.index("button"))
        self.assertLess(events.index("button"), events.index("toast"))
        self.assertEqual(
            "대상종목 5  |  기운영중 0  |  운영시작 0  |  운영불가 5\n수동운영 종료 1 · 시간운영 종료 4",
            operation_start_result_summary_toast_text(result),
        )

    def test_missing_instance_assignment_is_reported_without_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "첫종목", "", "")
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertEqual([], window.recalculate_calls)
        window.show_auto_trade_result_dialog.assert_not_called()
        self.assertEqual(
            "모든 등록 종목의 필수 설정이 완료되지 않았습니다.\n"
            "자동매매 설정을 확인하십시오.",
            result["user_message"],
        )

    def test_review_stock_is_excluded_before_recovery_and_normal_stock_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            normal = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            review = self._stock(root, "222222", "검토종목", "inst-a", "루틴 A")
            (review[0] / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_reason": "보유수량 있음 + 현재가 확인 불가",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = _StartWindow([normal, review])
            window.filter_start_targets_by_recovery = Mock(
                return_value={
                    "allowed": True,
                    "reason": "RECOVERY_COMPLETED",
                    "eligible": (normal,),
                    "excluded_review": (),
                }
            )

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        window.filter_start_targets_by_recovery.assert_called_once_with(
            [normal],
            action="운영시작",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(("222222 검토종목",), result["excluded_review"])
        self.assertEqual(("111111 정상종목",), result["eligible"])
        self.assertEqual(1, len(result["completed"]))
        self.assertEqual(1, len(window.recalculate_calls))
        self.assertIn("운영 시작 1개 · 검토 제외 1개", result["user_message"])
        self.assertIn("검토관리 필요: 1종목", result["user_message"])
        window.statusBarMessage.assert_called_with(result["user_message"])
        self.assertIn("운영시작 1", result["summary_toast_message"])
        window.show_auto_trade_result_dialog.assert_not_called()

    def test_one_review_stock_uses_single_message_before_recovery_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = self._stock(root, "222222", "검토종목", "inst-a", "루틴 A")
            (review[0] / "state.json").write_text(
                json.dumps(
                    {"status": "REVIEW_REQUIRED", "review_required": True},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = _StartWindow([review])
            window.filter_start_targets_by_recovery = Mock()

            result = auto_trade_start_selected_auto_trades(window)

        window.filter_start_targets_by_recovery.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual("NO_STARTABLE_TARGETS", result["reason"])
        self.assertEqual(("222222 검토종목",), result["excluded_review"])
        window.statusBarMessage.assert_called_with(
            "222222 검토종목은 검토관리 대상입니다.\n"
            "검토관리에서 처리한 뒤 다시 시도하십시오."
        )

    def test_all_emergency_stocks_report_only_actual_block_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._stock(root, "000660", "SK하이닉스", "inst-a", "루틴 A")
            second = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")
            for stock_dir, _code, _name in (first, second):
                (stock_dir / "state.json").write_text(
                    json.dumps(
                        {
                            "status": "EMERGENCY_STOPPED",
                            "review_required": True,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            for source in (
                "auto_trade_context_menu",
                "auto_trade_global_start_button",
            ):
                with self.subTest(source=source):
                    window = _StartWindow([first, second])
                    window.filter_start_targets_by_recovery = Mock()

                    result = auto_trade_start_selected_auto_trades(
                        window,
                        request_scope=START_REQUEST_MULTIPLE,
                        source=source,
                    )

                    window.filter_start_targets_by_recovery.assert_not_called()
                    self.assertFalse(result["ok"])
                    self.assertEqual(
                        "모든 종목이 긴급정지 상태입니다.",
                        result["user_message"],
                    )
                    self.assertNotIn(
                        "검토관리와 자동매매 설정을 확인하십시오.",
                        result["user_message"],
                    )
                    self.assertNotIn("2", result["user_message"])

    def test_one_emergency_target_uses_single_message_for_every_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005380", "현대차", "inst-a", "루틴 A")
            (stock[0] / "state.json").write_text(
                json.dumps(
                    {
                        "status": "EMERGENCY_STOPPED",
                        "review_required": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            cases = (
                (START_REQUEST_SINGLE, "auto_trade_status_indicator"),
                (START_REQUEST_MULTIPLE, "auto_trade_context_menu"),
            )
            for request_scope, source in cases:
                with self.subTest(request_scope=request_scope, source=source):
                    window = _StartWindow([stock])
                    result = auto_trade_start_selected_auto_trades(
                        window,
                        request_scope=request_scope,
                        source=source,
                    )

                    self.assertFalse(result["ok"])
                    self.assertEqual(request_scope, result["request_scope"])
                    self.assertEqual(source, result["source"])
                    self.assertEqual(
                        "005380 현대차는 긴급정지 상태입니다.",
                        result["user_message"],
                    )

    def test_recovery_block_preserves_user_message_without_exposing_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            window = _StartWindow([stock])
            window.filter_start_targets_by_recovery = Mock(
                return_value={
                    "allowed": False,
                    "reason": "RECOVERY_CONTEXT_MISSING",
                    "user_message": "키움 서버에 로그인되어 있지 않습니다.",
                    "eligible": (),
                    "excluded_review": (),
                }
            )

            result = auto_trade_start_selected_auto_trades(window)

        self.assertFalse(result["ok"])
        self.assertEqual("RECOVERY_CONTEXT_MISSING", result["reason"])
        self.assertEqual(
            "키움 서버에 로그인되어 있지 않습니다.",
            result["user_message"],
        )
        self.assertNotIn("RECOVERY_", window.statusBarMessage.call_args.args[0])

    def test_legacy_schema_blocks_before_operation_start_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930", "삼성전자", "inst-a", "지표추종매매A")
            state_before = (stock[0] / "state.json").read_bytes()
            window = _StartWindow([stock])
            window.send_order = Mock()
            window.filter_start_targets_by_recovery = Mock(
                return_value={
                    "allowed": False,
                    "reason": "MIGRATION_REQUIRED",
                    "user_message": (
                        "구형 감시/운영 설정 데이터가 감지되었습니다. "
                        "현재 운영 전에 데이터 마이그레이션이 필요합니다."
                    ),
                    "eligible": (),
                    "excluded_review": (),
                    "blocked_target_details": (
                        {
                            "stock_code": "005930",
                            "stock_name": "삼성전자",
                            "reason": "MIGRATION_REQUIRED",
                            "display_label": "005930 삼성전자",
                        },
                    ),
                }
            )

            result = auto_trade_start_selected_auto_trades(
                window,
                request_scope=START_REQUEST_SINGLE,
            )
            state_after = (stock[0] / "state.json").read_bytes()

        self.assertFalse(result["ok"])
        self.assertEqual("MIGRATION_REQUIRED", result["reason"])
        self.assertEqual(state_before, state_after)
        self.assertEqual([], window.recalculate_calls)
        self.assertEqual((), participant_codes(window))
        window.send_order.assert_not_called()

    def test_assigned_post_login_stock_is_blocked_before_operation_or_order_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000660", "SK하이닉스", "inst-a", "지표추종매매A")
            state_before = (stock[0] / "state.json").read_bytes()
            window = _StartWindow([stock])
            window.send_order = Mock()
            window.filter_start_targets_by_recovery = Mock(
                return_value={
                    "allowed": False,
                    "reason": "RECOVERY_STOCK_PENDING",
                    "user_message": (
                        "선택한 종목은 현재 로그인 세션에서 Recovery가 완료되지 않아 "
                        "운영을 시작할 수 없습니다. 다음 로그인 Recovery 완료 후 운영 가능합니다."
                    ),
                    "eligible": (),
                    "excluded_review": (),
                    "blocked_target_details": (
                        {
                            "stock_code": "000660",
                            "stock_name": "SK하이닉스",
                            "reason": "RECOVERY_STOCK_PENDING",
                            "display_label": "000660 SK하이닉스",
                        },
                    ),
                }
            )

            result = auto_trade_start_selected_auto_trades(
                window,
                request_scope=START_REQUEST_SINGLE,
            )

            self.assertFalse(result["ok"])
            self.assertEqual("RECOVERY_STOCK_PENDING", result["reason"])
            self.assertEqual(state_before, (stock[0] / "state.json").read_bytes())
            self.assertEqual([], window.recalculate_calls)
            self.assertEqual((), participant_codes(window))
            window.send_order.assert_not_called()
            self.assertIn("운영을 시작할 수 없습니다", result["user_message"])
            self.assertIn("다음 로그인 Recovery 완료 후", result["user_message"])

    def test_runtime_missing_isolated_as_review_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            (stock[0] / "state.json").unlink()
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertFalse(result["ok"])
        self.assertIn("검토관리 대상", result["user_message"])
        self.assertFalse(window._last_operation_failure_dialog_shown)
        window.statusBarMessage.assert_called_once_with(result["user_message"])
        window.show_auto_trade_result_dialog.assert_not_called()

    def test_state_save_failure_reports_one_aggregate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            window = _StartWindow([stock])
            window.recalculate_stock_status_by_operation_policy = Mock(
                return_value=("failed", "STOPPED", "MONITORING")
            )

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertFalse(result["ok"])
        self.assertEqual(
            "종목의 운영 상태를 저장하지 못했습니다.\n"
            "로그를 확인한 뒤 다시 시도하십시오.",
            result["user_message"],
        )
        self.assertTrue(window._last_operation_failure_dialog_shown)
        window.show_auto_trade_result_dialog.assert_not_called()

    def test_internal_exception_hides_exception_and_reason_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            window = _StartWindow([stock])
            window.pre_start_review_check = Mock(
                side_effect=RuntimeError("secret backend detail")
            )

            with (
                patch("gui_auto_trade_run_control.LOGGER.exception"),
                patch("gui_auto_trade_run_control.append_changelog"),
            ):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertFalse(result["ok"])
        self.assertIn("로그를 확인한 뒤 다시 시도", result["user_message"])
        self.assertNotIn("secret backend detail", result["user_message"])
        self.assertNotIn("INTERNAL_EXCEPTION", result["user_message"])

    def test_quantity_basis_is_not_subject_to_amount_minimum(self) -> None:
        result = initial_buy_start_validation(
            {"trade_amount_type": "QUANTITY", "buy_qty": 1},
            {},
        )

        self.assertTrue(result["allowed"])
        self.assertEqual("QUANTITY", result["mode"])

    def test_amount_default_uses_resolved_starting_budget_without_previous_close(self) -> None:
        result = initial_buy_start_validation(
            {"trade_amount_type": "AMOUNT", "buy_amount": 0},
            {},
            resolved_starting_budget=150_000,
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(150_000, result["resolved_starting_budget"])
        self.assertEqual("fresh_current_price", result["starting_budget_source"])

    def test_amount_explicit_value_does_not_require_previous_close(self) -> None:
        result = initial_buy_start_validation(
            {"trade_amount_type": "AMOUNT", "buy_amount": 1_000_000},
            {},
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(1_000_000, result["resolved_starting_budget"])
        self.assertEqual("explicit", result["starting_budget_source"])

    def test_amount_default_without_fresh_price_fails_closed(self) -> None:
        result = initial_buy_start_validation(
            {"trade_amount_type": "AMOUNT", "buy_amount": 0},
            {"previous_close": 100_000},
        )

        self.assertFalse(result["allowed"])
        self.assertEqual("STARTING_BUDGET_UNRESOLVED", result["reason"])

    def test_amount_below_minimum_does_not_start_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "첫종목", "inst-a", "루틴 A")
            config_path = stock[0] / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({"trade_amount_type": "AMOUNT", "buy_amount": 0})
            config_path.write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock[0] / "state.json").write_text(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "trade_enabled": False,
                        "previous_close": 100_000,
                    }
                ),
                encoding="utf-8",
            )
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertEqual([], window.recalculate_calls)
        window.show_auto_trade_result_dialog.assert_not_called()
        self.assertEqual(
            "현재 세션의 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n"
            "시세 정보를 확인한 뒤 다시 시도하십시오.",
            result["user_message"],
        )

    def test_partial_validation_exclusion_uses_status_summary_without_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            normal = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            blocked = self._stock(root, "222222", "설정미달", "inst-a", "루틴 A")
            config_path = blocked[0] / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({"trade_amount_type": "AMOUNT", "buy_amount": 0})
            config_path.write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )
            (blocked[0] / "state.json").write_text(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "trade_enabled": False,
                        "previous_close": 100_000,
                    }
                ),
                encoding="utf-8",
            )
            window = _StartWindow([normal, blocked])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual(1, result["excluded_validation_count"])
        self.assertEqual(
            "운영 시작 1개 · 설정 제외 1개",
            result["user_message"],
        )
        window.statusBarMessage.assert_called_with(result["user_message"])
        self.assertEqual(
            "대상종목 2  |  기운영중 0  |  운영시작 1  |  운영불가 1",
            result["summary_toast_message"],
        )
        self.assertFalse(
            bool(getattr(window, "_last_operation_failure_dialog_shown", False))
        )

    def test_explicit_single_success_uses_stock_message_without_result_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000660", "SK하이닉스", "inst-a", "루틴 A")
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertTrue(result["ok"])
        self.assertEqual("single", result["request_scope"])
        self.assertEqual("000660", result["target_stock_code"])
        self.assertEqual("SK하이닉스", result["target_stock_name"])
        self.assertEqual("SK하이닉스 운영을 시작했습니다.", result["user_message"])
        window.statusBarMessage.assert_called_with(result["user_message"])
        window.show_auto_trade_result_dialog.assert_not_called()

    def test_one_target_multiple_scope_keeps_aggregate_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000660", "SK하이닉스", "inst-a", "루틴 A")
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_MULTIPLE,
                )

        self.assertTrue(result["ok"])
        self.assertEqual("multiple", result["request_scope"])
        self.assertEqual("운영 시작 1개", result["user_message"])
        self.assertEqual(
            "대상종목 1  |  기운영중 0  |  운영시작 1  |  운영불가 0",
            result["summary_toast_message"],
        )

    def test_single_review_target_names_stock_and_shows_one_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000660", "SK하이닉스", "inst-a", "루틴 A")
            (stock[0] / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_reason": "보유수량 있음 + 현재가 확인 불가",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(
            "000660 SK하이닉스는 검토관리 대상입니다.\n"
            "검토관리에서 처리한 뒤 다시 시도하십시오.",
            result["user_message"],
        )
        self.assertTrue(window._last_operation_failure_dialog_shown)
        self.assertNotIn("REVIEW_REQUIRED", window._last_operation_failure_dialog_message)

    def test_single_emergency_target_reports_protection_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000660", "SK하이닉스", "inst-a", "루틴 A")
            (stock[0] / "state.json").write_text(
                json.dumps(
                    {
                        "status": "EMERGENCY_STOPPED",
                        "review_required": True,
                        "review_reason": "긴급정지",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(
            "000660 SK하이닉스는 긴급정지 상태입니다.",
            result["user_message"],
        )
        self.assertNotIn("EMERGENCY_STOPPED", result["user_message"])
        self.assertNotIn("검토관리에서 상태를 확인", result["user_message"])
        self.assertNotIn("다시 시도", result["user_message"])

    def test_single_already_running_names_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "068270", "셀트리온", "inst-a", "루틴 A")
            (stock[0] / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            window = _StartWindow([stock])
            attach_participant_owner(window, {"068270"})
            window.split_start_targets = Mock(
                return_value=([], ["068270 셀트리온(운영)"])
            )

            result = auto_trade_start_selected_auto_trades(
                window,
                request_scope=START_REQUEST_SINGLE,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            "068270 셀트리온은 이미 운영 중입니다.",
            result["user_message"],
        )

    def test_single_missing_settings_names_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "086520", "에코프로", "inst-a", "루틴 A")
            (stock[0] / "config.json").unlink()
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertFalse(result["ok"])
        self.assertIn("086520 에코프로의 필수 운영 설정", result["user_message"])
        self.assertNotIn("MISSING_REQUIRED_SETTINGS", result["user_message"])

    def test_single_validation_failures_use_stock_specific_messages(self) -> None:
        scenarios = (
            (
                "STARTING_BUDGET_UNRESOLVED",
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                {"previous_close": 100_000},
                "005930 삼성전자의 현재 세션 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.",
            ),
            (
                "INVALID_INITIAL_BUY_QUANTITY",
                {"trade_amount_type": "QUANTITY", "buy_qty": 0},
                {},
                "005930 삼성전자의 초회 매수 주수가 설정되지 않았습니다.",
            ),
        )
        for expected_reason, config_updates, state_updates, expected_message in scenarios:
            with self.subTest(reason=expected_reason), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                stock = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")
                config = json.loads((stock[0] / "config.json").read_text(encoding="utf-8"))
                config.update(config_updates)
                (stock[0] / "config.json").write_text(
                    json.dumps(config, ensure_ascii=False),
                    encoding="utf-8",
                )
                state = {"status": "STOPPED", "trade_enabled": False}
                state.update(state_updates)
                (stock[0] / "state.json").write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                window = _StartWindow([stock])

                with patch("gui_auto_trade_run_control.append_changelog"):
                    result = auto_trade_start_selected_auto_trades(
                        window,
                        request_scope=START_REQUEST_SINGLE,
                    )

            self.assertFalse(result["ok"])
            self.assertIn(expected_message, result["user_message"])
            self.assertNotIn(expected_reason, result["user_message"])

    def test_single_stock_recovery_failure_names_stock_but_global_failure_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")

            stock_window = _StartWindow([stock])
            stock_window.filter_start_targets_by_recovery = Mock(
                return_value={
                    "allowed": False,
                    "reason": "RECOVERY_STOCK_PENDING",
                    "user_message": "선택한 종목의 Recovery가 아직 완료되지 않았습니다.",
                    "eligible": (),
                    "excluded_review": (),
                }
            )
            stock_result = auto_trade_start_selected_auto_trades(
                stock_window,
                request_scope=START_REQUEST_SINGLE,
            )

            global_window = _StartWindow([stock])
            global_window.filter_start_targets_by_recovery = Mock(
                return_value={
                    "allowed": False,
                    "reason": "RECOVERY_CONTEXT_MISSING",
                    "user_message": (
                        "키움 서버에 로그인되어 있지 않습니다.\n"
                        "로그인한 뒤 다시 시도하십시오."
                    ),
                    "eligible": (),
                    "excluded_review": (),
                }
            )
            global_result = auto_trade_start_selected_auto_trades(
                global_window,
                request_scope=START_REQUEST_SINGLE,
            )

        self.assertIn(
            "005930 삼성전자는 현재 로그인 세션에서 Recovery가 완료되지 않아 "
            "운영을 시작할 수 없습니다.",
            stock_result["user_message"],
        )
        self.assertIn("다음 로그인 Recovery 완료 후 운영 가능합니다.", stock_result["user_message"])
        self.assertNotIn("RECOVERY_STOCK_PENDING", stock_result["user_message"])
        self.assertEqual(
            "키움 서버에 로그인되어 있지 않습니다.\n로그인한 뒤 다시 시도하십시오.",
            global_result["user_message"],
        )
        self.assertNotIn("삼성전자", global_result["user_message"])

    def test_single_state_save_and_backend_exception_hide_internal_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")

            save_window = _StartWindow([stock])
            save_window.recalculate_stock_status_by_operation_policy = Mock(
                return_value=("failed", "STOPPED", "MONITORING")
            )
            with patch("gui_auto_trade_run_control.append_changelog"):
                save_result = auto_trade_start_selected_auto_trades(
                    save_window,
                    request_scope=START_REQUEST_SINGLE,
                )

            exception_window = _StartWindow([stock])
            exception_window.pre_start_review_check = Mock(
                side_effect=RuntimeError("secret backend detail")
            )
            with (
                patch("gui_auto_trade_run_control.LOGGER.exception"),
                patch("gui_auto_trade_run_control.append_changelog"),
            ):
                exception_result = auto_trade_start_selected_auto_trades(
                    exception_window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertIn(
            "005930 삼성전자의 운영 상태를 저장하거나 다시 확인하지 못했습니다.",
            save_result["user_message"],
        )
        self.assertIn(
            "005930 삼성전자의 운영 상태를 확인하는 중 오류가 발생했습니다.",
            exception_result["user_message"],
        )
        self.assertNotIn("STATE_SAVE_FAILED", save_result["user_message"])
        self.assertNotIn("secret backend detail", exception_result["user_message"])
        self.assertTrue(save_window._last_operation_failure_dialog_shown)
        self.assertTrue(exception_window._last_operation_failure_dialog_shown)

    def test_single_missing_name_falls_back_to_stock_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930", "placeholder", "inst-a", "루틴 A")
            target = (stock[0], "005930", "")
            (stock[0] / "config.json").unlink()
            window = _StartWindow([target])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertIn("005930의 필수 운영 설정", result["user_message"])
        self.assertEqual("", result["target_stock_name"])

    def test_each_single_failure_request_may_show_exactly_one_toast(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")
            (stock[0] / "config.json").unlink()
            window = _StartWindow([stock])

            with patch(
                "gui_auto_trade_run_control._show_operation_start_failure_toast",
                wraps=run_control._show_operation_start_failure_toast,
            ) as toast, patch("gui_auto_trade_run_control.append_changelog"):
                auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )
                auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertEqual(2, toast.call_count)

    def test_invalid_single_scope_with_multiple_targets_falls_back_to_multiple(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")
            second = self._stock(root, "000660", "SK하이닉스", "inst-a", "루틴 A")
            window = _StartWindow([first, second])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    request_scope=START_REQUEST_SINGLE,
                )

        self.assertEqual("multiple", result["request_scope"])
        self.assertEqual("운영 시작 2개", result["user_message"])

    def test_explicit_targets_are_deduplicated_and_preserve_request_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930", "삼성전자", "inst-a", "루틴 A")
            window = _StartWindow([])

            result = auto_trade_start_selected_auto_trades(
                window,
                request_scope=START_REQUEST_MULTIPLE,
                selected_targets=[stock, stock],
                source="auto_trade_context_menu",
            )

        window.statusBarMessage.assert_not_called()
        self.assertEqual(1, result["requested_count"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual(
            "auto_trade_context_menu",
            result["source"],
        )

    def test_stock_context_menu_start_uses_selected_rows_multiple_entrypoint(self) -> None:
        class FakeAction:
            def __init__(self, text):
                self.text = text
                self.enabled = True

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

        class FakeMenu:
            def __init__(self):
                self.actions = []

            def addAction(self, text):
                action = FakeAction(text)
                self.actions.append(action)
                return action

            def addSeparator(self):
                return FakeAction("<separator>")

            def exec_(self, _position):
                return self.actions[0]

        selected = [(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        window = SimpleNamespace(
            stock_table=SimpleNamespace(
                itemAt=Mock(return_value=SimpleNamespace(row=lambda: 0)),
                viewport=Mock(
                    return_value=SimpleNamespace(mapToGlobal=lambda position: position)
                ),
            ),
            ensure_context_row_selected=Mock(),
            selected_stock_infos=Mock(return_value=selected),
            selected_operation_mode_set=Mock(return_value={"SCHEDULED"}),
            start_selected_rows_auto_trades=Mock(),
        )
        menu = FakeMenu()

        with (
            patch.object(context_menu, "_new_stock_context_menu", return_value=menu),
            patch.object(
                context_menu,
                "inspect_stock_review_state",
                return_value=SimpleNamespace(
                    review_required=False,
                    state={"status": "STOPPED"},
                ),
            ),
            patch.object(context_menu, "_context_menu_operation_policy", return_value={}),
            patch.object(
                context_menu,
                "_add_early_close_menu",
                return_value={
                    "menu": FakeAction("조기마감"),
                    "routine": FakeAction("루틴마감"),
                    "market": FakeAction("시장가"),
                    "current": FakeAction("현재가"),
                    "profit_loss": FakeAction("손/익절"),
                    "carry": FakeAction("이월"),
                    "cancel": FakeAction("취소"),
                },
            ),
            patch.object(
                context_menu,
                "_add_individual_liquidation_menu",
                return_value={
                    "menu": FakeAction("개별청산"),
                    "time_menu": FakeAction("시간"),
                    "time_actions": (),
                    "market": object(),
                    "current": object(),
                    "carry": object(),
                    "method": "이월",
                    "minutes": "5",
                },
            ),
        ):
            context_menu.show_auto_trade_stock_context_menu(window, object())

        self.assertEqual("운영시작", menu.actions[0].text)
        self.assertTrue(menu.actions[0].enabled)
        window.start_selected_rows_auto_trades.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
