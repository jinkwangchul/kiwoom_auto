# -*- coding: utf-8 -*-

import os
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QItemSelectionModel, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication

from gui_styles import (
    PLAIN_HEADER_GRID_COLOR_PROPERTY,
    REGISTERED_STOCK_STATUS_GRID_COLOR,
    registered_stock_status_table_stylesheet,
)

from gui_review_required_window import (
    GlobalReviewRequiredWindow,
    build_review_operator_guidance,
    review_operator_readiness_evidence,
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
            self._row(return_block_reason="RECOVERY_NOT_READY"),
            readiness_evidence={"cause": "SERVER_DISCONNECTED"},
        )
        self.assertIn("서버에 연결", result["block_reason"])
        self.assertNotIn("Recovery", str(result))
        self.assertNotIn("복구 상태", str(result))
        self.assertNotIn("RECOVERY_NOT_READY", str(result))

    def test_recovery_guidance_uses_specific_live_readiness_cause(self):
        expectations = {
            "SERVER_DISCONNECTED": "서버에 연결",
            "ACCOUNT_NOT_SELECTED": "계좌가 선택",
            "ACCOUNT_CHECK_INCOMPLETE": "계좌 확인이 완료",
            "ACCOUNT_OPERATION_CHECK_IN_PROGRESS": "확인 중",
            "RECOVERY_IDENTITY_MISMATCH": "이전 확인 상태와 일치하지",
            "ACCOUNT_OPERATION_CHECK_INCOMPLETE": "계좌 및 운영 상태 확인",
        }
        for cause, expected in expectations.items():
            with self.subTest(cause=cause):
                result = build_review_operator_guidance(
                    self._row(return_block_reason="RECOVERY_NOT_READY"),
                    readiness_evidence={"cause": cause},
                )
                self.assertIn(expected, result["block_reason"])
                self.assertNotIn("Recovery", str(result))

    def test_live_readiness_evidence_priority(self):
        connected_api = SimpleNamespace(
            is_connected=lambda: True,
            login_session_id=lambda: "LOGIN-1",
        )

        disconnected = SimpleNamespace(
            kiwoom_api=SimpleNamespace(is_connected=lambda: False),
            selected_account_no=lambda: "12345678",
        )
        self.assertEqual(
            "SERVER_DISCONNECTED",
            review_operator_readiness_evidence(disconnected)["cause"],
        )

        no_account = SimpleNamespace(
            kiwoom_api=connected_api,
            selected_account_no=lambda: "",
        )
        self.assertEqual(
            "ACCOUNT_NOT_SELECTED",
            review_operator_readiness_evidence(no_account)["cause"],
        )

        unchecked = SimpleNamespace(
            kiwoom_api=connected_api,
            selected_account_no=lambda: "12345678",
            _account_authentication_states={"12345678": "LOADING"},
            _account_query_states={"12345678": "READY"},
        )
        self.assertEqual(
            "ACCOUNT_CHECK_INCOMPLETE",
            review_operator_readiness_evidence(unchecked)["cause"],
        )

        identity = SimpleNamespace(
            account_no="12345678",
            login_session_id="LOGIN-1",
            trading_day=date.today().isoformat(),
        )
        checking = SimpleNamespace(
            kiwoom_api=connected_api,
            selected_account_no=lambda: "12345678",
            _account_authentication_states={"12345678": "READY"},
            _account_query_states={"12345678": "READY"},
        )
        with patch(
            "gui_review_required_window.production_recovery_registry.snapshot",
            return_value=SimpleNamespace(
                identity=identity,
                account_status="COLLECTING",
            ),
        ):
            self.assertEqual(
                "ACCOUNT_OPERATION_CHECK_IN_PROGRESS",
                review_operator_readiness_evidence(checking)["cause"],
            )

        with patch(
            "gui_review_required_window.production_recovery_registry.snapshot",
            return_value=SimpleNamespace(
                identity=SimpleNamespace(
                    account_no="87654321",
                    login_session_id="LOGIN-OLD",
                    trading_day=identity.trading_day,
                ),
                account_status="COMPLETED",
            ),
        ):
            self.assertEqual(
                "RECOVERY_IDENTITY_MISMATCH",
                review_operator_readiness_evidence(checking)["cause"],
            )

    def test_holding_residual_guidance(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="청산 후 보유잔량",
                return_block_reason="보유수량 존재",
            )
        )
        self.assertIn("보유", result["block_reason"])

    def test_emergency_release_holding_reason_is_not_active_emergency(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="긴급정지 해제 시 보유잔량 존재",
                display_status="미해결",
                return_block_reason="보유수량 존재",
                holding_qty=3,
            )
        )
        self.assertNotIn("긴급정지가 활성", str(result))
        self.assertIn("보유잔량", result["block_reason"])
        self.assertIn("상태재판정", result["operator_action"])

    def test_emergency_word_without_explicit_active_evidence_is_not_emergency(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="긴급정지 해제 후 운영 상태 확인",
                display_status="미해결",
                return_block_reason="복귀 안전조건 미충족",
            )
        )
        self.assertNotIn("긴급정지가 활성", str(result))

    def test_emergency_guidance_requires_release_first(self):
        result = build_review_operator_guidance(
            self._row(
                display_status="긴급정지",
                return_block_reason="EMERGENCY_STOP_ACTIVE",
            )
        )
        self.assertIn("긴급정지를 해제", result["operator_action"])
        self.assertNotIn("EMERGENCY_STOP_ACTIVE", str(result))

    def test_close_liquidation_guidance(self):
        result = build_review_operator_guidance(
            self._row(return_block_reason="ACTIVE_CLOSE_OR_LIQUIDATION")
        )
        self.assertIn("청산 처리가 진행 중", result["block_reason"])
        self.assertIn("상태재판정", result["operator_action"])

    def test_actual_block_reason_has_priority_over_review_history(self):
        result = build_review_operator_guidance(
            self._row(
                review_reason="사용자 긴급정지 / 미체결 데이터 오류",
                return_block_reason="RECOVERY_NOT_READY",
            ),
            readiness_evidence={"cause": "ACCOUNT_NOT_SELECTED"},
        )
        self.assertIn("계좌가 선택", result["block_reason"])

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
                self.assertNotIn("사유:", text)
                self.assertNotIn("검출:", text)
                self.assertNotIn("새로고침", text)
                self.assertIn("상태재판정", text)
                state_writer.assert_not_called()
            finally:
                window.close()

    def test_review_table_reuses_registered_stock_status_style_for_all_row_states(self):
        def row(code, status, availability, location="안정성 검사"):
            return {
                "routine_name": "테스트루틴",
                "stock_dir": Path(f"C:/isolated/{code}_테스트"),
                "code": code,
                "name": f"테스트{code}",
                "review_location": location,
                "review_reason": "운영 데이터 확인",
                "review_entered_at": "2026-08-16 10:00:00",
                "display_status": status,
                "return_availability": availability,
                "return_block_reason": (
                    "" if availability == "ALLOWED" else "복귀 안전조건 미충족"
                ),
            }

        rows = []

        def assert_white_content(window):
            window.show()
            self.app.processEvents()
            grid_color = REGISTERED_STOCK_STATUS_GRID_COLOR
            body_background = (
                window.table.viewport().palette().color(QPalette.Base).name()
            )
            self.assertEqual(
                "#ffffff", window.table.palette().color(QPalette.Base).name()
            )
            self.assertEqual(
                "#ffffff", window.table.viewport().palette().color(QPalette.Base).name()
            )
            self.assertEqual(
                "#ffffff", window.table.horizontalHeader()._section_background().name()
            )
            self.assertEqual(
                grid_color,
                window.table.horizontalHeader().property(
                    PLAIN_HEADER_GRID_COLOR_PROPERTY
                ),
            )
            self.assertTrue(window.table.showGrid())
            self.assertEqual(Qt.SolidLine, window.table.gridStyle())
            self.assertTrue(window.table.verticalHeader().isHidden())
            self.assertLessEqual(
                window.table.viewport().geometry().left(),
                window.table.frameWidth() + 1,
            )
            self.assertEqual(
                registered_stock_status_table_stylesheet(
                    window.table.objectName(),
                    body_background,
                ),
                window.table.styleSheet(),
            )
            self.assertIn(f"gridline-color: {grid_color}", window.table.styleSheet())
            self.assertIn("selection-background-color: #dbeafe", window.table.styleSheet())
            self.assertIn("background: #ffffff", window.table.styleSheet())
            self.assertIn("background: #ffffff", window.operator_guidance_label.styleSheet())
            self.assertIn("color: #000000", window.operator_guidance_label.styleSheet())
            for row_index in range(window.table.rowCount()):
                for column in range(window.table.columnCount()):
                    item = window.table.item(row_index, column)
                    self.assertIsNotNone(item)
                    self.assertEqual("#ffffff", item.background().color().name())
                    self.assertEqual("#000000", item.foreground().color().name())

        with patch.object(
            GlobalReviewRequiredWindow,
            "_central_review_rows",
            side_effect=lambda: list(rows),
        ), patch(
            "gui_review_required_window.read_review_policy",
            return_value={"long_term_holding_enabled": False},
        ):
            window = GlobalReviewRequiredWindow()
            try:
                assert_white_content(window)  # empty

                rows[:] = [row("000001", "미해결", "BLOCKED")]
                window.load_review_items()
                assert_white_content(window)  # unresolved

                rows[:] = [row("000002", "해결", "ALLOWED")]
                window.load_review_items()
                assert_white_content(window)  # resolved

                rows[:] = [
                    row("000003", "검토정지", "BLOCKED", "종목 긴급정지"),
                    row("000004", "미해결", "BLOCKED"),
                    row("000005", "해결", "ALLOWED"),
                ]
                window.load_review_items()
                assert_white_content(window)  # multiple / selected emergency

                window.table.selectRow(0)
                self.app.processEvents()
                assert_white_content(window)  # selected

                rows[0]["display_status"] = "해결"
                rows[0]["return_availability"] = "ALLOWED"
                rows[0]["return_block_reason"] = ""
                window.load_review_items()
                assert_white_content(window)  # status re-evaluation projection
            finally:
                window.close()

            reopened = GlobalReviewRequiredWindow()
            try:
                assert_white_content(reopened)
            finally:
                reopened.close()

    def test_refresh_is_in_top_summary_row_and_keeps_existing_reload_contract(self):
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
        rows = [row]
        with patch.object(
            GlobalReviewRequiredWindow,
            "_central_review_rows",
            side_effect=lambda: list(rows),
        ), patch(
            "gui_review_required_window.read_review_policy",
            return_value={"long_term_holding_enabled": False},
        ):
            window = GlobalReviewRequiredWindow()
            try:
                root_layout = window.layout()
                top_layout = root_layout.itemAt(0).layout()
                bottom_layout = root_layout.itemAt(root_layout.count() - 1).layout()
                self.assertEqual("상태재판정", window.btn_refresh.text())
                self.assertGreaterEqual(top_layout.indexOf(window.summary_label), 0)
                self.assertGreaterEqual(top_layout.indexOf(window.btn_refresh), 0)
                self.assertEqual(-1, bottom_layout.indexOf(window.btn_refresh))

                window.table.selectRow(0)
                row["display_status"] = "해결"
                row["return_availability"] = "ALLOWED"
                row["return_block_reason"] = ""
                rows.append(
                    {
                        **row,
                        "stock_dir": Path("C:/isolated/000002_테스트2"),
                        "code": "000002",
                        "name": "테스트2",
                    }
                )
                window.btn_refresh.click()
                self.app.processEvents()

                self.assertEqual("검토종목: 2개", window.summary_label.text())
                self.assertEqual("해결", window.table.item(0, 3).text())
                self.assertIn("복귀 가능", window.operator_guidance_label.text())
            finally:
                window.close()

    def test_return_and_unassign_share_allowed_selection_enabled_state(self):
        def row(code, status, availability):
            return {
                "routine_name": "테스트루틴",
                "stock_dir": Path(f"C:/isolated/{code}_테스트"),
                "code": code,
                "name": f"테스트{code}",
                "review_location": "안정성 검사",
                "review_reason": "운영 데이터 불일치",
                "review_entered_at": "2026-08-16 10:00:00",
                "display_status": status,
                "return_availability": availability,
                "return_block_reason": (
                    "" if availability == "ALLOWED" else "복귀 안전조건 미충족"
                ),
            }

        rows = [
            row("000001", "미해결", "BLOCKED"),
            row("000002", "긴급정지", "BLOCKED"),
            row("000003", "해결", "ALLOWED"),
        ]
        with patch.object(
            GlobalReviewRequiredWindow,
            "_central_review_rows",
            return_value=rows,
        ), patch(
            "gui_review_required_window.read_review_policy",
            return_value={"long_term_holding_enabled": False},
        ):
            window = GlobalReviewRequiredWindow()
            try:
                self.assertFalse(window.btn_return.isEnabled())
                self.assertFalse(window.btn_unassign.isEnabled())

                window.table.selectRow(0)
                self.app.processEvents()
                self.assertFalse(window.btn_return.isEnabled())
                self.assertFalse(window.btn_unassign.isEnabled())

                window.table.selectRow(1)
                self.app.processEvents()
                self.assertFalse(window.btn_return.isEnabled())
                self.assertFalse(window.btn_unassign.isEnabled())

                window.table.selectRow(2)
                self.app.processEvents()
                self.assertTrue(window.btn_return.isEnabled())
                self.assertTrue(window.btn_unassign.isEnabled())

                selection_model = window.table.selectionModel()
                selection_model.select(
                    window.table.model().index(0, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
                self.app.processEvents()
                self.assertTrue(window.btn_return.isEnabled())
                self.assertTrue(window.btn_unassign.isEnabled())

                rows[2]["return_availability"] = "BLOCKED"
                window.refresh_operator_guidance()
                self.assertFalse(window.btn_return.isEnabled())
                self.assertFalse(window.btn_unassign.isEnabled())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
