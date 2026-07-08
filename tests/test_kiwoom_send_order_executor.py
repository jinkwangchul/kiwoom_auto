# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from kiwoom_send_order_executor import execute_kiwoom_send_order


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


class RecordingAdapter:
    def __init__(self, result: object = 0, *, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.result


class KiwoomSendOrderExecutorTest(unittest.TestCase):
    def _call_preview(self, **overrides: object) -> dict[str, object]:
        args = ["0101", "BUY", "12345678", 1, "003550", 10, 85000, "03", ""]
        result: dict[str, object] = {
            "status": "SEND_ORDER_CALL_READY",
            "send_order_call_preview": {
                "preview_type": "KIWOOM_SEND_ORDER_CALL_PREVIEW",
                "final_call_token": "FINAL_CALL_TOKEN_EXECUTOR_1",
                "dispatch_id": "DISPATCH_EXECUTOR_1",
                "order_id": "ORDER_EXECUTOR_1",
                "account_no": "12345678",
                "screen_no": "0101",
                "send_order_args_ready": True,
            },
            "send_order_args": args,
            "issues": [],
            "warnings": [],
            "send_order_called": False,
            "broker_called": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "final_confirmation": True,
            "environment_send_order_enabled": True,
        }
        result.update(overrides)
        return result

    def test_send_order_sent_normal_calls_adapter_once(self) -> None:
        adapter = RecordingAdapter(0)

        result = execute_kiwoom_send_order(self._call_preview(), adapter, self._context())

        self.assertEqual("SEND_ORDER_SENT", result["status"])
        self.assertTrue(result["send_order_called"])
        self.assertTrue(result["broker_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["recorded"])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(tuple(["0101", "BUY", "12345678", 1, "003550", 10, 85000, "03", ""]), adapter.calls[0])
        self.assertEqual(0, result["send_order_result"]["return_code"])

    def test_non_zero_return_is_failed(self) -> None:
        adapter = RecordingAdapter(-308)

        result = execute_kiwoom_send_order(self._call_preview(), adapter, self._context())

        self.assertEqual("SEND_ORDER_FAILED", result["status"])
        self.assertTrue(result["send_order_called"])
        self.assertTrue(result["broker_called"])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(-308, result["send_order_result"]["return_code"])

    def test_adapter_exception_is_error(self) -> None:
        adapter = RecordingAdapter(raises=RuntimeError("boom"))

        result = execute_kiwoom_send_order(self._call_preview(), adapter, self._context())

        self.assertEqual("ERROR", result["status"])
        self.assertTrue(result["send_order_called"])
        self.assertTrue(result["broker_called"])
        self.assertEqual(1, len(adapter.calls))
        self.assertIn("send_order_adapter raised exception: boom", result["issues"])

    def test_call_preview_blocked_returns_blocked_without_call(self) -> None:
        adapter = RecordingAdapter(0)

        result = execute_kiwoom_send_order(
            self._call_preview(status="BLOCKED", issues=["blocked"]),
            adapter,
            self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["send_order_called"])
        self.assertEqual([], adapter.calls)

    def test_call_preview_invalid_returns_invalid_without_call(self) -> None:
        adapter = RecordingAdapter(0)

        result = execute_kiwoom_send_order(
            self._call_preview(status="INVALID", issues=["bad"]),
            adapter,
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["send_order_called"])
        self.assertEqual([], adapter.calls)

    def test_final_confirmation_false_blocks_without_call(self) -> None:
        adapter = RecordingAdapter(0)

        result = execute_kiwoom_send_order(
            self._call_preview(),
            adapter,
            self._context(final_confirmation=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("execution_context.final_confirmation is not true", result["issues"])
        self.assertEqual([], adapter.calls)

    def test_environment_send_order_enabled_false_blocks_without_call(self) -> None:
        adapter = RecordingAdapter(0)

        result = execute_kiwoom_send_order(
            self._call_preview(),
            adapter,
            self._context(environment_send_order_enabled=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("execution_context.environment_send_order_enabled is not true", result["issues"])
        self.assertEqual([], adapter.calls)

    def test_send_order_args_not_nine_is_invalid_without_call(self) -> None:
        adapter = RecordingAdapter(0)

        result = execute_kiwoom_send_order(
            self._call_preview(send_order_args=["0101"]),
            adapter,
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_args must contain 9 values", result["issues"])
        self.assertEqual([], adapter.calls)

    def test_adapter_not_callable_is_invalid(self) -> None:
        result = execute_kiwoom_send_order(
            self._call_preview(),
            object(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_adapter must be callable", result["issues"])

    def test_final_call_token_missing_blocks_without_call(self) -> None:
        adapter = RecordingAdapter(0)
        preview = self._call_preview()
        preview["send_order_call_preview"]["final_call_token"] = ""

        result = execute_kiwoom_send_order(preview, adapter, self._context())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("final_call_token is required", result["issues"])
        self.assertEqual([], adapter.calls)

    def test_inputs_are_not_mutated(self) -> None:
        preview = self._call_preview()
        context = self._context()
        adapter = RecordingAdapter({"return_code": 0})
        originals = (deepcopy(preview), deepcopy(context))

        result = execute_kiwoom_send_order(preview, adapter, context)
        result["send_order_result"]["send_order_args"][0] = "9999"

        self.assertEqual("SEND_ORDER_SENT", result["status"])
        self.assertEqual(originals[0], preview)
        self.assertEqual(originals[1], context)
        self.assertEqual(tuple(originals[0]["send_order_args"]), adapter.calls[0])

    def test_runtime_order_queue_rules_hash_unchanged_and_recorders_not_called(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}
        adapter = RecordingAdapter(0)

        with mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("chejan_event_recorder.record_chejan_event") as chejan_recorder, \
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub:
            result = execute_kiwoom_send_order(self._call_preview(), adapter, self._context())

        self.assertEqual("SEND_ORDER_SENT", result["status"])
        self.assertEqual(1, len(adapter.calls))
        result_recorder.assert_not_called()
        chejan_recorder.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
