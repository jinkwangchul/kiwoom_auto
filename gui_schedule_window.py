"""Schedule dialogs used by stock operation flows."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt


class ScheduleOperationDialog(QDialog):
    """종목별 개별 시간 예외 설정창.

    전역 기본값은 운영환경설정에서 관리하고,
    이 창의 값은 선택 종목의 개별 예외시간으로 저장한다.
    """

    def __init__(self, *args, **kwargs) -> None:
        parent = kwargs.get("parent", None)
        start_time = kwargs.get("start_time", "09:30:00")
        end_buy_time = kwargs.get("end_buy_time", "13:30:00")
        selected_count = kwargs.get("selected_count", 1)

        if args:
            if isinstance(args[0], QWidget):
                parent = args[0]
                if len(args) > 1:
                    start_time = args[1]
                if len(args) > 2:
                    end_buy_time = args[2]
                if len(args) > 3:
                    selected_count = args[3]
            else:
                start_time = args[0]
                if len(args) > 1:
                    end_buy_time = args[1]
                if len(args) > 2:
                    selected_count = args[2]

        super().__init__(parent)
        self.setWindowTitle("종목 시간 예외 설정")
        self.resize(420, 230)
        self._selected_count = int(selected_count or 1)

        start_h, start_m = self._split_hhmm(start_time, "09:30:00")
        end_h, end_m = self._split_hhmm(end_buy_time, "13:30:00")

        main_layout = QVBoxLayout()

        notice = QLabel(
            f"선택된 {self._selected_count}종목 개별시간적용"
        )
        notice.setMinimumHeight(28)
        main_layout.addWidget(notice)

        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(6)
        form_layout.setVerticalSpacing(8)

        self.start_hour_combo = self._make_hour_combo(start_h)
        self.start_minute_combo = self._make_minute_combo(start_m)
        self.end_hour_combo = self._make_hour_combo(end_h)
        self.end_minute_combo = self._make_minute_combo(end_m)

        form_layout.addWidget(QLabel("시작"), 0, 0)
        form_layout.addWidget(self.start_hour_combo, 0, 1)
        form_layout.addWidget(QLabel("시"), 0, 2)
        form_layout.addWidget(self.start_minute_combo, 0, 3)
        form_layout.addWidget(QLabel("분"), 0, 4)

        form_layout.addWidget(QLabel("매수종료"), 1, 0)
        form_layout.addWidget(self.end_hour_combo, 1, 1)
        form_layout.addWidget(QLabel("시"), 1, 2)
        form_layout.addWidget(self.end_minute_combo, 1, 3)
        form_layout.addWidget(QLabel("분"), 1, 4)
        form_layout.setColumnStretch(5, 1)
        main_layout.addLayout(form_layout)

        guide = QLabel(
            "※ 기본값은 환경설정에서 변경"
        )
        guide.setStyleSheet("color: #555555;")
        main_layout.addWidget(guide)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_apply = QPushButton("적용")
        self.btn_cancel = QPushButton("취소")
        self.btn_apply.setMinimumWidth(82)
        self.btn_cancel.setMinimumWidth(82)
        self.btn_apply.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_apply)
        button_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def _make_hour_combo(self, value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems([f"{hour:02d}" for hour in range(24)])
        combo.setCurrentText(str(value).zfill(2))
        combo.setFixedWidth(58)
        return combo

    def _make_minute_combo(self, value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems([f"{minute:02d}" for minute in range(60)])
        combo.setCurrentText(str(value).zfill(2))
        combo.setFixedWidth(58)
        return combo

    def _split_hhmm(self, value: object, default: str) -> tuple[str, str]:
        text = str(value or "").strip() or default
        parts = text.split(":")
        try:
            hour = int(parts[0]) if len(parts) >= 1 else int(default.split(":")[0])
            minute = int(parts[1]) if len(parts) >= 2 else int(default.split(":")[1])
        except Exception:
            hour = int(default.split(":")[0])
            minute = int(default.split(":")[1])
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return f"{hour:02d}", f"{minute:02d}"

    def start_time(self) -> str:
        return f"{self.start_hour_combo.currentText()}:{self.start_minute_combo.currentText()}:00"

    def end_buy_time(self) -> str:
        return f"{self.end_hour_combo.currentText()}:{self.end_minute_combo.currentText()}:00"

    def accept(self) -> None:
        start_text = self.start_time()
        end_text = self.end_buy_time()
        if start_text >= end_text:
            QMessageBox.warning(
                self,
                "시간 설정 오류",
                "시작 시간은 매수종료 시간보다 빨라야 합니다.",
            )
            return
        super().accept()

class ScheduleTradeManagementDialog(QDialog):
    """기존 스케줄매매관리창 호환용 임시 클래스."""

    def __init__(self, *args, **kwargs) -> None:
        parent = kwargs.get("parent", None)
        if parent is None and args:
            parent = args[0] if isinstance(args[0], QWidget) else None
        super().__init__(parent)
        self.setWindowTitle("운영환경설정 안내")
        self.resize(560, 260)

        layout = QVBoxLayout()
        label = QLabel(
            "스케줄매매관리는 운영환경설정으로 통합되었습니다.\n\n"
            "자동매매설정 창의 '운영환경설정' 버튼을 사용하세요."
        )
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self.setLayout(layout)
