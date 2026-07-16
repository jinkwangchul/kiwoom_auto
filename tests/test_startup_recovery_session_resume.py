import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from gui_auto_trade_run_control import auto_trade_start_selected_auto_trades
from gui_auto_trade_timer import auto_trade_on_time_policy_timer_tick
from operator_reconciliation_service import assess_startup_recovery


class StartupRecoverySessionResumeTest(unittest.TestCase):
    def _write(self, path: Path, field: str, items: list[dict[str, object]]) -> None:
        path.write_text(
            json.dumps(
                {"version": 1, "revision": 0, "updated_at": "before", field: items},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _runtime(self, root: Path) -> dict[str, Path]:
        paths = {
            "queue_path": root / "order_queue.json",
            "fills_path": root / "fills.json",
            "positions_path": root / "positions.json",
            "broker_holdings_path": root / "broker_holdings.json",
            "order_executions_path": root / "order_executions.json",
            "order_locks_path": root / "order_locks.json",
            "routine_signals_path": root / "routine_signals.json",
        }
        for key, field in (
            ("queue_path", "orders"),
            ("fills_path", "fills"),
            ("positions_path", "positions"),
            ("broker_holdings_path", "holdings"),
            ("order_executions_path", "executions"),
            ("order_locks_path", "locks"),
            ("routine_signals_path", "signals"),
        ):
            self._write(paths[key], field, [])
        return paths

    def test_clean_runtime_is_resume_ready_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            state = root / "state.json"
            state.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            before = {path: path.read_bytes() for path in [*paths.values(), state]}

            result = assess_startup_recovery(
                **paths,
                stock_state_paths=[state],
            )

            self.assertEqual("RESUME_READY", result["status"])
            self.assertTrue(result["operator_approval_allowed"])
            self.assertFalse(result["automatic_trading_allowed"])
            self.assertTrue(result["snapshot_hash"])
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_unfinished_queue_requires_operator_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            self._write(
                paths["queue_path"],
                "orders",
                [{"id": "Q1", "order_id": "O1", "status": "ORDER_QUEUED"}],
            )

            result = assess_startup_recovery(**paths)

            self.assertEqual("REVIEW_REQUIRED", result["status"])
            self.assertTrue(result["operator_approval_allowed"])
            self.assertIn("ORDER_QUEUED", " ".join(result["review_reasons"]))

    def test_uncertain_send_and_runtime_lock_block_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            self._write(
                paths["queue_path"],
                "orders",
                [{"id": "Q1", "order_id": "O1", "status": "SEND_UNCERTAIN"}],
            )
            self._write(paths["order_locks_path"], "locks", [{"lock_id": "LOCK1"}])

            result = assess_startup_recovery(**paths)

            self.assertEqual("BLOCKED_RECOVERY", result["status"])
            self.assertFalse(result["operator_approval_allowed"])
            reasons = " ".join(result["blocked_reasons"])
            self.assertIn("SEND_UNCERTAIN", reasons)
            self.assertIn("LOCK1", reasons)

    def test_invalid_json_and_partial_runtime_pair_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            paths["positions_path"].write_text("{broken", encoding="utf-8")
            paths["order_locks_path"].unlink()

            result = assess_startup_recovery(**paths)

            self.assertEqual("INVALID_RUNTIME", result["status"])
            reasons = " ".join(result["invalid_reasons"])
            self.assertIn("positions.json", reasons)
            self.assertIn("must both exist or both be absent", reasons)

    def test_broker_mismatch_and_manual_reconciliation_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            self._write(
                paths["queue_path"],
                "orders",
                [
                    {
                        "id": "Q1",
                        "order_id": "O1",
                        "status": "FILLED",
                        "manual_reconciliation_required": True,
                        "manual_reconciliation_reason": "POSITION_UPDATE",
                    }
                ],
            )
            self._write(
                paths["broker_holdings_path"],
                "holdings",
                [
                    {
                        "account_no": "123",
                        "code": "003550",
                        "manual_reconciliation_required": True,
                        "reconciliation_status": "QUANTITY_MISMATCH",
                    }
                ],
            )

            result = assess_startup_recovery(**paths)

            self.assertEqual("REVIEW_REQUIRED", result["status"])
            self.assertEqual(2, result["operator_reconciliation"]["summary"]["total"])

    def test_fill_without_position_application_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            self._write(
                paths["fills_path"],
                "fills",
                [
                    {
                        "fill_id": "FILL_1",
                        "execution_identity_source": "execution_no",
                        "execution_identity": "EXEC_NO_1",
                        "broker": "KIWOOM",
                        "account_no": "123",
                        "code": "003550",
                        "order_queued_id": "Q1",
                        "filled_quantity": 3,
                    }
                ],
            )

            result = assess_startup_recovery(**paths)

            self.assertEqual("REVIEW_REQUIRED", result["status"])
            self.assertIn("FILL_1", " ".join(result["review_reasons"]))

    def test_applied_fill_does_not_create_position_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            fill = {
                "fill_id": "FILL_1",
                "execution_identity_source": "execution_no",
                "execution_identity": "EXEC_NO_1",
                "broker": "KIWOOM",
                "account_no": "123",
                "code": "003550",
                "order_queued_id": "Q1",
                "filled_quantity": 3,
            }
            self._write(paths["fills_path"], "fills", [fill])
            self._write(
                paths["positions_path"],
                "positions",
                [
                    {
                        "position_id": "P1",
                        "broker": "KIWOOM",
                        "account_no": "123",
                        "code": "003550",
                        "applied_fill_ids": ["FILL_1"],
                    }
                ],
            )

            result = assess_startup_recovery(**paths)

            self.assertNotIn(
                "Fill is not applied",
                " ".join(result["review_reasons"]),
            )

    def test_runtime_execution_queue_identity_mismatch_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            self._write(
                paths["queue_path"],
                "orders",
                [
                    {
                        "id": "Q1",
                        "order_id": "O1",
                        "status": "FILLED",
                        "execution_id": "EXEC_1",
                        "request_hash": "QUEUE_HASH",
                    }
                ],
            )
            self._write(
                paths["order_executions_path"],
                "executions",
                [
                    {
                        "execution_id": "EXEC_1",
                        "order_id": "O1",
                        "request_hash": "EXECUTION_HASH",
                    }
                ],
            )

            result = assess_startup_recovery(**paths)

            self.assertEqual("INVALID_RUNTIME", result["status"])
            self.assertIn("identity mismatch", " ".join(result["invalid_reasons"]))

    def test_unfinished_routine_signal_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            self._write(
                paths["routine_signals_path"],
                "signals",
                [{"id": "SIG_1", "status": "PENDING"}],
            )

            result = assess_startup_recovery(**paths)

            self.assertEqual("REVIEW_REQUIRED", result["status"])
            self.assertIn("SIG_1", " ".join(result["review_reasons"]))

    def test_start_and_timer_do_nothing_before_recovery_approval(self) -> None:
        class StartWindow:
            def __init__(self) -> None:
                self.selected_stock_infos = Mock()

            def require_startup_recovery_session(self, _action: str) -> bool:
                return False

        start_window = StartWindow()

        auto_trade_start_selected_auto_trades(start_window)

        start_window.selected_stock_infos.assert_not_called()

        class TimerWindow:
            def __init__(self) -> None:
                self.recalculate_all_status_by_operation_policy = Mock()
                self.update_controls_calls = 0

            def isVisible(self) -> bool:
                return True

            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return False

            def update_startup_recovery_controls(self) -> None:
                self.update_controls_calls += 1

        timer_window = TimerWindow()

        auto_trade_on_time_policy_timer_tick(timer_window)

        timer_window.recalculate_all_status_by_operation_policy.assert_not_called()
        self.assertEqual(1, timer_window.update_controls_calls)


if __name__ == "__main__":
    unittest.main()
