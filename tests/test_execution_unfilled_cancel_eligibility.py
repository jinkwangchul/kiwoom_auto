# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
)
from execution_unfilled_cancel_eligibility import (
    inspect_unfilled_sell_cancel_eligibility,
)
from chejan_event_recorder import _apply_acceptance
import gui_auto_trade_timer


ACCOUNT = "81291234"
CODE = "005930"
PROCESS = "EXEC_PROCESS_SELL_1"
SIGNAL = "SIGNAL_SELL_1"
ACCEPTED_AT = "2026-09-03T10:00:00"


def _policy(*, scope: str = "EACH", timeout_ms: int = 20_000) -> dict[str, object]:
    return {
        "policy": "CANCEL_PENDING_ORDER",
        "scope": scope,
        "timeout_ms": timeout_ms,
        "configured_value": 20,
        "configured_unit": "초",
        "anchor": "BROKER_ACCEPTED_AT",
    }


def _order(
    identity: str = "SELL_1",
    *,
    status: str = "BROKER_ACCEPTED",
    remaining: int = 10,
    child_kind: str = "SINGLE_ORDER",
    scope: str = "EACH",
    broker_order_no: str | None = None,
    accepted_at: str | None = ACCEPTED_AT,
    process_id: str = PROCESS,
) -> dict[str, object]:
    broker_no = f"BROKER_{identity}" if broker_order_no is None else broker_order_no
    intent = {
        "side": "SELL",
        "routine_type": "INDICATOR_FOLLOW",
        "routine_instance_id": "INSTANCE_1",
        "source_signal_id": SIGNAL,
        "execution_process_id": process_id,
        "child_kind": child_kind,
        "unfilled_timeout_policy": _policy(scope=scope),
    }
    record: dict[str, object] = {
        "id": f"ORDER_QUEUED_{identity}",
        "order_id": identity,
        "source_signal_id": SIGNAL,
        "execution_process_id": process_id,
        "execution_id": f"EXEC_{identity}",
        "option_snapshot_hash": "HASH_1",
        "status": status,
        "broker_order_no": broker_no,
        "broker_accepted_at": accepted_at,
        "remaining_quantity": remaining,
        "quantity": 10,
        "account_no": ACCOUNT,
        "code": CODE,
        "side": "SELL",
        "routine": "INSTANCE_1",
        "order_action": "NEW",
        "execution_intent": intent,
        "execution_request": {
            "execution_intent": deepcopy(intent),
            "routine_provenance": {"routine_instance_id": "INSTANCE_1"},
        },
    }
    if status == "SEND_UNCERTAIN":
        record["manual_reconciliation_required"] = True
    return record


def _cancel(source: dict[str, object], *, status: str = "ORDER_QUEUED", confirmed: bool = False) -> dict[str, object]:
    return {
        "id": f"ORDER_QUEUED_CANCEL_{source['order_id']}",
        "order_id": f"CANCEL_{source['order_id']}",
        "status": status,
        "account_no": source["account_no"],
        "code": source["code"],
        "side": source["side"],
        "order_action": "CANCEL",
        "original_order_effect_confirmed": confirmed,
        "execution_request": {
            "request_preview": {
                "order_action": "CANCEL",
                "original_order_no": source["broker_order_no"],
            }
        },
    }


class ExecutionUnfilledCancelEligibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue_path = self.root / "order_queue.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, orders: list[dict[str, object]]) -> None:
        self.queue_path.write_text(
            json.dumps(
                {"version": 1, "revision": 0, "updated_at": "", "orders": orders},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _inspect(self, at: str, *, limit: int = 5) -> dict[str, object]:
        return inspect_unfilled_sell_cancel_eligibility(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=(CODE,),
            now=datetime.fromisoformat(at),
            limit=limit,
            order_queue_path=self.queue_path,
        )

    def test_before_exact_and_after_timeout_boundary(self) -> None:
        self._write([_order()])

        before = self._inspect("2026-09-03T10:00:19.999")
        exact = self._inspect("2026-09-03T10:00:20")
        after = self._inspect("2026-09-03T10:00:21")

        self.assertEqual([], before["proposals"])
        self.assertEqual("TIMEOUT_NOT_REACHED", before["waiting"][0]["reason"])
        self.assertEqual(1, len(exact["proposals"]))
        self.assertEqual(1, len(after["proposals"]))
        self.assertEqual("BROKER_ACCEPTED_AT", exact["proposals"][0]["timeout_anchor"])

    def test_terminal_zero_rejected_and_uncertain_orders_are_not_cancelled(self) -> None:
        orders = [
            _order("FILLED", status="FILLED", remaining=0),
            _order("ZERO", remaining=0),
            _order("CANCELLED", status="CANCELLED", remaining=0),
            _order("REJECTED", status="BROKER_REJECTED", remaining=10),
            _order("UNCERTAIN", status="SEND_UNCERTAIN", remaining=10),
        ]
        self._write(orders)

        result = self._inspect("2026-09-03T10:01:00")

        self.assertEqual([], result["proposals"])
        reasons = " ".join(
            reason
            for review in result["reviews"]
            for reason in review["review_reasons"]
        )
        self.assertIn("UNFILLED_TIMEOUT_ORDER_IDENTITY_UNCERTAIN", reasons)

    def test_partial_fill_uses_latest_remaining_quantity(self) -> None:
        self._write([_order(status="PARTIALLY_FILLED", remaining=5)])

        result = self._inspect("2026-09-03T10:00:20")

        self.assertEqual(5, result["proposals"][0]["remaining_quantity"])

    def test_nested_request_identity_and_anchor_fallbacks_match_production_queue_shape(self) -> None:
        nested = _order()
        preview = {
            "account_no": nested.pop("account_no"),
            "code": nested.pop("code"),
            "side": nested.pop("side"),
            "order_action": nested.pop("order_action"),
        }
        nested["execution_request"]["request_preview"] = preview
        nested["broker_accepted_at"] = None
        nested["chejan_events"] = [
            {"event_type": "ORDER_ACCEPTED", "received_at": ACCEPTED_AT}
        ]
        self._write([nested])

        result = self._inspect("2026-09-03T10:00:20")

        self.assertEqual(1, len(result["proposals"]))
        self.assertEqual("CHEJAN_ORDER_RECEIVED_AT", result["proposals"][0]["timeout_anchor"])

    def test_missing_broker_identity_or_anchor_requires_reconciliation(self) -> None:
        missing_broker = _order("NO_BROKER", broker_order_no="")
        missing_anchor = _order("NO_ANCHOR", accepted_at=None)
        self._write([missing_broker, missing_anchor])

        result = self._inspect("2026-09-03T10:01:00")

        self.assertEqual([], result["proposals"])
        reasons = " ".join(
            reason
            for review in result["reviews"]
            for reason in review["review_reasons"]
        )
        self.assertIn("UNFILLED_TIMEOUT_BROKER_ORDER_NO_MISSING", reasons)
        self.assertIn("UNFILLED_TIMEOUT_BROKER_ACCEPTED_AT_MISSING", reasons)

    def test_active_cancel_and_uncertain_cancel_never_duplicate(self) -> None:
        source = _order()
        self._write([source, _cancel(source)])
        active = self._inspect("2026-09-03T10:01:00")
        self.assertEqual([], active["proposals"])
        self.assertEqual("ACTIVE_CANCEL_EXISTS", active["waiting"][0]["reason"])

        self._write([source, _cancel(source, status="SEND_UNCERTAIN")])
        uncertain = self._inspect("2026-09-03T10:01:00")
        self.assertEqual([], uncertain["proposals"])
        self.assertIn(
            "UNFILLED_TIMEOUT_CANCEL_SEND_UNCERTAIN",
            uncertain["reviews"][0]["review_reasons"],
        )

    def test_confirmed_cancel_completion_and_restart_are_idempotent(self) -> None:
        source = _order(status="CANCELLED", remaining=0)
        cancel = _cancel(source, status="CANCELLED", confirmed=True)
        self._write([source, cancel])

        first = self._inspect("2026-09-03T10:01:00")
        restarted = self._inspect("2026-09-03T10:02:00")

        self.assertEqual([], first["proposals"])
        self.assertEqual([], restarted["proposals"])

    def test_restart_timeout_open_order_remains_eligible(self) -> None:
        self._write([_order()])
        first = self._inspect("2026-09-03T10:00:20")
        restarted = self._inspect("2026-09-03T10:05:00")
        self.assertEqual("ORDER_QUEUED_SELL_1", first["proposals"][0]["order_queued_id"])
        self.assertEqual("ORDER_QUEUED_SELL_1", restarted["proposals"][0]["order_queued_id"])

    def test_child_kind_is_generic_for_hoga_time_and_ratio(self) -> None:
        orders = [
            _order("HOGA", child_kind="HOGA_LEVEL", process_id="PROCESS_HOGA"),
            _order("TIME", child_kind="TIME_SLICE", process_id="PROCESS_TIME"),
            _order("RATIO", child_kind="RATIO_SLICE", process_id="PROCESS_RATIO"),
        ]
        self._write(orders)

        result = self._inspect("2026-09-03T10:01:00")

        self.assertEqual(3, len(result["proposals"]))
        self.assertEqual(
            {"HOGA_LEVEL", "TIME_SLICE", "RATIO_SLICE"},
            {
                proposal["source_order"]["execution_intent"]["child_kind"]
                for proposal in result["proposals"]
            },
        )

    def test_batch_scope_cancels_open_siblings_when_first_child_times_out(self) -> None:
        due = _order("BATCH_1", scope="BATCH")
        later = _order("BATCH_2", scope="BATCH", accepted_at="2026-09-03T10:00:15")
        self._write([due, later])

        result = self._inspect("2026-09-03T10:00:20")

        self.assertEqual(
            {"BROKER_BATCH_1", "BROKER_BATCH_2"},
            {proposal["broker_order_no"] for proposal in result["proposals"]},
        )

    def test_cycle_limit_bounds_candidates_without_global_stop(self) -> None:
        self._write(
            [
                _order(f"SELL_{index}", process_id=f"PROCESS_{index}")
                for index in range(1, 8)
            ]
        )
        result = self._inspect("2026-09-03T10:01:00", limit=3)
        self.assertEqual(3, len(result["proposals"]))

    def test_process_policy_or_active_cancel_identity_mismatch_is_reviewed(self) -> None:
        first = _order("FIRST", scope="BATCH")
        second = _order("SECOND", scope="EACH")
        self._write([first, second])
        mismatch = self._inspect("2026-09-03T10:01:00")
        self.assertEqual([], mismatch["proposals"])
        self.assertIn(
            "UNFILLED_TIMEOUT_PROCESS_POLICY_MISMATCH",
            mismatch["reviews"][0]["review_reasons"],
        )

        active = _cancel(first)
        active["account_no"] = "OTHER_ACCOUNT"
        self._write([first, active])
        cancel_mismatch = self._inspect("2026-09-03T10:01:00")
        self.assertEqual([], cancel_mismatch["proposals"])
        self.assertIn(
            "UNFILLED_TIMEOUT_ACTIVE_CANCEL_IDENTITY_MISMATCH",
            cancel_mismatch["reviews"][0]["review_reasons"],
        )

    def test_repeated_broker_open_evidence_preserves_first_acceptance_anchor(self) -> None:
        record = {"broker_accepted_at": ACCEPTED_AT}
        _apply_acceptance(
            record,
            broker_order_no="BROKER_1",
            event_identity="EVENT_2",
            received_at="2026-09-03T10:00:05",
        )
        self.assertEqual(ACCEPTED_AT, record["broker_accepted_at"])


class ExistingCancelProductionPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue_path = self.root / "order_queue.json"
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"
        context = AutoTradeOrderExecutionContext(
            kiwoom_connected=lambda: False,
            account_numbers=lambda: [ACCOUNT],
            selected_account_no=lambda: ACCOUNT,
            send_order_callable=lambda: None,
            selected_stock_info=lambda: None,
            selected_routine_metadata=lambda: None,
            selected_target_instance_ids=lambda: (),
            selected_routine_dir=lambda: None,
            routine_dirs=lambda: [],
            stock_dirs_in_routine=lambda _path: [],
            base_stocks=lambda: [],
            order_queue_path=lambda: self.queue_path,
            order_executions_path=lambda: self.executions_path,
            order_locks_path=lambda: self.locks_path,
        )
        self.boundary = AutoTradeOrderExecutionBoundary(context)
        self.boundary.send_order_for_order_queued_automatically = mock.Mock(
            return_value={"queue_result_recorded": True, "send_order_called": False}
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, orders: list[dict[str, object]]) -> None:
        self.queue_path.write_text(
            json.dumps({"version": 1, "revision": 0, "updated_at": "", "orders": orders}),
            encoding="utf-8",
        )

    def test_partial_fill_cancel_reuses_provenance_and_unique_request_identity(self) -> None:
        source = _order(status="PARTIALLY_FILLED", remaining=5)
        source["execution_request"]["request_preview"] = {
            "account_no": source.pop("account_no"),
            "code": source.pop("code"),
            "side": source.pop("side"),
            "order_action": source.pop("order_action"),
        }
        self._write([source])

        result = self.boundary.queue_open_order_cancel_automatically(
            source["id"],
            expected_account_no=ACCOUNT,
            expected_code=CODE,
            expected_side="SELL",
            expected_broker_order_no=source["broker_order_no"],
            cancel_evidence={"trigger": "UNFILLED_TIMEOUT", "timeout_ms": 20_000},
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"]
        cancel = next(item for item in saved if item.get("order_action") == "CANCEL")
        request = cancel["execution_request"]
        self.assertEqual(5, cancel["quantity"])
        self.assertEqual(PROCESS, cancel["execution_process_id"])
        self.assertEqual(SIGNAL, cancel["source_signal_id"])
        self.assertEqual("CANCEL", cancel["child_kind"])
        self.assertEqual(source["broker_order_no"], request["request_preview"]["original_order_no"])
        self.assertTrue(request["execution_id"])
        self.assertTrue(request["order_id"])
        self.assertTrue(request["request_hash"])
        self.assertTrue(request["lock_id"])
        self.assertEqual(
            "UNFILLED_TIMEOUT",
            request["child_plan"]["cancel_evidence"]["trigger"],
        )

    def test_identity_change_and_duplicate_cancel_fail_closed(self) -> None:
        source = _order()
        self._write([source])
        mismatch = self.boundary.queue_open_order_cancel_automatically(
            source["id"],
            expected_account_no=ACCOUNT,
            expected_code="000660",
            expected_side="SELL",
            expected_broker_order_no=source["broker_order_no"],
        )
        self.assertFalse(mismatch["ok"])
        self.boundary.send_order_for_order_queued_automatically.assert_not_called()

        self._write([source, _cancel(source)])
        duplicate = self.boundary.queue_open_order_cancel_automatically(
            source["id"],
            expected_account_no=ACCOUNT,
            expected_code=CODE,
            expected_side="SELL",
            expected_broker_order_no=source["broker_order_no"],
        )
        self.assertTrue(duplicate["ok"])
        self.assertEqual(1, duplicate["cancel_pending"])


class UnfilledCancelTimerRoutingTest(unittest.TestCase):
    def test_operation_cycle_routes_one_cancel_and_isolates_review(self) -> None:
        entry = SimpleNamespace(
            stock_code=CODE,
            stock_name="삼성전자",
            stock_dir=Path("unused"),
            execution_ready=True,
            real_trade_enabled=True,
            signal_probe_only=False,
        )
        snapshot = SimpleNamespace(entries=(entry,))
        requester = mock.Mock(return_value={"ok": True, "cancel_requested": 1, "cancel_pending": 0})
        window = SimpleNamespace(
            current_selected_account_no=lambda: ACCOUNT,
            queue_open_order_cancel_automatically=requester,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        inspected = {
            "proposals": [{
                "order_queued_id": "ORDER_1",
                "account_no": ACCOUNT,
                "code": CODE,
                "side": "SELL",
                "broker_order_no": "BROKER_1",
                "remaining_quantity": 5,
                "scope": "EACH",
                "timeout_ms": 20_000,
                "timeout_anchor": "BROKER_ACCEPTED_AT",
                "timeout_anchor_at": ACCEPTED_AT,
                "timeout_due_at": "2026-09-03T10:00:20",
            }],
            "reviews": [{"code": CODE, "review_reasons": ["OTHER_ORDER_UNCERTAIN"]}],
            "waiting": [],
            "errors": [],
        }
        empty = {"proposals": [], "reviews": [], "waiting": [], "errors": []}
        consumer = {"summary": {"signals_checked": 0, "blocked": 0, "allowed": 0, "errors": 0, "orders_created": 0, "approval_checked": 0, "approved": 0, "executable_order_ids": []}}
        with mock.patch.object(gui_auto_trade_timer, "inspect_unfilled_sell_cancel_eligibility", return_value=inspected), mock.patch.object(
            gui_auto_trade_timer, "inspect_due_time_slices", return_value=empty
        ), mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", return_value=empty), mock.patch.object(
            gui_auto_trade_timer, "inspect_execution_process_supplements", return_value=empty
        ), mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value=consumer):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        self.assertEqual(1, result["unfilled_cancel"]["cancel_requested"])
        self.assertEqual(1, result["unfilled_cancel"]["reviews"])
        requester.assert_called_once()


if __name__ == "__main__":
    unittest.main()
