from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui_auto_trade_runtime as auto_trade_runtime
import gui_auto_trade_policy as auto_trade_policy
import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_timer as auto_trade_timer
from gui_auto_trade_utils import auto_trade_unregister_category, mark_pending_order_integrity_review_required
from runtime_io import read_json_dict


class AutoTradeStatusRecalculationPipelineTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        routines_dir = root / "routines"
        routine_dir = routines_dir / "Strategy"
        routine_dir.mkdir(parents=True)

        stocks_dir = root / "stocks"
        stock_dir = stocks_dir / "111111_Stock"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "routine": "Strategy",
                    "operation_mode": "CONTINUOUS",
                }
            ),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "holding_qty": 0,
                }
            ),
            encoding="utf-8",
        )
        (stock_dir / "orders.json").write_text("[]", encoding="utf-8")
        return routines_dir, stocks_dir, stock_dir

    def _window(self, stock_dirs=()):
        window = SimpleNamespace()
        window.all_runtime_stock_dirs = lambda: list(stock_dirs)
        window.update_stock_status = (
            lambda stock_dir, code, name, status, metadata, log_suffix:
            status_ops.auto_trade_update_stock_status(
                window,
                stock_dir,
                code,
                name,
                status,
                metadata,
                log_suffix,
            )
        )
        window.recalculate_stock_status_by_operation_policy = (
            lambda stock_dir, code, name, reason, silent_unchanged=False:
            status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                window,
                stock_dir,
                code,
                name,
                reason,
                silent_unchanged=silent_unchanged,
            )
        )
        return window

    def test_central_stock_without_current_policy_transition_remains_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            window = self._window([stock_dir])
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                result = status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    "test reconciliation",
                )

            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual(
            {"changed": 0, "unchanged": 1, "protected": 0, "failed": 0},
            result,
        )
        self.assertEqual("RUNNING", state["status"])

    def test_emergency_status_precedes_disabled_trade_display(self) -> None:
        state = {
            "status": "EMERGENCY_STOPPED",
            "trade_enabled": False,
            "holding_qty": 0,
        }

        display_status = (
            auto_trade_policy.auto_trade_setting_display_status_for_current_session(
                state,
                {"operation_mode": "SCHEDULED"},
                holding_qty=0,
                current_session_trade_started=False,
                persisted_trade_started=False,
            )
        )

        self.assertEqual("긴급정지", display_status)

    def test_stopped_state_allows_mode_change_and_unregister(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state["status"] = "STOPPED"
            state["trade_enabled"] = False
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = self._window([stock_dir])
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "current_datetime", return_value=datetime(2026, 7, 25, 18, 0)),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                mode_changed = status_ops.auto_trade_update_stock_operation_mode(
                    window,
                    stock_dir,
                    "111111",
                    "Stock",
                    "SCHEDULED",
                    {
                        "start_time": "09:00:00",
                        "end_buy_time": "13:30:00",
                    },
                )
                unregister = auto_trade_unregister_category(
                    "Strategy",
                    stock_dir,
                    "111111",
                    "Stock",
                )

            config = read_json_dict(stock_dir / "config.json")

        self.assertTrue(mode_changed)
        self.assertEqual("SCHEDULED", config["operation_mode"])
        self.assertEqual("immediate", unregister["category"])
        self.assertEqual(["Strategy: 보유·미체결 없음"], unregister["reasons"])

    def test_unregister_policy_ignores_stale_active_status_and_blocks_emergency(self) -> None:
        scenarios = {
            "RUNNING": ("immediate", "Strategy: 보유·미체결 없음"),
            "STARTED": ("immediate", "Strategy: 보유·미체결 없음"),
            "AUTO": ("immediate", "Strategy: 보유·미체결 없음"),
            "TRADING": ("immediate", "Strategy: 보유·미체결 없음"),
            "SELL_ONLY": ("immediate", "Strategy: 보유·미체결 없음"),
            "EMERGENCY_STOP": ("blocked", "Strategy: 긴급정지 종목입니다."),
        }
        for raw_status, (expected_category, expected_reason) in scenarios.items():
            with self.subTest(raw_status=raw_status), tempfile.TemporaryDirectory() as temp:
                _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
                state_path = stock_dir / "state.json"
                state = read_json_dict(state_path)
                state["status"] = raw_status
                state["holding_qty"] = 0
                state["buy_pending_qty"] = 0
                state["sell_pending_qty"] = 0
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

                unregister = auto_trade_unregister_category(
                    "Strategy",
                    stock_dir,
                    "111111",
                    "Stock",
                )

            self.assertEqual(expected_category, unregister["category"])
            self.assertEqual([expected_reason], unregister["reasons"])

    def test_unregister_policy_blocks_review_required_as_review_management(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state["status"] = "REVIEW_REQUIRED"
            state["holding_qty"] = 0
            state["buy_pending_qty"] = 0
            state["sell_pending_qty"] = 0
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            unregister = auto_trade_unregister_category(
                "Strategy",
                stock_dir,
                "111111",
                "Stock",
            )

        self.assertEqual("blocked", unregister["category"])
        self.assertEqual(["Strategy: 검토관리 종목입니다."], unregister["reasons"])

    def test_unregister_policy_reports_pending_integrity_without_review_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state.update(
                {
                    "status": "STOPPED",
                    "holding_qty": 0,
                    "pending_order": True,
                    "pending_qty": 5,
                    "buy_pending_qty": 0,
                    "sell_pending_qty": 0,
                }
            )
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (stock_dir / "orders.json").write_text("[]", encoding="utf-8")

            unregister = auto_trade_unregister_category(
                "Strategy",
                stock_dir,
                "111111",
                "Stock",
            )
            saved_state = read_json_dict(state_path)

        self.assertEqual("blocked", unregister["category"])
        self.assertEqual(
            ["Strategy: 처리할 수 없는 종목입니다.\n검토관리에서 확인하세요."],
            unregister["reasons"],
        )
        self.assertEqual("STOPPED", saved_state["status"])
        self.assertFalse(bool(saved_state.get("review_required")))

    def test_unregister_policy_reports_unknown_pending_side_without_review_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state.update({"status": "STOPPED", "holding_qty": 0})
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (stock_dir / "orders.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "status": "OPEN",
                                "side": "",
                                "order_qty": 5,
                                "filled_qty": 0,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            unregister = auto_trade_unregister_category(
                "Strategy",
                stock_dir,
                "111111",
                "Stock",
            )
            saved_state = read_json_dict(state_path)

        self.assertEqual("blocked", unregister["category"])
        self.assertEqual(
            ["Strategy: 처리할 수 없는 종목입니다.\n검토관리에서 확인하세요."],
            unregister["reasons"],
        )
        self.assertEqual("STOPPED", saved_state["status"])
        self.assertFalse(bool(saved_state.get("review_required")))

    def test_unregister_policy_keeps_normal_buy_pending_as_policy_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state["status"] = "STOPPED"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (stock_dir / "orders.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "status": "OPEN",
                                "side": "BUY",
                                "order_qty": 5,
                                "filled_qty": 2,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            unregister = auto_trade_unregister_category("Strategy", stock_dir, "111111", "Stock")
            saved_state = read_json_dict(stock_dir / "state.json")

        self.assertEqual("blocked", unregister["category"])
        self.assertEqual(["Strategy: 매수미결 3"], unregister["reasons"])
        self.assertNotEqual("REVIEW_REQUIRED", saved_state["status"])

    def test_unregister_policy_keeps_normal_sell_pending_as_policy_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state["status"] = "STOPPED"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (stock_dir / "orders.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "status": "OPEN",
                                "side": "SELL",
                                "order_qty": 4,
                                "filled_qty": 1,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            unregister = auto_trade_unregister_category("Strategy", stock_dir, "111111", "Stock")
            saved_state = read_json_dict(stock_dir / "state.json")

        self.assertEqual("blocked", unregister["category"])
        self.assertEqual(["Strategy: 매도미결 3"], unregister["reasons"])
        self.assertNotEqual("REVIEW_REQUIRED", saved_state["status"])

    def test_unregister_policy_has_no_integrity_error_without_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state["status"] = "STOPPED"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            unregister = auto_trade_unregister_category("Strategy", stock_dir, "111111", "Stock")
            saved_state = read_json_dict(stock_dir / "state.json")

        self.assertEqual("immediate", unregister["category"])
        self.assertEqual(["Strategy: 보유·미체결 없음"], unregister["reasons"])
        self.assertNotEqual("REVIEW_REQUIRED", saved_state["status"])

    def test_unregister_policy_blocks_holding_or_pending_without_mutating_files(self) -> None:
        scenarios = [
            ("holding", {"holding_qty": 5}, [], "Strategy: 보유 5"),
            (
                "buy_pending",
                {},
                [{"status": "OPEN", "side": "BUY", "order_qty": 5, "filled_qty": 2}],
                "Strategy: 매수미결 3",
            ),
            (
                "sell_pending",
                {},
                [{"status": "OPEN", "side": "SELL", "order_qty": 4, "filled_qty": 1}],
                "Strategy: 매도미결 3",
            ),
        ]
        for label, state_updates, orders, expected_reason in scenarios:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
                state_path = stock_dir / "state.json"
                orders_path = stock_dir / "orders.json"
                state = read_json_dict(state_path)
                state.update({"status": "STOPPED", **state_updates})
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                orders_path.write_text(
                    json.dumps({"orders": orders}, ensure_ascii=False),
                    encoding="utf-8",
                )
                before_state = state_path.read_text(encoding="utf-8")
                before_orders = orders_path.read_text(encoding="utf-8")

                unregister = auto_trade_unregister_category("Strategy", stock_dir, "111111", "Stock")

                self.assertEqual("blocked", unregister["category"])
                self.assertEqual([expected_reason], unregister["reasons"])
                self.assertEqual(before_state, state_path.read_text(encoding="utf-8"))
                self.assertEqual(before_orders, orders_path.read_text(encoding="utf-8"))
                self.assertFalse((stock_dir / "orders_archive.json").exists())

    def test_pending_integrity_review_required_is_idempotent_for_same_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _routines_dir, _stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state.update(
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_reason": "PENDING_ORDER_DATA_INTEGRITY: LEGACY_PENDING_SUMMARY_ONLY",
                }
            )
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            with (
                patch("gui_auto_trade_utils.write_state_json") as writer,
                patch("gui_auto_trade_utils.append_stock_log") as stock_log,
            ):
                ok = mark_pending_order_integrity_review_required(
                    "Strategy",
                    stock_dir,
                    "111111",
                    "Stock",
                    ["LEGACY_PENDING_SUMMARY_ONLY"],
                    source="test",
                )

        self.assertTrue(ok)
        writer.assert_not_called()
        stock_log.assert_not_called()

    def test_first_timer_tick_before_recovery_keeps_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            base_window = self._window([stock_dir])

            class RecoveryBlockedWindow:
                def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                    return False

                def update_startup_recovery_controls(self) -> None:
                    self.update_startup_recovery_controls_mock()

            window = RecoveryBlockedWindow()
            window.__dict__.update(base_window.__dict__)
            window.startup_recovery_session_ready = Mock(return_value=False)
            window.isVisible = lambda: True
            window.current_time_policy_minute_key = lambda: "2026-07-25 18:00"
            window._last_time_policy_minute_key = ""
            window.capture_stock_table_view_state = lambda: (set(), 0)
            window.refresh_all = Mock()
            window.restore_stock_table_view_state = Mock()
            window.statusBarMessage = Mock()
            window.parent = lambda: SimpleNamespace(refresh_all=Mock())
            window.recalculate_all_status_by_operation_policy = (
                lambda reason, silent_unchanged=False, write_changelog_when_unchanged=True:
                status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    reason,
                    silent_unchanged,
                    write_changelog_when_unchanged,
                )
            )

            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
                patch.object(auto_trade_timer, "probe_all_enabled_routine_stocks_once", None),
            ):
                auto_trade_timer.auto_trade_run_operation_cycle(window)

            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual("RUNNING", state["status"])
        self.assertEqual("", window._last_time_policy_minute_key)
        window.refresh_all.assert_not_called()
        window.startup_recovery_session_ready.assert_called_once_with(refresh=True)

    def test_timer_no_target_tail_does_not_fallback_to_batch_probe(self) -> None:
        class RecoveryReadyWindow:
            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return True

        window = RecoveryReadyWindow()
        window.isVisible = lambda: True
        window.current_time_policy_minute_key = lambda: "2026-07-25 18:01"
        window._last_time_policy_minute_key = ""
        window.recalculate_all_status_by_operation_policy = Mock(
            return_value={"changed": 0, "failed": 0}
        )
        window.capture_stock_table_view_state = lambda: (set(), 0)
        window.refresh_all = Mock()
        window.restore_stock_table_view_state = Mock()
        window.statusBarMessage = Mock()
        window.parent = lambda: SimpleNamespace(refresh_all=Mock())
        window.rebind_startup_recovery_after_trusted_runtime_update = Mock(
            return_value=True
        )
        window.current_selected_routine_name = lambda: ""
        probe = Mock(
            return_value={
                "checked": 2,
                "logged": 0,
                "error": 0,
                "skip": 0,
                "queued": 0,
            }
        )
        pipeline = Mock(return_value={})

        with (
            patch.object(auto_trade_timer, "probe_all_enabled_routine_stocks_once", probe),
            patch.object(auto_trade_timer, "_process_pending_signal_pipeline", pipeline),
            patch.object(auto_trade_timer, "consume_pending_routine_signals_dry_run", None),
            patch.object(
                auto_trade_timer,
                "auto_trade_current_time_policy_minute_key",
                return_value="2026-07-25 18:01",
            ),
            patch.object(
                auto_trade_timer,
                "auto_trade_continue_pending_close_liquidations",
                return_value={"processed": 0, "blocked": 0},
            ),
            patch.object(
                auto_trade_timer,
                "auto_trade_continue_pending_manual_ats_liquidations",
                return_value={"processed": 0, "failed": 0},
            ),
            patch("execution_universe.all_registered_stock_dirs", return_value=[]),
        ):
            auto_trade_timer.auto_trade_run_operation_cycle(window)

        probe.assert_not_called()
        pipeline.assert_called_once_with(window)
        self.assertEqual("", window.current_selected_routine_name())

    def test_missing_runtime_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            (stock_dir / "state.json").unlink()
            window = self._window([stock_dir])
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                result = status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    "test reconciliation",
                )

        self.assertEqual(1, result["failed"])
        self.assertFalse((stock_dir / "state.json").exists())

    def test_write_without_matching_read_back_is_reported_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            routines_dir, stocks_dir, stock_dir = self._fixture(Path(temp))
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            state["signal_probe_only"] = True
            state_path.write_text(json.dumps(state), encoding="utf-8")
            window = self._window([stock_dir])
            with (
                patch.object(status_ops, "ROUTINES_DIR", routines_dir),
                patch.object(auto_trade_runtime, "CENTRAL_STOCKS_DIR", stocks_dir),
                patch("runtime_stock_state_mutation.write_state_json", return_value=True),
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                result = status_ops.auto_trade_recalculate_all_status_by_operation_policy(
                    window,
                    "test reconciliation",
                )
            state = read_json_dict(stock_dir / "state.json")

        self.assertEqual(1, result["failed"])
        self.assertEqual("RUNNING", state["status"])


if __name__ == "__main__":
    unittest.main()
