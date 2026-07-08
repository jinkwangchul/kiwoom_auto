# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_readiness_validator import validate_execution_readiness
from rule_apply_preview_execution_preview_controller import preview_execution_from_rule_apply_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class ExecutionReadinessValidatorTest(unittest.TestCase):
    def _apply_preview(self) -> dict[str, object]:
        return {
            "mode": "approved_rule_apply_preview",
            "stage": "RULE_APPLY_PREVIEW",
            "applied_rules_preview": {
                "bar": {"bar_minutes": 5},
                "buy": {"enabled": True},
                "sell": {"enabled": True},
            },
            "applied_patches": [{"operation": "set_value", "target_path": "bar.bar_minutes"}],
            "skipped_patches": [],
            "summary": {"patches": 1, "applied": 1, "skipped": 0},
            "warnings": [],
        }

    def _signal_context(self) -> dict[str, object]:
        return {
            "order_id": "ORDER_READINESS_1",
            "source_signal_id": "SIGNAL_READINESS_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "\uc2dc\uc7a5\uac00",
        }

    def _guard(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "account_no": "12345678",
        }
        result.update(overrides)
        return result

    def _runtime_snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "locks": [],
            "existing_orders": [],
        }
        result.update(overrides)
        return result

    def _operation_state(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "READY",
            "emergency_stop": False,
            "operation_allowed": True,
        }
        result.update(overrides)
        return result

    def _preview_controller_result(self) -> dict[str, object]:
        result = preview_execution_from_rule_apply_preview(
            self._apply_preview(),
            self._signal_context(),
            guard=self._guard(),
        )
        self.assertEqual("READY", result["status"])
        return result

    def test_ready_normal(self) -> None:
        result = validate_execution_readiness(
            self._preview_controller_result(),
            self._guard(),
            self._runtime_snapshot(),
            self._operation_state(),
        )

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["readiness"]["queue_commit_ready"])
        self.assertTrue(result["validation_summary"]["ready"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["queue_commit_called"])

    def test_preview_controller_blocked_returns_blocked(self) -> None:
        preview = self._preview_controller_result()
        preview["status"] = "BLOCKED"
        result = validate_execution_readiness(
            preview,
            self._guard(),
            self._runtime_snapshot(),
            self._operation_state(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("preview_controller_result.status is not READY", result["issues"])

    def test_missing_required_order_field_is_invalid(self) -> None:
        preview = self._preview_controller_result()
        preview["order_contract"].pop("code")
        result = validate_execution_readiness(
            preview,
            self._guard(),
            self._runtime_snapshot(),
            self._operation_state(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("order_contract.code is required", result["issues"])

    def test_guard_not_satisfied_is_blocked(self) -> None:
        result = validate_execution_readiness(
            self._preview_controller_result(),
            self._guard(operator_confirmed=False, account_no=""),
            self._runtime_snapshot(),
            self._operation_state(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("guard.operator_confirmed is not true", result["issues"])
        self.assertIn("guard.account_no is required", result["issues"])

    def test_runtime_lock_blocks(self) -> None:
        result = validate_execution_readiness(
            self._preview_controller_result(),
            self._guard(),
            self._runtime_snapshot(locks=[{"order_id": "ORDER_READINESS_1"}]),
            self._operation_state(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("runtime lock exists for order", result["issues"])

    def test_duplicate_order_blocks(self) -> None:
        result = validate_execution_readiness(
            self._preview_controller_result(),
            self._guard(),
            self._runtime_snapshot(existing_orders=[{"order_id": "ORDER_READINESS_1"}]),
            self._operation_state(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("duplicate order exists", result["issues"])

    def test_emergency_stop_blocks(self) -> None:
        result = validate_execution_readiness(
            self._preview_controller_result(),
            self._guard(),
            self._runtime_snapshot(),
            self._operation_state(emergency_stop=True),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("operation_state.emergency_stop is true", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        preview = self._preview_controller_result()
        guard = self._guard()
        runtime_snapshot = self._runtime_snapshot()
        operation_state = self._operation_state()
        originals = (
            deepcopy(preview),
            deepcopy(guard),
            deepcopy(runtime_snapshot),
            deepcopy(operation_state),
        )

        result = validate_execution_readiness(preview, guard, runtime_snapshot, operation_state)
        result["readiness"]["queue_commit_ready"] = False

        self.assertEqual(originals[0], preview)
        self.assertEqual(originals[1], guard)
        self.assertEqual(originals[2], runtime_snapshot)
        self.assertEqual(originals[3], operation_state)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order:
            result = validate_execution_readiness(
                self._preview_controller_result(),
                self._guard(),
                self._runtime_snapshot(),
                self._operation_state(),
            )

        self.assertEqual("READY", result["status"])
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
