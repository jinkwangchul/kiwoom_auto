# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import execution_fill_recorder
from execution_fill_recorder import record_execution_fill


def _fill_process_result(**overrides: object) -> dict[str, object]:
    result = {
        "recorded": True,
        "record_stage": "chejan_event_recorded",
        "next_stage": "FILL_RECORD_REQUIRED",
        "changed": True,
        "order_id": "ORDER_1",
        "order_queued_id": "ORDER_QUEUED_ORDER_1",
        "broker_order_no": "BRK_1",
        "event_type": "PARTIAL_FILL",
        "matched_by": "broker_order_no",
        "request_hash": "HASH_1",
        "lock_id": "LOCK_1",
        "execution_id": "EXEC_1",
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _fill_process_event(**overrides: object) -> dict[str, object]:
    event = {
        "normalized": True,
        "event_stage": "chejan_event_normalized",
        "event_type": "PARTIAL_FILL",
        "broker": "KIWOOM",
        "source": "kiwoom_chejan",
        "gubun": "0",
        "broker_order_no": "BRK_1",
        "account_no": "12345678",
        "code": "003550",
        "name": "LG",
        "side": "BUY",
        "order_status": "FILLED",
        "order_quantity": 10,
        "filled_quantity": 3,
        "remaining_quantity": 7,
        "order_price": 1000,
        "filled_price": 1000,
        "received_at": "2026-07-04 09:30:00",
        "request_hash": None,
        "lock_id": None,
        "execution_id": None,
        "unresolved": False,
        "blocked_reasons": [],
        "warnings": [],
        "raw_event": {},
    }
    event.update(overrides)
    return event


def _fill_process_worker(
    fill_path: str,
    start_event: multiprocessing.Event,
    output: multiprocessing.Queue,
    event_overrides: dict[str, object],
) -> None:
    try:
        start_event.wait(10)
        output.put(
            record_execution_fill(
                _fill_process_result(),
                _fill_process_event(**event_overrides),
                fill_path,
                context={"manual_fill_record_confirmed": True},
            )
        )
    except Exception as exc:  # pragma: no cover - returned to parent process
        output.put({"fill_recorded": False, "error": repr(exc)})


class ExecutionFillRecorderTest(unittest.TestCase):
    def _event_record_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "recorded": True,
            "record_stage": "chejan_event_recorded",
            "next_stage": "FILL_RECORD_REQUIRED",
            "changed": True,
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "broker_order_no": "BRK_1",
            "event_type": "PARTIAL_FILL",
            "matched_by": "broker_order_no",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _event(self, **overrides: object) -> dict[str, object]:
        event = {
            "normalized": True,
            "event_stage": "chejan_event_normalized",
            "event_type": "PARTIAL_FILL",
            "broker": "KIWOOM",
            "source": "kiwoom_chejan",
            "gubun": "0",
            "broker_order_no": "BRK_1",
            "account_no": "12345678",
            "code": "003550",
            "name": "LG",
            "side": "BUY",
            "order_status": "FILLED",
            "order_quantity": 10,
            "filled_quantity": 3,
            "remaining_quantity": 7,
            "order_price": 1000,
            "filled_price": 1000,
            "received_at": "2026-07-04 09:30:00",
            "request_hash": None,
            "lock_id": None,
            "execution_id": None,
            "unresolved": False,
            "blocked_reasons": [],
            "warnings": [],
            "raw_event": {},
        }
        event.update(overrides)
        return event

    def _write_fills(self, directory: str, root: object | None = None) -> Path:
        path = Path(directory) / "fills.json"
        data = {"version": 1, "updated_at": "2026-07-04 09:00:00", "fills": []}
        if root is not None:
            data = root
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    def _record_fill(
        self,
        path: Path | None,
        result: object | None = None,
        event: object | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        context = kwargs.pop("context", {"manual_fill_record_confirmed": True})
        return record_execution_fill(
            self._event_record_result() if result is None else result,
            self._event() if event is None else event,
            path,
            context=context,
            **kwargs,
        )

    def test_chejan_event_record_result_invalid_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", result="invalid")

            self.assertFalse(result["fill_recorded"])
            self.assertEqual("chejan_event_record_result", result["fill_stage"])

    def test_recorded_false_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", result=self._event_record_result(recorded=False))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("chejan_event_record_result.recorded is not true", result["blocked_reasons"])

    def test_next_stage_must_be_fill_record_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", result=self._event_record_result(next_stage="OTHER"))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("chejan_event_record_result.next_stage is not FILL_RECORD_REQUIRED", result["blocked_reasons"])

    def test_normalized_event_invalid_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", event="invalid")

            self.assertFalse(result["fill_recorded"])
            self.assertEqual("normalized_event", result["fill_stage"])

    def test_event_type_non_fill_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", event=self._event(event_type="ORDER_OPEN"))

            self.assertFalse(result["fill_recorded"])
            self.assertEqual("event_type", result["fill_stage"])

    def test_confirmation_missing_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", context={})

            self.assertFalse(result["fill_recorded"])
            self.assertEqual("operator_confirmation", result["fill_stage"])

    def test_fill_path_missing_blocked(self) -> None:
        result = self._record_fill(None)

        self.assertFalse(result["fill_recorded"])
        self.assertEqual("fill_path", result["fill_stage"])

    def test_required_fields_missing_blocked(self) -> None:
        fields = [
            "broker_order_no",
            "account_no",
            "code",
            "side",
            "filled_quantity",
            "filled_price",
            "remaining_quantity",
            "order_quantity",
            "received_at",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for field in fields:
                with self.subTest(field=field):
                    event = self._event()
                    event.pop(field)
                    result = self._record_fill(Path(tmpdir) / f"{field}.json", event=event)

                    self.assertFalse(result["fill_recorded"])

    def test_filled_quantity_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", event=self._event(filled_quantity=0))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("filled_quantity must be greater than 0", result["blocked_reasons"])

    def test_filled_price_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", event=self._event(filled_price=0))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("filled_price must be greater than 0", result["blocked_reasons"])

    def test_partial_fill_remaining_quantity_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._record_fill(Path(tmpdir) / "fills.json", event=self._event(remaining_quantity=0))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("PARTIAL_FILL remaining_quantity must be greater than 0", result["blocked_reasons"])

    def test_full_fill_remaining_quantity_must_be_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            event = self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=1)
            result = self._record_fill(Path(tmpdir) / "fills.json", event=event)

            self.assertFalse(result["fill_recorded"])
            self.assertIn("FULL_FILL remaining_quantity must be 0", result["blocked_reasons"])

    def test_missing_fills_file_creates_temp_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fills.json"

            result = self._record_fill(path)
            data = self._read_json(path)

            self.assertTrue(result["fill_recorded"])
            self.assertEqual(1, data["version"])
            self.assertEqual(1, len(data["fills"]))

    def test_corrupt_fills_json_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "fills.json"
            path.write_text("{bad json", encoding="utf-8")

            result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertEqual("read_fills", result["fill_stage"])

    def test_root_non_dict_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir, root=[])

            result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertIn("fills root must be an object", result["blocked_reasons"])

    def test_fills_non_list_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir, root={"version": 1, "fills": {}})

            result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertIn("fills must be a list", result["blocked_reasons"])

    def test_partial_fill_success_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            result = self._record_fill(path)
            fill = self._read_json(path)["fills"][0]

            self.assertTrue(result["fill_recorded"])
            self.assertEqual("POSITION_UPDATE_REQUIRED", result["next_stage"])
            self.assertEqual("PARTIAL_FILL", fill["event_type"])
            self.assertEqual("chejan_event", fill["fill_source"])
            self.assertEqual("ORDER_1", fill["order_id"])
            self.assertEqual("ORDER_QUEUED_ORDER_1", fill["order_queued_id"])
            self.assertEqual("HASH_1", fill["request_hash"])
            self.assertEqual("LOCK_1", fill["lock_id"])
            self.assertEqual("EXEC_1", fill["execution_id"])
            self.assertEqual(3, fill["filled_quantity"])
            self.assertEqual(1000, fill["filled_price"])
            self.assertEqual(fill, result["fill_record"])

    def test_live_chejan_context_records_without_manual_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            result = self._record_fill(
                path,
                context={
                    "kiwoom_api_live_event": True,
                    "live_event_source": "KiwoomApi.raw_chejan_received",
                },
            )

            self.assertTrue(result["fill_recorded"], result)
            self.assertEqual(1, len(self._read_json(path)["fills"]))

    def test_full_fill_success_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            event = self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0)

            result = self._record_fill(path, event=event)
            fill = self._read_json(path)["fills"][0]

            self.assertTrue(result["fill_recorded"])
            self.assertEqual("FULL_FILL", fill["event_type"])

    def test_fill_id_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            first_path = self._write_fills(tmpdir)
            first = self._record_fill(first_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            second_path = self._write_fills(tmpdir)
            second = self._record_fill(second_path)

        self.assertEqual(first["fill_id"], second["fill_id"])

    def test_duplicate_fill_id_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            first = self._record_fill(path)

            second = self._record_fill(path)

            self.assertFalse(second["fill_recorded"])
            self.assertIn("duplicate fill_id", second["blocked_reasons"])
            self.assertEqual(first["fill_id"], self._read_json(path)["fills"][0]["fill_id"])

    def test_duplicate_composite_key_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            self._record_fill(path)
            data = self._read_json(path)
            data["fills"][0]["fill_id"] = "DIFFERENT_FILL_ID"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertIn("duplicate fill composite key", result["blocked_reasons"])

    def test_same_fill_two_threads_records_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            barrier = threading.Barrier(3)
            results: list[dict[str, object]] = []

            def worker() -> None:
                barrier.wait()
                results.append(
                    self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "EXEC_NO_1"}}))
                )

            threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

            data = self._read_json(path)
            self.assertEqual(1, len(data["fills"]))
            self.assertEqual(1, sum(1 for result in results if result["fill_recorded"]))
            self.assertEqual(1, sum(1 for result in results if not result["fill_recorded"]))

    def test_same_fill_two_processes_records_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            start_event = multiprocessing.Event()
            output: multiprocessing.Queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(
                    target=_fill_process_worker,
                    args=(str(path), start_event, output, {"raw_event": {"fid_values": {"909": "EXEC_NO_1"}}}),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start_event.set()
            results = [output.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)

            data = self._read_json(path)
            self.assertEqual([0, 0], [process.exitcode for process in processes])
            self.assertEqual(1, len(data["fills"]))
            self.assertEqual(1, sum(1 for result in results if result["fill_recorded"]))

    def test_different_fill_two_processes_preserves_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            initial_sha256 = self._sha256(path)
            start_event = multiprocessing.Event()
            output: multiprocessing.Queue = multiprocessing.Queue()
            process_args = [
                {"raw_event": {"fid_values": {"909": "EXEC_NO_1"}}},
                {"raw_event": {"fid_values": {"909": "EXEC_NO_2"}}},
            ]
            processes = [
                multiprocessing.Process(target=_fill_process_worker, args=(str(path), start_event, output, overrides))
                for overrides in process_args
            ]
            for process in processes:
                process.start()
            start_event.set()
            results = [output.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)

            data = self._read_json(path)
            self.assertEqual([0, 0], [process.exitcode for process in processes])
            self.assertEqual(2, len(data["fills"]))
            self.assertTrue(all(result["fill_recorded"] for result in results))
            before_hashes = {result["before_sha256"] for result in results}
            after_hashes = {result["after_sha256"] for result in results}
            self.assertIn(initial_sha256, before_hashes)
            self.assertEqual(1, len(before_hashes & after_hashes))
            self.assertIn(self._sha256(path), after_hashes)

    def test_same_price_quantity_different_execution_no_records_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            first = self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "EXEC_NO_1"}}))
            second = self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "EXEC_NO_2"}}))

            self.assertTrue(first["fill_recorded"])
            self.assertTrue(second["fill_recorded"])
            self.assertEqual(2, len(self._read_json(path)["fills"]))

    def test_same_identity_value_different_source_records_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            first = self._record_fill(path, event=self._event(execution_no="123"))
            second = self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "123"}}))

            self.assertTrue(first["fill_recorded"])
            self.assertTrue(second["fill_recorded"])
            fills = self._read_json(path)["fills"]
            self.assertEqual(2, len(fills))
            self.assertEqual(["execution_no", "fid_909"], [fill["execution_identity_source"] for fill in fills])

    def test_same_execution_no_duplicate_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "EXEC_NO_1"}}))
            result = self._record_fill(path, event=self._event(filled_price=1100, raw_event={"fid_values": {"909": "EXEC_NO_1"}}))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("duplicate fid_909", result["blocked_reasons"])

    def test_execution_no_field_duplicate_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path, event=self._event(execution_no="EXEC_NO_1"))
            result = self._record_fill(path, event=self._event(execution_no="EXEC_NO_1", filled_price=1100))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("duplicate execution_no", result["blocked_reasons"])

    def test_same_identity_source_and_value_duplicate_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path, event=self._event(execution_no="123"))
            result = self._record_fill(path, event=self._event(execution_no="123", filled_price=1100))

            self.assertFalse(result["fill_recorded"])
            self.assertIn("duplicate execution_no", result["blocked_reasons"])

    def test_backup_created_for_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            result = self._record_fill(path)

            self.assertTrue(Path(result["backup_path"]).exists())

    def test_backup_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            result = self._record_fill(path, backup=False)

            self.assertTrue(result["fill_recorded"])
            self.assertIsNone(result["backup_path"])
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_backup_contains_latest_pre_write_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "EXEC_NO_1"}}))

            result = self._record_fill(path, event=self._event(raw_event={"fid_values": {"909": "EXEC_NO_2"}}))
            backup = self._read_json(Path(result["backup_path"]))

            self.assertTrue(result["fill_recorded"])
            self.assertEqual(1, len(backup["fills"]))
            self.assertEqual("EXEC_NO_1", backup["fills"][0]["execution_identity"])

    def test_stale_snapshot_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            snapshot = {"sha256": self._sha256(path)}
            data = self._read_json(path)
            data["updated_at"] = "changed"
            path.write_text(json.dumps(data), encoding="utf-8")

            result = self._record_fill(path, fill_snapshot=snapshot)

            self.assertFalse(result["fill_recorded"])
            self.assertIn("fills file changed after Chejan event record; manual review required", result["blocked_reasons"])

    def test_stale_snapshot_has_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            snapshot = {"sha256": self._sha256(path)}
            before = self._sha256(path)
            data = self._read_json(path)
            data["fills"].append({"fill_id": "OTHER"})
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            changed = self._sha256(path)

            result = self._record_fill(path, fill_snapshot=snapshot)

            self.assertFalse(result["fill_recorded"])
            self.assertFalse(result["file_write"])
            self.assertEqual(changed, self._sha256(path))
            self.assertNotEqual(before, changed)

    def test_replace_before_failure_has_no_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            before = self._sha256(path)

            with mock.patch.object(execution_fill_recorder.os, "replace", side_effect=OSError("replace failed")):
                result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertFalse(result["file_write"])
            self.assertFalse(result["fill_write"])
            self.assertFalse(result["fill_committed"])
            self.assertEqual(before, self._sha256(path))

    def test_post_write_read_failure_preserves_side_effect_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            initial_data = self._read_json(path)
            blocked = execution_fill_recorder._blocked("read_fills", "post read failed")

            with mock.patch.object(
                execution_fill_recorder,
                "_read_fills",
                side_effect=[(initial_data, None), ({}, blocked)],
            ):
                result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertTrue(result["changed"])
            self.assertTrue(result["file_write"])
            self.assertTrue(result["fill_write"])
            self.assertTrue(result["fill_committed"])
            self.assertFalse(result["post_write_verified"])

    def test_post_write_content_mismatch_preserves_side_effect_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            initial_data = self._read_json(path)
            mismatched = deepcopy(initial_data)

            with mock.patch.object(
                execution_fill_recorder,
                "_read_fills",
                side_effect=[(initial_data, None), (mismatched, None)],
            ):
                result = self._record_fill(path)

            self.assertFalse(result["fill_recorded"])
            self.assertTrue(result["file_write"])
            self.assertTrue(result["fill_committed"])
            self.assertFalse(result["post_write_verified"])

    def test_before_after_sha256_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            before = self._sha256(path)

            result = self._record_fill(path)

            self.assertEqual(before, result["before_sha256"])
            self.assertEqual(self._sha256(path), result["after_sha256"])
            self.assertNotEqual(result["before_sha256"], result["after_sha256"])

    def test_normalized_event_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            event = self._event()

            self._record_fill(path, event=event)
            fill = self._read_json(path)["fills"][0]

            self.assertEqual(event, fill["normalized_event"])

    def test_inputs_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)
            result = self._event_record_result()
            event = self._event()
            originals = (deepcopy(result), deepcopy(event))

            record_execution_fill(
                result,
                event,
                path,
                context={"manual_fill_record_confirmed": True},
            )

            self.assertEqual(originals[0], result)
            self.assertEqual(originals[1], event)

    def test_runtime_order_queue_hash_unchanged(self) -> None:
        runtime_order_queue = Path("runtime") / "order_queue.json"
        before = self._sha256(runtime_order_queue)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path)

        self.assertEqual(before, self._sha256(runtime_order_queue))

    def test_runtime_positions_not_created(self) -> None:
        runtime_positions = Path("runtime") / "positions.json"
        before_exists = runtime_positions.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path)

        self.assertEqual(before_exists, runtime_positions.exists())

    def test_runtime_fills_default_file_not_created(self) -> None:
        runtime_fills = Path("runtime") / "fills.json"
        before_exists = runtime_fills.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path)

        self.assertEqual(before_exists, runtime_fills.exists())

    def test_temp_positions_file_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_fills(tmpdir)

            self._record_fill(path)

            self.assertFalse((Path(tmpdir) / "positions.json").exists())

    def test_no_send_order_chejan_gui_or_timer_references(self) -> None:
        module_text = execution_fill_recorder.__loader__.get_source(execution_fill_recorder.__name__)

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("send_order_entrypoint", module_text)
        self.assertNotIn("record_chejan_event", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)

    def test_runtime_default_paths_are_not_referenced(self) -> None:
        module_text = execution_fill_recorder.__loader__.get_source(execution_fill_recorder.__name__)

        self.assertNotIn("runtime/fills.json", module_text)
        self.assertNotIn("runtime\\fills.json", module_text)
        self.assertNotIn("runtime/order_queue.json", module_text)
        self.assertNotIn("runtime/positions.json", module_text)


if __name__ == "__main__":
    unittest.main()
