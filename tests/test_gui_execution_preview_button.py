# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
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
    def __init__(self, *, connected: bool = True, accounts: list[str] | None = None) -> None:
        self.connected = connected
        self.accounts = list(accounts or [])

    def is_connected(self) -> bool:
        return self.connected

    def account_numbers(self) -> list[str]:
        if not self.connected:
            return []
        return list(self.accounts)


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
        send_order_stub.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
