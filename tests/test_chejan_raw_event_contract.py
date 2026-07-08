# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from chejan_raw_event_contract import build_chejan_raw_event_contract


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


class ChejanRawEventContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "policy_type": "CHEJAN_ENTRY_OPEN_POLICY",
            "status": "CHEJAN_ENTRY_OPEN",
            "policy": {
                "policy_stage": "chejan_entry_open_policy_evaluated",
                "chejan_entry_open_allowed": True,
                "identity": {
                    "record_id": "SEND_ORDER_RECORD_RAW_1",
                    "order_id": "ORDER_RAW_1",
                    "dispatch_id": "DISPATCH_RAW_1",
                    "source_order_id": "SOURCE_ORDER_RAW_1",
                    "source_signal_id": "SIGNAL_RAW_1",
                    "order_queued_id": "ORDER_QUEUED_RAW_1",
                    "request_hash": "REQUEST_HASH_RAW_1",
                    "lock_id": "LOCK_RAW_1",
                    "execution_id": "EXEC_RAW_1",
                },
                "next_stage": "CHEJAN_EVENT_RECEIVE_REQUIRED",
                "chejan_live_connected": False,
                "runtime_write": False,
                "queue_write": False,
                "lifecycle_created": False,
            },
            "issues": [],
            "warnings": [],
            "chejan_live_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "lifecycle_created": False,
        }
        result.update(overrides)
        return result

    def _raw(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "event_type": "RAW_CHEJAN_EVENT",
            "order_id": "ORDER_RAW_1",
            "dispatch_id": "DISPATCH_RAW_1",
            "source_signal_id": "SIGNAL_RAW_1",
            "broker_order_no": "12345",
            "received_at": "2026-07-07 10:00:00",
            "fid_values": {
                "9201": "12345678",
                "9203": "12345",
                "9001": "A003550",
                "913": "ORDER_OPEN",
            },
        }
        result.update(overrides)
        return result

    def _context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "chejan_event_enabled": True,
            "received_at": "2026-07-07 10:00:00",
            "operator_review_required": True,
        }
        result.update(overrides)
        return result

    def test_chejan_event_ready_normal(self) -> None:
        result = build_chejan_raw_event_contract(self._policy(), self._raw(), self._context())

        self.assertEqual("CHEJAN_EVENT_READY", result["status"])
        self.assertFalse(result["chejan_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["lifecycle_created"])
        contract = result["chejan_event_contract"]
        self.assertEqual("CHEJAN_EVENT_NORMALIZE_REQUIRED", contract["next_stage"])
        self.assertEqual("ORDER_RAW_1", contract["identity"]["order_id"])
        self.assertEqual("DISPATCH_RAW_1", contract["identity"]["dispatch_id"])
        self.assertEqual("SIGNAL_RAW_1", contract["identity"]["source_signal_id"])
        self.assertEqual("12345", contract["identity"]["broker_order_no"])

    def test_policy_blocked_is_blocked(self) -> None:
        result = build_chejan_raw_event_contract(
            self._policy(status="BLOCKED", issues=["blocked"]),
            self._raw(),
            self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("chejan_entry_policy_result.status is BLOCKED", result["issues"])

    def test_policy_invalid_is_invalid(self) -> None:
        result = build_chejan_raw_event_contract(
            self._policy(status="INVALID", issues=["bad"]),
            self._raw(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("chejan_entry_policy_result.status is INVALID", result["issues"])

    def test_raw_event_missing_is_blocked(self) -> None:
        result = build_chejan_raw_event_contract(self._policy(), None, self._context())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("raw_chejan_event is required", result["issues"])

    def test_event_context_disabled_is_blocked(self) -> None:
        result = build_chejan_raw_event_contract(
            self._policy(),
            self._raw(),
            self._context(chejan_event_enabled=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("chejan_event_context.chejan_event_enabled is not true", result["issues"])

    def test_identity_unlinkable_is_invalid(self) -> None:
        raw = self._raw(order_id="", broker_order_no="", fid_values={"9203": "", "913": "ORDER_OPEN"})

        result = build_chejan_raw_event_contract(self._policy(), raw, self._context())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("order_id or broker_order_no is required", result["issues"])

    def test_identity_mismatch_is_invalid(self) -> None:
        for field, value in (
            ("order_id", "OTHER_ORDER"),
            ("dispatch_id", "OTHER_DISPATCH"),
            ("source_signal_id", "OTHER_SIGNAL"),
        ):
            result = build_chejan_raw_event_contract(
                self._policy(),
                self._raw(**{field: value}),
                self._context(),
            )

            self.assertEqual("INVALID", result["status"])
            self.assertIn(f"{field} mismatch", result["issues"])

    def test_malformed_event_is_invalid(self) -> None:
        no_type = build_chejan_raw_event_contract(
            self._policy(),
            {"source": "kiwoom_chejan", "gubun": "0", "order_id": "ORDER_RAW_1"},
            self._context(),
        )
        no_gubun = build_chejan_raw_event_contract(
            self._policy(),
            self._raw(gubun=""),
            self._context(),
        )

        self.assertEqual("INVALID", no_type["status"])
        self.assertEqual("INVALID", no_gubun["status"])

    def test_malformed_input_is_invalid(self) -> None:
        self.assertEqual("INVALID", build_chejan_raw_event_contract(None, self._raw(), self._context())["status"])
        self.assertEqual("INVALID", build_chejan_raw_event_contract(self._policy(), self._raw(), {})["status"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        policy = self._policy()
        raw = self._raw()
        context = self._context()
        before = (deepcopy(policy), deepcopy(raw), deepcopy(context))

        result = build_chejan_raw_event_contract(policy, raw, context)
        result["chejan_event_contract"]["identity"]["order_id"] = "MUTATED_ORDER"
        result["chejan_event_contract"]["raw_chejan_event"]["order_id"] = "MUTATED_RAW"

        self.assertEqual(before, (policy, raw, context))
        fresh = build_chejan_raw_event_contract(policy, raw, context)
        self.assertEqual("ORDER_RAW_1", fresh["chejan_event_contract"]["identity"]["order_id"])
        self.assertEqual("ORDER_RAW_1", fresh["chejan_event_contract"]["raw_chejan_event"]["order_id"])

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        result = build_chejan_raw_event_contract(self._policy(), self._raw(), self._context())

        self.assertEqual("CHEJAN_EVENT_READY", result["status"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
