# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton, QWidget

import gui_main_emergency_ops as emergency_ops
import gui_review_required_window as review_window
from gui_auto_trade_run_control import _active_close_or_liquidation
from runtime_io import read_json_dict


class _RoundtripOwner(QWidget):
    def __init__(self, rows_provider):
        super().__init__()
        self.rows_provider = rows_provider
        self.refresh_calls = 0
        self.btn_review_required = QPushButton("검토관리(0)", self)

    def refresh_all(self):
        self.refresh_calls += 1
        self.btn_review_required.setText(
            f"검토관리({len(self.rows_provider())})"
        )


class ReviewResolvedReturnGuiRoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _stock(
        root: str,
        code: str,
        state_overrides: dict[str, object] | None = None,
    ) -> Path:
        stock_dir = Path(root) / f"{code}_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps({"routine": "루틴A"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "orders.json").write_text(
            '{"orders": []}\n', encoding="utf-8"
        )
        state = {
            "status": "REVIEW_REQUIRED",
            "review_required": True,
            "review_status": "RESOLVED",
            "review_location": "안정성 검사",
            "review_reason": "운영 데이터 불일치",
            "review_detail": "SERVER_MISMATCH",
            "review_entered_at": "2026-08-16 10:00:00",
            "review_routine": "루틴A",
            "holding_qty": 0,
            "avg_price": 0,
            "trade_enabled": False,
        }
        state.update(state_overrides or {})
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        return stock_dir

    @staticmethod
    def _row(stock_dir: Path, availability: str, reason: str = ""):
        state = read_json_dict(stock_dir / "state.json")
        if not state.get("review_required"):
            return None
        return {
            "routine_name": "루틴A",
            "stock_dir": stock_dir,
            "code": stock_dir.name[:6],
            "name": "TEST",
            "review_location": "안정성 검사",
            "review_reason": "운영 데이터 불일치",
            "review_entered_at": "2026-08-16 10:00:00",
            "display_status": "해결" if availability == "ALLOWED" else "미해결",
            "return_availability": availability,
            "return_block_reason": reason,
        }

    def _window_context(self, rows_provider, availability_provider):
        stack = ExitStack()
        owner = _RoundtripOwner(rows_provider)
        stack.callback(owner.close)
        stack.enter_context(
            patch.object(
                review_window.GlobalReviewRequiredWindow,
                "_central_review_rows",
                lambda _self: rows_provider(),
            )
        )
        stack.enter_context(
            patch.object(
                review_window,
                "read_review_policy",
                return_value={"long_term_holding_enabled": False},
            )
        )
        stack.enter_context(
            patch.object(
                emergency_ops,
                "review_return_availability",
                side_effect=availability_provider,
            )
        )
        stack.enter_context(patch.object(emergency_ops, "append_stock_log"))
        stack.enter_context(
            patch.object(emergency_ops, "observe_owner_failure_transition")
        )
        stack.enter_context(patch.object(review_window, "append_stock_log"))
        stack.enter_context(
            patch.object(review_window, "append_review_normalization_event")
        )
        stack.enter_context(patch.object(review_window, "show_toast"))
        window = review_window.GlobalReviewRequiredWindow(owner)
        stack.callback(window.close)
        return stack, owner, window

    def test_allowed_row_click_completes_full_gui_roundtrip(self):
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root, "000001")
            availability = {str(stock_dir): ("ALLOWED", "")}

            def rows():
                row = self._row(stock_dir, *availability[str(stock_dir)])
                return [row] if row else []

            def gate(_window, target_dir, _code, **_kwargs):
                value, reason = availability[str(Path(target_dir))]
                return {"availability": value, "reason": reason}

            stack, owner, window = self._window_context(rows, gate)
            with stack:
                owner.refresh_all()
                self.assertEqual("검토관리(1)", owner.btn_review_required.text())
                self.assertEqual("해결", window.table.item(0, 3).text())
                window.table.selectRow(0)
                self.app.processEvents()
                self.assertTrue(window.btn_return.isEnabled())
                self.assertIn("복귀 가능", window.operator_guidance_label.text())

                window.btn_return.click()
                self.app.processEvents()

                saved = read_json_dict(stock_dir / "state.json")
                self.assertEqual("STOPPED", saved["status"])
                self.assertFalse(saved["trade_enabled"])
                self.assertFalse(saved["review_required"])
                for key in (
                    "review_status",
                    "review_location",
                    "review_reason",
                    "review_detail",
                    "review_entered_at",
                ):
                    self.assertEqual("", saved[key])
                self.assertEqual("루틴A", saved["review_routine"])
                self.assertEqual("루틴A", read_json_dict(stock_dir / "config.json")["routine"])
                self.assertEqual(0, window.table.rowCount())
                self.assertEqual([], window.table.selectionModel().selectedRows())
                self.assertEqual("검토관리(0)", owner.btn_review_required.text())
                self.assertGreaterEqual(owner.refresh_calls, 2)
                self.assertFalse((Path(root) / "order_queue.json").exists())

    def test_restore_preserves_terminal_early_close_evidence(self):
        with TemporaryDirectory() as root:
            terminal = {
                "operation_command_mode": "EARLY_CLOSE",
                "operation_notice": "EARLY_CLOSE_NO_TARGET",
                "operation_notice_reason": "조기마감 대상 없음",
                "operation_notice_at": "2026-08-17 10:15:20",
            }
            position = {"holding_qty": 0, "avg_price": 0, "holding_amount": 0}
            stock_dir = self._stock(
                root,
                "323410",
                {**terminal, **position, "review_status": "PENDING"},
            )

            def rows():
                row = self._row(stock_dir, "ALLOWED")
                return [row] if row else []

            gate = Mock(return_value={"availability": "ALLOWED", "reason": ""})
            stack, _owner, window = self._window_context(rows, gate)
            with stack:
                before = read_json_dict(stock_dir / "state.json")
                self.assertFalse(_active_close_or_liquidation(before, datetime.now()))
                window.table.selectRow(0)
                self.app.processEvents()
                window.btn_return.click()
                self.app.processEvents()

                saved = read_json_dict(stock_dir / "state.json")
                self.assertEqual("STOPPED", saved["status"])
                self.assertFalse(saved["trade_enabled"])
                self.assertFalse(saved["review_required"])
                self.assertEqual("", saved["review_status"])
                for key, value in terminal.items():
                    self.assertEqual(value, saved[key])
                for key, value in position.items():
                    self.assertEqual(value, saved[key])
                self.assertFalse(_active_close_or_liquidation(saved, datetime.now()))

    def test_unassigned_preserves_terminal_early_close_evidence(self):
        with TemporaryDirectory() as root:
            terminal = {
                "operation_command_mode": "EARLY_CLOSE",
                "operation_notice": "EARLY_CLOSE_NO_TARGET",
                "operation_notice_reason": "조기마감 대상 없음",
                "operation_notice_at": "2026-08-17 10:15:20",
            }
            stock_dir = self._stock(
                root,
                "323410",
                {
                    **terminal,
                    "review_status": "PENDING",
                    "active_routine": "루틴A",
                    "routine_name": "루틴A",
                },
            )

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        emergency_ops,
                        "review_return_availability",
                        return_value={"availability": "ALLOWED", "reason": ""},
                    )
                )
                stack.enter_context(patch.object(emergency_ops, "append_stock_log"))
                stack.enter_context(
                    patch.object(emergency_ops, "observe_owner_failure_transition")
                )
                update_routines = stack.enter_context(
                    patch.object(
                        emergency_ops, "update_base_stock_routines", return_value=True
                    )
                )
                result = emergency_ops.normalize_review_emergency_target(
                    None,
                    stock_dir,
                    "323410",
                    "TEST",
                    destination="UNASSIGNED",
                )
                saved = read_json_dict(stock_dir / "state.json")
                self.assertEqual("NORMALIZED", result["status"])
                self.assertEqual("STOPPED", saved["status"])
                self.assertFalse(saved["trade_enabled"])
                self.assertFalse(saved["review_required"])
                self.assertEqual("", saved["review_status"])
                for key, value in terminal.items():
                    self.assertEqual(value, saved[key])
                self.assertEqual("", saved["active_routine"])
                self.assertEqual("", saved["routine_name"])
                self.assertFalse(_active_close_or_liquidation(saved, datetime.now()))
                update_routines.assert_called_once_with("323410", "TEST", [])

    def test_restore_keeps_nonterminal_notice_clear_behavior(self):
        with TemporaryDirectory() as root:
            stock_dir = self._stock(
                root,
                "000001",
                {
                    "operation_command_mode": "NORMAL",
                    "operation_notice": "일반 안내",
                    "operation_notice_reason": "일반 사유",
                    "operation_notice_at": "2026-08-16 11:22:33",
                },
            )

            def rows():
                row = self._row(stock_dir, "ALLOWED")
                return [row] if row else []

            gate = Mock(return_value={"availability": "ALLOWED", "reason": ""})
            stack, _owner, window = self._window_context(rows, gate)
            with stack:
                window.table.selectRow(0)
                self.app.processEvents()
                window.btn_return.click()
                self.app.processEvents()

                saved = read_json_dict(stock_dir / "state.json")
                self.assertEqual("NORMAL", saved["operation_command_mode"])
                for key in (
                    "operation_notice",
                    "operation_notice_reason",
                    "operation_notice_at",
                ):
                    self.assertEqual("", saved[key])

    def test_blocked_and_emergency_rows_disable_restore(self):
        for reason, status in (
            ("RECOVERY_NOT_READY", "미해결"),
            ("EMERGENCY_STOP_ACTIVE", "긴급정지"),
        ):
            with self.subTest(reason=reason), TemporaryDirectory() as root:
                stock_dir = self._stock(root, "000001")

                def rows():
                    row = self._row(stock_dir, "BLOCKED", reason)
                    row["display_status"] = status
                    return [row]

                gate = Mock(return_value={"availability": "BLOCKED", "reason": reason})
                stack, _owner, window = self._window_context(rows, gate)
                with stack:
                    before = (stock_dir / "state.json").read_bytes()
                    window.table.selectRow(0)
                    self.app.processEvents()
                    self.assertFalse(window.btn_return.isEnabled())
                    window.btn_return.click()
                    self.assertEqual(before, (stock_dir / "state.json").read_bytes())
                    gate.assert_not_called()

    def test_allowed_preview_rechecks_and_blocks_latest_state(self):
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root, "000001")
            current = {"availability": "ALLOWED", "reason": ""}

            def rows():
                row = self._row(
                    stock_dir, current["availability"], current["reason"]
                )
                return [row] if row else []

            gate = Mock(side_effect=lambda *_args, **_kwargs: dict(current))
            stack, _owner, window = self._window_context(rows, gate)
            with stack:
                before = (stock_dir / "state.json").read_bytes()
                window.table.selectRow(0)
                self.app.processEvents()
                self.assertTrue(window.btn_return.isEnabled())
                current.update(
                    availability="BLOCKED", reason="RECOVERY_NOT_READY"
                )
                window.btn_return.click()
                self.app.processEvents()
                self.assertEqual(before, (stock_dir / "state.json").read_bytes())
                self.assertEqual(1, window.table.rowCount())
                self.assertEqual(1, len(window.table.selectionModel().selectedRows()))
                self.assertIn("복구 상태", window.operator_guidance_label.text())
                gate.assert_called_once()

    def test_multi_selection_is_partial_success(self):
        with TemporaryDirectory() as root:
            allowed_dir = self._stock(root, "000001")
            blocked_dir = self._stock(root, "000002")
            current = {
                str(allowed_dir): ("ALLOWED", ""),
                str(blocked_dir): ("BLOCKED", "RECOVERY_NOT_READY"),
            }

            def rows():
                result = []
                for stock_dir in (allowed_dir, blocked_dir):
                    row = self._row(stock_dir, *current[str(stock_dir)])
                    if row:
                        result.append(row)
                return result

            def gate(_window, target_dir, _code, **_kwargs):
                value, reason = current[str(Path(target_dir))]
                return {"availability": value, "reason": reason}

            stack, owner, window = self._window_context(rows, gate)
            with stack:
                window.table.selectAll()
                self.app.processEvents()
                self.assertTrue(window.btn_return.isEnabled())
                window.btn_return.click()
                self.app.processEvents()
                self.assertFalse(read_json_dict(allowed_dir / "state.json")["review_required"])
                self.assertTrue(read_json_dict(blocked_dir / "state.json")["review_required"])
                self.assertEqual(1, window.table.rowCount())
                self.assertEqual("000002", window.table.item(0, 0).text())
                self.assertEqual("검토관리(1)", owner.btn_review_required.text())

    def test_all_allowed_and_all_blocked_multi_selection(self):
        for availability_value, expected_rows in (("ALLOWED", 0), ("BLOCKED", 2)):
            with self.subTest(availability=availability_value), TemporaryDirectory() as root:
                stock_dirs = [self._stock(root, code) for code in ("000001", "000002")]
                reason = "" if availability_value == "ALLOWED" else "RECOVERY_NOT_READY"

                def rows():
                    return [
                        row
                        for stock_dir in stock_dirs
                        for row in [self._row(stock_dir, availability_value, reason)]
                        if row
                    ]

                gate = Mock(
                    return_value={"availability": availability_value, "reason": reason}
                )
                stack, _owner, window = self._window_context(rows, gate)
                with stack:
                    window.table.selectAll()
                    self.app.processEvents()
                    self.assertEqual(
                        availability_value == "ALLOWED", window.btn_return.isEnabled()
                    )
                    window.btn_return.click()
                    self.app.processEvents()
                    self.assertEqual(expected_rows, window.table.rowCount())
                    if availability_value == "ALLOWED":
                        self.assertEqual(2, gate.call_count)
                    else:
                        gate.assert_not_called()

    def test_virtual_rows_are_blocked_without_file_repair(self):
        with TemporaryDirectory() as root:
            missing = Path(root) / "000001_MISSING"
            corrupt = Path(root) / "000002_CORRUPT"
            missing.mkdir()
            corrupt.mkdir()
            (corrupt / "state.json").write_text("{broken", encoding="utf-8")
            corrupt_before = (corrupt / "state.json").read_bytes()

            def rows():
                return [
                    {
                        "routine_name": "루틴A",
                        "stock_dir": stock_dir,
                        "code": stock_dir.name[:6],
                        "name": stock_dir.name,
                        "review_location": "종목관리",
                        "review_reason": row_reason,
                        "review_entered_at": "미기록",
                        "display_status": "미해결",
                        "return_availability": "BLOCKED",
                        "return_block_reason": row_reason,
                    }
                    for stock_dir, row_reason in (
                        (missing, "운영 데이터 없음"),
                        (corrupt, "운영 데이터 읽기 오류"),
                    )
                ]

            gate = Mock()
            stack, _owner, window = self._window_context(rows, gate)
            with stack:
                window.table.selectAll()
                self.app.processEvents()
                self.assertFalse(window.btn_return.isEnabled())
                window.btn_return.click()
                self.assertFalse((missing / "state.json").exists())
                self.assertEqual(corrupt_before, (corrupt / "state.json").read_bytes())
                gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
