# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from operation_close_completion_evaluator import (
    STATUS_CARRYOVER_DONE,
    STATUS_CLOSE_NOT_STARTED,
    STATUS_DONE,
    STATUS_EVIDENCE_CONFLICT,
    STATUS_HOLDING_REMAINS,
    STATUS_PENDING_ORDER,
    STATUS_REVIEW_REQUIRED,
    STATUS_UNKNOWN,
    evaluate_operation_close_completion,
    resolve_liquidation_holding_quantity,
)


class OperationCloseCompletionEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.stocks = self.root / "stocks"
        self.operation_state_path = self.runtime / "operation_state.json"
        self.order_queue_path = self.runtime / "order_queue.json"
        self.positions_path = self.runtime / "positions.json"
        self.broker_holdings_path = self.runtime / "broker_holdings.json"
        self.operation_policy_path = self.root / "operation_policy.json"
        self.runtime.mkdir(parents=True)
        self.stocks.mkdir(parents=True)
        self._write_json(self.order_queue_path, {"version": 1, "orders": []})
        self._write_json(self.positions_path, {"positions": []})
        self._write_json(self.broker_holdings_path, {"broker_holdings": []})
        self._set_long_hold_policy(False)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _operation_state(self, participants: list[str], **extra: object) -> None:
        data = {
            "operation_date": "2026-07-30",
            "operation_status": "CLOSING",
            "operation_participant_stock_codes": participants,
        }
        data.update(extra)
        self._write_json(self.operation_state_path, data)

    def _set_long_hold_policy(self, enabled: bool) -> None:
        self._write_json(
            self.operation_policy_path,
            {"review_policy": {"long_term_holding_enabled": enabled}},
        )

    def _stock(
        self,
        code: str,
        state: dict[str, object],
        *,
        orders: list[dict[str, object]] | None = None,
        legacy_long_term_holding_enabled: bool = False,
    ) -> Path:
        stock_dir = self.stocks / f"{code}_Test"
        stock_dir.mkdir(parents=True)
        self._write_json(
            stock_dir / "config.json",
            {"long_term_holding_enabled": legacy_long_term_holding_enabled},
        )
        self._write_json(stock_dir / "state.json", state)
        self._write_json(stock_dir / "orders.json", {"orders": orders or []})
        return stock_dir

    def _evaluate(self) -> dict[str, object]:
        return evaluate_operation_close_completion(
            today="2026-07-30",
            operation_state_path=self.operation_state_path,
            stocks_dir=self.stocks,
            order_queue_path=self.order_queue_path,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
            operation_policy_path=self.operation_policy_path,
        )

    def _statuses(self, result: dict[str, object]) -> dict[str, str]:
        return {
            str(item["stock_code"]): str(item["status"])
            for item in result["stock_results"]
        }

    def _hashes(self) -> dict[Path, str]:
        paths = [
            self.operation_state_path,
            self.order_queue_path,
            self.positions_path,
            self.broker_holdings_path,
            self.operation_policy_path,
            *self.stocks.glob("*/*.json"),
        ]
        return {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
            if path.exists()
        }

    def test_all_done_returns_global_complete(self) -> None:
        self._operation_state(["111111", "222222"])
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})
        self._stock("222222", {"status": "EARLY_CLOSED", "holding_qty": 0})

        result = self._evaluate()

        self.assertFalse(result["blocked"])
        self.assertTrue(result["global_complete"])
        self.assertEqual({"111111": STATUS_DONE, "222222": STATUS_DONE}, self._statuses(result))

    def test_done_and_carryover_done_returns_global_complete(self) -> None:
        self._set_long_hold_policy(True)
        self._operation_state(["111111", "222222"])
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})
        self._stock(
            "222222",
            {
                "status": "AUTO_CLOSING",
                "holding_qty": 7,
                "operation_notice": "CARRYOVER_DONE",
            },
        )

        result = self._evaluate()

        self.assertTrue(result["global_complete"])
        self.assertEqual({"111111": STATUS_DONE, "222222": STATUS_CARRYOVER_DONE}, self._statuses(result))

    def test_runtime_queue_pending_order_blocks_completion(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSING", "holding_qty": 0})
        self._write_json(
            self.order_queue_path,
            {
                "version": 1,
                "orders": [
                    {
                        "id": "ORDER-1",
                        "code": "111111",
                        "status": "SEND_CALL_ACCEPTED",
                    }
                ],
            },
        )

        result = self._evaluate()

        self.assertFalse(result["global_complete"])
        self.assertEqual({"111111": STATUS_PENDING_ORDER}, self._statuses(result))
        self.assertEqual(["111111"], result["blocking_stock_codes"])

    def test_partial_fill_remaining_blocks_completion(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSING", "holding_qty": 0})
        self._write_json(
            self.order_queue_path,
            {
                "version": 1,
                "orders": [
                    {
                        "id": "ORDER-1",
                        "code": "111111",
                        "status": "PARTIALLY_FILLED",
                        "remaining_quantity": 2,
                    }
                ],
            },
        )

        self.assertEqual({"111111": STATUS_PENDING_ORDER}, self._statuses(self._evaluate()))

    def test_stock_orders_pending_quantity_blocks_completion(self) -> None:
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {"status": "AUTO_CLOSING", "holding_qty": 0},
            orders=[{"side": "SELL", "status": "OPEN", "pending_qty": 1}],
        )

        self.assertEqual({"111111": STATUS_PENDING_ORDER}, self._statuses(self._evaluate()))

    def test_holding_remains_without_carryover_blocks_completion(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSING", "holding_qty": 3})

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_carryover_with_long_term_holding_disabled_blocks_completion(self) -> None:
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {"status": "EARLY_CLOSING", "holding_qty": 3, "early_close_method": "이월"},
        )

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_manual_holding_with_long_term_holding_enabled_is_carryover_done(self) -> None:
        self._set_long_hold_policy(True)
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {"status": "STOPPED", "holding_qty": 3},
        )

        self.assertEqual({"111111": STATUS_CARRYOVER_DONE}, self._statuses(self._evaluate()))

    def test_manual_holding_with_long_term_holding_disabled_requires_review(self) -> None:
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {"status": "STOPPED", "holding_qty": 3},
            legacy_long_term_holding_enabled=True,
        )

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_manual_holding_with_active_market_override_is_never_long_hold(self) -> None:
        self._set_long_hold_policy(True)
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {
                "status": "STOPPED",
                "holding_qty": 3,
                "individual_liquidation_request": {
                    "status": "REQUESTED",
                    "method": "시장가",
                },
            },
        )

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_individual_market_request_overrides_earlier_close_carryover(self) -> None:
        self._set_long_hold_policy(True)
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {
                "status": "EARLY_CLOSING",
                "holding_qty": 3,
                "early_close_method": "이월",
                "individual_liquidation_request": {
                    "status": "REQUESTED",
                    "method": "시장가",
                },
            },
        )

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_individual_current_request_overrides_earlier_auto_close_carryover(self) -> None:
        self._set_long_hold_policy(True)
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {
                "status": "AUTO_CLOSING",
                "holding_qty": 3,
                "auto_close_method": "이월",
                "individual_liquidation_request": {
                    "status": "REQUESTED",
                    "method": "현재가",
                },
            },
        )

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_individual_carryover_request_keeps_latest_carryover_intent(self) -> None:
        self._set_long_hold_policy(True)
        self._operation_state(["111111"])
        self._stock(
            "111111",
            {
                "status": "EARLY_CLOSING",
                "holding_qty": 3,
                "early_close_method": "이월",
                "individual_liquidation_request": {
                    "status": "REQUESTED",
                    "method": "이월",
                },
            },
        )

        self.assertEqual({"111111": STATUS_CARRYOVER_DONE}, self._statuses(self._evaluate()))

    def test_review_required_blocks_completion(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "REVIEW_REQUIRED", "holding_qty": 0})

        self.assertEqual({"111111": STATUS_REVIEW_REQUIRED}, self._statuses(self._evaluate()))
        self.assertFalse(self._evaluate()["global_complete"])

    def test_participant_without_close_evidence_is_close_not_started(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "RUNNING", "holding_qty": 0})

        self.assertEqual({"111111": STATUS_CLOSE_NOT_STARTED}, self._statuses(self._evaluate()))

    def test_closed_status_with_holding_is_terminal_residual(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 5})

        self.assertEqual({"111111": STATUS_HOLDING_REMAINS}, self._statuses(self._evaluate()))

    def test_positions_and_broker_holdings_conflict_is_evidence_conflict(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSING", "holding_qty": 0})
        self._write_json(self.positions_path, {"positions": [{"code": "111111", "quantity": 1}]})
        self._write_json(self.broker_holdings_path, {"broker_holdings": [{"code": "111111", "quantity": 2}]})

        self.assertEqual({"111111": STATUS_EVIDENCE_CONFLICT}, self._statuses(self._evaluate()))

    def test_liquidation_quantity_uses_matching_positions_and_broker_holdings(self) -> None:
        self._write_json(
            self.positions_path,
            {"positions": [{"code": "111111", "quantity": 70}]},
        )
        self._write_json(
            self.broker_holdings_path,
            {"holdings": [{"code": "111111", "holding_quantity": 70}]},
        )

        result = resolve_liquidation_holding_quantity(
            "111111",
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(70, result["position_qty"])
        self.assertEqual(70, result["broker_holding_qty"])
        self.assertEqual(70, result["resolved_liquidation_qty"])
        self.assertEqual("CONSISTENT", result["reconciliation_result"])

    def test_liquidation_quantity_blocks_positions_broker_mismatch(self) -> None:
        self._write_json(
            self.positions_path,
            {"positions": [{"code": "111111", "quantity": 70}]},
        )
        self._write_json(
            self.broker_holdings_path,
            {"holdings": [{"code": "111111", "holding_quantity": 110}]},
        )

        result = resolve_liquidation_holding_quantity(
            "111111",
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("QUANTITY_MISMATCH", result["reconciliation_result"])
        self.assertIsNone(result["resolved_liquidation_qty"])

    def test_liquidation_quantity_accepts_broker_zero_without_position_record(self) -> None:
        self._write_json(self.positions_path, {"positions": []})
        self._write_json(
            self.broker_holdings_path,
            {"holdings": [{"code": "111111", "holding_quantity": 0}]},
        )

        result = resolve_liquidation_holding_quantity(
            "111111",
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(0, result["resolved_liquidation_qty"])

    def test_liquidation_quantity_blocks_positive_broker_only_holding(self) -> None:
        self._write_json(self.positions_path, {"positions": []})
        self._write_json(
            self.broker_holdings_path,
            {"holdings": [{"code": "111111", "holding_quantity": 5}]},
        )

        result = resolve_liquidation_holding_quantity(
            "111111",
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_holdings_path,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("BROKER_ONLY", result["reconciliation_result"])

    def test_corrupt_state_is_unknown(self) -> None:
        self._operation_state(["111111"])
        stock_dir = self.stocks / "111111_Test"
        stock_dir.mkdir(parents=True)
        (stock_dir / "state.json").write_text("{", encoding="utf-8")

        result = self._evaluate()

        self.assertEqual({"111111": STATUS_UNKNOWN}, self._statuses(result))
        self.assertFalse(result["global_complete"])

    def test_running_operation_status_blocks_evaluation(self) -> None:
        self._operation_state(["111111"], operation_status="RUNNING")
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})

        result = self._evaluate()

        self.assertTrue(result["blocked"])
        self.assertFalse(result["global_complete"])
        self.assertEqual([], result["stock_results"])

    def test_operation_date_mismatch_blocks_evaluation(self) -> None:
        self._operation_state(["111111"], operation_date="2026-07-29")
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})

        result = self._evaluate()

        self.assertTrue(result["blocked"])
        self.assertFalse(result["global_complete"])

    def test_missing_participant_list_blocks_evaluation(self) -> None:
        self._operation_state([])

        result = self._evaluate()

        self.assertTrue(result["blocked"])
        self.assertFalse(result["global_complete"])

    def test_empty_participant_list_is_not_auto_complete(self) -> None:
        self._operation_state([], operation_status="CLOSING")

        result = self._evaluate()

        self.assertTrue(result["blocked"])
        self.assertFalse(result["global_complete"])

    def test_ats_participant_is_evaluated_like_regular_stock(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "EARLY_CLOSED", "holding_qty": 0, "manual_ats_selection": {"session": "ATS"}})

        self.assertEqual({"111111": STATUS_DONE}, self._statuses(self._evaluate()))

    def test_ats_registered_but_not_participant_is_not_evaluated(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})
        self._stock("222222", {"status": "AUTO_CLOSED", "holding_qty": 0, "manual_ats_selection": {"session": "ATS"}})

        result = self._evaluate()

        self.assertEqual(["111111"], result["participant_stock_codes"])
        self.assertEqual({"111111": STATUS_DONE}, self._statuses(result))

    def test_restart_evaluation_uses_durable_files_only(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})

        first = self._evaluate()
        second = self._evaluate()

        self.assertEqual(first, second)
        self.assertTrue(second["global_complete"])

    def test_evaluator_is_read_only(self) -> None:
        self._operation_state(["111111"])
        self._stock("111111", {"status": "AUTO_CLOSED", "holding_qty": 0})
        before = self._hashes()

        self._evaluate()

        self.assertEqual(before, self._hashes())


if __name__ == "__main__":
    unittest.main()
