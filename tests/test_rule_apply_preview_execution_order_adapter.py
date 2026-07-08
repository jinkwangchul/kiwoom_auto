# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_preview_service import preview_execution_for_order
from rule_apply_preview_execution_order_adapter import (
    ADAPTER_TYPE,
    build_rule_apply_preview_execution_order_contract,
)


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


class RuleApplyPreviewExecutionOrderAdapterTest(unittest.TestCase):
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
            "order_id": "ORDER_RULE_APPLY_ADAPTER_1",
            "source_signal_id": "SIGNAL_RULE_APPLY_ADAPTER_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "\uc2dc\uc7a5\uac00",
        }
        result.update(overrides)
        return result

    def _guard(self) -> dict[str, object]:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "account_no": "12345678",
        }

    def test_builds_real_ready_order_contract(self) -> None:
        order = build_rule_apply_preview_execution_order_contract(
            self._apply_preview(),
            self._signal_context(),
            guard_defaults=self._guard(),
            order_defaults={"memo": "preview default"},
        )

        self.assertEqual(ADAPTER_TYPE, order["adapter_type"])
        self.assertEqual("REAL_READY", order["status"])
        self.assertTrue(order["execution_enabled"])
        self.assertTrue(order["preview_only"])
        self.assertEqual("ORDER_RULE_APPLY_ADAPTER_1", order["id"])
        self.assertEqual("SIGNAL_RULE_APPLY_ADAPTER_1", order["source_signal_id"])
        self.assertEqual("BUY", order["order_intent"]["side"])
        self.assertEqual("\uc2dc\uc7a5\uac00", order["order_intent"]["hoga"])
        self.assertEqual("preview default", order["memo"])
        self.assertEqual(self._guard(), order["guard_defaults"])

    def test_missing_required_signal_context_field_raises_value_error(self) -> None:
        signal_context = self._signal_context()
        signal_context.pop("order_id")

        with self.assertRaisesRegex(ValueError, "order_id"):
            build_rule_apply_preview_execution_order_contract(self._apply_preview(), signal_context)

    def test_invalid_apply_preview_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved_rule_apply_preview"):
            build_rule_apply_preview_execution_order_contract({"stage": "RULE_APPLY_PREVIEW"}, self._signal_context())

    def test_inputs_are_not_mutated(self) -> None:
        apply_preview = self._apply_preview()
        signal_context = self._signal_context()
        guard = self._guard()
        order_defaults = {"order_intent": {"note": "keep"}}
        original_apply = deepcopy(apply_preview)
        original_signal = deepcopy(signal_context)
        original_guard = deepcopy(guard)
        original_defaults = deepcopy(order_defaults)

        order = build_rule_apply_preview_execution_order_contract(
            apply_preview,
            signal_context,
            guard_defaults=guard,
            order_defaults=order_defaults,
        )
        order["rule_apply_preview"]["applied_rules_preview"]["bar"]["bar_minutes"] = 99

        self.assertEqual(original_apply, apply_preview)
        self.assertEqual(original_signal, signal_context)
        self.assertEqual(original_guard, guard)
        self.assertEqual(original_defaults, order_defaults)

    def test_execution_queue_pending_and_queue_write_previews_are_generated(self) -> None:
        order = build_rule_apply_preview_execution_order_contract(
            self._apply_preview(),
            self._signal_context(),
            guard_defaults=self._guard(),
        )

        result = preview_execution_for_order(order, self._guard())

        self.assertTrue(result["ok"])
        self.assertTrue(result["candidate_result"]["candidate"])
        self.assertTrue(result["queue_pending_result"]["queue_pending"])
        self.assertTrue(result["queue_write_preview_result"]["write_preview"])
        self.assertEqual("QUEUE_WRITE_REQUIRED", result["queue_write_preview_result"]["next_stage"])
        self.assertTrue(result["queue_write_preview_result"]["preview_only"])
        self.assertTrue(result["queue_write_preview_result"]["no_write"])

    def test_does_not_commit_send_order_or_mutate_protected_files(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        order = build_rule_apply_preview_execution_order_contract(
            self._apply_preview(),
            self._signal_context(),
            guard_defaults=self._guard(),
        )

        with mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = preview_execution_for_order(order, self._guard())

        self.assertTrue(result["queue_write_preview_result"]["write_preview"])
        queue_write_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
