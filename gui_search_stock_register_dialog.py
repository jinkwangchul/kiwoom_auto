# -*- coding: utf-8 -*-
"""
gui_search_stock_register_dialog.py

종목 라이브러리 검색 등록창.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_stock_data import (
    append_base_stock,
    is_valid_stock_code,
    load_stock_library,
    normalize_stock_code,
    read_base_stocks,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_changelog(change_type: str, filename: str, message: str) -> None:
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )
    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


class SearchStockRegisterDialog(QDialog):
    """
    종목 라이브러리 검색 등록창.

    수동으로 종목코드와 종목명을 따로 입력하지 않는다.
    모든 신규 등록은 stock_library.json 검색 결과에서 선택한 종목만 허용한다.
    """

    def __init__(self, parent: QWidget | None = None, title: str = "종목 검색 등록") -> None:
        super().__init__(parent)

        self.setWindowTitle(title)
        self.resize(820, 560)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색어 입력")

        self.result_table = QTableWidget()

        self.btn_search = QPushButton("검색")
        self.btn_register = QPushButton("선택 등록")
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._connect_events()
        self.search_stocks()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        search_layout = QHBoxLayout()
        button_layout = QHBoxLayout()

        search_layout.addWidget(QLabel("검색어"))
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.btn_search)

        self._setup_result_table()

        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_register)
        button_layout.addWidget(self.btn_close)

        main_layout.addLayout(search_layout)
        main_layout.addWidget(self.result_table)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_result_table(self) -> None:
        headers = [
            "코드",
            "종목명",
            "시장",
            "등록상태",
        ]

        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setColumnWidth(0, 110)
        self.result_table.setColumnWidth(1, 220)
        self.result_table.setColumnWidth(2, 120)
        self.result_table.setColumnWidth(3, 130)
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.result_table.setSortingEnabled(True)
        self.result_table.horizontalHeader().setSortIndicatorShown(True)
        self.result_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _connect_events(self) -> None:
        self.btn_search.clicked.connect(self.search_stocks)
        self.search_input.returnPressed.connect(self.search_stocks)
        self.btn_register.clicked.connect(self.register_selected_stocks)
        self.btn_close.clicked.connect(self.close)
        self.result_table.customContextMenuRequested.connect(self.show_result_context_menu)

    def show_result_context_menu(self, position) -> None:
        """
        수동등록 검색 결과 테이블 우클릭 메뉴.
        """
        menu = QMenu(self)

        action_select_all = menu.addAction("전체 선택")
        action_clear_selection = menu.addAction("전체 해제")
        menu.addSeparator()
        action_register_selected = menu.addAction("선택 등록")

        has_rows = self.result_table.rowCount() > 0
        has_selection = bool(self.result_table.selectionModel().selectedRows())

        action_select_all.setEnabled(has_rows)
        action_clear_selection.setEnabled(has_selection)
        action_register_selected.setEnabled(has_selection)

        selected_action = menu.exec_(self.result_table.viewport().mapToGlobal(position))
        if selected_action is None:
            return

        if selected_action == action_select_all:
            self.select_all_results()
        elif selected_action == action_clear_selection:
            self.result_table.clearSelection()
        elif selected_action == action_register_selected:
            self.register_selected_stocks()

    def select_all_results(self) -> None:
        """
        현재 검색/필터 결과 전체를 선택한다.
        """
        if self.result_table.rowCount() <= 0:
            return

        self.result_table.selectAll()

    def search_stocks(self) -> None:
        keyword_text = self.search_input.text().strip().lower()
        keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]

        library = load_stock_library()
        base_stocks = read_base_stocks()
        existing_codes = {str(stock.get("code", "")).strip() for stock in base_stocks}
        existing_names = {str(stock.get("name", "")).strip() for stock in base_stocks}

        filtered: list[dict[str, str]] = []
        added_codes: set[str] = set()

        def stock_matches(stock: dict[str, str], keyword: str) -> bool:
            code = stock.get("code", "").strip()
            name = stock.get("name", "").strip()
            market = stock.get("market", "").strip()
            chosung = stock.get("chosung", "").strip()

            searchable_values = [
                code.lower(),
                name.lower(),
                market.lower(),
                chosung.lower(),
            ]
            return any(keyword in value for value in searchable_values)

        if not keywords:
            for stock in library:
                code = stock.get("code", "").strip()
                if code and code not in added_codes:
                    filtered.append(stock)
                    added_codes.add(code)
        else:
            for keyword in keywords:
                for stock in library:
                    code = stock.get("code", "").strip()
                    if not code or code in added_codes:
                        continue

                    if stock_matches(stock, keyword):
                        filtered.append(stock)
                        added_codes.add(code)

        self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(filtered))

        for row, stock in enumerate(filtered):
            code = stock.get("code", "").strip()
            name = stock.get("name", "").strip()
            registered = code in existing_codes or name in existing_names
            values = [
                code,
                name,
                stock.get("market", ""),
                "등록됨" if registered else "미등록",
            ]

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                item.setData(Qt.UserRole, value)
                self.result_table.setItem(row, col, item)

        self.result_table.setSortingEnabled(True)
        self.result_table.clearSelection()

    def register_selected_stocks(self) -> None:
        selected_rows = self.result_table.selectionModel().selectedRows()

        if not selected_rows:
            QMessageBox.warning(self, "선택 오류", "등록할 종목을 1개 이상 선택하세요.")
            return

        completed_count = 0
        duplicate_count = 0
        error_count = 0
        registered_items: list[str] = []
        duplicate_items: list[str] = []
        error_items: list[str] = []

        library_by_code = {
            stock.get("code", "").strip(): stock
            for stock in load_stock_library()
            if stock.get("code", "").strip()
        }

        parent = self.parent()

        # 분리 구조에서도 중복 검사가 parent 클래스명에 의존하지 않도록
        # 등록 시점의 중앙 종목관리를 직접 다시 읽어 1차 방어한다.
        # parent가 is_duplicate_stock()을 제공하면 그 결과도 함께 사용한다.
        base_stocks = read_base_stocks()
        existing_codes = {
            normalize_stock_code(str(stock.get("code", "")))
            for stock in base_stocks
            if str(stock.get("code", "")).strip()
        }
        existing_names = {
            str(stock.get("name", "")).strip()
            for stock in base_stocks
            if str(stock.get("name", "")).strip()
        }

        for index in selected_rows:
            row = index.row()
            code_item = self.result_table.item(row, 0)
            name_item = self.result_table.item(row, 1)

            if code_item is None or name_item is None:
                error_count += 1
                error_items.append(f"{row + 1}행")
                continue

            code = normalize_stock_code(code_item.text())
            name = name_item.text().strip()
            library_stock = library_by_code.get(code)

            if library_stock is None:
                error_count += 1
                error_items.append(f"{code},{name}" if code or name else f"{row + 1}행")
                continue

            library_name = library_stock.get("name", "").strip()
            if not is_valid_stock_code(code) or not name or name != library_name or "," in name:
                error_count += 1
                error_items.append(f"{code},{name}" if code or name else f"{row + 1}행")
                continue

            parent_duplicate = False
            if parent is not None and hasattr(parent, "is_duplicate_stock"):
                parent_duplicate = bool(parent.is_duplicate_stock(code, name))

            if code in existing_codes or name in existing_names or parent_duplicate:
                duplicate_count += 1
                duplicate_items.append(f"{code},{name}")
                continue

            if not append_base_stock(code, name):
                error_count += 1
                error_items.append(f"{code},{name}")
                continue
            existing_codes.add(code)
            existing_names.add(name)
            completed_count += 1
            registered_items.append(f"{code},{name}")

        if registered_items:
            append_changelog(
                "ADD",
                "중앙 종목관리",
                f"종목 라이브러리 선택등록: {' / '.join(registered_items)}",
            )

        if registered_items and parent is not None and hasattr(parent, "refresh_stock_table"):
            parent.refresh_stock_table()
            main_window = parent.parent() if hasattr(parent, "parent") else None
            if main_window is not None and hasattr(main_window, "refresh_all"):
                main_window.refresh_all()

        self.search_stocks()

        result_message = (
            "종목 등록 처리가 완료되었습니다.\n\n"
            f"신규등록 : {completed_count}개\n"
            f"등록제외 : {duplicate_count}개\n"
            f"등록불가 : {error_count}개"
        )

        if error_count > 0:
            result_message += (
                "\n\n"
                "※ 등록불가 종목이 발견되었습니다.\n"
                "종목 라이브러리 또는 프로그램 데이터 이상 가능성이 있습니다.\n"
                "무결성 검사를 권장합니다."
            )

        QMessageBox.information(
            self,
            "등록 결과",
            result_message,
        )

