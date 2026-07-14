# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import multiprocessing
import tempfile
from pathlib import Path
import threading
import unittest
from unittest import mock

from kiwoom_send_order_executor import execute_claimed_send_order, execute_kiwoom_send_order
from execution_queue_writer import mark_send_order_attempted, mark_send_order_call_in_progress


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


class ClaimedSendOrderCallable:
    def __init__(self, result: object = 0, *, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises
        return self.result


def _claimed_identity(record: dict[str, object]) -> dict[str, object]:
    return {
        "order_queued_id": record.get("id"),
        "order_id": record.get("order_id"),
        "candidate_id": record.get("candidate_id"),
        "queue_pending_id": record.get("queue_pending_id"),
        "execution_id": record.get("execution_id"),
        "request_hash": record.get("request_hash"),
        "lock_id": record.get("lock_id"),
        "source_signal_id": record.get("source_signal_id"),
    }


def _process_claimed_send_worker(queue_path: str, start_event: object, result_queue: object) -> None:
    data = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    record = data["orders"][0]
    start_event.wait()
    result = execute_claimed_send_order(
        queue_path,
        _claimed_identity(record),
        "CLAIM_1",
        "CLAIM_TOKEN",
        "GUI_MANUAL",
        1,
        ClaimedSendOrderCallable(0),
        ["0101", "SELL", "12345678", 2, "003550", 10, 85000, "00", ""],
        {"send_order_attempt_id": "ATTEMPT_PROCESS"},
    )
    result_queue.put(result)


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

    def _claimed_record(self, **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "id": "ORDER_QUEUED_1",
            "status": "DISPATCH_CLAIMED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "CANDIDATE_1",
            "queue_pending_id": "PENDING_1",
            "execution_id": "EXEC_1",
            "request_hash": "a" * 64,
            "lock_id": "LOCK_1",
            "execution_enabled": True,
            "send_order_called": False,
            "dispatch_claimed": True,
            "dispatch_claim_id": "CLAIM_1",
            "dispatch_claim_token_hash": hashlib.sha256("CLAIM_TOKEN".encode("utf-8")).hexdigest(),
            "dispatch_claim_owner": "GUI_MANUAL",
            "dispatch_claim_expires_at": "2999-01-01 00:00:00",
            "dispatch_generation": 1,
        }
        record.update(overrides)
        return record

    def _write_claimed_queue(self, queue_path: Path, record: dict[str, object] | None = None) -> dict[str, object]:
        target = record or self._claimed_record()
        queue_path.write_text(
            json.dumps({"version": 1, "revision": 1, "updated_at": "before", "orders": [target]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def _claimed_args(self) -> list[object]:
        return ["0101", "SELL", "12345678", 2, "003550", 10, 85000, "00", ""]

    def _execute_claimed(
        self,
        queue_path: Path,
        record: dict[str, object],
        callable_result: object = 0,
        *,
        raises: Exception | None = None,
        expected_revision: int | None = 1,
        context: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], ClaimedSendOrderCallable]:
        adapter = ClaimedSendOrderCallable(callable_result, raises=raises)
        result = execute_claimed_send_order(
            queue_path,
            _claimed_identity(record),
            "CLAIM_1",
            "CLAIM_TOKEN",
            "GUI_MANUAL",
            expected_revision,
            adapter,
            self._claimed_args(),
            context or {"send_order_attempt_id": "ATTEMPT_1"},
        )
        return result, adapter

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

    def test_claimed_send_order_success_records_send_call_accepted(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._write_claimed_queue(queue_path)

        result, adapter = self._execute_claimed(queue_path, record, 0)

        data = json.loads(queue_path.read_text(encoding="utf-8"))
        queued = data["orders"][0]
        self.assertEqual("SEND_CALL_ACCEPTED", result["status"])
        self.assertTrue(result["callable_executed"])
        self.assertTrue(result["queue_result_recorded"])
        self.assertTrue(result["send_order_called"])
        self.assertTrue(result["broker_call_executed"])
        self.assertTrue(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(tuple(self._claimed_args()), adapter.calls[0])
        self.assertEqual("SEND_CALL_ACCEPTED", queued["status"])
        self.assertTrue(queued["send_order_called"])
        self.assertTrue(queued["broker_call_executed"])
        self.assertTrue(queued["broker_api_called"])
        self.assertFalse(queued["call_execution_uncertain"])
        self.assertTrue(queued["send_call_accepted"])
        self.assertFalse(queued["broker_accepted"])
        self.assertFalse(queued["actual_order_sent"])
        self.assertEqual(4, data["revision"])
        self.assertNotIn("CLAIM_TOKEN", json.dumps(data))
        self.assertNotIn("CLAIM_TOKEN", json.dumps(result))

    def test_claimed_send_order_non_zero_records_send_call_rejected(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._write_claimed_queue(queue_path)

        result, adapter = self._execute_claimed(queue_path, record, -308)

        queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual("SEND_CALL_REJECTED", result["status"])
        self.assertTrue(result["send_call_rejected"])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual("SEND_CALL_REJECTED", queued["status"])
        self.assertTrue(queued["send_order_called"])
        self.assertTrue(queued["broker_call_executed"])
        self.assertTrue(queued["broker_api_called"])
        self.assertFalse(queued["call_execution_uncertain"])
        self.assertTrue(queued["send_call_rejected"])
        self.assertFalse(queued["actual_order_sent"])

    def test_claimed_send_order_exception_and_unknown_return_are_uncertain(self) -> None:
        for value, raises in ((None, None), (0, RuntimeError("boom"))):
            with self.subTest(raises=bool(raises)):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                queue_path = Path(tmp.name) / "order_queue.json"
                record = self._write_claimed_queue(queue_path)

                result, adapter = self._execute_claimed(queue_path, record, value, raises=raises)

                queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
                self.assertEqual("SEND_UNCERTAIN", result["status"])
                self.assertTrue(result["callable_executed"])
                self.assertTrue(result["queue_result_recorded"])
                self.assertEqual(1, len(adapter.calls))
                self.assertEqual("SEND_UNCERTAIN", queued["status"])
                self.assertTrue(queued["send_order_called"])
                self.assertTrue(queued["broker_call_executed"])
                self.assertTrue(queued["broker_api_called"])
                self.assertFalse(queued["call_execution_uncertain"])
                self.assertFalse(queued["send_call_result_known"])
                self.assertTrue(queued["send_uncertain"])
                self.assertTrue(queued["manual_reconciliation_required"])
                self.assertFalse(queued["automatic_retry_allowed"])

    def test_claimed_send_order_blocks_before_callable_when_attempt_fails(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._write_claimed_queue(queue_path)

        result, adapter = self._execute_claimed(queue_path, record, 0, expected_revision=0)

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["callable_executed"])
        self.assertEqual([], adapter.calls)
        queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual("DISPATCH_CLAIMED", queued["status"])

    def test_claimed_send_order_claim_mismatch_expired_and_bad_args_do_not_call(self) -> None:
        cases = [
            ("wrong_claim", {"dispatch_claim_id": "WRONG"}, self._claimed_args()),
            ("expired", {}, self._claimed_args()),
            ("bad_args", {}, ["0101"]),
        ]
        for name, kwargs, args in cases:
            with self.subTest(name=name):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                queue_path = Path(tmp.name) / "order_queue.json"
                record = self._claimed_record()
                if name == "expired":
                    record["dispatch_claim_expires_at"] = "2000-01-01 00:00:00"
                self._write_claimed_queue(queue_path, record)
                adapter = ClaimedSendOrderCallable(0)

                result = execute_claimed_send_order(
                    queue_path,
                    _claimed_identity(record),
                    kwargs.get("dispatch_claim_id", "CLAIM_1"),
                    "CLAIM_TOKEN",
                    "GUI_MANUAL",
                    1,
                    adapter,
                    args,
                    {"send_order_attempt_id": f"ATTEMPT_{name}"},
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["callable_executed"])
                self.assertEqual([], adapter.calls)

    def test_claimed_send_order_result_write_failure_preserves_callable_side_effect(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._write_claimed_queue(queue_path)

        with mock.patch(
            "kiwoom_send_order_executor.record_broker_send_accepted",
            return_value={"committed": False, "post_write_verified": False, "blocked_reasons": ["write failed"]},
        ):
            result, adapter = self._execute_claimed(queue_path, record, 0)

        queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual("SEND_UNCERTAIN", result["status"])
        self.assertTrue(result["callable_executed"])
        self.assertFalse(result["queue_result_recorded"])
        self.assertTrue(result["send_order_called"])
        self.assertTrue(result["broker_api_called"])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual("SEND_CALL_IN_PROGRESS", queued["status"])
        self.assertFalse(queued["send_order_called"])
        self.assertFalse(queued["broker_call_executed"])
        self.assertFalse(queued["broker_api_called"])
        self.assertTrue(queued["call_execution_uncertain"])
        self.assertTrue(queued["manual_reconciliation_required"])

    def test_send_call_in_progress_after_crash_blocks_automatic_reexecution(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._write_claimed_queue(queue_path)
        attempt = mark_send_order_attempted(
            queue_path,
            _claimed_identity(record),
            dispatch_claim_id="CLAIM_1",
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            expected_revision=1,
            attempt_id="ATTEMPT_CRASH",
        )
        marker = mark_send_order_call_in_progress(
            queue_path,
            _claimed_identity(record),
            dispatch_claim_id="CLAIM_1",
            send_order_attempt_id=attempt["send_order_attempt_id"],
            expected_revision=2,
        )
        adapter = ClaimedSendOrderCallable(0)

        restarted = execute_claimed_send_order(
            queue_path,
            _claimed_identity(record),
            "CLAIM_1",
            "CLAIM_TOKEN",
            "GUI_MANUAL",
            3,
            adapter,
            self._claimed_args(),
            {"send_order_attempt_id": "ATTEMPT_CRASH"},
        )

        queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertTrue(marker["send_call_started"])
        self.assertEqual("BLOCKED", restarted["status"])
        self.assertFalse(restarted["callable_executed"])
        self.assertEqual([], adapter.calls)
        self.assertEqual("SEND_CALL_IN_PROGRESS", queued["status"])
        self.assertFalse(queued["send_order_called"])
        self.assertFalse(queued["broker_api_called"])
        self.assertTrue(queued["call_execution_uncertain"])
        self.assertFalse(queued["automatic_retry_allowed"])
        self.assertTrue(queued["manual_reconciliation_required"])

    def test_same_claim_two_threads_only_one_callable_executes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._write_claimed_queue(queue_path)
        adapter = ClaimedSendOrderCallable(0)
        results: list[dict[str, object]] = []

        def worker() -> None:
            results.append(
                execute_claimed_send_order(
                    queue_path,
                    _claimed_identity(record),
                    "CLAIM_1",
                    "CLAIM_TOKEN",
                    "GUI_MANUAL",
                    1,
                    adapter,
                    self._claimed_args(),
                    {"send_order_attempt_id": "ATTEMPT_THREAD"},
                )
            )

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(1, sum(1 for item in results if item.get("callable_executed") is True))
        self.assertEqual(1, len(adapter.calls))
        queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual("SEND_CALL_ACCEPTED", queued["status"])

    def test_same_claim_two_processes_only_one_callable_executes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_claimed_queue(queue_path)
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        result_queue = ctx.Queue()
        processes = [
            ctx.Process(target=_process_claimed_send_worker, args=(str(queue_path), start_event, result_queue)),
            ctx.Process(target=_process_claimed_send_worker, args=(str(queue_path), start_event, result_queue)),
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(10)
            self.assertEqual(0, process.exitcode)
        results = [result_queue.get(timeout=5) for _ in processes]

        self.assertEqual(1, sum(1 for item in results if item.get("callable_executed") is True))
        queued = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual("SEND_CALL_ACCEPTED", queued["status"])
        self.assertEqual(4, json.loads(queue_path.read_text(encoding="utf-8"))["revision"])


if __name__ == "__main__":
    unittest.main()
