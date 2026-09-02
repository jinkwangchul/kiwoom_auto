# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import auto_trade_order_execution_boundary as execution_boundary
import order_approval_engine
from group_pack_packing import pack_group
from group_pack_registration import register_group_pack
from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
from logical_group_registry import LogicalGroupRepository
from order_candidate_engine import build_order_candidate_from_execution_intent
import routine_signal_queue
import routine_signal_consumer
from routine_instance_registry import load_routine_definitions
from routine_package_contract import validate_routine_definition_capabilities


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = PROJECT_ROOT / "routines" / "지표추종매매"


def _load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROUTINE_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROUTINE_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROUTINE_DIR))
    return module


sell_bridge = _load_module("routine_sell_execution.py", "indicator_follow_sell_execution_test")
rule_validator = _load_module("routine_rule_commit_validator.py", "indicator_follow_sell_rule_validator_test")


class IndicatorFollowSellExecutionConnectionTest(unittest.TestCase):
    def _rules(
        self,
        *,
        selected_sets=None,
        hoga_mode: str = "단일호가",
        price_method: str = "주문가",
    ) -> dict:
        selected = ["setting_a"] if selected_sets is None else selected_sets
        return {
            "principle": {"execution_enabled": True},
            "safety": {"real_order_allowed": False},
            "sell": {
                "method": {
                    "selected_sets": selected,
                    "setting_a": {
                        "perform1_title_combo": hoga_mode,
                        "perform1_single_combo": price_method,
                    },
                    "setting_b": {
                        "perform1_title_combo": "단일호가",
                        "perform1_single_combo": "시장가",
                    },
                    "setting_c": {
                        "perform1_title_combo": "단일호가",
                        "perform1_single_combo": "주문가",
                    },
                }
            },
        }

    def _context(self, *, holding_qty=7, status="resolved", rules=None, **overrides) -> dict:
        context = {
            "cycle": {
                "status": status,
                "holding_qty": holding_qty,
                "cycle_identity": "CYCLE-SELL-1",
                "unresolved_reason": "LEDGER_UNRESOLVED" if status != "resolved" else "",
            },
            "rules": rules if rules is not None else self._rules(),
            "reference_price": 81_000,
            "routine_instance_id": "INSTANCE-SELL-1",
            "source_signal_id": "SIGNAL-SELL-1",
        }
        context.update(overrides)
        return context

    def _build(self, *, context=None) -> dict:
        return sell_bridge.build_indicator_follow_sell_intent(
            sell_signal_result={"signal": "SELL", "reason": "indicator"},
            context=context if context is not None else self._context(),
        )

    def test_limit_sell_uses_confirmed_holding_quantity(self) -> None:
        result = self._build(context=self._context(holding_qty=11))
        intent = result["execution_intent"]

        self.assertEqual("READY", result["status"])
        self.assertEqual("SELL", intent["side"])
        self.assertEqual(11, intent["quantity"])
        self.assertEqual("LIMIT", intent["hoga"])
        self.assertEqual("ORDER_PRICE", intent["price_basis"])
        self.assertEqual(81_000, intent["price"])
        self.assertEqual("setting_a", intent["sell_method_set"])

    def test_market_sell_builds_one_market_intent(self) -> None:
        result = self._build(
            context=self._context(rules=self._rules(price_method="시장가"), reference_price=None)
        )
        intent = result["execution_intent"]

        self.assertEqual("READY", result["status"])
        self.assertEqual("MARKET", intent["hoga"])
        self.assertEqual("MARKET", intent["price_basis"])
        self.assertIsNone(intent["price"])
        candidate = build_order_candidate_from_execution_intent(intent)
        self.assertEqual("CANDIDATE_READY", candidate["candidate_status"])
        self.assertIsNone(candidate["price"])

    def test_cycle_quantity_identity_and_selected_set_fail_closed(self) -> None:
        unresolved = self._build(context=self._context(status="unresolved"))
        no_holding = self._build(context=self._context(holding_qty=0))
        no_instance = self._build(context=self._context(routine_instance_id=""))
        none_selected = self._build(context=self._context(rules=self._rules(selected_sets=[])))
        multiple = self._build(
            context=self._context(rules=self._rules(selected_sets=["setting_a", "setting_b"]))
        )

        self.assertEqual("LEDGER_UNRESOLVED", unresolved["reason"])
        self.assertEqual("SELL_HOLDING_QUANTITY_INVALID", no_holding["reason"])
        self.assertEqual("SELL_ROUTINE_INSTANCE_ID_MISSING", no_instance["reason"])
        self.assertEqual("SELL_SELECTED_SET_COUNT_INVALID", none_selected["reason"])
        self.assertEqual("SELL_SELECTED_SET_COUNT_INVALID", multiple["reason"])
        for result in (unresolved, no_holding, no_instance, none_selected, multiple):
            self.assertIsNone(result["execution_intent"])

    def test_multi_hoga_is_not_downgraded(self) -> None:
        result = self._build(context=self._context(rules=self._rules(hoga_mode="다중호가")))

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("SELL_MULTI_HOGA_NOT_IMPLEMENTED", result["reason"])
        self.assertIsNone(result["execution_intent"])

    def test_generic_candidate_and_order_approval_accept_sell(self) -> None:
        intent = self._build()["execution_intent"]
        candidate = build_order_candidate_from_execution_intent(intent)
        candidate.update({"status": "PENDING", "side": "SELL"})
        approval = order_approval_engine.evaluate_order_approval(candidate)

        self.assertEqual("CANDIDATE_READY", candidate["candidate_status"])
        self.assertEqual("APPROVED", approval["approval_status"])

    def test_sell_signal_reuses_admission_approval_and_operation_policy_chain(self) -> None:
        intent = self._build()["execution_intent"]
        signal = {
            "id": "SIGNAL-SELL-1",
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-SELL-1",
            "code": "005930",
            "name": "삼성전자",
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": intent,
        }
        captured: list[dict] = []

        def append_candidates(candidates):
            captured.extend(candidates)
            return {
                "ok": True,
                "orders_created": len(candidates),
                "duplicates": 0,
                "order_queue_written": True,
                "created_orders": candidates,
            }

        policy = mock.Mock(return_value={
            "ok": True,
            "reason": "",
            "policy_checked": 1,
            "policy_executable": 1,
            "policy_blocked": 0,
            "policy_errors": 0,
            "policy_results": [{"status": "EXECUTABLE"}],
        })
        with mock.patch.object(
            routine_signal_consumer,
            "evaluate_routine_gate",
            return_value={"allowed": True, "reason": "ROUTINE_EXECUTION_ENABLED"},
        ), mock.patch.object(
            routine_signal_consumer,
            "read_order_queue",
            return_value={"orders": []},
        ), mock.patch.object(
            routine_signal_consumer,
            "append_order_candidates",
            side_effect=append_candidates,
        ), mock.patch.object(
            routine_signal_consumer,
            "_apply_operation_policy_to_created_orders",
            policy,
        ):
            result = routine_signal_consumer._build_order_queue_candidates_for_signals(
                [signal],
                apply_approval=True,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(1, result["approved"])
        self.assertEqual(1, result["policy_checked"])
        self.assertEqual(1, result["policy_executable"])
        self.assertEqual("SELL_SIGNAL_CANDIDATE", captured[0]["order_type"])
        self.assertEqual("APPROVED", captured[0]["approval_status"])
        self.assertFalse(captured[0]["execution_enabled"])
        policy.assert_called_once()

    def test_routine_evaluation_queue_assigns_source_signal_identity(self) -> None:
        routine = _load_module("routine.py", "indicator_follow_sell_execution_routine_test")
        routine.evaluate_indicator_follow_routine = lambda candles, config, context: {"raw": True}
        routine.signal_to_dict = lambda signal: {"signal": "SELL", "reason": "indicator"}
        result = routine.evaluate({
            "candles": [],
            "rules": self._rules(),
            "cycle": self._context()["cycle"],
            "reference_price": 75_000,
            "routine_instance_id": "INSTANCE-SELL-1",
        })

        self.assertEqual("READY", result["sell_execution_policy_status"])
        self.assertIsNone(result["execution_intent"]["source_signal_id"])
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "routine_signals.json"
            with mock.patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                queued = routine_signal_queue.enqueue_routine_signal(
                    result,
                    routine="지표추종매매",
                    code="005930",
                    name="삼성전자",
                    tick_key="TICK-SELL-1",
                )
            record = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]

        self.assertEqual("queued", queued["status"])
        self.assertEqual(record["id"], record["execution_intent"]["source_signal_id"])
        candidate = build_order_candidate_from_execution_intent(record["execution_intent"])
        self.assertEqual("CANDIDATE_READY", candidate["candidate_status"])

    def test_final_safety_false_blocks_real_order_authority(self) -> None:
        routine = _load_module("routine.py", "indicator_follow_sell_final_safety_test")
        result = routine.evaluate_final_real_order_safety(
            subject={"side": "SELL"},
            rules=self._rules(),
            routine_identity={"instance_id": "INSTANCE-SELL-1"},
            rules_identity="RULES-HASH",
        )

        self.assertFalse(result["allowed"])
        self.assertEqual("ROUTINE_REAL_ORDER_NOT_ALLOWED", result["reason"])

        boundary = execution_boundary.AutoTradeOrderExecutionBoundary.__new__(
            execution_boundary.AutoTradeOrderExecutionBoundary
        )
        order = {"side": "SELL", "execution_intent": self._build()["execution_intent"]}
        with mock.patch.object(
            execution_boundary,
            "evaluate_routine_gate",
            return_value={
                "allowed": False,
                "reason": "ROUTINE_REAL_ORDER_NOT_ALLOWED",
                "reasons": ["ROUTINE_REAL_ORDER_NOT_ALLOWED"],
            },
        ):
            reasons = boundary.routine_real_order_block_reasons(order)
        self.assertEqual(["ROUTINE_REAL_ORDER_NOT_ALLOWED"], reasons)

    def test_committed_multiple_selected_sets_fail_validator(self) -> None:
        pre_rules = json.loads((ROUTINE_DIR / "rules.json").read_text(encoding="utf-8"))
        post_rules = json.loads(json.dumps(pre_rules, ensure_ascii=False))
        post_rules.setdefault("sell", {}).setdefault("method", {})["selected_sets"] = [
            "setting_a",
            "setting_b",
        ]
        result = rule_validator.validate_committed_rules(
            pre_rules,
            post_rules,
            [],
            {
                "rules_json_write": False,
                "engine_connected": False,
                "buy_groups_replace": False,
                "macd_sell_replace": False,
            },
        )
        target = next(
            check
            for check in result["checks"]
            if check["name"] == "sell_method_exactly_one_selected_set"
        )

        self.assertFalse(result["ok"])
        self.assertFalse(target["ok"])
        self.assertIn(
            "sell.method.selected_sets",
            [item["path"] for item in result["unexpected_changes"]],
        )

    def test_main_and_common_do_not_gain_indicator_follow_sell_branch(self) -> None:
        common_files = (
            "order_candidate_engine.py",
            "routine_signal_consumer.py",
            "order_approval_engine.py",
            "operation_policy_gate.py",
            "auto_trade_order_execution_boundary.py",
        )
        for filename in common_files:
            source = (PROJECT_ROOT / filename).read_text(encoding="utf-8").lower()
            self.assertNotIn("indicator_follow_sell", source, filename)

    def test_current_group_pack_round_trip_includes_sell_builder(self) -> None:
        spec = json.loads((ROUTINE_DIR / "group_pack_spec.json").read_text(encoding="utf-8"))
        self.assertIn(
            "routines/지표추종매매/routine_sell_execution.py",
            spec["files"],
        )
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as target_temp:
            source_root = Path(source_temp)
            target_root = Path(target_temp)
            for relative in spec["files"]:
                source = PROJECT_ROOT.joinpath(*Path(relative).parts)
                destination = source_root.joinpath(*Path(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            spec_destination = source_root / "routines" / "지표추종매매" / "group_pack_spec.json"
            shutil.copy2(ROUTINE_DIR / "group_pack_spec.json", spec_destination)
            group = LogicalGroupRepository(source_root).create_group(
                "indicator_follow",
                "지표추종매매",
                register=True,
            ).group
            pack_path = source_root / "indicator-follow.group.zip"
            packed = pack_group(group.group_id, pack_path, project_root=source_root)
            registered = register_group_pack(pack_path, project_root=target_root)
            definitions = {
                definition.definition_id: definition
                for definition in load_routine_definitions(project_root=target_root)
            }
            validation = validate_routine_definition_capabilities(
                definitions["indicator_follow"]
            )

        self.assertTrue(packed.success, packed)
        self.assertTrue(registered.success, registered)
        self.assertIn("indicator_follow", definitions)
        self.assertTrue(validation["ok"], validation)


class IndicatorFollowSellPresetUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp_dir.name) / "rules.json"
        self.rules_path.write_text(
            (ROUTINE_DIR / "rules.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.dialog = IndicatorFollowRoutineSettingsDialog(rules_path=self.rules_path)

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
        self.temp_dir.cleanup()

    def test_checkboxes_are_exclusive_and_last_selection_cannot_clear(self) -> None:
        a = self.dialog.sell_method_select_a_check
        b = self.dialog.sell_method_select_b_check
        c = self.dialog.sell_method_select_c_check

        self.assertEqual([True, False, False], [a.isChecked(), b.isChecked(), c.isChecked()])
        b.setChecked(True)
        self.assertEqual([False, True, False], [a.isChecked(), b.isChecked(), c.isChecked()])
        c.setChecked(True)
        self.assertEqual([False, False, True], [a.isChecked(), b.isChecked(), c.isChecked()])
        c.setChecked(False)
        self.assertEqual([False, False, True], [a.isChecked(), b.isChecked(), c.isChecked()])

    def test_loaded_multiple_selection_is_not_silently_repaired(self) -> None:
        state = self.dialog.collect_indicator_follow_ui_state()
        state["sell_ui"]["selected_sets"] = {"a": True, "b": True, "c": False}

        applied = self.dialog.apply_indicator_follow_ui_state(state)
        collected = self.dialog.collect_indicator_follow_ui_state()

        self.assertIn(
            {
                "name": "sell_ui.selected_sets",
                "reason": "exactly_one_sell_method_set_required",
            },
            applied["skipped"],
        )
        self.assertEqual(
            {"a": True, "b": True, "c": False},
            collected["sell_ui"]["selected_sets"],
        )


if __name__ == "__main__":
    unittest.main()
