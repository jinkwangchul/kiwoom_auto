from __future__ import annotations

from pathlib import Path
import json
import tempfile
import types
import unittest
from unittest.mock import patch

import routine_signal_probe
import routine_signal_consumer
import routine_signal_queue
import gui_auto_trade_timer
from routine_main_facts import (
    build_routine_main_facts_from_projection,
    routine_main_facts_identity,
)
from routine_instance_registry import load_routine_definitions
from routine_package_contract import validate_routine_definition_capabilities


def _facts(*, marker: str) -> dict:
    code = "005930"
    return build_routine_main_facts_from_projection(
        {
            "signals": [],
            "orders": [{"code": code, "marker": marker}],
            "executions": [],
            "processes": [],
            "fills": [{"code": code, "marker": marker}],
            "positions": [{"code": code, "quantity": 1, "marker": marker}],
            "holdings": [],
            "stock_configs": {
                code: {
                    "assigned_routine_instance_id": "instance-a",
                    "routine_definition_id": "indicator_follow",
                    "marker": marker,
                }
            },
            "stock_states": {
                code: {"trade_enabled": True, "status": "MONITORING"}
            },
            "selected_account_no": "account-a",
            "allowed_stock_codes": [code],
            "actionable_prices_by_code": {code: 70123},
            "current_orderable_cash": 999999,
            "budget": {"available": True, "marker": marker},
            "limits": {},
            "market": {
                "raw_candles_by_code": {code: []},
                "reference_prices_by_code": {code: 70000},
            },
            "review": {},
        }
    ).to_payload()


class FullBoundaryP0SignalFactsTest(unittest.TestCase):
    def test_indicator_follow_declares_and_resolves_all_production_callbacks(self) -> None:
        root = Path(__file__).resolve().parents[1]
        definition = next(
            value
            for value in load_routine_definitions(project_root=root)
            if value.definition_id == "indicator_follow"
        )
        capability = validate_routine_definition_capabilities(definition)
        spec = json.loads(
            (definition.package_dir / "group_pack_spec.json").read_text(encoding="utf-8")
        )

        self.assertTrue(capability["ok"], capability)
        for key in (
            "rule_commit_validator",
            "settings.registration_callable",
            "evaluation.market_bar_projection_callable",
            "evaluation.cycle_projection_callable",
        ):
            self.assertTrue(capability["resolved"].get(key), key)
        self.assertTrue(set(capability["required_files"]).issubset(set(spec["files"])))

    def test_stale_signal_facts_re_evaluate_before_single_queue_mutation(self) -> None:
        first = _facts(marker="old")
        fresh = _facts(marker="fresh")
        evaluated_markers: list[str] = []
        cycle_markers: list[str] = []
        queued: list[dict] = []

        def project_cycle_context(**facts):
            cycle_markers.append(facts["order_queue"][0]["marker"])
            facts["order_queue"][0]["marker"] = "routine-local-mutation"
            return {"status": "resolved"}

        def evaluate(context):
            evaluated_markers.append(context["stock_config"]["marker"])
            return {
                "signal": "BUY",
                "execution_intent": {"side": "BUY", "quantity": 1},
            }

        module = types.SimpleNamespace(
            ROUTINE_TYPE="auto_trade",
            market_bar_projection_request=lambda _rules: {
                "projection": "COMPLETED_TIMEFRAME"
            },
            project_cycle_context=project_cycle_context,
            evaluate=evaluate,
        )
        refreshes = iter((fresh, fresh))

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_TEST"
            stock_dir.mkdir()
            with (
                patch.object(routine_signal_probe, "_load_instance_rules", return_value={}),
                patch.object(routine_signal_probe, "_append_log"),
                patch.object(routine_signal_probe, "find_library_stock_by_code", return_value=None),
                patch.object(routine_signal_probe, "read_reference_price", return_value=None),
                patch.object(
                    routine_signal_probe,
                    "_maybe_enqueue_signal",
                    side_effect=lambda result, **_kwargs: (
                        queued.append(dict(result))
                        or {"status": "queued", "id": "signal-a"}
                    ),
                ),
                patch.object(
                    routine_signal_probe,
                    "transition_running_budget_adjustment_for_signal",
                    return_value={"ok": True},
                ),
            ):
                result = routine_signal_probe.probe_routine_for_stock(
                    module,
                    "지표추종매매A",
                    stock_dir,
                    "2026-09-06 09:00:00",
                    decision_trace_observer=None,
                    main_facts=first,
                    fresh_main_facts_provider=lambda: next(refreshes),
                )

        self.assertEqual(["old", "fresh"], evaluated_markers)
        self.assertEqual(["old", "fresh"], cycle_markers)
        self.assertEqual("old", first["orders"][0]["marker"])
        self.assertEqual(1, len(queued))
        self.assertEqual(fresh["snapshot_hash"], result["evaluation_facts_identity"]["snapshot_hash"])
        self.assertEqual(
            result["evaluation_facts_identity"],
            result["execution_intent"]["evaluation_facts_identity"],
        )
        self.assertEqual("queued", result["queue_status"])

    def test_missing_market_projection_callback_fails_closed_without_queue(self) -> None:
        facts = _facts(marker="stable")
        module = types.SimpleNamespace(
            ROUTINE_TYPE="auto_trade",
            project_cycle_context=lambda **_facts: {"status": "resolved"},
            evaluate=lambda _context: {
                "signal": "BUY",
                "execution_intent": {"side": "BUY", "quantity": 1},
            },
        )

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_TEST"
            stock_dir.mkdir()
            with (
                patch.object(routine_signal_probe, "_load_instance_rules", return_value={}),
                patch.object(routine_signal_probe, "_append_log"),
                patch.object(routine_signal_probe, "_maybe_enqueue_signal") as enqueue,
            ):
                result = routine_signal_probe.probe_routine_for_stock(
                    module,
                    "지표추종매매A",
                    stock_dir,
                    "2026-09-06 09:00:00",
                    decision_trace_observer=None,
                    main_facts=facts,
                    fresh_main_facts_provider=lambda: facts,
                )

        self.assertEqual("ERROR", result["signal"])
        self.assertIn("ROUTINE_MARKET_PROJECTION_REQUEST_UNAVAILABLE", result["reason"])
        enqueue.assert_not_called()

    def test_consumer_rejects_signal_and_intent_facts_identity_mismatch(self) -> None:
        base = _facts(marker="stable")
        projection = {
            key: value
            for key, value in base.items()
            if key not in {"revision", "captured_at", "snapshot_hash"}
        }
        projection["signals"] = [
            {
                "id": "signal-a",
                "code": "005930",
                "signal": "BUY",
                "status": "PENDING",
                "execution_enabled": False,
                "evaluation_facts_identity": {
                    "revision": "revision-a",
                    "snapshot_hash": "hash-a",
                    "candidate_guard_hash": "guard-a",
                },
                "execution_intent": {
                    "side": "BUY",
                    "quantity": 1,
                    "evaluation_facts_identity": {
                        "revision": "revision-b",
                        "snapshot_hash": "hash-b",
                        "candidate_guard_hash": "guard-b",
                    },
                },
            }
        ]
        facts = build_routine_main_facts_from_projection(projection).to_payload()

        with patch.object(
            routine_signal_consumer,
            "dry_run_order_manager_for_signal_with_payload_preview",
        ) as dry_run:
            result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
                write_order_queue=True,
                mark_previewed=True,
                main_facts=facts,
            )

        self.assertTrue(result["summary"]["facts_stale"])
        self.assertEqual("SIGNAL_FACTS_IDENTITY_MISMATCH", result["summary"]["reason"])
        self.assertFalse(result["summary"]["files_mutated"])
        dry_run.assert_not_called()

    def test_queue_binds_same_facts_identity_to_signal_and_intent(self) -> None:
        identity = {
            "revision": "revision-a",
            "snapshot_hash": "hash-a",
            "candidate_guard_hash": "guard-a",
        }
        with tempfile.TemporaryDirectory() as temp:
            queue_path = Path(temp) / "routine_signals.json"
            with patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                queued = routine_signal_queue.enqueue_routine_signal(
                    {
                        "signal": "BUY",
                        "evaluation_facts_identity": identity,
                        "execution_intent": {
                            "side": "BUY",
                            "quantity": 1,
                            "evaluation_facts_identity": dict(identity),
                        },
                    },
                    routine="지표추종매매A",
                    code="005930",
                    name="삼성전자",
                    tick_key="tick-a",
                )
            record = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]

        self.assertEqual("queued", queued["status"])
        self.assertEqual(identity, record["evaluation_facts_identity"])
        self.assertEqual(identity, record["execution_intent"]["evaluation_facts_identity"])

    def test_candidate_guard_stale_blocks_then_queue_refreshes_after_re_evaluation(self) -> None:
        first = _facts(marker="old")
        old_identity = routine_main_facts_identity(first, stock_code="005930")
        current_projection = {
            key: value
            for key, value in _facts(marker="fresh").items()
            if key not in {"revision", "captured_at", "snapshot_hash"}
        }
        current_projection["signals"] = [
            {
                "id": "signal-a",
                "code": "005930",
                "signal": "BUY",
                "status": "PENDING",
                "execution_enabled": False,
                "evaluation_facts_identity": old_identity,
                "execution_intent": {
                    "side": "BUY",
                    "quantity": 1,
                    "evaluation_facts_identity": dict(old_identity),
                },
            }
        ]
        current = build_routine_main_facts_from_projection(current_projection).to_payload()

        result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
            write_order_queue=True,
            mark_previewed=True,
            main_facts=current,
        )

        self.assertTrue(result["summary"]["facts_stale"])
        self.assertEqual("SIGNAL_EVALUATION_FACTS_STALE", result["summary"]["reason"])
        self.assertFalse(result["summary"]["files_mutated"])

        new_identity = routine_main_facts_identity(current, stock_code="005930")
        with tempfile.TemporaryDirectory() as temp:
            queue_path = Path(temp) / "routine_signals.json"
            with patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                initial = routine_signal_queue.enqueue_routine_signal(
                    {
                        "signal": "BUY",
                        "signal_index": 3,
                        "evaluation_facts_identity": old_identity,
                        "execution_intent": {
                            "side": "BUY",
                            "quantity": 1,
                            "evaluation_facts_identity": dict(old_identity),
                        },
                    },
                    routine="지표추종매매A",
                    code="005930",
                    name="삼성전자",
                    tick_key="tick-a",
                )
                refreshed = routine_signal_queue.enqueue_routine_signal(
                    {
                        "signal": "BUY",
                        "signal_index": 3,
                        "evaluation_facts_identity": new_identity,
                        "execution_intent": {
                            "side": "BUY",
                            "quantity": 2,
                            "evaluation_facts_identity": dict(new_identity),
                        },
                    },
                    routine="지표추종매매A",
                    code="005930",
                    name="삼성전자",
                    tick_key="tick-a",
                )
            records = json.loads(queue_path.read_text(encoding="utf-8"))["signals"]

        self.assertEqual("queued", initial["status"])
        self.assertEqual("refreshed", refreshed["status"])
        self.assertEqual(initial["id"], refreshed["id"])
        self.assertEqual(1, len(records))
        self.assertEqual(2, records[0]["execution_intent"]["quantity"])
        self.assertEqual(new_identity, records[0]["evaluation_facts_identity"])

    def test_timer_re_evaluates_once_before_retrying_consumer(self) -> None:
        window = types.SimpleNamespace(
            statusBarMessage=lambda _message: None,
        )
        snapshot = object()
        first_pipeline = {
            "signal_reevaluation_required": True,
            "consumer": {"facts_stale": True, "reason": "SIGNAL_EVALUATION_FACTS_STALE"},
        }
        second_pipeline = {"consumer": {"facts_stale": False}}
        with (
            patch.object(gui_auto_trade_timer, "project_execution_universe", return_value=snapshot),
            patch.object(
                gui_auto_trade_timer,
                "probe_all_enabled_routine_stocks_once",
                return_value={"logged": 0, "error": 0},
            ) as probe,
            patch.object(
                gui_auto_trade_timer,
                "_process_pending_signal_pipeline",
                side_effect=(first_pipeline, second_pipeline),
            ) as pipeline,
            patch.object(gui_auto_trade_timer, "observe_owner_failure_transition"),
        ):
            result = gui_auto_trade_timer._auto_trade_run_signal_cycle(window, "tick-a")

        self.assertEqual(second_pipeline, result)
        self.assertEqual(2, probe.call_count)
        self.assertEqual(2, pipeline.call_count)


if __name__ == "__main__":
    unittest.main()
