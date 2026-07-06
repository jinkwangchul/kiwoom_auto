from PyQt5.QtCore import Qt, QEvent
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

class IndicatorFollowSellControlsMixin:
    def _make_sell_signal_condition_1_overview_controls(self):
        box = QGroupBox("적용필터")
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

        self._buy_filter_label_index = 0
        filter_row_entries = []

        def _sync_filter_row_states():
            for index, entry in enumerate(filter_row_entries):
                row_checked = entry["check"].isChecked()
                for widget in entry["widgets"]:
                    widget.setEnabled(row_checked)

                logic_combo = entry["logic"]
                if logic_combo is not None:
                    # 행 우측 연산자는 현재 행과 뒤쪽의 활성 필터를 연결하는 의미다.
                    # 바로 아래 행이 꺼져 있어도 그 아래 행 중 하나라도 체크되어 있으면 활성화한다.
                    has_checked_later_row = any(
                        later_entry["check"].isChecked()
                        for later_entry in filter_row_entries[index + 1:]
                    )
                    logic_combo.setEnabled(row_checked and has_checked_later_row)

        def add_filter_row(widgets, logic="AND", checked=True, show_logic=True):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)

            # 신호검출조건 행은 사용 여부 체크박스를 유지한다.
            # 필터 앞 A/B/C 문자 라벨은 표시하지 않고, 마지막 행에는 우측 연산자를 두지 않는다.
            row_check = QCheckBox()
            row_check.setChecked(checked)
            row_check.setFixedWidth(24)
            row.addWidget(row_check)

            content_widgets = list(widgets)
            for widget in content_widgets:
                row.addWidget(widget)

            row.addStretch(1)

            logic_combo = None
            if show_logic:
                logic_combo = make_combo(["AND", "OR", "NOT"], logic, 62)
                row.addWidget(logic_combo)

            filter_row_entries.append({
                "check": row_check,
                "widgets": content_widgets,
                "logic": logic_combo,
            })
            row_check.toggled.connect(lambda _checked=False: _sync_filter_row_states())

            layout.addLayout(row)
            _sync_filter_row_states()

            return row_check

        # OCR 추세 감지
        ocr_sign_combo = make_combo(["+", "-"], "+", 52)
        ocr_value_line = make_line("1", 46)
        ocr_compare_combo = make_combo(["이상", "이하"], "이상", 66)
        ocr_direction_combo = make_combo(["상승", "하락"], "하락", 66)
        ocr_convert_line = make_line("0", 46)
        ocr_check = add_filter_row([
            QLabel("OCR"),
            ocr_sign_combo,
            ocr_value_line,
            ocr_compare_combo,
            ocr_direction_combo,
            QLabel("전환"),
            ocr_convert_line,
            QLabel("봉"),
        ], "AND", True)
        self.sell_signal_condition_a_ocr_check = ocr_check
        self.sell_signal_condition_a_ocr_sign_combo = ocr_sign_combo
        self.sell_signal_condition_a_ocr_value_line = ocr_value_line
        self.sell_signal_condition_a_ocr_compare_combo = ocr_compare_combo
        self.sell_signal_condition_a_ocr_direction_combo = ocr_direction_combo
        self.sell_signal_condition_a_ocr_convert_line = ocr_convert_line
        self.sell_signal_condition_a_ocr_logic_combo = filter_row_entries[-1]["logic"]

        # [주문가/현재가/평단가]에 [주문가/현재가/평단가] 조건
        gap_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 64)
        gap_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이내", 66)

        def sync_gap_compare_combo():
            direction = gap_direction_combo.currentText()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]
            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = gap_compare_combo.findText(item_text)
                if index >= 0:
                    gap_compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if gap_compare_combo.currentText() not in visible_items:
                gap_compare_combo.setCurrentText("이내" if direction == "상하" else "이하")

        gap_direction_combo.currentTextChanged.connect(lambda _: sync_gap_compare_combo())
        sync_gap_compare_combo()

        gap_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 78)
        gap_right_combo = make_combo(["주문가", "현재가", "평단가"], "평단가", 78)
        gap_value_line = make_line("0.25", 44)
        gap_check = add_filter_row([
            gap_left_combo,
            QLabel("대비"),
            gap_right_combo,
            gap_direction_combo,
            gap_value_line,
            QLabel("%"),
            gap_compare_combo,
        ], "AND", True)
        self.sell_signal_condition_a_gap_check = gap_check
        self.sell_signal_condition_a_gap_left_combo = gap_left_combo
        self.sell_signal_condition_a_gap_right_combo = gap_right_combo
        self.sell_signal_condition_a_gap_direction_combo = gap_direction_combo
        self.sell_signal_condition_a_gap_value_line = gap_value_line
        self.sell_signal_condition_a_gap_compare_combo = gap_compare_combo
        self.sell_signal_condition_a_gap_logic_combo = filter_row_entries[-1]["logic"]

        # RSI
        rsi_period_line = make_line("14", 38)
        rsi_value_line = make_line("45", 42)
        rsi_compare_combo = make_combo(["이하", "이상"], "이하", 76)
        rsi_check = add_filter_row([
            QLabel("RSI기간"),
            rsi_period_line,
            rsi_value_line,
            rsi_compare_combo,
        ], "AND", True, False)
        self.sell_signal_condition_a_rsi_check = rsi_check
        self.sell_signal_condition_a_rsi_period_line = rsi_period_line
        self.sell_signal_condition_a_rsi_value_line = rsi_value_line
        self.sell_signal_condition_a_rsi_compare_combo = rsi_compare_combo

        return box

    def _make_sell_signal_condition_2_overview_controls(self):
        box = QGroupBox("적용필터")
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
            line.setFixedHeight(32)
            line.setAlignment(align)
            line.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(32)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        self._buy_filter_label_index = 0
        filter_row_entries = []

        def _sync_filter_row_states():
            for index, entry in enumerate(filter_row_entries):
                row_checked = entry["check"].isChecked()
                for widget in entry["widgets"]:
                    widget.setEnabled(row_checked)

                logic_combo = entry["logic"]
                if logic_combo is not None:
                    # 행 우측 연산자는 현재 행과 뒤쪽의 활성 필터를 연결하는 의미다.
                    # 바로 아래 행이 꺼져 있어도 그 아래 행 중 하나라도 체크되어 있으면 활성화한다.
                    has_checked_later_row = any(
                        later_entry["check"].isChecked()
                        for later_entry in filter_row_entries[index + 1:]
                    )
                    logic_combo.setEnabled(row_checked and has_checked_later_row)

        def add_filter_row(widgets, logic="AND", checked=True, show_logic=True):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)

            # 신호검출조건 행은 사용 여부 체크박스를 유지한다.
            # 필터 앞 A/B/C 문자 라벨은 표시하지 않고, 마지막 행에는 우측 연산자를 두지 않는다.
            row_check = QCheckBox()
            row_check.setChecked(checked)
            row_check.setFixedWidth(24)
            row.addWidget(row_check)

            content_widgets = list(widgets)
            for widget in content_widgets:
                row.addWidget(widget)

            row.addStretch(1)

            logic_combo = None
            if show_logic:
                logic_combo = make_combo(["AND", "OR", "NOT"], logic, 62)
                row.addWidget(logic_combo)

            filter_row_entries.append({
                "check": row_check,
                "widgets": content_widgets,
                "logic": logic_combo,
            })
            row_check.toggled.connect(lambda _checked=False: _sync_filter_row_states())

            layout.addLayout(row)
            _sync_filter_row_states()

            return row_check

        gap_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 64)
        gap_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이내", 66)

        def sync_gap_compare_combo():
            direction = gap_direction_combo.currentText()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]
            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = gap_compare_combo.findText(item_text)
                if index >= 0:
                    gap_compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if gap_compare_combo.currentText() not in visible_items:
                gap_compare_combo.setCurrentText("이내" if direction == "상하" else "이하")

        gap_direction_combo.currentTextChanged.connect(lambda _: sync_gap_compare_combo())
        sync_gap_compare_combo()

        price_box_direction_combo = make_combo(["상향", "하향"], "하향", 64)
        price_box_value_line = make_line("0.1", 44)
        price_box_compare_combo = make_combo(["이상", "이하"], "이상", 66)

        price_box_check = add_filter_row([
            QLabel("가격박스"),
            price_box_direction_combo,
            price_box_value_line,
            QLabel("%"),
            price_box_compare_combo,
        ], "AND", True)
        self.sell_signal_condition_b_price_box_check = price_box_check
        self.sell_signal_condition_b_price_box_direction_combo = price_box_direction_combo
        self.sell_signal_condition_b_price_box_value_line = price_box_value_line
        self.sell_signal_condition_b_price_box_compare_combo = price_box_compare_combo
        self.sell_signal_condition_b_price_box_logic_combo = filter_row_entries[-1]["logic"]

        bollinger_direction_combo = make_combo(["상향", "하향"], "하향", 64)
        bollinger_value_line = make_line("0.1", 44)
        bollinger_compare_combo = make_combo(["이상", "이하"], "이상", 66)

        bollinger_check = add_filter_row([
            QLabel("볼린저밴드"),
            bollinger_direction_combo,
            bollinger_value_line,
            QLabel("%"),
            bollinger_compare_combo,
        ], "AND", True)
        self.sell_signal_condition_b_bollinger_check = bollinger_check
        self.sell_signal_condition_b_bollinger_direction_combo = bollinger_direction_combo
        self.sell_signal_condition_b_bollinger_value_line = bollinger_value_line
        self.sell_signal_condition_b_bollinger_compare_combo = bollinger_compare_combo
        self.sell_signal_condition_b_bollinger_logic_combo = filter_row_entries[-1]["logic"]

        gap_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 78)
        gap_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 78)
        gap_value_line = make_line("0.25", 44)
        gap_check = add_filter_row([
            gap_left_combo,
            QLabel("대비"),
            gap_right_combo,
            gap_direction_combo,
            gap_value_line,
            QLabel("%"),
            gap_compare_combo,
        ], "AND", True, False)
        self.sell_signal_condition_b_gap_check = gap_check
        self.sell_signal_condition_b_gap_left_combo = gap_left_combo
        self.sell_signal_condition_b_gap_right_combo = gap_right_combo
        self.sell_signal_condition_b_gap_direction_combo = gap_direction_combo
        self.sell_signal_condition_b_gap_value_line = gap_value_line
        self.sell_signal_condition_b_gap_compare_combo = gap_compare_combo

        return box

    def _make_sell_signal_condition_3_overview_controls(self):
        box = QGroupBox("적용필터")
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
            line.setFixedHeight(32)
            line.setAlignment(align)
            line.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(32)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        self._buy_filter_label_index = 0
        filter_row_entries = []

        def _sync_filter_row_states():
            for index, entry in enumerate(filter_row_entries):
                row_checked = entry["check"].isChecked()
                for widget in entry["widgets"]:
                    widget.setEnabled(row_checked)

                logic_combo = entry["logic"]
                if logic_combo is not None:
                    # 행 우측 연산자는 현재 행과 뒤쪽의 활성 필터를 연결하는 의미다.
                    # 바로 아래 행이 꺼져 있어도 그 아래 행 중 하나라도 체크되어 있으면 활성화한다.
                    has_checked_later_row = any(
                        later_entry["check"].isChecked()
                        for later_entry in filter_row_entries[index + 1:]
                    )
                    logic_combo.setEnabled(row_checked and has_checked_later_row)

        def add_filter_row(widgets, logic="AND", checked=True, show_logic=True):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)

            # 신호검출조건 행은 사용 여부 체크박스를 유지한다.
            # 필터 앞 A/B/C 문자 라벨은 표시하지 않고, 마지막 행에는 우측 연산자를 두지 않는다.
            row_check = QCheckBox()
            row_check.setChecked(checked)
            row_check.setFixedWidth(24)
            row.addWidget(row_check)

            content_widgets = list(widgets)
            for widget in content_widgets:
                row.addWidget(widget)

            row.addStretch(1)

            logic_combo = None
            if show_logic:
                logic_combo = make_combo(["AND", "OR", "NOT"], logic, 62)
                row.addWidget(logic_combo)

            filter_row_entries.append({
                "check": row_check,
                "widgets": content_widgets,
                "logic": logic_combo,
            })
            row_check.toggled.connect(lambda _checked=False: _sync_filter_row_states())

            layout.addLayout(row)
            _sync_filter_row_states()

            return row_check

        gap_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 64)
        gap_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이내", 66)

        def sync_gap_compare_combo():
            direction = gap_direction_combo.currentText()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]
            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = gap_compare_combo.findText(item_text)
                if index >= 0:
                    gap_compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if gap_compare_combo.currentText() not in visible_items:
                gap_compare_combo.setCurrentText("이내" if direction == "상하" else "이하")

        gap_direction_combo.currentTextChanged.connect(lambda _: sync_gap_compare_combo())
        sync_gap_compare_combo()

        gap_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 78)
        gap_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 78)
        gap_value_line = make_line("0.25", 44)
        gap_check = add_filter_row([
            gap_left_combo,
            QLabel("대비"),
            gap_right_combo,
            gap_direction_combo,
            gap_value_line,
            QLabel("%"),
            gap_compare_combo,
        ], "AND", True)
        self.sell_signal_condition_c_gap_check = gap_check
        self.sell_signal_condition_c_gap_left_combo = gap_left_combo
        self.sell_signal_condition_c_gap_right_combo = gap_right_combo
        self.sell_signal_condition_c_gap_direction_combo = gap_direction_combo
        self.sell_signal_condition_c_gap_value_line = gap_value_line
        self.sell_signal_condition_c_gap_compare_combo = gap_compare_combo
        self.sell_signal_condition_c_gap_logic_combo = filter_row_entries[-1]["logic"]

        macd_kind_combo = make_combo(["MACD선", "시그널선"], "MACD선", 120)
        macd_sign_combo = make_combo(["-", "+"], "-", 60)
        macd_value_line = make_line("1.0", 60)
        macd_compare_combo = make_combo(["이하", "이상"], "이하", 76)

        def _sync_macd_sign_combo():
            value = macd_value_line.text().strip()
            try:
                numeric_value = float(value)
            except ValueError:
                numeric_value = None
            macd_sign_combo.setEnabled(numeric_value is None or numeric_value != 0.0)

        macd_value_line.textChanged.connect(_sync_macd_sign_combo)
        _sync_macd_sign_combo()

        macd_check = add_filter_row([
            macd_kind_combo,
            macd_sign_combo,
            macd_value_line,
            macd_compare_combo,
        ], "AND", True)
        self.sell_signal_condition_c_macd_check = macd_check
        self.sell_signal_condition_c_macd_kind_combo = macd_kind_combo
        self.sell_signal_condition_c_macd_sign_combo = macd_sign_combo
        self.sell_signal_condition_c_macd_value_line = macd_value_line
        self.sell_signal_condition_c_macd_compare_combo = macd_compare_combo
        self.sell_signal_condition_c_macd_logic_combo = filter_row_entries[-1]["logic"]

        ma_period_items = ["5", "10", "20", "60", "120", "240"]
        array_first_period_combo = make_combo(ma_period_items, "5", 58)
        array_first_compare_combo = make_combo([">", "<", ">=", "<="], ">", 64)
        array_second_period_combo = make_combo(ma_period_items, "20", 58)
        array_second_compare_combo = make_combo([">", "<", ">=", "<="], ">", 64)
        array_third_period_combo = make_combo(ma_period_items, "60", 58)
        array_check = add_filter_row([
            QLabel("배열"),
            array_first_period_combo,
            array_first_compare_combo,
            array_second_period_combo,
            array_second_compare_combo,
            array_third_period_combo,
        ], "AND", True, False)
        self.sell_signal_condition_c_array_check = array_check
        self.sell_signal_condition_c_array_first_period_combo = array_first_period_combo
        self.sell_signal_condition_c_array_first_compare_combo = array_first_compare_combo
        self.sell_signal_condition_c_array_second_period_combo = array_second_period_combo
        self.sell_signal_condition_c_array_second_compare_combo = array_second_compare_combo
        self.sell_signal_condition_c_array_third_period_combo = array_third_period_combo

        return box

    def _make_sell_method_overview_controls(self):
        box = QGroupBox("매도방식")
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
            line.setFixedHeight(32)
            line.setAlignment(align)
            line.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(32)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        def add_row(indent=0):
            row = QHBoxLayout()
            row.setContentsMargins(indent, 2, 0, 2)
            row.setSpacing(4)
            layout.addLayout(row)
            return row

        row = add_row()
        self.sell_method_single_check = QCheckBox("단일호가")
        self.sell_method_single_check.setChecked(True)
        self.sell_method_single_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_method_single_check)
        single_check = self.sell_method_single_check
        row.addStretch(1)

        row = add_row()
        self.sell_method_multi_check = QCheckBox("상향")
        self.sell_method_multi_check.setChecked(False)
        self.sell_method_multi_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_method_multi_check)
        multi_check = self.sell_method_multi_check

        self.sell_method_multi_up_line = make_line("4", 38)
        self.sell_method_multi_down_line = make_line("2", 38)
        multi_up_line = self.sell_method_multi_up_line
        multi_down_line = self.sell_method_multi_down_line
        self.sell_method_multi_total_label = QLabel("합계 7호가")

        multi_total_label = self.sell_method_multi_total_label

        row.addWidget(multi_up_line)
        row.addWidget(QLabel("호가 / 기준 1호가 / 하향"))
        row.addWidget(multi_down_line)
        row.addWidget(QLabel("호가 |"))
        row.addWidget(multi_total_label)
        row.addStretch(1)

        def _sync_sell_multi_hoga_total():
            try:
                up = int(multi_up_line.text().strip())
            except ValueError:
                up = 0
            try:
                down = int(multi_down_line.text().strip())
            except ValueError:
                down = 0
            multi_total_label.setText(f"합계 {up + 1 + down}호가")

        multi_up_line.textChanged.connect(_sync_sell_multi_hoga_total)
        multi_down_line.textChanged.connect(_sync_sell_multi_hoga_total)
        _sync_sell_multi_hoga_total()

        row = add_row()
        point_title_label = QLabel("다중지점")
        point_title_label.setStyleSheet("font-size: 8pt; font-weight: bold;")
        row.addWidget(point_title_label)
        row.addStretch(1)

        child_indent = 18

        row = add_row(child_indent)
        self.sell_method_time_point_check = QCheckBox("시간")
        self.sell_method_time_point_check.setChecked(False)
        self.sell_method_time_point_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_method_time_point_check)
        time_point_check = self.sell_method_time_point_check
        row.addWidget(make_line("30", 38))
        row.addWidget(make_combo(["분", "초", "봉"], "초", 58))
        row.addWidget(make_combo(["이내", "간격"], "이내", 72))
        row.addWidget(make_line("3", 38))
        row.addWidget(QLabel("회"))
        self.sell_method_time_point_order_combo = make_combo(["주문가", "현재가"], "주문가", 96)
        row.addWidget(self.sell_method_time_point_order_combo)
        row.addStretch(1)

        row = add_row(child_indent)
        self.sell_method_avg_point_check = QCheckBox("")
        self.sell_method_avg_point_check.setChecked(False)
        self.sell_method_avg_point_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_method_avg_point_check)
        avg_point_check = self.sell_method_avg_point_check
        self.sell_method_avg_point_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 86)
        row.addWidget(self.sell_method_avg_point_left_combo)
        row.addWidget(QLabel("대비"))
        self.sell_method_avg_point_right_combo = make_combo(["주문가", "현재가", "평단가"], "평단가", 86)
        row.addWidget(self.sell_method_avg_point_right_combo)
        self.sell_method_avg_point_basis_combo = self.sell_method_avg_point_left_combo
        self.sell_method_avg_point_direction_combo = make_combo(["상향", "하향", "상하"], "하향", 72)
        row.addWidget(self.sell_method_avg_point_direction_combo)
        avg_point_direction_combo = self.sell_method_avg_point_direction_combo
        self.sell_method_avg_point_value_line = make_line("0.15", 48)
        row.addWidget(self.sell_method_avg_point_value_line)
        row.addWidget(QLabel("%"))
        self.sell_method_avg_point_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이하", 72)
        row.addWidget(self.sell_method_avg_point_compare_combo)
        avg_point_compare_combo = self.sell_method_avg_point_compare_combo
        row.addWidget(QLabel("/"))
        self.sell_method_avg_point_count_line = make_line("3", 38)
        row.addWidget(self.sell_method_avg_point_count_line)
        row.addWidget(QLabel("회"))
        row.addStretch(1)

        row = add_row(child_indent + 22)
        self.sell_method_last_market_sell_check = QCheckBox("마지막회")
        self.sell_method_last_market_sell_check.setChecked(False)
        self.sell_method_last_market_sell_check.setStyleSheet("font-weight: normal;")
        self.sell_method_last_order_type_combo = make_combo(["시장가", "현재가"], "시장가", 86)
        last_market_sell_check = self.sell_method_last_market_sell_check
        last_order_type_combo = self.sell_method_last_order_type_combo
        row.addWidget(last_market_sell_check)
        row.addWidget(last_order_type_combo)
        row.addStretch(1)

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

        avg_point_direction_combo.currentTextChanged.connect(
            lambda _: sync_direction_compare_combo(
                avg_point_direction_combo,
                avg_point_compare_combo,
            )
        )
        sync_direction_compare_combo(
            avg_point_direction_combo,
            avg_point_compare_combo,
        )

        exclusive_guard = {"active": False}

        def sync_price_method(source):
            if exclusive_guard["active"]:
                return
            exclusive_guard["active"] = True
            try:
                if source is single_check and source.isChecked():
                    multi_check.setChecked(False)
                elif source is multi_check and source.isChecked():
                    single_check.setChecked(False)
                elif not single_check.isChecked() and not multi_check.isChecked():
                    single_check.setChecked(True)
            finally:
                exclusive_guard["active"] = False

        single_check.toggled.connect(lambda _: sync_price_method(single_check))
        multi_check.toggled.connect(lambda _: sync_price_method(multi_check))

        def sync_point_method(source):
            if exclusive_guard["active"]:
                return
            exclusive_guard["active"] = True
            try:
                if source is time_point_check and source.isChecked():
                    avg_point_check.setChecked(False)
                elif source is avg_point_check and source.isChecked():
                    time_point_check.setChecked(False)
            finally:
                exclusive_guard["active"] = False

        time_point_check.toggled.connect(lambda _: sync_point_method(time_point_check))
        avg_point_check.toggled.connect(lambda _: sync_point_method(avg_point_check))

        def sync_last_market_sell_enabled():
            enabled = (
                time_point_check.isChecked()
                or avg_point_check.isChecked()
            )
            last_market_sell_check.setEnabled(enabled)
            last_order_type_combo.setEnabled(enabled)
            if not enabled:
                last_market_sell_check.setChecked(False)

        time_point_check.toggled.connect(lambda _: sync_last_market_sell_enabled())
        avg_point_check.toggled.connect(lambda _: sync_last_market_sell_enabled())
        sync_last_market_sell_enabled()

        return box

    def _make_sell_scenario_overview_controls(self):
        """
        매도 하단 설정 A/B/C UI.
        - 기존 3분할 레이아웃 유지.
        - 각 박스 내부는 섹션 단위로 묶어 표시한다.
        - 실제 저장/주문 로직 연결 없음.
        """
        container = QWidget()
        layout = QGridLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(0)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)

        def make_combo(items, current=None, width=92):
            combo = QComboBox()
            combo.addItems(items)
            if current is not None:
                combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(30)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        def make_line(text, width=34, align=Qt.AlignRight):
            line = QLineEdit()
            line.setText(text)
            line.setFixedWidth(width)
            line.setFixedHeight(30)
            line.setAlignment(align)
            line.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
            return line

        def make_label(text, width=None, align=Qt.AlignVCenter | Qt.AlignLeft):
            label = QLabel(text)
            label.setFixedHeight(30)
            label.setAlignment(align)
            label.setStyleSheet("font-size: 8pt;")
            if width is not None:
                label.setFixedWidth(width)
            return label

        def add_section_header(parent_layout, text):
            label = QLabel(text)
            label.setFixedHeight(24)
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            label.setStyleSheet(
                "font-size: 9pt; font-weight: bold; color: #003366; "
                "padding: 0px 0px 0px 0px;"
            )
            parent_layout.addWidget(label)
            return label

        def add_hoga_total(up_line, down_line, total_label):
            def sync_total():
                try:
                    up = int(up_line.text().strip())
                except ValueError:
                    up = 0
                try:
                    down = int(down_line.text().strip())
                except ValueError:
                    down = 0
                total_label.setText(f"| {up + 1 + down}호가")
            up_line.textChanged.connect(sync_total)
            down_line.textChanged.connect(sync_total)
            sync_total()

        def sync_direction_compare(direction_combo, compare_combo):
            direction = direction_combo.currentText()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]
            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = compare_combo.findText(item_text)
                if index >= 0:
                    compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if compare_combo.currentText() not in visible_items:
                compare_combo.setCurrentText("이내" if direction == "상하" else "이상")
            compare_combo.setEnabled(True)

        def add_perform_1(parent_layout, prefix):
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 0, 0)
            row.setSpacing(4)
            parent_layout.addLayout(row)

            title_combo = make_combo(["단일호가", "다중호가"], "단일호가", 116)
            row.addWidget(title_combo)
            row.addWidget(make_label("|", 8, Qt.AlignCenter))

            single_widget = QWidget()
            single_layout = QHBoxLayout(single_widget)
            single_layout.setContentsMargins(0, 0, 0, 0)
            single_layout.setSpacing(4)
            single_combo = make_combo(["주문가", "시장가"], "주문가", 100)
            single_layout.addWidget(single_combo)
            single_layout.addStretch(1)
            row.addWidget(single_widget)

            multi_widget = QWidget()
            multi_layout = QHBoxLayout(multi_widget)
            multi_layout.setContentsMargins(0, 0, 0, 0)
            multi_layout.setSpacing(4)
            up_line = make_line("3", 34)
            down_line = make_line("0", 34)
            total_label = make_label("| 4호가", 70)
            multi_layout.addWidget(make_label("상향", 42))
            multi_layout.addWidget(up_line)
            multi_layout.addWidget(make_label("/ 주문가 1 / 하향", 132))
            multi_layout.addWidget(down_line)
            multi_layout.addWidget(total_label)
            multi_layout.addStretch(1)
            row.addWidget(multi_widget)
            add_hoga_total(up_line, down_line, total_label)

            def sync_detail():
                is_multi = title_combo.currentText() == "다중호가"
                single_widget.setVisible(not is_multi)
                multi_widget.setVisible(is_multi)

            title_combo.currentTextChanged.connect(lambda _: sync_detail())
            sync_detail()
            row.addStretch(1)

            setattr(self, f"sell_{prefix}_perform1_title_combo", title_combo)
            setattr(self, f"sell_{prefix}_perform1_single_combo", single_combo)
            setattr(self, f"sell_{prefix}_perform1_multi_up_line", up_line)
            setattr(self, f"sell_{prefix}_perform1_multi_down_line", down_line)

        def add_perform_2(parent_layout, prefix):
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 0, 0)
            row.setSpacing(4)
            parent_layout.addLayout(row)

            title_combo = make_combo(["선택없음", "다중시간", "다중비율"], "다중시간", 116)
            row.addWidget(title_combo)
            row.addWidget(make_label("|", 8, Qt.AlignCenter))

            none_widget = QWidget()
            none_layout = QHBoxLayout(none_widget)
            none_layout.setContentsMargins(0, 0, 0, 0)
            none_layout.setSpacing(4)
            none_layout.addWidget(make_label("-", 20, Qt.AlignCenter))
            none_layout.addStretch(1)
            row.addWidget(none_widget)

            time_widget = QWidget()
            time_layout = QHBoxLayout(time_widget)
            time_layout.setContentsMargins(0, 0, 0, 0)
            time_layout.setSpacing(4)
            time_value = make_line("30", 34)
            time_unit = make_combo(["분", "초", "봉"], "초", 60)
            time_range = make_combo(["이내", "간격"], "이내", 76)
            time_count = make_line("3", 30)
            time_order = make_combo(["주문가", "현재가"], "주문가", 92)
            time_layout.addWidget(time_value)
            time_layout.addWidget(time_unit)
            time_layout.addWidget(time_range)
            time_layout.addWidget(time_count)
            time_layout.addWidget(make_label("회", 18))
            time_layout.addWidget(time_order)
            time_layout.addStretch(1)
            row.addWidget(time_widget)

            ratio_widget = QWidget()
            ratio_layout = QHBoxLayout(ratio_widget)
            ratio_layout.setContentsMargins(0, 0, 0, 0)
            ratio_layout.setSpacing(4)
            left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
            right_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
            direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
            value_line = make_line("0.15", 46)
            compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
            count_line = make_line("3", 30)
            ratio_layout.addWidget(left_combo)
            ratio_layout.addWidget(make_label("대비", 36))
            ratio_layout.addWidget(right_combo)
            ratio_layout.addWidget(direction_combo)
            ratio_layout.addWidget(value_line)
            ratio_layout.addWidget(make_label("%", 14))
            ratio_layout.addWidget(compare_combo)
            ratio_layout.addWidget(make_label("/", 8, Qt.AlignCenter))
            ratio_layout.addWidget(count_line)
            ratio_layout.addWidget(make_label("회", 18))
            ratio_layout.addStretch(1)
            row.addWidget(ratio_widget)

            def sync_ratio_compare():
                sync_direction_compare(direction_combo, compare_combo)

            def sync_mode():
                mode = title_combo.currentText()
                none_widget.setVisible(mode == "선택없음")
                time_widget.setVisible(mode == "다중시간")
                ratio_widget.setVisible(mode == "다중비율")

            direction_combo.currentTextChanged.connect(lambda _: sync_ratio_compare())
            title_combo.currentTextChanged.connect(lambda _: sync_mode())
            sync_ratio_compare()
            sync_mode()
            row.addStretch(1)

            setattr(self, f"sell_{prefix}_perform2_title_combo", title_combo)
            setattr(self, f"sell_{prefix}_perform2_time_value", time_value)
            setattr(self, f"sell_{prefix}_perform2_time_unit", time_unit)
            setattr(self, f"sell_{prefix}_perform2_time_range", time_range)
            setattr(self, f"sell_{prefix}_perform2_time_count", time_count)
            setattr(self, f"sell_{prefix}_perform2_time_order", time_order)
            setattr(self, f"sell_{prefix}_perform2_ratio_left", left_combo)
            setattr(self, f"sell_{prefix}_perform2_ratio_right", right_combo)
            setattr(self, f"sell_{prefix}_perform2_ratio_direction", direction_combo)
            setattr(self, f"sell_{prefix}_perform2_ratio_value", value_line)
            setattr(self, f"sell_{prefix}_perform2_ratio_compare", compare_combo)
            setattr(self, f"sell_{prefix}_perform2_ratio_count", count_line)
            return title_combo

        def add_perform_3(parent_layout, prefix):
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 0, 0)
            row.setSpacing(4)
            parent_layout.addLayout(row)

            title_combo = make_combo(["미체결", "가격비교"], "가격비교", 116)
            row.addWidget(title_combo)
            row.addWidget(make_label("|", 8, Qt.AlignCenter))

            pending_widget = QWidget()
            pending_layout = QHBoxLayout(pending_widget)
            pending_layout.setContentsMargins(0, 0, 0, 0)
            pending_layout.setSpacing(4)
            pending_scope = make_combo(["매회", "일괄"], "매회", 72)
            pending_value = make_line("20", 34)
            pending_unit = make_combo(["분", "초", "봉"], "초", 60)
            pending_layout.addWidget(pending_scope)
            pending_layout.addWidget(make_label("기준", 36))
            pending_layout.addWidget(pending_value)
            pending_layout.addWidget(pending_unit)
            pending_layout.addWidget(make_label("후 주문취소", 100))
            pending_layout.addStretch(1)
            row.addWidget(pending_widget)

            price_widget = QWidget()
            price_layout = QHBoxLayout(price_widget)
            price_layout.setContentsMargins(0, 0, 0, 0)
            price_layout.setSpacing(4)
            price_left = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
            price_right = make_combo(["주문가", "현재가", "평단가"], "현재가", 92)
            price_direction = make_combo(["상향", "하향", "상하"], "상향", 76)
            price_value = make_line("0.15", 46)
            price_compare = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
            price_action = make_combo(["매도리셋", "일괄취소"], "매도리셋", 108)
            price_layout.addWidget(price_left)
            price_layout.addWidget(make_label("대비", 36))
            price_layout.addWidget(price_right)
            price_layout.addWidget(price_direction)
            price_layout.addWidget(price_value)
            price_layout.addWidget(make_label("%", 14))
            price_layout.addWidget(price_compare)
            price_layout.addWidget(price_action)
            price_layout.addStretch(1)
            row.addWidget(price_widget)

            def sync_price_compare():
                sync_direction_compare(price_direction, price_compare)

            def sync_mode():
                is_price = title_combo.currentText() == "가격비교"
                pending_widget.setVisible(not is_price)
                price_widget.setVisible(is_price)

            price_direction.currentTextChanged.connect(lambda _: sync_price_compare())
            title_combo.currentTextChanged.connect(lambda _: sync_mode())
            sync_price_compare()
            sync_mode()
            row.addStretch(1)

            setattr(self, f"sell_{prefix}_perform3_title_combo", title_combo)
            setattr(self, f"sell_{prefix}_perform3_pending_scope", pending_scope)
            setattr(self, f"sell_{prefix}_perform3_pending_value", pending_value)
            setattr(self, f"sell_{prefix}_perform3_pending_unit", pending_unit)
            setattr(self, f"sell_{prefix}_perform3_price_left", price_left)
            setattr(self, f"sell_{prefix}_perform3_price_right", price_right)
            setattr(self, f"sell_{prefix}_perform3_price_direction", price_direction)
            setattr(self, f"sell_{prefix}_perform3_price_value", price_value)
            setattr(self, f"sell_{prefix}_perform3_price_compare", price_compare)
            setattr(self, f"sell_{prefix}_perform3_price_action", price_action)
            return title_combo

        def add_repeat_exit_conditions(parent_layout, prefix, repeat_time_combo=None, repeat_pending_combo=None):
            # 반복이탈조건 3개 항목은 체크된 항목 중 하나라도 만족하면 종료되는 OR 고정 정책이다.
            # 별도 AND/OR/NOT 연산자 UI는 두지 않는다.
            exit_condition_checks = []

            def add_exit_price_row():
                row = QHBoxLayout()
                row.setContentsMargins(16, 1, 0, 1)
                row.setSpacing(8)
                parent_layout.addLayout(row)

                check = QCheckBox()
                check.setChecked(False)
                check.setFixedWidth(34)
                row.addWidget(check)

                title_label = make_label("가격비교", 92)
                row.addWidget(title_label)
                row.addWidget(make_label("|", 8, Qt.AlignCenter))

                left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
                right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 92)
                direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
                value_line = make_line("0.15", 46)
                compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)

                row.addWidget(left_combo)
                row.addWidget(make_label("대비", 36))
                row.addWidget(right_combo)
                row.addWidget(direction_combo)
                row.addWidget(value_line)
                row.addWidget(make_label("%", 14))
                row.addWidget(compare_combo)
                row.addStretch(1)

                def sync_exit_price_compare():
                    sync_direction_compare(direction_combo, compare_combo)

                direction_combo.currentTextChanged.connect(lambda _: sync_exit_price_compare())
                sync_exit_price_compare()

                widgets = [left_combo, right_combo, direction_combo, value_line, compare_combo]

                def sync_enabled(enabled):
                    for widget in widgets:
                        widget.setEnabled(enabled)

                check.toggled.connect(sync_enabled)
                sync_enabled(check.isChecked())

                exit_condition_checks.append(check)
                setattr(self, f"sell_{prefix}_exit_price_check", check)
                setattr(self, f"sell_{prefix}_exit_price_left", left_combo)
                setattr(self, f"sell_{prefix}_exit_price_right", right_combo)
                setattr(self, f"sell_{prefix}_exit_price_direction", direction_combo)
                setattr(self, f"sell_{prefix}_exit_price_value", value_line)
                setattr(self, f"sell_{prefix}_exit_price_compare", compare_combo)

            def add_exit_count_row():
                row = QHBoxLayout()
                row.setContentsMargins(16, 1, 0, 1)
                row.setSpacing(8)
                parent_layout.addLayout(row)

                check = QCheckBox()
                check.setChecked(False)
                check.setFixedWidth(34)
                row.addWidget(check)

                title_label = make_label("반복횟수", 92)
                count_line = make_line("3", 34)
                row.addWidget(title_label)
                row.addWidget(make_label("|", 8, Qt.AlignCenter))
                row.addWidget(count_line)
                row.addWidget(make_label("회", 18))
                row.addStretch(1)

                widgets = [count_line]

                def sync_enabled(enabled):
                    for widget in widgets:
                        widget.setEnabled(enabled)

                check.toggled.connect(sync_enabled)
                sync_enabled(check.isChecked())

                exit_condition_checks.append(check)
                setattr(self, f"sell_{prefix}_exit_count_check", check)
                setattr(self, f"sell_{prefix}_exit_count_line", count_line)

            def add_exit_time_row():
                row = QHBoxLayout()
                row.setContentsMargins(16, 1, 0, 1)
                row.setSpacing(8)
                parent_layout.addLayout(row)

                check = QCheckBox()
                check.setChecked(False)
                check.setFixedWidth(34)
                row.addWidget(check)

                title_label = make_label("제한시간", 92)
                separator_label = make_label("|", 8, Qt.AlignCenter)
                time_line = make_line("2", 34)
                unit_combo = make_combo(["분", "초", "봉"], "분", 60)
                row.addWidget(title_label)
                row.addWidget(separator_label)
                row.addWidget(time_line)
                row.addWidget(unit_combo)
                row.addStretch(1)

                value_widgets = [time_line, unit_combo]
                row_widgets = [title_label, separator_label, time_line, unit_combo]

                def sync_enabled(enabled):
                    # 값/단위는 제한시간 체크가 켜졌을 때만 편집 가능하다.
                    # 체크박스 자체의 사용 가능 여부는 아래 sync_exit_time_by_repeat_time_setting에서
                    # 3번 후속매도반복설정의 시간 조건 여부에 따라 별도로 제어한다.
                    for widget in value_widgets:
                        widget.setEnabled(enabled and check.isEnabled())

                check.toggled.connect(sync_enabled)
                sync_enabled(check.isChecked())

                exit_condition_checks.append(check)
                setattr(self, f"sell_{prefix}_exit_time_check", check)
                setattr(self, f"sell_{prefix}_exit_time_line", time_line)
                setattr(self, f"sell_{prefix}_exit_time_unit", unit_combo)
                return check, time_line, unit_combo, row_widgets

            add_exit_price_row()
            add_exit_count_row()
            local_exit_time_check, local_exit_time_line, local_exit_time_unit, local_exit_time_row_widgets = add_exit_time_row()
            setattr(self, f"sell_{prefix}_exit_condition_checks", exit_condition_checks)

            def sync_exit_time_by_repeat_time_setting():
                # 현재 설정 박스에 실제 배치된 위젯만 로컬 참조로 직접 제어한다.
                # self 속성은 구성탭/매도탭 중복 생성으로 덮어써질 수 있으므로 사용하지 않는다.
                time_mode = repeat_time_combo.currentText() if repeat_time_combo is not None else ""
                pending_mode = repeat_pending_combo.currentText() if repeat_pending_combo is not None else ""

                # 3. 후속매도반복설정에 시간 제한 동작이 있을 때만 4번 제한시간을 막는다.
                # - add_perform_2: 다중시간
                # - add_perform_3: 미체결
                # 선택없음/다중비율/가격비교/단일호가/다중호가 등은 시간 제한 동작이 아니다.
                has_repeat_time_setting = (time_mode == "다중시간") or (pending_mode == "미체결")

                if has_repeat_time_setting:
                    local_exit_time_check.setChecked(False)

                local_exit_time_check.setEnabled(not has_repeat_time_setting)

                can_edit_time_value = (
                    not has_repeat_time_setting
                    and local_exit_time_check.isChecked()
                )
                local_exit_time_line.setEnabled(can_edit_time_value)
                local_exit_time_unit.setEnabled(can_edit_time_value)

                # 라벨은 시간 제한 사용 가능 여부를 같이 보여주되,
                # 값/단위처럼 체크 상태에 종속시키지는 않는다.
                for widget in local_exit_time_row_widgets[:2]:
                    widget.setEnabled(not has_repeat_time_setting)

            for combo in (repeat_time_combo, repeat_pending_combo):
                if combo is not None:
                    combo.currentTextChanged.connect(lambda _=None: sync_exit_time_by_repeat_time_setting())

            exit_time_check = getattr(self, f"sell_{prefix}_exit_time_check", None)
            if exit_time_check is not None:
                exit_time_check.toggled.connect(lambda _checked=False: sync_exit_time_by_repeat_time_setting())

            sync_exit_time_by_repeat_time_setting()
            return exit_condition_checks

        def add_sell_complete_policy(parent_layout, prefix, exit_checks=None):
            row = QHBoxLayout()
            row.setContentsMargins(16, 0, 0, 0)
            row.setSpacing(12)
            parent_layout.addLayout(row)

            # 5. 매도완료정책은 사용자가 직접 선택하는 설정이 아니라,
            # 바로 위 4. 반복이탈조건 체크 상태에 종속되는 결과 표시 영역이다.
            # 같은 함수가 구성탭/매도탭에서 여러 번 호출되어 self 속성이 덮어써져도
            # 현재 설정박스 안의 실제 체크박스 목록(exit_checks)과 결과 위젯을 직접 연결한다.
            carry_check = QCheckBox("다음신호로 이월")
            market_check = QCheckBox("보유잔량 시장가매도")

            for result_check in (carry_check, market_check):
                result_check.setChecked(True)
                result_check.setEnabled(False)
                result_check.setFixedHeight(30)
                result_check.setMinimumWidth(190)
                result_check.setStyleSheet(
                    "QCheckBox { font-size: 8pt; color: #003366; font-weight: bold; }"
                )
                row.addWidget(result_check, 0, Qt.AlignVCenter)

            row.addStretch(1)

            local_exit_checks = list(exit_checks or [])
            if not local_exit_checks:
                local_exit_checks = [
                    getattr(self, f"sell_{prefix}_exit_price_check", None),
                    getattr(self, f"sell_{prefix}_exit_count_check", None),
                    getattr(self, f"sell_{prefix}_exit_time_check", None),
                ]
            local_exit_checks = [check for check in local_exit_checks if check is not None]

            def sync_local_complete_policy():
                has_exit_condition = any(check.isChecked() for check in local_exit_checks)

                carry_check.setChecked(True)
                market_check.setChecked(True)
                carry_check.setVisible(not has_exit_condition)
                market_check.setVisible(has_exit_condition)

                carry_check.updateGeometry()
                market_check.updateGeometry()
                row.invalidate()

            for check in local_exit_checks:
                check.toggled.connect(lambda _checked=False: sync_local_complete_policy())

            sync_local_complete_policy()

            # 기존 코드 호환용 별칭은 유지하되, 실제 표시 동작은 위 local 연결이 담당한다.
            setattr(self, f"sell_{prefix}_complete_policy_result_check", carry_check)
            setattr(self, f"sell_{prefix}_complete_policy_check", carry_check)
            setattr(self, f"sell_{prefix}_complete_policy_label", carry_check)
            setattr(self, f"sell_{prefix}_complete_result_label", carry_check)
            setattr(self, f"sell_{prefix}_complete_policy_carry_check", carry_check)
            setattr(self, f"sell_{prefix}_complete_policy_market_check", market_check)
            setattr(self, f"sell_{prefix}_complete_policy_sync", sync_local_complete_policy)

        def make_setting_box(title, prefix):
            box = QGroupBox(title)
            box.setMinimumHeight(520)
            box.setStyleSheet(
                "QGroupBox { font-weight: bold; } "
                "QGroupBox::title {"
                "subcontrol-origin: margin;"
                "left: 8px;"
                "padding: 2px 4px;"
                "font-size: 26px;"
                "font-weight: bold;"
                "}"
            )
            inner = QVBoxLayout(box)
            inner.setContentsMargins(2, 20, 8, 10)
            inner.setSpacing(5)
            add_section_header(inner, "▼ 1. 기본매도설정")
            add_perform_1(inner, prefix)
            add_perform_2(inner, prefix)
            inner.addSpacing(10)
            add_section_header(inner, "▼ 2. 상황변화대응")
            add_perform_3(inner, prefix)
            inner.addSpacing(10)
            add_section_header(inner, "▼ 3. 순환설정")
            add_perform_1(inner, f"{prefix}_repeat")
            repeat_perform2_title_combo = add_perform_2(inner, f"{prefix}_repeat")
            repeat_perform3_title_combo = add_perform_3(inner, f"{prefix}_repeat")
            inner.addSpacing(10)
            add_section_header(inner, "▼ 4. 이탈조건")
            exit_checks = add_repeat_exit_conditions(
                inner,
                prefix,
                repeat_time_combo=repeat_perform2_title_combo,
                repeat_pending_combo=repeat_perform3_title_combo,
            )
            inner.addSpacing(10)
            add_section_header(inner, "● 5. 세트마감")
            add_sell_complete_policy(inner, prefix, exit_checks)
            inner.addStretch(1)
            return box

        self.sell_setting_a_box = make_setting_box("설정 A", "a")
        self.sell_setting_b_box = make_setting_box("설정 B", "b")
        self.sell_setting_c_box = make_setting_box("설정 C", "c")

        layout.addWidget(self.sell_setting_a_box, 0, 0)
        layout.addWidget(self.sell_setting_b_box, 0, 1)
        layout.addWidget(self.sell_setting_c_box, 0, 2)

        return container

    def _make_sell_cancel_overview_controls(self):
        return self._make_common_cancel_overview_controls("미체결정책", "매도주문", "sell")

    def _make_sell_method_detail_overview_controls(self):
        box = QGroupBox("매도방식 세부설정")
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

        def bind_row_enabled(check, widgets, direction_combo=None, compare_combo=None):
            def sync_row_enabled(enabled):
                for widget in widgets:
                    widget.setEnabled(enabled)
                if enabled and direction_combo is not None and compare_combo is not None:
                    sync_direction_compare_combo(direction_combo, compare_combo)
            check.toggled.connect(sync_row_enabled)
            sync_row_enabled(check.isChecked())

        def add_policy_row(direction, logic="AND", checked=True):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)
            layout.addLayout(row)

            policy_check = QCheckBox()
            policy_check.setChecked(checked)
            policy_check.setFixedWidth(20)
            row.addWidget(policy_check)

            ma_line = make_line("20", 38)
            ma_label = QLabel("이평")
            bar_line = make_line("5", 28)
            bar_label = QLabel("봉전")
            direction_combo = make_combo(["상향", "하향", "상하"], direction, 64)
            value_line = make_line("0.15", 44)
            unit_label = QLabel("%")
            compare_combo = make_combo(
                ["이상", "이하", "이내", "이탈"],
                "이내" if direction == "상하" else "이하",
                68,
            )
            logic_combo = make_combo(["AND", "OR", "NOT"], logic, 62)

            row.addWidget(ma_line)
            row.addWidget(ma_label)
            row.addWidget(bar_line)
            row.addWidget(bar_label)
            row.addWidget(direction_combo)
            row.addWidget(value_line)
            row.addWidget(unit_label)
            row.addWidget(compare_combo)
            row.addStretch(1)
            row.addWidget(logic_combo)

            target_widgets = [
                ma_line,
                ma_label,
                bar_line,
                bar_label,
                direction_combo,
                value_line,
                unit_label,
                compare_combo,
                logic_combo,
            ]
            direction_combo.currentTextChanged.connect(
                lambda _: sync_direction_compare_combo(direction_combo, compare_combo)
            )
            sync_direction_compare_combo(direction_combo, compare_combo)
            bind_row_enabled(policy_check, target_widgets, direction_combo, compare_combo)
            return policy_check

        self.sell_method_detail_up_check = add_policy_row("상향", "AND", True)
        self.sell_method_detail_side_check = add_policy_row("상하", "AND", True)
        self.sell_method_detail_down_check = add_policy_row("하향", "AND", True)

        def add_detail_policy_row(row_index, left_basis="주문가", right_basis="현재가", direction="상하", value="0.25", compare="이내", logic="AND", checked=True):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)
            layout.addLayout(row)

            detail_check = QCheckBox()
            detail_check.setChecked(checked)
            detail_check.setFixedWidth(20)
            setattr(self, f"sell_method_detail_{row_index}_check", detail_check)
            row.addWidget(detail_check)

            left_combo = make_combo(["주문가", "현재가", "평단가"], left_basis, 84)
            right_combo = make_combo(["주문가", "현재가", "평단가"], right_basis, 84)
            direction_combo = make_combo(["상향", "하향", "상하"], direction, 64)
            value_line = make_line(value, 44)
            unit_label = QLabel("%")
            compare_combo = make_combo(["이상", "이하", "이내", "이탈"], compare, 68)
            logic_combo = make_combo(["AND", "OR", "NOT"], logic, 62)

            setattr(self, f"sell_method_detail_{row_index}_left_combo", left_combo)
            setattr(self, f"sell_method_detail_{row_index}_right_combo", right_combo)
            setattr(self, f"sell_method_detail_{row_index}_direction_combo", direction_combo)
            setattr(self, f"sell_method_detail_{row_index}_value_line", value_line)
            setattr(self, f"sell_method_detail_{row_index}_compare_combo", compare_combo)
            setattr(self, f"sell_method_detail_{row_index}_logic_combo", logic_combo)

            row.addWidget(left_combo)
            row.addWidget(QLabel("대비"))
            row.addWidget(right_combo)
            row.addWidget(direction_combo)
            row.addWidget(value_line)
            row.addWidget(unit_label)
            row.addWidget(compare_combo)
            row.addStretch(1)
            row.addWidget(logic_combo)

            target_widgets = [
                left_combo,
                right_combo,
                direction_combo,
                value_line,
                unit_label,
                compare_combo,
                logic_combo,
            ]
            direction_combo.currentTextChanged.connect(
                lambda _: sync_direction_compare_combo(direction_combo, compare_combo)
            )
            sync_direction_compare_combo(direction_combo, compare_combo)
            bind_row_enabled(detail_check, target_widgets, direction_combo, compare_combo)
            return detail_check

        self.sell_method_detail_gap_1_check = add_detail_policy_row(1, "주문가", "현재가", "상하", "0.25", "이내", "AND", True)
        self.sell_method_detail_gap_2_check = add_detail_policy_row(2, "주문가", "현재가", "상하", "0.25", "이내", "AND", False)
        self.sell_method_detail_gap_3_check = add_detail_policy_row(3, "주문가", "현재가", "상하", "0.25", "이내", "AND", False)

        return box

    def _make_sell_complete_overview_controls(self):
        box = QGroupBox("완료정책")
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

        def add_row(indent=0):
            row = QHBoxLayout()
            row.setContentsMargins(indent, 2, 0, 2)
            row.setSpacing(4)
            layout.addLayout(row)
            return row

        row = add_row()
        self.sell_complete_single_check = QCheckBox("단일호가")
        self.sell_complete_single_check.setChecked(True)
        self.sell_complete_single_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_complete_single_check)
        single_check = self.sell_complete_single_check
        row.addStretch(1)

        row = add_row()
        self.sell_complete_multi_check = QCheckBox("상향")
        self.sell_complete_multi_check.setChecked(False)
        self.sell_complete_multi_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_complete_multi_check)
        multi_check = self.sell_complete_multi_check

        self.sell_complete_multi_up_line = make_line("4", 38)
        self.sell_complete_multi_down_line = make_line("2", 38)
        multi_up_line = self.sell_complete_multi_up_line
        multi_down_line = self.sell_complete_multi_down_line
        self.sell_complete_multi_total_label = QLabel("합계 7호가")

        multi_total_label = self.sell_complete_multi_total_label

        row.addWidget(multi_up_line)
        row.addWidget(QLabel("호가 / 기준 1호가 / 하향"))
        row.addWidget(multi_down_line)
        row.addWidget(QLabel("호가 |"))
        row.addWidget(multi_total_label)
        row.addStretch(1)

        def _sync_sell_complete_multi_hoga_total():
            try:
                up = int(multi_up_line.text().strip())
            except ValueError:
                up = 0
            try:
                down = int(multi_down_line.text().strip())
            except ValueError:
                down = 0
            multi_total_label.setText(f"합계 {up + 1 + down}호가")

        multi_up_line.textChanged.connect(_sync_sell_complete_multi_hoga_total)
        multi_down_line.textChanged.connect(_sync_sell_complete_multi_hoga_total)
        _sync_sell_complete_multi_hoga_total()

        row = add_row()
        point_title_label = QLabel("다중지점")
        point_title_label.setStyleSheet("font-size: 8pt; font-weight: bold;")
        row.addWidget(point_title_label)
        row.addStretch(1)

        child_indent = 18

        row = add_row(child_indent)
        self.sell_complete_time_point_check = QCheckBox("시간")
        self.sell_complete_time_point_check.setChecked(False)
        self.sell_complete_time_point_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_complete_time_point_check)
        time_point_check = self.sell_complete_time_point_check
        row.addWidget(make_line("30", 38))
        row.addWidget(make_combo(["분", "초", "봉"], "초", 58))
        row.addWidget(make_combo(["이내", "간격"], "이내", 72))
        row.addWidget(make_line("3", 38))
        row.addWidget(QLabel("회"))
        self.sell_complete_time_point_order_combo = make_combo(["주문가", "현재가"], "주문가", 96)
        row.addWidget(self.sell_complete_time_point_order_combo)
        row.addStretch(1)

        row = add_row(child_indent)
        self.sell_complete_avg_point_check = QCheckBox("")
        self.sell_complete_avg_point_check.setChecked(False)
        self.sell_complete_avg_point_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.sell_complete_avg_point_check)
        avg_point_check = self.sell_complete_avg_point_check
        self.sell_complete_avg_point_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 86)
        row.addWidget(self.sell_complete_avg_point_left_combo)
        row.addWidget(QLabel("대비"))
        self.sell_complete_avg_point_right_combo = make_combo(["주문가", "현재가", "평단가"], "평단가", 86)
        row.addWidget(self.sell_complete_avg_point_right_combo)
        self.sell_complete_avg_point_direction_combo = make_combo(["상향", "하향", "상하"], "하향", 72)
        row.addWidget(self.sell_complete_avg_point_direction_combo)
        avg_point_direction_combo = self.sell_complete_avg_point_direction_combo
        self.sell_complete_avg_point_value_line = make_line("0.15", 48)
        row.addWidget(self.sell_complete_avg_point_value_line)
        row.addWidget(QLabel("%"))
        self.sell_complete_avg_point_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이하", 72)
        row.addWidget(self.sell_complete_avg_point_compare_combo)
        avg_point_compare_combo = self.sell_complete_avg_point_compare_combo
        row.addWidget(QLabel("/"))
        self.sell_complete_avg_point_count_line = make_line("3", 38)
        row.addWidget(self.sell_complete_avg_point_count_line)
        row.addWidget(QLabel("회"))
        row.addStretch(1)

        row = add_row(child_indent + 22)
        self.sell_complete_last_market_check = QCheckBox("마지막회차 시장가")
        self.sell_complete_last_market_check.setChecked(False)
        self.sell_complete_last_market_check.setStyleSheet("font-weight: normal;")
        last_market_check = self.sell_complete_last_market_check
        row.addWidget(last_market_check)
        row.addStretch(1)

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

        avg_point_direction_combo.currentTextChanged.connect(
            lambda _: sync_direction_compare_combo(
                avg_point_direction_combo,
                avg_point_compare_combo,
            )
        )
        sync_direction_compare_combo(
            avg_point_direction_combo,
            avg_point_compare_combo,
        )

        exclusive_guard = {"active": False}

        def sync_price_method(source):
            if exclusive_guard["active"]:
                return
            exclusive_guard["active"] = True
            try:
                if source is single_check and source.isChecked():
                    multi_check.setChecked(False)
                elif source is multi_check and source.isChecked():
                    single_check.setChecked(False)
                elif not single_check.isChecked() and not multi_check.isChecked():
                    single_check.setChecked(True)
            finally:
                exclusive_guard["active"] = False

        single_check.toggled.connect(lambda _: sync_price_method(single_check))
        multi_check.toggled.connect(lambda _: sync_price_method(multi_check))

        def sync_point_method(source):
            if exclusive_guard["active"]:
                return
            exclusive_guard["active"] = True
            try:
                if source is time_point_check and source.isChecked():
                    avg_point_check.setChecked(False)
                elif source is avg_point_check and source.isChecked():
                    time_point_check.setChecked(False)
            finally:
                exclusive_guard["active"] = False

        time_point_check.toggled.connect(lambda _: sync_point_method(time_point_check))
        avg_point_check.toggled.connect(lambda _: sync_point_method(avg_point_check))

        self.sell_complete_after_cancel_check = QCheckBox()
        self.sell_complete_after_cancel_check.setChecked(True)
        self.sell_complete_after_cancel_check.setVisible(False)
        self.sell_complete_after_cancel_line = QLineEdit()
        self.sell_complete_after_cancel_unit_combo = QComboBox()
        self.sell_complete_after_cancel_tail_label = QLabel("")
        after_cancel_check = self.sell_complete_after_cancel_check
        after_cancel_line = self.sell_complete_after_cancel_line
        after_cancel_unit_combo = self.sell_complete_after_cancel_unit_combo
        after_cancel_tail_label = self.sell_complete_after_cancel_tail_label

        def sync_after_cancel_by_multi_point():
            multi_enabled = (
                time_point_check.isChecked()
                or avg_point_check.isChecked()
            )
            last_market_check.setEnabled(multi_enabled)
            if not multi_enabled:
                last_market_check.setChecked(False)
            enabled = not multi_enabled
            after_cancel_check.setEnabled(enabled)
            after_cancel_line.setEnabled(enabled and True)
            after_cancel_unit_combo.setEnabled(enabled and True)
            after_cancel_tail_label.setEnabled(enabled and True)

        after_cancel_check.toggled.connect(lambda _: sync_after_cancel_by_multi_point())
        time_point_check.toggled.connect(lambda _: sync_after_cancel_by_multi_point())
        avg_point_check.toggled.connect(lambda _: sync_after_cancel_by_multi_point())
        sync_after_cancel_by_multi_point()

        return box

    def _make_sell_complete_policy_overview_controls(self):
        box = QGroupBox("완료정책 세부설정")
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

        def bind_row_enabled(check, widgets, direction_combo=None, compare_combo=None):
            def sync_row_enabled(enabled):
                for widget in widgets:
                    widget.setEnabled(enabled)
                if enabled and direction_combo is not None and compare_combo is not None:
                    sync_direction_compare_combo(direction_combo, compare_combo)
            check.toggled.connect(sync_row_enabled)
            sync_row_enabled(check.isChecked())

        def add_ma_policy_row(row_index, direction, checked=True, action="단일호가"):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)
            layout.addLayout(row)

            check = QCheckBox()
            check.setChecked(checked)
            check.setFixedWidth(20)
            setattr(self, f"sell_complete_policy_ma_{row_index}_check", check)
            row.addWidget(check)

            ma_line = make_line("20", 38)
            ma_label = QLabel("이평")
            bar_line = make_line("5", 28)
            bar_label = QLabel("봉전")
            direction_combo = make_combo(["상향", "하향", "상하"], direction, 64)
            value_line = make_line("0.15", 44)
            unit_label = QLabel("%")
            compare_combo = make_combo(
                ["이상", "이하", "이내", "이탈"],
                "이내" if direction == "상하" else "이하",
                68,
            )
            action_combo = make_combo(["단일호가", "다중호가", "매도제외"], action, 96)
            multi_point_check = QCheckBox("다중지점")
            multi_point_check.setChecked(False)
            multi_point_check.setStyleSheet("font-weight: normal;")

            setattr(self, f"sell_complete_policy_ma_{row_index}_direction_combo", direction_combo)
            setattr(self, f"sell_complete_policy_ma_{row_index}_action_combo", action_combo)
            setattr(self, f"sell_complete_policy_ma_{row_index}_multi_point_check", multi_point_check)

            row.addWidget(ma_line)
            row.addWidget(ma_label)
            row.addWidget(bar_line)
            row.addWidget(bar_label)
            row.addWidget(direction_combo)
            row.addWidget(value_line)
            row.addWidget(unit_label)
            row.addWidget(compare_combo)
            row.addWidget(action_combo)
            row.addWidget(multi_point_check)
            row.addStretch(1)

            widgets = [
                ma_line, ma_label, bar_line, bar_label,
                direction_combo, value_line, unit_label,
                compare_combo, action_combo, multi_point_check,
            ]
            direction_combo.currentTextChanged.connect(
                lambda _: sync_direction_compare_combo(direction_combo, compare_combo)
            )
            sync_direction_compare_combo(direction_combo, compare_combo)
            bind_row_enabled(check, widgets, direction_combo, compare_combo)

        def add_price_policy_row(row_index, left_basis="주문가", right_basis="현재가", direction="상하", value="0.25", compare="이내", action="단일호가", checked=False):
            row = QHBoxLayout()
            row.setContentsMargins(0, 2, 0, 2)
            row.setSpacing(3)
            layout.addLayout(row)

            check = QCheckBox()
            check.setChecked(checked)
            check.setFixedWidth(20)
            setattr(self, f"sell_complete_policy_price_{row_index}_check", check)
            row.addWidget(check)

            left_combo = make_combo(["주문가", "현재가", "평단가"], left_basis, 84)
            right_combo = make_combo(["주문가", "현재가", "평단가"], right_basis, 84)
            direction_combo = make_combo(["상향", "하향", "상하"], direction, 64)
            value_line = make_line(value, 44)
            unit_label = QLabel("%")
            compare_combo = make_combo(["이상", "이하", "이내", "이탈"], compare, 68)
            action_combo = make_combo(["단일호가", "다중호가", "매도제외"], action, 96)
            multi_point_check = QCheckBox("다중지점")
            multi_point_check.setChecked(False)
            multi_point_check.setStyleSheet("font-weight: normal;")

            setattr(self, f"sell_complete_policy_price_{row_index}_left_combo", left_combo)
            setattr(self, f"sell_complete_policy_price_{row_index}_right_combo", right_combo)
            setattr(self, f"sell_complete_policy_price_{row_index}_direction_combo", direction_combo)
            setattr(self, f"sell_complete_policy_price_{row_index}_action_combo", action_combo)
            setattr(self, f"sell_complete_policy_price_{row_index}_multi_point_check", multi_point_check)

            row.addWidget(left_combo)
            row.addWidget(QLabel("대비"))
            row.addWidget(right_combo)
            row.addWidget(direction_combo)
            row.addWidget(value_line)
            row.addWidget(unit_label)
            row.addWidget(compare_combo)
            row.addWidget(action_combo)
            row.addWidget(multi_point_check)
            row.addStretch(1)

            widgets = [
                left_combo, right_combo, direction_combo, value_line,
                unit_label, compare_combo, action_combo, multi_point_check,
            ]
            direction_combo.currentTextChanged.connect(
                lambda _: sync_direction_compare_combo(direction_combo, compare_combo)
            )
            sync_direction_compare_combo(direction_combo, compare_combo)
            bind_row_enabled(check, widgets, direction_combo, compare_combo)

        add_ma_policy_row(1, "상향", True, "단일호가")
        add_ma_policy_row(2, "상하", True, "단일호가")
        add_ma_policy_row(3, "하향", True, "단일호가")

        add_price_policy_row(1, "주문가", "현재가", "상하", "0.25", "이내", "단일호가", True)
        add_price_policy_row(2, "주문가", "현재가", "상하", "0.25", "이내", "단일호가", False)
        add_price_policy_row(3, "주문가", "현재가", "상하", "0.25", "이내", "단일호가", False)

        return box

    def _build_sell_tab(self):
        self.sell_tab = QWidget()
        layout = QVBoxLayout(self.sell_tab)

        common_box = QGroupBox("매도 조건 결합")
        common_form = QFormLayout(common_box)

        self.sell_enabled_check = self._locked_checkbox("매도 신호 사용")
        self.sell_logic_combo = QComboBox()
        self.sell_logic_combo.addItems(["OR", "AND"])
        self.sell_logic_combo.setEnabled(False)

        common_form.addRow("매도 신호", self.sell_enabled_check)
        common_form.addRow("결합 방식", self.sell_logic_combo)
        layout.addWidget(common_box)

        macd_box = QGroupBox("MACD 반전 매도")
        macd_form = QFormLayout(macd_box)

        self.macd_sell_enabled_check = self._locked_checkbox("MACD 반전 매도 사용")
        self.macd_sell_delay_line = self._readonly_line()
        self.macd_sell_status_line = self._readonly_line()

        macd_form.addRow("사용 여부", self.macd_sell_enabled_check)
        macd_form.addRow("기준봉/지연봉", self.macd_sell_delay_line)
        macd_form.addRow("상태", self.macd_sell_status_line)
        layout.addWidget(macd_box)

        profit_box = QGroupBox("평단 대비 수익률 매도")
        profit_form = QFormLayout(profit_box)

        self.profit_sell_enabled_check = self._locked_checkbox("수익률 매도 사용")
        self.target_profit_line = self._readonly_line()
        self.profit_basis_line = self._readonly_line()

        profit_form.addRow("사용 여부", self.profit_sell_enabled_check)
        profit_form.addRow("목표 수익률", self.target_profit_line)
        profit_form.addRow("기준", self.profit_basis_line)
        layout.addWidget(profit_box)

        layout.addStretch(1)

    def _make_sell_edit_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel("매도설정")
        title.setStyleSheet("font-size: 26px; font-weight: bold; color: #C62828; padding: 1px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;")
        row.addWidget(title)

        title_sep = QLabel("|")
        title_sep.setStyleSheet("font-size: 26px; font-weight: bold; color: #000000; padding: 2px 1px;")
        row.addWidget(title_sep)

        def make_label(text):
            label = QLabel(text)
            label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
            return label

        def make_line(text, width, center=False):
            line = QLineEdit()
            line.setText(text)
            line.setFixedWidth(width)
            line.setFixedHeight(34)
            line.setAlignment(Qt.AlignCenter if center else Qt.AlignRight)
            line.setStyleSheet("font-size: 10pt; padding: 1px 6px; font-weight: bold;" if center else "font-size: 8pt; padding-right: 6px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(30)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        row.addWidget(make_label("● 신호검출조건 :"))
        row.addWidget(make_line("A OR B OR C", 240, True))
        for token in ["A", "/", "B", "/", "C", "/", "OR", "()", "지움"]:
            button = QPushButton(token)
            button.setFixedHeight(26)
            button.setStyleSheet("font-size: 8pt; padding: 1px 6px;")
            row.addWidget(button)
        row.addSpacing(24)
        row.addWidget(make_label("● 매도방식지정 :"))
        for name, checked in [("설정 A", True), ("설정 B", False), ("설정 C", False)]:
            cb = QCheckBox(name)
            cb.setChecked(checked)
            cb.setStyleSheet("font-size: 9pt;")
            row.addWidget(cb)
        row.addStretch(1)
        return row

    def _build_sell_edit_tab(self):
        self.sell_edit_tab = QWidget()
        outer = QVBoxLayout(self.sell_edit_tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        layout.addLayout(self._make_sell_edit_header())

        signal_grid = QGridLayout()
        signal_grid.setColumnStretch(0, 1)
        signal_grid.setColumnStretch(1, 1)
        signal_grid.setColumnStretch(2, 1)
        signal_grid.addWidget(self._make_sell_signal_condition_1_overview_controls(), 0, 0)
        signal_grid.addWidget(self._make_sell_signal_condition_2_overview_controls(), 0, 1)
        signal_grid.addWidget(self._make_sell_signal_condition_3_overview_controls(), 0, 2)
        layout.addLayout(signal_grid)

        scenario = self._make_sell_scenario_overview_controls()
        layout.addWidget(scenario)

        note = QLabel("매도 탭은 구성 탭의 매도설정만 분리 표시한 시험 배치입니다. 저장 기능은 현재 비활성입니다.")
        note.setAlignment(Qt.AlignCenter)
        note.setStyleSheet("color: #666; padding: 6px;")
        layout.addWidget(note)

        scroll.setWidget(page)
        page.adjustSize()
        outer.addWidget(scroll)
