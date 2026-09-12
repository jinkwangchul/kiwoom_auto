# -*- coding: utf-8 -*-
"""Selection-only stock picker for indicator-follow Validation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
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
from routines.지표추종매매.routine_validation_contract import ValidationStockRef


class IndicatorFollowValidationStockPicker(QDialog):
    """Choose one stock from an already-synced, read-only local snapshot."""

    def __init__(
        self,
        parent=None,
        *,
        project_root: str | Path | None = None,
        snapshot_loader: Callable[[Path | None], StockLibraryLoadSnapshot] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("검증 종목 선택")
        self.resize(480, 420)

        self._selection: ValidationStockRef | None = None
        self._records: tuple[dict[str, object], ...] = ()
        self._snapshot_valid = False

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("종목코드 또는 종목명 검색")
        self.stock_table = QTableWidget(0, 2, self)
        self.stock_table.setHorizontalHeaderLabels(("종목코드", "종목명"))
        self.stock_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.stock_table.setSelectionMode(QTableWidget.SingleSelection)
        self.stock_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.stock_table.horizontalHeader().setStretchLastSection(True)
        self.status_label = QLabel(self)
        self.select_button = QPushButton("선택", self)
        self.cancel_button = QPushButton("취소", self)
        self.select_button.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.select_button)
        button_row.addWidget(self.cancel_button)

        root = QVBoxLayout(self)
        root.addWidget(self.search_input)
        root.addWidget(self.stock_table)
        root.addWidget(self.status_label)
        root.addLayout(button_row)

        self.search_input.textChanged.connect(self._apply_filter)
        self.stock_table.itemSelectionChanged.connect(
            self._update_select_button_state
        )
        self.stock_table.itemDoubleClicked.connect(self._accept_selection)
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
            code = str(item.get("code", "") or "").strip()
            name = str(item.get("name", "") or "").strip()
            if not code or not name:
                self._snapshot_valid = False
                self._records = ()
                self.status_label.setText("종목 라이브러리를 사용할 수 없습니다.")
                self._render_records(())
                return
            records.append({"code": code, "name": name})

        self._snapshot_valid = True
        self._records = tuple(records)
        self.status_label.setText("")
        self._render_records(self._records)

    def _apply_filter(self, query: str) -> None:
        needle = str(query or "").strip().casefold()
        records = tuple(
            item
            for item in self._records
            if not needle
            or needle in str(item["code"]).casefold()
            or needle in str(item["name"]).casefold()
        )
        self._render_records(records)

    def _render_records(self, records: tuple[dict[str, object], ...]) -> None:
        self.stock_table.clearSelection()
        self.stock_table.setRowCount(len(records))
        for row, item in enumerate(records):
            code_item = QTableWidgetItem(str(item["code"]))
            name_item = QTableWidgetItem(str(item["name"]))
            code_item.setData(Qt.UserRole, (str(item["code"]), str(item["name"])))
            self.stock_table.setItem(row, 0, code_item)
            self.stock_table.setItem(row, 1, name_item)
        self._update_select_button_state()

    def _update_select_button_state(self) -> None:
        self.select_button.setEnabled(
            self._snapshot_valid and self.stock_table.currentRow() >= 0
        )

    def _accept_selection(self, *_args) -> None:
        if not self._snapshot_valid:
            return
        row = self.stock_table.currentRow()
        code_item = self.stock_table.item(row, 0) if row >= 0 else None
        if code_item is None:
            return
        identity = code_item.data(Qt.UserRole)
        if not isinstance(identity, tuple) or len(identity) != 2:
            return
        self._selection = ValidationStockRef(identity[0], identity[1])
        self.accept()
