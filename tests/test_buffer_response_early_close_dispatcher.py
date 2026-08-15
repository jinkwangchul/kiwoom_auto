# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from buffer_response_early_close_dispatcher import (
    BufferResponseEarlyCloseDispatcher,
    buffer_response_command_source,
    deterministic_buffer_early_close_command_id,
    resume_main_window_buffer_early_close,
)
from buffer_response_ownership_service import (
    BufferResponseOwnershipService,
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_OWNED,
)
from close_intent_service import apply_close_intent
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED


ACCOUNT = "81291234"
DAY = "2026-08-15"
STOCK_A = "000001"


def _source_evidence() -> dict[str, object]:
    return {
        "observation_id": "A" * 64,
        "observed_at": "2026-08-15T10:00:00+09:00",
        "previous_entry_amount": 0,
        "current_entry_amount": 100,
        "new_contributing_buy_ids": ["O1"],
        "contributing_buy_ids": ["O1"],
        "confirmed_evidence": {
            "recovery_session_id": "RECOVERY-1",
            "queue_revision": 1,
            "order_queue_sha256": "B" * 64,
            "positions_sha256": "C" * 64,
            "fills_sha256": "D" * 64,
        },
    }


def _recovery(status: str = ACCOUNT_COMPLETED) -> object:
    return SimpleNamespace(
        account_status=status,
        identity=SimpleNamespace(account_no=ACCOUNT, trading_day=DAY),
        stocks=(
            SimpleNamespace(
                stock_code=STOCK_A,
                stock_status=STOCK_RESTORED,
                review_required=False,
            ),
        ),
    )


class BufferResponseEarlyCloseDispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.positions_path = self.runtime / "positions.json"
        self.queue_path = self.runtime / "order_queue.json"
        self.fills_path = self.runtime / "fills.json"
        self.ownership_path = self.runtime / "buffer_response_ownership.json"
        self.stock_dir = self.root / "stocks" / f"{STOCK_A}_Test"
        self.stock_dir.mkdir(parents=True)
        self.state_path = self.stock_dir / "state.json"
        self.config_path = self.stock_dir / "config.json"
        self.stock_orders_path = self.stock_dir / "orders.json"
        self._write_json(
            self.state_path,
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
        self._write_json(
            self.config_path,
            {
                "enabled": True,
                "operation_excluded": False,
                "assigned_routine_instance_id": "routine-1",
            },
        )
        self._write_json(self.stock_orders_path, {"orders": []})
        self._write_json(
            self.positions_path,
            {
                "version": 1,
                "positions": [
                    {
                        "account_no": ACCOUNT,
                        "code": STOCK_A,
                        "quantity": 10,
                        "cost_basis": 1000,
                        "position_status": "OPEN",
                    }
                ],
            },
        )
        self._write_json(self.queue_path, {"revision": 1, "orders": []})
        self._write_json(self.fills_path, {"version": 1, "fills": []})
        self.ownership = BufferResponseOwnershipService(
            self.ownership_path,
            now_factory=lambda: "2026-08-15T10:00:00+09:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _claim(self, intent: str = RESPONSE_INTENT_EARLY_CLOSE) -> dict[str, object]:
        result = self.ownership.claim_batch_event_candidate(
            account_no=ACCOUNT,
            trading_day=DAY,
            event_sequence=1,
            source_evidence=_source_evidence(),
            selected_stock_code=STOCK_A,
            response_intent=intent,
            detected_at="2026-08-15T10:00:00+09:00",
            expected_revision=0,
        )
        self.assertTrue(result["ok"], result)
        return result

    def _actual_backend(self, **kwargs):
        return apply_close_intent(
            **kwargs,
            transition_guard=None,
            operation_state_writer=lambda **_ignored: {"ok": True},
        )

    def _dispatcher(self, backend=None) -> BufferResponseEarlyCloseDispatcher:
        return BufferResponseEarlyCloseDispatcher(
            ownership_service=self.ownership,
            project_root=self.root,
            positions_path=self.positions_path,
            order_queue_path=self.queue_path,
            fills_path=self.fills_path,
            close_backend=backend or self._actual_backend,
        )

    def test_owned_early_close_uses_existing_backend_once_and_reads_back(self) -> None:
        claim = self._claim()
        runtime_before = {
            path: path.read_bytes()
            for path in (self.positions_path, self.queue_path, self.fills_path)
        }
        backend = mock.Mock(side_effect=self._actual_backend)
        dispatcher = self._dispatcher(backend)
        with mock.patch(
            "close_intent_service.observe_close_started"
        ), mock.patch(
            "operation_command_service.observe_liquidation_requested"
        ):
            first = dispatcher.dispatch_event(
                event_id=claim["event_id"],
                account_no=ACCOUNT,
                trading_day=DAY,
                recovery_context=_recovery(),
            )
            second = dispatcher.dispatch_event(
                event_id=claim["event_id"],
                account_no=ACCOUNT,
                trading_day=DAY,
                recovery_context=_recovery(),
            )
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["dispatched"])
        self.assertEqual(1, backend.call_count)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["already_applied"])
        self.assertFalse(second["backend_called"])

        command_id = deterministic_buffer_early_close_command_id(claim["event_id"])
        source = buffer_response_command_source(claim["event_id"])
        state = self._read_state()
        self.assertEqual("EARLY_CLOSE", state["operation_command_mode"])
        self.assertEqual(command_id, state["operation_command_id"])
        self.assertEqual(source, state["operation_command_source"])
        self.assertEqual(source, state["early_close_source"])
        self.assertEqual("루틴", state["early_close_method"])
        ownership_event = self.ownership.read_snapshot()["snapshot"]["events"][claim["event_id"]]
        self.assertEqual(STATUS_OWNED, ownership_event["status"])
        self.assertIsNone(ownership_event["completion"])
        for path, content in runtime_before.items():
            self.assertEqual(content, path.read_bytes())

    def test_fresh_resume_before_and_after_backend_is_idempotent(self) -> None:
        claim = self._claim()
        backend = mock.Mock(side_effect=self._actual_backend)
        fresh = self._dispatcher(backend)
        with mock.patch(
            "close_intent_service.observe_close_started"
        ), mock.patch(
            "operation_command_service.observe_liquidation_requested"
        ):
            resumed_before = fresh.resume_owned_events(
                account_no=ACCOUNT,
                trading_day=DAY,
                recovery_context=_recovery(),
            )
            restarted = self._dispatcher(backend)
            resumed_after = restarted.resume_owned_events(
                account_no=ACCOUNT,
                trading_day=DAY,
                recovery_context=_recovery(),
            )
        self.assertTrue(resumed_before["ok"], resumed_before)
        self.assertEqual(1, resumed_before["attempted"])
        self.assertTrue(resumed_after["ok"], resumed_after)
        self.assertEqual(1, resumed_after["attempted"])
        self.assertTrue(resumed_after["results"][0]["already_applied"])
        self.assertEqual(1, backend.call_count)
        events = self.ownership.read_snapshot()["snapshot"]["events"]
        self.assertEqual((claim["event_id"],), tuple(events))

    def test_other_close_auto_close_and_liquidation_conflicts_never_overwrite(self) -> None:
        claim = self._claim()
        cases = (
            {"status": "EARLY_CLOSE", "early_close_source": "OTHER"},
            {"status": "AUTO_CLOSE"},
            {"status": "LIQUIDATING"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                state = self._read_state()
                state.update(changes)
                self._write_json(self.state_path, state)
                backend = mock.Mock()
                result = self._dispatcher(backend).dispatch_event(
                    event_id=claim["event_id"],
                    account_no=ACCOUNT,
                    trading_day=DAY,
                    recovery_context=_recovery(),
                )
                self.assertFalse(result["ok"], result)
                self.assertIn("CONFLICT", result["reason"])
                backend.assert_not_called()
                restored = dict(state)
                restored.update({"status": "RUNNING", "early_close_source": ""})
                self._write_json(self.state_path, restored)

    def test_holding_recovery_immediate_and_legacy_guards_are_fail_closed(self) -> None:
        early = self._claim()
        backend = mock.Mock()
        dispatcher = self._dispatcher(backend)
        incomplete = dispatcher.dispatch_event(
            event_id=early["event_id"],
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery("FAILED"),
        )
        self.assertFalse(incomplete["ok"])

        positions = json.loads(self.positions_path.read_text(encoding="utf-8"))
        positions["positions"][0]["quantity"] = 0
        positions["positions"][0]["position_status"] = "CLOSED"
        self._write_json(self.positions_path, positions)
        no_holding = dispatcher.dispatch_event(
            event_id=early["event_id"],
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )
        self.assertFalse(no_holding["ok"])
        backend.assert_not_called()

        self.ownership_path.unlink()
        immediate = self._claim(RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED)
        immediate_result = dispatcher.dispatch_event(
            event_id=immediate["event_id"],
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )
        self.assertFalse(immediate_result["ok"])
        self.assertEqual("IMMEDIATE_LIQUIDATION_NOT_CONNECTED", immediate_result["reason"])

        document = json.loads(self.ownership_path.read_text(encoding="utf-8"))
        document["schema_version"] = 2
        document["events"][immediate["event_id"]].pop("response_intent")
        self._write_json(self.ownership_path, document)
        legacy = dispatcher.dispatch_event(
            event_id=immediate["event_id"],
            account_no=ACCOUNT,
            trading_day=DAY,
            recovery_context=_recovery(),
        )
        self.assertFalse(legacy["ok"])
        self.assertEqual("LEGACY_OWNERSHIP_INTENT_NOT_EXECUTABLE", legacy["reason"])
        backend.assert_not_called()

    def test_recovery_main_window_adapter_resumes_current_identity_only(self) -> None:
        identity = SimpleNamespace(account_no=ACCOUNT, trading_day=DAY)
        window = SimpleNamespace(selected_account_no=lambda: ACCOUNT)
        dispatcher = mock.Mock()
        dispatcher.resume_owned_events.return_value = {"ok": True, "attempted": 1}
        with mock.patch(
            "buffer_response_early_close_dispatcher.date"
        ) as date_mock, mock.patch(
            "buffer_response_early_close_dispatcher.production_recovery_registry.snapshot",
            return_value=_recovery(),
        ), mock.patch(
            "buffer_response_early_close_dispatcher.BufferResponseEarlyCloseDispatcher",
            return_value=dispatcher,
        ):
            date_mock.today.return_value.isoformat.return_value = DAY
            result = resume_main_window_buffer_early_close(
                window,
                recovery_identity=identity,
            )
        self.assertTrue(result["ok"], result)
        dispatcher.resume_owned_events.assert_called_once()

    def test_dispatcher_has_no_send_order_cancel_or_immediate_executor(self) -> None:
        source = Path(__file__).parents[1].joinpath(
            "buffer_response_early_close_dispatcher.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "SendOrder",
            "cancel_queue",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
