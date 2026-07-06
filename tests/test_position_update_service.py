# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import position_update_service
from position_update_service import update_position_from_fill


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

    def test_duplicate_fill_id_across_positions_blocked(self) -> None:
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
            self.assertIn("fill_id already applied to position", result["blocked_reasons"])

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

    def test_before_after_sha256_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_positions(tmpdir)
            before = self._sha256(path)

            result = self._update(path)

            self.assertEqual(before, result["before_sha256"])
            self.assertEqual(self._sha256(path), result["after_sha256"])
            self.assertNotEqual(result["before_sha256"], result["after_sha256"])

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
