# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest
from unittest import mock

from execution_preview_service import preview_execution_for_order


ROOT = Path(__file__).resolve().parents[1]


def _load_mapper_module():
    mapper_path = next((ROOT / "routines").glob("*/routine_rule_mapper.py"))
    spec = spec_from_file_location("routine_rule_mapper_bridge_contract", mapper_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


class RuleApplyPreviewExecutionPreviewBridgeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = _load_mapper_module()
        self.current_rules = {
            "bar": {"bar_minutes": 1, "buy_delay_bar": 2, "sell_delay_bar": 3},
            "buy": {
                "enabled": True,
                "groups_logic": "OR",
                "groups": [
                    {
                        "enabled": True,
                        "name": "buy_group_1",
                        "conditions_logic": "AND",
                        "conditions": [{"enabled": True, "not": False, "target": "OSC", "operator": "TURN_UP"}],
                    }
                ],
            },
            "sell": {
                "enabled": True,
                "signal_logic": "OR",
                "signals": {
                    "macd_sell": {
                        "enabled": True,
                        "groups_logic": "OR",
                        "groups": [
                            {
                                "enabled": True,
                                "name": "sell_macd",
                                "conditions_logic": "AND",
                                "conditions": [{"enabled": True, "not": False, "target": "OSC", "operator": "TURN_DOWN"}],
                            }
                        ],
                    }
                },
            },
            "indicators": {"macd": {"enabled": True}, "rsi": {"period": 10}},
        }
        self.ui_state = {
            "basic": {"basic_signal_interval_combo": "5"},
            "buy_ui": {
                "signal_filter": {
                    "buy_ocr_bar_line": "0",
                    "buy_ocr_compare_combo": "이하",
                    "buy_ocr_sign_combo": "-",
                    "buy_ocr_turn_combo": "상승",
                    "buy_ocr_value_line": "91",
                    "buy_rsi_compare_combo": "이하",
                    "buy_rsi_period_line": "14",
                    "buy_rsi_value_line": "45",
                },
            },
            "sell_ui": {
                "signal_conditions": {
                    "condition_c": {
                        "macd_check": True,
                        "macd_compare_combo": "이하",
                        "macd_kind_combo": "MACD선",
                        "macd_sign_combo": "-",
                        "macd_value_line": "1.0",
                    }
                }
            },
        }

    def _guard(self) -> dict[str, object]:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "account_no": "12345678",
        }

    def _signal_context(self) -> dict[str, object]:
        return {
            "order_id": "ORDER_RULE_BRIDGE_1",
            "source_signal_id": "SIGNAL_RULE_BRIDGE_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "시장가",
        }

    def _build_apply_preview(self) -> dict[str, object]:
        preview = self.mapper.build_engine_rules_preview_from_ui_state(deepcopy(self.ui_state), deepcopy(self.current_rules))
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "bar.bar_minutes": "APPROVED",
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            },
        )
        patch_preview = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)
        return self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)

    def _bridge_apply_preview_to_order_contract(
        self,
        apply_preview: dict[str, object],
        signal_context: dict[str, object],
    ) -> dict[str, object]:
        return {
            "id": signal_context["order_id"],
            "order_id": signal_context["order_id"],
            "source_signal_id": signal_context["source_signal_id"],
            "status": "REAL_READY",
            "code": signal_context["code"],
            "side": signal_context["side"],
            "quantity": signal_context["quantity"],
            "price": signal_context["price"],
            "execution_enabled": True,
            "order_intent": {
                "side": signal_context["side"],
                "hoga": signal_context["hoga"],
            },
            "rule_apply_preview": deepcopy(apply_preview),
            "rule_snapshot_preview": deepcopy(apply_preview.get("applied_rules_preview")),
        }

    def test_apply_preview_alone_cannot_feed_execution_preview_contract(self) -> None:
        apply_preview = self._build_apply_preview()
        original = deepcopy(apply_preview)

        result = preview_execution_for_order(apply_preview, self._guard())

        self.assertEqual(original, apply_preview)
        self.assertFalse(result["ok"])
        self.assertEqual("execution_preview", result["summary"]["blocked_stage"])
        self.assertFalse(result["queue_pending_result"]["queue_pending"])
        self.assertFalse(result["queue_write_preview_result"]["write_preview"])
        self.assertEqual("order_intent hoga is unresolved", result["pipeline_result"]["blocked_reason"])
        self.assertIn(
            "order status is not REAL_READY",
            "\n".join(result["pipeline_result"]["pipeline"]["execution_preview"]["warnings"]),
        )

    def test_bridge_order_contract_generates_execution_queue_pending_and_write_previews(self) -> None:
        apply_preview = self._build_apply_preview()
        signal_context = self._signal_context()
        original_apply_preview = deepcopy(apply_preview)
        original_signal_context = deepcopy(signal_context)
        order_contract = self._bridge_apply_preview_to_order_contract(apply_preview, signal_context)
        original_order_contract = deepcopy(order_contract)

        result = preview_execution_for_order(order_contract, self._guard())

        self.assertEqual(original_apply_preview, apply_preview)
        self.assertEqual(original_signal_context, signal_context)
        self.assertEqual(original_order_contract, order_contract)
        self.assertTrue(result["ok"])
        self.assertTrue(result["summary"]["ready_for_execution_request"])
        self.assertTrue(result["candidate_result"]["candidate"])
        self.assertTrue(result["queue_pending_result"]["queue_pending"])
        self.assertTrue(result["queue_write_preview_result"]["write_preview"])
        self.assertEqual("QUEUE_WRITE_REQUIRED", result["queue_write_preview_result"]["next_stage"])
        self.assertTrue(result["queue_write_preview_result"]["preview_only"])
        self.assertTrue(result["queue_write_preview_result"]["no_write"])

    def test_bridge_flow_does_not_commit_send_order_or_mutate_protected_files(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        order_contract = self._bridge_apply_preview_to_order_contract(self._build_apply_preview(), self._signal_context())

        with mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write_commit, \
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = preview_execution_for_order(order_contract, self._guard())

        self.assertTrue(result["queue_write_preview_result"]["write_preview"])
        queue_write_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
