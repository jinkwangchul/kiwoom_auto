import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def _install_pyqt5_import_stubs() -> None:
    try:
        import PyQt5  # noqa: F401
        return
    except Exception:
        pass

    class _FakeQt:
        UserRole = 256
        AlignCenter = 0
        AlignLeft = 0
        AlignRight = 0
        AlignVCenter = 0

    class _FakeColor:
        def __init__(self, value: str = "") -> None:
            self._value = str(value or "").lower()

        def name(self) -> str:
            return self._value

    class _FakeBrush:
        def __init__(self, color: _FakeColor | None = None) -> None:
            self._color = color or _FakeColor()

        def color(self) -> _FakeColor:
            return self._color

    class _FakeFont:
        def __init__(self) -> None:
            self._point_size = 10
            self._bold = False

        def pointSize(self) -> int:
            return self._point_size

        def setPointSize(self, value: int) -> None:
            self._point_size = int(value)

        def setBold(self, value: bool) -> None:
            self._bold = bool(value)

    class _FakeTableWidgetItem:
        def __init__(self, text: str = "") -> None:
            self._text = str(text)
            self._font = _FakeFont()
            self._foreground = _FakeBrush()
            self._data: dict[int, object] = {}
            self._tooltip = ""
            self._alignment = 0

        def text(self) -> str:
            return self._text

        def font(self) -> _FakeFont:
            return self._font

        def setFont(self, font: _FakeFont) -> None:
            self._font = font

        def setTextAlignment(self, alignment: int) -> None:
            self._alignment = alignment

        def setForeground(self, color: _FakeColor) -> None:
            self._foreground = _FakeBrush(color)

        def foreground(self) -> _FakeBrush:
            return self._foreground

        def setToolTip(self, value: str) -> None:
            self._tooltip = str(value)

        def setData(self, role: int, value: object) -> None:
            self._data[role] = value

        def data(self, role: int) -> object:
            return self._data.get(role)

    class _FakeWidget:
        Yes = 1
        No = 0

        def __init__(self, *args, **kwargs) -> None:
            pass

        def __getattr__(self, _name: str):
            return _FakeWidget()

        def __call__(self, *args, **kwargs):
            return _FakeWidget()

        @staticmethod
        def warning(*args, **kwargs) -> None:
            return None

    pyqt5 = types.ModuleType("PyQt5")
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtgui = types.ModuleType("PyQt5.QtGui")
    qtwidgets = types.ModuleType("PyQt5.QtWidgets")

    qtcore.Qt = _FakeQt
    qtcore.__getattr__ = lambda _name: _FakeWidget
    qtgui.QColor = _FakeColor
    qtgui.QFont = _FakeFont
    qtgui.__getattr__ = lambda _name: _FakeWidget
    qtwidgets.QTableWidgetItem = _FakeTableWidgetItem
    qtwidgets.QMessageBox = _FakeWidget
    qtwidgets.__getattr__ = lambda _name: _FakeWidget

    sys.modules["PyQt5"] = pyqt5
    sys.modules["PyQt5.QtCore"] = qtcore
    sys.modules["PyQt5.QtGui"] = qtgui
    sys.modules["PyQt5.QtWidgets"] = qtwidgets


_install_pyqt5_import_stubs()

import gui_main_table_loader
from gui_auto_trade_policy import auto_trade_setting_current_session_trade_started
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

    def test_persisted_trade_enabled_does_not_mark_current_session_started_before_recovery(self) -> None:
        class Window:
            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return False

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps({"status": "WAIT_BUY", "trade_enabled": True}),
                encoding="utf-8",
            )
            before = state_path.read_bytes()

            self.assertFalse(
                auto_trade_setting_current_session_trade_started(Window(), True)
            )

            self.assertEqual(before, state_path.read_bytes())

    def test_persisted_trade_enabled_becomes_current_session_started_after_recovery(self) -> None:
        class Window:
            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return True

        self.assertTrue(auto_trade_setting_current_session_trade_started(Window(), True))
        self.assertFalse(auto_trade_setting_current_session_trade_started(Window(), False))

    def test_main_running_table_uses_same_current_session_recovery_gate(self) -> None:
        class Header:
            def setSortIndicator(self, *_args) -> None:
                return None

        class Table:
            def __init__(self) -> None:
                self.row_count = -1
                self.items: dict[tuple[int, int], object] = {}

            def columnCount(self) -> int:
                return 10

            def setRowCount(self, count: int) -> None:
                self.row_count = count

            def setItem(self, row: int, col: int, item: object) -> None:
                self.items[(row, col)] = item

            def sortItems(self, *_args) -> None:
                return None

            def horizontalHeader(self) -> Header:
                return Header()

        class Window:
            def __init__(self, ready: bool) -> None:
                self.running_stock_table = Table()
                self._main_running_sort_column = -1
                self._main_running_sort_order = 0
                self._ready = ready

            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return self._ready

        state = {
            "status": "WAIT_BUY",
            "trade_enabled": True,
            "trade_started_at": "2026-07-16 09:00:00",
        }
        config = {"operation_mode": "SCHEDULED"}
        stock_dir = Path("stocks") / "003550_LG"

        def read_json(path: Path) -> dict[str, object]:
            if path.name == "state.json":
                return dict(state)
            if path.name == "config.json":
                return dict(config)
            return {}

        patches = (
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=[
                    {
                        "code": "003550",
                        "name": "LG",
                        "routines": ["지표추종매매"],
                    }
                ],
            ),
            patch.object(
                gui_main_table_loader,
                "stock_runtime_dir_for_routine",
                return_value=stock_dir,
            ),
            patch.object(gui_main_table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                gui_main_table_loader,
                "pending_order_side_quantities",
                return_value=(0, 0),
            ),
            patch.object(
                gui_main_table_loader,
                "create_auto_trade_situation_item",
                side_effect=lambda _state, trade_started, _status: {
                    "trade_started": trade_started
                },
            ),
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            blocked = Window(ready=False)
            gui_main_table_loader.main_load_running_stock_table(blocked)
            self.assertEqual(1, blocked.running_stock_table.row_count)
            self.assertEqual(
                {"trade_started": False},
                blocked.running_stock_table.items[(0, 4)],
            )

            approved = Window(ready=True)
            gui_main_table_loader.main_load_running_stock_table(approved)
            self.assertEqual(1, approved.running_stock_table.row_count)
            self.assertEqual(
                {"trade_started": True},
                approved.running_stock_table.items[(0, 4)],
            )


if __name__ == "__main__":
    unittest.main()
