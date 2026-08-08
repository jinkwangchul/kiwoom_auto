# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import operation_policy_gate
from close_intent_service import (
    CLOSE_INTENT_AUTO_CLOSE,
    CLOSE_INTENT_EARLY_CLOSE,
    apply_close_intent,
)
from operation_command_service import OperationCommandService, STOCK_APPLIED


class _Transition:
    def __init__(self, allowed: bool, reason_code: str = "OK") -> None:
        self.allowed = allowed
        self.reason_code = reason_code
        self.evidence_status = "COMPLETE" if allowed else "UNKNOWN"


def _allowed_guard(**_kwargs):
    return _Transition(True)


def _blocked_guard(**_kwargs):
    return _Transition(False, "BLOCKED_BY_TEST")


class CloseIntentServiceTest(unittest.TestCase):
    def _stock_root(self, root: Path, name: str = "111111_Test") -> Path:
        stock_dir = root / "stocks" / name
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": "routine-1"}),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "operation_sequence": 0,
                    "holding_qty": 1,
                    "trade_enabled": True,
                }
            ),
            encoding="utf-8",
        )
        return stock_dir

    def _operation_state_path(self, root: Path, data: dict[str, object]) -> Path:
        path = root / "runtime" / "operation_state.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_auto_close_intent_uses_guard_writer_and_read_back(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock_root(Path(temp))
            calls = []

            def writer(target_dir, code, name, status, metadata, log_suffix):
                calls.append((code, name, status, metadata, log_suffix))
                state = json.loads((target_dir / "state.json").read_text(encoding="utf-8"))
                state["status"] = status
                state.update(metadata)
                (target_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
                return True

            def read_back(target_dir, status, metadata):
                state = json.loads((target_dir / "state.json").read_text(encoding="utf-8"))
                return state.get("status") == status and all(
                    state.get(key) == value for key, value in metadata.items()
                )

            operation_state_path = self._operation_state_path(
                Path(temp),
                {
                    "operation_date": "2026-07-30",
                    "operation_status": "RUNNING",
                    "operation_started_at": "2026-07-30 09:00:00",
                    "operation_updated_at": "2026-07-30 09:00:00",
                    "operation_participant_stock_codes": ["111111", "222222"],
                    "emergency_stop": True,
                    "existing_key": "preserved",
                },
            )
            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(operation_policy_gate, "now_text", return_value="2026-07-30 13:30:00"),
            ):
                result = apply_close_intent(
                    intent=CLOSE_INTENT_AUTO_CLOSE,
                    stock_dir=stock_dir,
                    stock_code="111111",
                    stock_name="Test",
                    runtime_state={"status": "RUNNING"},
                    runtime_config={"assigned_routine_instance_id": "routine-1"},
                    current_status="RUNNING",
                    requested_status="AUTO_CLOSE",
                    metadata={
                        "auto_close_method": "market",
                        "auto_close_requested_at": "2026-07-30 13:30:00",
                        "auto_close_source": "TIME_POLICY",
                    },
                    log_suffix="auto close",
                    status_writer=writer,
                    read_back_checker=read_back,
                    queue_path=Path(temp) / "order_queue.json",
                    fills_path=Path(temp) / "fills.json",
                    transition_guard=_allowed_guard,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["durable_applied"])
            self.assertTrue(result["read_back_verified"])
            self.assertTrue(result["operation_closing_applied"])
            self.assertFalse(result["operation_state_write_failed"])
            self.assertEqual(1, len(calls))
            self.assertEqual("AUTO_CLOSE", calls[0][2])
            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))
            self.assertEqual("CLOSING", operation_state["operation_status"])
            self.assertEqual("AUTO_CLOSE", operation_state["operation_close_reason"])
            self.assertEqual("2026-07-30 13:30:00", operation_state["operation_closing_started_at"])
            self.assertEqual("2026-07-30 09:00:00", operation_state["operation_started_at"])
            self.assertEqual(["111111", "222222"], operation_state["operation_participant_stock_codes"])
            self.assertTrue(operation_state["emergency_stop"])
            self.assertEqual("preserved", operation_state["existing_key"])

    def test_auto_close_intent_blocks_before_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock_root(Path(temp))
            calls = []
            result = apply_close_intent(
                intent=CLOSE_INTENT_AUTO_CLOSE,
                stock_dir=stock_dir,
                stock_code="111111",
                stock_name="Test",
                runtime_state={"status": "RUNNING"},
                runtime_config={"assigned_routine_instance_id": "routine-1"},
                current_status="RUNNING",
                requested_status="AUTO_CLOSE",
                metadata={"auto_close_method": "market"},
                log_suffix="auto close",
                status_writer=lambda *args: calls.append(args) or True,
                read_back_checker=lambda *args: True,
                transition_guard=_blocked_guard,
                operation_state_writer=lambda **_kwargs: {"ok": True},
            )

            self.assertFalse(result["ok"])
            self.assertTrue(result["blocked"])
            self.assertEqual("BLOCKED_BY_TEST", result["reason"])
            self.assertEqual([], calls)

    def test_auto_close_writer_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock_root(Path(temp))
            result = apply_close_intent(
                intent=CLOSE_INTENT_AUTO_CLOSE,
                stock_dir=stock_dir,
                stock_code="111111",
                stock_name="Test",
                runtime_state={"status": "RUNNING"},
                runtime_config={"assigned_routine_instance_id": "routine-1"},
                current_status="RUNNING",
                requested_status="AUTO_CLOSE",
                metadata={"auto_close_method": "market"},
                log_suffix="auto close",
                status_writer=lambda *args: False,
                read_back_checker=lambda *args: True,
                transition_guard=_allowed_guard,
                operation_state_writer=lambda **_kwargs: {"ok": True},
            )

            self.assertFalse(result["ok"])
            self.assertFalse(result["durable_applied"])
            self.assertEqual("WRITE_FAILED", result["reason"])

    def test_auto_close_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock_root(Path(temp))
            calls = []
            result = apply_close_intent(
                intent=CLOSE_INTENT_AUTO_CLOSE,
                stock_dir=stock_dir,
                stock_code="111111",
                stock_name="Test",
                runtime_state={"status": "RUNNING"},
                runtime_config={"assigned_routine_instance_id": "routine-1"},
                current_status="RUNNING",
                requested_status="AUTO_CLOSE",
                metadata={"auto_close_method": "market"},
                log_suffix="auto close",
                status_writer=lambda *args: calls.append(args) or True,
                read_back_checker=lambda *args: True,
                transition_guard=_allowed_guard,
                dry_run=True,
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["durable_applied"])
            self.assertEqual([], calls)

    def test_early_close_intent_uses_operation_command_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._stock_root(root)
            operation_state_path = self._operation_state_path(
                root,
                {
                    "operation_date": "2026-07-30",
                    "operation_status": "RUNNING",
                    "operation_started_at": "2026-07-30 09:00:00",
                    "operation_participant_stock_codes": ["111111"],
                    "emergency_stop": False,
                },
            )
            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(operation_policy_gate, "now_text", return_value="2026-07-30 13:31:00"),
            ):
                result = apply_close_intent(
                    intent=CLOSE_INTENT_EARLY_CLOSE,
                    target_scope="STOCK",
                    target_id=str(stock_dir.resolve()),
                    source="test",
                    requested_policy="market",
                    stock_code="111111",
                    runtime_state={"status": "RUNNING"},
                    runtime_routine_instance_id="routine-1",
                    current_policy="routine",
                    current_started_at="2026-07-30 09:00:00",
                    current_command_id="",
                    requested_at="2026-07-30 13:30:00",
                    project_root=root,
                    transition_guard=_allowed_guard,
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["operation_closing_applied"])
            command_result = result["command_result"]
            self.assertEqual(STOCK_APPLIED, command_result.stock_results[0].status)
            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("EARLY_CLOSE", saved["status"])
            self.assertEqual("EARLY_CLOSE", saved["operation_command_mode"])
            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))
            self.assertEqual("CLOSING", operation_state["operation_status"])
            self.assertEqual("EARLY_CLOSE", operation_state["operation_close_reason"])

    def test_early_close_duplicate_does_not_reapply_durable_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._stock_root(root)
            kwargs = dict(
                intent=CLOSE_INTENT_EARLY_CLOSE,
                target_scope="STOCK",
                target_id=str(stock_dir.resolve()),
                source="test",
                requested_policy="market",
                stock_code="111111",
                runtime_state={"status": "RUNNING"},
                runtime_routine_instance_id="routine-1",
                current_policy="routine",
                current_started_at="2026-07-30 09:00:00",
                current_command_id="",
                command_id="fixed-command",
                requested_at="2026-07-30 13:30:00",
                project_root=root,
                transition_guard=_allowed_guard,
                operation_state_writer=lambda close_reason: {"ok": True, "reason": close_reason},
            )

            first = apply_close_intent(**kwargs)
            second = apply_close_intent(**kwargs)

            self.assertTrue(first["durable_applied"])
            self.assertFalse(second["durable_applied"])
            self.assertTrue(second["ok"])
            self.assertTrue(first["operation_closing_applied"])
            self.assertFalse(second["operation_closing_applied"])

    def test_early_close_writer_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._stock_root(root)

            def factory(project_root):
                return OperationCommandService(
                    project_root,
                    atomic_writer=lambda *_args: {"status": "FAILED", "error": "write failed"},
                )

            result = apply_close_intent(
                intent=CLOSE_INTENT_EARLY_CLOSE,
                target_scope="STOCK",
                target_id=str(stock_dir.resolve()),
                source="test",
                requested_policy="market",
                stock_code="111111",
                runtime_state={"status": "RUNNING"},
                runtime_routine_instance_id="routine-1",
                current_policy="routine",
                current_started_at="2026-07-30 09:00:00",
                requested_at="2026-07-30 13:30:00",
                project_root=root,
                operation_command_service_factory=factory,
                transition_guard=_allowed_guard,
                operation_state_writer=lambda **_kwargs: {"ok": True},
            )

            self.assertFalse(result["ok"])
            self.assertEqual("write failed", result["reason"])

    def test_operation_state_writer_failure_does_not_roll_back_stock_intent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._stock_root(root)
            result = apply_close_intent(
                intent=CLOSE_INTENT_EARLY_CLOSE,
                target_scope="STOCK",
                target_id=str(stock_dir.resolve()),
                source="test",
                requested_policy="market",
                stock_code="111111",
                runtime_state={"status": "RUNNING"},
                runtime_routine_instance_id="routine-1",
                current_policy="routine",
                current_started_at="2026-07-30 09:00:00",
                requested_at="2026-07-30 13:30:00",
                project_root=root,
                transition_guard=_allowed_guard,
                operation_state_writer=lambda **_kwargs: {
                    "ok": False,
                    "reason": "operation state write failed",
                },
            )

            self.assertFalse(result["ok"])
            self.assertTrue(result["durable_applied"])
            self.assertFalse(result["operation_closing_applied"])
            self.assertTrue(result["operation_state_write_failed"])
            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("EARLY_CLOSE", saved["status"])

    def test_unsupported_one_shot_intent_does_not_write_operation_state(self):
        calls = []
        result = apply_close_intent(
            intent="IMMEDIATE_LIQUIDATION",
            stock_code="111111",
            operation_state_writer=lambda **kwargs: calls.append(kwargs) or {"ok": True},
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual([], calls)

    def test_closing_writer_blocks_without_today_running_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = self._operation_state_path(
                root,
                {"operation_date": "2026-07-30", "operation_status": "IDLE"},
            )
            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(operation_policy_gate, "now_text", return_value="2026-07-30 13:30:00"),
            ):
                result = operation_policy_gate.write_global_operation_closing_state(
                    close_reason="AUTO_CLOSE"
                )

            self.assertFalse(result["ok"])
            self.assertTrue(result["blocked"])
            self.assertEqual(
                {"operation_date": "2026-07-30", "operation_status": "IDLE"},
                json.loads(operation_state_path.read_text(encoding="utf-8")),
            )

    def test_closing_writer_preserves_first_started_at_and_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = self._operation_state_path(
                root,
                {
                    "operation_date": "2026-07-30",
                    "operation_status": "CLOSING",
                    "operation_started_at": "2026-07-30 09:00:00",
                    "operation_closing_started_at": "2026-07-30 13:30:00",
                    "operation_close_reason": "AUTO_CLOSE",
                    "operation_participant_stock_codes": ["111111"],
                },
            )
            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(operation_policy_gate, "now_text", return_value="2026-07-30 13:35:00"),
            ):
                result = operation_policy_gate.write_global_operation_closing_state(
                    close_reason="EARLY_CLOSE"
                )

            self.assertTrue(result["ok"])
            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))
            self.assertEqual("CLOSING", operation_state["operation_status"])
            self.assertEqual("2026-07-30 13:30:00", operation_state["operation_closing_started_at"])
            self.assertEqual("AUTO_CLOSE", operation_state["operation_close_reason"])
            self.assertEqual("2026-07-30 13:35:00", operation_state["operation_updated_at"])


if __name__ == "__main__":
    unittest.main()
