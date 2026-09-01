import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics, QPalette
from PyQt5.QtWidgets import QApplication, QHeaderView

import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_runtime as runtime
import gui_routine_policy as routine_policy
import gui_stock_register_window as stock_register_window
import gui_review_required_window as review_window
from gui_auto_trade_policy import auto_trade_setting_display_status_for_current_session
from gui_styles import registered_stock_status_table_stylesheet


class ReviewRequiredTransitionTimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_reader_uses_only_official_transition_timestamp(self) -> None:
        self.assertEqual(
            "2026-07-28 11:42:15",
            review_window.review_entered_at_display(
                {"review_entered_at": "2026-07-28 11:42:15"}
            ),
        )
        self.assertEqual(
            "미기록",
            review_window.review_entered_at_display(
                {"review_checked_at": "2026-07-28 12:00:00"}
            ),
        )

    def test_state_writer_blocks_review_required_to_normal_status_without_review_exit(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_status": "PENDING",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = runtime.write_state_json(
                stock_dir,
                {"status": "MONITORING", "review_required": False},
            )

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(result)
            self.assertEqual("REVIEW_REQUIRED", saved["status"])
            self.assertTrue(saved["review_required"])

    def test_state_writer_blocks_corrupt_state_to_normal_status(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "051910_LG화학"
            stock_dir.mkdir(parents=True)
            state_path = stock_dir / "state.json"
            state_path.write_text("{ invalid review state }", encoding="utf-8")

            result = runtime.write_state_json(
                stock_dir,
                {"status": "MONITORING", "review_required": False},
            )

            self.assertFalse(result)
            self.assertEqual("{ invalid review state }", state_path.read_text(encoding="utf-8"))

    def test_state_writer_allows_review_required_entry_and_review_exit_exception(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)

            self.assertTrue(
                runtime.write_state_json(
                    stock_dir,
                    {"status": "REVIEW_REQUIRED", "review_required": True},
                )
            )
            self.assertTrue(
                runtime.write_state_json(
                    stock_dir,
                    {"status": "MONITORING", "review_required": False},
                    allow_review_state_transition=True,
                )
            )

            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("MONITORING", saved["status"])
            self.assertFalse(saved["review_required"])

    def test_status_update_cannot_move_review_required_to_normal_status(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_status": "PENDING",
                        "review_entered_at": "2026-08-02 11:43:23",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            window = SimpleNamespace(_operation_start_batch_active=True)
            with patch.object(status_ops, "append_stock_log") as append_log:
                result = status_ops.auto_trade_update_stock_status(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    "MONITORING",
                )

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(result)
            self.assertEqual("REVIEW_REQUIRED", saved["status"])
            self.assertTrue(saved["review_required"])
            append_log.assert_called_once()

    def test_review_detection_event_display_uses_operator_facing_events(self) -> None:
        cases = {
            "운영시작": "운영 시작",
            "운영중": "운영 중",
            "안정성검사": "안정성 검사",
            "긴급정지해제": "긴급정지 해제",
            "강제종료": "운영 종료",
            "종목등록 창 미체결 데이터 무결성 오류": "종목 등록",
            "등록해제 미체결 데이터 무결성 오류": "종목 해제",
            "루틴 이동 미체결 데이터 무결성 오류": "루틴 등록",
            "루틴 해제 미체결 데이터 무결성 오류": "루틴 해제",
            "PRODUCTION_RECOVERY": "프로그램 시작",
            "": "미기록",
            "-": "미기록",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, review_window.review_detection_event_display(raw))

    def test_review_display_status_keeps_pending_unresolved_and_resolved_solved(self) -> None:
        self.assertEqual(
            "미해결",
            review_window._review_display_status_for_collected_row(
                {"status": "REVIEW_REQUIRED", "review_required": True, "review_status": "PENDING"},
                return_availability="BLOCKED",
            ),
        )
        self.assertEqual(
            "해결",
            review_window._review_display_status_for_collected_row(
                {"status": "REVIEW_REQUIRED", "review_required": True, "review_status": "RESOLVED"},
                return_availability="ALLOWED",
            ),
        )

    def test_review_display_status_keeps_emergency_before_pending_fallback(self) -> None:
        self.assertEqual(
            "긴급정지",
            review_window._review_display_status_for_collected_row(
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_status": "PENDING",
                    "emergency_reason": "운영 데이터 불일치",
                },
                review_location_source="운영중",
            ),
        )

    def test_review_display_status_distinguishes_selected_and_global_emergency(self) -> None:
        selected_state = {
            "status": "EMERGENCY_STOPPED",
            "emergency_scope": "SELECTED",
            "review_required": True,
            "review_status": "PENDING",
        }
        global_state = {
            **selected_state,
            "emergency_scope": "GLOBAL",
        }

        self.assertEqual(
            "미해결",
            review_window._review_display_status_for_collected_row(selected_state),
        )
        self.assertEqual(
            "긴급정지",
            review_window._review_display_status_for_collected_row(global_state),
        )

    def test_selected_emergency_uses_review_projection(self) -> None:
        selected_state = {
            "status": "EMERGENCY_STOPPED",
            "emergency_scope": "SELECTED",
            "review_required": True,
            "review_status": "PENDING",
        }
        shared_display = auto_trade_setting_display_status_for_current_session(
            selected_state,
            {},
            holding_qty=0,
            buy_pending_qty=0,
            sell_pending_qty=0,
            current_session_trade_started=False,
            persisted_trade_started=False,
        )
        review_display = review_window._review_display_status_for_collected_row(
            selected_state,
            return_availability="BLOCKED",
        )

        self.assertEqual("검토종목", shared_display)
        self.assertEqual("미해결", review_display)

    def test_selected_release_removes_review_emergency_label(self) -> None:
        released_state = {
            "status": "REVIEW_REQUIRED",
            "emergency_scope": "",
            "review_required": True,
            "review_status": "RESOLVED",
        }

        self.assertEqual(
            "해결",
            review_window._review_display_status_for_collected_row(
                released_state,
                return_availability="ALLOWED",
            ),
        )

    def test_corrupt_state_is_protected_by_routine_policy_gates(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "051910_LG화학"
            stock_dir.mkdir(parents=True)
            (stock_dir / "state.json").write_text("{ invalid review state }", encoding="utf-8")

            guard_info = {
                "code": "051910",
                "name": "LG화학",
                "routine_name": "지표추종매매",
                "stock_dir": stock_dir,
                "state": {},
                "raw_status": "STOPPED",
                "display_status": "감시/대기",
                "holding_qty": 0,
                "buy_pending_qty": 0,
                "sell_pending_qty": 0,
            }
            with patch.object(
                routine_policy,
                "routine_action_guard_info",
                return_value=dict(guard_info),
            ):
                allowed, info = routine_policy.routine_action_reasons_for_stock(
                    "051910",
                    "LG화학",
                    allow_unassigned=True,
                )

            self.assertFalse(allowed)
            self.assertEqual(["검토관리"], info["reasons"])

            unassign_decision = SimpleNamespace(
                allowed=False,
                evidence={"persisted_routine_fields": ("지표추종매매",)},
                user_reasons=("검토관리",),
            )
            with patch.object(
                routine_policy,
                "routine_unassign_decision",
                return_value=unassign_decision,
            ):
                can_unassign, routine_name, reasons = (
                    routine_policy.can_unassign_active_routine_from_stock(
                        "051910",
                        "LG화학",
                    )
                )

            self.assertFalse(can_unassign)
            self.assertEqual("지표추종매매", routine_name)
            self.assertEqual(["검토관리"], reasons)

    def test_corrupt_state_is_protected_by_stock_delete_gate(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "051910_LG화학"
            stock_dir.mkdir(parents=True)
            (stock_dir / "state.json").write_text("{ invalid review state }", encoding="utf-8")

            with patch.object(
                stock_register_window,
                "stock_runtime_dirs_for_stock",
                return_value=[("지표추종매매", stock_dir)],
            ):
                category, _title, reasons, _runtime_dirs = (
                    stock_register_window.stock_register_unavailable_reason(
                        "051910",
                        "LG화학",
                    )
                )

            self.assertEqual("blocked", category)
            self.assertEqual(["지표추종매매: 검토관리"], reasons)

    def test_review_window_places_transition_time_after_status(self) -> None:
        row = {
            "routine_name": "지표추종매매",
            "stock_dir": Path("stocks/005930_삼성전자"),
            "code": "005930",
            "name": "삼성전자",
            "review_location": "운영시작",
            "review_reason": "검토 필요",
            "review_entered_at": "2026-07-28 11:42:15",
            "holding_qty": 0,
            "avg_price": 0,
            "buy_pending_qty": 0,
            "sell_pending_qty": 0,
            "return_availability": "해결",
        }
        with (
            patch.object(
                review_window.GlobalReviewRequiredWindow,
                "_central_review_rows",
                return_value=[row],
            ),
        ):
            window = review_window.GlobalReviewRequiredWindow()

        self.assertEqual(
            "시간",
            window.table.horizontalHeaderItem(4).text(),
        )
        self.assertEqual(7, window.table.columnCount())
        headers = [
            window.table.horizontalHeaderItem(index).text()
            for index in range(window.table.columnCount())
        ]
        self.assertEqual(
            ["코드", "종목", "위치", "상태", "시간", "사유", "검출"],
            headers,
        )
        self.assertNotIn("보유", headers)
        self.assertNotIn("미수", headers)
        self.assertNotIn("미도", headers)
        self.assertEqual("2026-07-28 11:42:15", window.table.item(0, 4).text())
        self.assertEqual("검토 필요", window.table.item(0, 5).text())
        self.assertEqual("운영시작", window.table.item(0, 6).text())
        self.assertTrue(
            window.table.horizontalHeaderItem(6).textAlignment() & Qt.AlignHCenter
        )
        self.assertTrue(window.table.item(0, 6).textAlignment() & Qt.AlignHCenter)
        self.assertEqual(int(Qt.AlignCenter), window.table.item(0, 4).textAlignment())
        self.assertGreaterEqual(
            window.table.columnWidth(4),
            QFontMetrics(window.table.font()).horizontalAdvance("2026-07-28 11:42:15") + 20,
        )
        header = window.table.horizontalHeader()
        self.assertEqual(
            [75, 160, 140, 90, 360, 140],
            [window.table.columnWidth(index) for index in (0, 1, 2, 3, 4, 6)],
        )
        self.assertEqual(
            [
                QHeaderView.Interactive,
                QHeaderView.Interactive,
                QHeaderView.Interactive,
                QHeaderView.Interactive,
                QHeaderView.Interactive,
                QHeaderView.Stretch,
                QHeaderView.Interactive,
            ],
            [header.sectionResizeMode(index) for index in range(7)],
        )
        self.assertFalse(hasattr(window, "btn_emergency_release"))
        self.assertFalse(hasattr(window, "btn_position_reconcile"))
        self.assertFalse(hasattr(window, "btn_legacy_close_reconcile"))
        self.assertEqual("복귀", window.btn_return.text())
        self.assertEqual("미지정", window.btn_unassign.text())
        self.assertEqual("강제초기화", window.btn_delete.text())
        self.assertEqual("상태재판정", window.btn_refresh.text())
        window.close()

    def test_selected_emergency_remains_visible_across_refresh_and_reopen(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "086520_에코프로"
            stock_dir.mkdir(parents=True)
            state = {
                "status": "EMERGENCY_STOPPED",
                "trade_enabled": False,
                "emergency_scope": "SELECTED",
                "emergency_reason": "USER_EMERGENCY_STOP",
                "emergency_stopped_at": "2026-08-17 12:00:37",
                "review_required": True,
                "review_status": "PENDING",
                "review_reason": "사용자 긴급정지",
                "review_location": "종목 긴급정지",
                "review_entered_at": "2026-08-17 12:00:37",
                "holding_qty": 0,
                "avg_price": 0,
            }
            state_path = stock_dir / "state.json"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            (stock_dir / "orders.json").write_text('{"orders": []}', encoding="utf-8")
            (stock_dir / "config.json").write_text(
                '{"assigned_routine_instance_id": "INSTANCE-1"}', encoding="utf-8"
            )

            class FakeRepo:
                def list_stocks(self):
                    return [SimpleNamespace(code="086520", name="에코프로", routine="루틴")]

                def resolve_stock_dir(self, code, name):
                    return stock_dir

            before = state_path.read_bytes()
            fixed_widths = [75, 160, 140, 90, 360, 140]
            with (
                patch.object(review_window, "PROJECT_ROOT", root),
                patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()),
                patch.object(review_window, "read_review_policy", return_value={}),
            ):
                self.assertEqual(1, len(review_window.collect_global_review_required_rows()))
                first = review_window.GlobalReviewRequiredWindow()
                first.show()
                self.app.processEvents()
                self.assertEqual(1, first.table.rowCount())
                self.assertEqual(
                    fixed_widths,
                    [first.table.columnWidth(i) for i in (0, 1, 2, 3, 4, 6)],
                )
                reason_width = first.table.columnWidth(5)
                self.assertGreater(reason_width, 296)
                self.assertEqual(
                    int(Qt.AlignCenter),
                    first.table.item(0, 5).textAlignment(),
                )
                self.assertEqual(0, first.table.horizontalScrollBar().maximum())
                first.refresh_review_items()
                self.app.processEvents()
                self.assertEqual(1, first.table.rowCount())
                self.assertEqual(
                    fixed_widths,
                    [first.table.columnWidth(i) for i in (0, 1, 2, 3, 4, 6)],
                )
                self.assertEqual(reason_width, first.table.columnWidth(5))
                self.assertEqual(0, first.table.horizontalScrollBar().maximum())
                first.close()

                second = review_window.GlobalReviewRequiredWindow()
                second.show()
                self.app.processEvents()
                self.assertEqual(1, second.table.rowCount())
                self.assertEqual(
                    fixed_widths,
                    [second.table.columnWidth(i) for i in (0, 1, 2, 3, 4, 6)],
                )
                self.assertEqual(reason_width, second.table.columnWidth(5))
                self.assertEqual(
                    int(Qt.AlignCenter),
                    second.table.item(0, 5).textAlignment(),
                )
                self.assertEqual(0, second.table.horizontalScrollBar().maximum())
                second.close()

            self.assertEqual(before, state_path.read_bytes())

    def test_review_column_widths_are_stable_for_zero_and_multiple_rows(self) -> None:
        rows: list[dict[str, object]] = []
        fixed_widths = [75, 160, 140, 90, 360, 140]
        with patch.object(
            review_window.GlobalReviewRequiredWindow,
            "_central_review_rows",
            side_effect=lambda: list(rows),
        ):
            window = review_window.GlobalReviewRequiredWindow()
            window.show()
            self.app.processEvents()
            self.assertEqual(0, window.table.rowCount())
            self.assertEqual(
                fixed_widths,
                [window.table.columnWidth(i) for i in (0, 1, 2, 3, 4, 6)],
            )
            self.assertEqual(0, window.table.horizontalScrollBar().maximum())
            rows.extend(
                {
                    "routine_name": f"루틴{index}",
                    "stock_dir": Path(f"virtual-{index}"),
                    "code": f"{index:06d}",
                    "name": f"종목{index}",
                    "review_location": "테스트",
                    "review_reason": "검토 필요",
                    "review_entered_at": "2026-08-17 12:00:37",
                    "display_status": "미해결",
                    "return_availability": "BLOCKED",
                }
                for index in range(10)
            )
            window.refresh_review_items()
            self.app.processEvents()
            self.assertEqual(10, window.table.rowCount())
            self.assertEqual(
                fixed_widths,
                [window.table.columnWidth(i) for i in (0, 1, 2, 3, 4, 6)],
            )
            self.assertEqual(0, window.table.horizontalScrollBar().maximum())
            window.close()

    def test_collect_global_review_rows_includes_missing_and_unreadable_state(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            records = [
                SimpleNamespace(code="100001", name="상태없음", routine="A루틴"),
                SimpleNamespace(code="100002", name="읽기실패", routine="A루틴"),
                SimpleNamespace(code="100003", name="목록상태", routine="A루틴"),
                SimpleNamespace(code="100004", name="정상검토", routine="A루틴"),
                SimpleNamespace(code="100005", name="정상감시", routine="A루틴"),
                SimpleNamespace(code="100006", name="미연결", routine=""),
            ]

            for record in records:
                stock_dir = root / f"{record.code}_{record.name}"
                stock_dir.mkdir()
                if record.code == "100002":
                    (stock_dir / "state.json").write_text("{ invalid json", encoding="utf-8")
                elif record.code == "100003":
                    (stock_dir / "state.json").write_text("[]", encoding="utf-8")
                elif record.code == "100004":
                    (stock_dir / "state.json").write_text(
                        json.dumps(
                            {
                                "status": "REVIEW_REQUIRED",
                                "review_required": True,
                                "review_reason": "기존 사유",
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                elif record.code == "100005":
                    (stock_dir / "state.json").write_text(
                        json.dumps({"status": "STOPPED"}, ensure_ascii=False),
                        encoding="utf-8",
                    )

            class FakeRepo:
                def list_stocks(self):
                    return records

                def resolve_stock_dir(self, code, name):
                    return root / f"{code}_{name}"

            with patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()):
                rows = review_window.collect_global_review_required_rows()

        by_code = {str(row["code"]): row for row in rows}
        self.assertEqual("운영 데이터 없음", by_code["100001"]["review_reason"])
        self.assertEqual("종목관리", by_code["100001"]["review_location"])
        self.assertEqual("미해결", by_code["100001"]["display_status"])
        self.assertEqual("BLOCKED", by_code["100001"]["return_availability"])
        self.assertEqual("운영 데이터 읽기 오류", by_code["100002"]["review_reason"])
        self.assertEqual("종목관리", by_code["100002"]["review_location"])
        self.assertEqual("미해결", by_code["100002"]["display_status"])
        self.assertEqual("운영 데이터 읽기 오류", by_code["100003"]["review_reason"])
        self.assertEqual("종목관리", by_code["100003"]["review_location"])
        self.assertEqual("미해결", by_code["100003"]["display_status"])
        self.assertEqual("기존 사유", by_code["100004"]["review_reason"])
        self.assertEqual("미기록", by_code["100004"]["review_location"])
        self.assertEqual("미해결", by_code["100004"]["display_status"])
        self.assertEqual("BLOCKED", by_code["100004"]["return_availability"])
        self.assertNotIn("100005", by_code)
        self.assertNotIn("100006", by_code)

    def test_operation_data_mismatch_review_row_uses_emergency_status(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            records = [SimpleNamespace(code="100007", name="긴급검토", routine="A루틴")]
            stock_dir = root / "100007_긴급검토"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_status": "PENDING",
                        "review_reason": "운영 데이터 불일치",
                        "review_detail": "보유 0인데 평단 존재",
                        "review_location": "운영시작",
                        "review_entered_at": "2026-08-02 12:34:56",
                        "emergency_stopped_at": "2026-08-02 12:34:55",
                        "emergency_reason": "운영 데이터 불일치",
                        "trade_enabled": False,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class FakeRepo:
                def list_stocks(self):
                    return records

                def resolve_stock_dir(self, code, name):
                    return root / f"{code}_{name}"

            with patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()):
                rows = review_window.collect_global_review_required_rows()

        self.assertEqual(1, len(rows))
        self.assertEqual("긴급정지", rows[0]["display_status"])
        self.assertEqual("BLOCKED", rows[0]["return_availability"])
        self.assertEqual("운영 데이터 불일치", rows[0]["review_reason"])
        self.assertEqual("운영 시작", rows[0]["review_location"])
        self.assertEqual("2026-08-02 12:34:56", rows[0]["review_entered_at"])

    def test_running_operation_data_mismatch_review_row_displays_running_detection(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            records = [SimpleNamespace(code="100008", name="운영중검토", routine="A루틴")]
            stock_dir = root / "100008_운영중검토"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_status": "PENDING",
                        "review_reason": "운영 데이터 불일치",
                        "review_detail": "보유 0인데 평단 존재",
                        "review_location": "운영중",
                        "review_entered_at": "2026-08-02 12:44:56",
                        "emergency_stopped_at": "2026-08-02 12:44:55",
                        "emergency_reason": "운영 데이터 불일치",
                        "trade_enabled": False,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class FakeRepo:
                def list_stocks(self):
                    return records

                def resolve_stock_dir(self, code, name):
                    return root / f"{code}_{name}"

            with patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()):
                rows = review_window.collect_global_review_required_rows()

        self.assertEqual(1, len(rows))
        self.assertEqual("긴급정지", rows[0]["display_status"])
        self.assertEqual("BLOCKED", rows[0]["return_availability"])
        self.assertEqual("운영 데이터 불일치", rows[0]["review_reason"])
        self.assertEqual("운영 중", rows[0]["review_location"])
        self.assertEqual("2026-08-02 12:44:56", rows[0]["review_entered_at"])

    def test_state_issue_rows_without_detection_event_use_stock_management(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            records = [SimpleNamespace(code="100001", name="상태없음", routine="A루틴")]
            (root / "100001_상태없음").mkdir()

            class FakeRepo:
                def list_stocks(self):
                    return records

                def resolve_stock_dir(self, code, name):
                    return root / f"{code}_{name}"

            with patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()):
                rows = review_window.collect_global_review_required_rows()

        self.assertEqual("운영 데이터 없음", rows[0]["review_reason"])
        self.assertEqual("종목관리", rows[0]["review_location"])

    def test_state_issue_rows_use_manifest_record(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_root = root / "manifest"
            manifest_root.mkdir()
            records = [SimpleNamespace(code="100001", name="상태없음", routine="A루틴")]
            (root / "100001_상태없음").mkdir()
            (manifest_root / "review_required_library_cases_20260802_114323.json").write_text(
                json.dumps(
                    {
                        "created_at": "20260802_114323",
                        "root": str(review_window.PROJECT_ROOT),
                        "cases": [
                            {
                                "code": "100001",
                                "name": "상태없음",
                                "reason": "운영 데이터 없음",
                                "mode": "missing_state",
                                "review_location": "종목관리",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class FakeRepo:
                def list_stocks(self):
                    return records

                def resolve_stock_dir(self, code, name):
                    return root / f"{code}_{name}"

            with (
                patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()),
                patch.object(review_window, "gettempdir", return_value=str(manifest_root)),
            ):
                rows = review_window.collect_global_review_required_rows()

        self.assertEqual("종목관리", rows[0]["review_location"])
        self.assertEqual("2026-08-02 11:43:23", rows[0]["review_entered_at"])

    def test_detection_event_does_not_override_persisted_review_location(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            records = [SimpleNamespace(code="100004", name="정상검토", routine="A루틴")]
            stock_dir = root / "100004_정상검토"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_reason": "기존 사유",
                        "review_location": "긴급정지해제",
                        "holding_qty": 1,
                        "emergency_reason": "USER_EMERGENCY_STOP",
                        "emergency_stopped_at": "2026-07-30 06:39:02",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class FakeRepo:
                def list_stocks(self):
                    return records

                def resolve_stock_dir(self, code, name):
                    return root / f"{code}_{name}"

            with patch.object(review_window, "stock_repository_factory", return_value=FakeRepo()):
                rows = review_window.collect_global_review_required_rows()

        self.assertEqual("긴급정지 해제", rows[0]["review_location"])
        self.assertEqual("미해결", rows[0]["display_status"])

    def test_review_window_load_and_refresh_do_not_pass_caller_event(self) -> None:
        calls = 0

        def fake_rows(_availability_window=None):
            nonlocal calls
            calls += 1
            return []

        with patch.object(review_window, "collect_global_review_required_rows", side_effect=fake_rows):
            window = review_window.GlobalReviewRequiredWindow()
            window.load_review_items()

        self.assertEqual(3, calls)
        window.close()

    def test_review_window_does_not_load_operator_reconciliation_items(self) -> None:
        with (
            patch.object(review_window, "collect_global_review_required_rows", return_value=[]),
            patch(
                "operator_reconciliation_service.collect_operator_reconciliation_items",
                return_value=[],
            ) as collect_reconciliation,
        ):
            window = review_window.GlobalReviewRequiredWindow()
            window.load_review_items()

        collect_reconciliation.assert_not_called()
        self.assertFalse(hasattr(review_window, "collect_operator_reconciliation_items"))
        self.assertFalse(hasattr(review_window, "retry_operator_chejan_reconciliation"))
        self.assertFalse(hasattr(window, "runtime_table"))
        self.assertFalse(hasattr(window, "btn_runtime_retry"))
        window.close()

    def test_review_window_uses_registered_stock_status_table_style(self) -> None:
        row = {
            "routine_name": "지표추종매매",
            "stock_dir": Path("stocks/005930_삼성전자"),
            "code": "005930",
            "name": "삼성전자",
            "review_location": "-",
            "review_reason": "검토 필요",
            "review_entered_at": "2026-07-28 11:42:15",
            "holding_qty": 0,
            "avg_price": 0,
            "buy_pending_qty": 0,
            "sell_pending_qty": 0,
            "display_status": "해결",
        }
        with (
            patch.object(
                review_window.GlobalReviewRequiredWindow,
                "_central_review_rows",
                return_value=[row],
            ),
        ):
            window = review_window.GlobalReviewRequiredWindow()

        self.assertEqual(
            registered_stock_status_table_stylesheet(
                window.table.objectName(),
                window.table.viewport().palette().color(QPalette.Base).name(),
            ),
            window.table.styleSheet(),
        )
        self.assertEqual(
            "#ffffff",
            window.table.palette().color(QPalette.Base).name(),
        )
        self.assertTrue(window.table.verticalHeader().isHidden())
        self.assertFalse(hasattr(window, "runtime_summary_label"))
        self.assertFalse(hasattr(window, "runtime_table"))
        self.assertFalse(hasattr(window, "btn_runtime_retry"))
        self.assertFalse(
            hasattr(review_window.GlobalReviewRequiredWindow, "load_runtime_reconciliation_items")
        )
        self.assertFalse(
            hasattr(review_window.GlobalReviewRequiredWindow, "retry_selected_runtime_reconciliation_items")
        )

        for col in range(window.table.columnCount()):
            cell = window.table.item(0, col)
            self.assertIsNotNone(cell)
            self.assertEqual("#ffffff", cell.background().color().name())
            self.assertEqual("#000000", cell.foreground().color().name())

        item = window.table.item(0, 0)
        before = item.foreground().color().name()
        window.table.selectRow(0)
        window.table.clearSelection()
        self.assertEqual(before, item.foreground().color().name())
        for col in range(window.table.columnCount()):
            self.assertEqual(
                "#000000",
                window.table.item(0, col).foreground().color().name(),
            )
        window.close()

    def test_review_return_button_blocks_unresolved_status(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "100009_미해결"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_status": "PENDING",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            row = {
                "routine_name": "A루틴",
                "stock_dir": stock_dir,
                "code": "100009",
                "name": "미해결",
                "display_status": "미해결",
                "review_location": "종목관리",
                "review_reason": "운영 데이터 없음",
                "review_entered_at": "2026-08-02 11:43:23",
            }
            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                patch.object(review_window.QMessageBox, "information"),
                patch.object(review_window, "append_production_event"),
            ):
                window = review_window.GlobalReviewRequiredWindow()
                window.table.selectRow(0)
                window.return_selected_items_to_auto_list()

            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("REVIEW_REQUIRED", state["status"])
            self.assertTrue(state["review_required"])
            window.close()

    def test_review_return_button_allows_resolved_status(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "100010_해결"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "routine_instance_name": "A루틴",
                        "assigned_routine_instance_id": "routine-a",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "orders.json").write_text(
                json.dumps({"orders": []}, ensure_ascii=False), encoding="utf-8"
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "review_status": "RESOLVED",
                        "review_reason": "기존 사유",
                        "review_location": "운영시작",
                        "holding_qty": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            row = {
                "routine_name": "A루틴",
                "stock_dir": stock_dir,
                "code": "100010",
                "name": "해결",
                "display_status": "해결",
                "review_location": "운영 시작",
                "review_reason": "기존 사유",
                "review_entered_at": "2026-08-02 11:43:23",
            }
            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                patch.object(review_window.QMessageBox, "information"),
                patch.object(review_window, "append_production_event"),
                patch(
                    "gui_main_emergency_ops.review_return_availability",
                    return_value={"availability": "ALLOWED", "reason": ""},
                ),
            ):
                window = review_window.GlobalReviewRequiredWindow()
                window.table.selectRow(0)
                window.return_selected_items_to_auto_list()

            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("STOPPED", state["status"])
            self.assertFalse(state["review_required"])
            self.assertEqual("A루틴", state["review_routine"])
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual("routine-a", config["assigned_routine_instance_id"])
            window.close()

    def test_writer_preserves_current_entry_and_reentry_gets_new_time(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = MagicMock()

            with (
                patch.object(status_ops, "now_text", return_value="2026-07-28 11:42:15"),
                patch.object(status_ops, "append_stock_log"),
            ):
                self.assertTrue(
                    status_ops.auto_trade_update_stock_status(
                        window,
                        stock_dir,
                        "005930",
                        "삼성전자",
                        "REVIEW_REQUIRED",
                        {"review_checked_at": "2026-07-28 11:42:15"},
                    )
                )
            first = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-28 11:42:15", first["review_entered_at"])

            with (
                patch.object(status_ops, "now_text", return_value="2026-07-28 12:00:00"),
                patch.object(status_ops, "append_stock_log"),
            ):
                self.assertTrue(
                    status_ops.auto_trade_update_stock_status(
                        window,
                        stock_dir,
                        "005930",
                        "삼성전자",
                        "REVIEW_REQUIRED",
                        {"review_checked_at": "2026-07-28 12:00:00"},
                    )
                )
            refreshed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-28 11:42:15", refreshed["review_entered_at"])

            self.assertTrue(
                runtime.write_state_json(
                    stock_dir,
                    {"status": "STOPPED", "review_required": False},
                    allow_review_state_transition=True,
                )
            )
            with (
                patch.object(status_ops, "now_text", return_value="2026-07-29 09:05:00"),
                patch.object(status_ops, "append_stock_log"),
            ):
                self.assertTrue(
                    status_ops.auto_trade_update_stock_status(
                        window,
                        stock_dir,
                        "005930",
                        "삼성전자",
                        "REVIEW_REQUIRED",
                    )
                )
            reentered = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-29 09:05:00", reentered["review_entered_at"])


if __name__ == "__main__":
    unittest.main()
