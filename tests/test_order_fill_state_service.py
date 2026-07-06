# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

import order_fill_state_service
from order_fill_state_service import commit_order_fill_state, get_order_fill_state, review_order_fill_state


class OrderFillStateServiceTest(unittest.TestCase):
    def _position_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "position_updated": True,
            "position_stage": "position_updated_from_fill",
            "next_stage": "ORDER_FILL_STATE_REVIEW_REQUIRED",
            "changed": True,
            "position_id": "POSITION_KIWOOM_12345678_003550",
            "fill_id": "FILL_1",
            "code": "003550",
            "side": "BUY",
            "before_quantity": 0,
            "after_quantity": 3,
            "before_average_price": 0,
            "after_average_price": 1000,
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _fill_result(self, **overrides: object) -> dict[str, object]:
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
        }
        fill.update(overrides)
        return fill

    def _order(self, **overrides: object) -> dict[str, object]:
        order = {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "total_filled_quantity": 0,
            "remaining_quantity": 10,
        }
        order.update(overrides)
        return order

    def _review(
        self,
        position_result: object | None = None,
        fill_result: object | None = None,
        fill: object | None = None,
        order: object | None = None,
    ) -> dict[str, object]:
        return review_order_fill_state(
            self._position_result() if position_result is None else position_result,
            self._fill_result() if fill_result is None else fill_result,
            self._fill() if fill is None else fill,
            self._order() if order is None else order,
        )

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    def _write_queue(self, directory: str, order: dict[str, object] | None = None, root: object | None = None) -> Path:
        path = Path(directory) / "order_queue.json"
        data = {"version": 1, "updated_at": "2026-07-04 10:00:00", "orders": [order or self._order()]}
        if root is not None:
            data = root
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _commit(
        self,
        path: Path | None,
        review_result: object | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        context = kwargs.pop("context", {"manual_order_fill_state_commit_confirmed": True})
        return commit_order_fill_state(
            self._review() if review_result is None else review_result,
            path,
            context=context,
            **kwargs,
        )

    def test_position_update_failure_is_blocked(self) -> None:
        result = self._review(position_result=self._position_result(position_updated=False))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("position_update", result["fill_state_stage"])
        self.assertIn("position_update_result.position_updated is not true", result["blocked_reasons"])

    def test_position_update_next_stage_mismatch_is_blocked(self) -> None:
        result = self._review(position_result=self._position_result(next_stage="OTHER"))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertIn("position_update_result.next_stage is not ORDER_FILL_STATE_REVIEW_REQUIRED", result["blocked_reasons"])

    def test_fill_record_failure_is_blocked(self) -> None:
        result = self._review(fill_result=self._fill_result(fill_recorded=False))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("fill_record_result", result["fill_state_stage"])
        self.assertIn("fill_record_result.fill_recorded is not true", result["blocked_reasons"])

    def test_fill_record_next_stage_mismatch_is_blocked(self) -> None:
        result = self._review(fill_result=self._fill_result(next_stage="OTHER"))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertIn("fill_record_result.next_stage is not POSITION_UPDATE_REQUIRED", result["blocked_reasons"])

    def test_event_type_non_fill_is_blocked(self) -> None:
        result = self._review(fill=self._fill(event_type="ORDER_OPEN"))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("event_type", result["fill_state_stage"])

    def test_partial_fill_status_candidate(self) -> None:
        result = self._review()

        self.assertTrue(result["order_fill_state_review_ok"])
        self.assertEqual("ORDER_FILL_STATE_COMMIT_REQUIRED", result["next_stage"])
        self.assertEqual("PARTIALLY_FILLED", result["status_candidate"])
        self.assertEqual("PARTIAL_FILL", result["event_type"])
        self.assertEqual(3, result["total_filled_quantity_candidate"])
        self.assertEqual(7, result["remaining_quantity_candidate"])

    def test_full_fill_status_candidate(self) -> None:
        result = self._review(
            fill_result=self._fill_result(event_type="FULL_FILL", filled_quantity=10),
            fill=self._fill(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0),
        )

        self.assertTrue(result["order_fill_state_review_ok"])
        self.assertEqual("FILLED", result["status_candidate"])
        self.assertEqual("FULL_FILL", result["event_type"])
        self.assertEqual(10, result["total_filled_quantity_candidate"])
        self.assertEqual(0, result["remaining_quantity_candidate"])

    def test_partial_remaining_quantity_must_be_positive(self) -> None:
        result = self._review(fill=self._fill(remaining_quantity=0))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("remaining_quantity", result["fill_state_stage"])
        self.assertIn("PARTIAL_FILL remaining_quantity must be greater than 0", result["blocked_reasons"])

    def test_full_remaining_quantity_must_be_zero(self) -> None:
        result = self._review(fill=self._fill(event_type="FULL_FILL", remaining_quantity=1))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertIn("FULL_FILL remaining_quantity must be 0", result["blocked_reasons"])

    def test_filled_quantity_must_be_positive(self) -> None:
        result = self._review(fill=self._fill(filled_quantity=0))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("quantity", result["fill_state_stage"])

    def test_order_status_must_be_reviewable(self) -> None:
        result = self._review(order=self._order(status="FILLED"))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("order_record", result["fill_state_stage"])

    def test_partially_filled_order_is_reviewable(self) -> None:
        result = self._review(order=self._order(status="PARTIALLY_FILLED", total_filled_quantity=3))

        self.assertTrue(result["order_fill_state_review_ok"])
        self.assertEqual(6, result["total_filled_quantity_candidate"])

    def test_position_fill_id_mismatch_is_blocked(self) -> None:
        result = self._review(position_result=self._position_result(fill_id="OTHER"))

        self.assertFalse(result["order_fill_state_review_ok"])
        self.assertEqual("identity", result["fill_state_stage"])
        self.assertIn("position_update_result.fill_id does not match fill_record.fill_id", result["blocked_reasons"])

    def test_fill_result_identity_mismatch_is_blocked(self) -> None:
        cases = [
            ("fill_id", self._position_result(fill_id="OTHER"), self._fill_result(), self._fill(), self._order()),
            ("order_id", self._position_result(), self._fill_result(order_id="OTHER"), self._fill(), self._order()),
            ("order_queued_id", self._position_result(), self._fill_result(order_queued_id="OTHER"), self._fill(), self._order()),
            ("request_hash", self._position_result(), self._fill_result(request_hash="OTHER"), self._fill(), self._order()),
            ("lock_id", self._position_result(), self._fill_result(lock_id="OTHER"), self._fill(), self._order()),
            ("execution_id", self._position_result(), self._fill_result(execution_id="OTHER"), self._fill(), self._order()),
        ]
        for field, position_result, fill_result, fill, order in cases:
            with self.subTest(field=field):
                result = self._review(
                    position_result=position_result,
                    fill_result=fill_result,
                    fill=fill,
                    order=order,
                )

                self.assertFalse(result["order_fill_state_review_ok"])
                self.assertEqual("identity", result["fill_state_stage"])

    def test_order_identity_mismatch_is_blocked(self) -> None:
        cases = [
            ("order_id", self._order(order_id="OTHER")),
            ("order_queued_id", self._order(id="OTHER")),
            ("request_hash", self._order(request_hash="OTHER")),
            ("lock_id", self._order(lock_id="OTHER")),
            ("execution_id", self._order(execution_id="OTHER")),
        ]
        for field, order in cases:
            with self.subTest(field=field):
                result = self._review(order=order)

                self.assertFalse(result["order_fill_state_review_ok"])
                self.assertEqual("identity", result["fill_state_stage"])

    def test_inputs_are_not_mutated(self) -> None:
        position_result = self._position_result()
        fill_result = self._fill_result()
        fill = self._fill()
        order = self._order()
        originals = (
            deepcopy(position_result),
            deepcopy(fill_result),
            deepcopy(fill),
            deepcopy(order),
        )

        review_order_fill_state(position_result, fill_result, fill, order)

        self.assertEqual(originals[0], position_result)
        self.assertEqual(originals[1], fill_result)
        self.assertEqual(originals[2], fill)
        self.assertEqual(originals[3], order)

    def test_runtime_files_are_not_written(self) -> None:
        order_queue = Path("runtime") / "order_queue.json"
        before_order_queue = self._sha256(order_queue)
        fills_path = Path("runtime") / "fills.json"
        positions_path = Path("runtime") / "positions.json"
        before_fills = fills_path.exists()
        before_positions = positions_path.exists()

        result = self._review()

        self.assertTrue(result["order_fill_state_review_ok"])
        self.assertEqual(before_order_queue, self._sha256(order_queue))
        self.assertEqual(before_fills, fills_path.exists())
        self.assertEqual(before_positions, positions_path.exists())

    def test_no_send_order_chejan_gui_or_timer_references(self) -> None:
        module_text = order_fill_state_service.__loader__.get_source(order_fill_state_service.__name__)

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("send_order_entrypoint", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)

    def test_commit_requires_successful_review_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            review = self._review()
            review["order_fill_state_review_ok"] = False

            result = self._commit(path, review_result=review)

            self.assertFalse(result["order_fill_state_committed"])
            self.assertEqual("review_result", result["fill_state_stage"])

    def test_commit_requires_manual_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._commit(path, context={})

            self.assertFalse(result["order_fill_state_committed"])
            self.assertEqual("operator_confirmation", result["fill_state_stage"])

    def test_commit_requires_queue_path(self) -> None:
        result = self._commit(None)

        self.assertFalse(result["order_fill_state_committed"])
        self.assertEqual("queue_path", result["fill_state_stage"])

    def test_commit_blocks_bad_queue_structure(self) -> None:
        cases = [
            ("missing", None),
            ("corrupt", "{bad json"),
            ("root", []),
            ("orders", {"version": 1, "orders": {}}),
            ("item", {"version": 1, "orders": ["invalid"]}),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, payload in cases:
                with self.subTest(name=name):
                    path = Path(tmpdir) / f"{name}.json"
                    if name == "missing":
                        pass
                    elif name == "corrupt":
                        path.write_text(payload, encoding="utf-8")
                    else:
                        path.write_text(json.dumps(payload), encoding="utf-8")

                    result = self._commit(path)

                    self.assertFalse(result["order_fill_state_committed"])
                    self.assertEqual("read_queue", result["fill_state_stage"])

    def test_commit_order_queued_to_partially_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._commit(path)
            order = self._read_json(path)["orders"][0]

            self.assertTrue(result["order_fill_state_committed"])
            self.assertEqual("ORDER_LIFECYCLE_REVIEW_REQUIRED", result["next_stage"])
            self.assertEqual("ORDER_QUEUED", result["before_status"])
            self.assertEqual("PARTIALLY_FILLED", result["after_status"])
            self.assertEqual("PARTIALLY_FILLED", order["status"])
            self.assertEqual("PARTIAL_FILL", order["fill_state"])
            self.assertEqual("FILL_1", order["last_fill_id"])
            self.assertEqual(3, order["total_filled_quantity"])
            self.assertEqual(7, order["remaining_quantity"])

    def test_commit_order_queued_to_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            review = self._review(
                fill_result=self._fill_result(event_type="FULL_FILL", filled_quantity=10),
                fill=self._fill(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0),
            )

            result = self._commit(path, review_result=review)
            order = self._read_json(path)["orders"][0]

            self.assertTrue(result["order_fill_state_committed"])
            self.assertEqual("FILLED", result["after_status"])
            self.assertEqual("FILLED", order["status"])
            self.assertEqual("FULL_FILL", order["fill_state"])
            self.assertTrue(order["filled_at"])

    def test_commit_partially_filled_to_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            order = self._order(status="PARTIALLY_FILLED", total_filled_quantity=3, remaining_quantity=7)
            path = self._write_queue(tmpdir, order=order)
            review = self._review(
                fill_result=self._fill_result(event_type="FULL_FILL", filled_quantity=7),
                fill=self._fill(event_type="FULL_FILL", filled_quantity=7, remaining_quantity=0),
                order=order,
            )

            result = self._commit(path, review_result=review)

            self.assertTrue(result["order_fill_state_committed"])
            self.assertEqual("PARTIALLY_FILLED", result["before_status"])
            self.assertEqual("FILLED", result["after_status"])

    def test_commit_blocks_disallowed_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            order = self._order(status="PARTIALLY_FILLED", total_filled_quantity=3)
            path = self._write_queue(tmpdir, order=order)
            review = self._review(order=order)

            result = self._commit(path, review_result=review)

            self.assertFalse(result["order_fill_state_committed"])
            self.assertEqual("transition", result["fill_state_stage"])
            self.assertIn("transition PARTIALLY_FILLED -> PARTIALLY_FILLED is not allowed", result["blocked_reasons"])

    def test_commit_blocks_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, order=self._order(order_id="OTHER"))

            result = self._commit(path)

            self.assertFalse(result["order_fill_state_committed"])
            self.assertEqual("identity", result["fill_state_stage"])

    def test_commit_creates_backup_and_changes_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            before = self._sha256(path)

            result = self._commit(path)

            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertEqual(before, result["before_sha256"])
            self.assertEqual(self._sha256(path), result["after_sha256"])
            self.assertNotEqual(result["before_sha256"], result["after_sha256"])

    def test_commit_backup_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._commit(path, backup=False)

            self.assertTrue(result["order_fill_state_committed"])
            self.assertIsNone(result["backup_path"])
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_commit_stale_snapshot_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            snapshot = {"sha256": self._sha256(path)}
            data = self._read_json(path)
            data["updated_at"] = "changed"
            path.write_text(json.dumps(data), encoding="utf-8")

            result = self._commit(path, queue_snapshot=snapshot)

            self.assertFalse(result["order_fill_state_committed"])
            self.assertEqual("stale_queue", result["fill_state_stage"])
            self.assertIn("order_queue file changed after order fill state review; manual review required", result["blocked_reasons"])

    def test_commit_does_not_modify_fills_or_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = self._write_queue(tmpdir)
            fills_path = Path(tmpdir) / "fills.json"
            positions_path = Path(tmpdir) / "positions.json"
            fills_path.write_text(json.dumps({"version": 1, "fills": []}), encoding="utf-8")
            positions_path.write_text(json.dumps({"version": 1, "positions": []}), encoding="utf-8")
            fills_hash = self._sha256(fills_path)
            positions_hash = self._sha256(positions_path)

            result = self._commit(queue_path)

            self.assertTrue(result["order_fill_state_committed"])
            self.assertEqual(fills_hash, self._sha256(fills_path))
            self.assertEqual(positions_hash, self._sha256(positions_path))

    def test_commit_inputs_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            review = self._review()
            original = deepcopy(review)

            commit_order_fill_state(
                review,
                path,
                context={"manual_order_fill_state_commit_confirmed": True},
            )

            self.assertEqual(original, review)

    def test_get_order_fill_state_reads_committed_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            order = self._order(
                status="PARTIALLY_FILLED",
                fill_state="PARTIAL_FILL",
                total_filled_quantity=3,
                remaining_quantity=7,
                last_fill_id="FILL_1",
                updated_at="2026-07-04 10:10:00",
            )
            path = self._write_queue(tmpdir, order=order)

            result = get_order_fill_state("ORDER_1", path)

            self.assertTrue(result["ok"])
            self.assertEqual("ORDER_FILL_STATE_READ", result["stage"])
            self.assertEqual("ORDER_1", result["order_id"])
            self.assertEqual("PARTIALLY_FILLED", result["status"])
            self.assertEqual("PARTIAL_FILL", result["fill_state"])
            self.assertEqual(3, result["total_filled_quantity"])
            self.assertEqual(7, result["remaining_quantity"])
            self.assertEqual("FILL_1", result["last_fill_id"])
            self.assertEqual("2026-07-04 10:10:00", result["updated_at"])
            self.assertIsNone(result["filled_at"])

    def test_get_order_fill_state_missing_order_id_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = get_order_fill_state("MISSING_ORDER", path)

            self.assertFalse(result["ok"])
            self.assertEqual("not_found", result["read_stage"])
            self.assertIn("order_id was not found", result["blocked_reasons"])

    def test_get_order_fill_state_blocks_bad_queue_structure(self) -> None:
        cases = [
            ("missing", None),
            ("corrupt", "{bad json"),
            ("root", []),
            ("orders", {"version": 1, "orders": {}}),
            ("item", {"version": 1, "orders": ["invalid"]}),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, payload in cases:
                with self.subTest(name=name):
                    path = Path(tmpdir) / f"{name}.json"
                    if name == "missing":
                        pass
                    elif name == "corrupt":
                        path.write_text(payload, encoding="utf-8")
                    else:
                        path.write_text(json.dumps(payload), encoding="utf-8")

                    result = get_order_fill_state("ORDER_1", path)

                    self.assertFalse(result["ok"])
                    self.assertEqual("read_queue", result["read_stage"])

    def test_get_order_fill_state_is_read_only_for_runtime_queue(self) -> None:
        order_queue = Path("runtime") / "order_queue.json"
        before = self._sha256(order_queue)

        get_order_fill_state("ORDER_1", order_queue)

        self.assertEqual(before, self._sha256(order_queue))

    def test_get_order_fill_state_does_not_mutate_inputs_or_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            order = self._order(
                status="FILLED",
                fill_state="FULL_FILL",
                total_filled_quantity=10,
                remaining_quantity=0,
                last_fill_id="FILL_1",
                updated_at="2026-07-04 10:11:00",
                filled_at="2026-07-04 10:11:00",
            )
            original_order = deepcopy(order)
            path = self._write_queue(tmpdir, order=order)
            before = self._sha256(path)

            result = get_order_fill_state("ORDER_1", path)

            self.assertTrue(result["ok"])
            self.assertEqual(original_order, order)
            self.assertEqual(before, self._sha256(path))


if __name__ == "__main__":
    unittest.main()
