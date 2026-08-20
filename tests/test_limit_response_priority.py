# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from buffer_response_ownership_service import BufferResponseOwnershipService
from limit_response_priority import (
    BUFFER_CLEAR,
    BUFFER_OWNS,
    BUFFER_UNCERTAIN,
    ROUTINE_CLEAR,
    ROUTINE_OWNS,
    ROUTINE_UNCERTAIN,
    STAGE_ROUTINE,
    STAGE_STOCK,
    arbitrate_limit_response_priority,
)
from routine_limit_response_ownership_service import (
    INTENT_EARLY_CLOSE,
    RoutineLimitResponseOwnershipService,
)


ACCOUNT = "81291234"
DAY = "2026-08-21"


def buffer_clear(**updates):
    result = {
        "stable": True,
        "ingress_committed": True,
        "ownership_claimed": False,
        "ownership_existing": False,
        "event_created": False,
        "policy_projected": False,
    }
    result.update(updates)
    return result


def routine_clear(**updates):
    result = {"evaluated": True, "settled": True, "owns_response": False}
    result.update(updates)
    return result


class _BufferOwnership:
    def __init__(self, *, ok=True, codes=()):
        self.ok = ok
        self.codes = tuple(codes)

    def active_owned_stock_codes(self, **_kwargs):
        return {"ok": self.ok, "stock_codes": self.codes, "reason": "broken" if not self.ok else ""}


class _RoutineOwnership:
    def __init__(self, *, ok=True, events=None):
        self.ok = ok
        self.events = dict(events or {})

    def read_snapshot(self):
        return {"ok": self.ok, "snapshot": {"events": self.events}, "reason": "broken" if not self.ok else ""}


class LimitResponsePriorityTests(unittest.TestCase):
    def arbitrate(self, *, stage, buffer_result=None, routine_result=None, buffer=None, routine=None):
        return arbitrate_limit_response_priority(
            account_no=ACCOUNT,
            trading_day=DAY,
            buffer_result=buffer_clear() if buffer_result is None else buffer_result,
            routine_result=routine_result,
            stage=stage,
            buffer_ownership=buffer or _BufferOwnership(),
            routine_ownership=routine or _RoutineOwnership(),
        )

    def test_buffer_clear_allows_routine(self) -> None:
        result = self.arbitrate(stage=STAGE_ROUTINE)
        self.assertTrue(result["admitted"])
        self.assertEqual(BUFFER_CLEAR, result["buffer_status"])

    def test_buffer_current_or_existing_ownership_blocks_all_lower_layers(self) -> None:
        current = self.arbitrate(
            stage=STAGE_ROUTINE,
            buffer_result=buffer_clear(ownership_claimed=True),
        )
        existing = self.arbitrate(
            stage=STAGE_STOCK,
            routine_result=routine_clear(),
            buffer=_BufferOwnership(codes=("005930",)),
        )
        self.assertEqual(BUFFER_OWNS, current["buffer_status"])
        self.assertFalse(current["admitted"])
        self.assertEqual(BUFFER_OWNS, existing["buffer_status"])
        self.assertEqual(("005930",), existing["active_buffer_stock_codes"])
        self.assertFalse(existing["admitted"])

    def test_buffer_uncertain_or_malformed_blocks_routine_and_stock(self) -> None:
        for value in ({}, buffer_clear(stable=False), buffer_clear(stable="yes")):
            with self.subTest(value=value):
                result = self.arbitrate(stage=STAGE_ROUTINE, buffer_result=value)
                self.assertEqual(BUFFER_UNCERTAIN, result["buffer_status"])
                self.assertFalse(result["admitted"])
        unavailable = self.arbitrate(stage=STAGE_STOCK, routine_result=routine_clear(), buffer=_BufferOwnership(ok=False))
        self.assertEqual(BUFFER_UNCERTAIN, unavailable["buffer_status"])

    def test_completed_buffer_without_active_ownership_allows_routine(self) -> None:
        result = self.arbitrate(stage=STAGE_ROUTINE, buffer_result=buffer_clear())
        self.assertTrue(result["admitted"])

    def test_routine_clear_allows_stock(self) -> None:
        result = self.arbitrate(stage=STAGE_STOCK, routine_result=routine_clear())
        self.assertTrue(result["admitted"])
        self.assertEqual(ROUTINE_CLEAR, result["routine_status"])

    def test_routine_current_or_existing_ownership_blocks_stock_globally(self) -> None:
        current = self.arbitrate(
            stage=STAGE_STOCK,
            routine_result=routine_clear(owns_response=True),
        )
        event = {
            "account_no": ACCOUNT,
            "trading_day": DAY,
            "routine_instance_id": "routine-a",
            "status": "OWNED",
            "selected_stock_code": "005930",
        }
        existing = self.arbitrate(
            stage=STAGE_STOCK,
            routine_result=routine_clear(),
            routine=_RoutineOwnership(events={"event-a": event}),
        )
        self.assertEqual(ROUTINE_OWNS, current["routine_status"])
        self.assertFalse(current["admitted"])
        self.assertEqual(ROUTINE_OWNS, existing["routine_status"])
        self.assertEqual(("routine-a",), existing["active_routine_instance_ids"])
        self.assertFalse(existing["admitted"])

    def test_routine_uncertain_or_malformed_blocks_stock(self) -> None:
        for value in (None, {}, routine_clear(settled=False), routine_clear(settled="yes")):
            with self.subTest(value=value):
                result = self.arbitrate(stage=STAGE_STOCK, routine_result=value)
                self.assertEqual(ROUTINE_UNCERTAIN, result["routine_status"])
                self.assertFalse(result["admitted"])
        unavailable = self.arbitrate(stage=STAGE_STOCK, routine_result=routine_clear(), routine=_RoutineOwnership(ok=False))
        self.assertEqual(ROUTINE_UNCERTAIN, unavailable["routine_status"])

    def test_completed_routine_without_active_ownership_allows_stock(self) -> None:
        result = self.arbitrate(
            stage=STAGE_STOCK,
            routine_result=routine_clear(ownership_completed=True),
        )
        self.assertTrue(result["admitted"])

    def test_cross_stock_upper_ownership_blocks_stock_b(self) -> None:
        buffer = self.arbitrate(
            stage=STAGE_STOCK,
            routine_result=routine_clear(),
            buffer=_BufferOwnership(codes=("005930",)),
        )
        routine_event = {
            "account_no": ACCOUNT,
            "trading_day": DAY,
            "routine_instance_id": "routine-a",
            "status": "OWNED",
            "selected_stock_code": "005930",
        }
        routine = self.arbitrate(
            stage=STAGE_STOCK,
            routine_result=routine_clear(),
            routine=_RoutineOwnership(events={"event-a": routine_event}),
        )
        self.assertFalse(buffer["admitted"], "Buffer stock A must block Stock B admission")
        self.assertFalse(routine["admitted"], "Routine stock A must block Stock B admission")

    def test_corrupt_durable_ownership_fails_closed_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            buffer_path = runtime / "buffer_response_ownership.json"
            routine_path = runtime / "routine_limit_response_ownership.json"
            buffer_path.write_bytes(b"{not-json")
            routine_path.write_bytes(b"{not-json")
            buffer_before = buffer_path.read_bytes()
            routine_before = routine_path.read_bytes()
            buffer = arbitrate_limit_response_priority(
                account_no=ACCOUNT, trading_day=DAY, buffer_result=buffer_clear(),
                routine_result=routine_clear(), stage=STAGE_ROUTINE, project_root=root,
            )
            self.assertEqual(BUFFER_UNCERTAIN, buffer["buffer_status"])
            buffer_path.unlink()
            stock = arbitrate_limit_response_priority(
                account_no=ACCOUNT, trading_day=DAY, buffer_result=buffer_clear(),
                routine_result=routine_clear(), stage=STAGE_STOCK, project_root=root,
            )
            self.assertEqual(ROUTINE_UNCERTAIN, stock["routine_status"])
            self.assertEqual(routine_before, routine_path.read_bytes())
            self.assertEqual(buffer_before, b"{not-json")

    def test_arbitration_does_not_modify_existing_lower_close_or_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            routine = RoutineLimitResponseOwnershipService(
                runtime / "routine_limit_response_ownership.json",
                now_factory=lambda: "2026-08-21T10:00:00+09:00",
            )
            claimed = routine.claim(
                account_no=ACCOUNT, trading_day=DAY, routine_instance_id="routine-a",
                trigger_identity_source="BROKER_EXECUTION", trigger_identity="EXEC-1",
                trigger_stock_code="005930", detected_at="2026-08-21T09:59:00+09:00",
                selected_stock_code="005930", configured_response_mode="조기마감",
                response_intent=INTENT_EARLY_CLOSE, expected_revision=0,
            )
            self.assertTrue(claimed["changed"])
            ownership_path = runtime / "routine_limit_response_ownership.json"
            before = ownership_path.read_bytes()
            state = {"operation_command_mode": "EARLY_CLOSE", "operation_command_id": "EXISTING"}
            state_before = json.dumps(state, sort_keys=True)
            result = arbitrate_limit_response_priority(
                account_no=ACCOUNT, trading_day=DAY,
                buffer_result=buffer_clear(ownership_claimed=True),
                routine_result=routine_clear(), stage=STAGE_STOCK, project_root=root,
                buffer_ownership=_BufferOwnership(), routine_ownership=routine,
            )
            self.assertFalse(result["admitted"])
            self.assertEqual(before, ownership_path.read_bytes())
            self.assertEqual(state_before, json.dumps(state, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
