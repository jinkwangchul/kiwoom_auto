# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gui_main_emergency_ops as emergency_ops
import gui_review_required_window as review_window
from runtime_io import read_json_dict


class ReviewReturnAvailabilityTest(unittest.TestCase):
    @staticmethod
    def _stock(root: str, *, status: str = "REVIEW_REQUIRED") -> Path:
        stock_dir = Path(root) / "000001_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text("{}\n", encoding="utf-8")
        (stock_dir / "orders.json").write_text('{"orders": []}\n', encoding="utf-8")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "review_required": True,
                    "review_status": "RESOLVED",
                    "holding_qty": 0,
                    "avg_price": 0,
                    "trade_enabled": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return stock_dir

    @staticmethod
    def _window() -> SimpleNamespace:
        return SimpleNamespace(startup_recovery_session_ready=lambda refresh=False: True)

    def test_selected_emergency_provenance_uses_normal_return_gate(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root, status="EMERGENCY_STOPPED")
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            state["emergency_scope"] = "SELECTED"
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            state = read_json_dict(stock_dir / "state.json")
            state["emergency_reason"] = "USER_EMERGENCY_STOP"
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            with (
                patch.object(emergency_ops, "read_operation_state", return_value={"emergency_stop": False}),
                patch.object(
                    emergency_ops,
                    "emergency_release_common_guard",
                    return_value=(True, ""),
                ) as guard,
            ):
                result = emergency_ops.review_return_availability(
                    self._window(), stock_dir, "000001", state=state
                )

        self.assertEqual("ALLOWED", result["availability"])
        self.assertEqual("", result["reason"])
        self.assertEqual(
            "해결",
            review_window._review_display_status_for_collected_row(
                state, return_availability=result["availability"]
            ),
        )
        guard.assert_called_once()

    def test_global_latch_blocks_selected_emergency_provenance(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root, status="REVIEW_REQUIRED")
            state = read_json_dict(stock_dir / "state.json")
            state["emergency_scope"] = "SELECTED"
            with (
                patch.object(emergency_ops, "read_operation_state", return_value={"emergency_stop": True}),
                patch.object(emergency_ops, "emergency_release_common_guard") as guard,
            ):
                result = emergency_ops.review_return_availability(
                    self._window(), stock_dir, "000001", state=state
                )

        self.assertEqual("BLOCKED", result["availability"])
        self.assertEqual("EMERGENCY_STOP_ACTIVE", result["reason"])
        guard.assert_not_called()

    def test_resolved_metadata_does_not_override_recovery_or_mismatch_block(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root)
            state = read_json_dict(stock_dir / "state.json")
            for reason in ("RECOVERY_NOT_READY", "SERVER_MISMATCH"):
                with self.subTest(reason=reason), patch.object(
                    emergency_ops,
                    "emergency_release_common_guard",
                    return_value=(False, reason),
                ):
                    result = emergency_ops.review_return_availability(
                        self._window(), stock_dir, "000001", state=state
                    )
                    self.assertEqual("BLOCKED", result["availability"])
                    self.assertEqual(
                        "미해결",
                        review_window._review_display_status_for_collected_row(
                            state, return_availability=result["availability"]
                        ),
                    )

    def test_all_current_evidence_passes_as_resolved_and_allowed(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root)
            state = read_json_dict(stock_dir / "state.json")
            with patch.object(
                emergency_ops,
                "emergency_release_common_guard",
                return_value=(True, ""),
            ):
                result = emergency_ops.review_return_availability(
                    self._window(), stock_dir, "000001", state=state
                )

        self.assertEqual("ALLOWED", result["availability"])
        self.assertEqual(
            "해결",
            review_window._review_display_status_for_collected_row(
                state, return_availability=result["availability"]
            ),
        )

    def test_active_early_close_remains_blocked_from_review_return(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root)
            state = read_json_dict(stock_dir / "state.json")
            state["operation_command_mode"] = "EARLY_CLOSE"
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            queue_path = Path(root) / "order_queue.json"
            queue_path.write_text(
                json.dumps({"version": 1, "orders": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(
                emergency_ops,
                "emergency_review_reason_for_stock",
                return_value=(False, ""),
            ):
                result = emergency_ops.review_return_availability(
                    self._window(),
                    stock_dir,
                    "000001",
                    state=state,
                    order_queue_path=queue_path,
                )

        self.assertEqual("BLOCKED", result["availability"])
        self.assertEqual("ACTIVE_CLOSE_OR_LIQUIDATION", result["reason"])

    def test_execution_rechecks_after_allowed_preview_and_does_not_mutate(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root)
            before = (stock_dir / "state.json").read_bytes()
            with (
                patch.object(
                    emergency_ops,
                    "review_return_availability",
                    return_value={
                        "availability": "BLOCKED",
                        "reason": "RECOVERY_NOT_READY",
                    },
                ) as availability,
                patch.object(emergency_ops, "update_runtime_stock_status") as writer,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, "000001", "TEST", destination="RESTORE"
                )

            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual(before, (stock_dir / "state.json").read_bytes())
            availability.assert_called_once()
            writer.assert_not_called()

    def test_missing_and_corrupt_virtual_rows_are_blocked_without_write(self) -> None:
        with TemporaryDirectory() as root:
            missing = Path(root) / "000001_MISSING"
            missing.mkdir()
            corrupt = Path(root) / "000002_CORRUPT"
            corrupt.mkdir()
            corrupt_path = corrupt / "state.json"
            corrupt_path.write_text("{broken", encoding="utf-8")
            before = corrupt_path.read_bytes()

            for stock_dir, issue in (
                (missing, "운영 데이터 없음"),
                (corrupt, "운영 데이터 읽기 오류"),
            ):
                with self.subTest(stock_dir=stock_dir):
                    result = emergency_ops.review_return_availability(
                        self._window(),
                        stock_dir,
                        stock_dir.name[:6],
                        state_issue_reason=issue,
                    )
                    self.assertEqual("BLOCKED", result["availability"])

            self.assertFalse((missing / "state.json").exists())
            self.assertEqual(before, corrupt_path.read_bytes())

    def test_unassigned_uses_the_same_latest_availability_contract(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root)
            with (
                patch.object(
                    emergency_ops,
                    "review_return_availability",
                    return_value={"availability": "BLOCKED", "reason": "ACTIVE_ORDER"},
                ) as availability,
                patch.object(
                    emergency_ops,
                    "execute_assignment_unassign",
                ) as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, "000001", "TEST", destination="UNASSIGNED"
                )

        self.assertEqual("BLOCKED", result["status"])
        availability.assert_called_once()
        unassign.assert_not_called()

    def test_selected_emergency_release_preserves_review_until_explicit_return(self) -> None:
        with TemporaryDirectory() as root:
            stock_dir = self._stock(root, status="EMERGENCY_STOPPED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "emergency_reason": "USER_EMERGENCY_STOP",
                    "emergency_scope": "SELECTED",
                    "emergency_stopped_at": "2026-08-16 10:00:00",
                    "review_status": "PENDING",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            window = self._window()
            with (
                patch.object(
                    emergency_ops,
                    "read_operation_state",
                    return_value={"emergency_stop": False},
                ),
                patch.object(emergency_ops, "emergency_release_common_guard", return_value=(True, "")),
                patch.object(emergency_ops, "append_stock_log"),
            ):
                release = emergency_ops.release_emergency_stop_target(
                    window, stock_dir, "000001", "TEST"
                )
            released = read_json_dict(stock_dir / "state.json")
            self.assertEqual(emergency_ops.RELEASED_TO_REVIEW, release)
            self.assertEqual("REVIEW_REQUIRED", released["status"])
            self.assertEqual("RESOLVED", released["review_status"])
            self.assertTrue(released["review_required"])
            self.assertFalse(released["trade_enabled"])

            with (
                patch.object(emergency_ops, "emergency_release_common_guard", return_value=(True, "")),
                patch.object(emergency_ops, "append_stock_log"),
            ):
                returned = emergency_ops.normalize_review_emergency_target(
                    window, stock_dir, "000001", "TEST", destination="RESTORE"
                )
            restored = read_json_dict(stock_dir / "state.json")

        self.assertEqual("NORMALIZED", returned["status"])
        self.assertEqual("STOPPED", restored["status"])
        self.assertFalse(restored["review_required"])
        self.assertFalse(restored["trade_enabled"])


if __name__ == "__main__":
    unittest.main()
