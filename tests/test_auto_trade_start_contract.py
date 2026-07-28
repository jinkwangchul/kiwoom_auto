from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gui_auto_trade_run_control as run_control
import gui_auto_trade_context_menu as context_menu
from gui_auto_trade_run_control import (
    START_REQUEST_MULTIPLE,
    START_REQUEST_SINGLE,
    auto_trade_start_selected_auto_trades,
    initial_buy_start_validation,
    startup_recovery_operation_block_message,
)


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


class AutoTradeStartContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._changelog_patcher = patch(
            "gui_auto_trade_run_control.append_changelog"
        )
        self._changelog_patcher.start()
        self.addCleanup(self._changelog_patcher.stop)

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
                    "real_trade_enabled": True,
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
                auto_trade_start_selected_auto_trades(window)

        self.assertEqual(2, len(window.recalculate_calls))
        self.assertEqual(
            ["운영시작", "운영시작"],
            [call[3] for call in window.recalculate_calls],
        )
        for _stock_dir, _code, _name, _source, metadata in window.recalculate_calls:
            self.assertTrue(metadata["trade_enabled"])
            self.assertEqual(
                metadata["trade_started_at"],
                metadata["ignore_signals_before"],
            )
        append_changelog.assert_called_once()
        window.rebind_startup_recovery_after_trusted_runtime_update.assert_called_once_with()
        window.show_auto_trade_result_dialog.assert_called_once_with(
            "운영시작 처리 완료",
            "운영시작 결과",
            [
                "운영시작: 2개",
                "기운영중: 0개",
                "검토 대상 제외: 0개",
                "검토관리 이동: 0개",
                "실패: 0개",
            ],
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
        self.assertEqual("운영 시작 1개 · 검토 제외 1개", result["user_message"])
        window.statusBarMessage.assert_called_with(result["user_message"])
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

    def test_runtime_missing_reports_actionable_message_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            (stock[0] / "state.json").unlink()
            window = _StartWindow([stock])

            with patch("gui_auto_trade_run_control.append_changelog"):
                result = auto_trade_start_selected_auto_trades(window)

        self.assertFalse(result["ok"])
        self.assertIn("운영 상태 데이터를 읽을 수 없습니다.", result["user_message"])
        self.assertTrue(window._last_operation_failure_dialog_shown)
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

    def test_amount_basis_requires_previous_close_times_150_percent(self) -> None:
        blocked = initial_buy_start_validation(
            {"trade_amount_type": "AMOUNT", "buy_amount": 149_999},
            {"previous_close": 100_000},
        )
        allowed = initial_buy_start_validation(
            {"trade_amount_type": "AMOUNT", "buy_amount": 150_000},
            {"previous_close": 100_000},
        )

        self.assertFalse(blocked["allowed"])
        self.assertEqual(150_000, blocked["minimum_amount"])
        self.assertEqual("INITIAL_BUY_AMOUNT_BELOW_MINIMUM", blocked["reason"])
        self.assertTrue(allowed["allowed"])

    def test_amount_basis_without_previous_close_fails_closed(self) -> None:
        result = initial_buy_start_validation(
            {"trade_amount_type": "AMOUNT", "buy_amount": 1_000_000},
            {},
        )

        self.assertFalse(result["allowed"])
        self.assertEqual("PREVIOUS_CLOSE_UNAVAILABLE", result["reason"])

    def test_amount_below_minimum_does_not_start_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "111111", "첫종목", "inst-a", "루틴 A")
            config_path = stock[0] / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({"trade_amount_type": "AMOUNT", "buy_amount": 100_000})
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
            "초회 매수 금액이 최소 거래금액보다 작습니다.\n"
            "전일 종가의 150% 이상으로 설정한 뒤 다시 시도하십시오.",
            result["user_message"],
        )

    def test_partial_validation_exclusion_uses_status_summary_without_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            normal = self._stock(root, "111111", "정상종목", "inst-a", "루틴 A")
            blocked = self._stock(root, "222222", "설정미달", "inst-a", "루틴 A")
            config_path = blocked[0] / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({"trade_amount_type": "AMOUNT", "buy_amount": 100_000})
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
        window.show_auto_trade_result_dialog.assert_not_called()
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
        window.show_auto_trade_result_dialog.assert_called_once()

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
                "PREVIOUS_CLOSE_UNAVAILABLE",
                {"trade_amount_type": "AMOUNT", "buy_amount": 1_000_000},
                {},
                "005930 삼성전자의 전일 종가를 확인할 수 없습니다.",
            ),
            (
                "INITIAL_BUY_AMOUNT_BELOW_MINIMUM",
                {"trade_amount_type": "AMOUNT", "buy_amount": 100_000},
                {"previous_close": 100_000},
                "005930 삼성전자의 초회 매수 금액이 최소 거래금액보다 작습니다.",
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
            "005930 삼성전자의 Recovery가 아직 완료되지 않았습니다.",
            stock_result["user_message"],
        )
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
            patch.object(context_menu, "_context_menu_operation_policy", return_value={}),
            patch.object(context_menu, "_add_early_close_menu", return_value={}),
            patch.object(
                context_menu,
                "_add_individual_liquidation_menu",
                return_value={
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
