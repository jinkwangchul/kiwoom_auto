from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_auto_trade_run_control as run_control
import gui_auto_trade_status_ops as status_ops


class _Viewport:
    def update(self) -> None:
        return None


class _StockTable:
    def viewport(self) -> _Viewport:
        return _Viewport()

    def repaint(self) -> None:
        return None


class _StartWindow:
    def __init__(
        self,
        target: tuple[Path, str, str] | list[tuple[Path, str, str]],
    ) -> None:
        self.targets = list(target) if isinstance(target, list) else [target]
        self.stock_table = _StockTable()
        self.statusBarMessage = Mock()
        self.show_auto_trade_result_dialog = Mock()
        self.refresh_all = Mock()
        self.rebind_startup_recovery_after_trusted_runtime_update = Mock(
            return_value=True
        )
        self.recalculate_calls: list[dict[str, object]] = []

    def selected_stock_infos(self):
        return list(self.targets)

    def split_start_targets(self, selected):
        return list(selected), []

    def require_startup_recovery_session(self, _action: str) -> bool:
        return True

    def pre_start_review_check(self, *_args):
        return {"review_reasons": []}

    def mark_review_required(self, *_args, **_kwargs) -> bool:
        return True

    def recalculate_stock_status_by_operation_policy(
        self, _stock_dir, _code, _name, _source, metadata
    ):
        self.recalculate_calls.append(dict(metadata))
        return "changed", "STOPPED", "RUNNING"


class _PersistingStartWindow(_StartWindow):
    def __init__(self, target: tuple[Path, str, str]) -> None:
        super().__init__(target)
        self.state_write_count = 0

    def update_stock_status(
        self, stock_dir, _code, _name, new_status, extra_state=None, log_suffix=""
    ) -> bool:
        state_path = Path(stock_dir) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = new_status
        state.update(dict(extra_state or {}))
        state_path.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        self.state_write_count += 1
        return True

    def recalculate_stock_status_by_operation_policy(
        self, stock_dir, code, name, source, metadata
    ):
        self.recalculate_calls.append(dict(metadata))
        return status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
            self, stock_dir, code, name, source, metadata
        )


class SameDayRestartGuardTest(unittest.TestCase):
    NOW = datetime(2026, 8, 10, 10, 0, 0)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stock_dir = self.root / "005930_삼성전자"
        self.stock_dir.mkdir()
        self.queue_path = self.root / "order_queue.json"
        self.queue_path.write_text('{"orders": []}', encoding="utf-8")
        self.config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
            "assigned_routine_instance_id": "instance-a",
            "routine_instance_name": "루틴 A",
            "real_trade_enabled": True,
            "trade_amount_type": "QUANTITY",
            "buy_qty": 1,
        }
        self.state = {
            "status": "STOPPED",
            "holding_qty": 0,
            "trade_enabled": False,
            "buy_enabled": False,
            "sell_enabled": False,
            "emergency_released_at": "2026-08-10 09:30:00",
        }
        self.operation_state = {
            "operation_date": "2026-08-10",
            "operation_status": "STOPPED",
            "emergency_stop": False,
        }

    def guard(
        self,
        *,
        now: datetime | None = None,
        config: dict[str, object] | None = None,
        state: dict[str, object] | None = None,
        operation_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return run_control.auto_trade_same_day_restart_guard(
            stock_dir=self.stock_dir,
            stock_code="005930",
            config=dict(self.config if config is None else config),
            state=dict(self.state if state is None else state),
            operation_state=dict(
                self.operation_state if operation_state is None else operation_state
            ),
            now_dt=now or self.NOW,
            order_queue_path=self.queue_path,
        )

    def test_scheduled_restart_admission_is_independent_of_trade_window(self) -> None:
        for now in (
            datetime(2026, 8, 10, 8, 59, 59),
            datetime(2026, 8, 10, 13, 29, 59),
            datetime(2026, 8, 10, 13, 30, 0),
        ):
            with self.subTest(now=now.time()):
                self.assertEqual("ALLOWED", self.guard(now=now)["reason"])

    def test_manual_restart_admission_is_independent_of_regular_or_ats_window(self) -> None:
        config = dict(self.config)
        config.update(
            {
                "operation_mode": "CONTINUOUS",
                "ats_enabled": True,
                "ats_sessions": ["NXT"],
            }
        )
        for now in (
            datetime(2026, 8, 10, 8, 59, 59),
            datetime(2026, 8, 10, 14, 0, 0),
            datetime(2026, 8, 10, 15, 20, 0),
        ):
            with self.subTest(now=now.time()):
                self.assertEqual(
                    "ALLOWED",
                    self.guard(now=now, config=config)["reason"],
                )

    def test_today_normal_ended_does_not_block_per_stock_restart(self) -> None:
        allowed = self.guard(
            operation_state={
                "operation_date": "2026-08-10",
                "operation_status": "NORMAL_ENDED",
            }
        )
        self.assertTrue(allowed["allowed"])

        allowed = self.guard(
            operation_state={
                "operation_date": "2026-08-09",
                "operation_status": "NORMAL_ENDED",
            }
        )
        self.assertTrue(allowed["allowed"])

    def test_manual_ats_restart_does_not_consult_order_time_status(self) -> None:
        config = dict(self.config, operation_mode="CONTINUOUS")
        state = dict(
            self.state,
            manual_ats_selection={"selected_sessions": ["extra1"]},
        )
        with patch.object(
            run_control,
            "canonical_stock_trading_time_status",
            return_value={
                "evaluable": True,
                "active": True,
                "mode": "CONTINUOUS",
                "reason": "ACTIVE_ATS",
            },
        ) as time_status:
            result = self.guard(config=config, state=state)

        self.assertTrue(result["allowed"])
        time_status.assert_not_called()

        with patch.object(
            run_control,
            "canonical_stock_trading_time_status",
            return_value={
                "evaluable": True,
                "active": False,
                "mode": "CONTINUOUS",
                "reason": "OUTSIDE_OPERATION_TIME",
            },
        ):
            result = self.guard(config=config, state=state)
        self.assertTrue(result["allowed"])
        self.assertEqual("ALLOWED", result["reason"])

    def test_holding_and_side_pending_orders_block(self) -> None:
        holding = dict(self.state, holding_qty=1)
        self.assertEqual("HOLDING_EXISTS", self.guard(state=holding)["reason"])

        for side, expected in (("BUY", "PENDING_BUY"), ("SELL", "PENDING_SELL")):
            with self.subTest(side=side):
                (self.stock_dir / "orders.json").write_text(
                    json.dumps(
                        {
                            "orders": [
                                {
                                    "status": "BROKER_ACCEPTED",
                                    "side": side,
                                    "pending_qty": 1,
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(expected, self.guard()["reason"])
        (self.stock_dir / "orders.json").unlink()

    def test_pending_cancel_and_active_close_evidence_block(self) -> None:
        self.queue_path.write_text(
            json.dumps(
                {
                    "orders": [
                        {
                            "id": "cancel-1",
                            "stock_code": "005930",
                            "status": "CANCEL_REQUESTED",
                            "order_action": "CANCEL",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual("PENDING_CANCEL", self.guard()["reason"])

        self.queue_path.write_text('{"orders": []}', encoding="utf-8")
        self.assertTrue(
            self.guard(state=dict(self.state, status="EARLY_CLOSE"))["allowed"]
        )

        for updates in (
            {
                "status": "EARLY_CLOSING",
                "early_close_requested_at": "2026-08-10 09:50:00",
                "early_close_source": "routine_context_menu",
            },
            {"liquidation_policy_forced": True},
            {"close_routine_final_sell_ordered": True},
        ):
            with self.subTest(updates=updates):
                state = dict(self.state)
                state.update(updates)
                self.assertEqual(
                    "CLOSE_LIQUIDATION_ACTIVE", self.guard(state=state)["reason"]
                )

    def test_successful_backend_restart_restores_canonical_permission_atomically(self) -> None:
        (self.stock_dir / "config.json").write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
        )
        state_without_legacy_permissions = dict(self.state)
        state_without_legacy_permissions.pop("buy_enabled")
        state_without_legacy_permissions.pop("sell_enabled")
        (self.stock_dir / "state.json").write_text(
            json.dumps(state_without_legacy_permissions, ensure_ascii=False),
            encoding="utf-8",
        )
        target = (self.stock_dir, "005930", "삼성전자")
        window = _PersistingStartWindow(target)

        with (
            patch.object(run_control, "current_datetime", return_value=self.NOW),
            patch.object(run_control, "ORDER_QUEUE_PATH", self.queue_path),
            patch.object(run_control, "read_operation_state", return_value=self.operation_state),
            patch.object(run_control, "write_global_operation_running_state") as global_write,
            patch.object(run_control, "append_changelog"),
            patch.object(run_control, "append_production_event"),
            patch.object(status_ops, "append_stock_log"),
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                window,
                request_scope=run_control.START_REQUEST_SINGLE,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(1, len(window.recalculate_calls))
        metadata = window.recalculate_calls[0]
        self.assertEqual("RUNNING", metadata["start_policy_status"])
        self.assertIs(metadata["trade_enabled"], True)
        self.assertNotIn("buy_enabled", metadata)
        self.assertNotIn("sell_enabled", metadata)
        saved = json.loads(
            (self.stock_dir / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual("RUNNING", saved["status"])
        self.assertIs(saved["trade_enabled"], True)
        self.assertNotIn("buy_enabled", saved)
        self.assertNotIn("sell_enabled", saved)
        self.assertEqual(1, window.state_write_count)
        global_write.assert_called_once()

    def test_normal_ended_blocks_global_button_but_allows_context_restart(self) -> None:
        (self.stock_dir / "config.json").write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
        )
        (self.stock_dir / "state.json").write_text(
            json.dumps(self.state, ensure_ascii=False), encoding="utf-8"
        )
        target = (self.stock_dir, "005930", "삼성전자")
        ended = {
            "operation_date": "2026-08-10",
            "operation_status": "NORMAL_ENDED",
        }

        window = _StartWindow(target)
        with (
            patch.object(run_control, "current_datetime", return_value=self.NOW),
            patch.object(run_control, "read_operation_state", return_value=ended),
            patch.object(run_control, "write_global_operation_running_state") as write,
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                window,
                request_scope=run_control.START_REQUEST_SINGLE,
                source="auto_trade_global_start_button",
            )
        self.assertFalse(result["ok"])
        self.assertEqual("NORMAL_ENDED", result["reason"])
        self.assertEqual([], window.recalculate_calls)
        write.assert_not_called()

        for source in ("auto_trade_context_menu", "main_monitoring_window"):
            with self.subTest(source=source):
                restart_window = _PersistingStartWindow(target)
                (self.stock_dir / "state.json").write_text(
                    json.dumps(self.state, ensure_ascii=False),
                    encoding="utf-8",
                )
                with (
                    patch.object(run_control, "current_datetime", return_value=self.NOW),
                    patch.object(run_control, "ORDER_QUEUE_PATH", self.queue_path),
                    patch.object(run_control, "read_operation_state", return_value=ended),
                    patch.object(run_control, "write_global_operation_running_state") as write,
                    patch.object(run_control, "append_changelog"),
                    patch.object(run_control, "append_production_event"),
                    patch.object(status_ops, "append_stock_log"),
                ):
                    result = run_control.auto_trade_start_selected_auto_trades(
                        restart_window,
                        request_scope=run_control.START_REQUEST_SINGLE,
                        source=source,
                    )
                self.assertTrue(result["ok"])
                self.assertEqual(1, len(restart_window.recalculate_calls))
                write.assert_called_once()

    def test_multiple_targets_keep_partial_success_and_do_not_mutate_blocked_stock(self) -> None:
        first_target = (self.stock_dir, "005930", "삼성전자")
        (self.stock_dir / "config.json").write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
        )
        (self.stock_dir / "state.json").write_text(
            json.dumps(self.state, ensure_ascii=False), encoding="utf-8"
        )

        blocked_dir = self.root / "000660_SK하이닉스"
        blocked_dir.mkdir()
        blocked_config = dict(
            self.config,
            assigned_routine_instance_id="instance-b",
            routine_instance_name="루틴 B",
        )
        blocked_state = dict(self.state, holding_qty=2)
        (blocked_dir / "config.json").write_text(
            json.dumps(blocked_config, ensure_ascii=False), encoding="utf-8"
        )
        (blocked_dir / "state.json").write_text(
            json.dumps(blocked_state, ensure_ascii=False), encoding="utf-8"
        )
        second_target = (blocked_dir, "000660", "SK하이닉스")
        window = _StartWindow([first_target, second_target])

        with (
            patch.object(run_control, "current_datetime", return_value=self.NOW),
            patch.object(run_control, "ORDER_QUEUE_PATH", self.queue_path),
            patch.object(run_control, "read_operation_state", return_value=self.operation_state),
            patch.object(run_control, "write_global_operation_running_state") as global_write,
            patch.object(run_control, "append_changelog"),
            patch.object(run_control, "append_production_event"),
        ):
            result = run_control.auto_trade_start_selected_auto_trades(window)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual(("000660 SK하이닉스",), result["failed"])
        self.assertIn("HOLDING_EXISTS", result["internal_reason"])
        self.assertEqual(1, len(window.recalculate_calls))
        saved_blocked = json.loads(
            (blocked_dir / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(blocked_state, saved_blocked)
        global_write.assert_called_once_with(participant_stock_codes=["005930"])


if __name__ == "__main__":
    unittest.main()
