from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

import gui_auto_trade_ats_ops as ats_ops
import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_unregister as unregister_ops
from gui_toast import show_toast


class AutoTradeMultiTargetContextOpsTest(unittest.TestCase):

    @unittest.skip("모달 확인 방식 복원으로 등록해제 결과 토스트 helper는 사용하지 않는다.")
    def test_unregister_result_toast_text_contract(self) -> None:
        self.assertEqual(
            "등록해제 10종목",
            unregister_ops.unregister_result_toast_text(10, 0, []),
        )
        self.assertEqual(
            "등록해제 8종목 | 해제불가 1종목 (LG화학)",
            unregister_ops.unregister_result_toast_text(8, 1, ["LG화학"]),
        )
        self.assertEqual(
            "등록해제 8종목 | 해제불가 4종목 (LG화학, SK하이닉스, 카카오게임즈, 셀트리온)",
            unregister_ops.unregister_result_toast_text(
                8,
                4,
                ["LG화학", "SK하이닉스", "카카오게임즈", "셀트리온"],
            ),
        )
        self.assertEqual(
            "등록해제 12종목 | 해제불가 6종목 (LG화학, SK하이닉스, 카카오게임즈 외 3종목)",
            unregister_ops.unregister_result_toast_text(
                12,
                6,
                ["LG화학", "SK하이닉스", "카카오게임즈", "셀트리온", "KB금융", "NAVER"],
            ),
        )

    def test_common_toast_auto_close_clears_parent_reference(self) -> None:
        app = QApplication.instance() or QApplication([])
        parent = QDialog()
        parent.show()
        toast = show_toast(parent, "자동 종료", duration_ms=30)
        app.processEvents()

        QTest.qWait(60)
        app.processEvents()

        self.assertFalse(toast.isVisible())
        self.assertIsNone(getattr(parent, "_common_toast_message", None))
        parent.close()

    def test_old_toast_cleanup_does_not_clear_new_toast(self) -> None:
        app = QApplication.instance() or QApplication([])
        parent = QDialog()
        parent.show()
        first = show_toast(parent, "첫 번째", duration_ms=0)
        second = show_toast(parent, "두 번째", duration_ms=0)
        first.deleteLater()
        app.processEvents()

        self.assertIs(second, parent._common_toast_message)
        self.assertTrue(second.isVisible())
        parent.close()

    def test_parent_destroy_with_live_toast_has_no_callback_exception(self) -> None:
        app = QApplication.instance() or QApplication([])
        parent = QDialog()
        parent.show()
        toast = show_toast(parent, "종료 중", duration_ms=2000)
        app.processEvents()

        with patch.object(sys, "excepthook") as excepthook:
            parent.deleteLater()
            QTest.qWait(20)
            app.processEvents()

        excepthook.assert_not_called()

    def test_schedule_change_uses_fixed_targets_and_aggregates_partial_failure(self) -> None:
        targets = [
            (Path("C:/stocks/111111"), "111111", "첫번째"),
            (Path("C:/stocks/222222"), "222222", "두번째"),
        ]
        parent = SimpleNamespace(refresh_all=Mock())
        window = SimpleNamespace(
            selected_stock_infos=Mock(return_value=targets),
            current_selected_routine_name=Mock(return_value=""),
            update_stock_operation_mode=Mock(side_effect=[True, False]),
            recalculate_stock_status_by_operation_policy=Mock(
                return_value=("unchanged", "STOPPED", "STOPPED")
            ),
            refresh_all=Mock(),
            parent=Mock(return_value=parent),
        )
        with (
            patch.object(status_ops, "append_changelog"),
            patch.object(status_ops.QMessageBox, "warning") as warning,
        ):
            result = status_ops.auto_trade_set_selected_operation_mode(
                window,
                "SCHEDULED",
                {"start_time": "09:10:00", "end_buy_time": "13:20:00"},
            )

        self.assertEqual(2, result["requested"])
        self.assertEqual(1, result["succeeded"])
        self.assertEqual(1, result["failed"])
        self.assertEqual(
            [
                call(
                    targets[0][0],
                    "111111",
                    "첫번째",
                    "SCHEDULED",
                    {"start_time": "09:10:00", "end_buy_time": "13:20:00"},
                ),
                call(
                    targets[1][0],
                    "222222",
                    "두번째",
                    "SCHEDULED",
                    {"start_time": "09:10:00", "end_buy_time": "13:20:00"},
                ),
            ],
            window.update_stock_operation_mode.call_args_list,
        )
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        warning.assert_called_once()

    @unittest.skip("모달 확인 방식 복원으로 즉시 실행 helper는 사용하지 않는다.")
    def test_unregister_resolves_each_target_instance_and_keeps_partial_failure(self) -> None:
        targets = [
            (Path("C:/stocks/111111"), "111111", "첫번째"),
            (Path("C:/stocks/222222"), "222222", "두번째"),
        ]
        configs = {
            str(targets[0][0] / "config.json"): {
                "assigned_routine_instance_id": "instance-a",
                "routine_instance_name": "루틴 A",
            },
            str(targets[1][0] / "config.json"): {
                "assigned_routine_instance_id": "instance-b",
                "routine_instance_name": "루틴 B",
            },
        }
        category_calls: list[tuple[str, str]] = []

        def category(routine_name, stock_dir, code, name):
            category_calls.append((routine_name, code))
            return {
                "category": "immediate",
                "code": code,
                "name": name,
                "runtime_dirs": [(routine_name, stock_dir)],
                "reasons": [],
            }

        dialog = Mock()
        dialog.exec_.return_value = QDialog.Accepted
        dialog.selected_items.return_value = []
        parent = Mock()
        window = Mock()
        window.parent.return_value = parent
        window.current_selected_routine_name.return_value = "fallback"

        with (
            patch.object(
                unregister_ops,
                "read_json_dict",
                side_effect=lambda path: configs.get(str(path), {}),
            ),
            patch.object(
                unregister_ops,
                "auto_trade_unregister_category",
                side_effect=category,
            ),
            patch.object(
                unregister_ops,
                "update_base_stock_routines",
                side_effect=[True, False],
            ),
            patch.object(unregister_ops, "append_changelog"),
            patch.object(unregister_ops.QMessageBox, "warning") as warning,
            patch.object(unregister_ops, "show_toast") as toast,
            patch.object(unregister_ops.QMessageBox, "information"),
        ):
            result = unregister_ops.unregister_auto_trade_stock_targets(
                window,
                targets,
            )

        self.assertEqual([("루틴 A", "111111"), ("루틴 B", "222222")], category_calls)
        self.assertEqual(1, result["succeeded"])
        self.assertEqual(1, result["failed"])
        parent.refresh_all.assert_called_once_with()
        window.refresh_all.assert_called_once_with()
        warning.assert_not_called()
        toast.assert_called_once_with(window, "등록해제 1종목 | 해제불가 1종목 (두번째)")

    @unittest.skip("모달 확인 방식 복원으로 즉시 실행 토스트 경로는 사용하지 않는다.")
    def test_unregister_possible_targets_runs_immediately_with_toast(self) -> None:
        app = QApplication.instance() or QApplication([])
        self._qt_app = app
        target = (Path("C:/stocks/111111"), "111111", "가능종목")
        window = Mock()
        parent = Mock()
        stock_box = QDialog()
        self.addCleanup(stock_box.close)
        window.parent.return_value = parent
        window.stock_box = stock_box
        configs = {
            str(target[0] / "config.json"): {
                "assigned_routine_instance_id": "instance-a",
                "routine_instance_name": "루틴 A",
            },
        }

        with (
            patch.object(unregister_ops, "read_json_dict", side_effect=lambda path: configs.get(str(path), {})),
            patch.object(
                unregister_ops,
                "auto_trade_unregister_category",
                return_value={
                    "category": "immediate",
                    "code": "111111",
                    "name": "가능종목",
                    "runtime_dirs": [],
                    "reasons": [],
                },
            ),
            patch.object(unregister_ops, "update_base_stock_routines", return_value=True),
            patch.object(unregister_ops, "append_changelog"),
            patch.object(unregister_ops, "show_toast") as toast,
            patch.object(unregister_ops.QMessageBox, "warning") as warning,
            patch.object(unregister_ops.QMessageBox, "information") as information,
        ):
            result = unregister_ops.unregister_auto_trade_stock_targets(window, [target])

        self.assertEqual(1, result["succeeded"])
        self.assertEqual(0, result["failed"])
        toast.assert_called_once_with(window, "등록해제 1종목")
        warning.assert_not_called()
        information.assert_not_called()

    @unittest.skip("모달 확인 방식 복원으로 즉시 실행 helper는 사용하지 않는다.")
    def test_unregister_review_required_flag_is_auto_excluded_from_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            targets: list[tuple[Path, str, str]] = []
            for index in range(10):
                code = f"{index + 1:06d}"
                name = "ReviewStock" if index == 9 else f"Stock{index + 1}"
                stock_dir = root / "stocks" / f"{code}_{name}"
                stock_dir.mkdir(parents=True)
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "routine_instance_name": "Routine A",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                state = {
                    "status": "STOPPED",
                    "holding_qty": 0,
                    "buy_pending_qty": 0,
                    "sell_pending_qty": 0,
                }
                if index == 9:
                    state["review_required"] = True
                (stock_dir / "state.json").write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                (stock_dir / "orders.json").write_text("[]", encoding="utf-8")
                targets.append((stock_dir, code, name))

            window = Mock()
            window.parent.return_value = Mock()

            with (
                patch.object(unregister_ops, "update_base_stock_routines", return_value=True) as update_routines,
                patch.object(unregister_ops, "append_changelog"),
                patch.object(unregister_ops, "show_toast") as toast,
                patch.object(unregister_ops.QMessageBox, "warning") as warning,
                patch.object(unregister_ops.QMessageBox, "information") as information,
            ):
                result = unregister_ops.unregister_auto_trade_stock_targets(window, targets)

        self.assertEqual(9, result["succeeded"])
        self.assertEqual(1, result["failed"])
        self.assertEqual(9, update_routines.call_count)
        toast.assert_called_once_with(
            window,
            "등록해제 9종목 | 해제불가 1종목 (ReviewStock)",
        )
        warning.assert_not_called()
        information.assert_not_called()

    @unittest.skip("모달 확인 방식 복원으로 즉시 실행 토스트 경로는 사용하지 않는다.")
    def test_unregister_blocked_targets_are_auto_excluded_with_toast(self) -> None:
        targets = [
            (Path("C:/stocks/111111"), "111111", "불가1"),
            (Path("C:/stocks/222222"), "222222", "불가2"),
        ]
        window = Mock()
        parent = Mock()
        window.parent.return_value = parent
        configs = {
            str(stock_dir / "config.json"): {
                "assigned_routine_instance_id": "instance-a",
                "routine_instance_name": "루틴 A",
            }
            for stock_dir, _code, _name in targets
        }

        def category(_routine_name, _stock_dir, code, name):
            return {
                "category": "blocked",
                "code": code,
                "name": name,
                "runtime_dirs": [],
                "reasons": ["검토종목"],
            }

        with (
            patch.object(unregister_ops, "read_json_dict", side_effect=lambda path: configs.get(str(path), {})),
            patch.object(unregister_ops, "auto_trade_unregister_category", side_effect=category),
            patch.object(unregister_ops, "update_base_stock_routines") as update_routines,
            patch.object(unregister_ops, "show_toast") as toast,
            patch.object(unregister_ops.QMessageBox, "warning") as warning,
            patch.object(unregister_ops.QMessageBox, "information") as information,
        ):
            result = unregister_ops.unregister_auto_trade_stock_targets(window, targets)

        self.assertEqual(0, result["succeeded"])
        self.assertEqual(2, result["failed"])
        update_routines.assert_not_called()
        toast.assert_called_once_with(window, "등록해제 0종목 | 해제불가 2종목 (불가1, 불가2)")
        warning.assert_not_called()
        information.assert_not_called()

    def test_ats_writer_blocks_entire_mixed_target_snapshot(self) -> None:
        targets = [
            (Path("C:/stocks/111111"), "111111", "수동"),
            (Path("C:/stocks/222222"), "222222", "시간"),
            (Path("C:/stocks/333333"), "333333", "저장실패"),
        ]
        configs = {
            str(targets[0][0] / "config.json"): {"operation_mode": "CONTINUOUS"},
            str(targets[1][0] / "config.json"): {"operation_mode": "SCHEDULED"},
            str(targets[2][0] / "config.json"): {"operation_mode": "CONTINUOUS"},
        }
        window = Mock()
        window.capture_stock_table_view_state.return_value = (set(), 0)
        window.current_runtime_file_signature.return_value = ()

        with (
            patch.object(
                ats_ops,
                "read_json_dict",
                side_effect=lambda path: configs[str(path)],
            ),
            patch.object(
                ats_ops,
                "write_manual_ats_runtime_selection",
            ) as writer,
            patch.object(ats_ops, "append_stock_log"),
        ):
            result = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                window,
                targets,
                {"extra1": True},
            )

        self.assertEqual(3, result["requested"])
        self.assertEqual(0, result["succeeded"])
        self.assertEqual(0, result["failed"])
        self.assertEqual(1, result["excluded"])
        self.assertEqual(
            [
                "BLOCKED_MIXED_OPERATION_MODE",
                "EXCLUDED",
                "BLOCKED_MIXED_OPERATION_MODE",
            ],
            [item["status"] for item in result["results"]],
        )
        writer.assert_not_called()

    def test_ats_writer_keeps_partial_read_back_failure_for_manual_targets(
        self,
    ) -> None:
        targets = [
            (Path("C:/stocks/111111"), "111111", "수동1"),
            (Path("C:/stocks/222222"), "222222", "수동2"),
        ]
        window = Mock()
        window.capture_stock_table_view_state.return_value = (set(), 0)
        window.current_runtime_file_signature.return_value = ()

        with (
            patch.object(
                ats_ops,
                "read_json_dict",
                return_value={"operation_mode": "CONTINUOUS"},
            ),
            patch.object(
                ats_ops,
                "write_manual_ats_runtime_selection",
                side_effect=[True, False],
            ),
            patch.object(ats_ops, "append_stock_log"),
        ):
            result = ats_ops.auto_trade_save_manual_ats_state_for_targets(
                window,
                targets,
                {"extra1": True},
            )

        self.assertEqual(2, result["requested"])
        self.assertEqual(1, result["succeeded"])
        self.assertEqual(1, result["failed"])
        self.assertEqual(0, result["excluded"])


if __name__ == "__main__":
    unittest.main()
