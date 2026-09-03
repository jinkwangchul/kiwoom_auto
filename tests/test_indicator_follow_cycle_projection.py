# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "routines" / "지표추종매매" / "routine_cycle_projection.py"
SPEC = importlib.util.spec_from_file_location("indicator_follow_cycle_projection_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
projection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(projection)

ROUTINE_DIR = MODULE_PATH.parent
ROUTINE_SPEC = importlib.util.spec_from_file_location(
    "indicator_follow_routine_cycle_identity_test",
    ROUTINE_DIR / "routine.py",
)
assert ROUTINE_SPEC is not None and ROUTINE_SPEC.loader is not None
routine_module = importlib.util.module_from_spec(ROUTINE_SPEC)
sys.path.insert(0, str(ROUTINE_DIR))
try:
    ROUTINE_SPEC.loader.exec_module(routine_module)
finally:
    sys.path.remove(str(ROUTINE_DIR))


class IndicatorFollowCycleProjectionTest(unittest.TestCase):
    def _order(
        self,
        identity: str,
        *,
        side: str = "BUY",
        phase: str | None = "BASE",
        round_value: int | None = 1,
        instance: str = "INSTANCE_A",
        status: str = "FILLED",
    ) -> dict:
        intent = None
        if side == "BUY":
            intent = {
                "side": "BUY",
                "routine_type": "INDICATOR_FOLLOW",
                "routine_instance_id": instance,
                "buy_phase": phase,
                "buy_round": round_value,
                "source_signal_id": f"SIG_{identity}",
            }
        order = {
            "id": identity,
            "order_id": f"ORDER_{identity}",
            "execution_id": f"EXEC_{identity}",
            "broker_order_no": f"BRK_{identity}",
            "code": "005930",
            "side": side,
            "status": status,
            "routine_provenance": {
                "routine_type": "INDICATOR_FOLLOW",
                "routine_instance_id": instance,
            },
        }
        if intent is not None:
            order["execution_intent"] = intent
        return order

    def _fill(
        self,
        identity: str,
        *,
        cumulative: int,
        price: int,
        side: str = "BUY",
        timestamp: str = "2026-08-07 09:30:00",
    ) -> dict:
        return {
            "fill_id": f"FILL_{identity}_{cumulative}_{timestamp}",
            "order_queued_id": identity,
            "broker_order_no": f"BRK_{identity}",
            "code": "005930",
            "side": side,
            "filled_quantity": cumulative,
            "filled_price": price,
            "received_at": timestamp,
        }

    def _project(self, orders: list, fills: list, quantity: int, average: int = 100) -> dict:
        positions = [] if quantity == 0 else [{
            "position_id": "POSITION_005930",
            "code": "005930",
            "quantity": quantity,
            "average_price": average,
            "position_status": "OPEN",
        }]
        return projection.project_indicator_follow_cycle(
            code="005930",
            routine_instance_id="INSTANCE_A",
            order_queue={"orders": orders},
            fills={"fills": fills},
            positions={"positions": positions},
        )

    def test_empty_history_is_resolved_inactive(self) -> None:
        result = self._project([], [], 0)
        self.assertEqual("resolved", result["status"])
        self.assertFalse(result["active"])
        self.assertEqual(0, result["confirmed_buy_round"])

    def test_sibling_children_confirm_one_round_and_sum_actual_fills(self):
        for kind in ("TIME_SLICE", "HOGA_LEVEL"):
            with self.subTest(kind=kind):
                orders = [self._order(f"Q{i}") for i in (1, 2, 3)]
                for order in orders:
                    order["execution_intent"].update(execution_process_id="PROCESS", source_signal_id="SIGNAL", cycle_identity="CYCLE", child_kind=kind)
                fills = [self._fill(f"Q{i}", cumulative=q, price=100, timestamp=f"2026-08-07 09:3{i}:00") for i, q in ((1, 4), (2, 3), (3, 3))]
                result = self._project(orders, fills, 10)
                self.assertEqual("resolved", result["status"], result)
                self.assertEqual(1, result["confirmed_buy_round"])
                self.assertEqual(1000, result["cumulative_filled_buy_amount"])
                orders[1]["execution_intent"]["execution_process_id"] = "OTHER"
                self.assertEqual("BUY_ROUND_SEQUENCE_MISMATCH", self._project(orders, fills, 10)["unresolved_reason"])

    def test_base_first_fill_confirms_round_one(self) -> None:
        result = self._project(
            [self._order("Q1")],
            [self._fill("Q1", cumulative=3, price=100)],
            3,
        )
        self.assertEqual("resolved", result["status"])
        self.assertTrue(result["active"])
        self.assertEqual(1, result["confirmed_buy_round"])
        self.assertEqual(300, result["cumulative_filled_buy_amount"])
        self.assertEqual("Q1", result["last_buy_order_identity"])

    def test_source_candidate_and_dispatch_are_one_execution_not_pending_twice(self):
        from copy import deepcopy
        source = self._order("SOURCE", status="EXECUTABLE")
        source.pop("broker_order_no")
        dispatch = deepcopy(source)
        dispatch.update(id="DISPATCH", order_id="SOURCE", status="FILLED",
                        broker_order_no="BRK_DISPATCH", execution_request={})
        fill = self._fill("DISPATCH", cumulative=4, price=100)
        result = self._project([source, dispatch], [fill], 4)
        self.assertEqual("resolved", result["status"], result)
        self.assertEqual([], result["pending_buy_rounds"])
        dispatch["execution_intent"]["buy_round"] = 2
        self.assertEqual("unresolved", self._project([source, dispatch], [fill], 4)["status"])

    def test_same_order_partial_fills_keep_round_and_use_delta_amount(self) -> None:
        result = self._project(
            [self._order("Q1")],
            [
                self._fill("Q1", cumulative=3, price=100, timestamp="2026-08-07 09:30:00"),
                self._fill("Q1", cumulative=5, price=110, timestamp="2026-08-07 09:31:00"),
            ],
            5,
        )
        self.assertEqual(1, result["confirmed_buy_round"])
        self.assertEqual(520, result["cumulative_filled_buy_amount"])
        self.assertEqual({1: 520}, result["filled_buy_amount_by_round"])
        self.assertEqual(520, result["last_filled_buy_amount"])

    def test_new_repeat_order_confirms_next_round(self) -> None:
        result = self._project(
            [self._order("Q1"), self._order("Q2", phase="REPEAT", round_value=2)],
            [
                self._fill("Q1", cumulative=2, price=100, timestamp="2026-08-07 09:30:00"),
                self._fill("Q2", cumulative=1, price=120, timestamp="2026-08-07 10:30:00"),
            ],
            3,
        )
        self.assertEqual(2, result["confirmed_buy_round"])
        self.assertEqual(320, result["cumulative_filled_buy_amount"])
        self.assertEqual({1: 200, 2: 120}, result["filled_buy_amount_by_round"])
        self.assertEqual(200, result["base_filled_buy_amount"])
        self.assertEqual(120, result["last_filled_buy_amount"])

    def test_live_repeat_order_exposes_pending_round_for_duplicate_guard(self) -> None:
        result = self._project(
            [
                self._order("Q1"),
                self._order("Q2", phase="REPEAT", round_value=2, status="PENDING"),
            ],
            [self._fill("Q1", cumulative=2, price=100)],
            2,
        )

        self.assertEqual("resolved", result["status"])
        self.assertEqual([2], result["pending_buy_rounds"])
        self.assertEqual(["Q2"], result["pending_buy_order_identities"])

    def test_unfilled_cancelled_repeat_does_not_increment_round(self) -> None:
        result = self._project(
            [
                self._order("Q1"),
                self._order("Q2", phase="REPEAT", round_value=2, status="CANCELLED"),
            ],
            [self._fill("Q1", cumulative=2, price=100)],
            2,
        )
        self.assertEqual(1, result["confirmed_buy_round"])

    def test_partial_sell_keeps_cycle(self) -> None:
        result = self._project(
            [self._order("Q1"), self._order("S1", side="SELL")],
            [
                self._fill("Q1", cumulative=5, price=100, timestamp="2026-08-07 09:30:00"),
                self._fill("S1", cumulative=2, price=120, side="SELL", timestamp="2026-08-07 11:00:00"),
            ],
            3,
        )
        self.assertTrue(result["active"])
        self.assertTrue(result["partial_sell"])
        self.assertEqual(1, result["confirmed_buy_round"])
        self.assertEqual(500, result["cumulative_filled_buy_amount"])

    def test_full_sell_ends_cycle(self) -> None:
        result = self._project(
            [self._order("Q1"), self._order("S1", side="SELL")],
            [
                self._fill("Q1", cumulative=5, price=100, timestamp="2026-08-07 09:30:00"),
                self._fill("S1", cumulative=5, price=120, side="SELL", timestamp="2026-08-07 11:00:00"),
            ],
            0,
        )
        self.assertFalse(result["active"])
        self.assertTrue(result["cycle_ended"])
        self.assertEqual(0, result["confirmed_buy_round"])
        self.assertEqual(0, result["cumulative_filled_buy_amount"])

    def test_new_base_after_full_sell_starts_fresh_cycle(self) -> None:
        result = self._project(
            [
                self._order("Q1"),
                self._order("S1", side="SELL"),
                self._order("Q3"),
            ],
            [
                self._fill("Q1", cumulative=2, price=100, timestamp="2026-08-07 09:30:00"),
                self._fill("S1", cumulative=2, price=120, side="SELL", timestamp="2026-08-07 10:00:00"),
                self._fill("Q3", cumulative=1, price=150, timestamp="2026-08-07 13:00:00"),
            ],
            1,
            150,
        )
        self.assertTrue(result["active"])
        self.assertEqual(1, result["confirmed_buy_round"])
        self.assertEqual(150, result["cumulative_filled_buy_amount"])

    def test_existing_holding_without_fill_provenance_is_unresolved(self) -> None:
        result = self._project([], [], 4)
        self.assertEqual("unresolved", result["status"])
        self.assertEqual("FILL_POSITION_QUANTITY_MISMATCH", result["unresolved_reason"])

    def test_manual_order_mixed_into_active_cycle_is_unresolved(self) -> None:
        manual = self._order("M1", instance="")
        manual.pop("execution_intent")
        manual["routine_provenance"] = {}
        result = self._project(
            [self._order("Q1"), manual],
            [
                self._fill("Q1", cumulative=2, price=100, timestamp="2026-08-07 09:30:00"),
                self._fill("M1", cumulative=1, price=110, timestamp="2026-08-07 10:00:00"),
            ],
            3,
        )
        self.assertEqual("unresolved", result["status"])
        self.assertEqual("FOREIGN_ORDER_MIXED_IN_ACTIVE_CYCLE", result["unresolved_reason"])

    def test_other_routine_closed_history_is_not_attributed(self) -> None:
        result = self._project(
            [
                self._order("F1", instance="INSTANCE_B"),
                self._order("FS1", side="SELL", instance="INSTANCE_B"),
                self._order("Q1"),
            ],
            [
                self._fill("F1", cumulative=2, price=50, timestamp="2026-08-07 08:00:00"),
                self._fill("FS1", cumulative=2, price=60, side="SELL", timestamp="2026-08-07 08:30:00"),
                self._fill("Q1", cumulative=1, price=100, timestamp="2026-08-07 09:30:00"),
            ],
            1,
        )
        self.assertEqual("resolved", result["status"])
        self.assertEqual(1, result["confirmed_buy_round"])
        self.assertEqual(100, result["cumulative_filled_buy_amount"])

    def test_incomplete_buy_provenance_is_unresolved(self) -> None:
        order = self._order("Q1")
        order["execution_intent"].pop("buy_round")
        result = self._project([order], [self._fill("Q1", cumulative=1, price=100)], 1)
        self.assertEqual("unresolved", result["status"])
        self.assertEqual("BUY_PROVENANCE_INCOMPLETE", result["unresolved_reason"])

    def test_buy_exit_evidence_is_scoped_to_exact_cycle_and_process(self) -> None:
        def record(cycle: str, process: str) -> dict:
            signal_id = f"SIGNAL-{cycle}"
            return {
                "id": signal_id,
                "code": "005930",
                "routine_instance_id": "INSTANCE_A",
                "cycle_identity": cycle,
                "execution_intents": [{
                    "routine_instance_id": "INSTANCE_A",
                    "cycle_identity": cycle,
                    "execution_process_id": process,
                }],
                "buy_exit_evidence": {
                    "buy_phase_completed": True,
                    "routine_instance_id": "INSTANCE_A",
                    "cycle_identity": cycle,
                    "source_signal_id": signal_id,
                    "execution_process_id": process,
                },
            }

        old = record("CYCLE-A", "PROCESS-A")
        current = record("CYCLE-B", "PROCESS-B")
        matches = routine_module._matching_buy_exit_evidence(
            [old, current], code="005930", routine_instance_id="INSTANCE_A",
            cycle_identity="CYCLE-B",
        )
        self.assertEqual([current["buy_exit_evidence"]], matches)
        self.assertEqual([], routine_module._matching_buy_exit_evidence(
            [old], code="005930", routine_instance_id="INSTANCE_A",
            cycle_identity="CYCLE-B",
        ))
        self.assertEqual([], routine_module._matching_buy_exit_evidence(
            [current], code="005930", routine_instance_id="OTHER-INSTANCE",
            cycle_identity="CYCLE-B",
        ))


if __name__ == "__main__":
    unittest.main()
