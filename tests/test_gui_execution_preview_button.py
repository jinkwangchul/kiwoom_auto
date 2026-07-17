# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


class _QtImportStub:
    Accepted = 1
    Rejected = 0
    AlignCenter = 0
    AlignLeft = 0
    AlignRight = 0
    AlignVCenter = 0
    Checked = 2
    Unchecked = 0
    Horizontal = 1
    Vertical = 2
    NoFocus = 0
    CustomContextMenu = 0
    UserRole = 256
    DisplayRole = 0
    EditRole = 1
    BackgroundRole = 8
    ForegroundRole = 9
    TextAlignmentRole = 7
    AscendingOrder = 0
    DescendingOrder = 1
    ItemIsEnabled = 1
    ItemIsSelectable = 2

    def __init__(self, *args, **kwargs) -> None:
        self.clicked = _QtImportStubSignal()
        self.stateChanged = _QtImportStubSignal()
        self.currentIndexChanged = _QtImportStubSignal()
        self.itemSelectionChanged = _QtImportStubSignal()
        self.customContextMenuRequested = _QtImportStubSignal()

    def __getattr__(self, name):
        return _QtImportStub()

    def __call__(self, *args, **kwargs):
        return _QtImportStub()

    def __or__(self, other):
        return 0

    @staticmethod
    def getText(*args, **kwargs):
        return "", False


class _QtImportStubSignal:
    def connect(self, callback) -> None:
        self.callback = callback


def _install_pyqt5_import_stubs() -> None:
    if "PyQt5" in sys.modules:
        return

    pyqt5 = types.ModuleType("PyQt5")
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtgui = types.ModuleType("PyQt5.QtGui")
    qtwidgets = types.ModuleType("PyQt5.QtWidgets")

    qtcore.Qt = _QtImportStub()
    qtcore.QDate = _QtImportStub
    qtcore.QTime = _QtImportStub
    qtcore.QTimer = _QtImportStub
    qtcore.QItemSelectionModel = _QtImportStub
    qtcore.QRect = _QtImportStub

    qtgui.QColor = _QtImportStub
    qtgui.QFont = _QtImportStub
    qtcore.__getattr__ = lambda name: _QtImportStub
    qtgui.__getattr__ = lambda name: _QtImportStub
    qtwidgets.__getattr__ = lambda name: _QtImportStub

    for name in (
        "QApplication",
        "QAbstractItemView",
        "QCheckBox",
        "QComboBox",
        "QDateEdit",
        "QDialog",
        "QDialogButtonBox",
        "QFileDialog",
        "QFrame",
        "QGridLayout",
        "QGroupBox",
        "QHBoxLayout",
        "QInputDialog",
        "QLabel",
        "QLineEdit",
        "QListWidget",
        "QListWidgetItem",
        "QMenu",
        "QMessageBox",
        "QPushButton",
        "QStyle",
        "QStyleOptionButton",
        "QStyledItemDelegate",
        "QTableWidget",
        "QTableWidgetItem",
        "QTextEdit",
        "QTimeEdit",
        "QVBoxLayout",
        "QWidget",
        "QHeaderView",
    ):
        setattr(qtwidgets, name, _QtImportStub)

    qtwidgets.QDialog.Accepted = 1
    qtwidgets.QDialog.Rejected = 0
    qtwidgets.QTextEdit.NoWrap = "NoWrap"

    sys.modules["PyQt5"] = pyqt5
    sys.modules["PyQt5.QtCore"] = qtcore
    sys.modules["PyQt5.QtGui"] = qtgui
    sys.modules["PyQt5.QtWidgets"] = qtwidgets


_install_pyqt5_import_stubs()

import gui_auto_trade_setting_window as gui
import gui_windows as main_gui
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data


class _FakeWindow:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.reports: list[dict[str, object]] = []

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        self.messages.append(message)

    def show_execution_preview_report(self, report: dict[str, object]) -> None:
        self.reports.append(report)


class _FakeFont:
    def __init__(self, family: str = "", point_size: int = 10) -> None:
        self.family = family
        self.bold = False
        self.point_size = point_size

    def setBold(self, value: bool) -> None:
        self.bold = value

    def pointSize(self) -> int:
        return self.point_size

    def setPointSize(self, value: int) -> None:
        self.point_size = value


class _FakeMonoFont(_FakeFont):
    pass


class _FakeLabel:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.font_value = _FakeFont()

    def font(self) -> _FakeFont:
        return self.font_value

    def setFont(self, font: _FakeFont) -> None:
        self.font_value = font


class _FakeSignal:
    def __init__(self) -> None:
        self.connected_to = None

    def connect(self, callback) -> None:
        self.connected_to = callback


class _FakeButton:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.minimum_width = None
        self.enabled = None
        self.clicked = _FakeSignal()

    def setMinimumWidth(self, value: int) -> None:
        self.minimum_width = value

    def setEnabled(self, value: bool) -> None:
        self.enabled = value


class _FakeApi:
    def __init__(self, *, connected: bool = True, accounts: list[str] | None = None, send_order_result: object = 0) -> None:
        self.connected = connected
        self.accounts = list(accounts or [])
        self.send_order_result = send_order_result
        self.send_order_calls: list[tuple[object, ...]] = []

    def is_connected(self) -> bool:
        return self.connected

    def account_numbers(self) -> list[str]:
        if not self.connected:
            return []
        return list(self.accounts)

    def send_order(self, *args: object) -> object:
        self.send_order_calls.append(tuple(args))
        if isinstance(self.send_order_result, Exception):
            raise self.send_order_result
        return self.send_order_result


class _FakeAccountCombo:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.enabled = False
        self.current_index = -1
        self.signals_blocked = False

    def blockSignals(self, value: bool) -> None:
        self.signals_blocked = value

    def clear(self) -> None:
        self.items = []
        self.current_index = -1

    def addItems(self, items: list[str]) -> None:
        self.items.extend(items)

    def setEnabled(self, value: bool) -> None:
        self.enabled = value

    def isEnabled(self) -> bool:
        return self.enabled

    def setCurrentIndex(self, value: int) -> None:
        self.current_index = value

    def currentText(self) -> str:
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index]
        return ""


class _FakeTextEdit:
    NoWrap = "NoWrap"
    instances: list["_FakeTextEdit"] = []

    def __init__(self) -> None:
        self.read_only = None
        self.text = ""
        self.font_value = None
        self.minimum_height = None
        self.line_wrap_mode = None
        self.instances.append(self)

    def setReadOnly(self, value: bool) -> None:
        self.read_only = value

    def setPlainText(self, value: str) -> None:
        self.text = value

    def setFont(self, font) -> None:
        self.font_value = font

    def setMinimumHeight(self, value: int) -> None:
        self.minimum_height = value

    def setLineWrapMode(self, value) -> None:
        self.line_wrap_mode = value


class _FakeLayout:
    def __init__(self) -> None:
        self.items = []

    def addWidget(self, widget) -> None:
        self.items.append(widget)

    def addLayout(self, layout) -> None:
        self.items.append(layout)

    def addStretch(self, stretch: int = 0) -> None:
        self.items.append(("stretch", stretch))


class _FakeDialog:
    instances: list["_FakeDialog"] = []

    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.title = ""
        self.size = None
        self.layout = None
        self.exec_called = False
        self.accept_called = False
        self.instances.append(self)

    def setWindowTitle(self, value: str) -> None:
        self.title = value

    def resize(self, width: int, height: int) -> None:
        self.size = (width, height)

    def setLayout(self, layout) -> None:
        self.layout = layout

    def accept(self) -> None:
        self.accept_called = True

    def exec_(self) -> int:
        self.exec_called = True
        return 0


class GuiExecutionPreviewButtonTest(unittest.TestCase):
    def test_startup_recovery_approval_is_bound_to_runtime_snapshot(self) -> None:
        window = main_gui.MainWindow.__new__(main_gui.MainWindow)
        window._startup_recovery_approved = True
        window._startup_recovery_approved_snapshot = "SNAPSHOT_A"
        window._startup_recovery_result = {"snapshot_hash": "SNAPSHOT_A"}

        self.assertTrue(
            main_gui.MainWindow.startup_recovery_session_ready(
                window,
                refresh=False,
            )
        )

        window._startup_recovery_result = {"snapshot_hash": "SNAPSHOT_B"}

        self.assertFalse(
            main_gui.MainWindow.startup_recovery_session_ready(
                window,
                refresh=False,
            )
        )

    def test_startup_recovery_gate_applies_to_initialized_production_parent(self) -> None:
        window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
        parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
        parent._startup_recovery_result = {"snapshot_hash": "SNAPSHOT_A"}
        window.parent = lambda: parent
        window.require_startup_recovery_session = mock.Mock(return_value=False)

        allowed = gui.startup_recovery_action_allowed(window, "Manual SendOrder")

        self.assertFalse(allowed)
        window.require_startup_recovery_session.assert_called_once_with("Manual SendOrder")

    def test_start_button_recomputes_selected_stock_after_recovery_approval(self) -> None:
        class Parent:
            def __init__(self, ready: bool) -> None:
                self.ready = ready

            def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
                return self.ready

        def start_enabled(*, has_stock: bool, ready: bool) -> bool:
            window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
            for name in (
                "btn_start",
                "btn_stop",
                "btn_early_close",
                "btn_set_schedule",
                "btn_delete",
                "btn_order_view",
                "btn_log_view",
                "btn_review_view",
                "btn_execution_enable",
                "btn_real_ready_preflight",
                "btn_execution_preview",
                "btn_manual_send_order",
                "btn_manual_cancel_pending_order",
                "btn_manual_modify_pending_order",
                "btn_manual_queue_commit",
            ):
                setattr(window, name, _FakeButton(name))
            window.has_selected_stock = mock.Mock(return_value=has_stock)
            window.has_single_selected_stock = mock.Mock(return_value=has_stock)
            window.parent = lambda: Parent(ready)
            window._last_execution_preview_result = {}

            gui.AutoTradeSettingWindow.update_action_buttons(window)

            return bool(window.btn_start.enabled)

        self.assertFalse(start_enabled(has_stock=True, ready=False))
        self.assertTrue(start_enabled(has_stock=True, ready=True))
        self.assertFalse(start_enabled(has_stock=False, ready=True))

    def test_review_startup_recovery_refreshes_auto_trade_action_buttons(self) -> None:
        class StatusBar:
            def __init__(self) -> None:
                self.messages: list[str] = []

            def showMessage(self, message: str) -> None:
                self.messages.append(message)

        setting_window = mock.Mock()
        main = main_gui.MainWindow.__new__(main_gui.MainWindow)
        main._startup_recovery_approved = False
        main._startup_recovery_approved_snapshot = ""
        main._startup_recovery_result = {}
        main.auto_trade_setting_window = setting_window
        main.refresh_startup_recovery_status = mock.Mock(
            return_value={
                "status": "RESUME_READY",
                "operator_approval_allowed": True,
                "snapshot_hash": "SNAPSHOT_A",
            }
        )
        status_bar = StatusBar()
        main.statusBar = lambda: status_bar

        with mock.patch.object(main_gui.QMessageBox, "Yes", 1, create=True), mock.patch.object(
            main_gui.QMessageBox,
            "No",
            0,
            create=True,
        ), mock.patch.object(main_gui.QMessageBox, "question", return_value=1, create=True):
            main_gui.MainWindow.review_startup_recovery(main)

        self.assertTrue(main._startup_recovery_approved)
        self.assertEqual("SNAPSHOT_A", main._startup_recovery_approved_snapshot)
        setting_window.update_action_buttons.assert_called_once_with()
        setting_window.update_startup_recovery_controls.assert_not_called()

    def _window_for_queue_commit(self):
        window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
        window.messages = []
        window.commit_reports = []
        window.btn_manual_queue_commit = _FakeButton("수동 Queue 저장")
        window._last_execution_preview_result = None
        window.statusBarMessage = lambda message, timeout_ms=5000: window.messages.append(message)
        window.show_manual_queue_commit_result = lambda result: window.commit_reports.append(result)
        parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
        parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"])
        parent.account_combo = _FakeAccountCombo()
        main_gui.MainWindow.refresh_kiwoom_accounts(parent)
        window.parent = lambda: parent
        window.real_preflight_stock_config_for_order = lambda order: ({"real_trade_enabled": True}, "test_config")
        window.read_order_from_queue_by_id = lambda order_id, queue_path: {
            "ok": True,
            "order": {"id": str(order_id), "code": "005930"},
            "blocked_reasons": [],
        }
        window.confirm_execution_runtime_commit = lambda order, guard, **kwargs: True
        window.commit_execution_runtime_for_preview = lambda order, guard, result, **kwargs: {
            "runtime_commit_ready": True,
            "runtime_commit_stage": "runtime_committed",
            "runtime_commit_result": self._runtime_commit_result(),
            "runtime_dry_run_result": {"status": "READY"},
            "commit_plan_orchestrator_result": {"status": "READY", "commit_ready": True},
            "runtime_commit_readiness_policy_result": {"status": "READY_TO_OPEN_RUNTIME_COMMIT"},
            "blocked_reasons": [],
        }
        return window

    def _window_for_execution_enable(self):
        window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
        window.messages = []
        window.enable_reports = []
        window.statusBarMessage = lambda message, timeout_ms=5000: window.messages.append(message)
        window.show_execution_enable_result = lambda result: window.enable_reports.append(result)
        return window

    def _window_for_real_preflight(self):
        window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
        window.messages = []
        window.real_preflight_reports = []
        window.statusBarMessage = lambda message, timeout_ms=5000: window.messages.append(message)
        window.show_real_preflight_result = lambda result: window.real_preflight_reports.append(result)
        parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
        parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"])
        parent.account_combo = _FakeAccountCombo()
        main_gui.MainWindow.refresh_kiwoom_accounts(parent)
        window.parent = lambda: parent
        window.real_preflight_stock_config_for_order = lambda order: ({"real_trade_enabled": True}, "test_config")
        return window

    def _queue_write_preview_result(self) -> dict[str, object]:
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": {
                "id": "ORDER_QUEUED_ORDER_1",
                "status": "ORDER_QUEUED",
                "source_signal_id": "SIG_1",
                "order_id": "ORDER_1",
                "candidate_id": "EXEC_CANDIDATE_ORDER_1",
                "queue_pending_id": "QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1",
                "request_hash": "HASH_1",
                "lock_id": "LOCK_1",
                "execution_id": "EXEC_1",
            },
        }

    def _runtime_commit_result(self) -> dict[str, object]:
        return {
            "status": "COMMITTED",
            "committed": True,
            "runtime_write": True,
            "read_back_verified": True,
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
        }

    def _order_queued_record_for_send_order(self, *, side: str = "BUY") -> dict[str, object]:
        return {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "CANDIDATE_1",
            "queue_pending_id": "PENDING_1",
            "execution_id": "EXEC_1",
            "request_hash": "a" * 64,
            "lock_id": "LOCK_1",
            "queue_contract_version": "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
            "account_no": "12345678",
            "code": "003550",
            "side": side,
            "quantity": 10,
            "price": 1000,
            "order_type": "LIMIT",
            "execution_request": {
                "execution_id": "EXEC_1",
                "order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
                "lock_id": "LOCK_1",
                "request_hash": "a" * 64,
                "guard_snapshot": {"account_no": "12345678"},
                "request_preview": {
                    "account_no": "12345678",
                    "screen_no": "0101",
                    "side": side,
                    "code": "003550",
                    "quantity": 10,
                    "price": 1000,
                    "hoga": "LIMIT",
                    "original_order_no": "",
                },
            },
        }

    def _write_queue_for_send_order(self, queue_path, record: dict[str, object]) -> None:
        queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "revision": 0,
                    "updated_at": "before",
                    "orders": [record],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _write_open_position(self, positions_path, *, quantity: int = 10, average_price: int = 1000) -> None:
        positions_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": "before",
                    "positions": [
                        {
                            "position_id": "POSITION_KIWOOM_12345678_003550",
                            "broker": "KIWOOM",
                            "account_no": "12345678",
                            "code": "003550",
                            "side": "LONG",
                            "quantity": quantity,
                            "average_price": average_price,
                            "cost_basis": quantity * average_price,
                            "position_status": "OPEN",
                            "last_fill_id": None,
                            "last_fill_at": None,
                            "applied_fill_ids": [],
                            "applied_fill_identities": [],
                            "last_applied_cumulative_by_order": {},
                            "updated_at": "before",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _runtime_environment_flags(self, *args, **kwargs) -> dict[str, object]:
        return {
            "real_runtime_file_init_enabled": True,
            "allow_project_runtime_file_init": True,
            "real_runtime_commit_enabled": True,
            "allow_project_runtime_commit": True,
            "source": "test_runtime_environment_source",
            "issues": [],
        }

    def test_runtime_environment_flags_use_real_guard_and_canonical_paths(self) -> None:
        window = self._window_for_queue_commit()
        order = {"id": "ORDER_1"}
        guard = {
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "real_trade_enabled": True,
        }

        flags = gui.AutoTradeSettingWindow.execution_runtime_environment_flags(
            window,
            order,
            guard,
            order_executions_path=gui.ORDER_EXECUTIONS_PATH,
            order_locks_path=gui.ORDER_LOCKS_PATH,
        )

        self.assertTrue(flags["real_runtime_file_init_enabled"])
        self.assertTrue(flags["allow_project_runtime_file_init"])
        self.assertTrue(flags["real_runtime_commit_enabled"])
        self.assertTrue(flags["allow_project_runtime_commit"])
        self.assertEqual([], flags["issues"])

    def test_runtime_environment_flags_fail_closed_for_noncanonical_or_invalid_guard(self) -> None:
        window = self._window_for_queue_commit()
        order = {"id": "ORDER_1"}
        guard = {
            "kiwoom_logged_in": False,
            "account_selected": True,
            "account_no": "12345678",
            "real_trade_enabled": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            flags = gui.AutoTradeSettingWindow.execution_runtime_environment_flags(
                window,
                order,
                guard,
                order_executions_path=gui.Path(temp_dir) / "order_executions.json",
                order_locks_path=gui.Path(temp_dir) / "order_locks.json",
            )

        self.assertFalse(flags["real_runtime_file_init_enabled"])
        self.assertFalse(flags["allow_project_runtime_file_init"])
        self.assertFalse(flags["real_runtime_commit_enabled"])
        self.assertFalse(flags["allow_project_runtime_commit"])
        self.assertIn("kiwoom api is not connected", flags["issues"])
        self.assertIn("runtime target is not the canonical project runtime path", flags["issues"])

    def _queue_snapshot(self, sha256: str = "HASH_BEFORE") -> dict[str, object]:
        return {
            "path": str(gui.ORDER_QUEUE_PATH),
            "sha256": sha256,
            "size": 1071,
            "mtime": "2026-07-03 10:08:43",
            "orders_count": 1,
            "error": None,
        }

    def _executable_order(self) -> dict[str, object]:
        return {
            "id": "ORDER_EXEC_1",
            "status": "EXECUTABLE",
            "execution_enabled": False,
            "quantity": 10,
            "side": "BUY",
            "order_type": "LIMIT",
            "code": "005930",
            "source_signal_id": "SIG_EXEC_1",
            "approval_status": "APPROVED",
            "policy_status": "EXECUTABLE",
        }

    def _real_preflight_order(self) -> dict[str, object]:
        order = self._executable_order()
        order["execution_enabled"] = True
        return order

    def _real_preflight_guard(self) -> dict[str, object]:
        return {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }

    def _enable_preview_result(self) -> dict[str, object]:
        return {
            "enable_preview": True,
            "enable_stage": "execution_enable_preview_created",
            "next_stage": "EXECUTION_ENABLE_COMMIT_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "warnings": [],
            "order_id": "ORDER_EXEC_1",
            "source_signal_id": "SIG_EXEC_1",
            "code": "005930",
            "side": "BUY",
            "quantity": 10,
            "order_type": "LIMIT",
        }

    def _real_preflight_preview_result(self) -> dict[str, object]:
        return {
            "real_preflight_preview": True,
            "preflight_stage": "real_preflight_preview_created",
            "next_stage": "REAL_PREFLIGHT_COMMIT_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "order_id": "ORDER_EXEC_1",
            "source_signal_id": "SIG_EXEC_1",
            "code": "005930",
            "side": "BUY",
            "quantity": 10,
            "order_type": "LIMIT",
            "blocked_reasons": [],
            "warnings": [],
            "send_order_called": False,
        }

    def test_main_window_refresh_accounts_auto_selects_single_real_account(self) -> None:
        window = main_gui.MainWindow.__new__(main_gui.MainWindow)
        window.kiwoom_api = _FakeApi(connected=True, accounts=["12345678", "", "12345678"])
        window.account_combo = _FakeAccountCombo()

        accounts = main_gui.MainWindow.refresh_kiwoom_accounts(window)

        self.assertEqual(["12345678"], accounts)
        self.assertEqual("12345678", main_gui.MainWindow.selected_account_no(window))
        self.assertTrue(window.account_combo.enabled)

    def test_main_window_refresh_accounts_preserves_valid_multi_account_selection(self) -> None:
        window = main_gui.MainWindow.__new__(main_gui.MainWindow)
        window.kiwoom_api = _FakeApi(connected=True, accounts=["11111111", "22222222"])
        window.account_combo = _FakeAccountCombo()
        main_gui.MainWindow.refresh_kiwoom_accounts(window)
        window.account_combo.setCurrentIndex(1)

        accounts = main_gui.MainWindow.refresh_kiwoom_accounts(window)

        self.assertEqual(["11111111", "22222222"], accounts)
        self.assertEqual("22222222", main_gui.MainWindow.selected_account_no(window))

    def test_main_window_refresh_accounts_clears_disconnected_selection(self) -> None:
        window = main_gui.MainWindow.__new__(main_gui.MainWindow)
        window.kiwoom_api = _FakeApi(connected=False, accounts=["12345678"])
        window.account_combo = _FakeAccountCombo()

        accounts = main_gui.MainWindow.refresh_kiwoom_accounts(window)

        self.assertEqual([], accounts)
        self.assertEqual("", main_gui.MainWindow.selected_account_no(window))
        self.assertFalse(window.account_combo.enabled)

    def test_execution_enable_button_is_registered_separately(self) -> None:
        module_text = gui.__loader__.get_source(gui.__name__)

        self.assertIn('btn_execution_enable = QPushButton("수동 실주문 후보 활성화")', module_text)
        self.assertIn(
            "btn_execution_enable.clicked.connect(self.enable_execution_candidate_manually)",
            module_text,
        )
        self.assertIn(
            "btn_manual_queue_commit.clicked.connect(self.commit_last_execution_preview_queue_manually)",
            module_text,
        )

    def test_execution_enable_order_id_cancel_does_not_commit(self) -> None:
        window = self._window_for_execution_enable()

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("", False)),
            mock.patch.object(gui, "commit_execution_enable") as commit_service,
            mock.patch.object(gui, "QTimer") as qtimer,
        ):
            gui.AutoTradeSettingWindow.enable_execution_candidate_manually(window)

        commit_service.assert_not_called()
        qtimer.assert_not_called()
        self.assertEqual([], window.enable_reports)

    def test_execution_enable_non_executable_order_is_blocked_before_commit(self) -> None:
        window = self._window_for_execution_enable()
        order = self._executable_order()
        order["status"] = "APPROVED"

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": order, "blocked_reasons": []},
            ),
            mock.patch.object(gui, "commit_execution_enable") as commit_service,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.enable_execution_candidate_manually(window)

        commit_service.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual("status", window.enable_reports[0]["enable_stage"])
        self.assertEqual("BLOCKED", window.enable_reports[0]["next_stage"])

    def test_execution_enable_preview_failure_does_not_commit(self) -> None:
        window = self._window_for_execution_enable()
        preview_result = {
            "enable_preview": False,
            "enable_stage": "operator_confirmation",
            "next_stage": "BLOCKED",
            "blocked_reasons": ["operator confirmation is required"],
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._executable_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "preview_execution_enable", return_value=preview_result) as preview_service,
            mock.patch.object(gui, "commit_execution_enable") as commit_service,
        ):
            gui.AutoTradeSettingWindow.enable_execution_candidate_manually(window)

        preview_service.assert_called_once_with(
            self._executable_order(),
            {"operator_confirmed_for_execution_enable": True},
        )
        commit_service.assert_not_called()
        self.assertEqual([preview_result], [window._last_execution_enable_preview_result])
        self.assertEqual("operator_confirmation", window.enable_reports[0]["enable_stage"])

    def test_execution_enable_confirmation_cancel_does_not_commit(self) -> None:
        window = self._window_for_execution_enable()
        window.confirm_execution_enable_commit = lambda order, preview, queue_path, snapshot: False

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._executable_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "preview_execution_enable", return_value=self._enable_preview_result()),
            mock.patch.object(gui, "commit_execution_enable") as commit_service,
        ):
            gui.AutoTradeSettingWindow.enable_execution_candidate_manually(window)

        commit_service.assert_not_called()
        self.assertTrue(any("취소" in message for message in window.messages))

    def test_execution_enable_confirmed_passes_manual_commit_context(self) -> None:
        window = self._window_for_execution_enable()
        window.confirm_execution_enable_commit = lambda order, preview, queue_path, snapshot: True
        preview_result = self._enable_preview_result()
        commit_result = {
            "enabled": True,
            "enable_stage": "execution_enabled_committed",
            "next_stage": "REAL_PREFLIGHT_REQUIRED",
            "changed": True,
            "order_id": "ORDER_EXEC_1",
            "before_status": "EXECUTABLE",
            "after_status": "EXECUTABLE",
            "before_execution_enabled": False,
            "after_execution_enabled": True,
            "before_sha256": "HASH_BEFORE",
            "after_sha256": "HASH_AFTER",
            "backup_path": "queue.bak",
            "blocked_reasons": [],
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                side_effect=[self._queue_snapshot(), self._queue_snapshot()],
            ),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._executable_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "preview_execution_enable", return_value=preview_result),
            mock.patch.object(gui, "commit_execution_enable", return_value=commit_result) as commit_service,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.enable_execution_candidate_manually(window)

        commit_service.assert_called_once_with(
            preview_result,
            gui.ORDER_QUEUE_PATH,
            preview_queue_snapshot=self._queue_snapshot(),
            context={"manual_execution_enable_commit_confirmed": True},
        )
        self.assertEqual([commit_result], window.enable_reports)
        self.assertEqual("REAL_PREFLIGHT_REQUIRED", window.enable_reports[0]["next_stage"])
        self.assertEqual("EXECUTABLE", window.enable_reports[0]["after_status"])
        send_order_stub.assert_not_called()

    def test_execution_enable_stale_snapshot_blocks_without_commit(self) -> None:
        window = self._window_for_execution_enable()
        window.confirm_execution_enable_commit = lambda order, preview, queue_path, snapshot: True

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                side_effect=[self._queue_snapshot("HASH_OLD"), self._queue_snapshot("HASH_NEW")],
            ),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._executable_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "preview_execution_enable", return_value=self._enable_preview_result()),
            mock.patch.object(gui, "commit_execution_enable") as commit_service,
        ):
            gui.AutoTradeSettingWindow.enable_execution_candidate_manually(window)

        commit_service.assert_not_called()
        self.assertEqual("stale_preview", window.enable_reports[0]["enable_stage"])
        self.assertIn(
            "queue file changed after execution enable preview; rerun preview",
            window.enable_reports[0]["blocked_reasons"],
        )

    def test_execution_enable_result_dialog_contains_safety_and_status_fields(self) -> None:
        _FakeDialog.instances = []
        _FakeTextEdit.instances = []
        window = object()
        result = {
            "enabled": True,
            "enable_stage": "execution_enabled_committed",
            "next_stage": "REAL_PREFLIGHT_REQUIRED",
            "changed": True,
            "before_status": "EXECUTABLE",
            "after_status": "EXECUTABLE",
            "before_execution_enabled": False,
            "after_execution_enabled": True,
            "before_sha256": "HASH_BEFORE",
            "after_sha256": "HASH_AFTER",
            "backup_path": "queue.bak",
            "blocked_reasons": [],
        }

        with (
            mock.patch.object(gui, "QDialog", _FakeDialog),
            mock.patch.object(gui, "QVBoxLayout", _FakeLayout),
            mock.patch.object(gui, "QHBoxLayout", _FakeLayout),
            mock.patch.object(gui, "QTextEdit", _FakeTextEdit),
            mock.patch.object(gui, "QPushButton", _FakeButton),
            mock.patch.object(gui, "QFont", _FakeMonoFont),
        ):
            gui.AutoTradeSettingWindow.show_execution_enable_result(window, result)

        text = _FakeTextEdit.instances[0].text
        self.assertIn("enabled: True", text)
        self.assertIn("next_stage: REAL_PREFLIGHT_REQUIRED", text)
        self.assertIn("after_status: EXECUTABLE", text)
        self.assertIn("SendOrder called: False", text)
        self.assertIn("real_order_preflight auto-called: False", text)

    def test_real_ready_preflight_button_is_registered_between_enable_and_preview(self) -> None:
        module_text = gui.__loader__.get_source(gui.__name__)

        self.assertIn('btn_real_ready_preflight = QPushButton("REAL_READY 수동 점검")', module_text)
        self.assertIn(
            "btn_real_ready_preflight.clicked.connect(self.run_real_ready_preflight_manually)",
            module_text,
        )
        self.assertLess(
            module_text.index("selected_routine_header_layout.addWidget(self.btn_execution_enable)"),
            module_text.index("selected_routine_header_layout.addWidget(self.btn_real_ready_preflight)"),
        )
        self.assertLess(
            module_text.index("selected_routine_header_layout.addWidget(self.btn_real_ready_preflight)"),
            module_text.index("selected_routine_header_layout.addWidget(self.btn_execution_preview)"),
        )

    def test_real_preflight_order_id_cancel_does_not_commit(self) -> None:
        window = self._window_for_real_preflight()

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("", False)),
            mock.patch.object(gui, "commit_real_order_preflight") as commit_service,
            mock.patch.object(gui, "QTimer") as qtimer,
        ):
            gui.AutoTradeSettingWindow.run_real_ready_preflight_manually(window)

        commit_service.assert_not_called()
        qtimer.assert_not_called()
        self.assertEqual([], window.real_preflight_reports)

    def test_real_preflight_disconnected_login_is_blocked(self) -> None:
        window = self._window_for_real_preflight()
        window.parent().kiwoom_api.connected = False

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._real_preflight_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "commit_real_order_preflight") as commit_service,
        ):
            gui.AutoTradeSettingWindow.run_real_ready_preflight_manually(window)

        commit_service.assert_not_called()
        self.assertEqual("guard", window.real_preflight_reports[0]["preflight_stage"])
        self.assertEqual("BLOCKED", window.real_preflight_reports[0]["next_stage"])
        self.assertIn("kiwoom api is not connected", window.real_preflight_reports[0]["blocked_reasons"])

    def test_real_preflight_preview_failure_does_not_commit(self) -> None:
        window = self._window_for_real_preflight()
        window.confirm_real_preflight_commit = lambda order, guard, preview, queue_path, snapshot: True
        preview_result = {
            "real_preflight_preview": False,
            "preflight_stage": "execution_enabled",
            "next_stage": "BLOCKED",
            "blocked_reasons": ["order.execution_enabled must be true"],
            "send_order_called": False,
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._real_preflight_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "preview_real_order_preflight", return_value=preview_result) as preview_service,
            mock.patch.object(gui, "commit_real_order_preflight") as commit_service,
        ):
            gui.AutoTradeSettingWindow.run_real_ready_preflight_manually(window)

        expected_guard = self._real_preflight_guard()
        expected_guard.update(
            {
                "account_numbers": ["12345678"],
                "selected_account_valid": True,
                "real_trade_source": "test_config",
                "real_trade_config_found": True,
                "real_trade_guard_source": "gui_session",
            }
        )
        preview_service.assert_called_once_with(
            self._real_preflight_order(),
            expected_guard,
            {"manual_real_preflight_confirmed": True},
        )
        commit_service.assert_not_called()
        self.assertEqual(preview_result, window._last_real_preflight_preview_result)
        self.assertEqual("execution_enabled", window.real_preflight_reports[0]["preflight_stage"])

    def test_real_preflight_confirmation_cancel_does_not_commit(self) -> None:
        window = self._window_for_real_preflight()
        window.confirm_real_preflight_commit = lambda order, guard, preview, queue_path, snapshot: False

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._real_preflight_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "read_json_dict", return_value=self._real_preflight_guard()),
            mock.patch.object(gui, "preview_real_order_preflight", return_value=self._real_preflight_preview_result()),
            mock.patch.object(gui, "commit_real_order_preflight") as commit_service,
        ):
            gui.AutoTradeSettingWindow.run_real_ready_preflight_manually(window)

        commit_service.assert_not_called()
        self.assertTrue(window.messages)
        self.assertEqual([], window.real_preflight_reports)

    def test_real_preflight_confirmed_passes_manual_commit_context(self) -> None:
        window = self._window_for_real_preflight()
        window.confirm_real_preflight_commit = lambda order, guard, preview, queue_path, snapshot: True
        preview_result = self._real_preflight_preview_result()
        commit_result = {
            "real_preflight_committed": True,
            "preflight_stage": "real_ready_committed",
            "next_stage": "EXECUTION_PREVIEW_REQUIRED",
            "changed": True,
            "order_id": "ORDER_EXEC_1",
            "before_status": "EXECUTABLE",
            "after_status": "REAL_READY",
            "execution_enabled": True,
            "real_preflight_status": "REAL_READY",
            "before_sha256": "HASH_BEFORE",
            "after_sha256": "HASH_AFTER",
            "backup_path": "queue.bak",
            "send_order_called": False,
            "blocked_reasons": [],
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                side_effect=[self._queue_snapshot(), self._queue_snapshot()],
            ),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._real_preflight_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "preview_real_order_preflight", return_value=preview_result),
            mock.patch.object(gui, "commit_real_order_preflight", return_value=commit_result) as commit_service,
            mock.patch.object(gui, "preview_execution_for_real_ready_order") as execution_preview,
            mock.patch.object(gui, "commit_execution_queue_manually") as queue_commit,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.run_real_ready_preflight_manually(window)

        commit_service.assert_called_once_with(
            preview_result,
            gui.ORDER_QUEUE_PATH,
            guard_path=None,
            preview_queue_snapshot=self._queue_snapshot(),
            context={"manual_real_preflight_commit_confirmed": True},
        )
        self.assertEqual([commit_result], window.real_preflight_reports)
        self.assertEqual("REAL_READY", window.real_preflight_reports[0]["after_status"])
        self.assertEqual("EXECUTION_PREVIEW_REQUIRED", window.real_preflight_reports[0]["next_stage"])
        self.assertFalse(window.real_preflight_reports[0]["send_order_called"])
        execution_preview.assert_not_called()
        queue_commit.assert_not_called()
        send_order_stub.assert_not_called()

    def test_real_preflight_stale_snapshot_blocks_without_commit(self) -> None:
        window = self._window_for_real_preflight()
        window.confirm_real_preflight_commit = lambda order, guard, preview, queue_path, snapshot: True

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_EXEC_1", True)),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                side_effect=[self._queue_snapshot("HASH_OLD"), self._queue_snapshot("HASH_NEW")],
            ),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "read_order_from_queue_by_id",
                return_value={"ok": True, "order": self._real_preflight_order(), "blocked_reasons": []},
            ),
            mock.patch.object(gui, "read_json_dict", return_value=self._real_preflight_guard()),
            mock.patch.object(gui, "preview_real_order_preflight", return_value=self._real_preflight_preview_result()),
            mock.patch.object(gui, "commit_real_order_preflight") as commit_service,
        ):
            gui.AutoTradeSettingWindow.run_real_ready_preflight_manually(window)

        commit_service.assert_not_called()
        self.assertEqual("stale_preview", window.real_preflight_reports[0]["preflight_stage"])
        self.assertIn(
            "queue file changed after real preflight preview; rerun REAL Preflight",
            window.real_preflight_reports[0]["blocked_reasons"],
        )

    def test_real_preflight_result_dialog_contains_safety_and_status_fields(self) -> None:
        _FakeDialog.instances = []
        _FakeTextEdit.instances = []
        window = object()
        result = {
            "real_preflight_committed": True,
            "preflight_stage": "real_ready_committed",
            "next_stage": "EXECUTION_PREVIEW_REQUIRED",
            "changed": True,
            "before_status": "EXECUTABLE",
            "after_status": "REAL_READY",
            "execution_enabled": True,
            "real_preflight_status": "REAL_READY",
            "before_sha256": "HASH_BEFORE",
            "after_sha256": "HASH_AFTER",
            "backup_path": "queue.bak",
            "send_order_called": False,
            "blocked_reasons": [],
        }

        with (
            mock.patch.object(gui, "QDialog", _FakeDialog),
            mock.patch.object(gui, "QVBoxLayout", _FakeLayout),
            mock.patch.object(gui, "QHBoxLayout", _FakeLayout),
            mock.patch.object(gui, "QTextEdit", _FakeTextEdit),
            mock.patch.object(gui, "QPushButton", _FakeButton),
            mock.patch.object(gui, "QFont", _FakeMonoFont),
        ):
            gui.AutoTradeSettingWindow.show_real_preflight_result(window, result)

        text = _FakeTextEdit.instances[0].text
        self.assertIn("real_preflight_committed: True", text)
        self.assertIn("next_stage: EXECUTION_PREVIEW_REQUIRED", text)
        self.assertIn("after_status: REAL_READY", text)
        self.assertIn("send_order_called: False", text)
        self.assertIn("Execution Preview auto-called: False", text)

    def test_real_preflight_confirmation_text_contains_required_fields(self) -> None:
        window = self._window_for_real_preflight()
        text = gui.AutoTradeSettingWindow.real_preflight_confirmation_text(
            window,
            self._real_preflight_order(),
            self._real_preflight_guard(),
            self._real_preflight_preview_result(),
            gui.ORDER_QUEUE_PATH,
            self._queue_snapshot(),
        )

        self.assertIn("REAL_READY 수동 점검 확인", text)
        self.assertIn("SendOrder 호출이 아닙니다.", text)
        self.assertIn("Execution Preview는 자동 실행되지 않습니다.", text)
        self.assertIn("Queue 저장이 아닙니다.", text)
        self.assertIn("order_id: ORDER_EXEC_1", text)
        self.assertIn("guard.real_trade_enabled: True", text)
        self.assertIn("before_sha256: HASH_BEFORE", text)

    def test_real_ready_order_id_runs_read_only_preview_and_reporter(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        guard = {"operator_confirmed": True, "real_trade_enabled": True}
        queue_write_preview = self._queue_write_preview_result()
        preview_result = {
            "ok": True,
            "read_result": {"ok": True, "order": {"id": "ORDER_1"}},
            "preview_result": {
                "summary": {"ok": True},
                "queue_write_preview_result": queue_write_preview,
            },
        }
        report = {
            "ok": True,
            "order_id": "ORDER_1",
            "text": (
                "Execution Preview Report: OK\n"
                "[Approval]\napproved: True\n"
                "[Candidate]\ncandidate: True\n"
                "[Queue Pending]\nqueue_pending: True\n"
                "[Queue Writer Dry-Run]\nwrite_preview: True"
            ),
        }
        queue_snapshot = self._queue_snapshot()

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=(" ORDER_1 ", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value=preview_result) as service,
            mock.patch.object(gui, "build_execution_preview_report", return_value=report) as reporter,
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=queue_snapshot),
            mock.patch.object(gui, "commit_execution_queue_manually") as commit_service,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        service.assert_called_once()
        self.assertEqual("ORDER_1", service.call_args.args[0])
        service_guard = service.call_args.args[1]
        self.assertEqual("gui_session", service_guard["real_trade_guard_source"])
        self.assertEqual("12345678", service_guard["account_no"])
        reporter.assert_called_once_with(preview_result)
        self.assertEqual([report], window.reports)
        self.assertIn("[Approval]", window.reports[0]["text"])
        self.assertIn("[Candidate]", window.reports[0]["text"])
        self.assertIn("[Queue Pending]", window.reports[0]["text"])
        self.assertIn("[Queue Writer Dry-Run]", window.reports[0]["text"])
        self.assertTrue(any("Execution Preview" in message for message in window.messages))
        self.assertEqual(preview_result, window._last_execution_preview_result)
        self.assertEqual(queue_snapshot, window._last_execution_preview_queue_snapshot)
        self.assertTrue(window.btn_manual_queue_commit.enabled)
        commit_service.assert_not_called()
        write_text.assert_not_called()
        send_order_stub.assert_not_called()

    def test_execution_preview_confirmation_cancel_stops_before_preview_runtime_and_queue(self) -> None:
        window = self._window_for_queue_commit()
        window.confirm_execution_runtime_commit = lambda order, guard, **kwargs: False
        window.commit_execution_runtime_for_preview = mock.Mock()

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_1", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order") as service,
            mock.patch.object(gui, "commit_execution_queue_manually") as commit_service,
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        service.assert_not_called()
        window.commit_execution_runtime_for_preview.assert_not_called()
        commit_service.assert_not_called()
        self.assertTrue(any("cancelled" in message for message in window.messages))

    def test_execution_preview_confirmation_is_only_source_of_operator_confirmed(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        guards: list[dict[str, object]] = []

        def capture_confirmation(order, guard, **kwargs):
            guards.append(dict(guard))
            return True

        window.confirm_execution_runtime_commit = capture_confirmation
        preview_result = {
            "ok": True,
            "read_result": {"ok": True, "order": {"id": "ORDER_1"}},
            "preview_result": {"queue_write_preview_result": self._queue_write_preview_result()},
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_1", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value=preview_result) as service,
            mock.patch.object(gui, "build_execution_preview_report", return_value={"ok": True, "text": "ok"}),
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        self.assertEqual(False, guards[0]["operator_confirmed"])
        self.assertEqual(True, service.call_args.args[1]["operator_confirmed"])
        self.assertEqual("COMMITTED", window._last_execution_preview_result["runtime_commit_result"]["status"])

    def test_runtime_commit_helper_writes_existing_runtime_files_and_returns_identity(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        order = {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {"side": "BUY", "hoga": "MARKET"},
        }
        guard = {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"
            executions_path.write_text(
                json.dumps(default_order_executions_data(), ensure_ascii=False),
                encoding="utf-8",
            )
            locks_path.write_text(
                json.dumps(default_order_locks_data(), ensure_ascii=False),
                encoding="utf-8",
            )

            result = gui.AutoTradeSettingWindow.commit_execution_runtime_for_preview(
                window,
                order,
                guard,
                {"ok": True},
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            runtime_commit = result["runtime_commit_result"]
            self.assertTrue(result["runtime_commit_ready"])
            self.assertEqual("COMMITTED", runtime_commit["status"])
            self.assertTrue(runtime_commit["committed"])
            self.assertTrue(runtime_commit["runtime_write"])
            self.assertTrue(runtime_commit["read_back_verified"])
            self.assertEqual("ORDER_1", runtime_commit["order_id"])
            self.assertTrue(runtime_commit["execution_id"])
            self.assertTrue(runtime_commit["request_hash"])
            self.assertTrue(runtime_commit["lock_id"])
            self.assertEqual(1, len(json.loads(executions_path.read_text(encoding="utf-8"))["executions"]))
            self.assertEqual(1, len(json.loads(locks_path.read_text(encoding="utf-8"))["locks"]))

    def test_runtime_commit_helper_initializes_missing_runtime_files_after_confirmation(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        window.confirm_execution_runtime_file_init = lambda **kwargs: True
        order = {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {"side": "BUY", "hoga": "MARKET"},
        }
        guard = {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"

            result = gui.AutoTradeSettingWindow.commit_execution_runtime_for_preview(
                window,
                order,
                guard,
                {"ok": True},
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertTrue(result["runtime_commit_ready"])
            self.assertTrue(result["runtime_file_init"]["runtime_file_init_required"])
            self.assertEqual("COMMITTED", result["runtime_file_init"]["runtime_file_init_result"]["status"])
            self.assertEqual("COMMITTED", result["runtime_commit_result"]["status"])
            self.assertTrue(executions_path.exists())
            self.assertTrue(locks_path.exists())
            self.assertEqual(1, len(json.loads(executions_path.read_text(encoding="utf-8"))["executions"]))

    def test_runtime_file_init_cancel_blocks_without_creating_files(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        window.confirm_execution_runtime_file_init = lambda **kwargs: False
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"

            result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
                window,
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertFalse(result["runtime_files_ready"])
            self.assertIn("runtime file initialization cancelled by operator", result["blocked_reasons"])
            self.assertFalse(executions_path.exists())
            self.assertFalse(locks_path.exists())

    def test_runtime_file_init_environment_missing_blocks_without_creating_files(self) -> None:
        window = self._window_for_queue_commit()
        window.confirm_execution_runtime_file_init = lambda **kwargs: True
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"

            result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
                window,
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertFalse(result["runtime_files_ready"])
            self.assertFalse(executions_path.exists())
            self.assertFalse(locks_path.exists())
            self.assertIn("REAL_RUNTIME_FILE_INIT_DISABLED", result["blocked_reasons"])

    def test_project_runtime_file_init_requires_environment_source_before_dialog(self) -> None:
        window = self._window_for_queue_commit()
        window.confirm_execution_runtime_file_init = mock.Mock(return_value=True)

        result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
            window,
            order_executions_path=gui.ORDER_EXECUTIONS_PATH,
            order_locks_path=gui.ORDER_LOCKS_PATH,
        )

        self.assertFalse(result["runtime_files_ready"])
        self.assertIn("PROJECT_RUNTIME_PATH_NOT_ALLOWED", result["blocked_reasons"])
        window.confirm_execution_runtime_file_init.assert_not_called()
        self.assertFalse(gui.ORDER_EXECUTIONS_PATH.exists())
        self.assertFalse(gui.ORDER_LOCKS_PATH.exists())

    def test_partial_runtime_files_create_only_missing_file_without_overwrite(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        window.confirm_execution_runtime_file_init = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"
            existing = default_order_executions_data()
            existing["updated_at"] = "existing"
            executions_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

            result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
                window,
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertTrue(result["runtime_files_ready"], result)
            self.assertTrue(result["runtime_file_init_required"])
            self.assertEqual("COMMITTED", result["runtime_file_init_result"]["status"])
            self.assertTrue(executions_path.exists())
            self.assertTrue(locks_path.exists())
            self.assertEqual(existing, json.loads(executions_path.read_text(encoding="utf-8")))
            self.assertEqual(default_order_locks_data(), json.loads(locks_path.read_text(encoding="utf-8")))
            window.confirm_execution_runtime_file_init.assert_called_once()

    def test_partial_runtime_files_create_missing_executions_without_overwriting_locks(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        window.confirm_execution_runtime_file_init = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"
            existing = default_order_locks_data()
            existing["updated_at"] = "existing"
            locks_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

            result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
                window,
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertTrue(result["runtime_files_ready"], result)
            self.assertTrue(executions_path.exists())
            self.assertTrue(locks_path.exists())
            self.assertEqual(default_order_executions_data(), json.loads(executions_path.read_text(encoding="utf-8")))
            self.assertEqual(existing, json.loads(locks_path.read_text(encoding="utf-8")))
            window.confirm_execution_runtime_file_init.assert_called_once()

    def test_partial_runtime_files_block_when_existing_file_is_invalid(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        window.confirm_execution_runtime_file_init = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"
            executions_path.write_text("not-json", encoding="utf-8")

            result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
                window,
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertFalse(result["runtime_files_ready"])
            self.assertEqual("not-json", executions_path.read_text(encoding="utf-8"))
            self.assertFalse(locks_path.exists())
            self.assertTrue(result["blocked_reasons"])

    def test_invalid_existing_runtime_files_block_without_overwrite(self) -> None:
        window = self._window_for_queue_commit()
        window.execution_runtime_environment_flags = self._runtime_environment_flags
        window.confirm_execution_runtime_file_init = mock.Mock(return_value=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"
            executions_path.write_text("not-json", encoding="utf-8")
            locks_path.write_text(
                json.dumps(default_order_locks_data(), ensure_ascii=False),
                encoding="utf-8",
            )

            result = gui.AutoTradeSettingWindow.ensure_execution_runtime_files_ready(
                window,
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertFalse(result["runtime_files_ready"])
            self.assertTrue(result["blocked_reasons"])
            self.assertEqual("not-json", executions_path.read_text(encoding="utf-8"))
            window.confirm_execution_runtime_file_init.assert_not_called()

    def test_runtime_commit_environment_missing_blocks_after_valid_existing_runtime_files(self) -> None:
        window = self._window_for_queue_commit()
        order = {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {"side": "BUY", "hoga": "MARKET"},
        }
        guard = {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            executions_path = gui.Path(temp_dir) / "order_executions.json"
            locks_path = gui.Path(temp_dir) / "order_locks.json"
            executions_path.write_text(
                json.dumps(default_order_executions_data(), ensure_ascii=False),
                encoding="utf-8",
            )
            locks_path.write_text(
                json.dumps(default_order_locks_data(), ensure_ascii=False),
                encoding="utf-8",
            )

            result = gui.AutoTradeSettingWindow.commit_execution_runtime_for_preview(
                window,
                order,
                guard,
                {"ok": True},
                order_executions_path=executions_path,
                order_locks_path=locks_path,
            )

            self.assertFalse(result["runtime_commit_ready"])
            self.assertEqual("runtime_real_commit_readiness", result["runtime_commit_stage"])
            self.assertIn("REAL_RUNTIME_COMMIT_DISABLED", result["blocked_reasons"])
            self.assertEqual(0, len(json.loads(executions_path.read_text(encoding="utf-8"))["executions"]))

    def test_execution_preview_button_displays_readiness_controller_text_when_available(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        guard = {"operator_confirmed": True, "real_trade_enabled": True}
        preview_result = {
            "ok": True,
            "read_result": {"ok": True, "order": {"id": "ORDER_1"}},
            "preview_result": {"queue_write_preview_result": self._queue_write_preview_result()},
        }
        report = {"ok": True, "order_id": "ORDER_1", "text": "Legacy Execution Preview Report"}
        controller_result = {
            "status": "READY",
            "formatted_result": {"text": "Execution Readiness Preview\nREADY"},
            "view_model": {"status": "READY"},
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_1", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value=preview_result),
            mock.patch.object(gui, "build_execution_preview_report", return_value=report),
            mock.patch.object(
                gui,
                "build_execution_readiness_preview_from_context",
                return_value=controller_result,
            ) as readiness_controller,
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        readiness_controller.assert_called_once()
        _, kwargs = readiness_controller.call_args
        self.assertEqual("ORDER_1", kwargs["order_id"])
        self.assertEqual(
            {
                "source": "gui_execution_preview_button",
                "guard": mock.ANY,
                "legacy_execution_preview_result": preview_result,
            },
            kwargs["preview_context"],
        )
        self.assertEqual("gui_session", kwargs["preview_context"]["guard"]["real_trade_guard_source"])
        self.assertEqual("Execution Readiness Preview\nREADY", window.reports[0]["text"])
        self.assertEqual(controller_result, window.reports[0]["readiness_controller_result"])
        self.assertNotIn("gate_result", kwargs["preview_context"])
        self.assertNotIn("order_candidate", kwargs["preview_context"])
        self.assertNotIn("queue_preview_result", kwargs["preview_context"])

    def test_execution_preview_button_falls_back_to_legacy_report_when_readiness_text_missing(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        guard = {"operator_confirmed": True, "real_trade_enabled": True}
        preview_result = {
            "ok": False,
            "read_result": {"ok": False, "blocked_reasons": ["blocked"]},
            "preview_result": None,
        }
        report = {"ok": False, "order_id": "ORDER_1", "text": "Legacy fallback report"}
        controller_result = {
            "status": "BLOCKED",
            "formatted_result": None,
            "view_model": None,
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_1", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value=preview_result),
            mock.patch.object(gui, "build_execution_preview_report", return_value=report),
            mock.patch.object(
                gui,
                "build_execution_readiness_preview_from_context",
                return_value=controller_result,
            ),
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        self.assertEqual([report], window.reports)
        self.assertEqual("Legacy fallback report", window.reports[0]["text"])
        self.assertNotIn("readiness_controller_result", window.reports[0])

    def test_blank_order_id_does_not_call_preview_service(self) -> None:
        window = _FakeWindow()

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("   ", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order") as service,
            mock.patch.object(gui, "build_execution_preview_report") as reporter,
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        service.assert_not_called()
        reporter.assert_not_called()
        self.assertEqual([], window.reports)
        self.assertTrue(window.messages)

    def test_non_real_ready_order_is_reported_as_blocked(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        guard = {"operator_confirmed": True, "real_trade_enabled": True}
        blocked_preview = {
            "ok": False,
            "read_result": {
                "ok": False,
                "blocked_reasons": ["order status is not REAL_READY: APPROVED"],
            },
            "preview_result": None,
        }
        blocked_report = {
            "ok": False,
            "order_id": None,
            "blocked_stage": None,
            "blocked_reasons": ["order status is not REAL_READY: APPROVED"],
            "text": "Execution Preview Report: BLOCKED",
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_1", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value=blocked_preview) as service,
            mock.patch.object(gui, "build_execution_preview_report", return_value=blocked_report) as reporter,
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        service.assert_called_once()
        self.assertEqual("ORDER_1", service.call_args.args[0])
        self.assertEqual("gui_session", service.call_args.args[1]["real_trade_guard_source"])
        reporter.assert_called_once_with(blocked_preview)
        self.assertEqual([blocked_report], window.reports)
        self.assertTrue(any("Execution Preview" in message for message in window.messages))

    def test_missing_order_id_lookup_is_reported_without_runtime_write_or_timer(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        guard = {"operator_confirmed": True, "real_trade_enabled": True}
        blocked_preview = {
            "ok": False,
            "read_result": {
                "ok": False,
                "blocked_reasons": ["order_id not found: MISSING_ORDER"],
            },
            "preview_result": None,
        }
        blocked_report = {
            "ok": False,
            "order_id": None,
            "blocked_stage": None,
            "blocked_reasons": ["order_id not found: MISSING_ORDER"],
            "text": "Execution Preview Report: BLOCKED",
        }

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("MISSING_ORDER", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value=blocked_preview) as service,
            mock.patch.object(gui, "build_execution_preview_report", return_value=blocked_report) as reporter,
            mock.patch.object(gui, "QTimer") as qtimer,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        service.assert_called_once()
        self.assertEqual("MISSING_ORDER", service.call_args.args[0])
        self.assertEqual("gui_session", service.call_args.args[1]["real_trade_guard_source"])
        reporter.assert_called_once_with(blocked_preview)
        self.assertEqual([blocked_report], window.reports)
        qtimer.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_report_dialog_uses_read_only_text_area(self) -> None:
        _FakeDialog.instances = []
        _FakeTextEdit.instances = []
        window = object()
        report = {
            "text": (
                "Execution Preview Report: OK\n"
                "[Approval]\n[Candidate]\n[Queue Pending]\n[Queue Writer Dry-Run]\n"
                "request_hash: abc"
            )
        }

        with (
            mock.patch.object(gui, "QDialog", _FakeDialog),
            mock.patch.object(gui, "QVBoxLayout", _FakeLayout),
            mock.patch.object(gui, "QHBoxLayout", _FakeLayout),
            mock.patch.object(gui, "QLabel", _FakeLabel),
            mock.patch.object(gui, "QTextEdit", _FakeTextEdit),
            mock.patch.object(gui, "QPushButton", _FakeButton),
            mock.patch.object(gui, "QFont", _FakeMonoFont),
        ):
            gui.AutoTradeSettingWindow.show_execution_preview_report(window, report)

        self.assertEqual(1, len(_FakeDialog.instances))
        self.assertTrue(_FakeDialog.instances[0].exec_called)
        self.assertEqual((900, 650), _FakeDialog.instances[0].size)
        self.assertEqual(1, len(_FakeTextEdit.instances))
        text_edit = _FakeTextEdit.instances[0]
        self.assertTrue(text_edit.read_only)
        self.assertEqual(report["text"], text_edit.text)
        self.assertIn("[Approval]", text_edit.text)
        self.assertIn("[Candidate]", text_edit.text)
        self.assertIn("[Queue Pending]", text_edit.text)
        self.assertIn("[Queue Writer Dry-Run]", text_edit.text)
        self.assertIsInstance(text_edit.font_value, _FakeMonoFont)
        self.assertEqual("Consolas", text_edit.font_value.family)
        self.assertEqual(10, text_edit.font_value.point_size)
        self.assertGreaterEqual(text_edit.minimum_height, 500)
        self.assertEqual(_FakeTextEdit.NoWrap, text_edit.line_wrap_mode)

    def test_runtime_forbidden_files_are_not_created_by_mocked_button_flow(self) -> None:
        window = self._window_for_queue_commit()
        window.reports = []
        window.show_execution_preview_report = lambda report: window.reports.append(report)
        report = {"ok": True, "text": "Execution Preview Report: OK"}

        with (
            mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_1", True)),
            mock.patch.object(gui, "preview_execution_for_real_ready_order", return_value={"ok": True}),
            mock.patch.object(gui, "build_execution_preview_report", return_value=report),
            mock.patch.object(gui, "commit_execution_queue_manually") as commit_service,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)

        write_text.assert_not_called()
        open_mock.assert_not_called()
        commit_service.assert_not_called()
        send_order_stub.assert_not_called()

    def test_manual_queue_commit_button_is_disabled_without_preview(self) -> None:
        window = self._window_for_queue_commit()

        gui.AutoTradeSettingWindow.update_manual_queue_commit_button_state(window)

        self.assertFalse(window.btn_manual_queue_commit.enabled)

    def test_manual_queue_commit_without_preview_does_not_call_commit_service(self) -> None:
        window = self._window_for_queue_commit()

        with mock.patch.object(gui, "commit_execution_queue_manually") as commit_service:
            gui.AutoTradeSettingWindow.commit_last_execution_preview_queue_manually(window)

        commit_service.assert_not_called()
        self.assertTrue(any("Execution Preview" in message for message in window.messages))

    def test_manual_queue_commit_cancel_does_not_call_commit_service(self) -> None:
        window = self._window_for_queue_commit()
        window._last_execution_preview_result = {
            "preview_result": {
                "queue_write_preview_result": self._queue_write_preview_result(),
                "runtime_commit_result": self._runtime_commit_result(),
            }
        }
        window._last_execution_preview_queue_snapshot = self._queue_snapshot()
        window.confirm_manual_queue_commit = lambda queue_write_preview, queue_path, queue_snapshot=None: False

        with (
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot()),
            mock.patch.object(gui, "commit_execution_queue_manually") as commit_service,
        ):
            gui.AutoTradeSettingWindow.commit_last_execution_preview_queue_manually(window)

        commit_service.assert_not_called()
        self.assertTrue(any("취소" in message for message in window.messages))

    def test_manual_queue_commit_confirmed_passes_manual_context(self) -> None:
        window = self._window_for_queue_commit()
        queue_write_preview = self._queue_write_preview_result()
        window._last_execution_preview_result = {
            "preview_result": {
                "queue_write_preview_result": queue_write_preview,
                "runtime_commit_result": self._runtime_commit_result(),
            }
        }
        window._last_execution_preview_queue_snapshot = self._queue_snapshot()
        window.confirm_manual_queue_commit = lambda queue_write_preview_result, queue_path, queue_snapshot=None: True
        commit_result = {
            "manual_commit": True,
            "commit_stage": "committed",
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "commit_result": {
                "status": "ORDER_QUEUED",
                "send_order_called": False,
                "execution_enabled": False,
            },
            "blocked_reasons": [],
        }

        with (
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                side_effect=[self._queue_snapshot(), self._queue_snapshot("HASH_AFTER")],
            ),
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "verify_manual_queue_commit_read_back",
                return_value={"verified": True, "stage": "verified", "record": {}, "issues": []},
            ) as read_back,
            mock.patch.object(gui, "commit_execution_queue_manually", return_value=commit_result) as commit_service,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            gui.AutoTradeSettingWindow.commit_last_execution_preview_queue_manually(window)

        commit_service.assert_called_once_with(
            queue_write_preview,
            gui.ORDER_QUEUE_PATH,
            context={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
            queue_commit_readiness_policy_result=mock.ANY,
            manual_queue_commit_after_runtime_confirmed=True,
        )
        self.assertEqual([commit_result], window.commit_reports)
        self.assertEqual("HASH_BEFORE", window.commit_reports[0]["before_sha256"])
        self.assertEqual("HASH_AFTER", window.commit_reports[0]["after_sha256"])
        self.assertTrue(window.commit_reports[0]["changed"])
        self.assertTrue(window.commit_reports[0]["queue_commit_read_back_verified"])
        read_back.assert_called_once()
        send_order_stub.assert_not_called()

    def test_manual_queue_commit_read_back_verifies_order_queued_identity(self) -> None:
        window = self._window_for_queue_commit()
        queue_write_preview = self._queue_write_preview_result()
        record = dict(queue_write_preview["order_queued_record_preview"])
        record.update(
            {
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = gui.Path(temp_dir) / "order_queue.json"
            queue_path.write_text(json.dumps({"orders": [record]}, ensure_ascii=False), encoding="utf-8")

            result = gui.AutoTradeSettingWindow.verify_manual_queue_commit_read_back(
                window,
                queue_path=queue_path,
                queue_write_preview_result=queue_write_preview,
                runtime_commit_result=self._runtime_commit_result(),
            )

        self.assertTrue(result["verified"])
        self.assertEqual("ORDER_QUEUED", result["record"]["status"])

    def test_manual_queue_commit_read_back_blocks_identity_mismatch(self) -> None:
        window = self._window_for_queue_commit()
        queue_write_preview = self._queue_write_preview_result()
        runtime_result = dict(self._runtime_commit_result())
        runtime_result["lock_id"] = "OTHER_LOCK"
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = gui.Path(temp_dir) / "order_queue.json"
            queue_path.write_text(json.dumps({"orders": []}, ensure_ascii=False), encoding="utf-8")

            result = gui.AutoTradeSettingWindow.verify_manual_queue_commit_read_back(
                window,
                queue_path=queue_path,
                queue_write_preview_result=queue_write_preview,
                runtime_commit_result=runtime_result,
            )

        self.assertFalse(result["verified"])
        self.assertIn("runtime/queue identity mismatch before read-back: lock_id", result["issues"])

    def test_gui_production_caller_reaches_order_queued_for_buy_and_sell(self) -> None:
        for side in ("BUY", "SELL"):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as temp_dir:
                runtime_dir = gui.Path(temp_dir) / "runtime"
                runtime_dir.mkdir()
                queue_path = runtime_dir / "order_queue.json"
                executions_path = runtime_dir / "order_executions.json"
                locks_path = runtime_dir / "order_locks.json"
                order_id = f"ORDER_{side}_1"
                queue_path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "revision": 0,
                            "updated_at": "",
                            "orders": [
                                {
                                    "id": order_id,
                                    "status": "REAL_READY",
                                    "source_signal_id": f"SIG_{side}_1",
                                    "code": "003550",
                                    "side": side,
                                    "quantity": 3,
                                    "price": 85000,
                                    "execution_enabled": True,
                                    "order_intent": {"side": side, "hoga": "MARKET"},
                                    "send_order_called": False,
                                    "broker_api_called": False,
                                    "actual_order_sent": False,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                window = self._window_for_queue_commit()
                window.reports = []
                window.show_execution_preview_report = lambda report: window.reports.append(report)
                window.read_order_from_queue_by_id = (
                    lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                        window,
                        current_order_id,
                        current_queue_path,
                    )
                )
                window.commit_execution_runtime_for_preview = (
                    lambda order, guard, result, **kwargs: gui.AutoTradeSettingWindow.commit_execution_runtime_for_preview(
                        window,
                        order,
                        guard,
                        result,
                        **kwargs,
                    )
                )
                window.confirm_execution_runtime_commit = lambda order, guard, **kwargs: True
                window.confirm_execution_runtime_file_init = lambda **kwargs: True
                window.confirm_manual_queue_commit = lambda queue_write_preview, queue_path, queue_snapshot=None: True

                with (
                    mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                    mock.patch.object(gui, "ORDER_EXECUTIONS_PATH", executions_path),
                    mock.patch.object(gui, "ORDER_LOCKS_PATH", locks_path),
                    mock.patch.object(gui.QInputDialog, "getText", return_value=(order_id, True)),
                    mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
                ):
                    gui.AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual(window)
                    gui.AutoTradeSettingWindow.commit_last_execution_preview_queue_manually(window)

                data = json.loads(queue_path.read_text(encoding="utf-8"))
                queued = [
                    item for item in data["orders"]
                    if isinstance(item, dict)
                    and item.get("status") == "ORDER_QUEUED"
                    and item.get("order_id") == order_id
                ]
                self.assertEqual(1, len(queued))
                self.assertTrue(window.commit_reports[-1]["manual_commit"])
                self.assertTrue(window.commit_reports[-1]["queue_commit_read_back_verified"])
                self.assertTrue(executions_path.exists())
                self.assertTrue(locks_path.exists())
                send_order_stub.assert_not_called()

    def test_auto_trade_process_executable_order_reaches_mock_send_order_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = gui.Path(temp_dir)
            runtime_dir = root / "runtime"
            routine_dir = root / "routines" / "지표추종매매"
            stock_dir = routine_dir / "003550_LG"
            runtime_dir.mkdir(parents=True)
            stock_dir.mkdir(parents=True)
            queue_path = runtime_dir / "order_queue.json"
            executions_path = runtime_dir / "order_executions.json"
            locks_path = runtime_dir / "order_locks.json"
            (stock_dir / "config.json").write_text(
                json.dumps({"real_trade_enabled": True}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "real_trade_enabled": True,
                        "signal_probe_only": False,
                        "review_required": False,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "revision": 0,
                        "updated_at": "",
                        "orders": [
                            {
                                "id": "ORDER_AUTO_1",
                                "status": "EXECUTABLE",
                                "source_signal_id": "SIG_AUTO_1",
                                "code": "003550",
                                "side": "BUY",
                                "quantity": 3,
                                "price": 85000,
                                "order_type": "LIMIT",
                                "order_intent": {"side": "BUY", "hoga": "LIMIT"},
                                "approval_status": "APPROVED",
                                "policy_status": "EXECUTABLE",
                                "execution_enabled": False,
                                "send_order_called": False,
                                "broker_api_called": False,
                                "actual_order_sent": False,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
            window.messages = []
            window.statusBarMessage = lambda message, timeout_ms=5000: window.messages.append(message)
            parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            parent.account_combo = _FakeAccountCombo()
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.parent = lambda: parent
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )
            window.current_selected_routine_dir = lambda: routine_dir

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "ORDER_EXECUTIONS_PATH", executions_path),
                mock.patch.object(gui, "ORDER_LOCKS_PATH", locks_path),
                mock.patch.object(gui, "get_stock_dirs_in_routine", return_value=[stock_dir]),
            ):
                result = gui.AutoTradeSettingWindow.process_executable_order_for_auto_trade(
                    window,
                    "ORDER_AUTO_1",
                )
                duplicate = gui.AutoTradeSettingWindow.process_executable_order_for_auto_trade(
                    window,
                    "ORDER_AUTO_1",
                )

            self.assertTrue(result["processed"], result)
            self.assertEqual("send_order", result["stage"])
            self.assertTrue(executions_path.exists())
            self.assertTrue(locks_path.exists())
            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), result)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            queued = [
                item for item in data["orders"]
                if isinstance(item, dict) and item.get("status") == "SEND_CALL_ACCEPTED"
            ]
            self.assertEqual(1, len(queued), data)
            self.assertTrue(queued[0]["send_order_called"])
            self.assertTrue(queued[0]["broker_api_called"])
            self.assertFalse(duplicate["processed"])
            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), duplicate)

    def test_auto_trade_process_executable_order_blocks_probe_only_without_send_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = gui.Path(temp_dir)
            runtime_dir = root / "runtime"
            routine_dir = root / "routines" / "지표추종매매"
            stock_dir = routine_dir / "003550_LG"
            runtime_dir.mkdir(parents=True)
            stock_dir.mkdir(parents=True)
            queue_path = runtime_dir / "order_queue.json"
            executions_path = runtime_dir / "order_executions.json"
            locks_path = runtime_dir / "order_locks.json"
            executions_path.write_text(
                json.dumps(default_order_executions_data(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            locks_path.write_text(
                json.dumps(default_order_locks_data(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text(
                json.dumps({"real_trade_enabled": True}, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "real_trade_enabled": True,
                        "signal_probe_only": True,
                        "review_required": False,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "revision": 0,
                        "updated_at": "",
                        "orders": [
                            {
                                "id": "ORDER_AUTO_1",
                                "status": "EXECUTABLE",
                                "source_signal_id": "SIG_AUTO_1",
                                "code": "003550",
                                "side": "BUY",
                                "quantity": 3,
                                "price": 85000,
                                "order_type": "LIMIT",
                                "order_intent": {"side": "BUY", "hoga": "LIMIT"},
                                "approval_status": "APPROVED",
                                "policy_status": "EXECUTABLE",
                                "execution_enabled": False,
                                "send_order_called": False,
                                "broker_api_called": False,
                                "actual_order_sent": False,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
            parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            parent.account_combo = _FakeAccountCombo()
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.parent = lambda: parent
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )
            window.current_selected_routine_dir = lambda: routine_dir

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "ORDER_EXECUTIONS_PATH", executions_path),
                mock.patch.object(gui, "ORDER_LOCKS_PATH", locks_path),
                mock.patch.object(gui, "get_stock_dirs_in_routine", return_value=[stock_dir]),
            ):
                result = gui.AutoTradeSettingWindow.process_executable_order_for_auto_trade(
                    window,
                    "ORDER_AUTO_1",
                )

            self.assertFalse(result["processed"])
            self.assertEqual("auto_trade_runtime_state", result["stage"])
            self.assertIn("signal_probe_only is true", result["blocked_reasons"])
            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))

    def test_auto_trade_runtime_state_blocks_unsafe_flags(self) -> None:
        cases = [
            ({"status": "RUNNING", "trade_enabled": False, "real_trade_enabled": True}, "trade_enabled is not true"),
            ({"status": "RUNNING", "trade_enabled": True, "real_trade_enabled": False}, "real_trade_enabled is not true"),
            ({"status": "RUNNING", "trade_enabled": True, "real_trade_enabled": True, "review_required": True}, "review_required is true"),
            ({"status": "EMERGENCY_STOPPED", "trade_enabled": True, "real_trade_enabled": True}, "auto trade status is not RUNNING"),
        ]
        for state_update, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason), tempfile.TemporaryDirectory() as temp_dir:
                routine_dir = gui.Path(temp_dir) / "routine"
                stock_dir = routine_dir / "003550_LG"
                stock_dir.mkdir(parents=True)
                (stock_dir / "config.json").write_text("{}", encoding="utf-8")
                state = {
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                    "signal_probe_only": False,
                    "review_required": False,
                }
                state.update(state_update)
                (stock_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
                window.current_selected_routine_dir = lambda: routine_dir

                with mock.patch.object(gui, "get_stock_dirs_in_routine", return_value=[stock_dir]):
                    reasons = gui.AutoTradeSettingWindow.auto_trade_execution_block_reasons(
                        window,
                        {"code": "003550"},
                    )

                self.assertIn(expected_reason, reasons)

    def test_auto_send_order_blocks_without_send_order_callable(self) -> None:
        class ApiWithoutSendOrder:
            def is_connected(self) -> bool:
                return True

            def account_numbers(self) -> list[str]:
                return ["12345678"]

        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            self._write_queue_for_send_order(queue_path, record)
            routine_dir = gui.Path(tmp) / "routine"
            stock_dir = routine_dir / "003550_LG"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(json.dumps({"real_trade_enabled": True}), encoding="utf-8")
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "real_trade_enabled": True,
                        "signal_probe_only": False,
                        "review_required": False,
                    }
                ),
                encoding="utf-8",
            )
            window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
            parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
            parent.kiwoom_api = ApiWithoutSendOrder()
            parent.account_combo = _FakeAccountCombo()
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.parent = lambda: parent
            window.current_selected_routine_dir = lambda: routine_dir
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "get_stock_dirs_in_routine", return_value=[stock_dir]),
            ):
                result = gui.AutoTradeSettingWindow.send_order_for_order_queued_automatically(
                    window,
                    "ORDER_QUEUED_ORDER_1",
                    queue_path=queue_path,
                )

            self.assertEqual("send_order_environment", result["stage"])
            self.assertIn("kiwoom api SendOrder callable is unavailable", result["blocked_reasons"])

    def test_auto_send_order_final_gate_failure_does_not_call_send_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            self._write_queue_for_send_order(queue_path, record)
            routine_dir = gui.Path(tmp) / "routine"
            stock_dir = routine_dir / "003550_LG"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(json.dumps({"real_trade_enabled": True}), encoding="utf-8")
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "trade_enabled": True,
                        "real_trade_enabled": True,
                        "signal_probe_only": False,
                        "review_required": False,
                    }
                ),
                encoding="utf-8",
            )
            window = gui.AutoTradeSettingWindow.__new__(gui.AutoTradeSettingWindow)
            parent = main_gui.MainWindow.__new__(main_gui.MainWindow)
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            parent.account_combo = _FakeAccountCombo()
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.parent = lambda: parent
            window.current_selected_routine_dir = lambda: routine_dir
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )
            window.build_manual_final_send_gate_result = lambda *_args, **_kwargs: {
                "final_send_gate_ok": False,
                "blocked_reasons": ["forced final gate failure"],
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "get_stock_dirs_in_routine", return_value=[stock_dir]),
            ):
                result = gui.AutoTradeSettingWindow.send_order_for_order_queued_automatically(
                    window,
                    "ORDER_QUEUED_ORDER_1",
                    queue_path=queue_path,
                )

            self.assertEqual("final_send_gate", result["stage"])
            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))

    def test_manual_queue_commit_failure_result_is_displayed(self) -> None:
        window = self._window_for_queue_commit()
        window._last_execution_preview_result = {
            "preview_result": {
                "queue_write_preview_result": self._queue_write_preview_result(),
                "runtime_commit_result": self._runtime_commit_result(),
            }
        }
        window._last_execution_preview_queue_snapshot = self._queue_snapshot()
        window.confirm_manual_queue_commit = lambda queue_write_preview_result, queue_path, queue_snapshot=None: True
        commit_result = {
            "manual_commit": False,
            "commit_stage": "duplicate",
            "next_stage": "BLOCKED",
            "commit_result": None,
            "blocked_reasons": ["duplicate request_hash"],
        }

        with (
            mock.patch.object(
                gui.AutoTradeSettingWindow,
                "queue_file_snapshot",
                side_effect=[self._queue_snapshot(), self._queue_snapshot()],
            ),
            mock.patch.object(gui, "commit_execution_queue_manually", return_value=commit_result),
        ):
            gui.AutoTradeSettingWindow.commit_last_execution_preview_queue_manually(window)

        self.assertEqual([commit_result], window.commit_reports)
        self.assertIn("duplicate request_hash", window.commit_reports[0]["blocked_reasons"])

    def test_manual_queue_commit_stale_preview_blocks_without_commit_call(self) -> None:
        window = self._window_for_queue_commit()
        window._last_execution_preview_result = {
            "preview_result": {
                "queue_write_preview_result": self._queue_write_preview_result(),
            }
        }
        window._last_execution_preview_queue_snapshot = self._queue_snapshot("HASH_OLD")

        with (
            mock.patch.object(gui.AutoTradeSettingWindow, "queue_file_snapshot", return_value=self._queue_snapshot("HASH_NEW")),
            mock.patch.object(gui, "commit_execution_queue_manually") as commit_service,
        ):
            gui.AutoTradeSettingWindow.commit_last_execution_preview_queue_manually(window)

        commit_service.assert_not_called()
        self.assertEqual("stale_preview", window.commit_reports[0]["commit_stage"])
        self.assertIn(
            "queue file changed after preview; rerun Execution Preview",
            window.commit_reports[0]["blocked_reasons"],
        )

    def test_manual_queue_commit_confirmation_text_contains_required_safety_fields(self) -> None:
        window = self._window_for_queue_commit()
        text = gui.AutoTradeSettingWindow.manual_queue_commit_confirmation_text(
            window,
            self._queue_write_preview_result(),
            gui.ORDER_QUEUE_PATH,
            self._queue_snapshot(),
        )

        self.assertIn("SendOrder 호출이 아닙니다.", text)
        self.assertIn("주문 전송이 아닙니다.", text)
        self.assertIn("자동 실행 루프에 연결되지 않습니다.", text)
        self.assertIn("order_id: ORDER_1", text)
        self.assertIn("request_hash: HASH_1", text)
        self.assertIn("lock_id: LOCK_1", text)
        self.assertIn("queue_pending_id: QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1", text)
        self.assertIn("order_queued_id: ORDER_QUEUED_ORDER_1", text)
        self.assertIn("queue_path:", text)
        self.assertIn("before_sha256: HASH_BEFORE", text)
        self.assertIn("file_size: 1071", text)
        self.assertIn("mtime: 2026-07-03 10:08:43", text)
        self.assertIn("orders_count: 1", text)
        self.assertIn("backup_path:", text)

    def test_manual_send_order_claims_and_records_send_call_accepted_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order(side="BUY")
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            confirmation_previews = []

            def confirm(order, call_preview, queue_path, queue_snapshot):
                confirmation_previews.append(call_preview)
                self.assertNotEqual("SEND_ORDER_CALL_READY", call_preview.get("status"))
                self.assertIn("operator final send confirmation is required", call_preview.get("issues", []))
                self.assertEqual("SEND_ORDER_CONTRACT_READY", call_preview["adapter_contract_result"]["status"])
                return True

            window.confirm_manual_send_order = confirm
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            self.assertEqual(["0101", "BUY", "12345678", 1, "003550", 10, 1000, "00", ""], list(parent.kiwoom_api.send_order_calls[0]))
            self.assertEqual("SEND_CALL_ACCEPTED", window.send_order_reports[-1]["status"])
            self.assertEqual(1, len(confirmation_previews))
            self.assertEqual(
                "FINAL_SEND_GATE_SERVICE",
                window.send_order_reports[-1]["final_send_gate_result"]["final_send_gate_result_type"],
            )
            self.assertNotEqual(
                "SELL_DISPATCH_FINAL_EXECUTION_GUARD",
                window.send_order_reports[-1]["final_send_gate_result"].get("guard_type"),
            )
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            actual = data["orders"][0]
            self.assertEqual("SEND_CALL_ACCEPTED", actual["status"])
            self.assertTrue(actual["send_order_called"])
            self.assertTrue(actual["broker_api_called"])
            self.assertFalse(actual["actual_order_sent"])

    def test_manual_send_order_sends_queued_cancel_with_original_order_no_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order(side="BUY")
            record["order_id"] = "ORDER_CANCEL_1"
            record["id"] = "ORDER_QUEUED_CANCEL_1"
            record["execution_request"]["order_id"] = "ORDER_CANCEL_1"
            request_preview = record["execution_request"]["request_preview"]
            request_preview.update(
                {
                    "order_action": "CANCEL",
                    "original_order_no": "987654",
                    "quantity": 4,
                    "price": 0,
                    "hoga": "LIMIT",
                }
            )
            record["quantity"] = 4
            record["price"] = 0
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: True
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_CANCEL_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            self.assertEqual(
                ["0101", "BUY_CANCEL", "12345678", 3, "003550", 4, 0, "00", "987654"],
                list(parent.kiwoom_api.send_order_calls[0]),
            )
            self.assertEqual("SEND_CALL_ACCEPTED", window.send_order_reports[-1]["status"])

    def test_manual_cancel_open_order_creates_queued_cancel_and_sends_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order(side="BUY")
            record.update(
                {
                    "status": "BROKER_ACCEPTED",
                    "broker_order_no": "987654",
                    "remaining_quantity": 4,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_cancel_pending_order = lambda source_order, preview: True
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: True
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.cancel_pending_order_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            self.assertEqual(
                ["0101", "BUY_CANCEL", "12345678", 3, "003550", 4, 0, "00", "987654"],
                list(parent.kiwoom_api.send_order_calls[0]),
            )
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(data["orders"]))
            self.assertEqual("BROKER_ACCEPTED", data["orders"][0]["status"])
            self.assertEqual("SEND_CALL_ACCEPTED", data["orders"][1]["status"])

    def test_manual_cancel_duplicate_active_request_blocks_without_second_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            source = self._order_queued_record_for_send_order(side="BUY")
            source.update({"status": "BROKER_ACCEPTED", "broker_order_no": "987654", "remaining_quantity": 4})
            cancel = self._order_queued_record_for_send_order(side="BUY")
            cancel["id"] = "ORDER_QUEUED_CANCEL_EXISTING"
            cancel["order_id"] = "ORDER_CANCEL_EXISTING"
            cancel["execution_request"]["order_id"] = "ORDER_CANCEL_EXISTING"
            cancel["execution_request"]["request_preview"].update(
                {"order_action": "CANCEL", "original_order_no": "987654", "quantity": 4, "price": 0}
            )
            self._write_queue_for_send_order(queue_path, source)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            data["orders"].append(cancel)
            queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.cancel_pending_order_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertIn("active cancel/modify request already exists", window.send_order_reports[-1]["blocked_reasons"][0])

    def test_manual_modify_blocks_when_active_cancel_exists_for_same_original_order_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            source = self._order_queued_record_for_send_order(side="BUY")
            source.update({"status": "BROKER_ACCEPTED", "broker_order_no": "987654", "remaining_quantity": 4})
            cancel = self._order_queued_record_for_send_order(side="BUY")
            cancel["id"] = "ORDER_QUEUED_CANCEL_EXISTING"
            cancel["order_id"] = "ORDER_CANCEL_EXISTING"
            cancel["execution_request"]["order_id"] = "ORDER_CANCEL_EXISTING"
            cancel["execution_request"]["request_preview"].update(
                {"order_action": "CANCEL", "original_order_no": "987654", "quantity": 4, "price": 0}
            )
            self._write_queue_for_send_order(queue_path, source)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            data["orders"].append(cancel)
            queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", side_effect=[("ORDER_QUEUED_ORDER_1", True), ("3,1200", True)]),
            ):
                gui.AutoTradeSettingWindow.modify_pending_order_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertIn("active cancel/modify request already exists", window.send_order_reports[-1]["blocked_reasons"][0])

    def test_manual_cancel_blocks_when_modify_request_already_broker_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            source = self._order_queued_record_for_send_order(side="SELL")
            source.update({"status": "PARTIALLY_FILLED", "broker_order_no": "222333", "remaining_quantity": 6})
            modify = self._order_queued_record_for_send_order(side="SELL")
            modify["id"] = "ORDER_QUEUED_MODIFY_EXISTING"
            modify["order_id"] = "ORDER_MODIFY_EXISTING"
            modify["status"] = "BROKER_ACCEPTED"
            modify["broker_order_no"] = "MODIFY_BRK_1"
            modify["execution_request"]["order_id"] = "ORDER_MODIFY_EXISTING"
            modify["execution_request"]["request_preview"].update(
                {"order_action": "MODIFY", "original_order_no": "222333", "quantity": 5, "price": 1200}
            )
            self._write_queue_for_send_order(queue_path, source)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            data["orders"].append(modify)
            queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.cancel_pending_order_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertIn("active cancel/modify request already exists", window.send_order_reports[-1]["blocked_reasons"][0])

    def test_manual_cancel_allows_after_modify_original_effect_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            source = self._order_queued_record_for_send_order(side="SELL")
            source.update({"status": "PARTIALLY_FILLED", "broker_order_no": "222333", "remaining_quantity": 6})
            modify = self._order_queued_record_for_send_order(side="SELL")
            modify["id"] = "ORDER_QUEUED_MODIFY_CONFIRMED"
            modify["order_id"] = "ORDER_MODIFY_CONFIRMED"
            modify["status"] = "BROKER_ACCEPTED"
            modify["broker_order_no"] = "MODIFY_BRK_1"
            modify["original_order_effect_confirmed"] = True
            modify["execution_request"]["order_id"] = "ORDER_MODIFY_CONFIRMED"
            modify["execution_request"]["request_preview"].update(
                {"order_action": "MODIFY", "original_order_no": "222333", "quantity": 5, "price": 1200}
            )
            self._write_queue_for_send_order(queue_path, source)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            data["orders"].append(modify)
            queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_cancel_pending_order = lambda source_order, preview: True
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: True
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.cancel_pending_order_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            self.assertEqual("SEND_CALL_ACCEPTED", window.send_order_reports[-1]["status"])

    def test_manual_modify_open_order_creates_queued_modify_and_sends_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order(side="SELL")
            record.update(
                {
                    "status": "PARTIALLY_FILLED",
                    "broker_order_no": "222333",
                    "remaining_quantity": 6,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_modify_pending_order = lambda source_order, preview: True
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: True
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", side_effect=[("ORDER_QUEUED_ORDER_1", True), ("5,1200", True)]),
            ):
                gui.AutoTradeSettingWindow.modify_pending_order_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            self.assertEqual(
                ["0101", "SELL_MODIFY", "12345678", 6, "003550", 5, 1200, "00", "222333"],
                list(parent.kiwoom_api.send_order_calls[0]),
            )
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(2, len(data["orders"]))
            self.assertEqual("PARTIALLY_FILLED", data["orders"][0]["status"])
            self.assertEqual("SEND_CALL_ACCEPTED", data["orders"][1]["status"])

    def test_manual_send_order_dialog_cancel_does_not_claim_or_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: False
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual("ORDER_QUEUED", data["orders"][0]["status"])

    def test_manual_send_order_dialog_login_change_blocks_before_claim_and_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)

            def confirm(order, call_preview, queue_path, queue_snapshot):
                parent.kiwoom_api.connected = False
                return True

            window.confirm_manual_send_order = confirm
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertEqual("send_order_environment_after_confirmation", window.send_order_reports[-1]["stage"])
            self.assertIn("kiwoom api is not connected", window.send_order_reports[-1]["blocked_reasons"])
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual("ORDER_QUEUED", data["orders"][0]["status"])

    def test_manual_send_order_login_failure_blocks_before_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=False, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertEqual("send_order_environment", window.send_order_reports[-1]["stage"])
            self.assertIn("kiwoom api is not connected", window.send_order_reports[-1]["blocked_reasons"])

    def test_manual_send_order_missing_order_account_blocks_without_selected_account_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            record.pop("account_no", None)
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertEqual("send_order_environment", window.send_order_reports[-1]["stage"])
            self.assertIn("ORDER_QUEUED account_no is required", window.send_order_reports[-1]["blocked_reasons"])

    def test_manual_send_order_request_account_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            record["execution_request"]["request_preview"]["account_no"] = "87654321"
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678", "87654321"], send_order_result=0)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(0, len(parent.kiwoom_api.send_order_calls))
            self.assertEqual("send_order_environment", window.send_order_reports[-1]["stage"])
            self.assertIn(
                "ORDER_QUEUED account_no does not match execution request account_no",
                window.send_order_reports[-1]["blocked_reasons"],
            )

    def test_manual_send_order_nonzero_records_send_call_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order(side="SELL")
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=-308)
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: True
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual("SEND_CALL_REJECTED", data["orders"][0]["status"])
            self.assertEqual("SEND_CALL_REJECTED", window.send_order_reports[-1]["status"])

    def test_manual_send_order_exception_records_uncertain_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            parent = window.parent()
            parent.kiwoom_api = _FakeApi(connected=True, accounts=["12345678"], send_order_result=RuntimeError("boom"))
            main_gui.MainWindow.refresh_kiwoom_accounts(parent)
            window.send_order_reports = []
            window.show_manual_send_order_result = lambda result: window.send_order_reports.append(result)
            window.confirm_manual_send_order = lambda order, call_preview, queue_path, queue_snapshot: True
            window.read_order_from_queue_by_id = (
                lambda current_order_id, current_queue_path: gui.AutoTradeSettingWindow.read_order_from_queue_by_id(
                    window,
                    current_order_id,
                    current_queue_path,
                )
            )

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui.QInputDialog, "getText", return_value=("ORDER_QUEUED_ORDER_1", True)),
            ):
                gui.AutoTradeSettingWindow.send_order_for_order_queued_manually(window)

            self.assertEqual(1, len(parent.kiwoom_api.send_order_calls), window.send_order_reports)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual("SEND_UNCERTAIN", data["orders"][0]["status"])
            self.assertFalse(data["orders"][0]["automatic_retry_allowed"])
            self.assertEqual("SEND_UNCERTAIN", window.send_order_reports[-1]["status"])

    def test_raw_chejan_order_open_links_broker_order_no_through_existing_recorder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "ACCEPT",
                    "900": "10",
                    "911": "0",
                    "902": "10",
                    "910": "0",
                    "901": "1000",
                },
                "received_at": "2026-07-16 10:00:00",
            }

            with mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path):
                result = gui.AutoTradeSettingWindow.handle_raw_chejan_event(
                    window,
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )
                duplicate = gui.AutoTradeSettingWindow.handle_raw_chejan_event(
                    window,
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertTrue(result["recorded"], result)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            actual = data["orders"][0]
            self.assertEqual("BRK_1", actual["broker_order_no"])
            self.assertFalse(duplicate["recorded"])
            self.assertTrue(duplicate.get("blocked_reasons"))

    def test_main_window_raw_chejan_signal_records_without_auto_trade_setting_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_UNCERTAIN",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": False,
                    "send_uncertain": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            main = main_gui.MainWindow.__new__(main_gui.MainWindow)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "ACCEPT",
                    "900": "10",
                    "911": "0",
                    "902": "10",
                    "910": "0",
                    "901": "1000",
                },
                "received_at": "2026-07-16 10:00:00",
            }

            with mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path):
                main_gui.MainWindow.on_kiwoom_raw_chejan_received(main, raw_event)

            self.assertTrue(main.last_chejan_record_result["recorded"], main.last_chejan_record_result)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual("BRK_1", data["orders"][0]["broker_order_no"])

    def test_main_window_live_partial_fill_updates_queue_fills_and_position_without_setting_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            main = main_gui.MainWindow.__new__(main_gui.MainWindow)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_1",
                },
                "received_at": "2026-07-16 10:01:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                main_gui.MainWindow.on_kiwoom_raw_chejan_received(main, raw_event)
                first = main.last_chejan_record_result
                queue_after_first = queue_path.read_text(encoding="utf-8")
                fills_after_first = fills_path.read_text(encoding="utf-8")
                positions_after_first = positions_path.read_text(encoding="utf-8")
                main_gui.MainWindow.on_kiwoom_raw_chejan_received(main, raw_event)
                duplicate = main.last_chejan_record_result

            self.assertTrue(first["recorded"], first)
            self.assertTrue(first["fill_result"]["fill_recorded"], first)
            self.assertTrue(first["position_result"]["position_updated"], first)
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual("PARTIALLY_FILLED", queue["orders"][0]["status"])
            self.assertEqual(3, queue["orders"][0]["cumulative_filled_quantity"])
            self.assertEqual(7, queue["orders"][0]["remaining_quantity"])
            self.assertFalse(queue["orders"][0]["manual_reconciliation_required"])
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(3, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])
            self.assertFalse(duplicate["recorded"], duplicate)
            self.assertTrue(duplicate["duplicate_noop"], duplicate)
            self.assertNotIn("fill_result", duplicate)
            self.assertNotIn("position_result", duplicate)
            self.assertEqual(queue_after_first, queue_path.read_text(encoding="utf-8"))
            self.assertEqual(fills_after_first, fills_path.read_text(encoding="utf-8"))
            self.assertEqual(positions_after_first, positions_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(3, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])

    def test_live_fill_failure_is_persisted_and_same_event_reprocesses_missing_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            fills_path.write_text("{bad json", encoding="utf-8")
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_RETRY_FILL",
                },
                "received_at": "2026-07-16 10:03:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )
                queue_after_failure = json.loads(queue_path.read_text(encoding="utf-8"))
                fills_path.write_text(json.dumps({"version": 1, "updated_at": None, "fills": []}), encoding="utf-8")
                second = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertTrue(first["manual_reconciliation_required"], first)
            failed_order = queue_after_failure["orders"][0]
            self.assertTrue(failed_order["manual_reconciliation_required"])
            self.assertEqual("FILL_RECORD", failed_order["chejan_reconciliation_failed_stage"])
            self.assertEqual(["QUEUE_LIFECYCLE"], failed_order["chejan_reconciliation_completed_steps"])
            self.assertTrue(second["duplicate_reprocess"], second)
            self.assertTrue(second["fill_result"]["fill_recorded"], second)
            self.assertTrue(second["position_result"]["position_updated"], second)
            final_order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertFalse(final_order["manual_reconciliation_required"])
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(3, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])

    def test_live_position_failure_is_persisted_and_same_event_reprocesses_position_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            positions_path.write_text("{bad json", encoding="utf-8")
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_2",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_RETRY_POSITION",
                },
                "received_at": "2026-07-16 10:04:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )
                queue_after_failure = json.loads(queue_path.read_text(encoding="utf-8"))
                positions_path.write_text(json.dumps({"version": 1, "updated_at": None, "positions": []}), encoding="utf-8")
                second = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertTrue(first["manual_reconciliation_required"], first)
            self.assertTrue(first["fill_result"]["fill_recorded"], first)
            failed_order = queue_after_failure["orders"][0]
            self.assertTrue(failed_order["manual_reconciliation_required"])
            self.assertEqual("POSITION_UPDATE", failed_order["chejan_reconciliation_failed_stage"])
            self.assertEqual(["QUEUE_LIFECYCLE", "FILL_RECORD"], failed_order["chejan_reconciliation_completed_steps"])
            self.assertTrue(second["duplicate_reprocess"], second)
            self.assertFalse(second["fill_result"]["fill_recorded"], second)
            self.assertTrue(second["position_result"]["position_updated"], second)
            final_order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertFalse(final_order["manual_reconciliation_required"])
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(3, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])

    def test_live_full_fill_failure_reprocesses_after_queue_is_filled_when_event_identity_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            fills_path.write_text("{bad json", encoding="utf-8")
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_FULL_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "10",
                    "902": "0",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_FULL_RETRY",
                },
                "received_at": "2026-07-16 10:06:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )
                after_failure = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
                fills_path.write_text(json.dumps({"version": 1, "updated_at": None, "fills": []}), encoding="utf-8")
                second = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertEqual("FILLED", after_failure["status"])
            self.assertTrue(after_failure["manual_reconciliation_required"])
            self.assertEqual("FILL_RECORD", after_failure["chejan_reconciliation_failed_stage"])
            self.assertTrue(first["manual_reconciliation_required"], first)
            self.assertTrue(second["duplicate_reprocess"], second)
            self.assertTrue(second["fill_result"]["fill_recorded"], second)
            self.assertTrue(second["position_result"]["position_updated"], second)
            final_order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertEqual("FILLED", final_order["status"])
            self.assertFalse(final_order["manual_reconciliation_required"])
            self.assertEqual(10, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])

    def test_completed_full_fill_duplicate_is_noop_for_all_stores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_FULL_NOOP",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "10",
                    "902": "0",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_FULL_NOOP",
                },
                "received_at": "2026-07-16 10:16:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )
                queue_after_first = queue_path.read_text(encoding="utf-8")
                fills_after_first = fills_path.read_text(encoding="utf-8")
                positions_after_first = positions_path.read_text(encoding="utf-8")
                duplicate = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )

            self.assertTrue(first["recorded"], first)
            self.assertEqual("FILLED", json.loads(queue_after_first)["orders"][0]["status"])
            self.assertFalse(duplicate["recorded"], duplicate)
            self.assertTrue(duplicate["duplicate_noop"], duplicate)
            self.assertNotIn("fill_result", duplicate)
            self.assertNotIn("position_result", duplicate)
            self.assertEqual(queue_after_first, queue_path.read_text(encoding="utf-8"))
            self.assertEqual(fills_after_first, fills_path.read_text(encoding="utf-8"))
            self.assertEqual(positions_after_first, positions_path.read_text(encoding="utf-8"))

    def test_new_full_fill_does_not_attach_to_filled_order_without_pending_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "FILLED",
                    "broker_order_no": "BRK_FULL_DONE",
                    "cumulative_filled_quantity": 10,
                    "remaining_quantity": 0,
                    "fill_count": 1,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_FULL_DONE",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "10",
                    "902": "0",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_NOT_PENDING",
                },
                "received_at": "2026-07-16 10:07:00",
            }

            with mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path):
                result = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertFalse(result["recorded"], result)
            self.assertEqual("chejan_target_match", result["stage"])

    def test_later_fill_success_does_not_clear_earlier_pending_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            fills_path.write_text("{bad json", encoding="utf-8")
            event_a = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_MULTI",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_MULTI_A",
                },
                "received_at": "2026-07-16 10:08:00",
            }
            event_b = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_MULTI",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "체결",
                    "900": "10",
                    "911": "5",
                    "902": "5",
                    "910": "1100",
                    "901": "1000",
                    "909": "EXEC_MULTI_B",
                },
                "received_at": "2026-07-16 10:09:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                fills_path.write_text(json.dumps({"version": 1, "updated_at": None, "fills": []}), encoding="utf-8")
                second = gui.handle_kiwoom_raw_chejan_event(event_b, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                retry_first = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                queue_after_retry = queue_path.read_text(encoding="utf-8")
                fills_after_retry = fills_path.read_text(encoding="utf-8")
                positions_after_retry = positions_path.read_text(encoding="utf-8")
                repeat_a = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                repeat_b = gui.handle_kiwoom_raw_chejan_event(event_b, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})

            self.assertTrue(first["manual_reconciliation_required"], first)
            self.assertNotIn("manual_reconciliation_required", second)
            self.assertTrue(retry_first["duplicate_reprocess"], retry_first)
            self.assertEqual("later_cumulative_fill_already_applied", retry_first["position_result"]["position_stage"])
            order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            pending = [item for item in order["chejan_reconciliation_items"] if item.get("required") is True]
            resolved = [item for item in order["chejan_reconciliation_items"] if item.get("required") is False]
            self.assertFalse(order["manual_reconciliation_required"])
            self.assertEqual([], pending)
            self.assertEqual(
                {
                    first["reconciliation_result"]["event_identity"],
                    second["reconciliation_result"]["event_identity"],
                },
                {item["event_identity"] for item in resolved},
            )
            self.assertEqual(2, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(5, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])
            self.assertTrue(repeat_a["duplicate_noop"], repeat_a)
            self.assertTrue(repeat_b["duplicate_noop"], repeat_b)
            self.assertEqual(queue_after_retry, queue_path.read_text(encoding="utf-8"))
            self.assertEqual(fills_after_retry, fills_path.read_text(encoding="utf-8"))
            self.assertEqual(positions_after_retry, positions_path.read_text(encoding="utf-8"))

    def test_live_full_fill_position_failure_reprocesses_after_queue_is_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            positions_path.write_text("{bad json", encoding="utf-8")
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_FULL_POSITION_RETRY",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "10",
                    "902": "0",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_FULL_POSITION_RETRY",
                },
                "received_at": "2026-07-16 10:10:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )
                after_failure = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
                positions_path.write_text(json.dumps({"version": 1, "updated_at": None, "positions": []}), encoding="utf-8")
                second = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )

            self.assertEqual("FILLED", after_failure["status"])
            self.assertEqual("POSITION_UPDATE", after_failure["chejan_reconciliation_failed_stage"])
            self.assertTrue(first["manual_reconciliation_required"], first)
            self.assertTrue(second["duplicate_reprocess"], second)
            self.assertFalse(second["fill_result"]["fill_recorded"], second)
            self.assertTrue(second["position_result"]["position_updated"], second)
            final_order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertFalse(final_order["manual_reconciliation_required"])
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(10, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])

    def test_two_failed_fill_events_recover_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            fills_path.write_text("{bad json", encoding="utf-8")
            event_a = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_TWO_FAILED",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_TWO_FAILED_A",
                },
                "received_at": "2026-07-16 10:11:00",
            }
            event_b = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_TWO_FAILED",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "5",
                    "902": "5",
                    "910": "1100",
                    "901": "1000",
                    "909": "EXEC_TWO_FAILED_B",
                },
                "received_at": "2026-07-16 10:12:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                second = gui.handle_kiwoom_raw_chejan_event(event_b, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                fills_path.write_text(json.dumps({"version": 1, "updated_at": None, "fills": []}), encoding="utf-8")
                retry_first = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})

            self.assertTrue(first["manual_reconciliation_required"], first)
            self.assertTrue(second["manual_reconciliation_required"], second)
            self.assertTrue(retry_first["duplicate_reprocess"], retry_first)
            order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            pending = [item for item in order["chejan_reconciliation_items"] if item.get("required") is True]
            resolved = [item for item in order["chejan_reconciliation_items"] if item.get("required") is False]
            self.assertEqual([second["reconciliation_result"]["event_identity"]], [item["event_identity"] for item in pending])
            self.assertIn(first["reconciliation_result"]["event_identity"], [item["event_identity"] for item in resolved])
            self.assertTrue(order["manual_reconciliation_required"])
            self.assertEqual(1, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))

    def test_reconciliation_queue_mutation_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            fills_path.write_text("{bad json", encoding="utf-8")
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_RECON_FAIL",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_RECON_FAIL",
                },
                "received_at": "2026-07-16 10:13:00",
            }

            failed_reconciliation = {
                "committed": True,
                "post_write_verified": False,
                "reconciliation_persisted": False,
                "blocked_reasons": ["post write verification failed"],
            }
            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
                mock.patch.object(gui, "mark_chejan_reconciliation_state", return_value=failed_reconciliation),
            ):
                result = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )

            self.assertTrue(result["manual_reconciliation_required"], result)
            self.assertFalse(result["reconciliation_persisted"], result)
            self.assertEqual(["post write verification failed"], result["reconciliation_persist_failed_reasons"])

    def test_other_manual_reconciliation_reason_is_preserved_after_chejan_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                    "manual_reconciliation_required": True,
                    "manual_reconciliation_source": "runtime_commit_review",
                    "manual_reconciliation_reason": "runtime review is still required",
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_OTHER_RECON",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_OTHER_RECON",
                },
                "received_at": "2026-07-16 10:14:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                result = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )

            self.assertNotIn("manual_reconciliation_required", result)
            order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertTrue(order["manual_reconciliation_required"])
            self.assertFalse(order["chejan_reconciliation_required"])
            self.assertEqual("runtime_commit_review", order["manual_reconciliation_source"])

    def test_sell_full_fill_with_existing_position_decreases_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order(side="SELL")
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            self._write_open_position(positions_path, quantity=10, average_price=1000)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_SELL_FULL",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "1",
                    "913": "泥닿껐",
                    "900": "10",
                    "911": "10",
                    "902": "0",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_SELL_FULL",
                },
                "received_at": "2026-07-16 10:15:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                result = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"},
                )

            self.assertNotIn("manual_reconciliation_required", result)
            order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            position = json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]
            self.assertEqual("FILLED", order["status"])
            self.assertEqual(0, position["quantity"])

    def test_sell_late_failed_fill_reprocess_does_not_double_decrease_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order(side="SELL")
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            self._write_open_position(positions_path, quantity=10, average_price=1000)
            fills_path.write_text("{bad json", encoding="utf-8")
            event_a = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_SELL_LATE",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "1",
                    "913": "체결",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_SELL_LATE_A",
                },
                "received_at": "2026-07-16 10:17:00",
            }
            event_b = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_SELL_LATE",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "1",
                    "913": "체결",
                    "900": "10",
                    "911": "5",
                    "902": "5",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_SELL_LATE_B",
                },
                "received_at": "2026-07-16 10:18:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                first = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                fills_path.write_text(json.dumps({"version": 1, "updated_at": None, "fills": []}), encoding="utf-8")
                second = gui.handle_kiwoom_raw_chejan_event(event_b, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})
                retry_first = gui.handle_kiwoom_raw_chejan_event(event_a, {"kiwoom_api_live_event": True, "live_event_source": "KiwoomApi.raw_chejan_received"})

            self.assertTrue(first["manual_reconciliation_required"], first)
            self.assertNotIn("manual_reconciliation_required", second)
            self.assertEqual("later_cumulative_fill_already_applied", retry_first["position_result"]["position_stage"])
            order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            position = json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]
            pending = [item for item in order["chejan_reconciliation_items"] if item.get("required") is True]
            self.assertEqual([], pending)
            self.assertFalse(order["manual_reconciliation_required"])
            self.assertEqual(2, len(json.loads(fills_path.read_text(encoding="utf-8"))["fills"]))
            self.assertEqual(5, position["quantity"])

    def test_sell_fill_without_existing_position_persists_reconciliation_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            record = self._order_queued_record_for_send_order(side="SELL")
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_SELL_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "1",
                    "913": "체결",
                    "900": "10",
                    "911": "3",
                    "902": "7",
                    "910": "1000",
                    "901": "1000",
                    "909": "EXEC_NO_SELL_NO_POSITION",
                },
                "received_at": "2026-07-16 10:05:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                result = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertTrue(result["manual_reconciliation_required"], result)
            self.assertTrue(result["fill_result"]["fill_recorded"], result)
            self.assertFalse(result["position_result"]["position_updated"], result)
            order = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]
            self.assertTrue(order["manual_reconciliation_required"])
            self.assertEqual("POSITION_UPDATE", order["chejan_reconciliation_failed_stage"])
            self.assertIn("SELL requires an existing open position", order["chejan_reconciliation_blocked_reasons"])

    def test_raw_chejan_source_string_without_live_context_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            record = self._order_queued_record_for_send_order()
            record.update(
                {
                    "status": "SEND_CALL_ACCEPTED",
                    "send_order_called": True,
                    "broker_api_called": True,
                    "broker_call_executed": True,
                    "send_call_result_known": True,
                    "send_call_accepted": True,
                }
            )
            self._write_queue_for_send_order(queue_path, record)
            window = self._window_for_queue_commit()
            raw_event = {
                "source": "KiwoomApi.raw_chejan_received",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": "BRK_1",
                    "9001": "A003550",
                    "302": "LG",
                    "907": "2",
                    "913": "ACCEPT",
                    "900": "10",
                    "911": "0",
                    "902": "10",
                    "910": "0",
                    "901": "1000",
                },
                "received_at": "2026-07-16 10:00:00",
            }

            with mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path):
                result = gui.AutoTradeSettingWindow.handle_raw_chejan_event(window, raw_event)

            self.assertFalse(result["recorded"], result)
            self.assertEqual("chejan_record", result["stage"])
            self.assertIn("Chejan event record confirmation is required", result["blocked_reasons"])
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertFalse(data["orders"][0].get("chejan_events"))

    def test_balance_chejan_records_broker_holding_without_queue_fill_or_position_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "order_queue.json"
            fills_path = Path(tmp) / "fills.json"
            positions_path = Path(tmp) / "positions.json"
            broker_holdings_path = Path(tmp) / "broker_holdings.json"
            self._write_queue_for_send_order(queue_path, self._order_queued_record_for_send_order())
            self._write_open_position(positions_path, quantity=3, average_price=1000)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "1",
                "fid_values": {
                    "9201": "12345678",
                    "9001": "A003550",
                    "930": "3",
                    "933": "3",
                    "931": "1000",
                    "932": "3000",
                    "10": "1000",
                    "8019": "0",
                },
                "received_at": "2026-07-16 10:02:00",
            }

            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue_path),
                mock.patch.object(gui, "FILLS_PATH", fills_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
                mock.patch.object(gui, "BROKER_HOLDINGS_PATH", broker_holdings_path),
            ):
                result = gui.handle_kiwoom_raw_chejan_event(
                    raw_event,
                    {
                        "kiwoom_api_live_event": True,
                        "live_event_source": "KiwoomApi.raw_chejan_received",
                    },
                )

            self.assertTrue(result["recorded"], result)
            self.assertTrue(result["balance_event_received"])
            self.assertFalse(result["manual_reconciliation_required"])
            self.assertEqual("CONSISTENT", result["reconciliation_status"])
            holdings = json.loads(broker_holdings_path.read_text(encoding="utf-8"))["holdings"]
            self.assertEqual(1, len(holdings))
            self.assertEqual(3, holdings[0]["holding_quantity"])
            self.assertFalse(fills_path.exists())
            self.assertEqual(3, json.loads(positions_path.read_text(encoding="utf-8"))["positions"][0]["quantity"])
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertFalse(data["orders"][0].get("chejan_events"))

    def test_main_window_balance_chejan_records_without_auto_trade_setting_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker_holdings_path = Path(tmp) / "broker_holdings.json"
            positions_path = Path(tmp) / "positions.json"
            self._write_open_position(positions_path, quantity=3, average_price=1000)
            main = main_gui.MainWindow.__new__(main_gui.MainWindow)
            raw_event = {
                "source": "kiwoom_chejan",
                "gubun": "1",
                "fid_values": {
                    "9201": "12345678",
                    "9001": "A003550",
                    "302": "LG",
                    "930": "3",
                    "933": "3",
                    "931": "1000",
                    "932": "3000",
                    "10": "1000",
                    "8019": "0",
                },
                "received_at": "2026-07-16 10:03:00",
            }

            with (
                mock.patch.object(gui, "BROKER_HOLDINGS_PATH", broker_holdings_path),
                mock.patch.object(gui, "POSITIONS_PATH", positions_path),
            ):
                main_gui.MainWindow.on_kiwoom_raw_chejan_received(main, raw_event)

            result = main.last_chejan_record_result
            self.assertTrue(result["recorded"], result)
            self.assertEqual("CONSISTENT", result["reconciliation_status"])
            self.assertEqual(1, len(json.loads(broker_holdings_path.read_text(encoding="utf-8"))["holdings"]))


if __name__ == "__main__":
    unittest.main()
