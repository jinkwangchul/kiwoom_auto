# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import routine_signal_queue


def _configure_worker(path_text: str) -> None:
    path = Path(path_text)
    routine_signal_queue.RUNTIME_DIR = path.parent
    routine_signal_queue.QUEUE_PATH = path
    routine_signal_queue._DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _enqueue_worker(path_text: str, tick_key: str, start: object, output: object) -> None:
    _configure_worker(path_text)
    start.wait()
    result = routine_signal_queue.enqueue_routine_signal(
        {"signal": "BUY", "reason": "worker", "signal_index": 1},
        routine="TEST",
        code="003550",
        name="LG",
        tick_key=tick_key,
    )
    output.put(result)


def _update_worker(path_text: str, signal_id: str, status: str, start: object, output: object) -> None:
    _configure_worker(path_text)
    start.wait()
    output.put(routine_signal_queue.update_signal_status(signal_id, status))


def _hold_lock_worker(path_text: str, ready: object, release: object) -> None:
    _configure_worker(path_text)
    with routine_signal_queue._QueueFileLock(Path(path_text), 5.0):
        ready.set()
        release.wait(5.0)


class RoutineSignalQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue_path = self.root / "runtime" / "routine_signals.json"
        self.queue_path.parent.mkdir(parents=True)
        self.patches = [
            mock.patch.object(routine_signal_queue, "RUNTIME_DIR", self.queue_path.parent),
            mock.patch.object(routine_signal_queue, "QUEUE_PATH", self.queue_path),
            mock.patch.object(routine_signal_queue, "_DEFAULT_LOCK_TIMEOUT_SECONDS", 1.0),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def _write(self, signals: list[dict[str, object]] | None = None) -> None:
        self.queue_path.write_text(
            json.dumps({"version": 1, "updated_at": "", "signals": signals or []}, indent=2),
            encoding="utf-8",
        )

    def _record(self, signal_id: str, *, status: str = "PENDING") -> dict[str, object]:
        return {
            "id": signal_id,
            "routine": "TEST",
            "code": "003550",
            "name": "LG",
            "signal": "BUY",
            "signal_index": 1,
            "tick_key": signal_id,
            "status": status,
        }

    def _enqueue(self, tick_key: str = "T1", *, signal_index: int = 1) -> dict[str, object]:
        return routine_signal_queue.enqueue_routine_signal(
            {"signal": "BUY", "reason": "test", "signal_index": signal_index},
            routine="TEST",
            code="003550",
            name="LG",
            tick_key=tick_key,
        )

    def _data(self) -> dict[str, object]:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _run_processes(self, targets: list[tuple[object, tuple[object, ...]]]) -> list[dict[str, object]]:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        output = context.Queue()
        processes = [context.Process(target=target, args=(*args, start, output)) for target, args in targets]
        for process in processes:
            process.start()
        start.set()
        results = [output.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(15)
            self.assertEqual(0, process.exitcode)
        return results

    def test_enqueue_preserves_contract_and_metadata(self) -> None:
        result = self._enqueue()
        self.assertEqual("queued", result["status"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["signal_committed"])
        self.assertTrue(result["post_write_verified"])
        self.assertEqual(1, len(self._data()["signals"]))

    def test_dedupe_key_contract_is_preserved(self) -> None:
        first = self._enqueue("T1", signal_index=1)
        duplicate = self._enqueue("T1", signal_index=1)
        distinct = self._enqueue("T2", signal_index=1)
        self.assertEqual("queued", first["status"])
        self.assertEqual("duplicate", duplicate["status"])
        self.assertFalse(duplicate["file_write"])
        self.assertEqual("queued", distinct["status"])

    def test_status_update_requires_exactly_one_id(self) -> None:
        self._write([self._record("DUP"), self._record("DUP")])
        before = self.queue_path.read_bytes()
        result = routine_signal_queue.update_signal_status("DUP", "READY")
        self.assertFalse(result["ok"])
        self.assertIn("multiple", result["reason"])
        self.assertEqual(before, self.queue_path.read_bytes())

    def test_status_update_preserves_contract(self) -> None:
        self._write([self._record("SIG")])
        result = routine_signal_queue.update_signal_status("SIG", "READY", {"review": "ok"})
        self.assertTrue(result["ok"])
        self.assertEqual("PENDING", result["before_status"])
        self.assertEqual("READY", result["after_status"])
        record = self._data()["signals"][0]
        self.assertEqual("READY", record["status"])
        self.assertEqual("ok", record["review"])

    def test_same_signal_two_threads_is_recorded_once(self) -> None:
        barrier = threading.Barrier(3)
        results: list[dict[str, object]] = []

        def worker() -> None:
            barrier.wait()
            results.append(self._enqueue("SAME"))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        self.assertEqual(["duplicate", "queued"], sorted(item["status"] for item in results))
        self.assertEqual(1, len(self._data()["signals"]))

    def test_different_signals_two_threads_are_both_preserved_with_hash_chain(self) -> None:
        self._write()
        barrier = threading.Barrier(3)
        results: list[dict[str, object]] = []

        def worker(tick_key: str) -> None:
            barrier.wait()
            results.append(self._enqueue(tick_key))

        threads = [threading.Thread(target=worker, args=(key,)) for key in ("A", "B")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        self.assertEqual(2, len(self._data()["signals"]))
        self.assertTrue(
            results[0]["after_sha256"] == results[1]["before_sha256"]
            or results[1]["after_sha256"] == results[0]["before_sha256"]
        )

    def test_enqueue_and_update_threads_preserve_both_changes(self) -> None:
        self._write([self._record("OLD")])
        barrier = threading.Barrier(3)

        def enqueue() -> None:
            barrier.wait()
            self._enqueue("NEW")

        def update() -> None:
            barrier.wait()
            routine_signal_queue.update_signal_status("OLD", "READY")

        threads = [threading.Thread(target=enqueue), threading.Thread(target=update)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        signals = self._data()["signals"]
        self.assertEqual(2, len(signals))
        self.assertEqual("READY", next(item for item in signals if item["id"] == "OLD")["status"])

    def test_two_status_update_threads_preserve_both_changes(self) -> None:
        self._write([self._record("A"), self._record("B")])
        barrier = threading.Barrier(3)

        def worker(signal_id: str, status: str) -> None:
            barrier.wait()
            routine_signal_queue.update_signal_status(signal_id, status)

        threads = [
            threading.Thread(target=worker, args=("A", "READY")),
            threading.Thread(target=worker, args=("B", "BLOCKED")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        statuses = {item["id"]: item["status"] for item in self._data()["signals"]}
        self.assertEqual({"A": "READY", "B": "BLOCKED"}, statuses)

    def test_same_signal_two_processes_is_recorded_once(self) -> None:
        results = self._run_processes(
            [
                (_enqueue_worker, (str(self.queue_path), "SAME")),
                (_enqueue_worker, (str(self.queue_path), "SAME")),
            ]
        )
        self.assertEqual(["duplicate", "queued"], sorted(item["status"] for item in results))
        self.assertEqual(1, len(self._data()["signals"]))

    def test_different_signals_two_processes_are_both_preserved(self) -> None:
        self._run_processes(
            [
                (_enqueue_worker, (str(self.queue_path), "A")),
                (_enqueue_worker, (str(self.queue_path), "B")),
            ]
        )
        self.assertEqual(2, len(self._data()["signals"]))

    def test_enqueue_and_update_processes_preserve_both_changes(self) -> None:
        self._write([self._record("OLD")])
        self._run_processes(
            [
                (_enqueue_worker, (str(self.queue_path), "NEW")),
                (_update_worker, (str(self.queue_path), "OLD", "READY")),
            ]
        )
        signals = self._data()["signals"]
        self.assertEqual(2, len(signals))
        self.assertEqual("READY", next(item for item in signals if item["id"] == "OLD")["status"])

    def test_two_status_update_processes_preserve_both_changes(self) -> None:
        self._write([self._record("A"), self._record("B")])
        self._run_processes(
            [
                (_update_worker, (str(self.queue_path), "A", "READY")),
                (_update_worker, (str(self.queue_path), "B", "BLOCKED")),
            ]
        )
        statuses = {item["id"]: item["status"] for item in self._data()["signals"]}
        self.assertEqual({"A": "READY", "B": "BLOCKED"}, statuses)

    def test_invalid_json_shapes_block_mutation_without_overwrite(self) -> None:
        invalid_values = ["", "{bad", "[]", '{"signals": {}}']
        for index, value in enumerate(invalid_values):
            with self.subTest(index=index):
                self.queue_path.write_text(value, encoding="utf-8")
                before = self.queue_path.read_bytes()
                result = self._enqueue(f"INVALID-{index}")
                self.assertEqual("error", result["status"])
                self.assertTrue(result["manual_review_required"])
                self.assertFalse(result["file_write"])
                self.assertEqual(before, self.queue_path.read_bytes())

    def test_backup_is_latest_locked_snapshot(self) -> None:
        self._write([self._record("OLD")])
        before = self.queue_path.read_bytes()
        result = self._enqueue("NEW")
        self.assertTrue(result["backup_path"])
        self.assertEqual(before, Path(result["backup_path"]).read_bytes())

    def test_unique_temp_path_is_used_and_cleaned(self) -> None:
        seen: list[Path] = []
        original = routine_signal_queue._write_json_temp

        def capture(path: Path, data: dict[str, object]) -> Path:
            temp_path = original(path, data)
            seen.append(temp_path)
            return temp_path

        with mock.patch.object(routine_signal_queue, "_write_json_temp", side_effect=capture):
            result = self._enqueue()
        self.assertEqual("queued", result["status"])
        self.assertEqual(1, len(seen))
        self.assertTrue(seen[0].name.startswith(".routine_signals.json."))
        self.assertFalse(seen[0].exists())

    def test_replace_failure_reports_no_queue_write(self) -> None:
        self._write()
        before = self.queue_path.read_bytes()
        with mock.patch.object(routine_signal_queue.os, "replace", side_effect=OSError("replace failed")):
            result = self._enqueue()
        self.assertEqual("error", result["status"])
        self.assertFalse(result["file_write"])
        self.assertFalse(result["signal_committed"])
        self.assertEqual(before, self.queue_path.read_bytes())
        self.assertEqual([], list(self.queue_path.parent.glob(".routine_signals.json.*.tmp")))

    def test_post_write_read_failure_preserves_side_effect(self) -> None:
        self._write()
        original = routine_signal_queue._read_queue_strict
        calls = 0

        def fail_after_write(path: Path) -> tuple[dict[str, object], str | None]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(path)
            return {}, "forced post-write read failure"

        with mock.patch.object(routine_signal_queue, "_read_queue_strict", side_effect=fail_after_write):
            result = self._enqueue()
        self.assertEqual("error", result["status"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["signal_committed"])
        self.assertFalse(result["post_write_verified"])
        self.assertEqual(1, len(self._data()["signals"]))

    def test_lock_timeout_does_not_write_or_backup(self) -> None:
        self._write()
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        process = context.Process(target=_hold_lock_worker, args=(str(self.queue_path), ready, release))
        process.start()
        self.assertTrue(ready.wait(10))
        before = self.queue_path.read_bytes()
        try:
            with mock.patch.object(routine_signal_queue, "_DEFAULT_LOCK_TIMEOUT_SECONDS", 0.05):
                result = self._enqueue()
        finally:
            release.set()
            process.join(10)
        self.assertEqual(0, process.exitcode)
        self.assertEqual("error", result["status"])
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["file_write"])
        self.assertEqual(before, self.queue_path.read_bytes())
        self.assertFalse(self.queue_path.with_name("routine_signals.json.bak").exists())


if __name__ == "__main__":
    unittest.main()
