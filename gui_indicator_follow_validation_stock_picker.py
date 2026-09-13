# -*- coding: utf-8 -*-
"""Operator-friendly, selection-only stock browser for Validation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
    load_stock_library_snapshot,
)
from gui_stock_library_browser import (
    AFTER_MARKET_COLUMN,
    CHANGE_RATE_COLUMN,
    CODE_COLUMN,
    CURRENT_PRICE_COLUMN,
    EXECUTION_STRENGTH_COLUMN,
    INSTRUMENT_CLASSIFICATION_COLUMN,
    MARKET_CAP_COLUMN,
    MARKET_COLUMN,
    NAME_COLUMN,
    NUMERIC_SNAPSHOT_COLUMNS,
    PREVIOUS_DAY_VOLUME_RATE_COLUMN,
    RANKING_BADGES,
    RANKING_HIGHLIGHT_COLUMNS,
    REGISTRATION_STATUS_COLUMN,
    STOCK_BROWSER_HEADERS,
    STOCK_STATUS_COLUMN,
    TRADING_VALUE_COLUMN,
    VOLUME_COLUMN,
    filter_stock_library_records,
    market_snapshot_display_values,
    normalize_browser_code,
    snapshot_number,
    stock_browser_display_values,
    stock_status_full_text,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef


class _NumericStockBrowserItem(QTableWidgetItem):
    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = snapshot_number(self.data(Qt.UserRole))
        right = snapshot_number(other.data(Qt.UserRole))
        if left is None:
            return right is None and self.text() < other.text()
        if right is None:
            return True
        return left < right


class IndicatorFollowValidationStockPicker(QDialog):
    """Choose one stock without exposing any registration mutation surface."""

    CODE_COLUMN = CODE_COLUMN
    NAME_COLUMN = NAME_COLUMN
    MARKET_COLUMN = MARKET_COLUMN
    REGISTRATION_STATUS_COLUMN = REGISTRATION_STATUS_COLUMN
    INSTRUMENT_CLASSIFICATION_COLUMN = INSTRUMENT_CLASSIFICATION_COLUMN
    AFTER_MARKET_COLUMN = AFTER_MARKET_COLUMN
    CURRENT_PRICE_COLUMN = CURRENT_PRICE_COLUMN
    CHANGE_RATE_COLUMN = CHANGE_RATE_COLUMN
    EXECUTION_STRENGTH_COLUMN = EXECUTION_STRENGTH_COLUMN
    PREVIOUS_DAY_VOLUME_RATE_COLUMN = PREVIOUS_DAY_VOLUME_RATE_COLUMN
    TRADING_VALUE_COLUMN = TRADING_VALUE_COLUMN
    VOLUME_COLUMN = VOLUME_COLUMN
    MARKET_CAP_COLUMN = MARKET_CAP_COLUMN
    STOCK_STATUS_COLUMN = STOCK_STATUS_COLUMN
    NUMERIC_SNAPSHOT_COLUMNS = NUMERIC_SNAPSHOT_COLUMNS
    RANKING_BADGES = RANKING_BADGES
    RANKING_HIGHLIGHT_COLUMNS = RANKING_HIGHLIGHT_COLUMNS

    IDENTITY_ROLE = Qt.UserRole + 1
    RANKING_ACTIVE_COLOR = "#2563EB"
    RANKING_INACTIVE_COLOR = "#4B5563"
    RANKING_HIGHLIGHT_COLOR = "#EFF6FF"

    def __init__(
        self,
        parent=None,
        *,
        project_root: str | Path | None = None,
        snapshot_loader: Callable[[Path | None], StockLibraryLoadSnapshot] | None = None,
        market_snapshot_api: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("검증 종목 선택")
        self.resize(1180, 520)

        self._selection: ValidationStockRef | None = None
        self._records: tuple[dict[str, object], ...] = ()
        self._snapshot_valid = False
        self._search_generation = 0
        self._result_source = "SEARCH"
        self._active_ranking_source = ""
        self._result_sort_column = -1
        self._result_sort_order = Qt.AscendingOrder
        self._visible_stock_codes: set[str] = set()
        self._market_snapshot_api = (
            market_snapshot_api
            if market_snapshot_api is not None
            else getattr(parent, "kiwoom_api", None)
        )

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("종목코드 / 종목명 / 시장 / 초성 검색")
        self.btn_search = QPushButton("검색", self)
        self.btn_search.setAutoDefault(False)
        self.btn_search.setDefault(False)
        self.general_stock_button = QPushButton("일반종목", self)
        self.general_stock_button.setCheckable(True)
        self.general_stock_button.setChecked(False)
        self.ranking_separator_label = QLabel("|", self)
        self.ranking_title_label = QLabel("TOP100 :", self)
        self.ranking_buttons: dict[str, QPushButton] = {}
        for source, text in self.RANKING_BADGES:
            button = QPushButton(text, self)
            button.setAutoDefault(False)
            button.setDefault(False)
            self.ranking_buttons[source] = button

        self.result_table = QTableWidget(0, len(STOCK_BROWSER_HEADERS), self)
        self.stock_table = self.result_table
        self.result_table.setHorizontalHeaderLabels(STOCK_BROWSER_HEADERS)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.setSortingEnabled(False)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        header.setSectionsMovable(False)
        self.result_table.verticalHeader().setDefaultSectionSize(24)

        self.status_label = QLabel(self)
        self.select_button = QPushButton("선택", self)
        self.cancel_button = QPushButton("취소", self)
        self.select_button.setEnabled(False)
        for button in (self.select_button, self.cancel_button):
            button.setAutoDefault(False)
            button.setDefault(False)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("검색어", self))
        search_row.addWidget(self.search_input)
        search_row.addWidget(self.btn_search)
        search_row.addStretch(1)
        search_row.addWidget(self.general_stock_button)
        search_row.addSpacing(6)
        search_row.addWidget(self.ranking_separator_label)
        search_row.addSpacing(6)
        search_row.addWidget(self.ranking_title_label)
        for source, _text in self.RANKING_BADGES:
            search_row.addWidget(self.ranking_buttons[source])

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.select_button)
        button_row.addWidget(self.cancel_button)

        root = QVBoxLayout(self)
        root.addLayout(search_row)
        root.addWidget(self.result_table)
        root.addWidget(self.status_label)
        root.addLayout(button_row)

        self.search_input.textChanged.connect(self._apply_filter)
        self.search_input.returnPressed.connect(self.search_stocks)
        self.btn_search.clicked.connect(self.search_stocks)
        self.general_stock_button.toggled.connect(
            lambda _checked: self._apply_general_stock_visibility()
        )
        for source, button in self.ranking_buttons.items():
            button.clicked.connect(
                lambda _checked=False, value=source: self.request_stock_ranking(value)
            )
        self.result_table.itemSelectionChanged.connect(
            self._update_select_button_state
        )
        self.result_table.itemDoubleClicked.connect(self._accept_double_clicked)
        self.result_table.horizontalHeader().sectionClicked.connect(
            self.on_result_header_clicked
        )
        self.select_button.clicked.connect(self._accept_selection)
        self.cancel_button.clicked.connect(self.reject)

        loader = snapshot_loader or load_stock_library_snapshot
        root_path = None if project_root is None else Path(project_root)
        try:
            snapshot = loader(root_path)
        except Exception:
            snapshot = None
        self._load_snapshot(snapshot)

    @property
    def selected_stock(self) -> ValidationStockRef | None:
        return self._selection

    def reject(self) -> None:
        self._selection = None
        super().reject()

    def _load_snapshot(self, snapshot: object) -> None:
        valid = (
            isinstance(snapshot, StockLibraryLoadSnapshot)
            and snapshot.state == STOCK_LIBRARY_READY
            and snapshot.source == STOCK_LIBRARY_RUNTIME_SOURCE
            and bool(snapshot.records)
        )
        if not valid:
            self._snapshot_valid = False
            self._records = ()
            self.status_label.setText("종목 라이브러리를 사용할 수 없습니다.")
            self._render_records(())
            return

        records: list[dict[str, object]] = []
        for item in snapshot.records:
            record = dict(item)
            code = normalize_browser_code(record.get("code"))
            name = str(record.get("name", "") or "").strip()
            if not code or not name:
                self._snapshot_valid = False
                self._records = ()
                self.status_label.setText("종목 라이브러리를 사용할 수 없습니다.")
                self._render_records(())
                return
            record["code"] = code
            record["name"] = name
            records.append(record)

        self._snapshot_valid = True
        self._records = tuple(records)
        self.status_label.setText("")
        self._render_records(self._records)

    def _apply_filter(self, query: object = "") -> None:
        if not self._snapshot_valid:
            return
        self._result_source = "SEARCH"
        self._active_ranking_source = ""
        records = filter_stock_library_records(
            self._records,
            query,
            include_all_when_empty=True,
        )
        self._render_records(tuple(records), source="SEARCH")

    def search_stocks(self, *_args) -> None:
        self._search_generation += 1
        self._apply_filter(self.search_input.text())
        self._request_market_snapshot(self._search_generation)

    def request_stock_ranking(self, source: str) -> None:
        clean_source = str(source or "").strip().upper()
        if clean_source not in {item[0] for item in self.RANKING_BADGES}:
            return
        self._search_generation += 1
        generation = self._search_generation
        self._result_source = clean_source
        self._active_ranking_source = ""
        self._render_records(())
        request = getattr(
            self._market_snapshot_api,
            "request_stock_ranking_snapshot",
            None,
        )
        if not callable(request):
            self.status_label.setText("TOP100 정보를 사용할 수 없습니다.")
            return
        try:
            request(
                clean_source,
                callback=lambda payload, value=generation, expected=clean_source: (
                    self._on_stock_ranking_result(value, expected, payload)
                ),
            )
        except Exception:
            self.status_label.setText("TOP100 정보를 사용할 수 없습니다.")

    def _on_stock_ranking_result(
        self,
        generation: int,
        expected_source: str,
        result: object,
    ) -> None:
        if generation != self._search_generation or expected_source != self._result_source:
            return
        payload = dict(result) if isinstance(result, dict) else {}
        rows = payload.get("rows")
        if payload.get("ok") is not True or not isinstance(rows, list):
            self.status_label.setText("TOP100 정보를 사용할 수 없습니다.")
            return

        library_by_code = {
            normalize_browser_code(item.get("code")): dict(item)
            for item in self._records
        }
        records: list[dict[str, object]] = []
        seen_codes: set[str] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            code = normalize_browser_code(raw.get("stock_code"))
            if not code or code in seen_codes:
                continue
            record = dict(library_by_code.get(code, {}))
            name = str(raw.get("stock_name") or record.get("name") or "").strip()
            if not name:
                continue
            record.update(code=code, name=name, ranking_snapshot=dict(raw))
            records.append(record)
            seen_codes.add(code)
            if len(records) >= 100:
                break

        self.status_label.setText("")
        self._active_ranking_source = expected_source
        self._render_records(tuple(records), source=expected_source)
        self._request_market_snapshot(generation)

    def _request_market_snapshot(self, generation: int) -> None:
        if not self._visible_stock_codes:
            return
        request = getattr(
            self._market_snapshot_api,
            "request_initial_market_snapshot",
            None,
        )
        if not callable(request):
            return
        try:
            request(
                tuple(sorted(self._visible_stock_codes)),
                callback=lambda payload, value=generation: (
                    self._on_market_snapshot_result(value, payload)
                ),
            )
        except Exception:
            return

    def _on_market_snapshot_result(self, generation: int, result: object) -> None:
        if generation != self._search_generation:
            return
        payload = dict(result) if isinstance(result, dict) else {}
        rows = payload.get("rows")
        if payload.get("ok") is not True or not isinstance(rows, list):
            return
        for snapshot in rows:
            if not isinstance(snapshot, dict):
                continue
            row = self._find_row(normalize_browser_code(snapshot.get("stock_code")))
            if row >= 0:
                self._apply_market_snapshot_to_row(row, snapshot)
        self._apply_result_sort()

    def _render_records(
        self,
        records: tuple[dict[str, object], ...],
        *,
        source: str | None = None,
    ) -> None:
        self.result_table.setSortingEnabled(False)
        self.result_table.clearSelection()
        self.result_table.setRowCount(len(records))
        self._visible_stock_codes = set()
        for row, record in enumerate(records):
            status = stock_status_full_text(record.get("status"))
            values = stock_browser_display_values(
                record,
                registration_status=record.get("registration_status", "-"),
            )
            for column, value in enumerate(values):
                item = (
                    _NumericStockBrowserItem(str(value))
                    if column in self.NUMERIC_SNAPSHOT_COLUMNS
                    else QTableWidgetItem(str(value))
                )
                item.setTextAlignment(
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column == self.NAME_COLUMN
                    else Qt.AlignCenter
                )
                item.setData(Qt.UserRole, value)
                if column == self.CODE_COLUMN:
                    item.setData(
                        self.IDENTITY_ROLE,
                        (
                            normalize_browser_code(record.get("code")),
                            str(record.get("name", "") or "").strip(),
                        ),
                    )
                if column == self.STOCK_STATUS_COLUMN and status != "-":
                    item.setToolTip(status)
                self.result_table.setItem(row, column, item)
            code = normalize_browser_code(record.get("code"))
            if code:
                self._visible_stock_codes.add(code)
            ranking_snapshot = record.get("ranking_snapshot")
            if isinstance(ranking_snapshot, dict):
                self._apply_market_snapshot_to_row(row, ranking_snapshot)
        self._apply_result_sort()
        self._apply_general_stock_visibility()
        self._update_ranking_styles()
        self._update_select_button_state()

    def _apply_market_snapshot_to_row(
        self,
        row: int,
        snapshot: dict[str, object],
    ) -> None:
        for column, (sort_value, text) in market_snapshot_display_values(snapshot).items():
            item = self.result_table.item(row, column)
            if item is None:
                item = _NumericStockBrowserItem()
                self.result_table.setItem(row, column, item)
            item.setText(text)
            item.setData(Qt.UserRole, sort_value)
            item.setTextAlignment(Qt.AlignCenter)

    def _apply_general_stock_visibility(self) -> None:
        general_only = self.general_stock_button.isChecked()
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, self.INSTRUMENT_CLASSIFICATION_COLUMN)
            hidden = general_only and (item is None or item.text() != "일반")
            self.result_table.setRowHidden(row, hidden)
            if hidden and self.result_table.currentRow() == row:
                self.result_table.clearSelection()
        self._update_select_button_state()

    def _update_ranking_styles(self) -> None:
        for source, button in self.ranking_buttons.items():
            color = (
                self.RANKING_ACTIVE_COLOR
                if source == self._active_ranking_source
                else self.RANKING_INACTIVE_COLOR
            )
            button.setStyleSheet(
                f"QPushButton {{ color: {color}; border: 1px solid {color}; }}"
            )
        target_column = self.RANKING_HIGHLIGHT_COLUMNS.get(self._active_ranking_source)
        for column in set(self.RANKING_HIGHLIGHT_COLUMNS.values()):
            for row in range(self.result_table.rowCount()):
                item = self.result_table.item(row, column)
                if item is not None:
                    item.setBackground(
                        QBrush(QColor(self.RANKING_HIGHLIGHT_COLOR))
                        if column == target_column
                        else QBrush()
                    )

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
        self.result_table.sortItems(
            self._result_sort_column,
            self._result_sort_order,
        )
        self._apply_general_stock_visibility()

    def _find_row(self, code: str) -> int:
        for row in range(self.result_table.rowCount()):
            item = self.result_table.item(row, self.CODE_COLUMN)
            if item is not None and normalize_browser_code(item.text()) == code:
                return row
        return -1

    def _update_select_button_state(self) -> None:
        row = self.result_table.currentRow()
        self.select_button.setEnabled(
            self._snapshot_valid
            and row >= 0
            and not self.result_table.isRowHidden(row)
        )

    def _accept_double_clicked(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        self.result_table.setCurrentCell(item.row(), self.CODE_COLUMN)
        self._accept_selection()

    def _accept_selection(self, *_args) -> None:
        if not self._snapshot_valid:
            return
        row = self.result_table.currentRow()
        if row < 0 or self.result_table.isRowHidden(row):
            return
        code_item = self.result_table.item(row, self.CODE_COLUMN)
        identity = code_item.data(self.IDENTITY_ROLE) if code_item is not None else None
        if not isinstance(identity, tuple) or len(identity) != 2:
            return
        self._selection = ValidationStockRef(identity[0], identity[1])
        self.accept()
