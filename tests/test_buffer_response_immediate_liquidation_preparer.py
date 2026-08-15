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
from buffer_response_immediate_liquidation_preparer import (
    BufferResponseImmediateLiquidationPreparer,
    STATE_ALREADY_FLAT,
    STATE_BLOCKED,
    STATE_READY,
    STATE_WAITING_BUY_CANCEL,
    _main_window_cancel_requester,
    resume_main_window_buffer_immediate_liquidation_preparation,
)
from buffer_response_ingress_state_service import (
    BufferResponseIngressStateService,
    build_stable_buffer_observation,
)
from buffer_response_ownership_service import (
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_OWNED,
    BufferResponseOwnershipService,
)
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED


ACCOUNT = "81291234"
DAY = "2026-08-15"
CODE = "005930"
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


def _recovery() -> object:
    return SimpleNamespace(
        account_status=ACCOUNT_COMPLETED,
        identity=SimpleNamespace(account_no=ACCOUNT, trading_day=DAY),
        stocks=(
            SimpleNamespace(
                stock_code=CODE,
                stock_status=STOCK_RESTORED,
                review_required=False,
            ),
        ),
    )


def _buy(
    identity: str = "BUY-1",
    *,
    quantity: int = 100,
    remaining: int = 60,
    status: str = "PARTIALLY_FILLED",
) -> dict[str, object]:
    return {
        "id": f"ORDER_QUEUED_{identity}",
        "order_id": identity,
        "source_signal_id": f"SIGNAL_{identity}",
        "account_no": ACCOUNT,
        "code": CODE,
        "side": "BUY",
        "routine": ROUTINE,
        "created_at": "2026-08-15 09:30:00",
        "order_action": "NEW",
        "status": status,
        "broker_order_no": f"BROKER_{identity}",
        "quantity": quantity,
        "remaining_quantity": remaining,
    }


def _sell(identity: str = "SELL-1") -> dict[str, object]:
    return {
        **_buy(identity, quantity=10, remaining=10, status="BROKER_ACCEPTED"),
        "side": "SELL",
    }


def _cancel(source: dict[str, object], *, completed: bool = False) -> dict[str, object]:
    return {
        "id": f"ORDER_QUEUED_CANCEL_{source['order_id']}",
        "order_id": f"CANCEL_{source['order_id']}",
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


class BufferResponseImmediateLiquidationPreparerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.queue_path = self.runtime / "order_queue.json"
        self.positions_path = self.runtime / "positions.json"
        self.broker_path = self.runtime / "broker_holdings.json"
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
                "trade_started_at": "2026-08-15 09:00:00",
                "operation_command_mode": "NORMAL",
                "operation_command_id": "",
                "operation_command_source": "",
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "liquidation_policy_forced": False,
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
        self._write_holding(40, 40)
        self.ingress = BufferResponseIngressStateService(
            self.ingress_path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )
        self.ownership = BufferResponseOwnershipService(
            self.ownership_path,
            now_factory=lambda: "2026-08-15T10:30:00+09:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _write_orders(self, orders: list[dict[str, object]]) -> None:
        self._write_json(self.queue_path, {"revision": 1, "orders": orders})

    def _read_orders(self) -> list[dict[str, object]]:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"]

    def _write_holding(self, position_qty: int | None, broker_qty: int | None) -> None:
        positions = [] if position_qty is None else [{"code": CODE, "quantity": position_qty}]
        holdings = (
            []
            if broker_qty is None
            else [{"code": CODE, "holding_quantity": broker_qty}]
        )
        self._write_json(self.positions_path, {"positions": positions})
        self._write_json(self.broker_path, {"holdings": holdings})

    def _claim(self, intent: str = RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED) -> str:
        baseline = _observation(0, (), revision=1, marker="A")
        baseline_preview = self.ingress.preview_observation(baseline)
        self.assertTrue(baseline_preview["ok"], baseline_preview)
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
            response_intent=intent,
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

    def _preparer(self, requester=None) -> BufferResponseImmediateLiquidationPreparer:
        return BufferResponseImmediateLiquidationPreparer(
            ownership_service=self.ownership,
            ingress_service=self.ingress,
            project_root=self.root,
            order_queue_path=self.queue_path,
            positions_path=self.positions_path,
            broker_holdings_path=self.broker_path,
            cancel_requester=requester,
        )

    def _dispatch(self, event_id: str, requester=None) -> dict[str, object]:
        return self._preparer(requester).dispatch_event(
            event_id=event_id,
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )

    def test_no_buy_resolves_ready_flat_and_holding_mismatch(self) -> None:
        event_id = self._claim()
        requester = mock.Mock()
        ready = self._dispatch(event_id, requester)
        self.assertEqual(STATE_READY, ready["state"])
        self.assertEqual(40, ready["holding_quantity"])
        self.assertTrue(ready["ready_for_liquidation"])
        requester.assert_not_called()

        self._write_holding(0, 0)
        flat = self._dispatch(event_id, requester)
        self.assertEqual(STATE_ALREADY_FLAT, flat["state"])
        self.assertFalse(flat["ready_for_liquidation"])

        self._write_holding(10, 9)
        blocked = self._dispatch(event_id, requester)
        self.assertEqual(STATE_BLOCKED, blocked["state"])
        self.assertIn("conflict", blocked["reason"])

    def test_pending_buy_requests_cancel_then_waits_for_chejan(self) -> None:
        event_id = self._claim()
        buy = _buy()
        self._write_orders([buy])

        def queue_cancel(*_args, **kwargs):
            self.assertEqual("BUY_ONLY", kwargs["side_scope"])
            self.assertEqual(ACCOUNT, kwargs["account_no"])
            self._write_orders([buy, _cancel(buy)])
            return {"ok": True, "cancel_requested": 1, "cancel_pending": 0}

        result = self._dispatch(event_id, mock.Mock(side_effect=queue_cancel))
        self.assertEqual(STATE_WAITING_BUY_CANCEL, result["state"])
        self.assertEqual(1, result["cancel_requested"])
        self.assertEqual(60, result["remaining_buy_pending_qty"])
        self.assertFalse(result["holding_confirmed"])

    def test_cancel_complete_partial_fill_with_active_sell_stays_fail_closed(self) -> None:
        event_id = self._claim()
        buy = _buy()
        sell = _sell()
        sell_before = deepcopy(sell)
        self._write_orders([buy, sell])

        def complete_cancel(*_args, **_kwargs):
            completed_buy = {**buy, "status": "PARTIAL_CANCELLED", "remaining_quantity": 0}
            self._write_orders([completed_buy, sell, _cancel(buy, completed=True)])
            return {"ok": True, "cancel_requested": 1, "cancel_pending": 0}

        result = self._dispatch(event_id, mock.Mock(side_effect=complete_cancel))
        self.assertEqual(STATE_BLOCKED, result["state"])
        self.assertIn("ACTIVE_SELL_ORDER", result["reason"])
        saved_sell = next(item for item in self._read_orders() if item["side"] == "SELL")
        self.assertEqual(sell_before, saved_sell)

    def test_existing_cancel_is_idempotent_and_fresh_restart_resumes(self) -> None:
        event_id = self._claim()
        buy = _buy()
        self._write_orders([buy, _cancel(buy)])
        requester = mock.Mock(
            return_value={"ok": True, "cancel_requested": 0, "cancel_pending": 1}
        )
        first = self._dispatch(event_id, requester)
        second = self._dispatch(event_id, requester)
        self.assertEqual(STATE_WAITING_BUY_CANCEL, first["state"])
        self.assertEqual(STATE_WAITING_BUY_CANCEL, second["state"])
        self.assertEqual(0, first["cancel_requested"])
        self.assertEqual(1, first["cancel_already_pending"])

        completed_buy = {**buy, "status": "PARTIAL_CANCELLED", "remaining_quantity": 0}
        self._write_orders([completed_buy, _cancel(buy, completed=True)])
        fresh = self._preparer(requester).resume_owned_events(
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )
        self.assertTrue(fresh["ok"], fresh)
        self.assertEqual(1, fresh["attempted"])
        self.assertEqual(STATE_READY, fresh["results"][0]["state"])
        before = requester.call_count
        repeated = self._dispatch(event_id, requester)
        self.assertEqual(STATE_READY, repeated["state"])
        self.assertEqual(before, requester.call_count)
        ownership_event = self.ownership.read_snapshot()["snapshot"]["events"][event_id]
        self.assertEqual(STATUS_OWNED, ownership_event["status"])

    def test_early_close_is_not_handled_by_immediate_preparer(self) -> None:
        early_id = self._claim(RESPONSE_INTENT_EARLY_CLOSE)
        requester = mock.Mock()
        early = self._dispatch(early_id, requester)
        self.assertEqual(STATE_BLOCKED, early["state"])
        self.assertEqual(
            "EARLY_CLOSE_NOT_HANDLED_BY_IMMEDIATE_PREPARER", early["reason"]
        )
        requester.assert_not_called()

    def test_existing_close_conflict_blocks_before_buy_cancel(self) -> None:
        event_id = self._claim()
        requester = mock.Mock()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state.update({"status": "EARLY_CLOSE", "early_close_source": "OTHER"})
        self._write_json(self.state_path, state)
        self._write_orders([_buy()])
        conflict = self._dispatch(event_id, requester)
        self.assertEqual(STATE_BLOCKED, conflict["state"])
        self.assertIn("EXISTING_CLOSE_CONFLICT", conflict["reason"])
        requester.assert_not_called()

    def test_recovery_adapter_resumes_current_identity_only(self) -> None:
        identity = SimpleNamespace(account_no=ACCOUNT, trading_day=DAY)
        window = SimpleNamespace(
            selected_account_no=lambda: ACCOUNT,
            queue_pending_order_cancellations_for_stock_automatically=mock.Mock(),
        )
        preparer = mock.Mock()
        preparer.resume_owned_events.return_value = {"ok": True, "attempted": 1}
        with mock.patch(
            "buffer_response_immediate_liquidation_preparer.date"
        ) as date_mock, mock.patch(
            "buffer_response_immediate_liquidation_preparer.production_recovery_registry.snapshot",
            return_value=_recovery(),
        ), mock.patch(
            "buffer_response_immediate_liquidation_preparer.BufferResponseImmediateLiquidationPreparer",
            return_value=preparer,
        ):
            date_mock.today.return_value.isoformat.return_value = DAY
            result = resume_main_window_buffer_immediate_liquidation_preparation(
                window,
                recovery_identity=identity,
            )
        self.assertTrue(result["ok"], result)
        preparer.resume_owned_events.assert_called_once()

    def test_main_window_cancel_requester_reuses_existing_operation_host(self) -> None:
        requester = mock.Mock()
        host = SimpleNamespace(
            queue_pending_order_cancellations_for_stock_automatically=requester
        )
        window = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=lambda: host
        )
        self.assertIs(requester, _main_window_cancel_requester(window))

    def test_module_has_no_sell_executor_or_ownership_mutation(self) -> None:
        source = Path(__file__).parents[1].joinpath(
            "buffer_response_immediate_liquidation_preparer.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "SendOrder",
            "apply_close_intent",
            "apply_early_close_intent",
            "complete_event",
            "claim_batch_event_candidate",
            "IMMEDIATE_LIQUIDATION executor",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
