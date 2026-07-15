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

import position_update_service
from position_update_service import update_position_from_fill


def _position_process_result(**overrides: object) -> dict[str, object]:
    result = {
        "fill_recorded": True,
        "fill_stage": "execution_fill_recorded",
        "next_stage": "POSITION_UPDATE_REQUIRED",
        "changed": True,
        "fill_id": "FILL_1",
        "event_type": "PARTIAL_FILL",
        "order_id": "ORDER_1",
        "order_queued_id": "ORDER_QUEUED_ORDER_1",
        "broker_order_no": "BRK_1",
        "request_hash": "HASH_1",
        "lock_id": "LOCK_1",
        "execution_id": "EXEC_1",
        "filled_quantity": 3,
        "filled_price": 1000,
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _position_process_fill(**overrides: object) -> dict[str, object]:
    fill = {
        "fill_id": "FILL_1",
        "execution_identity_source": "execution_no",
        "execution_identity": "EXEC_NO_1",
        "fill_source": "chejan_event",
        "event_type": "PARTIAL_FILL",
        "broker": "KIWOOM",
        "broker_order_no": "BRK_1",
        "order_id": "ORDER_1",
        "order_queued_id": "ORDER_QUEUED_ORDER_1",
        "execution_id": "EXEC_1",
        "request_hash": "HASH_1",
        "lock_id": "LOCK_1",
        "account_no": "12345678",
        "code": "003550",
        "side": "BUY",
        "filled_quantity": 3,
        "filled_price": 1000,
        "remaining_quantity": 7,
        "order_quantity": 10,
        "order_price": 1000,
        "received_at": "2026-07-04 09:30:00",
        "recorded_at": "2026-07-04 09:30:01",
        "normalized_event": {},
    }
    fill.update(overrides)
    return fill


def _position_process_worker(
    positions_path: str,
    start_event: multiprocessing.Event,
    output: multiprocessing.Queue,
    fill_overrides: dict[str, object],
) -> None:
    try:
        start_event.wait(10)
        output.put(
            update_position_from_fill(
                _position_process_result(),
                _position_process_fill(**fill_overrides),
                positions_path,
                context={"manual_position_update_confirmed": True},
            )
        )
    except Exception as exc:  # pragma: no cover - returned to parent process
        output.put({"position_updated": False, "error": repr(exc)})


class PositionUpdateServiceTest(unittest.TestCase):
    def _result(self, **overrides: object) -> dict[str, object]:
        result = {
            "fill_recorded": True,
            "fill_stage": "execution_fill_recorded",
            "next_stage": "POSITION_UPDATE_REQUIRED",
            "changed": True,
            "fill_id": "FILL_1",
            "event_type": "PARTIAL_FILL",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "broker_order_no": "BRK_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "filled_quantity": 3,
            "filled_price": 1000,
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _fill(self, **overrides: object) -> dict[str, object]:
        fill = {
            "fill_id": "FILL_1",
            "execution_identity_source": "execution_no",
            "execution_identity": "EXEC_NO_1",
            "fill_source": "chejan_event",
            "event_type": "PARTIAL_FILL",
            "broker": "KIWOOM",
            "broker_order_no": "BRK_1",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "execution_id": "EXEC_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "account_no": "12345678",
            "code": "003550",
            "side": "BUY",
            "filled_quantity": 3,
            "filled_price": 1000,
            "remaining_quantity": 7,
            "order_quantity": 10,
            "order_price": 1000,
            "received_at": "2026-07-04 09:30:00",
            "recorded_at": "2026-07-04 09:30:01",
            "normalized_event": {},
        }
        fill.update(overrides)
        return fill

    def _position(self, **overrides: object) -> dict[str, object]:
        position = {
            "position_id": "POSITION_KIWOOM_12345678_003550",
            "broker": "KIWOOM",
            "account_no": "12345678",
            "code": "003550",
            "side": "LONG",
            "quantity": 10,
            "average_price": 1000,
            "cost_basis": 10000,
            "position_status": "OPEN",
            "last_fill_id": "OLD_FILL",
            "last_fill_at": "2026-07-04 09:00:00",
            "applied_fill_ids": ["OLD_FILL"],
            "updated_at": "2026-07-04 09:00:00",
        }
        position.update(overrides)
        return position

    def _write_positions(self, directory: str, root: object | None = None) -> Path:
        path = Path(directory) / "positions.json"
        data = {"version": 1, "updated_at": "2026-07-04 09:00:00", "positions": []}
        if root is not None:
            data = root
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    def _update(
        self,
        path: Path | None,
        result: object | None = None,
        fill: object | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        context = kwargs.pop("context", {"manual_position_update_confirmed": True})
        return update_position_from_fill(
            self._result() if result is None else result,
            self._fill() if fill is None else fill,
            path,
            context=context,
            **kwargs,
        )

    def test_fill_record_result_invalid_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", result="invalid")

            self.assertFalse(result["position_updated"])
            self.assertEqual("fill_record_result", result["position_stage"])

    def test_fill_recorded_false_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", result=self._result(fill_recorded=False))

            self.assertFalse(result["position_updated"])
            self.assertIn("fill_record_result.fill_recorded is not true", result["blocked_reasons"])

    def test_next_stage_must_be_position_update_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", result=self._result(next_stage="OTHER"))

            self.assertFalse(result["position_updated"])
            self.assertIn("fill_record_result.next_stage is not POSITION_UPDATE_REQUIRED", result["blocked_reasons"])

    def test_fill_record_invalid_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", fill="invalid")

            self.assertFalse(result["position_updated"])
            self.assertEqual("fill_record", result["position_stage"])

    def test_confirmation_missing_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", context={})

            self.assertFalse(result["position_updated"])
            self.assertEqual("operator_confirmation", result["position_stage"])

    def test_positions_path_missing_blocked(self) -> None:
        result = self._update(None)

        self.assertFalse(result["position_updated"])
        self.assertEqual("positions_path", result["position_stage"])

    def test_required_fill_fields_missing_blocked(self) -> None:
        fields = [
            "fill_id",
            "broker",
            "account_no",
            "code",
            "side",
            "filled_quantity",
            "filled_price",
            "received_at",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for field in fields:
                with self.subTest(field=field):
                    fill = self._fill()
                    fill.pop(field)

                    result = self._update(Path(tmpdir) / f"{field}.json", fill=fill)

                    self.assertFalse(result["position_updated"])

    def test_filled_quantity_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", fill=self._fill(filled_quantity=0))

            self.assertFalse(result["position_updated"])
            self.assertIn("filled_quantity must be greater than 0", result["blocked_reasons"])

    def test_filled_price_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", fill=self._fill(filled_price=0))

            self.assertFalse(result["position_updated"])
            self.assertIn("filled_price must be greater than 0", result["blocked_reasons"])

    def test_side_must_be_buy_or_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._update(Path(tmpdir) / "positions.json", fill=self._fill(side="HOLD"))

            self.assertFalse(result["position_updated"])
            self.assertIn("fill_record.side must be BUY or SELL", result["blocked_reasons"])

    def test_missing_positions_file_creates_temp_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "positions.json"

            result = self._update(path)
            data = self._read_json(path)

            self.assertTrue(result["position_updated"])
            self.assertEqual(1, data["version"])
            self.assertEqual(1, len(data["positions"]))

    def test_corrupt_positions_json_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "positions.json"
            path.write_text("{bad json", encoding="utf-8")

            result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertEqual("read_positions", result["position_stage"])

    def test_root_non_dict_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root=[])

            result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertIn("positions root must be an object", result["blocked_reasons"])

    def test_positions_non_list_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={"version": 1, "positions": {}})

            result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertIn("positions must be a list", result["blocked_reasons"])

    def test_buy_creates_new_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            result = self._update(path)
            position = self._read_json(path)["positions"][0]

            self.assertTrue(result["position_updated"])
            self.assertEqual("POSITION_KIWOOM_12345678_003550", position["position_id"])
            self.assertEqual(3, position["quantity"])
            self.assertEqual(1000, position["average_price"])
            self.assertEqual(3000, position["cost_basis"])
            self.assertEqual("OPEN", position["position_status"])
            self.assertEqual(["FILL_1"], position["applied_fill_ids"])

    def test_buy_updates_existing_average_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={
                "version": 1,
                "updated_at": "old",
                "positions": [self._position()],
            })

            result = self._update(path)
            position = self._read_json(path)["positions"][0]

            self.assertTrue(result["position_updated"])
            self.assertEqual(13, position["quantity"])
            self.assertEqual(1000, position["average_price"])
            self.assertEqual(13000, position["cost_basis"])
            self.assertEqual(10, result["before_quantity"])
            self.assertEqual(13, result["after_quantity"])

    def test_buy_cumulative_partial_fill_applies_delta_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            first = self._update(path, fill=self._fill(fill_id="FILL_1", execution_identity="EXEC_NO_1", filled_quantity=3, remaining_quantity=7))
            second = self._update(path, fill=self._fill(fill_id="FILL_2", execution_identity="EXEC_NO_2", filled_quantity=5, remaining_quantity=5, filled_price=1100))
            position = self._read_json(path)["positions"][0]

            self.assertTrue(first["position_updated"])
            self.assertTrue(second["position_updated"])
            self.assertEqual(3, first["fill_delta_applied"])
            self.assertEqual(2, second["fill_delta_applied"])
            self.assertEqual(5, position["quantity"])
            self.assertEqual(1040, position["average_price"])
            self.assertEqual(5200, position["cost_basis"])

    def test_same_cumulative_quantity_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            self._update(path, fill=self._fill(fill_id="FILL_1", execution_identity="EXEC_NO_1", filled_quantity=3, remaining_quantity=7))
            result = self._update(path, fill=self._fill(fill_id="FILL_2", execution_identity="EXEC_NO_2", filled_quantity=3, remaining_quantity=7))
            position = self._read_json(path)["positions"][0]

            self.assertFalse(result["position_updated"])
            self.assertEqual("fill_delta_noop", result["position_stage"])
            self.assertFalse(result["changed"])
            self.assertEqual(0, result["fill_delta_applied"])
            self.assertEqual(3, position["quantity"])

    def test_same_execution_identity_redelivery_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            self._update(path, fill=self._fill(fill_id="FILL_1", execution_identity="EXEC_NO_1", filled_quantity=3))
            result = self._update(path, fill=self._fill(fill_id="FILL_2", execution_identity="EXEC_NO_1", filled_quantity=5))
            position = self._read_json(path)["positions"][0]

            self.assertFalse(result["position_updated"])
            self.assertEqual("duplicate_fill", result["position_stage"])
            self.assertEqual(3, position["quantity"])

    def test_same_identity_value_different_source_applies_as_separate_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            first = self._update(path, fill=self._fill(fill_id="FILL_1", execution_identity_source="execution_no", execution_identity="123", filled_quantity=3))
            second = self._update(path, fill=self._fill(fill_id="FILL_2", execution_identity_source="fid_909", execution_identity="123", filled_quantity=5))

            self.assertTrue(first["position_updated"])
            self.assertTrue(second["position_updated"])
            self.assertEqual(2, second["fill_delta_applied"])
            self.assertEqual(5, self._read_json(path)["positions"][0]["quantity"])

    def test_out_of_order_cumulative_quantity_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            self._update(path, fill=self._fill(fill_id="FILL_1", execution_identity="EXEC_NO_1", filled_quantity=5, remaining_quantity=5))
            result = self._update(path, fill=self._fill(fill_id="FILL_2", execution_identity="EXEC_NO_2", filled_quantity=3, remaining_quantity=7))

            self.assertFalse(result["position_updated"])
            self.assertEqual("out_of_order_fill", result["position_stage"])
            self.assertIn("filled_quantity is less than last applied cumulative quantity", result["blocked_reasons"])

    def test_sell_decreases_existing_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={
                "version": 1,
                "updated_at": "old",
                "positions": [self._position()],
            })
            fill = self._fill(fill_id="SELL_FILL", side="SELL", filled_quantity=3, filled_price=1100)

            result = self._update(path, fill=fill)
            position = self._read_json(path)["positions"][0]

            self.assertTrue(result["position_updated"])
            self.assertEqual(7, position["quantity"])
            self.assertEqual(1000, position["average_price"])
            self.assertEqual(7000, position["cost_basis"])
            self.assertEqual("OPEN", position["position_status"])

    def test_sell_cumulative_partial_fill_applies_delta_only(self) -> None:
        position = self._position(quantity=10, average_price=1000, cost_basis=10000)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={"version": 1, "updated_at": "old", "positions": [position]})

            first = self._update(path, fill=self._fill(fill_id="SELL_1", execution_identity="SELL_EXEC_1", side="SELL", filled_quantity=3, remaining_quantity=7))
            second = self._update(path, fill=self._fill(fill_id="SELL_2", execution_identity="SELL_EXEC_2", side="SELL", filled_quantity=5, remaining_quantity=5))
            updated = self._read_json(path)["positions"][0]

            self.assertEqual(3, first["fill_delta_applied"])
            self.assertEqual(2, second["fill_delta_applied"])
            self.assertEqual(5, updated["quantity"])
            self.assertEqual(1000, updated["average_price"])
            self.assertEqual(5000, updated["cost_basis"])

    def test_sell_excess_quantity_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={
                "version": 1,
                "updated_at": "old",
                "positions": [self._position(quantity=2)],
            })
            fill = self._fill(fill_id="SELL_FILL", side="SELL", filled_quantity=3)

            result = self._update(path, fill=fill)

            self.assertFalse(result["position_updated"])
            self.assertIn("SELL filled_quantity exceeds position quantity", result["blocked_reasons"])

    def test_sell_to_zero_closes_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={
                "version": 1,
                "updated_at": "old",
                "positions": [self._position(quantity=3, average_price=1000, cost_basis=3000)],
            })
            fill = self._fill(fill_id="SELL_FILL", side="SELL", filled_quantity=3, filled_price=1100)

            result = self._update(path, fill=fill)
            position = self._read_json(path)["positions"][0]

            self.assertTrue(result["position_updated"])
            self.assertEqual(0, position["quantity"])
            self.assertEqual(0, position["average_price"])
            self.assertEqual(0, position["cost_basis"])
            self.assertEqual("CLOSED", position["position_status"])
            self.assertTrue(position["closed_at"])

    def test_duplicate_fill_id_on_target_position_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={
                "version": 1,
                "updated_at": "old",
                "positions": [self._position(applied_fill_ids=["FILL_1"])],
            })

            result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertEqual("duplicate_fill", result["position_stage"])

    def test_duplicate_fill_id_across_positions_noop(self) -> None:
        other = self._position(
            position_id="POSITION_KIWOOM_12345678_000000",
            code="000000",
            applied_fill_ids=["FILL_1"],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir, root={
                "version": 1,
                "updated_at": "old",
                "positions": [other],
            })

            result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertEqual("duplicate_fill", result["position_stage"])
            self.assertFalse(result["changed"])
            self.assertIn("fill already applied to position", result["warnings"])

    def test_backup_created_for_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            result = self._update(path)

            self.assertTrue(Path(result["backup_path"]).exists())

    def test_backup_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            result = self._update(path, backup=False)

            self.assertTrue(result["position_updated"])
            self.assertIsNone(result["backup_path"])
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_stale_snapshot_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            snapshot = {"sha256": self._sha256(path)}
            data = self._read_json(path)
            data["updated_at"] = "changed"
            path.write_text(json.dumps(data), encoding="utf-8")

            result = self._update(path, positions_snapshot=snapshot)

            self.assertFalse(result["position_updated"])
            self.assertIn("positions file changed after fill record; manual review required", result["blocked_reasons"])

    def test_stale_snapshot_has_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            snapshot = {"sha256": self._sha256(path)}
            data = self._read_json(path)
            data["updated_at"] = "changed"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            changed = self._sha256(path)

            result = self._update(path, positions_snapshot=snapshot)

            self.assertFalse(result["position_updated"])
            self.assertFalse(result["position_write"])
            self.assertEqual(changed, self._sha256(path))

    def test_before_after_sha256_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            before = self._sha256(path)

            result = self._update(path)

            self.assertEqual(before, result["before_sha256"])
            self.assertEqual(self._sha256(path), result["after_sha256"])
            self.assertNotEqual(result["before_sha256"], result["after_sha256"])

    def test_same_fill_two_threads_updates_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            barrier = threading.Barrier(3)
            results: list[dict[str, object]] = []

            def worker() -> None:
                barrier.wait()
                results.append(self._update(path))

            threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

            position = self._read_json(path)["positions"][0]
            self.assertEqual(1, sum(1 for result in results if result["position_updated"]))
            self.assertEqual(1, sum(1 for result in results if not result["position_updated"]))
            self.assertEqual(3, position["quantity"])

    def test_same_fill_two_processes_updates_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            start_event = multiprocessing.Event()
            output: multiprocessing.Queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(target=_position_process_worker, args=(str(path), start_event, output, {}))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start_event.set()
            results = [output.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)

            position = self._read_json(path)["positions"][0]
            self.assertEqual([0, 0], [process.exitcode for process in processes])
            self.assertEqual(1, sum(1 for result in results if result["position_updated"]))
            self.assertEqual(3, position["quantity"])

    def test_different_fill_two_processes_preserves_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            start_event = multiprocessing.Event()
            output: multiprocessing.Queue = multiprocessing.Queue()
            overrides = [
                {"fill_id": "FILL_1", "execution_identity": "EXEC_NO_1", "filled_quantity": 3, "remaining_quantity": 7},
                {"fill_id": "FILL_2", "execution_identity": "EXEC_NO_2", "filled_quantity": 5, "remaining_quantity": 5},
            ]
            processes = [
                multiprocessing.Process(target=_position_process_worker, args=(str(path), start_event, output, item))
                for item in overrides
            ]
            for process in processes:
                process.start()
            start_event.set()
            results = [output.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)

            position = self._read_json(path)["positions"][0]
            self.assertEqual([0, 0], [process.exitcode for process in processes])
            self.assertTrue(all(result["position_updated"] for result in results))
            self.assertEqual(5, position["quantity"])

    def test_replace_before_failure_has_no_position_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            before = self._sha256(path)

            with mock.patch.object(position_update_service.os, "replace", side_effect=OSError("replace failed")):
                result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertFalse(result["position_write"])
            self.assertFalse(result["position_committed"])
            self.assertEqual(before, self._sha256(path))

    def test_post_write_read_failure_preserves_side_effect_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            initial_data = self._read_json(path)
            blocked = position_update_service._blocked("read_positions", "post read failed")

            with mock.patch.object(
                position_update_service,
                "_read_positions",
                side_effect=[(initial_data, None), ({}, blocked)],
            ):
                result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertTrue(result["changed"])
            self.assertTrue(result["position_write"])
            self.assertTrue(result["position_committed"])
            self.assertFalse(result["post_write_verified"])

    def test_post_write_content_mismatch_preserves_side_effect_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            initial_data = self._read_json(path)
            mismatched = deepcopy(initial_data)

            with mock.patch.object(
                position_update_service,
                "_read_positions",
                side_effect=[(initial_data, None), (mismatched, None)],
            ):
                result = self._update(path)

            self.assertFalse(result["position_updated"])
            self.assertTrue(result["position_write"])
            self.assertTrue(result["position_committed"])
            self.assertFalse(result["post_write_verified"])

    def test_fills_json_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fills_path = Path(tmpdir) / "fills.json"
            fills_path.write_text(json.dumps({"version": 1, "fills": []}), encoding="utf-8")
            before = self._sha256(fills_path)
            positions_path = self._write_positions(tmpdir)

            self._update(positions_path)

            self.assertEqual(before, self._sha256(fills_path))

    def test_runtime_order_queue_hash_unchanged(self) -> None:
        runtime_order_queue = Path("runtime") / "order_queue.json"
        before = self._sha256(runtime_order_queue)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            self._update(path)

        self.assertEqual(before, self._sha256(runtime_order_queue))

    def test_runtime_default_paths_not_used(self) -> None:
        runtime_positions = Path("runtime") / "positions.json"
        runtime_fills = Path("runtime") / "fills.json"
        before_positions = runtime_positions.exists()
        before_fills = runtime_fills.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)

            self._update(path)

        self.assertEqual(before_positions, runtime_positions.exists())
        self.assertEqual(before_fills, runtime_fills.exists())

    def test_no_send_order_chejan_gui_or_timer_references(self) -> None:
        module_text = position_update_service.__loader__.get_source(position_update_service.__name__)

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("send_order_entrypoint", module_text)
        self.assertNotIn("record_chejan_event", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)

    def test_runtime_paths_are_not_referenced(self) -> None:
        module_text = position_update_service.__loader__.get_source(position_update_service.__name__)

        self.assertNotIn("runtime/positions.json", module_text)
        self.assertNotIn("runtime\\positions.json", module_text)
        self.assertNotIn("runtime/fills.json", module_text)
        self.assertNotIn("runtime/order_queue.json", module_text)

    def test_inputs_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            result = self._result()
            fill = self._fill()
            originals = (deepcopy(result), deepcopy(fill))

            update_position_from_fill(
                result,
                fill,
                path,
                context={"manual_position_update_confirmed": True},
            )

            self.assertEqual(originals[0], result)
            self.assertEqual(originals[1], fill)


if __name__ == "__main__":
    unittest.main()
