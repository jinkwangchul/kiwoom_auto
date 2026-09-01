from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import close_liquidation_command as command
from operation_command_service import OperationCommandService
from tests.participant_owner_fixture import participant_owner


CODE = "005930"


class CloseLiquidationCommandTest(unittest.TestCase):
    @staticmethod
    def _stock(
        root: Path,
        *,
        holding_qty: int = 3,
        state_patch: dict[str, object] | None = None,
    ) -> Path:
        stock = root / "stocks" / "005930_Samsung"
        stock.mkdir(parents=True)
        (stock / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": "instance-1"}),
            encoding="utf-8",
        )
        state = {
            "status": "RUNNING",
            "holding_qty": holding_qty,
            "trade_enabled": True,
            "trade_started_at": "2026-08-30 09:00:00",
            "operation_sequence": 0,
        }
        state.update(state_patch or {})
        (stock / "state.json").write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock / "orders.json").write_text(
            json.dumps({"orders": []}),
            encoding="utf-8",
        )
        return stock

    @staticmethod
    def _owner(*, participant: bool = True):
        return SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(
                {CODE} if participant else ()
            )
        )

    @staticmethod
    def _recovery_allowed(_code: str, _caller: str):
        return SimpleNamespace(allowed=True, reason_code="", evidence=())

    @staticmethod
    def _active_close_state(**updates: object) -> dict[str, object]:
        state: dict[str, object] = {
            "status": "EARLY_CLOSE",
            "operation_command_mode": "EARLY_CLOSE",
            "operation_command_id": "early-command",
            "early_close_requested_at": "2026-08-30 10:00:00",
            "early_close_method": "시장가",
            "liquidation_policy_forced": True,
            "liquidation_policy_reason": "EARLY_CLOSE",
        }
        state.update(updates)
        return state

    def test_availability_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            before = {
                path.name: path.read_bytes()
                for path in stock.iterdir()
                if path.is_file()
            }
            result = command.inspect_close_liquidation_availability(
                self._owner(),
                stock,
                CODE,
                intent=command.INDIVIDUAL_LIQUIDATION,
                requested_method="시장가",
                requested_minutes="5",
                recovery_inspector=self._recovery_allowed,
            )
            after = {
                path.name: path.read_bytes()
                for path in stock.iterdir()
                if path.is_file()
            }

        self.assertTrue(result.allowed)
        self.assertEqual(before, after)

    def test_individual_liquidation_requires_current_participant_and_holding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, holding_qty=0)
            factory = Mock()
            no_holding = command.execute_individual_liquidation_command(
                self._owner(),
                stock,
                CODE,
                method="시장가",
                minutes_before_regular_close="5",
                source="test",
                project_root=root,
                recovery_inspector=self._recovery_allowed,
                command_service_factory=factory,
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state["holding_qty"] = 3
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            stale_raw = command.execute_individual_liquidation_command(
                self._owner(participant=False),
                stock,
                CODE,
                method="시장가",
                minutes_before_regular_close="5",
                source="test",
                project_root=root,
                recovery_inspector=self._recovery_allowed,
                command_service_factory=factory,
            )

        self.assertEqual("NO_HOLDING", no_holding.reason_code)
        self.assertEqual("NOT_CURRENT_PARTICIPANT", stale_raw.reason_code)
        factory.assert_not_called()

    def test_review_and_recovery_block_before_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                state_patch={"status": "REVIEW_REQUIRED", "review_required": True},
            )
            factory = Mock()
            review = command.execute_individual_liquidation_command(
                self._owner(),
                stock,
                CODE,
                method="시장가",
                minutes_before_regular_close="5",
                source="test",
                project_root=root,
                command_service_factory=factory,
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state.update({"status": "RUNNING", "review_required": False})
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            recovery = command.execute_individual_liquidation_command(
                self._owner(),
                stock,
                CODE,
                method="시장가",
                minutes_before_regular_close="5",
                source="test",
                project_root=root,
                recovery_inspector=lambda _code, _caller: SimpleNamespace(
                    allowed=False,
                    reason_code="RECOVERY_BLOCKED",
                    evidence=("fixture",),
                ),
                command_service_factory=factory,
            )

        self.assertEqual("REVIEW_REQUIRED", review.reason_code)
        self.assertEqual("RECOVERY_BLOCKED", recovery.reason_code)
        factory.assert_not_called()

    def test_individual_liquidation_persists_requested_without_config_or_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root)
            config_before = (stock / "config.json").read_bytes()
            with patch("operation_command_service.observe_liquidation_requested"):
                result = command.execute_individual_liquidation_command(
                    self._owner(),
                    stock,
                    CODE,
                    method="현재가",
                    minutes_before_regular_close="7",
                    source="test",
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                    transition_guard=lambda **_kwargs: SimpleNamespace(
                        allowed=True,
                        reason_code="ALLOWED",
                    ),
                    command_service_factory=OperationCommandService,
                )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            config_after = (stock / "config.json").read_bytes()
            queue_created = (root / "runtime" / "order_queue.json").exists()

        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertTrue(result.requested)
        self.assertEqual("REQUESTED", state["individual_liquidation_request"]["status"])
        self.assertEqual("현재가", state["individual_liquidation_request"]["method"])
        self.assertEqual(config_before, config_after)
        self.assertFalse(queue_created)

    def test_cancel_uses_canonical_request_and_ignores_ui_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                state_patch=self._active_close_state(),
            )
            config_before = (stock / "config.json").read_bytes()
            orders_before = (stock / "orders.json").read_bytes()
            with patch("operation_command_service.observe_liquidation_requested"):
                result = command.execute_early_close_cancel_command(
                    self._owner(),
                    stock,
                    CODE,
                    source="test",
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                    irreversible_evidence_reader=lambda *_args: "",
                    command_service_factory=OperationCommandService,
                )
            saved = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            config_after = (stock / "config.json").read_bytes()
            orders_after = (stock / "orders.json").read_bytes()
            queue_created = (root / "runtime" / "order_queue.json").exists()

        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertEqual("", saved["early_close_requested_at"])
        self.assertFalse(saved["liquidation_policy_forced"])
        self.assertEqual(config_before, config_after)
        self.assertEqual(orders_before, orders_after)
        self.assertFalse(queue_created)

    def test_cancel_without_request_and_irreversible_request_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root)
            no_request = command.inspect_close_liquidation_availability(
                self._owner(),
                stock,
                CODE,
                intent=command.EARLY_CLOSE_CANCEL,
                recovery_inspector=self._recovery_allowed,
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))
            state.update(
                self._active_close_state()
            )
            (stock / "state.json").write_text(json.dumps(state), encoding="utf-8")
            irreversible = command.inspect_close_liquidation_availability(
                self._owner(),
                stock,
                CODE,
                intent=command.EARLY_CLOSE_CANCEL,
                recovery_inspector=self._recovery_allowed,
                irreversible_evidence_reader=lambda *_args: "ORDER_QUEUED",
            )

        self.assertEqual("NOT_CANCELABLE", no_request.reason_code)
        self.assertEqual("NOT_CANCELABLE", irreversible.reason_code)
        self.assertEqual(("ORDER_QUEUED",), irreversible.evidence)

    def test_cancel_skips_participant_review_emergency_and_recovery_guards(self) -> None:
        cases = (
            (
                "review",
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_status": "REQUIRED",
                    "review_reason": "fixture-review",
                },
            ),
            (
                "emergency",
                {
                    "status": "EMERGENCY_STOPPED",
                    "emergency_reason": "fixture-emergency",
                },
            ),
        )
        for label, status_patch in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                stock = self._stock(
                    root,
                    state_patch=self._active_close_state(**status_patch),
                )
                recovery = Mock(
                    side_effect=AssertionError("cancel must not inspect Recovery")
                )
                with patch("operation_command_service.observe_liquidation_requested"):
                    result = command.execute_early_close_cancel_command(
                        self._owner(participant=False),
                        stock,
                        CODE,
                        source="test",
                        project_root=root,
                        recovery_inspector=recovery,
                        expected_command_id="early-command",
                        irreversible_evidence_reader=lambda *_args: "",
                        command_service_factory=OperationCommandService,
                    )
                saved = json.loads(
                    (stock / "state.json").read_text(encoding="utf-8")
                )

                self.assertTrue(result.ok, result)
                self.assertTrue(result.changed)
                self.assertEqual("", saved["early_close_requested_at"])
                self.assertEqual(status_patch["status"], saved["status"])
                for key, value in status_patch.items():
                    if key != "status":
                        self.assertEqual(value, saved[key])
                recovery.assert_not_called()

    def test_cancel_allows_missing_recovery_context_without_broker_io(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, state_patch=self._active_close_state())
            broker_calls = {
                name: Mock()
                for name in ("CommRqData", "SendOrder")
            }
            owner = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner(),
                startup_recovery_session_ready=lambda refresh=False: False,
                **broker_calls,
            )
            with patch("operation_command_service.observe_liquidation_requested"):
                result = command.execute_early_close_cancel_command(
                    owner,
                    stock,
                    CODE,
                    source="test",
                    project_root=root,
                    expected_command_id="early-command",
                    irreversible_evidence_reader=lambda *_args: "",
                    command_service_factory=OperationCommandService,
                )

        self.assertTrue(result.ok, result)
        for broker_call in broker_calls.values():
            broker_call.assert_not_called()

    def test_cancel_blocks_every_irreversible_stage_and_preserves_bytes(self) -> None:
        stages = (
            "ORDER_QUEUED",
            "DISPATCH_CLAIMED",
            "SEND_ORDER_CALLED",
            "SEND_CALL_ACCEPTED",
            "BROKER_ACCEPTED",
            "PARTIALLY_FILLED",
            "FILLED",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, state_patch=self._active_close_state())
            for stage in stages:
                with self.subTest(stage=stage):
                    before = {
                        path.name: path.read_bytes()
                        for path in stock.iterdir()
                        if path.is_file()
                    }
                    result = command.inspect_close_liquidation_availability(
                        self._owner(participant=False),
                        stock,
                        CODE,
                        intent=command.EARLY_CLOSE_CANCEL,
                        recovery_inspector=Mock(
                            side_effect=AssertionError("Recovery must be skipped")
                        ),
                        expected_command_id="early-command",
                        irreversible_evidence_reader=lambda *_args, value=stage: value,
                    )
                    after = {
                        path.name: path.read_bytes()
                        for path in stock.iterdir()
                        if path.is_file()
                    }

                    self.assertFalse(result.allowed)
                    self.assertEqual("NOT_CANCELABLE", result.reason_code)
                    self.assertEqual((stage,), result.evidence)
                    self.assertEqual(before, after)

    def test_cancel_blocks_changed_or_missing_command_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, state_patch=self._active_close_state())
            changed = command.inspect_close_liquidation_availability(
                self._owner(),
                stock,
                CODE,
                intent=command.EARLY_CLOSE_CANCEL,
                expected_command_id="other-command",
                irreversible_evidence_reader=lambda *_args: "",
            )
            state_path = stock / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["operation_command_mode"] = "NORMAL"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            missing = command.inspect_close_liquidation_availability(
                self._owner(),
                stock,
                CODE,
                intent=command.EARLY_CLOSE_CANCEL,
                irreversible_evidence_reader=lambda *_args: "",
            )

        self.assertEqual("COMMAND_IDENTITY_CHANGED", changed.reason_code)
        self.assertEqual("COMMAND_IDENTITY_MISSING", missing.reason_code)

    def test_early_close_execute_revalidates_current_session_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root)
            owner = self._owner()
            close_intent = Mock(
                return_value={
                    "ok": True,
                    "durable_applied": True,
                    "blocked": False,
                    "reason": "",
                    "command_result": SimpleNamespace(command_id="early-1"),
                }
            )
            with patch.object(command, "apply_close_intent", close_intent):
                allowed = command.execute_early_close_request_command(
                    owner,
                    stock,
                    CODE,
                    method="시장가",
                    source="test",
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                )
                owner._main_monitoring_auto_trade_operation_host = participant_owner()
                blocked = command.execute_early_close_request_command(
                    owner,
                    stock,
                    CODE,
                    method="시장가",
                    source="test",
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                )

        self.assertTrue(allowed.ok)
        self.assertEqual("NOT_CURRENT_PARTICIPANT", blocked.reason_code)
        close_intent.assert_called_once()

    def test_manual_ats_request_uses_current_session_final_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                state_patch={
                    "manual_ats_selection": {"selected_sessions": ["extra2"]},
                },
            )
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))
            config["operation_mode"] = "CONTINUOUS"
            (stock / "config.json").write_text(json.dumps(config), encoding="utf-8")
            preview = {
                "ok": True,
                "stock_dir": str(stock),
                "code": CODE,
                "command_id": "ats-1",
                "sell_method": "MARKET",
            }
            writer = Mock()
            with patch.object(command, "manual_ats_active_now", return_value=True):
                blocked = command.execute_manual_ats_liquidation_request_command(
                    self._owner(participant=False),
                    preview,
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                    request_writer=writer,
                )

        self.assertEqual("NOT_CURRENT_PARTICIPANT", blocked.reason_code)
        writer.assert_not_called()

    def test_manual_ats_request_persists_requested_without_queue_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                state_patch={
                    "manual_ats_selection": {"selected_sessions": ["extra2"]},
                },
            )
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))
            config["operation_mode"] = "CONTINUOUS"
            (stock / "config.json").write_text(json.dumps(config), encoding="utf-8")
            preview = {
                "ok": True,
                "stock_dir": str(stock),
                "code": CODE,
                "command_id": "ats-1",
                "sell_method": "MARKET",
            }
            writer = Mock(
                return_value={
                    "ok": True,
                    "stage": "runtime_request",
                    "request_status": "REQUESTED",
                    "command_result": SimpleNamespace(command_id="ats-1"),
                    "blocked_reasons": [],
                }
            )
            with patch.object(command, "manual_ats_active_now", return_value=True):
                result = command.execute_manual_ats_liquidation_request_command(
                    self._owner(),
                    preview,
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                    request_writer=writer,
                )
            queue_created = (root / "runtime" / "order_queue.json").exists()

        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertTrue(result.requested)
        writer.assert_called_once()
        self.assertFalse(queue_created)

    def test_manual_ats_duplicate_request_is_reused_without_new_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                state_patch={
                    "manual_ats_selection": {"selected_sessions": ["extra2"]},
                },
            )
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))
            config["operation_mode"] = "CONTINUOUS"
            (stock / "config.json").write_text(json.dumps(config), encoding="utf-8")
            preview = {
                "ok": True,
                "stock_dir": str(stock),
                "code": CODE,
                "command_id": "ats-duplicate",
                "sell_method": "MARKET",
            }
            writer = Mock(
                return_value={
                    "ok": True,
                    "stage": "runtime_request_reused",
                    "request_status": "READY_TO_RESUME",
                    "command_result": SimpleNamespace(command_id="ats-duplicate"),
                    "blocked_reasons": [],
                }
            )
            with patch.object(command, "manual_ats_active_now", return_value=True):
                result = command.execute_manual_ats_liquidation_request_command(
                    self._owner(),
                    preview,
                    project_root=root,
                    recovery_inspector=self._recovery_allowed,
                    request_writer=writer,
                )

        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertTrue(result.requested)
        self.assertEqual("DUPLICATE", result.reason_code)


if __name__ == "__main__":
    unittest.main()
