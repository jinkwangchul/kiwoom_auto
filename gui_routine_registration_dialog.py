# -*- coding: utf-8 -*-
"""Reusable metadata dialog for registering a routine instance."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from gui_routine_registry import normalize_routine_name
from routine_instance_repository import RoutineInstanceCreateRequest


class _AlignedTitleLabel(QLabel):
    """Paint title glyphs against the same visual left and right edges."""

    def _draw_positions(self) -> list[tuple[str, int]]:
        characters = [character for character in self.text() if not character.isspace()]
        if not characters:
            return []
        metrics = self.fontMetrics()
        right_edge = max(0, self.width() - 1)
        positions: list[tuple[str, int]] = []
        for index, character in enumerate(characters):
            bounds = metrics.boundingRect(character)
            if len(characters) == 1:
                x = round((right_edge - bounds.width()) / 2) - bounds.left()
            elif index == 0:
                x = -bounds.left()
            elif index == len(characters) - 1:
                x = right_edge - bounds.right()
            else:
                ratio = index / (len(characters) - 1)
                center = ratio * right_edge
                x = round(center - ((bounds.left() + bounds.right()) / 2))
            positions.append((character, int(x)))
        return positions

    def visual_ink_edges(self) -> tuple[int, int]:
        positions = self._draw_positions()
        if not positions:
            return (0, 0)
        metrics = self.fontMetrics()
        first_character, first_x = positions[0]
        last_character, last_x = positions[-1]
        return (
            first_x + metrics.boundingRect(first_character).left(),
            last_x + metrics.boundingRect(last_character).right(),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        metrics = self.fontMetrics()
        baseline = ((self.height() - metrics.height()) // 2) + metrics.ascent()
        for character, x in self._draw_positions():
            painter.drawText(x, baseline, character)


def suggest_routine_instance_display_name(
    definition_display_name: str,
    persisted_instance_count: int,
) -> str:
    base = str(definition_display_name or "").strip()
    count = max(0, int(persisted_instance_count))
    if not base or count >= 26:
        return ""
    return f"{base}{chr(ord('A') + count)}"


def suggest_cloned_routine_instance_display_name(
    source_display_name: str,
    existing_display_names: list[str],
) -> str:
    source = str(source_display_name or "").strip()
    if not source:
        return ""
    suffix_index = ord(source[-1]) - ord("A") if "A" <= source[-1] <= "Z" else -1
    base = source[:-1] if suffix_index >= 0 else source
    used_indexes = {
        ord(name[-1]) - ord("A")
        for raw_name in existing_display_names
        if (
            (name := str(raw_name or "").strip()).startswith(base)
            and len(name) == len(base) + 1
            and "A" <= name[-1] <= "Z"
        )
    }
    next_index = max([suffix_index, *used_indexes], default=-1) + 1
    if next_index >= 26:
        return ""
    return f"{base}{chr(ord('A') + next_index)}"


class RoutineRegistrationDialog(QDialog):
    def __init__(
        self,
        *,
        definition_id: str,
        definition_display_name: str,
        initial_display_name: str = "",
        group_id: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.definition_id = str(definition_id or "").strip()
        self.definition_display_name = str(definition_display_name or "").strip()
        self.group_id = str(group_id or "").strip()
        self.registration_request: RoutineInstanceCreateRequest | None = None

        self.setWindowTitle("루틴 등록")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 12, 34, 12)
        root.setSizeConstraint(QLayout.SetFixedSize)
        form = QFormLayout()
        self.form_layout = form
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(10)

        group_display_name = (
            normalize_routine_name(Path(self.group_id).name)
            if self.group_id
            else "-"
        )
        self.definition_label = QLabel(group_display_name)
        self.definition_label.setObjectName("routineRegistrationDefinition")
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("routineRegistrationName")
        self.name_edit.setPlaceholderText("필수")
        self.name_edit.setText(str(initial_display_name or "").strip())
        self.name_edit.setMaxLength(12)
        self.description_edit = QLineEdit()
        self.description_edit.setObjectName("routineRegistrationDescription")
        self.description_edit.setMaxLength(12)
        input_width = self.fontMetrics().horizontalAdvance("한한한A00000000") + 10
        line_edit_style = (
            "QLineEdit {"
            " border: none;"
            " background-color: #ffffff;"
            " padding: 2px 5px;"
            "}"
        )
        self.name_edit.setStyleSheet(line_edit_style)
        self.description_edit.setStyleSheet(line_edit_style)
        self.name_edit.setFixedWidth(input_width)
        self.description_edit.setFixedWidth(input_width)

        title_texts = ("그      룹", "루 틴 명", "메     모")
        title_width = self.fontMetrics().horizontalAdvance("한" * 3) + 8
        self.group_title_label = self._form_title(title_texts[0], title_width)
        self.name_title_label = self._form_title(title_texts[1], title_width)
        self.description_title_label = self._form_title(title_texts[2], title_width)
        form.addRow(self.group_title_label, self.definition_label)
        form.addRow(self.name_title_label, self.name_edit)
        form.addRow(self.description_title_label, self.description_edit)
        root.addLayout(form)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        self.button_box.button(QDialogButtonBox.Ok).setText("확인")
        self.button_box.button(QDialogButtonBox.Cancel).setText("취소")
        self.button_box.accepted.connect(self._accept_validated)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

    @staticmethod
    def _form_title(text: str, width: int) -> QLabel:
        label = _AlignedTitleLabel(text)
        label.setFixedWidth(width)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return label

    def _accept_validated(self) -> None:
        display_name = self.name_edit.text().strip()
        if not display_name:
            QMessageBox.warning(self, "루틴 등록", "루틴명을 입력하세요.")
            self.name_edit.setFocus()
            return

        self.registration_request = RoutineInstanceCreateRequest(
            definition_id=self.definition_id,
            display_name=display_name,
            description=self.description_edit.text().strip(),
            buy_limit_enabled=False,
            buy_limit_amount=None,
            group_id=self.group_id,
        )
        self.accept()
