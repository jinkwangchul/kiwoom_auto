# -*- coding: utf-8 -*-
"""
gui_auto_trade_setting_window.py

자동매매설정창 전용 모듈.
- AutoTradeSettingWindow
- 자동매매설정창에서 직접 쓰는 상태/청산/등록해제 헬퍼
- 자동매매설정창 전용 소형 다이얼로그

주의:
- MainWindow 본체와 StockRegisterWindow 본체는 포함하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from PyQt5 import sip
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PyQt5.QtCore import Qt, QDate, QTime, QTimer, QItemSelectionModel, QRect, QSize, QEvent, QSignalBlocker
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)
from event_journal_production import append_production_event
from pnl_ui_refresh import PNL_REFRESH_INTERVAL_MS, project_current_stock_pnl
from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
)
from kiwoom_screen_allocator import project_order_default_screen_no

LOGGER = logging.getLogger(__name__)
ROUTINE_INLINE_EDIT_STYLE = """
QLineEdit {
    border: none;
    background: transparent;
    padding: 0px;
    margin: 0px;
}
QLineEdit:focus {
    background: transparent;
}
"""


def _setting_leaf_changes(
    before: object,
    after: object,
    *,
    prefix: str,
) -> list[dict[str, object]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, object]] = []
        for key in sorted(set(before) | set(after)):
            changes.extend(
                _setting_leaf_changes(
                    before.get(key),
                    after.get(key),
                    prefix=f"{prefix}.{key}" if prefix else str(key),
                )
            )
        return changes
    if before == after:
        return []
    return [{"field_key": prefix, "before": before, "after": after}]


from gui_styles import (
    PLAIN_HEADER_GRID_COLOR_PROPERTY,
    PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY,
    REGISTERED_STOCK_STATUS_GRID_COLOR,
    apply_plain_table_header,
    registered_stock_status_table_stylesheet,
)
from gui_toast import show_toast
from gui_common_utils import safe_int_value, sanitize_path_part
from gui_stock_data import append_base_stock, active_routine_for_stock, stock_runtime_dir_for_routine
from gui_stock_instance_chart_window import open_stock_instance_chart
from gui_order_utils import (
    directional_value_color,
    format_signed_percent,
    pending_order_side_quantities,
    order_value,
    order_status_display,
    order_side_display,
    format_signed_money,
    build_order_rows,
    build_order_timeline_text,
    filter_orders_by_range,
    build_grouped_order_timeline_text,
    settlement_summary_text,
    date_range_for_mode,
    filter_orders_by_dates,
    today_orders,
    build_current_status_rows,
    build_full_trade_export_text,
    numeric_order_value,
    order_datetime,
    parse_order_datetime_value,
    order_sort_key,
    summarize_orders,
)
from gui_schedule_utils import schedule_change_log_text, schedule_status_suffix
from gui_schedule_window import (
    ScheduleOperationDialog,
    ScheduleTradeManagementDialog,
)
from gui_config_utils import (
    default_config,
    ensure_stock_runtime_files,
)
from gui_search_stock_register_dialog import SearchStockRegisterDialog
from gui_auto_trade_utils import auto_trade_unregister_category
from gui_review_utils import (
    build_review_required_item,
    safe_float_value,
)
from gui_routine_assign_utils import (
    build_routine_assign_result_lines,
    build_routine_assign_status_text,
    build_routine_unassign_result_lines,
    build_routine_unassign_status_text,
)
from gui_routine_guard import routine_action_guard_info
from gui_routine_policy import (
    routine_action_reasons_for_stock,
    classify_routine_assign_targets,
    can_unassign_active_routine_from_stock,
)
from runtime_io import (
    read_json_dict,
    read_orders_data,
    write_json_if_missing,
)
from gui_auto_trade_runtime import (
    all_registered_stock_dirs,
    now_text,
    parse_stock_folder_name,
    get_stock_dirs_in_routine,
)


def _apply_routine_inline_edit_style(editor: QLineEdit, table) -> None:
    editor.setFrame(False)
    editor.setStyleSheet(ROUTINE_INLINE_EDIT_STYLE)
    editor.setFont(table.font())
    editor.setContentsMargins(0, 0, 0, 0)


class _AutoTradeRoutineInstanceNameEdit(QLineEdit):
    def __init__(self, window: "AutoTradeSettingWindow") -> None:
        super().__init__(window.routine_table.viewport())
        self.window = window

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.window.finish_routine_instance_name_edit(save=True)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.window.finish_routine_instance_name_edit(save=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.window.finish_routine_instance_name_edit(save=True)
        super().focusOutEvent(event)


class InstanceStockSearchRegisterDialog(QDialog):
    """Instance-scoped stock search and one-stock registration window."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        instance_metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)
        self.instance_metadata = dict(instance_metadata or {})
        self.setWindowTitle(self._window_title())
        self.resize(520, 420)

        self.search_input = QLineEdit(self)
        self.search_input.setObjectName("instanceStockSearchInput")
        self.btn_search = QPushButton("검색", self)
        self.btn_search.setObjectName("instanceStockSearchButton")
        self.result_table = QTableWidget(self)
        self.result_table.setObjectName("instanceStockSearchResultTable")
        self.btn_register = QPushButton("등록", self)
        self.btn_register.setObjectName("instanceStockRegisterButton")
        self.btn_close = QPushButton("닫기", self)
        self.btn_close.setObjectName("instanceStockRegisterCloseButton")
        self._result_sort_column = -1
        self._result_sort_order = Qt.AscendingOrder

        self._setup_ui()
        self.search_input.textChanged.connect(self.search_stocks)
        self.search_input.returnPressed.connect(
            lambda: self.search_stocks(notify_empty=True)
        )
        self.btn_search.clicked.connect(lambda: self.search_stocks(notify_empty=True))
        self.result_table.itemDoubleClicked.connect(self.on_result_item_double_clicked)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_table.customContextMenuRequested.connect(
            self.on_result_table_context_menu
        )
        self.result_table.itemSelectionChanged.connect(
            self._update_register_button_enabled
        )
        self.result_table.horizontalHeader().sectionClicked.connect(
            self.on_result_header_clicked
        )
        self.btn_register.clicked.connect(
            lambda _checked=False: self.register_selected_result_rows()
        )
        self.btn_close.clicked.connect(self.close)
        self.search_stocks()

    def _window_title(self) -> str:
        for key in ("instance_name", "display_name", "routine_name"):
            display_name = str(self.instance_metadata.get(key, "") or "").strip()
            if display_name:
                return f"{display_name} - 종목등록"
        return "종목등록"

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("검색어", self))
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.btn_search)

        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["종목코드", "종목명", "분류"])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_table.setSortingEnabled(False)
        self.result_table.horizontalHeader().setSortIndicatorShown(True)
        self.result_table.setStyleSheet(
            """
            QTableWidget::item:selected {
                background: #dbeafe;
                color: #111827;
            }
            QTableWidget::item:selected:active {
                background: #dbeafe;
                color: #111827;
            }
            QTableWidget::item:selected:!active {
                background: #dbeafe;
                color: #111827;
            }
            """
        )

        main_layout.addLayout(search_layout)
        main_layout.addWidget(self.result_table)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_register)
        button_layout.addWidget(self.btn_close)
        main_layout.addLayout(button_layout)
        self._update_register_button_enabled()

    def _update_register_button_enabled(self) -> None:
        has_selection = bool(self.result_table.selectionModel().selectedRows())
        self.btn_register.setEnabled(has_selection)

    def search_stocks(self, *_args, notify_empty: bool = False) -> None:
        keyword_text = self.search_input.text().strip().lower()
        keywords = [
            part.strip()
            for part in re.split(r"[,，、]+", keyword_text)
            if part.strip()
        ]
        if not keywords:
            self.result_table.setRowCount(0)
            self._update_register_button_enabled()
            return

        matches: list[dict[str, str]] = []
        seen_codes: set[str] = set()
        library = load_stock_library()

        def stock_matches(stock: dict[str, str], keyword: str) -> bool:
            searchable_values = [
                str(stock.get("code", "") or "").strip().lower(),
                str(stock.get("name", "") or "").strip().lower(),
                str(stock.get("chosung", "") or "").strip().lower(),
            ]
            return any(keyword in value for value in searchable_values)

        for keyword in keywords:
            for stock in library:
                code = str(stock.get("code", "") or "").strip()
                name = str(stock.get("name", "") or "").strip()
                if not code or not name or code in seen_codes:
                    continue
                if stock_matches(stock, keyword):
                    matches.append({"code": code, "name": name})
                    seen_codes.add(code)

        self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(matches))
        for row, stock in enumerate(matches):
            values = (
                stock["code"],
                stock["name"],
                self._classification_text(stock["code"], stock["name"]),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter if col == 0 else Qt.AlignLeft | Qt.AlignVCenter)
                item.setData(Qt.UserRole, value)
                self.result_table.setItem(row, col, item)
        self._apply_result_sort()
        self._update_register_button_enabled()
        if notify_empty and not matches:
            self._toast("검색 결과가 없습니다.")

    def on_result_header_clicked(self, column: int) -> None:
        if column == self._result_sort_column:
            self._result_sort_order = (
                Qt.DescendingOrder
                if self._result_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._result_sort_column = column
            self._result_sort_order = Qt.AscendingOrder
        self._apply_result_sort()

    def _apply_result_sort(self) -> None:
        if self._result_sort_column < 0:
            self.result_table.setSortingEnabled(False)
            return
        self.result_table.setSortingEnabled(True)
        self.result_table.horizontalHeader().setSortIndicator(
            self._result_sort_column,
            self._result_sort_order,
        )
        self.result_table.sortItems(
            self._result_sort_column,
            self._result_sort_order,
        )

    def _toast(self, message: str) -> None:
        show_toast(self, message)

    def _target_instance(self) -> tuple[str, str, str, str] | None:
        instance_id = str(self.instance_metadata.get("instance_id", "") or "").strip()
        instance_name = str(self.instance_metadata.get("instance_name", "") or "").strip()
        definition_id = str(self.instance_metadata.get("definition_id", "") or "").strip()
        routine_type = str(self.instance_metadata.get("definition_name", "") or "").strip()
        if self._is_unassigned_target() and instance_name:
            return "", instance_name, "", routine_type or instance_name
        if not all((instance_id, instance_name, definition_id, routine_type)):
            return None
        return instance_id, instance_name, definition_id, routine_type

    def _is_unassigned_target(self) -> bool:
        return str(self.instance_metadata.get("target_kind", "") or "").strip() == "unassigned"

    def _registered_stock(self, code: str) -> dict[str, object] | None:
        clean_code = normalize_stock_code(code)
        for stock in read_base_stocks():
            if normalize_stock_code(str(stock.get("code", "") or "")) == clean_code:
                return dict(stock)
        return None

    def _classification_text(self, code: str, name: str) -> str:
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
            state = read_json_dict(stock_dir / "state.json")
            if is_review_required_state(state):
                return "검토관리"
        except Exception:
            pass

        stock = self._registered_stock(code)
        if stock is None:
            return "등록가능" if self._is_unassigned_target() else "등록대기"

        assigned_instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if not assigned_instance_id:
            return "등록대기"

        current_instance_id = str(
            self.instance_metadata.get("instance_id", "") or ""
        ).strip()
        if assigned_instance_id == current_instance_id:
            routine_name = str(
                self.instance_metadata.get("instance_name", "") or ""
            ).strip()
            return routine_name or assigned_instance_id

        for instance in load_persisted_routine_instances():
            if str(getattr(instance, "instance_id", "") or "").strip() == assigned_instance_id:
                routine_name = str(getattr(instance, "display_name", "") or "").strip()
                return routine_name or assigned_instance_id
        return assigned_instance_id

    def _refresh_parent_views(self) -> None:
        parent = persistent_feature_owner(self)
        refresh_assignment_views = getattr(
            parent,
            "refresh_auto_trade_assignment_views",
            None,
        )
        if callable(refresh_assignment_views):
            try:
                refresh_assignment_views()
                return
            except Exception:
                LOGGER.exception("Failed to refresh assignment views")
        if parent is not None and hasattr(parent, "refresh_all"):
            try:
                parent.refresh_all()
                return
            except Exception:
                LOGGER.exception("Failed to refresh auto-trade setting window")
        if parent is not None and hasattr(parent, "load_routine_table"):
            try:
                parent.load_routine_table()
            except Exception:
                LOGGER.exception("Failed to refresh routine table")
        if parent is not None and hasattr(parent, "load_selected_routine_stocks"):
            try:
                parent.load_selected_routine_stocks()
            except Exception:
                LOGGER.exception("Failed to refresh stock table")

    def on_result_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        self.register_or_assign_result_row(item.row())

    def on_result_table_context_menu(self, pos) -> None:
        menu = QMenu(self)
        select_all_action = menu.addAction("전체선택")
        clear_selection_action = menu.addAction("선택해제")
        register_action = menu.addAction("선택등록")
        unregister_action = menu.addAction("등록해제")
        selected_rows = self.result_table.selectionModel().selectedRows()
        has_selection = bool(selected_rows)
        unregister_action.setEnabled(
            self._has_selected_routine_registered_stock()
        )
        register_action.setEnabled(has_selection)
        clear_selection_action.setEnabled(has_selection)
        select_all_action.triggered.connect(lambda _checked=False: self.result_table.selectAll())
        clear_selection_action.triggered.connect(lambda _checked=False: self.result_table.clearSelection())
        register_action.triggered.connect(lambda _checked=False: self.register_selected_result_rows())
        unregister_action.triggered.connect(lambda _checked=False: self.unregister_selected_result_rows())
        menu.exec_(self.result_table.viewport().mapToGlobal(pos))

    def _result_stock_at_row(self, row: int) -> tuple[str, str] | None:
        code_item = self.result_table.item(row, 0)
        name_item = self.result_table.item(row, 1)
        if code_item is None or name_item is None:
            return None
        code = normalize_stock_code(code_item.text())
        name = name_item.text().strip()
        return code, name

    def _selected_result_stocks(self) -> list[tuple[str, str]]:
        selection_model = self.result_table.selectionModel()
        if selection_model is None:
            return []
        selected: list[tuple[str, str]] = []
        seen_codes: set[str] = set()
        for index in selection_model.selectedRows():
            stock = self._result_stock_at_row(index.row())
            if stock is None:
                continue
            code, name = stock
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            selected.append((code, name))
        return selected

    def _is_review_required_stock(self, code: str, name: str) -> bool:
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
            state = read_json_dict(stock_dir / "state.json")
        except Exception:
            return False
        return is_review_required_state(state)

    def _registered_routine_name_for_stock(self, code: str, name: str) -> str:
        stock = self._registered_stock(code)
        if stock is None:
            return ""
        routines = stock.get("routines", [])
        if isinstance(routines, list):
            active_routines = single_routine_list(routines)
            if active_routines:
                return str(active_routines[0]).strip()
        else:
            routine_name = str(routines or "").strip()
            if routine_name:
                return routine_name

        assigned_instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if not assigned_instance_id:
            return ""

        current_instance_id = str(
            self.instance_metadata.get("instance_id", "") or ""
        ).strip()
        if assigned_instance_id == current_instance_id:
            routine_name = str(
                self.instance_metadata.get("instance_name", "") or ""
            ).strip()
            return routine_name or assigned_instance_id

        for instance in load_persisted_routine_instances():
            if str(getattr(instance, "instance_id", "") or "").strip() == assigned_instance_id:
                routine_name = str(getattr(instance, "display_name", "") or "").strip()
                return routine_name or assigned_instance_id
        return assigned_instance_id

    def _has_selected_routine_registered_stock(self) -> bool:
        for code, name in self._selected_result_stocks():
            if self._is_review_required_stock(code, name):
                continue
            if self._registered_routine_name_for_stock(code, name):
                return True
        return False

    def _find_result_row_by_stock_code(self, code: str) -> int:
        target_code = normalize_stock_code(code)
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, 0)
            if item is None:
                continue
            if normalize_stock_code(item.text()) == target_code:
                return row
        return -1

    def _selected_result_stock_codes(self) -> set[str]:
        selected_codes: set[str] = set()
        selection_model = self.result_table.selectionModel()
        if selection_model is None:
            return selected_codes
        for index in selection_model.selectedRows():
            item = self.result_table.item(index.row(), 0)
            if item is not None:
                selected_codes.add(normalize_stock_code(item.text()))
        return selected_codes

    def _restore_result_selection(self, selected_codes: set[str]) -> None:
        if not selected_codes:
            return
        self.result_table.clearSelection()
        for code in selected_codes:
            row = self._find_result_row_by_stock_code(code)
            if row >= 0:
                self.result_table.selectRow(row)

    def _refresh_classification_for_stock(self, code: str) -> bool:
        row = self._find_result_row_by_stock_code(code)
        if row < 0:
            return False
        stock = self._result_stock_at_row(row)
        if stock is None:
            return False
        stock_code, stock_name = stock
        selected_codes = self._selected_result_stock_codes()
        classification = self._classification_text(stock_code, stock_name)
        item = self.result_table.item(row, 2)
        if item is None:
            item = QTableWidgetItem()
            self.result_table.setItem(row, 2, item)
        item.setText(classification)
        item.setData(Qt.UserRole, classification)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._apply_result_sort()
        self._restore_result_selection(selected_codes)
        return True

    def unregister_selected_result_rows(self) -> bool:
        selected = self._selected_result_stocks()
        if not selected:
            self._toast("등록해제할 종목을 선택하세요.")
            return False

        allowed: list[tuple[str, str, str]] = []
        skipped = 0
        blocked: list[tuple[str, str, list[str]]] = []
        seen_codes: set[str] = set()
        for code, name in selected:
            if code in seen_codes:
                continue
            seen_codes.add(code)
            if self._is_review_required_stock(code, name):
                skipped += 1
                continue
            can_unassign, routine_name, reasons = can_unassign_active_routine_from_stock(
                code,
                name,
            )
            routine_name = str(routine_name or "").strip()
            if not routine_name:
                skipped += 1
                continue
            if can_unassign:
                allowed.append((code, name, routine_name))
            else:
                blocked.append((code, name, list(reasons)))

        if not allowed:
            if blocked:
                self._toast(f"등록해제 차단 {len(blocked)}건")
            else:
                self._toast("등록해제할 종목이 없습니다.")
            return False

        succeeded: list[str] = []
        succeeded_names: list[str] = []
        failed: list[str] = []
        for code, name, _routine_name in allowed:
            if update_base_stock_routines(code, name, []):
                ensure_single_real_trade_routine_for_stock(code, name)
                succeeded.append(code)
                succeeded_names.append(name)
            else:
                failed.append(code)

        if succeeded:
            self._refresh_parent_views()
            for code in succeeded:
                self._refresh_classification_for_stock(code)

        parts: list[str] = []
        if succeeded:
            self._toast(f"등록해제 {len(succeeded)}건 | {', '.join(succeeded_names)}")
            return True
        if failed:
            parts.append(f"처리불가 {len(failed)}건")
        self._toast(" | ".join(parts) if parts else "등록해제할 종목이 없습니다.")
        return False

    def _valid_library_stock(self, code: str, name: str) -> bool:
        library_stock = find_library_stock_by_code(code)
        return bool(
            library_stock is not None
            and library_stock.get("name", "").strip() == name
            and is_valid_stock_code(code)
        )

    def _assignment_block_reason(self, code: str, name: str) -> str:
        can_process, guard_info = routine_action_reasons_for_stock(
            code,
            name,
            allow_unassigned=True,
        )
        if can_process:
            return ""
        reasons = guard_info.get("reasons", [])
        return ", ".join(str(reason) for reason in reasons) if reasons else "처리할 수 없는 종목입니다."

    def _assign_stock_to_target_instance(
        self,
        code: str,
        name: str,
        target: tuple[str, str, str, str],
        *,
        needs_registration: bool,
    ) -> bool:
        instance_id, instance_name, definition_id, routine_type = target
        if needs_registration and not append_base_stock(code, name):
            return False

        repo = StockRepository(PROJECT_ROOT)
        if not update_base_stock_routine_instance(
            code,
            name,
            instance_id=instance_id,
            instance_name=instance_name,
            definition_id=definition_id,
            routine_type=routine_type,
        ):
            return False

        try:
            stock_dir = repo.ensure_stock_folder(code, name, routine=routine_type)
            ensure_single_real_trade_routine_for_stock(code, name, routine_type)
        except Exception:
            LOGGER.exception("Failed to prepare stock assignment files")
            return False

        append_changelog(
            "UPDATE",
            "config.json",
            f"검색형 종목등록 지정: {code},{name}({instance_name})",
        )
        return True

    def _register_stock_to_unassigned(
        self,
        code: str,
        name: str,
        *,
        needs_registration: bool,
    ) -> bool:
        if not needs_registration:
            return False
        return append_base_stock(code, name)

    def _summary_toast_text(self, counts: dict[str, int]) -> str:
        labels = (
            ("new", "등록"),
            ("moved", "이동"),
            ("duplicate", "중복"),
            ("blocked", "차단"),
            ("move_cancelled", "등록 취소"),
            ("failed", "처리불가"),
            ("invalid", "처리불가"),
        )
        parts = [
            f"{label} {int(counts.get(key, 0))}건"
            for key, label in labels
            if int(counts.get(key, 0)) > 0
        ]
        return " | ".join(parts) if parts else "처리할 종목이 없습니다."

    def register_selected_result_rows(self) -> bool:
        selected_rows = sorted(
            {index.row() for index in self.result_table.selectionModel().selectedRows()}
        )
        if not selected_rows:
            self._toast("등록할 종목을 선택하세요.")
            return False

        target = self._target_instance()
        if target is None:
            self._toast("대상 루틴 정보를 확인하지 못했습니다.")
            return False
        instance_id = target[0]

        new_targets: list[tuple[str, str, bool]] = []
        move_targets: list[tuple[str, str]] = []
        counts = {
            "new": 0,
            "duplicate": 0,
            "moved": 0,
            "move_cancelled": 0,
            "blocked": 0,
            "failed": 0,
            "invalid": 0,
        }
        seen_codes: set[str] = set()
        unassigned_target = self._is_unassigned_target()

        for row in selected_rows:
            result_stock = self._result_stock_at_row(row)
            if result_stock is None:
                counts["invalid"] += 1
                continue
            code, name = result_stock
            if not code or code in seen_codes or not self._valid_library_stock(code, name):
                counts["invalid"] += 1
                continue
            seen_codes.add(code)

            stock = self._registered_stock(code)
            assigned_instance_id = (
                str(stock.get("assigned_routine_instance_id", "") or "").strip()
                if stock is not None
                else ""
            )
            if unassigned_target:
                if stock is None:
                    new_targets.append((code, name, True))
                elif assigned_instance_id:
                    counts["blocked"] += 1
                else:
                    counts["duplicate"] += 1
                continue
            if assigned_instance_id == instance_id:
                counts["duplicate"] += 1
            elif assigned_instance_id:
                if self._assignment_block_reason(code, name):
                    counts["blocked"] += 1
                else:
                    move_targets.append((code, name))
            else:
                new_targets.append((code, name, stock is None))

        move_allowed = False
        if move_targets:
            answer = QMessageBox.question(
                self,
                "선택 종목 처리 결과",
                "선택 종목 처리 결과\n\n"
                f"등록 {len(new_targets)}건 | 이동 {len(move_targets)}건 | 중복 {counts['duplicate']}건 | 차단 {counts['blocked']}건\n\n"
                f"다른 루틴에 등록된 {len(move_targets)}종목을 현재 루틴으로 이동하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            move_allowed = answer == QMessageBox.Yes
            if not move_allowed:
                counts["move_cancelled"] = len(move_targets)

        changed = False
        changed_codes: list[str] = []
        for code, name, needs_registration in new_targets:
            if unassigned_target:
                assigned = self._register_stock_to_unassigned(
                    code,
                    name,
                    needs_registration=needs_registration,
                )
            else:
                assigned = self._assign_stock_to_target_instance(
                    code,
                    name,
                    target,
                    needs_registration=needs_registration,
                )
            if assigned:
                counts["new"] += 1
                changed = True
                changed_codes.append(code)
            else:
                counts["failed"] += 1

        if move_allowed:
            for code, name in move_targets:
                if self._assign_stock_to_target_instance(
                    code,
                    name,
                    target,
                    needs_registration=False,
                ):
                    counts["moved"] += 1
                    changed = True
                    changed_codes.append(code)
                else:
                    counts["failed"] += 1

        if changed:
            self._refresh_parent_views()
            for code in changed_codes:
                self._refresh_classification_for_stock(code)
        if counts.get("move_cancelled") and not counts.get("new") and not changed:
            self._toast("등록 취소")
        else:
            self._toast(self._summary_toast_text(counts))
        return changed

    def register_or_assign_result_row(self, row: int) -> bool:
        result_stock = self._result_stock_at_row(row)
        if result_stock is None:
            self._toast("처리할 종목을 확인하지 못했습니다.")
            return False

        code, name = result_stock
        target = self._target_instance()
        if target is None:
            self._toast("대상 루틴 정보를 확인하지 못했습니다.")
            return False
        instance_id, instance_name, definition_id, routine_type = target

        if not self._valid_library_stock(code, name):
            self._toast("종목 정보를 확인하지 못했습니다.")
            return False

        stock = self._registered_stock(code)
        assigned_instance_id = (
            str(stock.get("assigned_routine_instance_id", "") or "").strip()
            if stock is not None
            else ""
        )
        if self._is_unassigned_target():
            if stock is not None and not assigned_instance_id:
                self._toast("중복 1건")
                return False
            if assigned_instance_id:
                self._toast("차단 1건")
                return False
            if not self._register_stock_to_unassigned(
                code,
                name,
                needs_registration=stock is None,
            ):
                self._toast("종목 등록에 실패했습니다.")
                return False
            self._refresh_parent_views()
            self._refresh_classification_for_stock(code)
            self._toast("등록 1건")
            return True

        if assigned_instance_id == instance_id:
            self._toast("이미 같은 루틴에 지정된 종목입니다.")
            return False

        if assigned_instance_id and assigned_instance_id != instance_id:
            reason_text = self._assignment_block_reason(code, name)
            if reason_text:
                self._toast(reason_text)
                return False
            answer = QMessageBox.question(
                self,
                "종목등록",
                "이 종목은 다른 루틴에 지정되어 있습니다.\n"
                "현재 루틴으로 지정을 변경하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._toast("등록 취소")
                return False

        newly_registered = False
        if stock is None:
            newly_registered = True

        if not self._assign_stock_to_target_instance(
            code,
            name,
            target,
            needs_registration=newly_registered,
        ):
            if newly_registered:
                self._toast("종목 등록에 실패했습니다.")
            else:
                self._toast("종목 지정에 실패했습니다.")
            return False

        self._refresh_parent_views()
        self._refresh_classification_for_stock(code)
        if newly_registered:
            self._toast("종목 등록 및 지정이 완료됐습니다.")
        elif assigned_instance_id:
            self._toast("종목 지정이 변경됐습니다.")
        else:
            self._toast("종목 지정이 완료됐습니다.")
        return True


def auto_trade_register_historical_stock_to_original_instance(
    parent: QWidget,
    metadata: dict[str, object],
) -> bool:
    """Register a historical stock back to its original routine instance."""
    if not bool(metadata.get("is_historical", False)):
        return False

    code = normalize_stock_code(str(metadata.get("stock_code", "") or ""))
    name = str(metadata.get("display_name", "") or "").strip()
    instance_id = str(metadata.get("instance_id", "") or "").strip()
    instance_name = str(metadata.get("instance_name", "") or "").strip()
    definition_id = str(metadata.get("definition_id", "") or "").strip()
    definition_name = str(metadata.get("definition_name", "") or "").strip()
    if not all((code, name, instance_id, instance_name, definition_id, definition_name)):
        show_toast(parent, "처리할 수 없는 종목입니다.")
        return False

    dialog = InstanceStockSearchRegisterDialog(
        parent,
        instance_metadata={
            "instance_id": instance_id,
            "instance_name": instance_name,
            "definition_id": definition_id,
            "definition_name": definition_name,
        },
    )
    try:
        def emit_toast(message: str) -> None:
            show_toast(parent, message)

        if not dialog._valid_library_stock(code, name):
            emit_toast("종목 정보를 확인하지 못했습니다.")
            return False

        stock = dialog._registered_stock(code)
        assigned_instance_id = (
            str(stock.get("assigned_routine_instance_id", "") or "").strip()
            if stock is not None
            else ""
        )
        if assigned_instance_id == instance_id:
            emit_toast("이미 같은 루틴에 지정된 종목입니다.")
            return False
        if assigned_instance_id:
            emit_toast("중복 1건")
            return False

        reason_text = dialog._assignment_block_reason(code, name)
        if reason_text:
            emit_toast(reason_text)
            return False

        target = (instance_id, instance_name, definition_id, definition_name)
        if not dialog._assign_stock_to_target_instance(
            code,
            name,
            target,
            needs_registration=stock is None,
        ):
            emit_toast("종목 등록에 실패했습니다.")
            return False

        dialog._refresh_parent_views()
        dialog._refresh_classification_for_stock(code)
        emit_toast(f"등록 1건 | {name}")
        return True
    finally:
        dialog.close()
        dialog.deleteLater()


from gui_base_stock_service import (
    ensure_single_real_trade_routine_for_all_stocks,
    find_library_stock_by_code,
    is_valid_stock_code,
    load_stock_library,
    normalize_base_stock_single_routine_file,
    normalize_stock_code,
    read_base_stocks,
    single_routine_list,
    update_base_stock_routine_instance,
    update_base_stock_routines,
    validate_base_stock_record,
)
from state_policy import (
    auto_trade_status_color,
    auto_trade_status_display,
    auto_trade_status_dot,
    effective_schedule_times,
    minutes_from_hhmm,
    normalize_after_trade_end_status,
    normalize_operation_mode,
    normalized_hhmm_or_empty,
    normalized_hhmmss_or_empty,
    operation_mode_check_text,
    operation_mode_display,
    real_trade_enabled,
    trade_permission_display,
    operation_mode_recalculation_target_status,
    operation_text_and_color,
    schedule_override_enabled,
    scheduled_status_for_now,
    seconds_from_hhmmss,
    start_status_by_operation_mode,
    status_after_operation_mode_change,
    validate_buy_time_range,
    write_global_schedule,
)
from gui_ats_utils import (
    auto_trade_setting_regular_market_active_now,
    manual_ats_active_now,
    manual_ats_enabled_labels,
    manual_ats_session_labels,
)
from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_BADGE_BORDER_COLOR,
    AUTO_TRADE_SETTING_BADGE_HEIGHT,
    AUTO_TRADE_SETTING_BADGE_TEXT_COLOR,
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    auto_trade_setting_badge_stylesheet,
    auto_trade_setting_display_status,
    auto_trade_setting_status_color,
    confirmable_stock_profit_metric,
    create_auto_trade_setting_status_item,
    create_auto_trade_status_item,
    draw_stock_position_metric,
    yes_no_display,
    display_status_text_for_gui,
    profit_loss_value_color,
    ratio_metric_text,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_situation import create_auto_trade_situation_item
from gui_auto_trade_policy import (
    auto_trade_setting_ats_after_regular_blocked,
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_trade_started,
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_no_next_step_notice,
    short_close_method_text,
    compact_operation_time_range,
    operation_policy_section,
    auto_trade_setting_close_timestamp_later,
    auto_trade_setting_early_close_metadata_is_stale,
    clear_early_close_runtime_metadata_only,
    auto_trade_setting_early_close_requested,
    clear_auto_close_runtime_metadata,
    close_method_from_state_or_policy,
    auto_trade_setting_method_text,
    individual_liquidation_policy_from_config,
    effective_liquidation_policy_for_config,
    auto_trade_setting_liquidation_text,
    auto_trade_setting_regular_end_seconds,
    auto_trade_setting_is_after_regular_end,
    auto_trade_setting_has_unresolved_quantity,
    auto_trade_setting_has_buy_pending_problem,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_today_date_text,
    auto_trade_setting_liquidation_completed_today,
    auto_trade_setting_effective_liquidation_method,
    auto_trade_setting_liquidation_result_policy,
    auto_trade_setting_mark_liquidation_result_for_display,
    auto_trade_setting_liquidation_active,
    auto_trade_setting_liquidation_phase_active,
    auto_trade_setting_close_routine_mode_active,
    auto_trade_setting_close_routine_order_allowed,
)
from gui_auto_trade_integrity import (
    unique_review_reasons,
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_required_state,
    is_review_required_stock_dir,
    auto_trade_setting_data_inconsistency_reasons,
    restart_initial_review_reason_for_stock,
    operator_review_location,
    operator_review_reason,
    auto_trade_setting_server_mismatch_detected,
)
from gui_stock_performance_window import open_stock_performance_prototype
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)
from gui_auto_trade_unregister import (
    unregister_selected_auto_trade_stocks,
)
from gui_auto_trade_context_menu import (
    CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR,
    show_auto_trade_stock_context_menu,
)
from gui_auto_trade_selection import (
    clear_current_routine_stock_selection,
    ensure_context_row_selected,
    has_selected_stock,
    has_single_selected_stock,
    select_all_current_routine_stocks,
    selected_stock_dir,
    selected_stock_info,
    selected_stock_infos,
    selected_stock_rows,
)
from gui_auto_trade_close import (
    ProfitLossEarlyCloseDialog,
    auto_trade_apply_selected_individual_liquidation_method,
    auto_trade_apply_selected_early_close,
    auto_trade_apply_selected_early_close_default,
    auto_trade_apply_selected_early_close_profit_loss,
    auto_trade_cancel_selected_early_close,
)
from gui_auto_trade_ats_ops import (
    auto_trade_execute_selected_manual_ats_liquidation,
    auto_trade_save_selected_manual_ats_state,
    auto_trade_selected_manual_ats_liquidation_available,
    auto_trade_selected_manual_ats_state,
    auto_trade_set_selected_manual_ats_flag,
)
from gui_auto_trade_timer import (
    auto_trade_current_runtime_file_signature,
    auto_trade_current_time_policy_minute_key,
    auto_trade_on_runtime_file_timer_tick,
    auto_trade_on_time_policy_gui_timer_tick,
)
from gui_operation_ui_context import refresh_auto_trade_views
from gui_auto_trade_status_ops import (
    OPERATION_EXCLUDED_CONFIG_KEY,
    append_changelog,
    append_stock_log,
    auto_trade_apply_schedule_times_to_targets,
    auto_trade_clear_selected_stock_operation_exclusions,
    auto_trade_finalize_operation_mode_result,
    auto_trade_operation_policy_protected_status,
    auto_trade_recalculate_all_status_by_operation_policy,
    auto_trade_recalculate_stock_status_by_operation_policy,
    auto_trade_reset_schedule_times_for_targets,
    auto_trade_set_selected_stock_operation_exclusions,
    auto_trade_set_stock_operation_exclusion,
    auto_trade_stock_operation_excluded,
    auto_trade_set_selected_operation_mode,
    auto_trade_set_selected_schedule_operation_mode,
    auto_trade_toggle_stock_operation_exclusion,
    auto_trade_update_stock_operation_mode,
    auto_trade_update_stock_status,
    handle_auto_trade_operation_mode_double_click,
)
from gui_auto_trade_run_control import (
    auto_trade_registered_operation_start_targets,
    auto_trade_registered_operation_targets,
    auto_trade_running_registered_operation_targets,
    auto_trade_start_selected_auto_trades,
    auto_trade_start_selected_rows_auto_trades,
    auto_trade_start_status_indicator,
    auto_trade_update_global_operation_button_state,
    startup_recovery_operation_block_message,
    today_global_operation_status as _today_global_operation_status,
)
from operation_policy_gate import read_operation_state
from order_manager import (
    decide_routine_order_for_stock_dir,
    mark_routine_order_accepted_for_stock_dir,
)
from gui_auto_trade_review_ops import (
    auto_trade_open_review_required_window,
)
from gui_review_required_window import (
    _read_central_review_state,
    collect_global_review_required_rows,
)
from gui_auto_trade_table_loader import (
    _selected_instance_stock_dirs,
    auto_trade_load_selected_routine_stocks,
)
from gui_routine_service import (
    ensure_single_real_trade_routine_for_stock,
)
from gui_operation_environment import (
    OperationEnvironmentSettingsDialog,
    TimeComboWidget,
    default_operation_policy,
    read_operation_policy,
    write_operation_policy,
)
from gui_review_required_window import (
    GlobalReviewRequiredWindow,
)
from gui_routine_registry import (
    get_group_dirs as registry_get_group_dirs,
    get_group_records,
    routine_display_name as registry_routine_display_name,
    read_routine_budget,
)
from main_group_projection import build_main_group_projection
from routine_instance_registry import (
    load_persisted_routine_instances,
    load_routine_definitions,
    routine_definition_by_id,
    routine_instance_by_id,
)
from routine_instance_repository import RoutineInstanceRepository
from stock_repository import StockRepository
from group_scope import load_group_scope
from execution_enable_service import commit_execution_enable, preview_execution_enable
from execution_final_send_gate_input_adapter import adapt_final_send_gate_readiness_to_input
from execution_final_send_gate_orchestrator import orchestrate_final_send_gate_preview
from execution_final_send_gate_readiness_policy import evaluate_execution_final_send_gate_readiness
from execution_queue_commit_service import commit_execution_queue_manually
from execution_queue_commit_readiness_policy import evaluate_execution_queue_commit_readiness
from execution_queue_review_to_send_order_preview_adapter import adapt_queue_review_to_send_order_preview
from execution_queue_writer import claim_order_for_dispatch, commit_execution_queue_write
from execution_preview_order_service import preview_execution_for_real_ready_order
from execution_preview_reporter import build_execution_preview_report
from execution_readiness_preview_controller import build_execution_readiness_preview_from_context
from execution_runtime_commit_service import commit_execution_runtime_plan
from execution_runtime_controller import run_execution_runtime_dry_run
from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_commit_service import commit_execution_runtime_file_init_plan
from execution_runtime_file_init_open_policy import evaluate_execution_runtime_file_init_open_policy
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness
from execution_runtime_storage import ExecutionRuntimeStorage
from execution_fill_recorder import find_existing_execution_fill_record, record_execution_fill
from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract
from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call
from kiwoom_send_order_executor import execute_claimed_send_order
from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety
from broker_holding_recorder import record_broker_holding_snapshot
from chejan_event_normalizer import normalize_kiwoom_chejan_event
from chejan_event_recorder import (
    chejan_event_identity,
    existing_chejan_record_result,
    mark_chejan_reconciliation_state,
    record_chejan_event,
)
from chejan_event_review_service import review_chejan_event
from final_send_gate_service import evaluate_final_send_gate
from order_queued_review_service import review_order_queued_record
from position_update_service import update_position_from_fill
from realized_pnl_ledger import record_realized_pnl
from real_order_preflight_service import commit_real_order_preflight, preview_real_order_preflight


ROUTINE_INSTANCE_REQUIRED_MESSAGE = "이 작업을 수행할 대상 루틴을 선택하세요."
ROUTINE_STATUS_DEFAULT = "기본운영"
ROUTINE_TREE_STOCK_TITLE_DISPLAY_CHARS = 7
ROUTINE_TREE_STOCK_TITLE_PREFIX_CHARS = 7
AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR = "#16A34A"
AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR = "#111827"
AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT = AUTO_TRADE_SETTING_BADGE_HEIGHT
AUTO_TRADE_SETTING_TOP_CONTROL_MARGIN = 1
AUTO_TRADE_SETTING_TOP_CONTROL_BODY_SPACING = 2
AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE = "color: #2563eb; font-weight: bold;"
AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA = {
    "category": frozenset({"period", "profit", "average", "efficiency"}),
    "routine": frozenset({"period", "profit", "average", "efficiency"}),
    "stock": frozenset({"period", "profit", "average", "efficiency"}),
}
AUTO_TRADE_SETTING_STOCK_ROW_TEXT_COLOR = "#7E22CE"
AUTO_TRADE_SETTING_HISTORICAL_STOCK_ROW_TEXT_COLOR = "#9CA3AF"
AUTO_TRADE_SETTING_APP_ENV = "KIWOOM_AUTO_APP_ENV"
AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV = (
    "KIWOOM_AUTO_HISTORICAL_STOCK_FIXTURE"
)
AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_CANDIDATES = (
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("005380", "현대차"),
    ("051910", "LG화학"),
    ("068270", "셀트리온"),
    ("055550", "신한지주"),
    ("066570", "LG전자"),
    ("086520", "에코프로"),
    ("105560", "KB금융"),
    ("247540", "에코프로비엠"),
    ("091990", "셀트리온헬스케어"),
    ("293490", "카카오게임즈"),
    ("323410", "카카오뱅크"),
    ("003550", "LG"),
    ("012330", "현대모비스"),
    ("028260", "삼성물산"),
    ("006400", "삼성SDI"),
    ("005930", "삼성전자"),
    ("096770", "SK이노베이션"),
)
AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_PERFORMANCE = (
    {
        "trade_days": 3,
        "realized_profit": 125000.0,
        "profit_rate": 3.25,
        "average": 62500.0,
        "average_rate": 1.63,
        "gross_profit": 125000.0,
        "gross_loss_abs": 39062.5,
        "profit_factor": 3.2,
    },
    {
        "trade_days": 2,
        "realized_profit": -48000.0,
        "profit_rate": -1.40,
        "average": -24000.0,
        "average_rate": -0.70,
        "gross_profit": None,
        "gross_loss_abs": None,
        "profit_factor": 0.0,
    },
    {
        "trade_days": 0,
        "realized_profit": 0.0,
        "profit_rate": 0.0,
        "average": 0.0,
        "average_rate": 0.0,
        "gross_profit": None,
        "gross_loss_abs": None,
        "profit_factor": 0.0,
    },
)
AUTO_TRADE_SETTING_HISTORICAL_MANUAL_AGGREGATION_FIXTURES = (
    {
        "stock_code": "000660",
        "stock_name": "SK하이닉스",
        "performance_fixture": {
            "trade_days": 2,
            "realized_profit": 48000.0,
            "profit_rate": 0.0,
            "average": 24000.0,
            "average_rate": 0.0,
            "gross_profit": 48000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
    {
        "stock_code": "000660",
        "stock_name": "SK하이닉스",
        "performance_fixture": {
            "trade_days": 5,
            "realized_profit": 77000.0,
            "profit_rate": 0.0,
            "average": 15400.0,
            "average_rate": 0.0,
            "gross_profit": 77000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
    {
        "stock_code": "006400",
        "stock_name": "삼성SDI",
        "performance_fixture": {
            "trade_days": 2,
            "realized_profit": 40000.0,
            "profit_rate": 0.0,
            "average": 20000.0,
            "average_rate": 0.0,
            "gross_profit": 40000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
    {
        "stock_code": "006400",
        "stock_name": "삼성SDI",
        "performance_fixture": {
            "trade_days": 1,
            "realized_profit": 20000.0,
            "profit_rate": 0.0,
            "average": 20000.0,
            "average_rate": 0.0,
            "gross_profit": 20000.0,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
        },
    },
)
AUTO_TRADE_SETTING_STOCK_TABLE_COLUMN_WIDTHS = {
    0: 80,    # 코드: 6자리 여유
    1: 205,   # 종목: 13자 기준
    2: 120,   # 운영: 09:30~13:30 표시
    3: 50,    # 현황: 종목 운영 건강도 표시등
    4: 90,    # 상태: 감시/대기, 매수/매도
    5: 80,    # 방식: 루틴, 시장가, 현재가
    6: 120,   # 청산: 10분/시장가, 10분/현재가
    7: 204,   # 보유: 수량 / 총매수금액
    8: 194,   # 가격: 평단가 / 현재가
    9: 199,   # 손익: 손익금 / 수익률
    10: 78,   # 매매: 매수회차 / 매도회차
}
AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN = 6
AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP = 6
AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT = 24
AUTO_TRADE_SETTING_STOCK_ROW_MARGIN_X = 4
AUTO_TRADE_SETTING_STOCK_ROW_SPACING = 2
AUTO_TRADE_SETTING_ROUTINE_TREE_METRIC_SAMPLES = {
    "period": (("9999",), ()),
    "profit": (("-99,999,999", "+99,999,999"), ("-99.99%", "+99.99%")),
    "average": (("-99,999,999", "+99,999,999"), ("-99.99%", "+99.99%")),
    "efficiency": (("999.9",), ()),
}
AUTO_TRADE_SETTING_ROUTINE_TREE_PERFORMANCE_ITEM_SPECS = (
    {
        "key": "period",
        "object_name": "autoTradeSettingRoutineTreePerformancePeriod",
        "label": "기간",
        "left_fallback": "0",
        "left_sample": "9999",
        "right_fallback": "",
        "right_sample": "",
    },
    {
        "key": "profit",
        "object_name": "autoTradeSettingRoutineTreePerformanceProfit",
        "label": "수익",
        "left_fallback": "0",
        "left_sample": "-99,999,999",
        "right_fallback": "0.00%",
        "right_sample": "-99.99%",
    },
    {
        "key": "average",
        "object_name": "autoTradeSettingRoutineTreePerformanceAverage",
        "label": "평균",
        "left_fallback": "0",
        "left_sample": "-99,999,999",
        "right_fallback": "0.00%",
        "right_sample": "-99.99%",
    },
    {
        "key": "efficiency",
        "object_name": "autoTradeSettingRoutineTreePerformanceEfficiency",
        "label": "효율",
        "left_fallback": "0.0",
        "left_sample": "999.9",
        "right_fallback": "",
        "right_sample": "",
    },
)
AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP = 9
AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE = (
    "QGroupBox {"
    " border: 1px solid #9CA3AF;"
    f" margin-top: {AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP}px;"
    f" padding-top: {AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP}px;"
    "}"
    " QGroupBox::title {"
    " subcontrol-origin: margin;"
    " subcontrol-position: top left;"
    " left: 4px;"
    " padding: 0 4px;"
    " background-color: palette(window);"
    "}"
)


def routine_tree_title_text(display_name: object) -> str:
    """종목 제목은 7자까지 표시하고 8자부터 7자로 축약한다."""
    text = str(display_name or "").strip()
    if len(text) <= ROUTINE_TREE_STOCK_TITLE_DISPLAY_CHARS:
        return text
    return f"{text[:ROUTINE_TREE_STOCK_TITLE_PREFIX_CHARS]}..."


def auto_trade_setting_historical_fixture_enabled() -> bool:
    app_env = str(
        os.environ.get(AUTO_TRADE_SETTING_APP_ENV, "") or ""
    ).strip().lower()
    if app_env in {"production", "prod"}:
        return False
    if app_env not in {"", "development", "dev", "test", "testing"}:
        return False
    fixture_value = str(
        os.environ.get(AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV, "")
        or ""
    ).strip().lower()
    if fixture_value in {"0", "false", "no", "off"}:
        return False
    return fixture_value in {"1", "true", "yes", "on"}


def auto_trade_projected_instance_ids(
    instances: list[object],
    *,
    groups: list[object] | None = None,
    stocks: list[dict[str, object]] | None = None,
) -> set[str]:
    """Return instances backed by the canonical Group/stock assignment relation."""
    projection = build_main_group_projection(
        get_group_records() if groups is None else groups,
        instances,
        read_base_stocks() if stocks is None else stocks,
    )
    return {
        projected_instance.instance_id
        for projected_group in projection
        for projected_instance in projected_group.instances
    }


def auto_trade_initial_read_snapshot(window) -> dict[str, object] | None:
    snapshot = getattr(window, "_auto_trade_initial_read_snapshot", None)
    return snapshot if isinstance(snapshot, dict) else None


def routine_tree_instance_title_text(display_name: object) -> str:
    return tree_title_text(display_name)


def routine_tree_parent_identity(metadata: dict[str, object]) -> str:
    return str(
        metadata.get("group_id", "")
        or metadata.get("definition_id", "")
        or ""
    ).strip()


def routine_tree_title_width(font_metrics) -> int:
    samples = (
        "가" * ROUTINE_TREE_STOCK_TITLE_DISPLAY_CHARS,
        ("가" * ROUTINE_TREE_STOCK_TITLE_PREFIX_CHARS) + "...",
    )
    return max(
        max(font_metrics.horizontalAdvance(sample), font_metrics.boundingRect(sample).width())
        for sample in samples
    )


def routine_tree_layout_metrics(font: QFont) -> dict[str, int]:
    """Return font-derived geometry for the shared tree identity region."""
    base_metrics = QFontMetrics(font)
    label_font = QFont(QApplication.font("QLabel"))
    label_metrics = QFontMetrics(label_font)
    parent_font = QFont(font)
    parent_font.setPointSize(parent_font.pointSize() + 1)
    parent_font.setWeight(QFont.DemiBold)
    parent_metrics = QFontMetrics(parent_font)
    parent_label_font = QFont(label_font)
    parent_label_font.setPointSize(parent_label_font.pointSize() + 1)
    parent_label_font.setWeight(QFont.DemiBold)
    parent_label_metrics = QFontMetrics(parent_label_font)

    # Keep the established Group origin while deriving child depth from the
    # footprint reserved for the hierarchy icon.
    outer_margin = 6
    # The live 32-bit layout leaves 24 px after efficiency with 1 px gaps.
    # Symmetric 5/4/5 px separator gaps consume that space while retaining a
    # 2 px right-edge safety margin.
    performance_separator_edge_side_gap = 5
    performance_separator_inner_side_gap = 4
    performance_trailing_margin = 0
    item_gap = 4
    parent_icon_width = 28
    child_icon_width = 18
    hierarchy_step = max(item_gap, child_icon_width // 2)
    count_badge_width = 64
    parent_title_width = max(
        tree_title_slot_width(metrics, padding=0)
        for metrics in (parent_metrics, parent_label_metrics)
    )
    child_title_width = max(
        tree_title_slot_width(metrics, padding=0)
        for metrics in (base_metrics, label_metrics)
    )
    stock_title_width = routine_tree_title_width(base_metrics)
    hangul_width = max(
        width
        for metrics in (base_metrics, label_metrics)
        for width in (
            metrics.horizontalAdvance("가"),
            metrics.boundingRect("가").width(),
        )
    )
    group_metric_gap = max(1, hangul_width // 2)
    compact_metric_gap = max(1, hangul_width // 2)
    parent_title_x = parent_icon_width + item_gap
    child_left_shift = item_gap
    child_title_shift = compact_metric_gap
    original_instance_title_x = parent_title_x + hierarchy_step - child_left_shift
    original_stock_title_x = parent_title_x + (hierarchy_step * 2) - child_left_shift
    instance_title_x = original_instance_title_x - child_title_shift
    stock_title_x = original_stock_title_x - child_title_shift
    instance_metric_x = instance_title_x + child_title_width + compact_metric_gap
    instance_metric_gap = max(
        0,
        instance_metric_x - instance_title_x - child_title_width,
    )
    stock_metric_gap = max(
        0,
        instance_metric_x - stock_title_x - stock_title_width,
    )
    instance_indent = max(0, instance_title_x - child_icon_width - item_gap)
    stock_indent = max(0, stock_title_x - child_icon_width - item_gap)
    parent_identity_width = parent_title_x + parent_title_width + count_badge_width
    instance_identity_width = instance_metric_x
    stock_identity_width = stock_title_x + stock_title_width + stock_metric_gap
    identity_width = max(
        parent_identity_width,
        instance_identity_width,
        stock_identity_width,
    )
    return {
        "outer_margin": outer_margin,
        "performance_separator_edge_side_gap": performance_separator_edge_side_gap,
        "performance_separator_inner_side_gap": performance_separator_inner_side_gap,
        "performance_trailing_margin": performance_trailing_margin,
        "item_gap": item_gap,
        "hierarchy_step": hierarchy_step,
        "child_left_shift": child_left_shift,
        "child_title_shift": child_title_shift,
        "parent_icon_width": parent_icon_width,
        "child_icon_width": child_icon_width,
        "count_badge_width": count_badge_width,
        "parent_title_width": parent_title_width,
        "child_title_width": child_title_width,
        "stock_title_width": stock_title_width,
        "group_metric_gap": group_metric_gap,
        "instance_metric_gap": instance_metric_gap,
        "stock_metric_gap": stock_metric_gap,
        "instance_indent": instance_indent,
        "stock_indent": stock_indent,
        "parent_identity_width": parent_identity_width,
        "instance_identity_width": instance_identity_width,
        "stock_identity_width": stock_identity_width,
        "identity_width": identity_width,
    }


def normalize_profit_factor(value: object) -> float:
    """PF display input is non-negative; unavailable values normalize to zero."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


class StockPositionMetricDelegate(QStyledItemDelegate):
    """보유/가격/손익/매매 셀의 숫자 슬롯을 우측 정렬해 그린다."""

    LABEL_BY_COLUMN = {
        7: "보유",
        8: "가격",
        9: "수익",
        10: "매매",
    }

    def paint(self, painter, option, index) -> None:
        painter.setFont(option.font)
        font_metrics = QFontMetrics(option.font)
        text = str(index.data(Qt.DisplayRole) or "")
        label_hint = self.LABEL_BY_COLUMN.get(index.column())
        foreground = index.data(Qt.ForegroundRole)
        color = (
            foreground.color()
            if isinstance(foreground, QBrush)
            else option.palette.text().color()
        )
        metric_option = QStyleOptionViewItem(option)
        metric_option.text = ""
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, metric_option, painter, option.widget)
        if draw_stock_position_metric(
            painter,
            option.rect,
            text,
            color,
            label_hint=label_hint,
            compact=True,
            compact_margins=self._operation_column_margins(
                option.widget,
                font_metrics,
            ),
        ):
            return
        super().paint(painter, option, index)

    @staticmethod
    def _operation_column_margins(
        table,
        font_metrics: QFontMetrics,
    ) -> tuple[int, int]:
        if table is None:
            return 2, 2
        text_width = font_metrics.horizontalAdvance("09:30~13:30")
        column_width = table.columnWidth(2)
        spare_width = max(0, column_width - text_width)
        left_margin = spare_width // 2
        right_margin = spare_width - left_margin
        return left_margin, right_margin


class SelectedTextReadableDelegate(QStyledItemDelegate):
    """옅은 선택 배경에서도 셀의 상태별 foreground를 유지한다."""

    def paint(self, painter, option, index) -> None:
        if option.state & QStyle.State_Selected:
            readable_option = QStyleOptionViewItem(option)
            self.initStyleOption(readable_option, index)
            foreground = index.data(Qt.ForegroundRole)
            if isinstance(foreground, QBrush):
                readable_option.palette.setBrush(QPalette.HighlightedText, foreground)
            style = option.widget.style() if option.widget is not None else QApplication.style()
            style.drawControl(
                QStyle.CE_ItemViewItem,
                readable_option,
                painter,
                option.widget,
            )
            return
        super().paint(painter, option, index)


class AutoTradeNotificationPopup(QFrame):
    """자동매매설정창 안에서 쓰는 버튼 없는 비모달 자동닫힘 알림."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setObjectName("autoTradeNotificationPopup")
        self._label = QLabel(self)
        self._label.setObjectName("autoTradeNotificationText")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addWidget(self._label)
        self.setStyleSheet(
            """
            QFrame#autoTradeNotificationPopup {
                background-color: #111827;
                border: 1px solid #374151;
                border-radius: 6px;
            }
            QLabel#autoTradeNotificationText {
                color: #ffffff;
                font-weight: 600;
            }
            """
        )

    def show_message(self, message: str, timeout_ms: int = 2500) -> None:
        self._label.setText(str(message or ""))
        self.adjustSize()
        self._move_to_parent_center()
        self.show()
        self.raise_()
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, self.hide)

    def text(self) -> str:
        return self._label.text()

    def button_count(self) -> int:
        return 0

    def _move_to_parent_center(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        parent_rect = parent.frameGeometry()
        popup_rect = self.frameGeometry()
        center = parent_rect.center()
        popup_rect.moveCenter(center)
        self.move(popup_rect.topLeft())


PROJECT_ROOT = Path(__file__).resolve().parent
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"
REAL_TRADE_GUARD_PATH = PROJECT_ROOT / "runtime" / "real_trade_guard.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
ORDER_EXECUTIONS_PATH = PROJECT_ROOT / "runtime" / "order_executions.json"
ORDER_LOCKS_PATH = PROJECT_ROOT / "runtime" / "order_locks.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"
REALIZED_PNL_LEDGER_PATH = PROJECT_ROOT / "runtime" / "realized_pnl.json"


def startup_recovery_action_allowed(window, action: str) -> bool:
    """Enforce session recovery only for the real MainWindow production caller."""
    if not isinstance(window, AutoTradeSettingWindow):
        return True
    try:
        parent = persistent_feature_owner(window)
    except Exception:
        return True
    if "_startup_recovery_result" not in getattr(parent, "__dict__", {}):
        return True
    if not callable(getattr(type(parent), "startup_recovery_session_ready", None)):
        return True
    checker = getattr(window, "require_startup_recovery_session", None)
    if callable(checker):
        return checker(action) is True
    return True


def handle_kiwoom_raw_chejan_event(
    raw_event: dict[str, object],
    live_context: dict[str, object] | None = None,
) -> dict[str, object]:
    if str(raw_event.get("gubun") or "").strip() == "1":
        result = record_broker_holding_snapshot(
            raw_event,
            BROKER_HOLDINGS_PATH,
            POSITIONS_PATH,
            context=live_context or {},
        )
        result["recorded"] = result.get("holding_recorded") is True
        result["stage"] = result.get("holding_stage", "broker_holding_snapshot")
        result["balance_event_received"] = True
        return result

    normalized = normalize_kiwoom_chejan_event(raw_event)
    if normalized.get("normalized") is not True:
        return {"recorded": False, "stage": "normalize", "normalized_event": normalized}

    queue_path = ORDER_QUEUE_PATH
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"recorded": False, "stage": "queue_read", "blocked_reasons": [str(exc)]}
    orders = data.get("orders") if isinstance(data, dict) else None
    if not isinstance(orders, list):
        return {"recorded": False, "stage": "queue_structure", "blocked_reasons": ["queue orders must be a list"]}

    broker_order_no = str(normalized.get("broker_order_no") or "").strip()
    account_no = str(normalized.get("account_no") or "").strip()
    code = str(normalized.get("code") or "").strip()
    side = str(normalized.get("side") or "").strip().upper()
    candidates: list[dict[str, object]] = []
    for item in orders:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip()
        if status not in {"SEND_CALL_ACCEPTED", "SEND_UNCERTAIN", "BROKER_ACCEPTED", "PARTIALLY_FILLED", "FILLED"}:
            continue
        if str(item.get("account_no") or "").strip() not in {"", account_no}:
            continue
        if str(item.get("code") or "").strip() != code:
            continue
        if str(item.get("side") or "").strip().upper() != side:
            continue
        item_broker_order_no = str(item.get("broker_order_no") or "").strip()
        if item_broker_order_no and broker_order_no and item_broker_order_no != broker_order_no:
            continue
        if (
            status == "FILLED"
            and not _has_pending_chejan_reconciliation_for_event(item, normalized)
            and existing_chejan_record_result(item, normalized) is None
        ):
            continue
        candidates.append(dict(item))

    if len(candidates) != 1:
        return {
            "recorded": False,
            "stage": "chejan_target_match",
            "normalized_event": normalized,
            "blocked_reasons": [f"matching send order record count is {len(candidates)}"],
        }

    review = review_chejan_event(normalized, order_record=candidates[0])
    if review.get("chejan_review_ok") is not True:
        return {
            "recorded": False,
            "stage": "chejan_review",
            "normalized_event": normalized,
            "review_result": review,
            "blocked_reasons": list(review.get("blocked_reasons") or []),
        }
    recorded = record_chejan_event(
        review,
        normalized,
        queue_path,
        context=live_context or {},
    )
    response = {
        "recorded": recorded.get("recorded") is True or recorded.get("committed") is True,
        "stage": "chejan_record",
        "normalized_event": normalized,
        "review_result": review,
        "record_result": recorded,
        "blocked_reasons": list(recorded.get("blocked_reasons") or []),
    }
    if response["recorded"] is True:
        response["close_routine_final_sell_marker"] = (
            _mark_close_routine_final_sell_from_broker_acceptance(
                candidates[0],
                normalized,
                recorded,
            )
        )
    downstream_source = recorded
    if response["recorded"] is not True and _chejan_record_duplicate(recorded):
        if _has_pending_chejan_reconciliation_for_event(candidates[0], normalized):
            reconstructed = existing_chejan_record_result(candidates[0], normalized, recorded)
        else:
            reconstructed = None
        if reconstructed is not None:
            downstream_source = reconstructed
            response["duplicate_reprocess"] = True
            live_context = dict(live_context or {})
            live_context["chejan_reconciliation_reprocess"] = True
        else:
            response["duplicate_noop"] = True
            return response

    fill_result, position_result, realized_pnl_result, reconciliation_result = _record_fill_and_position_from_chejan(
        downstream_source,
        normalized,
        candidates[0],
        live_context or {},
    )
    if fill_result is not None:
        response["fill_result"] = fill_result
        if fill_result.get("fill_recorded") is not True:
            response["manual_reconciliation_required"] = True
            response["fill_blocked_reasons"] = list(fill_result.get("blocked_reasons") or [])
    if position_result is not None:
        response["position_result"] = position_result
        if position_result.get("position_updated") is not True:
            response["manual_reconciliation_required"] = True
            response["position_blocked_reasons"] = list(position_result.get("blocked_reasons") or [])
    if realized_pnl_result is not None:
        response["realized_pnl_result"] = realized_pnl_result
        if realized_pnl_result.get("realized_pnl_recorded") is not True:
            response["manual_reconciliation_required"] = True
            response["realized_pnl_blocked_reasons"] = list(realized_pnl_result.get("blocked_reasons") or [])
    if reconciliation_result is not None:
        response["reconciliation_result"] = reconciliation_result
        response["reconciliation_persisted"] = reconciliation_result.get("reconciliation_persisted") is True
        if response["reconciliation_persisted"] is not True:
            response["manual_reconciliation_required"] = True
            response["reconciliation_persist_failed_reasons"] = list(
                reconciliation_result.get("reconciliation_persist_failed_reasons")
                or reconciliation_result.get("blocked_reasons")
                or ["chejan reconciliation state was not persisted"]
            )
    return response


def _order_routine_instance_id(order: dict[str, object]) -> str:
    for container in (
        order,
        order.get("execution_intent"),
        order.get("order_provenance"),
    ):
        if not isinstance(container, dict):
            continue
        value = str(
            container.get("routine_instance_id")
            or container.get("assigned_routine_instance_id")
            or ""
        ).strip()
        if value:
            return value
    return ""


def _mark_close_routine_final_sell_from_broker_acceptance(
    order: dict[str, object],
    normalized_event: dict[str, object],
    record_result: dict[str, object],
) -> dict[str, object]:
    """Persist the routine-close final SELL marker only at broker acceptance."""
    event_type = str(
        record_result.get("event_type") or normalized_event.get("event_type") or ""
    ).strip().upper()
    if event_type not in {"ORDER_ACCEPTED", "ORDER_OPEN"}:
        return {"attempted": False, "marked": False, "reason": "not broker acceptance"}
    if str(order.get("source") or "").strip() != "routine_signals":
        return {"attempted": False, "marked": False, "reason": "not routine signal order"}
    if str(order.get("side") or "").strip().upper() != "SELL":
        return {"attempted": False, "marked": False, "reason": "not SELL order"}

    code = str(order.get("code") or normalized_event.get("code") or "").strip()
    routine_instance_id = _order_routine_instance_id(order)
    matches: list[Path] = []
    for stock_dir_value in all_registered_stock_dirs():
        stock_dir = Path(stock_dir_value)
        stock_code, _ = parse_stock_folder_name(stock_dir.name)
        if stock_code != code:
            continue
        config = read_json_dict(stock_dir / "config.json")
        assigned_instance_id = str(
            config.get("assigned_routine_instance_id") or ""
        ).strip()
        if routine_instance_id and assigned_instance_id != routine_instance_id:
            continue
        matches.append(stock_dir)

    if len(matches) != 1:
        return {
            "attempted": True,
            "marked": False,
            "reason": f"runtime stock match count is {len(matches)}",
        }

    stock_dir = matches[0]
    state = read_json_dict(stock_dir / "state.json")
    display_status = str(state.get("status") or "")
    decision = decide_routine_order_for_stock_dir(
        stock_dir,
        "SELL",
        display_status=display_status,
    )
    if decision.get("allowed") is not True or decision.get(
        "mark_close_final_sell_after_order"
    ) is not True:
        return {
            "attempted": True,
            "marked": False,
            "reason": str(decision.get("reason") or "routine close marker not required"),
            "stock_dir": str(stock_dir),
        }

    marked = mark_routine_order_accepted_for_stock_dir(
        stock_dir,
        decision,
        source="kiwoom_chejan",
    )
    return {
        "attempted": True,
        "marked": marked is True,
        "reason": "broker accepted routine SELL" if marked else "state write failed",
        "stock_dir": str(stock_dir),
    }


def _clean_runtime_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _chejan_record_duplicate(result: dict[str, object]) -> bool:
    return result.get("duplicate") is True or result.get("idempotent") is True or _clean_runtime_text(result.get("record_stage")) == "duplicate_event"


def _has_pending_chejan_reconciliation_for_event(
    order_record: dict[str, object],
    normalized_event: dict[str, object],
) -> bool:
    broker_order_no = _clean_runtime_text(order_record.get("broker_order_no") or normalized_event.get("broker_order_no"))
    event_identity, _ = chejan_event_identity(normalized_event, broker_order_no=broker_order_no)
    items = order_record.get("chejan_reconciliation_items")
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("required") is True
        and _clean_runtime_text(item.get("event_identity")).upper() == event_identity
        for item in items
    )


def _position_result_ok(result: dict[str, object] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("position_updated") is True:
        return True
    return _clean_runtime_text(result.get("position_stage")) in {
        "duplicate_fill",
        "fill_delta_noop",
        "later_cumulative_fill_already_applied",
    }


def _record_fill_and_position_from_chejan(
    chejan_result: dict[str, object],
    normalized_event: dict[str, object],
    order_record: dict[str, object],
    live_context: dict[str, object],
) -> tuple[
    dict[str, object] | None,
    dict[str, object] | None,
    dict[str, object] | None,
    dict[str, object] | None,
]:
    if chejan_result.get("recorded") is not True or chejan_result.get("next_stage") != "FILL_RECORD_REQUIRED":
        return None, None, None, None

    completed_steps = ["QUEUE_LIFECYCLE"]
    live_context = dict(live_context)
    provenance = order_record.get("routine_provenance")
    if isinstance(provenance, dict):
        live_context.setdefault(
            "routine_instance_id",
            str(provenance.get("routine_instance_id") or "").strip(),
        )
    else:
        live_context.setdefault(
            "routine_instance_id",
            str(order_record.get("routine_instance_id") or "").strip(),
        )
    fill_result = record_execution_fill(
        chejan_result,
        normalized_event,
        FILLS_PATH,
        context=live_context,
    )
    fill_record = fill_result.get("fill_record") if isinstance(fill_result, dict) else None
    if not isinstance(fill_record, dict):
        fill_record = find_existing_execution_fill_record(FILLS_PATH, chejan_result, normalized_event)
    if not isinstance(fill_record, dict):
        reconciliation = mark_chejan_reconciliation_state(
            ORDER_QUEUE_PATH,
            chejan_result,
            required=True,
            failed_stage="FILL_RECORD",
            completed_steps=completed_steps,
            reasons=list(fill_result.get("blocked_reasons") or []) if isinstance(fill_result, dict) else ["fill record failed"],
            context=live_context,
        )
        return fill_result, None, None, reconciliation

    completed_steps.append("FILL_RECORD")
    position_result = update_position_from_fill(
        fill_result if isinstance(fill_result, dict) and fill_result.get("fill_recorded") is True else {
            "fill_recorded": True,
            "fill_stage": "execution_fill_already_recorded",
            "next_stage": "POSITION_UPDATE_REQUIRED",
            "fill_id": fill_record.get("fill_id"),
            "event_type": fill_record.get("event_type"),
            "order_id": fill_record.get("order_id"),
            "order_queued_id": fill_record.get("order_queued_id"),
            "broker_order_no": fill_record.get("broker_order_no"),
            "request_hash": fill_record.get("request_hash"),
            "lock_id": fill_record.get("lock_id"),
            "execution_id": fill_record.get("execution_id"),
            "filled_quantity": fill_record.get("filled_quantity"),
            "filled_price": fill_record.get("filled_price"),
            "blocked_reasons": [],
            "warnings": [],
        },
        fill_record,
        POSITIONS_PATH,
        context=live_context,
    )
    if not _position_result_ok(position_result):
        reconciliation = mark_chejan_reconciliation_state(
            ORDER_QUEUE_PATH,
            chejan_result,
            required=True,
            failed_stage="POSITION_UPDATE",
            completed_steps=completed_steps,
            reasons=list(position_result.get("blocked_reasons") or []) if isinstance(position_result, dict) else ["position update failed"],
            context=live_context,
        )
        return fill_result, position_result, None, reconciliation

    completed_steps.append("POSITION_UPDATE")
    realized_pnl_result = None
    if _clean_runtime_text(fill_record.get("side")).upper() == "SELL":
        realized_context = dict(live_context)
        realized_context["fills_path"] = str(FILLS_PATH)
        realized_pnl_result = record_realized_pnl(
            fill_record,
            position_result,
            order_record,
            REALIZED_PNL_LEDGER_PATH,
            context=realized_context,
        )
        if realized_pnl_result.get("realized_pnl_recorded") is not True:
            reconciliation = mark_chejan_reconciliation_state(
                ORDER_QUEUE_PATH,
                chejan_result,
                required=True,
                failed_stage="REALIZED_PNL_LEDGER",
                completed_steps=completed_steps,
                reasons=list(realized_pnl_result.get("blocked_reasons") or ["realized P/L ledger write failed"]),
                context=live_context,
            )
            return fill_result, position_result, realized_pnl_result, reconciliation
        completed_steps.append("REALIZED_PNL_LEDGER")
    reconciliation = mark_chejan_reconciliation_state(
        ORDER_QUEUE_PATH,
        chejan_result,
        required=False,
        completed_steps=completed_steps,
        context=live_context,
    )
    return fill_result, position_result, realized_pnl_result, reconciliation


def get_group_dirs() -> list[Path]:
    """Return project-root Group paths used by stock assignment operations."""
    return registry_get_group_dirs()


def get_routine_dirs() -> list[Path]:
    """Compatibility wrapper for historical Group-path callers."""
    return get_group_dirs()


def routine_display_name(routine_dir: Path) -> str:
    """루틴 원본 경로에서 GUI 표시 루틴명을 반환한다."""
    return registry_routine_display_name(routine_dir)









def default_operation_policy() -> dict[str, object]:
    """운영환경설정 기본값.

    현재 단계에서는 UI/저장 구조를 먼저 확정한다.
    실제 자동판정 엔진 연결은 후속 패치에서 단계적으로 반영한다.
    """
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {"enabled": False, "name": "추가시간1", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": False, "name": "추가시간2", "start_time": "15:40:00", "end_time": "19:50:00"},
            {"enabled": False, "name": "추가시간3", "start_time": "", "end_time": ""},
        ],
        "scheduled_operation": {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
        },
        "manual_operation": {
            "use_regular_market": True,
            "use_extra_session_1": False,
            "use_extra_session_2": False,
            "use_extra_session_3": False,
            "enabled_status": "매수/매도",
            "disabled_status": "감시/대기",
            "use_liquidation_policy": False,
        },
        "auto_close": {
            "method": "루틴매도신호",
            "profit_percent": "",
            "loss_percent": "",
        },
        "early_close": {
            "method": "시장가",
            "profit_percent": "",
            "loss_percent": "",
        },
        "liquidation": {
            "minutes_before_regular_close": "5",
            "method": "이월",
        },
        "updated_at": "",
    }


def read_operation_policy() -> dict[str, object]:
    default = default_operation_policy()
    if not OPERATION_POLICY_PATH.exists():
        return default
    try:
        data = json.loads(OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default

    # 얕은 병합: 누락된 상위 항목은 기본값으로 보완한다.
    merged = default_operation_policy()
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)  # type: ignore[index]
        else:
            merged[key] = value
    scheduled = merged.get("scheduled_operation")
    if isinstance(scheduled, dict):
        scheduled.pop("after_buy_end_status", None)
    return merged


class StockPolicyOverrideDialog(QDialog):
    """개별종목 예외설정 1차 UI.

    환경설정이 디폴트이고, 이 창은 해당 종목만 예외로 둘 때 사용한다.
    전체 리셋은 종목별 예외 설정값을 제거한다.
    """

    OVERRIDE_KEYS = (
        "policy_override_enabled",
        "operation_policy_override",
        "manual_operation_override",
        "scheduled_operation_override",
        "auto_close_override",
        "early_close_override",
        "liquidation_override",
    )

    def __init__(self, stock_dir: Path, code: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stock_dir = stock_dir
        self.code = code
        self.name = name
        self.config_path = stock_dir / "config.json"
        self.config = read_json_dict(self.config_path) or default_config()
        self.setWindowTitle(f"개별종목 설정 - {code} {name}")
        self.resize(520, 360)

        self.use_override = QCheckBox("이 종목만 개별설정 사용")
        self.memo = QTextEdit()
        self.memo.setPlaceholderText("개별 예외 사유 또는 메모")
        self.memo.setMinimumHeight(90)
        self.btn_reset_all = QPushButton("환경설정값으로 전체 리셋")
        self.btn_save = QPushButton("저장")
        self.btn_cancel = QPushButton("취소")

        self._setup_ui()
        self.load_config_to_widgets()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        info = QLabel(
            "환경설정은 기본값입니다.\n"
            "선택 종목만 예외로 적용합니다.\n"
            "현재 1차 구현은 예외 사용 여부와 메모, 전체 리셋 흐름을 먼저 제공합니다."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(self.use_override)
        layout.addWidget(QLabel("개별설정 메모"))
        layout.addWidget(self.memo)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.btn_reset_all)
        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.btn_reset_all.clicked.connect(self.reset_all_to_global)
        self.btn_save.clicked.connect(self.save_override)
        self.btn_cancel.clicked.connect(self.reject)

    def load_config_to_widgets(self) -> None:
        self.use_override.setChecked(bool(self.config.get("policy_override_enabled", False)))
        self.memo.setPlainText(str(self.config.get("policy_override_memo", "")))

    def write_config(self) -> None:
        self.config["updated_at"] = now_text()
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _append_override_changed(
        self,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        changes: list[dict[str, object]] = []
        for key in self.OVERRIDE_KEYS:
            changes.extend(
                _setting_leaf_changes(
                    before.get(key),
                    after.get(key),
                    prefix=key,
                )
            )
        before_memo = bool(str(before.get("policy_override_memo") or "").strip())
        after_memo = bool(str(after.get("policy_override_memo") or "").strip())
        if before_memo != after_memo:
            changes.append(
                {
                    "field_key": "policy_override_memo_present",
                    "before": before_memo,
                    "after": after_memo,
                }
            )
        if not changes:
            return
        append_production_event(
            "SETTING_CHANGED",
            result="SUCCESS",
            source="STOCK_POLICY_OVERRIDE_DIALOG",
            template_args={"target": "개별종목 설정"},
            target_type="STOCK",
            target_id=self.code,
            target_name=self.name,
            stock_code=self.code,
            stock_name=self.name,
            changes=changes,
        )

    def save_override(self) -> None:
        before = deepcopy(self.config)
        self.config["policy_override_enabled"] = self.use_override.isChecked()
        self.config["policy_override_memo"] = self.memo.toPlainText().strip()
        self.config["policy_override_updated_at"] = now_text()
        try:
            self.write_config()
            saved = read_json_dict(self.config_path)
            if any(saved.get(key) != self.config.get(key) for key in self.OVERRIDE_KEYS):
                raise RuntimeError("개별종목 설정 저장 후 검증이 일치하지 않습니다.")
            if str(saved.get("policy_override_memo") or "") != str(
                self.config.get("policy_override_memo") or ""
            ):
                raise RuntimeError("개별종목 메모 저장 후 검증이 일치하지 않습니다.")
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 저장")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 저장: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"개별종목 설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        self._append_override_changed(before, saved)
        QMessageBox.information(self, "저장 완료", "개별종목 설정을 저장했습니다.")
        self.accept()

    def reset_all_to_global(self) -> None:
        before = deepcopy(self.config)
        for key in self.OVERRIDE_KEYS:
            self.config.pop(key, None)
        self.config.pop("policy_override_memo", None)
        self.config["policy_override_enabled"] = False
        self.config["policy_override_reset_at"] = now_text()
        try:
            self.write_config()
            saved = read_json_dict(self.config_path)
            if bool(saved.get("policy_override_enabled", False)):
                raise RuntimeError("개별종목 설정 리셋 후 검증이 일치하지 않습니다.")
            if any(key in saved for key in self.OVERRIDE_KEYS[1:]):
                raise RuntimeError("개별종목 설정 리셋 후 예외값이 남아 있습니다.")
            if str(saved.get("policy_override_memo") or ""):
                raise RuntimeError("개별종목 설정 리셋 후 메모가 남아 있습니다.")
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 전체 리셋")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 전체 리셋: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "리셋 오류", f"개별종목 설정 리셋 중 오류가 발생했습니다.\n\n{exc}")
            return
        self._append_override_changed(before, saved)
        QMessageBox.information(self, "리셋 완료", "해당 종목의 개별설정을 환경설정값으로 전체 리셋했습니다.")
        self.accept()


def handle_stock_name_operation_exclusion_double_click(
    host,
    target: tuple[Path, str, str],
) -> bool:
    running_targets_getter = getattr(
        host,
        "running_registered_operation_targets",
        None,
    )
    if callable(running_targets_getter) and running_targets_getter():
        status_message = getattr(host, "statusBarMessage", None)
        if callable(status_message):
            status_message(
                "운영 중에는 더블클릭으로 운영 대상을 변경할 수 없습니다. 우클릭 운영시작을 사용하세요."
            )
        return False
    changed = bool(host.toggle_stock_operation_exclusion(target, refresh=False))
    if not changed:
        return False

    def refresh_after_double_click(context=host) -> None:
        try:
            refresh_auto_trade_views(context)
        except RuntimeError:
            # The originating window may close before the queued UI refresh runs.
            return

    QTimer.singleShot(0, refresh_after_double_click)
    return True


def delete_routine_instance_with_existing_policy(
    window: QWidget,
    metadata: dict[str, object],
) -> None:
    """Apply the canonical RoutineInstance deletion policy for a UI owner."""
    if str(metadata.get("row_kind", "") or "") != "instance":
        return
    instance_id = str(metadata.get("instance_id", "") or "").strip()
    instance_name = str(metadata.get("instance_name", "") or "").strip()
    if not instance_id:
        return

    assigned_stocks: list[dict[str, object]] = []
    for stock in read_base_stocks():
        assigned_instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if not assigned_instance_id and stock_path:
            config = read_json_dict(PROJECT_ROOT / stock_path / "config.json")
            assigned_instance_id = str(
                config.get("assigned_routine_instance_id", "") or ""
            ).strip()
        if assigned_instance_id == instance_id:
            assigned_stocks.append(stock)
    if assigned_stocks:
        QMessageBox.warning(
            window,
            "등록삭제",
            "연결된 종목이 있는 루틴은 삭제할 수 없습니다.\n"
            "매매루틴등록 창에서 종목의 루틴 연결을 먼저 해제하세요.",
        )
        return

    answer = QMessageBox.question(
        window,
        "등록삭제",
        f"'{instance_name or instance_id}' 루틴 등록을 삭제하시겠습니까?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return

    result = RoutineInstanceRepository(PROJECT_ROOT).delete_instance(instance_id)
    if not result.success:
        QMessageBox.warning(
            window,
            "등록삭제",
            result.error or "루틴 등록을 삭제하지 못했습니다.",
        )
        return
    window.refresh_all()


def open_routine_settings_dialog_for_owner(
    owner: QWidget,
    metadata: dict[str, object],
    *,
    registration: bool,
) -> None:
    definition_id = str(metadata.get("definition_id", "") or "").strip()
    definition = routine_definition_by_id(definition_id)
    if definition is None:
        QMessageBox.warning(owner, "루틴 설정", "선택한 루틴 정의를 확인할 수 없습니다.")
        return

    instance = None
    if not registration:
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            QMessageBox.warning(owner, "루틴 설정", "선택한 루틴을 확인할 수 없습니다.")
            return

    settings_ui = str(definition.settings_ui or "").strip().lower()
    if settings_ui != "indicator_follow":
        QMessageBox.information(
            owner,
            "루틴 설정",
            f"선택한 루틴의 설정창이 아직 연결되지 않았습니다.\n루틴명: {definition.display_name}",
        )
        return

    rules_path = (
        definition.package_dir / definition.default_rules_file
        if registration
        else instance.rules_path
    )
    if rules_path is None or not rules_path.exists():
        QMessageBox.warning(
            owner,
            "rules.json 없음",
            f"선택한 루틴의 rules.json을 찾을 수 없습니다.\n{rules_path}",
        )
        return

    try:
        from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
    except Exception as exc:
        QMessageBox.critical(
            owner,
            "설정창 로드 실패",
            "gui_indicator_follow_routine_settings_dialog.py 파일을 불러오지 못했습니다.\n"
            f"{exc}",
        )
        return

    registration_display_name = str(
        metadata.get("group_display_name", "")
        or metadata.get("display_name", "")
        or metadata.get("definition_name", "")
        or definition.display_name
    ).strip()
    dialog = IndicatorFollowRoutineSettingsDialog(
        rules_path=rules_path,
        routine_path=definition.package_dir,
        routine_name=registration_display_name if registration else instance.display_name,
        parent=owner,
        definition_id=definition.definition_id,
        definition_display_name=definition.display_name,
        instance_id="" if registration else instance.instance_id,
        group_id=str(metadata.get("group_id", "") or "").strip(),
        group_display_name=registration_display_name if registration else "",
        settings_mode="registration" if registration else "edit",
    )
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    windows = getattr(owner, "_routine_settings_windows", None)
    if not isinstance(windows, set):
        windows = set()
        owner._routine_settings_windows = windows
    windows.add(dialog)
    dialog.destroyed.connect(
        lambda _obj=None, target=dialog: windows.discard(target)
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


class AutoTradeSettingWindow(QDialog):
    """
    자동매매설정 창.

    1차 구현 범위:
    - 자동매매 루틴 목록 표시
    - 선택 루틴의 종목별 저장 폴더 표시
    - state.json 기준 상태 요약 표시
    - 실제 자동매매 시작/정지/삭제/환경설정/로그 기능은 다음 단계에서 구현
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)

        self.setObjectName("autoTradeSettingWindow")
        self.setWindowTitle("자동매매설정")
        flags = self.windowFlags()
        flags &= ~Qt.WindowContextHelpButtonHint
        flags |= Qt.WindowMinimizeButtonHint
        flags |= Qt.WindowMaximizeButtonHint
        flags |= Qt.WindowCloseButtonHint
        self.setWindowFlags(flags)
        self.setMinimumHeight(650)

        self.routine_table = QTableWidget()
        self.routine_table.setObjectName("autoTradeSettingRoutineTable")
        self.stock_table = QTableWidget()
        self.stock_table.setObjectName("autoTradeSettingStockTable")

        self.btn_start = QPushButton("▶ 운영시작")
        self.btn_early_close = QPushButton("조기마감")
        self.btn_early_close.setStyleSheet(AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE)
        self.btn_all_stocks = QPushButton("전체")
        self.btn_all_stocks.setFocusPolicy(Qt.NoFocus)
        self.btn_all_stocks.setCursor(Qt.PointingHandCursor)
        self.btn_all_stocks.setFixedSize(
            64,
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
        )
        self.all_stocks_command_separator = QLabel("|")
        self.all_stocks_command_separator.setObjectName(
            "autoTradeSettingAllStocksCommandSeparator"
        )
        self.all_stocks_command_separator.setAlignment(Qt.AlignCenter)
        self.all_stocks_command_separator.setFixedSize(
            12,
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
        )
        self.all_stocks_command_separator.setFocusPolicy(Qt.NoFocus)
        self.all_stocks_command_separator.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            True,
        )
        self.all_stocks_command_separator.setStyleSheet(
            "background: transparent; color: #9CA3AF;"
        )
        self.btn_preview_order_candidates = QPushButton("주문후보검증")
        self.btn_execution_enable = QPushButton("수동 실주문 후보 활성화")
        self.btn_real_ready_preflight = QPushButton("REAL_READY 수동 점검")
        self.btn_execution_preview = QPushButton("Execution Preview")
        self.btn_manual_send_order = QPushButton("Manual SendOrder")
        self.btn_manual_cancel_pending_order = QPushButton("Manual Cancel")
        self.btn_manual_modify_pending_order = QPushButton("Manual Modify")
        self.btn_manual_queue_commit = QPushButton("수동 Queue 저장")
        self.btn_fetch_minute_candles = QPushButton("분봉조회")
        self.btn_early_close.setMinimumHeight(28)
        self.btn_preview_order_candidates.setMinimumHeight(28)
        self.btn_execution_enable.setMinimumHeight(28)
        self.btn_real_ready_preflight.setMinimumHeight(28)
        self.btn_execution_preview.setMinimumHeight(28)
        self.btn_manual_send_order.setMinimumHeight(28)
        self.btn_manual_cancel_pending_order.setMinimumHeight(28)
        self.btn_manual_modify_pending_order.setMinimumHeight(28)
        self.btn_manual_queue_commit.setMinimumHeight(28)
        self.btn_manual_queue_commit.setEnabled(False)
        self.btn_fetch_minute_candles.setMinimumHeight(28)
        for button in (
            self.btn_fetch_minute_candles,
            self.btn_preview_order_candidates,
            self.btn_execution_enable,
            self.btn_real_ready_preflight,
            self.btn_execution_preview,
            self.btn_manual_queue_commit,
            self.btn_manual_send_order,
            self.btn_manual_cancel_pending_order,
            self.btn_manual_modify_pending_order,
        ):
            button.setVisible(False)
        self.btn_set_schedule = QPushButton("환경설정")
        self.btn_stock_register = QPushButton("종목관리")
        self.btn_log_view = QPushButton("종목실적")
        self.btn_review_view = QPushButton("검토관리(0)")
        self.btn_close = QPushButton("닫기")
        for button, object_name in (
            (self.btn_start, "autoTradeSettingStartButton"),
            (self.btn_early_close, "autoTradeSettingEarlyCloseButton"),
            (self.btn_all_stocks, "autoTradeSettingAllStocksButton"),
            (self.btn_preview_order_candidates, "autoTradeSettingPreviewOrderCandidatesButton"),
            (self.btn_execution_enable, "autoTradeSettingExecutionEnableButton"),
            (self.btn_real_ready_preflight, "autoTradeSettingRealReadyPreflightButton"),
            (self.btn_execution_preview, "autoTradeSettingExecutionPreviewButton"),
            (self.btn_manual_send_order, "autoTradeSettingManualSendOrderButton"),
            (self.btn_manual_cancel_pending_order, "autoTradeSettingManualCancelPendingOrderButton"),
            (self.btn_manual_modify_pending_order, "autoTradeSettingManualModifyPendingOrderButton"),
            (self.btn_manual_queue_commit, "autoTradeSettingManualQueueCommitButton"),
            (self.btn_fetch_minute_candles, "autoTradeSettingFetchMinuteCandlesButton"),
            (self.btn_set_schedule, "autoTradeSettingScheduleButton"),
            (self.btn_stock_register, "autoTradeSettingStockRegisterButton"),
            (self.btn_log_view, "autoTradeSettingStockPerformanceButton"),
            (self.btn_review_view, "autoTradeSettingReviewViewButton"),
            (self.btn_close, "autoTradeSettingCloseButton"),
        ):
            button.setObjectName(object_name)
        self._notification_popup = None

        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder
        self._stock_sort_column = -1
        self._stock_sort_order = Qt.AscendingOrder
        # 헤더 정렬 후에는 정렬 규칙이 아니라 "그 순간의 화면 순서"를 보존한다.
        # 설정 변경/조기마감/개별청산 저장 중 종목 위치가 튀는 것을 막기 위한 고정 순서다.
        self._stock_visual_order: list[str] = []
        self._collapsed_auto_trade_definition_ids: set[str] = set()
        self._default_operation_instance_by_definition: dict[str, str] = {}
        self._routine_operation_status_by_instance: dict[str, str] = {}
        # Run one policy reconciliation as soon as startup recovery permits it.
        self._last_time_policy_gui_minute_key = ""
        self._time_policy_timer = QTimer(self)
        self._time_policy_timer.setInterval(10_000)
        self._time_policy_timer.timeout.connect(self.on_time_policy_timer_tick)

        # 외부에서 state/config/orders 파일을 직접 수정한 경우 화면에 자동 반영한다.
        # 예: VSCode에서 보유수량/평단을 임시 입력하면 별도 버튼 없이 종목표가 갱신된다.
        self._runtime_file_snapshot: dict[str, int] = {}
        self._runtime_file_timer = QTimer(self)
        self._runtime_file_timer.setInterval(2_000)
        self._runtime_file_timer.timeout.connect(self.on_runtime_file_timer_tick)
        self._pnl_refresh_timer = QTimer(self)
        self._pnl_refresh_timer.setInterval(PNL_REFRESH_INTERVAL_MS)
        self._pnl_refresh_timer.timeout.connect(self.refresh_stock_pnl_cells)
        self._last_execution_preview_result: dict[str, object] | None = None
        self._last_execution_preview_queue_snapshot: dict[str, object] | None = None
        self._last_execution_enable_preview_result: dict[str, object] | None = None
        self._last_execution_enable_queue_snapshot: dict[str, object] | None = None
        self._last_real_preflight_preview_result: dict[str, object] | None = None
        self._last_real_preflight_queue_snapshot: dict[str, object] | None = None
        self._stock_status_filter = "normal"
        self._selected_stock_normal_projection_active = True
        self._selected_stock_double_click_release_pending = False
        self._last_strategy_workspace_width = 0
        self._collapsed_auto_trade_instance_ids: set[str] = set()
        self._routine_tree_display_level = "stock"
        self._routine_tree_display_scope = ""
        self._routine_tree_last_stock_scope = "all"
        self._routine_tree_display_criterion = "profit"
        self._routine_tree_stock_performance_sort_active = False
        self._routine_tree_valid_only = False
        self._hidden_historical_stock_fixture_keys: set[tuple[str, str]] = set()
        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        self._routine_instance_name_edit_finishing = False
        self._all_stocks_scope_active = False
        self._fixed_signals_connected = False

        self._setup_ui()
        # Initialization contract: font, state, input state, geometry, then signals.
        for control_type in (QPushButton, QLabel, QGroupBox):
            for control in self.findChildren(control_type):
                control.setFont(QFont(control.font()))
        for widget_type in (QPushButton, QLabel, QGroupBox, QTableWidget, QComboBox, QCheckBox):
            for widget in self.findChildren(widget_type):
                widget.setEnabled(widget.isEnabled())
                widget.setVisible(not widget.isHidden())
        for input_type in (QLineEdit, QTextEdit):
            for input_widget in self.findChildren(input_type):
                input_widget.setReadOnly(input_widget.isReadOnly())
        self._apply_initial_strategy_workspace_size()
        self._apply_default_filter_state_for_open()
        self._connect_events()

        self._initializing_open_refresh = True
        try:
            self.refresh_all()
        finally:
            self._initializing_open_refresh = False
            self._auto_trade_initial_read_snapshot = None

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()

        routine_box = QGroupBox("자동매매운영실적")
        routine_box.setObjectName("autoTradeSettingRoutineGroup")
        routine_layout = QVBoxLayout()
        self._setup_routine_table()
        routine_layout.addWidget(self.routine_table)
        routine_box.setLayout(routine_layout)
        routine_box.setMinimumWidth(1000)
        self.routine_box = routine_box
        self._setup_routine_tree_display_level_badges()

        self.stock_box = QGroupBox("등록종목상태")
        self.stock_box.setObjectName("autoTradeSettingStockGroup")
        stock_layout = QVBoxLayout()
        self._setup_selected_routine_status_bar()
        self._setup_stock_table()

        selected_routine_header_layout = QHBoxLayout()
        selected_routine_header_layout.setContentsMargins(0, 0, 0, 0)
        selected_routine_header_layout.addWidget(self.selected_routine_status_bar)
        selected_routine_header_layout.addStretch(1)
        selected_routine_header_layout.addWidget(self.btn_fetch_minute_candles)
        selected_routine_header_layout.addWidget(self.btn_preview_order_candidates)
        selected_routine_header_layout.addWidget(self.btn_execution_enable)
        selected_routine_header_layout.addWidget(self.btn_real_ready_preflight)
        selected_routine_header_layout.addWidget(self.btn_execution_preview)
        selected_routine_header_layout.addSpacing(16)
        selected_routine_header_layout.addWidget(self.btn_manual_queue_commit)
        selected_routine_header_layout.addWidget(self.btn_manual_send_order)
        selected_routine_header_layout.addWidget(self.btn_manual_cancel_pending_order)
        selected_routine_header_layout.addWidget(self.btn_manual_modify_pending_order)
        selected_routine_header_layout.addWidget(self.btn_all_stocks, 0, Qt.AlignVCenter)
        selected_routine_header_layout.addWidget(
            self.all_stocks_command_separator,
            0,
            Qt.AlignVCenter,
        )
        selected_routine_header_layout.addWidget(self.btn_early_close, 0, Qt.AlignVCenter)

        stock_layout.addLayout(selected_routine_header_layout)
        stock_layout.addWidget(self.stock_table)
        self.stock_box.setLayout(stock_layout)
        for group_box in (routine_box, self.stock_box):
            group_box.setAlignment(Qt.AlignLeft)
            group_box.setFlat(False)
            group_box.setStyleSheet(AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE)

        workspace_widget = QWidget()
        workspace_widget.setObjectName("autoTradeSettingStockWorkspace")
        workspace_widget.setMinimumWidth(700)
        self.strategy_workspace_widget = workspace_widget
        workspace_layout = QVBoxLayout()
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(self.stock_box, 1)
        workspace_widget.setLayout(workspace_layout)

        self.strategy_workspace_splitter = QSplitter(Qt.Horizontal)
        self.strategy_workspace_splitter.setObjectName("autoTradeSettingWorkspaceSplitter")
        self.strategy_workspace_splitter.addWidget(routine_box)
        self.strategy_workspace_splitter.addWidget(workspace_widget)
        self.strategy_workspace_splitter.setStretchFactor(0, 0)
        self.strategy_workspace_splitter.setStretchFactor(1, 1)

        buttons = [
            self.btn_start,
            self.btn_set_schedule,
            self.btn_stock_register,
            self.btn_log_view,
            self.btn_review_view,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        # v20.9.1g: 좌우 분할 구조를 상하 구조로 변경한다.
        # 루틴 목록은 상단 요약 영역으로 압축하고, 종목표는 하단 전체 폭을 사용한다.
        main_layout.addWidget(self.strategy_workspace_splitter, 1)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_routine_tree_display_level_badges()
        if not hasattr(self, "strategy_workspace_splitter"):
            return
        current_width = int(self.width())
        grew = current_width > int(getattr(self, "_last_strategy_workspace_width", 0) or 0)
        self._last_strategy_workspace_width = current_width
        if grew or self.isMaximized():
            QTimer.singleShot(0, self._rebalance_strategy_workspace_splitter)

    def _stock_table_required_width(self, last_column: int | None = None) -> int:
        self._apply_stock_table_column_widths()
        if last_column is None:
            column_range = range(self.stock_table.columnCount())
        else:
            column_range = range(min(last_column, self.stock_table.columnCount() - 1) + 1)
        header_width = sum(
            self.stock_table.horizontalHeader().sectionSize(col)
            for col in column_range
        )
        vertical_header_width = self.stock_table.verticalHeader().width()
        if vertical_header_width <= 0:
            vertical_header_width = self.stock_table.verticalHeader().minimumWidth()
        vertical_scroll_width = self.stock_table.verticalScrollBar().sizeHint().width()
        frame_width = self.stock_table.frameWidth() * 2
        return header_width + vertical_header_width + vertical_scroll_width + frame_width

    def _right_workspace_required_width(self, last_stock_column: int | None = None) -> int:
        stock_layout_margins = self.stock_box.layout().contentsMargins()
        stock_box_extra = (
            stock_layout_margins.left()
            + stock_layout_margins.right()
        )
        stock_required_width = self._stock_table_required_width(last_stock_column) + stock_box_extra
        return max(stock_required_width, self.strategy_workspace_widget.minimumWidth())

    def _right_workspace_initial_width(self) -> int:
        return self._right_workspace_required_width(AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN)

    def _apply_initial_strategy_workspace_size(self) -> None:
        self._apply_stock_table_column_widths()
        main_layout = self.layout()
        margins = main_layout.contentsMargins() if main_layout is not None else None
        outer_width = (margins.left() + margins.right()) if margins is not None else 0
        left_width = self.routine_box.minimumWidth()
        right_width = self._right_workspace_initial_width()
        handle_width = self.strategy_workspace_splitter.handleWidth()
        initial_width = left_width + right_width + handle_width + outer_width
        self.setMinimumSize(initial_width, 650)
        self.resize(initial_width, 680)
        self.strategy_workspace_splitter.setSizes([left_width, right_width])
        self._last_strategy_workspace_width = initial_width
        QTimer.singleShot(0, self._rebalance_strategy_workspace_splitter)

    def _rebalance_strategy_workspace_splitter(self) -> None:
        if not hasattr(self, "strategy_workspace_splitter"):
            return
        available_width = self.strategy_workspace_splitter.width()
        if available_width <= 0:
            return
        handle_width = self.strategy_workspace_splitter.handleWidth()
        left_minimum_width = self.routine_box.minimumWidth()
        right_required_width = self._right_workspace_required_width()
        right_width = min(
            right_required_width,
            max(self.strategy_workspace_widget.minimumWidth(), available_width - left_minimum_width - handle_width),
        )
        left_width = max(left_minimum_width, available_width - right_width - handle_width)
        current_sizes = self.strategy_workspace_splitter.sizes()
        if len(current_sizes) == 2 and current_sizes[1] >= right_required_width:
            return
        if right_width > 0 and left_width > 0:
            self.strategy_workspace_splitter.setSizes([left_width, right_width])

    def _setup_selected_routine_status_bar(self) -> None:
        self.selected_routine_status_bar = QWidget()
        self.selected_routine_status_bar.setObjectName("autoTradeSettingSelectedRoutineStatusBar")
        self.selected_routine_status_bar.setFixedHeight(AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
        layout = QHBoxLayout(self.selected_routine_status_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.selected_routine_signal_label = QLabel("●")
        self.selected_routine_signal_label.setObjectName("autoTradeSettingSelectedRoutineSignal")
        self.selected_routine_signal_label.setAlignment(Qt.AlignCenter)
        self.selected_routine_signal_label.setFixedWidth(18)
        self.selected_routine_signal_label.setStyleSheet("background: transparent; color: #6B7280;")
        layout.addWidget(self.selected_routine_signal_label, 0, Qt.AlignVCenter)

        self.selected_routine_name_button = QPushButton("-")
        self.selected_routine_name_button.setObjectName("autoTradeSettingSelectedRoutineNameButton")
        self.selected_routine_name_button.setFlat(True)
        self.selected_routine_name_button.setFocusPolicy(Qt.NoFocus)
        self.selected_routine_name_button.setCursor(Qt.PointingHandCursor)
        self.selected_routine_name_button.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #111827; "
            "font-weight: 600; padding: 0 2px; text-align: left; }"
            "QPushButton:hover { color: #1D4ED8; }"
        )
        self.selected_routine_name_button.clicked.connect(lambda: self.set_stock_status_filter("all"))
        layout.addWidget(self.selected_routine_name_button, 0, Qt.AlignVCenter)

        self.selected_routine_group_count_badge = QLabel("")
        self.selected_routine_group_count_badge.setObjectName(
            "autoTradeSettingSelectedRoutineGroupCount"
        )
        self.selected_routine_group_count_badge.setAlignment(Qt.AlignCenter)
        self.selected_routine_group_count_badge.setFixedWidth(64)
        self.selected_routine_group_count_badge.setStyleSheet(
            "background-color: transparent; color: #7E22CE; border: 1px solid #C084FC; "
            "border-radius: 3px; padding: 0 3px;"
        )
        self.selected_routine_group_count_badge.hide()
        layout.addWidget(self.selected_routine_group_count_badge, 0, Qt.AlignVCenter)

        self.selected_routine_instance_count_badge = QLabel("")
        self.selected_routine_instance_count_badge.setObjectName("autoTradeSettingSelectedRoutineInstanceCount")
        self.selected_routine_instance_count_badge.setAlignment(Qt.AlignCenter)
        self.selected_routine_instance_count_badge.setFixedWidth(64)
        self.selected_routine_instance_count_badge.setStyleSheet(
            "background-color: transparent; color: #7E22CE; border: 1px solid #C084FC; "
            "border-radius: 3px; padding: 0 3px;"
        )
        layout.addWidget(self.selected_routine_instance_count_badge, 0, Qt.AlignVCenter)

        self.selected_routine_status_buttons: dict[str, QPushButton] = {}
        for filter_key, object_name in (
            ("all", "autoTradeSettingSelectedRoutineRegistered"),
            ("operation", "autoTradeSettingSelectedRoutineRunning"),
            ("waiting", "autoTradeSettingSelectedRoutineWaiting"),
            ("excluded", "autoTradeSettingSelectedRoutineExcluded"),
            ("review", "autoTradeSettingSelectedRoutineError"),
        ):
            button = QPushButton()
            button.setObjectName(object_name)
            button.setFlat(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; color: #374151; padding: 0 2px; }"
                "QPushButton:hover { color: #1D4ED8; text-decoration: underline; }"
            )
            button.clicked.connect(lambda _checked=False, key=filter_key: self.set_stock_status_filter(key))
            if filter_key == "all" and isinstance(self, AutoTradeSettingWindow):
                button.installEventFilter(self)
            self.selected_routine_status_buttons[filter_key] = button
            layout.addWidget(button, 0, Qt.AlignVCenter)

        layout.addStretch(1)
        self.update_selected_routine_status_bar()

    def set_stock_status_filter(self, filter_key: str) -> None:
        normalized = str(filter_key or "all").strip().lower()
        normalized = {
            "running": "operation",
            "stopped": "waiting",
            "error": "review",
        }.get(normalized, normalized)
        if normalized not in {"all", "operation", "waiting", "excluded", "review"}:
            normalized = "operation"
        self._stock_status_filter = (
            "normal"
            if normalized == "all"
            and bool(getattr(self, "_selected_stock_normal_projection_active", False))
            else normalized
        )
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def _toggle_selected_stock_normal_projection(self) -> None:
        """Toggle operation/waiting projection in the selected-stock table only."""
        projection_active = not bool(
            getattr(self, "_selected_stock_normal_projection_active", False)
        )
        self._selected_stock_normal_projection_active = projection_active
        self._stock_status_filter = "normal" if projection_active else "all"
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def update_selected_routine_status_bar(self) -> None:
        if not hasattr(self, "selected_routine_status_bar"):
            return
        all_stocks_scope_active = bool(
            getattr(self, "_all_stocks_scope_active", False)
        )
        stock_display_scope_active = (
            str(getattr(self, "_routine_tree_display_level", "") or "") == "stock"
        )
        all_stocks_badge_color = (
            AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
            if all_stocks_scope_active
            else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
        )
        all_stocks_button = getattr(self, "btn_all_stocks", None)
        if all_stocks_button is not None:
            all_stocks_button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=all_stocks_badge_color,
                    border_color=all_stocks_badge_color,
                )
            )
        if all_stocks_scope_active or stock_display_scope_active:
            counts = (
                self._stock_display_scope_summary()
                if stock_display_scope_active
                else self._all_stocks_scope_summary()
            )
            self.selected_routine_signal_label.hide()
            self.selected_routine_name_button.setText("전체")
            self.selected_routine_group_count_badge.setText(
                f"그룹({counts['groups']})"
            )
            self.selected_routine_group_count_badge.show()
            self.selected_routine_instance_count_badge.setText(
                f"루틴({counts['routines']})"
            )
            self.selected_routine_instance_count_badge.show()
        else:
            self.selected_routine_signal_label.show()
            self.selected_routine_group_count_badge.setText("")
            self.selected_routine_group_count_badge.hide()
            counts = None
        if counts is None:
            metadata = self.current_selected_routine_row_metadata()
            if not metadata:
                self.selected_routine_signal_label.setText("●")
                self.selected_routine_name_button.setText("-")
                self.selected_routine_instance_count_badge.setText("")
                self.selected_routine_instance_count_badge.hide()
                counts = {
                    "registered": 0,
                    "operation_running": 0,
                    "waiting": 0,
                    "excluded": 0,
                    "review": 0,
                }
            else:
                row_kind = str(metadata.get("row_kind", "") or "")
                self.selected_routine_signal_label.setText("●")
                if row_kind in {"instance", "stock"}:
                    self.selected_routine_name_button.setText(str(metadata.get("instance_name", "") or "-"))
                    self.selected_routine_instance_count_badge.setText("")
                    self.selected_routine_instance_count_badge.hide()
                    if row_kind == "stock":
                        instance_id = str(metadata.get("instance_id", "") or "").strip()
                        instance_counts = self._routine_instance_operation_counts().get(
                            instance_id,
                            {
                                "registered": 0,
                                "operation_running": 0,
                                "waiting": 0,
                                "excluded": 0,
                                "review": 0,
                            },
                        )
                        counts = {
                            "registered": int(instance_counts.get("registered", 0) or 0),
                            "operation_running": int(instance_counts.get("operation_running", 0) or 0),
                            "waiting": int(instance_counts.get("waiting", 0) or 0),
                            "excluded": int(instance_counts.get("excluded", 0) or 0),
                            "review": int(instance_counts.get("review", instance_counts.get("error", 0)) or 0),
                        }
                    else:
                        counts = None
                else:
                    self.selected_routine_name_button.setText(str(metadata.get("definition_name", "") or "-"))
                    self.selected_routine_instance_count_badge.setText("")
                    self.selected_routine_instance_count_badge.hide()
                    counts = None
                if counts is None:
                    counts = {
                        "registered": int(metadata.get("registered", 0) or 0),
                        "operation_running": int(metadata.get("operation_running", 0) or 0),
                        "waiting": int(metadata.get("waiting", 0) or 0),
                        "excluded": int(metadata.get("excluded", 0) or 0),
                        "review": int(metadata.get("review", metadata.get("error", 0)) or 0),
                    }
        button_texts = {
            "all": (
                f"종목({counts['operation_running'] + counts['waiting']})"
                if bool(
                    getattr(
                        self,
                        "_selected_stock_normal_projection_active",
                        False,
                    )
                )
                else f"종목({counts['registered']})"
            ),
            "operation": f"운영({counts['operation_running']})",
            "waiting": f"대기({counts['waiting']})",
            "excluded": f"제외({counts['excluded']})",
            "review": f"검토({counts['review']})",
        }
        current_filter = str(getattr(self, "_stock_status_filter", "all") or "all")
        current_filter = {
            "running": "operation",
            "stopped": "waiting",
            "error": "review",
        }.get(current_filter, current_filter)
        if current_filter == "normal":
            current_filter = "all"
        for key, button in self.selected_routine_status_buttons.items():
            button.setText(button_texts[key])
            is_active = key == current_filter
            projection_active = bool(
                getattr(self, "_selected_stock_normal_projection_active", False)
            )
            active_color = (
                CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR
                if key == "all" and projection_active
                else "#1D4ED8"
            )
            text_color = (
                CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR
                if key == "all" and projection_active
                else active_color if is_active else "#374151"
            )
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; "
                f"color: {text_color}; "
                f"font-weight: {'600' if is_active else '400'}; padding: 0 2px; }}"
                "QPushButton:hover { color: #1D4ED8; text-decoration: underline; }"
            )

    def _stock_operation_status_label(self) -> str:
        try:
            operation_status = _today_global_operation_status(read_operation_state())
        except Exception:
            operation_status = ""
        return "운영" if operation_status in {"RUNNING", "CLOSING"} else "정지"

    def _all_stocks_scope_summary(self) -> dict[str, int]:
        summary = {
            "groups": 0,
            "routines": 0,
            "registered": 0,
            "normal": 0,
            "operation_running": 0,
            "waiting": 0,
            "excluded": 0,
            "review": 0,
        }
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("row_kind", "") or "") != "definition":
                continue
            summary["groups"] += 1
            summary["routines"] += int(metadata.get("instance_count", 0) or 0)
            for key in (
                "registered", "normal", "operation_running", "waiting",
                "excluded", "review",
            ):
                summary[key] += int(metadata.get(key, 0) or 0)
        return summary

    def _stock_display_scope_summary(self) -> dict[str, int]:
        """Summarize the full registered-stock scope for the flat Stock view."""
        summary = {
            "groups": 0,
            "routines": 0,
            "registered": 0,
            "normal": 0,
            "operation_running": 0,
            "waiting": 0,
            "excluded": 0,
            "review": 0,
        }
        snapshot = auto_trade_initial_read_snapshot(self)
        loaded_instances = (
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        instances = {
            str(instance.instance_id or "").strip(): instance
            for instance in loaded_instances
            if str(instance.instance_id or "").strip()
        }
        counts_by_instance = self._routine_instance_operation_counts()
        group_ids: set[str] = set()
        for instance_id, instance in instances.items():
            counts = counts_by_instance.get(instance_id, {})
            if int(counts.get("registered", 0) or 0) <= 0:
                continue
            group_id = str(getattr(instance, "group_id", "") or "").strip()
            if group_id:
                group_ids.add(group_id)
            summary["routines"] += 1
            for key in (
                "registered", "normal", "operation_running", "waiting",
                "excluded", "review",
            ):
                summary[key] += int(
                    counts.get(
                        key,
                        counts.get("error", 0) if key == "review" else 0,
                    )
                    or 0
                )
        summary["groups"] = len(group_ids)
        return summary

    def all_registered_instance_ids(self) -> tuple[str, ...]:
        snapshot = auto_trade_initial_read_snapshot(self)
        instances = (
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        return tuple(
            str(instance.instance_id)
            for instance in instances
            if str(instance.instance_id or "").strip()
        )

    def show_all_registered_stocks(self) -> None:
        """전체 그룹/루틴의 등록 종목을 조회하는 전용 Production Caller."""
        was_blocked = self.routine_table.blockSignals(True)
        try:
            self.routine_table.clearSelection()
        finally:
            self.routine_table.blockSignals(was_blocked)
        self._all_stocks_scope_active = True
        self.update_selection_summary_panel()
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def _apply_default_filter_state_for_open(self) -> None:
        """Apply the open-time filter contract without rebuilding either table."""
        self._routine_tree_valid_only = False
        self._routine_tree_display_level = "stock"
        self._routine_tree_display_scope = "all"
        self._routine_tree_last_stock_scope = "all"
        self._routine_tree_display_criterion = "profit"
        self._routine_tree_stock_performance_sort_active = False
        self._selected_stock_normal_projection_active = True
        self._stock_status_filter = "normal"
        self._all_stocks_scope_active = True
        self._apply_routine_tree_display_level_command("stock")
        self._update_routine_tree_display_level_badges()
        was_blocked = self.routine_table.blockSignals(True)
        try:
            self.routine_table.clearSelection()
        finally:
            self.routine_table.blockSignals(was_blocked)

    def reset_default_filters_for_open(self) -> None:
        """창을 열 때마다 자동매매설정 기본 필터 계약을 적용한다."""
        self._apply_default_filter_state_for_open()
        self.refresh_all()

    def update_selection_summary_panel(self) -> None:
        return

    def _setup_routine_table(self) -> None:
        headers = [
            "루틴명",
            "종목수",
            "총예산",
            "사용예산",
            "가용예산",
        ]

        routine_table_font = QFont(self.routine_table.font())
        self.routine_table.setFont(routine_table_font)
        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.routine_table)
        header = self.routine_table.horizontalHeader()
        header.setObjectName("autoTradeSettingRoutineHeader")
        header.setFont(routine_table_font)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setHighlightSections(False)
        header.setSectionsClickable(False)
        header.setSectionsMovable(False)
        header.setSortIndicatorShown(False)
        for col in range(len(headers)):
            header_item = self.routine_table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setFont(routine_table_font)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.routine_table.setColumnWidth(0, 220)
        self.routine_table.setColumnWidth(1, 90)
        self.routine_table.setColumnWidth(2, 140)
        self.routine_table.setColumnWidth(3, 140)
        self.routine_table.setColumnWidth(4, 140)
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.routine_table.setFocusPolicy(Qt.StrongFocus)
        self.routine_table.setTabKeyNavigation(True)
        self.routine_table.setMouseTracking(False)
        self.routine_table.viewport().setMouseTracking(False)
        self.routine_table.setAttribute(Qt.WA_Hover, False)
        self.routine_table.viewport().setAttribute(Qt.WA_Hover, False)
        self.routine_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerItem)
        self.routine_table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.routine_table.setSortingEnabled(False)
        self.routine_table.setColumnCount(1)
        self.routine_table.horizontalHeader().hide()
        self.routine_table.verticalHeader().hide()
        self.routine_table.setShowGrid(False)
        self.routine_table.setAlternatingRowColors(False)
        self.routine_table.setWordWrap(False)
        self.routine_table.setTextElideMode(Qt.ElideRight)
        self.routine_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.routine_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.routine_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.routine_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.routine_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.routine_table.horizontalHeader().setStretchLastSection(True)
        self.routine_table.verticalHeader().setMinimumSectionSize(22)
        self.routine_table.setMinimumWidth(970)
        self.routine_table.setColumnWidth(0, 950)
        self.routine_table.setStyleSheet(
            """
            QTableWidget {
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QTableWidget::item:selected {
                background: #dbeafe;
                color: #111827;
            }
            QTableWidget::item:focus {
                outline: 0;
            }
            """
        )

    def _setup_stock_table(self) -> None:
        headers = [
            "코드",
            "종목",
            "운영",
            "현황",
            "상태",
            "방식",
            "청산",
            "보유",
            "가격",
            "손익",
            "매매",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.stock_table)
        header = self.stock_table.horizontalHeader()
        header.setObjectName("autoTradeSettingStockHeader")
        header.setProperty(PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY, True)
        vertical_header = self.stock_table.verticalHeader()
        stock_table_grid_color = REGISTERED_STOCK_STATUS_GRID_COLOR
        header.setProperty(PLAIN_HEADER_GRID_COLOR_PROPERTY, stock_table_grid_color)
        header_font = QFont(self.stock_table.font())
        header.setFont(header_font)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setSectionsMovable(False)
        header.setSortIndicatorShown(False)
        for col in range(len(headers)):
            header_item = self.stock_table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setFont(header_font)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.stock_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.stock_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.stock_table.setShowGrid(True)
        self.stock_table.setGridStyle(Qt.SolidLine)
        header.setMinimumSectionSize(30)
        vertical_header.setSectionsMovable(False)
        vertical_header.setDefaultAlignment(Qt.AlignCenter)
        vertical_header.setSectionResizeMode(QHeaderView.Fixed)
        vertical_header.setMinimumWidth(40)
        vertical_header.setMaximumWidth(40)
        vertical_header.setFixedWidth(40)
        self._stock_position_metric_delegate = StockPositionMetricDelegate(self.stock_table)
        self._stock_selected_text_delegate = SelectedTextReadableDelegate(self.stock_table)
        for col in (2, 4, 5, 6):
            self.stock_table.setItemDelegateForColumn(
                col,
                self._stock_selected_text_delegate,
            )
        for col in (7, 8, 9, 10):
            self.stock_table.setItemDelegateForColumn(
                col,
                self._stock_position_metric_delegate,
            )

        # 자동매매설정창 하단 종목표 고정폭 배분.
        # 보유/가격/손익/매매는 관제 트리와 같은 묶음 단위로 표시한다.
        self._apply_stock_table_column_widths()
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        body_background = self.stock_table.viewport().palette().color(QPalette.Base).name()
        self.stock_table.setStyleSheet(
            registered_stock_status_table_stylesheet(
                self.stock_table.objectName(),
                body_background,
            )
        )
        self._sync_stock_table_header_background_to_body()
        self.stock_table.setWordWrap(True)
        self.stock_table.setTextElideMode(Qt.ElideRight)
        self.stock_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stock_table.setFocusPolicy(Qt.StrongFocus)
        self.stock_table.setTabKeyNavigation(True)
        self.stock_table.setMouseTracking(False)
        self.stock_table.viewport().setMouseTracking(False)
        self.stock_table.setAttribute(Qt.WA_Hover, False)
        self.stock_table.viewport().setAttribute(Qt.WA_Hover, False)
        self.stock_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerItem)
        self.stock_table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.stock_table.setSortingEnabled(False)
        QTimer.singleShot(0, self._apply_stock_table_column_widths)

    def _sync_stock_table_header_background_to_body(self) -> None:
        """등록종목상태 표의 header 빈 영역을 body 배경과 맞춘다."""
        body_color = self.stock_table.viewport().palette().color(QPalette.Base)

        header = self.stock_table.horizontalHeader()
        header.setProperty(PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY, True)
        header.setProperty(
            PLAIN_HEADER_GRID_COLOR_PROPERTY,
            REGISTERED_STOCK_STATUS_GRID_COLOR,
        )
        header.viewport().update()

        vertical_header = self.stock_table.verticalHeader()
        vertical_header.setDefaultAlignment(Qt.AlignCenter)
        vertical_palette = QPalette(vertical_header.palette())
        for role in (QPalette.Button, QPalette.Window, QPalette.Base):
            vertical_palette.setColor(role, body_color)
        vertical_header.setPalette(vertical_palette)
        vertical_header.viewport().setPalette(vertical_palette)
        vertical_header.viewport().update()

    def _apply_stock_table_column_widths(self) -> None:
        """자동매매설정창 하단 종목표 컬럼 폭을 강제로 재적용한다."""
        self.stock_table.verticalHeader().setMinimumWidth(40)
        self.stock_table.verticalHeader().setMaximumWidth(40)
        self.stock_table.verticalHeader().setFixedWidth(40)
        header = self.stock_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        for col, width in AUTO_TRADE_SETTING_STOCK_TABLE_COLUMN_WIDTHS.items():
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            header.resizeSection(col, width)
            self.stock_table.setColumnWidth(col, width)

    def _connect_events(self) -> None:
        if self._fixed_signals_connected:
            return
        self.routine_table.itemSelectionChanged.connect(self.on_routine_selection_changed)
        self.routine_table.itemClicked.connect(self.on_routine_table_item_clicked)
        self.routine_table.customContextMenuRequested.connect(self.on_routine_table_context_menu)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_routine_table_by_column)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.horizontalHeader().sectionClicked.connect(self.sort_stock_table_by_column)
        self.stock_table.itemDoubleClicked.connect(self.on_stock_table_item_double_clicked)
        self.stock_table.itemDoubleClicked.connect(
            self.on_stock_table_name_item_double_clicked
        )
        self.stock_table.itemDoubleClicked.connect(
            self.on_stock_table_code_item_double_clicked
        )
        self.stock_table.customContextMenuRequested.connect(self.on_stock_table_context_menu)
        self.btn_close.clicked.connect(self.close)
        self.btn_start.clicked.connect(self.start_selected_auto_trades)
        self.btn_fetch_minute_candles.clicked.connect(self.fetch_minute_candles_for_selected_stock)
        self.btn_preview_order_candidates.clicked.connect(self.preview_order_candidates_for_pending_signals)
        self.btn_execution_enable.clicked.connect(self.enable_execution_candidate_manually)
        self.btn_real_ready_preflight.clicked.connect(self.run_real_ready_preflight_manually)
        self.btn_execution_preview.clicked.connect(self.preview_execution_for_real_ready_order_manual)
        self.btn_manual_queue_commit.clicked.connect(self.commit_last_execution_preview_queue_manually)
        self.btn_manual_send_order.clicked.connect(self.send_order_for_order_queued_manually)
        self.btn_manual_cancel_pending_order.clicked.connect(self.cancel_pending_order_manually)
        self.btn_manual_modify_pending_order.clicked.connect(self.modify_pending_order_manually)
        self.btn_all_stocks.clicked.connect(self.show_all_registered_stocks)
        self.btn_early_close.clicked.connect(self.apply_selected_early_close_default)
        self.btn_set_schedule.clicked.connect(self.open_operation_environment_settings)
        self.btn_stock_register.clicked.connect(self.open_stock_register_window)
        self.btn_log_view.clicked.connect(self.open_stock_performance_window)
        self.btn_review_view.clicked.connect(self.open_review_required_window)
        self._fixed_signals_connected = True

    def sort_routine_table_by_column(self, column: int) -> None:
        """상단 루틴표 헤더 클릭 정렬."""
        return

    def capture_stock_visual_order(self) -> list[str]:
        """현재 하단 종목표에 보이는 행 순서를 종목 runtime 경로 기준으로 저장한다."""
        order: list[str] = []
        seen: set[str] = set()
        for row in range(self.stock_table.rowCount()):
            path_text = ""
            for col in range(self.stock_table.columnCount()):
                item = self.stock_table.item(row, col)
                if item is None:
                    continue
                value = item.data(Qt.UserRole)
                if value:
                    path_text = str(value)
                    break
            if path_text and path_text not in seen:
                order.append(path_text)
                seen.add(path_text)
        return order

    def sort_stock_table_by_column(self, column: int) -> None:
        """하단 종목표 헤더 클릭 정렬.

        헤더 클릭 순간에만 정렬 규칙을 적용하고, 그 결과 화면 순서를 고정한다.
        이후 설정 변경/조기마감/개별청산 저장으로 표가 다시 로딩되어도
        정렬 규칙을 재적용하지 않고 이 화면 순서를 유지한다.
        """
        if column < 0 or column >= self.stock_table.columnCount():
            return

        if self._stock_sort_column == column:
            self._stock_sort_order = (
                Qt.DescendingOrder
                if self._stock_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._stock_sort_column = column
            self._stock_sort_order = Qt.AscendingOrder

        selected_paths = set()
        for row in self.selected_stock_rows():
            item = self.stock_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole):
                selected_paths.add(str(item.data(Qt.UserRole)))

        self.stock_table.sortItems(column, self._stock_sort_order)
        self._stock_visual_order = self.capture_stock_visual_order()

        if selected_paths:
            self.stock_table.clearSelection()
            for row in range(self.stock_table.rowCount()):
                item = self.stock_table.item(row, 0)
                if item is not None and str(item.data(Qt.UserRole)) in selected_paths:
                    self.stock_table.selectRow(row)
        self.update_action_buttons()

    def apply_auto_trade_table_sorts(self) -> None:
        """목록 갱신 후 상단 루틴표 정렬만 재적용한다.

        하단 종목표는 헤더 클릭 시점의 화면 순서를 고정 보존한다.
        refresh/load 중 stock_table.sortItems()를 재실행하면 작업 중인 종목이
        정렬 규칙에 따라 이동하므로 여기서는 재정렬하지 않는다.
        """
        if self._routine_sort_column >= 0:
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)

    def _active_initial_read_snapshot(self) -> dict[str, object] | None:
        return auto_trade_initial_read_snapshot(self)

    def _build_initial_read_snapshot(self) -> dict[str, object]:
        """Read one coherent data set for this window's first refresh only."""
        definitions = tuple(load_routine_definitions())
        instances = tuple(load_persisted_routine_instances())
        groups = tuple(get_group_records())
        stocks = tuple(read_base_stocks())
        stock_data_by_dir: dict[str, dict[str, object]] = {}
        count_stocks: list[dict[str, object]] = []

        for stock in stocks:
            stock_path = str(stock.get("stock_path", "") or "").strip()
            if not stock_path:
                continue
            stock_dir = PROJECT_ROOT / stock_path
            config = read_json_dict(stock_dir / "config.json")
            state, state_issue_reason = _read_central_review_state(
                stock_dir / "state.json"
            )
            stock_data_by_dir[str(stock_dir)] = {
                "config": dict(config),
                "state": dict(state),
                "state_issue_reason": state_issue_reason,
                "orders": read_orders_data(stock_dir / "orders.json"),
            }
            instance_id = str(
                stock.get("assigned_routine_instance_id", "")
                or config.get("assigned_routine_instance_id", "")
                or ""
            ).strip()
            if not instance_id:
                continue
            raw_routines = stock.get("routines", ())
            routines = (
                tuple(
                    str(item or "").strip()
                    for item in raw_routines
                    if str(item or "").strip()
                )
                if isinstance(raw_routines, (list, tuple, set))
                else tuple()
            )
            count_stocks.append(
                {
                    "stock_path": stock_path,
                    "stock_dir": stock_dir,
                    "stock_dir_key": str(stock_dir),
                    "instance_id": instance_id,
                    "operation_excluded": is_operation_excluded(config),
                    "code": str(stock.get("code", "") or "").strip(),
                    "name": str(stock.get("name", "") or "").strip(),
                    "enabled": bool(stock.get("enabled", True)),
                    "routines": routines,
                    "assigned_routine_instance_id": instance_id,
                }
            )

        return {
            "definitions": definitions,
            "instances": instances,
            "groups": groups,
            "stocks": stocks,
            "stock_data_by_dir": stock_data_by_dir,
            "assignment_history": tuple(
                StockRepository(PROJECT_ROOT).list_routine_assignment_history()
            ),
            "count_static_data": {
                "definitions": definitions,
                "instances": instances,
                "stocks": tuple(count_stocks),
            },
        }

    def refresh_all(self) -> None:
        # 자동매매설정 창 전체 갱신 전 하단 종목표 위치를 보존한다.
        # 시간변경/운영시작 등 상태 갱신 후 종목표가 맨 위로 튀는 문제를 막는다.
        selected_stock_paths, stock_scroll_value = self.capture_stock_table_view_state()

        initial_refresh = bool(getattr(self, "_initializing_open_refresh", False))
        normalize_base_stock_single_routine_file()
        ensure_single_real_trade_routine_for_all_stocks()
        if initial_refresh:
            self._auto_trade_initial_read_snapshot = (
                self._build_initial_read_snapshot()
            )
        selected_routine_metadata = self.current_selected_routine_row_metadata()
        all_stocks_scope_active = bool(
            getattr(self, "_all_stocks_scope_active", False)
        )
        routine_selection_blocker = QSignalBlocker(self.routine_table)
        self.routine_table.setUpdatesEnabled(False)
        try:
            self.load_routine_table()

            if selected_routine_metadata:
                self.restore_routine_selection_metadata(selected_routine_metadata)

            if (
                not all_stocks_scope_active
                and self.current_selected_routine_row_metadata() is None
                and self.routine_table.rowCount() > 0
            ):
                self.routine_table.selectRow(0)
        finally:
            del routine_selection_blocker
            self.routine_table.setUpdatesEnabled(True)
            self.routine_table.viewport().update()

        self.update_selected_routine_status_bar()
        self._defer_stock_loader_action_update = True
        try:
            self.load_selected_routine_stocks()
        finally:
            self._defer_stock_loader_action_update = False
        self.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
        self._runtime_file_snapshot = self.current_runtime_file_signature()
        self.update_review_required_button_text()
        self.update_action_buttons()

    def review_required_stock_count(self) -> int:
        """검토관리창과 동일 Collector 기준으로 대상 종목 수를 계산한다."""
        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is None:
            return len(collect_global_review_required_rows())
        return len(
            collect_global_review_required_rows(
                preloaded_stocks=snapshot.get("stocks", ()),
                preloaded_stock_data_by_dir=snapshot.get(
                    "stock_data_by_dir",
                    {},
                ),
            )
        )

    def update_review_required_button_text(self) -> None:
        if not hasattr(self, "btn_review_view"):
            return
        self.btn_review_view.setText(f"검토관리({self.review_required_stock_count()})")

    def current_time_policy_minute_key(self) -> str:
        return auto_trade_current_time_policy_minute_key(self)

    def current_runtime_file_signature(self) -> tuple[tuple[str, int, int], ...]:
        return auto_trade_current_runtime_file_signature(self)

    def on_runtime_file_timer_tick(self) -> None:
        previous_snapshot = self._runtime_file_snapshot
        try:
            auto_trade_on_runtime_file_timer_tick(self)
        except Exception:
            LOGGER.exception("Runtime file timer refresh failed")
            self._runtime_file_snapshot = previous_snapshot
            self.statusBarMessage(
                "Runtime 상태를 갱신하지 못했습니다. "
                "로그를 확인한 뒤 Recovery를 다시 실행하십시오."
            )

    def on_time_policy_timer_tick(self) -> None:
        previous_minute_key = self._last_time_policy_gui_minute_key
        try:
            auto_trade_on_time_policy_gui_timer_tick(self)
        except Exception:
            LOGGER.exception("Time policy GUI timer refresh failed")
            self._last_time_policy_gui_minute_key = previous_minute_key
            self.statusBarMessage(
                "시간정책 상태를 갱신하지 못했습니다. "
                "로그를 확인한 뒤 Recovery를 다시 실행하십시오."
            )

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        parent = persistent_feature_owner(self)
        checker = getattr(parent, "startup_recovery_session_ready", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(refresh=refresh))
        except Exception:
            return False

    def rebind_startup_recovery_after_trusted_runtime_update(self) -> bool:
        parent = persistent_feature_owner(self)
        rebind = getattr(
            parent,
            "rebind_startup_recovery_after_trusted_runtime_update",
            None,
        )
        if not callable(rebind):
            return False
        try:
            return bool(rebind())
        except Exception:
            return False

    def require_startup_recovery_session(self, action: str) -> bool:
        if self.startup_recovery_session_ready(refresh=True):
            self._last_operation_block_reason = ""
            return True
        parent = persistent_feature_owner(self)
        reason_getter = getattr(parent, "startup_recovery_block_reason", None)
        reason = ""
        if callable(reason_getter):
            try:
                reason = str(reason_getter() or "").strip()
            except Exception:
                reason = ""
        self._last_operation_block_reason = reason or "RECOVERY_NOT_READY"
        message = startup_recovery_operation_block_message(action, reason)
        self.statusBarMessage(message)
        self.update_startup_recovery_controls()
        return False

    def start_target_is_review_isolated(
        self,
        stock_dir: Path,
        stock_code: str,
    ) -> bool:
        if is_review_required_stock_dir(stock_dir):
            return True
        parent = persistent_feature_owner(self)
        checker = getattr(
            parent,
            "production_recovery_stock_is_review_required",
            None,
        )
        return bool(checker(stock_code)) if callable(checker) else False

    def filter_start_targets_by_recovery(
        self,
        targets: list[tuple[Path, str, str]],
        *,
        action: str,
    ) -> dict[str, object]:
        parent = persistent_feature_owner(self)
        filter_targets = getattr(
            parent,
            "filter_start_targets_by_production_recovery",
            None,
        )
        if callable(filter_targets):
            result = filter_targets(targets, caller_name=action)
            if result.get("allowed") is not True:
                reason = str(result.get("reason") or "RECOVERY_NOT_READY")
                user_message = str(result.get("user_message") or "").strip()
                self._last_operation_block_reason = reason
                self._last_operation_user_message = (
                    user_message
                    or startup_recovery_operation_block_message(action, reason)
                )
                self.statusBarMessage(self._last_operation_user_message)
            return result
        if self.require_startup_recovery_session(action):
            return {
                "allowed": True,
                "reason": "RECOVERY_COMPLETED",
                "eligible": tuple(targets),
                "excluded_review": (),
            }
        return {
            "allowed": False,
            "reason": self._last_operation_block_reason or "RECOVERY_NOT_READY",
            "eligible": (),
            "excluded_review": (),
        }

    def update_startup_recovery_controls(self) -> None:
        ready = self.startup_recovery_session_ready(refresh=False)
        for button in (
            self.btn_execution_enable,
            self.btn_real_ready_preflight,
            self.btn_execution_preview,
            self.btn_manual_send_order,
            self.btn_manual_cancel_pending_order,
            self.btn_manual_modify_pending_order,
        ):
            button.setEnabled(ready)
        if ready:
            self.update_manual_queue_commit_button_state()
        else:
            self.btn_manual_queue_commit.setEnabled(False)
        self.update_global_operation_button_state()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for timer in (self._time_policy_timer, self._runtime_file_timer, self._pnl_refresh_timer):
            if not timer.isActive():
                timer.start()

    def start_periodic_timers_after_recovery(self, identity) -> dict[str, object]:
        """Compatibility hook: these timers refresh the settings UI only."""
        del identity
        started_count = 0
        if self.isVisible():
            for timer in (self._time_policy_timer, self._runtime_file_timer, self._pnl_refresh_timer):
                if timer.isActive():
                    continue
                timer.start()
                started_count += 1
        return {
            "started": True,
            "reason_code": "SETTINGS_GUI_TIMERS_STARTED",
            "started_count": started_count,
        }

    def stop_periodic_timers_for_recovery(self) -> dict[str, object]:
        """Stop settings-only GUI refresh timers."""
        stopped_count = 0
        for timer in (self._time_policy_timer, self._runtime_file_timer, self._pnl_refresh_timer):
            if not timer.isActive():
                continue
            timer.stop()
            stopped_count += 1
        return {
            "stopped": True,
            "reason_code": "SETTINGS_GUI_TIMERS_STOPPED",
            "stopped_count": stopped_count,
        }

    def closeEvent(self, event) -> None:
        """창을 닫을 때 주기 갱신 타이머를 정리한다."""
        self.stop_periodic_timers_for_recovery()
        super().closeEvent(event)

    def refresh_stock_pnl_cells(self) -> None:
        """Update only PnL cells; never rebuild the settings table."""
        for row in range(self.stock_table.rowCount()):
            code_item = self.stock_table.item(row, 0)
            pnl_item = self.stock_table.item(row, 9)
            if code_item is None or pnl_item is None:
                continue
            result = project_current_stock_pnl(code_item.text(), project_root=PROJECT_ROOT)
            if result.get("available") is not True:
                continue
            profit_metric, amount, _rate = confirmable_stock_profit_metric(result)
            text = ratio_metric_text(profit_metric)
            if pnl_item.text() != text:
                pnl_item.setText(text)
            color = QColor(profit_loss_value_color(amount))
            if pnl_item.foreground().color() != color:
                pnl_item.setForeground(color)

    def capture_stock_table_view_state(self) -> tuple[set[str], int]:
        """하단 종목표의 선택 종목 경로와 세로 스크롤 위치를 저장한다."""
        selected_paths: set[str] = set()
        try:
            for row in self.selected_stock_rows():
                item = self.stock_table.item(row, 0)
                if item is not None and item.data(Qt.UserRole):
                    selected_paths.add(str(item.data(Qt.UserRole)))
        except Exception:
            selected_paths = set()

        try:
            scroll_value = self.stock_table.verticalScrollBar().value()
        except Exception:
            scroll_value = 0

        return selected_paths, scroll_value

    def restore_stock_table_view_state(self, selected_paths: set[str], scroll_value: int) -> None:
        """하단 종목표의 선택 종목과 세로 스크롤 위치를 복원한다.

        selectRow()는 선택 복원 중 현재 행으로 자동 스크롤을 이동시킬 수 있다.
        설정 저장/갱신 직후 종목이 튀는 현상을 막기 위해 selectionModel().select()로
        선택 상태만 복원하고, 마지막에 기존 스크롤 위치를 다시 적용한다.
        """
        try:
            if selected_paths:
                self.stock_table.clearSelection()
                selection_model = self.stock_table.selectionModel()
                if selection_model is not None:
                    flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
                    for row in range(self.stock_table.rowCount()):
                        item = self.stock_table.item(row, 0)
                        if item is not None and str(item.data(Qt.UserRole)) in selected_paths:
                            index = self.stock_table.model().index(row, 0)
                            selection_model.select(index, flags)
        except Exception:
            pass

        try:
            scroll_bar = self.stock_table.verticalScrollBar()
            scroll_bar.setValue(min(max(0, scroll_value), scroll_bar.maximum()))
        except Exception:
            pass

    def selected_stock_rows(self) -> list[int]:
        return selected_stock_rows(self)

    def has_selected_stock(self) -> bool:
        return has_selected_stock(self)

    def has_single_selected_stock(self) -> bool:
        return has_single_selected_stock(self)

    def update_action_buttons(self) -> None:
        has_stock = self.has_selected_stock()
        single_stock = self.has_single_selected_stock()

        self.btn_early_close.setEnabled(self.has_early_close_scope_targets())
        self.btn_set_schedule.setEnabled(True)
        self.btn_stock_register.setEnabled(True)
        self.btn_log_view.setEnabled(single_stock)
        self.btn_review_view.setEnabled(True)
        self.update_startup_recovery_controls()

    def registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is not None:
            targets: list[tuple[Path, str, str]] = []
            for stock in snapshot.get("stocks", ()):
                stock_path = str(stock.get("stock_path", "") or "").strip()
                code = str(stock.get("code", "") or "").strip()
                name = str(stock.get("name", "") or "").strip()
                if stock_path and code:
                    targets.append((PROJECT_ROOT / stock_path, code, name))
            return targets
        return auto_trade_registered_operation_targets()

    def registered_operation_start_targets(self) -> list[tuple[Path, str, str]]:
        return auto_trade_registered_operation_start_targets(self)

    def running_registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is not None:
            stock_data_by_dir = snapshot.get("stock_data_by_dir", {})
            return auto_trade_running_registered_operation_targets(
                self,
                registered_targets=self.registered_operation_targets(),
                operation_excluded_by_stock_dir={
                    stock_dir: is_operation_excluded(
                        data.get("config", {})
                    )
                    for stock_dir, data in stock_data_by_dir.items()
                },
                state_by_stock_dir={
                    stock_dir: data.get("state", {})
                    for stock_dir, data in stock_data_by_dir.items()
                },
            )
        return auto_trade_running_registered_operation_targets(self)

    def update_global_operation_button_state(self) -> None:
        auto_trade_update_global_operation_button_state(self)

    def on_stock_selection_changed(self) -> None:
        self.update_action_buttons()

    def stock_info_from_row(self, row: int) -> tuple[Path, str, str] | None:
        stock_dir = self.operation_stock_dir_from_row(row)
        code_item = self.stock_table.item(row, 0)
        name_item = self.stock_table.item(row, 1)
        if stock_dir is None or code_item is None or name_item is None:
            return None
        return stock_dir, code_item.text().strip(), name_item.text().strip()

    def on_stock_table_code_item_double_clicked(
        self,
        item: QTableWidgetItem,
    ) -> None:
        """Open the common instance chart only from the stock-code column."""
        if item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= self.stock_table.rowCount():
            return
        stock_code = item.text().strip()
        if not stock_code:
            return
        open_stock_instance_chart(
            stock_code,
            trade_date=None,
            parent=self,
        )

    def on_stock_table_name_item_double_clicked(
        self,
        item: QTableWidgetItem,
    ) -> None:
        if item.column() != 1:
            return
        target = self.stock_info_from_row(item.row())
        if target is None:
            return
        handle_stock_name_operation_exclusion_double_click(self, target)

    def set_stock_operation_exclusion(
        self,
        target: tuple[Path, str, str],
        excluded: bool,
        *,
        notify: bool = True,
        refresh: bool = True,
    ) -> bool:
        return auto_trade_set_stock_operation_exclusion(
            self,
            target,
            excluded,
            notify=notify,
            refresh=refresh,
        )

    def toggle_stock_operation_exclusion(
        self,
        target: tuple[Path, str, str],
        *,
        refresh: bool = True,
    ) -> bool:
        return auto_trade_toggle_stock_operation_exclusion(
            self,
            target,
            refresh=refresh,
        )

    def set_selected_stock_operation_exclusions(self) -> None:
        auto_trade_set_selected_stock_operation_exclusions(self)

    def clear_selected_stock_operation_exclusions(self) -> None:
        auto_trade_clear_selected_stock_operation_exclusions(self)

    def operation_stock_dir_from_row(self, row: int) -> Path | None:
        code_item = self.stock_table.item(row, 0)
        if code_item is None:
            return None
        stock_dir_text = code_item.data(Qt.UserRole)
        if not stock_dir_text:
            return None
        stock_dir = Path(str(stock_dir_text))
        if not stock_dir.exists():
            return None
        return stock_dir

    def on_stock_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """운영 칸 더블클릭 시 시간/수동을 빠르게 전환한다."""
        if item.column() != 2:
            return

        row = item.row()
        stock_dir = self.operation_stock_dir_from_row(row)
        if stock_dir is None:
            return

        self.stock_table.selectRow(row)
        target = self.stock_info_from_row(row)
        if target is None:
            return
        if bool(getattr(self, "_stock_operation_mode_double_click_pending", False)):
            return
        self._stock_operation_mode_double_click_pending = True

        def run_operation_mode_toggle(target_snapshot=target) -> None:
            try:
                handle_auto_trade_operation_mode_double_click(self, target_snapshot)
            finally:
                try:
                    self._stock_operation_mode_double_click_pending = False
                except RuntimeError:
                    pass

        QTimer.singleShot(0, run_operation_mode_toggle)

    def ensure_context_row_selected(self, row: int) -> None:
        ensure_context_row_selected(self, row)

    def select_all_current_routine_stocks(self) -> None:
        select_all_current_routine_stocks(self)

    def clear_current_routine_stock_selection(self) -> None:
        clear_current_routine_stock_selection(self)

    def on_stock_table_context_menu(self, pos) -> None:
        show_auto_trade_stock_context_menu(self, pos)

    def apply_selected_individual_liquidation_method(
        self,
        method: str,
        minutes_before_regular_close: str = "5",
    ) -> None:
        auto_trade_apply_selected_individual_liquidation_method(
            self,
            method,
            minutes_before_regular_close,
        )

    def individual_liquidation_status_text(self, policy_values: dict[str, object]) -> str:
        if not bool(policy_values.get("enabled", False)):
            return "환경설정 사용"
        method = short_close_method_text(policy_values.get("method", "이월"))
        if method == "이월":
            return "청산 안함(이월)"
        minutes = str(policy_values.get("minutes_before_regular_close", "5")).strip() or "5"
        return f"개별 {minutes}분/{method}"

    def selected_manual_ats_state(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, bool]:
        return auto_trade_selected_manual_ats_state(self, selected)

    def selected_manual_ats_liquidation_available(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> bool:
        return auto_trade_selected_manual_ats_liquidation_available(
            self,
            selected,
        )

    def save_selected_manual_ats_state(
        self,
        ats_state: dict[str, bool],
        selected: list[tuple[Path, str, str]] | None = None,
        editable_keys: tuple[str, ...] | None = None,
    ) -> int:
        return auto_trade_save_selected_manual_ats_state(
            self,
            ats_state,
            selected,
            editable_keys,
        )

    def set_selected_manual_ats_flag(self, flag_key: str, enabled: bool, label: str) -> None:
        auto_trade_set_selected_manual_ats_flag(self, flag_key, enabled, label)

    def execute_selected_manual_ats_liquidation(
        self,
        method: str,
        ats_state: dict[str, bool],
        selected: list[tuple[Path, str, str]] | None = None,
        editable_keys: tuple[str, ...] | None = None,
        selected_sessions: tuple[str, ...] | None = None,
    ) -> None:
        auto_trade_execute_selected_manual_ats_liquidation(
            self,
            method,
            ats_state,
            selected,
            editable_keys,
            selected_sessions,
        )

    def selected_operation_mode_set(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> set[str]:
        """선택 종목들의 운영방식 집합을 반환한다."""
        selected = selected if selected is not None else self.selected_stock_infos()
        modes: set[str] = set()
        for stock_dir, _, _ in selected:
            config = read_json_dict(stock_dir / "config.json")
            if not config:
                config = default_config()
            modes.add(normalize_operation_mode(config.get("operation_mode", "SCHEDULED")))
        return modes

    def toggle_selected_manual_override_flag(self, flag_key: str, label: str) -> None:
        """수동운영 종목의 개별 수동시간 사용 여부를 즉시 전환한다.

        저장 위치:
        - config.json / manual_operation_override
        - 아직 실제 주문 연동 전 단계이므로, 우클릭 즉시 설정값을 먼저 보존한다.
        """
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "설정할 종목을 1개 이상 선택하세요.")
            return

        changed: list[str] = []
        for stock_dir, code, name in selected:
            config_path = stock_dir / "config.json"
            config = read_json_dict(config_path)
            if not config:
                config = default_config()

            if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
                continue

            manual_override = config.get("manual_operation_override", {})
            if not isinstance(manual_override, dict):
                manual_override = {}

            current_value = bool(manual_override.get(flag_key, False))
            manual_override[flag_key] = not current_value
            config["manual_operation_override"] = manual_override
            config["policy_override_enabled"] = True
            config["policy_override_updated_at"] = now_text()
            config["updated_at"] = now_text()

            try:
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                QMessageBox.critical(self, "저장 오류", f"{code} {name} 설정 저장 중 오류가 발생했습니다.\n\n{exc}")
                continue

            changed.append(f"{code} {name}({label}: {'ON' if manual_override[flag_key] else 'OFF'})")
            append_stock_log(stock_dir, "GUI", f"우클릭 수동운영 설정 변경: {label} -> {'ON' if manual_override[flag_key] else 'OFF'}")

        if changed:
            append_changelog("UPDATE", "config.json", f"수동운영 개별설정 변경: {' / '.join(changed)}")
            self.statusBarMessage(f"{label} 전환 완료: {len(changed)}개")
            self.refresh_all()
        else:
            QMessageBox.information(self, "처리 없음", "수동운영 종목만 이 메뉴를 사용할 수 있습니다.")

    def reset_selected_manual_override(self) -> None:
        """선택 수동운영 종목의 수동운영 개별설정을 제거한다."""
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "리셋할 종목을 1개 이상 선택하세요.")
            return

        changed: list[str] = []
        for stock_dir, code, name in selected:
            config_path = stock_dir / "config.json"
            config = read_json_dict(config_path)
            if not config:
                config = default_config()

            if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
                continue

            if "manual_operation_override" not in config:
                continue

            config.pop("manual_operation_override", None)
            config["policy_override_updated_at"] = now_text()
            config["updated_at"] = now_text()

            try:
                config_path.write_text(
                    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                QMessageBox.critical(self, "리셋 오류", f"{code} {name} 설정 리셋 중 오류가 발생했습니다.\n\n{exc}")
                continue

            changed.append(f"{code} {name}")
            append_stock_log(stock_dir, "GUI", "우클릭 수동운영 개별설정 리셋")

        if changed:
            append_changelog("UPDATE", "config.json", f"수동운영 개별설정 리셋: {' / '.join(changed)}")
            self.statusBarMessage(f"수동운영 기본 리셋 완료: {len(changed)}개")
            self.refresh_all()
        else:
            QMessageBox.information(self, "처리 없음", "리셋할 수동운영 개별설정이 없습니다.")

    def _routine_instance_stock_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        root = Path(__file__).resolve().parent
        for stock_dir, instance_id, _stock in self._current_stock_entries_by_instance(root=root):
            counts[instance_id] = counts.get(instance_id, 0) + 1
        return counts

    def _current_stock_entries_by_instance(
        self,
        root: Path | None = None,
    ) -> list[tuple[Path, str, dict[str, object]]]:
        project_root = root if root is not None else Path(__file__).resolve().parent
        entries: list[tuple[Path, str, dict[str, object]]] = []
        snapshot = auto_trade_initial_read_snapshot(self)
        stocks = (
            snapshot.get("stocks", ())
            if snapshot is not None
            else read_base_stocks()
        )
        stock_data_by_dir = (
            snapshot.get("stock_data_by_dir", {})
            if snapshot is not None
            else {}
        )
        for stock in stocks:
            stock_path = str(stock.get("stock_path", "") or "").strip()
            if not stock_path:
                continue
            stock_dir = project_root / stock_path
            instance_id = str(stock.get("assigned_routine_instance_id", "") or "").strip()
            if not instance_id:
                snapshot_data = stock_data_by_dir.get(str(stock_dir), {})
                config = (
                    snapshot_data.get("config", {})
                    if snapshot_data
                    else read_json_dict(stock_dir / "config.json")
                )
                instance_id = str(config.get("assigned_routine_instance_id", "") or "").strip()
            if not instance_id:
                continue
            entries.append((stock_dir, instance_id, dict(stock)))
        return entries

    def _current_stocks_by_instance(self) -> dict[str, list[dict[str, object]]]:
        stocks_by_instance: dict[str, list[dict[str, object]]] = {}
        for stock_dir, instance_id, stock in self._current_stock_entries_by_instance():
            stock_name = str(stock.get("name", "") or "").strip()
            stock_code = str(stock.get("code", "") or "").strip()
            if not stock_name or not stock_code:
                parsed_code, parsed_name = parse_stock_folder_name(stock_dir.name)
                stock_code = stock_code or parsed_code
                stock_name = stock_name or parsed_name
            stocks_by_instance.setdefault(instance_id, []).append(
                {
                    "stock_path": str(stock.get("stock_path", "") or stock_dir),
                    "stock_code": stock_code,
                    "stock_name": stock_name or stock_code or stock_dir.name,
                }
            )
        for stocks in stocks_by_instance.values():
            stocks.sort(key=lambda item: (str(item.get("stock_name", "")), str(item.get("stock_code", ""))))
        return stocks_by_instance

    def _historical_stocks_by_instance(self) -> dict[str, list[dict[str, object]]]:
        stocks_by_instance: dict[str, list[dict[str, object]]] = {}
        snapshot = auto_trade_initial_read_snapshot(self)
        assignment_history = (
            snapshot.get("assignment_history", ())
            if snapshot is not None
            else StockRepository(PROJECT_ROOT).list_routine_assignment_history()
        )
        for stock in assignment_history:
            instance_id = str(stock.get("instance_id", "") or "").strip()
            if not instance_id:
                continue
            stocks_by_instance.setdefault(instance_id, []).append(
                {
                    **stock,
                    "is_historical": True,
                }
            )
        if auto_trade_setting_historical_fixture_enabled():
            current_stocks_by_instance = self._current_stocks_by_instance()
            hidden_fixture_keys = getattr(
                self,
                "_hidden_historical_stock_fixture_keys",
                set(),
            )
            existing_keys = {
                (
                    instance_id,
                    str(stock.get("stock_code", "") or "").strip(),
                )
                for instance_id, stocks in stocks_by_instance.items()
                for stock in stocks
            }
            fixture_instances = list(
                snapshot.get("instances", ())
                if snapshot is not None
                else load_persisted_routine_instances()
            )
            for fixture in AUTO_TRADE_SETTING_HISTORICAL_MANUAL_AGGREGATION_FIXTURES:
                if not fixture_instances:
                    break
                stock_code = str(fixture.get("stock_code", "") or "").strip()
                stock_name = str(fixture.get("stock_name", "") or "").strip()
                if not stock_code or not stock_name:
                    continue
                target_instance = None
                for instance in fixture_instances:
                    instance_id = str(instance.instance_id)
                    fixture_key = (instance_id, stock_code)
                    current_codes = {
                        str(stock.get("stock_code", "") or "").strip()
                        for stock in current_stocks_by_instance.get(instance_id, [])
                    }
                    if (
                        stock_code in current_codes
                        or fixture_key in existing_keys
                        or fixture_key in hidden_fixture_keys
                    ):
                        continue
                    target_instance = instance
                    break
                if target_instance is None:
                    continue
                instance_id = str(target_instance.instance_id)
                fixture_key = (instance_id, stock_code)
                stocks_by_instance.setdefault(instance_id, []).append(
                    {
                        "instance_id": instance_id,
                        "stock_path": "",
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "is_historical": True,
                        "is_development_fixture": True,
                        "performance_fixture": dict(
                            fixture.get("performance_fixture", {})
                        ),
                    }
                )
                existing_keys.add(fixture_key)

            for instance in fixture_instances:
                instance_id = str(instance.instance_id)
                current_codes = {
                    str(stock.get("stock_code", "") or "").strip()
                    for stock in current_stocks_by_instance.get(instance_id, [])
                }
                fixture_stocks: list[tuple[str, str]] = []
                for stock_code, stock_name in (
                    AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_CANDIDATES
                ):
                    fixture_key = (instance_id, stock_code)
                    if (
                        stock_code in current_codes
                        or fixture_key in existing_keys
                    ):
                        continue
                    fixture_stocks.append((stock_code, stock_name))
                    if len(fixture_stocks) == 5:
                        break
                for fixture_index, (stock_code, stock_name) in enumerate(
                    fixture_stocks
                ):
                    fixture_key = (instance_id, stock_code)
                    if fixture_key in hidden_fixture_keys:
                        continue
                    stocks_by_instance.setdefault(instance_id, []).append(
                        {
                            "instance_id": instance_id,
                            "stock_path": "",
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "is_historical": True,
                            "is_development_fixture": True,
                            "performance_fixture": dict(
                                AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_PERFORMANCE[
                                    fixture_index
                                    % len(
                                        AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_PERFORMANCE
                                    )
                                ]
                            ),
                        }
                    )
        for stocks in stocks_by_instance.values():
            stocks.sort(
                key=lambda item: (
                    str(item.get("stock_name", "")),
                    str(item.get("stock_code", "")),
                )
            )
        return stocks_by_instance

    def _routine_tree_stock_performance_source(
        self,
        stock: dict[str, object],
    ) -> dict[str, object]:
        if bool(stock.get("is_development_fixture", False)):
            fixture = stock.get("performance_fixture")
            if isinstance(fixture, dict):
                profit_factor = normalize_profit_factor(
                    fixture.get(
                        "profit_factor",
                        fixture.get("efficiency"),
                    )
                )
                return {
                    **fixture,
                    "gross_profit": fixture.get("gross_profit"),
                    "gross_loss_abs": fixture.get("gross_loss_abs"),
                    "profit_factor": profit_factor,
                    "is_current": False,
                }
        stock_path = Path(str(stock.get("stock_path", "") or "").strip())
        if not stock_path.is_absolute():
            stock_path = Path(__file__).resolve().parent / stock_path
        snapshot = auto_trade_initial_read_snapshot(self)
        snapshot_data = (
            snapshot.get("stock_data_by_dir", {}).get(str(stock_path), {})
            if snapshot is not None
            else {}
        )
        orders = (
            list(snapshot_data.get("orders", ()))
            if snapshot_data
            else read_orders_data(stock_path / "orders.json")
        )
        is_historical = bool(stock.get("is_historical", False))
        if is_historical:
            registered_at = parse_order_datetime_value(
                stock.get("registered_at")
            )
            unregistered_at = parse_order_datetime_value(
                stock.get("unregistered_at")
            )
            orders = [
                order
                for order in orders
                if (parsed := order_datetime(order)) is not None
                and (registered_at is None or parsed >= registered_at)
                and (unregistered_at is None or parsed <= unregistered_at)
            ]
        filled_orders = [
            order
            for order in orders
            if numeric_order_value(order, ["filled_qty", "filled", "executed_qty"], 0.0) > 0
        ]
        trade_dates = {
            parsed.date()
            for order in filled_orders
            if (parsed := order_datetime(order)) is not None
        }
        realized_profit: float | None = None
        if filled_orders:
            realized_profit = float(summarize_orders(orders).get("realized_pnl", 0.0) or 0.0)
        return {
            "trade_days": len(trade_dates) if trade_dates else None,
            "realized_profit": realized_profit,
            "profit_rate": None,
            "average": None,
            "average_rate": None,
            "gross_profit": None,
            "gross_loss_abs": None,
            "profit_factor": 0.0,
            "is_current": bool(stock.get("is_current", not is_historical)),
        }

    def _routine_tree_performance_texts(
        self,
        stocks: list[dict[str, object]],
        source_cache: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, str]:
        cache = source_cache if source_cache is not None else {}
        source_rows: list[dict[str, object]] = []
        for stock in stocks:
            stock_path_key = str(stock.get("stock_path", "") or "").strip()
            is_historical = bool(stock.get("is_historical", False))
            cache_key = stock_path_key
            if is_historical or not cache_key:
                cache_key = "|".join(
                    (
                        str(stock.get("instance_id", "") or "").strip(),
                        str(stock.get("stock_code", "") or "").strip(),
                        stock_path_key,
                        "historical" if is_historical else "current",
                    )
                )
            if cache_key not in cache:
                cache[cache_key] = self._routine_tree_stock_performance_source(stock)
            source_rows.append(cache[cache_key])
        trade_days = [
            int(source.get("trade_days", 0) or 0)
            for source in source_rows
            if int(source.get("trade_days", 0) or 0) > 0
        ]
        period_text = "0"
        if len(stocks) == 1 and trade_days:
            period_text = str(trade_days[0])
        elif len(stocks) > 1 and trade_days:
            period_text = str(safe_int_value(sum(trade_days) / len(trade_days), 0))

        realized_values = [
            float(source["realized_profit"])
            for source in source_rows
            if source.get("realized_profit") is not None
        ]
        profit_value = sum(realized_values) if realized_values else 0.0
        profit_amount_text = format_signed_money(profit_value)

        profit_rate_value = (
            source_rows[0].get("profit_rate")
            if len(source_rows) == 1
            else None
        )
        profit_rate_text = format_signed_percent(
            profit_rate_value if profit_rate_value is not None else 0.0,
            digits=2,
        )
        average_values = [
            float(source["average"])
            for source in source_rows
            if source.get("average") is not None
        ]
        average_value = (
            sum(average_values) / len(average_values)
            if average_values
            else 0.0
        )
        average_rate_values = [
            float(source["average_rate"])
            for source in source_rows
            if source.get("average_rate") is not None
        ]
        average_rate_value = (
            sum(average_rate_values) / len(average_rate_values)
            if average_rate_values
            else 0.0
        )
        average_amount_text = format_signed_money(average_value)
        average_rate_text = format_signed_percent(
            average_rate_value,
            digits=2,
        )
        profit_factor_value = (
            source_rows[0].get(
                "profit_factor",
                source_rows[0].get("efficiency"),
            )
            if len(source_rows) == 1
            else 0.0
        )
        profit_factor_value = normalize_profit_factor(profit_factor_value)
        efficiency_text = f"{profit_factor_value:.1f}"

        return {
            "performance_period_text": f"기간({period_text})",
            "performance_profit_text": (
                f"수익({profit_amount_text} / {profit_rate_text})"
            ),
            "performance_average_text": (
                f"평균({average_amount_text} / {average_rate_text})"
            ),
            "performance_efficiency_text": f"효율({efficiency_text})",
            "performance_period_value": period_text,
            "performance_profit_amount": profit_amount_text,
            "performance_profit_rate": profit_rate_text,
            "performance_profit_color": profit_loss_value_color(profit_value),
            "performance_average_amount": average_amount_text,
            "performance_average_rate": average_rate_text,
            "performance_average_color": profit_loss_value_color(
                average_value if average_values else None
            ),
            "performance_efficiency_value": efficiency_text,
            "performance_efficiency_color": directional_value_color(
                profit_factor_value
            ),
            "performance_period_sort_value": float(sum(trade_days)),
            "performance_profit_sort_value": float(profit_value),
            "performance_average_sort_value": float(average_value),
            "performance_efficiency_sort_value": float(profit_factor_value),
        }

    def _routine_tree_stock_group_performance_source(
        self,
        stocks: list[dict[str, object]],
        source_cache: dict[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        cache = source_cache if source_cache is not None else {}
        source_rows: list[dict[str, object]] = []
        for stock in stocks:
            stock_path_key = str(stock.get("stock_path", "") or "").strip()
            is_historical = bool(stock.get("is_historical", False))
            cache_key = stock_path_key
            if is_historical or not cache_key:
                cache_key = "|".join(
                    (
                        str(stock.get("instance_id", "") or "").strip(),
                        str(stock.get("stock_code", "") or "").strip(),
                        stock_path_key,
                        "historical" if is_historical else "current",
                    )
                )
            if cache_key not in cache:
                cache[cache_key] = self._routine_tree_stock_performance_source(stock)
            source_rows.append(cache[cache_key])

        trade_day_total = sum(
            int(source.get("trade_days", 0) or 0)
            for source in source_rows
            if int(source.get("trade_days", 0) or 0) > 0
        )
        realized_values = [
            float(source["realized_profit"])
            for source in source_rows
            if source.get("realized_profit") is not None
        ]
        realized_profit = sum(realized_values) if realized_values else 0.0
        average = (
            realized_profit / trade_day_total
            if trade_day_total > 0 and realized_values
            else 0.0
        )
        weighted_average_rates = [
            (
                float(source.get("average_rate", 0.0) or 0.0),
                int(source.get("trade_days", 0) or 0),
            )
            for source in source_rows
            if source.get("average_rate") is not None
            and int(source.get("trade_days", 0) or 0) > 0
        ]
        average_rate = (
            sum(rate * weight for rate, weight in weighted_average_rates)
            / sum(weight for _rate, weight in weighted_average_rates)
            if weighted_average_rates
            else 0.0
        )
        gross_profit_values = [
            float(source.get("gross_profit", 0.0) or 0.0)
            for source in source_rows
            if source.get("gross_profit") is not None
        ]
        gross_loss_values = [
            float(source.get("gross_loss_abs", 0.0) or 0.0)
            for source in source_rows
            if source.get("gross_loss_abs") is not None
        ]
        gross_profit = sum(gross_profit_values) if gross_profit_values else None
        gross_loss_abs = sum(gross_loss_values) if gross_loss_values else None
        profit_factor = 0.0
        if gross_profit is not None and gross_loss_abs and gross_loss_abs > 0:
            profit_factor = gross_profit / gross_loss_abs
        elif len(source_rows) == 1:
            profit_factor = normalize_profit_factor(
                source_rows[0].get(
                    "profit_factor",
                    source_rows[0].get("efficiency"),
                )
            )

        return {
            "trade_days": trade_day_total,
            "realized_profit": realized_profit,
            "profit_rate": None,
            "average": average,
            "average_rate": average_rate,
            "gross_profit": gross_profit,
            "gross_loss_abs": gross_loss_abs,
            "profit_factor": profit_factor,
            "is_current": any(
                bool(source.get("is_current", False)) for source in source_rows
            ),
        }

    def _routine_tree_group_stock_rows_by_code(
        self,
        stock_rows: list[dict[str, object]],
        source_cache: dict[str, dict[str, object]],
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        first_index: dict[str, int] = {}
        for index, row in enumerate(stock_rows):
            stock_code = str(row.get("stock_code", "") or "").strip()
            if not stock_code:
                continue
            grouped.setdefault(stock_code, []).append(row)
            first_index.setdefault(stock_code, index)

        result: list[dict[str, object]] = []
        for stock_code, rows_for_code in sorted(
            grouped.items(),
            key=lambda item: first_index.get(item[0], 0),
        ):
            representative = next(
                (
                    row
                    for row in rows_for_code
                    if not bool(row.get("is_historical", False))
                ),
                rows_for_code[0],
            )
            source_stocks = [
                dict(row.get("_source_stock", row))
                for row in rows_for_code
            ]
            aggregate_source = self._routine_tree_stock_group_performance_source(
                source_stocks,
                source_cache,
            )
            stock_path = f"aggregate://{stock_code}"
            cache_key = stock_path
            source_cache[cache_key] = aggregate_source
            row = dict(representative)
            row.pop("_source_stock", None)
            row.update(
                {
                    "instance_id": str(representative.get("instance_id", "") or ""),
                    "instance_name": str(representative.get("instance_name", "") or ""),
                    "stock_path": stock_path,
                    "tree_icon": (
                        "\u2713"
                        if any(not bool(item.get("is_historical", False)) for item in rows_for_code)
                        else "\u25aa"
                    ),
                    "is_historical": not any(
                        not bool(item.get("is_historical", False))
                        for item in rows_for_code
                    ),
                    "is_development_fixture": True,
                    "performance_fixture": aggregate_source,
                    **self._routine_tree_performance_texts(
                        [
                            {
                                "stock_path": stock_path,
                                "stock_code": stock_code,
                                "is_development_fixture": True,
                                "performance_fixture": aggregate_source,
                            }
                        ],
                        source_cache,
                    ),
                }
            )
            result.append(row)
        if bool(getattr(self, "_routine_tree_stock_performance_sort_active", False)):
            display_criterion = str(
                getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
            )
            source_key_by_criterion = {
                "period": "trade_days",
                "profit": "realized_profit",
                "average": "average",
                "efficiency": "profit_factor",
            }
            source_key = source_key_by_criterion.get(
                display_criterion,
                "realized_profit",
            )

            def _aggregate_sort_value(row: dict[str, object]) -> float:
                fixture = row.get("performance_fixture")
                source = fixture if isinstance(fixture, dict) else {}
                raw_value = source.get(source_key)
                if source_key == "profit_factor":
                    return normalize_profit_factor(raw_value)
                try:
                    return float(raw_value)
                except (TypeError, ValueError):
                    return 0.0

            result = sorted(result, key=_aggregate_sort_value, reverse=True)
        return result

    def _routine_tree_row_sort_value(
        self,
        row: dict[str, object],
        criterion: str,
    ) -> float:
        sort_key_by_criterion = {
            "period": "performance_period_sort_value",
            "profit": "performance_profit_sort_value",
            "average": "performance_average_sort_value",
            "efficiency": "performance_efficiency_sort_value",
        }
        raw_value = row.get(sort_key_by_criterion.get(criterion, ""))
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return 0.0

    def _routine_tree_instance_identity_sort_key(
        self,
        row: dict[str, object],
    ) -> tuple[str, str]:
        return (
            str(row.get("instance_name", "") or "").casefold(),
            str(row.get("instance_id", "") or ""),
        )

    def _routine_tree_sort_definition_blocks(
        self,
        rows: list[dict[str, object]],
        criterion: str,
    ) -> list[dict[str, object]]:
        blocks: list[list[dict[str, object]]] = []
        current_block: list[dict[str, object]] = []
        for row in rows:
            if str(row.get("row_kind", "") or "") == "definition":
                if current_block:
                    blocks.append(current_block)
                current_block = [row]
            else:
                current_block.append(row)
        if current_block:
            blocks.append(current_block)

        sorted_blocks = sorted(
            blocks,
            key=lambda block: self._routine_tree_row_sort_value(block[0], criterion),
            reverse=True,
        )
        return [row for block in sorted_blocks for row in block]

    def _routine_tree_sort_instance_blocks(
        self,
        rows: list[dict[str, object]],
        criterion: str,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            if str(row.get("row_kind", "") or "") != "definition":
                result.append(row)
                index += 1
                continue

            result.append(row)
            index += 1
            instance_blocks: list[list[dict[str, object]]] = []
            while index < len(rows) and str(rows[index].get("row_kind", "") or "") != "definition":
                if str(rows[index].get("row_kind", "") or "") != "instance":
                    result.append(rows[index])
                    index += 1
                    continue
                block = [rows[index]]
                index += 1
                while index < len(rows) and str(rows[index].get("row_kind", "") or "") == "stock":
                    block.append(rows[index])
                    index += 1
                if len(block) > 2:
                    sorted_children = sorted(
                        block[1:],
                        key=lambda child: self._routine_tree_row_sort_value(child, criterion),
                        reverse=True,
                    )
                    for child_index, child in enumerate(sorted_children):
                        child["first_stock_for_instance"] = child_index == 0
                    block = [block[0], *sorted_children]
                instance_blocks.append(block)

            sorted_blocks = sorted(
                instance_blocks,
                key=lambda block: (
                    -self._routine_tree_row_sort_value(block[0], criterion),
                    *self._routine_tree_instance_identity_sort_key(block[0]),
                ),
            )
            for block_index, block in enumerate(sorted_blocks):
                block[0]["instance_group_top_gap"] = block_index > 0
                result.extend(block)
        return result

    def _routine_instance_operation_counts(self) -> dict[str, dict[str, object]]:
        from gui_main_table_loader import _instance_stock_counts

        snapshot = auto_trade_initial_read_snapshot(self)
        if snapshot is None:
            return _instance_stock_counts(window=self)
        return _instance_stock_counts(
            window=self,
            static_data=snapshot.get("count_static_data", {}),
            state_by_stock_dir={
                stock_dir: data.get("state", {})
                for stock_dir, data in snapshot.get(
                    "stock_data_by_dir",
                    {},
                ).items()
            },
        )

    def _is_default_operation_instance(self, metadata: dict[str, object]) -> bool:
        definition_id = str(metadata.get("definition_id", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        if not definition_id or not instance_id:
            return False
        return (
            getattr(self, "_default_operation_instance_by_definition", {}).get(definition_id)
            == instance_id
        )

    def _routine_status_text_for_metadata(self, metadata: dict[str, object]) -> str:
        if self._is_default_operation_instance(metadata):
            return ROUTINE_STATUS_DEFAULT
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        return str(
            getattr(self, "_routine_operation_status_by_instance", {}).get(instance_id, "")
            or ""
        )

    def set_default_operation_instance_from_metadata(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        definition_id = str(metadata.get("definition_id", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        if not definition_id or not instance_id:
            return
        defaults = getattr(self, "_default_operation_instance_by_definition", {})
        if defaults.get(definition_id) == instance_id:
            defaults.pop(definition_id, None)
            self._routine_operation_status_by_instance.pop(instance_id, None)
        else:
            previous_instance_id = defaults.get(definition_id)
            if previous_instance_id:
                self._routine_operation_status_by_instance.pop(previous_instance_id, None)
            defaults[definition_id] = instance_id
            self._routine_operation_status_by_instance[instance_id] = ROUTINE_STATUS_DEFAULT
        self._default_operation_instance_by_definition = defaults
        self._refresh_default_operation_stamps()
        self.update_selection_summary_panel()

    def _refresh_default_operation_stamps(self) -> None:
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            widget = self.routine_table.cellWidget(row, 0)
            if widget is None:
                continue
            stamp = widget.findChild(QPushButton, "autoTradeSettingDefaultOperationStamp")
            if stamp is None:
                continue
            row_kind = str(metadata.get("row_kind", "") or "")
            if row_kind == "instance":
                active_default = self._is_default_operation_instance(metadata)
            else:
                active_default = bool(
                    getattr(self, "_default_operation_instance_by_definition", {}).get(
                        str(metadata.get("definition_id", "") or "")
                    )
                )
            from gui_main_table_loader import routine_status_stamp_spec

            _display_status, color = routine_status_stamp_spec(
                ROUTINE_STATUS_DEFAULT if active_default else ""
            )
            stamp_color = color or "#9CA3AF"
            stamp.setChecked(active_default)
            stamp.setStyleSheet(
                "QPushButton {"
                " background-color: #FFFFFF;"
                f" border: 1px solid {stamp_color};"
                " border-radius: 4px;"
                f" color: {stamp_color};"
                " font-weight: 600;"
                "}"
                "QPushButton:disabled {"
                " color: #9CA3AF;"
                " border-color: #D1D5DB;"
                " background-color: #FFFFFF;"
                "}"
            )

    def _routine_tree_metric_text_parts(
        self,
        raw_text: object,
        left_fallback: str,
        right_fallback: str,
    ) -> tuple[str, str]:
        text = str(raw_text or "").strip()
        if text:
            open_index = text.find("(")
            close_index = text.rfind(")")
            if open_index >= 0 and close_index > open_index:
                inside = text[open_index + 1:close_index]
                if " / " in inside:
                    left, right = inside.split(" / ", 1)
                    return left.strip() or left_fallback, right.strip() or right_fallback
                return inside.strip() or left_fallback, right_fallback
        return left_fallback, right_fallback

    def _routine_tree_metric_values(
        self,
        row_data: dict[str, object],
        metric: str,
        left_fallback: str,
        right_fallback: str,
    ) -> tuple[str, str]:
        value_keys = {
            "period": ("performance_period_value", ""),
            "profit": (
                "performance_profit_amount",
                "performance_profit_rate",
            ),
            "average": (
                "performance_average_amount",
                "performance_average_rate",
            ),
            "efficiency": ("performance_efficiency_value", ""),
        }
        left_key, right_key = value_keys.get(metric, ("", ""))
        if left_key and left_key in row_data:
            left_value = str(row_data.get(left_key, "") or "").strip() or left_fallback
            right_value = (
                str(row_data.get(right_key, "") or "").strip() or right_fallback
                if right_key
                else right_fallback
            )
            return left_value, right_value
        return self._routine_tree_metric_text_parts(
            row_data.get(f"performance_{metric}_text", ""),
            left_fallback,
            right_fallback,
        )

    def _configure_routine_tree_row_layout(
        self,
        rows: list[dict[str, object]],
    ) -> None:
        """Configure the fixed title and performance axes used by every tree row."""
        base_font = QFont(self.routine_table.font())
        base_metrics = QFontMetrics(base_font)
        label_metrics = QFontMetrics(QApplication.font("QLabel"))
        geometry = routine_tree_layout_metrics(base_font)

        def _text_width(value: object) -> int:
            text = str(value or "")
            return max(
                width
                for metrics in (base_metrics, label_metrics)
                for width in (
                    metrics.horizontalAdvance(text),
                    metrics.boundingRect(text).width(),
                )
            )

        metric_slots: dict[str, tuple[int, int]] = {}
        for key, (left_samples, right_samples) in (
            AUTO_TRADE_SETTING_ROUTINE_TREE_METRIC_SAMPLES.items()
        ):
            left_width = max(
                (_text_width(sample) for sample in left_samples),
                default=0,
            )
            right_width = max(
                (_text_width(sample) for sample in right_samples),
                default=0,
            )
            metric_slots[key] = (left_width, right_width)

        close_width = _text_width(")")
        metric_geometry: dict[str, dict[str, int]] = {}
        for spec in AUTO_TRADE_SETTING_ROUTINE_TREE_PERFORMANCE_ITEM_SPECS:
            key = str(spec["key"])
            left_width, right_width = metric_slots[key]
            prefix_width = _text_width(f"{spec['label']}(")
            slash_width = _text_width(" / ") if spec["right_sample"] else 0
            metric_geometry[key] = {
                "prefix_width": prefix_width,
                "left_width": int(left_width),
                "slash_width": slash_width,
                "right_width": int(right_width) if spec["right_sample"] else 0,
                "close_width": close_width,
                "metric_width": (
                    prefix_width
                    + int(left_width)
                    + slash_width
                    + (int(right_width) if spec["right_sample"] else 0)
                    + close_width
                ),
            }

        self._routine_tree_row_geometry = geometry
        self._routine_tree_metric_slots = metric_slots
        self._routine_tree_render_context = {
            "font": base_font,
            "geometry": geometry,
            "metric_geometry": metric_geometry,
            "separator_width": _text_width("|"),
        }

    def _routine_tree_row_widget(self, row_data: dict[str, object], text: str) -> QWidget:
        row_kind = str(row_data.get("row_kind", "") or "")
        is_instance = row_kind == "instance"
        is_stock = row_kind == "stock"
        is_historical_stock = is_stock and bool(row_data.get("is_historical", False))
        is_definition = row_kind == "definition"
        render_context = getattr(self, "_routine_tree_render_context", None)
        if not isinstance(render_context, dict):
            self._configure_routine_tree_row_layout([])
            render_context = self._routine_tree_render_context
        container = QWidget()
        container.setFont(QFont(render_context["font"]))
        container.setFocusPolicy(Qt.NoFocus)
        container.setMouseTracking(True)
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        top_margin = (
            AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP
            if (
                (is_instance and bool(row_data.get("instance_group_top_gap")))
                or (is_stock and bool(row_data.get("first_stock_for_instance")))
            )
            else 0
        )
        tree_geometry = render_context["geometry"]
        horizontal_margin = tree_geometry["outer_margin"]
        layout.setContentsMargins(
            horizontal_margin,
            top_margin,
            tree_geometry["performance_trailing_margin"],
            0,
        )
        layout.setSpacing(0)
        if is_stock:
            row_data = dict(row_data)
            row_data["tree_icon"] = "\u25aa" if is_historical_stock else "\u2713"
        stock_row_color = (
            AUTO_TRADE_SETTING_HISTORICAL_STOCK_ROW_TEXT_COLOR
            if is_historical_stock
            else AUTO_TRADE_SETTING_STOCK_ROW_TEXT_COLOR
        )
        icon_label = QLabel(str(row_data.get("tree_icon", "") or ""))
        icon_label.setObjectName("autoTradeSettingRoutineTreeIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedWidth(
            tree_geometry["parent_icon_width"]
            if row_kind == "definition"
            else tree_geometry["child_icon_width"]
        )
        icon_label.setWordWrap(False)
        icon_label.setFocusPolicy(Qt.NoFocus)
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        icon_label.setStyleSheet(
            "background: transparent;"
            f" color: {stock_row_color if is_stock else '#6B7280'};"
        )
        if is_definition:
            icon_font = QFont(container.font())
            icon_font.setPointSize(icon_font.pointSize() + 2)
            icon_label.setFont(icon_font)
            icon_label.setCursor(Qt.PointingHandCursor)
            icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleDefinitionId",
                routine_tree_parent_identity(row_data),
            )
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleEnabled",
                bool(row_data.get("has_toggle_children", True)),
            )
            try:
                can_install_event_filter = isinstance(self, AutoTradeSettingWindow) and self.routine_table is not None
            except RuntimeError:
                can_install_event_filter = False
            if can_install_event_filter:
                icon_label.installEventFilter(self)
        elif is_instance:
            icon_label.setCursor(Qt.PointingHandCursor)
            icon_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleInstanceId",
                str(row_data.get("instance_id", "") or ""),
            )
            icon_label.setProperty(
                "autoTradeSettingRoutineTreeToggleEnabled",
                bool(row_data.get("has_toggle_children", True)),
            )
            try:
                can_install_event_filter = isinstance(self, AutoTradeSettingWindow) and self.routine_table is not None
            except RuntimeError:
                can_install_event_filter = False
            if can_install_event_filter:
                icon_label.installEventFilter(self)
        raw_title_text = str(row_data.get("display_name", "") or text)
        title_text = (
            routine_tree_instance_title_text(raw_title_text)
            if is_instance
            else tree_title_text(raw_title_text)
            if is_definition
            else routine_tree_title_text(raw_title_text)
        )
        title_label = QLabel(title_text)
        title_label.setObjectName("autoTradeSettingRoutineTreeTitle")
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        title_label.setWordWrap(False)
        title_label.setFocusPolicy(Qt.NoFocus)
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if is_stock:
            title_label.setToolTip(str(row_data.get("display_name", "") or text))
        elif is_definition or is_instance:
            title_label.setToolTip(tree_title_tooltip(raw_title_text))
        base_title_font = QFont(container.font())
        title_font = QFont(base_title_font)
        if not is_definition:
            title_font.setBold(False)
        else:
            title_font.setPointSize(title_font.pointSize() + 1)
            title_font.setWeight(QFont.DemiBold)
        title_label.setStyleSheet(
            "background: transparent;"
            " border: none;"
            f" color: {stock_row_color if is_stock else '#374151'};"
        )
        title_label.setFont(title_font)
        title_width = (
            tree_geometry["parent_title_width"]
            if is_definition
            else tree_geometry["stock_title_width"]
            if is_stock
            else tree_geometry["child_title_width"]
        )
        title_label.setFixedWidth(title_width)
        title_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        performance_item_specs = (
            AUTO_TRADE_SETTING_ROUTINE_TREE_PERFORMANCE_ITEM_SPECS
        )
        separator_width = int(render_context["separator_width"])

        identity_widget = QWidget()
        identity_widget.setObjectName(
            "autoTradeSettingRoutineTreeMetaGroup"
            if is_definition
            else "autoTradeSettingRoutineTreeIdentity"
        )
        identity_widget.setFocusPolicy(Qt.NoFocus)
        identity_widget.setAttribute(Qt.WA_StyledBackground, True)
        identity_widget.setAttribute(
            Qt.WA_TransparentForMouseEvents,
            not (is_definition or is_instance),
        )
        identity_widget.setStyleSheet("background: transparent;")
        if is_definition or is_instance:
            identity_widget.setMouseTracking(True)
            identity_widget.setProperty(
                "autoTradeSettingRoutineTreeHoverIdentityRowKind",
                row_kind,
            )
            try:
                can_install_event_filter = (
                    isinstance(self, AutoTradeSettingWindow)
                    and self.routine_table is not None
                )
            except RuntimeError:
                can_install_event_filter = False
            if can_install_event_filter:
                identity_widget.installEventFilter(self)
        identity_width = (
            tree_geometry["parent_identity_width"]
            if is_definition
            else tree_geometry["instance_identity_width"]
            if is_instance
            else tree_geometry["stock_identity_width"]
        )
        identity_widget.setFixedWidth(identity_width)
        identity_layout = QHBoxLayout(identity_widget)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(0)
        indent_width = (
            tree_geometry["instance_indent"]
            if is_instance
            else tree_geometry["stock_indent"]
            if is_stock
            else 0
        )
        if indent_width:
            indent_spacer = QWidget()
            indent_spacer.setObjectName(
                "autoTradeSettingRoutineTreeIndent"
                if is_instance
                else "autoTradeSettingRoutineTreeStockIndent"
            )
            indent_spacer.setFixedWidth(indent_width)
            indent_spacer.setFocusPolicy(Qt.NoFocus)
            indent_spacer.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            identity_layout.addWidget(indent_spacer, 0, Qt.AlignVCenter)
        identity_layout.addWidget(icon_label, 0, Qt.AlignVCenter)
        identity_layout.addSpacing(tree_geometry["item_gap"])
        identity_layout.addWidget(title_label, 0, Qt.AlignVCenter)
        if is_definition:
            instance_count = int(row_data.get("instance_count", 0) or 0)
            routine_count_label = QLabel(f"루틴{instance_count}")
            routine_count_label.setObjectName("autoTradeSettingRoutineTreeInstanceCount")
            routine_count_label.setAlignment(Qt.AlignCenter)
            routine_count_label.setWordWrap(False)
            routine_count_label.setFixedSize(tree_geometry["count_badge_width"], 22)
            routine_count_label.setFocusPolicy(Qt.NoFocus)
            routine_count_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            routine_count_label.setStyleSheet(auto_trade_setting_badge_stylesheet("QLabel"))
            identity_layout.addWidget(routine_count_label, 0, Qt.AlignVCenter)
        elif is_instance or is_stock:
            title_metric_gap = QWidget()
            title_metric_gap.setObjectName(
                "autoTradeSettingRoutineTreeStockMetricGap"
                if is_stock
                else "autoTradeSettingRoutineTreeInstanceMetricGap"
            )
            title_metric_gap.setFixedWidth(
                tree_geometry[
                    "stock_metric_gap" if is_stock else "instance_metric_gap"
                ]
            )
            title_metric_gap.setFocusPolicy(Qt.NoFocus)
            title_metric_gap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            identity_layout.addWidget(title_metric_gap, 0, Qt.AlignVCenter)
        else:
            identity_layout.addStretch(1)
        layout.addWidget(identity_widget, 0, Qt.AlignVCenter)
        if is_definition:
            group_metric_gap = QWidget()
            group_metric_gap.setObjectName(
                "autoTradeSettingRoutineTreeGroupMetricGap"
            )
            group_metric_gap.setFixedWidth(tree_geometry["group_metric_gap"])
            group_metric_gap.setFocusPolicy(Qt.NoFocus)
            group_metric_gap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(group_metric_gap, 0, Qt.AlignVCenter)

        def _metric_label(
            text: str,
            object_name: str,
            width: int,
            alignment: Qt.AlignmentFlag,
        ) -> QLabel:
            label = QLabel(text, metric_widget)
            label.setObjectName(object_name)
            label.setAlignment(alignment)
            label.setWordWrap(False)
            label.setFixedWidth(width)
            label.setFocusPolicy(Qt.NoFocus)
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            label.setStyleSheet(
                f"background: transparent; color: {metric_color};"
            )
            if is_definition:
                label.setProperty("autoTradeSettingParentSummaryMetric", True)
            return label

        row_performance_item_specs = (
            performance_item_specs[1:]
            if is_definition
            else performance_item_specs
        )
        separator_count = max(0, len(row_performance_item_specs) - 1)
        for index, spec in enumerate(row_performance_item_specs):
            key = str(spec["key"])
            if index > 0:
                separator_index = index - 1
                separator_side_gap = (
                    tree_geometry["performance_separator_edge_side_gap"]
                    if separator_index in {0, separator_count - 1}
                    else tree_geometry["performance_separator_inner_side_gap"]
                )
                layout.addSpacing(separator_side_gap)
                separator = QLabel("|")
                separator.setObjectName("autoTradeSettingRoutineTreePerformanceSeparator")
                separator.setAlignment(Qt.AlignCenter)
                separator.setFixedWidth(separator_width)
                separator.setFocusPolicy(Qt.NoFocus)
                separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                separator_color = stock_row_color if is_stock else "#9CA3AF"
                separator.setStyleSheet(f"background: transparent; color: {separator_color};")
                if is_definition:
                    separator.setProperty("autoTradeSettingParentSummaryMetric", True)
                layout.addWidget(separator, 0, Qt.AlignVCenter)
                layout.addSpacing(separator_side_gap)
            label_text = str(spec["label"])
            right_sample = str(spec["right_sample"])
            left_value, right_value = self._routine_tree_metric_values(
                row_data,
                key,
                str(spec["left_fallback"]),
                str(spec["right_fallback"]),
            )
            metric_color = (
                str(row_data.get(f"performance_{key}_color", "") or "")
                if key in {"profit", "average", "efficiency"}
                else ""
            )
            if not metric_color:
                metric_color = stock_row_color if is_stock else "#6B7280"
            metric_widget = QWidget(container)
            metric_widget.setObjectName(str(spec["object_name"]))
            metric_widget.setFocusPolicy(Qt.NoFocus)
            metric_widget.setAttribute(Qt.WA_StyledBackground, True)
            metric_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            metric_widget.setStyleSheet("background: transparent;")
            metric_layout = QHBoxLayout(metric_widget)
            metric_layout.setContentsMargins(0, 0, 0, 0)
            metric_layout.setSpacing(0)
            label_prefix = f"{label_text}("
            configured_geometry = render_context["metric_geometry"][key]
            prefix_width = configured_geometry["prefix_width"]
            left_width = configured_geometry["left_width"]
            slash_width = configured_geometry["slash_width"]
            right_width = configured_geometry["right_width"]
            close_width = configured_geometry["close_width"]
            metric_width = configured_geometry["metric_width"]
            metric_layout.addWidget(
                _metric_label(
                    label_prefix,
                    f"{spec['object_name']}Label",
                    prefix_width,
                    Qt.AlignLeft | Qt.AlignVCenter,
                ),
                0,
                Qt.AlignVCenter,
            )
            metric_layout.addWidget(
                _metric_label(
                    left_value,
                    f"{spec['object_name']}LeftValue",
                    left_width,
                    Qt.AlignRight | Qt.AlignVCenter,
                ),
                0,
                Qt.AlignVCenter,
            )
            if right_sample:
                metric_layout.addWidget(
                    _metric_label(
                        " / ",
                        f"{spec['object_name']}Slash",
                        slash_width,
                        Qt.AlignCenter | Qt.AlignVCenter,
                    ),
                    0,
                    Qt.AlignVCenter,
                )
                metric_layout.addWidget(
                    _metric_label(
                        right_value,
                        f"{spec['object_name']}RightValue",
                        right_width,
                        Qt.AlignRight | Qt.AlignVCenter,
                    ),
                    0,
                    Qt.AlignVCenter,
                )
            metric_layout.addWidget(
                _metric_label(
                    ")",
                    f"{spec['object_name']}Close",
                    close_width,
                    Qt.AlignCenter | Qt.AlignVCenter,
                ),
                0,
                Qt.AlignVCenter,
            )
            metric_widget.setFixedWidth(metric_width)
            metric_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            if is_definition:
                metric_widget.setProperty("autoTradeSettingParentSummaryMetric", True)
            layout.addWidget(metric_widget, 0, Qt.AlignVCenter)

        layout.addStretch(1)
        container.setProperty("autoTradeSettingRoutineTreeRowKind", row_kind)
        try:
            can_install_event_filter = (
                isinstance(self, AutoTradeSettingWindow)
                and self.routine_table is not None
            )
        except RuntimeError:
            can_install_event_filter = False
        if can_install_event_filter:
            container.installEventFilter(self)
        if is_definition:
            container.setProperty(
                "autoTradeSettingRoutineTreeSummaryPinned",
                str(row_data.get("tree_icon", "") or "") == "\u25b6",
            )
            self._set_routine_tree_parent_summary_visible(
                container,
                bool(container.property("autoTradeSettingRoutineTreeSummaryPinned")),
            )
        return container

    def _set_routine_tree_parent_summary_visible(self, row_widget: QWidget, visible: bool) -> None:
        for widget in row_widget.findChildren(QWidget):
            if widget.property("autoTradeSettingParentSummaryMetric"):
                widget.setVisible(visible)

    def _hide_routine_tree_unpinned_parent_summaries(self) -> None:
        for row in range(self.routine_table.rowCount()):
            row_widget = self.routine_table.cellWidget(row, 0)
            if (
                row_widget is None
                or row_widget.property("autoTradeSettingRoutineTreeRowKind")
                != "definition"
                or bool(
                    row_widget.property(
                        "autoTradeSettingRoutineTreeSummaryPinned"
                    )
                )
            ):
                continue
            self._set_routine_tree_parent_summary_visible(row_widget, False)

    def _setup_routine_tree_display_level_badges(self) -> None:
        container = QWidget(self.routine_box)
        container.setObjectName("autoTradeSettingRoutineTreeDisplayLevelBadges")
        container.setFocusPolicy(Qt.NoFocus)
        container.setAttribute(Qt.WA_StyledBackground, True)
        container.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        valid_button = QPushButton("유효", container)
        valid_button.setObjectName("autoTradeSettingRoutineTreeValidBadge")
        valid_button.setFocusPolicy(Qt.NoFocus)
        valid_button.setCursor(Qt.PointingHandCursor)
        valid_button.setCheckable(True)
        valid_button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
        valid_button.clicked.connect(self._set_routine_tree_valid_only)
        layout.addWidget(valid_button, 0, Qt.AlignVCenter)

        separators: list[QLabel] = []

        def add_separator(object_name: str) -> None:
            separator = QLabel("|", container)
            separator.setObjectName(object_name)
            separator.setAlignment(Qt.AlignCenter)
            separator.setFixedSize(12, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            separator.setFocusPolicy(Qt.NoFocus)
            separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            separator.setStyleSheet("background: transparent; color: #9CA3AF;")
            layout.addWidget(separator, 0, Qt.AlignVCenter)
            separators.append(separator)

        add_separator("autoTradeSettingRoutineTreeValidSeparator")

        buttons: dict[str, QPushButton] = {}
        for level, text, object_name in (
            ("category", "그룹", "autoTradeSettingRoutineTreeCategoryLevelBadge"),
            ("routine", "루틴", "autoTradeSettingRoutineTreeRoutineLevelBadge"),
            ("stock", "종목", "autoTradeSettingRoutineTreeStockLevelBadge"),
        ):
            button = QPushButton(text, container)
            button.setObjectName(object_name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_level=level:
                self._set_routine_tree_display_level(target_level)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            buttons[level] = button

        add_separator("autoTradeSettingRoutineTreeLevelSeparator")
        scope_buttons: dict[str, QPushButton] = {}
        for scope, text, object_name in (
            ("all", "전체", "autoTradeSettingRoutineTreeAllScopeBadge"),
            ("current", "현재", "autoTradeSettingRoutineTreeCurrentScopeBadge"),
            ("historical", "과거", "autoTradeSettingRoutineTreeHistoricalScopeBadge"),
        ):
            button = QPushButton(text, container)
            button.setObjectName(object_name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_scope=scope:
                self._set_routine_tree_display_scope(target_scope)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            scope_buttons[scope] = button

        add_separator("autoTradeSettingRoutineTreeScopeSeparator")
        criterion_buttons: dict[str, QPushButton] = {}
        for criterion, text, object_name in (
            ("period", "기간", "autoTradeSettingRoutineTreePeriodCriterionBadge"),
            ("profit", "수익", "autoTradeSettingRoutineTreeProfitCriterionBadge"),
            ("average", "평균", "autoTradeSettingRoutineTreeAverageCriterionBadge"),
            ("efficiency", "효율", "autoTradeSettingRoutineTreeEfficiencyCriterionBadge"),
        ):
            button = QPushButton(text, container)
            button.setObjectName(object_name)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(64, AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT)
            button.clicked.connect(
                lambda _checked=False, target_criterion=criterion:
                self._set_routine_tree_display_criterion(target_criterion)
            )
            layout.addWidget(button, 0, Qt.AlignVCenter)
            criterion_buttons[criterion] = button

        container.setFixedSize(layout.sizeHint())
        self._routine_tree_display_level_badges = container
        self._routine_tree_valid_button = valid_button
        self._routine_tree_display_separators = tuple(separators)
        self._routine_tree_display_level_buttons = buttons
        self._routine_tree_display_scope_buttons = scope_buttons
        self._routine_tree_display_criterion_buttons = criterion_buttons
        self._update_routine_tree_display_level_badges()
        container.show()
        container.raise_()
        self.routine_box.installEventFilter(self)
        QTimer.singleShot(0, self._position_routine_tree_display_level_badges)

    def _position_routine_tree_display_level_badges(self) -> None:
        container = getattr(self, "_routine_tree_display_level_badges", None)
        routine_box = getattr(self, "routine_box", None)
        if container is None or routine_box is None:
            return
        stock_box = getattr(self, "stock_box", None)
        stock_layout = stock_box.layout() if stock_box is not None else None
        if stock_layout is not None:
            stock_margins = stock_layout.contentsMargins()
            if stock_margins.top() != AUTO_TRADE_SETTING_TOP_CONTROL_MARGIN:
                stock_layout.setContentsMargins(
                    stock_margins.left(),
                    AUTO_TRADE_SETTING_TOP_CONTROL_MARGIN,
                    stock_margins.right(),
                    stock_margins.bottom(),
                )
            stock_layout.setSpacing(AUTO_TRADE_SETTING_TOP_CONTROL_BODY_SPACING)
            stock_layout.activate()
        routine_layout = routine_box.layout()
        right_margin = routine_layout.contentsMargins().right() if routine_layout is not None else 0
        control_row_top = routine_box.contentsRect().top()
        status_bar = getattr(self, "selected_routine_status_bar", None)
        if status_bar is not None and status_bar.parentWidget() is not None:
            status_bar_top = routine_box.mapFromGlobal(
                status_bar.mapToGlobal(status_bar.rect().topLeft())
            ).y()
            control_row_top = status_bar_top
            container.setFixedHeight(status_bar.height())
        if routine_layout is not None:
            margins = routine_layout.contentsMargins()
            required_top_margin = container.height()
            stock_table = getattr(self, "stock_table", None)
            if stock_table is not None and stock_table.parentWidget() is not None:
                stock_table_top = routine_box.mapFromGlobal(
                    stock_table.mapToGlobal(stock_table.rect().topLeft())
                ).y()
                required_top_margin = max(
                    required_top_margin,
                    stock_table_top - routine_box.contentsRect().top(),
                )
            if margins.top() < required_top_margin:
                routine_layout.setContentsMargins(
                    margins.left(),
                    required_top_margin,
                    margins.right(),
                    margins.bottom(),
                )
                routine_layout.activate()
        container.move(
            max(0, routine_box.width() - right_margin - container.width()),
            control_row_top,
        )
        container.raise_()

    def _update_routine_tree_display_level_badges(self) -> None:
        valid_only = bool(getattr(self, "_routine_tree_valid_only", False))
        valid_button = getattr(self, "_routine_tree_valid_button", None)
        if valid_button is not None:
            valid_button.setChecked(valid_only)
            valid_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if valid_only
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            valid_button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=valid_color,
                    border_color=valid_color,
                )
            )

        selected_level = str(getattr(self, "_routine_tree_display_level", "category") or "category")
        for level, button in getattr(self, "_routine_tree_display_level_buttons", {}).items():
            color = AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            if level == selected_level:
                color = AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=color,
                )
            )

        selected_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        scope_enabled = self._routine_tree_scope_filter_available()
        disabled_style = (
            "QPushButton:disabled, QPushButton:disabled:hover {"
            " background-color: transparent;"
            " border: 1px solid #D1D5DB;"
            " border-radius: 4px;"
            " color: #9CA3AF;"
            " font-weight: 600;"
            " padding: 0 6px;"
            "}"
        )
        for scope, button in getattr(self, "_routine_tree_display_scope_buttons", {}).items():
            button.setEnabled(scope_enabled)
            button.setCursor(Qt.PointingHandCursor if scope_enabled else Qt.ArrowCursor)
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if scope_enabled and scope == selected_scope
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=color,
                )
                + disabled_style
            )

        supported_criteria = AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA.get(
            selected_level,
            frozenset(),
        )
        selected_criterion = str(
            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
        )
        for criterion, button in getattr(self, "_routine_tree_display_criterion_buttons", {}).items():
            enabled = criterion in supported_criteria
            button.setEnabled(enabled)
            button.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if enabled and criterion == selected_criterion
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton",
                    text_color=color,
                    border_color=color,
                )
                + disabled_style
        )

    def _set_routine_tree_valid_only(self, enabled: bool) -> None:
        previous_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        was_valid_stock_only = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and previous_level == "stock"
        )
        was_representative_sort = previous_level in {"category", "routine"}
        self._routine_tree_valid_only = bool(enabled)
        self._update_routine_tree_display_level_badges()
        current_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        is_representative_sort = current_level in {"category", "routine"}
        is_valid_stock_only = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and current_level == "stock"
        )
        stock_sort_active = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and current_level == "stock"
            and str(getattr(self, "_routine_tree_display_scope", "") or "")
            in {"all", "current", "historical"}
        )
        self._routine_tree_stock_performance_sort_active = bool(stock_sort_active)
        if (
            was_valid_stock_only
            or is_valid_stock_only
            or
            was_representative_sort
            or is_representative_sort
            or stock_sort_active
        ):
            self.load_routine_table()
        else:
            self._apply_routine_tree_collapse_visibility()
            self.routine_table.viewport().update()

    def _routine_tree_scope_filter_available(self) -> bool:
        return (
            str(getattr(self, "_routine_tree_display_level", "category") or "category")
            in {"category", "routine", "stock"}
        )

    def _set_routine_tree_display_scope(self, scope: str) -> None:
        clean_scope = str(scope or "").strip()
        if clean_scope not in {"all", "current", "historical"}:
            return
        if not self._routine_tree_scope_filter_available():
            return
        current_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        if clean_scope == current_scope and not (
            clean_scope == "current"
            and bool(
                getattr(
                    self,
                    "_routine_tree_stock_performance_sort_active",
                    False,
                )
            )
        ):
            return

        scroll_bar = self.routine_table.verticalScrollBar()
        scroll_value = scroll_bar.value()
        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        if display_level == "stock":
            self._routine_tree_stock_performance_sort_active = bool(
                getattr(self, "_routine_tree_valid_only", False)
            )
        self._routine_tree_display_scope = clean_scope
        self._routine_tree_last_stock_scope = clean_scope
        self.load_routine_table()
        scroll_bar.setValue(scroll_value)
        self._update_routine_tree_display_level_badges()

    def _refresh_routine_tree_display_state(self) -> None:
        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        display_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        display_metric = str(
            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
        )
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            updated_metadata = dict(metadata)
            updated_metadata["display_level"] = display_level
            updated_metadata["display_scope"] = display_scope
            updated_metadata["display_metric"] = display_metric
            item.setData(Qt.UserRole, updated_metadata)
            row_widget = self.routine_table.cellWidget(row, 0)
            if row_widget is None:
                continue
            for metric in ("period", "profit", "average", "efficiency"):
                metric_name = metric.title()
                left_label = row_widget.findChild(
                    QLabel,
                    f"autoTradeSettingRoutineTreePerformance{metric_name}LeftValue",
                )
                if left_label is None:
                    continue
                right_label = row_widget.findChild(
                    QLabel,
                    f"autoTradeSettingRoutineTreePerformance{metric_name}RightValue",
                )
                default_values = {
                    "period": ("0", ""),
                    "profit": ("0", "0.00%"),
                    "average": ("0", "0.00%"),
                    "efficiency": ("0.0", ""),
                }
                left_fallback, right_fallback = default_values[metric]
                left_value, right_value = self._routine_tree_metric_values(
                    updated_metadata,
                    metric,
                    left_fallback,
                    right_fallback,
                )
                left_label.setText(left_value)
                if right_label is not None:
                    right_label.setText(right_value)
        self._apply_routine_tree_collapse_visibility()
        self._update_routine_tree_display_level_badges()
        self.routine_table.viewport().update()

    def _set_routine_tree_display_criterion(self, criterion: str) -> None:
        clean_criterion = str(criterion or "").strip()
        supported_criteria = AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA.get(
            str(getattr(self, "_routine_tree_display_level", "category") or "category"),
            frozenset(),
        )
        if clean_criterion not in supported_criteria:
            return

        self._routine_tree_display_criterion = clean_criterion
        display_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        should_sort_stocks = (
            display_level == "stock"
            and str(getattr(self, "_routine_tree_display_scope", "") or "")
            in {"all", "current", "historical"}
        )
        should_sort_representatives = (
            display_level in {"category", "routine"}
        )
        self._routine_tree_stock_performance_sort_active = should_sort_stocks
        self._update_routine_tree_display_level_badges()
        if should_sort_stocks or should_sort_representatives:
            scroll_bar = self.routine_table.verticalScrollBar()
            scroll_value = scroll_bar.value()
            self.load_routine_table()
            scroll_bar.setValue(scroll_value)
        else:
            self._refresh_routine_tree_display_state()

    def _apply_routine_tree_display_level_command(self, level: str) -> None:
        clean_level = str(level or "").strip()
        collapsed_definitions = set(
            getattr(self, "_collapsed_auto_trade_definition_ids", set())
        )
        collapsed_instances = set(
            getattr(self, "_collapsed_auto_trade_instance_ids", set())
        )
        definition_ids: set[str] = set()
        instance_ids: set[str] = set()
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict) or not bool(
                metadata.get("has_toggle_children", True)
            ):
                continue
            row_kind = str(metadata.get("row_kind", "") or "")
            if row_kind == "definition":
                parent_id = routine_tree_parent_identity(metadata)
                if parent_id:
                    definition_ids.add(parent_id)
            elif row_kind == "instance":
                instance_id = str(metadata.get("instance_id", "") or "").strip()
                if instance_id:
                    instance_ids.add(instance_id)

        if clean_level == "category":
            collapsed_definitions.update(definition_ids)
        elif clean_level == "routine":
            collapsed_definitions.difference_update(definition_ids)
            collapsed_instances.update(instance_ids)
        elif clean_level == "stock":
            collapsed_definitions.difference_update(definition_ids)
            collapsed_instances.difference_update(instance_ids)

        self._collapsed_auto_trade_definition_ids = collapsed_definitions
        self._collapsed_auto_trade_instance_ids = collapsed_instances

    def _set_routine_tree_display_level(self, level: str) -> None:
        clean_level = str(level or "").strip()
        if clean_level not in {"category", "routine", "stock"}:
            return

        previous_level = str(getattr(self, "_routine_tree_display_level", "") or "")
        was_sort_level = previous_level in {"category", "routine"}
        self._routine_tree_display_level = clean_level
        if clean_level == "stock":
            restored_scope = str(
                getattr(self, "_routine_tree_last_stock_scope", "all") or "all"
            )
            self._routine_tree_display_scope = (
                restored_scope
                if restored_scope in {"all", "current", "historical"}
                else "all"
            )
        else:
            current_scope = str(
                getattr(self, "_routine_tree_display_scope", "") or ""
            )
            if current_scope not in {"all", "current", "historical"}:
                current_scope = str(
                    getattr(self, "_routine_tree_last_stock_scope", "all") or "all"
                )
            if current_scope not in {"all", "current", "historical"}:
                current_scope = "all"
            self._routine_tree_display_scope = current_scope
            self._routine_tree_last_stock_scope = current_scope
        supported_criteria = AUTO_TRADE_SETTING_ROUTINE_TREE_DISPLAY_CRITERIA[clean_level]
        if str(getattr(self, "_routine_tree_display_criterion", "profit") or "profit") not in supported_criteria:
            self._routine_tree_display_criterion = "profit"
        representative_sort = clean_level in {"category", "routine"}
        stock_sort_active = (
            bool(getattr(self, "_routine_tree_valid_only", False))
            and
            clean_level == "stock"
            and str(getattr(self, "_routine_tree_display_scope", "") or "")
            in {"all", "current", "historical"}
        )
        self._routine_tree_stock_performance_sort_active = bool(stock_sort_active)
        self._apply_routine_tree_display_level_command(clean_level)
        self._update_routine_tree_display_level_badges()
        if was_sort_level or representative_sort or stock_sort_active:
            self.load_routine_table()
        else:
            self._refresh_routine_tree_display_state()

    def eventFilter(self, obj, event) -> bool:
        selected_stock_button = getattr(
            self,
            "selected_routine_status_buttons",
            {},
        ).get("all")
        if (
            obj is selected_stock_button
            and event.type() == QEvent.MouseButtonRelease
            and bool(
                getattr(
                    self,
                    "_selected_stock_double_click_release_pending",
                    False,
                )
            )
        ):
            self._selected_stock_double_click_release_pending = False
            return True
        if (
            obj is selected_stock_button
            and event.type() == QEvent.MouseButtonDblClick
        ):
            if hasattr(event, "button") and event.button() != Qt.LeftButton:
                return False
            self._selected_stock_double_click_release_pending = True
            self._toggle_selected_stock_normal_projection()
            return True
        if obj is getattr(self, "routine_box", None) and event.type() == QEvent.Resize:
            self._position_routine_tree_display_level_badges()
        if (
            isinstance(obj, QWidget)
            and obj.property("autoTradeSettingRoutineTreeHoverIdentityRowKind")
            and event.type() in {QEvent.Enter, QEvent.MouseMove}
        ):
            row_widget = obj.parentWidget()
            if obj.property("autoTradeSettingRoutineTreeHoverIdentityRowKind") == "definition":
                self._set_routine_tree_parent_summary_visible(
                    row_widget,
                    True,
                )
            else:
                self._hide_routine_tree_unpinned_parent_summaries()
        if (
            isinstance(obj, QLabel)
            and obj.objectName() == "autoTradeSettingRoutineTreeIcon"
            and (
                obj.property("autoTradeSettingRoutineTreeToggleDefinitionId")
                or obj.property("autoTradeSettingRoutineTreeToggleInstanceId")
            )
        ):
            if (
                obj.property("autoTradeSettingRoutineTreeToggleDefinitionId")
                and event.type() in {QEvent.Enter, QEvent.MouseMove}
            ):
                identity_widget = obj.parentWidget()
                definition_row = (
                    identity_widget.parentWidget()
                    if identity_widget is not None
                    else None
                )
                if definition_row is not None:
                    self._set_routine_tree_parent_summary_visible(
                        definition_row,
                        True,
                    )
            if event.type() == QEvent.MouseButtonPress:
                return True
            if event.type() == QEvent.MouseButtonRelease:
                if hasattr(event, "button") and event.button() != Qt.LeftButton:
                    return True
                if not bool(obj.property("autoTradeSettingRoutineTreeToggleEnabled")):
                    return True
                definition_id = str(obj.property("autoTradeSettingRoutineTreeToggleDefinitionId") or "").strip()
                instance_id = str(obj.property("autoTradeSettingRoutineTreeToggleInstanceId") or "").strip()
                if definition_id:
                    self._toggle_routine_definition_collapsed(definition_id)
                elif instance_id:
                    self._toggle_routine_instance_collapsed(instance_id)
                return True
        if (
            isinstance(obj, QWidget)
            and obj.property("autoTradeSettingRoutineTreeRowKind") == "definition"
        ):
            pinned = bool(obj.property("autoTradeSettingRoutineTreeSummaryPinned"))
            if event.type() in {QEvent.Enter, QEvent.MouseMove}:
                self._set_routine_tree_parent_summary_visible(obj, True)
            elif event.type() == QEvent.Leave:
                self._set_routine_tree_parent_summary_visible(obj, pinned)
        elif (
            isinstance(obj, QWidget)
            and obj.property("autoTradeSettingRoutineTreeRowKind")
            in {"instance", "stock"}
            and event.type() in {QEvent.Enter, QEvent.MouseMove}
        ):
            self._hide_routine_tree_unpinned_parent_summaries()
        return super().eventFilter(obj, event)

    def _toggle_routine_definition_collapsed(self, definition_id: str) -> None:
        clean_definition_id = str(definition_id or "").strip()
        if not clean_definition_id:
            return
        if not self._routine_tree_toggle_enabled("definition", clean_definition_id):
            return
        collapsed = getattr(self, "_collapsed_auto_trade_definition_ids", set())
        if clean_definition_id in collapsed:
            collapsed.remove(clean_definition_id)
        else:
            collapsed.add(clean_definition_id)
        self._collapsed_auto_trade_definition_ids = collapsed
        self._apply_routine_tree_collapse_visibility()
        self._update_routine_tree_display_level_badges()

    def _toggle_routine_instance_collapsed(self, instance_id: str) -> None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id:
            return
        if not self._routine_tree_toggle_enabled("instance", clean_instance_id):
            return
        collapsed_instances = getattr(self, "_collapsed_auto_trade_instance_ids", set())
        if clean_instance_id in collapsed_instances:
            collapsed_instances.remove(clean_instance_id)
        else:
            collapsed_instances.add(clean_instance_id)
        self._collapsed_auto_trade_instance_ids = collapsed_instances
        self._apply_routine_tree_collapse_visibility()
        self._update_routine_tree_display_level_badges()

    def _routine_tree_toggle_enabled(self, row_kind: str, target_id: str) -> bool:
        clean_row_kind = str(row_kind or "").strip()
        clean_target_id = str(target_id or "").strip()
        if not clean_row_kind or not clean_target_id:
            return False
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("row_kind", "") or "") != clean_row_kind:
                continue
            metadata_target_id = (
                routine_tree_parent_identity(metadata)
                if clean_row_kind == "definition"
                else str(metadata.get("instance_id", "") or "")
            )
            if metadata_target_id != clean_target_id:
                continue
            return bool(metadata.get("has_toggle_children", True))
        return False

    def _apply_routine_tree_collapse_visibility(self) -> None:
        collapsed_definitions = getattr(self, "_collapsed_auto_trade_definition_ids", set())
        collapsed_instances = getattr(self, "_collapsed_auto_trade_instance_ids", set())
        valid_only = bool(getattr(self, "_routine_tree_valid_only", False))
        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        display_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        historical_scope = (
            display_level == "stock" and display_scope == "historical"
        )
        valid_stock_only = valid_only and display_level == "stock"
        current_definition_collapsed = False
        current_definition_filtered = False
        current_instance_id = ""
        current_instance_collapsed = False
        current_instance_filtered = False
        child_rows_visible = False
        definition_summary_rows: list[tuple[QWidget, bool, bool]] = []
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(metadata, dict):
                continue
            row_kind = str(metadata.get("row_kind", "") or "")
            definition_id = routine_tree_parent_identity(metadata)
            instance_id = str(metadata.get("instance_id", "") or "")
            widget = self.routine_table.cellWidget(row, 0)
            icon = widget.findChild(QLabel, "autoTradeSettingRoutineTreeIcon") if widget is not None else None
            if row_kind == "definition":
                has_toggle_children = bool(metadata.get("has_toggle_children", True))
                if display_level == "category":
                    definition_valid = bool(
                        metadata.get("has_valid_stock_relation", False)
                    )
                else:
                    definition_valid = bool(metadata.get("has_stocked_instances", False))
                current_definition_filtered = valid_only and not definition_valid
                if not has_toggle_children:
                    collapsed_definitions.discard(definition_id)
                current_definition_collapsed = has_toggle_children and definition_id in collapsed_definitions
                current_instance_id = ""
                current_instance_collapsed = False
                current_instance_filtered = False
                self.routine_table.setRowHidden(
                    row,
                    valid_stock_only or current_definition_filtered,
                )
                if icon is not None:
                    icon.setText("\u25b6" if current_definition_collapsed or not has_toggle_children else "\u25bc")
                if widget is not None:
                    definition_summary_rows.append(
                        (
                            widget,
                            has_toggle_children,
                            current_definition_collapsed,
                        )
                    )
                continue
            hidden_by_definition = current_definition_collapsed
            if row_kind == "instance":
                has_toggle_children = bool(metadata.get("has_toggle_children", True))
                current_instance_filtered = (
                    valid_only
                    and display_level in {"routine", "stock"}
                    and not bool(metadata.get("has_displayable_stocks", False))
                )
                if not has_toggle_children:
                    collapsed_instances.discard(instance_id)
                current_instance_id = instance_id
                current_instance_collapsed = has_toggle_children and instance_id in collapsed_instances
                row_hidden = (
                    valid_stock_only
                    or
                    hidden_by_definition
                    or current_definition_filtered
                    or current_instance_filtered
                )
                self.routine_table.setRowHidden(row, row_hidden)
                child_rows_visible = child_rows_visible or not row_hidden
                if icon is not None:
                    icon.setText("\u25b6" if current_instance_collapsed or not has_toggle_children else "\u25bc")
                continue
            if valid_stock_only and row_kind == "stock":
                self.routine_table.setRowHidden(row, False)
                child_rows_visible = True
                continue
            hidden_by_instance = bool(
                current_instance_id
                and instance_id == current_instance_id
                and current_instance_collapsed
            )
            row_hidden = (
                hidden_by_definition
                or hidden_by_instance
                or current_definition_filtered
                or (current_instance_filtered and row_kind != "stock")
            )
            self.routine_table.setRowHidden(row, row_hidden)
            if row_kind == "stock":
                child_rows_visible = child_rows_visible or not row_hidden

        for widget, has_toggle_children, definition_collapsed in definition_summary_rows:
            summary_visible = (
                definition_collapsed
                if has_toggle_children
                else not child_rows_visible
            )
            widget.setProperty(
                "autoTradeSettingRoutineTreeSummaryPinned",
                summary_visible,
            )
            self._set_routine_tree_parent_summary_visible(
                widget,
                summary_visible,
            )

    def load_routine_table(self) -> None:
        current_metadata = self.current_selected_routine_row_metadata()
        snapshot = auto_trade_initial_read_snapshot(self)
        definitions = list(
            snapshot.get("definitions", ())
            if snapshot is not None
            else load_routine_definitions()
        )
        loaded_instances = list(
            snapshot.get("instances", ())
            if snapshot is not None
            else load_persisted_routine_instances()
        )
        definition_by_id = {
            str(definition.definition_id): definition
            for definition in definitions
        }
        projection_override = getattr(
            self,
            "_routine_tree_projected_instance_ids_override",
            None,
        )
        parent_specs: list[dict[str, object]] = []
        def definition_parent_specs(
            projected_instance_ids: set[str],
        ) -> tuple[list[object], list[dict[str, object]]]:
            projected_instances = [
                instance
                for instance in loaded_instances
                if str(instance.instance_id) in projected_instance_ids
            ]
            instances_by_definition: dict[str, list[object]] = {}
            for instance in projected_instances:
                instances_by_definition.setdefault(
                    str(instance.definition_id),
                    [],
                ).append(instance)
            specs = [
                {
                    "group_id": "",
                    "parent_id": str(definition.definition_id),
                    "display_name": str(definition.display_name),
                    "registration_definition": definition,
                    "child_instances": instances_by_definition.get(
                        str(definition.definition_id),
                        [],
                    ),
                }
                for definition in definitions
            ]
            return projected_instances, specs

        if callable(projection_override):
            override_instance_ids = {
                str(instance_id or "").strip()
                for instance_id in projection_override(loaded_instances)
                if str(instance_id or "").strip()
            }
            instances, parent_specs = definition_parent_specs(override_instance_ids)
        else:
            group_records = list(
                snapshot.get("groups", ())
                if snapshot is not None
                else get_group_records()
            )
            base_stocks = list(
                snapshot.get("stocks", ())
                if snapshot is not None
                else read_base_stocks()
            )
            group_projection = build_main_group_projection(
                group_records,
                loaded_instances,
                base_stocks,
            )
            canonical_instance_ids = {
                projected_instance.instance_id
                for projected_group in group_projection
                for projected_instance in projected_group.instances
            }
            try:
                effective_instance_ids = auto_trade_projected_instance_ids(
                    loaded_instances,
                    groups=group_records,
                    stocks=base_stocks,
                )
            except TypeError:
                effective_instance_ids = auto_trade_projected_instance_ids(
                    loaded_instances
                )
            if effective_instance_ids != canonical_instance_ids:
                instances, parent_specs = definition_parent_specs(
                    effective_instance_ids
                )
                group_projection = ()
            else:
                instances = [
                projected_instance.instance
                for projected_group in group_projection
                for projected_instance in projected_group.instances
                ]
            group_record_by_id = {
                str(getattr(group, "group_id", "") or "").strip(): group
                for group in group_records
                if str(getattr(group, "group_id", "") or "").strip()
            }
            for projected_group in group_projection:
                child_instances = [
                    projected_instance.instance
                    for projected_instance in projected_group.instances
                ]
                group_record = group_record_by_id.get(projected_group.group_id)
                group_definition_id = str(
                    getattr(group_record, "definition_id", "") or ""
                ).strip()
                registration_definition = definition_by_id.get(group_definition_id)
                parent_specs.append(
                    {
                        "group_id": projected_group.group_id,
                        "parent_id": projected_group.group_id,
                        "display_name": projected_group.display_name,
                        "registration_definition": registration_definition,
                        "child_instances": child_instances,
                    }
                )
        instance_counts = self._routine_instance_operation_counts()
        current_stocks_by_instance = self._current_stocks_by_instance()
        historical_stocks_by_instance = self._historical_stocks_by_instance()
        performance_source_cache: dict[str, dict[str, object]] = {}
        collapsed = getattr(self, "_collapsed_auto_trade_definition_ids", set())
        collapsed_instances = getattr(self, "_collapsed_auto_trade_instance_ids", set())
        selected_scope = str(
            getattr(self, "_routine_tree_display_scope", "") or ""
        )
        stock_data_scope = selected_scope
        if stock_data_scope not in {"all", "current", "historical"}:
            stock_data_scope = str(
                getattr(self, "_routine_tree_last_stock_scope", "all") or "all"
            )
        if stock_data_scope not in {"all", "current", "historical"}:
            stock_data_scope = "all"
        rows: list[dict[str, object]] = []
        global_stock_sort_values: dict[tuple[str, str, str], float] = {}
        display_level_for_rows = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        valid_only_for_rows = bool(
            getattr(self, "_routine_tree_valid_only", False)
        )
        sort_visible_stocks_by_metric = (
            bool(
                getattr(
                    self,
                    "_routine_tree_stock_performance_sort_active",
                    False,
                )
            )
            or (
                display_level_for_rows == "routine"
            )
        )

        for parent_spec in parent_specs:
            definition = parent_spec.get("registration_definition")
            definition_id = str(
                getattr(definition, "definition_id", "") or ""
            ).strip()
            group_id = str(parent_spec.get("group_id", "") or "").strip()
            parent_id = str(parent_spec.get("parent_id", "") or definition_id).strip()
            parent_display_name = str(
                parent_spec.get("display_name", "") or ""
            ).strip()
            child_instances = sorted(
                list(parent_spec.get("child_instances", []) or []),
                key=lambda instance: (
                    str(getattr(instance, "display_name", "") or "").casefold(),
                    str(getattr(instance, "instance_id", "") or ""),
                ),
            )
            if valid_only_for_rows and display_level_for_rows == "routine":
                child_instances = [
                    instance
                    for instance in child_instances
                    if current_stocks_by_instance.get(str(instance.instance_id), [])
                ]
            display_stocks_by_instance: dict[str, list[dict[str, object]]] = {}
            for instance in child_instances:
                instance_id = str(instance.instance_id)
                current_stocks = current_stocks_by_instance.get(instance_id, [])
                historical_stocks = [
                    stock
                    for stock in historical_stocks_by_instance.get(instance_id, [])
                ]
                if stock_data_scope == "historical":
                    display_stocks = list(historical_stocks)
                elif stock_data_scope == "all":
                    display_stocks = current_stocks + historical_stocks
                else:
                    display_stocks = list(current_stocks)
                if sort_visible_stocks_by_metric:
                    display_criterion = str(
                        getattr(
                            self,
                            "_routine_tree_display_criterion",
                            "profit",
                        )
                        or "profit"
                    )
                    source_key_by_criterion = {
                        "period": "trade_days",
                        "profit": "realized_profit",
                        "average": "average",
                        "efficiency": "profit_factor",
                    }
                    source_key = source_key_by_criterion.get(
                        display_criterion,
                        "realized_profit",
                    )

                    def _performance_sort_value(
                        stock: dict[str, object],
                    ) -> float:
                        stock_path_key = str(
                            stock.get("stock_path", "") or ""
                        ).strip()
                        is_historical = bool(
                            stock.get("is_historical", False)
                        )
                        cache_key = stock_path_key
                        if is_historical or not cache_key:
                            cache_key = "|".join(
                                (
                                    str(
                                        stock.get("instance_id", "")
                                        or instance_id
                                    ).strip(),
                                    str(
                                        stock.get("stock_code", "") or ""
                                    ).strip(),
                                    stock_path_key,
                                    (
                                        "historical"
                                        if is_historical
                                        else "current"
                                    ),
                                )
                            )
                        if cache_key not in performance_source_cache:
                            performance_source_cache[cache_key] = (
                                self._routine_tree_stock_performance_source(
                                    stock
                                )
                            )
                        raw_value = performance_source_cache[cache_key].get(
                            source_key
                        )
                        if (
                            source_key == "profit_factor"
                            and raw_value is None
                        ):
                            raw_value = performance_source_cache[
                                cache_key
                            ].get("efficiency")
                        if source_key == "profit_factor":
                            value = normalize_profit_factor(raw_value)
                        else:
                            try:
                                value = float(raw_value)
                            except (TypeError, ValueError):
                                value = 0.0
                        stock_key = (
                            str(stock.get("instance_id", "") or instance_id).strip(),
                            str(stock.get("stock_code", "") or "").strip(),
                            str(stock.get("stock_path", "") or "").strip(),
                        )
                        global_stock_sort_values[stock_key] = value
                        return value

                    display_stocks = sorted(
                        display_stocks,
                        key=_performance_sort_value,
                        reverse=True,
                    )
                display_stocks_by_instance[instance_id] = display_stocks
            definition_stocks = [
                stock
                for instance in child_instances
                for stock in (
                    display_stocks_by_instance.get(str(instance.instance_id), [])
                    if selected_scope
                    else current_stocks_by_instance.get(str(instance.instance_id), [])
                )
            ]
            definition_performance = self._routine_tree_performance_texts(
                definition_stocks,
                performance_source_cache,
            )
            has_definition_children = bool(child_instances)
            if not has_definition_children:
                collapsed.discard(parent_id)
            is_collapsed = has_definition_children and parent_id in collapsed
            rows.append(
                {
                    "row_kind": "definition",
                    "definition_id": definition_id,
                    "group_id": group_id,
                    "tree_parent_id": parent_id,
                    "is_discovered_group": bool(group_id),
                    "instance_id": "",
                    "definition_name": parent_display_name,
                    "instance_name": "",
                    "package_dir": str(getattr(definition, "package_dir", "") or ""),
                    "instance_dir": "",
                    "display_name": parent_display_name,
                    "tree_icon": "\u25b6" if is_collapsed or not has_definition_children else "\u25bc",
                    "has_toggle_children": has_definition_children,
                    "has_instances": has_definition_children,
                    "target_instance_ids": tuple(
                        str(instance.instance_id)
                        for instance in child_instances
                        if str(instance.instance_id).strip()
                    ),
                    "has_stocked_instances": any(
                        (
                            display_stocks_by_instance.get(
                                str(instance.instance_id),
                                [],
                            )
                            if selected_scope
                            else current_stocks_by_instance.get(
                                str(instance.instance_id),
                                [],
                            )
                        )
                        for instance in child_instances
                    ),
                    "has_valid_stock_relation": any(
                        int(
                            instance_counts.get(
                                str(instance.instance_id),
                                {},
                            ).get("normal", 0)
                            or 0
                        )
                        > 0
                        for instance in child_instances
                    ),
                    "instance_count": len(child_instances),
                    "registered": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("registered", 0) or 0)
                        for instance in child_instances
                    ),
                    "running": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("running", 0) or 0)
                        for instance in child_instances
                    ),
                    "stopped": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("stopped", 0) or 0)
                        for instance in child_instances
                    ),
                    "error": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("error", 0) or 0)
                        for instance in child_instances
                    ),
                    "normal": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("normal", 0) or 0)
                        for instance in child_instances
                    ),
                    "operation_running": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("operation_running", 0) or 0)
                        for instance in child_instances
                    ),
                    "waiting": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("waiting", 0) or 0)
                        for instance in child_instances
                    ),
                    "excluded": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("excluded", 0) or 0)
                        for instance in child_instances
                    ),
                    "review": sum(
                        int(instance_counts.get(str(instance.instance_id), {}).get("review", 0) or 0)
                        for instance in child_instances
                    ),
                    "display_level": str(
                        getattr(self, "_routine_tree_display_level", "category") or "category"
                    ),
                    "display_scope": selected_scope,
                    "display_metric": str(
                        getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
                    ),
                    **definition_performance,
                }
            )
            for _index, instance in enumerate(child_instances):
                instance_definition_id = str(
                    getattr(instance, "definition_id", "") or ""
                ).strip()
                instance_definition = definition_by_id.get(instance_definition_id)
                instance_dir = Path(instance.rules_path).parent if instance.rules_path else Path()
                instance_id = str(instance.instance_id)
                current_stocks = current_stocks_by_instance.get(instance_id, [])
                historical_stocks = [
                    stock
                    for stock in historical_stocks_by_instance.get(instance_id, [])
                ]
                visible_stocks = display_stocks_by_instance.get(instance_id, [])
                instance_performance = self._routine_tree_performance_texts(
                    visible_stocks if selected_scope else current_stocks,
                    performance_source_cache,
                )
                has_instance_children = bool(current_stocks or historical_stocks)
                if not has_instance_children:
                    collapsed_instances.discard(instance_id)
                count = instance_counts.get(
                    instance_id,
                    {
                        "registered": 0,
                        "running": 0,
                        "stopped": 0,
                        "error": 0,
                        "normal": 0,
                        "excluded": 0,
                        "review": 0,
                    },
                )
                is_instance_collapsed = has_instance_children and instance_id in collapsed_instances
                rows.append(
                    {
                        "row_kind": "instance",
                        "definition_id": instance_definition_id,
                        "group_id": group_id,
                        "instance_id": instance_id,
                        "definition_name": parent_display_name,
                        "instance_name": str(instance.display_name),
                        "package_dir": str(
                            getattr(instance_definition, "package_dir", "") or ""
                        ),
                        "instance_dir": str(instance_dir) if instance_dir else "",
                        "display_name": str(instance.display_name),
                        "tree_icon": "\u25b6" if is_instance_collapsed or not has_instance_children else "\u25bc",
                        "has_toggle_children": has_instance_children,
                        "has_displayable_stocks": bool(
                            visible_stocks if selected_scope else current_stocks
                        ),
                        "instance_group_top_gap": _index > 0,
                        "instance_count": 0,
                        "registered": int(count.get("registered", 0) or 0),
                        "running": int(count.get("running", 0) or 0),
                        "stopped": int(count.get("stopped", 0) or 0),
                        "error": int(count.get("error", 0) or 0),
                        "normal": int(count.get("normal", 0) or 0),
                        "operation_running": int(count.get("operation_running", 0) or 0),
                        "waiting": int(count.get("waiting", 0) or 0),
                        "excluded": int(count.get("excluded", 0) or 0),
                        "review": int(count.get("review", 0) or 0),
                        "display_level": str(
                            getattr(self, "_routine_tree_display_level", "category") or "category"
                        ),
                        "display_scope": selected_scope,
                        "display_metric": str(
                            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
                        ),
                        **instance_performance,
                    }
                )
                for stock_index, stock in enumerate(visible_stocks):
                    stock_name = str(stock.get("stock_name", "") or "").strip()
                    is_historical = bool(stock.get("is_historical", False))
                    stock_performance = self._routine_tree_performance_texts(
                        [stock],
                        performance_source_cache,
                    )
                    rows.append(
                        {
                            "row_kind": "stock",
                            "definition_id": instance_definition_id,
                            "group_id": group_id,
                            "instance_id": instance_id,
                            "definition_name": parent_display_name,
                            "instance_name": str(instance.display_name),
                            "package_dir": str(
                                getattr(instance_definition, "package_dir", "") or ""
                            ),
                            "instance_dir": str(instance_dir) if instance_dir else "",
                            "display_name": stock_name,
                            "display_level": str(
                                getattr(self, "_routine_tree_display_level", "category") or "category"
                            ),
                            "display_scope": selected_scope,
                            "display_metric": str(
                                getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
                            ),
                            "stock_code": str(stock.get("stock_code", "") or ""),
                            "stock_path": str(stock.get("stock_path", "") or ""),
                            "first_stock_for_instance": stock_index == 0,
                            "tree_icon": "\u25aa" if is_historical else "\u2713",
                            "is_historical": is_historical,
                            "is_development_fixture": bool(
                                stock.get("is_development_fixture", False)
                            ),
                            "_source_stock": dict(stock),
                            **stock_performance,
                        }
                    )

        valid_stock_only = bool(
            getattr(self, "_routine_tree_valid_only", False)
        ) and str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        ) == "stock"
        if valid_stock_only:
            parent_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") != "stock"
            ]
            stock_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") == "stock"
            ]
            stock_rows = self._routine_tree_group_stock_rows_by_code(
                stock_rows,
                performance_source_cache,
            )
            rows = parent_rows + stock_rows

        if valid_stock_only and bool(
            getattr(self, "_routine_tree_stock_performance_sort_active", False)
        ):
            display_criterion = str(
                getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
            )
            source_key_by_criterion = {
                "period": "trade_days",
                "profit": "realized_profit",
                "average": "average",
                "efficiency": "profit_factor",
            }
            source_key = source_key_by_criterion.get(
                display_criterion,
                "realized_profit",
            )

            def _global_stock_sort_value(row: dict[str, object]) -> float:
                fixture = row.get("performance_fixture")
                if isinstance(fixture, dict):
                    raw_value = fixture.get(source_key)
                    if source_key == "profit_factor":
                        return normalize_profit_factor(raw_value)
                    try:
                        return float(raw_value)
                    except (TypeError, ValueError):
                        return 0.0
                stock_key = (
                    str(row.get("instance_id", "") or "").strip(),
                    str(row.get("stock_code", "") or "").strip(),
                    str(row.get("stock_path", "") or "").strip(),
                )
                if stock_key in global_stock_sort_values:
                    return global_stock_sort_values[stock_key]
                cache_key = str(row.get("stock_path", "") or "").strip()
                if bool(row.get("is_historical", False)) or not cache_key:
                    cache_key = "|".join(
                        (
                            stock_key[0],
                            stock_key[1],
                            stock_key[2],
                            (
                                "historical"
                                if bool(row.get("is_historical", False))
                                else "current"
                            ),
                        )
                    )
                raw_value = performance_source_cache.get(cache_key, {}).get(source_key)
                if source_key == "profit_factor" and raw_value is None:
                    raw_value = performance_source_cache.get(cache_key, {}).get(
                        "efficiency"
                    )
                if source_key == "profit_factor":
                    return normalize_profit_factor(raw_value)
                try:
                    return float(raw_value)
                except (TypeError, ValueError):
                    return 0.0

            parent_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") != "stock"
            ]
            stock_rows = [
                row
                for row in rows
                if str(row.get("row_kind", "") or "") == "stock"
            ]
            stock_rows = sorted(
                stock_rows,
                key=_global_stock_sort_value,
                reverse=True,
            )
            for index, row in enumerate(stock_rows):
                row["first_stock_for_instance"] = index == 0
            rows = parent_rows + stock_rows

        display_level = str(
            getattr(self, "_routine_tree_display_level", "category") or "category"
        )
        display_criterion = str(
            getattr(self, "_routine_tree_display_criterion", "profit") or "profit"
        )
        if display_level == "category":
            rows = self._routine_tree_sort_definition_blocks(
                rows,
                display_criterion,
            )
        elif display_level == "routine":
            rows = self._routine_tree_sort_instance_blocks(
                rows,
                display_criterion,
            )

        table_updates_enabled = self.routine_table.updatesEnabled()
        sorting_enabled = self.routine_table.isSortingEnabled()
        table_signal_blocker = QSignalBlocker(self.routine_table)
        if table_updates_enabled:
            self.routine_table.setUpdatesEnabled(False)
        if sorting_enabled:
            self.routine_table.setSortingEnabled(False)
        try:
            self.routine_table.setRowCount(len(rows))
            self._configure_routine_tree_row_layout(rows)

            for row, row_data in enumerate(rows):
                row_kind = str(row_data.get("row_kind", "") or "")
                instance_count_text = (
                    f"루틴({int(row_data.get('instance_count', 0) or 0)}) | "
                    if row_kind == "definition"
                    else ""
                )
                display_text = (
                    f"{row_data['display_name']}   "
                    f"{instance_count_text}"
                )
                item = SortableTableWidgetItem("")
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item.setData(Qt.UserRole, dict(row_data))
                item.setData(
                    Qt.ToolTipRole,
                    (
                        str(row_data.get("display_name", "") or "")
                        if row_kind == "stock"
                        else tree_title_tooltip(row_data.get("display_name", ""))
                        if row_kind in {"definition", "instance"}
                        else ""
                    ),
                )
                item.setData(SORT_ROLE, str(row_data["definition_name"]))
                self.routine_table.setItem(row, 0, item)
                row_widget = self._routine_tree_row_widget(
                    dict(row_data),
                    display_text,
                )
                row_height = (
                    32
                    if row_kind == "definition"
                    else AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT
                    if row_kind == "stock"
                    else 30
                )
                if row_kind == "instance" and bool(
                    row_data.get("instance_group_top_gap")
                ):
                    row_height += AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP
                elif row_kind == "stock" and bool(
                    row_data.get("first_stock_for_instance")
                ):
                    row_height += AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP
                row_widget.setMinimumHeight(row_height)
                item.setSizeHint(QSize(0, row_height))
                self.routine_table.setCellWidget(row, 0, row_widget)
                self.routine_table.setRowHeight(row, row_height)

            self.routine_table.clearSelection()
            if current_metadata:
                self.restore_routine_selection_metadata(current_metadata)
            self._refresh_routine_tree_display_state()
        finally:
            if sorting_enabled:
                self.routine_table.setSortingEnabled(True)
            del table_signal_blocker
            if table_updates_enabled:
                self.routine_table.setUpdatesEnabled(True)
                self.routine_table.viewport().update()

    def current_selected_routine_row_metadata(self) -> dict[str, object] | None:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        item = self.routine_table.item(selected_rows[0].row(), 0)
        if item is None:
            return None
        metadata = item.data(Qt.UserRole)
        return dict(metadata) if isinstance(metadata, dict) else None

    def current_selected_definition_id(self) -> str:
        metadata = self.current_selected_routine_row_metadata()
        return str(metadata.get("definition_id", "") or "").strip() if metadata else ""

    def current_selected_instance_id(self) -> str:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata or str(metadata.get("row_kind", "")) not in {"instance", "stock"}:
            return ""
        return str(metadata.get("instance_id", "") or "").strip()

    def current_selected_instance_dir(self) -> Path | None:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata or str(metadata.get("row_kind", "")) not in {"instance", "stock"}:
            return None
        if bool(metadata.get("is_historical", False)):
            return None
        path_text = str(metadata.get("instance_dir", "") or "").strip()
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.exists() else None

    def current_selected_target_instance_ids(self) -> tuple[str, ...]:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata:
            return ()
        row_kind = str(metadata.get("row_kind", "") or "")
        if row_kind in {"instance", "stock"}:
            instance_id = str(metadata.get("instance_id", "") or "").strip()
            return (instance_id,) if instance_id else ()
        if row_kind == "definition":
            projected_instance_ids = metadata.get("target_instance_ids")
            if isinstance(projected_instance_ids, (list, tuple, set)):
                return tuple(
                    instance_id
                    for instance_id in (
                        str(value or "").strip()
                        for value in projected_instance_ids
                    )
                    if instance_id
                )
            group_id = str(metadata.get("group_id", "") or "").strip()
            snapshot = auto_trade_initial_read_snapshot(self)
            instances = (
                snapshot.get("instances", ())
                if snapshot is not None
                else load_persisted_routine_instances()
            )
            if group_id:
                return tuple(
                    str(instance.instance_id)
                    for instance in instances
                    if str(getattr(instance, "group_id", "") or "").strip()
                    == group_id
                )
            definition_id = str(metadata.get("definition_id", "") or "").strip()
            return tuple(
                str(instance.instance_id)
                for instance in instances
                if str(instance.definition_id) == definition_id
            )
        return ()

    def current_selected_routine_name(self) -> str:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata:
            return ""
        if str(metadata.get("row_kind", "")) in {"instance", "stock"}:
            if bool(metadata.get("is_historical", False)):
                return ""
            return str(metadata.get("instance_name", "") or "").strip()
        return str(metadata.get("definition_name", "") or "").strip()

    def current_selected_routine_dir(self) -> Path | None:
        metadata = self.current_selected_routine_row_metadata()
        if not metadata or str(metadata.get("row_kind", "")) not in {"instance", "stock"}:
            return None
        if bool(metadata.get("is_historical", False)):
            return None
        path_text = str(metadata.get("package_dir", "") or "").strip()
        if not path_text:
            return None
        path = Path(path_text)
        return path if path.exists() else None

    def order_execution_boundary(self) -> AutoTradeOrderExecutionBoundary:
        """Return this window's widget-free order execution boundary."""
        boundary = self.__dict__.get("_order_execution_boundary")
        if isinstance(boundary, AutoTradeOrderExecutionBoundary):
            return boundary

        def api_object():
            parent = persistent_feature_owner(self)
            return getattr(parent, "kiwoom_api", None)

        def kiwoom_connected() -> bool:
            api = api_object()
            checker = getattr(api, "is_connected", None)
            return bool(checker()) if callable(checker) else False

        def account_numbers() -> list[str]:
            parent = persistent_feature_owner(self)
            getter = getattr(parent, "kiwoom_account_numbers", None)
            if callable(getter):
                values = getter()
            else:
                api = api_object()
                getter = getattr(api, "account_numbers", None)
                values = getter() if callable(getter) else []
            return list(values) if isinstance(values, list) else []

        def selected_account_no() -> str:
            parent = persistent_feature_owner(self)
            getter = getattr(parent, "selected_account_no", None)
            return str(getter() or "").strip() if callable(getter) else ""

        def send_order_callable():
            return getattr(api_object(), "send_order", None)

        context = AutoTradeOrderExecutionContext(
            kiwoom_connected=kiwoom_connected,
            account_numbers=account_numbers,
            selected_account_no=selected_account_no,
            send_order_callable=send_order_callable,
            selected_stock_info=lambda: self.selected_stock_info(),
            selected_routine_metadata=lambda: self.current_selected_routine_row_metadata(),
            selected_target_instance_ids=lambda: self.current_selected_target_instance_ids(),
            selected_routine_dir=lambda: self.current_selected_routine_dir(),
            routine_dirs=lambda: [],
            stock_dirs_in_routine=lambda _routine_dir: [],
            base_stocks=lambda: read_base_stocks(),
            order_queue_path=lambda: ORDER_QUEUE_PATH,
            order_executions_path=lambda: ORDER_EXECUTIONS_PATH,
            order_locks_path=lambda: ORDER_LOCKS_PATH,
            confirm_runtime_file_init=lambda executions_path, locks_path: (
                self.confirm_execution_runtime_file_init(
                    order_executions_path=executions_path,
                    order_locks_path=locks_path,
                )
            ),
            all_group_stock_dirs=lambda: list(
                load_group_scope().all_group_stock_dirs()
            ),
        )
        boundary = AutoTradeOrderExecutionBoundary(context)
        self._order_execution_boundary = boundary
        return boundary

    def restore_routine_selection(self, routine_name: str) -> None:
        clean_name = str(routine_name or "").strip()
        if not clean_name:
            return
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            metadata = item.data(Qt.UserRole) if item is not None else None
            if isinstance(metadata, dict):
                names = {
                    str(metadata.get("definition_name", "") or "").strip(),
                    str(metadata.get("instance_name", "") or "").strip(),
                }
                if clean_name in names:
                    self.routine_table.selectRow(row)
                    return

    def restore_routine_selection_metadata(self, metadata: dict[str, object]) -> None:
        row_kind = str(metadata.get("row_kind", "") or "").strip()
        definition_id = str(metadata.get("definition_id", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        stock_path = str(metadata.get("stock_path", "") or "").strip()
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            candidate = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("row_kind", "") or "") != row_kind:
                continue
            if str(candidate.get("definition_id", "") or "") != definition_id:
                continue
            if row_kind in {"instance", "stock"} and str(candidate.get("instance_id", "") or "") != instance_id:
                continue
            if row_kind == "stock" and str(candidate.get("stock_path", "") or "") != stock_path:
                continue
            self.routine_table.selectRow(row)
            return

    def on_routine_table_item_clicked(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        metadata = item.data(Qt.UserRole)
        if not isinstance(metadata, dict):
            return
        row_kind = str(metadata.get("row_kind", "") or "")
        return

    def on_routine_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        return

    def _routine_tree_instance_stock_register_metadata(
        self,
        instance_id: object,
    ) -> dict[str, object] | None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id:
            return None
        instance = routine_instance_by_id(clean_instance_id)
        if instance is None:
            return None
        definition_id = str(getattr(instance, "definition_id", "") or "").strip()
        definition = routine_definition_by_id(definition_id) if definition_id else None
        if definition is None:
            return None
        rules_path = getattr(instance, "rules_path", None)
        instance_dir = str(Path(rules_path).parent) if rules_path else ""
        return {
            "row_kind": "instance",
            "definition_id": str(getattr(definition, "definition_id", "") or definition_id),
            "definition_name": str(getattr(definition, "display_name", "") or ""),
            "instance_id": clean_instance_id,
            "instance_name": str(getattr(instance, "display_name", "") or ""),
            "instance_dir": instance_dir,
        }

    def on_routine_table_context_menu(self, pos) -> None:
        item = self.routine_table.itemAt(pos)
        if item is None:
            return
        metadata = item.data(Qt.UserRole)
        if not isinstance(metadata, dict):
            return

        row_kind = str(metadata.get("row_kind", "") or "").strip()
        if row_kind == "definition":
            menu = QMenu(self.routine_table)
            register_action = menu.addAction("루틴등록")
            register_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.open_routine_registration(target)
            )
            group_id = str(metadata.get("group_id", "") or "").strip()
            if group_id:
                menu.addSeparator()
                delete_group_action = menu.addAction("그룹삭제")
                delete_group_action.setEnabled(True)
                delete_group_action.triggered.connect(
                    lambda _checked=False, target=dict(metadata): self.delete_routine_group(target)
                )
                packing_action = menu.addAction("그룹패킹")
                packing_action.setEnabled(True)
                packing_action.triggered.connect(
                    lambda _checked=False, target=dict(metadata): self.pack_routine_group(target)
                )
        elif row_kind == "instance":
            menu = QMenu(self.routine_table)
            settings_action = menu.addAction("설정변경")
            delete_action = menu.addAction("루틴삭제")
            rename_action = menu.addAction("이름변경")
            stock_register_action = menu.addAction("종목등록")
            settings_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.open_routine_instance_settings(target)
            )
            delete_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.delete_routine_instance(target)
            )
            rename_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.rename_routine_instance(target)
            )
            stock_register_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.open_instance_stock_search_register_window(target)
            )
        elif row_kind == "stock":
            menu = QMenu(self.routine_table)
            stock_register_action = menu.addAction("종목등록")
            unregister_action = menu.addAction("등록해제")
            menu.addSeparator()
            convert_action = menu.addAction("등록전환")
            hide_action = menu.addAction("표시삭제")
            stock_register_target = self._routine_tree_instance_stock_register_metadata(
                metadata.get("instance_id", "")
            )
            stock_register_action.setEnabled(stock_register_target is not None)
            unregister_target = self._routine_tree_unregister_target_for_stock_row(metadata)
            convert_action.setEnabled(
                self._routine_tree_register_convert_enabled_for_stock_row(metadata)
            )
            unregister_action.setEnabled(unregister_target is not None)
            hide_action.setEnabled(
                self._routine_tree_hide_historical_display_enabled_for_stock_row(metadata)
            )
            stock_register_action.triggered.connect(
                lambda _checked=False, target=dict(stock_register_target or {}): self.open_instance_stock_search_register_window(target)
            )
            convert_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.convert_historical_stock_to_registered(target)
            )
            unregister_action.triggered.connect(
                lambda _checked=False, unregister_target=dict(unregister_target or {}): self.unregister_routine_tree_stock(
                    unregister_target
                )
            )
            hide_action.triggered.connect(
                lambda _checked=False, target=dict(metadata): self.hide_historical_stock_display(target)
            )
        else:
            return
        menu.exec_(self.routine_table.viewport().mapToGlobal(pos))

    def _routine_tree_stock_is_review_required(self, code: str, name: str) -> bool:
        try:
            stock_dir = StockRepository(PROJECT_ROOT).resolve_stock_dir(code, name)
            state = read_json_dict(stock_dir / "state.json")
        except Exception:
            return False
        return is_review_required_state(state)

    def _routine_tree_registered_stock_for_code(self, code: str) -> dict[str, object] | None:
        clean_code = normalize_stock_code(code)
        if not clean_code:
            return None
        for stock in read_base_stocks():
            if normalize_stock_code(str(stock.get("code", "") or "")) == clean_code:
                return dict(stock)
        return None

    def _routine_tree_register_convert_enabled_for_stock_row(
        self,
        metadata: dict[str, object],
    ) -> bool:
        if str(metadata.get("row_kind", "") or "") != "stock":
            return False
        if not bool(metadata.get("is_historical", False)):
            return False

        code = normalize_stock_code(str(metadata.get("stock_code", "") or ""))
        name = str(metadata.get("display_name", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        if not code or not name or not instance_id:
            return False
        if self._routine_tree_stock_is_review_required(code, name):
            return False

        registered_stock = self._routine_tree_registered_stock_for_code(code)
        if registered_stock is None:
            return True
        assigned_instance_id = str(
            registered_stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if assigned_instance_id:
            return False
        routines = registered_stock.get("routines", [])
        if isinstance(routines, list):
            return not bool(single_routine_list(routines))
        return not bool(str(routines or "").strip())

    def _routine_tree_hide_historical_display_enabled_for_stock_row(
        self,
        metadata: dict[str, object],
    ) -> bool:
        if str(metadata.get("row_kind", "") or "") != "stock":
            return False
        if not bool(metadata.get("is_historical", False)):
            return False
        code = normalize_stock_code(str(metadata.get("stock_code", "") or ""))
        name = str(metadata.get("display_name", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        return bool(code and name and instance_id)

    def _routine_tree_unregister_target_for_stock_row(
        self,
        metadata: dict[str, object],
    ) -> dict[str, str] | None:
        if str(metadata.get("row_kind", "") or "") != "stock":
            return None
        if bool(metadata.get("is_historical", False)):
            return None

        code = normalize_stock_code(str(metadata.get("stock_code", "") or ""))
        name = str(metadata.get("display_name", "") or "").strip()
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        if not code or not name or not instance_id:
            return None
        if self._routine_tree_stock_is_review_required(code, name):
            return None

        matched_stock = self._routine_tree_registered_stock_for_code(code)
        if matched_stock is None:
            return None
        assigned_instance_id = str(
            matched_stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if assigned_instance_id != instance_id:
            return None
        routines = matched_stock.get("routines", [])
        if isinstance(routines, list) and not single_routine_list(routines):
            return None
        if not isinstance(routines, list) and not str(routines or "").strip():
            return None
        can_unassign, _routine_name, _reasons = can_unassign_active_routine_from_stock(
            code,
            name,
        )
        if not can_unassign:
            return None

        return {
            "stock_code": code,
            "stock_name": name,
            "instance_id": instance_id,
            "instance_name": str(metadata.get("instance_name", "") or "").strip(),
            "definition_id": str(metadata.get("definition_id", "") or "").strip(),
            "definition_name": str(metadata.get("definition_name", "") or "").strip(),
        }

    def unregister_routine_tree_stock(
        self,
        target: dict[str, object],
    ) -> bool:
        code = normalize_stock_code(str(target.get("stock_code", "") or ""))
        name = str(target.get("stock_name", "") or "").strip()
        instance_id = str(target.get("instance_id", "") or "").strip()
        if not code or not name or not instance_id:
            show_toast(self, "등록해제할 수 없는 종목입니다.\n검토관리에서 확인하세요.")
            return False

        current_target = self._routine_tree_unregister_target_for_stock_row(
            {
                "row_kind": "stock",
                "stock_code": code,
                "display_name": name,
                "instance_id": instance_id,
                "is_historical": False,
            }
        )
        if current_target is None:
            show_toast(self, "등록해제할 수 없는 종목입니다.\n검토관리에서 확인하세요.")
            return False

        if not update_base_stock_routines(code, name, []):
            show_toast(self, "등록해제할 수 없는 종목입니다.\n검토관리에서 확인하세요.")
            return False

        ensure_single_real_trade_routine_for_stock(code, name)
        scroll_bar = self.routine_table.verticalScrollBar()
        scroll_value = scroll_bar.value()
        self.refresh_all()
        scroll_bar.setValue(scroll_value)
        show_toast(self, f"등록해제 1건 | {name}")
        return True

    def convert_historical_stock_to_registered(
        self,
        metadata: dict[str, object],
    ) -> bool:
        return auto_trade_register_historical_stock_to_original_instance(
            self,
            metadata,
        )

    def _open_routine_settings_dialog(
        self,
        metadata: dict[str, object],
        *,
        registration: bool,
    ) -> None:
        open_routine_settings_dialog_for_owner(
            self,
            metadata,
            registration=registration,
        )

    def open_routine_registration(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "definition":
            return
        self._open_routine_settings_dialog(metadata, registration=True)

    def delete_routine_group(self, metadata: dict[str, object]) -> bool:
        owner = persistent_feature_owner(self)
        handler = getattr(owner, "delete_routine_group_completely", None)
        if not callable(handler):
            return False
        return bool(
            handler(
                str(metadata.get("group_id", "") or "").strip(),
                str(metadata.get("display_name", "") or metadata.get("definition_name", "")).strip(),
            )
        )

    def pack_routine_group(self, metadata: dict[str, object]) -> bool:
        owner = persistent_feature_owner(self)
        handler = getattr(owner, "pack_routine_group", None)
        if not callable(handler):
            return False
        return bool(handler(str(metadata.get("group_id", "") or "").strip()))

    def open_routine_instance_settings(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        self._open_routine_settings_dialog(metadata, registration=False)

    def rename_routine_instance(self, metadata: dict[str, object]) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        current_name = str(metadata.get("instance_name", "") or "").strip()
        if not instance_id:
            return
        self.finish_routine_instance_name_edit(save=True)

        row = -1
        item = None
        for candidate_row in range(self.routine_table.rowCount()):
            candidate_item = self.routine_table.item(candidate_row, 0)
            candidate_metadata = (
                candidate_item.data(Qt.UserRole)
                if candidate_item is not None
                else None
            )
            if not isinstance(candidate_metadata, dict):
                continue
            if str(candidate_metadata.get("row_kind", "") or "") != "instance":
                continue
            if str(candidate_metadata.get("instance_id", "") or "").strip() != instance_id:
                continue
            row = candidate_row
            item = candidate_item
            break
        if row < 0 or item is None:
            return

        index = self.routine_table.model().index(row, 0)
        row_widget = self.routine_table.cellWidget(row, 0)
        title_label = (
            row_widget.findChild(QLabel, "autoTradeSettingRoutineTreeTitle")
            if row_widget is not None
            else None
        )
        if title_label is not None:
            top_left = title_label.mapTo(self.routine_table.viewport(), title_label.rect().topLeft())
            label_rect = QRect(top_left, title_label.size())
        else:
            cell_rect = self.routine_table.visualRect(index)
            label_rect = cell_rect.adjusted(52, 2, -4, -2)

        editor_rect = QRect(
            label_rect.left(),
            label_rect.top(),
            max(96, label_rect.width()),
            max(20, label_rect.height()),
        )
        editor = _AutoTradeRoutineInstanceNameEdit(self)
        editor.setObjectName("routineInstanceNameEditor")
        _apply_routine_inline_edit_style(editor, self.routine_table)
        editor.setText(current_name)
        editor.setGeometry(editor_rect)
        editor.selectAll()
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)

        self._routine_instance_name_editor = editor
        self._routine_instance_name_editor_instance_id = instance_id
        self._routine_instance_name_editor_original = current_name

    def finish_routine_instance_name_edit(self, *, save: bool) -> None:
        editor = self._routine_instance_name_editor
        if editor is None or self._routine_instance_name_edit_finishing:
            return
        self._routine_instance_name_edit_finishing = True
        instance_id = self._routine_instance_name_editor_instance_id
        current_name = self._routine_instance_name_editor_original
        new_name = editor.text().strip()

        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        editor.hide()
        editor.deleteLater()
        self._routine_instance_name_edit_finishing = False

        if not save:
            return
        clean_name = str(new_name or "").strip()
        if not clean_name or clean_name == current_name:
            return

        result = RoutineInstanceRepository(PROJECT_ROOT).rename_instance(instance_id, clean_name)
        if not result.success:
            QMessageBox.warning(
                self,
                "이름변경",
                result.error or "루틴 이름을 변경하지 못했습니다.",
            )
            return
        self.refresh_all()

    def delete_routine_instance(self, metadata: dict[str, object]) -> None:
        delete_routine_instance_with_existing_policy(self, metadata)

    def open_instance_stock_search_register_window(
        self,
        metadata: dict[str, object],
    ) -> None:
        if str(metadata.get("row_kind", "") or "") != "instance":
            return
        self.instance_stock_search_register_window = InstanceStockSearchRegisterDialog(
            self,
            instance_metadata=dict(metadata),
        )
        self.instance_stock_search_register_window.show()

    def hide_historical_stock_display(self, metadata: dict[str, object]) -> None:
        if (
            str(metadata.get("row_kind", "") or "") != "stock"
            or not bool(metadata.get("is_historical", False))
        ):
            return
        instance_id = str(metadata.get("instance_id", "") or "").strip()
        stock_code = str(metadata.get("stock_code", "") or "").strip()
        stock_name = str(metadata.get("display_name", "") or "").strip()
        if not instance_id or not stock_code:
            return
        answer = QMessageBox.question(
            self,
            "표시삭제",
            f"'{stock_name or stock_code}'를 실적 목록에서 제거하시겠습니까?\n\n"
            "이력 데이터는 유지됩니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if bool(metadata.get("is_development_fixture", False)):
            hidden_fixture_keys = getattr(
                self,
                "_hidden_historical_stock_fixture_keys",
                set(),
            )
            hidden_fixture_keys.add((instance_id, stock_code))
            self._hidden_historical_stock_fixture_keys = hidden_fixture_keys
            self.load_routine_table()
            return
        if not StockRepository(PROJECT_ROOT).hide_routine_assignment_history(
            code=stock_code,
            instance_id=instance_id,
        ):
            QMessageBox.warning(
                self,
                "표시삭제",
                "과거 종목 표시 정보를 갱신하지 못했습니다.",
            )
            return
        self.load_routine_table()

    def on_routine_selection_changed(self) -> None:
        if self.current_selected_routine_row_metadata() is not None:
            self._all_stocks_scope_active = False
        self.update_selection_summary_panel()
        self.update_selected_routine_status_bar()
        self.load_selected_routine_stocks()

    def load_selected_routine_stocks(self) -> None:
        auto_trade_load_selected_routine_stocks(self)

    def selected_stock_dir(self) -> Path | None:
        return selected_stock_dir(self)

    def selected_stock_info(self) -> tuple[Path, str, str] | None:
        return selected_stock_info(self)

    def selected_stock_infos(self) -> list[tuple[Path, str, str]]:
        return selected_stock_infos(self)

    def has_early_close_scope_targets(self) -> bool:
        try:
            return bool(_selected_instance_stock_dirs(self))
        except Exception:
            return False

    def fetch_minute_candles_for_selected_stock(self) -> None:
        selected = self.selected_stock_info()
        if selected is None:
            self.statusBarMessage("분봉조회할 종목 1개를 선택하세요.")
            return

        _stock_dir, code, name = selected
        parent = persistent_feature_owner(self)
        api = getattr(parent, "kiwoom_api", None)
        if api is None:
            self.statusBarMessage("키움 API가 초기화되지 않았습니다.")
            return

        if not api.is_available():
            reason = api.unavailable_reason() or "unknown reason"
            self.statusBarMessage(f"키움 API 사용불가: {reason}")
            return

        if not api.is_connected():
            self.statusBarMessage("키움 로그인 후 분봉조회가 가능합니다.")
            return

        def handle_result(result: dict[str, object]) -> None:
            if result.get("ok"):
                saved_count = result.get("saved_count", 0)
                message = f"{code} {name} candles.json 저장 완료: {saved_count}개"
                warning = result.get("warning") or ""
                if result.get("has_more") or warning:
                    message = f"{message} ({warning or 'additional pages available'})"
                self.statusBarMessage(message)
                return

            message = result.get("error") or result.get("message") or result.get("result") or "unknown error"
            self.statusBarMessage(f"{code} {name} 분봉조회 실패: {message}")

        try:
            result = api.request_minute_candles(
                code,
                name,
                interval=1,
                count=300,
                callback=handle_result,
            )
        except Exception as exc:
            self.statusBarMessage(f"{code} {name} 분봉조회 실패: {exc}")
            return

        if result.get("ok"):
            self.statusBarMessage(f"{code} {name} 분봉조회 요청됨")
        else:
            message = result.get("error") or result.get("message") or result.get("result") or "unknown error"
            self.statusBarMessage(f"{code} {name} 분봉조회 실패: {message}")

    def preview_order_candidates_for_pending_signals(self) -> None:
        try:
            from routine_signal_consumer import consume_pending_routine_signals_dry_run

            result = consume_pending_routine_signals_dry_run(limit=5)
            summary = result.get("summary", {}) if isinstance(result, dict) else {}
            signals_checked = int(summary.get("signals_checked", 0) or 0)
            blocked = int(summary.get("blocked", 0) or 0)
            allowed = int(summary.get("allowed", 0) or 0)
            errors = int(summary.get("errors", 0) or 0)
            self.statusBarMessage(
                f"주문후보검증: 확인 {signals_checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}"
            )
        except Exception as exc:
            self.statusBarMessage(f"주문후보검증 실패: {exc}")

    def read_order_from_queue_by_id(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).read_order_from_queue_by_id(*args, **kwargs)

    def execution_enable_confirmation_text(
        self,
        order: dict[str, object],
        enable_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> str:
        return "\n".join(
            [
                "execution_enabled 수동 활성화 확인",
                "",
                "이 작업은 order.execution_enabled 값을 True로 변경합니다.",
                "SendOrder 호출이 아닙니다.",
                "주문 전송이 아닙니다.",
                "REAL_READY 생성이 아닙니다.",
                "real_order_preflight는 자동 실행되지 않습니다.",
                "status는 EXECUTABLE로 유지됩니다.",
                "",
                f"order_id: {enable_preview_result.get('order_id', order.get('id', '-'))}",
                f"code: {enable_preview_result.get('code', order.get('code', '-'))}",
                f"side: {enable_preview_result.get('side', order.get('side', '-'))}",
                f"quantity: {enable_preview_result.get('quantity', order.get('quantity', '-'))}",
                f"order_type: {enable_preview_result.get('order_type', order.get('order_type', '-'))}",
                f"source_signal_id: {enable_preview_result.get('source_signal_id', order.get('source_signal_id', '-'))}",
                f"approval_status: {order.get('approval_status', '-')}",
                f"policy_status: {order.get('policy_status', '-')}",
                "",
                f"queue_path: {queue_path}",
                f"before_sha256: {queue_snapshot.get('sha256', '-')}",
                f"file_size: {queue_snapshot.get('size', '-')}",
                f"mtime: {queue_snapshot.get('mtime', '-')}",
                f"orders_count: {queue_snapshot.get('orders_count', '-')}",
                "",
                "계속하려면 수동 실주문 후보 활성화를 선택하세요.",
            ]
        )

    def confirm_execution_enable_commit(
        self,
        order: dict[str, object],
        enable_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("execution_enabled 수동 활성화 확인")
        dialog.resize(760, 520)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.execution_enable_confirmation_text(
                order,
                enable_preview_result,
                queue_path,
                queue_snapshot,
            )
        )
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("수동 실주문 후보 활성화")
        cancel_button = QPushButton("취소")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(enable_preview_result.get("order_id") or order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_execution_enable_commit",
            target_type="ORDER",
            target_id=order_id or None,
            target_name="Execution Enable",
            order_id=order_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EXECUTION_ENABLE_COMMIT",
                "prompt_title": "execution_enabled 수동 활성화 확인",
                "prompt_summary": "실주문 후보 execution enable 승인",
                "offered_options": ["수동 실주문 후보 활성화", "취소"],
                "selected_option": "수동 실주문 후보 활성화" if accepted else "취소",
                "approval_stage": "EXECUTION_ENABLE",
            },
        )
        return accepted

    def show_execution_enable_result(self, result: dict[str, object]) -> None:
        lines = [
            "Execution Enable Result",
            "",
            f"enabled: {result.get('enabled', result.get('enable_preview', False))}",
            f"enable_stage: {result.get('enable_stage', '-')}",
            f"next_stage: {result.get('next_stage', '-')}",
            f"changed: {result.get('changed', False)}",
            f"order_id: {result.get('order_id', '-')}",
            f"before_status: {result.get('before_status', '-')}",
            f"after_status: {result.get('after_status', '-')}",
            f"before_execution_enabled: {result.get('before_execution_enabled', '-')}",
            f"after_execution_enabled: {result.get('after_execution_enabled', '-')}",
            f"before_sha256: {result.get('before_sha256', '-')}",
            f"after_sha256: {result.get('after_sha256', '-')}",
            f"backup_path: {result.get('backup_path', '-')}",
            "SendOrder called: False",
            "real_order_preflight auto-called: False",
            "",
            "blocked_reasons:",
        ]
        blocked_reasons = result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            lines.extend(f"- {reason}" for reason in blocked_reasons)
        else:
            lines.append("-")

        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Enable Result")
        dialog.resize(760, 520)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def enable_execution_candidate_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "Execution Enable"):
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "수동 실주문 후보 활성화",
            "EXECUTABLE order_id:",
        )
        if not accepted:
            return

        order_id = str(order_id or "").strip()
        if not order_id:
            self.statusBarMessage("수동 실주문 후보 활성화: order_id를 입력하세요.")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            result = {
                "enabled": False,
                "enable_stage": "read_order",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": read_result.get("blocked_reasons", []),
            }
            self.show_execution_enable_result(result)
            self.statusBarMessage("수동 실주문 후보 활성화 차단")
            return

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        enable_preview = preview_execution_enable(
            order_dict,
            {"operator_confirmed_for_execution_enable": True},
        )
        self._last_execution_enable_preview_result = enable_preview
        self._last_execution_enable_queue_snapshot = snapshot

        if enable_preview.get("enable_preview") is not True:
            result = {
                "enabled": False,
                "enable_stage": enable_preview.get("enable_stage"),
                "next_stage": enable_preview.get("next_stage"),
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": enable_preview.get("blocked_reasons", []),
            }
            self.show_execution_enable_result(result)
            self.statusBarMessage("수동 실주문 후보 활성화 차단")
            return

        if not self.confirm_execution_enable_commit(order_dict, enable_preview, queue_path, snapshot):
            self.statusBarMessage("수동 실주문 후보 활성화 취소")
            return

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "enabled": False,
                "enable_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "blocked_reasons": ["queue file changed after execution enable preview; rerun preview"],
            }
            self.show_execution_enable_result(result)
            self.statusBarMessage("수동 실주문 후보 활성화 차단")
            return

        result = commit_execution_enable(
            enable_preview,
            queue_path,
            preview_queue_snapshot=snapshot,
            context={"manual_execution_enable_commit_confirmed": True},
        )
        self.show_execution_enable_result(result)
        status_text = "완료" if result.get("enabled") else "차단"
        self.statusBarMessage(f"수동 실주문 후보 활성화 {status_text}")

    def real_preflight_confirmation_text(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        preflight_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> str:
        return "\n".join(
            [
                "REAL_READY 수동 점검 확인",
                "",
                "이 작업은 대상 order를 REAL_READY로 전환합니다.",
                "",
                "SendOrder 호출이 아닙니다.",
                "",
                "주문 전송이 아닙니다.",
                "",
                "Execution Preview는 자동 실행되지 않습니다.",
                "",
                "Queue 저장이 아닙니다.",
                "",
                "자동 실행 루프에 연결되지 않습니다.",
                "",
                "status",
                "EXECUTABLE",
                "↓",
                "REAL_READY",
                "",
                "execution_enabled",
                "True 유지",
                "",
                f"order_id: {preflight_preview_result.get('order_id', order.get('id', '-'))}",
                f"code: {preflight_preview_result.get('code', order.get('code', '-'))}",
                f"side: {preflight_preview_result.get('side', order.get('side', '-'))}",
                f"quantity: {preflight_preview_result.get('quantity', order.get('quantity', '-'))}",
                f"order_type: {preflight_preview_result.get('order_type', order.get('order_type', '-'))}",
                f"source_signal_id: {preflight_preview_result.get('source_signal_id', order.get('source_signal_id', '-'))}",
                f"approval_status: {order.get('approval_status', '-')}",
                f"policy_status: {order.get('policy_status', '-')}",
                "",
                f"guard.real_trade_enabled: {guard.get('real_trade_enabled', '-')}",
                f"guard.kiwoom_logged_in: {guard.get('kiwoom_logged_in', '-')}",
                f"guard.account_selected: {guard.get('account_selected', '-')}",
                f"guard.account_no: {guard.get('account_no', '-')}",
                f"guard.operator_confirmed: {guard.get('operator_confirmed', '-')}",
                "",
                f"queue_path: {queue_path}",
                f"before_sha256: {queue_snapshot.get('sha256', '-')}",
                f"file_size: {queue_snapshot.get('size', '-')}",
                f"mtime: {queue_snapshot.get('mtime', '-')}",
                f"orders_count: {queue_snapshot.get('orders_count', '-')}",
            ]
        )

    def confirm_real_preflight_commit(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        preflight_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("REAL_READY 수동 점검 확인")
        dialog.resize(760, 560)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.real_preflight_confirmation_text(
                order,
                guard,
                preflight_preview_result,
                queue_path,
                queue_snapshot,
            )
        )
        body.setMinimumHeight(420)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("REAL_READY 수동 점검 실행")
        cancel_button = QPushButton("취소")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(preflight_preview_result.get("order_id") or order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_real_preflight_commit",
            target_type="ORDER",
            target_id=order_id or None,
            target_name="REAL_READY preflight",
            order_id=order_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "REAL_READY_PREFLIGHT_COMMIT",
                "prompt_title": "REAL_READY 수동 점검 확인",
                "prompt_summary": "REAL_READY 수동 preflight 승인",
                "offered_options": ["REAL_READY 수동 점검 실행", "취소"],
                "selected_option": "REAL_READY 수동 점검 실행" if accepted else "취소",
                "approval_stage": "REAL_READY_PREFLIGHT",
            },
        )
        return accepted

    def show_real_preflight_result(self, result: dict[str, object]) -> None:
        lines = [
            "REAL_READY Manual Preflight Result",
            "",
            f"real_preflight_committed: {result.get('real_preflight_committed', result.get('real_preflight_preview', False))}",
            f"preflight_stage: {result.get('preflight_stage', '-')}",
            f"next_stage: {result.get('next_stage', '-')}",
            f"changed: {result.get('changed', False)}",
            f"order_id: {result.get('order_id', '-')}",
            f"before_status: {result.get('before_status', '-')}",
            f"after_status: {result.get('after_status', '-')}",
            f"execution_enabled: {result.get('execution_enabled', '-')}",
            f"real_preflight_status: {result.get('real_preflight_status', '-')}",
            f"real_preflight_reason: {result.get('real_preflight_reason', '-')}",
            f"before_sha256: {result.get('before_sha256', '-')}",
            f"after_sha256: {result.get('after_sha256', '-')}",
            f"backup_path: {result.get('backup_path', '-')}",
            f"send_order_called: {result.get('send_order_called', False)}",
            "Execution Preview auto-called: False",
            "",
            "blocked_reasons:",
        ]
        blocked_reasons = result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            lines.extend(f"- {reason}" for reason in blocked_reasons)
        else:
            lines.append("-")

        dialog = QDialog(self)
        dialog.setWindowTitle("REAL_READY Manual Preflight Result")
        dialog.resize(760, 560)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(420)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def real_preflight_stock_config_for_order(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).real_preflight_stock_config_for_order(*args, **kwargs)

    def build_real_preflight_guard_from_gui(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_real_preflight_guard_from_gui(*args, **kwargs)

    def real_preflight_guard_block_reasons(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).real_preflight_guard_block_reasons(*args, **kwargs)

    def real_preflight_confirmation_preview(self, order: dict[str, object]) -> dict[str, object]:
        try:
            quantity = int(order.get("quantity", 0) or 0)
        except Exception:
            quantity = order.get("quantity", "-")
        return {
            "real_preflight_preview": False,
            "preflight_stage": "operator_confirmation_pending",
            "next_stage": "REAL_PREFLIGHT_COMMIT_REQUIRED",
            "order_id": str(order.get("id", "") or "").strip(),
            "source_signal_id": str(order.get("source_signal_id", "") or "").strip(),
            "code": str(order.get("code", "") or "").strip(),
            "side": str(order.get("side", "") or "").strip().upper(),
            "quantity": quantity,
            "order_type": str(order.get("order_type", "") or "").strip(),
            "blocked_reasons": [],
            "send_order_called": False,
        }

    def run_real_ready_preflight_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "REAL_READY 수동 점검"):
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "REAL_READY 수동 점검",
            "EXECUTABLE order_id:",
        )
        if not accepted:
            return

        order_id = str(order_id or "").strip()
        if not order_id:
            self.statusBarMessage("REAL_READY 수동 점검: order_id를 입력하세요.")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "read_order",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": read_result.get("blocked_reasons", []),
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        guard = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=False)
        guard_reasons = self.real_preflight_guard_block_reasons(guard, include_operator=False)
        if guard_reasons:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "guard",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": guard_reasons,
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        confirmation_preview = self.real_preflight_confirmation_preview(order_dict)
        if not self.confirm_real_preflight_commit(order_dict, guard, confirmation_preview, queue_path, snapshot):
            self.statusBarMessage("REAL_READY manual preflight cancelled")
            return

        guard = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=True)
        guard_reasons = self.real_preflight_guard_block_reasons(guard, include_operator=True)
        if guard_reasons:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "guard",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": guard_reasons,
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY manual preflight blocked")
            return

        preflight_preview = preview_real_order_preflight(
            order_dict,
            guard,
            {"manual_real_preflight_confirmed": True},
        )
        self._last_real_preflight_preview_result = preflight_preview
        self._last_real_preflight_queue_snapshot = snapshot

        if preflight_preview.get("real_preflight_preview") is not True:
            result = {
                "real_preflight_committed": False,
                "preflight_stage": preflight_preview.get("preflight_stage"),
                "next_stage": preflight_preview.get("next_stage"),
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "blocked_reasons": preflight_preview.get("blocked_reasons", []),
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "real_preflight_committed": False,
                "preflight_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "changed": False,
                "order_id": order_id,
                "before_sha256": snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "blocked_reasons": ["queue file changed after real preflight preview; rerun REAL Preflight"],
                "send_order_called": False,
            }
            self.show_real_preflight_result(result)
            self.statusBarMessage("REAL_READY 수동 점검 차단")
            return

        result = commit_real_order_preflight(
            preflight_preview,
            queue_path,
            guard_path=None,
            preview_queue_snapshot=snapshot,
            context={"manual_real_preflight_commit_confirmed": True},
        )
        self.show_real_preflight_result(result)
        status_text = "완료" if result.get("real_preflight_committed") else "차단"
        self.statusBarMessage(f"REAL_READY 수동 점검 {status_text}")

    def execution_runtime_commit_confirmation_text(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        *,
        order_executions_path: Path,
        order_locks_path: Path,
        queue_path: Path,
    ) -> str:
        return "\n".join(
            [
                "Execution Runtime Commit / Queue Commit confirmation",
                "",
                "This action will run Execution Preview, commit runtime records, then allow Queue commit.",
                "SendOrder is not called.",
                "Broker API is not called.",
                "OrderRequest is not created.",
                "DISPATCH_CLAIMED is not entered.",
                "",
                f"account_no: {guard.get('account_no', '-')}",
                f"order_id: {order.get('id', order.get('order_id', '-'))}",
                f"code: {order.get('code', '-')}",
                f"side: {order.get('side', order.get('order_side', '-'))}",
                f"quantity: {order.get('quantity', order.get('order_quantity', '-'))}",
                f"order_executions_path: {order_executions_path}",
                f"order_locks_path: {order_locks_path}",
                f"queue_path: {queue_path}",
                "",
                "Continue only if the selected account, runtime targets, and queue write intent are correct.",
            ]
        )

    def confirm_execution_runtime_commit(
        self,
        order: dict[str, object],
        guard: dict[str, object],
        *,
        order_executions_path: Path,
        order_locks_path: Path,
        queue_path: Path,
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Runtime Commit Confirmation")
        dialog.resize(760, 460)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.execution_runtime_commit_confirmation_text(
                order,
                guard,
                order_executions_path=order_executions_path,
                order_locks_path=order_locks_path,
                queue_path=queue_path,
            )
        )
        body.setMinimumHeight(330)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("Confirm runtime and queue preview")
        cancel_button = QPushButton("Cancel")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        execution_id = str(guard.get("execution_id") or order.get("execution_id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_execution_runtime_commit",
            target_type="EXECUTION",
            target_id=execution_id or order_id or None,
            target_name="Execution Runtime Commit",
            order_id=order_id or None,
            execution_id=execution_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EXECUTION_RUNTIME_COMMIT",
                "prompt_title": "Execution Runtime Commit Confirmation",
                "prompt_summary": "Execution Runtime 및 Queue preview commit 승인",
                "offered_options": ["Confirm runtime and queue preview", "Cancel"],
                "selected_option": "Confirm runtime and queue preview" if accepted else "Cancel",
                "approval_stage": "RUNTIME_COMMIT",
            },
        )
        return accepted

    def runtime_file_init_confirmation_text(
        self,
        *,
        order_executions_path: Path,
        order_locks_path: Path,
    ) -> str:
        return "\n".join(
            [
                "Execution Runtime File Initialization",
                "",
                "Both runtime execution files are missing.",
                "The existing runtime file-init service will create the initial files.",
                "No queue commit is performed by this step.",
                "SendOrder is not called.",
                "",
                f"order_executions_path: {order_executions_path}",
                f"order_locks_path: {order_locks_path}",
                "",
                "Continue only if these project runtime files should be initialized now.",
            ]
        )

    def confirm_execution_runtime_file_init(
        self,
        *,
        order_executions_path: Path,
        order_locks_path: Path,
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Runtime File Initialization")
        dialog.resize(760, 380)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(
            self.runtime_file_init_confirmation_text(
                order_executions_path=order_executions_path,
                order_locks_path=order_locks_path,
            )
        )
        body.setMinimumHeight(260)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("Initialize runtime files")
        cancel_button = QPushButton("Cancel")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_execution_runtime_file_init",
            target_type="EXECUTION_RUNTIME",
            target_name="Execution Runtime 파일 초기화",
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "EXECUTION_RUNTIME_FILE_INIT",
                "prompt_title": "Execution Runtime File Initialization",
                "prompt_summary": "Execution Runtime 파일 초기화 승인",
                "offered_options": ["Initialize runtime files", "Cancel"],
                "selected_option": "Initialize runtime files" if accepted else "Cancel",
                "approval_stage": "RUNTIME_FILE_INITIALIZATION",
            },
        )
        return accepted

    def execution_runtime_environment_flags(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).execution_runtime_environment_flags(*args, **kwargs)

    def ensure_execution_runtime_files_ready(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).ensure_execution_runtime_files_ready(*args, **kwargs)

    def commit_execution_runtime_for_preview(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).commit_execution_runtime_for_preview(*args, **kwargs)

    def preview_execution_for_real_ready_order_manual(self) -> None:
        if not startup_recovery_action_allowed(self, "Execution Preview"):
            return
        order_id, accepted = QInputDialog.getText(
            self,
            "Execution Preview",
            "REAL_READY order_id:",
        )
        if not accepted:
            return

        order_id = str(order_id or "").strip()
        if not order_id:
            self.statusBarMessage("Execution Preview: order_id를 입력하세요.")
            return

        try:
            read_result = self.read_order_from_queue_by_id(order_id, ORDER_QUEUE_PATH)
            order = read_result.get("order") if isinstance(read_result, dict) else {}
            order_dict = order if isinstance(order, dict) else {"id": order_id}
            guard_preview = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=False)
            guard_reasons = self.real_preflight_guard_block_reasons(guard_preview, include_operator=False)
            if guard_reasons:
                self.statusBarMessage("Execution Preview blocked: real trade guard is not ready")
                QMessageBox.warning(
                    self,
                    "Execution Preview blocked",
                    "\n".join(str(reason) for reason in guard_reasons),
                )
                return
            if not self.confirm_execution_runtime_commit(
                order_dict,
                guard_preview,
                order_executions_path=ORDER_EXECUTIONS_PATH,
                order_locks_path=ORDER_LOCKS_PATH,
                queue_path=ORDER_QUEUE_PATH,
            ):
                self.statusBarMessage("Execution Preview cancelled before runtime commit confirmation")
                return

            guard = self.build_real_preflight_guard_from_gui(order_dict, operator_confirmed=True)
            result = preview_execution_for_real_ready_order(order_id, guard, ORDER_QUEUE_PATH)
            runtime_commit = {}
            if result.get("ok") is True:
                runtime_commit = self.commit_execution_runtime_for_preview(
                    order_dict,
                    guard,
                    result,
                    order_executions_path=ORDER_EXECUTIONS_PATH,
                    order_locks_path=ORDER_LOCKS_PATH,
                )
                result["runtime_dry_run_result"] = runtime_commit.get("runtime_dry_run_result")
                result["commit_plan_orchestrator_result"] = runtime_commit.get("commit_plan_orchestrator_result")
                result["runtime_commit_readiness_policy_result"] = runtime_commit.get("runtime_commit_readiness_policy_result")
                result["runtime_commit_result"] = runtime_commit.get("runtime_commit_result")
                result["runtime_commit_blocked_reasons"] = list(runtime_commit.get("blocked_reasons") or [])
                preview_result = result.get("preview_result")
                if isinstance(preview_result, dict):
                    preview_result["runtime_dry_run_result"] = runtime_commit.get("runtime_dry_run_result")
                    preview_result["commit_plan_orchestrator_result"] = runtime_commit.get("commit_plan_orchestrator_result")
                    preview_result["runtime_commit_readiness_policy_result"] = runtime_commit.get("runtime_commit_readiness_policy_result")
                    preview_result["runtime_commit_result"] = runtime_commit.get("runtime_commit_result")
                    preview_result["runtime_commit_blocked_reasons"] = list(runtime_commit.get("blocked_reasons") or [])
            self._last_execution_preview_result = result
            self._last_execution_preview_queue_snapshot = AutoTradeSettingWindow.queue_file_snapshot(ORDER_QUEUE_PATH)
            AutoTradeSettingWindow.update_manual_queue_commit_button_state(self)
            report = build_execution_preview_report(result)
            preview_context = {
                "source": "gui_execution_preview_button",
                "guard": guard,
                "legacy_execution_preview_result": result,
            }
            controller_result = build_execution_readiness_preview_from_context(
                order_id=order_id,
                preview_context=preview_context,
            )
            formatted_result = (
                controller_result.get("formatted_result")
                if isinstance(controller_result, dict)
                else None
            )
            readiness_text = ""
            if isinstance(formatted_result, dict):
                readiness_text = str(formatted_result.get("text", "") or "")
            if readiness_text:
                readiness_report = dict(report)
                readiness_report["text"] = readiness_text
                readiness_report["readiness_controller_result"] = controller_result
                report = readiness_report
            if result.get("ok") is True and runtime_commit and runtime_commit.get("runtime_commit_ready") is not True:
                blocked = "\n".join(str(reason) for reason in runtime_commit.get("blocked_reasons") or [])
                runtime_report = dict(report)
                runtime_report["ok"] = False
                runtime_report["runtime_commit_result"] = runtime_commit.get("runtime_commit_result")
                runtime_report["runtime_commit_blocked_reasons"] = list(runtime_commit.get("blocked_reasons") or [])
                runtime_report["text"] = f"{runtime_report.get('text', '')}\n\n[Runtime Commit]\nBLOCKED\n{blocked}"
                report = runtime_report
            self.show_execution_preview_report(report)
            status_text = "통과" if report.get("ok") else "차단"
            self.statusBarMessage(f"Execution Preview {status_text}: {order_id}")
        except Exception as exc:
            self.statusBarMessage(f"Execution Preview 실패: {exc}")
            QMessageBox.critical(
                self,
                "Execution Preview 실패",
                f"Execution Preview 처리 중 오류가 발생했습니다.\n\n{exc}",
            )

    def show_execution_preview_report(self, report: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Execution Preview Report")
        dialog.resize(900, 650)

        layout = QVBoxLayout()
        title_label = QLabel("Execution Preview Report")
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(str(report.get("text", "")))
        body.setMinimumHeight(500)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def execution_preview_result_dict(self) -> dict[str, object]:
        result = getattr(self, "_last_execution_preview_result", None)
        return result if isinstance(result, dict) else {}

    def queue_write_preview_from_last_execution_preview(self) -> dict[str, object]:
        result = AutoTradeSettingWindow.execution_preview_result_dict(self)
        if isinstance(result.get("queue_write_preview_result"), dict):
            return result["queue_write_preview_result"]

        preview_result = result.get("preview_result")
        if isinstance(preview_result, dict) and isinstance(preview_result.get("queue_write_preview_result"), dict):
            return preview_result["queue_write_preview_result"]

        return {}

    def runtime_commit_result_from_last_execution_preview(self) -> dict[str, object]:
        result = AutoTradeSettingWindow.execution_preview_result_dict(self)
        if isinstance(result.get("runtime_commit_result"), dict):
            return result["runtime_commit_result"]

        preview_result = result.get("preview_result")
        if isinstance(preview_result, dict) and isinstance(preview_result.get("runtime_commit_result"), dict):
            return preview_result["runtime_commit_result"]

        return {}

    @staticmethod
    def queue_file_snapshot(queue_path: Path) -> dict[str, object]:
        return AutoTradeOrderExecutionBoundary.queue_file_snapshot(queue_path)

    def last_execution_preview_queue_snapshot(self) -> dict[str, object]:
        snapshot = getattr(self, "_last_execution_preview_queue_snapshot", None)
        return snapshot if isinstance(snapshot, dict) else {}

    def update_manual_queue_commit_button_state(self) -> None:
        button = getattr(self, "btn_manual_queue_commit", None)
        if button is None:
            return

        queue_write_preview = AutoTradeSettingWindow.queue_write_preview_from_last_execution_preview(self)
        runtime_commit_result = AutoTradeSettingWindow.runtime_commit_result_from_last_execution_preview(self)
        button.setEnabled(
            queue_write_preview.get("write_preview") is True
            and runtime_commit_result.get("status") == "COMMITTED"
            and runtime_commit_result.get("committed") is True
        )

    def manual_queue_commit_confirmation_text(
        self,
        queue_write_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object] | None = None,
    ) -> str:
        record = queue_write_preview_result.get("order_queued_record_preview")
        record_dict = record if isinstance(record, dict) else {}
        snapshot = queue_snapshot if isinstance(queue_snapshot, dict) else AutoTradeSettingWindow.queue_file_snapshot(queue_path)

        return "\n".join(
            [
                "수동 Queue 저장 확인",
                "",
                "이 작업은 ORDER_QUEUED record를 order_queue JSON에 저장합니다.",
                "SendOrder 호출이 아닙니다.",
                "주문 전송이 아닙니다.",
                "자동 실행 루프에 연결되지 않습니다.",
                "",
                f"order_id: {record_dict.get('order_id', '-')}",
                f"request_hash: {record_dict.get('request_hash', '-')}",
                f"lock_id: {record_dict.get('lock_id', '-')}",
                f"queue_pending_id: {record_dict.get('queue_pending_id', '-')}",
                f"order_queued_id: {record_dict.get('id', '-')}",
                f"queue_path: {queue_path}",
                f"before_sha256: {snapshot.get('sha256', '-')}",
                f"file_size: {snapshot.get('size', '-')}",
                f"mtime: {snapshot.get('mtime', '-')}",
                f"orders_count: {snapshot.get('orders_count', '-')}",
                f"backup_path: {queue_path}.bak",
                "",
                "계속하려면 수동 Queue 저장 실행을 선택하세요.",
            ]
        )

    def confirm_manual_queue_commit(
        self,
        queue_write_preview_result: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object] | None = None,
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("수동 Queue 저장 확인")
        dialog.resize(720, 420)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(self.manual_queue_commit_confirmation_text(queue_write_preview_result, queue_path, queue_snapshot))
        body.setMinimumHeight(300)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("수동 Queue 저장 실행")
        cancel_button = QPushButton("취소")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        queued_record = queue_write_preview_result.get("order_queued_record_preview")
        queued_record = queued_record if isinstance(queued_record, dict) else {}
        order_id = str(
            queued_record.get("order_id")
            or queued_record.get("id")
            or queue_write_preview_result.get("order_id")
            or ""
        ).strip()
        execution_id = str(queued_record.get("execution_id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_queue_commit",
            target_type="ORDER",
            target_id=order_id or execution_id or None,
            target_name="수동 Queue 저장",
            order_id=order_id or None,
            execution_id=execution_id or None,
            signal_id=str(queued_record.get("source_signal_id") or "").strip() or None,
            stock_code=str(queued_record.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_QUEUE_COMMIT",
                "prompt_title": "수동 Queue 저장 확인",
                "prompt_summary": "검증된 수동 주문 Queue 저장 승인",
                "offered_options": ["수동 Queue 저장 실행", "취소"],
                "selected_option": "수동 Queue 저장 실행" if accepted else "취소",
                "approval_stage": "MANUAL_QUEUE_COMMIT",
            },
        )
        return accepted

    def verify_manual_queue_commit_read_back(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).verify_manual_queue_commit_read_back(*args, **kwargs)

    def show_manual_queue_commit_result(self, result: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual Queue Commit Result")
        dialog.resize(760, 520)

        commit_result = result.get("commit_result")
        commit_result_dict = commit_result if isinstance(commit_result, dict) else {}
        lines = [
            "Manual Queue Commit Result",
            "",
            f"manual_commit: {result.get('manual_commit')}",
            f"commit_stage: {result.get('commit_stage')}",
            f"next_stage: {result.get('next_stage')}",
            f"before_sha256: {result.get('before_sha256', '-')}",
            f"after_sha256: {result.get('after_sha256', '-')}",
            f"changed: {commit_result_dict.get('changed', result.get('changed', '-'))}",
            f"status: {commit_result_dict.get('status', '-')}",
            f"order_id: {commit_result_dict.get('order_id', '-')}",
            f"order_queued_id: {commit_result_dict.get('order_queued_id', '-')}",
            f"request_hash: {commit_result_dict.get('request_hash', '-')}",
            f"lock_id: {commit_result_dict.get('lock_id', '-')}",
            f"order_queue_path: {commit_result_dict.get('order_queue_path', '-')}",
            f"backup_path: {commit_result_dict.get('backup_path', '-')}",
            f"send_order_called: {commit_result_dict.get('send_order_called', False)}",
            f"execution_enabled: {commit_result_dict.get('execution_enabled', False)}",
            "",
            "blocked_reasons:",
        ]
        blocked_reasons = result.get("blocked_reasons")
        if isinstance(blocked_reasons, list) and blocked_reasons:
            lines.extend(f"- {reason}" for reason in blocked_reasons)
        else:
            lines.append("-")

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def commit_last_execution_preview_queue_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "수동 Queue 저장"):
            return
        queue_write_preview = self.queue_write_preview_from_last_execution_preview()
        if queue_write_preview.get("write_preview") is not True:
            self.statusBarMessage("수동 Queue 저장: 먼저 유효한 Execution Preview를 실행하세요.")
            self.update_manual_queue_commit_button_state()
            return

        queue_path = ORDER_QUEUE_PATH
        preview_snapshot = AutoTradeSettingWindow.last_execution_preview_queue_snapshot(self)
        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if not preview_snapshot.get("sha256"):
            result = {
                "manual_commit": False,
                "commit_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": preview_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": ["queue snapshot from preview is required"],
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.")
            return

        if preview_snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "manual_commit": False,
                "commit_stage": "stale_preview",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": preview_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": ["queue file changed after preview; rerun Execution Preview"],
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.")
            return

        runtime_commit_result = self.runtime_commit_result_from_last_execution_preview()
        if not runtime_commit_result:
            result = {
                "manual_commit": False,
                "commit_stage": "runtime_commit_result",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": current_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": ["runtime commit result is required before runtime queue commit"],
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("Manual Queue commit blocked: runtime commit result is required")
            return

        if not self.confirm_manual_queue_commit(queue_write_preview, queue_path, current_snapshot):
            self.statusBarMessage("수동 Queue 저장: 취소됨")
            return

        queue_commit_readiness = evaluate_execution_queue_commit_readiness(
            runtime_commit_result=runtime_commit_result,
            queue_write_preview_result=queue_write_preview,
            queue_path=queue_path,
            confirmations={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
            },
        )
        if queue_commit_readiness.get("status") != "READY_TO_COMMIT_QUEUE":
            result = {
                "manual_commit": False,
                "commit_stage": "queue_commit_readiness_policy",
                "next_stage": "BLOCKED",
                "commit_result": None,
                "before_sha256": current_snapshot.get("sha256"),
                "after_sha256": current_snapshot.get("sha256"),
                "changed": False,
                "blocked_reasons": list(queue_commit_readiness.get("issues") or ["queue commit readiness policy is not ready"]),
                "queue_commit_readiness_policy_result": queue_commit_readiness,
            }
            self.show_manual_queue_commit_result(result)
            self.statusBarMessage("Manual Queue commit blocked: readiness policy failed")
            return

        result = commit_execution_queue_manually(
            queue_write_preview,
            queue_path,
            context={
                "manual_queue_write_confirmed": True,
                "manual_runtime_queue_write_confirmed": True,
                "event_journal_order": order_dict,
            },
            queue_commit_readiness_policy_result=queue_commit_readiness,
            manual_queue_commit_after_runtime_confirmed=True,
        )
        after_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        result["before_sha256"] = current_snapshot.get("sha256")
        result["after_sha256"] = after_snapshot.get("sha256")
        result["changed"] = current_snapshot.get("sha256") != after_snapshot.get("sha256")
        if result.get("manual_commit") is True:
            read_back = self.verify_manual_queue_commit_read_back(
                queue_path=queue_path,
                queue_write_preview_result=queue_write_preview,
                runtime_commit_result=runtime_commit_result,
            )
            result["queue_commit_read_back"] = read_back
            result["queue_commit_read_back_verified"] = read_back.get("verified") is True
            if read_back.get("verified") is not True:
                blocked = result.get("blocked_reasons")
                if not isinstance(blocked, list):
                    blocked = []
                blocked.extend(str(reason) for reason in read_back.get("issues") or [])
                result["blocked_reasons"] = blocked
        self.show_manual_queue_commit_result(result)
        status_text = "완료" if result.get("manual_commit") and result.get("queue_commit_read_back_verified") else "차단"
        self.statusBarMessage(f"수동 Queue 저장 {status_text}")

    def manual_send_order_confirmation_text(
        self,
        order: dict[str, object],
        call_preview: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> str:
        preview = call_preview.get("send_order_call_preview")
        preview_dict = preview if isinstance(preview, dict) else {}
        params = preview_dict.get("send_order_params")
        params_dict = params if isinstance(params, dict) else {}
        if not params_dict:
            adapter_contract = call_preview.get("adapter_contract_result")
            adapter_contract_dict = adapter_contract if isinstance(adapter_contract, dict) else {}
            params = adapter_contract_dict.get("send_order_params")
            params_dict = params if isinstance(params, dict) else {}
        return "\n".join(
            [
                "Manual Kiwoom SendOrder confirmation",
                "",
                "This action will call Kiwoom SendOrder exactly once.",
                "Queue claim and SendOrder result are recorded before/after the callable boundary.",
                "Broker acceptance is not assumed from SEND_CALL_ACCEPTED.",
                "",
                f"account_no: {params_dict.get('account_no', '-')}",
                f"order_id: {order.get('order_id', order.get('id', '-'))}",
                f"code: {params_dict.get('code', order.get('code', '-'))}",
                f"side/order_name: {params_dict.get('order_name', order.get('side', '-'))}",
                f"quantity: {params_dict.get('quantity', order.get('quantity', '-'))}",
                f"price: {params_dict.get('price', order.get('price', '-'))}",
                f"hoga: {params_dict.get('hoga', '-')}",
                f"queue_path: {queue_path}",
                f"queue_revision: {queue_snapshot.get('revision', '-')}",
                f"queue_sha256: {queue_snapshot.get('sha256', '-')}",
                "",
                "Continue only if this real order should be submitted now.",
            ]
        )

    def confirm_manual_send_order(
        self,
        order: dict[str, object],
        call_preview: dict[str, object],
        queue_path: Path,
        queue_snapshot: dict[str, object],
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual Kiwoom SendOrder Confirmation")
        dialog.resize(760, 520)

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(self.manual_send_order_confirmation_text(order, call_preview, queue_path, queue_snapshot))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        proceed_button = QPushButton("Call SendOrder once")
        cancel_button = QPushButton("Cancel")
        proceed_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(proceed_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        accepted = dialog.exec_() == QDialog.Accepted
        order_id = str(order.get("id") or order.get("order_id") or "").strip()
        execution_id = str(order.get("execution_id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_send_order",
            target_type="ORDER",
            target_id=order_id or None,
            target_name="Manual SendOrder",
            order_id=order_id or None,
            execution_id=execution_id or None,
            signal_id=str(order.get("source_signal_id") or "").strip() or None,
            stock_code=str(order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_SEND_ORDER",
                "prompt_title": "Manual Kiwoom SendOrder Confirmation",
                "prompt_summary": "Queue 주문의 Kiwoom SendOrder 호출 승인",
                "offered_options": ["Call SendOrder once", "Cancel"],
                "selected_option": "Call SendOrder once" if accepted else "Cancel",
                "approval_stage": "SEND_ORDER",
            },
        )
        return accepted

    def build_manual_send_order_environment(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_manual_send_order_environment(*args, **kwargs)

    def send_order_identity_from_record(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).send_order_identity_from_record(*args, **kwargs)

    def build_manual_send_order_call_preview(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_manual_send_order_call_preview(*args, **kwargs)

    def build_manual_final_send_gate_result(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).build_manual_final_send_gate_result(*args, **kwargs)

    def show_manual_send_order_result(self, result: dict[str, object]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual SendOrder Result")
        dialog.resize(760, 520)

        lines = [
            "Manual SendOrder Result",
            "",
            f"status: {result.get('status', '-')}",
            f"stage: {result.get('stage', result.get('executor_stage', '-'))}",
            f"order_id: {result.get('order_id', '-')}",
            f"callable_executed: {result.get('callable_executed', False)}",
            f"send_order_called: {result.get('send_order_called', False)}",
            f"broker_api_called: {result.get('broker_api_called', False)}",
            f"actual_order_sent: {result.get('actual_order_sent', False)}",
            f"queue_result_recorded: {result.get('queue_result_recorded', False)}",
            "",
            "blocked_reasons/issues:",
        ]
        reasons = result.get("blocked_reasons") or result.get("issues") or []
        if isinstance(reasons, list) and reasons:
            lines.extend(f"- {reason}" for reason in reasons)
        else:
            lines.append("-")

        layout = QVBoxLayout()
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText("\n".join(str(line) for line in lines))
        body.setMinimumHeight(380)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def _queue_data_for_manual_order_action(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self)._queue_data_for_manual_order_action(*args, **kwargs)

    def _pending_cancel_duplicate_reason(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self)._pending_cancel_duplicate_reason(*args, **kwargs)

    def _pending_modify_duplicate_reason(self, orders: list[object], original_order_no: str) -> str:
        active_statuses = {
            "ORDER_QUEUED",
            "DISPATCH_CLAIMED",
            "SEND_ATTEMPTED",
            "SEND_CALL_IN_PROGRESS",
            "SEND_CALL_ACCEPTED",
            "SEND_UNCERTAIN",
            "BROKER_ACCEPTED",
        }
        for item in orders:
            record = item if isinstance(item, dict) else {}
            execution_request = record.get("execution_request")
            request_preview = execution_request.get("request_preview") if isinstance(execution_request, dict) else {}
            if not isinstance(request_preview, dict):
                continue
            if str(request_preview.get("order_action") or "").strip().upper() not in {"CANCEL", "MODIFY"}:
                continue
            if str(request_preview.get("original_order_no") or "").strip() != original_order_no:
                continue
            if record.get("original_order_effect_confirmed") is True:
                continue
            if str(record.get("status") or "").strip().upper() in active_statuses:
                return "active cancel/modify request already exists for original_order_no"
        return ""

    def _build_manual_cancel_order_queued_preview(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self)._build_manual_cancel_order_queued_preview(*args, **kwargs)

    def queue_pending_order_cancellations_for_stock_automatically(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).queue_pending_order_cancellations_for_stock_automatically(*args, **kwargs)

    def _build_manual_modify_order_queued_preview(
        self,
        source_order: dict[str, object],
        *,
        queue_revision: object,
        modify_quantity: int,
        modify_price: int,
    ) -> dict[str, object]:
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        source_signal_id = str(source_order.get("source_signal_id") or "").strip()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        account_no = str(source_order.get("account_no") or "").strip()
        code = str(source_order.get("code") or "").strip()
        side = str(source_order.get("side") or "").strip().upper()
        suffix = uuid4().hex[:12]
        order_id = f"{source_order_id}_MODIFY_{suffix}"
        execution_id = f"EXEC_MODIFY_{suffix}"
        lock_id = f"LOCK_MODIFY_{suffix}"
        candidate_id = f"MODIFY_CANDIDATE_{suffix}"
        queue_pending_id = f"QUEUE_PENDING_{candidate_id}"
        hash_payload = {
            "action": "MODIFY",
            "source_order_id": source_order_id,
            "broker_order_no": broker_order_no,
            "account_no": account_no,
            "code": code,
            "side": side,
            "quantity": modify_quantity,
            "price": modify_price,
            "lock_id": lock_id,
        }
        request_hash = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        execution_request = {
            "execution_id": execution_id,
            "order_id": order_id,
            "source_signal_id": source_signal_id,
            "lock_id": lock_id,
            "request_hash": request_hash,
            "guard_snapshot": {"account_no": account_no, "source_queue_revision": queue_revision},
            "request_preview": {
                "account_no": account_no,
                "screen_no": project_order_default_screen_no(),
                "side": side,
                "order_action": "MODIFY",
                "code": code,
                "quantity": modify_quantity,
                "price": modify_price,
                "hoga": "LIMIT",
                "original_order_no": broker_order_no,
                "source_order_id": source_order_id,
            },
        }
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": {
                "id": f"ORDER_QUEUED_{order_id}",
                "status": "ORDER_QUEUED",
                "source": "execution_queue_pending",
                "source_signal_id": source_signal_id,
                "order_id": order_id,
                "candidate_id": candidate_id,
                "queue_pending_id": queue_pending_id,
                "request_hash": request_hash,
                "lock_id": lock_id,
                "execution_id": execution_id,
                "execution_request": execution_request,
                "queue_contract_version": "manual-modify-1",
                "send_order_called": False,
                "execution_enabled": False,
                "blocked_reasons": [],
                "account_no": account_no,
                "code": code,
                "side": side,
                "quantity": modify_quantity,
                "price": modify_price,
                "order_type": "LIMIT",
                "order_action": "MODIFY",
                "modify_source_order_id": source_order_id,
            },
        }

    def confirm_manual_cancel_pending_order(self, source_order: dict[str, object], preview: dict[str, object]) -> bool:
        message = "\n".join(
            [
                "Manual pending order cancel",
                "",
                "This creates an ORDER_QUEUED cancel request and then uses the existing Manual SendOrder flow.",
                "The original open order is not marked cancelled until Kiwoom Chejan confirms it.",
                "",
                f"source_order_id: {source_order.get('order_id', source_order.get('id', '-'))}",
                f"broker_order_no: {source_order.get('broker_order_no', '-')}",
                f"remaining_quantity: {source_order.get('remaining_quantity', '-')}",
                f"account_no: {source_order.get('account_no', '-')}",
                f"code: {source_order.get('code', '-')}",
            ]
        )
        answer = QMessageBox.question(self, "Manual Cancel", message, QMessageBox.Yes | QMessageBox.No)
        accepted = answer == QMessageBox.Yes
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "REJECTED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_cancel_pending_order",
            target_type="ORDER",
            target_id=source_order_id or None,
            target_name="Manual Cancel",
            order_id=source_order_id or None,
            broker_order_no=str(source_order.get("broker_order_no") or "").strip() or None,
            stock_code=str(source_order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_ORDER_CANCEL",
                "prompt_title": "Manual Cancel",
                "prompt_summary": "미체결 원주문의 취소 Queue 생성",
                "offered_options": ["예", "아니오"],
                "selected_option": "예" if accepted else "아니오",
                "order_action": "CANCEL",
            },
        )
        return accepted

    def confirm_manual_modify_pending_order(self, source_order: dict[str, object], preview: dict[str, object]) -> bool:
        request_preview = preview["order_queued_record_preview"]["execution_request"]["request_preview"]
        message = "\n".join(
            [
                "Manual pending order modify",
                "",
                "This creates an ORDER_QUEUED modify request and then uses the existing Manual SendOrder flow.",
                "The original open order is not changed until Kiwoom Chejan confirms it.",
                "",
                f"source_order_id: {source_order.get('order_id', source_order.get('id', '-'))}",
                f"broker_order_no: {source_order.get('broker_order_no', '-')}",
                f"remaining_quantity: {source_order.get('remaining_quantity', '-')}",
                f"modify_quantity: {request_preview.get('quantity', '-')}",
                f"modify_price: {request_preview.get('price', '-')}",
            ]
        )
        answer = QMessageBox.question(self, "Manual Modify", message, QMessageBox.Yes | QMessageBox.No)
        accepted = answer == QMessageBox.Yes
        source_order_id = str(source_order.get("order_id") or source_order.get("id") or "").strip()
        append_production_event(
            "OPERATOR_ORDER_DECISION",
            result="ACCEPTED" if accepted else "REJECTED",
            source="gui_auto_trade_setting_window.AutoTradeSettingWindow.confirm_manual_modify_pending_order",
            target_type="ORDER",
            target_id=source_order_id or None,
            target_name="Manual Modify",
            order_id=source_order_id or None,
            broker_order_no=str(source_order.get("broker_order_no") or "").strip() or None,
            stock_code=str(source_order.get("code") or "").strip() or None,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MANUAL_ORDER_MODIFY",
                "prompt_title": "Manual Modify",
                "prompt_summary": "미체결 원주문의 정정 Queue 생성",
                "offered_options": ["예", "아니오"],
                "selected_option": "예" if accepted else "아니오",
                "order_action": "MODIFY",
                "input_value": {
                    "quantity": request_preview.get("quantity"),
                    "price": request_preview.get("price"),
                },
            },
        )
        return accepted

    def cancel_pending_order_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "Manual Cancel"):
            return
        source_id, accepted = QInputDialog.getText(self, "Manual Cancel", "BROKER_ACCEPTED/PARTIALLY_FILLED order id:")
        if not accepted:
            return
        source_id = str(source_id or "").strip()
        if not source_id:
            self.statusBarMessage("Manual Cancel: source order id is required")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        data, orders, issues = self._queue_data_for_manual_order_action(queue_path)
        if issues:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "cancel_read_queue", "blocked_reasons": issues})
            return
        source_order = None
        for item in orders:
            record = item if isinstance(item, dict) else {}
            if str(record.get("id") or "").strip() == source_id or str(record.get("order_id") or "").strip() == source_id:
                source_order = deepcopy(record)
                break
        if not isinstance(source_order, dict):
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "cancel_source_order", "blocked_reasons": ["source order not found"]})
            return

        status = str(source_order.get("status") or "").strip().upper()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        try:
            remaining_quantity = int(source_order.get("remaining_quantity") or 0)
        except Exception:
            remaining_quantity = 0
        blocked: list[str] = []
        if status not in {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}:
            blocked.append("source order status is not cancelable")
        if not broker_order_no:
            blocked.append("source order broker_order_no is required")
        if remaining_quantity <= 0:
            blocked.append("source order remaining_quantity must be greater than 0")
        duplicate_reason = self._pending_cancel_duplicate_reason(orders, broker_order_no)
        if duplicate_reason:
            blocked.append(duplicate_reason)
        environment = self.build_manual_send_order_environment(source_order, queue_path)
        if environment.get("send_order_environment_ready") is not True:
            blocked.extend(list(environment.get("issues") or []))
        if blocked:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "cancel_source_order", "blocked_reasons": blocked})
            return

        preview = self._build_manual_cancel_order_queued_preview(source_order, queue_revision=snapshot.get("revision"))
        if not self.confirm_manual_cancel_pending_order(source_order, preview):
            self.statusBarMessage("Manual Cancel cancelled")
            return
        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "cancel_stale_queue_snapshot",
                    "blocked_reasons": ["queue file changed after cancel preview; retry from latest queue"],
                }
            )
            return
        commit_result = commit_execution_queue_write(
            preview,
            queue_path,
            context={"manual_queue_write_confirmed": True, "manual_pending_cancel_confirmed": True},
            expected_revision=current_snapshot.get("revision"),
        )
        if commit_result.get("committed") is not True or commit_result.get("post_write_verified") is not True:
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "cancel_queue_commit",
                    "blocked_reasons": list(commit_result.get("blocked_reasons") or ["cancel queue commit failed"]),
                    "cancel_queue_commit_result": commit_result,
                }
            )
            return

        cancel_record = preview["order_queued_record_preview"]
        self.send_order_for_order_queued_manually(str(cancel_record.get("id") or ""))

    def modify_pending_order_manually(self) -> None:
        if not startup_recovery_action_allowed(self, "Manual Modify"):
            return
        source_id, accepted = QInputDialog.getText(self, "Manual Modify", "BROKER_ACCEPTED/PARTIALLY_FILLED order id:")
        if not accepted:
            return
        source_id = str(source_id or "").strip()
        if not source_id:
            self.statusBarMessage("Manual Modify: source order id is required")
            return
        raw_details, accepted = QInputDialog.getText(self, "Manual Modify", "modify quantity,price:")
        if not accepted:
            return
        parts = [part.strip() for part in str(raw_details or "").split(",")]
        if len(parts) != 2:
            self.show_manual_send_order_result(
                {"status": "BLOCKED", "stage": "modify_input", "blocked_reasons": ["modify input must be quantity,price"]}
            )
            return
        try:
            modify_quantity = int(parts[0])
            modify_price = int(parts[1])
        except Exception:
            self.show_manual_send_order_result(
                {"status": "BLOCKED", "stage": "modify_input", "blocked_reasons": ["modify quantity and price must be integers"]}
            )
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        data, orders, issues = self._queue_data_for_manual_order_action(queue_path)
        if issues:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "modify_read_queue", "blocked_reasons": issues})
            return
        source_order = None
        for item in orders:
            record = item if isinstance(item, dict) else {}
            if str(record.get("id") or "").strip() == source_id or str(record.get("order_id") or "").strip() == source_id:
                source_order = deepcopy(record)
                break
        if not isinstance(source_order, dict):
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "modify_source_order", "blocked_reasons": ["source order not found"]})
            return

        status = str(source_order.get("status") or "").strip().upper()
        broker_order_no = str(source_order.get("broker_order_no") or "").strip()
        try:
            remaining_quantity = int(source_order.get("remaining_quantity") or 0)
        except Exception:
            remaining_quantity = 0
        blocked: list[str] = []
        if status not in {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}:
            blocked.append("source order status is not modifiable")
        if not broker_order_no:
            blocked.append("source order broker_order_no is required")
        if remaining_quantity <= 0:
            blocked.append("source order remaining_quantity must be greater than 0")
        if modify_quantity <= 0 or modify_quantity > remaining_quantity:
            blocked.append("modify quantity must be between 1 and remaining_quantity")
        if modify_price <= 0:
            blocked.append("modify price must be greater than 0")
        duplicate_reason = self._pending_modify_duplicate_reason(orders, broker_order_no)
        if duplicate_reason:
            blocked.append(duplicate_reason)
        environment = self.build_manual_send_order_environment(source_order, queue_path)
        if environment.get("send_order_environment_ready") is not True:
            blocked.extend(list(environment.get("issues") or []))
        if blocked:
            self.show_manual_send_order_result({"status": "BLOCKED", "stage": "modify_source_order", "blocked_reasons": blocked})
            return

        preview = self._build_manual_modify_order_queued_preview(
            source_order,
            queue_revision=snapshot.get("revision"),
            modify_quantity=modify_quantity,
            modify_price=modify_price,
        )
        if not self.confirm_manual_modify_pending_order(source_order, preview):
            self.statusBarMessage("Manual Modify cancelled")
            return
        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "modify_stale_queue_snapshot",
                    "blocked_reasons": ["queue file changed after modify preview; retry from latest queue"],
                }
            )
            return
        commit_result = commit_execution_queue_write(
            preview,
            queue_path,
            context={"manual_queue_write_confirmed": True, "manual_pending_modify_confirmed": True},
            expected_revision=current_snapshot.get("revision"),
        )
        if commit_result.get("committed") is not True or commit_result.get("post_write_verified") is not True:
            self.show_manual_send_order_result(
                {
                    "status": "BLOCKED",
                    "stage": "modify_queue_commit",
                    "blocked_reasons": list(commit_result.get("blocked_reasons") or ["modify queue commit failed"]),
                    "modify_queue_commit_result": commit_result,
                }
            )
            return

        modify_record = preview["order_queued_record_preview"]
        self.send_order_for_order_queued_manually(str(modify_record.get("id") or ""))

    def send_order_for_order_queued_manually(self, order_id_override: str | None = None) -> None:
        if not startup_recovery_action_allowed(self, "Manual SendOrder"):
            return
        if order_id_override is None:
            order_id, accepted = QInputDialog.getText(self, "Manual SendOrder", "ORDER_QUEUED record id:")
            if not accepted:
                return
            order_id = str(order_id or "").strip()
        else:
            order_id = str(order_id_override or "").strip()
        if not order_id:
            self.statusBarMessage("Manual SendOrder: ORDER_QUEUED record id is required")
            return

        queue_path = ORDER_QUEUE_PATH
        snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if read_result.get("ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "read_order",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": read_result.get("blocked_reasons", []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        order = read_result.get("order")
        order_dict = order if isinstance(order, dict) else {}
        if order_dict.get("status") != "ORDER_QUEUED":
            result = {
                "status": "BLOCKED",
                "stage": "order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["target record status is not ORDER_QUEUED"],
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        environment = self.build_manual_send_order_environment(order_dict, queue_path)
        if environment.get("send_order_environment_ready") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "send_order_environment",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(environment.get("issues") or []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        display_preview = self.build_manual_send_order_call_preview(order_dict, environment, operator_confirmed=False)
        adapter_contract_result = display_preview.get("adapter_contract_result")
        adapter_contract_dict = adapter_contract_result if isinstance(adapter_contract_result, dict) else {}
        if adapter_contract_dict.get("status") != "SEND_ORDER_CONTRACT_READY":
            result = {
                "status": "BLOCKED",
                "stage": "send_order_display_preview",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(adapter_contract_dict.get("issues") or ["send order adapter contract is not ready"]),
                "send_order_call_preview_result": display_preview,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        if not self.confirm_manual_send_order(order_dict, display_preview, queue_path, snapshot):
            self.statusBarMessage("Manual SendOrder cancelled")
            return

        current_snapshot = AutoTradeSettingWindow.queue_file_snapshot(queue_path)
        if snapshot.get("sha256") != current_snapshot.get("sha256"):
            result = {
                "status": "BLOCKED",
                "stage": "stale_queue_snapshot",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["queue file changed after SendOrder preview; retry from latest queue"],
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        latest_read_result = self.read_order_from_queue_by_id(order_id, queue_path)
        if latest_read_result.get("ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "latest_order_read",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": latest_read_result.get("blocked_reasons", []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return
        latest_order = latest_read_result.get("order")
        latest_order_dict = latest_order if isinstance(latest_order, dict) else {}
        if latest_order_dict.get("status") != "ORDER_QUEUED":
            result = {
                "status": "BLOCKED",
                "stage": "latest_order_status",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": ["latest target record status is not ORDER_QUEUED"],
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        latest_environment = self.build_manual_send_order_environment(latest_order_dict, queue_path)
        if latest_environment.get("send_order_environment_ready") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "send_order_environment_after_confirmation",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(latest_environment.get("issues") or []),
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        identity = self.send_order_identity_from_record(latest_order_dict)
        final_gate = self.build_manual_final_send_gate_result(
            latest_order_dict,
            latest_environment,
            queue_path,
            snapshot,
            current_snapshot,
        )
        if final_gate.get("final_send_gate_ok") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "final_send_gate",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(final_gate.get("blocked_reasons") or ["final send gate blocked"]),
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        call_preview = self.build_manual_send_order_call_preview(latest_order_dict, latest_environment, operator_confirmed=True)
        if call_preview.get("status") != "SEND_ORDER_CALL_READY":
            result = {
                "status": "BLOCKED",
                "stage": "send_order_call_preview",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(call_preview.get("issues") or ["send order call preview is not ready"]),
                "send_order_call_preview_result": call_preview,
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        claim_token = f"GUI_CLAIM_{uuid4().hex}"
        claim = claim_order_for_dispatch(
            queue_path,
            identity,
            final_gate,
            claim_token=claim_token,
            claim_owner="GUI_MANUAL_SEND_ORDER",
            claim_source="gui_manual_send_order",
            context={
                "dispatch_claim_owner": "GUI_MANUAL_SEND_ORDER",
                "dispatch_claim_source": "gui_manual_send_order",
                "dispatch_claim_ttl_sec": 60,
                "queue_path": str(queue_path),
                "queue_snapshot_hash": current_snapshot.get("sha256"),
            },
            expected_revision=current_snapshot.get("revision"),
        )
        if claim.get("claimed") is not True or claim.get("post_write_verified") is not True:
            result = {
                "status": "BLOCKED",
                "stage": "dispatch_claim",
                "order_id": order_id,
                "callable_executed": False,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "blocked_reasons": list(claim.get("blocked_reasons") or ["dispatch claim failed"]),
                "dispatch_claim_result": claim,
                "final_send_gate_result": final_gate,
            }
            self.show_manual_send_order_result(result)
            self.statusBarMessage("Manual SendOrder blocked")
            return

        result = execute_claimed_send_order(
            queue_path,
            identity,
            str(claim.get("dispatch_claim_id") or ""),
            claim_token,
            "GUI_MANUAL_SEND_ORDER",
            claim.get("revision_after"),
            latest_environment.get("send_order_callable"),
            call_preview.get("send_order_args"),
            context={
                "send_order_attempt_owner": "GUI_MANUAL_SEND_ORDER",
                "send_order_attempt_source": "gui_manual_send_order",
            },
        )
        result["order_id"] = order_id
        result["dispatch_claim_result"] = claim
        result["final_send_gate_result"] = final_gate
        result["send_order_call_preview_result"] = call_preview
        self.show_manual_send_order_result(result)
        status_text = "completed" if result.get("queue_result_recorded") else "blocked"
        self.statusBarMessage(f"Manual SendOrder {status_text}")

    def auto_trade_runtime_state_for_order(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).auto_trade_runtime_state_for_order(*args, **kwargs)

    def auto_trade_execution_block_reasons(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).auto_trade_execution_block_reasons(*args, **kwargs)

    def order_with_execution_request_defaults(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).order_with_execution_request_defaults(*args, **kwargs)

    def send_order_for_order_queued_automatically(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).send_order_for_order_queued_automatically(*args, **kwargs)

    def process_executable_order_for_auto_trade(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).process_executable_order_for_auto_trade(*args, **kwargs)

    def auto_process_executable_orders_for_real_trade(self, *args, **kwargs):
        return AutoTradeSettingWindow.order_execution_boundary(self).auto_process_executable_orders_for_real_trade(*args, **kwargs)

    def handle_raw_chejan_event(
        self,
        raw_event: dict[str, object],
        live_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return handle_kiwoom_raw_chejan_event(raw_event, live_context)

    def int_state_value(self, state: dict[str, object], key: str) -> int:
        try:
            return int(state.get(key, 0) or 0)
        except Exception:
            return 0

    def pre_start_review_check(self, routine_name: str, stock_dir: Path, code: str, name: str) -> dict[str, object]:
        """
        자동매매 시작 전 사전점검.

        프로그램이 먼저 점검하고, 문제 없는 종목만 RUNNING으로 전환한다.
        문제 소지가 있는 종목은 REVIEW_REQUIRED로 전환한 뒤 검토관리창에서 HTS 검토 후 처리한다.
        """
        item = build_review_required_item(routine_name, stock_dir, code, name)
        state = read_json_dict(stock_dir / "state.json")
        data_reasons = auto_trade_setting_data_inconsistency_reasons(state)
        if data_reasons:
            return build_review_required_item(routine_name, stock_dir, code, name, data_reasons)

        return item

    def mark_review_required(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        item: dict[str, object],
        source: str = "",
    ) -> bool:
        reasons = unique_review_reasons(list(item.get("review_reasons", [])))
        state_before = read_json_dict(stock_dir / "state.json")
        if not isinstance(state_before, dict):
            state_before = {}
        before_status = str(state_before.get("status", "STOPPED")).strip().upper() or "STOPPED"
        data_reasons = auto_trade_setting_data_inconsistency_reasons(state_before)
        operation_active_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}
        operation_data_issue = bool(data_reasons) and before_status in operation_active_statuses
        operator_reasons = unique_review_reasons(
            [operator_review_reason(reason) for reason in reasons]
        )
        reason_text = (
            "운영 데이터 불일치"
            if operation_data_issue
            else (" / ".join(operator_reasons) or "수동 검토 필요")
        )
        review_location = str(
            source
            or item.get("review_location", "")
            or item.get("review_source", "")
            or item.get("detected_by", "")
            or "-"
        ).strip() or "-"
        review_location = operator_review_location(review_location)

        metadata = {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": review_location,
            "review_reason": reason_text,
            "review_checked_at": now_text(),
            "last_checked_price": safe_float_value(item.get("current_price"), 0.0),
            "last_checked_pnl_rate": str(item.get("pnl_rate_text", "-")),
        }
        raw_reason_text = " / ".join(reasons)
        if raw_reason_text and raw_reason_text != reason_text:
            metadata["review_detail"] = raw_reason_text
        if operation_data_issue:
            stopped_at = now_text()
            emergency_metadata = {
                "emergency_stopped_at": stopped_at,
                "emergency_reason": "운영 데이터 불일치",
                "trade_enabled": False,
                "operation_notice": "",
                "operation_notice_reason": "",
                "operation_notice_at": "",
                "early_close_requested_at": "",
                "early_close_source": "",
                "early_close_method": "",
                "early_close_policy": {},
                "auto_close_method": "",
                "auto_close_policy": {},
                "liquidation_policy_forced": False,
                "liquidation_policy_reason": "",
            }
            if not self.update_stock_status(
                stock_dir,
                code,
                name,
                "EMERGENCY_STOPPED",
                emergency_metadata,
                "운영 데이터 불일치",
            ):
                return False
            metadata.update(emergency_metadata)
            metadata["review_detail"] = " / ".join(data_reasons)
        resume_metadata = item.get("resume_metadata")
        if isinstance(resume_metadata, dict):
            metadata.update(resume_metadata)

        return self.update_stock_status(stock_dir, code, name, "REVIEW_REQUIRED", metadata, reason_text)

    def update_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state: dict[str, object] | None = None,
        log_suffix: str = "",
    ) -> bool:
        return auto_trade_update_stock_status(
            self,
            stock_dir,
            code,
            name,
            new_status,
            extra_state,
            log_suffix,
        )

    def operation_policy_protected_status(self, status: object) -> bool:
        return auto_trade_operation_policy_protected_status(self, status)

    def recalculate_stock_status_by_operation_policy(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        reason: str,
        extra_state: dict[str, object] | None = None,
        silent_unchanged: bool = False,
    ) -> tuple[str, str, str]:
        return auto_trade_recalculate_stock_status_by_operation_policy(
            self,
            stock_dir,
            code,
            name,
            reason,
            extra_state,
            silent_unchanged,
        )
    def recalculate_all_status_by_operation_policy(
        self,
        reason: str,
        silent_unchanged: bool = False,
        write_changelog_when_unchanged: bool = True,
    ) -> dict[str, int]:
        return auto_trade_recalculate_all_status_by_operation_policy(
            self,
            reason,
            silent_unchanged,
            write_changelog_when_unchanged,
        )
    def update_stock_operation_mode(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        operation_mode: str,
        config_updates: dict[str, object] | None = None,
    ) -> bool:
        return auto_trade_update_stock_operation_mode(
            self,
            stock_dir,
            code,
            name,
            operation_mode,
            config_updates,
        )

    def unregister_selected_auto_trade_stocks(self) -> None:
        unregister_selected_auto_trade_stocks(self)

    def statusBar_message(self, message: str, timeout_ms: int = 7000) -> None:
        self.statusBarMessage(message, timeout_ms)


    def open_operation_environment_settings(self) -> None:
        """스케줄매매관리 대체: 운영환경설정 창을 연다."""
        existing = getattr(self, "operation_environment_settings_window", None)
        if existing is not None and not sip.isdeleted(existing) and existing.isVisible():
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        dialog = OperationEnvironmentSettingsDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self.operation_environment_settings_window = dialog

        def settings_saved() -> None:
            self.statusBarMessage("환경설정 저장 완료")
            self.refresh_all()

        dialog.accepted.connect(settings_saved)
        dialog.destroyed.connect(
            lambda _obj=None, target=dialog: (
                setattr(self, "operation_environment_settings_window", None)
                if getattr(self, "operation_environment_settings_window", None) is target
                else None
            )
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_stock_register_window(self) -> None:
        """관제창과 동일한 중앙 종목 등록 창을 연다."""
        owner = persistent_feature_owner(self)
        owner_opener = getattr(owner, "open_stock_register_window", None)
        if callable(owner_opener):
            owner_opener()
            self.stock_register_window = getattr(owner, "stock_register_window", None)
            return
        from gui_stock_register_window import StockRegisterWindow

        self.stock_register_window = StockRegisterWindow(self)
        self.stock_register_window.setAttribute(Qt.WA_DeleteOnClose, True)
        self.stock_register_window.finished.connect(
            lambda _result: self.refresh_all()
        )
        self.stock_register_window.show()

    def open_selected_stock_policy_settings(self) -> None:
        """종목 우클릭용 개별종목 설정 창."""
        selected = self.selected_stock_info()
        if selected is None:
            QMessageBox.warning(self, "선택 오류", "개별종목 설정은 종목 1개를 선택한 상태에서 사용할 수 있습니다.")
            return
        stock_dir, code, name = selected
        dialog = StockPolicyOverrideDialog(stock_dir, code, name, self)
        if dialog.exec_() == QDialog.Accepted:
            self.refresh_all()

    def set_global_schedule_time(self) -> None:
        """하위 호환용: 스케줄매매관리 창을 연다."""
        self.open_schedule_trade_management_window()

    def open_schedule_trade_management_window(self) -> None:
        dialog = ScheduleTradeManagementDialog(self)
        dialog.exec_()
        self.refresh_all()

    def set_selected_individual_schedule_time(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "시간을 변경할 종목을 1개 이상 선택하세요.")
            return

        first_config = read_json_dict(selected[0][0] / "config.json")
        if not first_config:
            first_config = default_config()
        start_time, end_buy_time, _ = effective_schedule_times(first_config)

        dialog = ScheduleOperationDialog(self, start_time, end_buy_time, len(selected))
        dialog.setWindowTitle("종목 시간 예외 설정")
        if dialog.exec_() != QDialog.Accepted:
            return

        result = auto_trade_apply_schedule_times_to_targets(
            self,
            selected,
            dialog.start_time(),
            dialog.end_buy_time(),
        )
        auto_trade_finalize_operation_mode_result(self, result)

    def reset_selected_schedule_to_global(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "기본 시간으로 리셋할 종목을 1개 이상 선택하세요.")
            return

        result = auto_trade_reset_schedule_times_for_targets(
            self,
            selected,
        )
        auto_trade_finalize_operation_mode_result(self, result)

    def set_selected_schedule_operation_mode(self) -> None:
        auto_trade_set_selected_schedule_operation_mode(self)

    def set_selected_operation_mode(
        self,
        operation_mode: str,
        config_updates: dict[str, object] | None = None,
    ) -> None:
        auto_trade_set_selected_operation_mode(self, operation_mode, config_updates)

    def split_start_targets(
        self,
        selected: list[tuple[Path, str, str]],
    ) -> tuple[list[tuple[Path, str, str]], list[str]]:
        """
        운영시작 대상과 제외 대상을 분리한다.

        정책:
        - STOPPED: 운영 전/마감 후 상태이므로 운영시작 가능
        - MONITORING/WATCHING/WATCH/WATCH_BUY: 화면상 감시/대기지만 주문 비활성 상태이므로
          운영시작 버튼으로 현재 시간/운영방식에 맞게 재판정 가능
        - RUNNING/SELL_ONLY/REVIEW_REQUIRED/EMERGENCY 계열은 보호 상태로 제외
        """
        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []

        for stock_dir, code, name in selected:
            if self.start_target_is_review_isolated(stock_dir, code):
                skipped.append(f"{code} {name}(검토종목)")
                continue
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if auto_trade_setting_start_target_allowed(self, state, code):
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")

        return targets, skipped

    def show_auto_trade_result_dialog(self, title: str, heading: str, lines: list[str]) -> None:
        """복수 종목 처리 결과를 표시하는 공용 창."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(420, 320)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        title_label = QLabel(heading)
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText("\n".join(lines))
        body.setMinimumHeight(180)
        body.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(body)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("확인")
        ok_button.setMinimumWidth(80)
        ok_button.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        dialog.exec_()

    def start_selected_auto_trades(self) -> None:
        if _today_global_operation_status(read_operation_state()) == "NORMAL_ENDED":
            status_message = getattr(self, "statusBarMessage", None)
            if callable(status_message):
                status_message("오늘 운영이 종료되었습니다.")
            self.update_global_operation_button_state()
            return
        running_targets = self.running_registered_operation_targets()
        if running_targets:
            self.update_global_operation_button_state()
            return
        selected_targets = self.registered_operation_start_targets()
        if not selected_targets:
            status_message = getattr(self, "statusBarMessage", None)
            if callable(status_message):
                status_message("운영시작 대상이 없습니다. 운영 제외를 해제한 뒤 다시 시도하세요.")
            self.update_global_operation_button_state()
            return
        auto_trade_start_selected_auto_trades(
            self,
            request_scope="multiple",
            selected_targets=selected_targets,
            source="auto_trade_global_start_button",
        )
        parent_refreshed = False
        parent_getter = getattr(self, "parent", None)
        parent = parent_getter() if callable(parent_getter) else None
        parent_refresh = getattr(parent, "refresh_all", None)
        if callable(parent_refresh):
            parent_refresh()
            parent_refreshed = True
        if not parent_refreshed:
            self.update_global_operation_button_state()

    def recalculate_routine_limits_for_new_operation_session(self) -> dict[str, object]:
        from routine_limit_recalculation import (
            recalculate_enabled_routine_limits_for_new_session,
        )

        return recalculate_enabled_routine_limits_for_new_session(self)

    def start_selected_rows_auto_trades(self) -> dict[str, object] | None:
        return auto_trade_start_selected_rows_auto_trades(self)

    def emergency_stop_selected_auto_trade_stocks(self) -> dict[str, object]:
        selected_targets = self.selected_stock_infos()
        if not selected_targets:
            return {
                "changed": (),
                "skipped": (),
                "changed_count": 0,
                "skipped_count": 0,
            }
        from gui_main_emergency_ops import execute_selected_emergency_stop

        return execute_selected_emergency_stop(self, selected_targets)

    def apply_selected_early_close_default(self, checked: bool = False) -> None:
        # QPushButton.clicked may pass a checked(bool) argument.
        # The default early-close method is read inside auto_trade_apply_selected_early_close_default().
        auto_trade_apply_selected_early_close_default(self)

    def apply_selected_early_close_profit_loss(self) -> None:
        auto_trade_apply_selected_early_close_profit_loss(self)

    def cancel_selected_early_close(self) -> None:
        auto_trade_cancel_selected_early_close(self)

    def apply_selected_early_close(
        self,
        method: str,
        profit_percent: str = "",
        loss_percent: str = "",
        source: str = "우클릭",
        extra_policy: dict[str, object] | None = None,
    ) -> None:
        if extra_policy is None and (str(profit_percent).strip() or str(loss_percent).strip()):
            extra_policy = {
                "profit_percent": str(profit_percent).strip(),
                "loss_percent": str(loss_percent).strip(),
            }
        auto_trade_apply_selected_early_close(
            self,
            method,
            source=source,
            extra_policy=extra_policy,
        )
    def open_review_required_window(self, _checked: object | None = None) -> None:
        auto_trade_open_review_required_window(self)

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        """부모 창 상태바에 메시지를 전달한다.

        분리 모듈에서는 MainWindow를 직접 참조하지 않는다.
        """
        parent = persistent_feature_owner(self)
        status_bar_getter = getattr(parent, "statusBar", None)
        if callable(status_bar_getter):
            try:
                status_bar_getter().showMessage(message, timeout_ms)
            except Exception:
                pass

    def showAutoTradePopupMessage(self, message: str, timeout_ms: int = 2500) -> None:
        popup = getattr(self, "_notification_popup", None)
        if popup is None:
            popup = AutoTradeNotificationPopup(self)
            self._notification_popup = popup
        popup.show_message(message, timeout_ms)

    def open_stock_performance_window(self) -> None:
        open_stock_performance_prototype(self)

def base_stock_routine_assignments() -> dict[tuple[str, str], set[str]]:
    """
    기초종목.txt 기준 종목-루틴 연결 정보를 반환한다.

    자동매매설정 창은 루틴 폴더에 남은 종목 폴더만으로 종목을 표시하지 않고,
    기초종목.txt 에 실제 연결된 종목만 표시한다.
    """
    result: dict[tuple[str, str], set[str]] = {}
    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        routines = stock.get("routines", [])
        if not code or not name:
            continue
        if isinstance(routines, list):
            routine_set = {str(routine).strip() for routine in routines if str(routine).strip()}
        else:
            routine_text = str(routines).strip()
            routine_set = {routine_text} if routine_text else set()
        result[(code, name)] = routine_set
    return result


def is_stock_assigned_to_routine(code: str, name: str, routine_name: str) -> bool:
    """
    기초종목.txt 기준으로 종목이 해당 루틴에 연결되어 있는지 확인한다.
    """
    assignments = base_stock_routine_assignments()
    return routine_name in assignments.get((code, name), set())


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """
    자동매매설정 표시용 루틴 종목 폴더 목록을 반환한다.

    루틴 폴더 안에 물리 폴더가 남아 있어도 기초종목.txt 에 연결 정보가 없으면
    자동매매설정 창에는 표시하지 않는다.
    """
    result: list[Path] = []
    for stock_dir in get_stock_dirs_in_routine(routine_dir):
        if is_review_required_stock_dir(stock_dir):
            continue
        result.append(stock_dir)
    return result
