import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QHeaderView

import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_runtime as runtime
import gui_routine_policy as routine_policy
import gui_stock_register_window as stock_register_window
import gui_review_required_window as review_window
from gui_styles import TABLE_LIGHT_SELECTION_STYLE


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

            with (
                patch.object(
                    routine_policy,
                    "base_stock_routines_for_stock",
                    return_value=(True, ["지표추종매매"]),
                ),
                patch.object(
                    routine_policy,
                    "stock_runtime_dir_for_routine",
                    return_value=stock_dir,
                ),
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
            "검토 전환 시각",
            window.table.horizontalHeaderItem(4).text(),
        )
        self.assertEqual(7, window.table.columnCount())
        headers = [
            window.table.horizontalHeaderItem(index).text()
            for index in range(window.table.columnCount())
        ]
        self.assertEqual(
            ["코드", "종목", "위치", "상태", "검토 전환 시각", "사유", "검출"],
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
        header = window.table.horizontalHeader()
        self.assertEqual(QHeaderView.Stretch, header.sectionResizeMode(5))
        self.assertEqual(QHeaderView.ResizeToContents, header.sectionResizeMode(6))
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

        self.assertEqual(2, calls)
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

    def test_review_window_uses_project_light_selection_style(self) -> None:
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

        self.assertEqual(TABLE_LIGHT_SELECTION_STYLE, window.table.styleSheet())
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
