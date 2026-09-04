# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
)
from execution_preview_service import preview_execution_for_order
from execution_queue_writer import commit_execution_queue_write
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED


ACCOUNT = "12345678"
CODE = "003550"


class CurrentPricePreHashRevalidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue_path = self.root / "order_queue.json"
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"
        self.holdings_path = self.root / "broker_holdings.json"
        self.stock_dir = self.root / f"{CODE}_LG"
        self.stock_dir.mkdir()
        (self.stock_dir / "config.json").write_text(
            json.dumps({}),
            encoding="utf-8",
        )
        (self.stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "signal_probe_only": False,
                    "review_required": False,
                }
            ),
            encoding="utf-8",
        )
        (self.root / "positions.json").write_text(
            json.dumps({"version": 1, "positions": []}),
            encoding="utf-8",
        )
        self.current_price: int | None = 12_000
        self.price_calls: list[str] = []
        self.orderable_cash = 1_000_000
        self.boundary = AutoTradeOrderExecutionBoundary(
            AutoTradeOrderExecutionContext(
                kiwoom_connected=lambda: True,
                account_numbers=lambda: [ACCOUNT],
                selected_account_no=lambda: ACCOUNT,
                send_order_callable=lambda: None,
                selected_stock_info=lambda: (self.stock_dir, CODE, "LG"),
                selected_routine_metadata=lambda: None,
                selected_target_instance_ids=lambda: (),
                selected_routine_dir=lambda: None,
                routine_dirs=lambda: [],
                stock_dirs_in_routine=lambda _path: [],
                base_stocks=lambda: [],
                order_queue_path=lambda: self.queue_path,
                order_executions_path=lambda: self.executions_path,
                order_locks_path=lambda: self.locks_path,
                all_group_stock_dirs=lambda: [self.stock_dir],
                current_orderable_cash=lambda: self.orderable_cash,
                broker_holdings_path=lambda: self.holdings_path,
                fresh_current_price=self._fresh_price,
            )
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fresh_price(self, code: str) -> int | None:
        self.price_calls.append(code)
        return self.current_price

    def _order(
        self,
        *,
        side: str = "BUY",
        price_basis: str = "CURRENT_PRICE",
        price: int = 10_000,
        quantity: int = 10,
        budget: int = 150_000,
    ) -> dict[str, object]:
        hoga = "MARKET" if price_basis == "MARKET" else "CURRENT_PRICE"
        order_price = 0 if price_basis == "MARKET" else price
        return {
            "id": "ORDER_E0B_1",
            "status": "EXECUTABLE",
            "source_signal_id": "SIG_E0B_1",
            "account_no": ACCOUNT,
            "code": CODE,
            "side": side,
            "quantity": quantity,
            "amount": budget,
            "price": order_price,
            "price_basis": price_basis,
            "order_type": "LIMIT" if price_basis != "MARKET" else "MARKET",
            "order_intent": {
                "side": side,
                "hoga": hoga,
                "price_basis": price_basis,
                "price": order_price,
            },
            "execution_intent": {
                "side": side,
                "price_basis": price_basis,
                "price": order_price,
                "budget": budget,
            },
            "approval_status": "APPROVED",
            "policy_status": "EXECUTABLE",
            "execution_enabled": False,
        }

    def _write_queue(self, order: dict[str, object]) -> bytes:
        self.queue_path.write_text(
            json.dumps(
                {"version": 1, "revision": 0, "updated_at": "before", "orders": [order]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.queue_path.read_bytes()

    def _write_holding(self, *, holding: int, available: int) -> None:
        self.holdings_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "holdings": [
                        {
                            "account_no": ACCOUNT,
                            "stock_code": CODE,
                            "holding_quantity": holding,
                            "available_quantity": available,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _patch_budget_evidence(self, *, total_budget: int = 1_000_000):
        snapshot = SimpleNamespace(
            account_status=ACCOUNT_COMPLETED,
            identity=SimpleNamespace(
                account_no=ACCOUNT,
                trading_day=date.today().isoformat(),
            ),
            stocks=(
                SimpleNamespace(
                    stock_code=CODE,
                    stock_status=STOCK_RESTORED,
                    review_required=False,
                ),
            ),
        )
        return (
            mock.patch(
                "auto_trade_order_execution_boundary.production_recovery_registry.snapshot",
                return_value=snapshot,
            ),
            mock.patch(
                "auto_trade_order_execution_boundary.read_system_total_budget_for_recalculation",
                return_value=total_budget,
            ),
        )

    def _finalize(self, order: dict[str, object]) -> dict[str, object]:
        self._write_queue(order)
        recovery_patch, budget_patch = self._patch_budget_evidence()
        with recovery_patch, budget_patch:
            return self.boundary.finalize_current_price_before_hash(
                order,
                queue_path=self.queue_path,
            )

    def test_price_increase_within_budget_keeps_quantity_and_updates_final_price(self) -> None:
        order = self._order(price=10_000, quantity=10, budget=150_000)

        result = self._finalize(order)

        self.assertTrue(result["ok"], result)
        self.assertEqual(12_000, result["final_price"])
        self.assertEqual(120_000, result["refreshed_exposure"])
        self.assertEqual(10, result["order"]["quantity"])
        self.assertEqual(12_000, result["order"]["price"])
        self.assertEqual(12_000, result["order"]["execution_intent"]["price"])
        self.assertEqual(10_000, order["price"])

    def test_price_increase_over_approved_budget_fails_without_quantity_change(self) -> None:
        order = self._order(price=10_000, quantity=10, budget=110_000)

        result = self._finalize(order)

        self.assertFalse(result["ok"])
        self.assertEqual("current_price_pre_hash_approved_budget_exceeded", result["stage"])
        self.assertEqual(10, result["quantity"])
        self.assertEqual(120_000, result["refreshed_exposure"])
        self.assertEqual(10, order["quantity"])
        self.assertEqual(10_000, order["price"])

    def test_price_decrease_does_not_increase_quantity(self) -> None:
        self.current_price = 10_000
        order = self._order(price=12_000, quantity=10, budget=150_000)

        result = self._finalize(order)

        self.assertTrue(result["ok"], result)
        self.assertEqual(10_000, result["order"]["price"])
        self.assertEqual(10, result["order"]["quantity"])

    def test_same_price_keeps_the_existing_quantity_and_identity_input(self) -> None:
        self.current_price = 10_000
        order = self._order(price=10_000, quantity=10, budget=150_000)

        result = self._finalize(order)

        self.assertTrue(result["ok"], result)
        self.assertEqual(10_000, result["original_price"])
        self.assertEqual(10_000, result["final_price"])
        self.assertEqual(10, result["order"]["quantity"])

    def test_refreshed_cash_guard_uses_quantity_times_final_price(self) -> None:
        self.orderable_cash = 119_999
        order = self._order(price=10_000, quantity=10, budget=150_000)

        result = self._finalize(order)

        self.assertFalse(result["ok"])
        self.assertEqual("fresh_buy_orderable_cash_exceeded", result["stage"])
        self.assertEqual(120_000, result["refreshed_exposure"])

    def test_system_total_budget_guard_uses_refreshed_exposure(self) -> None:
        order = self._order(price=10_000, quantity=10, budget=150_000)
        self._write_queue(order)
        recovery_patch, budget_patch = self._patch_budget_evidence(total_budget=119_999)
        with recovery_patch, budget_patch:
            result = self.boundary.finalize_current_price_before_hash(
                order,
                queue_path=self.queue_path,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("fresh_buy_budget_exceeded", result["stage"])
        self.assertEqual(120_000, result["refreshed_exposure"])

    def test_market_and_order_price_do_not_call_current_price_resolver(self) -> None:
        for basis in ("MARKET", "ORDER_PRICE"):
            with self.subTest(price_basis=basis):
                self.price_calls.clear()
                order = self._order(price_basis=basis)
                result = self.boundary.finalize_current_price_before_hash(
                    order,
                    queue_path=self.queue_path,
                )
                self.assertTrue(result["ok"])
                self.assertFalse(result["applied"])
                self.assertEqual([], self.price_calls)
                self.assertEqual(order, result["order"])

    def test_ratio_trigger_current_price_requires_fresh_pre_hash_evidence(self) -> None:
        order = self._order(price_basis="ORDER_PRICE", price=10_000)
        order["execution_intent"]["final_current_price_evidence_required"] = True

        result = self.boundary.finalize_current_price_before_hash(
            order,
            queue_path=self.queue_path,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("eligibility_current_price_pre_hash_validated", result["stage"])
        self.assertEqual(12_000, result["validated_current_price"])
        self.assertEqual([CODE], self.price_calls)
        self.assertEqual(10_000, result["order"]["price"])

    def test_ratio_trigger_current_price_blocks_when_fresh_evidence_is_missing(self) -> None:
        self.current_price = None
        order = self._order(price_basis="ORDER_PRICE", price=10_000)
        order["execution_intent"]["final_current_price_evidence_required"] = True

        result = self.boundary.finalize_current_price_before_hash(
            order,
            queue_path=self.queue_path,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("eligibility_current_price_pre_hash_unavailable", result["stage"])

    def test_ats_current_price_result_is_reused_without_second_market_data_read(self) -> None:
        order = self._order(price_basis="ORDER_PRICE", price=10_000, budget=150_000)
        self._write_queue(order)
        recovery_patch, budget_patch = self._patch_budget_evidence()
        with recovery_patch, budget_patch:
            result = self.boundary.finalize_current_price_before_hash(
                order,
                queue_path=self.queue_path,
                execution_method="CURRENT_PRICE",
                resolved_current_price=12_000,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(12_000, result["order"]["price"])
        self.assertEqual([], self.price_calls)

    def test_sell_current_price_keeps_quantity_and_reuses_holding_guard(self) -> None:
        order = self._order(side="SELL", quantity=10)
        self._write_queue(order)
        self._write_holding(holding=12, available=10)

        result = self.boundary.finalize_current_price_before_hash(
            order,
            queue_path=self.queue_path,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(12_000, result["order"]["price"])
        self.assertEqual(10, result["order"]["quantity"])
        self.assertEqual("fresh_sell_preflight_passed", result["risk_result"]["stage"])

    def test_sell_current_price_blocks_when_fresh_holding_is_insufficient(self) -> None:
        order = self._order(side="SELL", quantity=10)
        self._write_queue(order)
        self._write_holding(holding=8, available=5)

        result = self.boundary.finalize_current_price_before_hash(
            order,
            queue_path=self.queue_path,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("fresh_sell_quantity_exceeded", result["stage"])
        self.assertEqual(10, result["quantity"])
        self.assertEqual(12_000, result["final_price"])

    def test_unavailable_current_price_blocks_before_any_lifecycle_commit(self) -> None:
        self.current_price = None
        order = self._order()
        before = self._write_queue(order)
        ats_result = {
            "ok": True,
            "applied": False,
            "execution_method": "ROUTINE",
            "order": deepcopy(order),
        }
        with mock.patch.object(
            self.boundary,
            "auto_trade_execution_block_reasons",
            return_value=[],
        ), mock.patch.object(
            self.boundary,
            "project_ats_execution_order",
            return_value=ats_result,
        ), mock.patch(
            "auto_trade_order_execution_boundary.commit_execution_enable"
        ) as enable_commit, mock.patch(
            "auto_trade_order_execution_boundary.commit_real_order_preflight"
        ) as real_ready_commit, mock.patch.object(
            self.boundary,
            "commit_execution_runtime_for_preview",
        ) as runtime_commit, mock.patch(
            "auto_trade_order_execution_boundary.commit_execution_queue_manually"
        ) as queue_commit:
            result = self.boundary.process_executable_order_for_auto_trade(order["id"])

        self.assertEqual("current_price_pre_hash", result["stage"])
        self.assertEqual(before, self.queue_path.read_bytes())
        enable_commit.assert_not_called()
        real_ready_commit.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()

    def test_process_passes_frozen_price_to_hash_pipeline(self) -> None:
        order = self._order()
        self._write_queue(order)
        enabled = deepcopy(order)
        enabled["execution_enabled"] = True
        real_ready = deepcopy(enabled)
        real_ready["status"] = "REAL_READY"
        reads = [
            {"ok": True, "order": order, "blocked_reasons": []},
            {"ok": True, "order": enabled, "blocked_reasons": []},
            {"ok": True, "order": real_ready, "blocked_reasons": []},
        ]
        ats_result = {
            "ok": True,
            "applied": False,
            "execution_method": "ROUTINE",
            "order": deepcopy(order),
        }
        preview_block = {
            "ok": False,
            "blocked_reasons": ["TEST_STOP_BEFORE_RUNTIME_COMMIT"],
            "issues": ["TEST_STOP_BEFORE_RUNTIME_COMMIT"],
        }
        with mock.patch.object(
            self.boundary, "read_order_from_queue_by_id", side_effect=reads
        ), mock.patch.object(
            self.boundary, "auto_trade_execution_block_reasons", return_value=[]
        ), mock.patch.object(
            self.boundary, "project_ats_execution_order", return_value=ats_result
        ), mock.patch.object(
            self.boundary,
            "_fresh_buy_dispatch_preflight",
            return_value={"ok": True, "stage": "fresh_buy_preflight_passed", "blocked_reasons": []},
        ), mock.patch.object(
            self.boundary, "queue_file_snapshot", return_value={"sha256": "x"}
        ), mock.patch.object(
            self.boundary,
            "build_real_preflight_guard_from_gui",
            return_value={"operator_confirmed": True},
        ), mock.patch.object(
            self.boundary, "real_preflight_guard_block_reasons", return_value=[]
        ), mock.patch(
            "auto_trade_order_execution_boundary.preview_execution_enable",
            return_value={"enable_preview": True, "blocked_reasons": []},
        ), mock.patch(
            "auto_trade_order_execution_boundary.commit_execution_enable",
            return_value={"enabled": True, "blocked_reasons": []},
        ), mock.patch(
            "auto_trade_order_execution_boundary.preview_real_order_preflight",
            return_value={"real_preflight_preview": True, "blocked_reasons": []},
        ), mock.patch(
            "auto_trade_order_execution_boundary.commit_real_order_preflight",
            return_value={"real_preflight_committed": True, "blocked_reasons": []},
        ), mock.patch(
            "auto_trade_order_execution_boundary.preview_execution_for_real_ready_order",
            return_value=preview_block,
        ) as preview_call:
            result = self.boundary.process_executable_order_for_auto_trade(order["id"])

        self.assertEqual("execution_preview", result["stage"])
        frozen = preview_call.call_args.kwargs["order_override"]
        self.assertEqual("REAL_READY", frozen["status"])
        self.assertEqual(12_000, frozen["price"])
        self.assertEqual(10, frozen["quantity"])

    def test_hash_and_queue_preview_use_final_price_and_commit_readback_matches(self) -> None:
        order = self._order()
        result = self._finalize(order)
        self.assertTrue(result["ok"], result)
        finalized = deepcopy(result["order"])
        finalized["status"] = "REAL_READY"
        finalized["execution_enabled"] = True
        guard = {
            "operator_confirmed": True,
            "account_no": ACCOUNT,
        }

        preview = preview_execution_for_order(finalized, guard)

        self.assertTrue(preview["ok"], preview)
        pipeline = preview["pipeline_result"]["pipeline"]
        self.assertEqual("12000", pipeline["request_hash_preview"]["hash_source"]["price"])
        request = pipeline["execution_request_preview"]["execution_request"]
        self.assertEqual(12_000, request["request_preview"]["price"])
        queue_preview = preview["queue_write_preview_result"]
        record_preview = queue_preview["order_queued_record_preview"]
        self.assertEqual(12_000, record_preview["execution_request"]["request_preview"]["price"])
        request_hash = record_preview["request_hash"]

        commit = commit_execution_queue_write(
            queue_preview,
            self.queue_path,
            backup=False,
            context={"manual_queue_write_confirmed": True},
            expected_revision=0,
        )

        self.assertTrue(commit["committed"], commit)
        stored = json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"][-1]
        self.assertEqual(request_hash, stored["request_hash"])
        self.assertEqual(12_000, stored["execution_request"]["request_preview"]["price"])
        self.assertEqual(10, stored["execution_request"]["request_preview"]["quantity"])


if __name__ == "__main__":
    unittest.main()
