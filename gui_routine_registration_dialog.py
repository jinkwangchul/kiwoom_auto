# -*- coding: utf-8 -*-
"""Reusable metadata dialog for registering a routine instance."""

from __future__ import annotations

from PyQt5.QtCore import QRegularExpression
from PyQt5.QtGui import QRegularExpressionValidator
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from routine_instance_repository import RoutineInstanceCreateRequest


def suggest_routine_instance_display_name(
    definition_display_name: str,
    persisted_instance_count: int,
) -> str:
    base = str(definition_display_name or "").strip()
    count = max(0, int(persisted_instance_count))
    if not base or count >= 26:
        return ""
    return f"{base}{chr(ord('A') + count)}"


class RoutineRegistrationDialog(QDialog):
    def __init__(
        self,
        *,
        definition_id: str,
        definition_display_name: str,
        initial_display_name: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.definition_id = str(definition_id or "").strip()
        self.definition_display_name = str(definition_display_name or "").strip()
        self.registration_request: RoutineInstanceCreateRequest | None = None

        self.setWindowTitle("루틴 등록")
        self.setModal(True)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.form_layout = form
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.definition_label = QLabel(self.definition_display_name or "-")
        self.definition_label.setObjectName("routineRegistrationDefinition")
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("routineRegistrationName")
        self.name_edit.setPlaceholderText("필수")
        self.name_edit.setText(str(initial_display_name or "").strip())
        self.description_edit = QLineEdit()
        self.description_edit.setObjectName("routineRegistrationDescription")
        self.description_edit.setPlaceholderText("선택")
        self.buy_limit_enabled_check = QCheckBox("사용")
        self.buy_limit_enabled_check.setObjectName("routineRegistrationBuyLimitEnabled")
        self.buy_limit_amount_edit = QLineEdit()
        self.buy_limit_amount_edit.setObjectName("routineRegistrationBuyLimitAmount")
        self.buy_limit_amount_edit.setPlaceholderText("원 단위 금액")
        self.buy_limit_amount_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[0-9,]{0,20}"), self)
        )
        self.buy_limit_amount_edit.setEnabled(False)
        self.new_status_label = QLabel("비활성")
        self.new_status_label.setObjectName("routineRegistrationStatus")

        self.buy_limit_enabled_check.toggled.connect(self.buy_limit_amount_edit.setEnabled)

        form.addRow("루틴 유형", self.definition_label)
        form.addRow("루틴 이름", self.name_edit)
        form.addRow("메모", self.description_edit)
        form.addRow("매수한도", self.buy_limit_enabled_check)
        form.addRow("매수한도 금액", self.buy_limit_amount_edit)
        form.addRow("신규 상태", self.new_status_label)
        root.addLayout(form)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        self.button_box.button(QDialogButtonBox.Ok).setText("확인")
        self.button_box.button(QDialogButtonBox.Cancel).setText("취소")
        self.button_box.accepted.connect(self._accept_validated)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

    def _accept_validated(self) -> None:
        display_name = self.name_edit.text().strip()
        if not display_name:
            QMessageBox.warning(self, "루틴 등록", "루틴 이름을 입력하세요.")
            self.name_edit.setFocus()
            return

        buy_limit_enabled = self.buy_limit_enabled_check.isChecked()
        buy_limit_amount = None
        if buy_limit_enabled:
            amount_text = self.buy_limit_amount_edit.text().replace(",", "").strip()
            try:
                buy_limit_amount = int(amount_text)
            except (TypeError, ValueError):
                buy_limit_amount = None
            if buy_limit_amount is None or buy_limit_amount <= 0:
                QMessageBox.warning(self, "루틴 등록", "매수한도는 0보다 큰 원 단위 금액이어야 합니다.")
                self.buy_limit_amount_edit.setFocus()
                return

        self.registration_request = RoutineInstanceCreateRequest(
            definition_id=self.definition_id,
            display_name=display_name,
            description=self.description_edit.text().strip(),
            buy_limit_enabled=buy_limit_enabled,
            buy_limit_amount=buy_limit_amount,
        )
        self.accept()
