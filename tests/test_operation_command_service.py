from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from operation_command_service import (
    COMMAND_INDIVIDUAL_LIQUIDATION,
    COMMAND_MANUAL_ATS_LIQUIDATION,
    INDIVIDUAL_LIQUIDATION_REQUEST_KEY,
    INDIVIDUAL_LIQUIDATION_STATUS_REQUESTED,
    EarlyCloseCompatibility,
    IndividualLiquidationOverride,
    MANUAL_ATS_LIQUIDATION_REQUEST_KEY,
    MANUAL_ATS_LIQUIDATION_STATUS_REQUESTED,
    ManualAtsLiquidationOverride,
    MODE_CARRY_OVER,
    MODE_EARLY_CLOSE,
    MODE_NORMAL,
    OperationCommandRequest,
    OperationCommandResult,
    OperationCommandService,
    RESULT_FAILED,
    RESULT_PARTIAL_SUCCESS,
    RESULT_SUCCESS,
    SCOPE_ROUTINE_INSTANCE,
    SCOPE_STOCK,
    STOCK_APPLIED,
    STOCK_FAILED,
    STOCK_IGNORED_DUPLICATE,
    STOCK_IGNORED_STALE,
    StockOperationCommandResult,
)
from gui_auto_trade_policy import (
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_display_status,
    auto_trade_setting_display_status_for_current_session,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_liquidation_text,
    auto_trade_setting_method_text,
    effective_liquidation_policy_for_config,
)


COMMAND_ID = UUID("65e91e64-7f45-4120-a7d9-1cf18bfe0ccd")


class OperationCommandServiceTest(unittest.TestCase):
    def _service(self, root: Path, **kwargs) -> OperationCommandService:
        return OperationCommandService(
            root,
            now_factory=lambda: datetime(2026, 7, 19, 10, 30, tzinfo=timezone.utc),
            id_factory=lambda: COMMAND_ID,
            **kwargs,
        )

    @staticmethod
    def _stock(
        root: Path,
        folder: str,
        *,
        instance_id: str = "instance-1",
        state: dict | None = None,
    ) -> Path:
        path = root / "stocks" / folder
        path.mkdir(parents=True)
        (path / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": instance_id}, ensure_ascii=False),
            encoding="utf-8",
        )
        (path / "state.json").write_text(
            json.dumps(state if state is not None else {"status": "RUNNING", "trade_enabled": True}),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _state(path: Path) -> dict:
        return json.loads((path / "state.json").read_text(encoding="utf-8"))

    def test_stock_mode_is_atomically_saved_and_read_back_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_EARLY_CLOSE,
                    "monitoring_window",
                    occurred_at="2026-07-19T10:29:59+09:00",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(STOCK_APPLIED, result.stock_results[0].status)
        self.assertEqual(1, result.stock_results[0].sequence)
        self.assertEqual(MODE_EARLY_CLOSE, state["operation_command_mode"])
        self.assertEqual(1, state["operation_sequence"])
        self.assertEqual(str(COMMAND_ID), state["operation_command_id"])
        self.assertEqual("monitoring_window", state["operation_command_source"])
        self.assertEqual("EARLY_CLOSE", state["status"])
        self.assertFalse(state["liquidation_policy_forced"])

    def test_early_close_preserves_operation_start_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "trade_started_at": "2026-07-19 09:00:00",
                    "buy_enabled": False,
                    "sell_enabled": True,
                },
            )
            result = self._service(root).apply_early_close(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_EARLY_CLOSE,
                    "monitoring_window",
                ),
                EarlyCloseCompatibility(
                    method="시장가",
                    has_close_progress_quantity=True,
                ),
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(
            "2026-07-19 09:00:00",
            state["trade_started_at"],
        )
        self.assertFalse(state["buy_enabled"])
        self.assertTrue(state["sell_enabled"])

    def test_early_close_no_target_cleanup_is_committed_by_command_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            result = self._service(root).apply_early_close(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_EARLY_CLOSE,
                    "monitoring_window",
                ),
                EarlyCloseCompatibility(
                    method="루틴",
                    has_close_progress_quantity=False,
                ),
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual("WAIT_BUY", state["status"])
        self.assertTrue(state["trade_enabled"])
        self.assertEqual("EARLY_CLOSE_NO_TARGET", state["operation_notice"])
        self.assertEqual("", state["early_close_requested_at"])
        self.assertEqual("", state["early_close_method"])
        self.assertEqual({}, state["early_close_policy"])

    def test_duplicate_command_id_does_not_increment_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")
            service = self._service(root)
            request = OperationCommandRequest(
                SCOPE_STOCK,
                "005930",
                MODE_NORMAL,
                "settings_window",
                command_id="same-command",
            )

            first = service.apply(request)
            second = service.apply(request)
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, first.status)
        self.assertEqual(RESULT_SUCCESS, second.status)
        self.assertEqual(STOCK_IGNORED_DUPLICATE, second.stock_results[0].status)
        self.assertEqual(1, state["operation_sequence"])

    def test_modes_replace_future_policy_without_clearing_order_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={
                    "status": "EARLY_CLOSE",
                    "trade_enabled": True,
                    "close_routine_final_sell_ordered": True,
                    "close_routine_final_sell_ordered_at": "2026-07-19 10:00:00",
                },
            )
            service = self._service(root)

            carry = service.apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_CARRY_OVER,
                    "auto_close_timer",
                    command_id="carry-command",
                )
            )
            normal = service.apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_NORMAL,
                    "monitoring_window",
                    command_id="normal-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, carry.status)
        self.assertEqual(RESULT_SUCCESS, normal.status)
        self.assertEqual(2, state["operation_sequence"])
        self.assertEqual(MODE_NORMAL, state["operation_command_mode"])
        self.assertEqual("", state["early_close_requested_at"])
        self.assertTrue(state["close_routine_final_sell_ordered"])
        self.assertEqual("2026-07-19 10:00:00", state["close_routine_final_sell_ordered_at"])

    def test_routine_instance_targets_are_sorted_and_processed_one_by_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_b = self._stock(root, "222222_B", instance_id="instance-9")
            stock_a = self._stock(root, "111111_A", instance_id="instance-9")

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_ROUTINE_INSTANCE,
                    "instance-9",
                    MODE_EARLY_CLOSE,
                    "monitoring_window",
                    command_id="routine-command",
                )
            )
            state_a = self._state(stock_a)
            state_b = self._state(stock_b)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(["111111", "222222"], [item.stock_id for item in result.stock_results])
        self.assertEqual(1, state_a["operation_sequence"])
        self.assertEqual(1, state_b["operation_sequence"])

    def test_routine_partial_write_failure_is_reported_without_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_a = self._stock(root, "111111_A", instance_id="instance-9")
            stock_b = self._stock(root, "222222_B", instance_id="instance-9")

            from runtime_atomic_writer import write_json_atomic

            def writer(path, data):
                if Path(path).parent.name == "222222_B":
                    return {"status": "ERROR", "error": "injected failure"}
                return write_json_atomic(path, data)

            result = self._service(root, atomic_writer=writer).apply(
                OperationCommandRequest(
                    SCOPE_ROUTINE_INSTANCE,
                    "instance-9",
                    MODE_NORMAL,
                    "monitoring_window",
                    command_id="partial-command",
                )
            )
            state_a = self._state(stock_a)
            state_b = self._state(stock_b)

        self.assertEqual(RESULT_PARTIAL_SUCCESS, result.status)
        self.assertEqual([STOCK_APPLIED, STOCK_FAILED], [item.status for item in result.stock_results])
        self.assertEqual(1, state_a["operation_sequence"])
        self.assertNotIn("operation_sequence", state_b)

    def test_read_back_mismatch_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._stock(root, "005930_Samsung")

            def lying_writer(_path, _data):
                return {"status": "OK", "written": True}

            result = self._service(root, atomic_writer=lying_writer).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_NORMAL,
                    "settings_window",
                )
            )

        self.assertEqual(RESULT_FAILED, result.status)
        self.assertIn("read-back verification failed", result.stock_results[0].error)

    def test_newer_read_back_command_is_ignored_as_stale_not_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            from runtime_atomic_writer import write_json_atomic

            def newer_writer(path, data):
                newer = dict(data)
                newer["operation_sequence"] = int(data["operation_sequence"]) + 1
                newer["operation_command_id"] = "newer-command"
                newer["operation_command_mode"] = MODE_CARRY_OVER
                return write_json_atomic(path, newer)

            result = self._service(root, atomic_writer=newer_writer).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_NORMAL,
                    "settings_window",
                    command_id="older-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(STOCK_IGNORED_STALE, result.stock_results[0].status)
        self.assertEqual(2, result.stock_results[0].sequence)
        self.assertEqual("newer-command", state["operation_command_id"])
        self.assertEqual(MODE_CARRY_OVER, state["operation_command_mode"])

    def test_missing_instance_targets_fail_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._stock(root, "005930_Samsung", instance_id="other")

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_ROUTINE_INSTANCE,
                    "missing",
                    MODE_NORMAL,
                    "monitoring_window",
                )
            )

        self.assertEqual(RESULT_FAILED, result.status)
        self.assertEqual("routine instance has no assigned stocks", result.error)
        self.assertEqual((), result.stock_results)

    def test_corrupt_state_is_not_silently_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")
            state_path = stock / "state.json"
            state_path.write_text("{broken", encoding="utf-8")

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_NORMAL,
                    "settings_window",
                )
            )
            persisted = state_path.read_text(encoding="utf-8")

        self.assertEqual(RESULT_FAILED, result.status)
        self.assertEqual(STOCK_FAILED, result.stock_results[0].status)
        self.assertEqual("{broken", persisted)

    def test_same_stock_concurrent_commands_keep_monotonic_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")
            service = self._service(root)
            barrier = threading.Barrier(3)
            results = []

            def apply(command: str, command_id: str) -> None:
                barrier.wait()
                results.append(
                    service.apply(
                        OperationCommandRequest(
                            SCOPE_STOCK,
                            "005930",
                            command,
                            "concurrency_test",
                            command_id=command_id,
                        )
                    )
                )

            first = threading.Thread(target=apply, args=(MODE_EARLY_CLOSE, "command-a"))
            second = threading.Thread(target=apply, args=(MODE_CARRY_OVER, "command-b"))
            first.start()
            second.start()
            barrier.wait()
            first.join(timeout=2)
            second.join(timeout=2)
            state = self._state(stock)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(2, len(results))
        self.assertTrue(all(result.status == RESULT_SUCCESS for result in results))
        self.assertEqual([1, 2], sorted(result.stock_results[0].sequence for result in results))
        self.assertEqual(2, state["operation_sequence"])

    def test_routine_mixed_applied_stale_duplicate_and_failed_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_a = self._stock(root, "111111_A", instance_id="instance-mixed")
            stock_b = self._stock(root, "222222_B", instance_id="instance-mixed")
            stock_c = self._stock(
                root,
                "333333_C",
                instance_id="instance-mixed",
                state={
                    "status": "RUNNING",
                    "operation_sequence": 8,
                    "operation_command_id": "mixed-command",
                },
            )
            stock_d = self._stock(root, "444444_D", instance_id="instance-mixed")

            from runtime_atomic_writer import write_json_atomic

            def mixed_writer(path, data):
                stock_name = Path(path).parent.name
                if stock_name == "222222_B":
                    newer = dict(data)
                    newer["operation_sequence"] = int(data["operation_sequence"]) + 1
                    newer["operation_command_id"] = "newer-command"
                    return write_json_atomic(path, newer)
                if stock_name == "444444_D":
                    return {"status": "ERROR", "error": "injected failure"}
                return write_json_atomic(path, data)

            result = self._service(root, atomic_writer=mixed_writer).apply(
                OperationCommandRequest(
                    SCOPE_ROUTINE_INSTANCE,
                    "instance-mixed",
                    MODE_NORMAL,
                    "monitoring_window",
                    command_id="mixed-command",
                )
            )
            states = [self._state(path) for path in (stock_a, stock_b, stock_c, stock_d)]

        self.assertEqual(RESULT_PARTIAL_SUCCESS, result.status)
        self.assertEqual(
            [STOCK_APPLIED, STOCK_IGNORED_STALE, STOCK_IGNORED_DUPLICATE, STOCK_FAILED],
            [item.status for item in result.stock_results],
        )
        self.assertEqual(["111111", "222222", "333333", "444444"], [item.stock_id for item in result.stock_results])
        self.assertEqual(1, len(result.applied))
        self.assertEqual(2, len(result.ignored))
        self.assertEqual(1, len(result.failed))
        self.assertEqual(1, states[0]["operation_sequence"])
        self.assertEqual("newer-command", states[1]["operation_command_id"])
        self.assertEqual(8, states[2]["operation_sequence"])
        self.assertNotIn("operation_sequence", states[3])

    def test_read_back_failure_does_not_stop_later_routine_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_a = self._stock(root, "111111_A", instance_id="instance-readback")
            stock_b = self._stock(root, "222222_B", instance_id="instance-readback")

            from runtime_atomic_writer import write_json_atomic

            def writer(path, data):
                if Path(path).parent.name == "111111_A":
                    return {"status": "OK", "written": True}
                return write_json_atomic(path, data)

            result = self._service(root, atomic_writer=writer).apply(
                OperationCommandRequest(
                    SCOPE_ROUTINE_INSTANCE,
                    "instance-readback",
                    MODE_NORMAL,
                    "monitoring_window",
                    command_id="readback-command",
                )
            )
            state_a = self._state(stock_a)
            state_b = self._state(stock_b)

        self.assertEqual(RESULT_PARTIAL_SUCCESS, result.status)
        self.assertEqual([STOCK_FAILED, STOCK_APPLIED], [item.status for item in result.stock_results])
        self.assertNotIn("operation_sequence", state_a)
        self.assertEqual(1, state_b["operation_sequence"])

    def test_writer_exception_releases_stock_lock_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            from runtime_atomic_writer import write_json_atomic

            calls = 0

            def writer(path, data):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("injected writer exception")
                return write_json_atomic(path, data)

            service = self._service(root, atomic_writer=writer)
            first = service.apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_NORMAL,
                    "settings_window",
                    command_id="first-command",
                )
            )
            second = service.apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_CARRY_OVER,
                    "settings_window",
                    command_id="second-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_FAILED, first.status)
        self.assertEqual(RESULT_SUCCESS, second.status)
        self.assertEqual(1, state["operation_sequence"])
        self.assertEqual(MODE_CARRY_OVER, state["operation_command_mode"])

    def test_early_close_compatibility_fields_drive_existing_policy_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    MODE_EARLY_CLOSE,
                    "legacy_early_close",
                    command_id="compat-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertTrue(auto_trade_setting_early_close_requested(state))
        self.assertTrue(auto_trade_setting_close_routine_mode_active(state))
        self.assertEqual("legacy_early_close", state["early_close_source"])
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)

    def test_early_close_method_controls_direct_liquidation_permission(self) -> None:
        from routine_order_permission import canonical_routine_order_permission

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routine_stock = self._stock(root, "111111_Routine")
            market_stock = self._stock(root, "222222_Market")
            service = self._service(root)
            routine_result = service.apply_early_close(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "111111",
                    MODE_EARLY_CLOSE,
                    "monitoring_instance",
                    command_id="routine-command",
                ),
                EarlyCloseCompatibility(method="루틴"),
            )
            market_result = service.apply_early_close(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "222222",
                    MODE_EARLY_CLOSE,
                    "monitoring_instance",
                    command_id="market-command",
                ),
                EarlyCloseCompatibility(method="시장가"),
            )
            routine_state = self._state(routine_stock)
            market_state = self._state(market_stock)

        operation_state = {
            "operation_date": "2026-07-19",
            "operation_status": "RUNNING",
        }
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        now_dt = datetime(2026, 7, 19, 10, 0, 0)

        self.assertEqual(RESULT_SUCCESS, routine_result.status)
        self.assertEqual("조기마감", auto_trade_setting_display_status(routine_state["status"]))
        self.assertEqual("루틴", auto_trade_setting_method_text("조기마감", {}, routine_state))
        self.assertFalse(routine_state["liquidation_policy_forced"])
        self.assertEqual("", routine_state["liquidation_policy_reason"])
        self.assertTrue(
            canonical_routine_order_permission(
                state=routine_state,
                signal_type="BUY",
                config=config,
                operation_state=operation_state,
                now_dt=now_dt,
            )["allowed"]
        )
        self.assertTrue(
            canonical_routine_order_permission(
                state=routine_state,
                signal_type="SELL",
                config=config,
                operation_state=operation_state,
                now_dt=now_dt,
            )["allowed"]
        )

        compatibility_state = dict(
            routine_state,
            liquidation_policy_forced=True,
            liquidation_policy_reason="EARLY_CLOSE",
        )
        self.assertTrue(
            canonical_routine_order_permission(
                state=compatibility_state,
                signal_type="BUY",
                config=config,
                operation_state=operation_state,
                now_dt=now_dt,
            )["allowed"]
        )

        routine_state["close_routine_final_sell_ordered"] = True
        self.assertFalse(
            canonical_routine_order_permission(
                state=routine_state,
                signal_type="BUY",
                config=config,
                operation_state=operation_state,
                now_dt=now_dt,
            )["allowed"]
        )
        self.assertFalse(
            canonical_routine_order_permission(
                state=routine_state,
                signal_type="SELL",
                config=config,
                operation_state=operation_state,
                now_dt=now_dt,
            )["allowed"]
        )

        self.assertEqual(RESULT_SUCCESS, market_result.status)
        self.assertEqual("조기마감", auto_trade_setting_display_status(market_state["status"]))
        self.assertEqual("시장가", auto_trade_setting_method_text("조기마감", {}, market_state))
        self.assertTrue(market_state["liquidation_policy_forced"])
        self.assertEqual("EARLY_CLOSE", market_state["liquidation_policy_reason"])
        for signal_type in ("BUY", "SELL"):
            self.assertFalse(
                canonical_routine_order_permission(
                    state=market_state,
                    signal_type=signal_type,
                    config=config,
                    operation_state=operation_state,
                    now_dt=now_dt,
                )["allowed"]
            )

    def test_early_close_aliases_are_written_as_canonical_methods(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            market_stock = self._stock(root, "111111_Market")
            current_stock = self._stock(root, "222222_Current")
            service = self._service(root)

            market_result = service.apply_early_close(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "111111",
                    MODE_EARLY_CLOSE,
                    "context_menu",
                    command_id="market-command",
                ),
                EarlyCloseCompatibility(method="시장가즉시"),
            )
            current_result = service.apply_early_close(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "222222",
                    MODE_EARLY_CLOSE,
                    "context_menu",
                    command_id="current-command",
                ),
                EarlyCloseCompatibility(method="현재가즉시"),
            )
            market_state = self._state(market_stock)
            current_state = self._state(current_stock)

        self.assertEqual(RESULT_SUCCESS, market_result.status)
        self.assertEqual("시장가", market_state["early_close_method"])
        self.assertEqual("시장가", market_state["early_close_policy"]["method"])
        self.assertEqual(RESULT_SUCCESS, current_result.status)
        self.assertEqual("현재가", current_state["early_close_method"])
        self.assertEqual("현재가", current_state["early_close_policy"]["method"])

    def test_individual_liquidation_records_one_shot_override_without_changing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "operation_command_mode": MODE_NORMAL,
                    "operation_sequence": 2,
                },
            )

            result = self._service(root).apply_individual_liquidation(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_INDIVIDUAL_LIQUIDATION,
                    "auto_trade_setting_context_menu",
                    command_id="individual-1",
                ),
                IndividualLiquidationOverride("현재가", "15"),
            )
            state = self._state(stock)
            request = state[INDIVIDUAL_LIQUIDATION_REQUEST_KEY]
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(STOCK_APPLIED, result.stock_results[0].status)
        self.assertEqual(MODE_NORMAL, state["operation_command_mode"])
        self.assertEqual("individual-1", request["command_id"])
        self.assertEqual(3, request["operation_sequence"])
        self.assertEqual(INDIVIDUAL_LIQUIDATION_STATUS_REQUESTED, request["status"])
        self.assertEqual("현재가", request["method"])
        self.assertEqual("15", request["minutes_before_regular_close"])
        self.assertNotIn("individual_liquidation", config)

    def test_individual_liquidation_does_not_create_order_or_send_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            result = self._service(root).apply_individual_liquidation(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_INDIVIDUAL_LIQUIDATION,
                    "auto_trade_setting_context_menu",
                ),
                IndividualLiquidationOverride("시장가", "10"),
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertTrue(
            {
                "order_queue",
                "ORDER_QUEUED",
                "send_order",
                "send_order_status",
                "chejan",
                "broker_order_no",
            }.isdisjoint(state)
        )

    def test_manual_ats_liquidation_records_distinct_one_shot_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "operation_command_mode": MODE_NORMAL,
                    "operation_sequence": 4,
                },
            )

            result = self._service(root).apply_manual_ats_liquidation(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_MANUAL_ATS_LIQUIDATION,
                    "ATS_SETTINGS",
                    command_id="ats-command-1",
                ),
                ManualAtsLiquidationOverride(
                    sell_method="CURRENT_PRICE",
                    selected_ats_sessions=("extra1", "extra3"),
                    trade_date="2026-07-25",
                    program_session_id="program-session-1",
                ),
            )
            state = self._state(stock)
            request = state[MANUAL_ATS_LIQUIDATION_REQUEST_KEY]

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(STOCK_APPLIED, result.stock_results[0].status)
        self.assertEqual(MODE_NORMAL, state["operation_command_mode"])
        self.assertEqual(5, state["operation_sequence"])
        self.assertEqual("ats-command-1", request["command_id"])
        self.assertEqual(MANUAL_ATS_LIQUIDATION_STATUS_REQUESTED, request["status"])
        self.assertEqual("CURRENT_PRICE", request["sell_method"])
        self.assertEqual(["extra1", "extra3"], request["selected_ats_sessions"])
        self.assertEqual("2026-07-25", request["trade_date"])
        self.assertEqual("program-session-1", request["program_session_id"])
        self.assertNotIn(INDIVIDUAL_LIQUIDATION_REQUEST_KEY, state)

    def test_manual_ats_liquidation_result_status_is_read_back_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")
            service = self._service(root)
            service.apply_manual_ats_liquidation(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_MANUAL_ATS_LIQUIDATION,
                    "ATS_SETTINGS",
                    command_id="ats-command-2",
                ),
                ManualAtsLiquidationOverride("MARKET", ("extra1",)),
            )

            status_result = service.record_manual_ats_liquidation_status(
                str(stock),
                "ats-command-2",
                "SEND_CALL_ACCEPTED",
                order_id="ATS_ORDER_1",
            )
            request = self._state(stock)[MANUAL_ATS_LIQUIDATION_REQUEST_KEY]

        self.assertEqual(STOCK_APPLIED, status_result.status)
        self.assertEqual("SEND_CALL_ACCEPTED", request["status"])
        self.assertEqual("ATS_ORDER_1", request["order_id"])

    def test_manual_ats_liquidation_waiting_status_persists_cancel_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")
            service = self._service(root)
            service.apply_manual_ats_liquidation(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_MANUAL_ATS_LIQUIDATION,
                    "MANUAL_ATS_LIQUIDATION",
                    command_id="ats-command-waiting",
                ),
                ManualAtsLiquidationOverride("MARKET", ("extra1",)),
            )
            identities = [
                {
                    "order_queued_id": "source-queued-1",
                    "order_id": "source-order-1",
                    "broker_order_no": "broker-1",
                }
            ]
            waiting_result = service.record_manual_ats_liquidation_status(
                str(stock),
                "ats-command-waiting",
                "WAITING_CANCEL_CONFIRMATION",
                cancel_order_identities=identities,
                holding_readback={
                    "holding_checked_at": "2026-08-09T16:00:00+09:00",
                    "position_qty": 7,
                    "broker_holding_qty": 7,
                    "resolved_liquidation_qty": 7,
                    "reconciliation_result": "CONSISTENT",
                },
                cancel_readback={
                    "initial_holding_qty": 7,
                    "pending_order_count": 1,
                    "cancel_requested_count": 1,
                    "cancel_pending_count": 0,
                },
            )
            ready_result = service.record_manual_ats_liquidation_status(
                str(stock),
                "ats-command-waiting",
                "READY_TO_RESUME",
            )
            request = self._state(stock)[MANUAL_ATS_LIQUIDATION_REQUEST_KEY]

        self.assertEqual(STOCK_APPLIED, waiting_result.status)
        self.assertEqual(STOCK_APPLIED, ready_result.status)
        self.assertEqual("READY_TO_RESUME", request["status"])
        self.assertEqual(identities, request["cancel_order_identities"])
        self.assertEqual("MANUAL_ATS_LIQUIDATION", request["source"])
        self.assertEqual(7, request["resolved_liquidation_qty"])
        self.assertEqual("CONSISTENT", request["reconciliation_result"])
        self.assertEqual(1, request["pending_order_count"])
        self.assertEqual(1, request["cancel_requested_count"])

    def test_runtime_individual_liquidation_override_precedes_environment_policy(
        self,
    ) -> None:
        state = {
            INDIVIDUAL_LIQUIDATION_REQUEST_KEY: {
                "status": INDIVIDUAL_LIQUIDATION_STATUS_REQUESTED,
                "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "method": "현재가",
                "minutes_before_regular_close": "15",
            }
        }
        with patch(
            "gui_auto_trade_policy.read_operation_policy",
            return_value={
                "liquidation": {
                    "method": "시장가",
                    "minutes_before_regular_close": "5",
                }
            },
        ):
            policy, is_override = effective_liquidation_policy_for_config({}, state)

        self.assertTrue(is_override)
        self.assertEqual("현재가", policy["method"])
        self.assertEqual("15", policy["minutes_before_regular_close"])

    def test_legacy_config_override_is_not_an_execution_policy(self) -> None:
        config = {
            "individual_liquidation": {
                "enabled": True,
                "method": "현재가",
                "minutes_before_regular_close": "15",
            }
        }
        with patch(
            "gui_auto_trade_policy.read_operation_policy",
            return_value={
                "liquidation": {
                    "method": "시장가",
                    "minutes_before_regular_close": "5",
                }
            },
        ):
            policy, is_override = effective_liquidation_policy_for_config(config)

        self.assertFalse(is_override)
        self.assertEqual("시장가", policy["method"])
        self.assertEqual("5", policy["minutes_before_regular_close"])

    def test_completed_individual_liquidation_override_is_discarded(self) -> None:
        now_text = datetime.now().astimezone().isoformat(timespec="seconds")
        state = {
            INDIVIDUAL_LIQUIDATION_REQUEST_KEY: {
                "status": INDIVIDUAL_LIQUIDATION_STATUS_REQUESTED,
                "requested_at": now_text,
                "method": "현재가",
                "minutes_before_regular_close": "15",
            },
            "liquidation_completed_at": now_text,
        }
        with patch(
            "gui_auto_trade_policy.read_operation_policy",
            return_value={
                "liquidation": {
                    "method": "시장가",
                    "minutes_before_regular_close": "5",
                }
            },
        ):
            policy, is_override = effective_liquidation_policy_for_config({}, state)

        self.assertFalse(is_override)
        self.assertEqual("시장가", policy["method"])


class EarlyCloseProductionCallerTest(unittest.TestCase):
    class _MessageBox:
        Warning = 1
        Information = 2
        Question = 3
        AcceptRole = 4
        RejectRole = 5
        proceed = True
        instances = []

        def __init__(self, _parent=None) -> None:
            type(self).instances.append(self)
            self._proceed_button = None
            self._cancel_button = None

        def setIcon(self, _icon) -> None:
            pass

        def setWindowTitle(self, _title) -> None:
            pass

        def setText(self, _text) -> None:
            pass

        def addButton(self, _text, role):
            button = object()
            if role == self.AcceptRole:
                self._proceed_button = button
            else:
                self._cancel_button = button
            return button

        def setDefaultButton(self, _button) -> None:
            pass

        def exec_(self) -> int:
            return 0

        def clickedButton(self):
            return self._proceed_button if self.proceed else self._cancel_button

    @staticmethod
    def _window(selected) -> Mock:
        window = Mock()
        window.selected_stock_infos.return_value = selected
        window.current_selected_routine_name.return_value = "indicator_follow"
        parent = Mock()
        parent.kiwoom_api.is_connected.return_value = True
        window.parent.return_value = parent
        viewport = Mock()
        window.stock_table.viewport.return_value = viewport
        return window

    @staticmethod
    def _write_stock(
        root: Path,
        folder: str,
        holding_qty: int = 5,
        state: dict | None = None,
    ) -> tuple[Path, str, str]:
        stock_dir = root / "stocks" / folder
        stock_dir.mkdir(parents=True)
        code, name = folder.split("_", 1)
        state_data = state if state is not None else {"status": "RUNNING", "holding_qty": holding_qty}
        (stock_dir / "state.json").write_text(json.dumps(state_data), encoding="utf-8")
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        return stock_dir, code, name

    def test_cancel_does_not_create_or_call_command(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            window = self._window(selected)
            self._MessageBox.proceed = False

            with (
                patch("gui_auto_trade_close.QMessageBox", self._MessageBox),
                patch("gui_auto_trade_close.OperationCommandService") as service_type,
                patch("gui_auto_trade_close.pending_order_side_quantities", return_value=(0, 0)),
                patch("gui_auto_trade_close.auto_trade_setting_liquidation_phase_active", return_value=False),
            ):
                auto_trade_apply_selected_early_close(window, "시장가즉시")

        service_type.assert_not_called()
        window.update_stock_status.assert_not_called()
        window.statusBarMessage.assert_called_with("조기마감 취소")

    def test_invalid_selection_does_not_call_command_service(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        window = self._window([])
        self._MessageBox.proceed = True
        with (
            patch("gui_auto_trade_close.QMessageBox", self._MessageBox),
            patch("gui_auto_trade_close.OperationCommandService") as service_type,
        ):
            auto_trade_apply_selected_early_close(window, "루틴")

        service_type.assert_not_called()
        window.update_stock_status.assert_not_called()

    def test_disconnected_server_shows_login_toast_and_does_not_apply_backend(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            window = self._window(selected)
            window.parent.return_value.kiwoom_api.is_connected.return_value = False

            with (
                patch("gui_auto_trade_close.OperationCommandService") as service_type,
                patch("gui_auto_trade_close.show_toast") as show_toast,
            ):
                auto_trade_apply_selected_early_close(window, "루틴")

        service_type.assert_not_called()
        window.selected_stock_infos.assert_not_called()
        show_toast.assert_called_once_with(
            window,
            "키움 서버에 로그인되어 있지 않습니다.",
            duration_ms=2500,
        )
        window.statusBarMessage.assert_not_called()

    def test_disconnected_server_silent_mode_returns_login_message(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            window = self._window(selected)
            window.parent.return_value.kiwoom_api.is_connected.return_value = False

            with (
                patch("gui_auto_trade_close.OperationCommandService") as service_type,
                patch("gui_auto_trade_close.show_toast") as show_toast,
            ):
                result = auto_trade_apply_selected_early_close(
                    window,
                    "루틴",
                    show_error_dialog=False,
                    show_result_toast=False,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(
            "키움 서버에 로그인되어 있지 않습니다.",
            result["message"],
        )
        service_type.assert_not_called()
        show_toast.assert_not_called()

    def test_shared_early_close_backend_canonicalizes_direct_aliases(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        for alias, canonical in (
            ("시장가즉시", "시장가"),
            ("현재가즉시", "현재가"),
        ):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as temp:
                selected = [self._write_stock(Path(temp), "005930_Samsung")]
                window = self._window(selected)
                self._MessageBox.proceed = True
                command_result = OperationCommandResult(
                    RESULT_SUCCESS,
                    f"{canonical}-command",
                    (
                        StockOperationCommandResult(
                            "005930",
                            str(selected[0][0]),
                            STOCK_APPLIED,
                            1,
                        ),
                    ),
                )
                with (
                    patch("gui_auto_trade_close.QMessageBox", self._MessageBox),
                    patch(
                        "gui_auto_trade_close.pending_order_side_quantities",
                        return_value=(0, 0),
                    ),
                    patch(
                        "gui_auto_trade_close.auto_trade_setting_liquidation_phase_active",
                        return_value=False,
                    ),
                    patch("gui_auto_trade_close._production_recovery_gate", return_value=None),
                    patch(
                        "gui_auto_trade_close.evaluate_production_transition",
                        return_value=Mock(allowed=True),
                    ),
                    patch(
                        "gui_auto_trade_close.apply_close_intent",
                        return_value={"command_result": command_result},
                    ) as close_intent,
                    patch(
                        "gui_auto_trade_close._start_close_liquidation_execution",
                        return_value={"ok": True, "stage": "send_order"},
                    ) as execute,
                    patch(
                        "gui_auto_trade_close._persist_early_close_execution_result",
                        return_value=True,
                    ),
                    patch(
                        "gui_auto_trade_close.check_global_close_completion_after_durable_update",
                        return_value={"ok": True},
                    ),
                    patch("gui_auto_trade_close.append_changelog"),
                    patch("gui_auto_trade_close.append_stock_log"),
                    patch("gui_auto_trade_close.show_toast"),
                ):
                    auto_trade_apply_selected_early_close(window, alias)

                self.assertEqual(
                    canonical,
                    close_intent.call_args.kwargs["requested_policy"],
                )
                self.assertEqual(canonical, execute.call_args.kwargs["method"])

    def test_partial_failure_is_reported_and_direct_writer_is_not_called(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [
                self._write_stock(root, "111111_A"),
                self._write_stock(root, "222222_B"),
            ]
            window = self._window(selected)
            self._MessageBox.proceed = True
            service = Mock()
            service.apply_early_close.side_effect = [
                OperationCommandResult(
                    RESULT_SUCCESS,
                    "command-a",
                    (StockOperationCommandResult("111111", str(selected[0][0]), STOCK_APPLIED, 1),),
                ),
                OperationCommandResult(
                    RESULT_FAILED,
                    "command-b",
                    (StockOperationCommandResult("222222", str(selected[1][0]), STOCK_FAILED, 1, "write failed"),),
                ),
            ]

            with (
                patch("gui_auto_trade_close.QMessageBox", self._MessageBox),
                patch("gui_auto_trade_close.OperationCommandService", return_value=service),
                patch("gui_auto_trade_close.pending_order_side_quantities", return_value=(0, 0)),
                patch("gui_auto_trade_close.auto_trade_setting_liquidation_phase_active", return_value=False),
                patch(
                    "gui_auto_trade_close.evaluate_production_transition",
                    return_value=Mock(allowed=True),
                ),
                patch(
                    "gui_auto_trade_close._start_close_liquidation_execution",
                    return_value={"ok": True, "stage": "send_order"},
                ),
                patch("gui_auto_trade_close.append_changelog") as append_changelog,
                patch("gui_auto_trade_close.append_stock_log") as append_stock_log,
                patch("gui_auto_trade_close.show_toast") as show_toast,
            ):
                auto_trade_apply_selected_early_close(
                    window,
                    "손/익절",
                    source="우클릭",
                    extra_policy={"profit_percent": "3", "loss_percent": "2"},
                )

        self.assertEqual(2, service.apply_early_close.call_count)
        first_request, first_compatibility = service.apply_early_close.call_args_list[0].args
        self.assertEqual(MODE_EARLY_CLOSE, first_request.command)
        self.assertEqual("우클릭", first_request.source)
        self.assertEqual("손/익절", first_compatibility.method)
        self.assertEqual({"profit_percent": "3", "loss_percent": "2"}, first_compatibility.policy)
        window.update_stock_status.assert_not_called()
        append_stock_log.assert_called_once()
        append_changelog.assert_called_once()
        window.refresh_all.assert_called_once()
        window.statusBarMessage.assert_called_with("조기마감 적용: 1개 / 제외 1개")
        show_toast.assert_called_once_with(
            window,
            "1종목을 조기마감 적용하였습니다.",
            duration_ms=2500,
        )

    def test_all_view_does_not_require_selected_routine_name(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            window = self._window(selected)
            window.current_selected_routine_name.return_value = ""
            self._MessageBox.proceed = True
            service = Mock()
            service.apply_early_close.return_value = OperationCommandResult(
                RESULT_SUCCESS,
                "command-a",
                (
                    StockOperationCommandResult(
                        "005930",
                        str(selected[0][0]),
                        STOCK_APPLIED,
                        1,
                    ),
                ),
            )
            with (
                patch("gui_auto_trade_close.QMessageBox", self._MessageBox),
                patch(
                    "gui_auto_trade_close.OperationCommandService",
                    return_value=service,
                ),
                patch(
                    "gui_auto_trade_close.pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch(
                    "gui_auto_trade_close.auto_trade_setting_liquidation_phase_active",
                    return_value=False,
                ),
                patch(
                    "gui_auto_trade_close.evaluate_production_transition",
                    return_value=Mock(allowed=True),
                ),
                patch(
                    "gui_auto_trade_close._start_close_liquidation_execution",
                    return_value={
                        "ok": True,
                        "stage": "send_order",
                        "runtime_status": "EARLY_CLOSING",
                    },
                ),
                patch(
                    "gui_auto_trade_close._persist_early_close_execution_result",
                    return_value=True,
                ),
                patch("gui_auto_trade_close.append_changelog"),
                patch("gui_auto_trade_close.append_stock_log"),
                patch("gui_auto_trade_close.show_toast") as show_toast,
            ):
                auto_trade_apply_selected_early_close(window, "시장가")

        service.apply_early_close.assert_called_once()
        window.statusBarMessage.assert_called_with("조기마감 적용: 1개")
        show_toast.assert_called_once_with(
            window,
            "1종목을 조기마감 적용하였습니다.",
            duration_ms=2500,
        )

    def test_no_holding_early_close_result_uses_toast_not_information_messagebox(self) -> None:
        from gui_auto_trade_close import auto_trade_apply_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [
                self._write_stock(Path(temp), "111111_A", holding_qty=0),
                self._write_stock(
                    Path(temp),
                    "222222_B",
                    holding_qty=0,
                    state={"status": "REVIEW_REQUIRED", "holding_qty": 0},
                ),
            ]
            window = self._window(selected)
            self._MessageBox.instances = []
            service = Mock()
            service.apply_early_close.return_value = OperationCommandResult(
                RESULT_SUCCESS,
                "command-a",
                (
                    StockOperationCommandResult(
                        "111111",
                        str(selected[0][0]),
                        STOCK_APPLIED,
                        1,
                    ),
                ),
            )
            with (
                patch("gui_auto_trade_close.QMessageBox", self._MessageBox),
                patch("gui_auto_trade_close.OperationCommandService", return_value=service),
                patch("gui_auto_trade_close.pending_order_side_quantities", return_value=(0, 0)),
                patch("gui_auto_trade_close.auto_trade_setting_liquidation_phase_active", return_value=False),
                patch(
                    "gui_auto_trade_close.evaluate_production_transition",
                    return_value=Mock(allowed=True),
                ),
                patch("gui_auto_trade_close.append_changelog"),
                patch("gui_auto_trade_close.append_stock_log"),
                patch("gui_auto_trade_close.show_toast") as show_toast,
            ):
                auto_trade_apply_selected_early_close(window, "루틴")

        service.apply_early_close.assert_called_once()
        self.assertEqual([], self._MessageBox.instances)
        show_toast.assert_called_once()
        toast_args = show_toast.call_args.args
        self.assertIs(toast_args[0], window)
        self.assertEqual("조기마감 적용 대상이 없습니다.", toast_args[1])
        self.assertNotIn("111111", toast_args[1])
        self.assertNotIn("222222", toast_args[1])
        self.assertNotIn("\n", toast_args[1])
        self.assertEqual(2500, show_toast.call_args.kwargs["duration_ms"])
        self.assertNotIn("position", show_toast.call_args.kwargs)
        window.statusBarMessage.assert_called_with("조기마감 적용: 1개 / 제외 1개")


class AutoTradeSettingWindowStatusMessageTest(unittest.TestCase):
    def test_unregister_processes_only_immediate_and_blocks_unsafe_items(self) -> None:
        import gui_auto_trade_unregister as unregister

        immediate = {
            "category": "immediate",
            "code": "111111",
            "name": "즉시종목",
            "runtime_dirs": [],
        }
        blocked_holding = {
            "category": "blocked",
            "code": "222222",
            "name": "보유종목",
            "runtime_dirs": [("보유루틴", Path("holding"))],
        }
        blocked_pending = {
            "category": "blocked",
            "code": "333333",
            "name": "미체결종목",
            "runtime_dirs": [("미체결루틴", Path("pending"))],
        }
        items_by_code = {
            "111111": immediate,
            "222222": blocked_holding,
            "333333": blocked_pending,
        }
        window = Mock()
        parent = Mock()
        window.parent.return_value = parent
        window.current_selected_routine_name.return_value = "테스트루틴"
        window.selected_stock_infos.return_value = [
            (Path("immediate"), "111111", "즉시종목"),
            (Path("holding"), "222222", "보유종목"),
            (Path("pending"), "333333", "미체결종목"),
        ]

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(unregister, "CHANGELOG_PATH", Path(temp) / "PROJECT_CHANGELOG.txt"),
            patch.object(
                unregister,
                "auto_trade_unregister_category",
                side_effect=lambda routine_name, stock_dir, code, name: items_by_code[code],
            ),
            patch.object(unregister, "update_base_stock_routines", return_value=True) as update_routines,
            patch.object(unregister.QMessageBox, "warning") as warning,
            patch.object(unregister, "show_toast") as toast,
        ):
            unregister.unregister_selected_auto_trade_stocks(window)

        update_routines.assert_called_once_with("111111", "즉시종목", [])
        parent.refresh_all.assert_called_once_with()
        window.refresh_all.assert_called_once_with()
        warning.assert_not_called()
        toast.assert_called_once_with(
            window,
            "종목해제 1종목 | 해제불가 2종목",
        )

    def test_unregister_result_toast_text_contract(self) -> None:
        import gui_auto_trade_unregister as unregister

        self.assertEqual(
            "종목해제 10종목 | 해제불가 0종목",
            unregister.unregister_result_toast_text(10, 0, []),
        )
        self.assertEqual(
            "종목해제 8종목 | 해제불가 2종목",
            unregister.unregister_result_toast_text(8, 2, ["LG화학", "SK하이닉스"]),
        )
        self.assertEqual(
            "종목해제 0종목 | 해제불가 5종목",
            unregister.unregister_result_toast_text(
                0,
                5,
                ["LG화학", "SK하이닉스", "카카오게임즈", "셀트리온", "KB금융"],
            ),
        )
        self.assertEqual(
            "종목해제 0종목 | 해제불가 0종목",
            unregister.unregister_result_toast_text(0, 0, []),
        )
        self.assertEqual(
            "종목해제 8종목 | 해제불가 1종목",
            unregister.unregister_result_toast_text(8, 1, ["LG화학"]),
        )
        self.assertEqual(
            "종목해제 8종목 | 해제불가 4종목",
            unregister.unregister_result_toast_text(
                8,
                4,
                ["LG화학", "SK하이닉스", "카카오게임즈", "셀트리온"],
            ),
        )
        self.assertEqual(
            "종목해제 12종목 | 해제불가 6종목",
            unregister.unregister_result_toast_text(
                12,
                6,
                ["LG화학", "SK하이닉스", "카카오게임즈", "셀트리온", "KB금융", "NAVER"],
            ),
        )

    def test_unregister_all_scope_allows_empty_routine_name_with_selected_rows(self) -> None:
        import gui_auto_trade_unregister as unregister

        stock_dir = Path("stocks/005930_Samsung")
        window = Mock()
        parent = Mock()
        window.parent.return_value = parent
        window._all_stocks_scope_active = True
        window.current_selected_routine_name.return_value = ""
        window.selected_stock_infos.return_value = [
            (stock_dir, "005930", "Samsung"),
        ]

        with (
            patch.object(
                unregister,
                "auto_trade_unregister_category",
                return_value={
                    "category": "immediate",
                    "code": "005930",
                    "name": "Samsung",
                    "runtime_dirs": [],
                },
            ) as category,
            patch.object(unregister, "update_base_stock_routines", return_value=True) as update_routines,
            patch.object(unregister, "append_changelog"),
            patch.object(unregister.QMessageBox, "warning") as warning,
            patch.object(unregister, "show_toast") as toast,
        ):
            unregister.unregister_selected_auto_trade_stocks(window)

        category.assert_called_once_with("전체", stock_dir, "005930", "Samsung")
        update_routines.assert_called_once_with("005930", "Samsung", [])
        warning.assert_not_called()
        parent.refresh_all.assert_called_once_with()
        window.refresh_all.assert_called_once_with()
        toast.assert_called_once_with(window, "종목해제 1종목 | 해제불가 0종목")

    def test_unregister_mixed_selection_sends_only_allowed_items_to_backend(self) -> None:
        import gui_auto_trade_unregister as unregister

        allowed = {
            "category": "immediate",
            "code": "111111",
            "name": "Allowed",
            "runtime_dirs": [],
        }
        blocked_running = {
            "category": "blocked",
            "code": "222222",
            "name": "Running",
            "runtime_dirs": [],
        }
        blocked_emergency = {
            "category": "blocked",
            "code": "333333",
            "name": "Emergency",
            "runtime_dirs": [],
        }
        blocked_review = {
            "category": "blocked",
            "code": "444444",
            "name": "Review",
            "runtime_dirs": [],
        }
        items_by_code = {
            "111111": allowed,
            "222222": blocked_running,
            "333333": blocked_emergency,
            "444444": blocked_review,
        }
        window = Mock()
        parent = Mock()
        window.parent.return_value = parent
        window.current_selected_routine_name.return_value = "테스트루틴"
        window.selected_stock_infos.return_value = [
            (Path("allowed"), "111111", "Allowed"),
            (Path("running"), "222222", "Running"),
            (Path("emergency"), "333333", "Emergency"),
            (Path("review"), "444444", "Review"),
        ]

        with (
            patch.object(
                unregister,
                "auto_trade_unregister_category",
                side_effect=lambda routine_name, stock_dir, code, name: items_by_code[code],
            ),
            patch.object(unregister, "update_base_stock_routines", return_value=True) as update_routines,
            patch.object(unregister, "append_changelog"),
            patch.object(unregister.QMessageBox, "warning") as warning,
            patch.object(unregister, "show_toast") as toast,
        ):
            unregister.unregister_selected_auto_trade_stocks(window)

        update_routines.assert_called_once_with("111111", "Allowed", [])
        warning.assert_not_called()
        parent.refresh_all.assert_called_once_with()
        window.refresh_all.assert_called_once_with()
        toast.assert_called_once_with(window, "종목해제 1종목 | 해제불가 3종목")

    def test_unregister_empty_selection_still_warns_in_all_scope(self) -> None:
        import gui_auto_trade_unregister as unregister

        window = Mock()
        window._all_stocks_scope_active = True
        window.current_selected_routine_name.return_value = ""
        window.selected_stock_infos.return_value = []

        with (
            patch.object(unregister, "auto_trade_unregister_category") as category,
            patch.object(unregister.QMessageBox, "warning") as warning,
            patch.object(unregister, "show_toast") as toast,
        ):
            unregister.unregister_selected_auto_trade_stocks(window)

        warning.assert_called_once_with(
            window,
            "선택 오류",
            "등록해제할 종목을 1개 이상 선택하세요.",
        )
        category.assert_not_called()
        toast.assert_not_called()

    def test_status_bar_message_updates_parent_status_bar_only(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        dialog = Mock()
        parent_status_bar = Mock()
        parent = Mock()
        parent.statusBar.return_value = parent_status_bar
        dialog.parent.return_value = parent

        AutoTradeSettingWindow.statusBarMessage(dialog, "마감정책이 취소되었습니다.", 1234)

        parent_status_bar.showMessage.assert_called_once_with("마감정책이 취소되었습니다.", 1234)
        dialog.setWindowTitle.assert_not_called()

    def test_legacy_status_bar_message_uses_same_visible_path(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        dialog = Mock()

        AutoTradeSettingWindow.statusBar_message(dialog, "현재 상태는 마감정책 취소 대상이 아닙니다.", 2345)

        dialog.statusBarMessage.assert_called_once_with("현재 상태는 마감정책 취소 대상이 아닙니다.", 2345)

    def test_popup_message_is_non_modal_buttonless_and_auto_closes(self) -> None:
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        app = QApplication.instance() or QApplication([])
        window = AutoTradeSettingWindow()
        window.show()
        app.processEvents()
        original_title = window.windowTitle()

        window.showAutoTradePopupMessage("현재 상태는 마감정책 취소 대상이 아닙니다.", 80)
        app.processEvents()
        popup = window._notification_popup

        self.assertIsNotNone(popup)
        self.assertTrue(popup.isVisible())
        self.assertEqual("현재 상태는 마감정책 취소 대상이 아닙니다.", popup.text())
        self.assertIs(popup.parentWidget(), window)
        self.assertEqual(Qt.NonModal, popup.windowModality())
        self.assertEqual(0, popup.button_count())
        self.assertEqual(original_title, window.windowTitle())

        deadline = time.time() + 1.0
        while popup.isVisible() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        self.assertFalse(popup.isVisible())

    def test_popup_message_reuses_single_popup_instance(self) -> None:
        from PyQt5.QtWidgets import QApplication
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        app = QApplication.instance() or QApplication([])
        window = AutoTradeSettingWindow()
        window.show()
        app.processEvents()

        window.showAutoTradePopupMessage("현재 상태는 마감정책 취소 대상이 아닙니다.", 0)
        app.processEvents()
        first_popup = window._notification_popup
        window.showAutoTradePopupMessage("마감정책이 취소되었습니다.", 0)
        app.processEvents()

        self.assertIs(first_popup, window._notification_popup)
        self.assertTrue(window._notification_popup.isVisible())
        self.assertEqual("마감정책이 취소되었습니다.", window._notification_popup.text())

    def test_selected_text_delegate_preserves_item_foreground_when_selected(self) -> None:
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QColor
        from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTableWidgetItem
        from gui_auto_trade_setting_window import AutoTradeSettingWindow, SelectedTextReadableDelegate

        app = QApplication.instance() or QApplication([])
        window = AutoTradeSettingWindow()
        delegate = SelectedTextReadableDelegate(window.stock_table)
        window.stock_table.setRowCount(1)
        item = QTableWidgetItem("검토")
        expected_color = QColor("#94a3b8")
        item.setForeground(expected_color)
        window.stock_table.setItem(0, 4, item)
        index = window.stock_table.model().index(0, 4)
        option = QStyleOptionViewItem()
        option.state |= QStyle.State_Selected
        option.palette = window.stock_table.palette()
        painted_options = []

        class FakeStyle:
            def drawControl(self, element, option_arg, painter_arg, widget_arg=None):
                self.element = element
                painted_options.append(option_arg)

        with patch.object(QApplication, "style", return_value=FakeStyle()):
            delegate.paint(Mock(), option, index)

        self.assertEqual(1, len(painted_options))
        self.assertEqual(
            expected_color,
            painted_options[0].palette.highlightedText().color(),
        )

    def test_stock_position_metric_delegate_paints_empty_background_before_metric_text(self) -> None:
        from PyQt5.QtCore import QRect, Qt
        from PyQt5.QtGui import QPainter, QPixmap
        from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
        import gui_auto_trade_setting_window as setting_window
        from gui_auto_trade_setting_window import AutoTradeSettingWindow, StockPositionMetricDelegate

        app = QApplication.instance() or QApplication([])
        window = AutoTradeSettingWindow()
        events = []

        class FakeStyle:
            def drawControl(self, element, option_arg, painter_arg, widget_arg=None):
                events.append(("background", element, option_arg.text, widget_arg))

        delegate = StockPositionMetricDelegate(window.stock_table)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 220, 24)
        option.state |= QStyle.State_Selected
        option.palette = window.stock_table.palette()
        index = Mock()
        index.data.return_value = "0주 / 0"
        pixmap = QPixmap(220, 24)
        pixmap.fill(Qt.white)
        painter = QPainter(pixmap)

        def record_metric(*args, **kwargs):
            events.append(("metric", None, None, None))
            return True

        with patch.object(QApplication, "style", return_value=FakeStyle()), patch.object(
            setting_window,
            "draw_stock_position_metric",
            side_effect=record_metric,
        ):
            delegate.paint(painter, option, index)
        painter.end()

        self.assertEqual(
            [("background", QStyle.CE_ItemViewItem, "", None), ("metric", None, None, None)],
            events,
        )

    def test_stock_position_metric_delegate_uses_compact_value_paint_without_separator(self) -> None:
        from PyQt5.QtCore import QRect, Qt
        from PyQt5.QtGui import QPainter, QPixmap
        from PyQt5.QtWidgets import QApplication, QStyleOptionViewItem
        import gui_auto_trade_setting_window as setting_window
        from gui_auto_trade_setting_window import AutoTradeSettingWindow, StockPositionMetricDelegate

        app = QApplication.instance() or QApplication([])
        window = AutoTradeSettingWindow()
        before_widths = [window.stock_table.columnWidth(i) for i in range(window.stock_table.columnCount())]
        delegate = StockPositionMetricDelegate(window.stock_table)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, window.stock_table.columnWidth(7), 24)
        option.palette = window.stock_table.palette()
        option.widget = window.stock_table
        index = Mock()
        index.column.return_value = 7
        index.data.return_value = "0주 / 0"
        pixmap = QPixmap(option.rect.width(), option.rect.height())
        pixmap.fill(Qt.white)
        painter = QPainter(pixmap)

        with patch.object(setting_window, "draw_stock_position_metric", return_value=True) as draw_metric:
            delegate.paint(painter, option, index)
        painter.end()

        self.assertEqual("0주 / 0", index.data.return_value)
        self.assertEqual(before_widths, [window.stock_table.columnWidth(i) for i in range(window.stock_table.columnCount())])
        self.assertEqual(True, draw_metric.call_args.kwargs["compact"])
        self.assertEqual("보유", draw_metric.call_args.kwargs["label_hint"])

    def test_compact_stock_position_metric_uses_fixed_slots(self) -> None:
        from PyQt5.QtCore import QRect, Qt
        from PyQt5.QtGui import QFont, QFontMetrics
        from PyQt5.QtWidgets import QApplication
        from gui_auto_trade_display import (
            compact_stock_position_metric_rects,
            default_stock_position_metric_value_widths,
            draw_stock_position_metric,
        )
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        class FakePainter:
            def __init__(self, font) -> None:
                self.calls = []
                self._font = QFont(font)

            def save(self) -> None:
                pass

            def restore(self) -> None:
                pass

            def setPen(self, color) -> None:
                pass

            def font(self):
                return self._font

            def fontMetrics(self):
                return QFontMetrics(self._font)

            def drawText(self, rect, alignment, text) -> None:
                self.calls.append((QRect(rect), alignment, text))

        app = QApplication.instance() or QApplication([])
        window = AutoTradeSettingWindow()
        test_font = window.stock_table.font()
        samples = [
            ("보유", "0주 / 0", "120주 / 3,450,000"),
            ("가격", "0 / 0", "28,750 / 29,100"),
            ("손익", "0 / 0.00%", "+42,000 / +1.22%"),
            ("미체결", "0 / 0", "10 / 0"),
        ]
        column_widths = {
            "보유": 204,
            "가격": 194,
            "손익": 199,
            "미체결": 78,
        }
        compact_margins = (9, 10)
        for label, short_text, long_text in samples:
            painter = FakePainter(test_font)
            cell_rect = QRect(0, 0, column_widths[label], 24)
            self.assertTrue(
                draw_stock_position_metric(
                    painter,
                    cell_rect,
                    short_text,
                    label_hint=label,
                    compact=True,
                    compact_margins=compact_margins,
                )
            )
            left_width, right_width = default_stock_position_metric_value_widths(painter.font())[label]
            slash_width = painter.fontMetrics().horizontalAdvance(" / ")
            expected_rects = compact_stock_position_metric_rects(
                cell_rect,
                left_width,
                slash_width,
                right_width,
                left_margin=compact_margins[0],
                right_margin=compact_margins[1],
            )
            short_left_value, short_right_value = short_text.split(" / ", 1)
            expected_left_alignment = (
                Qt.AlignCenter | Qt.AlignVCenter
                if label == "가격" and short_left_value == "-"
                else Qt.AlignRight | Qt.AlignVCenter
            )
            expected_right_alignment = (
                Qt.AlignCenter | Qt.AlignVCenter
                if label == "가격" and short_right_value == "-"
                else Qt.AlignRight | Qt.AlignVCenter
            )
            self.assertEqual(
                [
                    (expected_rects[0], expected_left_alignment, short_left_value),
                    (expected_rects[1], Qt.AlignCenter | Qt.AlignVCenter, " / "),
                    (expected_rects[2], expected_right_alignment, short_right_value),
                ],
                painter.calls,
            )
            self.assertEqual(compact_margins[0], expected_rects[0].left())
            self.assertLess(expected_rects[0].right(), expected_rects[1].left())
            self.assertLess(expected_rects[1].right(), expected_rects[2].left())

            painter_long = FakePainter(test_font)
            self.assertTrue(
                draw_stock_position_metric(
                    painter_long,
                    cell_rect,
                    long_text,
                    label_hint=label,
                    compact=True,
                    compact_margins=compact_margins,
                )
            )
            self.assertEqual([call[0] for call in painter.calls], [call[0] for call in painter_long.calls])
            if label == "가격":
                self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, painter_long.calls[0][1])
                self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, painter_long.calls[2][1])

class EarlyCloseCancelSafetyTest(unittest.TestCase):
    @staticmethod
    def _write_stock(root: Path, name: str) -> tuple[Path, str, str]:
        stock_dir = root / "stocks" / name
        stock_dir.mkdir(parents=True)
        code, display_name = name.split("_", 1)
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "RUNNING", "holding_qty": 5, "trade_enabled": True}),
            encoding="utf-8",
        )
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        return stock_dir, code, display_name

    @staticmethod
    def _window(selected) -> Mock:
        window = Mock()
        window.selected_stock_infos.return_value = selected
        window.showAutoTradePopupMessage = Mock()
        viewport = Mock()
        window.stock_table.viewport.return_value = viewport
        window.stock_table.selectionModel.return_value.selectedRows.return_value = []
        return window

    def test_cancel_early_close_succeeds_when_safe(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "EARLY_CLOSE"
            state["trade_enabled"] = True
            state["operation_command_mode"] = MODE_EARLY_CLOSE
            state["early_close_requested_at"] = "2026-07-21 09:30:00"
            state["early_close_source"] = "우클릭"
            state["early_close_method"] = "루틴"
            state["liquidation_policy_forced"] = True
            state["liquidation_policy_reason"] = "EARLY_CLOSE"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            window = self._window(selected)

            with (
                patch("gui_auto_trade_close.QMessageBox.warning") as warning,
                patch("gui_auto_trade_close.ORDER_QUEUE_PATH", Path(temp) / "runtime" / "order_queue.json"),
                patch("gui_auto_trade_close.OperationCommandService", return_value=OperationCommandService(Path(temp))),
                patch("gui_auto_trade_close.append_changelog") as append_changelog,
                patch("gui_auto_trade_close.append_stock_log") as append_stock_log,
            ):
                auto_trade_cancel_selected_early_close(window)

            warning.assert_not_called()
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("", saved["early_close_requested_at"])
            self.assertEqual("", saved["early_close_source"])
            self.assertEqual("", saved["early_close_method"])
            self.assertFalse(saved["liquidation_policy_forced"])
            self.assertEqual(MODE_NORMAL, saved["operation_command_mode"])
            append_stock_log.assert_called_once()
            append_changelog.assert_called_once()
            window.refresh_all.assert_called_once()
            window.showAutoTradePopupMessage.assert_called_with("마감정책이 취소되었습니다.")

    def test_cancel_early_close_blocks_not_applied_stock(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            window = self._window(selected)

            with patch("gui_auto_trade_close.QMessageBox.warning") as warning:
                auto_trade_cancel_selected_early_close(window)

            warning.assert_not_called()
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_when_selected_row_displays_waiting(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "EARLY_CLOSE"
            state["trade_enabled"] = True
            state["operation_command_mode"] = MODE_EARLY_CLOSE
            state["early_close_requested_at"] = "2026-07-21 09:30:00"
            state["early_close_source"] = "우클릭"
            state["early_close_method"] = "루틴"
            state["liquidation_policy_forced"] = True
            state["liquidation_policy_reason"] = "EARLY_CLOSE"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            window = self._window(selected)
            selected_index = Mock()
            selected_index.row.return_value = 0
            path_item = Mock()
            path_item.data.return_value = str(selected[0][0])
            status_item = Mock()
            status_item.text.return_value = "감시/대기"
            window.stock_table.selectionModel.return_value.selectedRows.return_value = [selected_index]
            window.stock_table.item.side_effect = lambda _row, col: path_item if col == 0 else status_item if col == 4 else None

            with patch("gui_auto_trade_close.OperationCommandService") as service_type:
                auto_trade_cancel_selected_early_close(window)

            service_type.assert_not_called()
            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_when_not_trading(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": False,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            window = self._window(selected)

            with patch("gui_auto_trade_close.append_stock_log") as append_stock_log:
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            append_stock_log.assert_called_once()
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_order_queued(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [self._write_stock(root, "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            queue_path = root / "runtime" / "order_queue.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps({"orders": [{"status": "ORDER_QUEUED", "code": "005930"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window(selected)

            with patch("gui_auto_trade_close.ORDER_QUEUE_PATH", queue_path):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_send_order_called_without_fill(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [self._write_stock(root, "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            queue_path = root / "runtime" / "order_queue.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps({"orders": [{"status": "REAL_READY", "code": "005930", "send_order_called": True}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window(selected)

            with patch("gui_auto_trade_close.ORDER_QUEUE_PATH", queue_path):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_dispatch_claim(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [self._write_stock(root, "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            queue_path = root / "runtime" / "order_queue.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps({"orders": [{"status": "REAL_READY", "code": "005930", "dispatch_id": "D1"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window(selected)

            with patch("gui_auto_trade_close.ORDER_QUEUE_PATH", queue_path):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_broker_or_pending_order(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [self._write_stock(root, "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (selected[0][0] / "orders.json").write_text(
                json.dumps({
                    "orders": [{
                        "side": "SELL",
                        "order_time": "2026-07-21 09:31:00",
                        "status": "ACCEPTED",
                        "broker_order_no": "BRK1",
                        "pending_qty": 1,
                    }]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window(selected)

            with patch("gui_auto_trade_close.ORDER_QUEUE_PATH", root / "runtime" / "order_queue.json"):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_filled_sell_order(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [self._write_stock(root, "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (selected[0][0] / "orders.json").write_text(
                json.dumps({
                    "orders": [{
                        "side": "SELL",
                        "order_time": "2026-07-21 09:31:00",
                        "filled_qty": 1,
                    }]
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            window = self._window(selected)

            with patch("gui_auto_trade_close.ORDER_QUEUE_PATH", root / "runtime" / "order_queue.json"):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_runtime_queue_read_failure(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = [self._write_stock(root, "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            queue_path = root / "runtime" / "order_queue.json"
            queue_path.parent.mkdir()
            queue_path.write_text("{broken", encoding="utf-8")
            window = self._window(selected)

            with patch("gui_auto_trade_close.ORDER_QUEUE_PATH", queue_path):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_blocks_read_back_failure(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            service = Mock()
            service.apply.return_value = OperationCommandResult(
                RESULT_SUCCESS,
                "cancel-readback-fail",
                (StockOperationCommandResult("005930", str(selected[0][0]), STOCK_APPLIED, 1),),
            )
            window = self._window(selected)

            with (
                patch("gui_auto_trade_close.ORDER_QUEUE_PATH", Path(temp) / "runtime" / "order_queue.json"),
                patch("gui_auto_trade_close.OperationCommandService", return_value=service),
            ):
                auto_trade_cancel_selected_early_close(window)

            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))
            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")

    def test_cancel_early_close_second_run_is_blocked(self) -> None:
        from gui_auto_trade_close import auto_trade_cancel_selected_early_close

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp), "005930_Samsung")]
            state_path = selected[0][0] / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({
                "status": "EARLY_CLOSE",
                "trade_enabled": True,
                "operation_command_mode": MODE_EARLY_CLOSE,
                "early_close_requested_at": "2026-07-21 09:30:00",
                "liquidation_policy_forced": True,
            })
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            window = self._window(selected)

            with (
                patch("gui_auto_trade_close.ORDER_QUEUE_PATH", Path(temp) / "runtime" / "order_queue.json"),
                patch("gui_auto_trade_close.CHANGELOG_PATH", Path(temp) / "PROJECT_CHANGELOG.txt"),
                patch("gui_auto_trade_close.OperationCommandService", return_value=OperationCommandService(Path(temp))),
            ):
                auto_trade_cancel_selected_early_close(window)
                auto_trade_cancel_selected_early_close(window)

            window.showAutoTradePopupMessage.assert_called_with("현재 상태는 마감정책 취소 대상이 아닙니다.")


class AutoTradeContextMenuTest(unittest.TestCase):
    class _FakeAction:
        def __init__(self, text: str, separator: bool = False) -> None:
            self.text = text
            self.enabled = True
            self.icon = None
            self.properties = {}
            self.separator = separator

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = bool(enabled)

        def setText(self, text: str) -> None:
            self.text = str(text)

        def setIcon(self, icon) -> None:
            self.icon = icon

        def setProperty(self, name: str, value) -> None:
            self.properties[str(name)] = value

        def property(self, name: str):
            return self.properties.get(str(name))

    class _FakeMenu:
        root = None
        chosen_text = None
        chosen_menu_title = None

        def __init__(self, _parent=None, title: str = "") -> None:
            self.title = title
            self.enabled = True
            self.actions: list[AutoTradeContextMenuTest._FakeAction] = []
            self.submenus: list[AutoTradeContextMenuTest._FakeMenu] = []
            if not title:
                AutoTradeContextMenuTest._FakeMenu.root = self

        def addAction(self, text: str):
            action = AutoTradeContextMenuTest._FakeAction(text)
            self.actions.append(action)
            return action

        def addMenu(self, text: str):
            submenu = AutoTradeContextMenuTest._FakeMenu(title=text)
            self.submenus.append(submenu)
            return submenu

        def addSeparator(self) -> None:
            self.actions.append(AutoTradeContextMenuTest._FakeAction("<separator>", separator=True))
            return None

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = bool(enabled)

        def exec_(self, _pos):
            chosen_text = AutoTradeContextMenuTest._FakeMenu.chosen_text
            chosen_menu_title = (
                AutoTradeContextMenuTest._FakeMenu.chosen_menu_title
            )
            if chosen_text is None:
                return None
            menus = [self]
            for menu in menus:
                menus.extend(menu.submenus)
            for menu in menus:
                if not menu.enabled:
                    continue
                if chosen_menu_title and menu.title != chosen_menu_title:
                    continue
                for action in menu.actions:
                    if action.text == chosen_text:
                        return action
            return None

    @staticmethod
    def _window() -> Mock:
        AutoTradeContextMenuTest._FakeMenu.chosen_menu_title = None
        window = Mock()
        item = Mock()
        item.row.return_value = 0
        window.stock_table.itemAt.return_value = item
        window.stock_table.viewport.return_value.mapToGlobal.return_value = object()
        window.selected_stock_infos.return_value = [(Path("stocks/005930_Samsung"), "005930", "Samsung")]
        window.selected_operation_mode_set.return_value = set()
        return window

    def test_early_close_menu_order_and_display_labels(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu._context_menu_operation_policy",
                return_value={},
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        early_menu = self._FakeMenu.root.submenus[0]
        self.assertEqual("조기마감", early_menu.title)
        self.assertEqual(
            ["루틴마감", "시장가", "현재가", "손/익절", "이월", "취소"],
            [action.text for action in early_menu.actions if not action.separator],
        )
        self.assertEqual("<separator>", early_menu.actions[5].text)

    def test_early_close_menu_display_labels_keep_existing_call_values(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        expected = {
            "루틴마감": ("apply_selected_early_close", ("루틴",), {"source": "우클릭"}),
            "시장가": ("apply_selected_early_close", ("시장가즉시",), {"source": "우클릭"}),
            "현재가": ("apply_selected_early_close", ("현재가즉시",), {"source": "우클릭"}),
            "손/익절": ("apply_selected_early_close_profit_loss", (), {}),
            "이월": ("apply_selected_early_close", ("이월",), {"source": "우클릭"}),
            "취소": ("cancel_selected_early_close", (), {}),
        }
        for chosen_text, (method_name, args, kwargs) in expected.items():
            with self.subTest(chosen_text=chosen_text):
                window = self._window()
                self._FakeMenu.chosen_text = chosen_text
                with (
                    patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
                    patch(
                        "gui_auto_trade_context_menu._context_menu_operation_policy",
                        return_value={},
                    ),
                ):
                    show_auto_trade_stock_context_menu(window, object())
                getattr(window, method_name).assert_called_once_with(*args, **kwargs)

    def test_early_close_menu_marks_only_current_environment_method(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        cases = {
            "루틴매도신호": "루틴마감",
            "시장가": "시장가",
            "현재가": "현재가",
            "익절/손절": "손/익절",
            "이월": "이월",
            "취소": "취소",
        }
        for method, selected_label in cases.items():
            with self.subTest(method=method):
                window = self._window()
                self._FakeMenu.chosen_text = None
                with (
                    patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
                    patch(
                        "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
                    ) as policy_path,
                ):
                    policy_path.read_text.return_value = json.dumps(
                        {"early_close": {"method": method}},
                        ensure_ascii=False,
                    )
                    show_auto_trade_stock_context_menu(window, object())

                labels = [
                    action.text
                    for action in self._FakeMenu.root.submenus[0].actions
                    if not action.separator
                ]
                actions = [
                    action
                    for action in self._FakeMenu.root.submenus[0].actions
                    if not action.separator
                ]
                self.assertEqual(
                    ["루틴마감", "시장가", "현재가", "손/익절", "이월", "취소"],
                    labels,
                )
                self.assertTrue(all(action.icon is not None for action in actions))
                selected_actions = [
                    action
                    for action in actions
                    if action.property("earlyCloseCurrent")
                ]
                self.assertEqual(1, len(selected_actions))
                self.assertEqual(selected_label, selected_actions[0].text)

    def test_early_close_menu_refreshes_marker_without_duplication(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        policies = [
            {"early_close": {"method": "루틴매도신호"}},
            {"early_close": {"method": "시장가"}},
        ]
        rendered_states = []
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
            ) as policy_path,
        ):
            policy_path.read_text.side_effect = [
                json.dumps(policy, ensure_ascii=False)
                for policy in policies
            ]
            for _ in policies:
                window = self._window()
                self._FakeMenu.chosen_text = None
                show_auto_trade_stock_context_menu(window, object())
                rendered_states.append(
                    [
                        (
                            action.text,
                            action.property("earlyCloseCurrent"),
                        )
                        for action in self._FakeMenu.root.submenus[0].actions
                        if not action.separator
                    ]
                )

        self.assertIn(("루틴마감", True), rendered_states[0])
        self.assertIn(("시장가", True), rendered_states[1])
        self.assertTrue(
            all(
                not label.startswith("▪")
                for states in rendered_states
                for label, _selected in states
            )
        )

    def test_marked_early_close_action_keeps_existing_click_behavior(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_text = "시장가"
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
            ) as policy_path,
        ):
            policy_path.read_text.return_value = json.dumps(
                {"early_close": {"method": "시장가"}},
                ensure_ascii=False,
            )
            show_auto_trade_stock_context_menu(window, object())

        window.apply_selected_early_close.assert_called_once_with(
            "시장가즉시",
            source="우클릭",
        )

    def test_individual_liquidation_menu_structure_and_current_method(
        self,
    ) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
            ) as policy_path,
        ):
            policy_path.read_text.return_value = json.dumps(
                {
                    "liquidation": {
                        "method": "현재가",
                        "minutes_before_regular_close": "7",
                    }
                },
                ensure_ascii=False,
            )
            show_auto_trade_stock_context_menu(window, object())

        individual_menu = self._FakeMenu.root.submenus[1]
        self.assertEqual("개별청산", individual_menu.title)
        actions = [
            action
            for action in individual_menu.actions
            if not action.separator
        ]
        self.assertEqual(
            ["시장가", "현재가", "이월"],
            [action.text for action in actions],
        )
        self.assertTrue(all(action.icon is not None for action in actions))
        selected_actions = [
            action
            for action in actions
            if action.property("individualLiquidationCurrent")
        ]
        self.assertEqual(1, len(selected_actions))
        self.assertEqual("현재가", selected_actions[0].text)
        time_menu = individual_menu.submenus[0]
        self.assertEqual("시간", time_menu.title)
        time_actions = [
            action
            for action in time_menu.actions
            if not action.separator
        ]
        self.assertEqual(
            ["1분", "3분", "5분", "10분", "15분", "20분", "30분", "7분"],
            [action.text for action in time_actions],
        )
        selected_time_actions = [
            action
            for action in time_actions
            if action.property("individualLiquidationMinutesCurrent")
        ]
        self.assertEqual(1, len(selected_time_actions))
        self.assertEqual("7분", selected_time_actions[0].text)
        self.assertTrue(time_menu.enabled)

    def test_individual_liquidation_menu_uses_existing_apply_path(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        expected = {
            "시장가": ("시장가", "7"),
            "현재가": ("현재가", "7"),
            "이월": ("이월", "7"),
        }
        for chosen_text, expected_args in expected.items():
            with self.subTest(chosen_text=chosen_text):
                window = self._window()
                self._FakeMenu.chosen_menu_title = "개별청산"
                self._FakeMenu.chosen_text = chosen_text
                with (
                    patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
                    patch(
                        "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
                    ) as policy_path,
                ):
                    policy_path.read_text.return_value = json.dumps(
                        {
                            "liquidation": {
                                "method": "시장가",
                                "minutes_before_regular_close": "7",
                            }
                        },
                        ensure_ascii=False,
                    )
                    show_auto_trade_stock_context_menu(window, object())

                window.apply_selected_individual_liquidation_method.assert_called_once_with(
                    *expected_args
                )
                window.open_selected_individual_liquidation_settings.assert_not_called()

    def test_individual_liquidation_menu_adapter_records_runtime_without_config_write(
        self,
    ) -> None:
        import gui_auto_trade_close as close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = OperationCommandServiceTest._stock(root, "005930_Samsung")
            config_path = stock / "config.json"
            before = config_path.read_bytes()
            window = Mock()
            window.selected_stock_infos.return_value = [
                (stock, "005930", "Samsung")
            ]
            window.capture_stock_table_view_state.return_value = ([str(stock)], 0)

            with (
                patch.object(close, "PROJECT_ROOT", root),
                patch.object(
                    close,
                    "evaluate_production_transition",
                    return_value=Mock(allowed=True),
                ),
                patch.object(
                    close,
                    "_start_close_liquidation_execution",
                    return_value={"ok": True, "stage": "send_order"},
                ) as start,
            ):
                close.auto_trade_apply_selected_individual_liquidation_method(
                    window,
                    "현재가",
                    "7",
                )

            after = config_path.read_bytes()
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(before, after)
        request = state[INDIVIDUAL_LIQUIDATION_REQUEST_KEY]
        self.assertEqual("현재가", request["method"])
        self.assertEqual("7", request["minutes_before_regular_close"])
        start.assert_not_called()
        window.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_called_once_with(
            "개별청산 설정 완료: 7분/현재가 / 대상 1개"
        )

    def test_individual_liquidation_time_keeps_current_method(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_menu_title = "시간"
        self._FakeMenu.chosen_text = "15분"
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu._context_menu_operation_policy",
                return_value={
                    "liquidation": {
                        "method": "시장가",
                        "minutes_before_regular_close": "5",
                    }
                },
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        window.apply_selected_individual_liquidation_method.assert_called_once_with(
            "시장가",
            "15",
        )

    def test_individual_liquidation_time_is_disabled_for_carry(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_menu_title = "시간"
        self._FakeMenu.chosen_text = "15분"
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu._context_menu_operation_policy",
                return_value={
                    "liquidation": {
                        "method": "이월",
                        "minutes_before_regular_close": "10",
                    }
                },
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        time_menu = self._FakeMenu.root.submenus[1].submenus[0]
        self.assertFalse(time_menu.enabled)
        window.apply_selected_individual_liquidation_method.assert_not_called()

    def test_individual_liquidation_command_failure_has_no_success_message(
        self,
    ) -> None:
        import gui_auto_trade_close as close

        window = Mock()
        window.selected_stock_infos.return_value = [
            (Path("stocks/005930_Samsung"), "005930", "Samsung")
        ]
        service = Mock()
        service.apply_individual_liquidation.return_value = OperationCommandResult(
            RESULT_FAILED,
            "failed-command",
            error="injected failure",
        )
        with (
            patch.object(close, "OperationCommandService", return_value=service),
            patch.object(close.QMessageBox, "critical") as critical,
        ):
            close.auto_trade_apply_selected_individual_liquidation_method(
                window,
                "시장가",
                "15",
            )

        window.statusBarMessage.assert_not_called()
        critical.assert_called_once()

    def test_individual_liquidation_silent_failure_returns_message_without_modal(
        self,
    ) -> None:
        import gui_auto_trade_close as close

        window = Mock()
        window.selected_stock_infos.return_value = [
            (Path("stocks/005930_Samsung"), "005930", "Samsung")
        ]
        service = Mock()
        service.apply_individual_liquidation.return_value = OperationCommandResult(
            RESULT_FAILED,
            "failed-command",
            error="키움 서버에 로그인되어 있지 않습니다.",
        )
        with (
            patch.object(close, "OperationCommandService", return_value=service),
            patch.object(
                close,
                "evaluate_production_transition",
                return_value=Mock(allowed=True),
            ),
            patch.object(close.QMessageBox, "critical") as critical,
            patch.object(close.QMessageBox, "warning") as warning,
        ):
            result = close.auto_trade_apply_selected_individual_liquidation_method(
                window,
                "시장가",
                "15",
                show_error_dialog=False,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(
            "키움 서버에 로그인되어 있지 않습니다.",
            result["message"],
        )
        critical.assert_not_called()
        warning.assert_not_called()

    def test_individual_liquidation_menu_has_no_current_marker_on_read_failure(
        self,
    ) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
            ) as policy_path,
        ):
            policy_path.read_text.side_effect = OSError("unreadable")
            show_auto_trade_stock_context_menu(window, object())

        actions = [
            action
            for action in self._FakeMenu.root.submenus[1].actions
            if not action.separator
        ]
        self.assertTrue(
            all(
                not action.property("individualLiquidationCurrent")
                for action in actions
            )
        )

    def test_early_close_menu_keeps_plain_labels_when_setting_read_fails(
        self,
    ) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.OPERATION_POLICY_PATH"
            ) as policy_path,
        ):
            policy_path.read_text.side_effect = OSError("unreadable")
            show_auto_trade_stock_context_menu(window, object())

        labels = [
            (
                action.text,
                action.property("earlyCloseCurrent"),
            )
            for action in self._FakeMenu.root.submenus[0].actions
            if not action.separator
        ]
        self.assertTrue(all(not selected for _label, selected in labels))

    def test_mixed_operation_modes_hide_all_mode_specific_actions(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        window.selected_operation_mode_set.return_value = {
            "SCHEDULED",
            "CONTINUOUS",
        }
        self._FakeMenu.chosen_text = "ATS설정"
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu._context_menu_operation_policy",
                return_value={},
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        root_actions = [
            action.text
            for action in self._FakeMenu.root.actions
            if not action.separator
        ]
        self.assertNotIn("ATS설정", root_actions)
        self.assertNotIn("시간변경", root_actions)
        self.assertNotIn("변경리셋", root_actions)
        self.assertNotIn("혼합 선택: 공통 메뉴만 사용", root_actions)

    def test_all_manual_selection_shows_only_ats_mode_action(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        window.selected_operation_mode_set.return_value = {"CONTINUOUS"}
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu._context_menu_operation_policy",
                return_value={},
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        root_actions = [
            action.text
            for action in self._FakeMenu.root.actions
            if not action.separator
        ]
        self.assertIn(
            "ATS설정",
            [submenu.title for submenu in self._FakeMenu.root.submenus],
        )
        self.assertNotIn("시간변경", root_actions)
        self.assertNotIn("변경리셋", root_actions)

    def test_ats_submenu_uses_visible_sessions_and_current_state(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        window.selected_operation_mode_set.return_value = {"CONTINUOUS"}
        window.selected_manual_ats_state.return_value = {
            "extra1": True,
            "extra2": False,
            "extra3": False,
        }
        window.selected_manual_ats_liquidation_available.return_value = True
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.manual_ats_visible_session_keys",
                return_value=("extra1", "extra3"),
            ),
            patch(
                "gui_auto_trade_context_menu.manual_ats_session_labels",
                return_value={
                    "extra1": "ATS 장전",
                    "extra2": "ATS 주간",
                    "extra3": "ATS 야간",
                },
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        ats_menu = self._FakeMenu.root.submenus[2]
        self.assertEqual("ATS설정", ats_menu.title)
        self.assertTrue(ats_menu.enabled)
        self.assertEqual(
            ["ATS 장전", "ATS 야간", "시장가", "현재가"],
            [action.text for action in ats_menu.actions if not action.separator],
        )
        self.assertEqual(1, sum(action.separator for action in ats_menu.actions))
        self.assertTrue(ats_menu.actions[0].property("atsSessionCurrent"))
        self.assertFalse(ats_menu.actions[1].property("atsSessionCurrent"))
        self.assertTrue(ats_menu.actions[3].enabled)
        self.assertTrue(ats_menu.actions[4].enabled)

    def test_ats_liquidation_actions_are_disabled_outside_selected_session(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        window.selected_operation_mode_set.return_value = {"CONTINUOUS"}
        window.selected_manual_ats_state.return_value = {
            "extra1": True,
            "extra2": False,
            "extra3": False,
        }
        window.selected_manual_ats_liquidation_available.return_value = False
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.manual_ats_visible_session_keys",
                return_value=("extra1",),
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        ats_menu = self._FakeMenu.root.submenus[2]
        actions = [action for action in ats_menu.actions if not action.separator]
        self.assertFalse(actions[-2].enabled)
        self.assertFalse(actions[-1].enabled)

    def test_ats_submenu_is_disabled_without_visible_sessions(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        window.selected_operation_mode_set.return_value = {"CONTINUOUS"}
        self._FakeMenu.chosen_menu_title = "ATS설정"
        self._FakeMenu.chosen_text = "시장가"
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu.manual_ats_visible_session_keys",
                return_value=(),
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        ats_menu = self._FakeMenu.root.submenus[2]
        self.assertFalse(ats_menu.enabled)
        window.execute_selected_manual_ats_liquidation.assert_not_called()

    def test_ats_submenu_dispatches_existing_setting_and_liquidation_backends(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        selected = [(Path("stocks/005930_Samsung"), "005930", "Samsung")]
        state = {"extra1": True, "extra2": False, "extra3": False}
        for chosen_text in ("ATS 주간", "현재가"):
            with self.subTest(chosen_text=chosen_text):
                window = self._window()
                window.selected_stock_infos.return_value = selected
                window.selected_operation_mode_set.return_value = {"CONTINUOUS"}
                window.selected_manual_ats_state.return_value = dict(state)
                self._FakeMenu.chosen_menu_title = "ATS설정"
                self._FakeMenu.chosen_text = chosen_text
                with (
                    patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
                    patch(
                        "gui_auto_trade_context_menu.manual_ats_visible_session_keys",
                        return_value=("extra1", "extra2"),
                    ),
                    patch(
                        "gui_auto_trade_context_menu.manual_ats_session_labels",
                        return_value={"extra1": "ATS 장전", "extra2": "ATS 주간"},
                    ),
                ):
                    show_auto_trade_stock_context_menu(window, object())

                if chosen_text == "ATS 주간":
                    window.set_selected_manual_ats_flag.assert_called_once_with(
                        "extra2",
                        True,
                        "ATS 주간",
                    )
                    window.execute_selected_manual_ats_liquidation.assert_not_called()
                else:
                    window.execute_selected_manual_ats_liquidation.assert_called_once_with(
                        "현재가",
                        state,
                        selected,
                        ("extra1", "extra2"),
                        ("extra1",),
                    )
                    window.set_selected_manual_ats_flag.assert_not_called()

    def test_all_scheduled_selection_shows_only_schedule_mode_actions(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        window = self._window()
        window.selected_operation_mode_set.return_value = {"SCHEDULED"}
        self._FakeMenu.chosen_text = None
        with (
            patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu),
            patch(
                "gui_auto_trade_context_menu._context_menu_operation_policy",
                return_value={},
            ),
        ):
            show_auto_trade_stock_context_menu(window, object())

        root_actions = [
            action.text
            for action in self._FakeMenu.root.actions
            if not action.separator
        ]
        self.assertNotIn("ATS설정", root_actions)
        self.assertIn("시간변경", root_actions)
        self.assertIn("변경리셋", root_actions)


if __name__ == "__main__":
    unittest.main()
