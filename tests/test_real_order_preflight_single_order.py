# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import real_order_preflight


class RealOrderPreflightSingleOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.queue_path = self.root / "runtime" / "order_queue.json"
        self.guard_path = self.root / "runtime" / "real_trade_guard.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_guard(self, **overrides: object) -> None:
        guard = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "operator_confirmed": True,
            "account_no": "12345678",
        }
        guard.update(overrides)
        self._write_json(self.guard_path, guard)

    def _write_queue(
        self,
        *,
        status: str = "EXECUTABLE",
        execution_enabled: bool = False,
        order_id: str = "ORDER_1",
    ) -> None:
        self._write_json(
            self.queue_path,
            {
                "version": 1,
                "updated_at": "",
                "orders": [
                    {
                        "id": order_id,
                        "status": status,
                        "side": "BUY",
                        "quantity": 10,
                        "order_type": "BUY_SIGNAL_CANDIDATE",
                        "execution_enabled": execution_enabled,
                    }
                ],
            },
        )

    def _single_order(self) -> dict:
        data = json.loads(self.queue_path.read_text(encoding="utf-8"))
        orders = data.get("orders", [])
        self.assertEqual(1, len(orders))
        return orders[0]

    def test_executable_with_blocking_guard_moves_to_blocked_real(self) -> None:
        self._write_guard(real_trade_enabled=False)
        self._write_queue(status="EXECUTABLE", execution_enabled=False)

        result = real_order_preflight.apply_real_order_preflight_for_order(
            "ORDER_1",
            queue_path=self.queue_path,
            guard_path=self.guard_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("BLOCKED_REAL", result["after_status"])
        self.assertEqual("BLOCKED_REAL", order.get("status"))
        self.assertEqual("BLOCKED_REAL", order.get("real_preflight_status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_executable_with_passing_guard_and_execution_enabled_moves_to_real_ready(self) -> None:
        self._write_guard()
        self._write_queue(status="EXECUTABLE", execution_enabled=True)

        result = real_order_preflight.apply_real_order_preflight_for_order(
            "ORDER_1",
            queue_path=self.queue_path,
            guard_path=self.guard_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("REAL_READY", result["after_status"])
        self.assertEqual("REAL_READY", order.get("status"))
        self.assertEqual("REAL_READY", order.get("real_preflight_status"))
        self.assertTrue(order.get("execution_enabled"))

    def test_non_executable_statuses_are_skipped(self) -> None:
        self._write_guard()
        for status in ("APPROVED", "PENDING", "BLOCKED", "REAL_READY", "BLOCKED_REAL"):
            with self.subTest(status=status):
                self._write_queue(status=status, execution_enabled=False)
                result = real_order_preflight.apply_real_order_preflight_for_order(
                    "ORDER_1",
                    queue_path=self.queue_path,
                    guard_path=self.guard_path,
                )
                order = self._single_order()
                self.assertTrue(result["ok"])
                self.assertFalse(result["changed"])
                self.assertEqual("skipped", result["status"])
                self.assertEqual(status, order.get("status"))

    def test_missing_order_id_is_not_found(self) -> None:
        self._write_guard()
        self._write_queue(status="EXECUTABLE", execution_enabled=False)

        result = real_order_preflight.apply_real_order_preflight_for_order(
            "ORDER_MISSING",
            queue_path=self.queue_path,
            guard_path=self.guard_path,
        )
        order = self._single_order()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("not_found", result["status"])
        self.assertEqual("EXECUTABLE", order.get("status"))

    def test_missing_guard_does_not_create_default_guard_file(self) -> None:
        self._write_queue(status="EXECUTABLE", execution_enabled=False)
        self.assertFalse(self.guard_path.exists())

        result = real_order_preflight.apply_real_order_preflight_for_order(
            "ORDER_1",
            queue_path=self.queue_path,
            guard_path=self.guard_path,
        )
        order = self._single_order()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("skipped", result["status"])
        self.assertFalse(self.guard_path.exists())
        self.assertEqual("EXECUTABLE", order.get("status"))


if __name__ == "__main__":
    unittest.main()
