# -*- coding: utf-8 -*-

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from gui_review_required_window import (
    GlobalReviewRequiredWindow,
    build_review_operator_guidance,
)


class ReviewOperatorGuidanceProjectionTests(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "review_reason": "운영 데이터 불일치",
            "review_location": "안정성 검사",
            "return_availability": "BLOCKED",
            "return_block_reason": "복귀 안전조건 미충족",
            "display_status": "미해결",
        }
        row.update(overrides)
        return row

    def test_missing_state_guidance(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="운영 데이터 없음",
                return_block_reason="운영 데이터 없음",
            )
        )
        self.assertEqual("운영 데이터가 없습니다.", result["block_reason"])
        self.assertIn("운영 데이터 확인", result["operator_action"])

    def test_corrupt_state_guidance(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="운영 데이터 읽기 오류",
                return_block_reason="운영 데이터 읽기 오류",
            )
        )
        self.assertEqual("운영 데이터를 읽을 수 없습니다.", result["block_reason"])

    def test_pending_integrity_guidance(self):
        result = build_review_operator_guidance(
            self._row(return_block_reason="PENDING_ORDER_DATA_INTEGRITY")
        )
        self.assertIn("미체결 상태", result["block_reason"])
        self.assertNotIn("PENDING_ORDER_DATA_INTEGRITY", str(result))

    def test_recovery_guidance(self):
        result = build_review_operator_guidance(
            self._row(return_block_reason="RECOVERY_NOT_READY")
        )
        self.assertIn("복구 상태", result["block_reason"])
        self.assertNotIn("RECOVERY_NOT_READY", str(result))

    def test_holding_residual_guidance(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="청산 후 보유잔량",
                return_block_reason="보유수량 존재",
            )
        )
        self.assertIn("보유", result["block_reason"])

    def test_emergency_guidance_requires_release_first(self):
        result = build_review_operator_guidance(
            self._row(return_block_reason="EMERGENCY_STOP_ACTIVE")
        )
        self.assertIn("긴급정지를 해제", result["operator_action"])
        self.assertNotIn("EMERGENCY_STOP_ACTIVE", str(result))

    def test_actual_block_reason_has_priority_over_review_history(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="사용자 긴급정지 / 미체결 데이터 오류",
                return_block_reason="RECOVERY_NOT_READY",
            )
        )
        self.assertIn("복구 상태", result["block_reason"])

    def test_allowed_guidance_states_stopped_without_auto_start(self):
        result = build_review_operator_guidance(
            self._row(return_availability="ALLOWED", return_block_reason="")
        )
        self.assertEqual("복귀 가능", result["summary"])
        self.assertIn("STOPPED", result["operator_action"])
        self.assertIn("자동 운영은 시작되지 않습니다", result["operator_action"])

    def test_multiple_reasons_are_deduplicated(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="미체결 데이터 오류 / 복구 상태 오류 / 미체결 데이터 오류"
            )
        )
        self.assertEqual(1, result["reason"].count("미체결 데이터 오류"))

    def test_internal_unknown_code_is_not_exposed(self):
        result = build_review_operator_guidance(
            self._row(review_reason="SOME_INTERNAL_REASON_CODE")
        )
        self.assertNotIn("SOME_INTERNAL_REASON_CODE", str(result))
        self.assertNotIn("PAUSED", str(result))


class ReviewOperatorGuidanceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_updates_existing_window_guidance_without_writer(self):
        row = {
            "routine_name": "테스트루틴",
            "stock_dir": Path("C:/isolated/000001_테스트"),
            "code": "000001",
            "name": "테스트",
            "review_location": "안정성 검사",
            "review_reason": "미체결 데이터 오류",
            "review_entered_at": "2026-08-16 10:00:00",
            "display_status": "미해결",
            "return_availability": "BLOCKED",
            "return_block_reason": "PENDING_ORDER_DATA_INTEGRITY",
        }
        with patch.object(
            GlobalReviewRequiredWindow,
            "_central_review_rows",
            return_value=[row],
        ), patch(
            "gui_review_required_window.read_review_policy",
            return_value={"long_term_holding_enabled": False},
        ), patch(
            "gui_review_required_window.write_state_json"
        ) as state_writer:
            window = GlobalReviewRequiredWindow()
            try:
                self.assertEqual("종목을 선택하세요.", window.operator_guidance_label.text())
                window.table.selectRow(0)
                self.app.processEvents()
                text = window.operator_guidance_label.text()
                self.assertIn("미체결 상태", text)
                self.assertIn("운영자 조치", text)
                state_writer.assert_not_called()
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
