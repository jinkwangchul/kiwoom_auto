# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import real_order_preflight_service
from real_order_preflight_service import (
    commit_real_order_preflight,
    preview_real_order_preflight,
)


class RealOrderPreflightServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.queue_path = Path(self.tmp.name) / "order_queue.json"
        self.guard_path = Path(self.tmp.name) / "real_trade_guard.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _order(self, **overrides: object) -> dict[str, object]:
        order = {
            "id": "ORDER_1",
            "status": "EXECUTABLE",
            "execution_enabled": True,
            "approval_status": "APPROVED",
            "policy_status": "EXECUTABLE",
            "quantity": 10,
            "side": "BUY",
            "order_type": "LIMIT",
            "code": "003550",
            "source_signal_id": "SIG_1",
            "updated_at": "",
        }
        order.update(overrides)
        return order

    def _guard(self, **overrides: object) -> dict[str, object]:
        guard = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }
        guard.update(overrides)
        return guard

    def _context(self, **overrides: object) -> dict[str, object]:
        context = {"operator_confirmed_for_real_preflight": True}
        context.update(overrides)
        return context

    def _commit_context(self, **overrides: object) -> dict[str, object]:
        context = {"manual_real_preflight_commit_confirmed": True}
        context.update(overrides)
        return context

    def _preview(self, **order_overrides: object) -> dict[str, object]:
        preview = preview_real_order_preflight(
            self._order(**order_overrides),
            self._guard(),
            self._context(),
        )
        self.assertTrue(preview["real_preflight_preview"])
        return preview

    def _write_queue(self, orders: list[dict[str, object]]) -> None:
        self.queue_path.write_text(
            json.dumps({"version": 1, "updated_at": "", "orders": orders}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_guard(self) -> None:
        self.guard_path.write_text(json.dumps(self._guard(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_queue(self) -> dict[str, object]:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _queue_sha256(self) -> str:
        return hashlib.sha256(self.queue_path.read_bytes()).hexdigest().upper()

    def test_preview_status_not_executable_is_blocked(self) -> None:
        result = preview_real_order_preflight(self._order(status="APPROVED"), self._guard(), self._context())

        self.assertFalse(result["real_preflight_preview"])
        self.assertEqual("status", result["preflight_stage"])

    def test_preview_execution_enabled_false_is_blocked(self) -> None:
        result = preview_real_order_preflight(self._order(execution_enabled=False), self._guard(), self._context())

        self.assertFalse(result["real_preflight_preview"])
        self.assertEqual("execution_enabled", result["preflight_stage"])

    def test_preview_approval_or_policy_mismatch_is_blocked(self) -> None:
        cases = [
            ("approval_status", {"approval_status": "BLOCKED"}),
            ("policy_status", {"policy_status": "BLOCKED_POLICY"}),
        ]
        for expected_stage, overrides in cases:
            with self.subTest(overrides=overrides):
                result = preview_real_order_preflight(self._order(**overrides), self._guard(), self._context())

                self.assertFalse(result["real_preflight_preview"])
                self.assertEqual(expected_stage, result["preflight_stage"])

    def test_preview_quantity_zero_or_negative_is_blocked(self) -> None:
        for quantity in (0, -1, None, "bad"):
            with self.subTest(quantity=quantity):
                result = preview_real_order_preflight(self._order(quantity=quantity), self._guard(), self._context())

                self.assertFalse(result["real_preflight_preview"])
                self.assertEqual("quantity", result["preflight_stage"])

    def test_preview_required_order_fields_are_blocked(self) -> None:
        cases = [
            ("side", {"side": ""}),
            ("side", {"side": "HOLD"}),
            ("order_type", {"order_type": ""}),
            ("code", {"code": ""}),
            ("source_signal_id", {"source_signal_id": ""}),
        ]
        for expected_stage, overrides in cases:
            with self.subTest(overrides=overrides):
                result = preview_real_order_preflight(self._order(**overrides), self._guard(), self._context())

                self.assertFalse(result["real_preflight_preview"])
                self.assertEqual(expected_stage, result["preflight_stage"])

    def test_preview_guard_fields_are_blocked(self) -> None:
        cases = [
            {"real_trade_enabled": False},
            {"kiwoom_logged_in": False},
            {"account_selected": False},
            {"account_no": ""},
            {"operator_confirmed": False},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = preview_real_order_preflight(self._order(), self._guard(**overrides), self._context())

                self.assertFalse(result["real_preflight_preview"])
                self.assertEqual("guard", result["preflight_stage"])

    def test_preview_manual_or_operator_context_is_required(self) -> None:
        result = preview_real_order_preflight(self._order(), self._guard(), {"operator_confirmed": True})

        self.assertFalse(result["real_preflight_preview"])
        self.assertEqual("operator_confirmation", result["preflight_stage"])

    def test_preview_accepts_manual_context(self) -> None:
        result = preview_real_order_preflight(
            self._order(),
            self._guard(),
            {"manual_real_preflight_confirmed": True},
        )

        self.assertTrue(result["real_preflight_preview"])

    def test_preview_success(self) -> None:
        result = preview_real_order_preflight(self._order(), self._guard(), self._context())

        self.assertTrue(result["real_preflight_preview"])
        self.assertEqual("real_preflight_preview_created", result["preflight_stage"])
        self.assertEqual("REAL_PREFLIGHT_COMMIT_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_write"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertFalse(result["send_order_called"])

    def test_commit_without_confirmation_is_blocked(self) -> None:
        self._write_queue([self._order()])

        result = commit_real_order_preflight(self._preview(), self.queue_path, context={})

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("operator_confirmation", result["preflight_stage"])

    def test_commit_without_queue_path_is_blocked(self) -> None:
        result = commit_real_order_preflight(self._preview(), None, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("queue_path", result["preflight_stage"])

    def test_commit_invalid_preview_is_blocked(self) -> None:
        self._write_queue([self._order()])

        result = commit_real_order_preflight(
            {"real_preflight_preview": False, "next_stage": "BLOCKED"},
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("preflight_preview", result["preflight_stage"])

    def test_commit_missing_queue_file_is_blocked(self) -> None:
        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("read_queue", result["preflight_stage"])

    def test_commit_corrupt_json_is_blocked(self) -> None:
        self.queue_path.write_text("{bad", encoding="utf-8")

        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("read_queue", result["preflight_stage"])

    def test_commit_root_non_dict_is_blocked(self) -> None:
        self.queue_path.write_text("[]", encoding="utf-8")

        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("read_queue", result["preflight_stage"])

    def test_commit_orders_non_list_is_blocked(self) -> None:
        self.queue_path.write_text(json.dumps({"orders": {}}), encoding="utf-8")

        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("read_queue", result["preflight_stage"])

    def test_commit_target_order_missing_is_blocked(self) -> None:
        self._write_queue([self._order(id="OTHER")])

        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("order", result["preflight_stage"])

    def test_commit_target_status_not_executable_is_blocked(self) -> None:
        self._write_queue([self._order(status="APPROVED")])

        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("status", result["preflight_stage"])

    def test_commit_execution_enabled_false_is_blocked(self) -> None:
        self._write_queue([self._order(execution_enabled=False)])

        result = commit_real_order_preflight(self._preview(), self.queue_path, context=self._commit_context())

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("execution_enabled", result["preflight_stage"])

    def test_commit_with_stale_snapshot_is_blocked(self) -> None:
        self._write_queue([self._order()])

        result = commit_real_order_preflight(
            self._preview(),
            self.queue_path,
            preview_queue_snapshot={"sha256": "OLD_HASH"},
            context=self._commit_context(),
        )

        self.assertFalse(result["real_preflight_committed"])
        self.assertEqual("stale_preview", result["preflight_stage"])
        self.assertIn(
            "queue file changed after real preflight preview; rerun REAL Preflight",
            result["blocked_reasons"],
        )

    def test_temp_queue_success_commit_creates_real_ready(self) -> None:
        self._write_queue([self._order()])
        self._write_guard()
        before_sha = self._queue_sha256()

        result = commit_real_order_preflight(
            self._preview(),
            self.queue_path,
            guard_path=self.guard_path,
            preview_queue_snapshot={"sha256": before_sha},
            context=self._commit_context(),
        )
        data = self._read_queue()
        order = data["orders"][0]

        self.assertTrue(result["real_preflight_committed"])
        self.assertEqual("real_ready_committed", result["preflight_stage"])
        self.assertEqual("EXECUTION_PREVIEW_REQUIRED", result["next_stage"])
        self.assertTrue(result["changed"])
        self.assertEqual("EXECUTABLE", result["before_status"])
        self.assertEqual("REAL_READY", result["after_status"])
        self.assertTrue(result["execution_enabled"])
        self.assertEqual("REAL_READY", result["real_preflight_status"])
        self.assertEqual("실주문 사전검사 통과", result["real_preflight_reason"])
        self.assertEqual(str(self.guard_path), result["guard_path"])
        self.assertEqual(before_sha, result["before_sha256"])
        self.assertNotEqual(result["before_sha256"], result["after_sha256"])
        self.assertFalse(result["send_order_called"])
        self.assertEqual("REAL_READY", order["status"])
        self.assertEqual("REAL_READY", order["real_preflight_status"])
        self.assertEqual("실주문 사전검사 통과", order["real_preflight_reason"])
        self.assertIn("real_preflight_checked_at", order)
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_commit_with_backup_false_does_not_create_backup(self) -> None:
        self._write_queue([self._order()])

        result = commit_real_order_preflight(
            self._preview(),
            self.queue_path,
            context=self._commit_context(),
            backup=False,
        )

        self.assertTrue(result["real_preflight_committed"])
        self.assertIsNone(result["backup_path"])
        self.assertFalse(Path(str(self.queue_path) + ".bak").exists())

    def test_commit_accepts_operator_commit_confirmation(self) -> None:
        self._write_queue([self._order()])

        result = commit_real_order_preflight(
            self._preview(),
            self.queue_path,
            context={"operator_confirmed_for_real_preflight_commit": True},
        )

        self.assertTrue(result["real_preflight_committed"])

    def test_service_does_not_call_send_order_or_execution_preview(self) -> None:
        self._write_queue([self._order()])

        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("execution_preview_order_service.preview_execution_for_real_ready_order") as execution_preview,
        ):
            result = commit_real_order_preflight(
                self._preview(),
                self.queue_path,
                context=self._commit_context(),
            )

        self.assertTrue(result["real_preflight_committed"])
        send_order_stub.assert_not_called()
        execution_preview.assert_not_called()

    def test_module_does_not_reference_gui_timer_or_queue_writer(self) -> None:
        module_text = real_order_preflight_service.__loader__.get_source(
            real_order_preflight_service.__name__
        )

        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("ORDER_QUEUED", module_text)
        self.assertNotIn("apply_real_order_preflight_for_order", module_text)

    def test_input_dicts_are_not_mutated(self) -> None:
        self._write_queue([self._order()])
        order = self._order()
        guard = self._guard()
        context = self._context()
        preview = preview_real_order_preflight(order, guard, context)
        snapshot = {"sha256": self._queue_sha256()}
        commit_context = self._commit_context()
        original_order = deepcopy(order)
        original_guard = deepcopy(guard)
        original_context = deepcopy(context)
        original_preview = deepcopy(preview)
        original_snapshot = deepcopy(snapshot)
        original_commit_context = deepcopy(commit_context)

        commit_real_order_preflight(preview, self.queue_path, self.guard_path, snapshot, commit_context)

        self.assertEqual(original_order, order)
        self.assertEqual(original_guard, guard)
        self.assertEqual(original_context, context)
        self.assertEqual(original_preview, preview)
        self.assertEqual(original_snapshot, snapshot)
        self.assertEqual(original_commit_context, commit_context)


if __name__ == "__main__":
    unittest.main()
