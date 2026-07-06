# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kiwoom_order_adapter


class KiwoomOrderAdapterPreviewTests(unittest.TestCase):
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

    def _write_guard(self) -> None:
        self._write_json(
            self.guard_path,
            {
                "real_trade_enabled": True,
                "kiwoom_logged_in": True,
                "account_selected": True,
                "operator_confirmed": True,
                "account_no": "12345678",
            },
        )

    def _write_queue(self, *, status: str = "REAL_READY", side: str = "SELL") -> dict:
        order = {
            "id": "ORDER_1",
            "status": status,
            "side": side,
            "code": "003550",
            "name": "LG",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "source_signal_id": "SIG_1",
        }
        self._write_json(
            self.queue_path,
            {
                "version": 1,
                "updated_at": "",
                "orders": [order],
            },
        )
        return order

    def _queue_text(self) -> str:
        return self.queue_path.read_text(encoding="utf-8")

    def test_real_ready_sell_builds_preview_without_saving(self) -> None:
        self._write_guard()
        self._write_queue(status="REAL_READY", side="SELL")
        before = self._queue_text()

        with mock.patch.object(kiwoom_order_adapter, "send_order_stub") as send_stub:
            result = kiwoom_order_adapter.build_kiwoom_order_request_preview_for_order(
                "ORDER_1",
                queue_path=self.queue_path,
                guard_path=self.guard_path,
            )

        after = self._queue_text()
        self.assertTrue(result["ok"])
        self.assertEqual("preview_built", result["status"])
        self.assertTrue(result["not_saved"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["send_order_stub_called"])
        self.assertEqual("003550", result["request_preview"]["code"])
        self.assertEqual(10, result["request_preview"]["quantity"])
        self.assertEqual("12345678", result["request_preview"]["account_no"])
        self.assertEqual(before, after)
        send_stub.assert_not_called()

    def test_real_ready_buy_builds_preview_without_send_order_stub(self) -> None:
        self._write_guard()
        self._write_queue(status="REAL_READY", side="BUY")

        with mock.patch.object(kiwoom_order_adapter, "send_order_stub") as send_stub:
            result = kiwoom_order_adapter.build_kiwoom_order_request_preview_for_order(
                "ORDER_1",
                queue_path=self.queue_path,
                guard_path=self.guard_path,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("preview_built", result["status"])
        self.assertEqual("003550", result["request_preview"]["code"])
        self.assertFalse(result["request_preview"]["send_order_enabled"])
        self.assertFalse(result["send_order_called"])
        send_stub.assert_not_called()

    def test_missing_guard_is_skipped_without_creating_file(self) -> None:
        self._write_queue(status="REAL_READY", side="SELL")
        self.assertFalse(self.guard_path.exists())

        result = kiwoom_order_adapter.build_kiwoom_order_request_preview_for_order(
            "ORDER_1",
            queue_path=self.queue_path,
            guard_path=self.guard_path,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("skipped", result["status"])
        self.assertFalse(self.guard_path.exists())

    def test_non_real_ready_statuses_are_skipped_without_file_change(self) -> None:
        self._write_guard()
        for status in ("APPROVED", "EXECUTABLE", "BLOCKED_REAL"):
            with self.subTest(status=status):
                self._write_queue(status=status, side="SELL")
                before = self._queue_text()
                result = kiwoom_order_adapter.build_kiwoom_order_request_preview_for_order(
                    "ORDER_1",
                    queue_path=self.queue_path,
                    guard_path=self.guard_path,
                )
                after = self._queue_text()
                self.assertTrue(result["ok"])
                self.assertEqual("skipped", result["status"])
                self.assertEqual(before, after)

    def test_missing_order_id_is_not_found(self) -> None:
        self._write_guard()
        self._write_queue(status="REAL_READY", side="SELL")
        before = self._queue_text()

        result = kiwoom_order_adapter.build_kiwoom_order_request_preview_for_order(
            "ORDER_MISSING",
            queue_path=self.queue_path,
            guard_path=self.guard_path,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("not_found", result["status"])
        self.assertEqual(before, self._queue_text())


if __name__ == "__main__":
    unittest.main()
