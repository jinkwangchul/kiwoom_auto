from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt5.QtWidgets import QMessageBox

import gui_auto_trade_ats_ops as ats_ops
from manual_ats_runtime import write_manual_ats_runtime_selection


class GuiAutoTradeAtsOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.outcome_observer_patch = patch.object(
            ats_ops,
            "observe_manual_ats_liquidation_outcome",
        )
        self.outcome_observer = self.outcome_observer_patch.start()
        self.addCleanup(self.outcome_observer_patch.stop)

    def test_liquidation_availability_uses_selected_session_time_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp)
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "CONTINUOUS"}),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "holding_qty": 10,
                    }
                ),
                encoding="utf-8",
            )
            current = datetime.now().astimezone()
            target = [(stock_dir, "005930", "삼성전자")]
            window = MagicMock()
            window.selected_stock_infos.return_value = target

            def session_definition(key: str) -> dict[str, object]:
                return {
                    "extra1": {"start_time": "08:00:00", "end_time": "08:30:00"},
                    "extra2": {"start_time": "18:00:00", "end_time": "20:00:00"},
                }.get(key, {})

            with patch(
                "gui_ats_utils.manual_ats_session_definition",
                side_effect=session_definition,
            ):
                self.assertFalse(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=8, minute=0, second=0),
                    )
                )
                self.assertTrue(
                    write_manual_ats_runtime_selection(
                        stock_dir,
                        ("extra1",),
                        now_dt=current,
                    )
                )
                self.assertTrue(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=8, minute=0, second=0),
                    )
                )
                self.assertTrue(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=8, minute=29, second=59),
                    )
                )
                self.assertFalse(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=8, minute=30, second=0),
                    )
                )
                self.assertFalse(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=9, minute=0, second=0),
                    )
                )
                self.assertTrue(
                    write_manual_ats_runtime_selection(
                        stock_dir,
                        ("extra1", "extra2"),
                        now_dt=current,
                    )
                )
                self.assertTrue(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=18, minute=0, second=0),
                    )
                )

                state = json.loads(
                    (stock_dir / "state.json").read_text(encoding="utf-8")
                )
                state["holding_qty"] = 0
                (stock_dir / "state.json").write_text(
                    json.dumps(state),
                    encoding="utf-8",
                )
                self.assertFalse(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=18, minute=0, second=0),
                    )
                )
                state["holding_qty"] = 10
                state["trade_enabled"] = False
                (stock_dir / "state.json").write_text(
                    json.dumps(state),
                    encoding="utf-8",
                )
                self.assertFalse(
                    ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                        window,
                        target,
                        now_dt=current.replace(hour=18, minute=0, second=0),
                    )
                )

    def test_liquidation_availability_is_true_when_any_selected_stock_is_eligible(self) -> None:
        window = MagicMock()
        selected = [
            (Path("C:/temp/A"), "000001", "A"),
            (Path("C:/temp/B"), "000002", "B"),
            (Path("C:/temp/C"), "000003", "C"),
        ]
        with patch.object(
            ats_ops,
            "_manual_ats_liquidation_target_eligibility",
            side_effect=[
                {"eligible": False},
                {"eligible": True},
                {"eligible": False},
            ],
        ):
            self.assertTrue(
                ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                    window,
                    selected,
                )
            )
        with patch.object(
            ats_ops,
            "_manual_ats_liquidation_target_eligibility",
            return_value={"eligible": False},
        ):
            self.assertFalse(
                ats_ops.auto_trade_selected_manual_ats_liquidation_available(
                    window,
                    selected,
                )
            )

    def test_ineligible_stock_records_one_blocked_outcome(self) -> None:
        window = MagicMock()
        selected = [(Path("C:/temp/A"), "000001", "A")]
        with (
            patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                return_value={
                    "eligible": False,
                    "selected_sessions": ("extra2",),
                    "blocked_reasons": ["auto trade is not running"],
                },
            ),
            patch.object(ats_ops.QMessageBox, "warning"),
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "시장가",
                {},
                selected,
            )

        self.outcome_observer.assert_called_once()
        fields = self.outcome_observer.call_args.kwargs
        self.assertEqual("BLOCKED", fields["result"])
        self.assertEqual("ELIGIBILITY_BLOCKED", fields["reason_code"])
        self.assertEqual("000001", fields["stock_code"])

    def test_liquidation_uses_each_stock_runtime_sessions_without_overwrite(self) -> None:
        window = MagicMock()
        selected = [
            (Path("C:/temp/A"), "000001", "A"),
            (Path("C:/temp/B"), "000002", "B"),
        ]
        preview_a = {
            "ok": True,
            "code": "000001",
            "name": "A",
            "selected_ats_sessions": ["extra1"],
        }
        preview_b = {
            "ok": True,
            "code": "000002",
            "name": "B",
            "selected_ats_sessions": ["extra2"],
        }
        with (
            patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                side_effect=[
                    {
                        "eligible": True,
                        "selected_sessions": ("extra1",),
                        "blocked_reasons": [],
                    },
                    {
                        "eligible": True,
                        "selected_sessions": ("extra2",),
                        "blocked_reasons": [],
                    },
                ],
            ),
            patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                side_effect=[preview_a, preview_b],
            ) as build_preview,
            patch.object(
                ats_ops.QMessageBox,
                "question",
                return_value=QMessageBox.No,
            ),
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "시장가",
                {"extra1": True, "extra2": True},
                selected,
                ("extra1", "extra2"),
                ("extra1", "extra2"),
            )

        self.assertEqual(("extra1",), build_preview.call_args_list[0].args[3])
        self.assertEqual(("extra2",), build_preview.call_args_list[1].args[3])
        window.save_selected_manual_ats_state.assert_not_called()

    def test_submenu_toggle_edits_only_the_clicked_visible_session(self) -> None:
        window = MagicMock()
        current = {"extra1": False, "extra2": True, "extra3": True}
        window.selected_manual_ats_state.return_value = dict(current)
        window.save_selected_manual_ats_state.return_value = 2

        ats_ops.auto_trade_set_selected_manual_ats_flag(
            window,
            "extra1",
            True,
            "ATS 장전",
        )

        window.save_selected_manual_ats_state.assert_called_once_with(
            {"extra1": True, "extra2": True, "extra3": True},
            None,
            ("extra1",),
        )
        window.statusBarMessage.assert_called_once_with(
            "ATS설정 변경 완료: ATS 장전 ON / 2개"
        )

    def test_apply_selection_writes_runtime_only_and_ignores_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp)
            config = {
                "operation_mode": "CONTINUOUS",
                "manual_ats_sessions": {"extra2": True},
            }
            (stock_dir / "config.json").write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                '{"status":"RUNNING"}',
                encoding="utf-8",
            )
            window = MagicMock()
            selected = [(stock_dir, "005930", "삼성전자")]
            window.selected_stock_infos.return_value = selected
            window.capture_stock_table_view_state.return_value = (set(), 0)
            window.current_runtime_file_signature.return_value = ()

            self.assertEqual(
                {"extra1": False, "extra2": False, "extra3": False},
                ats_ops.auto_trade_selected_manual_ats_state(window, selected),
            )
            changed = ats_ops.auto_trade_save_selected_manual_ats_state(
                window,
                {"extra1": True, "extra2": False, "extra3": False},
            )
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            saved_config = json.loads(
                (stock_dir / "config.json").read_text(encoding="utf-8")
            )

        self.assertEqual(1, changed)
        self.assertEqual(["extra1"], state["manual_ats_selection"]["selected_sessions"])
        self.assertEqual(config, saved_config)

    def test_operator_cancel_does_not_commit_runtime_or_order_candidate(self) -> None:
        window = MagicMock()
        window.selected_stock_infos.return_value = [
            (Path("C:/temp/005930"), "005930", "삼성전자")
        ]
        window.save_selected_manual_ats_state.return_value = 1
        preview = {
            "ok": True,
            "code": "005930",
            "name": "삼성전자",
            "stock_dir": "C:/temp/005930",
            "command_id": "ats-cancel",
            "blocked_reasons": [],
        }
        with (
            patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                return_value={
                    "eligible": True,
                    "selected_sessions": ("extra1",),
                    "blocked_reasons": [],
                },
            ),
            patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                return_value=preview,
            ),
            patch.object(
                ats_ops.QMessageBox,
                "question",
                return_value=QMessageBox.No,
            ),
            patch.object(
                ats_ops,
                "commit_manual_ats_liquidation_preview",
            ) as commit,
            patch.object(ats_ops, "OperationCommandService") as command_service,
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "시장가",
                {"extra1": True},
            )

        commit.assert_not_called()
        command_service.assert_not_called()
        window.process_executable_order_for_auto_trade.assert_not_called()

    def test_failed_preview_does_not_block_another_eligible_stock(self) -> None:
        window = MagicMock()
        selected = [
            (Path("C:/temp/A"), "000001", "A"),
            (Path("C:/temp/B"), "000002", "B"),
        ]
        window.selected_stock_infos.return_value = selected
        window.capture_stock_table_view_state.return_value = (set(), 0)
        window.current_runtime_file_signature.return_value = ("runtime",)
        window.process_executable_order_for_auto_trade.return_value = {
            "blocked_reasons": [],
            "send_order_result": {"send_call_accepted": True},
        }
        failed_preview = {
            "ok": False,
            "code": "000001",
            "name": "A",
            "blocked_reasons": ["preview failed"],
        }
        ready_preview = {
            "ok": True,
            "code": "000002",
            "name": "B",
            "stock_dir": "C:/temp/B",
            "command_id": "ats-B",
            "selected_ats_sessions": ["extra2"],
            "blocked_reasons": [],
        }
        command_service = MagicMock()
        command_service.record_manual_ats_liquidation_status.return_value.status = (
            "APPLIED"
        )
        with (
            patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                side_effect=[
                    {
                        "eligible": True,
                        "selected_sessions": ("extra1",),
                        "blocked_reasons": [],
                    },
                    {
                        "eligible": True,
                        "selected_sessions": ("extra2",),
                        "blocked_reasons": [],
                    },
                ],
            ),
            patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                side_effect=[failed_preview, ready_preview],
            ),
            patch.object(
                ats_ops.QMessageBox,
                "question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(ats_ops.QMessageBox, "warning"),
            patch.object(
                ats_ops,
                "_start_manual_ats_liquidation_with_cancel_boundary",
                return_value={
                    "ok": True,
                    "stage": "send_order",
                    "result_status": "SEND_CALL_ACCEPTED",
                },
            ) as start,
            patch.object(
                ats_ops,
                "OperationCommandService",
                return_value=command_service,
            ),
            patch.object(ats_ops, "append_stock_log"),
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "시장가",
                {"extra1": True, "extra2": True},
                selected,
            )

        start.assert_called_once_with(window, ready_preview)
        window.save_selected_manual_ats_state.assert_not_called()

    def test_accepted_request_reuses_existing_executable_order_entrypoint(self) -> None:
        window = MagicMock()
        window.selected_stock_infos.return_value = [
            (Path("C:/temp/005930"), "005930", "삼성전자")
        ]
        window.save_selected_manual_ats_state.return_value = 1
        window.capture_stock_table_view_state.return_value = (["C:/temp/005930"], 7)
        window.current_runtime_file_signature.return_value = ("runtime",)
        window.process_executable_order_for_auto_trade.return_value = {
            "processed": True,
            "stage": "send_order",
            "blocked_reasons": [],
            "send_order_result": {
                "send_call_accepted": True,
                "send_call_rejected": False,
                "send_uncertain": False,
                "queue_result_recorded": True,
            },
        }
        preview = {
            "ok": True,
            "code": "005930",
            "name": "삼성전자",
            "stock_dir": "C:/temp/005930",
            "command_id": "ats-accepted",
            "blocked_reasons": [],
        }
        command_service = MagicMock()
        command_service.record_manual_ats_liquidation_status.return_value.status = (
            "APPLIED"
        )
        with (
            patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                return_value={
                    "eligible": True,
                    "selected_sessions": ("extra1",),
                    "blocked_reasons": [],
                },
            ),
            patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                return_value=preview,
            ),
            patch.object(
                ats_ops.QMessageBox,
                "question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(
                ats_ops,
                "_start_manual_ats_liquidation_with_cancel_boundary",
                return_value={
                    "ok": True,
                    "stage": "send_order",
                    "result_status": "SEND_CALL_ACCEPTED",
                },
            ) as start,
            patch.object(
                ats_ops,
                "OperationCommandService",
                return_value=command_service,
            ),
            patch.object(ats_ops, "append_stock_log"),
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "시장가",
                {"extra1": True},
            )

        start.assert_called_once_with(window, preview)
        window.refresh_all.assert_called_once()

    def test_pending_orders_persist_waiting_and_do_not_dispatch_liquidation(self) -> None:
        window = MagicMock()
        window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
            "ok": True,
            "cancel_requested": 1,
            "cancel_pending": 0,
            "cancel_order_identities": [
                {
                    "order_queued_id": "source-1",
                    "order_id": "order-1",
                    "broker_order_no": "broker-1",
                }
            ],
        }
        preview = {
            "ok": True,
            "code": "005930",
            "name": "삼성전자",
            "stock_dir": "C:/temp/005930_삼성전자",
            "command_id": "ats-wait-1",
            "requested_at": "2026-08-09T16:00:00+09:00",
            "order_candidate": {"routine": "instance-1"},
        }
        command_service = MagicMock()
        command_service.record_manual_ats_liquidation_status.return_value.status = "APPLIED"
        with (
            patch.object(
                ats_ops,
                "ensure_manual_ats_liquidation_request",
                return_value={"ok": True},
            ),
            patch.object(ats_ops, "read_json_dict", return_value={}),
            patch.object(
                ats_ops,
                "OperationCommandService",
                return_value=command_service,
            ),
            patch.object(ats_ops, "_dispatch_manual_ats_liquidation_preview") as dispatch,
        ):
            result = ats_ops._start_manual_ats_liquidation_with_cancel_boundary(
                window,
                preview,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("awaiting_cancel_confirmation", result["stage"])
        dispatch.assert_not_called()
        window.process_executable_order_for_auto_trade.assert_not_called()
        command_service.record_manual_ats_liquidation_status.assert_called_once_with(
            "C:\\temp\\005930_삼성전자",
            "ats-wait-1",
            "WAITING_CANCEL_CONFIRMATION",
            cancel_order_identities=[
                {
                    "order_queued_id": "source-1",
                    "order_id": "order-1",
                    "broker_order_no": "broker-1",
                }
            ],
            cancel_readback={
                "initial_holding_qty": None,
                "pending_order_count": 1,
                "cancel_requested_count": 1,
                "cancel_pending_count": 0,
            },
        )
        self.outcome_observer.assert_not_called()

    def test_cancel_failure_records_failed_outcome_without_dispatch(self) -> None:
        window = MagicMock()
        window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
            "ok": False,
            "cancel_requested": 0,
            "cancel_pending": 0,
            "cancel_order_identities": [],
            "blocked_reasons": ["cancel queue commit failed"],
        }
        preview = {
            "ok": True,
            "code": "005930",
            "name": "삼성전자",
            "stock_dir": "C:/temp/005930_삼성전자",
            "command_id": "ats-cancel-failed",
            "requested_at": "2026-08-09T16:00:00+09:00",
            "holding_qty": 10,
            "order_candidate": {"routine": "instance-1"},
        }
        command_service = MagicMock()
        with (
            patch.object(
                ats_ops,
                "ensure_manual_ats_liquidation_request",
                return_value={"ok": True},
            ),
            patch.object(ats_ops, "read_json_dict", return_value={}),
            patch.object(ats_ops, "OperationCommandService", return_value=command_service),
            patch.object(ats_ops, "_finalize_manual_ats_liquidation_with_latest_holding") as finalize,
        ):
            result = ats_ops._start_manual_ats_liquidation_with_cancel_boundary(
                window,
                preview,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("pending_cancel", result["stage"])
        finalize.assert_not_called()
        self.assertEqual("FAILED", self.outcome_observer.call_args.kwargs["result"])
        self.assertEqual(
            "PENDING_CANCEL_FAILED",
            self.outcome_observer.call_args.kwargs["reason_code"],
        )

    def test_no_pending_orders_dispatch_immediately_without_waiting_state(self) -> None:
        window = MagicMock()
        window.queue_pending_order_cancellations_for_stock_automatically.return_value = {
            "ok": True,
            "cancel_requested": 0,
            "cancel_pending": 0,
            "cancel_order_identities": [],
        }
        preview = {
            "ok": True,
            "code": "005930",
            "stock_dir": "C:/temp/005930_삼성전자",
            "command_id": "ats-direct-1",
            "requested_at": "2026-08-09T16:00:00+09:00",
            "order_candidate": {"routine": "instance-1"},
        }
        with (
            patch.object(
                ats_ops,
                "ensure_manual_ats_liquidation_request",
                return_value={"ok": True},
            ),
            patch.object(ats_ops, "read_json_dict", return_value={}),
            patch.object(
                ats_ops,
                "_finalize_manual_ats_liquidation_with_latest_holding",
                return_value={"ok": True, "stage": "send_order"},
            ) as finalize,
        ):
            result = ats_ops._start_manual_ats_liquidation_with_cancel_boundary(
                window,
                preview,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("send_order", result["stage"])
        finalize.assert_called_once_with(window, preview)

    def test_multiple_stocks_wait_execute_and_fail_independently(self) -> None:
        window = MagicMock()
        selected = [
            (Path("C:/temp/A"), "000001", "A"),
            (Path("C:/temp/B"), "000002", "B"),
            (Path("C:/temp/C"), "000003", "C"),
        ]
        window.capture_stock_table_view_state.return_value = (set(), 0)
        window.current_runtime_file_signature.return_value = ("runtime",)
        previews = [
            {"ok": True, "code": code, "name": name, "stock_dir": str(path)}
            for path, code, name in selected
        ]
        with (
            patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                return_value={
                    "eligible": True,
                    "selected_sessions": ("extra2",),
                    "blocked_reasons": [],
                },
            ),
            patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                side_effect=previews,
            ),
            patch.object(ats_ops.QMessageBox, "question", return_value=QMessageBox.Yes),
            patch.object(ats_ops.QMessageBox, "warning"),
            patch.object(
                ats_ops,
                "_start_manual_ats_liquidation_with_cancel_boundary",
                side_effect=[
                    {"ok": True, "stage": "awaiting_cancel_confirmation"},
                    {
                        "ok": True,
                        "stage": "send_order",
                        "result_status": "SEND_CALL_ACCEPTED",
                    },
                    {
                        "ok": False,
                        "stage": "pending_cancel",
                        "blocked_reasons": ["cancel failed"],
                    },
                ],
            ) as start,
            patch.object(ats_ops, "append_stock_log"),
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "시장가",
                {"extra2": True},
                selected,
            )

        self.assertEqual(3, start.call_count)
        self.assertEqual(
            [preview for preview in previews],
            [call.args[1] for call in start.call_args_list],
        )
        window.refresh_all.assert_called_once()

    def test_cancel_effects_require_each_original_order_terminal_with_zero_remaining(self) -> None:
        request = {
            "cancel_order_identities": [
                {"order_queued_id": "source-A"},
                {"order_queued_id": "source-B"},
            ]
        }
        waiting = (
            {"id": "source-A", "status": "CANCELLED", "remaining_quantity": 0},
            {"id": "source-B", "status": "BROKER_ACCEPTED", "remaining_quantity": 2},
        )
        confirmed = (
            {"id": "source-A", "status": "PARTIAL_CANCELLED", "remaining_quantity": 0},
            {"id": "source-B", "status": "FILLED", "remaining_quantity": 0},
            {"id": "other-stock", "status": "BROKER_ACCEPTED", "remaining_quantity": 5},
        )

        self.assertFalse(ats_ops._manual_ats_cancel_effects_confirmed(request, waiting))
        self.assertTrue(ats_ops._manual_ats_cancel_effects_confirmed(request, confirmed))

    def test_timer_resume_waits_then_dispatches_from_durable_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            request = {
                "status": "WAITING_CANCEL_CONFIRMATION",
                "command_id": "ats-resume-1",
                "selected_ats_sessions": ["extra1"],
                "sell_method": "시장가",
                "cancel_order_identities": [{"order_queued_id": "source-1"}],
            }
            queue_records = {
                "ok": True,
                "records": (
                    {"id": "source-1", "status": "CANCELLED", "remaining_quantity": 0},
                ),
            }
            with (
                patch("gui_auto_trade_runtime.all_registered_stock_dirs", return_value=[stock_dir]),
                patch.object(ats_ops, "read_execution_queue_records", return_value=queue_records),
                patch.object(ats_ops, "read_json_dict", return_value={
                    ats_ops.MANUAL_ATS_LIQUIDATION_REQUEST_KEY: request
                }),
                patch.object(
                    ats_ops,
                    "_finalize_manual_ats_liquidation_with_latest_holding",
                    return_value={"ok": True, "stage": "send_order"},
                ) as finalize,
            ):
                result = ats_ops.auto_trade_continue_pending_manual_ats_liquidations(
                    MagicMock(),
                    limit=5,
                )

        self.assertEqual(1, result["processed"])
        self.assertEqual(0, result["waiting"])
        resumed_preview = finalize.call_args.args[1]
        self.assertEqual(str(stock_dir), resumed_preview["stock_dir"])
        self.assertEqual("ats-resume-1", resumed_preview["command_id"])
        self.assertEqual(["extra1"], resumed_preview["selected_ats_sessions"])

    def test_latest_holding_rebuilds_candidate_with_reconciled_quantity(self) -> None:
        window = MagicMock()
        preview = {
            "ok": True,
            "stock_dir": "C:/temp/005930_삼성전자",
            "code": "005930",
            "name": "삼성전자",
            "command_id": "ats-latest-70",
            "selected_ats_sessions": ["extra2"],
            "sell_method": "MARKET",
        }
        holding_result = {
            "ok": True,
            "holding_checked_at": "2026-08-09T16:01:00+09:00",
            "position_qty": 70,
            "broker_holding_qty": 70,
            "resolved_liquidation_qty": 70,
            "reconciliation_result": "CONSISTENT",
            "blocked_reasons": [],
        }
        command_service = MagicMock()
        command_service.record_manual_ats_liquidation_status.return_value.status = "APPLIED"
        refreshed = {"ok": True, "order_candidate": {"quantity": 70}}
        with (
            patch.object(
                ats_ops,
                "resolve_liquidation_holding_quantity",
                return_value=holding_result,
            ),
            patch.object(ats_ops, "OperationCommandService", return_value=command_service),
            patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                return_value=refreshed,
            ) as build,
            patch.object(
                ats_ops,
                "_dispatch_manual_ats_liquidation_preview",
                return_value={
                    "ok": True,
                    "stage": "send_order",
                    "result_status": "SEND_CALL_ACCEPTED",
                },
            ) as dispatch,
        ):
            result = ats_ops._finalize_manual_ats_liquidation_with_latest_holding(
                window,
                preview,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(70, build.call_args.kwargs["holding_qty_override"])
        dispatch.assert_called_once_with(window, refreshed)
        command_service.record_manual_ats_liquidation_status.assert_called_once_with(
            preview["stock_dir"],
            preview["command_id"],
            "READY_TO_RESUME",
            detail="",
            holding_readback=holding_result,
        )
        self.assertEqual("REQUESTED", self.outcome_observer.call_args.kwargs["result"])

    def test_latest_zero_holding_completes_without_order_candidate(self) -> None:
        preview = {
            "stock_dir": "C:/temp/005930_삼성전자",
            "code": "005930",
            "command_id": "ats-latest-zero",
        }
        holding_result = {
            "ok": True,
            "holding_checked_at": "2026-08-09T16:01:00+09:00",
            "position_qty": None,
            "broker_holding_qty": 0,
            "resolved_liquidation_qty": 0,
            "reconciliation_result": "CONSISTENT",
            "blocked_reasons": [],
        }
        command_service = MagicMock()
        command_service.record_manual_ats_liquidation_status.return_value.status = "APPLIED"
        with (
            patch.object(
                ats_ops,
                "resolve_liquidation_holding_quantity",
                return_value=holding_result,
            ),
            patch.object(ats_ops, "OperationCommandService", return_value=command_service),
            patch.object(ats_ops, "build_manual_ats_liquidation_preview") as build,
            patch.object(ats_ops, "_dispatch_manual_ats_liquidation_preview") as dispatch,
        ):
            result = ats_ops._finalize_manual_ats_liquidation_with_latest_holding(
                MagicMock(),
                preview,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("completed_no_holding", result["stage"])
        build.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual("COMPLETED", self.outcome_observer.call_args.kwargs["result"])
        self.assertEqual(
            "NO_REMAINING_HOLDING",
            self.outcome_observer.call_args.kwargs["reason_code"],
        )

    def test_latest_holding_mismatch_fails_without_order_candidate(self) -> None:
        preview = {
            "stock_dir": "C:/temp/005930_삼성전자",
            "code": "005930",
            "command_id": "ats-latest-mismatch",
        }
        holding_result = {
            "ok": False,
            "position_qty": 70,
            "broker_holding_qty": 71,
            "resolved_liquidation_qty": None,
            "reconciliation_result": "QUANTITY_MISMATCH",
            "blocked_reasons": ["holding quantity conflict"],
        }
        command_service = MagicMock()
        with (
            patch.object(
                ats_ops,
                "resolve_liquidation_holding_quantity",
                return_value=holding_result,
            ),
            patch.object(ats_ops, "OperationCommandService", return_value=command_service),
            patch.object(ats_ops, "build_manual_ats_liquidation_preview") as build,
            patch.object(ats_ops, "_dispatch_manual_ats_liquidation_preview") as dispatch,
        ):
            result = ats_ops._finalize_manual_ats_liquidation_with_latest_holding(
                MagicMock(),
                preview,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("holding_reconciliation", result["stage"])
        build.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual("FAILED", self.outcome_observer.call_args.kwargs["result"])


if __name__ == "__main__":
    unittest.main()
