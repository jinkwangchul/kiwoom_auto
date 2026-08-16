# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from gui_auto_trade_integrity import (
    operator_review_location,
    operator_review_reason,
)


class ReviewEntryContractTest(unittest.TestCase):
    def test_internal_evidence_is_not_an_operator_reason(self) -> None:
        cases = {
            "PENDING_ORDER_DATA_INTEGRITY: PENDING_ORDER_QTY_MISSING": "미체결 데이터 오류",
            "[STOCK_CODE_FORMAT] invalid code": "운영 데이터 불일치",
            "보유 0인데 평단 존재": "운영 데이터 불일치",
            "EARLY_CLOSE_EXECUTION_FAILED": "청산 처리 오류",
            "HOLDING_REMAINS": "청산 후 보유잔량",
            "RECOVERY_NOT_READY": "복구 상태 오류",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, operator_review_reason(raw))

    def test_detection_point_vocabulary(self) -> None:
        cases = {
            "PRODUCTION_RECOVERY": "프로그램 시작",
            "운영시작": "운영 시작",
            "운영중": "운영 중",
            "무결성검사": "안정성 검사",
            "긴급정지해제": "긴급정지 해제",
            "강제종료": "운영 종료",
            "사용자 긴급정지": "전체 긴급정지",
            "종목 우클릭 긴급정지": "종목 긴급정지",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, operator_review_location(raw))

    def test_legacy_paused_metadata_is_not_a_review_reason(self) -> None:
        self.assertEqual("paused_at", operator_review_reason("paused_at"))
        self.assertNotEqual("검토 필요", operator_review_reason("paused_at"))


if __name__ == "__main__":
    unittest.main()
