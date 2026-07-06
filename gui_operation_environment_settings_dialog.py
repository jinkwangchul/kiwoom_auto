# -*- coding: utf-8 -*-

"""
gui_stock_register_window.py 다음 분리 대상인 운영환경설정 창 분리본.

분리 내용:
- write_operation_policy
- TimeComboWidget
- OperationEnvironmentSettingsDialog
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from state_policy import normalized_hhmmss_or_empty
from gui_auto_trade_setting_window import (
    append_changelog,
    now_text,
    read_operation_policy,
)

PROJECT_ROOT = Path(__file__).resolve().parent
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"


def write_operation_policy(policy: dict[str, object]) -> None:
    policy = dict(policy)
    policy["updated_at"] = now_text()
    OPERATION_POLICY_PATH.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )



class TimeComboWidget(QWidget):
    """시/분 콤보박스로 시간을 선택하는 작은 위젯."""

    def __init__(self, default_time: str = "09:00:00", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hour_combo = QComboBox()
        self.minute_combo = QComboBox()
        self.hour_combo.addItems([f"{hour:02d}" for hour in range(24)])
        self.minute_combo.addItems([f"{minute:02d}" for minute in range(0, 60, 5)])
        self.hour_combo.setFixedWidth(68)
        self.minute_combo.setFixedWidth(68)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.hour_combo)
        layout.addWidget(QLabel("시"))
        layout.addWidget(self.minute_combo)
        layout.addWidget(QLabel("분"))
        self.setLayout(layout)
        self.set_time(default_time, default_time)

    def set_time(self, value: object, default_time: str = "09:00:00") -> None:
        normalized = normalized_hhmmss_or_empty(value) or normalized_hhmmss_or_empty(default_time) or "09:00:00"
        try:
            hour, minute, _second = [int(part) for part in normalized.split(":")]
        except Exception:
            hour, minute = 9, 0
        rounded_minute = int(minute / 5) * 5
        self.hour_combo.setCurrentText(f"{hour:02d}")
        self.minute_combo.setCurrentText(f"{rounded_minute:02d}")

    def time_text(self) -> str:
        return f"{int(self.hour_combo.currentText()):02d}:{int(self.minute_combo.currentText()):02d}:00"

class OperationEnvironmentSettingsDialog(QDialog):
    """스케줄매매관리 대체용 운영환경설정 UI.

    환경설정은 전체 기본값이며, 개별 종목 예외는 종목 우클릭 설정에서 처리한다.
    """

    CLOSE_METHODS = ["루틴매도신호", "시장가", "현재가", "익절/손절", "이월"]
    LIQUIDATION_METHODS = ["이월", "시장가", "현재가"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("환경설정")
        self.setStyleSheet("""
            QDialog, QWidget, QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton {
                font-family: '맑은 고딕';
                font-size: 9pt;
            }
            QGroupBox {
                font-family: '맑은 고딕';
                font-size: 10pt;
                font-weight: bold;
            }
            QComboBox {
                min-height: 24px;
            }
            QLineEdit {
                min-height: 24px;
            }
            QPushButton {
                min-height: 28px;
                min-width: 82px;
            }
        """)
        self.resize(1080, 640)
        self.policy = read_operation_policy()
        self.setStyleSheet(
            "QGroupBox { font-size: 9pt; font-weight: bold; margin-top: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }"
            "QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton { font-size: 9pt; }"
            "QComboBox, QLineEdit { min-height: 30px; }"
        )

        self.regular_start = self._make_time_edit("09:00:00")
        self.regular_end = self._make_time_edit("15:20:00")
        self.scheduled_start = self._make_time_edit("09:00:00")
        self.scheduled_end_buy = self._make_time_edit("13:30:00")
        self.scheduled_after_status = QComboBox()
        self.scheduled_after_status.addItems(["감시/매도", "감시/대기"])
        self.scheduled_after_status.setMinimumWidth(110)

        self.extra_name: list[QLineEdit] = []
        self.extra_start: list[TimeComboWidget] = []
        self.extra_end: list[TimeComboWidget] = []

        self.manual_use_regular = QCheckBox("정규장 사용")
        self.manual_extra_checks = [QCheckBox(f"추가{i}") for i in range(1, 4)]
        self.manual_liquidation = QCheckBox("청산정책 적용")

        
        self.auto_close_method = QComboBox()
        # 체크박스 UI와 저장용 숨김 콤보의 항목은 반드시 1회만 등록한다.
        self.auto_close_method.setVisible(False)
        self.auto_close_signal = QCheckBox("루틴매도신호")
        self.auto_close_market = QCheckBox("시장가")
        self.auto_close_current = QCheckBox("현재가")
        self.auto_close_profit_loss = QCheckBox("익절/손절")
        self.auto_close_signal.setChecked(True)
        self.auto_close_options = [
            self.auto_close_signal,
            self.auto_close_market,
            self.auto_close_current,
            self.auto_close_profit_loss,
        ]
        for _cb in self.auto_close_options:
            _cb.setMinimumWidth(92)

        self.auto_close_method.setMinimumWidth(150)
        self.auto_close_method.addItems(self.CLOSE_METHODS)
        self.auto_close_method.setMinimumWidth(145)
        self.auto_profit = self._make_short_line()
        self.auto_loss = self._make_short_line()

        
        self.early_close_method = QComboBox()
        # 체크박스 UI와 저장용 숨김 콤보의 항목은 반드시 1회만 등록한다.
        self.early_close_method.setVisible(False)
        self.early_close_signal = QCheckBox("루틴매도신호")
        self.early_close_market = QCheckBox("시장가")
        self.early_close_current = QCheckBox("현재가")
        self.early_close_profit_loss = QCheckBox("익절/손절")
        self.early_close_market.setChecked(True)
        self.early_close_options = [
            self.early_close_signal,
            self.early_close_market,
            self.early_close_current,
            self.early_close_profit_loss,
        ]
        for _cb in self.early_close_options:
            _cb.setMinimumWidth(92)

        self.early_close_method.setMinimumWidth(150)
        self.early_close_method.addItems(self.CLOSE_METHODS)
        self.early_close_method.setMinimumWidth(145)
        self.early_profit = self._make_short_line()
        self.early_loss = self._make_short_line()

        self.liquidation_minutes = self._make_short_line("5")
        self.liquidation_checks: dict[str, QCheckBox] = {
            name: QCheckBox(name) for name in self.LIQUIDATION_METHODS
        }
        for checkbox in self.liquidation_checks.values():
            checkbox.clicked.connect(lambda _checked=False, cb=checkbox: self._select_liquidation_method(cb))

        self.liquidation_minutes = QComboBox()
        self.liquidation_minutes.addItems([str(value) for value in range(5, 101, 5)])
        self.liquidation_minutes.setFixedWidth(70)
        self._setup_ui()
        self._connect_close_option_checks()
        self.manual_liquidation.clicked.connect(lambda _checked=False: self._update_manual_liquidation_mode())
        self.load_policy_to_widgets()

    def _make_short_line(self, default: str = "") -> QLineEdit:
        line = QLineEdit(default)
        line.setMinimumWidth(70)
        return line


    def _make_time_edit(self, default_time: str) -> TimeComboWidget:
        return TimeComboWidget(default_time)

    def _set_time_edit(self, edit: TimeComboWidget, value: object, default_time: str) -> None:
        edit.set_time(value, default_time)

    def _time_edit_text(self, edit: TimeComboWidget) -> str:
        return edit.time_text()

    def _select_liquidation_method(self, selected: QCheckBox) -> None:
        for checkbox in self.liquidation_checks.values():
            checkbox.setChecked(checkbox is selected)

    def _current_liquidation_method(self) -> str:
        for name, checkbox in self.liquidation_checks.items():
            if checkbox.isChecked():
                return name
        return "이월"




    def _exclusive_close_check(self, current: QCheckBox, checks: list[QCheckBox]) -> None:
        """체크박스형 표시지만 마감방식은 1개만 선택한다."""
        for cb in checks:
            cb.setChecked(cb is current)
        current.setChecked(True)
        self._update_profit_loss_input_enabled()

    def _update_profit_loss_input_enabled(self) -> None:
        """익절/손절 옵션 선택 여부에 따라 입력칸 활성/비활성을 맞춘다."""
        auto_enabled = (
            hasattr(self, "auto_close_checks")
            and len(self.auto_close_checks) > 3
            and self.auto_close_checks[3].isChecked()
        )
        early_enabled = (
            hasattr(self, "early_close_checks")
            and len(self.early_close_checks) > 3
            and self.early_close_checks[3].isChecked()
        )

        if hasattr(self, "auto_profit"):
            self.auto_profit.setEnabled(auto_enabled)
        if hasattr(self, "auto_loss"):
            self.auto_loss.setEnabled(auto_enabled)
        if hasattr(self, "early_profit"):
            self.early_profit.setEnabled(early_enabled)
        if hasattr(self, "early_loss"):
            self.early_loss.setEnabled(early_enabled)

    def _connect_close_option_checks(self) -> None:
        for checks in [getattr(self, "auto_close_checks", []), getattr(self, "early_close_checks", [])]:
            for cb in checks:
                cb.clicked.connect(
                    lambda checked, current=cb, group=checks: self._exclusive_close_check(current, group)
                )
        self._update_profit_loss_input_enabled()

    def _sync_close_checkboxes_to_combo(self) -> None:
        def sync(checks: list[QCheckBox], combo: QComboBox, default_index: int) -> None:
            if not checks:
                return
            selected = default_index
            for idx, cb in enumerate(checks):
                if cb.isChecked():
                    selected = idx
                    break
            for idx, cb in enumerate(checks):
                cb.setChecked(idx == selected)
            combo.setCurrentIndex(selected)

        if hasattr(self, "auto_close_method") and hasattr(self, "auto_close_checks"):
            sync(self.auto_close_checks, self.auto_close_method, 0)
        if hasattr(self, "early_close_method") and hasattr(self, "early_close_checks"):
            sync(self.early_close_checks, self.early_close_method, 1)
        self._update_profit_loss_input_enabled()

    def _sync_combo_to_close_checkboxes(self) -> None:
        def sync(combo: QComboBox, checks: list[QCheckBox]) -> None:
            if not checks:
                return
            idx = combo.currentIndex()
            if idx < 0 or idx >= len(checks):
                idx = 0
            for i, cb in enumerate(checks):
                cb.setChecked(i == idx)

        if hasattr(self, "auto_close_method") and hasattr(self, "auto_close_checks"):
            sync(self.auto_close_method, self.auto_close_checks)
        if hasattr(self, "early_close_method") and hasattr(self, "early_close_checks"):
            sync(self.early_close_method, self.early_close_checks)
        self._update_profit_loss_input_enabled()


    def update_manual_extra_labels(self) -> None:
        """추가시간 구간명을 수동운영 옵션 표시명에 반영한다."""
        for index, checkbox in enumerate(self.manual_extra_checks):
            name = self.extra_name[index].text().strip() if index < len(self.extra_name) else ""
            checkbox.setText(name or f"추가{index + 1}")
            checkbox.setMinimumWidth(max(82, min(150, len(checkbox.text()) * 12 + 34)))

    def _update_manual_liquidation_mode(self) -> None:
        """청산정책 적용 시 수동운영은 정규장만 허용한다.

        청산정책은 정규장 종료 기준으로 동작하므로 추가시간과 함께 선택되면
        의미가 충돌한다. 따라서 청산정책 적용 ON 상태에서는 정규장만 유지하고
        추가시간 체크박스는 자동 해제 후 비활성화한다.
        """
        liquidation_enabled = self.manual_liquidation.isChecked()

        if liquidation_enabled:
            self.manual_use_regular.setChecked(True)

        self.manual_use_regular.setEnabled(not liquidation_enabled)

        for checkbox in self.manual_extra_checks:
            if liquidation_enabled:
                checkbox.setChecked(False)
            checkbox.setEnabled(not liquidation_enabled)

    def save_extra_sessions_only(self) -> None:
        """추가시간 이름/시간만 저장하고 수동운영 옵션 표시를 즉시 갱신한다."""
        self.update_manual_extra_labels()
        # 추가시간 저장 버튼을 눌러도 화면에 선택된 마감/조기마감 체크 상태가
        # 숨김 콤보 저장값과 어긋나지 않도록 먼저 동기화한다.
        self._sync_close_checkboxes_to_combo()
        policy = self.build_policy_from_widgets()
        try:
            write_operation_policy(policy)
            self.policy = policy
            append_changelog("UPDATE", "operation_policy.json", "추가시간 설정 저장")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"추가시간 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "저장 완료", "추가시간 설정을 저장했습니다.")

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(18, 16, 18, 16)

        # 1. 운영시간 설정
        operation_time_box = QGroupBox("")
        operation_time_layout = QGridLayout()
        operation_time_layout.setContentsMargins(16, 12, 16, 12)
        operation_time_layout.setHorizontalSpacing(8)
        operation_time_layout.setVerticalSpacing(8)

        # 정규장 행: 제목 / 정규장 / 시작 / 종료를 같은 높이로 정렬
        op_title = QLabel("1. 운영시간 설정")
        op_title.setStyleSheet("font-weight: bold;")
        operation_time_layout.addWidget(op_title, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        regular_label = QLabel("정규장")
        regular_label.setStyleSheet("font-weight: bold;")
        operation_time_layout.addWidget(regular_label, 0, 1, Qt.AlignCenter)

        operation_time_layout.addWidget(QLabel("시작"), 0, 3, Qt.AlignRight | Qt.AlignVCenter)
        operation_time_layout.addWidget(self.regular_start, 0, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        operation_time_layout.addWidget(QLabel("종료"), 0, 7, Qt.AlignRight | Qt.AlignVCenter)
        operation_time_layout.addWidget(self.regular_end, 0, 8, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        operation_time_layout.addWidget(separator, 1, 0, 1, 10)

        # ATS 위치는 유지. 시작/종료 열만 정규장 시작/종료 열과 동일하게 사용.
        ats_label = QLabel("ATS")
        ats_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        ats_label.setAlignment(Qt.AlignCenter)
        operation_time_layout.addWidget(ats_label, 4, 0, Qt.AlignCenter)

        for index in range(3):
            name = QLineEdit()
            name.setFixedWidth(120)
            start_time = self._make_time_edit("09:00:00")
            end_time = self._make_time_edit("15:20:00")
            self.extra_name.append(name)
            self.extra_start.append(start_time)
            self.extra_end.append(end_time)

            row = index + 3
            operation_time_layout.addWidget(name, row, 1, 1, 1, Qt.AlignLeft | Qt.AlignVCenter)

            save_button = QPushButton("저장")
            save_button.setFixedWidth(44)
            save_button.clicked.connect(self.save_extra_sessions_only)
            operation_time_layout.addWidget(save_button, row, 2, 1, 1, Qt.AlignLeft | Qt.AlignVCenter)

            if index == 1:
                operation_time_layout.addWidget(QLabel("시작"), row, 3, Qt.AlignRight | Qt.AlignVCenter)
                operation_time_layout.addWidget(QLabel("종료"), row, 7, Qt.AlignRight | Qt.AlignVCenter)

            operation_time_layout.addWidget(start_time, row, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
            operation_time_layout.addWidget(end_time, row, 8, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        operation_time_layout.setColumnMinimumWidth(0, 130)
        operation_time_layout.setColumnMinimumWidth(1, 124)
        operation_time_layout.setColumnMinimumWidth(2, 50)
        operation_time_layout.setColumnMinimumWidth(3, 54)
        operation_time_layout.setColumnMinimumWidth(4, 92)
        operation_time_layout.setColumnMinimumWidth(5, 52)
        operation_time_layout.setColumnMinimumWidth(6, 70)
        operation_time_layout.setColumnMinimumWidth(7, 54)
        operation_time_layout.setColumnMinimumWidth(8, 92)
        operation_time_layout.setColumnMinimumWidth(9, 52)
        operation_time_layout.setColumnStretch(10, 1)

        operation_time_box.setLayout(operation_time_layout)
        layout.addWidget(operation_time_box)

        # 2. 시간운영 기본설정
        scheduled_box = QGroupBox("")
        scheduled_layout = QHBoxLayout()
        scheduled_layout.setContentsMargins(12, 8, 12, 8)
        scheduled_layout.setSpacing(18)
        scheduled_title = QLabel("2. 시간운영 기본설정")
        scheduled_title.setStyleSheet("font-weight: bold;")
        scheduled_title.setMinimumWidth(205)
        scheduled_layout.addWidget(scheduled_title)
        scheduled_layout.addWidget(QLabel("시작"))
        scheduled_layout.addWidget(self.scheduled_start)
        scheduled_layout.addSpacing(22)
        scheduled_layout.addWidget(QLabel("매수종료"))
        scheduled_layout.addWidget(self.scheduled_end_buy)
        scheduled_layout.addSpacing(22)
        scheduled_layout.addWidget(QLabel("매수종료 후"))
        scheduled_layout.addWidget(self.scheduled_after_status)
        scheduled_layout.addStretch(1)
        scheduled_box.setLayout(scheduled_layout)
        layout.addWidget(scheduled_box)

        # 3~6. 옵션 열 정렬 영역
        # 기준:
        # - 1, 2번 영역은 수정하지 않는다.
        # - 체크박스 사각형의 x축을 기준으로 정렬한다.
        # - 3번 청산정책 적용과 4/5번 이월은 같은 후방 열.
        # - 6번 시장가 = 5번 현재가 열, 6번 현재가 = 5번 익절/손절 열.
        # - 6번 이월은 6번 시장가↔현재가 간격만큼 오른쪽.
        title_width = 205
        option_col_width = 128
        late_col_width = 78

        def make_row_box(title_text: str) -> tuple[QGroupBox, QGridLayout]:
            box = QGroupBox("")
            row_layout = QGridLayout()
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setHorizontalSpacing(0)
            row_layout.setVerticalSpacing(0)

            title = QLabel(title_text)
            title.setStyleSheet("font-weight: bold;")
            title.setMinimumWidth(title_width)
            row_layout.addWidget(title, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

            # 1~4번 기본 옵션열은 동일 간격.
            for col in range(1, 5):
                row_layout.setColumnMinimumWidth(col, option_col_width)
                row_layout.setColumnStretch(col, 0)

            # 5번은 익절/손절 입력칸 소속 영역.
            row_layout.setColumnMinimumWidth(5, 178)
            row_layout.setColumnStretch(5, 0)

            # 6번은 후방 체크박스열. 기존보다 약 40% 정도 뒤쪽으로 밀린 위치.
            row_layout.setColumnMinimumWidth(6, late_col_width)
            row_layout.setColumnStretch(7, 1)

            box.setLayout(row_layout)
            return box, row_layout

        # 3. 수동운영 기본설정
        manual_box, manual_layout = make_row_box("3. 수동운영 기본설정")
        self.manual_use_regular.setText("정규장")
        manual_layout.addWidget(self.manual_use_regular, 0, 1, Qt.AlignLeft | Qt.AlignVCenter)

        for index, checkbox in enumerate(self.manual_extra_checks):
            checkbox.setMinimumWidth(100)
            manual_layout.addWidget(checkbox, 0, index + 2, Qt.AlignLeft | Qt.AlignVCenter)

        self.manual_liquidation.setText("청산정책 적용")
        self.manual_liquidation.setMinimumWidth(130)

        slash_label = QLabel("/")
        slash_label.setFixedWidth(18)
        manual_layout.addWidget(slash_label, 0, 5, Qt.AlignRight | Qt.AlignVCenter)
        manual_layout.addWidget(self.manual_liquidation, 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(manual_box)

        # 4. 자동마감 설정
        auto_box, auto_layout = make_row_box("4. 자동마감 설정")
        self.auto_close_checks = [
            QCheckBox("루틴매도"),
            QCheckBox("시장가"),
            QCheckBox("현재가"),
            QCheckBox("익절/손절"),
            QCheckBox("이월"),
        ]
        self.auto_close_checks[0].setChecked(True)

        auto_layout.addWidget(self.auto_close_checks[0], 0, 1, Qt.AlignLeft | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_close_checks[1], 0, 2, Qt.AlignLeft | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_close_checks[2], 0, 3, Qt.AlignLeft | Qt.AlignVCenter)

        profit_auto_wrap = QWidget()
        profit_auto_layout = QHBoxLayout()
        profit_auto_layout.setContentsMargins(0, 0, 0, 0)
        profit_auto_layout.setSpacing(4)
        profit_auto_layout.addWidget(self.auto_close_checks[3])
        profit_auto_layout.addWidget(QLabel("+"))
        self.auto_profit.setFixedWidth(54)
        self.auto_profit.setPlaceholderText("입력")
        profit_auto_layout.addWidget(self.auto_profit)
        profit_auto_layout.addWidget(QLabel("/ -"))
        self.auto_loss.setFixedWidth(54)
        self.auto_loss.setPlaceholderText("입력")
        profit_auto_layout.addWidget(self.auto_loss)
        profit_auto_wrap.setLayout(profit_auto_layout)
        auto_layout.addWidget(profit_auto_wrap, 0, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        auto_layout.addWidget(self.auto_close_checks[4], 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(auto_box)

        # 5. 조기마감 설정
        early_box, early_layout = make_row_box("5. 조기마감 설정")
        self.early_close_checks = [
            QCheckBox("루틴매도"),
            QCheckBox("시장가"),
            QCheckBox("현재가"),
            QCheckBox("익절/손절"),
            QCheckBox("이월"),
        ]
        self.early_close_checks[1].setChecked(True)

        early_layout.addWidget(self.early_close_checks[0], 0, 1, Qt.AlignLeft | Qt.AlignVCenter)
        early_layout.addWidget(self.early_close_checks[1], 0, 2, Qt.AlignLeft | Qt.AlignVCenter)
        early_layout.addWidget(self.early_close_checks[2], 0, 3, Qt.AlignLeft | Qt.AlignVCenter)

        profit_early_wrap = QWidget()
        profit_early_layout = QHBoxLayout()
        profit_early_layout.setContentsMargins(0, 0, 0, 0)
        profit_early_layout.setSpacing(4)
        profit_early_layout.addWidget(self.early_close_checks[3])
        profit_early_layout.addWidget(QLabel("+"))
        self.early_profit.setFixedWidth(54)
        self.early_profit.setPlaceholderText("입력")
        profit_early_layout.addWidget(self.early_profit)
        profit_early_layout.addWidget(QLabel("/ -"))
        self.early_loss.setFixedWidth(54)
        self.early_loss.setPlaceholderText("입력")
        profit_early_layout.addWidget(self.early_loss)
        profit_early_wrap.setLayout(profit_early_layout)
        early_layout.addWidget(profit_early_wrap, 0, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        early_layout.addWidget(self.early_close_checks[4], 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(early_box)

        # 6. 청산설정
        liquidation_box, liquidation_layout = make_row_box("6. 청산설정")

        liquidation_start_wrap = QWidget()
        liquidation_start_layout = QHBoxLayout()
        liquidation_start_layout.setContentsMargins(0, 0, 0, 0)
        liquidation_start_layout.setSpacing(8)
        liquidation_start_layout.addWidget(QLabel("정규장 종료"))
        self.liquidation_minutes.setFixedWidth(64)
        liquidation_start_layout.addWidget(self.liquidation_minutes)
        liquidation_start_layout.addWidget(QLabel("분전"))
        liquidation_start_wrap.setLayout(liquidation_start_layout)
        liquidation_layout.addWidget(liquidation_start_wrap, 0, 1, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        # 확정 기준:
        # 5번 현재가 체크박스 열 == 6번 시장가 체크박스 열
        # 5번 익절/손절 체크박스 열 == 6번 현재가 체크박스 열
        # 6번 이월은 시장가↔현재가와 동일한 한 칸 거리만큼 오른쪽.
        if "시장가" in self.liquidation_checks:
            liquidation_layout.addWidget(self.liquidation_checks["시장가"], 0, 3, Qt.AlignLeft | Qt.AlignVCenter)
        if "현재가" in self.liquidation_checks:
            liquidation_layout.addWidget(self.liquidation_checks["현재가"], 0, 4, Qt.AlignLeft | Qt.AlignVCenter)
        if "이월" in self.liquidation_checks:
            liquidation_layout.addWidget(self.liquidation_checks["이월"], 0, 5, Qt.AlignLeft | Qt.AlignVCenter)

        layout.addWidget(liquidation_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.button(QDialogButtonBox.Save).setMinimumWidth(110)
        buttons.button(QDialogButtonBox.Cancel).setMinimumWidth(110)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def load_policy_to_widgets(self) -> None:
        regular = self.policy.get("regular_market", {}) if isinstance(self.policy.get("regular_market"), dict) else {}
        self._set_time_edit(self.regular_start, regular.get("start_time", "09:00:00"), "09:00:00")
        self._set_time_edit(self.regular_end, regular.get("end_time", "15:20:00"), "15:20:00")

        extra_sessions = self.policy.get("extra_sessions", [])
        if not isinstance(extra_sessions, list):
            extra_sessions = []
        for index in range(3):
            item = extra_sessions[index] if index < len(extra_sessions) and isinstance(extra_sessions[index], dict) else {}
            self.extra_name[index].setText(str(item.get("name", f"추가시간{index + 1}")))
            self._set_time_edit(self.extra_start[index], item.get("start_time", "09:00:00"), "09:00:00")
            self._set_time_edit(self.extra_end[index], item.get("end_time", "15:20:00"), "15:20:00")

        scheduled = self.policy.get("scheduled_operation", {}) if isinstance(self.policy.get("scheduled_operation"), dict) else {}
        self._set_time_edit(self.scheduled_start, scheduled.get("default_start_time", "09:00:00"), "09:00:00")
        self._set_time_edit(self.scheduled_end_buy, scheduled.get("default_end_buy_time", "13:30:00"), "13:30:00")
        self.scheduled_after_status.setCurrentText(str(scheduled.get("after_buy_end_status", "감시/매도")))

        manual = self.policy.get("manual_operation", {}) if isinstance(self.policy.get("manual_operation"), dict) else {}
        self.manual_use_regular.setChecked(bool(manual.get("use_regular_market", True)))
        for index, checkbox in enumerate(self.manual_extra_checks, start=1):
            checkbox.setChecked(bool(manual.get(f"use_extra_session_{index}", False)))
        self.manual_liquidation.setChecked(bool(manual.get("use_liquidation_policy", False)))

        auto = self.policy.get("auto_close", {}) if isinstance(self.policy.get("auto_close"), dict) else {}
        self.auto_close_method.setCurrentText(str(auto.get("method", "루틴매도신호")))
        self.auto_profit.setText(str(auto.get("profit_percent", "")))
        self.auto_loss.setText(str(auto.get("loss_percent", "")))

        early = self.policy.get("early_close", {}) if isinstance(self.policy.get("early_close"), dict) else {}
        self.early_close_method.setCurrentText(str(early.get("method", "시장가")))
        self.early_profit.setText(str(early.get("profit_percent", "")))
        self.early_loss.setText(str(early.get("loss_percent", "")))

        liquidation = self.policy.get("liquidation", {}) if isinstance(self.policy.get("liquidation"), dict) else {}
        liq_minutes = str(liquidation.get("minutes_before_regular_close", "5")).strip() or "5"
        if liq_minutes not in [str(value) for value in range(5, 101, 5)]:
            liq_minutes = "5"
        self.liquidation_minutes.setCurrentText(liq_minutes)
        method = str(liquidation.get("method", "이월"))
        if method not in self.liquidation_checks:
            method = "이월"
        self._select_liquidation_method(self.liquidation_checks[method])

        self._sync_combo_to_close_checkboxes()
        self.update_manual_extra_labels()
        self._update_manual_liquidation_mode()

    def _validate_profit_loss_inputs(self) -> bool:
        """익절/손절 체크 시 최소 한쪽 값 입력을 강제한다."""
        checks = [
            ("자동마감", getattr(self, "auto_close_checks", []), self.auto_profit, self.auto_loss),
            ("조기마감", getattr(self, "early_close_checks", []), self.early_profit, self.early_loss),
        ]
        for title, close_checks, profit_edit, loss_edit in checks:
            if len(close_checks) <= 3 or not close_checks[3].isChecked():
                continue
            profit_value = profit_edit.text().strip()
            loss_value = loss_edit.text().strip()
            if profit_value or loss_value:
                continue
            QMessageBox.warning(
                self,
                "입력 필요",
                f"{title} 설정에서 익절/손절을 선택했습니다.\n\n+ 입력 또는 - 입력 중 최소 1개 값을 입력하세요.",
            )
            profit_edit.setFocus()
            return False
        return True


    def build_policy_from_widgets(self) -> dict[str, object]:
        # 저장 직전에도 체크박스 선택값을 저장용 콤보값에 맞춘다.
        # accept() 외 경로에서 호출되어도 저장값이 흔들리지 않게 하기 위함이다.
        self._sync_close_checkboxes_to_combo()
        self._update_manual_liquidation_mode()
        return {
            "regular_market": {
                "start_time": self._time_edit_text(self.regular_start),
                "end_time": self._time_edit_text(self.regular_end),
            },
            "extra_sessions": [
                {
                    "name": self.extra_name[index].text().strip() or f"추가시간{index + 1}",
                    "start_time": self._time_edit_text(self.extra_start[index]),
                    "end_time": self._time_edit_text(self.extra_end[index]),
                }
                for index in range(3)
            ],
            "scheduled_operation": {
                "default_start_time": self._time_edit_text(self.scheduled_start),
                "default_end_buy_time": self._time_edit_text(self.scheduled_end_buy),
                "after_buy_end_status": self.scheduled_after_status.currentText(),
            },
            "manual_operation": {
                "use_regular_market": self.manual_use_regular.isChecked(),
                "use_extra_session_1": self.manual_extra_checks[0].isChecked(),
                "use_extra_session_2": self.manual_extra_checks[1].isChecked(),
                "use_extra_session_3": self.manual_extra_checks[2].isChecked(),
                "enabled_status": "매수/매도",
                "disabled_status": "감시/대기",
                "use_liquidation_policy": self.manual_liquidation.isChecked(),
            },
            "auto_close": {
                "method": self.auto_close_method.currentText(),
                "profit_percent": self.auto_profit.text().strip(),
                "loss_percent": self.auto_loss.text().strip(),
            },
            "early_close": {
                "method": self.early_close_method.currentText(),
                "profit_percent": self.early_profit.text().strip(),
                "loss_percent": self.early_loss.text().strip(),
            },
            "liquidation": {
                "minutes_before_regular_close": self.liquidation_minutes.currentText(),
                "method": self._current_liquidation_method(),
            },
        }




    def accept(self) -> None:
        self._sync_close_checkboxes_to_combo()
        if not self._validate_profit_loss_inputs():
            return
        policy = self.build_policy_from_widgets()
        try:
            write_operation_policy(policy)
            append_changelog("UPDATE", "operation_policy.json", "환경설정 저장")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"환경설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "저장 완료", "환경설정을 저장했습니다.")
        super().accept()
