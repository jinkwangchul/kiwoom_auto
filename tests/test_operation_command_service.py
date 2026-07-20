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
    COMMAND_IMMEDIATE_LIQUIDATION,
    IMMEDIATE_LIQUIDATION_REQUEST_KEY,
    IMMEDIATE_LIQUIDATION_STATUS_REQUESTED,
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
    auto_trade_setting_early_close_requested,
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
        self.assertTrue(state["liquidation_policy_forced"])

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

    def test_immediate_liquidation_records_requested_without_changing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={
                    "status": "RUNNING",
                    "operation_command_mode": MODE_CARRY_OVER,
                    "operation_sequence": 4,
                },
            )

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_IMMEDIATE_LIQUIDATION,
                    "monitoring_window",
                    command_id="liquidation-1",
                )
            )
            state = self._state(stock)
            request = state[IMMEDIATE_LIQUIDATION_REQUEST_KEY]

        self.assertEqual(RESULT_SUCCESS, result.status)
        self.assertEqual(STOCK_APPLIED, result.stock_results[0].status)
        self.assertEqual(5, result.stock_results[0].sequence)
        self.assertEqual(MODE_CARRY_OVER, state["operation_command_mode"])
        self.assertEqual("liquidation-1", request["command_id"])
        self.assertEqual(5, request["operation_sequence"])
        self.assertEqual(IMMEDIATE_LIQUIDATION_STATUS_REQUESTED, request["status"])
        self.assertEqual("monitoring_window", request["source"])
        self.assertEqual(SCOPE_STOCK, request["target"]["scope"])
        self.assertEqual("005930", request["target"]["id"])

    def test_immediate_liquidation_duplicate_does_not_increment_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={"status": "RUNNING", "operation_command_mode": MODE_NORMAL},
            )
            service = self._service(root)
            request = OperationCommandRequest(
                SCOPE_STOCK,
                "005930",
                COMMAND_IMMEDIATE_LIQUIDATION,
                "monitoring_window",
                command_id="same-liquidation",
            )

            first = service.apply(request)
            second = service.apply(request)
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, first.status)
        self.assertEqual(RESULT_SUCCESS, second.status)
        self.assertEqual(STOCK_IGNORED_DUPLICATE, second.stock_results[0].status)
        self.assertEqual(1, state["operation_sequence"])
        self.assertEqual(MODE_NORMAL, state["operation_command_mode"])

    def test_immediate_liquidation_does_not_create_order_or_send_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "005930_Samsung")

            result = self._service(root).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_IMMEDIATE_LIQUIDATION,
                    "monitoring_window",
                    command_id="boundary-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_SUCCESS, result.status)
        forbidden = {
            "order_queue",
            "ORDER_QUEUED",
            "send_order",
            "send_order_status",
            "chejan",
            "broker_order_no",
        }
        self.assertTrue(forbidden.isdisjoint(state))
        self.assertEqual("REQUESTED", state[IMMEDIATE_LIQUIDATION_REQUEST_KEY]["status"])

    def test_immediate_liquidation_writer_failure_preserves_mode_and_request_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={"status": "RUNNING", "operation_command_mode": MODE_EARLY_CLOSE},
            )

            def writer(_path, _data):
                return {"status": "ERROR", "error": "injected failure"}

            result = self._service(root, atomic_writer=writer).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_IMMEDIATE_LIQUIDATION,
                    "monitoring_window",
                    command_id="failed-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_FAILED, result.status)
        self.assertEqual(MODE_EARLY_CLOSE, state["operation_command_mode"])
        self.assertNotIn(IMMEDIATE_LIQUIDATION_REQUEST_KEY, state)

    def test_immediate_liquidation_read_back_failure_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "005930_Samsung",
                state={"status": "RUNNING", "operation_command_mode": MODE_NORMAL},
            )

            def lying_writer(_path, _data):
                return {"status": "OK", "written": True}

            result = self._service(root, atomic_writer=lying_writer).apply(
                OperationCommandRequest(
                    SCOPE_STOCK,
                    "005930",
                    COMMAND_IMMEDIATE_LIQUIDATION,
                    "monitoring_window",
                    command_id="readback-command",
                )
            )
            state = self._state(stock)

        self.assertEqual(RESULT_FAILED, result.status)
        self.assertIn("read-back verification failed", result.stock_results[0].error)
        self.assertEqual(MODE_NORMAL, state["operation_command_mode"])
        self.assertNotIn(IMMEDIATE_LIQUIDATION_REQUEST_KEY, state)


class EarlyCloseProductionCallerTest(unittest.TestCase):
    class _MessageBox:
        Warning = 1
        Information = 2
        Question = 3
        AcceptRole = 4
        RejectRole = 5
        proceed = True

        def __init__(self, _parent=None) -> None:
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
        viewport = Mock()
        window.stock_table.viewport.return_value = viewport
        return window

    @staticmethod
    def _write_stock(root: Path, folder: str, holding_qty: int = 5) -> tuple[Path, str, str]:
        stock_dir = root / "stocks" / folder
        stock_dir.mkdir(parents=True)
        code, name = folder.split("_", 1)
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "RUNNING", "holding_qty": holding_qty}),
            encoding="utf-8",
        )
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
                patch("gui_auto_trade_close.append_changelog") as append_changelog,
                patch("gui_auto_trade_close.append_stock_log") as append_stock_log,
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


class AutoTradeSettingWindowStatusMessageTest(unittest.TestCase):
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
            self.separator = separator

        def setEnabled(self, enabled: bool) -> None:
            self.enabled = bool(enabled)

    class _FakeMenu:
        root = None
        chosen_text = None

        def __init__(self, _parent=None, title: str = "") -> None:
            self.title = title
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

        def setEnabled(self, _enabled: bool) -> None:
            return None

        def exec_(self, _pos):
            chosen_text = AutoTradeContextMenuTest._FakeMenu.chosen_text
            if chosen_text is None:
                return None
            for menu in [self, *self.submenus]:
                for action in menu.actions:
                    if action.text == chosen_text:
                        return action
            return None

    @staticmethod
    def _window() -> Mock:
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
        with patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu):
            show_auto_trade_stock_context_menu(window, object())

        early_menu = self._FakeMenu.root.submenus[0]
        self.assertEqual("조기마감", early_menu.title)
        self.assertEqual(
            ["조기마감", "시장가", "현재가", "손/익절", "이월", "취소"],
            [action.text for action in early_menu.actions if not action.separator],
        )
        self.assertEqual("<separator>", early_menu.actions[5].text)

    def test_early_close_menu_display_labels_keep_existing_call_values(self) -> None:
        from gui_auto_trade_context_menu import show_auto_trade_stock_context_menu

        expected = {
            "조기마감": ("apply_selected_early_close", ("루틴",), {"source": "우클릭"}),
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
                with patch("gui_auto_trade_context_menu.QMenu", self._FakeMenu):
                    show_auto_trade_stock_context_menu(window, object())
                getattr(window, method_name).assert_called_once_with(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
