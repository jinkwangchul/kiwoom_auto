# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import gui_auto_trade_setting_window as auto_gui
import gui_windows as main_gui


class RealtimeEventRuntimeE2ETest(unittest.TestCase):
    def _context(self) -> dict[str, object]:
        return {
            "kiwoom_api_live_event": True,
            "live_event_source": "KiwoomApi.raw_chejan_received",
        }

    def _write_queue(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": "2026-07-24 09:00:00",
                    "orders": [
                        {
                            "id": "ORDER_QUEUED_EVENT_E2E",
                            "status": "SEND_CALL_ACCEPTED",
                            "order_id": "ORDER_EVENT_E2E",
                            "request_hash": "HASH_EVENT_E2E",
                            "lock_id": "LOCK_EVENT_E2E",
                            "execution_id": "EXEC_EVENT_E2E",
                            "broker_order_no": "BRK_EVENT_E2E",
                            "account_no": "12345678",
                            "code": "003550",
                            "side": "BUY",
                            "quantity": 10,
                            "send_order_called": True,
                            "send_order_result_status": "SEND_ORDER_CALLED",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _event(
        self,
        *,
        execution_no: str,
        filled_quantity: int,
        remaining_quantity: int,
        received_at: str = "2026-07-24 10:00:00",
    ) -> dict[str, object]:
        return {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "fid_values": {
                "9201": "12345678",
                "9203": "BRK_EVENT_E2E",
                "9001": "A003550",
                "302": "LG",
                "907": "2",
                "913": "FILLED",
                "900": "10",
                "911": str(filled_quantity),
                "902": str(remaining_quantity),
                "910": "1000",
                "901": "1000",
                "909": execution_no,
            },
            "received_at": received_at,
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    def test_missing_event_evidence_is_fail_closed_for_both_gui_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_queue(queue_path)
            before = self._sha256(queue_path)
            raw_event = self._event(execution_no="MISSING_TIME", filled_quantity=3, remaining_quantity=7)
            raw_event.pop("received_at")
            setting_window = SimpleNamespace()
            main_window = SimpleNamespace(auto_trade_setting_window=setting_window)

            with (
                mock.patch.object(auto_gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(auto_gui, "FILLS_PATH", fills_path),
                mock.patch.object(auto_gui, "POSITIONS_PATH", positions_path),
            ):
                main_gui.MainWindow.on_kiwoom_raw_chejan_received(main_window, raw_event)

            self.assertFalse(main_window.last_chejan_record_result["recorded"])
            self.assertEqual("normalize", main_window.last_chejan_record_result["stage"])
            self.assertIs(setting_window.last_chejan_record_result, main_window.last_chejan_record_result)
            self.assertEqual(before, self._sha256(queue_path))
            self.assertFalse(fills_path.exists())
            self.assertFalse(positions_path.exists())

    def test_duplicate_out_of_order_and_restart_readback_preserve_latest_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_queue(queue_path)
            partial = self._event(
                execution_no="EXEC_EVENT_PARTIAL",
                filled_quantity=3,
                remaining_quantity=7,
                received_at="2026-07-24 10:00:00",
            )
            full = self._event(
                execution_no="EXEC_EVENT_FULL",
                filled_quantity=10,
                remaining_quantity=0,
                received_at="2026-07-24 10:01:00",
            )
            late_partial = self._event(
                execution_no="EXEC_EVENT_LATE",
                filled_quantity=5,
                remaining_quantity=5,
                received_at="2026-07-24 09:59:00",
            )

            with (
                mock.patch.object(auto_gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(auto_gui, "FILLS_PATH", fills_path),
                mock.patch.object(auto_gui, "POSITIONS_PATH", positions_path),
            ):
                first = auto_gui.handle_kiwoom_raw_chejan_event(partial, self._context())
                duplicate = auto_gui.handle_kiwoom_raw_chejan_event(partial, self._context())
                completed = auto_gui.handle_kiwoom_raw_chejan_event(full, self._context())
                completed_hashes = tuple(self._sha256(path) for path in (queue_path, fills_path, positions_path))
                stale = auto_gui.handle_kiwoom_raw_chejan_event(late_partial, self._context())

            self.assertTrue(first["recorded"], first)
            self.assertTrue(duplicate["duplicate_noop"], duplicate)
            self.assertTrue(completed["recorded"], completed)
            self.assertFalse(stale["recorded"], stale)
            self.assertEqual("chejan_target_match", stale["stage"])
            self.assertEqual(completed_hashes, tuple(self._sha256(path) for path in (queue_path, fills_path, positions_path)))

            queue_after_restart = json.loads(queue_path.read_text(encoding="utf-8"))
            fills_after_restart = json.loads(fills_path.read_text(encoding="utf-8"))
            positions_after_restart = json.loads(positions_path.read_text(encoding="utf-8"))
            self.assertEqual("FILLED", queue_after_restart["orders"][0]["status"])
            self.assertEqual(10, queue_after_restart["orders"][0]["cumulative_filled_quantity"])
            self.assertEqual(2, len(fills_after_restart["fills"]))
            self.assertEqual(10, positions_after_restart["positions"][0]["quantity"])


if __name__ == "__main__":
    unittest.main()
