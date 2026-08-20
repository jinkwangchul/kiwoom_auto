from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest

from close_intent_service import apply_close_intent
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import RecoveryAccountContext, RecoveryStockContext
from production_recovery_contract import RecoverySessionIdentity
from stock_limit_response_service import (
    StockLimitResponseService,
    stock_limit_event_id,
)


@dataclass
class _Ownership:
    codes: tuple[str, ...] = ()
    ok: bool = True

    def active_owned_stock_codes(self, **_kwargs):
        return {
            "ok": self.ok,
            "reason": "" if self.ok else "ownership damaged",
            "stock_codes": self.codes,
        }


class StockLimitResponseServiceTest(unittest.TestCase):
    account = "12345678"
    day = "2026-08-21"
    code = "005930"

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.runtime = self.root / "runtime"
        self.stock_dir = self.root / "stocks" / f"{self.code}_Samsung"
        self.runtime.mkdir(parents=True)
        self.stock_dir.mkdir(parents=True)
        self.positions_path = self.runtime / "positions.json"
        self.queue_path = self.runtime / "order_queue.json"
        self.fills_path = self.runtime / "fills.json"
        self.ownership = _Ownership()
        self.backend_calls: list[dict[str, object]] = []
        self._write_json(self.queue_path, {"revision": 0, "orders": []})
        self._write_json(self.fills_path, {"fills": []})
        self._write_config(enabled=True, amount=1_000)
        self._write_json(self.stock_dir / "state.json", {"status": "RUNNING"})
        self._write_position(cost_basis=900)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def _write_config(self, *, enabled: bool, amount: object) -> None:
        self._write_json(
            self.stock_dir / "config.json",
            {
                "code": self.code,
                "buy_limit_enabled": enabled,
                "buy_limit_amount": amount,
                "assigned_routine_instance_id": "routine-1",
                "operation_excluded": False,
            },
        )

    def _position(self, *, cost_basis: object, identity: str = "exec-1", quantity: int = 10):
        return {
            "position_id": f"POSITION_KIWOOM_{self.account}_{self.code}",
            "broker": "KIWOOM",
            "account_no": self.account,
            "code": self.code,
            "quantity": quantity,
            "average_price": 100,
            "cost_basis": cost_basis,
            "position_status": "OPEN" if quantity > 0 else "CLOSED",
            "last_fill_identity_source": "BROKER_EXECUTION",
            "last_fill_identity": identity,
            "last_fill_at": "2026-08-21T10:00:00",
        }

    def _write_position(self, *, cost_basis: object, identity: str = "exec-1", quantity: int = 10) -> None:
        self._write_json(
            self.positions_path,
            {"positions": [self._position(cost_basis=cost_basis, identity=identity, quantity=quantity)]},
        )

    def _recovery(self, *, account: str | None = None, restored: bool = True):
        identity = RecoverySessionIdentity(
            login_session_id="login-1",
            account_no=account or self.account,
            trading_day=self.day,
            recovery_session_id="recovery-1",
            requested_at="2026-08-21T09:00:00",
        )
        return RecoveryAccountContext(
            identity=identity,
            account_status=ACCOUNT_COMPLETED,
            created_at="2026-08-21T09:00:00",
            updated_at="2026-08-21T09:01:00",
            stocks=(
                RecoveryStockContext(
                    stock_code=self.code,
                    stock_status=STOCK_RESTORED if restored else "REVIEW_REQUIRED",
                    review_required=not restored,
                    reason_codes=(),
                    updated_at="2026-08-21T09:01:00",
                ),
            ),
        )

    def _policy(self):
        return {
            "early_close": {
                "method": "현재가즉시",
                "profit_percent": "3",
                "loss_percent": "2",
            }
        }

    def _backend(self, **kwargs):
        self.backend_calls.append(kwargs)
        state_path = Path(str(kwargs["target_id"])) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(
            {
                "status": "EARLY_CLOSE",
                "operation_command_mode": "EARLY_CLOSE",
                "operation_command_id": kwargs["command_id"],
                "operation_command_source": kwargs["source"],
                "early_close_source": kwargs["source"],
                "early_close_method": kwargs["requested_policy"],
            }
        )
        self._write_json(state_path, state)
        return {"ok": True, "command_result": object()}

    def _service(self, *, ownership=None, backend=None):
        return StockLimitResponseService(
            project_root=self.root,
            positions_path=self.positions_path,
            order_queue_path=self.queue_path,
            fills_path=self.fills_path,
            ownership_service=ownership or self.ownership,
            policy_reader=self._policy,
            close_backend=backend or self._backend,
        )

    def _evaluate(self, **updates):
        values = {
            "account_no": self.account,
            "trading_day": self.day,
            "stock_code": self.code,
            "recovery_context": self._recovery(),
        }
        values.update(updates)
        return self._service().evaluate_stock(**values)

    def test_disabled_waiting_below_and_equal_limits_do_not_close(self) -> None:
        self._write_config(enabled=False, amount=None)
        self.assertEqual("STOCK_LIMIT_DISABLED", self._evaluate()["reason"])
        self._write_config(enabled=True, amount=None)
        self.assertEqual("STOCK_LIMIT_WAITING_OR_INVALID", self._evaluate()["reason"])
        self._write_config(enabled=True, amount=1_000)
        self._write_position(cost_basis=999)
        self.assertEqual("LIMIT_NOT_EXCEEDED", self._evaluate()["reason"])
        self._write_position(cost_basis=1_000)
        equal = self._evaluate()
        self.assertFalse(equal["overrun"])
        self.assertFalse(self.backend_calls)

    def test_only_committed_cost_basis_strictly_above_limit_closes(self) -> None:
        self._write_position(cost_basis=1_001)
        queue_before = self.queue_path.read_bytes()
        result = self._evaluate()
        self.assertTrue(result["overrun"])
        self.assertTrue(result["early_close_requested"])
        self.assertEqual(queue_before, self.queue_path.read_bytes())
        call = self.backend_calls[0]
        self.assertEqual("EARLY_CLOSE", call["intent"])
        self.assertEqual("STOCK", call["target_scope"])
        self.assertEqual("현재가", call["requested_policy"])
        self.assertEqual({"profit_percent": "3", "loss_percent": "2"}, call["extra_policy"])
        self.assertTrue(str(call["source"]).startswith("STOCK_LIMIT:"))

    def test_overrun_uses_actual_canonical_operation_command_writer(self) -> None:
        self._write_position(cost_basis=1_001)
        self._write_json(
            self.stock_dir / "state.json",
            {
                "status": "RUNNING",
                "holding_qty": 10,
                "operation_sequence": 0,
                "operation_command_mode": "NORMAL",
                "operation_command_id": "",
                "operation_command_source": "",
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "liquidation_policy_forced": False,
                "liquidation_policy_reason": "",
                "close_routine_final_sell_ordered": False,
                "review_required": False,
            },
        )

        def actual_backend(**kwargs):
            return apply_close_intent(
                **kwargs,
                transition_guard=None,
                operation_state_writer=lambda **_ignored: {"ok": True},
            )

        result = self._service(backend=actual_backend).evaluate_stock(
            account_no=self.account,
            trading_day=self.day,
            stock_code=self.code,
            recovery_context=self._recovery(),
        )
        self.assertTrue(result["early_close_requested"], result)
        state = json.loads((self.stock_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("EARLY_CLOSE", state["operation_command_mode"])
        self.assertEqual(result["command_id"], state["operation_command_id"])
        self.assertEqual(result["source"], state["operation_command_source"])

    def test_market_value_pending_buy_and_other_stock_do_not_affect_principal(self) -> None:
        self._write_position(cost_basis=900)
        positions = json.loads(self.positions_path.read_text(encoding="utf-8"))
        positions["positions"][0].update({"current_price": 500, "market_value": 5_000})
        positions["positions"].append(
            {
                **self._position(cost_basis=99_000, identity="other"),
                "position_id": "POSITION_KIWOOM_12345678_000660",
                "code": "000660",
            }
        )
        self._write_json(self.positions_path, positions)
        self._write_json(
            self.queue_path,
            {
                "revision": 1,
                "orders": [
                    {
                        "account_no": self.account,
                        "code": self.code,
                        "side": "BUY",
                        "status": "BROKER_ACCEPTED",
                        "quantity": 100,
                        "price": 1_000,
                    }
                ],
            },
        )
        result = self._evaluate()
        self.assertEqual(900, result["invested_principal"])
        self.assertFalse(result["overrun"])
        self.assertFalse(self.backend_calls)

    def test_partial_sell_reduced_cost_basis_and_full_close_do_not_close(self) -> None:
        self._write_position(cost_basis=800, identity="sell-partial", quantity=8)
        self.assertEqual("LIMIT_NOT_EXCEEDED", self._evaluate()["reason"])
        self._write_position(cost_basis=0, identity="sell-full", quantity=0)
        self.assertEqual("NO_OPEN_HOLDING", self._evaluate()["reason"])
        self.assertFalse(self.backend_calls)

    def test_buffer_ownership_has_priority_and_unavailable_ownership_fails_closed(self) -> None:
        self._write_position(cost_basis=1_100)
        owned = self._service(ownership=_Ownership((self.code,))).evaluate_stock(
            account_no=self.account,
            trading_day=self.day,
            stock_code=self.code,
            recovery_context=self._recovery(),
        )
        self.assertTrue(owned["higher_priority_blocked"])
        self.assertEqual("BUFFER_RESPONSE_OWNS_STOCK", owned["reason"])
        unavailable = self._service(ownership=_Ownership(ok=False)).evaluate_stock(
            account_no=self.account,
            trading_day=self.day,
            stock_code=self.code,
            recovery_context=self._recovery(),
        )
        self.assertTrue(unavailable["higher_priority_blocked"])
        self.assertFalse(self.backend_calls)

    def test_existing_close_and_same_event_are_idempotent(self) -> None:
        self._write_position(cost_basis=1_100)
        first = self._evaluate()
        second = self._evaluate()
        self.assertTrue(first["early_close_requested"])
        self.assertTrue(second["already_applied"])
        self.assertEqual(1, len(self.backend_calls))

        self._write_json(
            self.stock_dir / "state.json",
            {"status": "EARLY_CLOSING"},
        )
        self._write_position(cost_basis=1_200, identity="exec-2")
        conflict = self._evaluate()
        self.assertTrue(conflict["existing_close_blocked"])
        self.assertEqual(1, len(self.backend_calls))

    def test_new_position_lifecycle_uses_new_deterministic_identity(self) -> None:
        first = stock_limit_event_id(self._position(cost_basis=1_100, identity="entry-one"))
        second = stock_limit_event_id(self._position(cost_basis=1_100, identity="entry-two"))
        self.assertTrue(first)
        self.assertNotEqual(first, second)
        self.assertEqual(first, stock_limit_event_id(self._position(cost_basis=1_100, identity="entry-one")))

    def test_recovery_identity_stock_and_corrupt_cost_basis_fail_closed(self) -> None:
        self._write_position(cost_basis=1_100)
        mismatch = self._evaluate(recovery_context=self._recovery(account="99999999"))
        self.assertEqual("RECOVERY_INCOMPLETE_OR_IDENTITY_MISMATCH", mismatch["reason"])
        unreconciled = self._evaluate(recovery_context=self._recovery(restored=False))
        self.assertEqual("RECOVERY_STOCK_NOT_RESTORED", unreconciled["reason"])
        self._write_position(cost_basis="damaged")
        damaged = self._evaluate()
        self.assertEqual("POSITION_COST_BASIS_INVALID", damaged["reason"])
        self.assertFalse(self.backend_calls)

    def test_gui_live_and_recovery_order_buffer_before_stock_limit(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gui_windows.py").read_text(encoding="utf-8")
        live_start = source.index("def on_kiwoom_raw_chejan_received")
        live_buffer = source.index("coordinate_main_window_buffer_response(", live_start)
        live_stock = source.index("evaluate_main_window_stock_limit_after_chejan(", live_start)
        self.assertLess(live_buffer, live_stock)
        recovery_start = source.index("def _finish_production_recovery")
        recovery_buffer = source.index("coordinate_main_window_buffer_response(", recovery_start)
        recovery_stock = source.index("resume_main_window_stock_limit_responses(", recovery_start)
        self.assertLess(recovery_buffer, recovery_stock)


if __name__ == "__main__":
    unittest.main()
