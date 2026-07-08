# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from chejan_entry_open_policy import evaluate_chejan_entry_open_policy


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


class ChejanEntryOpenPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _entry(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "adapter_type": "SEND_ORDER_RECORD_REVIEW_TO_CHEJAN_ENTRY_ADAPTER",
            "status": "CHEJAN_ENTRY_READY",
            "chejan_entry_contract": {
                "contract_type": "CHEJAN_ENTRY_CONTRACT",
                "next_stage": "CHEJAN_ENTRY_OPEN_POLICY_REQUIRED",
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_POLICY_1",
                    "order_id": "ORDER_CHEJAN_POLICY_1",
                    "dispatch_id": "DISPATCH_CHEJAN_POLICY_1",
                    "source_order_id": "SOURCE_ORDER_CHEJAN_POLICY_1",
                    "source_signal_id": "SIGNAL_CHEJAN_POLICY_1",
                    "order_queued_id": "ORDER_QUEUED_CHEJAN_POLICY_1",
                    "request_hash": "REQUEST_HASH_CHEJAN_POLICY_1",
                    "lock_id": "LOCK_CHEJAN_POLICY_1",
                    "execution_id": "EXEC_CHEJAN_POLICY_1",
                },
                "chejan_live_connected": False,
                "chejan_called": False,
                "runtime_write": False,
                "queue_write": False,
                "lifecycle_created": False,
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "chejan_called": False,
            "runtime_write": False,
            "queue_write": False,
            "lifecycle_created": False,
        }
        result.update(overrides)
        return result

    def _runtime(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "chejan_runtime_enabled": True,
            "chejan_entry_enabled": True,
            "duplicate_entry_ids": [],
            "existing_entry_ids": [],
            "active_chejan_entries": [],
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def _operation(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "READY",
            "operation_allowed": True,
            "chejan_allowed": True,
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def test_chejan_entry_open_normal(self) -> None:
        result = evaluate_chejan_entry_open_policy(
            self._entry(),
            self._runtime(),
            self._operation(),
        )

        self.assertEqual("CHEJAN_ENTRY_OPEN", result["status"])
        self.assertFalse(result["chejan_live_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lifecycle_created"])
        self.assertTrue(result["policy"]["chejan_entry_open_allowed"])
        self.assertEqual("CHEJAN_EVENT_RECEIVE_REQUIRED", result["policy"]["next_stage"])

    def test_entry_blocked_is_blocked(self) -> None:
        result = evaluate_chejan_entry_open_policy(
            self._entry(status="BLOCKED", issues=["blocked"]),
            self._runtime(),
            self._operation(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("chejan_entry_contract_result.status is BLOCKED", result["issues"])

    def test_entry_invalid_is_invalid(self) -> None:
        result = evaluate_chejan_entry_open_policy(
            self._entry(status="INVALID", issues=["bad"]),
            self._runtime(),
            self._operation(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("chejan_entry_contract_result.status is INVALID", result["issues"])

    def test_runtime_disabled_is_blocked(self) -> None:
        result = evaluate_chejan_entry_open_policy(
            self._entry(),
            self._runtime(chejan_runtime_enabled=False),
            self._operation(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("chejan_runtime_context.chejan_runtime_enabled is not true", result["issues"])

    def test_emergency_stop_is_blocked(self) -> None:
        runtime_result = evaluate_chejan_entry_open_policy(
            self._entry(),
            self._runtime(emergency_stop=True),
            self._operation(),
        )
        operation_result = evaluate_chejan_entry_open_policy(
            self._entry(),
            self._runtime(),
            self._operation(emergency_stop=True),
        )

        self.assertEqual("BLOCKED", runtime_result["status"])
        self.assertEqual("BLOCKED", operation_result["status"])
        self.assertIn("chejan_runtime_context.emergency_stop is true", runtime_result["issues"])
        self.assertIn("operation_state.emergency_stop is true", operation_result["issues"])

    def test_duplicate_entry_is_blocked(self) -> None:
        for key, value in (
            ("duplicate_entry_ids", ["SEND_ORDER_RECORD_POLICY_1"]),
            ("existing_entry_ids", ["ORDER_CHEJAN_POLICY_1"]),
            ("active_chejan_entries", [{"dispatch_id": "DISPATCH_CHEJAN_POLICY_1"}]),
            ("duplicate_entry", True),
        ):
            result = evaluate_chejan_entry_open_policy(
                self._entry(),
                self._runtime(**{key: value}),
                self._operation(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("duplicate Chejan entry exists", result["issues"])

    def test_operation_state_blocked_is_blocked(self) -> None:
        result = evaluate_chejan_entry_open_policy(
            self._entry(),
            self._runtime(),
            self._operation(status="HALTED"),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("operation_state.status is blocked", result["issues"])

    def test_malformed_input_is_invalid(self) -> None:
        self.assertEqual(
            "INVALID",
            evaluate_chejan_entry_open_policy(None, self._runtime(), self._operation())["status"],
        )
        self.assertEqual(
            "INVALID",
            evaluate_chejan_entry_open_policy(self._entry(), {}, self._operation())["status"],
        )
        self.assertEqual(
            "INVALID",
            evaluate_chejan_entry_open_policy(self._entry(), self._runtime(), {})["status"],
        )

    def test_missing_identity_field_is_invalid(self) -> None:
        entry = self._entry()
        entry["chejan_entry_contract"]["identity"]["dispatch_id"] = ""

        result = evaluate_chejan_entry_open_policy(entry, self._runtime(), self._operation())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("identity missing fields: dispatch_id", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        entry = self._entry()
        runtime = self._runtime()
        operation = self._operation()
        before = (deepcopy(entry), deepcopy(runtime), deepcopy(operation))

        result = evaluate_chejan_entry_open_policy(entry, runtime, operation)
        result["policy"]["identity"]["order_id"] = "MUTATED_ORDER"

        self.assertEqual(before, (entry, runtime, operation))
        fresh = evaluate_chejan_entry_open_policy(entry, runtime, operation)
        self.assertEqual("ORDER_CHEJAN_POLICY_1", fresh["policy"]["identity"]["order_id"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = evaluate_chejan_entry_open_policy(self._entry(), self._runtime(), self._operation())

        self.assertEqual("CHEJAN_ENTRY_OPEN", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
