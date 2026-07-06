from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from pathlib import Path
import unittest

from engines.signal_result import RoutineSignal
import routine_signal_preview_service
import signal_decision_policy_service
import signal_decision_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTUAL_RULES_PATH = next((PROJECT_ROOT / "routines").glob("*/rules.json"))
RUNTIME_QUEUE_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decision(signal: str | None) -> dict:
    routine_signal = RoutineSignal(
        signal,
        "policy source reason",
        ["matched_group"] if signal else [],
        ["PASS detail"] if signal else ["FAIL detail"],
        2,
        0,
    )
    preview = routine_signal_preview_service.build_routine_signal_preview(
        routine_signal,
        {
            "rule_source": "unit_rules",
            "matched_rule_paths": ["buy.groups"] if signal == "BUY" else ["sell.signals"] if signal == "SELL" else [],
            "condition_summary": ["summary"],
            "preview_time": "2026-07-05T12:00:00+09:00",
        },
    )
    return signal_decision_service.build_signal_decision_preview(preview)


def _policy(signal: str | None) -> dict:
    return signal_decision_policy_service.build_signal_policy_preview(_decision(signal))


def _snapshot() -> dict:
    return {"symbol": "005930", "timeframe": "1m"}


def _at(hhmm: str) -> datetime:
    hour, minute = [int(part) for part in hhmm.split(":")]
    return datetime(2026, 7, 5, hour, minute, 0)


def _time_policy(signal: str | None = "BUY") -> dict:
    return signal_decision_policy_service.apply_time_policy(
        _policy(signal),
        _snapshot(),
        now=_at("10:00"),
    )


def _operation_state(status: str = "RUNNING", *, enabled: bool = True, emergency_stop: bool = False) -> dict:
    return {
        "enabled": enabled,
        "emergency_stop": emergency_stop,
        "operation_status": status,
    }


def _routine_state(status: str = "ACTIVE", *, enabled: bool = True) -> dict:
    return {"enabled": enabled, "status": status}


def _stock_state(status: str = "ACTIVE", *, enabled: bool = True) -> dict:
    return {"enabled": enabled, "status": status}


def _emergency_state(
    *,
    emergency_stop: bool = False,
    force_stop: bool = False,
    safety_lock: bool = False,
) -> dict:
    return {
        "emergency_stop": emergency_stop,
        "force_stop": force_stop,
        "safety_lock": safety_lock,
    }


def _budget_state(*, enabled: bool = True, available_budget: int = 1000, required_budget: int = 500) -> dict:
    return {
        "enabled": enabled,
        "available_budget": available_budget,
        "required_budget": required_budget,
    }


def _signal_history(*, last_signal: str = "SELL", signal: str = "BUY") -> dict:
    return {
        "last_signal": last_signal,
        "symbol": "005930",
        "routine_id": "routine-a",
        "signal": signal,
    }


def _cooldown_state(
    *,
    enabled: bool = True,
    last_signal_time: object = "2026-07-05T09:58:00",
    cooldown_seconds: int = 60,
) -> dict:
    return {
        "enabled": enabled,
        "last_signal_time": last_signal_time,
        "cooldown_seconds": cooldown_seconds,
    }


def _delay_state(*, enabled: bool = True, remaining_delay_bar: int = 0) -> dict:
    return {
        "enabled": enabled,
        "remaining_delay_bar": remaining_delay_bar,
    }


def _orchestrator_kwargs(**overrides) -> dict:
    values = {
        "market_snapshot": _snapshot(),
        "operation_state": _operation_state("RUNNING"),
        "routine_state": _routine_state("ACTIVE"),
        "stock_state": _stock_state("ACTIVE"),
        "emergency_state": _emergency_state(),
        "budget_state": _budget_state(),
        "signal_history": _signal_history(last_signal="SELL", signal="BUY"),
        "cooldown_state": _cooldown_state(last_signal_time="2026-07-05T09:58:00"),
        "delay_state": _delay_state(remaining_delay_bar=0),
        "now": _at("10:00"),
    }
    values.update(overrides)
    return values


class SignalDecisionPolicyServiceTest(unittest.TestCase):
    def test_buy_decision_passes_policy_without_mutating_input(self):
        decision_preview = _decision("BUY")
        before = deepcopy(decision_preview)

        result = signal_decision_policy_service.build_signal_policy_preview(decision_preview)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "SIGNAL_POLICY_PREVIEW")
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["reason"], "policy source reason")
        self.assertEqual(result["rule_source"], "unit_rules")
        self.assertEqual(result["matched_rule_paths"], ["buy.groups"])
        self.assertEqual(result["condition_summary"], ["summary"])
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual(decision_preview, before)

    def test_sell_decision_passes_policy(self):
        result = signal_decision_policy_service.apply_signal_policy(_decision("SELL"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["signal"], "SELL")
        self.assertEqual(result["matched_rule_paths"], ["sell.signals"])

    def test_ignore_decision_passes_policy(self):
        result = signal_decision_policy_service.build_signal_policy_preview(_decision(None))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "IGNORE")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertIsNone(result["signal"])

    def test_invalid_decision_preview_is_rejected(self):
        result = signal_decision_policy_service.build_signal_policy_preview(
            {
                "ok": True,
                "stage": "SIGNAL_DECISION_PREVIEW",
                "decision": "ACCEPT",
                "signal": None,
                "reason": "invalid accept",
                "decision_reason": "invalid accept",
                "rule_source": "unit_rules",
                "matched_rule_paths": [],
                "condition_summary": [],
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_result"], "REJECT")
        self.assertEqual(
            result["blocked_reasons"],
            ["decision_preview.ACCEPT requires BUY or SELL signal"],
        )

    def test_rejected_decision_preview_is_rejected_by_policy_validation(self):
        result = signal_decision_policy_service.build_signal_policy_preview(
            {
                "ok": False,
                "stage": "SIGNAL_DECISION_PREVIEW",
                "decision": "REJECT",
                "signal": None,
                "reason": "invalid source",
                "decision_reason": "source rejected",
                "rule_source": "unit_rules",
                "matched_rule_paths": [],
                "condition_summary": [],
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["decision_preview.ok is not true"])

    def test_policy_service_does_not_touch_rules_or_runtime_queue(self):
        before_rules_hash = _file_sha256(ACTUAL_RULES_PATH)
        before_queue_hash = _file_sha256(RUNTIME_QUEUE_PATH)

        result = signal_decision_policy_service.build_signal_policy_preview(_decision("BUY"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(_file_sha256(ACTUAL_RULES_PATH), before_rules_hash)
        self.assertEqual(_file_sha256(RUNTIME_QUEUE_PATH), before_queue_hash)

    def test_policy_service_has_no_queue_runtime_execution_or_send_order_imports(self):
        module_text = Path(signal_decision_policy_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("routine_signal_queue", module_text)
        self.assertNotIn("runtime_io", module_text)
        self.assertNotIn("import execution", module_text)
        self.assertNotIn("from execution", module_text)
        self.assertNotIn("SendOrder", module_text)
        self.assertNotIn("import send_order", module_text)
        self.assertNotIn("from send_order", module_text)

    def test_time_policy_rejects_0859(self):
        result = signal_decision_policy_service.apply_time_policy(
            _policy("BUY"),
            _snapshot(),
            now=_at("08:59"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["time_policy"], "REGULAR")
        self.assertEqual(result["time_policy_result"], "REJECT")

    def test_time_policy_passes_0900_1000_and_1520(self):
        for hhmm in ("09:00", "10:00", "15:20"):
            with self.subTest(hhmm=hhmm):
                result = signal_decision_policy_service.apply_time_policy(
                    _policy("BUY"),
                    _snapshot(),
                    now=_at(hhmm),
                )

                self.assertTrue(result["ok"], result)
                self.assertEqual(result["decision"], "ACCEPT")
                self.assertEqual(result["policy_result"], "PASS")
                self.assertEqual(result["time_policy"], "REGULAR")
                self.assertEqual(result["time_policy_result"], "PASS")
                self.assertFalse(result["queue_connected"])
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["execution_connected"])
                self.assertFalse(result["send_order_connected"])

    def test_time_policy_rejects_1521(self):
        result = signal_decision_policy_service.apply_time_policy(
            _policy("BUY"),
            _snapshot(),
            now=_at("15:21"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_result"], "REJECT")
        self.assertEqual(result["time_policy_result"], "REJECT")

    def test_time_policy_accepts_datetime_provider(self):
        result = signal_decision_policy_service.apply_time_policy(
            _policy("SELL"),
            _snapshot(),
            datetime_provider=lambda: _at("10:00"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["signal"], "SELL")
        self.assertEqual(result["time_policy_result"], "PASS")

    def test_time_policy_requires_time_input(self):
        result = signal_decision_policy_service.apply_time_policy(
            _policy("BUY"),
            _snapshot(),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["now or datetime_provider is required"])

    def test_time_policy_rejects_invalid_preview(self):
        result = signal_decision_policy_service.apply_time_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _snapshot(),
            now=_at("10:00"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_time_policy_requires_market_snapshot_timeframe(self):
        result = signal_decision_policy_service.apply_time_policy(
            _policy("BUY"),
            {"symbol": "005930"},
            now=_at("10:00"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["market_snapshot.timeframe is required"])

    def test_operation_state_policy_passes_running_without_mutating_input(self):
        policy_preview = _time_policy("BUY")
        before = deepcopy(policy_preview)

        result = signal_decision_policy_service.apply_operation_state_policy(
            policy_preview,
            _operation_state("RUNNING"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["operation_policy"], "OPERATION_STATE")
        self.assertEqual(result["operation_policy_result"], "PASS")
        self.assertEqual(result["operation_policy_reason"], "operation state policy passed")
        self.assertEqual(result["time_policy_result"], "PASS")
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual(policy_preview, before)

    def test_operation_state_policy_rejects_stopped_paused_and_emergency_stop_statuses(self):
        for status in ("STOPPED", "PAUSED", "EMERGENCY_STOP"):
            with self.subTest(status=status):
                result = signal_decision_policy_service.apply_operation_state_policy(
                    _time_policy("BUY"),
                    _operation_state(status),
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["decision"], "REJECT")
                self.assertEqual(result["policy_result"], "REJECT")
                self.assertEqual(result["operation_policy_result"], "REJECT")
                self.assertEqual(
                    result["operation_policy_reason"],
                    f"operation_state.operation_status is {status}",
                )

    def test_operation_state_policy_rejects_enabled_false(self):
        result = signal_decision_policy_service.apply_operation_state_policy(
            _time_policy("BUY"),
            _operation_state("RUNNING", enabled=False),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["operation_policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["operation_state.enabled is not true"])

    def test_operation_state_policy_rejects_emergency_stop_flag(self):
        result = signal_decision_policy_service.apply_operation_state_policy(
            _time_policy("BUY"),
            _operation_state("RUNNING", emergency_stop=True),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["operation_policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["operation_state.emergency_stop is true"])

    def test_operation_state_policy_requires_operation_state(self):
        result = signal_decision_policy_service.apply_operation_state_policy(
            _time_policy("BUY"),
            None,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["operation_state must be dict"])

    def test_operation_state_policy_rejects_invalid_preview(self):
        result = signal_decision_policy_service.apply_operation_state_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _operation_state("RUNNING"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_operation_state_policy_rejects_missing_state_fields(self):
        result = signal_decision_policy_service.apply_operation_state_policy(
            _time_policy("BUY"),
            {"enabled": True, "emergency_stop": False},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(
            result["blocked_reasons"],
            ["missing required operation_state fields: operation_status"],
        )

    def test_routine_active_policy_passes_active_without_mutating_input(self):
        policy_preview = _time_policy("BUY")
        before = deepcopy(policy_preview)

        result = signal_decision_policy_service.apply_routine_active_policy(
            policy_preview,
            _routine_state("ACTIVE"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["routine_policy"], "ROUTINE_ACTIVE")
        self.assertEqual(result["routine_policy_result"], "PASS")
        self.assertEqual(result["routine_policy_reason"], "routine active policy passed")
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual(policy_preview, before)

    def test_routine_active_policy_rejects_inactive_paused_stopped(self):
        for status in ("INACTIVE", "PAUSED", "STOPPED"):
            with self.subTest(status=status):
                result = signal_decision_policy_service.apply_routine_active_policy(
                    _time_policy("BUY"),
                    _routine_state(status),
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["decision"], "REJECT")
                self.assertEqual(result["routine_policy_result"], "REJECT")
                self.assertEqual(result["routine_policy_reason"], f"routine_state.status is {status}")

    def test_routine_active_policy_rejects_enabled_false_missing_state_and_invalid_preview(self):
        disabled = signal_decision_policy_service.apply_routine_active_policy(
            _time_policy("BUY"),
            _routine_state("ACTIVE", enabled=False),
        )
        missing_state = signal_decision_policy_service.apply_routine_active_policy(
            _time_policy("BUY"),
            None,
        )
        missing_field = signal_decision_policy_service.apply_routine_active_policy(
            _time_policy("BUY"),
            {"enabled": True},
        )
        invalid_preview = signal_decision_policy_service.apply_routine_active_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _routine_state("ACTIVE"),
        )

        self.assertEqual(disabled["blocked_reasons"], ["routine_state.enabled is not true"])
        self.assertEqual(missing_state["blocked_reasons"], ["routine_state must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required routine_state fields: status"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_stock_active_policy_passes_active(self):
        result = signal_decision_policy_service.apply_stock_active_policy(
            _time_policy("BUY"),
            _stock_state("ACTIVE"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["stock_policy"], "STOCK_ACTIVE")
        self.assertEqual(result["stock_policy_result"], "PASS")
        self.assertEqual(result["stock_policy_reason"], "stock active policy passed")

    def test_stock_active_policy_rejects_review_and_enabled_false(self):
        review = signal_decision_policy_service.apply_stock_active_policy(
            _time_policy("BUY"),
            _stock_state("REVIEW"),
        )
        disabled = signal_decision_policy_service.apply_stock_active_policy(
            _time_policy("BUY"),
            _stock_state("ACTIVE", enabled=False),
        )

        self.assertFalse(review["ok"])
        self.assertEqual(review["decision"], "REJECT")
        self.assertEqual(review["stock_policy_result"], "REJECT")
        self.assertEqual(review["stock_policy_reason"], "stock_state.status is REVIEW")
        self.assertEqual(disabled["blocked_reasons"], ["stock_state.enabled is not true"])

    def test_stock_active_policy_rejects_inactive_paused_stopped_missing_state_and_invalid_preview(self):
        for status in ("INACTIVE", "PAUSED", "STOPPED"):
            with self.subTest(status=status):
                result = signal_decision_policy_service.apply_stock_active_policy(
                    _time_policy("BUY"),
                    _stock_state(status),
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["stock_policy_result"], "REJECT")
                self.assertEqual(result["stock_policy_reason"], f"stock_state.status is {status}")

        missing_state = signal_decision_policy_service.apply_stock_active_policy(_time_policy("BUY"), None)
        missing_field = signal_decision_policy_service.apply_stock_active_policy(
            _time_policy("BUY"),
            {"enabled": True},
        )
        invalid_preview = signal_decision_policy_service.apply_stock_active_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _stock_state("ACTIVE"),
        )

        self.assertEqual(missing_state["blocked_reasons"], ["stock_state must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required stock_state fields: status"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_emergency_detail_policy_passes_normal_state(self):
        result = signal_decision_policy_service.apply_emergency_detail_policy(
            _time_policy("BUY"),
            _emergency_state(),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["emergency_policy"], "EMERGENCY_DETAIL")
        self.assertEqual(result["emergency_policy_result"], "PASS")
        self.assertEqual(result["emergency_policy_reason"], "emergency detail policy passed")

    def test_emergency_detail_policy_rejects_each_stop_flag(self):
        for field in ("emergency_stop", "force_stop", "safety_lock"):
            with self.subTest(field=field):
                state = _emergency_state()
                state[field] = True

                result = signal_decision_policy_service.apply_emergency_detail_policy(
                    _time_policy("BUY"),
                    state,
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["decision"], "REJECT")
                self.assertEqual(result["emergency_policy_result"], "REJECT")
                self.assertEqual(result["emergency_policy_reason"], f"emergency_state.{field} is true")

    def test_emergency_detail_policy_rejects_missing_state_missing_field_and_invalid_preview(self):
        missing_state = signal_decision_policy_service.apply_emergency_detail_policy(
            _time_policy("BUY"),
            None,
        )
        missing_field = signal_decision_policy_service.apply_emergency_detail_policy(
            _time_policy("BUY"),
            {"emergency_stop": False, "force_stop": False},
        )
        invalid_preview = signal_decision_policy_service.apply_emergency_detail_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _emergency_state(),
        )

        self.assertEqual(missing_state["blocked_reasons"], ["emergency_state must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required emergency_state fields: safety_lock"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_budget_policy_passes_when_budget_is_available(self):
        result = signal_decision_policy_service.apply_budget_policy(
            _time_policy("BUY"),
            _budget_state(available_budget=1000, required_budget=500),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["budget_policy"], "BUDGET")
        self.assertEqual(result["budget_policy_result"], "PASS")
        self.assertEqual(result["budget_policy_reason"], "budget policy passed")

    def test_budget_policy_rejects_shortage_and_disabled(self):
        shortage = signal_decision_policy_service.apply_budget_policy(
            _time_policy("BUY"),
            _budget_state(available_budget=100, required_budget=500),
        )
        disabled = signal_decision_policy_service.apply_budget_policy(
            _time_policy("BUY"),
            _budget_state(enabled=False),
        )

        self.assertFalse(shortage["ok"])
        self.assertEqual(shortage["decision"], "REJECT")
        self.assertEqual(shortage["budget_policy_result"], "REJECT")
        self.assertEqual(shortage["blocked_reasons"], ["budget_state.available_budget is less than required_budget"])
        self.assertEqual(disabled["blocked_reasons"], ["budget_state.enabled is not true"])

    def test_budget_policy_rejects_missing_state_missing_field_and_invalid_preview(self):
        missing_state = signal_decision_policy_service.apply_budget_policy(_time_policy("BUY"), None)
        missing_field = signal_decision_policy_service.apply_budget_policy(
            _time_policy("BUY"),
            {"enabled": True, "available_budget": 1000},
        )
        invalid_preview = signal_decision_policy_service.apply_budget_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _budget_state(),
        )

        self.assertEqual(missing_state["blocked_reasons"], ["budget_state must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required budget_state fields: required_budget"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_duplicate_signal_policy_rejects_duplicate_and_passes_non_duplicate(self):
        duplicate = signal_decision_policy_service.apply_duplicate_signal_policy(
            _time_policy("BUY"),
            _signal_history(last_signal="BUY", signal="BUY"),
        )
        non_duplicate = signal_decision_policy_service.apply_duplicate_signal_policy(
            _time_policy("BUY"),
            _signal_history(last_signal="SELL", signal="BUY"),
        )

        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["decision"], "REJECT")
        self.assertEqual(duplicate["duplicate_policy"], "DUPLICATE_SIGNAL")
        self.assertEqual(duplicate["duplicate_policy_result"], "REJECT")
        self.assertEqual(duplicate["blocked_reasons"], ["duplicate signal for same symbol, routine_id, and signal"])
        self.assertTrue(non_duplicate["ok"], non_duplicate)
        self.assertEqual(non_duplicate["duplicate_policy_result"], "PASS")

    def test_duplicate_signal_policy_keeps_none_signal_ignored(self):
        result = signal_decision_policy_service.apply_duplicate_signal_policy(
            _time_policy(None),
            _signal_history(last_signal="BUY", signal="BUY"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "IGNORE")
        self.assertIsNone(result["signal"])
        self.assertEqual(result["duplicate_policy_result"], "IGNORE")

    def test_duplicate_signal_policy_rejects_missing_state_missing_field_and_invalid_preview(self):
        missing_state = signal_decision_policy_service.apply_duplicate_signal_policy(_time_policy("BUY"), None)
        missing_field = signal_decision_policy_service.apply_duplicate_signal_policy(
            _time_policy("BUY"),
            {"last_signal": "BUY", "symbol": "005930", "routine_id": "routine-a"},
        )
        invalid_preview = signal_decision_policy_service.apply_duplicate_signal_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _signal_history(),
        )

        self.assertEqual(missing_state["blocked_reasons"], ["signal_history must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required signal_history fields: signal"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_cooldown_policy_passes_elapsed_and_disabled(self):
        elapsed = signal_decision_policy_service.apply_cooldown_policy(
            _time_policy("BUY"),
            _cooldown_state(last_signal_time="2026-07-05T09:58:00", cooldown_seconds=60),
            now=_at("10:00"),
        )
        disabled = signal_decision_policy_service.apply_cooldown_policy(
            _time_policy("BUY"),
            _cooldown_state(enabled=False),
            now=_at("10:00"),
        )

        self.assertTrue(elapsed["ok"], elapsed)
        self.assertEqual(elapsed["cooldown_policy"], "COOLDOWN")
        self.assertEqual(elapsed["cooldown_policy_result"], "PASS")
        self.assertTrue(disabled["ok"], disabled)
        self.assertEqual(disabled["cooldown_policy_result"], "PASS")

    def test_cooldown_policy_rejects_not_elapsed(self):
        result = signal_decision_policy_service.apply_cooldown_policy(
            _time_policy("BUY"),
            _cooldown_state(last_signal_time="2026-07-05T09:59:30", cooldown_seconds=60),
            now=_at("10:00"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["cooldown_policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["cooldown has not elapsed"])

    def test_cooldown_policy_rejects_missing_state_missing_field_and_invalid_preview(self):
        missing_state = signal_decision_policy_service.apply_cooldown_policy(
            _time_policy("BUY"),
            None,
            now=_at("10:00"),
        )
        missing_field = signal_decision_policy_service.apply_cooldown_policy(
            _time_policy("BUY"),
            {"enabled": True, "last_signal_time": "2026-07-05T09:58:00"},
            now=_at("10:00"),
        )
        invalid_preview = signal_decision_policy_service.apply_cooldown_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _cooldown_state(),
            now=_at("10:00"),
        )

        self.assertEqual(missing_state["blocked_reasons"], ["cooldown_state must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required cooldown_state fields: cooldown_seconds"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_delay_policy_passes_zero_and_disabled(self):
        zero = signal_decision_policy_service.apply_delay_policy(
            _time_policy("BUY"),
            _delay_state(remaining_delay_bar=0),
        )
        disabled = signal_decision_policy_service.apply_delay_policy(
            _time_policy("BUY"),
            _delay_state(enabled=False, remaining_delay_bar=3),
        )

        self.assertTrue(zero["ok"], zero)
        self.assertEqual(zero["delay_policy"], "DELAY")
        self.assertEqual(zero["delay_policy_result"], "PASS")
        self.assertTrue(disabled["ok"], disabled)
        self.assertEqual(disabled["delay_policy_result"], "PASS")

    def test_delay_policy_rejects_remaining_delay(self):
        result = signal_decision_policy_service.apply_delay_policy(
            _time_policy("BUY"),
            _delay_state(remaining_delay_bar=2),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["delay_policy_result"], "REJECT")
        self.assertEqual(result["blocked_reasons"], ["delay_state.remaining_delay_bar is greater than 0"])

    def test_delay_policy_rejects_missing_state_missing_field_and_invalid_preview(self):
        missing_state = signal_decision_policy_service.apply_delay_policy(_time_policy("BUY"), None)
        missing_field = signal_decision_policy_service.apply_delay_policy(
            _time_policy("BUY"),
            {"enabled": True},
        )
        invalid_preview = signal_decision_policy_service.apply_delay_policy(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            _delay_state(),
        )

        self.assertEqual(missing_state["blocked_reasons"], ["delay_state must be dict"])
        self.assertEqual(missing_field["blocked_reasons"], ["missing required delay_state fields: remaining_delay_bar"])
        self.assertEqual(invalid_preview["blocked_reasons"], ["policy_preview.ok is not true"])

    def test_orchestrator_passes_all_policies_in_order(self):
        result = signal_decision_policy_service.apply_all_signal_policies(
            _policy("BUY"),
            **_orchestrator_kwargs(),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_orchestrator"], "SIGNAL_POLICY_ORCHESTRATOR")
        self.assertEqual(result["policy_orchestrator_result"], "PASS")
        self.assertIsNone(result["blocked_policy"])
        self.assertEqual(
            result["applied_policies"],
            [
                "TIME_POLICY",
                "OPERATION_STATE_POLICY",
                "ROUTINE_ACTIVE_POLICY",
                "STOCK_ACTIVE_POLICY",
                "EMERGENCY_DETAIL_POLICY",
                "BUDGET_POLICY",
                "DUPLICATE_SIGNAL_POLICY",
                "COOLDOWN_POLICY",
                "DELAY_POLICY",
            ],
        )
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])

    def test_orchestrator_stops_on_each_rejecting_policy(self):
        cases = [
            (
                "TIME_POLICY",
                {"now": _at("08:59")},
                ["TIME_POLICY"],
            ),
            (
                "OPERATION_STATE_POLICY",
                {"operation_state": _operation_state("STOPPED")},
                ["TIME_POLICY", "OPERATION_STATE_POLICY"],
            ),
            (
                "ROUTINE_ACTIVE_POLICY",
                {"routine_state": _routine_state("INACTIVE")},
                ["TIME_POLICY", "OPERATION_STATE_POLICY", "ROUTINE_ACTIVE_POLICY"],
            ),
            (
                "STOCK_ACTIVE_POLICY",
                {"stock_state": _stock_state("REVIEW")},
                ["TIME_POLICY", "OPERATION_STATE_POLICY", "ROUTINE_ACTIVE_POLICY", "STOCK_ACTIVE_POLICY"],
            ),
            (
                "EMERGENCY_DETAIL_POLICY",
                {"emergency_state": _emergency_state(emergency_stop=True)},
                [
                    "TIME_POLICY",
                    "OPERATION_STATE_POLICY",
                    "ROUTINE_ACTIVE_POLICY",
                    "STOCK_ACTIVE_POLICY",
                    "EMERGENCY_DETAIL_POLICY",
                ],
            ),
            (
                "BUDGET_POLICY",
                {"budget_state": _budget_state(available_budget=100, required_budget=500)},
                [
                    "TIME_POLICY",
                    "OPERATION_STATE_POLICY",
                    "ROUTINE_ACTIVE_POLICY",
                    "STOCK_ACTIVE_POLICY",
                    "EMERGENCY_DETAIL_POLICY",
                    "BUDGET_POLICY",
                ],
            ),
            (
                "DUPLICATE_SIGNAL_POLICY",
                {"signal_history": _signal_history(last_signal="BUY", signal="BUY")},
                [
                    "TIME_POLICY",
                    "OPERATION_STATE_POLICY",
                    "ROUTINE_ACTIVE_POLICY",
                    "STOCK_ACTIVE_POLICY",
                    "EMERGENCY_DETAIL_POLICY",
                    "BUDGET_POLICY",
                    "DUPLICATE_SIGNAL_POLICY",
                ],
            ),
            (
                "COOLDOWN_POLICY",
                {"cooldown_state": _cooldown_state(last_signal_time="2026-07-05T09:59:30", cooldown_seconds=60)},
                [
                    "TIME_POLICY",
                    "OPERATION_STATE_POLICY",
                    "ROUTINE_ACTIVE_POLICY",
                    "STOCK_ACTIVE_POLICY",
                    "EMERGENCY_DETAIL_POLICY",
                    "BUDGET_POLICY",
                    "DUPLICATE_SIGNAL_POLICY",
                    "COOLDOWN_POLICY",
                ],
            ),
            (
                "DELAY_POLICY",
                {"delay_state": _delay_state(remaining_delay_bar=2)},
                [
                    "TIME_POLICY",
                    "OPERATION_STATE_POLICY",
                    "ROUTINE_ACTIVE_POLICY",
                    "STOCK_ACTIVE_POLICY",
                    "EMERGENCY_DETAIL_POLICY",
                    "BUDGET_POLICY",
                    "DUPLICATE_SIGNAL_POLICY",
                    "COOLDOWN_POLICY",
                    "DELAY_POLICY",
                ],
            ),
        ]

        for blocked_policy, overrides, applied_policies in cases:
            with self.subTest(blocked_policy=blocked_policy):
                result = signal_decision_policy_service.apply_all_signal_policies(
                    _policy("BUY"),
                    **_orchestrator_kwargs(**overrides),
                )

                self.assertFalse(result["ok"])
                self.assertEqual(result["decision"], "REJECT")
                self.assertEqual(result["policy_orchestrator_result"], "REJECT")
                self.assertEqual(result["blocked_policy"], blocked_policy)
                self.assertEqual(result["applied_policies"], applied_policies)

    def test_orchestrator_keeps_ignore_when_all_policies_pass(self):
        result = signal_decision_policy_service.apply_all_signal_policies(
            _policy(None),
            **_orchestrator_kwargs(),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "IGNORE")
        self.assertEqual(result["policy_orchestrator_result"], "IGNORE")
        self.assertIsNone(result["blocked_policy"])
        self.assertEqual(result["duplicate_policy_result"], "IGNORE")

    def test_orchestrator_rejects_invalid_preview(self):
        result = signal_decision_policy_service.apply_all_signal_policies(
            {"ok": False, "stage": "SIGNAL_POLICY_PREVIEW", "policy_result": "REJECT"},
            **_orchestrator_kwargs(),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_orchestrator_result"], "REJECT")
        self.assertEqual(result["blocked_policy"], "SIGNAL_POLICY_ORCHESTRATOR")
        self.assertEqual(result["applied_policies"], [])


if __name__ == "__main__":
    unittest.main()
