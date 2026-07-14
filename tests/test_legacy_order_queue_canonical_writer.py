# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import multiprocessing
import msvcrt
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import order_queue
import routine_signal_consumer


def _candidate(index: int, *, source_signal_id: str | None = None) -> dict:
    return {
        "id": f"ORDER_LEGACY_{index}",
        "status": "PENDING",
        "source": "routine_signals",
        "source_signal_id": source_signal_id or f"SIG_LEGACY_{index}",
        "routine": "routine",
        "code": f"00{index:04d}",
        "name": f"NAME_{index}",
        "side": "SELL",
        "execution_enabled": False,
    }


def _process_append_worker(queue_path: str, candidate: dict, start_event: object, result_queue: object) -> None:
    import order_queue as child_order_queue

    child_order_queue.ORDER_QUEUE_PATH = Path(queue_path)
    start_event.wait()
    result_queue.put(child_order_queue.append_order_candidates([candidate], backup=True))


def _signal(index: int, *, source_signal_id: str | None = None) -> dict:
    return {
        "id": source_signal_id or f"SIG_BUILD_{index}",
        "status": "PENDING",
        "routine": "routine",
        "code": f"00{index:04d}",
        "name": f"NAME_{index}",
        "signal": "SELL",
        "reason": "test",
    }


def _process_build_worker(signal_path: str, queue_path: str, signal: dict, start_event: object, result_queue: object) -> None:
    import order_queue as child_order_queue

    child_order_queue.SIGNAL_QUEUE_PATH = Path(signal_path)
    child_order_queue.ORDER_QUEUE_PATH = Path(queue_path)
    Path(signal_path).write_text(
        json.dumps({"version": 1, "updated_at": "", "signals": [signal]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    start_event.wait()
    result_queue.put(child_order_queue.build_order_queue_from_signals())


class LegacyOrderQueueCanonicalWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.queue_path = self.root / "order_queue.json"
        self.signal_path = self.root / "routine_signals.json"
        self.patchers = [
            mock.patch.object(order_queue, "ORDER_QUEUE_PATH", self.queue_path),
            mock.patch.object(order_queue, "SIGNAL_QUEUE_PATH", self.signal_path),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()

    def _write_queue(self, *, revision: int | None = None, orders: list[dict] | None = None) -> None:
        data = {"version": 1, "updated_at": "before", "orders": orders or []}
        if revision is not None:
            data["revision"] = revision
        self.queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_queue(self) -> dict:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _write_signals(self, signals: list[dict]) -> None:
        self.signal_path.write_text(
            json.dumps({"version": 1, "updated_at": "", "signals": signals}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_append_uses_canonical_writer_and_adds_revision(self) -> None:
        result = order_queue.append_order_candidates([_candidate(1)])

        data = self._read_queue()
        self.assertTrue(result["ok"])
        self.assertTrue(result["committed"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["post_write_verified"])
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(1, data["revision"])
        self.assertEqual("SIG_LEGACY_1", data["orders"][0]["source_signal_id"])

    def test_existing_revision_advances_once(self) -> None:
        self._write_queue(revision=5)

        result = order_queue.append_order_candidates([_candidate(1)])

        self.assertTrue(result["ok"])
        self.assertEqual(5, result["revision_before"])
        self.assertEqual(6, result["revision_after"])
        self.assertEqual(6, self._read_queue()["revision"])

    def test_duplicate_source_signal_id_is_idempotent_noop(self) -> None:
        self._write_queue(revision=3, orders=[_candidate(1, source_signal_id="SIG_DUP")])

        result = order_queue.append_order_candidates([_candidate(2, source_signal_id="SIG_DUP")])

        data = self._read_queue()
        self.assertTrue(result["ok"])
        self.assertFalse(result["committed"])
        self.assertFalse(result["queue_write"])
        self.assertEqual(1, result["duplicates"])
        self.assertEqual(3, data["revision"])
        self.assertEqual(1, len(data["orders"]))

    def test_legacy_key_fallback_blocks_duplicate_without_source_signal_id(self) -> None:
        first = _candidate(1)
        first["source_signal_id"] = ""
        second = dict(first)
        second["id"] = "ORDER_OTHER"
        self._write_queue(revision=2, orders=[first])

        result = order_queue.append_order_candidates([second])

        self.assertTrue(result["ok"])
        self.assertFalse(result["committed"])
        self.assertEqual(1, result["duplicates"])
        self.assertEqual(2, self._read_queue()["revision"])

    def test_stale_expected_revision_blocks_without_write(self) -> None:
        self._write_queue(revision=7)

        result = order_queue.append_order_candidates([_candidate(1)], expected_revision=6)

        self.assertFalse(result["ok"])
        self.assertFalse(result["queue_write"])
        self.assertEqual(7, self._read_queue()["revision"])

    def test_lock_timeout_blocks_without_write(self) -> None:
        self._write_queue(revision=1)
        lock_path = self.queue_path.with_name(f"{self.queue_path.name}.lock")
        handle = lock_path.open("a+b")
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            result = order_queue.append_order_candidates(
                [_candidate(1)],
                context={"manual_queue_write_confirmed": True, "queue_lock_timeout_sec": 0.05},
            )
        finally:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                handle.close()

        self.assertFalse(result["ok"])
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["queue_write"])
        self.assertEqual(1, self._read_queue()["revision"])

    def test_two_threads_same_signal_append_once(self) -> None:
        self._write_queue(revision=0)
        results: list[dict] = []
        start = threading.Event()

        def worker() -> None:
            start.wait()
            results.append(order_queue.append_order_candidates([_candidate(1, source_signal_id="SIG_THREAD")]))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        data = self._read_queue()
        self.assertEqual(1, len(data["orders"]))
        self.assertEqual(1, data["revision"])
        self.assertEqual(1, sum(1 for item in results if item.get("orders_created") == 1))
        self.assertEqual(1, sum(1 for item in results if item.get("duplicates") == 1))

    def test_two_threads_different_signals_preserve_both(self) -> None:
        self._write_queue(revision=0)
        results: list[dict] = []
        start = threading.Event()

        def worker(index: int) -> None:
            start.wait()
            results.append(order_queue.append_order_candidates([_candidate(index)]))

        threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        data = self._read_queue()
        self.assertEqual(2, len(data["orders"]))
        self.assertEqual(2, data["revision"])
        self.assertEqual(2, sum(1 for item in results if item.get("orders_created") == 1))

    def test_two_processes_same_signal_append_once(self) -> None:
        self._write_queue(revision=0)
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        result_queue = ctx.Queue()
        workers = [
            ctx.Process(
                target=_process_append_worker,
                args=(str(self.queue_path), _candidate(1, source_signal_id="SIG_PROCESS"), start_event, result_queue),
            )
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        start_event.set()
        results = [result_queue.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(0, worker.exitcode)

        data = self._read_queue()
        self.assertEqual(1, len(data["orders"]))
        self.assertEqual(1, data["revision"])
        self.assertEqual(1, sum(1 for item in results if item.get("orders_created") == 1))
        self.assertEqual(1, sum(1 for item in results if item.get("duplicates") == 1))

    def test_order_queue_module_has_no_direct_order_queue_write_text(self) -> None:
        source = Path(order_queue.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ORDER_QUEUE_PATH.write_text", source)
        self.assertNotIn("path.write_text", source)

    def test_build_order_queue_from_signals_uses_append_not_replace(self) -> None:
        self._write_signals([_signal(1)])
        with (
            mock.patch.object(order_queue, "append_order_candidates", return_value={"orders_created": 1, "duplicates": 0, "order_queue_written": True}) as append,
            mock.patch.object(order_queue, "replace_order_queue") as replace,
            mock.patch.object(order_queue, "write_order_queue") as write,
        ):
            result = order_queue.build_order_queue_from_signals()

        append.assert_called_once()
        replace.assert_not_called()
        write.assert_not_called()
        self.assertEqual(1, result["orders_created"])

    def test_build_order_queue_from_signals_preserves_existing_record_patch(self) -> None:
        existing = {
            **_candidate(99, source_signal_id="SIG_EXISTING"),
            "chejan_events": [{"event_identity": "CHEJAN_1"}],
        }
        self._write_queue(revision=4, orders=[existing])
        self._write_signals([_signal(1, source_signal_id="SIG_NEW")])

        result = order_queue.build_order_queue_from_signals()

        data = self._read_queue()
        self.assertEqual(1, result["orders_created"])
        self.assertEqual(5, data["revision"])
        self.assertEqual([{"event_identity": "CHEJAN_1"}], data["orders"][0]["chejan_events"])
        self.assertEqual("SIG_NEW", data["orders"][1]["source_signal_id"])

    def test_stale_full_replacement_is_blocked_without_expected_revision(self) -> None:
        self._write_queue(revision=8, orders=[_candidate(1)])

        result = order_queue.replace_order_queue({"orders": []})

        self.assertFalse(result["ok"])
        self.assertEqual("full_replace_blocked", result["write_stage"])
        self.assertFalse(result["order_queue_written"])
        self.assertEqual(1, len(self._read_queue()["orders"]))

    def test_explicit_full_replacement_requires_matching_revision(self) -> None:
        self._write_queue(revision=8, orders=[_candidate(1)])

        stale = order_queue.replace_order_queue(
            {"orders": []},
            expected_revision=7,
            context={"allow_full_queue_replace": True},
        )
        self.assertFalse(stale["ok"])
        self.assertEqual(1, len(self._read_queue()["orders"]))

        success = order_queue.replace_order_queue(
            {"orders": []},
            expected_revision=8,
            context={"allow_full_queue_replace": True},
        )

        self.assertTrue(success["committed"])
        self.assertEqual(0, len(self._read_queue()["orders"]))
        self.assertEqual(9, self._read_queue()["revision"])

    def test_two_processes_build_order_queue_from_same_signal_appends_once(self) -> None:
        self._write_queue(revision=0)
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        result_queue = ctx.Queue()
        workers = [
            ctx.Process(
                target=_process_build_worker,
                args=(str(self.signal_path), str(self.queue_path), _signal(1, source_signal_id="SIG_BUILD_PROCESS"), start_event, result_queue),
            )
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        start_event.set()
        results = [result_queue.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(0, worker.exitcode)

        data = self._read_queue()
        self.assertEqual(1, len(data["orders"]))
        self.assertEqual(1, data["revision"])
        self.assertEqual(1, sum(1 for item in results if item.get("orders_created") == 1))
        self.assertEqual(1, sum(1 for item in results if item.get("duplicates") == 1))


class RoutineSignalConsumerCanonicalAppendTest(unittest.TestCase):
    def _signal(self) -> dict:
        return {
            "id": "SIG_CONSUMER_1",
            "status": "PENDING",
            "routine": "routine",
            "code": "003550",
            "name": "LG",
            "signal": "SELL",
            "reason": "test",
        }

    def _manager_result(self) -> dict:
        return {
            "signal_id": "SIG_CONSUMER_1",
            "signal_type": "SELL",
            "payload_built": True,
            "order_manager_allowed": True,
            "payload_candidate_status": "CANDIDATE_READY",
            "order_manager": {"ok": True, "allowed": True, "reason": "", "order_executor_called": False, "state_saved": False},
            "payload_preview": {"candidate_status": "CANDIDATE_READY", "execution_enabled": False},
        }

    def test_queue_write_failure_skips_signal_status_update(self) -> None:
        with (
            mock.patch.object(routine_signal_consumer, "load_pending_routine_signals", return_value=[self._signal()]),
            mock.patch.object(routine_signal_consumer, "dry_run_order_manager_for_signal_with_payload_preview", return_value=self._manager_result()),
            mock.patch.object(routine_signal_consumer, "read_order_queue", return_value={"orders": []}),
            mock.patch.object(routine_signal_consumer, "signal_to_order_candidate", return_value=_candidate(1, source_signal_id="SIG_CONSUMER_1")),
            mock.patch.object(
                routine_signal_consumer,
                "append_order_candidates",
                return_value={"ok": False, "reason": "queue lock timeout", "order_queue_written": False},
            ),
            mock.patch.object(routine_signal_consumer, "update_signal_status") as update_signal_status,
        ):
            result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
                mark_previewed=True,
                write_order_queue=True,
            )

        update_signal_status.assert_not_called()
        self.assertFalse(result["order_queue"]["ok"])
        self.assertEqual(1, result["summary"]["marked_error"])
        self.assertFalse(result["summary"]["order_queue_written"])

    def test_signal_status_failure_preserves_queue_side_effect_result(self) -> None:
        with (
            mock.patch.object(routine_signal_consumer, "load_pending_routine_signals", return_value=[self._signal()]),
            mock.patch.object(routine_signal_consumer, "dry_run_order_manager_for_signal_with_payload_preview", return_value=self._manager_result()),
            mock.patch.object(routine_signal_consumer, "read_order_queue", return_value={"orders": []}),
            mock.patch.object(routine_signal_consumer, "signal_to_order_candidate", return_value=_candidate(1, source_signal_id="SIG_CONSUMER_1")),
            mock.patch.object(
                routine_signal_consumer,
                "append_order_candidates",
                return_value={"ok": True, "orders_created": 1, "duplicates": 0, "order_queue_written": True},
            ),
            mock.patch.object(routine_signal_consumer, "update_signal_status", side_effect=RuntimeError("signal write failed")),
        ):
            result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
                mark_previewed=True,
                write_order_queue=True,
            )

        self.assertTrue(result["order_queue"]["ok"])
        self.assertTrue(result["summary"]["order_queue_written"])
        self.assertEqual(1, result["summary"]["marked_error"])
        self.assertIn("status update failed", result["status_updates"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
