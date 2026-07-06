# -*- coding: utf-8 -*-
"""
gui_integrity_check_window.py

무결성검증 창.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from integrity_checker import (
    run_integrity_checks,
    write_invalid_items_log,
)


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
STOCK_LIBRARY_PATH = PROJECT_ROOT / "stock_library.json"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
INVALID_ITEMS_LOG_PATH = PROJECT_ROOT / "invalid_items.log"


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


class IntegrityCheckWindow(QDialog):
    """
    무결성검증 창.

    1차 구현 범위:
    - 검증 결과를 표로 출력한다.
    - invalid_items.log 에 결과를 저장한다.
    - 삭제/격리는 즉시 수행하지 않는다.
    """

    CHECK_ITEMS = [
        ("base_duplicate", "기초종목.txt 중복 검증"),
        ("stock_code", "종목코드 유효성 검증"),
        ("stock_name", "종목명 유효성 검증"),
        ("routine_folder", "루틴 패키지 일치 검증"),
        ("required_files", "필수 파일 존재 검증"),
        ("config_json", "config.json 검증"),
        ("state_json", "state.json 검증"),
        ("orders_json", "orders.json 검증"),
        ("budget_json", "routine.json 검증"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("무결성검증")
        self.resize(1040, 680)

        self.checkboxes: dict[str, QCheckBox] = {}
        self.result_table = QTableWidget()
        self.current_issues: list[dict[str, str]] = []

        self.btn_run = QPushButton("검증 시작")
        self.btn_quarantine = QPushButton("선택 항목 격리")
        self.btn_delete = QPushButton("선택 항목 삭제")
        self.btn_save = QPushButton("결과 저장")
        self.btn_close = QPushButton("닫기")

        self.btn_quarantine.setEnabled(False)
        self.btn_delete.setEnabled(False)
        self.btn_quarantine.setToolTip("2차 구현 예정: 현재 단계에서는 즉시 격리하지 않습니다.")
        self.btn_delete.setToolTip("2차 구현 예정: 현재 단계에서는 즉시 삭제하지 않습니다.")

        self._setup_ui()
        self._connect_events()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        check_box = QGroupBox("검증 항목")
        check_layout = QGridLayout()
        button_layout = QHBoxLayout()

        for index, (key, label) in enumerate(self.CHECK_ITEMS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.checkboxes[key] = checkbox
            check_layout.addWidget(checkbox, index // 3, index % 3)

        check_box.setLayout(check_layout)
        self._setup_result_table()

        buttons = [
            self.btn_run,
            self.btn_quarantine,
            self.btn_delete,
            self.btn_save,
            self.btn_close,
        ]

        button_layout.addStretch(1)
        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        main_layout.addWidget(check_box)
        main_layout.addWidget(QLabel("검증 결과"))
        main_layout.addWidget(self.result_table)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_result_table(self) -> None:
        headers = [
            "구분",
            "위치",
            "문제 내용",
            "권장 조치",
        ]

        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_table.setTextElideMode(Qt.ElideRight)
        self.result_table.setWordWrap(False)
        self.result_table.verticalHeader().setFixedWidth(42)
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.result_table.setColumnWidth(0, 120)
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def _connect_events(self) -> None:
        self.btn_run.clicked.connect(self.run_check)
        self.btn_save.clicked.connect(self.save_results)
        self.btn_quarantine.clicked.connect(self.show_deferred_message)
        self.btn_delete.clicked.connect(self.show_deferred_message)
        self.btn_close.clicked.connect(self.close)

    def selected_check_keys(self) -> set[str]:
        return {
            key for key, checkbox in self.checkboxes.items()
            if checkbox.isChecked()
        }

    def run_check(self) -> None:
        selected_checks = self.selected_check_keys()

        if not selected_checks:
            QMessageBox.warning(
                self,
                "검증 항목 없음",
                "검증할 항목을 1개 이상 선택하세요.",
            )
            return

        self.current_issues = run_integrity_checks(
            selected_checks,
            PROJECT_ROOT,
            BASE_STOCK_PATH,
            STOCK_LIBRARY_PATH,
        )
        self.populate_result_table(self.current_issues)

        try:
            write_invalid_items_log(self.current_issues, INVALID_ITEMS_LOG_PATH)
        except Exception:
            pass

        if self.current_issues:
            QMessageBox.information(
                self,
                "검증 완료",
                f"무결성검증이 완료되었습니다.\n\n검출항목: {len(self.current_issues)}건",
            )
        else:
            QMessageBox.information(
                self,
                "검증 완료",
                "무결성검증이 완료되었습니다.\n\n검출항목이 없습니다.",
            )

    def populate_result_table(self, issues: list[dict[str, str]]) -> None:
        self.result_table.setRowCount(len(issues))

        for row, issue in enumerate(issues):
            values = [
                issue.get("category", ""),
                issue.get("location", ""),
                issue.get("message", ""),
                issue.get("action", ""),
            ]

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                else:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                self.result_table.setItem(row, col, item)

        self.result_table.clearSelection()

    def save_results(self) -> None:
        try:
            write_invalid_items_log(self.current_issues, INVALID_ITEMS_LOG_PATH)
            append_changelog(
                "CHECK",
                "invalid_items.log",
                f"무결성검증 결과 저장: {len(self.current_issues)}건",
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "저장 오류",
                f"검증 결과 저장 중 오류가 발생했습니다.\n\n{exc}",
            )
            return

        QMessageBox.information(
            self,
            "저장 완료",
            f"검증 결과를 invalid_items.log에 저장했습니다.\n\n검출항목: {len(self.current_issues)}건",
        )

    def show_deferred_message(self) -> None:
        QMessageBox.information(
            self,
            "다음 단계 구현",
            "현재 단계에서는 문제 항목을 즉시 삭제하거나 격리하지 않습니다.\n"
            "검증 결과 확인과 로그 저장만 수행합니다.",
        )



