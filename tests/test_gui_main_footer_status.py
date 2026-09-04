# -*- coding: utf-8 -*-

import unittest

from gui_main_footer_status import (
    OPERATOR_FOOTER_FAILURE_COLOR,
    OPERATOR_FOOTER_PROGRESS_COLOR,
    OPERATOR_FOOTER_STATE_COLOR,
    OPERATOR_FOOTER_SUCCESS_COLOR,
    OPERATOR_FOOTER_WARNING_COLOR,
    operator_footer_canonical_messages,
    project_operator_footer_message,
    should_defer_operator_footer_message,
)


class MainFooterStatusProjectionTest(unittest.TestCase):
    def assert_projection(self, raw, expected_text, expected_color) -> None:
        projection = project_operator_footer_message(raw)
        self.assertIsNotNone(projection)
        self.assertEqual(expected_text, projection.text)
        self.assertEqual(expected_color, projection.color)

    def test_canonical_vocabulary_is_fixed_to_thirty_messages(self) -> None:
        messages = operator_footer_canonical_messages()
        self.assertEqual(30, len(messages))
        self.assertEqual(30, len(set(messages)))
        self.assertEqual(8, sum(message.startswith("✓") for message in messages))
        self.assertEqual(11, sum(message.startswith("✕") for message in messages))
        self.assertEqual(7, sum(message.startswith("▷") for message in messages))
        self.assertEqual(3, sum(message.startswith("●") for message in messages))
        self.assertEqual(1, sum(message.startswith("※") for message in messages))

    def test_connection_messages_are_short_korean_operator_messages(self) -> None:
        cases = (
            ("로그인 요청됨", "▷ 로그인 중", OPERATOR_FOOTER_PROGRESS_COLOR),
            ("login succeeded", "✓ 서버 연결 완료", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("login failed: -100", "✕ 로그인 실패 (-100)", OPERATOR_FOOTER_FAILURE_COLOR),
            ("kiwoom api disconnected", "✕ 서버 연결 끊김", OPERATOR_FOOTER_FAILURE_COLOR),
            ("server connection failed", "✕ 서버 연결 실패", OPERATOR_FOOTER_FAILURE_COLOR),
            ("user info exchange failed", "✕ 사용자 정보 확인 실패", OPERATOR_FOOTER_FAILURE_COLOR),
            ("version processing failed", "✕ 버전 확인 실패", OPERATOR_FOOTER_FAILURE_COLOR),
        )
        for raw, expected, color in cases:
            with self.subTest(raw=raw):
                self.assert_projection(raw, expected, color)

    def test_auth_program_and_market_data_messages(self) -> None:
        cases = (
            ("서버 인증을 진행하고 있습니다.", "▷ 서버 인증 중", OPERATOR_FOOTER_PROGRESS_COLOR),
            ("서버 인증 완료", "✓ 서버 인증 완료", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("서버 인증 실패", "✕ 서버 인증 실패", OPERATOR_FOOTER_FAILURE_COLOR),
            ("준비 중", "▷ 준비 중", OPERATOR_FOOTER_PROGRESS_COLOR),
            ("준비 완료", "✓ 준비 완료", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("시장데이터 수신 대기", "▷ 시장데이터 수신 대기", OPERATOR_FOOTER_PROGRESS_COLOR),
            ("시장데이터 수신 정상", "✓ 시장데이터 수신 정상", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("시장데이터 수신 중단", "✕ 시장데이터 수신 중단", OPERATOR_FOOTER_FAILURE_COLOR),
        )
        for raw, expected, color in cases:
            with self.subTest(raw=raw):
                self.assert_projection(raw, expected, color)

    def test_operation_safety_and_settings_messages(self) -> None:
        cases = (
            ("운영 시작 대기", "▷ 운영 시작 대기", OPERATOR_FOOTER_PROGRESS_COLOR),
            ("기본루틴 운영시작 완료 (대상 5종목)", "✓ 운영 시작", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("운영 정지 완료", "✓ 운영 정지", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("운영 시작 실패: 상태 오류", "✕ 운영 시작 실패", OPERATOR_FOOTER_FAILURE_COLOR),
            ("운영 정지 실패", "✕ 운영 정지 실패", OPERATOR_FOOTER_FAILURE_COLOR),
            ("긴급정지 실행 완료: 4개 종목", "✕ 긴급정지", OPERATOR_FOOTER_FAILURE_COLOR),
            ("전역 긴급정지 상태입니다. 정지해제 후 다시 시도하십시오.", "✕ 긴급정지", OPERATOR_FOOTER_FAILURE_COLOR),
            ("정지해제 완료: 정상 4개", "✓ 긴급정지 해제", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("환경설정 저장 완료", "✓ 설정 저장 완료", OPERATOR_FOOTER_SUCCESS_COLOR),
            ("운영시작 대상이 없습니다.", "※ 운영 대상 없음", OPERATOR_FOOTER_WARNING_COLOR),
            ("선택한 종목은 복구 검토 대상입니다.", "✕ 서버 인증 실패", OPERATOR_FOOTER_FAILURE_COLOR),
            ("모든 등록 종목의 필수 설정이 완료되지 않았습니다.", "✕ 운영 시작 실패", OPERATOR_FOOTER_FAILURE_COLOR),
        )
        for raw, expected, color in cases:
            with self.subTest(raw=raw):
                self.assert_projection(raw, expected, color)

    def test_dynamic_operation_summary_uses_resolved_counts(self) -> None:
        cases = (
            (
                "운영 시작 4개 · 검토 제외 0개 · 설정 제외 0개 · 실패 0개",
                "✓ 운영 시작",
                OPERATOR_FOOTER_SUCCESS_COLOR,
            ),
            (
                "운영 시작 0개 · 검토 제외 2개 · 설정 제외 0개 · 실패 1개",
                "✕ 운영 시작 실패",
                OPERATOR_FOOTER_FAILURE_COLOR,
            ),
            (
                "대상종목 5 | 기운영중 1 | 운영시작 2 | 운영불가 2",
                "✓ 운영 시작",
                OPERATOR_FOOTER_SUCCESS_COLOR,
            ),
            (
                "대상종목 5 | 기운영중 0 | 운영시작 0 | 운영불가 5",
                "✕ 운영 시작 실패",
                OPERATOR_FOOTER_FAILURE_COLOR,
            ),
            (
                "대상종목 5 | 기운영중 5 | 운영시작 0 | 운영불가 0",
                "✓ 운영 시작",
                OPERATOR_FOOTER_SUCCESS_COLOR,
            ),
        )
        for raw, expected, color in cases:
            with self.subTest(raw=raw):
                self.assert_projection(raw, expected, color)

    def test_internal_execution_messages_do_not_project_to_footer(self) -> None:
        internal_messages = (
            "REAL_READY 수동 점검 완료",
            "Execution Preview 통과: ORDER-1",
            "Manual Queue commit blocked: runtime commit result is required",
            "Manual SendOrder completed",
            "ORDER_QUEUED record id is required",
            "Dispatch Claim completed",
            "Runtime Commit completed",
        )
        for raw in internal_messages:
            with self.subTest(raw=raw):
                self.assertIsNone(project_operator_footer_message(raw))

    def test_unmapped_english_message_never_leaks_to_footer(self) -> None:
        self.assertIsNone(project_operator_footer_message("arbitrary internal stage completed"))

    def test_priority_hold_only_defers_lower_priority_message(self) -> None:
        self.assertTrue(
            should_defer_operator_footer_message(
                current_priority=5,
                incoming_priority=2,
                hold_until=12.0,
                now=10.0,
            )
        )
        self.assertFalse(
            should_defer_operator_footer_message(
                current_priority=5,
                incoming_priority=5,
                hold_until=12.0,
                now=10.0,
            )
        )
        self.assertFalse(
            should_defer_operator_footer_message(
                current_priority=5,
                incoming_priority=2,
                hold_until=10.0,
                now=10.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
