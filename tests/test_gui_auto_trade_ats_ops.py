from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt5.QtWidgets import QDialog, QMessageBox

import gui_auto_trade_ats_ops as ats_ops


class _AcceptedSellDialog:
    requested_sell_method = "시장가"

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def exec_(self) -> int:
        return QDialog.Accepted

    def values(self) -> dict[str, bool]:
        return {"extra1": True, "extra2": False, "extra3": False}


class GuiAutoTradeAtsOpsTest(unittest.TestCase):
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

    def test_sell_button_executes_without_saving_ats_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp)
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            window = MagicMock()
            selected = [(stock_dir, "005930", "삼성전자")]
            window.selected_stock_infos.return_value = selected
            window.selected_operation_mode_set.return_value = {"CONTINUOUS"}
            window.selected_manual_ats_state.return_value = {
                "extra1": True,
                "extra2": False,
                "extra3": False,
            }
            with patch.object(
                ats_ops,
                "ManualAtsSettingsDialog",
                _AcceptedSellDialog,
            ):
                ats_ops.auto_trade_open_selected_manual_ats_settings_dialog(window)

        window.execute_selected_manual_ats_liquidation.assert_called_once_with(
            "시장가",
            {"extra1": True, "extra2": False, "extra3": False},
        )
        window.save_selected_manual_ats_state.assert_not_called()

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
                "commit_manual_ats_liquidation_preview",
                return_value={
                    "ok": True,
                    "order_id": "ATS_LIQUIDATION_ats-accepted",
                    "blocked_reasons": [],
                },
            ) as commit,
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

        commit.assert_called_once()
        window.process_executable_order_for_auto_trade.assert_called_once_with(
            "ATS_LIQUIDATION_ats-accepted"
        )
        command_service.record_manual_ats_liquidation_status.assert_called_once_with(
            "C:/temp/005930",
            "ats-accepted",
            "SEND_CALL_ACCEPTED",
            order_id="ATS_LIQUIDATION_ats-accepted",
            detail="",
        )
        window.refresh_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
