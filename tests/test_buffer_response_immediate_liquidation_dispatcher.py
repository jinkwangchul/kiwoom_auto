# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from buffer_response_coordinator import _source_evidence
from buffer_response_early_close_dispatcher import (
    buffer_response_command_source,
    deterministic_buffer_early_close_command_id,
)
from buffer_response_immediate_liquidation_dispatcher import (
    BufferResponseImmediateLiquidationDispatcher,
    MARKET_CLOSE_MODE,
    deterministic_buffer_immediate_market_close_command_id,
    dispatch_ready_main_window_buffer_immediate_preparations,
)
from buffer_response_immediate_liquidation_preparer import (
    STATE_READY,
    BufferResponseImmediateLiquidationPreparer,
)
from buffer_response_ingress_state_service import (
    BufferResponseIngressStateService,
    build_stable_buffer_observation,
)
from buffer_response_ownership_service import (
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_OWNED,
    BufferResponseOwnershipService,
)
from close_intent_service import apply_close_intent
from close_liquidation_transition_service import POLICY_ROUTINE_CLOSE
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED


ACCOUNT = "81291234"
DAY = "2026-08-15"
CODE = "005930"
OTHER_CODE = "000660"
ROUTINE = "routine-1"


def _evidence(revision: int, marker: str) -> dict[str, object]:
    return {
        "recovery_session_id": "RECOVERY-1",
        "queue_revision": revision,
        "order_queue_sha256": marker * 64,
        "positions_sha256": "B" * 64,
        "fills_sha256": "C" * 64,
    }


def _observation(
    amount: int,
    contributors: tuple[str, ...],
    *,
    revision: int,
    marker: str,
) -> dict[str, object]:
    evidence = _evidence(revision, marker)
    projected = build_stable_buffer_observation(
        account_no=ACCOUNT,
        trading_day=DAY,
        confirmed_entry_amount=amount,
        contributing_buy_ids=contributors,
        evidence_before=evidence,
        evidence_after=deepcopy(evidence),
        observed_at=f"2026-08-15T10:{revision:02d}:00+09:00",
    )
    assert projected["available"] is True, projected
    return projected["observation"]


def _recovery(status: str = ACCOUNT_COMPLETED) -> object:
    return SimpleNamespace(
        account_status=status,
        identity=SimpleNamespace(account_no=ACCOUNT, trading_day=DAY),
        stocks=(
            SimpleNamespace(
                stock_code=CODE,
                stock_status=STOCK_RESTORED,
                review_required=False,
            ),
        ),
    )


def _buy(*, remaining: int = 60, status: str = "PARTIALLY_FILLED") -> dict[str, object]:
    return {
        "id": "ORDER_QUEUED_BUY-1",
        "order_id": "BUY-1",
        "source_signal_id": "SIGNAL-BUY-1",
        "account_no": ACCOUNT,
        "code": CODE,
        "side": "BUY",
        "routine": ROUTINE,
        "created_at": "2026-08-15 09:30:00",
        "order_action": "NEW",
        "status": status,
        "broker_order_no": "BROKER-BUY-1",
        "quantity": 100,
        "remaining_quantity": remaining,
    }


def _cancel(source: dict[str, object], *, completed: bool = False) -> dict[str, object]:
    return {
        "id": "ORDER_QUEUED_CANCEL-BUY-1",
        "order_id": "CANCEL-BUY-1",
        "account_no": ACCOUNT,
        "code": CODE,
        "side": "BUY",
        "order_action": "CANCEL",
        "status": "CANCELLED" if completed else "ORDER_QUEUED",
        "original_order_effect_confirmed": completed,
        "execution_request": {
            "request_preview": {
                "account_no": ACCOUNT,
                "code": CODE,
                "side": "BUY",
                "order_action": "CANCEL",
                "original_order_no": source["broker_order_no"],
            }
        },
    }


def _sell() -> dict[str, object]:
    return {
        **_buy(remaining=10, status="BROKER_ACCEPTED"),
        "id": "ORDER_QUEUED_SELL-1",
        "order_id": "SELL-1",
        "source_signal_id": "SIGNAL-SELL-1",
        "side": "SELL",
        "broker_order_no": "BROKER-SELL-1",
        "quantity": 10,
    }


class BufferResponseImmediateLiquidationDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.queue_path = self.runtime / "order_queue.json"
        self.positions_path = self.runtime / "positions.json"
        self.broker_path = self.runtime / "broker_holdings.json"
        self.fills_path = self.runtime / "fills.json"
        self.ingress_path = self.runtime / "buffer_response_ingress_state.json"
        self.ownership_path = self.runtime / "buffer_response_ownership.json"
        self.stock_dir = self.root / "stocks" / f"{CODE}_Test"
        self.stock_dir.mkdir(parents=True)
        self.state_path = self.stock_dir / "state.json"
        self.config_path = self.stock_dir / "config.json"
        self._write_json(
            self.state_path,
            {
                "status": "RUNNING",
                "holding_qty": 100,
                "trade_started_at": "2026-08-15 09:00:00",
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
        self._write_json(
            self.config_path,
            {
                "enabled": True,
                "operation_excluded": False,
                "assigned_routine_instance_id": ROUTINE,
            },
        )
        self._write_json(self.stock_dir / "orders.json", {"orders": []})
        self._write_orders([])
        self._write_holding(100, 100)
        self._write_json(self.fills_path, {"fills": []})
        self.ingress = BufferResponseIngressStateService(
            self.ingress_path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )
        self.ownership = BufferResponseOwnershipService(
            self.ownership_path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )
        self.event_id = self._claim()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_orders(self, orders: list[dict[str, object]]) -> None:
        self._write_json(self.queue_path, {"revision": 1, "orders": orders})

    def _write_holding(self, position_qty: int | None, broker_qty: int | None) -> None:
        positions = [] if position_qty is None else [{"code": CODE, "quantity": position_qty}]
        holdings = (
            []
            if broker_qty is None
            else [{"code": CODE, "holding_quantity": broker_qty}]
        )
        self._write_json(self.positions_path, {"positions": positions})
        self._write_json(self.broker_path, {"holdings": holdings})

    def _claim(self) -> str:
        baseline = _observation(0, (), revision=1, marker="A")
        baseline_preview = self.ingress.preview_observation(baseline)
        self.assertTrue(
            self.ingress.commit_stable_observation(
                observation=baseline,
                expected_revision=baseline_preview["expected_revision"],
            )["ok"]
        )
        observation = _observation(100, ("O1",), revision=2, marker="D")
        preview = self.ingress.preview_observation(observation)
        claim = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=preview["event_sequence"],
            source_evidence=_source_evidence(observation, preview),
            selected_stock_code=CODE,
            response_intent=RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
            detected_at=observation["observed_at"],
            expected_revision=0,
        )
        self.assertTrue(claim["ok"], claim)
        committed = self.ingress.commit_event_observation(
            observation=observation,
            claimed_event=claim["event"],
            expected_revision=preview["expected_revision"],
        )
        self.assertTrue(committed["ok"], committed)
        return str(claim["event_id"])

    def _preparation(self, quantity: int = 100, **changes: object) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": True,
            "blocked": False,
            "state": STATE_READY,
            "event_id": self.event_id,
            "stock_code": CODE,
            "response_intent": RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
            "cancel_complete": True,
            "remaining_buy_pending_qty": 0,
            "holding_confirmed": True,
            "holding_quantity": quantity,
            "ready_for_liquidation": True,
            "reason": "",
        }
        result.update(changes)
        return result

    @staticmethod
    def _actual_backend(**kwargs):
        return apply_close_intent(
            **kwargs,
            transition_guard=None,
            operation_state_writer=lambda **_ignored: {"ok": True},
        )

    def _dispatcher(self, backend=None) -> BufferResponseImmediateLiquidationDispatcher:
        return BufferResponseImmediateLiquidationDispatcher(
            ownership_service=self.ownership,
            ingress_service=self.ingress,
            project_root=self.root,
            order_queue_path=self.queue_path,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_path,
            fills_path=self.fills_path,
            close_backend=backend or self._actual_backend,
        )

    def _dispatch(self, backend=None, preparation=None, recovery=None) -> dict[str, object]:
        return self._dispatcher(backend).dispatch_event(
            event_id=self.event_id,
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=recovery or _recovery(),
            preparation_result=preparation or self._preparation(),
        )

    def test_ready_applies_existing_market_early_close_and_reads_back(self) -> None:
        backend = mock.Mock(side_effect=self._actual_backend)
        with mock.patch("close_intent_service.observe_close_started"):
            result = self._dispatch(backend)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dispatch_requested"])
        self.assertTrue(result["read_back_verified"])
        self.assertEqual(MARKET_CLOSE_MODE, result["close_mode"])
        self.assertEqual(1, backend.call_count)
        self.assertEqual("시장가", backend.call_args.kwargs["requested_policy"])
        command_id = deterministic_buffer_immediate_market_close_command_id(self.event_id)
        self.assertEqual(command_id, result["command_id"])
        self.assertEqual(f"BUFFER_RESPONSE:{self.event_id}", result["source"])
        state = self._read_state()
        self.assertEqual("EARLY_CLOSE", state["operation_command_mode"])
        self.assertEqual(command_id, state["operation_command_id"])
        self.assertEqual("시장가", state["early_close_method"])
        self.assertTrue(state["liquidation_policy_forced"])
        ownership = self.ownership.read_snapshot()["snapshot"]["events"][self.event_id]
        self.assertEqual(STATUS_OWNED, ownership["status"])

    def test_same_event_routine_early_close_can_be_promoted_to_market_once(self) -> None:
        source = buffer_response_command_source(self.event_id)
        state = self._read_state()
        state.update(
            {
                "status": "EARLY_CLOSE",
                "operation_command_mode": "EARLY_CLOSE",
                "operation_command_id": deterministic_buffer_early_close_command_id(
                    self.event_id
                ),
                "operation_command_source": source,
                "early_close_source": source,
                "early_close_method": POLICY_ROUTINE_CLOSE,
                "early_close_requested_at": "2026-08-15T10:00:00+09:00",
            }
        )
        self._write_json(self.state_path, state)
        cancel_requester = mock.Mock()
        preparer = BufferResponseImmediateLiquidationPreparer(
            ownership_service=self.ownership,
            ingress_service=self.ingress,
            project_root=self.root,
            order_queue_path=self.queue_path,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_path,
            cancel_requester=cancel_requester,
        )
        prepared = preparer.dispatch_event(
            event_id=self.event_id,
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )
        self.assertEqual(STATE_READY, prepared["state"], prepared)
        cancel_requester.assert_not_called()

        backend = mock.Mock(side_effect=self._actual_backend)
        with mock.patch("close_intent_service.observe_close_started"):
            first = self._dispatch(backend, preparation=prepared)
            second = self._dispatch(backend, preparation=prepared)
        self.assertTrue(first["read_back_verified"], first)
        self.assertTrue(second["already_applied"], second)
        self.assertEqual(1, backend.call_count)
        self.assertEqual("시장가", self._read_state()["early_close_method"])

    def test_same_event_and_fresh_process_are_idempotent(self) -> None:
        backend = mock.Mock(side_effect=self._actual_backend)
        with mock.patch("close_intent_service.observe_close_started"):
            first = self._dispatch(backend)
            second = self._dispatch(backend)
            fresh = self._dispatcher(backend).dispatch_event(
                event_id=self.event_id,
                account_no=ACCOUNT,
                trading_day=DAY,
                recovery_context=_recovery(),
                preparation_result=self._preparation(),
            )
        self.assertTrue(first["dispatch_requested"])
        self.assertTrue(second["already_applied"])
        self.assertTrue(fresh["already_applied"])
        self.assertEqual(1, backend.call_count)

        resumed_preparation = BufferResponseImmediateLiquidationPreparer(
            ownership_service=self.ownership,
            ingress_service=self.ingress,
            project_root=self.root,
            order_queue_path=self.queue_path,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_path,
        ).resume_owned_events(
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )
        self.assertTrue(resumed_preparation["ok"], resumed_preparation)
        self.assertEqual(STATE_READY, resumed_preparation["results"][0]["state"])
        recovered = self._dispatcher(backend).dispatch_event(
            event_id=self.event_id,
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
            preparation_result=resumed_preparation["results"][0],
        )
        self.assertTrue(recovered["already_applied"])
        self.assertEqual(1, backend.call_count)

    def test_pending_buy_and_cancel_request_never_dispatch_market_close(self) -> None:
        backend = mock.Mock()
        buy = _buy()
        self._write_orders([buy])
        pending = self._dispatch(backend)
        self.assertFalse(pending["ok"])
        self.assertEqual(60, pending["buy_pending_qty"])
        backend.assert_not_called()

        self._write_orders([buy, _cancel(buy)])
        requested = self._dispatch(backend)
        self.assertFalse(requested["ok"])
        self.assertEqual(60, requested["buy_pending_qty"])
        backend.assert_not_called()

    def test_partial_fill_cancel_complete_dispatches_only_reconciled_40(self) -> None:
        completed_buy = {**_buy(), "status": "PARTIAL_CANCELLED", "remaining_quantity": 0}
        self._write_orders([completed_buy, _cancel(_buy(), completed=True)])
        self._write_holding(40, 40)
        state = self._read_state()
        state["holding_qty"] = 40
        self._write_json(self.state_path, state)
        backend = mock.Mock(side_effect=self._actual_backend)
        with mock.patch("close_intent_service.observe_close_started"):
            result = self._dispatch(backend, preparation=self._preparation(40))
        self.assertTrue(result["ok"], result)
        self.assertEqual(40, result["holding_quantity"])
        self.assertEqual(1, backend.call_count)

    def test_flat_mismatch_and_state_quantity_mismatch_are_fail_closed(self) -> None:
        backend = mock.Mock()
        self._write_holding(0, 0)
        flat = self._dispatch(backend)
        self.assertTrue(flat["ok"], flat)
        self.assertEqual("ALREADY_FLAT", flat["reason"])
        backend.assert_not_called()

        self._write_holding(10, 9)
        mismatch = self._dispatch(backend, preparation=self._preparation(10))
        self.assertFalse(mismatch["ok"])
        backend.assert_not_called()

        self._write_holding(40, 40)
        state_mismatch = self._dispatch(backend, preparation=self._preparation(40))
        self.assertFalse(state_mismatch["ok"])
        self.assertEqual(
            "STOCK_STATE_HOLDING_QUANTITY_MISMATCH", state_mismatch["reason"]
        )
        backend.assert_not_called()

    def test_existing_sell_auto_close_recovery_and_identity_conflicts_block(self) -> None:
        backend = mock.Mock()
        self._write_orders([_sell()])
        sell = self._dispatch(backend)
        self.assertIn("ACTIVE_SELL_ORDER", sell["reason"])
        backend.assert_not_called()

        self._write_orders([])
        state = self._read_state()
        state.update({"status": "AUTO_CLOSE", "auto_close_source": "OTHER"})
        self._write_json(self.state_path, state)
        auto_close = self._dispatch(backend)
        self.assertIn("EXISTING_CLOSE_CONFLICT", auto_close["reason"])
        backend.assert_not_called()

        state.update({"status": "RUNNING", "auto_close_source": ""})
        self._write_json(self.state_path, state)
        recovery = self._dispatch(backend, recovery=_recovery("FAILED"))
        self.assertFalse(recovery["ok"])
        other_stock = self._dispatch(
            backend,
            preparation=self._preparation(stock_code=OTHER_CODE),
        )
        self.assertEqual("PREPARATION_STOCK_IDENTITY_MISMATCH", other_stock["reason"])
        backend.assert_not_called()

    def test_resume_dispatches_only_ready_results_without_candidate_selection(self) -> None:
        window = SimpleNamespace(selected_account_no=lambda: ACCOUNT)
        ready = self._preparation()
        waiting = {**ready, "state": "WAITING_BUY_CANCEL", "ready_for_liquidation": False}
        with mock.patch(
            "buffer_response_immediate_liquidation_dispatcher.dispatch_main_window_buffer_immediate_market_close",
            return_value={"ok": True},
        ) as dispatch:
            result = dispatch_ready_main_window_buffer_immediate_preparations(
                window,
                preparation_resume_result={"ok": True, "results": (ready, waiting)},
                ownership_service=self.ownership,
                ingress_service=self.ingress,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(1, result["attempted"])
        dispatch.assert_called_once()

    def test_dispatcher_has_no_new_immediate_executor_cancel_or_send_order(self) -> None:
        source = Path(__file__).parents[1].joinpath(
            "buffer_response_immediate_liquidation_dispatcher.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "queue_pending_order_cancellations",
            "SendOrder",
            "claim_batch_event_candidate",
            "complete_event",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
