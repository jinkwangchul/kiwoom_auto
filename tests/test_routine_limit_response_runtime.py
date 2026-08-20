# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from buffer_response_ownership_service import BufferResponseOwnershipService
from close_liquidation_transition_service import POLICY_MARKET, POLICY_ROUTINE_CLOSE
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from routine_instance_registry import default_routine_limit_response_policy
from routine_limit_response_ownership_service import INTENT_IMMEDIATE, RoutineLimitResponseOwnershipService
from routine_limit_response_service import (
    RoutineLimitResponseCoordinator,
    routine_layer_allows_stock,
    routine_limit_early_command_id,
    routine_limit_immediate_command_id,
    routine_limit_source,
)


ACCOUNT = "81291234"
DAY = "2026-08-21"
ROUTINE = "routine-1"
CODE = "005930"


class RoutineLimitResponseRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.stock_dir = self.root / "stocks" / f"{CODE}_Test"
        self.runtime.mkdir(parents=True)
        self.stock_dir.mkdir(parents=True)
        self.positions = self.runtime / "positions.json"
        self.fills = self.runtime / "fills.json"
        self.queue = self.runtime / "order_queue.json"
        self.holdings = self.runtime / "broker_holdings.json"
        self.routine_ownership = RoutineLimitResponseOwnershipService(
            self.runtime / "routine_limit_response_ownership.json",
            now_factory=lambda: "2026-08-21T10:00:00+09:00",
        )
        self.buffer_ownership = BufferResponseOwnershipService(
            self.runtime / "buffer_response_ownership.json",
            now_factory=lambda: "2026-08-21T10:00:00+09:00",
        )
        self.policy = default_routine_limit_response_policy()
        self.instance = SimpleNamespace(
            instance_id=ROUTINE,
            buy_limit_enabled=True,
            buy_limit_amount=100,
            buy_limit_response_policy=self.policy,
        )
        self.context = SimpleNamespace(
            account_status=ACCOUNT_COMPLETED,
            identity=SimpleNamespace(account_no=ACCOUNT, trading_day=DAY),
            stocks=(SimpleNamespace(stock_code=CODE, stock_status=STOCK_RESTORED, review_required=False),),
        )
        self._write(self.stock_dir / "config.json", {"assigned_routine_instance_id": ROUTINE})
        self._write(self.stock_dir / "state.json", {"status": "RUNNING", "holding_qty": 1, "trade_started_at": "2026-08-21T09:00:00+09:00"})
        self._write(self.stock_dir / "orders.json", {"orders": []})
        self._write(self.queue, {"version": 1, "orders": []})
        self._write(self.holdings, {"broker_holdings": [{"code": CODE, "quantity": 1}]})
        self._write(self.fills, {"fills": []})
        self._set_position(101)
        self.close_calls = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _set_position(self, cost_basis: int, **extra: object) -> None:
        position = {
            "position_id": "POSITION-1",
            "account_no": ACCOUNT,
            "code": CODE,
            "position_status": "OPEN",
            "quantity": 1,
            "average_price": cost_basis,
            "cost_basis": cost_basis,
        }
        position.update(extra)
        self._write(self.positions, {"positions": [position]})

    def _close_backend(self, **kwargs):
        active = self.routine_ownership.active_event(account_no=ACCOUNT, trading_day=DAY, routine_instance_id=ROUTINE)
        self.assertIsNotNone(active.get("event"), "ownership must be durable before close action")
        self.close_calls.append(dict(kwargs))
        state = json.loads((self.stock_dir / "state.json").read_text(encoding="utf-8"))
        state.update(
            {
                "status": "EARLY_CLOSE",
                "operation_command_mode": "EARLY_CLOSE",
                "operation_command_id": kwargs["command_id"],
                "operation_command_source": kwargs["source"],
                "early_close_source": kwargs["source"],
                "early_close_method": kwargs["requested_policy"],
                "early_close_requested_at": kwargs["requested_at"],
            }
        )
        self._write(self.stock_dir / "state.json", state)
        return {"ok": True, "reason": ""}

    def _coordinator(self):
        return RoutineLimitResponseCoordinator(
            project_root=self.root,
            positions_path=self.positions,
            fills_path=self.fills,
            order_queue_path=self.queue,
            broker_holdings_path=self.holdings,
            ownership=self.routine_ownership,
            buffer_ownership=self.buffer_ownership,
            close_backend=self._close_backend,
            pnl_projector=lambda codes, **_kwargs: {
                code: {"available": True, "cumulative_profit": 10, "cumulative_rate": 1, "open_cost": 101}
                for code in codes
            },
            completion_projector=lambda: {
                "evaluated": True,
                "blocked": False,
                "evidence_errors": [],
                "stock_results": [{"stock_code": CODE, "status": "HOLDING_REMAINS", "reasons": ["holding remains"]}],
            },
            instance_reader=lambda routine_id: self.instance if routine_id == ROUTINE else None,
            participant_reader=lambda _window: [CODE],
            operation_policy_reader=lambda: {"early_close": {"method": "현재가", "offset_ticks": 1}},
            recovery_snapshot_reader=lambda: self.context,
        )

    @staticmethod
    def _buffer_clear():
        return {"stable": True, "ingress_committed": True, "event_created": False, "policy_projected": False, "ownership_claimed": False, "ownership_existing": False}

    @staticmethod
    def _trigger(side="BUY"):
        return {"position_committed": True, "side": side, "code": CODE, "execution_identity_source": "BROKER_EXECUTION", "execution_identity": "EXEC-1"}

    def test_normal_early_close_uses_environment_policy_and_stock_scope(self) -> None:
        result = self._coordinator().run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger())
        self.assertTrue(result["ownership_claimed"])
        self.assertTrue(result["early_close_requested"])
        self.assertEqual(1, len(self.close_calls))
        call = self.close_calls[0]
        self.assertEqual("현재가", call["requested_policy"])
        self.assertEqual("STOCK", call["target_scope"])
        self.assertEqual({"offset_ticks": 1}, call["extra_policy"])

    def test_buffer_uncertain_blocks_routine_and_routine_ownership_blocks_stock(self) -> None:
        blocked = self._coordinator().run(
            object(),
            buffer_result={"stable": False, "ingress_committed": False},
            trigger=self._trigger(),
        )
        self.assertTrue(blocked["higher_priority_blocked"])
        self.assertFalse(self.routine_ownership.path.exists())

        owned = self._coordinator().run(
            object(), buffer_result=self._buffer_clear(), trigger=self._trigger()
        )
        self.assertTrue(owned["owns_response"])
        self.assertFalse(routine_layer_allows_stock(owned))
        self.assertTrue(routine_layer_allows_stock({"settled": True, "owns_response": False}))

    def test_direct_immediate_claims_before_market_close(self) -> None:
        self.policy["strategies"]["unified"]["response_mode"] = "즉시청산"
        result = self._coordinator().run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger())
        self.assertTrue(result["ownership_claimed"])
        self.assertTrue(result["immediate_dispatch_requested"])
        event_id = result["event_id"]
        self.assertEqual(POLICY_MARKET, self.close_calls[0]["requested_policy"])
        self.assertEqual(routine_limit_immediate_command_id(event_id), self.close_calls[0]["command_id"])
        self.assertEqual(routine_limit_source(event_id), self.close_calls[0]["source"])

    def test_immediate_does_not_close_before_buy_cancel_completion(self) -> None:
        self.policy["strategies"]["unified"]["response_mode"] = "즉시청산"
        self._write(
            self.queue,
            {
                "version": 1,
                "orders": [{
                    "id": "ORDER_QUEUED_BUY-1",
                    "order_id": "BUY-1",
                    "account_no": ACCOUNT,
                    "code": CODE,
                    "side": "BUY",
                    "order_action": "NEW",
                    "status": "PARTIALLY_FILLED",
                    "broker_order_no": "BROKER_BUY-1",
                    "quantity": 10,
                    "remaining_quantity": 5,
                }],
            },
        )
        result = self._coordinator().run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger())
        self.assertTrue(result["ownership_claimed"])
        self.assertEqual("BUY_CANCEL_REQUESTER_UNAVAILABLE", result["reason"])
        self.assertEqual([], self.close_calls)

    def test_immediate_does_not_close_with_uncertain_holding(self) -> None:
        self.policy["strategies"]["unified"]["response_mode"] = "즉시청산"
        self._write(self.holdings, {"broker_holdings": []})
        result = self._coordinator().run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger())
        self.assertTrue(result["ownership_claimed"])
        self.assertEqual("BLOCKED_HOLDING_UNCERTAIN", result["reason"])
        self.assertEqual([], self.close_calls)

    def test_segment_early_promotes_same_event_and_selected_stock(self) -> None:
        self.policy["strategies"]["unified"]["response_mode"] = "구간마감"
        self._set_position(95)
        coordinator = self._coordinator()
        first = coordinator.run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger())
        self.assertEqual(POLICY_ROUTINE_CLOSE, self.close_calls[0]["requested_policy"])
        self.assertEqual(routine_limit_early_command_id(first["event_id"]), self.close_calls[0]["command_id"])

        self._set_position(101)
        second = coordinator.run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger(side="SELL"))
        self.assertTrue(second["ownership_promoted"])
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["selected_stock_code"], second["selected_stock_code"])
        self.assertEqual(POLICY_MARKET, self.close_calls[1]["requested_policy"])
        snapshot = self.routine_ownership.read_snapshot()["snapshot"]
        self.assertEqual(INTENT_IMMEDIATE, snapshot["events"][first["event_id"]]["response_intent"])

    def test_only_same_routine_event_early_evidence_can_promote(self) -> None:
        coordinator = self._coordinator()
        event_id = "EVENT-1"
        canonical = {
            "operation_command_mode": "EARLY_CLOSE",
            "operation_command_source": routine_limit_source(event_id),
            "operation_command_id": routine_limit_early_command_id(event_id),
            "early_close_method": POLICY_ROUTINE_CLOSE,
        }
        self.assertTrue(coordinator._same_event_early(canonical, event_id))
        for source in ("BUFFER_RESPONSE:OTHER", "STOCK_LIMIT:OTHER", "OPERATOR", "ROUTINE_LIMIT_RESPONSE:OTHER"):
            with self.subTest(source=source):
                state = dict(canonical, operation_command_source=source)
                self.assertFalse(coordinator._same_event_early(state, event_id))
        self.assertFalse(
            coordinator._same_event_early(
                dict(canonical, operation_command_id="OTHER-COMMAND"), event_id
            )
        )

    def test_active_event_completes_after_settings_are_disabled(self) -> None:
        coordinator = self._coordinator()
        first = coordinator.run(object(), buffer_result=self._buffer_clear(), trigger=self._trigger())
        self.instance.buy_limit_enabled = False
        self.instance.buy_limit_amount = None
        coordinator.completion_projector = lambda: {
            "evaluated": True,
            "blocked": False,
            "evidence_errors": [],
            "stock_results": [{"stock_code": CODE, "status": "DONE", "reasons": []}],
        }
        completed = coordinator.run(
            object(), buffer_result=self._buffer_clear(), trigger=self._trigger(side="SELL")
        )
        self.assertEqual(first["event_id"], completed["event_id"])
        self.assertTrue(completed["ownership_completed"])
        self.assertTrue(routine_layer_allows_stock(completed))

    def test_recovery_reconstructs_only_proven_crossing_fill(self) -> None:
        self.policy["strategies"]["unified"]["response_mode"] = "구간마감"
        self._set_position(
            95,
            last_fill_identity_source="BROKER_EXECUTION",
            last_fill_identity="EXEC-RECOVERY",
            last_applied_fill_delta=6,
        )
        self._write(
            self.fills,
            {"fills": [{"routine_instance_id": ROUTINE, "side": "BUY", "code": CODE, "execution_identity_source": "BROKER_EXECUTION", "execution_identity": "EXEC-RECOVERY", "filled_price": 1, "received_at": "2026-08-21T09:59:00+09:00", "fill_id": "FILL-1"}]},
        )
        coordinator = self._coordinator()
        snapshot = coordinator._snapshot(object(), context=self.context, account=ACCOUNT, routine_id=ROUTINE)
        evidence = coordinator._reconstruct_trigger(ROUTINE, snapshot, snapshot["projection"])
        self.assertTrue(evidence["ok"])
        self.assertEqual("EXEC-RECOVERY", evidence["identity"])

        self._set_position(
            95,
            last_fill_identity_source="BROKER_EXECUTION",
            last_fill_identity="EXEC-RECOVERY",
            last_applied_fill_delta=1,
        )
        snapshot = coordinator._snapshot(object(), context=self.context, account=ACCOUNT, routine_id=ROUTINE)
        blocked = coordinator._reconstruct_trigger(ROUTINE, snapshot, snapshot["projection"])
        self.assertFalse(blocked["ok"])
        self.assertEqual("RECOVERY_TRIGGER_CROSSING_NOT_PROVEN", blocked["reason"])

    def test_main_window_live_calls_buffer_then_routine_then_stock(self) -> None:
        import buffer_response_coordinator as buffer_module
        import gui_windows

        calls = []
        main = SimpleNamespace(
            auto_trade_setting_window=None,
            kiwoom_api=None,
            account_combo=None,
            _production_recovery_identity=None,
            _main_budget_orderable_valid=False,
        )
        buffer_module.register_main_window_buffer_response_integration(main)
        self.addCleanup(buffer_module._INTEGRATION_READY_WINDOW_IDS.discard, id(main))

        def handle(*_args, **_kwargs):
            calls.append("chejan")
            return {"recorded": True, "stage": "chejan_record", "position_result": {"position_committed": True, "side": "BUY", "code": CODE}}

        with mock.patch.object(gui_windows, "handle_kiwoom_raw_chejan_event", side_effect=handle), mock.patch.object(
            gui_windows, "coordinate_main_window_buffer_response", side_effect=lambda *_args, **_kwargs: calls.append("buffer") or {"stable": True}
        ), mock.patch.object(
            gui_windows, "evaluate_main_window_routine_limit_after_chejan", side_effect=lambda *_args, **_kwargs: calls.append("routine") or {"settled": True}
        ), mock.patch.object(
            gui_windows, "evaluate_main_window_stock_limit_after_chejan", side_effect=lambda *_args, **_kwargs: calls.append("stock") or {"evaluated": True}
        ):
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(main, {"gubun": "0"})
        self.assertEqual(["chejan", "buffer", "routine", "stock"], calls)

    def test_main_window_recovery_calls_buffer_resumes_then_routine_then_stock(self) -> None:
        import gui_windows

        calls = []
        main = SimpleNamespace()
        identity = SimpleNamespace(account_no=ACCOUNT, trading_day=DAY)
        patches = (
            mock.patch.object(gui_windows, "main_window_buffer_response_integration_ready", return_value=True),
            mock.patch.object(gui_windows, "coordinate_main_window_buffer_response", side_effect=lambda *_args, **_kwargs: calls.append("buffer-coordinate") or {"stable": True}),
            mock.patch.object(gui_windows, "resume_main_window_buffer_early_close", side_effect=lambda *_args, **_kwargs: calls.append("buffer-early") or {}),
            mock.patch.object(gui_windows, "resume_main_window_buffer_immediate_liquidation_preparation", side_effect=lambda *_args, **_kwargs: calls.append("buffer-prepare") or {}),
            mock.patch.object(gui_windows, "dispatch_ready_main_window_buffer_immediate_preparations", side_effect=lambda *_args, **_kwargs: calls.append("buffer-dispatch") or {}),
            mock.patch.object(gui_windows, "resume_main_window_routine_limit_responses", side_effect=lambda *_args, **_kwargs: calls.append("routine") or {"settled": True}),
            mock.patch.object(gui_windows, "resume_main_window_stock_limit_responses", side_effect=lambda *_args, **_kwargs: calls.append("stock") or {}),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            gui_windows.MainWindow._resume_limit_responses_after_recovery(main, identity)
        self.assertEqual(
            ["buffer-coordinate", "buffer-early", "buffer-prepare", "buffer-dispatch", "routine", "stock"],
            calls,
        )


if __name__ == "__main__":
    unittest.main()
