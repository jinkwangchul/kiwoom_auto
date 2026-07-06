# -*- coding: utf-8 -*-
"""
gui_order_status_window.py

주문상태 보기 창 UI.
선택한 종목 runtime 폴더의 orders.json 을 읽어 주문/체결/타임라인을 표시한다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QDate
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui_order_utils import (
    build_current_status_rows,
    build_full_trade_export_text,
    build_grouped_order_timeline_text,
    date_range_for_mode,
    filter_orders_by_dates,
    order_sort_key,
    settlement_summary_text,
    today_orders,
)
from runtime_io import read_json_dict, read_orders_data
from state_policy import auto_trade_status_color, auto_trade_status_display


def create_current_trade_title_widget(
    status_text: str,
    title_text: str,
) -> QWidget:
    """
    주문상태 보기 최상단 현재 자동매매현황 제목 위젯.
    상태 점 + 제목을 한 줄로 표시한다.
    """
    container = QWidget()
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(7)

    dot = QLabel()
    dot.setFixedSize(11, 11)

    color = auto_trade_status_color(status_text)
    dot.setStyleSheet(
        "border-radius: 5px;"
        "border: 1px solid #555555;"
        f"background-color: {color};"
    )
    dot.setToolTip(f"자동매매 상태: {status_text}")

    label = QLabel(title_text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

    layout.addWidget(dot)
    layout.addWidget(label, 1)
    container.setLayout(layout)
    return container

def stock_runtime_status_from_state(stock_dir: Path) -> str:
    """
    선택 종목 폴더의 state.json status 값을 자동매매 상태 표시명으로 변환한다.
    """
    state = read_json_dict(stock_dir / "state.json")
    return auto_trade_status_display(state.get("status", "STOPPED"))

class OrderStatusWindow(QDialog):
    """
    주문상태 보기 창.

    선택한 루틴/종목 폴더의 orders.json 을 읽어
    주문/체결/미체결 현황과 간단한 매매 타임라인을 표시한다.
    """

    def __init__(
        self,
        stock_dir: Path,
        routine_name: str,
        stock_code: str,
        stock_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.stock_dir = stock_dir
        self.routine_name = routine_name
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.orders_path = stock_dir / "orders.json"

        self.setWindowTitle(f"주문상태 보기 - {stock_code} {stock_name}")
        self.resize(1040, 620)

        self.status_title_container = QWidget()
        self.status_title_layout = QVBoxLayout()
        self.status_title_layout.setContentsMargins(0, 0, 0, 0)
        self.status_title_container.setLayout(self.status_title_layout)
        self.timeline_summary_label = QLabel("")
        self.range_combo = QComboBox()
        self.range_combo.addItems(["이번주", "이번달", "3개월", "직접입력"])
        self.range_combo.setCurrentText("이번주")
        self.custom_start_date: date | None = None
        self.custom_end_date: date | None = None
        self.order_table = QTableWidget()
        self.timeline_text = QTextEdit()
        self.btn_export = QPushButton("전체내역 다운로드")
        self.btn_refresh = QPushButton("새로고침")
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._connect_events()
        self.load_orders()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()
        timeline_header_layout = QHBoxLayout()

        self.timeline_summary_label.setWordWrap(True)
        self.timeline_summary_label.hide()

        self._setup_order_table()

        self.timeline_text.setReadOnly(True)
        self.timeline_text.setMinimumHeight(270)
        self.timeline_text.setStyleSheet(
            "background-color: white;"
            "border: 1px solid #d0d0d0;"
            "padding: 6px;"
        )

        timeline_header_layout.setContentsMargins(0, 14, 0, 3)
        timeline_header_layout.addWidget(QLabel("매매 타임라인"))
        timeline_header_layout.addStretch(1)
        timeline_header_layout.addWidget(QLabel("기간설정"))
        timeline_header_layout.addWidget(self.range_combo)

        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_export)
        button_layout.addWidget(self.btn_refresh)
        button_layout.addWidget(self.btn_close)

        main_layout.addWidget(self.status_title_container)
        main_layout.addWidget(self.order_table)
        main_layout.addSpacing(8)
        main_layout.addLayout(timeline_header_layout)
        main_layout.addWidget(self.timeline_text)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_order_table(self) -> None:
        headers = [
            "시간",
            "구분",
            "주문수량",
            "체결수량",
            "미체결수량",
            "주문가격",
            "체결가격",
            "비용",
            "상태",
        ]

        self.order_table.setColumnCount(len(headers))
        self.order_table.setHorizontalHeaderLabels(headers)
        self.order_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.order_table.horizontalHeader().setStretchLastSection(True)
        self.order_table.setColumnWidth(0, 90)
        self.order_table.setColumnWidth(1, 70)
        self.order_table.setColumnWidth(2, 90)
        self.order_table.setColumnWidth(3, 90)
        self.order_table.setColumnWidth(4, 100)
        self.order_table.setColumnWidth(5, 100)
        self.order_table.setColumnWidth(6, 100)
        self.order_table.setColumnWidth(7, 90)
        self.order_table.setColumnWidth(8, 100)
        self.order_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.order_table.setMaximumHeight(230)

    def _connect_events(self) -> None:
        self.range_combo.currentTextChanged.connect(self.on_range_changed)
        self.btn_export.clicked.connect(self.export_full_trade_history)
        self.btn_refresh.clicked.connect(self.load_orders)
        self.btn_close.clicked.connect(self.close)

    def on_range_changed(self) -> None:
        if self.range_combo.currentText() == "직접입력":
            try:
                self.select_custom_range()
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "기간 설정 오류",
                    f"직접입력 기간 설정 중 오류가 발생했습니다.\\n\\n{exc}",
                )
                self.range_combo.blockSignals(True)
                self.range_combo.setCurrentText("이번주")
                self.range_combo.blockSignals(False)
                self.custom_start_date = None
                self.custom_end_date = None

        self.load_orders()

    def select_custom_range(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("기간 직접입력")

        layout = QVBoxLayout()
        start_edit = QDateEdit()
        end_edit = QDateEdit()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        today_qdate = QDate.currentDate()
        start_edit.setCalendarPopup(True)
        end_edit.setCalendarPopup(True)
        start_edit.setDate(today_qdate.addDays(-6))
        end_edit.setDate(today_qdate)

        layout.addWidget(QLabel("시작일"))
        layout.addWidget(start_edit)
        layout.addWidget(QLabel("종료일"))
        layout.addWidget(end_edit)
        layout.addWidget(buttons)
        dialog.setLayout(layout)

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec_() == QDialog.Accepted:
            self.custom_start_date = start_edit.date().toPyDate()
            self.custom_end_date = end_edit.date().toPyDate()
        else:
            self.range_combo.blockSignals(True)
            self.range_combo.setCurrentText("이번주")
            self.range_combo.blockSignals(False)
            self.custom_start_date = None
            self.custom_end_date = None

    def timeline_date_range(self) -> tuple[date | None, date | None]:
        mode = self.range_combo.currentText()

        if mode == "직접입력":
            return self.custom_start_date, self.custom_end_date

        return date_range_for_mode(mode)

    def range_label_text(self) -> str:
        mode = self.range_combo.currentText()
        start_date, end_date = self.timeline_date_range()

        if start_date is not None and end_date is not None:
            return f"{mode}({start_date.isoformat()} ~ {end_date.isoformat()})"

        return mode

    def set_current_status_title(self, status_text: str, title_text: str) -> None:
        while self.status_title_layout.count():
            item = self.status_title_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.status_title_layout.addWidget(
            create_current_trade_title_widget(status_text, title_text)
        )

    def load_orders(self) -> None:
        all_orders = read_orders_data(self.orders_path)

        current_orders = today_orders(all_orders)
        current_rows = build_current_status_rows(current_orders)

        start_date, end_date = self.timeline_date_range()
        timeline_orders = filter_orders_by_dates(all_orders, start_date, end_date)
        timeline_orders = sorted(timeline_orders, key=order_sort_key)

        now_text = datetime.now().strftime("%Y-%m-%d / %H:%M:%S")
        status_text = stock_runtime_status_from_state(self.stock_dir)

        self.set_current_status_title(
            status_text,
            f"{now_text} 현재 자동매매현황 | "
            f"루틴: {self.routine_name} | 종목: {self.stock_code} {self.stock_name} | "
            f"오늘 주문건수: {len(current_orders)}건",
        )

        start_date, end_date = self.timeline_date_range()
        if start_date is not None and end_date is not None:
            period_text = f"{start_date.isoformat()} ~ {end_date.isoformat()}"
        else:
            period_text = self.range_label_text()

        settlement_header_text = (
            f"기간: {period_text}\n"
            f"{settlement_summary_text(timeline_orders)} / "
            f"주문건수 {len(timeline_orders)}건 / 전체 {len(all_orders)}건"
        )

        self.order_table.setRowCount(len(current_rows))

        for row_index, values in enumerate(current_rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)

                if col in (1, 2, 3, 4, 5, 6, 7, 8):
                    item.setTextAlignment(Qt.AlignCenter)
                else:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

                self.order_table.setItem(row_index, col, item)

        timeline_body = build_grouped_order_timeline_text(timeline_orders)
        if timeline_body:
            self.timeline_text.setPlainText(f"{settlement_header_text}\n\n{timeline_body}")
        else:
            self.timeline_text.setPlainText(settlement_header_text)

    def export_full_trade_history(self) -> None:
        orders = read_orders_data(self.orders_path)
        default_name = (
            f"{self.stock_code}_{self.stock_name}_{self.routine_name}_"
            f"전체매매내역_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "전체 매매내역 저장",
            str(Path.cwd() / default_name),
            "Text Files (*.txt);;All Files (*)",
        )

        if not save_path:
            return

        export_text = build_full_trade_export_text(
            orders=orders,
            routine_name=self.routine_name,
            stock_code=self.stock_code,
            stock_name=self.stock_name,
            orders_path=self.orders_path,
        )

        try:
            Path(save_path).write_text(export_text, encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(
                self,
                "저장 오류",
                f"전체 매매내역 저장 중 오류가 발생했습니다.\n\n{exc}",
            )
            return

        QMessageBox.information(
            self,
            "저장 완료",
            f"전체 매매내역을 저장했습니다.\n\n{save_path}",
        )
