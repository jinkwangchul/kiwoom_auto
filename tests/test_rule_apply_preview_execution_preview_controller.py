# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from rule_apply_preview_execution_preview_controller import preview_execution_from_rule_apply_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class RuleApplyPreviewExecutionPreviewControllerTest(unittest.TestCase):
    def _apply_preview(self) -> dict[str, object]:
        return {
            "mode": "approved_rule_apply_preview",
            "stage": "RULE_APPLY_PREVIEW",
            "applied_rules_preview": {
                "bar": {"bar_minutes": 5},
                "buy": {"enabled": True},
                "sell": {"enabled": True},
            },
            "applied_patches": [{"operation": "set_value", "target_path": "bar.bar_minutes"}],
            "skipped_patches": [],
            "summary": {"patches": 1, "applied": 1, "skipped": 0},
            "warnings": [],
        }

    def _signal_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "order_id": "ORDER_RULE_CONTROLLER_1",
            "source_signal_id": "SIGNAL_RULE_CONTROLLER_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "\uc2dc\uc7a5\uac00",
        }
        result.update(overrides)
        return result

    def _guard(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "account_no": "12345678",
        }
        result.update(overrides)
        return result

    def test_ready_result_wraps_execution_queue_pending_and_queue_write_previews(self) -> None:
        result = preview_execution_from_rule_apply_preview(
            self._apply_preview(),
            self._signal_context(),
            guard=self._guard(),
        )

        self.assertEqual("RULE_APPLY_PREVIEW_EXECUTION_PREVIEW_CONTROLLER", result["controller_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["queue_commit_called"])
        self.assertEqual("REAL_READY", result["order_contract"]["status"])
        self.assertTrue(result["execution_preview"]["ok"])
        self.assertTrue(result["queue_pending_result"]["queue_pending"])
        self.assertTrue(result["queue_write_preview_result"]["write_preview"])
        self.assertTrue(result["queue_write_preview_result"]["preview_only"])
        self.assertTrue(result["queue_write_preview_result"]["no_write"])

    def test_missing_signal_context_required_field_is_invalid(self) -> None:
        signal_context = self._signal_context()
        signal_context.pop("source_signal_id")

        result = preview_execution_from_rule_apply_preview(
            self._apply_preview(),
            signal_context,
            guard=self._guard(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("source_signal_id", result["issues"][0])
        self.assertIsNone(result["execution_preview"])

    def test_invalid_apply_preview_is_invalid(self) -> None:
        result = preview_execution_from_rule_apply_preview(
            {"stage": "RULE_APPLY_PREVIEW"},
            self._signal_context(),
            guard=self._guard(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("approved_rule_apply_preview", result["issues"][0])

    def test_execution_preview_blocked_is_blocked(self) -> None:
        result = preview_execution_from_rule_apply_preview(
            self._apply_preview(),
            self._signal_context(),
            guard=self._guard(operator_confirmed=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["execution_preview"]["ok"])
        self.assertTrue(any("execution preview blocked" in issue for issue in result["issues"]))
        self.assertFalse(result["queue_write_preview_result"]["write_preview"])

    def test_queue_write_preview_blocked_is_blocked(self) -> None:
        preview_result = {
            "ok": True,
            "warnings": [],
            "queue_pending_result": {"queue_pending": True, "warnings": []},
            "queue_write_preview_result": {"write_preview": False, "blocked_reasons": ["duplicate order_id"]},
        }
        with mock.patch(
            "rule_apply_preview_execution_preview_controller.preview_execution_for_order",
            return_value=preview_result,
        ):
            result = preview_execution_from_rule_apply_preview(
                self._apply_preview(),
                self._signal_context(),
                guard=self._guard(),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("duplicate order_id", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        apply_preview = self._apply_preview()
        signal_context = self._signal_context()
        guard = self._guard()
        guard_defaults = {"real_trade_guard_ok": True}
        order_defaults = {"memo": "default"}
        originals = (
            deepcopy(apply_preview),
            deepcopy(signal_context),
            deepcopy(guard),
            deepcopy(guard_defaults),
            deepcopy(order_defaults),
        )

        result = preview_execution_from_rule_apply_preview(
            apply_preview,
            signal_context,
            guard=guard,
            guard_defaults=guard_defaults,
            order_defaults=order_defaults,
        )
        result["order_contract"]["rule_apply_preview"]["applied_rules_preview"]["bar"]["bar_minutes"] = 99

        self.assertEqual(originals[0], apply_preview)
        self.assertEqual(originals[1], signal_context)
        self.assertEqual(originals[2], guard)
        self.assertEqual(originals[3], guard_defaults)
        self.assertEqual(originals[4], order_defaults)

    def test_runtime_order_queue_rules_hash_unchanged_and_no_commit_or_send_order(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = preview_execution_from_rule_apply_preview(
                self._apply_preview(),
                self._signal_context(),
                guard=self._guard(),
            )

        self.assertEqual("READY", result["status"])
        queue_write_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
