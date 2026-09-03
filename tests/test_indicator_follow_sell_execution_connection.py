# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import auto_trade_order_execution_boundary as execution_boundary
import gui_auto_trade_timer
import order_queue
import order_approval_engine
from execution_provenance_contract import materialize_execution_intent_children
from execution_preview_service import preview_execution_for_order
from krx_tick_price import move_krx_price_by_ticks
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
        multi_up: int = 2,
        multi_down: int = 2,
        perform2_mode: str = "선택없음",
        time_value: int = 30,
        time_unit: str = "초",
        time_range: str = "이내",
        time_count: int = 3,
        time_order: str = "주문가",
        ratio_left: str = "주문가",
        ratio_right: str = "현재가",
        ratio_direction: str = "상향",
        ratio_value: float = 0.15,
        ratio_compare: str = "이상",
        ratio_count: int = 3,
        perform3_mode: str = "가격비교",
        pending_scope: str = "매회",
        pending_value: object = 20,
        pending_unit: str = "초",
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
                        "perform1_multi_up_line": str(multi_up),
                        "perform1_multi_down_line": str(multi_down),
                        "perform2_title_combo": perform2_mode,
                        "perform2_time_value": str(time_value),
                        "perform2_time_unit": time_unit,
                        "perform2_time_range": time_range,
                        "perform2_time_count": str(time_count),
                        "perform2_time_order": time_order,
                        "perform2_ratio_left": ratio_left,
                        "perform2_ratio_right": ratio_right,
                        "perform2_ratio_direction": ratio_direction,
                        "perform2_ratio_value": str(ratio_value),
                        "perform2_ratio_compare": ratio_compare,
                        "perform2_ratio_count": str(ratio_count),
                        "perform3_title_combo": perform3_mode,
                        "perform3_pending_scope": pending_scope,
                        "perform3_pending_value": str(pending_value),
                        "perform3_pending_unit": pending_unit,
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

    def test_unfilled_timeout_policy_is_frozen_into_every_sell_child(self) -> None:
        each = self._build(
            context=self._context(
                rules=self._rules(
                    hoga_mode="다중호가",
                    perform3_mode="미체결",
                    pending_scope="매회",
                    pending_value=20,
                    pending_unit="초",
                )
            )
        )
        batch = self._build(
            context=self._context(
                rules=self._rules(
                    perform2_mode="다중시간",
                    perform3_mode="미체결",
                    pending_scope="일괄",
                    pending_value=2,
                    pending_unit="분",
                )
            )
        )
        bars = self._build(
            context=self._context(
                rules=self._rules(
                    perform3_mode="미체결",
                    pending_value=3,
                    pending_unit="봉",
                ),
                candles=[{"timeframe_minutes": 5}],
            )
        )

        self.assertEqual(
            {"EACH"},
            {item["unfilled_timeout_policy"]["scope"] for item in each["execution_intents"]},
        )
        self.assertEqual(
            {20_000},
            {item["unfilled_timeout_policy"]["timeout_ms"] for item in each["execution_intents"]},
        )
        self.assertEqual(
            {"BATCH"},
            {item["unfilled_timeout_policy"]["scope"] for item in batch["execution_intents"]},
        )
        self.assertEqual(120_000, batch["execution_intent"]["unfilled_timeout_policy"]["timeout_ms"])
        self.assertEqual(900_000, bars["execution_intent"]["unfilled_timeout_policy"]["timeout_ms"])
        self.assertEqual(
            "BROKER_ACCEPTED_AT",
            bars["execution_intent"]["unfilled_timeout_policy"]["anchor"],
        )

    def test_invalid_unfilled_timeout_policy_fails_closed(self) -> None:
        invalid_scope = self._build(
            context=self._context(
                rules=self._rules(
                    perform3_mode="미체결",
                    pending_scope="broken",
                )
            )
        )
        unresolved_bar = self._build(
            context=self._context(
                rules=self._rules(
                    perform3_mode="미체결",
                    pending_unit="봉",
                )
            )
        )

        self.assertEqual("SELL_UNFILLED_TIMEOUT_SCOPE_INVALID", invalid_scope["reason"])
        self.assertEqual("SELL_UNFILLED_TIMEOUT_UNIT_UNRESOLVED", unresolved_bar["reason"])

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

    def test_multi_hoga_builds_balanced_nearest_first_children(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=13,
                rules=self._rules(hoga_mode="다중호가", multi_up=2, multi_down=2),
            )
        )
        intents = result["execution_intents"]

        self.assertEqual("READY", result["status"])
        self.assertEqual([3, 3, 3, 2, 2], [item["quantity"] for item in intents])
        self.assertEqual(
            [0, 1, -1, 2, -2],
            [item["child_plan"]["hoga_offset_ticks"] for item in intents],
        )
        self.assertEqual([1, 2, 3, 4, 5], [item["child_sequence_index"] for item in intents])
        self.assertEqual({5}, {item["child_sequence_total"] for item in intents})
        self.assertEqual({"HOGA_LEVEL"}, {item["child_kind"] for item in intents})
        self.assertEqual({0}, {item["plan_generation"] for item in intents})

    def test_multi_time_within_builds_balanced_time_slice_children(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=10,
                rules=self._rules(perform2_mode="다중시간"),
            )
        )
        intents = result["execution_intents"]

        self.assertEqual("READY", result["status"], result)
        self.assertEqual([4, 3, 3], [item["quantity"] for item in intents])
        self.assertEqual({"TIME_SLICE"}, {item["child_kind"] for item in intents})
        self.assertEqual({"MULTI_TIME"}, {item["execution_mode"] for item in intents})
        self.assertEqual(
            [0, 15_000, 30_000],
            [item["child_plan"]["scheduled_offset_ms"] for item in intents],
        )

    def test_multi_time_interval_and_bar_units_use_existing_context(self) -> None:
        interval = self._build(
            context=self._context(
                holding_qty=3,
                rules=self._rules(
                    perform2_mode="다중시간",
                    time_value=2,
                    time_unit="분",
                    time_range="간격",
                ),
            )
        )
        bars = self._build(
            context=self._context(
                holding_qty=3,
                rules=self._rules(
                    perform2_mode="다중시간",
                    time_value=2,
                    time_unit="봉",
                ),
                candles=[{"timeframe_minutes": 5}],
            )
        )

        self.assertEqual(
            [0, 120_000, 240_000],
            [item["child_plan"]["scheduled_offset_ms"] for item in interval["execution_intents"]],
        )
        self.assertEqual(
            [0, 300_000, 600_000],
            [item["child_plan"]["scheduled_offset_ms"] for item in bars["execution_intents"]],
        )

    def test_multi_time_current_price_and_hoga_combination_fail_closed(self) -> None:
        current = self._build(
            context=self._context(
                rules=self._rules(
                    perform2_mode="다중시간",
                    time_order="현재가",
                ),
                actionable_current_price=82_000,
            )
        )
        combined = self._build(
            context=self._context(
                rules=self._rules(
                    hoga_mode="다중호가",
                    perform2_mode="다중시간",
                )
            )
        )
        self.assertEqual("CURRENT_PRICE", current["execution_intent"]["price_basis"])
        self.assertEqual("LIMIT", current["execution_intent"]["hoga"])
        self.assertEqual("SELL_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED", combined["reason"])

    def test_multi_ratio_builds_balanced_triggered_children(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=10,
                rules=self._rules(
                    perform2_mode="다중비율",
                    ratio_left="주문가",
                    ratio_right="현재가",
                    ratio_direction="상향",
                    ratio_value=0.15,
                    ratio_compare="이상",
                    ratio_count=3,
                ),
            )
        )
        intents = result["execution_intents"]

        self.assertEqual("READY", result["status"], result)
        self.assertEqual([4, 3, 3], [item["quantity"] for item in intents])
        self.assertEqual({"MULTI_RATIO"}, {item["execution_mode"] for item in intents})
        self.assertEqual({"RATIO_SLICE"}, {item["child_kind"] for item in intents})
        self.assertEqual([1, 2, 3], [item["child_sequence_index"] for item in intents])
        self.assertEqual({3}, {item["child_sequence_total"] for item in intents})
        self.assertEqual({"ORDER_PRICE"}, {item["ratio_left"] for item in intents})
        self.assertEqual({"CURRENT_PRICE"}, {item["ratio_right"] for item in intents})
        self.assertEqual({0.15}, {item["ratio_value"] for item in intents})

    def test_price_reset_policy_is_frozen_without_changing_execution_price_contract(self) -> None:
        rules = self._rules()
        setting = rules["sell"]["method"]["setting_a"]
        setting.update(
            {
                "perform3_title_combo": "가격비교",
                "perform3_price_left": "주문가",
                "perform3_price_right": "현재가",
                "perform3_price_direction": "상향",
                "perform3_price_value": "0.15",
                "perform3_price_compare": "이상",
                "perform3_price_action": "매도리셋",
            }
        )
        result = self._build(context=self._context(holding_qty=10, rules=rules))

        self.assertEqual("READY", result["status"], result)
        policy = result["execution_intent"]["sell_price_reset_policy"]
        self.assertEqual("SELL_PRICE_CHANGE_RESET", policy["policy"])
        self.assertEqual("ORDER_PRICE", policy["left_source"])
        self.assertEqual("CURRENT_PRICE", policy["right_source"])
        self.assertEqual("UP", policy["direction"])
        self.assertEqual(">=", policy["compare"])
        self.assertEqual(0.15, policy["threshold_percent"])

    def test_repeat_perform_policy_is_frozen_independently_from_initial_perform(self) -> None:
        rules = self._rules(price_method="시장가")
        setting = rules["sell"]["method"]["setting_a"]
        setting.update(
            {
                "repeat_perform1_title_combo": "단일호가",
                "repeat_perform1_single_combo": "주문가",
                "repeat_perform2_title_combo": "다중시간",
                "repeat_perform2_time_count": "3",
                "repeat_perform2_time_value": "20",
                "repeat_perform2_time_unit": "초",
                "repeat_perform2_time_range": "간격",
                "repeat_perform2_time_order": "주문가",
                "repeat_perform3_title_combo": "미체결",
                "repeat_perform3_pending_scope": "매회",
                "repeat_perform3_pending_value": "15",
                "repeat_perform3_pending_unit": "초",
                "exit_price_check": True,
                "exit_price_left": "현재가",
                "exit_price_right": "평단가",
                "exit_price_direction": "상향",
                "exit_price_value": "1",
                "exit_price_compare": "이상",
            }
        )
        result = self._build(context=self._context(rules=rules))

        self.assertEqual("READY", result["status"], result)
        initial = result["execution_intent"]
        repeat = initial["sell_repeat_policy"]
        self.assertEqual("MARKET", initial["hoga"])
        self.assertEqual("MULTI_TIME", repeat["execution_template"]["execution_mode"])
        self.assertEqual("ORDER_PRICE", repeat["execution_template"]["price_basis"])
        self.assertEqual(15_000, repeat["unfilled_timeout_policy"]["timeout_ms"])
        self.assertTrue(repeat["exit_policy_snapshot"]["exit_price_check"])
        self.assertEqual("OR", repeat["exit_policy"]["logic"])
        self.assertEqual("PRICE", repeat["exit_policy"]["conditions"][0]["condition_type"])
        self.assertEqual(
            "LEFT_VALUE_RELATIVE_TO_RIGHT_BASE",
            repeat["exit_policy"]["conditions"][0]["orientation"],
        )
        self.assertTrue(repeat["plan_snapshot_hash"])

    def test_repeat_exit_count_and_time_are_normalized_without_new_runtime_state(self) -> None:
        rules = self._rules()
        setting = rules["sell"]["method"]["setting_a"]
        setting.update(
            {
                "repeat_perform1_title_combo": "단일호가",
                "repeat_perform1_single_combo": "주문가",
                "repeat_perform2_title_combo": "선택없음",
                "repeat_perform3_title_combo": "가격비교",
                "repeat_perform3_price_left": "주문가",
                "repeat_perform3_price_right": "현재가",
                "repeat_perform3_price_direction": "상향",
                "repeat_perform3_price_value": "1",
                "repeat_perform3_price_compare": "이상",
                "repeat_perform3_price_action": "매도리셋",
                "exit_count_check": True,
                "exit_count_line": "3",
                "exit_time_check": True,
                "exit_time_line": "2",
                "exit_time_unit": "분",
                "exit_price_check": False,
            }
        )
        result = self._build(context=self._context(rules=rules))

        self.assertEqual("READY", result["status"], result)
        policy = result["execution_intent"]["sell_repeat_policy"]["exit_policy"]
        self.assertEqual("OR", policy["logic"])
        self.assertEqual(["COUNT", "TIME"], [item["condition_type"] for item in policy["conditions"]])
        self.assertEqual(3, policy["conditions"][0]["target_repeat_generations"])
        self.assertFalse(policy["conditions"][0]["initial_generation_included"])
        self.assertEqual(120_000, policy["conditions"][1]["duration_ms"])
        self.assertEqual("FIRST_REPEAT_GENERATION_AT", policy["conditions"][1]["anchor"])

    def test_repeat_exit_bar_time_uses_frozen_candle_timeframe(self) -> None:
        rules = self._rules()
        setting = rules["sell"]["method"]["setting_a"]
        setting.update(
            {
                "repeat_perform1_title_combo": "단일호가",
                "repeat_perform1_single_combo": "주문가",
                "repeat_perform2_title_combo": "선택없음",
                "repeat_perform3_title_combo": "가격비교",
                "repeat_perform3_price_left": "주문가",
                "repeat_perform3_price_right": "현재가",
                "repeat_perform3_price_direction": "상향",
                "repeat_perform3_price_value": "1",
                "repeat_perform3_price_compare": "이상",
                "repeat_perform3_price_action": "매도리셋",
                "exit_time_check": True,
                "exit_time_line": "2",
                "exit_time_unit": "봉",
            }
        )
        result = self._build(
            context=self._context(
                rules=rules,
                candles=[{"timeframe_minutes": 5}],
            )
        )

        self.assertEqual("READY", result["status"], result)
        condition = result["execution_intent"]["sell_repeat_policy"]["exit_policy"]["conditions"][0]
        self.assertEqual("TIME", condition["condition_type"])
        self.assertEqual(600_000, condition["duration_ms"])

    def test_multi_ratio_market_order_and_unsupported_hoga_combination(self) -> None:
        market = self._build(
            context=self._context(
                rules=self._rules(
                    price_method="시장가",
                    perform2_mode="다중비율",
                )
            )
        )
        combined = self._build(
            context=self._context(
                rules=self._rules(
                    hoga_mode="다중호가",
                    perform2_mode="다중비율",
                )
            )
        )

        self.assertEqual("MARKET", market["execution_intent"]["hoga"])
        self.assertEqual("MARKET", market["execution_intent"]["price_basis"])
        self.assertIsNone(market["execution_intent"]["price"])
        self.assertEqual("SELL_MULTI_RATIO_HOGA_COMBINATION_NOT_IMPLEMENTED", combined["reason"])

    def test_multi_hoga_small_remaining_quantity_collapses_to_base_limit(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=3,
                rules=self._rules(hoga_mode="다중호가", multi_up=2, multi_down=2),
            )
        )
        intents = result["execution_intents"]

        self.assertEqual(1, len(intents))
        self.assertEqual(3, intents[0]["quantity"])
        self.assertEqual(0, intents[0]["child_plan"]["hoga_offset_ticks"])
        self.assertEqual(81_000, intents[0]["price"])
        self.assertEqual("LIMIT", intents[0]["hoga"])

    def test_multi_hoga_uses_sequential_tick_boundaries(self) -> None:
        self.assertEqual(2_000, move_krx_price_by_ticks(1_999, 1))
        self.assertEqual(1_999, move_krx_price_by_ticks(2_000, -1))
        self.assertEqual(20_000, move_krx_price_by_ticks(19_990, 1))
        self.assertEqual(19_990, move_krx_price_by_ticks(20_000, -1))
        self.assertEqual(50_000, move_krx_price_by_ticks(49_950, 1, instrument_type="SPAC"))
        self.assertEqual(49_950, move_krx_price_by_ticks(50_000, -1, instrument_type="REIT"))
        self.assertEqual(2_000, move_krx_price_by_ticks(1_999, 1, instrument_type="ETF"))
        self.assertEqual(1_999, move_krx_price_by_ticks(2_000, -1, instrument_type="ETF"))

    def test_signal_queue_materializes_one_process_and_unique_child_identities(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=13,
                source_signal_id="",
                rules=self._rules(hoga_mode="다중호가", multi_up=2, multi_down=2),
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "routine_signals.json"
            with mock.patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                queued = routine_signal_queue.enqueue_routine_signal(
                    {"signal": "SELL", "reason": "indicator", **result},
                    routine="지표추종매매",
                    code="005930",
                    name="삼성전자",
                    tick_key="TICK-MULTI-1",
                )
            record = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]

        intents = record["execution_intents"]
        self.assertEqual("queued", queued["status"])
        self.assertEqual({record["id"]}, {item["source_signal_id"] for item in intents})
        self.assertEqual(1, len({item["execution_process_id"] for item in intents}))
        self.assertEqual(5, len({item["execution_id"] for item in intents}))
        self.assertEqual({0}, {item["plan_generation"] for item in intents})
        self.assertEqual(record["id"], record["execution_intent"]["source_signal_id"])

    def test_queue_allows_distinct_process_children_and_dedupes_replay(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=13,
                rules=self._rules(hoga_mode="다중호가", multi_up=2, multi_down=2),
            )
        )
        intents = materialize_execution_intent_children(
            result["execution_intents"],
            source_signal_id="SIGNAL-SELL-1",
        )
        signal = {
            "id": "SIGNAL-SELL-1",
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-SELL-1",
            "code": "005930",
            "name": "삼성전자",
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": intents[0],
            "execution_intents": intents,
        }
        candidates = order_queue.signal_to_order_candidates(signal, 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "order_queue.json"
            with mock.patch.object(order_queue, "ORDER_QUEUE_PATH", queue_path):
                first = order_queue.append_order_candidates(candidates, backup=False)
                replay = order_queue.append_order_candidates(candidates, backup=False)

        self.assertTrue(first["ok"], first)
        self.assertEqual(5, first["orders_created"])
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(0, replay["orders_created"])
        self.assertEqual(5, replay["duplicates"])

    def test_auto_executor_continues_after_one_child_failure(self) -> None:
        boundary = execution_boundary.AutoTradeOrderExecutionBoundary.__new__(
            execution_boundary.AutoTradeOrderExecutionBoundary
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "order_queue.json"
            queue_path.write_text(
                json.dumps(
                    {
                        "orders": [
                            {"id": f"CHILD-{index}", "status": "EXECUTABLE"}
                            for index in range(1, 6)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            boundary._context = mock.Mock()
            boundary._context.order_queue_path.return_value = queue_path
            outcomes = [True, True, False, True, True]
            boundary.process_executable_order_for_auto_trade = mock.Mock(
                side_effect=[
                    {"processed": outcome, "order_id": f"CHILD-{index}"}
                    for index, outcome in enumerate(outcomes, start=1)
                ]
            )
            execution = boundary.auto_process_executable_orders_for_real_trade(limit=5)

        self.assertEqual(4, execution["processed"])
        self.assertEqual(1, execution["blocked"])
        self.assertEqual(5, boundary.process_executable_order_for_auto_trade.call_count)

    def test_timer_targets_every_new_multi_child_beyond_legacy_limit(self) -> None:
        child_ids = [f"CHILD-{index}" for index in range(1, 8)]
        window = SimpleNamespace(
            statusBarMessage=mock.Mock(),
            auto_process_executable_orders_for_real_trade=mock.Mock(
                return_value={"processed": 7, "blocked": 0}
            ),
        )
        snapshot = SimpleNamespace(
            entries=(
                SimpleNamespace(
                    execution_ready=True,
                    stock_code="005930",
                    stock_dir=None,
                ),
            )
        )
        summary = {
            "signals_checked": 1,
            "blocked": 0,
            "allowed": 1,
            "errors": 0,
            "orders_created": 7,
            "approval_checked": 7,
            "approved": 7,
            "executable_order_ids": child_ids,
        }
        with mock.patch.object(
            gui_auto_trade_timer,
            "consume_pending_routine_signals_dry_run",
            return_value={"summary": summary},
        ), mock.patch.object(
            gui_auto_trade_timer,
            "auto_trade_signal_probe_only_active",
            return_value=False,
        ), mock.patch.object(
            gui_auto_trade_timer,
            "auto_trade_real_execution_active",
            return_value=True,
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        self.assertEqual(7, result["orders_processed"])
        window.auto_process_executable_orders_for_real_trade.assert_called_once_with(
            limit=7,
            order_ids=child_ids,
        )

    def test_multi_hoga_children_reach_generic_execution_preview_with_one_process(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=13,
                rules=self._rules(hoga_mode="다중호가", multi_up=2, multi_down=2),
            )
        )
        intents = materialize_execution_intent_children(
            result["execution_intents"],
            source_signal_id="SIGNAL-SELL-1",
        )
        for intent in intents:
            intent["provenance_approved_at"] = "2026-09-02T10:00:00+09:00"
        signal = {
            "id": "SIGNAL-SELL-1",
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-SELL-1",
            "code": "005930",
            "name": "삼성전자",
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": intents[0],
            "execution_intents": intents,
        }
        orders = order_queue.signal_to_order_candidates(signal, 1)
        previews = []
        for order in orders:
            order["status"] = "REAL_READY"
            order["execution_enabled"] = True
            previews.append(
                preview_execution_for_order(
                    order,
                    {
                        "operator_confirmed": True,
                        "real_trade_enabled": True,
                        "account_no": "12345678",
                    },
                )
            )

        self.assertTrue(all(item["ok"] for item in previews), previews)
        candidates = [item["candidate_result"] for item in previews]
        self.assertEqual(1, len({item["execution_process_id"] for item in candidates}))
        self.assertEqual(5, len({item["child_contract"]["execution_id"] for item in candidates}))
        self.assertEqual(
            [1, 2, 3, 4, 5],
            [item["child_contract"]["child_sequence_index"] for item in candidates],
        )
        self.assertEqual(1, len({item["option_snapshot_hash"] for item in candidates}))
        self.assertEqual(
            1,
            len(
                {
                    json.dumps(item["process_record"], ensure_ascii=False, sort_keys=True)
                    for item in candidates
                }
            ),
        )

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

    def test_one_multi_hoga_signal_creates_n_approved_policy_candidates(self) -> None:
        result = self._build(
            context=self._context(
                holding_qty=13,
                rules=self._rules(hoga_mode="다중호가", multi_up=2, multi_down=2),
            )
        )
        intents = materialize_execution_intent_children(
            result["execution_intents"],
            source_signal_id="SIGNAL-SELL-1",
        )
        signal = {
            "id": "SIGNAL-SELL-1",
            "routine": "지표추종매매",
            "routine_instance_id": "INSTANCE-SELL-1",
            "code": "005930",
            "name": "삼성전자",
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": intents[0],
            "execution_intents": intents,
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
            "policy_checked": 5,
            "policy_executable": 5,
            "policy_blocked": 0,
            "policy_errors": 0,
            "policy_results": [{"status": "EXECUTABLE"}] * 5,
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
            consumed = routine_signal_consumer._build_order_queue_candidates_for_signals(
                [signal],
                apply_approval=True,
            )

        self.assertTrue(consumed["ok"], consumed)
        self.assertEqual(5, consumed["orders_created"])
        self.assertEqual(5, consumed["approved"])
        self.assertEqual(5, consumed["policy_checked"])
        self.assertEqual([3, 3, 3, 2, 2], [item["quantity"] for item in captured])
        self.assertEqual(1, len({item["execution_process_id"] for item in captured}))
        self.assertEqual(5, len({item["execution_id"] for item in captured}))
        self.assertEqual(1, len({item["source_signal_id"] for item in captured}))

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
        self.assertIn("krx_tick_price.py", spec["files"])
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
