# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gui_auto_trade_status_ops as status_ops
import operation_policy_gate
from auto_close_runtime_policy_snapshot import (
    auto_close_runtime_snapshot_metadata,
)
from gui_auto_trade_policy import clear_auto_close_runtime_metadata


class AutoCloseRuntimePolicySnapshotTest(unittest.TestCase):
    def test_capture_once_and_clear_after_exit(self):
        state = {"status": "RUNNING"}
        first = auto_close_runtime_snapshot_metadata(
            state=state,
            before_status="RUNNING",
            after_status="AUTO_CLOSE",
            auto_close_policy={"method": "현재가", "profit_percent": "1.0"},
            captured_at="2026-07-27 13:30:00",
        )
        self.assertEqual(first["auto_close_method"], "현재가")
        self.assertEqual(first["auto_close_policy"]["profit_percent"], "1.0")

        active_state = {**state, **first, "status": "AUTO_CLOSE"}
        repeated = auto_close_runtime_snapshot_metadata(
            state=active_state,
            before_status="AUTO_CLOSE",
            after_status="AUTO_CLOSE",
            auto_close_policy={"method": "시장가"},
            captured_at="2026-07-27 13:31:00",
        )
        self.assertEqual(repeated, {})

        cleared = auto_close_runtime_snapshot_metadata(
            state=active_state,
            before_status="AUTO_CLOSE",
            after_status="AUTO_CLOSED",
            auto_close_policy={"method": "시장가"},
            captured_at="2026-07-27 15:30:00",
        )
        self.assertEqual(
            cleared,
            {
                "auto_close_requested_at": "",
                "auto_close_source": "",
                "auto_close_method": "",
                "auto_close_policy": {},
            },
        )

    def test_recalculation_writer_captures_and_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_test"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            config_path = stock_dir / "config.json"
            queue_path = Path(temp_dir) / "order_queue.json"
            fills_path = Path(temp_dir) / "fills.json"
            operation_state_path = Path(temp_dir) / "operation_state.json"
            queue_path.write_text(
                json.dumps({"version": 1, "orders": []}),
                encoding="utf-8",
            )
            fills_path.write_text(
                json.dumps({"version": 1, "fills": []}),
                encoding="utf-8",
            )
            operation_state_path.write_text(
                json.dumps(
                    {
                        "operation_date": "2026-07-27",
                        "operation_status": "RUNNING",
                        "operation_started_at": "2026-07-27 09:00:00",
                        "operation_participant_stock_codes": ["005930"],
                    }
                ),
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "trade_started_at": "2026-07-27 09:00:00",
                    }
                ),
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "operation_mode": "SCHEDULED",
                        "assigned_routine_instance_id": "routine-instance-1",
                    }
                ),
                encoding="utf-8",
            )

            class Window:
                @staticmethod
                def update_stock_status(
                    target_dir,
                    _code,
                    _name,
                    new_status,
                    metadata,
                    _log_suffix,
                ):
                    state = json.loads(
                        (target_dir / "state.json").read_text(encoding="utf-8")
                    )
                    state["status"] = new_status
                    state.update(metadata)
                    (target_dir / "state.json").write_text(
                        json.dumps(state),
                        encoding="utf-8",
                    )
                    return True

            with (
                patch.object(
                    status_ops,
                    "status_after_operation_mode_change",
                    return_value="AUTO_CLOSE",
                ),
                patch.object(
                    status_ops,
                    "read_operation_policy",
                    return_value={
                        "auto_close": {
                            "method": "현재가",
                            "profit_percent": "1.0",
                        }
                    },
                ),
                patch.object(
                    status_ops,
                    "now_text",
                    return_value="2026-07-27 13:30:00",
                ),
                patch.object(status_ops, "ORDER_QUEUE_PATH", queue_path),
                patch.object(status_ops, "FILLS_PATH", fills_path),
                patch.object(
                    operation_policy_gate,
                    "OPERATION_STATE_PATH",
                    operation_state_path,
                ),
                patch.object(
                    operation_policy_gate,
                    "now_text",
                    return_value="2026-07-27 13:30:00",
                ),
            ):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    Window(),
                    stock_dir,
                    "005930",
                    "test",
                    "timer",
                    silent_unchanged=True,
                )
            self.assertEqual(result, ("changed", "RUNNING", "AUTO_CLOSE"))
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["auto_close_method"], "현재가")
            self.assertEqual(saved["auto_close_policy"]["profit_percent"], "1.0")
            self.assertEqual(
                saved["auto_close_requested_at"],
                "2026-07-27 13:30:00",
            )

            with (
                patch.object(
                    status_ops,
                    "status_after_operation_mode_change",
                    return_value="AUTO_CLOSE",
                ),
                patch.object(
                    status_ops,
                    "read_operation_policy",
                    return_value={"auto_close": {"method": "시장가"}},
                ),
                patch.object(
                    status_ops,
                    "now_text",
                    return_value="2026-07-27 13:31:00",
                ),
                patch.object(status_ops, "ORDER_QUEUE_PATH", queue_path),
                patch.object(status_ops, "FILLS_PATH", fills_path),
            ):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    Window(),
                    stock_dir,
                    "005930",
                    "test",
                    "timer",
                    silent_unchanged=True,
                )
            self.assertEqual(result, ("unchanged", "AUTO_CLOSE", "AUTO_CLOSE"))
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["auto_close_method"], "현재가")
            self.assertEqual(saved["auto_close_policy"]["profit_percent"], "1.0")

            with (
                patch.object(
                    status_ops,
                    "status_after_operation_mode_change",
                    return_value="MONITORING",
                ),
                patch.object(
                    status_ops,
                    "read_operation_policy",
                    return_value={"auto_close": {"method": "시장가"}},
                ),
                patch.object(
                    status_ops,
                    "now_text",
                    return_value="2026-07-27 15:30:00",
                ),
                patch.object(status_ops, "ORDER_QUEUE_PATH", queue_path),
                patch.object(status_ops, "FILLS_PATH", fills_path),
            ):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    Window(),
                    stock_dir,
                    "005930",
                    "test",
                    "timer",
                    silent_unchanged=True,
                )
            self.assertEqual(result, ("changed", "AUTO_CLOSE", "MONITORING"))
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["auto_close_requested_at"], "")
            self.assertEqual(saved["auto_close_method"], "")
            self.assertEqual(saved["auto_close_policy"], {})

    def test_no_target_cleanup_clears_snapshot_identity(self):
        cleaned = clear_auto_close_runtime_metadata(
            {
                "status": "AUTO_CLOSE",
                "auto_close_requested_at": "2026-07-27 13:30:00",
                "auto_close_source": "TIME_POLICY",
                "auto_close_method": "현재가",
                "auto_close_policy": {"method": "현재가"},
            }
        )
        self.assertEqual(cleaned["auto_close_requested_at"], "")
        self.assertEqual(cleaned["auto_close_source"], "")
        self.assertEqual(cleaned["auto_close_method"], "")
        self.assertEqual(cleaned["auto_close_policy"], {})


if __name__ == "__main__":
    unittest.main()
