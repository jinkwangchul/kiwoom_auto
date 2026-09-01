from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock
from uuid import UUID

from assignment_authorization_service import (
    ASSIGNMENT_INTENT_ASSIGN,
    ASSIGNMENT_INTENT_REASSIGN,
    ASSIGNMENT_INTENT_REVIEW_RESOLUTION_UNASSIGN,
    ASSIGNMENT_INTENT_STOCK_UNREGISTER,
    ASSIGNMENT_INTENT_UNASSIGN,
    execute_assignment_change,
    execute_assignment_unassign,
    inspect_assignment_authorization,
    inspect_stock_unregister_availability,
)
from stock_repository import StockRepository
from tests.participant_owner_fixture import (
    attach_participant_owner,
    participant_codes,
    participant_owner,
)


CODE = "005930"
NAME = "Sample"
GROUP_ID = str(UUID("4b366f80-1bbf-4a5e-b010-f411c3620e2e"))
INSTANCE_A = str(UUID("0f86470a-6368-4b31-802e-d25d6ce72a5f"))
INSTANCE_B = str(UUID("5d8a8cc8-5421-4bb2-af3f-e5179782a1d4"))


class AssignmentAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self._write_foundation()
        self.repository = StockRepository(self.root)
        self.stock_dir = self.repository.ensure_stock_folder(CODE, NAME)
        self.owner = SimpleNamespace()
        attach_participant_owner(self.owner)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _write_foundation(self) -> None:
        self._write_json(
            self.root / "routines" / "sample" / "routine.json",
            {
                "schema_version": "1.0",
                "definition_id": "sample",
                "name": "Sample",
                "entry_file": "routine.py",
                "rules_file": "rules.json",
                "enabled": True,
            },
        )
        (self.root / "routines" / "sample" / "routine.py").write_text(
            "",
            encoding="utf-8",
        )
        self._write_json(
            self.root / "groups" / GROUP_ID / "group.json",
            {
                "schema_version": "1.0",
                "group_id": GROUP_ID,
                "definition_id": "sample",
                "base_name": "Group",
                "display_name": "Group",
                "slot": 0,
                "created_at": "2026-08-30T09:00:00+09:00",
            },
        )
        self._write_json(
            self.root / "groups" / "registry.json",
            {
                "schema_version": "1.0",
                "mode": "logical",
                "group_ids": [GROUP_ID],
                "cutover_at": "2026-08-30T09:00:00+09:00",
            },
        )
        for instance_id, display_name in (
            (INSTANCE_A, "Instance A"),
            (INSTANCE_B, "Instance B"),
        ):
            self._write_json(
                self.root / "routine_instances" / instance_id / "instance.json",
                {
                    "schema_version": "1.0",
                    "instance_id": instance_id,
                    "definition_id": "sample",
                    "display_name": display_name,
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                    "created_at": "2026-08-30T09:00:00+09:00",
                    "updated_at": "2026-08-30T09:00:00+09:00",
                    "group_id": GROUP_ID,
                },
            )
            self._write_json(
                self.root / "routine_instances" / instance_id / "rules.json",
                {},
            )

    def _assign(self, target: str, expected: str, *, owner=None, intent=ASSIGNMENT_INTENT_ASSIGN):
        return execute_assignment_change(
            self.owner if owner is None else owner,
            self.root,
            CODE,
            NAME,
            instance_id=target,
            instance_name="caller snapshot",
            definition_id="sample",
            routine_type="Sample",
            expected_instance_id=expected,
            intent=intent,
        )

    def _set_state(self, **updates: object) -> None:
        path = self.stock_dir / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(updates)
        self._write_json(path, state)

    def _fingerprint(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for base in (
            self.stock_dir,
            self.root / "assignment_episodes",
            self.root / "runtime" / "assignment_transactions",
        ):
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.is_file():
                    result[str(path.relative_to(self.root))] = path.read_bytes()
        return result

    def test_current_participant_blocks_assign_before_journal(self) -> None:
        attach_participant_owner(self.owner, {CODE})
        before = self._fingerprint()

        result = self._assign(INSTANCE_A, "")

        self.assertFalse(result.ok)
        self.assertEqual("CURRENTLY_RUNNING", result.reason_code)
        self.assertEqual(before, self._fingerprint())

    def test_main_adapter_resolves_current_participant_from_window_owner(self) -> None:
        attach_participant_owner(self.owner, {CODE})
        adapter = SimpleNamespace(_window=self.owner)
        before = self._fingerprint()

        result = self._assign(INSTANCE_A, "", owner=adapter)

        self.assertFalse(result.ok)
        self.assertEqual("CURRENTLY_RUNNING", result.reason_code)
        self.assertEqual(before, self._fingerprint())

    def test_current_participant_blocks_reassign_unassign_and_unregister(self) -> None:
        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        attach_participant_owner(self.owner, {CODE})
        before = self._fingerprint()

        reassigned = self._assign(
            INSTANCE_B,
            INSTANCE_A,
            intent=ASSIGNMENT_INTENT_REASSIGN,
        )
        unassigned = execute_assignment_unassign(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
        )
        unregister = execute_assignment_unassign(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        )

        self.assertEqual(
            ["CURRENTLY_RUNNING"] * 3,
            [reassigned.reason_code, unassigned.reason_code, unregister.reason_code],
        )
        self.assertEqual(before, self._fingerprint())

    def test_stale_raw_running_is_not_current_session_authority(self) -> None:
        self._set_state(status="RUNNING", trade_started=True)

        result = self._assign(INSTANCE_A, "")

        self.assertTrue(result.ok, result)
        self.assertTrue(result.changed)

    def test_review_and_assign_recovery_blocks_are_read_only(self) -> None:
        self._set_state(status="REVIEW_REQUIRED", review_required=True)
        before_review = self._fingerprint()
        review = inspect_assignment_authorization(
            self.owner,
            self.root,
            CODE,
            NAME,
            intent=ASSIGNMENT_INTENT_ASSIGN,
            target_instance_id=INSTANCE_A,
            expected_instance_id="",
        )
        self.assertFalse(review.allowed)
        self.assertEqual("REVIEW_REQUIRED", review.reason_code)
        self.assertEqual(before_review, self._fingerprint())

        self._set_state(status="STOPPED", review_required=False, review_status="")
        recovery_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            startup_recovery_session_ready=lambda refresh=False: False,
        )
        before_recovery = self._fingerprint()
        recovery = inspect_assignment_authorization(
            recovery_owner,
            self.root,
            CODE,
            NAME,
            intent=ASSIGNMENT_INTENT_ASSIGN,
            target_instance_id=INSTANCE_A,
            expected_instance_id="",
        )
        self.assertFalse(recovery.allowed)
        self.assertEqual("RECOVERY_BLOCKED", recovery.reason_code)
        self.assertEqual(before_recovery, self._fingerprint())

        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        before_unregister = self._fingerprint()
        unregister = inspect_stock_unregister_availability(
            recovery_owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
        )
        self.assertTrue(unregister.allowed)
        self.assertEqual("ALLOWED", unregister.reason_code)
        self.assertEqual(before_unregister, self._fingerprint())

    def test_pending_recovery_allows_only_stopped_initial_assignment_without_broker_io(self) -> None:
        holdings_request = Mock()
        open_orders_request = Mock()
        comm_rq_data = Mock()
        send_order = Mock()
        recovery_gate = Mock(
            return_value=SimpleNamespace(
                allowed=False,
                reason_code="RECOVERY_STOCK_PENDING",
                evidence=("caller=ASSIGNMENT_AUTHORIZATION",),
            )
        )
        recovery_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=recovery_gate,
            request_account_holdings_snapshot=holdings_request,
            request_open_orders_snapshot=open_orders_request,
            CommRqData=comm_rq_data,
            SendOrder=send_order,
        )

        result = self._assign(INSTANCE_A, "", owner=recovery_owner)

        self.assertTrue(result.ok, result)
        self.assertTrue(result.changed)
        self.assertEqual("ALLOWED", result.authorization.reason_code)
        recovery_gate.assert_called_once_with(
            CODE,
            caller_name="ASSIGNMENT_AUTHORIZATION",
        )
        holdings_request.assert_not_called()
        open_orders_request.assert_not_called()
        comm_rq_data.assert_not_called()
        send_order.assert_not_called()

    def test_prelogin_recovery_absence_allows_stopped_initial_assignment_without_broker_io(self) -> None:
        for reason_code in ("RECOVERY_CONTEXT_MISSING", "RECOVERY_NOT_STARTED"):
            with self.subTest(reason_code=reason_code):
                recovery_gate = Mock(
                    return_value=SimpleNamespace(
                        allowed=False,
                        reason_code=reason_code,
                        evidence=("login_session_id=", "account_no="),
                    )
                )
                broker_calls = {
                    name: Mock()
                    for name in (
                        "CommConnect",
                        "request_account_holdings_snapshot",
                        "request_open_orders_snapshot",
                        "request_minute_candles",
                        "SetRealReg",
                        "SendOrder",
                    )
                }
                owner = SimpleNamespace(
                    _main_monitoring_auto_trade_operation_host=participant_owner(),
                    production_recovery_gate_for_stock=recovery_gate,
                    **broker_calls,
                )

                result = self._assign(INSTANCE_A, "", owner=owner)

                self.assertTrue(result.ok, result)
                self.assertTrue(result.changed)
                self.assertEqual("ALLOWED", result.authorization.reason_code)
                self.assertEqual((), participant_codes(owner))
                self.assertFalse((self.root / "runtime" / "order_queue.json").exists())
                recovery_gate.assert_called_once_with(
                    CODE,
                    caller_name="ASSIGNMENT_AUTHORIZATION",
                )
                for broker_call in broker_calls.values():
                    broker_call.assert_not_called()

                removed = execute_assignment_unassign(
                    owner,
                    self.root,
                    CODE,
                    NAME,
                    expected_instance_id=INSTANCE_A,
                    intent=ASSIGNMENT_INTENT_UNASSIGN,
                )
                self.assertTrue(removed.ok, removed)

    def test_prelogin_first_assignment_preserves_local_safety_guards(self) -> None:
        owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=lambda *_args, **_kwargs: SimpleNamespace(
                allowed=False,
                reason_code="RECOVERY_CONTEXT_MISSING",
                evidence=(),
            ),
        )

        self._set_state(status="REVIEW_REQUIRED", review_required=True)
        review = self._assign(INSTANCE_A, "", owner=owner)
        self.assertEqual("REVIEW_REQUIRED", review.reason_code)

        self._set_state(
            status="EMERGENCY_STOPPED",
            review_required=False,
            review_status="",
        )
        emergency = self._assign(INSTANCE_A, "", owner=owner)
        self.assertEqual("EMERGENCY_STOP", emergency.reason_code)

        self._set_state(
            status="STOPPED",
            holding_qty=1,
            avg_price=1_000,
            holding_amount=1_000,
            review_required=False,
            review_status="",
            review_reason="",
            review_location="",
            review_detail="",
        )
        holding = self._assign(INSTANCE_A, "", owner=owner)
        self.assertEqual("HAS_HOLDING", holding.reason_code)

        self._set_state(holding_qty=0, avg_price=0, holding_amount=0)
        self._write_json(
            self.stock_dir / "orders.json",
            {"orders": [{"side": "SELL", "status": "PENDING", "unfilled_qty": 1}]},
        )
        pending = self._assign(INSTANCE_A, "", owner=owner)
        self.assertEqual("HAS_PENDING_ORDER", pending.reason_code)

        self._write_json(self.stock_dir / "orders.json", {"orders": []})
        missing_target = self._assign(str(UUID(int=1)), "", owner=owner)
        self.assertEqual("TARGET_INSTANCE_MISSING", missing_target.reason_code)

    def test_recovery_failure_and_review_decisions_still_block_first_assignment(self) -> None:
        reasons = (
            "RECOVERY_ACCOUNT_FAILED",
            "RECOVERY_ACCOUNT_REVIEW_REQUIRED",
            "RECOVERY_STOCK_FAILED",
            "RECOVERY_STOCK_REVIEW_REQUIRED",
        )
        for reason_code in reasons:
            with self.subTest(reason_code=reason_code):
                owner = SimpleNamespace(
                    _main_monitoring_auto_trade_operation_host=participant_owner(),
                    production_recovery_gate_for_stock=lambda *_args, value=reason_code, **_kwargs: SimpleNamespace(
                        allowed=False,
                        reason_code=value,
                        evidence=("explicit_recovery_decision",),
                    ),
                )
                result = self._assign(INSTANCE_A, "", owner=owner)
                self.assertFalse(result.ok)
                self.assertEqual(reason_code, result.reason_code)
                self.assertTrue(result.authorization.recovery_blocked)

    def test_restored_recovery_keeps_normal_initial_assignment_contract(self) -> None:
        recovery_gate = Mock(
            return_value=SimpleNamespace(
                allowed=True,
                reason_code="RECOVERY_COMPLETED",
                evidence=(),
            )
        )
        owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=recovery_gate,
        )

        result = self._assign(INSTANCE_A, "", owner=owner)

        self.assertTrue(result.ok, result)
        self.assertTrue(result.changed)
        recovery_gate.assert_called_once_with(
            CODE,
            caller_name="ASSIGNMENT_AUTHORIZATION",
        )

    def test_pending_recovery_still_blocks_non_stopped_or_non_initial_assignment(self) -> None:
        recovery_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=lambda *_args, **_kwargs: SimpleNamespace(
                allowed=False,
                reason_code="RECOVERY_STOCK_PENDING",
                evidence=(),
            ),
        )

        self._set_state(status="MONITORING")
        non_stopped = self._assign(INSTANCE_A, "", owner=recovery_owner)
        self.assertFalse(non_stopped.ok)
        self.assertEqual("RECOVERY_STOCK_PENDING", non_stopped.reason_code)

        self._set_state(status="STOPPED")
        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        reassignment = self._assign(
            INSTANCE_B,
            INSTANCE_A,
            owner=recovery_owner,
            intent=ASSIGNMENT_INTENT_REASSIGN,
        )
        self.assertFalse(reassignment.ok)
        self.assertEqual("RECOVERY_STOCK_PENDING", reassignment.reason_code)

    def test_failed_recovery_remains_an_assignment_blocker(self) -> None:
        recovery_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=lambda *_args, **_kwargs: SimpleNamespace(
                allowed=False,
                reason_code="RECOVERY_STOCK_FAILED",
                evidence=("BROKER_ONLY_ORDER",),
            ),
        )

        result = self._assign(INSTANCE_A, "", owner=recovery_owner)

        self.assertFalse(result.ok)
        self.assertEqual("RECOVERY_STOCK_FAILED", result.reason_code)
        self.assertTrue(result.authorization.recovery_blocked)

    def test_pending_recovery_does_not_block_unassign_or_unregister(self) -> None:
        recovery_gate = Mock(
            return_value=SimpleNamespace(
                allowed=False,
                reason_code="RECOVERY_STOCK_PENDING",
                evidence=("fixture",),
            )
        )
        broker_calls = {
            name: Mock()
            for name in (
                "request_account_holdings_snapshot",
                "request_open_orders_snapshot",
                "CommRqData",
                "SendOrder",
            )
        }
        recovery_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=recovery_gate,
            **broker_calls,
        )
        self.assertTrue(self._assign(INSTANCE_A, "").ok)

        unassigned = execute_assignment_unassign(
            recovery_owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
        )
        self.assertTrue(unassigned.ok, unassigned)
        self.assertTrue(unassigned.changed)

        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        unregistered = execute_assignment_unassign(
            recovery_owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        )
        self.assertTrue(unregistered.ok, unregistered)
        self.assertTrue(unregistered.changed)
        recovery_gate.assert_not_called()
        for broker_call in broker_calls.values():
            broker_call.assert_not_called()

    def test_missing_recovery_context_does_not_block_unassign_or_unregister(self) -> None:
        context_missing_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            startup_recovery_session_ready=lambda refresh=False: False,
        )
        self.assertTrue(self._assign(INSTANCE_A, "").ok)

        unassigned = execute_assignment_unassign(
            context_missing_owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
        )
        self.assertTrue(unassigned.ok, unassigned)

        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        unregistered = execute_assignment_unassign(
            context_missing_owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        )
        self.assertTrue(unregistered.ok, unregistered)

    def test_000660_pending_recovery_assignment_removal_lifecycle_has_no_broker_io(self) -> None:
        code = "000660"
        name = "SK하이닉스"
        stock_dir = self.repository.ensure_stock_folder(code, name)
        recovery_gate = Mock(
            return_value=SimpleNamespace(
                allowed=False,
                reason_code="RECOVERY_STOCK_PENDING",
                evidence=("fixture=000660",),
            )
        )
        broker_calls = {
            name: Mock()
            for name in (
                "request_account_holdings_snapshot",
                "request_open_orders_snapshot",
                "CommRqData",
                "SendOrder",
            )
        }
        owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            production_recovery_gate_for_stock=recovery_gate,
            **broker_calls,
        )

        def assign() -> object:
            return execute_assignment_change(
                owner,
                self.root,
                code,
                name,
                instance_id=INSTANCE_A,
                instance_name="지표추종매매A",
                definition_id="sample",
                routine_type="Sample",
                expected_instance_id="",
                intent=ASSIGNMENT_INTENT_ASSIGN,
            )

        assigned_for_unassign = assign()
        unassigned = execute_assignment_unassign(
            owner,
            self.root,
            code,
            name,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
        )
        assigned_for_unregister = assign()
        unregistered = execute_assignment_unassign(
            owner,
            self.root,
            code,
            name,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        )

        self.assertTrue(assigned_for_unassign.ok, assigned_for_unassign)
        self.assertTrue(unassigned.ok, unassigned)
        self.assertTrue(assigned_for_unregister.ok, assigned_for_unregister)
        self.assertTrue(unregistered.ok, unregistered)
        saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("", saved["assigned_routine_instance_id"])
        self.assertFalse((self.root / "runtime" / "order_queue.json").exists())
        self.assertEqual(2, recovery_gate.call_count)
        for broker_call in broker_calls.values():
            broker_call.assert_not_called()

    def test_removal_still_blocks_review_emergency_and_assignment_mismatch(self) -> None:
        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        self._set_state(status="REVIEW_REQUIRED", review_required=True)
        review = inspect_assignment_authorization(
            self.owner,
            self.root,
            CODE,
            NAME,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
            expected_instance_id=INSTANCE_A,
        )
        self.assertEqual("REVIEW_REQUIRED", review.reason_code)

        self._set_state(
            status="EMERGENCY_STOPPED",
            review_required=False,
            review_status="",
        )
        emergency = inspect_stock_unregister_availability(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
        )
        self.assertEqual("EMERGENCY_STOP", emergency.reason_code)

        self._set_state(status="STOPPED")
        mismatch = inspect_stock_unregister_availability(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_B,
        )
        self.assertEqual("ASSIGNMENT_CHANGED", mismatch.reason_code)

    def test_pending_integrity_inspection_does_not_mark_review(self) -> None:
        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        (self.stock_dir / "orders.json").write_text(
            '{"orders": "broken"}',
            encoding="utf-8",
        )
        before = self._fingerprint()

        availability = inspect_stock_unregister_availability(
            self.owner,
            self.root,
            CODE,
            NAME,
        )

        self.assertFalse(availability.allowed)
        self.assertEqual(
            "PENDING_ORDER_INTEGRITY_UNKNOWN",
            availability.reason_code,
        )
        self.assertEqual(before, self._fingerprint())
        state = json.loads((self.stock_dir / "state.json").read_text(encoding="utf-8"))
        self.assertFalse(bool(state.get("review_required")))

    def test_normal_assign_reassign_unassign_roundtrip(self) -> None:
        assigned = self._assign(INSTANCE_A, "")
        reassigned = self._assign(
            INSTANCE_B,
            INSTANCE_A,
            intent=ASSIGNMENT_INTENT_REASSIGN,
        )
        unassigned = execute_assignment_unassign(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_B,
            intent=ASSIGNMENT_INTENT_UNASSIGN,
        )

        self.assertTrue(assigned.ok and assigned.changed)
        self.assertTrue(reassigned.ok and reassigned.changed)
        self.assertTrue(unassigned.ok and unassigned.changed)
        saved = json.loads((self.stock_dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("", saved["assigned_routine_instance_id"])

    def test_review_specific_unassign_preserves_explicit_workflow(self) -> None:
        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        self._set_state(status="REVIEW_REQUIRED", review_required=True)
        review_owner = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            startup_recovery_session_ready=lambda refresh=False: False,
        )

        result = execute_assignment_unassign(
            review_owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_REVIEW_RESOLUTION_UNASSIGN,
        )

        self.assertTrue(result.ok, result)
        self.assertTrue(result.changed)
        state = json.loads((self.stock_dir / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["review_required"])

    def test_normal_nonrunning_unregister_uses_guarded_transaction(self) -> None:
        self.assertTrue(self._assign(INSTANCE_A, "").ok)

        result = execute_assignment_unassign(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
            intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        )

        self.assertTrue(result.ok, result)
        self.assertTrue(result.changed)
        saved = json.loads((self.stock_dir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual("", saved["assigned_routine_instance_id"])

    def test_holding_and_pending_unregister_guards_are_preserved(self) -> None:
        self.assertTrue(self._assign(INSTANCE_A, "").ok)
        self._set_state(holding_qty=1, avg_price=1_000)
        holding = inspect_stock_unregister_availability(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
        )
        self.assertEqual("HAS_HOLDING", holding.reason_code)

        self._set_state(holding_qty=0, avg_price=0)
        self._write_json(
            self.stock_dir / "orders.json",
            {"orders": [{"side": "BUY", "status": "PENDING", "unfilled_qty": 1}]},
        )
        pending = inspect_stock_unregister_availability(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
        )
        self.assertEqual("HAS_PENDING_ORDER", pending.reason_code)

        self._write_json(
            self.stock_dir / "orders.json",
            {"orders": [{"side": "SELL", "status": "PENDING", "unfilled_qty": 1}]},
        )
        sell_pending = inspect_stock_unregister_availability(
            self.owner,
            self.root,
            CODE,
            NAME,
            expected_instance_id=INSTANCE_A,
        )
        self.assertEqual("HAS_PENDING_ORDER", sell_pending.reason_code)

    def test_missing_target_or_group_fails_before_journal(self) -> None:
        before = self._fingerprint()
        missing_target = self._assign(str(UUID(int=1)), "")
        self.assertEqual("TARGET_INSTANCE_MISSING", missing_target.reason_code)
        self.assertEqual(before, self._fingerprint())

        group_path = self.root / "groups" / GROUP_ID / "group.json"
        group_path.unlink()
        missing_group = self._assign(INSTANCE_A, "")
        self.assertEqual("GROUP_MISSING", missing_group.reason_code)
        self.assertEqual(before, self._fingerprint())


if __name__ == "__main__":
    unittest.main()
