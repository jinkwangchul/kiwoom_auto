# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import execution_enable_service
from execution_enable_service import commit_execution_enable, preview_execution_enable


class ExecutionEnableServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.queue_path = Path(self.tmp.name) / "order_queue.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _order(self, **overrides: object) -> dict:
        order = {
            "id": "ORDER_1",
            "status": "EXECUTABLE",
            "execution_enabled": False,
            "quantity": 10,
            "side": "BUY",
            "order_type": "BUY_SIGNAL_CANDIDATE",
            "code": "003550",
            "source_signal_id": "SIG_1",
            "approval_status": "APPROVED",
            "policy_status": "EXECUTABLE",
        }
        order.update(overrides)
        return order

    def _context(self, **overrides: object) -> dict:
        context = {"operator_confirmed_for_execution_enable": True}
        context.update(overrides)
        return context

    def _commit_context(self, **overrides: object) -> dict:
        context = {"manual_execution_enable_commit_confirmed": True}
        context.update(overrides)
        return context

    def _enable_preview(self, **overrides: object) -> dict:
        preview = preview_execution_enable(self._order(**overrides), self._context())
        self.assertTrue(preview["enable_preview"])
        return preview

    def _write_queue(self, orders: list[dict]) -> None:
        self.queue_path.write_text(
            json.dumps({"version": 1, "updated_at": "", "orders": orders}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_queue(self) -> dict:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _queue_sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.queue_path.read_bytes()).hexdigest().upper()

    def test_status_not_executable_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(status="APPROVED"), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("status", result["enable_stage"])
        self.assertIn("order.status must be EXECUTABLE", result["blocked_reasons"])

    def test_execution_enabled_true_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(execution_enabled=True), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("execution_enabled", result["enable_stage"])

    def test_quantity_zero_or_negative_is_blocked(self) -> None:
        for quantity in (0, -1, None, "bad"):
            with self.subTest(quantity=quantity):
                result = preview_execution_enable(self._order(quantity=quantity), self._context())

                self.assertFalse(result["enable_preview"])
                self.assertEqual("quantity", result["enable_stage"])

    def test_side_missing_or_invalid_is_blocked(self) -> None:
        for side in ("", None, "HOLD"):
            with self.subTest(side=side):
                result = preview_execution_enable(self._order(side=side), self._context())

                self.assertFalse(result["enable_preview"])
                self.assertEqual("side", result["enable_stage"])

    def test_order_type_missing_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(order_type=""), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("order_type", result["enable_stage"])

    def test_code_missing_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(code=""), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("code", result["enable_stage"])

    def test_source_signal_id_missing_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(source_signal_id=""), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("source_signal_id", result["enable_stage"])

    def test_approval_status_not_approved_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(approval_status="BLOCKED"), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("approval_status", result["enable_stage"])

    def test_policy_status_not_executable_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(policy_status="BLOCKED_POLICY"), self._context())

        self.assertFalse(result["enable_preview"])
        self.assertEqual("policy_status", result["enable_stage"])

    def test_operator_confirmed_for_execution_enable_is_required(self) -> None:
        result = preview_execution_enable(self._order(), {})

        self.assertFalse(result["enable_preview"])
        self.assertEqual("operator_confirmation", result["enable_stage"])
        self.assertIn(
            "context.operator_confirmed_for_execution_enable is not true",
            result["blocked_reasons"],
        )

    def test_existing_operator_confirmed_alone_is_blocked(self) -> None:
        result = preview_execution_enable(self._order(), {"operator_confirmed": True})

        self.assertFalse(result["enable_preview"])
        self.assertEqual("operator_confirmation", result["enable_stage"])

    def test_all_conditions_create_enable_preview(self) -> None:
        result = preview_execution_enable(self._order(), self._context())

        self.assertTrue(result["enable_preview"])
        self.assertEqual("execution_enable_preview_created", result["enable_stage"])
        self.assertEqual("EXECUTION_ENABLE_COMMIT_REQUIRED", result["next_stage"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("SIG_1", result["source_signal_id"])
        self.assertEqual("003550", result["code"])
        self.assertEqual("BUY", result["side"])
        self.assertEqual(10, result["quantity"])
        self.assertEqual("BUY_SIGNAL_CANDIDATE", result["order_type"])

    def test_preview_only_and_no_write_are_always_true(self) -> None:
        success = preview_execution_enable(self._order(), self._context())
        blocked = preview_execution_enable(self._order(status="APPROVED"), self._context())

        for result in (success, blocked):
            self.assertTrue(result["preview_only"])
            self.assertTrue(result["no_write"])

    def test_send_order_and_runtime_write_are_not_called(self) -> None:
        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = preview_execution_enable(self._order(), self._context())

        self.assertTrue(result["enable_preview"])
        send_order_stub.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_service_module_does_not_reference_gui_timer_or_send_order(self) -> None:
        module_text = execution_enable_service.__loader__.get_source(
            execution_enable_service.__name__
        )

        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("SendOrder", module_text)
        self.assertNotIn("send_order", module_text)

    def test_input_dict_is_not_mutated(self) -> None:
        order = self._order()
        context = self._context()
        original_order = deepcopy(order)
        original_context = deepcopy(context)

        preview_execution_enable(order, context)

        self.assertEqual(original_order, order)
        self.assertEqual(original_context, context)

    def test_commit_without_confirmation_is_blocked(self) -> None:
        self._write_queue([self._order()])

        result = commit_execution_enable(self._enable_preview(), self.queue_path, context={})

        self.assertFalse(result["enabled"])
        self.assertEqual("operator_confirmation", result["enable_stage"])
        self.assertIn("manual execution enable commit confirmation is required", result["blocked_reasons"])

    def test_commit_without_queue_path_is_blocked(self) -> None:
        result = commit_execution_enable(self._enable_preview(), None, context=self._commit_context())

        self.assertFalse(result["enabled"])
        self.assertEqual("queue_path", result["enable_stage"])

    def test_commit_with_invalid_enable_preview_is_blocked(self) -> None:
        self._write_queue([self._order()])

        result = commit_execution_enable(
            {"enable_preview": False, "next_stage": "BLOCKED"},
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("enable_preview", result["enable_stage"])

    def test_commit_with_missing_queue_file_is_blocked(self) -> None:
        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("read_queue", result["enable_stage"])

    def test_commit_with_corrupt_json_is_blocked(self) -> None:
        self.queue_path.write_text("{bad", encoding="utf-8")

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("read_queue", result["enable_stage"])

    def test_commit_with_root_non_dict_is_blocked(self) -> None:
        self.queue_path.write_text("[]", encoding="utf-8")

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("read_queue", result["enable_stage"])

    def test_commit_with_orders_non_list_is_blocked(self) -> None:
        self.queue_path.write_text(json.dumps({"orders": {}}), encoding="utf-8")

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("read_queue", result["enable_stage"])

    def test_commit_with_missing_order_id_is_blocked(self) -> None:
        preview = self._enable_preview()
        preview["order_id"] = ""
        self._write_queue([self._order()])

        result = commit_execution_enable(preview, self.queue_path, context=self._commit_context())

        self.assertFalse(result["enabled"])
        self.assertEqual("order_id", result["enable_stage"])

    def test_commit_with_target_order_missing_is_blocked(self) -> None:
        self._write_queue([self._order(id="OTHER")])

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("order", result["enable_stage"])

    def test_commit_revalidates_target_order_fields(self) -> None:
        cases = [
            ("status", {"status": "APPROVED"}),
            ("execution_enabled", {"execution_enabled": True}),
            ("quantity", {"quantity": 0}),
            ("side", {"side": ""}),
            ("side", {"side": "HOLD"}),
            ("order_type", {"order_type": ""}),
            ("code", {"code": ""}),
            ("source_signal_id", {"source_signal_id": ""}),
            ("approval_status", {"approval_status": "BLOCKED"}),
            ("policy_status", {"policy_status": "BLOCKED_POLICY"}),
        ]
        for expected_stage, overrides in cases:
            with self.subTest(overrides=overrides):
                self._write_queue([self._order(**overrides)])
                result = commit_execution_enable(
                    self._enable_preview(),
                    self.queue_path,
                    context=self._commit_context(),
                )

                self.assertFalse(result["enabled"])
                self.assertEqual(expected_stage, result["enable_stage"])

    def test_commit_with_stale_snapshot_is_blocked(self) -> None:
        self._write_queue([self._order()])
        snapshot = {"sha256": "OLD_HASH"}

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            preview_queue_snapshot=snapshot,
            context=self._commit_context(),
        )

        self.assertFalse(result["enabled"])
        self.assertEqual("stale_preview", result["enable_stage"])
        self.assertIn("queue file changed after execution enable preview; rerun preview", result["blocked_reasons"])

    def test_commit_success_enables_execution_only(self) -> None:
        self._write_queue([self._order()])
        before_sha = self._queue_sha256()

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            preview_queue_snapshot={"sha256": before_sha},
            context=self._commit_context(),
        )
        data = self._read_queue()
        order = data["orders"][0]

        self.assertTrue(result["enabled"])
        self.assertEqual("execution_enabled_committed", result["enable_stage"])
        self.assertEqual("REAL_PREFLIGHT_REQUIRED", result["next_stage"])
        self.assertTrue(result["changed"])
        self.assertEqual("EXECUTABLE", result["before_status"])
        self.assertEqual("EXECUTABLE", result["after_status"])
        self.assertFalse(result["before_execution_enabled"])
        self.assertTrue(result["after_execution_enabled"])
        self.assertEqual(before_sha, result["before_sha256"])
        self.assertNotEqual(result["before_sha256"], result["after_sha256"])
        self.assertEqual("EXECUTABLE", order["status"])
        self.assertTrue(order["execution_enabled"])
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_commit_with_backup_false_does_not_create_backup(self) -> None:
        self._write_queue([self._order()])

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context=self._commit_context(),
            backup=False,
        )

        self.assertTrue(result["enabled"])
        self.assertIsNone(result["backup_path"])
        self.assertFalse(Path(str(self.queue_path) + ".bak").exists())

    def test_commit_supports_operator_commit_confirmation(self) -> None:
        self._write_queue([self._order()])

        result = commit_execution_enable(
            self._enable_preview(),
            self.queue_path,
            context={"operator_confirmed_for_execution_enable_commit": True},
        )

        self.assertTrue(result["enabled"])

    def test_commit_input_dicts_are_not_mutated(self) -> None:
        self._write_queue([self._order()])
        preview = self._enable_preview()
        snapshot = {"sha256": self._queue_sha256()}
        context = self._commit_context()
        original_preview = deepcopy(preview)
        original_snapshot = deepcopy(snapshot)
        original_context = deepcopy(context)

        commit_execution_enable(preview, self.queue_path, snapshot, context)

        self.assertEqual(original_preview, preview)
        self.assertEqual(original_snapshot, snapshot)
        self.assertEqual(original_context, context)

    def test_commit_does_not_call_send_order_or_real_preflight(self) -> None:
        self._write_queue([self._order()])

        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("real_order_preflight.apply_real_order_preflight_for_order") as preflight,
        ):
            result = commit_execution_enable(
                self._enable_preview(),
                self.queue_path,
                context=self._commit_context(),
            )

        self.assertTrue(result["enabled"])
        send_order_stub.assert_not_called()
        preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
