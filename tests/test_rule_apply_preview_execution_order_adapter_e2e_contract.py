# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_preview_service import preview_execution_for_order
from rule_apply_preview_execution_order_adapter import build_rule_apply_preview_execution_order_contract


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


class RuleApplyPreviewExecutionOrderAdapterE2EContractTest(unittest.TestCase):
    def _apply_preview(self) -> dict[str, object]:
        return {
            "mode": "approved_rule_apply_preview",
            "stage": "RULE_APPLY_PREVIEW",
            "applied_rules_preview": {
                "bar": {"bar_minutes": 5},
                "buy": {"enabled": True},
                "sell": {"enabled": True},
                "indicators": {"macd": {"enabled": True}},
            },
            "applied_patches": [
                {"operation": "set_value", "target_path": "bar.bar_minutes"},
                {"operation": "merge_conditions", "target_path": "buy.groups[0].conditions"},
            ],
            "skipped_patches": [],
            "summary": {"patches": 2, "applied": 2, "skipped": 0},
            "warnings": [],
        }

    def _signal_context(self) -> dict[str, object]:
        return {
            "order_id": "ORDER_RULE_APPLY_E2E_1",
            "source_signal_id": "SIGNAL_RULE_APPLY_E2E_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "\uc2dc\uc7a5\uac00",
        }

    def _guard_defaults(self) -> dict[str, object]:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "account_no": "12345678",
        }

    def test_apply_preview_to_queue_write_preview_e2e_contract(self) -> None:
        apply_preview = self._apply_preview()
        signal_context = self._signal_context()
        guard_defaults = self._guard_defaults()
        original_apply_preview = deepcopy(apply_preview)
        original_signal_context = deepcopy(signal_context)
        original_guard_defaults = deepcopy(guard_defaults)
        before = {path: _sha256(path) for path in _protected_paths()}

        order_contract = build_rule_apply_preview_execution_order_contract(
            apply_preview,
            signal_context,
            guard_defaults=guard_defaults,
        )
        order_contract_before = deepcopy(order_contract)

        with mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            preview = preview_execution_for_order(order_contract, guard_defaults)

        self.assertEqual(original_apply_preview, apply_preview)
        self.assertEqual(original_signal_context, signal_context)
        self.assertEqual(original_guard_defaults, guard_defaults)
        self.assertEqual(order_contract_before, order_contract)

        self.assertEqual("REAL_READY", order_contract["status"])
        self.assertTrue(order_contract["execution_enabled"])
        self.assertTrue(order_contract["preview_only"])
        self.assertEqual("BUY", order_contract["order_intent"]["side"])
        self.assertEqual("\uc2dc\uc7a5\uac00", order_contract["order_intent"]["hoga"])

        self.assertTrue(preview["ok"])
        self.assertTrue(preview["pipeline_result"]["ok"])
        self.assertTrue(preview["summary"]["ready_for_execution_request"])

        queue_pending = preview["queue_pending_result"]
        self.assertTrue(queue_pending["queue_pending"])
        self.assertEqual("queue_pending_created", queue_pending["queue_pending_stage"])
        self.assertEqual("QUEUE_WRITER_REQUIRED", queue_pending["next_stage"])
        self.assertTrue(queue_pending["preview_only"])
        self.assertTrue(queue_pending["no_write"])

        queue_write = preview["queue_write_preview_result"]
        self.assertTrue(queue_write["write_preview"])
        self.assertEqual("order_queued_record_preview_created", queue_write["write_stage"])
        self.assertEqual("QUEUE_WRITE_REQUIRED", queue_write["next_stage"])
        self.assertTrue(queue_write["preview_only"])
        self.assertTrue(queue_write["no_write"])
        self.assertEqual("ORDER_QUEUED", queue_write["order_queued_record_preview"]["status"])
        self.assertFalse(queue_write["order_queued_record_preview"]["send_order_called"])
        self.assertFalse(queue_write["order_queued_record_preview"]["execution_enabled"])

        queue_write_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})

    def test_missing_signal_context_blocks_before_preview_services(self) -> None:
        with self.assertRaisesRegex(ValueError, "source_signal_id"):
            build_rule_apply_preview_execution_order_contract(
                self._apply_preview(),
                {
                    "order_id": "ORDER_RULE_APPLY_E2E_1",
                    "code": "003550",
                    "side": "BUY",
                    "quantity": 10,
                    "price": 85000,
                    "hoga": "\uc2dc\uc7a5\uac00",
                },
                guard_defaults=self._guard_defaults(),
            )


if __name__ == "__main__":
    unittest.main()
