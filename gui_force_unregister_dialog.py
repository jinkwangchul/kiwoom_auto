# -*- coding: utf-8 -*-
"""
gui_force_unregister_dialog.py

주의 종목 강제 삭제 확인창.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui_styles import apply_plain_table_header


class ForceUnregisterConfirmDialog(QDialog):
    """
    보유/미체결 등이 남아 있어 일반 삭제는 주의가 필요한 종목을
    개별 체크 후 강제 삭제할 수 있도록 하는 확인창.
    """

    def __init__(
        self,
        parent: QWidget | None,
        force_items: list[dict[str, object]],
        blocked_items: list[dict[str, object]],
        immediate_count: int = 0,
    ) -> None:
        super().__init__(parent)
        self.force_items = force_items
        self.blocked_items = blocked_items
        self.checkboxes: list[tuple[QCheckBox, dict[str, object]]] = []

        self.setWindowTitle("주의 종목 삭제")
        self.resize(850, 560)
        self._setup_ui(immediate_count)

    @staticmethod
    def _reason_cells(item: dict[str, object]) -> tuple[str, str, str, str]:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        raw_reasons = [str(reason) for reason in item.get("reasons", [])]

        routines: list[str] = []
        details: list[str] = []
        for reason in raw_reasons:
            if ": " in reason:
                routine_name, detail = reason.split(": ", 1)
                if routine_name and routine_name not in routines:
                    routines.append(routine_name)
                if detail:
                    details.append(detail)
            else:
                details.append(reason)

        routine_text = " / ".join(routines) if routines else "-"
        reason_text = " / ".join(details) if details else "-"
        return code, name, routine_text, reason_text

    @staticmethod
    def _set_text_item(table: QTableWidget, row: int, col: int, value: str, alignment: Qt.AlignmentFlag) -> None:
        table_item = QTableWidgetItem(value)
        table_item.setTextAlignment(alignment)
        table_item.setToolTip(value)
        table.setItem(row, col, table_item)

    def _setup_ui(self, immediate_count: int) -> None:
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(18, 16, 18, 14)
        main_layout.setSpacing(8)

        summary_parts: list[str] = []
        if immediate_count > 0:
            summary_parts.append(f"즉시 삭제 {immediate_count}개")
        if self.force_items:
            summary_parts.append(f"확인 필요 {len(self.force_items)}개")
        if self.blocked_items:
            summary_parts.append(f"삭제 불가 {len(self.blocked_items)}개")
        summary_text = " / ".join(summary_parts) if summary_parts else "삭제 대상 없음"
        summary_label = QLabel(summary_text)
        summary_label.setStyleSheet("font-weight: 600; padding: 0px 0px 4px 0px;")
        main_layout.addWidget(summary_label)

        if self.force_items:
            force_title = QLabel("확인 필요 종목")
            force_title.setStyleSheet("font-weight: 600; padding-top: 4px;")
            main_layout.addWidget(force_title)

            force_table = QTableWidget()
            force_table.setColumnCount(5)
            force_table.setHorizontalHeaderLabels(["선택", "코드", "종목명", "루틴", "사유"])

            # 중요: apply_plain_table_header()는 헤더 객체를 교체한다.
            # 따라서 컬럼 폭은 헤더 교체 후에 지정해야 실제 화면에 반영된다.
            apply_plain_table_header(force_table)
            force_header = force_table.horizontalHeader()
            force_header.setSectionResizeMode(QHeaderView.Fixed)
            force_header.setStretchLastSection(False)
            force_header.setMinimumSectionSize(1)
            force_header.setDefaultSectionSize(80)

            force_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            force_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            force_table.setRowCount(len(self.force_items))

            # 행 번호 영역을 숨기고, 표 폭과 컬럼 합계를 정확히 맞춘다.
            # 800 = 선택 48 + 코드 82 + 종목명 170 + 루틴 180 + 사유 320
            force_table.verticalHeader().setVisible(False)
            force_table.setFixedWidth(800)
            column_widths = [48, 82, 170, 180, 320]
            for col, width in enumerate(column_widths):
                force_table.setColumnWidth(col, width)
                force_header.resizeSection(col, width)

            force_table.setMinimumHeight(min(220, 58 + len(self.force_items) * 32))
            force_table.setMaximumHeight(min(240, 70 + len(self.force_items) * 34))
            force_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            force_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            force_table.setWordWrap(False)
            force_table.setAlternatingRowColors(False)

            for row, item in enumerate(self.force_items):
                checkbox = QCheckBox()
                checkbox.setChecked(False)
                checkbox.setToolTip("체크한 종목만 삭제합니다.")
                box_widget = QWidget()
                box_layout = QHBoxLayout()
                box_layout.setContentsMargins(0, 0, 0, 0)
                box_layout.addStretch(1)
                box_layout.addWidget(checkbox)
                box_layout.addStretch(1)
                box_widget.setLayout(box_layout)
                force_table.setCellWidget(row, 0, box_widget)

                code, name, routine_text, reason_text = self._reason_cells(item)
                self._set_text_item(force_table, row, 1, code, Qt.AlignCenter)
                self._set_text_item(force_table, row, 2, name, Qt.AlignLeft | Qt.AlignVCenter)
                self._set_text_item(force_table, row, 3, routine_text, Qt.AlignLeft | Qt.AlignVCenter)
                self._set_text_item(force_table, row, 4, reason_text, Qt.AlignLeft | Qt.AlignVCenter)
                self.checkboxes.append((checkbox, item))

            main_layout.addWidget(force_table)

        if self.blocked_items:
            blocked_title = QLabel("삭제 불가 ")
            blocked_title.setStyleSheet("font-weight: 600; color: #b91c1c; padding-top: 4px;")
            main_layout.addWidget(blocked_title)

            blocked_text = QTextEdit()
            blocked_text.setReadOnly(True)
            blocked_text.setAcceptRichText(False)
            blocked_text.setLineWrapMode(QTextEdit.NoWrap)
            blocked_text.setStyleSheet(
                "QTextEdit {"
                " background: #ffffff;"
                " border: 1px solid #a8adb3;"
                " padding: 6px;"
                "}"
            )

            blocked_lines: list[str] = []
            for item in self.blocked_items:
                code, name, routine_text, reason_text = self._reason_cells(item)
                blocked_lines.append(f"{code} / {name} / {routine_text} / {reason_text}")

            blocked_text.setPlainText("\n".join(blocked_lines))
            blocked_text.setMinimumHeight(min(220, 44 + len(self.blocked_items) * 28))
            blocked_text.setMaximumHeight(min(260, 56 + len(self.blocked_items) * 30))
            main_layout.addWidget(blocked_text)

        notice = QLabel("※ 삭제 불가 종목은 상태 정리 후 다시 진행하세요.")
        notice.setStyleSheet("color: #555555; padding-top: 4px;")
        main_layout.addWidget(notice)

        if self.force_items or immediate_count > 0:
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            ok_text = "체크 항목 삭제" if self.force_items else "삭제 실행"
            buttons.button(QDialogButtonBox.Ok).setText(ok_text)
            buttons.button(QDialogButtonBox.Cancel).setText("취소")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.button(QDialogButtonBox.Close).setText("닫기")
            buttons.rejected.connect(self.reject)

        main_layout.addWidget(buttons)

        self.setLayout(main_layout)

    def selected_items(self) -> list[dict[str, object]]:
        return [item for checkbox, item in self.checkboxes if checkbox.isChecked()]
