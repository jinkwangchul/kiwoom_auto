from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from decision_trace_production_observer import (
    DecisionObservationCollector,
    LiveDiagnosticModeProvider,
    ProductionDecisionTraceObserver,
)
from decision_trace_snapshot_service import DecisionTraceSnapshotService
from decision_trace_writer import DecisionTraceWriter
from engines.condition_engine import evaluate_condition, evaluate_group, evaluate_groups_or
from market_evidence_store import MarketEvidenceStore
import routine_signal_probe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_routine_engine():
    path = next((PROJECT_ROOT / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_macd_engine_for_trace_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FixedMode:
    def __init__(self, level: str) -> None:
        self.level = level

    def mode_for(self, **_kwargs) -> str:
        return self.level


class _FailingStore:
    def save_window(self, _candles):
        raise OSError("evidence unavailable")


class _FailingWriter:
    def append_record(self, _record):
        raise OSError("trace unavailable")


class _FailingSnapshot:
    def save_rules(self, _rules):
        raise OSError("snapshot unavailable")


class _FailingProbeObserver:
    def begin(self, **_kwargs):
        return DecisionObservationCollector(trace_id="trace", trace_level="DIAGNOSTIC")

    def append_decision(self, *_args, **_kwargs):
        raise OSError("observer unavailable")


class _NoneRoutine:
    ROUTINE_TYPE = "INDICATOR_FOLLOW"

    @staticmethod
    def evaluate(_context):
        return {
            "signal": None,
            "reason": "조건 미충족",
            "matched_groups": [],
            "details": ["same"],
            "signal_index": -1,
            "delay_bar": 0,
        }

    def save_settings(self, _settings):
        raise OSError("snapshot unavailable")


class DecisionTraceProductionObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 8, 8, 9, 30, tzinfo=timezone(timedelta(hours=9)))
        self.candles = [
            {
                "timestamp": f"2026-08-08T09:{minute:02d}:00+09:00",
                "open": 100 + index,
                "high": 102 + index,
                "low": 99 + index,
                "close": 101 + index,
                "volume": 1000 + index,
            }
            for index, minute in enumerate((27, 28, 29))
        ]
        self.rules = {
            "schema_version": "0.2.0",
            "enabled": True,
            "bar": {"bar_minutes": 1},
            "buy": {"groups": []},
            "sell": {"signals": {}},
        }
        self.context = {
            "routine_instance_id": "routine-instance-1",
            "routine_type": "INDICATOR_FOLLOW",
            "stock_config": {"routine_definition_id": "indicator-follow", "buy_qty": 1},
            "timezone": "Asia/Seoul",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _observer(self, level: str = "DIAGNOSTIC", *, market_store=None, writer=None, snapshot_service=None):
        return ProductionDecisionTraceObserver(
            writer=writer or DecisionTraceWriter(self.root / "live"),
            snapshot_service=snapshot_service or DecisionTraceSnapshotService(self.root / "evidence"),
            market_store=market_store or MarketEvidenceStore(self.root / "market"),
            mode_provider=_FixedMode(level),
            project_root=PROJECT_ROOT,
            now_factory=lambda: self.now,
        )

    def test_condition_observer_uses_actual_operands_for_supported_condition_families(self) -> None:
        series_map = {
            "RSI": [60.0, 50.0, 45.0],
            "MACD": [0.1, 0.2, 0.5],
            "SIGNAL": [0.2, 0.3, 0.4],
            "CLOSE": [9.0, 10.0, 12.0],
            "MA60": [10.0, 10.0, 11.0],
            "AVG_PRICE": [11.0, 11.0, 11.5],
            "BOLLINGER_UPPER": [10.0, 11.0, 12.0],
        }
        conditions = [
            {"target": "RSI", "operator": "<=", "value": 45},
            {"target": "MACD", "operator": ">", "compare_target": "SIGNAL"},
            {"target": "CLOSE", "operator": "CROSS_UP", "compare_target": "MA", "period": 60},
            {"target": "CLOSE", "operator": ">", "compare_target": "MA", "compare_period": 60},
            {"target": "CLOSE", "operator": ">=", "compare_target": "BOLLINGER_UPPER"},
            {"target": "CLOSE", "operator": ">=", "compare_target": "AVG_PRICE", "value": 0.15},
            {"not": True, "target": "RSI", "operator": "<", "value": 10},
        ]
        collector = DecisionObservationCollector(trace_id="trace", trace_level="DIAGNOSTIC")

        baseline = [evaluate_condition(item, series_map) for item in conditions]
        observed = [
            evaluate_condition(item, series_map, -1, collector, f"buy.groups[0].conditions[{index}]")
            for index, item in enumerate(conditions)
        ]

        self.assertEqual(baseline, observed)
        self.assertEqual(len(collector.conditions), len(conditions))
        self.assertEqual(collector.conditions[0]["left_operand"]["value"], 45.0)
        self.assertEqual(collector.conditions[0]["right_operand"]["value"], 45.0)
        self.assertEqual(collector.conditions[1]["right_operand"]["value"], 0.4)
        self.assertTrue(collector.conditions[-1]["negated"])
        self.assertFalse(collector.conditions[-1]["raw_result"])
        self.assertTrue(collector.conditions[-1]["final_result"])
        self.assertTrue(any(item["indicator"] == "BOLLINGER_UPPER" for item in collector.indicator_snapshots))

    def test_group_observation_preserves_and_or_disabled_results_and_paths(self) -> None:
        series = {"VALUE": [1, 2, 3]}
        groups = [
            {
                "name": "and-pass",
                "conditions_logic": "AND",
                "conditions": [
                    {"target": "VALUE", "operator": ">", "value": 2},
                    {"target": "VALUE", "operator": ">=", "value": 3},
                ],
            },
            {
                "name": "or-pass",
                "conditions_logic": "OR",
                "conditions": [
                    {"target": "VALUE", "operator": "<", "value": 0},
                    {"target": "VALUE", "operator": "=", "value": 3},
                ],
            },
            {"name": "disabled", "enabled": False, "conditions": []},
        ]
        collector = DecisionObservationCollector(trace_id="trace", trace_level="DIAGNOSTIC")

        baseline = evaluate_groups_or(groups, series)
        observed = evaluate_groups_or(groups, series, -1, collector, "buy.groups")

        self.assertEqual(baseline, observed)
        self.assertEqual([item["path"] for item in collector.groups], [
            "buy.groups[0]", "buy.groups[1]", "buy.groups[2]"
        ])
        self.assertEqual([item["logic"] for item in collector.groups], ["AND", "OR", "AND"])
        self.assertEqual([item["result"] for item in collector.groups], [True, True, False])

    def test_routine_buy_sell_none_are_identical_with_observer_off_and_on(self) -> None:
        module = _load_routine_engine()
        buy_group = {
            "enabled": True,
            "name": "buy-pass",
            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
        }
        sell_group = {
            "enabled": True,
            "name": "sell-pass",
            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
        }
        candles = [{"close": 10, "volume": 100}, {"close": 11, "volume": 100}, {"close": 12, "volume": 100}]
        cases = []
        for expected in ("BUY", "SELL", None):
            config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
            config["buy"]["delay_bar"] = 0
            config["buy"]["groups"] = [buy_group] if expected == "BUY" else [{
                "enabled": True,
                "name": "buy-fail",
                "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}],
            }]
            config["sell"] = {
                "delay_bar": 0,
                "signals": {
                    "macd_sell": {
                        "enabled": expected == "SELL",
                        "groups": [sell_group] if expected == "SELL" else [],
                    }
                },
            }
            cases.append((expected, config))

        for expected, config in cases:
            with self.subTest(expected=expected):
                collector = DecisionObservationCollector(trace_id="trace", trace_level="DIAGNOSTIC")
                baseline = module.evaluate_indicator_follow_routine(candles, config, {})
                observed = module.evaluate_indicator_follow_routine(
                    candles, config, {"decision_trace_observer": collector}
                )
                self.assertEqual(baseline, observed)
                self.assertEqual(observed.signal, expected)
                self.assertTrue(collector.groups)
                self.assertIn("SELL", collector.aggregations)
                if expected != "SELL":
                    self.assertIn("BUY", collector.aggregations)

    def test_normal_records_buy_but_skips_none_before_evidence(self) -> None:
        observer = self._observer("NORMAL")
        buy_collector = observer.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        buy = observer.append_decision(
            buy_collector,
            result={"signal": "BUY", "reason": "매수조건 충족", "details": [], "signal_index": -1},
            candles=self.candles,
            context=self.context,
            routine_name="지표추종매매",
            code="293490",
            name="카카오게임즈",
        )
        none_collector = observer.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        none = observer.append_decision(
            none_collector,
            result={"signal": None, "reason": "조건 미충족", "details": [], "signal_index": -1},
            candles=[dict(self.candles[-1], close=999)],
            context=self.context,
            routine_name="지표추종매매",
            code="293490",
            name="카카오게임즈",
        )

        self.assertEqual(buy["status"], "appended")
        self.assertEqual(buy["record"]["final_decision"], "BUY")
        self.assertEqual(buy["record"]["conditions"], [])
        self.assertEqual(none["status"], "skipped")
        self.assertEqual(len(list((self.root / "market").glob("*.json"))), 1)

        sell_collector = observer.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        sell = observer.append_decision(
            sell_collector,
            result={"signal": "SELL", "reason": "매도조건 충족", "details": [], "signal_index": -1},
            candles=self.candles,
            context=self.context,
            routine_name="지표추종매매",
            code="293490",
            name="카카오게임즈",
        )
        self.assertEqual(sell["status"], "appended")
        self.assertEqual(sell["record"]["final_decision"], "SELL")

    def test_diagnostic_records_none_with_condition_group_and_indicator_details(self) -> None:
        observer = self._observer("DIAGNOSTIC")
        collector = observer.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        evaluate_group(
            {
                "name": "none",
                "conditions": [
                    {"target": "CLOSE", "operator": ">", "value": 999},
                    {"target": "OSC", "operator": "TURN_UP"},
                ],
            },
            {"CLOSE": [100.0, 101.0, 102.0], "OSC": [3.0, 2.0, 3.0]},
            -1,
            collector,
            "buy.groups[0]",
        )
        collector.observe_aggregation("BUY", {
            "logic": "OR",
            "active_group_paths": ["buy.groups[0]"],
            "matched_group_paths": [],
            "result": False,
        })

        outcome = observer.append_decision(
            collector,
            result={"signal": None, "reason": "조건 미충족", "details": ["FAIL"], "signal_index": -1},
            candles=self.candles,
            context=self.context,
            routine_name="지표추종매매",
            code="293490",
            name="카카오게임즈",
        )

        self.assertEqual(outcome["status"], "appended", outcome)
        record = outcome["record"]
        self.assertEqual(record["final_decision"], "NONE")
        self.assertEqual(record["evaluation_status"], "COMPLETED")
        self.assertEqual(record["conditions"][0]["left_operand"]["value"], 102.0)
        self.assertEqual(record["groups"][0]["group_ref"]["path"], "buy.groups[0]")
        self.assertEqual(record["buy_aggregation"]["active_group_refs"][0]["path"], "buy.groups[0]")
        self.assertTrue(record["indicator_snapshots"])

    def test_diagnostic_mode_requires_scope_and_expires(self) -> None:
        no_scope = LiveDiagnosticModeProvider(environ={"KIWOOM_DIAGNOSTIC_TRACE": "1"}, activated_at=self.now)
        scoped = LiveDiagnosticModeProvider(
            environ={
                "KIWOOM_DIAGNOSTIC_TRACE": "1",
                "KIWOOM_DIAGNOSTIC_SCOPE_STOCK": "293490",
                "KIWOOM_DIAGNOSTIC_MINUTES": "30",
            },
            activated_at=self.now,
        )

        self.assertEqual(no_scope.mode_for(
            stock_code="293490", routine_instance_id="x", routine_name="r", now=self.now
        ), "NORMAL")
        self.assertEqual(scoped.mode_for(
            stock_code="293490", routine_instance_id="x", routine_name="r", now=self.now
        ), "DIAGNOSTIC")
        self.assertEqual(scoped.mode_for(
            stock_code="293490", routine_instance_id="x", routine_name="r", now=self.now + timedelta(minutes=31)
        ), "NORMAL")

    def test_observation_failures_are_fail_open(self) -> None:
        evidence_failure = self._observer("DIAGNOSTIC", market_store=_FailingStore())
        collector = evidence_failure.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        result = {"signal": "SELL", "reason": "매도조건 충족", "details": [], "signal_index": -1}
        unchanged = deepcopy(result)
        outcome = evidence_failure.append_decision(
            collector, result=result, candles=self.candles, context=self.context,
            routine_name="지표추종매매", code="293490", name="카카오게임즈",
        )
        self.assertEqual(outcome["status"], "write_failed")
        self.assertEqual(result, unchanged)

        snapshot_failure = self._observer("DIAGNOSTIC", snapshot_service=_FailingSnapshot())
        collector = snapshot_failure.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        outcome = snapshot_failure.append_decision(
            collector, result=result, candles=self.candles, context=self.context,
            routine_name="지표추종매매", code="293490", name="카카오게임즈",
        )
        self.assertEqual(outcome["status"], "write_failed")
        self.assertEqual(result, unchanged)

        writer_failure = self._observer("DIAGNOSTIC", writer=_FailingWriter())
        collector = writer_failure.begin(
            stock_code="293490", routine_instance_id="routine-instance-1", routine_name="지표추종매매",
            initial_rules=self.rules,
        )
        outcome = writer_failure.append_decision(
            collector, result=result, candles=self.candles, context=self.context,
            routine_name="지표추종매매", code="293490", name="카카오게임즈",
        )
        self.assertEqual(outcome["status"], "write_failed")
        self.assertEqual(result, unchanged)

    def test_probe_return_contract_is_identical_when_observer_raises(self) -> None:
        stock_dir = self.root / "293490_카카오게임즈"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text(
            json.dumps({"trade_enabled": True, "status": "WATCHING"}), encoding="utf-8"
        )
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "candles.json").write_text(json.dumps(self.candles), encoding="utf-8")

        with patch.object(routine_signal_probe, "_append_log"), patch.object(
            routine_signal_probe, "read_reference_price", return_value=None
        ):
            baseline = routine_signal_probe.probe_routine_for_stock(
                _NoneRoutine, "지표추종매매", stock_dir, "tick", decision_trace_observer=None
            )
            observed = routine_signal_probe.probe_routine_for_stock(
                _NoneRoutine,
                "지표추종매매",
                stock_dir,
                "tick",
                decision_trace_observer=_FailingProbeObserver(),
            )

        self.assertEqual(observed, baseline)


if __name__ == "__main__":
    unittest.main()
