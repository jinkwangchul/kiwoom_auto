# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_main_emergency_ops as emergency_ops
import gui_review_required_window as review_window
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from runtime_io import read_json_dict


class SelectedEmergencyOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        operation_state = patch.object(
            emergency_ops,
            "read_operation_state",
            return_value={"emergency_stop": False},
        )
        operation_state.start()
        self.addCleanup(operation_state.stop)

    def _target(
        self,
        root: str,
        code: str,
        *,
        status: str = "STOPPED",
        emergency_scope: str | None = None,
    ) -> tuple[Path, str, str]:
        stock_dir = Path(root) / f"{code}_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text("{}\n", encoding="utf-8")
        (stock_dir / "orders.json").write_text('{"orders": []}\n', encoding="utf-8")
        state = {
            "status": status,
            "holding_qty": 0,
            "trade_enabled": status == "RUNNING",
        }
        if status == "EMERGENCY_STOPPED":
            state["emergency_scope"] = emergency_scope or "SELECTED"
        (stock_dir / "state.json").write_text(
            json.dumps(
                state,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return stock_dir, code, "테스트"

    @staticmethod
    def _window() -> SimpleNamespace:
        return SimpleNamespace(
            refresh_all=Mock(),
            statusBarMessage=Mock(),
            startup_recovery_session_ready=lambda refresh=False: True,
        )

    @staticmethod
    def _global_window(targets) -> SimpleNamespace:
        status_bar = SimpleNamespace(showMessage=Mock())
        return SimpleNamespace(
            all_runtime_stock_dirs=lambda: [target[0] for target in targets],
            refresh_auto_trade_assignment_views=Mock(),
            refresh_all=Mock(),
            statusBar=lambda: status_bar,
            btn_emergency_stop=SimpleNamespace(setText=Mock()),
            startup_recovery_session_ready=lambda refresh=False: True,
            start_production_recovery=Mock(),
        )

    @staticmethod
    def _collect_rows_for(targets):
        records = [
            SimpleNamespace(code=code, name=name, routine="테스트루틴")
            for _stock_dir, code, name in targets
        ]
        directories = {
            (code, name): stock_dir for stock_dir, code, name in targets
        }
        repository = SimpleNamespace(
            list_stocks=lambda: records,
            resolve_stock_dir=lambda code, name: directories[(code, name)],
        )
        with (
            patch.object(review_window, "stock_repository_factory", return_value=repository),
            patch.object(
                review_window,
                "read_review_policy",
                return_value={"long_term_holding_enabled": False},
            ),
        ):
            return review_window.collect_global_review_required_rows()

    def test_global_stop_blocks_trading_without_creating_review_entry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="RUNNING")
            window = self._global_window([target])
            with (
                patch.object(
                    emergency_ops,
                    "write_global_emergency_stop_state",
                    return_value={"ok": True},
                ),
                patch.object(
                    emergency_ops,
                    "read_operation_state",
                    return_value={"emergency_stop": False},
                ),
                patch.object(emergency_ops, "append_production_event"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 10:00:00"),
            ):
                emergency_ops.execute_emergency_stop(window)

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual("EMERGENCY_STOPPED", saved["status"])
            self.assertFalse(saved["trade_enabled"])
            self.assertNotIn("review_required", saved)
            self.assertNotIn("review_status", saved)
            self.assertNotIn("review_entered_at", saved)
            self.assertEqual([], self._collect_rows_for([target]))

    def test_global_stop_preserves_preexisting_review_identity_without_reentry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(target[0] / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_reason": "기존 사유",
                    "review_entered_at": "2026-08-15 09:00:00",
                }
            )
            (target[0] / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            window = self._global_window([target])
            with (
                patch.object(
                    emergency_ops,
                    "write_global_emergency_stop_state",
                    return_value={"ok": True},
                ),
                patch.object(
                    emergency_ops,
                    "read_operation_state",
                    return_value={"emergency_stop": False},
                ),
                patch.object(emergency_ops, "append_production_event"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 10:00:00"),
            ):
                emergency_ops.execute_emergency_stop(window)

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual("REVIEW_REQUIRED", saved["status"])
            self.assertTrue(saved["review_required"])
            self.assertEqual("기존 사유", saved["review_reason"])
            self.assertEqual("2026-08-15 09:00:00", saved["review_entered_at"])

    def test_global_stop_preserves_existing_reviews_and_adds_no_review_rows(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            reviews = [
                self._target(root, f"00000{index}", status="REVIEW_REQUIRED")
                for index in range(1, 3)
            ]
            normal = [
                self._target(root, f"0000{index:02d}", status="STOPPED")
                for index in range(3, 13)
            ]
            identities = {}
            for index, target in enumerate(reviews, start=1):
                state = read_json_dict(target[0] / "state.json")
                state.update(
                    {
                        "review_required": True,
                        "review_status": "PENDING",
                        "review_reason": f"기존 사유 {index}",
                        "review_location": f"기존 검출 {index}",
                        "review_entered_at": f"2026-08-1{index} 09:00:00",
                    }
                )
                (target[0] / "state.json").write_text(
                    json.dumps(state, ensure_ascii=False), encoding="utf-8"
                )
                identities[target[1]] = dict(state)

            targets = reviews + normal
            window = self._global_window(targets)
            with (
                patch.object(emergency_ops, "write_global_emergency_stop_state", return_value={"ok": True}),
                patch.object(emergency_ops, "read_operation_state", return_value={"emergency_stop": False}),
                patch.object(emergency_ops, "append_production_event"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical") as critical,
            ):
                emergency_ops.execute_emergency_stop(window)

            critical.assert_not_called()
            window.start_production_recovery.assert_not_called()
            rows = self._collect_rows_for(targets)
            self.assertEqual(2, len(rows))
            self.assertEqual(2, len(self._collect_rows_for(targets)))
            for target in reviews:
                self.assertEqual(identities[target[1]], read_json_dict(target[0] / "state.json"))
            for target in normal:
                state = read_json_dict(target[0] / "state.json")
                self.assertEqual("EMERGENCY_STOPPED", state["status"])
                self.assertEqual("GLOBAL", state["emergency_scope"])
                self.assertFalse(state.get("review_required", False))
                self.assertNotIn("review_entered_at", state)

    def test_global_latch_write_failure_starts_no_stock_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="RUNNING")
            before = read_json_dict(target[0] / "state.json")
            window = self._global_window([target])
            with (
                patch.object(emergency_ops, "write_global_emergency_stop_state", return_value={"ok": False}),
                patch.object(emergency_ops, "read_operation_state", return_value={"emergency_stop": False}),
                patch.object(emergency_ops, "update_runtime_stock_status") as stock_writer,
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical"),
            ):
                emergency_ops.execute_emergency_stop(window)

            stock_writer.assert_not_called()
            self.assertEqual(before, read_json_dict(target[0] / "state.json"))

    def test_global_release_preserves_old_review_and_reviews_only_failed_target(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            existing_review = self._target(root, "000001", status="REVIEW_REQUIRED")
            review_state = read_json_dict(existing_review[0] / "state.json")
            review_state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_reason": "기존 사유",
                    "review_location": "기존 검출",
                    "review_entered_at": "2026-08-01 09:00:00",
                }
            )
            (existing_review[0] / "state.json").write_text(
                json.dumps(review_state, ensure_ascii=False), encoding="utf-8"
            )
            normal = self._target(
                root, "000002", status="EMERGENCY_STOPPED", emergency_scope="GLOBAL"
            )
            blocked = self._target(
                root, "000003", status="EMERGENCY_STOPPED", emergency_scope="GLOBAL"
            )
            blocked_state = read_json_dict(blocked[0] / "state.json")
            blocked_state.update({"holding_qty": 1, "avg_price": 1000})
            (blocked[0] / "state.json").write_text(
                json.dumps(blocked_state, ensure_ascii=False), encoding="utf-8"
            )
            targets = [existing_review, normal, blocked]
            window = self._global_window(targets)
            with (
                patch.object(emergency_ops, "read_operation_state", return_value={"emergency_stop": True}),
                patch.object(emergency_ops, "global_emergency_release_preflight", return_value=(True, "")),
                patch.object(emergency_ops, "write_global_emergency_stop_state", return_value={"ok": True}),
                patch.object(emergency_ops, "_active_queue_reason", return_value=""),
                patch.object(emergency_ops, "append_production_event"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops.QMessageBox, "critical"),
            ):
                emergency_ops.release_emergency_stop(window)

            self.assertEqual(review_state, read_json_dict(existing_review[0] / "state.json"))
            self.assertEqual("STOPPED", read_json_dict(normal[0] / "state.json")["status"])
            failed_state = read_json_dict(blocked[0] / "state.json")
            self.assertTrue(failed_state["review_required"])
            self.assertEqual("긴급정지 해제", failed_state["review_location"])

    def test_stop_changes_only_selected_target_and_verifies_saved_state(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            selected = self._target(root, "000001")
            untouched = self._target(root, "000002", status="RUNNING")
            before_untouched = read_json_dict(untouched[0] / "state.json")
            window = self._window()
            with (
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
                patch.object(emergency_ops, "now_text", return_value="2026-08-06 14:00:00"),
                patch.object(
                    emergency_ops,
                    "review_return_availability",
                    return_value={"availability": "ALLOWED", "reason": ""},
                ),
            ):
                result = emergency_ops.execute_selected_emergency_stop(window, [selected])

            saved = read_json_dict(selected[0] / "state.json")
            self.assertEqual(("000001 테스트",), result["changed"])
            self.assertEqual((), result["failed"])
            self.assertEqual("REVIEW_REQUIRED", saved["status"])
            self.assertEqual("SELECTED", saved["emergency_scope"])
            self.assertFalse(saved["trade_enabled"])
            self.assertTrue(saved["review_required"])
            self.assertEqual("PENDING", saved["review_status"])
            self.assertEqual("종목 검토정지", saved["review_location"])
            self.assertEqual("사용자 검토정지", saved["review_reason"])
            self.assertEqual("", saved["emergency_reason"])
            self.assertEqual("", saved["emergency_stopped_at"])
            self.assertEqual("ALLOWED", result["availability_by_stock"]["000001"]["availability"])
            self.assertEqual("2026-08-06 14:00:00", saved["review_entered_at"])
            self.assertNotIn("buy_enabled", saved)
            self.assertNotIn("sell_enabled", saved)
            self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))
            window.refresh_all.assert_called_once_with()
            toast.assert_called_once()

    def test_selected_stop_skips_existing_review_without_reentry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(target[0] / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "RESOLVED",
                    "review_reason": "운영 데이터 불일치",
                    "review_location": "운영 시작",
                    "review_entered_at": "2026-08-01 09:00:00",
                }
            )
            (target[0] / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            with (
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 14:00:00"),
            ):
                result = emergency_ops.execute_selected_emergency_stop(
                    self._window(), [target]
                )
            saved = read_json_dict(target[0] / "state.json")

        self.assertEqual((), result["changed"])
        self.assertEqual(("000001 테스트",), result["skipped"])
        self.assertEqual("운영 데이터 불일치", saved["review_reason"])
        self.assertEqual("2026-08-01 09:00:00", saved["review_entered_at"])
        self.assertEqual("운영 시작", saved["review_location"])
        self.assertEqual("RESOLVED", saved["review_status"])
        self.assertEqual("REVIEW_REQUIRED", saved["status"])

    def test_stop_write_failure_is_reported_without_success_toast(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001")
            window = self._window()
            with (
                patch("runtime_stock_state_mutation.write_state_json", return_value=False),
                patch.object(emergency_ops.QMessageBox, "critical"),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
            ):
                result = emergency_ops.execute_selected_emergency_stop(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["changed"])
        toast.assert_not_called()

    def test_multi_selected_stop_reviews_only_selected_without_global_latch(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = self._target(root, "000001", status="RUNNING")
            second = self._target(root, "000002", status="STOPPED")
            untouched = self._target(root, "000003", status="RUNNING")
            untouched_before = read_json_dict(untouched[0] / "state.json")
            window = self._window()
            with (
                patch.object(emergency_ops, "write_global_emergency_stop_state") as global_writer,
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
                patch.object(emergency_ops, "now_text", return_value="2026-08-16 10:30:00"),
            ):
                result = emergency_ops.execute_selected_emergency_stop(
                    window, [first, second]
                )

            self.assertEqual(2, result["changed_count"])
            global_writer.assert_not_called()
            for target in (first, second):
                saved = read_json_dict(target[0] / "state.json")
                self.assertTrue(saved["review_required"])
                self.assertEqual("SELECTED", saved["emergency_scope"])
                self.assertEqual("2026-08-16 10:30:00", saved["review_entered_at"])
            self.assertEqual(
                untouched_before, read_json_dict(untouched[0] / "state.json")
            )
            self.assertEqual(2, len(self._collect_rows_for([first, second, untouched])))

    def test_stop_readback_mismatch_is_reported_as_failure(self) -> None:
        target = (Path("stocks/000001_TEST"), "000001", "테스트")
        initial = {"status": "STOPPED"}
        window = self._window()
        with (
            patch.object(
                emergency_ops,
                "read_json_dict",
                side_effect=[dict(initial), dict(initial), dict(initial)],
            ),
            patch("runtime_stock_state_mutation.write_state_json", return_value=True),
            patch.object(emergency_ops, "_routine_name_for_emergency_release", return_value=""),
            patch.object(emergency_ops, "append_changelog"),
            patch.object(emergency_ops, "append_stock_log"),
            patch.object(emergency_ops, "show_toast") as toast,
        ):
            result = emergency_ops.execute_selected_emergency_stop(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["changed"])
        toast.assert_not_called()

    def test_release_after_safety_check_returns_selected_target_to_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="EMERGENCY_STOPPED")
            untouched = self._target(root, "000002", status="EMERGENCY_STOPPED")
            before_untouched = read_json_dict(untouched[0] / "state.json")
            window = self._window()
            with (
                patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(False, "정상")),
                patch.object(emergency_ops, "_active_queue_reason", return_value=""),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast") as toast,
            ):
                result = emergency_ops.execute_selected_emergency_release(window, [target])

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual(("000001 테스트",), result["normal"])
            self.assertEqual("STOPPED", saved["status"])
            self.assertFalse(saved["review_required"])
            self.assertFalse(saved["trade_enabled"])
            self.assertNotIn("buy_enabled", saved)
            self.assertNotIn("sell_enabled", saved)
            self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))
            toast.assert_called_once()

    def test_release_safety_failure_moves_only_target_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = self._target(root, "000001", status="EMERGENCY_STOPPED")
            window = self._window()
            with (
                patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(True, "보유잔량 존재")),
                patch.object(emergency_ops, "append_changelog"),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(emergency_ops, "show_toast"),
            ):
                result = emergency_ops.execute_selected_emergency_release(window, [target])

            saved = read_json_dict(target[0] / "state.json")
            self.assertEqual(("000001 테스트",), result["blocked"])
            self.assertEqual("EMERGENCY_STOPPED", saved["status"])
            self.assertTrue(saved["review_required"])
            self.assertEqual("PENDING", saved["review_status"])
            self.assertEqual("긴급정지 해제", saved["review_location"])
            self.assertTrue(saved["review_entered_at"])
            self.assertFalse(saved["trade_enabled"])

    def test_release_readback_mismatch_is_reported_as_failure(self) -> None:
        target = (Path("stocks/000001_TEST"), "000001", "테스트")
        initial = {"status": "EMERGENCY_STOPPED", "emergency_scope": "SELECTED"}
        window = self._window()
        with (
            patch.object(
                emergency_ops,
                "read_json_dict",
                side_effect=[
                    dict(initial),
                    dict(initial),
                    dict(initial),
                    dict(initial),
                    dict(initial),
                    dict(initial),
                ],
            ),
            patch("runtime_stock_state_mutation.write_state_json", return_value=True),
            patch.object(emergency_ops, "emergency_review_reason_for_stock", return_value=(False, "정상")),
            patch.object(emergency_ops, "_active_queue_reason", return_value=""),
            patch.object(emergency_ops, "_routine_name_for_emergency_release", return_value=""),
            patch.object(emergency_ops, "append_changelog"),
            patch.object(emergency_ops, "append_stock_log"),
            patch.object(emergency_ops, "show_toast") as toast,
        ):
            result = emergency_ops.execute_selected_emergency_release(window, [target])

        self.assertEqual(("000001 테스트",), result["failed"])
        self.assertEqual((), result["normal"])
        toast.assert_called_once()
        self.assertNotIn("완료", toast.call_args.kwargs["message"])

    def test_common_restore_releases_review_and_preserves_routine_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="REVIEW_REQUIRED")
            config = {
                "routine_instance_name": "지표추종매매-A",
                "assigned_routine_instance_id": "instance-a",
            }
            (stock_dir / "config.json").write_text(
                json.dumps(config, ensure_ascii=False), encoding="utf-8"
            )
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_reason": "기존 사유",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )

            with (
                patch.object(emergency_ops, "emergency_release_common_guard", return_value=(True, "")),
                patch.object(emergency_ops, "append_stock_log"),
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="RESTORE"
                )

            saved = read_json_dict(stock_dir / "state.json")
            self.assertEqual("NORMALIZED", result["status"])
            self.assertEqual("STOPPED", saved["status"])
            self.assertFalse(saved["review_required"])
            self.assertEqual("지표추종매매-A", saved["review_routine"])
            self.assertEqual(config, read_json_dict(stock_dir / "config.json"))

    def test_common_unassigned_releases_review_and_clears_routine_without_deleting_stock(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "active_routine": "지표추종매매-A",
                    "routine_name": "지표추종매매-A",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )

            def unassign_result(*_args, **_kwargs):
                (stock_dir / "config.json").write_text("{}\n", encoding="utf-8")
                return SimpleNamespace(
                    ok=True,
                    reason_code="OK",
                    reconciliation_required=False,
                )

            with (
                patch.object(emergency_ops, "emergency_release_common_guard", return_value=(True, "")),
                patch.object(emergency_ops, "append_stock_log"),
                patch.object(
                    emergency_ops,
                    "execute_assignment_unassign",
                    side_effect=unassign_result,
                ) as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="UNASSIGNED"
                )

            saved = read_json_dict(stock_dir / "state.json")
            self.assertEqual("NORMALIZED", result["status"])
            self.assertEqual("STOPPED", saved["status"])
            self.assertFalse(saved["review_required"])
            self.assertEqual("", saved["active_routine"])
            self.assertEqual("", saved["routine_name"])
            self.assertTrue(stock_dir.exists())
            unassign.assert_called_once()
            self.assertEqual((code, name), unassign.call_args.args[2:])
            self.assertEqual("", unassign.call_args.kwargs["expected_instance_id"])

    def test_common_unassigned_transaction_failure_never_writes_stopped(self) -> None:
        for reason_code, reconciliation_required in (
            ("FIELD_CONFLICT", False),
            ("RECONCILIATION_REQUIRED", True),
        ):
            with self.subTest(reason_code=reason_code):
                with tempfile.TemporaryDirectory() as root:
                    stock_dir, code, name = self._target(
                        root,
                        "000001",
                        status="REVIEW_REQUIRED",
                    )
                    config = {
                        "assigned_routine_instance_id": "instance-a",
                        "routine_instance_name": "Routine A",
                    }
                    (stock_dir / "config.json").write_text(
                        json.dumps(config),
                        encoding="utf-8",
                    )
                    before_state = read_json_dict(stock_dir / "state.json")
                    failure = SimpleNamespace(
                        ok=False,
                        reason_code=reason_code,
                        reconciliation_required=reconciliation_required,
                    )
                    with (
                        patch.object(
                            emergency_ops,
                            "emergency_release_common_guard",
                            return_value=(True, ""),
                        ),
                        patch.object(
                            emergency_ops,
                            "execute_assignment_unassign",
                            return_value=failure,
                        ),
                        patch.object(
                            emergency_ops,
                            "update_runtime_stock_status",
                        ) as runtime_write,
                    ):
                        result = emergency_ops.normalize_review_emergency_target(
                            self._window(),
                            stock_dir,
                            code,
                            name,
                            destination="UNASSIGNED",
                        )

                    self.assertEqual("FAILED", result["status"])
                    self.assertEqual(reason_code, result["reason_code"])
                    self.assertEqual(
                        reconciliation_required,
                        result["reconciliation_required"],
                    )
                    runtime_write.assert_not_called()
                    self.assertEqual(before_state, read_json_dict(stock_dir / "state.json"))
                    self.assertEqual(config, read_json_dict(stock_dir / "config.json"))

    def test_common_unassigned_readback_failure_never_writes_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(
                root,
                "000001",
                status="REVIEW_REQUIRED",
            )
            config = {"assigned_routine_instance_id": "instance-a"}
            (stock_dir / "config.json").write_text(
                json.dumps(config),
                encoding="utf-8",
            )
            success_without_readback = SimpleNamespace(
                ok=True,
                reason_code="OK",
                reconciliation_required=False,
            )
            with (
                patch.object(
                    emergency_ops,
                    "emergency_release_common_guard",
                    return_value=(True, ""),
                ),
                patch.object(
                    emergency_ops,
                    "execute_assignment_unassign",
                    return_value=success_without_readback,
                ),
                patch.object(
                    emergency_ops,
                    "update_runtime_stock_status",
                ) as runtime_write,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(),
                    stock_dir,
                    code,
                    name,
                    destination="UNASSIGNED",
                )

            self.assertEqual("FAILED", result["status"])
            self.assertEqual("READ_BACK_FAILED", result["reason_code"])
            runtime_write.assert_not_called()

    def test_common_restore_blocks_without_release_or_routine_change(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="EMERGENCY_STOPPED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "active_routine": "지표추종매매-A",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            before = read_json_dict(stock_dir / "state.json")

            with (
                patch.object(
                    emergency_ops,
                    "emergency_review_reason_for_stock",
                    return_value=(True, "보유잔량 존재"),
                ),
                patch.object(emergency_ops, "execute_assignment_unassign") as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="RESTORE"
                )

            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual(before, read_json_dict(stock_dir / "state.json"))
            unassign.assert_not_called()

    def test_common_unassigned_blocks_without_release_or_routine_change(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir, code, name = self._target(root, "000001", status="REVIEW_REQUIRED")
            state = read_json_dict(stock_dir / "state.json")
            state.update(
                {
                    "review_required": True,
                    "review_status": "PENDING",
                    "active_routine": "지표추종매매-A",
                }
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            before = read_json_dict(stock_dir / "state.json")

            with (
                patch.object(
                    emergency_ops,
                    "emergency_review_reason_for_stock",
                    return_value=(True, "보유잔량 존재"),
                ),
                patch.object(emergency_ops, "execute_assignment_unassign") as unassign,
            ):
                result = emergency_ops.normalize_review_emergency_target(
                    self._window(), stock_dir, code, name, destination="UNASSIGNED"
                )

            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual(before, read_json_dict(stock_dir / "state.json"))
            unassign.assert_not_called()

    def test_setting_window_callers_pass_current_selection_snapshot(self) -> None:
        target = (Path("stocks/000001_TEST"), "000001", "테스트")
        window = SimpleNamespace(selected_stock_infos=Mock(return_value=[target]))
        with (
            patch.object(emergency_ops, "execute_selected_emergency_stop", return_value={"ok": True}) as stop,
            patch.object(emergency_ops, "execute_selected_emergency_release", return_value={"ok": True}) as release,
        ):
            AutoTradeSettingWindow.emergency_stop_selected_auto_trade_stocks(window)

        stop.assert_called_once_with(window, [target])
        release.assert_not_called()
        self.assertFalse(
            hasattr(AutoTradeSettingWindow, "release_selected_emergency_stopped_auto_trade_stocks")
        )


if __name__ == "__main__":
    unittest.main()
