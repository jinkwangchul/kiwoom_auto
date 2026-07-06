from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

class IndicatorFollowCommonWidgetsMixin:
    def _status_badge(self, text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumWidth(48)
        label.setStyleSheet(
            "QLabel {"
            "border: 1px solid #bdbdbd;"
            "border-radius: 2px;"
            "padding: 2px 8px;"
            "background: #f6f6f6;"
            "font-weight: bold;"
            "}"
        )
        return label

    def _section_title(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; padding: 2px 0px;")
        return label

    def _make_common_cancel_overview_controls(self, title, point_label_text, prefix):
        box = QGroupBox(title)
        box.setStyleSheet(
            "QGroupBox { font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(10)

        def make_line(text, width, align=Qt.AlignRight):
            line = QLineEdit()
            line.setText(text)
            line.setFixedWidth(width)
            line.setFixedHeight(30)
            line.setAlignment(align)
            line.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(30)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        def add_row():
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(4)
            layout.addLayout(row)
            return row

        def bind_row_enabled(check, widgets):
            def sync_row_enabled(enabled):
                for widget in widgets:
                    widget.setEnabled(enabled)
            check.toggled.connect(sync_row_enabled)
            sync_row_enabled(check.isChecked())

        def sync_direction_compare_combo(direction_combo, compare_combo):
            direction = direction_combo.currentText()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]
            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = compare_combo.findText(item_text)
                if index >= 0:
                    compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if compare_combo.currentText() not in visible_items:
                compare_combo.setCurrentText("이내" if direction == "상하" else "이하")
            compare_combo.setEnabled(True)

        row = add_row()
        point_check = QCheckBox()
        point_check.setChecked(True)
        setattr(self, f"{prefix}_cancel_point_check", point_check)
        row.addWidget(point_check)

        point_label = QLabel("매도 미체결 발생 시" if prefix == "sell" else "매수 미체결 발생 시")
        point_mode_combo = make_combo(["매회", "일괄"], "매회", 64)
        point_seconds_line = make_line("20", 38)
        point_unit_combo = make_combo(["분", "초", "봉"], "초", 54)
        point_tail_label = QLabel("후 주문취소")
        point_logic_combo = make_combo(["AND", "OR"], "AND", 68)

        setattr(self, f"{prefix}_cancel_point_mode_combo", point_mode_combo)
        setattr(self, f"{prefix}_cancel_point_seconds_line", point_seconds_line)
        setattr(self, f"{prefix}_cancel_point_unit_combo", point_unit_combo)
        setattr(self, f"{prefix}_cancel_point_logic_combo", point_logic_combo)

        row.addWidget(point_label)
        row.addWidget(point_mode_combo)
        row.addWidget(QLabel("기준"))
        row.addWidget(point_seconds_line)
        row.addWidget(point_unit_combo)
        row.addWidget(point_tail_label)
        row.addStretch(1)
        row.addWidget(point_logic_combo)

        bind_row_enabled(
            point_check,
            [
                point_label,
                point_mode_combo,
                point_seconds_line,
                point_unit_combo,
                point_tail_label,
                point_logic_combo,
            ],
        )

        row = add_row()
        price_gap_check = QCheckBox()
        price_gap_check.setChecked(False)
        setattr(self, f"{prefix}_cancel_price_gap_check", price_gap_check)
        row.addWidget(price_gap_check)

        price_gap_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 84)
        price_gap_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 84)
        price_gap_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 64)
        price_gap_value_line = make_line("0.15", 44)
        price_gap_unit_label = QLabel("%")
        price_gap_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이내", 72)
        price_gap_cancel_label = QLabel("전량주문취소" if prefix == "sell" else "매수주문취소")
        price_gap_logic_combo = make_combo(["AND", "OR"], "AND", 68)

        setattr(self, f"{prefix}_cancel_price_gap_left_combo", price_gap_left_combo)
        setattr(self, f"{prefix}_cancel_price_gap_right_combo", price_gap_right_combo)
        setattr(self, f"{prefix}_cancel_price_gap_direction_combo", price_gap_direction_combo)
        setattr(self, f"{prefix}_cancel_price_gap_value_line", price_gap_value_line)
        setattr(self, f"{prefix}_cancel_price_gap_compare_combo", price_gap_compare_combo)
        setattr(self, f"{prefix}_cancel_price_gap_logic_combo", price_gap_logic_combo)

        row.addWidget(price_gap_left_combo)
        row.addWidget(QLabel("대비"))
        row.addWidget(price_gap_right_combo)
        row.addWidget(price_gap_direction_combo)
        row.addWidget(price_gap_value_line)
        row.addWidget(price_gap_unit_label)
        row.addWidget(price_gap_compare_combo)
        row.addWidget(price_gap_cancel_label)
        row.addStretch(1)
        row.addWidget(price_gap_logic_combo)

        price_gap_direction_combo.currentTextChanged.connect(
            lambda _: sync_direction_compare_combo(price_gap_direction_combo, price_gap_compare_combo)
        )
        sync_direction_compare_combo(price_gap_direction_combo, price_gap_compare_combo)

        bind_row_enabled(
            price_gap_check,
            [
                price_gap_left_combo,
                price_gap_right_combo,
                price_gap_direction_combo,
                price_gap_value_line,
                price_gap_unit_label,
                price_gap_compare_combo,
                price_gap_cancel_label,
                price_gap_logic_combo,
            ],
        )

        return box

    def _make_buy_complete_policy_overview_controls(self):
        box = QGroupBox("완료정책 세부설정")
        box.setStyleSheet(
            "QGroupBox { font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(10)

        return box

    def _make_basic_settings_row_for_edit_tab(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel("기본설정")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #2E6B3A; padding: 1px 4px; border: 1px solid #000000; border-radius: 2px; background: transparent;")
        row.addWidget(title)

        title_sep = QLabel("|")
        title_sep.setStyleSheet("font-size: 13pt; font-weight: bold; color: #000000; padding: 2px 1px;")
        row.addWidget(title_sep)

        def make_label(text):
            label = QLabel(text)
            label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
            return label

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(30)
            combo.setStyleSheet("font-size: 9pt;")
            return combo

        row.addWidget(make_label("신호검출기준"))
        row.addWidget(make_combo(["1", "3", "5", "10", "15", "30", "직접입력"], "5", 88))
        row.addWidget(make_label("분봉 |"))
        row.addWidget(make_label("중복신호처리"))
        row.addWidget(make_combo(["후행신호 우선", "선행신호 우선"], "후행신호 우선", 132))
        row.addWidget(make_label("|"))
        row.addWidget(make_label("오류발생"))
        row.addWidget(make_combo(["매매중지", "매매지속"], "매매중지", 96))
        row.addStretch(1)
        return row

    def _make_overview_text(self, title, body):
        box = QGroupBox(title)
        box.setStyleSheet(
            "QGroupBox { font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        layout = QVBoxLayout(box)

        for raw_line in body.splitlines():
            text = raw_line.replace("☑", "").strip()
            if not text:
                continue
            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.setEnabled(True)
            cb.setStyleSheet("font-weight: normal;")
            layout.addWidget(cb)

        return box

    def _make_panel_card(self, title, status, callback=None):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setMinimumHeight(86)

        layout = QVBoxLayout(frame)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_label = QLabel(status)
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")

        layout.addWidget(title_label)
        layout.addWidget(status_label)

        button = None
        if callback is not None:
            button = QPushButton("열기")
            button.clicked.connect(callback)
            layout.addWidget(button)
        else:
            spacer = QLabel("")
            layout.addWidget(spacer)

        return {
            "frame": frame,
            "title": title_label,
            "status": status_label,
            "button": button,
        }

    def _set_card_status(self, card, text, kind="normal"):
        card["status"].setText(text)

        if kind == "active":
            color = "#0a7a2f"
        elif kind == "inactive":
            color = "#777"
        elif kind == "locked":
            color = "#a65f00"
        elif kind == "error":
            color = "#b00020"
        else:
            color = "#333"

        card["status"].setStyleSheet(
            f"font-size: 16px; font-weight: bold; padding: 4px; color: {color};"
        )

    def _readonly_line(self):
        line = QLineEdit()
        line.setReadOnly(True)
        line.setFrame(False)
        line.setStyleSheet("background: transparent; border: none; padding: 1px;")
        return line

    def _locked_checkbox(self, text=""):
        cb = QCheckBox(text)
        cb.setEnabled(False)
        return cb
