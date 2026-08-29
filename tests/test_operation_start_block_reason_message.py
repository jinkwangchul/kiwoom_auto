from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_run_control as run_control
import gui_windows
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_auto_trade_run_control import (
    auto_trade_start_selected_auto_trades,
    operation_start_result_summary_toast_text,
)


class _BlockedStartWindow:
    def __init__(self, targets, details):
        self.targets = list(targets)
        self.details = tuple(details)
        self.status_messages = []
        viewport = SimpleNamespace(update=lambda: None)
        self.stock_table = SimpleNamespace(
            viewport=lambda: viewport,
            repaint=lambda: None,
        )
        self.show_auto_trade_result_dialog = Mock()

    def selected_stock_infos(self):
        return list(self.targets)

    def split_start_targets(self, selected):
        self.split_selected = list(selected)
        return [], [
            f"{code} {name}(자동마감)" for _stock_dir, code, name in selected
        ]

    def start_target_block_details(self):
        return self.details

    def statusBarMessage(self, message):
        self.status_messages.append(str(message))


class OperationStartBlockReasonMessageTest(unittest.TestCase):
    def test_recovery_filter_collects_all_block_reasons_without_admitting(self):
        targets = [
            (Path("stocks/000001"), "000001", "첫째"),
            (Path("stocks/000002"), "000002", "둘째"),
            (Path("stocks/000003"), "000003", "셋째"),
        ]
        decisions = iter(
            (
                SimpleNamespace(allowed=True, reason_code="RECOVERY_COMPLETED"),
                SimpleNamespace(allowed=False, reason_code="RECOVERY_STOCK_PENDING"),
                SimpleNamespace(allowed=False, reason_code="RECOVERY_STOCK_FAILED"),
            )
        )
        window = SimpleNamespace(
            production_recovery_gate_for_stock=lambda *_args, **_kwargs: next(decisions),
            production_recovery_block_user_message=lambda decision: decision.reason_code,
        )

        result = gui_windows.MainWindow.filter_start_targets_by_production_recovery(
            window,
            targets,
            caller_name="운영시작",
        )

        self.assertFalse(result["allowed"])
        self.assertEqual("RECOVERY_STOCK_PENDING", result["reason"])
        self.assertEqual((targets[0],), result["eligible"])
        self.assertEqual(
            ("RECOVERY_STOCK_PENDING", "RECOVERY_STOCK_FAILED"),
            tuple(item["reason"] for item in result["blocked_target_details"]),
        )

    def test_split_preserves_canonical_final_session_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "002810_삼영무역"
            stock.mkdir()
            (stock / "state.json").write_text(
                '{"status":"AUTO_CLOSE"}', encoding="utf-8"
            )
            (stock / "config.json").write_text(
                '{"operation_mode":"SCHEDULED"}', encoding="utf-8"
            )
            host = AutoTradeOperationHost(None)
            with patch(
                "gui_auto_trade_policy.auto_trade_operation_session_phase",
                return_value={"phase": "FINAL_SESSION_ENDED"},
            ):
                targets, skipped = host.split_start_targets(
                    [(stock, "002810", "삼영무역")]
                )

        self.assertEqual([], targets)
        self.assertEqual(["002810 삼영무역(자동마감)"], skipped)
        self.assertEqual(
            "FINAL_SESSION_ENDED",
            host.start_target_block_details()[0]["reason"],
        )

    def test_setting_split_blocks_stopped_scheduled_target_after_final_end(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "002810_삼영무역"
            stock.mkdir()
            (stock / "state.json").write_text(
                '{"status":"STOPPED","trade_enabled":false}', encoding="utf-8"
            )
            (stock / "config.json").write_text(
                '{"operation_mode":"SCHEDULED","start_time":"09:00:00",'
                '"end_buy_time":"13:30:00"}',
                encoding="utf-8",
            )
            target = (stock, "002810", "삼영무역")
            window = SimpleNamespace(
                start_target_is_review_isolated=lambda _stock_dir, _code: False,
            )
            with patch(
                "gui_auto_trade_policy.auto_trade_operation_session_phase",
                return_value={"phase": "FINAL_SESSION_ENDED"},
            ):
                targets, skipped = AutoTradeSettingWindow.split_start_targets(
                    window,
                    [target],
                )

        self.assertEqual([], targets)
        self.assertEqual(1, len(skipped))
        details = AutoTradeSettingWindow.start_target_block_details(window)
        self.assertEqual("FINAL_SESSION_ENDED", details[0]["reason"])

    def test_reason_groups_keep_mixed_running_context(self):
        details = (
            {
                "stock_code": "002810",
                "stock_name": "삼영무역",
                "reason": "FINAL_SESSION_ENDED",
                "display_label": "002810 삼영무역",
            },
            {
                "stock_code": "005070",
                "stock_name": "코스모신소재",
                "reason": "FINAL_SESSION_ENDED",
                "display_label": "005070 코스모신소재",
            },
        )

        message = run_control._start_failure_user_message(
            [],
            blocked_target_details=details,
            already_running_targets=((Path("stocks/012210"), "012210", "삼미금속"),),
        )

        self.assertIn("운영중 유지: 1종목", message)
        self.assertIn("시간운영 종료: 2종목", message)
        self.assertIn("- 002810 삼영무역", message)
        self.assertNotIn("검토관리와 자동매매 설정을 확인하십시오", message)

    def test_blocked_only_result_preserves_final_session_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "002810_삼영무역"
            stock.mkdir()
            (stock / "state.json").write_text(
                '{"status":"AUTO_CLOSE"}', encoding="utf-8"
            )
            (stock / "config.json").write_text(
                '{"operation_mode":"SCHEDULED"}', encoding="utf-8"
            )
            target = (stock, "002810", "삼영무역")
            window = _BlockedStartWindow(
                [target],
                ({
                    "stock_code": "002810",
                    "stock_name": "삼영무역",
                    "reason": "FINAL_SESSION_ENDED",
                    "display_label": "002810 삼영무역",
                },),
            )

            with (
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(run_control, "_global_start_prerequisite_result", return_value=None),
                patch.object(run_control, "_show_start_failure_once"),
            ):
                result = auto_trade_start_selected_auto_trades(
                    window,
                    selected_targets=[target],
                    source="test",
                )

        self.assertFalse(result["ok"])
        self.assertEqual("NO_STARTABLE_TARGETS", result["reason"])
        self.assertEqual("FINAL_SESSION_ENDED", result["blocked_target_details"][0]["reason"])
        self.assertIn("002810 삼영무역은 시간운영 종료로", result["user_message"])
        self.assertNotIn("현재 운영을 시작할 수 있는 종목이 없습니다", result["user_message"])

    def test_mixed_result_reports_running_and_blocked_groups(self):
        blocked = (
            {
                "stock_code": "002810",
                "stock_name": "삼영무역",
                "reason": "FINAL_SESSION_ENDED",
                "display_label": "002810 삼영무역",
            },
        )
        target = (Path("tests/002810_삼영무역"), "002810", "삼영무역")
        running = (Path("tests/012210_삼미금속"), "012210", "삼미금속")
        window = _BlockedStartWindow([target], blocked)

        with (
            patch.object(run_control, "read_operation_state", return_value={}),
            patch.object(run_control, "_global_start_prerequisite_result", return_value=None),
            patch.object(run_control, "_show_start_failure_once"),
        ):
            result = auto_trade_start_selected_auto_trades(
                window,
                selected_targets=[target],
                already_running_targets=[running],
                source="test",
            )

        self.assertEqual(2, result["requested_count"])
        self.assertEqual(1, result["already_running_count"])
        self.assertEqual(1, result["blocked_count"])
        self.assertEqual(0, result["eligible_count"])
        self.assertEqual(0, result["started_count"])
        self.assertEqual(0, result["failed_count"])
        self.assertIn("운영중 유지: 1종목", result["user_message"])
        self.assertIn("시간운영 종료: 1종목", result["user_message"])
        window.show_auto_trade_result_dialog.assert_not_called()
        self.assertEqual(
            "대상종목 2  |  기운영중 1  |  운영시작 0  |  운영불가 1\n시간운영 종료 1",
            operation_start_result_summary_toast_text(result),
        )

    def test_summary_toast_cases_use_counts_and_reason_groups(self):
        def detail(code, reason, mode="SCHEDULED"):
            return {
                "stock_code": code,
                "reason": reason,
                "operation_mode": mode,
            }

        self.assertEqual(
            "대상종목 5  |  기운영중 0  |  운영시작 5  |  운영불가 0",
            operation_start_result_summary_toast_text(
                {"started_count": 5, "blocked_count": 0, "failed_count": 0}
            ),
        )
        self.assertEqual(
            "대상종목 5  |  기운영중 0  |  운영시작 1  |  운영불가 4\n시간운영 종료 4",
            operation_start_result_summary_toast_text(
                {
                    "started_count": 1,
                    "blocked_count": 4,
                    "failed_count": 0,
                    "blocked_target_details": tuple(
                        detail(str(index), "FINAL_SESSION_ENDED")
                        for index in range(4)
                    ),
                }
            ),
        )
        self.assertEqual(
            "대상종목 5  |  기운영중 0  |  운영시작 0  |  운영불가 5\n수동운영 종료 1 · 시간운영 종료 4",
            operation_start_result_summary_toast_text(
                {
                    "started_count": 0,
                    "blocked_count": 5,
                    "failed_count": 0,
                    "blocked_target_details": (
                        detail("continuous", "FINAL_SESSION_ENDED", "CONTINUOUS"),
                        *(detail(str(index), "FINAL_SESSION_ENDED") for index in range(4)),
                    ),
                }
            ),
        )
        self.assertEqual(
            "대상종목 5  |  기운영중 1  |  운영시작 2  |  운영불가 2\n시간운영 종료 2",
            operation_start_result_summary_toast_text(
                {
                    "started_count": 2,
                    "already_running_count": 1,
                    "blocked_count": 2,
                    "failed_count": 0,
                    "already_running_targets": ((Path("running"), "r", "r"),),
                    "blocked_target_details": (
                        detail("a", "FINAL_SESSION_ENDED"),
                        detail("b", "FINAL_SESSION_ENDED"),
                    ),
                }
            ),
        )
        self.assertEqual(
            "대상종목 3  |  기운영중 0  |  운영시작 0  |  운영불가 3\n검토관리 1 · 복구 미완료 1 · 시간운영 종료 1",
            operation_start_result_summary_toast_text(
                {
                    "started_count": 0,
                    "blocked_count": 3,
                    "failed_count": 0,
                    "blocked_target_details": (
                        detail("review", "REVIEW_REQUIRED"),
                        detail("recovery", "RECOVERY_STOCK_PENDING"),
                        detail("final", "FINAL_SESSION_ENDED"),
                    ),
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
