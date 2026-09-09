# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication, QDialog, QLabel

from candle_timeframe_aggregation import read_canonical_bar_minutes
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
    _INTERNAL_VALIDATION_MESSAGE,
    _REGISTRATION_PROCESSING_FAILURE_MESSAGE,
    _RULES_LOAD_FAILURE_MESSAGE,
    _SETTINGS_CHANGE_PROCESSING_FAILURE_MESSAGE,
    _SETTINGS_SAVE_FAILURE_MESSAGE,
    _settings_validation_user_reason,
    register_routine_instance_snapshot,
)
from mock_validation_host import MockValidationHost
from mock_validation_quick_chart import _bar_minutes
from routines.지표추종매매.routine import market_bar_projection_request


class IndicatorFollowSettingsValidationOnCommitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.source_rules_path = cls.routine_dir / "rules.json"

    def _dialog(self, rules_path: Path, *, instance_id: str = ""):
        return IndicatorFollowRoutineSettingsDialog(
            rules_path=rules_path,
            routine_path=self.routine_dir,
            routine_name="검증 루틴",
            definition_id="indicator_follow",
            instance_id=instance_id,
            settings_mode="edit" if instance_id else "registration",
        )

    def test_validation_button_is_absent_and_registration_snapshot_is_applied(self) -> None:
        before = hashlib.sha256(self.source_rules_path.read_bytes()).hexdigest()
        dialog = self._dialog(self.source_rules_path)
        try:
            self.assertFalse(hasattr(dialog, "validate_button"))
            dialog.basic_signal_interval_combo.setCurrentText("120")
            result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(120, result["rules"]["bar"]["bar_minutes"])
        self.assertNotIn("candidate_decisions", result["validation"])
        self.assertEqual(
            "120",
            result["rules"]["indicator_follow_ui_state"]["state"]["basic"][
                "basic_signal_interval_combo"
            ],
        )
        self.assertEqual(before, hashlib.sha256(self.source_rules_path.read_bytes()).hexdigest())

    def test_invalid_sell_selection_is_blocked_by_common_validator(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        try:
            state = dialog.collect_indicator_follow_ui_state()
            state["sell_ui"]["selected_sets"] = {"a": False, "b": False, "c": False}
            mapper = dialog._load_indicator_follow_rule_mapper()
            result = mapper.validate_settings_candidate(state, dialog.rules_data)
        finally:
            dialog.close()

        self.assertFalse(result["valid"])
        self.assertTrue(
            any("exactly one" in reason for reason in result["blocked_reasons"])
        )

    def test_both_situation_response_modes_are_blocked_by_common_validator(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        try:
            state = dialog.collect_indicator_follow_ui_state()
            state["buy_ui"]["situation"].update({
                "unfilled_enabled_check": True,
                "price_enabled_check": True,
            })
            mapper = dialog._load_indicator_follow_rule_mapper()
            result = mapper.validate_settings_candidate(state, dialog.rules_data)
        finally:
            dialog.close()

        self.assertFalse(result["valid"])
        self.assertIn(
            "buy situation response modes are mutually exclusive",
            result["blocked_reasons"],
        )

    def test_both_situation_response_modes_block_registration_without_write(self) -> None:
        before = self.source_rules_path.read_bytes()
        dialog = self._dialog(self.source_rules_path)
        try:
            state = dialog.collect_indicator_follow_ui_state()
            state["buy_ui"]["situation"].update({
                "unfilled_enabled_check": True,
                "price_enabled_check": True,
            })
            with patch.object(dialog, "collect_indicator_follow_ui_state", return_value=state):
                result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        self.assertFalse(result["success"])
        self.assertEqual(
            "미체결과 가격비교는 동시에 사용할 수 없습니다.\n둘 중 하나만 선택하세요.",
            result["user_messages"][0],
        )
        self.assertNotIn("mutually exclusive", result["error"])
        self.assertEqual(before, self.source_rules_path.read_bytes())

    def test_registration_both_on_error_uses_exact_korean_message(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        registration_dialog = Mock()
        registration_dialog.exec_.return_value = QDialog.Accepted
        registration_dialog.registration_request = SimpleNamespace(display_name="검증 루틴")
        message = "미체결과 가격비교는 동시에 사용할 수 없습니다.\n둘 중 하나만 선택하세요."
        state = dialog.collect_indicator_follow_ui_state()
        state["buy_ui"]["situation"].update({
            "unfilled_enabled_check": True,
            "price_enabled_check": True,
        })
        try:
            with (
                patch(
                    "gui_routine_registration_dialog.RoutineRegistrationDialog",
                    return_value=registration_dialog,
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.load_persisted_routine_instances",
                    return_value=[],
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.RoutineInstanceRepository.create_instance",
                    side_effect=AssertionError("invalid settings must not reach repository writer"),
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.QMessageBox.critical"
                ) as critical,
                patch.object(dialog, "collect_indicator_follow_ui_state", return_value=state),
            ):
                result = register_routine_instance_snapshot(
                    dialog,
                    definition_id="indicator_follow",
                    definition_display_name="지표추종매매",
                    rules_provider=dialog.build_registration_rules_from_current_ui_state,
                )
        finally:
            dialog.close()

        self.assertIsNone(result)
        critical.assert_called_once()
        self.assertEqual(dialog, critical.call_args.args[0])
        self.assertEqual("루틴 등록 실패", critical.call_args.args[1])
        self.assertTrue(critical.call_args.args[2].startswith(message))
        self.assertNotIn("mutually exclusive", critical.call_args.args[2])

    def test_both_situation_response_modes_block_change_with_korean_message(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = rules_path.read_bytes()
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            state = dialog.collect_indicator_follow_ui_state()
            state["buy_ui"]["situation"].update({
                "unfilled_enabled_check": True,
                "price_enabled_check": True,
            })
            try:
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch.object(dialog, "collect_indicator_follow_ui_state", return_value=state),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.rule_apply_commit_service.commit_approved_rule_patch_to_rules",
                        side_effect=AssertionError("invalid settings must not reach writer"),
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.QMessageBox.warning"
                    ) as warning,
                ):
                    result = dialog.save_edit_settings_and_close()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual(before, rules_path.read_bytes())
            warning.assert_called_once_with(
                dialog,
                "설정 변경 실패",
                "미체결과 가격비교는 동시에 사용할 수 없습니다.\n둘 중 하나만 선택하세요.",
            )

    def test_registration_mapper_error_is_blocked_without_source_write(self) -> None:
        before = self.source_rules_path.read_bytes()
        dialog = self._dialog(self.source_rules_path)
        try:
            with patch.object(
                dialog,
                "_load_indicator_follow_rule_mapper",
                side_effect=RuntimeError("mapper unavailable"),
            ):
                result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        self.assertFalse(result["success"])
        self.assertEqual(_REGISTRATION_PROCESSING_FAILURE_MESSAGE, result["error"])
        self.assertEqual("mapper unavailable", result["internal_error"])
        self.assertEqual(before, self.source_rules_path.read_bytes())

    def test_user_message_formatter_covers_each_action_family_without_raw_english(self) -> None:
        cases = {
            "signal runtime policy is invalid": "기본설정이 올바르지 않습니다.",
            "basic signal interval is not numeric": "신호검출 기준 분봉 설정이 올바르지 않습니다.",
            "buy signal expression is invalid: CONDITION_EXPRESSION_EMPTY": "매수 신호검출 조건 조합이 올바르지 않습니다.",
            "buy OCR threshold is not fully mapped": "매수 OCR 설정이 올바르지 않습니다.",
            "buy RSI period is not numeric": "매수 RSI 설정이 올바르지 않습니다.",
            "buy MA period is not numeric": "매수 이동평균 설정이 올바르지 않습니다.",
            "buy Bollinger threshold is not numeric": "매수 볼린저밴드 설정이 올바르지 않습니다.",
            "buy composite has no active groups": "매수 필터 조합 설정이 올바르지 않습니다.",
            "buy base MULTI_RATIO direction/comparator pair is invalid": "기본매수 설정이 올바르지 않습니다.",
            "buy repeat ACTIVE_BUY policy is invalid": "반복매수 설정이 올바르지 않습니다.",
            "buy price compare branch operators overlap": "추가비교매수의 비교 연산자 조합이 올바르지 않습니다.",
            "buy price compare left target is not mapped": "추가비교매수 설정이 올바르지 않습니다.",
            "buy situation unfilled timeout is invalid": "상황변화대응 설정이 올바르지 않습니다.",
            "buy additional enabled values must be boolean": "추가매수 설정이 올바르지 않습니다.",
            "buy cycle mode is invalid": "순환설정이 올바르지 않습니다.",
            "buy exit count is not numeric": "매수 이탈조건 또는 회차마감 설정이 올바르지 않습니다.",
            "sell method selected_sets must select exactly one": "매도 설정을 하나만 선택하세요.",
            "sell condition A OCR threshold is not numeric": "매도조건 A 설정이 올바르지 않습니다.",
            "sell condition B Price Box policy is invalid": "매도조건 B 설정이 올바르지 않습니다.",
            "sell condition C MACD value is not numeric": "매도조건 C 설정이 올바르지 않습니다.",
            "sell signal expression is invalid: CONDITION_EXPRESSION_EMPTY": "매도 신호 조합이 올바르지 않습니다.",
            "sell profit_rate_sell profit_rate_percent is not numeric": "수익률 매도 설정이 올바르지 않습니다.",
            "final_diff buy condition missing from post rules": _INTERNAL_VALIDATION_MESSAGE.splitlines()[0],
        }
        for internal_reason, expected_first_line in cases.items():
            with self.subTest(internal_reason=internal_reason):
                user_message = _settings_validation_user_reason(internal_reason)
                self.assertEqual(expected_first_line, user_message.splitlines()[0])
                self.assertNotIn(internal_reason, user_message)

    def test_price_compare_detail_family_covers_all_branches_without_english(self) -> None:
        for direction, direction_text in (("below", "하단"), ("above", "상단")):
            for basis, basis_text in (("round", "회차기준"), ("budget", "예산기준")):
                reason = f"buy price compare {direction} {basis} policy is invalid"
                with self.subTest(reason=reason):
                    user_message = _settings_validation_user_reason(reason)
                    self.assertEqual(
                        f"추가비교매수 {direction_text} 조건의 {basis_text} 설정이 올바르지 않습니다.\n"
                        "설정값을 확인하세요.",
                        user_message,
                    )
                    self.assertNotIn(reason, user_message)

    def test_unknown_internal_reason_uses_korean_fail_safe_fallback(self) -> None:
        reason = "UNKNOWN INTERNAL BUY VALIDATOR FAILURE XYZ"
        self.assertEqual(_INTERNAL_VALIDATION_MESSAGE, _settings_validation_user_reason(reason))
        self.assertNotIn(reason, _settings_validation_user_reason(reason))

    def test_registration_deduplicates_same_family_and_preserves_internal_reasons(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        internal_reasons = [
            "buy OCR threshold is not fully mapped",
            "buy OCR order delay is not a non-negative integer",
            "sell condition A RSI period is not numeric",
        ]
        mapper = SimpleNamespace(
            validate_settings_candidate=Mock(return_value={
                "valid": False,
                "blocked_reasons": internal_reasons,
            })
        )
        try:
            with patch.object(dialog, "_load_indicator_follow_rule_mapper", return_value=mapper):
                result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        self.assertFalse(result["success"])
        self.assertEqual(internal_reasons, result["internal_blocked_reasons"])
        self.assertEqual(2, len(result["user_messages"]))
        self.assertEqual(1, result["error"].count("매수 OCR 설정이 올바르지 않습니다."))
        self.assertNotIn("buy OCR", result["error"])

    def test_registration_provider_exception_hides_raw_detail_and_path(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        registration_dialog = Mock()
        registration_dialog.exec_.return_value = QDialog.Accepted
        registration_dialog.registration_request = SimpleNamespace(display_name="검증 루틴")
        raw_error = r"registration failed at C:\secret\rules.json hash mismatch"
        try:
            with (
                patch(
                    "gui_routine_registration_dialog.RoutineRegistrationDialog",
                    return_value=registration_dialog,
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.load_persisted_routine_instances",
                    return_value=[],
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.QMessageBox.critical"
                ) as critical,
            ):
                result = register_routine_instance_snapshot(
                    dialog,
                    definition_id="indicator_follow",
                    definition_display_name="지표추종매매",
                    rules_provider=Mock(side_effect=RuntimeError(raw_error)),
                )
        finally:
            dialog.close()

        self.assertIsNone(result)
        critical.assert_called_once_with(
            dialog,
            "루틴 등록 실패",
            _REGISTRATION_PROCESSING_FAILURE_MESSAGE,
        )
        self.assertEqual(raw_error, dialog._last_routine_registration_error)
        self.assertNotIn("C:\\secret", critical.call_args.args[2])

    def test_repository_error_hides_raw_detail_and_preserves_internal_evidence(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        registration_dialog = Mock()
        registration_dialog.exec_.return_value = QDialog.Accepted
        registration_dialog.registration_request = SimpleNamespace(display_name="검증 루틴")
        raw_error = r"create failed at C:\secret\routine_instances"
        repository_result = SimpleNamespace(
            success=False,
            instance=None,
            error_code="INSTANCE_CREATE_FAILED",
            error=raw_error,
        )
        try:
            with (
                patch(
                    "gui_routine_registration_dialog.RoutineRegistrationDialog",
                    return_value=registration_dialog,
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.load_persisted_routine_instances",
                    return_value=[],
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.RoutineInstanceRepository.create_instance",
                    return_value=repository_result,
                ),
                patch(
                    "gui_indicator_follow_routine_settings_dialog.QMessageBox.critical"
                ) as critical,
            ):
                result = register_routine_instance_snapshot(
                    dialog,
                    definition_id="indicator_follow",
                    definition_display_name="지표추종매매",
                    rules_provider=lambda: {"success": True, "rules": {}},
                )
        finally:
            dialog.close()

        self.assertIsNone(result)
        critical.assert_called_once_with(
            dialog,
            "루틴 등록 실패",
            _REGISTRATION_PROCESSING_FAILURE_MESSAGE,
        )
        self.assertEqual(raw_error, dialog._last_routine_registration_error)

    def test_rules_load_errors_hide_exception_and_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_path = Path(temp_dir) / "rules.json"
            invalid_path.write_text("{not-json", encoding="utf-8")
            with patch(
                "gui_indicator_follow_routine_settings_dialog.QMessageBox.critical"
            ) as critical:
                dialog = self._dialog(invalid_path)
            try:
                critical.assert_called_once_with(dialog, "로드 실패", _RULES_LOAD_FAILURE_MESSAGE)
                self.assertTrue(dialog._last_rules_load_error)
                self.assertNotIn(str(invalid_path), critical.call_args.args[2])
            finally:
                dialog.close()

            missing_path = Path(temp_dir) / "missing-rules.json"
            with patch(
                "gui_indicator_follow_routine_settings_dialog.QMessageBox.warning"
            ) as warning:
                dialog = self._dialog(missing_path)
            try:
                warning.assert_called_once_with(
                    dialog,
                    "설정 파일 없음",
                    "설정 파일을 찾을 수 없습니다.\n파일과 저장 상태를 확인하세요.",
                )
                self.assertNotIn(str(missing_path), warning.call_args.args[2])
            finally:
                dialog.close()

    def test_change_atomic_exception_hides_raw_detail_and_preserves_internal_evidence(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = rules_path.read_bytes()
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            raw_error = r"atomic commit failed at C:\secret\rules.json hash mismatch"
            dialog.basic_signal_interval_combo.setCurrentText("120")
            try:
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.rule_apply_commit_service.commit_approved_rule_patch_to_rules",
                        side_effect=RuntimeError(raw_error),
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.QMessageBox.warning"
                    ) as warning,
                ):
                    result = dialog.save_edit_settings_and_close()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual(before, rules_path.read_bytes())
            self.assertEqual(raw_error, result["internal_error"])
            self.assertEqual([_SETTINGS_CHANGE_PROCESSING_FAILURE_MESSAGE], result["blocked_reasons"])
            warning.assert_called_once_with(
                dialog,
                "설정 변경 실패",
                _SETTINGS_CHANGE_PROCESSING_FAILURE_MESSAGE,
            )
            self.assertNotIn("C:\\secret", warning.call_args.args[2])

    def test_composite_group_limit_warning_is_korean(self) -> None:
        dialog = self._dialog(self.source_rules_path)
        try:
            dialog.buy_composite_warning_label = QLabel("")
            dialog._apply_buy_composite_ui_state({"groups": [{}, {}, {}]})
            message = dialog.buy_composite_warning_label.text()
        finally:
            dialog.close()

        self.assertEqual(
            "현재 매수 필터 조합은 이 화면에서 지원하는 그룹 수를 초과합니다. "
            "기존 설정은 유지되지만 이 화면에서는 편집할 수 없습니다.",
            message,
        )
        self.assertNotIn("Composite setting", message)

    def test_price_compare_operators_commit_and_reopen_without_forced_override(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            try:
                dialog.buy_price_compare_check.setChecked(True)
                with patch(
                    "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                    return_value=instance,
                ):
                    result = dialog.commit_current_settings()
                    self.assertTrue(result["success"], result.get("blocked_reasons"))
                    applied = json.loads(rules_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        ["<=", ">"],
                        [
                            item["operator"]
                            for item in applied["buy"]["filters"]["price_compare"]["conditions"]
                        ],
                    )

                    dialog.buy_price_compare_condition_combo.setCurrentText("<")
                    result = dialog.commit_current_settings()
                    self.assertTrue(result["success"], result.get("blocked_reasons"))
                    applied = json.loads(rules_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        ["<", ">"],
                        [
                            item["operator"]
                            for item in applied["buy"]["filters"]["price_compare"]["conditions"]
                        ],
                    )

                    dialog.buy_price_compare_above_condition_combo.setCurrentText(">=")
                    result = dialog.commit_current_settings()
                    self.assertTrue(result["success"], result.get("blocked_reasons"))
            finally:
                dialog.close()

            applied = json.loads(rules_path.read_text(encoding="utf-8"))
            self.assertEqual(
                ["<", ">="],
                [
                    item["operator"]
                    for item in applied["buy"]["filters"]["price_compare"]["conditions"]
                ],
            )
            before_reopen = rules_path.read_bytes()
            reopened = self._dialog(rules_path, instance_id="instance-id")
            try:
                self.assertEqual("<", reopened.buy_price_compare_condition_combo.currentText())
                self.assertEqual(">=", reopened.buy_price_compare_above_condition_combo.currentText())
            finally:
                reopened.close()
            self.assertEqual(before_reopen, rules_path.read_bytes())

    def test_situation_price_response_independent_slots_commit_and_reopen(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            try:
                dialog.buy_situation_response_price_enabled_check.setChecked(True)
                dialog.buy_situation_response_setting1_left_combo.setCurrentText("평단가")
                dialog.buy_situation_response_setting1_right_combo.setCurrentText("현재가")
                dialog.buy_situation_response_setting1_direction_combo.setCurrentText("상향")
                dialog.buy_situation_response_setting1_ratio_line.setText("0.15")
                dialog.buy_situation_response_setting1_compare_combo.setCurrentText("이상")
                dialog.buy_situation_response_setting1_action_combo.setCurrentText("일괄취소")
                dialog.buy_situation_response_setting2_left_combo.setCurrentText("평단가")
                dialog.buy_situation_response_setting2_right_combo.setCurrentText("현재가")
                dialog.buy_situation_response_setting2_direction_combo.setCurrentText("하향")
                dialog.buy_situation_response_setting2_ratio_line.setText("0.10")
                dialog.buy_situation_response_setting2_compare_combo.setCurrentText("이하")
                dialog.buy_situation_response_setting2_action_combo.setCurrentText("매수리셋")
                with patch(
                    "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                    return_value=instance,
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertTrue(result["success"], result.get("blocked_reasons"))
            applied = json.loads(rules_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [
                    {
                        "slot": "SETTING1",
                        "enabled": True,
                        "left_source": "AVG_PRICE",
                        "right_source": "CURRENT_PRICE",
                        "direction": "UP",
                        "threshold_percent": 0.15,
                        "compare": ">=",
                        "action": "CANCEL_BATCH",
                    },
                    {
                        "slot": "SETTING2",
                        "enabled": True,
                        "left_source": "AVG_PRICE",
                        "right_source": "CURRENT_PRICE",
                        "direction": "DOWN",
                        "threshold_percent": 0.10,
                        "compare": "<=",
                        "action": "RESET",
                    },
                ],
                applied["buy"]["execution"]["base"]["buy_price_response_policies"],
            )

            reopened = self._dialog(rules_path, instance_id="instance-id")
            try:
                self.assertEqual(
                    {
                        "setting1_left_combo": "평단가",
                        "setting1_right_combo": "현재가",
                        "setting1_direction_combo": "상향",
                        "setting1_ratio_line": "0.15",
                        "setting1_compare_combo": "이상",
                        "setting1_action_combo": "일괄취소",
                        "setting2_left_combo": "평단가",
                        "setting2_right_combo": "현재가",
                        "setting2_direction_combo": "하향",
                        "setting2_ratio_line": "0.10",
                        "setting2_compare_combo": "이하",
                        "setting2_action_combo": "매수리셋",
                    },
                    {
                        key: value
                        for key, value in reopened.collect_indicator_follow_ui_state()["buy_ui"][
                            "situation"
                        ].items()
                        if key.startswith("setting1_") or key.startswith("setting2_")
                        if not key.endswith("enabled_check")
                    },
                )
            finally:
                reopened.close()

    def test_overlapping_price_compare_operators_block_commit_without_write(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = rules_path.read_bytes()
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            state = dialog.collect_indicator_follow_ui_state()
            state["buy_ui"]["price_compare"].update({
                "check": True,
                "condition_combo": "<=",
                "above_condition_combo": ">=",
            })
            try:
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch.object(dialog, "collect_indicator_follow_ui_state", return_value=state),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.rule_apply_commit_service.commit_approved_rule_patch_to_rules",
                        side_effect=AssertionError("overlap must not reach writer"),
                    ),
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual(
                "추가비교매수의 비교 연산자 조합이 올바르지 않습니다.\n"
                "상단과 하단 조건을 확인하세요.",
                result["blocked_reasons"][0],
            )
            self.assertIn(
                "buy price compare branch operators overlap",
                result["internal_blocked_reasons"],
            )
            self.assertEqual(before, rules_path.read_bytes())

    def test_invalid_price_compare_budget_message_is_korean_and_does_not_write(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = rules_path.read_bytes()
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            dialog.buy_price_compare_check.setChecked(True)
            dialog.buy_price_compare_mode_combo.setCurrentText("예산기준")
            dialog.buy_price_compare_budget_ratio_line.setText("")
            try:
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.QMessageBox.warning"
                    ) as warning,
                ):
                    result = dialog.save_edit_settings_and_close()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual(before, rules_path.read_bytes())
            warning.assert_called_once_with(
                dialog,
                "설정 변경 실패",
                "추가비교매수 하단 조건의 예산기준 설정이 올바르지 않습니다.\n"
                "설정값을 확인하세요.",
            )
            self.assertNotIn(
                "buy price compare below budget policy is invalid",
                warning.call_args.args[2],
            )

    def test_change_atomically_applies_each_supported_representative_interval(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            try:
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.rule_approval_session_file_service.load_rule_approval_session",
                        side_effect=AssertionError("approval session must not be read"),
                    ),
                ):
                    for interval in (1, 5, 120, 240):
                        dialog.basic_signal_interval_combo.setCurrentText(str(interval))
                        result = dialog.commit_current_settings()
                        self.assertTrue(result["success"], result.get("blocked_reasons"))
                        applied = json.loads(rules_path.read_text(encoding="utf-8"))
                        self.assertEqual(interval, applied["bar"]["bar_minutes"])
                        self.assertEqual(interval, read_canonical_bar_minutes(applied))
                        self.assertIn(
                            market_bar_projection_request(applied)["projection"],
                            {"COMPLETED_TIMEFRAME", "FORMING_BASE_BAR"},
                        )
                        self.assertTrue(all(result["read_back_checks"].values()))

                    before_sell = json.loads(rules_path.read_text(encoding="utf-8"))[
                        "sell"
                    ]["signals"]["ui_condition_a"]
                    dialog.sell_signal_condition_a_ocr_value_line.setText("72")
                    result = dialog.commit_current_settings()
                    self.assertTrue(result["success"], result.get("blocked_reasons"))
                    after_sell = json.loads(rules_path.read_text(encoding="utf-8"))[
                        "sell"
                    ]["signals"]["ui_condition_a"]
                    self.assertNotEqual(before_sell, after_sell)
                    self.assertEqual(
                        "72",
                        dialog.collect_indicator_follow_ui_state()["sell_ui"]
                        ["signal_conditions"]["condition_a"]["ocr_value_line"],
                    )
            finally:
                dialog.close()

            reopened = self._dialog(rules_path, instance_id="instance-id")
            try:
                self.assertEqual("240", reopened.basic_signal_interval_combo.currentText())
                self.assertEqual(240, reopened.rules_data["bar"]["bar_minutes"])
            finally:
                reopened.close()

    def test_invalid_change_does_not_mutate_applied_rules(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = rules_path.read_bytes()
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            invalid_state = dialog.collect_indicator_follow_ui_state()
            invalid_state["sell_ui"]["selected_sets"] = {
                "a": True,
                "b": True,
                "c": False,
            }
            try:
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch.object(dialog, "collect_indicator_follow_ui_state", return_value=invalid_state),
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual(before, rules_path.read_bytes())
            self.assertEqual("매도 설정을 하나만 선택하세요.", result["blocked_reasons"][0])
            self.assertTrue(result["internal_blocked_reasons"])
            self.assertNotIn("commit_result", result)

    def test_writer_failure_preserves_applied_rules(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            before = rules_path.read_bytes()
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id="instance-id")
            try:
                dialog.basic_signal_interval_combo.setCurrentText("120")
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.rule_apply_commit_service.commit_approved_rule_patch_to_rules",
                        return_value={
                            "ok": False,
                            "committed": False,
                            "blocked_reasons": ["writer failed"],
                        },
                    ),
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual(before, rules_path.read_bytes())
            self.assertEqual([_SETTINGS_SAVE_FAILURE_MESSAGE], result["blocked_reasons"])
            self.assertEqual(["writer failed"], result["internal_blocked_reasons"])

    def test_read_back_mismatch_rolls_back_to_previous_applied_rules(self) -> None:
        source = json.loads(self.source_rules_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "validated-backup.json"
            payload = json.dumps(source, ensure_ascii=False, indent=2) + "\n"
            rules_path.write_text(payload, encoding="utf-8")
            backup_path.write_text(payload, encoding="utf-8")
            instance = SimpleNamespace(
                instance_id="instance-id",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )

            def corrupt_after_write(*_args, **_kwargs):
                rules_path.write_text('{"corrupt": true}\n', encoding="utf-8")
                return {
                    "ok": True,
                    "committed": True,
                    "backup_path": str(backup_path),
                    "post_rules_hash": "mismatch",
                }

            dialog = self._dialog(rules_path, instance_id="instance-id")
            try:
                dialog.basic_signal_interval_combo.setCurrentText("120")
                with (
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                        return_value=instance,
                    ),
                    patch(
                        "gui_indicator_follow_routine_settings_dialog.rule_apply_commit_service.commit_approved_rule_patch_to_rules",
                        side_effect=corrupt_after_write,
                    ),
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertFalse(result["success"])
            self.assertEqual("SETTINGS_READ_BACK_BLOCKED", result["stage"])
            self.assertTrue(result["rollback_result"]["ok"])
            self.assertEqual(source, json.loads(rules_path.read_text(encoding="utf-8")))

    def test_mock_operation_rules_snapshot_wins_over_registration_snapshot(self) -> None:
        document = {
            "reference_snapshot": {
                "routine_instances": [
                    {
                        "routine_instance_id": "A",
                        "rules_snapshot": {"bar": {"bar_minutes": 1}},
                    }
                ]
            },
            "mock_operation_lifecycle": {
                "current": None,
                "instance_operations": {
                    "A": {
                        "state": "RUNNING",
                        "operation_policy_snapshot": {
                            "mock_instance_rules_snapshot": {
                                "bar": {"bar_minutes": 5}
                            }
                        },
                    }
                },
            },
        }
        rules = MockValidationHost._operation_rules(document, "A")
        self.assertEqual(5, rules["bar"]["bar_minutes"])
        self.assertEqual(5, _bar_minutes(document, "A", rules))
        rules["bar"]["bar_minutes"] = 240
        self.assertEqual(
            5,
            document["mock_operation_lifecycle"]["instance_operations"]["A"][
                "operation_policy_snapshot"
            ]["mock_instance_rules_snapshot"]["bar"]["bar_minutes"],
        )

    def test_next_mock_operation_reads_latest_applied_rules(self) -> None:
        host = MockValidationHost.__new__(MockValidationHost)
        host.project_root = self.project_root
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            instance = SimpleNamespace(rules_path=rules_path)
            with patch("mock_validation_host.routine_instance_by_id", return_value=instance):
                for interval in (1, 5, 240):
                    rules_path.write_text(
                        json.dumps({"bar": {"bar_minutes": interval}}),
                        encoding="utf-8",
                    )
                    self.assertEqual(
                        interval,
                        host._latest_applied_rules("A")["bar"]["bar_minutes"],
                    )

    def test_mock_operation_start_snapshots_latest_applied_without_hot_swap(self) -> None:
        host = MockValidationHost.__new__(MockValidationHost)
        host.project_root = self.project_root
        document = {
            "revision": 1,
            "session": {
                "state": "RUNNING",
                "validation_session_id": "S",
                "mock_tax_enabled": True,
                "mock_tax_rate": 0.002,
            },
            "instance_execution": {"A": {}},
        }
        host.current_session = lambda _stock_code: document
        host._operation_snapshot = lambda: {}
        host._instance_effective_settings = lambda *_args, **_kwargs: {}
        host._publish_projection_if_changed = lambda: None
        host.lifecycle = SimpleNamespace(
            start_instance_operation=Mock(return_value={"ok": True})
        )
        now = datetime(2026, 9, 8, 10, 0, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            instance = SimpleNamespace(rules_path=rules_path)
            with patch("mock_validation_host.routine_instance_by_id", return_value=instance):
                rules_path.write_text('{"bar":{"bar_minutes":1}}', encoding="utf-8")
                host.start_instance_operation("005930", "A", as_of=now)
                operation_one = deepcopy(
                    host.lifecycle.start_instance_operation.call_args.kwargs[
                        "operation_policy_snapshot"
                    ]
                )

                rules_path.write_text('{"bar":{"bar_minutes":5}}', encoding="utf-8")
                host.start_instance_operation("005930", "A", as_of=now)
                operation_two = deepcopy(
                    host.lifecycle.start_instance_operation.call_args.kwargs[
                        "operation_policy_snapshot"
                    ]
                )

                rules_path.write_text('{"bar":{"bar_minutes":240}}', encoding="utf-8")
                host.start_instance_operation("005930", "A", as_of=now)
                operation_three = deepcopy(
                    host.lifecycle.start_instance_operation.call_args.kwargs[
                        "operation_policy_snapshot"
                    ]
                )

        self.assertEqual(1, operation_one["mock_instance_rules_snapshot"]["bar"]["bar_minutes"])
        self.assertEqual(5, operation_two["mock_instance_rules_snapshot"]["bar"]["bar_minutes"])
        self.assertEqual(240, operation_three["mock_instance_rules_snapshot"]["bar"]["bar_minutes"])


if __name__ == "__main__":
    unittest.main()
