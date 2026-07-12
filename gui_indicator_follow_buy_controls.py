from PyQt5 import sip
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
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui_indicator_follow_buy_method_controls import IndicatorFollowBuyMethodControlsMixin


class IndicatorFollowBuyControlsMixin(IndicatorFollowBuyMethodControlsMixin):
    def _make_buy_filter_overview_controls(self):
        box = QGroupBox("신호검출필터")
        box.setStyleSheet(
            "QGroupBox { font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(6)

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

        def make_filter_label(text):
            label = QLabel(text)
            label.setFixedWidth(22)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-size: 9pt; font-weight: bold;")
            return label

        def add_inline_separator():
            separator = QLabel("|")
            separator.setAlignment(Qt.AlignCenter)
            separator.setFixedWidth(12)
            separator.setStyleSheet("font-size: 9pt; font-weight: bold; color: #555555;")
            layout.addWidget(separator)

        def add_filter_group(label_text, widgets):
            layout.addWidget(make_filter_label(label_text))
            for widget in widgets:
                layout.addWidget(widget)

        # A: OCR
        self.buy_ocr_sign_combo = make_combo(["-", "+"], "-", 52)
        self.buy_ocr_value_line = make_line("1", 44)
        self.buy_ocr_compare_combo = make_combo(["이하", "이상"], "이하", 64)
        self.buy_ocr_turn_combo = make_combo(["상승", "하락"], "상승", 64)
        self.buy_ocr_bar_line = make_line("0", 44)

        def _sync_buy_ocr_sign():
            value = self.buy_ocr_value_line.text().strip()
            self.buy_ocr_sign_combo.setEnabled(value != "0")

        self.buy_ocr_value_line.textChanged.connect(_sync_buy_ocr_sign)
        _sync_buy_ocr_sign()

        add_filter_group("A", [
            QLabel("OCR"),
            self.buy_ocr_sign_combo,
            self.buy_ocr_value_line,
            self.buy_ocr_compare_combo,
            self.buy_ocr_turn_combo,
            QLabel("전환"),
            self.buy_ocr_bar_line,
            QLabel("봉"),
        ])
        add_inline_separator()

        # B: 볼린저밴드
        self.buy_bollinger_direction_combo = make_combo(["상향", "하향"], "하향", 64)
        self.buy_bollinger_value_line = make_line("0.1", 42)
        self.buy_bollinger_compare_combo = make_combo(["이상", "이하"], "이상", 64)
        add_filter_group("B", [
            QLabel("볼린저밴드"),
            self.buy_bollinger_direction_combo,
            self.buy_bollinger_value_line,
            QLabel("%"),
            self.buy_bollinger_compare_combo,
        ])
        add_inline_separator()

        # C: 현재가 60이평
        self.buy_ma_value_line = make_line("60", 38)
        self.buy_ma_direction_combo = make_combo(["상향", "하향"], "상향", 64)
        self.buy_ma_compare_combo = make_combo(["돌파", "이상", "이하"], "돌파", 64)
        ma_direction_combo = self.buy_ma_direction_combo
        ma_compare_combo = self.buy_ma_compare_combo

        def _sync_buy_ma_compare_combo():
            direction = ma_direction_combo.currentText()
            visible_items = ["돌파", "이상"] if direction == "상향" else ["돌파", "이하"]
            for item_text in ["돌파", "이상", "이하"]:
                index = ma_compare_combo.findText(item_text)
                if index >= 0:
                    ma_compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if ma_compare_combo.currentText() not in visible_items:
                ma_compare_combo.setCurrentText("돌파")

        ma_direction_combo.currentTextChanged.connect(lambda _: _sync_buy_ma_compare_combo())
        _sync_buy_ma_compare_combo()

        add_filter_group("C", [
            QLabel("현재가"),
            self.buy_ma_value_line,
            QLabel("이평"),
            ma_direction_combo,
            ma_compare_combo,
        ])
        add_inline_separator()

        # D: RSI
        self.buy_rsi_period_line = make_line("14", 36)
        self.buy_rsi_value_line = make_line("45", 38)
        self.buy_rsi_compare_combo = make_combo(["이하", "이상"], "이하", 64)
        add_filter_group("D", [
            QLabel("RSI기간"),
            self.buy_rsi_period_line,
            self.buy_rsi_value_line,
            self.buy_rsi_compare_combo,
        ])

        layout.addStretch(1)
        return box

    def _make_buy_composite_filter_controls(self):
        box = QGroupBox("Composite BUY Filter")
        box.setStyleSheet(
            "QGroupBox { font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.setSpacing(4)

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(28)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        def make_check(text, checked=False):
            check = QCheckBox(text)
            check.setChecked(checked)
            check.setFixedHeight(28)
            check.setStyleSheet("font-size: 8pt;")
            return check

        def make_label(text, width=None):
            label = QLabel(text)
            label.setFixedHeight(28)
            label.setStyleSheet("font-size: 8pt;")
            if width is not None:
                label.setFixedWidth(width)
            return label

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        layout.addLayout(top_row)

        self.buy_composite_enabled_check = make_check("Use Composite", False)
        self.buy_composite_logic_combo = make_combo(["AND", "OR"], "OR", 62)
        self.buy_composite_include_unreferenced_combo = make_combo(["AND_REQUIRED"], "AND_REQUIRED", 128)
        self.buy_composite_include_unreferenced_combo.setEnabled(False)
        top_row.addWidget(self.buy_composite_enabled_check)
        top_row.addWidget(make_label("Logic", 38))
        top_row.addWidget(self.buy_composite_logic_combo)
        top_row.addWidget(make_label("Unreferenced", 84))
        top_row.addWidget(self.buy_composite_include_unreferenced_combo)
        top_row.addStretch(1)

        self.buy_composite_warning_label = QLabel("")
        self.buy_composite_warning_label.setStyleSheet("font-size: 8pt; color: #8a4b00;")
        self.buy_composite_warning_label.setVisible(False)
        layout.addWidget(self.buy_composite_warning_label)

        filter_specs = [
            ("rsi", "RSI"),
            ("moving_average", "MA"),
            ("price_compare", "Price"),
            ("bollinger", "Bollinger"),
            ("ocr", "OCR"),
        ]
        default_filters = {
            1: {"rsi", "moving_average"},
            2: {"bollinger", "ocr"},
        }

        for group_index in (1, 2):
            row = QHBoxLayout()
            row.setContentsMargins(12, 0, 0, 0)
            row.setSpacing(4)
            layout.addLayout(row)

            enabled = make_check(f"Group {group_index}", True)
            logic = make_combo(["AND", "OR"], "AND", 62)
            setattr(self, f"buy_composite_group_{group_index}_enabled_check", enabled)
            setattr(self, f"buy_composite_group_{group_index}_logic_combo", logic)

            row.addWidget(enabled)
            row.addWidget(logic)
            for filter_name, label in filter_specs:
                check = make_check(label, filter_name in default_filters[group_index])
                setattr(self, f"buy_composite_group_{group_index}_{filter_name}_check", check)
                row.addWidget(check)
            row.addStretch(1)

        def connect_signal(widget, signal_name, callback):
            signal = getattr(widget, signal_name, None)
            if hasattr(signal, "connect"):
                signal.connect(callback)

        connect_signal(
            self.buy_composite_enabled_check,
            "toggled",
            lambda *_args: self._sync_buy_composite_control_states(),
        )
        for group_index in (1, 2):
            group_enabled = getattr(self, f"buy_composite_group_{group_index}_enabled_check")
            connect_signal(group_enabled, "toggled", lambda *_args: self._sync_buy_composite_control_states())

        self._sync_buy_composite_control_states()
        return box

    def _sync_buy_composite_control_states(self):
        enabled_widget = getattr(self, "buy_composite_enabled_check", None)
        composite_enabled = enabled_widget.isChecked() if hasattr(enabled_widget, "isChecked") else False

        for name in (
            "buy_composite_logic_combo",
            "buy_composite_include_unreferenced_combo",
        ):
            widget = getattr(self, name, None)
            if hasattr(widget, "setEnabled"):
                widget.setEnabled(composite_enabled and name != "buy_composite_include_unreferenced_combo")

        for group_index in (1, 2):
            group_enabled_widget = getattr(self, f"buy_composite_group_{group_index}_enabled_check", None)
            group_enabled = (
                group_enabled_widget.isChecked()
                if hasattr(group_enabled_widget, "isChecked")
                else False
            )
            if hasattr(group_enabled_widget, "setEnabled"):
                group_enabled_widget.setEnabled(composite_enabled)

            for name in [f"buy_composite_group_{group_index}_logic_combo"] + [
                f"buy_composite_group_{group_index}_{filter_name}_check"
                for filter_name in ("rsi", "moving_average", "price_compare", "bollinger", "ocr")
            ]:
                widget = getattr(self, name, None)
                if hasattr(widget, "setEnabled"):
                    widget.setEnabled(composite_enabled and group_enabled)

    def _make_buy_method_overview_controls_legacy(self):
        box = QGroupBox("")
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

        section_title_style = "font-size: 9pt; font-weight: bold;"
        title_indent = 0
        child_indent = 12
        grandchild_indent = 24

        # 가격방식: 단일호가 / 다중 상향·하향은 상호배타.
        row = add_row(title_indent)
        buy_method_title_label = QLabel("매수방식")
        buy_method_title_label.setStyleSheet(section_title_style)
        row.addWidget(buy_method_title_label)
        row.addStretch(1)

        row = add_row(child_indent)
        self.buy_method_single_check = QCheckBox("단일호가")
        self.buy_method_single_check.setChecked(True)
        self.buy_method_single_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_method_single_check)
        row.addStretch(1)

        row = add_row(child_indent)
        self.buy_method_multi_check = QCheckBox("상향")
        self.buy_method_multi_check.setChecked(False)
        self.buy_method_multi_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_method_multi_check)

        # 매수방식은 단일호가/다중호가 중 하나만 선택한다.
        # 기존 체크박스 표현은 유지하고, 선택 동작만 배타 처리한다.
        self.buy_method_price_button_group = QButtonGroup(self)
        self.buy_method_price_button_group.setExclusive(True)
        self.buy_method_price_button_group.addButton(self.buy_method_single_check)
        self.buy_method_price_button_group.addButton(self.buy_method_multi_check)

        self.buy_method_multi_up_line = make_line("0", 38)
        self.buy_method_multi_down_line = make_line("2", 38)
        self.buy_method_multi_total_label = QLabel("다중 3호가")

        row.addWidget(QLabel("["))
        row.addWidget(self.buy_method_multi_up_line)
        row.addWidget(QLabel("] / 기준가 1 / 하향 ["))
        row.addWidget(self.buy_method_multi_down_line)
        row.addWidget(QLabel("] |"))
        row.addWidget(self.buy_method_multi_total_label)
        row.addStretch(1)

        def _sync_multi_hoga_total():
            try:
                up = int(self.buy_method_multi_up_line.text().strip())
            except ValueError:
                up = 0
            try:
                down = int(self.buy_method_multi_down_line.text().strip())
            except ValueError:
                down = 0
            self.buy_method_multi_total_label.setText(f"다중 {up + 1 + down}호가")

        self.buy_method_multi_up_line.textChanged.connect(_sync_multi_hoga_total)
        self.buy_method_multi_down_line.textChanged.connect(_sync_multi_hoga_total)
        _sync_multi_hoga_total()

        row = add_row(title_indent)
        point_title_label = QLabel("다중지점")
        point_title_label.setStyleSheet(section_title_style)
        row.addWidget(point_title_label)
        row.addStretch(1)

        # 다중지점 하위 항목: 시간 / 평단 조건은 상호배타.
        # 하위 행은 제목보다 과하게 들어가지 않도록 반칸만 들여쓴다.

        row = add_row(child_indent)
        self.buy_method_time_point_check = QCheckBox("시간")
        self.buy_method_time_point_check.setChecked(False)
        self.buy_method_time_point_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_method_time_point_check)
        row.addWidget(make_line("30", 38))
        row.addWidget(make_combo(["분", "초", "봉"], "초", 58))
        row.addWidget(make_combo(["이내", "간격"], "이내", 72))
        row.addWidget(make_line("3", 38))
        row.addWidget(QLabel("회"))
        self.buy_method_time_point_order_combo = make_combo(["주문가", "현재가", "시장가"], "주문가", 96)
        row.addWidget(self.buy_method_time_point_order_combo)
        row.addStretch(1)

        row = add_row(child_indent)
        self.buy_method_avg_point_check = QCheckBox("")
        self.buy_method_avg_point_check.setChecked(False)
        self.buy_method_avg_point_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_method_avg_point_check)
        self.buy_method_avg_point_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 86)
        row.addWidget(self.buy_method_avg_point_left_combo)
        row.addWidget(QLabel("대비"))
        self.buy_method_avg_point_right_combo = make_combo(["주문가", "현재가", "평단가"], "평단가", 86)
        row.addWidget(self.buy_method_avg_point_right_combo)
        self.buy_method_avg_point_basis_combo = self.buy_method_avg_point_left_combo
        self.buy_method_avg_point_direction_combo = make_combo(["상향", "하향", "상하"], "하향", 72)
        row.addWidget(self.buy_method_avg_point_direction_combo)
        self.buy_method_avg_point_value_line = make_line("0.15", 48)
        row.addWidget(self.buy_method_avg_point_value_line)
        row.addWidget(QLabel("%"))
        self.buy_method_avg_point_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이하", 72)
        row.addWidget(self.buy_method_avg_point_compare_combo)
        row.addWidget(QLabel("/"))
        self.buy_method_avg_point_count_line = make_line("3", 38)
        row.addWidget(self.buy_method_avg_point_count_line)
        row.addWidget(QLabel("회"))
        row.addStretch(1)

        # 마지막회차 능동매수는 다중지점의 하위 보조옵션이므로
        # 시간/가격비교 행보다 약 두 칸 더 들여쓴다.
        last_active_indent = child_indent + 24
        last_active_detail_indent = last_active_indent + 12

        row = add_row(last_active_indent)
        self.buy_method_last_point_active_buy_check = QCheckBox("마지막회차 능동매수")
        self.buy_method_last_point_active_buy_check.setChecked(False)
        self.buy_method_last_point_active_buy_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_method_last_point_active_buy_check)
        row.addStretch(1)

        row = add_row(last_active_detail_indent)
        self.buy_method_last_point_active_buy_label = QLabel("설정가에 평단가")
        self.buy_method_last_point_active_buy_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 72)
        self.buy_method_last_point_active_buy_value_line = make_line("0.15", 48)
        self.buy_method_last_point_active_buy_unit_label = QLabel("%")
        self.buy_method_last_point_active_buy_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이내", 72)
        row.addWidget(self.buy_method_last_point_active_buy_label)
        row.addWidget(self.buy_method_last_point_active_buy_direction_combo)
        row.addWidget(self.buy_method_last_point_active_buy_value_line)
        row.addWidget(self.buy_method_last_point_active_buy_unit_label)
        row.addWidget(self.buy_method_last_point_active_buy_compare_combo)
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

        self.buy_method_avg_point_direction_combo.currentTextChanged.connect(
            lambda _: sync_direction_compare_combo(
                self.buy_method_avg_point_direction_combo,
                self.buy_method_avg_point_compare_combo,
            )
        )
        self.buy_method_last_point_active_buy_direction_combo.currentTextChanged.connect(
            lambda _: sync_direction_compare_combo(
                self.buy_method_last_point_active_buy_direction_combo,
                self.buy_method_last_point_active_buy_compare_combo,
            )
        )
        sync_direction_compare_combo(
            self.buy_method_avg_point_direction_combo,
            self.buy_method_avg_point_compare_combo,
        )
        sync_direction_compare_combo(
            self.buy_method_last_point_active_buy_direction_combo,
            self.buy_method_last_point_active_buy_compare_combo,
        )

        self._buy_method_exclusive_guard = False

        def sync_price_method(source):
            if self._buy_method_exclusive_guard:
                return
            self._buy_method_exclusive_guard = True
            try:
                if source is self.buy_method_single_check and source.isChecked():
                    self.buy_method_multi_check.setChecked(False)
                elif source is self.buy_method_multi_check and source.isChecked():
                    self.buy_method_single_check.setChecked(False)
                elif not self.buy_method_single_check.isChecked() and not self.buy_method_multi_check.isChecked():
                    self.buy_method_single_check.setChecked(True)
            finally:
                self._buy_method_exclusive_guard = False

        self.buy_method_single_check.toggled.connect(lambda _: sync_price_method(self.buy_method_single_check))
        self.buy_method_multi_check.toggled.connect(lambda _: sync_price_method(self.buy_method_multi_check))

        def sync_time_point_order_combo():
            market_index = self.buy_method_time_point_order_combo.findText("시장가")
            if market_index >= 0:
                item = self.buy_method_time_point_order_combo.model().item(market_index)
                if item is not None:
                    if self.buy_method_single_check.isChecked():
                        item.setFlags(item.flags() | Qt.ItemIsEnabled)
                    else:
                        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                        if self.buy_method_time_point_order_combo.currentText() == "시장가":
                            self.buy_method_time_point_order_combo.setCurrentText("현재가")

        self.buy_method_single_check.toggled.connect(lambda _: sync_time_point_order_combo())
        self.buy_method_multi_check.toggled.connect(lambda _: sync_time_point_order_combo())
        sync_time_point_order_combo()

        self._buy_method_point_guard = False
        self._buy_method_selected_point_check = None

        def enforce_point_method_exclusive(preferred=None):
            """다중지점 하위의 시간/가격비교는 동시에 선택될 수 없다.

            체크박스 표현은 유지하되, 배타 동작은 이 함수에서만 처리한다.
            기존 가격방식 배타 처리와 guard를 공유하지 않아 다른 영역의 상태 변경에
            끌려가지 않게 한다.
            """
            if self._buy_method_point_guard:
                return
            self._buy_method_point_guard = True
            try:
                time_checked = self.buy_method_time_point_check.isChecked()
                avg_checked = self.buy_method_avg_point_check.isChecked()

                if preferred is self.buy_method_time_point_check and time_checked:
                    self.buy_method_avg_point_check.setChecked(False)
                    self._buy_method_selected_point_check = self.buy_method_time_point_check
                elif preferred is self.buy_method_avg_point_check and avg_checked:
                    self.buy_method_time_point_check.setChecked(False)
                    self._buy_method_selected_point_check = self.buy_method_avg_point_check
                elif time_checked and avg_checked:
                    # 외부 로드/초기화로 둘 다 켜진 경우에는 마지막 선택 기록을 우선한다.
                    if self._buy_method_selected_point_check is self.buy_method_avg_point_check:
                        self.buy_method_time_point_check.setChecked(False)
                    else:
                        self.buy_method_avg_point_check.setChecked(False)
                elif not time_checked and not avg_checked:
                    self._buy_method_selected_point_check = None
            finally:
                self._buy_method_point_guard = False

        def sync_point_method(source, checked):
            if checked:
                self._buy_method_selected_point_check = source
            enforce_point_method_exclusive(source)

        self.buy_method_time_point_check.toggled.connect(
            lambda checked, c=self.buy_method_time_point_check: sync_point_method(c, checked)
        )
        self.buy_method_avg_point_check.toggled.connect(
            lambda checked, c=self.buy_method_avg_point_check: sync_point_method(c, checked)
        )

        # 다중지점은 다중시간/다중비율(가격비교) 중 하나만 선택한다.
        # QButtonGroup은 객체 보관용으로만 쓰고, 실제 배타 처리는 위 함수에서 수행한다.
        self.buy_method_point_button_group = QButtonGroup(self)
        self.buy_method_point_button_group.setExclusive(False)
        self.buy_method_point_button_group.addButton(self.buy_method_time_point_check)
        self.buy_method_point_button_group.addButton(self.buy_method_avg_point_check)

        # 마지막회차 능동매수는 다중지점 하위 항목(시간/가격비교) 중
        # 하나라도 선택되어 있을 때만 체크 가능하다.
        # self 속성은 이후 다른 탭/구성에서 덮일 가능성이 있으므로,
        # 현재 매수방식 박스에 실제 배치된 위젯을 로컬 참조로 고정한다.
        local_time_point_check = self.buy_method_time_point_check
        local_avg_point_check = self.buy_method_avg_point_check
        local_last_active_check = self.buy_method_last_point_active_buy_check
        local_last_active_widgets = [
            self.buy_method_last_point_active_buy_label,
            self.buy_method_last_point_active_buy_direction_combo,
            self.buy_method_last_point_active_buy_value_line,
            self.buy_method_last_point_active_buy_unit_label,
            self.buy_method_last_point_active_buy_compare_combo,
        ]

        def sync_last_point_active_buy_enabled():
            enforce_point_method_exclusive(getattr(self, "_buy_method_selected_point_check", None))
            point_enabled = (
                local_time_point_check.isChecked()
                or local_avg_point_check.isChecked()
            )

            local_last_active_check.setEnabled(point_enabled)
            if not point_enabled:
                local_last_active_check.setChecked(False)

            active_detail_enabled = point_enabled and local_last_active_check.isChecked()

            if point_enabled:
                sync_direction_compare_combo(
                    self.buy_method_last_point_active_buy_direction_combo,
                    self.buy_method_last_point_active_buy_compare_combo,
                )

            for widget in local_last_active_widgets:
                widget.setEnabled(active_detail_enabled)

        for check in (local_time_point_check, local_avg_point_check, local_last_active_check):
            check.toggled.connect(lambda _checked=False: sync_last_point_active_buy_enabled())
            check.stateChanged.connect(lambda _state=0: sync_last_point_active_buy_enabled())

        enforce_point_method_exclusive()
        sync_last_point_active_buy_enabled()
        QTimer.singleShot(0, enforce_point_method_exclusive)
        QTimer.singleShot(0, sync_last_point_active_buy_enabled)

        row = add_row(title_indent)
        detail_situation_title_label = QLabel("세부상황설정")
        detail_situation_title_label.setStyleSheet(section_title_style)
        row.addWidget(detail_situation_title_label)
        row.addStretch(1)

        # 세부상황설정: 일반매수 / 가격비교매수는 체크박스 형태로 배치한다.
        # 아직 실제 매수 로직 연결 전 단계이므로 UI 배치와 기본 활성 상태만 구성한다.
        row = add_row(child_indent)
        self.buy_detail_normal_buy_check = QCheckBox("일반매수")
        self.buy_detail_normal_buy_check.setChecked(True)
        self.buy_detail_normal_buy_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_detail_normal_buy_check)
        self.buy_detail_normal_policy_combo = make_combo(["회차기준", "예산기준", "능동매수"], "회차기준", 96)
        row.addWidget(self.buy_detail_normal_policy_combo)
        row.addWidget(QLabel("해당설정"))
        row.addStretch(1)

        row = add_row(child_indent)
        self.buy_detail_price_compare_check = QCheckBox("가격비교매수")
        self.buy_detail_price_compare_check.setChecked(False)
        self.buy_detail_price_compare_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_detail_price_compare_check)
        row.addStretch(1)

        price_compare_detail_indent = child_indent + 24

        row = add_row(price_compare_detail_indent)
        row.addWidget(QLabel("평단"))
        self.buy_detail_avg_above_operator_combo = make_combo([">=", ">"], ">=", 58)
        row.addWidget(self.buy_detail_avg_above_operator_combo)
        row.addWidget(QLabel("현재가"))
        self.buy_detail_avg_above_policy_combo = make_combo(["회차기준", "예산기준", "능동매수"], "회차기준", 96)
        row.addWidget(self.buy_detail_avg_above_policy_combo)
        row.addWidget(QLabel("해당설정"))
        row.addStretch(1)

        row = add_row(price_compare_detail_indent)
        row.addWidget(QLabel("평단"))
        self.buy_detail_avg_below_operator_combo = make_combo(["<", "<="], "<", 58)
        row.addWidget(self.buy_detail_avg_below_operator_combo)
        row.addWidget(QLabel("현재가"))
        self.buy_detail_avg_below_policy_combo = make_combo(["회차기준", "예산기준"], "회차기준", 96)
        row.addWidget(self.buy_detail_avg_below_policy_combo)
        row.addWidget(QLabel("해당설정"))
        row.addStretch(1)

        row = add_row(price_compare_detail_indent)
        self.buy_detail_prev_price_no_buy_check = QCheckBox("직전가 대비 현재가")
        self.buy_detail_prev_price_no_buy_check.setChecked(False)
        self.buy_detail_prev_price_no_buy_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.buy_detail_prev_price_no_buy_check)
        self.buy_detail_prev_price_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 72)
        row.addWidget(self.buy_detail_prev_price_direction_combo)
        self.buy_detail_prev_price_value_line = make_line("0.5", 48)
        row.addWidget(self.buy_detail_prev_price_value_line)
        row.addWidget(QLabel("%"))
        # 직전가 대비 현재가 비교 콤보는 방향값에 따라 항목 자체가 바뀐다.
        # 상향/하향: 이상/이하, 상하: 이내/이탈.
        self.buy_detail_prev_price_compare_combo = make_combo(["이내", "이탈"], "이탈", 72)
        row.addWidget(self.buy_detail_prev_price_compare_combo)
        row.addWidget(QLabel("매수안함"))
        row.addStretch(1)

        prev_price_direction_combo = self.buy_detail_prev_price_direction_combo
        prev_price_compare_combo = self.buy_detail_prev_price_compare_combo

        def sync_prev_price_no_buy_compare_combo():
            direction = prev_price_direction_combo.currentText().strip()
            current_text = prev_price_compare_combo.currentText().strip()
            if direction == "상하":
                items = ["이내", "이탈"]
                default_text = "이탈"
            else:
                items = ["이상", "이하"]
                default_text = "이상"

            current_items = [prev_price_compare_combo.itemText(i) for i in range(prev_price_compare_combo.count())]
            if current_items != items:
                prev_price_compare_combo.blockSignals(True)
                try:
                    prev_price_compare_combo.clear()
                    prev_price_compare_combo.addItems(items)
                finally:
                    prev_price_compare_combo.blockSignals(False)

            prev_price_compare_combo.setCurrentText(current_text if current_text in items else default_text)

        prev_price_direction_combo.currentTextChanged.connect(lambda _text="": sync_prev_price_no_buy_compare_combo())
        prev_price_direction_combo.currentIndexChanged[int].connect(lambda _index=0: sync_prev_price_no_buy_compare_combo())
        prev_price_direction_combo.activated[int].connect(lambda _index=0: QTimer.singleShot(0, sync_prev_price_no_buy_compare_combo))
        prev_price_direction_combo.activated[str].connect(lambda _text="": QTimer.singleShot(0, sync_prev_price_no_buy_compare_combo))

        # 일부 환경에서 QComboBox 선택 후 신호가 늦게 반영되는 경우를 대비해
        # 이 콤보만 eventFilter 후속 동기화 대상에 등록한다.
        if not hasattr(self, "_direction_compare_sync_handlers"):
            self._direction_compare_sync_handlers = {}
        self._direction_compare_sync_handlers[prev_price_direction_combo] = sync_prev_price_no_buy_compare_combo
        prev_price_direction_combo.installEventFilter(self)

        sync_prev_price_no_buy_compare_combo()
        QTimer.singleShot(0, sync_prev_price_no_buy_compare_combo)

        # 직전가 대비 현재가 매수안함: 체크박스가 켜지면 세부 설정을 즉시 활성화한다.
        # QButtonGroup/외부 초기화 이후에도 상태가 다시 덮이지 않도록 로컬 참조와 지연 동기화를 함께 둔다.
        local_prev_price_no_buy_check = self.buy_detail_prev_price_no_buy_check
        local_prev_price_no_buy_widgets = [
            self.buy_detail_prev_price_direction_combo,
            self.buy_detail_prev_price_value_line,
            self.buy_detail_prev_price_compare_combo,
        ]

        def sync_prev_price_no_buy_widgets():
            enabled = local_prev_price_no_buy_check.isChecked()
            if enabled:
                sync_prev_price_no_buy_compare_combo()
            for widget in local_prev_price_no_buy_widgets:
                widget.setEnabled(enabled)

        local_prev_price_no_buy_check.toggled.connect(
            lambda _checked=False: sync_prev_price_no_buy_widgets()
        )
        local_prev_price_no_buy_check.stateChanged.connect(
            lambda _state=0: sync_prev_price_no_buy_widgets()
        )
        sync_prev_price_no_buy_widgets()
        QTimer.singleShot(0, sync_prev_price_no_buy_widgets)

        # 세부상황설정의 일반매수/가격비교매수는 체크박스 UI를 유지하되
        # QButtonGroup의 exclusive 동작으로 실제 체크 상태가 동시에 켜지지 않도록 고정한다.
        # 수동 클릭뿐 아니라 초기값/외부 setChecked 호출 후에도 항상 1개만 남긴다.
        self.buy_detail_mode_group = QButtonGroup(self)
        self.buy_detail_mode_group.setExclusive(True)
        self.buy_detail_mode_group.addButton(self.buy_detail_normal_buy_check)
        self.buy_detail_mode_group.addButton(self.buy_detail_price_compare_check)

        self._buy_detail_mode_exclusive_guard = False

        def sync_detail_mode_exclusive(source_check=None):
            if self._buy_detail_mode_exclusive_guard:
                return
            self._buy_detail_mode_exclusive_guard = True
            try:
                normal_checked = self.buy_detail_normal_buy_check.isChecked()
                price_checked = self.buy_detail_price_compare_check.isChecked()

                if normal_checked and price_checked:
                    if source_check is self.buy_detail_price_compare_check:
                        self.buy_detail_normal_buy_check.setChecked(False)
                    else:
                        self.buy_detail_price_compare_check.setChecked(False)
                elif not normal_checked and not price_checked:
                    # 둘 다 해제된 상태는 허용하지 않는다. 기본값은 일반매수.
                    self.buy_detail_normal_buy_check.setChecked(True)
            finally:
                self._buy_detail_mode_exclusive_guard = False

        self.buy_detail_normal_buy_check.toggled.connect(
            lambda _checked=False, c=self.buy_detail_normal_buy_check: sync_detail_mode_exclusive(c)
        )
        self.buy_detail_price_compare_check.toggled.connect(
            lambda _checked=False, c=self.buy_detail_price_compare_check: sync_detail_mode_exclusive(c)
        )
        QTimer.singleShot(0, lambda: sync_detail_mode_exclusive(self.buy_detail_normal_buy_check))
        QTimer.singleShot(0, sync_prev_price_no_buy_widgets)

        return box

    def _make_buy_avg_overview_controls(self, sections=None):
        section_set = set(sections or ("exit", "close"))
        flat_mode = "flat" in section_set
        show_cycle = "cycle" in section_set
        show_finish = bool({"exit", "close"} & section_set)
        box = QGroupBox("")
        if flat_mode:
            box.setStyleSheet(
                "QGroupBox { border: 0px; margin-top: 0px; background: transparent; font-weight: bold; } "
                "QGroupBox::title { padding: 0px; }"
            )
        else:
            box.setStyleSheet(
                "QGroupBox { font-weight: bold; } "
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        grid = QGridLayout(box)
        if flat_mode:
            grid.setContentsMargins(0, 0, 0, 0)
        else:
            grid.setContentsMargins(8, 12, 8, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        cycle_column = QWidget()
        cycle_layout = QVBoxLayout(cycle_column)
        cycle_layout.setContentsMargins(0, 0, 0, 0)
        cycle_layout.setSpacing(2)
        if show_cycle:
            grid.addWidget(cycle_column, 0, 0)
        else:
            cycle_column.setParent(box)
            cycle_column.hide()

        finish_column = QWidget()
        finish_layout = QVBoxLayout(finish_column)
        finish_layout.setContentsMargins(0, 0, 0, 0)
        finish_layout.setSpacing(4)
        if show_finish:
            grid.addWidget(finish_column, 0, 1 if show_cycle else 0)
        else:
            finish_column.setParent(box)
            finish_column.hide()

        layout = cycle_layout

        def make_line(text, width, align=Qt.AlignRight):
            line = QLineEdit()
            line.setText(text)
            line.setFixedWidth(width)
            line.setFixedHeight(26)
            line.setAlignment(align)
            line.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(26)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        def make_label(text, width=None, align=Qt.AlignVCenter | Qt.AlignLeft):
            label = QLabel(text)
            label.setFixedHeight(26)
            label.setAlignment(align)
            label.setStyleSheet("font-size: 8pt;")
            if width is not None:
                label.setFixedWidth(width)
            return label

        def add_header(text):
            label = QLabel(text)
            label.setFixedHeight(24)
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            label.setStyleSheet("font-size: 9pt; font-weight: bold;")
            layout.addWidget(label, 0, Qt.AlignLeft)
            return label

        def add_row(indent=16):
            row = QHBoxLayout()
            row.setContentsMargins(indent, 0, 0, 0)
            row.setSpacing(4)
            layout.addLayout(row)
            return row

        def set_compare_items_by_direction(direction_combo, compare_combo):
            direction = direction_combo.currentText().strip()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]
            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = compare_combo.findText(item_text)
                if index >= 0:
                    compare_combo.view().setRowHidden(index, item_text not in visible_items)
            if compare_combo.currentText() not in visible_items:
                compare_combo.setCurrentText("이내" if direction == "상하" else "이상")

        def set_widgets_enabled(widgets, enabled):
            for widget in widgets:
                widget.setEnabled(enabled)

        # ▶순환설정: 매도설정 3번 후속매도반복설정 형식을 매수 중간 박스에 배치한다.
        add_header("▶순환설정")

        hoga_row = add_row()
        cycle_hoga_combo = make_combo(["단일호가", "다중호가"], "다중호가", 116)
        hoga_stack = QStackedWidget()
        hoga_stack.setFixedHeight(26)
        hoga_row.addWidget(cycle_hoga_combo)
        hoga_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        hoga_row.addWidget(hoga_stack)

        single_hoga_widget = QWidget()
        single_hoga_layout = QHBoxLayout(single_hoga_widget)
        single_hoga_layout.setContentsMargins(0, 0, 0, 0)
        single_hoga_layout.setSpacing(4)
        cycle_order_combo = make_combo(["주문가", "현재가", "시장가"], "주문가", 92)
        single_hoga_layout.addWidget(cycle_order_combo)
        single_hoga_layout.addStretch(1)
        hoga_stack.addWidget(single_hoga_widget)

        multi_hoga_widget = QWidget()
        multi_hoga_layout = QHBoxLayout(multi_hoga_widget)
        multi_hoga_layout.setContentsMargins(0, 0, 0, 0)
        multi_hoga_layout.setSpacing(4)
        cycle_hoga_up_line = make_line("0", 34)
        cycle_hoga_down_line = make_line("2", 34)
        cycle_hoga_total_label = make_label("| 3호가", 56)
        multi_hoga_layout.addWidget(make_label("상향", 42))
        multi_hoga_layout.addWidget(cycle_hoga_up_line)
        multi_hoga_layout.addWidget(make_label("/ 기본가 1 / 하향", 132))
        multi_hoga_layout.addWidget(cycle_hoga_down_line)
        multi_hoga_layout.addWidget(cycle_hoga_total_label)
        multi_hoga_layout.addStretch(1)
        hoga_stack.addWidget(multi_hoga_widget)
        hoga_row.addStretch(1)

        def update_cycle_hoga_total(*_args):
            try:
                up = int(cycle_hoga_up_line.text().strip() or "0")
            except ValueError:
                up = 0
            try:
                down = int(cycle_hoga_down_line.text().strip() or "0")
            except ValueError:
                down = 0
            cycle_hoga_total_label.setText(f"| {up + 1 + down}호가")

        def update_cycle_hoga_mode(*_args):
            hoga_stack.setCurrentIndex(max(cycle_hoga_combo.currentIndex(), 0))
            update_cycle_hoga_total()

        cycle_hoga_up_line.textChanged.connect(update_cycle_hoga_total)
        cycle_hoga_down_line.textChanged.connect(update_cycle_hoga_total)
        cycle_hoga_combo.currentIndexChanged.connect(update_cycle_hoga_mode)
        update_cycle_hoga_mode()

        cycle_time_row = add_row()
        cycle_time_combo = make_combo(["선택없음", "다중시간", "다중비율"], "다중시간", 116)
        cycle_time_stack = QStackedWidget()
        cycle_time_stack.setFixedHeight(26)
        cycle_time_row.addWidget(cycle_time_combo)
        cycle_time_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        cycle_time_row.addWidget(cycle_time_stack)

        cycle_none_widget = QWidget()
        cycle_none_layout = QHBoxLayout(cycle_none_widget)
        cycle_none_layout.setContentsMargins(0, 0, 0, 0)
        cycle_none_layout.setSpacing(4)
        cycle_none_layout.addWidget(make_label("-", 20, Qt.AlignCenter))
        cycle_none_layout.addStretch(1)
        cycle_time_stack.addWidget(cycle_none_widget)

        cycle_multi_time_widget = QWidget()
        cycle_multi_time_layout = QHBoxLayout(cycle_multi_time_widget)
        cycle_multi_time_layout.setContentsMargins(0, 0, 0, 0)
        cycle_multi_time_layout.setSpacing(4)
        cycle_time_value_line = make_line("30", 34)
        cycle_time_unit_combo = make_combo(["분", "초", "봉"], "초", 60)
        cycle_time_range_combo = make_combo(["이내", "간격"], "이내", 76)
        cycle_time_count_line = make_line("3", 30)
        cycle_time_order_combo = make_combo(["주문가", "현재가"], "현재가", 92)
        cycle_multi_time_layout.addWidget(cycle_time_value_line)
        cycle_multi_time_layout.addWidget(cycle_time_unit_combo)
        cycle_multi_time_layout.addWidget(cycle_time_range_combo)
        cycle_multi_time_layout.addWidget(cycle_time_count_line)
        cycle_multi_time_layout.addWidget(make_label("회", 18))
        cycle_multi_time_layout.addWidget(cycle_time_order_combo)
        cycle_multi_time_layout.addStretch(1)
        cycle_time_stack.addWidget(cycle_multi_time_widget)

        cycle_ratio_widget = QWidget()
        cycle_ratio_layout = QHBoxLayout(cycle_ratio_widget)
        cycle_ratio_layout.setContentsMargins(0, 0, 0, 0)
        cycle_ratio_layout.setSpacing(4)
        cycle_ratio_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
        cycle_ratio_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 92)
        cycle_ratio_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        cycle_ratio_value_line = make_line("0.15", 46)
        cycle_ratio_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        cycle_ratio_count_line = make_line("3", 30)
        cycle_ratio_layout.addWidget(cycle_ratio_left_combo)
        cycle_ratio_layout.addWidget(make_label("대비", 36))
        cycle_ratio_layout.addWidget(cycle_ratio_right_combo)
        cycle_ratio_layout.addWidget(cycle_ratio_direction_combo)
        cycle_ratio_layout.addWidget(cycle_ratio_value_line)
        cycle_ratio_layout.addWidget(make_label("%", 14))
        cycle_ratio_layout.addWidget(cycle_ratio_compare_combo)
        cycle_ratio_layout.addWidget(make_label("/", 8, Qt.AlignCenter))
        cycle_ratio_layout.addWidget(cycle_ratio_count_line)
        cycle_ratio_layout.addWidget(make_label("회", 18))
        cycle_ratio_layout.addStretch(1)
        cycle_time_stack.addWidget(cycle_ratio_widget)
        cycle_time_row.addStretch(1)

        def update_cycle_time_mode(*_args):
            cycle_time_stack.setCurrentIndex(max(cycle_time_combo.currentIndex(), 0))

        def update_cycle_ratio_compare(*_args):
            set_compare_items_by_direction(cycle_ratio_direction_combo, cycle_ratio_compare_combo)

        cycle_time_combo.currentIndexChanged.connect(update_cycle_time_mode)
        cycle_ratio_direction_combo.currentTextChanged.connect(update_cycle_ratio_compare)
        update_cycle_time_mode()
        update_cycle_ratio_compare()

        cycle_situation_row = add_row()
        cycle_situation_combo = make_combo(["미체결", "가격비교"], "가격비교", 116)
        cycle_situation_stack = QStackedWidget()
        cycle_situation_stack.setFixedHeight(26)
        cycle_situation_row.addWidget(cycle_situation_combo)
        cycle_situation_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        cycle_situation_row.addWidget(cycle_situation_stack)

        cycle_pending_widget = QWidget()
        cycle_pending_layout = QHBoxLayout(cycle_pending_widget)
        cycle_pending_layout.setContentsMargins(0, 0, 0, 0)
        cycle_pending_layout.setSpacing(4)
        cycle_pending_scope_combo = make_combo(["매회", "일괄"], "매회", 66)
        cycle_pending_value_line = make_line("10", 34)
        cycle_pending_unit_combo = make_combo(["분", "초", "봉"], "초", 60)
        cycle_pending_layout.addWidget(cycle_pending_scope_combo)
        cycle_pending_layout.addWidget(make_label("기준", 36))
        cycle_pending_layout.addWidget(cycle_pending_value_line)
        cycle_pending_layout.addWidget(cycle_pending_unit_combo)
        cycle_pending_layout.addWidget(make_label("후 주문취소", 86))
        cycle_pending_layout.addStretch(1)
        cycle_situation_stack.addWidget(cycle_pending_widget)

        cycle_price_widget = QWidget()
        cycle_price_layout = QHBoxLayout(cycle_price_widget)
        cycle_price_layout.setContentsMargins(0, 0, 0, 0)
        cycle_price_layout.setSpacing(4)
        cycle_price_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
        cycle_price_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 92)
        cycle_price_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        cycle_price_value_line = make_line("0.15", 46)
        cycle_price_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        cycle_price_action_combo = make_combo(["매수리셋", "일괄취소"], "일괄취소", 100)
        cycle_price_layout.addWidget(cycle_price_left_combo)
        cycle_price_layout.addWidget(make_label("대비", 36))
        cycle_price_layout.addWidget(cycle_price_right_combo)
        cycle_price_layout.addWidget(cycle_price_direction_combo)
        cycle_price_layout.addWidget(cycle_price_value_line)
        cycle_price_layout.addWidget(make_label("%", 14))
        cycle_price_layout.addWidget(cycle_price_compare_combo)
        cycle_price_layout.addWidget(cycle_price_action_combo)
        cycle_price_layout.addStretch(1)
        cycle_situation_stack.addWidget(cycle_price_widget)
        cycle_situation_row.addStretch(1)

        def update_cycle_situation_mode(*_args):
            cycle_situation_stack.setCurrentIndex(0 if cycle_situation_combo.currentText().strip() == "미체결" else 1)

        def update_cycle_price_compare(*_args):
            set_compare_items_by_direction(cycle_price_direction_combo, cycle_price_compare_combo)

        cycle_situation_combo.currentTextChanged.connect(update_cycle_situation_mode)
        cycle_price_direction_combo.currentTextChanged.connect(update_cycle_price_compare)
        update_cycle_situation_mode()
        update_cycle_price_compare()

        if show_cycle:
            self.avg_policy_group = QButtonGroup(self)
            self.avg_round_increase_check = cycle_hoga_combo
            self.avg_amount_increase_check = cycle_time_combo
            self.avg_active_buy_check = cycle_situation_combo
            self.buy_cycle_hoga_mode_combo = cycle_hoga_combo
            self.buy_cycle_order_combo = cycle_order_combo
            self.buy_cycle_hoga_up_line = cycle_hoga_up_line
            self.buy_cycle_hoga_down_line = cycle_hoga_down_line
            self.buy_cycle_time_mode_combo = cycle_time_combo
            self.buy_cycle_time_value_line = cycle_time_value_line
            self.buy_cycle_time_unit_combo = cycle_time_unit_combo
            self.buy_cycle_time_range_combo = cycle_time_range_combo
            self.buy_cycle_time_count_line = cycle_time_count_line
            self.buy_cycle_time_order_combo = cycle_time_order_combo
            self.buy_cycle_ratio_left_combo = cycle_ratio_left_combo
            self.buy_cycle_ratio_right_combo = cycle_ratio_right_combo
            self.buy_cycle_ratio_direction_combo = cycle_ratio_direction_combo
            self.buy_cycle_ratio_value_line = cycle_ratio_value_line
            self.buy_cycle_ratio_compare_combo = cycle_ratio_compare_combo
            self.buy_cycle_ratio_count_line = cycle_ratio_count_line
            self.buy_cycle_situation_mode_combo = cycle_situation_combo
            self.buy_cycle_pending_scope_combo = cycle_pending_scope_combo
            self.buy_cycle_pending_value_line = cycle_pending_value_line
            self.buy_cycle_pending_unit_combo = cycle_pending_unit_combo
            self.buy_cycle_price_left_combo = cycle_price_left_combo
            self.buy_cycle_price_right_combo = cycle_price_right_combo
            self.buy_cycle_price_direction_combo = cycle_price_direction_combo
            self.buy_cycle_price_value_line = cycle_price_value_line
            self.buy_cycle_price_compare_combo = cycle_price_compare_combo
            self.buy_cycle_price_action_combo = cycle_price_action_combo
            # 순환설정과 이탈조건/회차마감이 별도 박스로 분리되어도
            # 제한시간 비활성 조건을 유지하기 위한 안전 참조.
            self._buy_cycle_time_combo = cycle_time_combo
            self._buy_cycle_situation_combo = cycle_situation_combo

            # 이탈조건 박스가 별도 호출에서 이미 만들어졌거나 이후 만들어지는 경우를 모두 처리한다.
            # 로컬 클로저 참조가 아니라 등록된 updater를 호출해 제한시간 활성조건을 동기화한다.
            for updater in list(getattr(self, "_buy_exit_time_state_updaters", [])):
                try:
                    updater()
                except RuntimeError:
                    pass

        if not show_finish:
            return box

        layout = finish_layout
        add_header("▶이탈조건")

        exit_checks = []

        exit_price_row = add_row()
        exit_price_check = QCheckBox()
        exit_price_check.setChecked(False)
        exit_price_check.setFixedWidth(22)
        exit_price_row.addWidget(exit_price_check)
        exit_price_row.addWidget(make_label("가격비교", 92))
        exit_price_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        exit_price_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
        exit_price_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 92)
        exit_price_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        exit_price_value_line = make_line("0.15", 46)
        exit_price_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        exit_price_row.addWidget(exit_price_left_combo)
        exit_price_row.addWidget(make_label("대비", 36))
        exit_price_row.addWidget(exit_price_right_combo)
        exit_price_row.addWidget(exit_price_direction_combo)
        exit_price_row.addWidget(exit_price_value_line)
        exit_price_row.addWidget(make_label("%", 14))
        exit_price_row.addWidget(exit_price_compare_combo)
        exit_price_row.addStretch(1)
        exit_price_widgets = [
            exit_price_left_combo,
            exit_price_right_combo,
            exit_price_direction_combo,
            exit_price_value_line,
            exit_price_compare_combo,
        ]

        exit_count_row = add_row()
        exit_count_check = QCheckBox()
        exit_count_check.setChecked(False)
        exit_count_check.setFixedWidth(22)
        exit_count_row.addWidget(exit_count_check)
        exit_count_row.addWidget(make_label("반복횟수", 92))
        exit_count_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        exit_count_line = make_line("3", 34)
        exit_count_row.addWidget(exit_count_line)
        exit_count_row.addWidget(make_label("회", 18))
        exit_count_row.addStretch(1)
        exit_count_widgets = [exit_count_line]

        exit_time_row = add_row()
        exit_time_check = QCheckBox()
        exit_time_check.setChecked(False)
        exit_time_check.setFixedWidth(22)
        exit_time_row.addWidget(exit_time_check)
        exit_time_row.addWidget(make_label("제한시간", 92))
        exit_time_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        exit_time_line = make_line("2", 34)
        exit_time_unit_combo = make_combo(["분", "초", "봉"], "분", 60)
        exit_time_row.addWidget(exit_time_line)
        exit_time_row.addWidget(exit_time_unit_combo)
        exit_time_row.addStretch(1)
        exit_time_widgets = [exit_time_line, exit_time_unit_combo]

        self.buy_exit_price_check = exit_price_check
        self.buy_exit_price_left_combo = exit_price_left_combo
        self.buy_exit_price_right_combo = exit_price_right_combo
        self.buy_exit_price_direction_combo = exit_price_direction_combo
        self.buy_exit_price_value_line = exit_price_value_line
        self.buy_exit_price_compare_combo = exit_price_compare_combo
        self.buy_exit_count_check = exit_count_check
        self.buy_exit_count_line = exit_count_line
        self.buy_exit_time_check = exit_time_check
        self.buy_exit_time_line = exit_time_line
        self.buy_exit_time_unit_combo = exit_time_unit_combo

        exit_checks.extend([exit_price_check, exit_count_check, exit_time_check])

        def update_exit_price_compare(*_args):
            set_compare_items_by_direction(exit_price_direction_combo, exit_price_compare_combo)

        cycle_time_source = getattr(self, "_buy_cycle_time_combo", None)
        cycle_situation_source = getattr(self, "_buy_cycle_situation_combo", None)

        def update_exit_widgets_enabled(*_args):
            set_widgets_enabled(exit_price_widgets, exit_price_check.isChecked())
            set_widgets_enabled(exit_count_widgets, exit_count_check.isChecked())

            # 순환설정에서 시간 기반 제어가 이미 사용 중이면 이탈조건의
            # 제한시간은 중복 시간 조건이 되므로 선택/입력을 막는다.
            # 순환설정과 이탈조건/회차마감은 현재 서로 다른 박스에서 생성될 수
            # 있으므로 로컬 클로저가 아니라 self에 저장한 현재 순환 콤보를 참조한다.
            def _is_live_widget(widget):
                try:
                    return widget is not None and not sip.isdeleted(widget)
                except Exception:
                    return False

            if _is_live_widget(cycle_time_source):
                cycle_time_active = cycle_time_source.currentText().strip() == "다중시간"
            else:
                cycle_time_active = False

            if _is_live_widget(cycle_situation_source):
                cycle_pending_active = cycle_situation_source.currentText().strip() == "미체결"
            else:
                cycle_pending_active = False
            exit_time_blocked = cycle_time_active or cycle_pending_active
            if exit_time_blocked:
                exit_time_check.setChecked(False)
            exit_time_check.setEnabled(not exit_time_blocked)
            set_widgets_enabled(exit_time_widgets, (not exit_time_blocked) and exit_time_check.isChecked())
            update_cycle_close_policy()

        exit_price_direction_combo.currentTextChanged.connect(update_exit_price_compare)
        if not hasattr(self, "_buy_exit_time_state_updaters"):
            self._buy_exit_time_state_updaters = []
        self._buy_exit_time_state_updaters.append(update_exit_widgets_enabled)

        cycle_time_signal_source = cycle_time_source
        cycle_situation_signal_source = cycle_situation_source
        for combo in (cycle_time_signal_source, cycle_situation_signal_source):
            try:
                if combo is not None and not sip.isdeleted(combo):
                    combo.currentTextChanged.connect(update_exit_widgets_enabled)
                    combo.currentIndexChanged.connect(update_exit_widgets_enabled)
            except RuntimeError:
                pass
        for check in exit_checks:
            check.toggled.connect(update_exit_widgets_enabled)
            check.stateChanged.connect(update_exit_widgets_enabled)
        update_exit_price_compare()

        layout.addSpacing(4)
        add_header("▶회차마감")
        close_row = add_row()
        cycle_close_carry_check = QCheckBox("다음신호로 이월")
        cycle_close_finish_check = QCheckBox("현상태로 회차마감")
        for result_check in (cycle_close_carry_check, cycle_close_finish_check):
            result_check.setChecked(True)
            result_check.setEnabled(False)
            result_check.setFixedHeight(26)
            result_check.setMinimumWidth(150)
            result_check.setStyleSheet("QCheckBox { font-size: 8pt; color: #003366; font-weight: bold; }")
            close_row.addWidget(result_check)
        close_row.addStretch(1)

        def update_cycle_close_policy():
            has_exit_condition = any(check.isChecked() for check in exit_checks)
            cycle_close_carry_check.setVisible(not has_exit_condition)
            cycle_close_finish_check.setVisible(has_exit_condition)
            cycle_close_carry_check.setChecked(True)
            cycle_close_finish_check.setChecked(True)
            close_row.invalidate()

        update_exit_widgets_enabled()
        update_cycle_close_policy()

        # 기존 저장/로드 코드가 평단관리 속성명을 찾을 가능성에 대비해 일부 별칭은 유지한다.
        if show_cycle:
            self.avg_policy_group = QButtonGroup(self)
            self.avg_round_increase_check = cycle_hoga_combo
            self.avg_amount_increase_check = cycle_time_combo
            self.avg_active_buy_check = cycle_situation_combo

        return box

    def _make_buy_cancel_overview_controls(self):
        return self._make_common_cancel_overview_controls("미체결정책", "매수주문", "buy")

    def _make_buy_complete_overview_controls(self):
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
        self.complete_current_state_check = QCheckBox("현상태로 완료판정")
        self.complete_current_state_check.setChecked(True)
        self.complete_current_state_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.complete_current_state_check)
        row.addStretch(1)

        row = add_row(8)
        self.complete_policy_remain_buy_option_check = QCheckBox("잔량매수")
        self.complete_policy_remain_buy_option_check.setChecked(True)
        self.complete_policy_remain_buy_option_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.complete_policy_remain_buy_option_check)
        row.addStretch(1)

        self.complete_remain_buy_gap_widgets = []
        self.complete_remain_buy_detail_row_controls = []
        self.complete_fill_ratio_widgets = []

        def sync_remain_buy_direction_compare_combo(direction_combo, compare_combo):
            direction = direction_combo.currentText()
            visible_items = ["이내", "이탈"] if direction == "상하" else ["이상", "이하"]

            for item_text in ["이상", "이하", "이내", "이탈"]:
                index = compare_combo.findText(item_text)
                if index >= 0:
                    compare_combo.view().setRowHidden(index, item_text not in visible_items)

            if compare_combo.currentText() not in visible_items:
                compare_combo.setCurrentText("이내" if direction == "상하" else "이하")
            compare_combo.setEnabled(True)

        self.complete_after_cancel_check = QCheckBox()
        self.complete_after_cancel_check.setChecked(True)
        self.complete_after_cancel_check.setVisible(False)

        def add_remain_buy_detail_row(
            row_index,
            left_basis="주문가",
            right_basis="현재가",
            direction="상하",
            value="0.25",
            compare="이내",
            action="매수안함",
            logic="AND",
            checked=False,
        ):
            # 잔량매수 하위 상세행: 잔량매수보다 반칸만 들여쓰기
            row = add_row(14)
            check = QCheckBox()
            check.setChecked(checked)
            setattr(self, f"complete_remain_buy_detail_{row_index}_check", check)
            row.addWidget(check)

            left_combo = make_combo(["주문가", "현재가", "평단가"], left_basis, 86)
            right_combo = make_combo(["주문가", "현재가", "평단가"], right_basis, 86)
            direction_combo = make_combo(["상향", "하향", "상하"], direction, 64)
            value_line = make_line(value, 44)
            unit_label = QLabel("%")
            compare_combo = make_combo(["이상", "이하", "이내", "이탈"], compare, 66)
            logic_combo = make_combo(["AND", "OR", "NOT"], logic, 62)

            setattr(self, f"complete_remain_buy_detail_{row_index}_left_combo", left_combo)
            setattr(self, f"complete_remain_buy_detail_{row_index}_right_combo", right_combo)
            setattr(self, f"complete_remain_buy_detail_{row_index}_direction_combo", direction_combo)
            setattr(self, f"complete_remain_buy_detail_{row_index}_value_line", value_line)
            setattr(self, f"complete_remain_buy_detail_{row_index}_compare_combo", compare_combo)
            setattr(self, f"complete_remain_buy_detail_{row_index}_logic_combo", logic_combo)

            row.addWidget(left_combo)
            row.addWidget(QLabel("대비"))
            row.addWidget(right_combo)
            row.addWidget(direction_combo)
            row.addWidget(value_line)
            row.addWidget(unit_label)
            row.addWidget(compare_combo)
            row.addStretch(1)
            row.addWidget(logic_combo)

            direction_combo.currentTextChanged.connect(
                lambda _: sync_remain_buy_direction_compare_combo(direction_combo, compare_combo)
            )
            sync_remain_buy_direction_compare_combo(direction_combo, compare_combo)

            detail_widgets = [
                left_combo,
                right_combo,
                direction_combo,
                value_line,
                unit_label,
                compare_combo,
                logic_combo,
            ]
            self.complete_remain_buy_gap_widgets.append(check)
            self.complete_remain_buy_detail_row_controls.append((
                check,
                detail_widgets,
                direction_combo,
                compare_combo,
            ))

            def sync_detail_row_enabled(_=None):
                parent_enabled = (
                    self.complete_policy_remain_buy_option_check.isChecked()
                )
                row_enabled = parent_enabled and check.isChecked()
                check.setEnabled(parent_enabled)
                for widget in detail_widgets:
                    widget.setEnabled(row_enabled)
                if row_enabled:
                    sync_remain_buy_direction_compare_combo(direction_combo, compare_combo)

            check.toggled.connect(sync_detail_row_enabled)
            sync_detail_row_enabled()

            return check

        self.complete_remain_buy_detail_1_check = add_remain_buy_detail_row(
            1, "주문가", "현재가", "상하", "0.25", "이내", "매수안함", "AND", True
        )
        self.complete_remain_buy_detail_2_check = add_remain_buy_detail_row(
            2, "주문가", "현재가", "상하", "0.25", "이내", "매수안함", "AND", False
        )
        self.complete_remain_buy_detail_3_check = add_remain_buy_detail_row(
            3, "주문가", "현재가", "상하", "0.25", "이내", "매수안함", "AND", False
        )

        row = add_row(14)
        self.complete_fill_ratio_check = QCheckBox()
        self.complete_fill_ratio_check.setChecked(True)
        self.complete_fill_ratio_label = QLabel("예산충족률")
        self.complete_fill_ratio_value_line = make_line("95", 44)
        self.complete_fill_ratio_unit_label = QLabel("%")
        self.complete_fill_ratio_compare_combo = make_combo(["이상", "이하"], "이상", 66)
        self.complete_fill_ratio_logic_combo = make_combo(["AND", "OR", "NOT"], "AND", 62)
        row.addWidget(self.complete_fill_ratio_check)
        row.addWidget(self.complete_fill_ratio_label)
        row.addWidget(self.complete_fill_ratio_value_line)
        row.addWidget(self.complete_fill_ratio_unit_label)
        row.addWidget(self.complete_fill_ratio_compare_combo)
        row.addStretch(1)
        row.addWidget(self.complete_fill_ratio_logic_combo)
        self.complete_fill_ratio_widgets = [
            self.complete_fill_ratio_label,
            self.complete_fill_ratio_value_line,
            self.complete_fill_ratio_unit_label,
            self.complete_fill_ratio_compare_combo,
            self.complete_fill_ratio_logic_combo,
        ]

        def sync_fill_ratio_enabled(_=None):
            parent_enabled = (
                True
                and self.complete_policy_remain_buy_option_check.isChecked()
            )
            row_enabled = parent_enabled and self.complete_fill_ratio_check.isChecked()
            self.complete_fill_ratio_check.setEnabled(parent_enabled)
            for widget in self.complete_fill_ratio_widgets:
                widget.setEnabled(row_enabled)

        self.complete_fill_ratio_check.toggled.connect(sync_fill_ratio_enabled)
        sync_fill_ratio_enabled()

        row = add_row(8)
        self.complete_policy_active_buy_option_check = QCheckBox("능동매수 |")
        self.complete_policy_active_buy_option_check.setChecked(False)
        self.complete_policy_active_buy_option_check.setStyleSheet("font-weight: normal;")
        row.addWidget(self.complete_policy_active_buy_option_check)
        self.complete_policy_active_buy_price_basis_combo = make_combo(["주문가", "현재가"], "주문가", 96)
        row.addWidget(self.complete_policy_active_buy_price_basis_combo)
        self.complete_policy_active_buy_basis_label = QLabel("에 평단가")
        row.addWidget(self.complete_policy_active_buy_basis_label)
        self.complete_policy_active_buy_direction_combo = make_combo(["상향", "하향", "상하"], "상하", 68)
        row.addWidget(self.complete_policy_active_buy_direction_combo)
        self.complete_policy_active_buy_value_line = make_line("0.15", 48)
        row.addWidget(self.complete_policy_active_buy_value_line)
        self.complete_policy_active_buy_unit_label = QLabel("% 이내")
        row.addWidget(self.complete_policy_active_buy_unit_label)
        row.addStretch(1)

        self._complete_mode_guard = False

        def sync_complete_mode(source=None):
            if self._complete_mode_guard:
                return
            self._complete_mode_guard = True
            try:
                if source is self.complete_current_state_check and self.complete_current_state_check.isChecked():
                    self.complete_after_cancel_check.setChecked(False)
                elif source is self.complete_after_cancel_check and True:
                    self.complete_current_state_check.setChecked(False)
                elif (
                    not self.complete_current_state_check.isChecked()
                    and not True
                ):
                    self.complete_current_state_check.setChecked(True)

                after_cancel_mode = True

                self.complete_policy_remain_buy_option_check.setEnabled(after_cancel_mode)
                self.complete_policy_active_buy_option_check.setEnabled(after_cancel_mode)

                if not after_cancel_mode:
                    self.complete_policy_remain_buy_option_check.setChecked(False)
                    self.complete_policy_active_buy_option_check.setChecked(False)
                elif (
                    not self.complete_policy_remain_buy_option_check.isChecked()
                    and not self.complete_policy_active_buy_option_check.isChecked()
                ):
                    self.complete_policy_remain_buy_option_check.setChecked(True)
            finally:
                self._complete_mode_guard = False

            sync_sub_policy_mode()

        self._complete_sub_policy_guard = False

        def sync_sub_policy_mode(source=None):
            if self._complete_sub_policy_guard:
                return
            self._complete_sub_policy_guard = True
            try:
                after_cancel_mode = True
                if after_cancel_mode:
                    if source is self.complete_policy_remain_buy_option_check and self.complete_policy_remain_buy_option_check.isChecked():
                        self.complete_policy_active_buy_option_check.setChecked(False)
                    elif source is self.complete_policy_active_buy_option_check and self.complete_policy_active_buy_option_check.isChecked():
                        self.complete_policy_remain_buy_option_check.setChecked(False)
                    elif (
                        not self.complete_policy_remain_buy_option_check.isChecked()
                        and not self.complete_policy_active_buy_option_check.isChecked()
                    ):
                        self.complete_policy_remain_buy_option_check.setChecked(True)

                remain_mode = after_cancel_mode and self.complete_policy_remain_buy_option_check.isChecked()
                active_mode = after_cancel_mode and self.complete_policy_active_buy_option_check.isChecked()

                active_widgets = [
                    self.complete_policy_active_buy_price_basis_combo,
                    self.complete_policy_active_buy_basis_label,
                    self.complete_policy_active_buy_direction_combo,
                    self.complete_policy_active_buy_value_line,
                    self.complete_policy_active_buy_unit_label,
                ]

                for check in self.complete_remain_buy_gap_widgets:
                    check.setEnabled(remain_mode)

                for check, detail_widgets, direction_combo, compare_combo in self.complete_remain_buy_detail_row_controls:
                    row_enabled = remain_mode and check.isChecked()
                    check.setEnabled(remain_mode)
                    for widget in detail_widgets:
                        widget.setEnabled(row_enabled)
                    if row_enabled:
                        sync_remain_buy_direction_compare_combo(direction_combo, compare_combo)

                self.complete_fill_ratio_check.setEnabled(remain_mode)
                fill_ratio_enabled = remain_mode and self.complete_fill_ratio_check.isChecked()
                for widget in self.complete_fill_ratio_widgets:
                    widget.setEnabled(fill_ratio_enabled)

                for widget in active_widgets:
                    widget.setEnabled(active_mode)
            finally:
                self._complete_sub_policy_guard = False

        self.complete_current_state_check.toggled.connect(
            lambda _: sync_complete_mode(self.complete_current_state_check)
        )
        self.complete_after_cancel_check.toggled.connect(
            lambda _: sync_complete_mode(self.complete_after_cancel_check)
        )
        self.complete_policy_remain_buy_option_check.toggled.connect(
            lambda _: sync_sub_policy_mode(self.complete_policy_remain_buy_option_check)
        )
        self.complete_policy_active_buy_option_check.toggled.connect(
            lambda _: sync_sub_policy_mode(self.complete_policy_active_buy_option_check)
        )
        sync_complete_mode()

        return box

    def _build_buy_tab(self):
        self.buy_tab = QWidget()
        root = QVBoxLayout(self.buy_tab)

        title = QLabel("매수 구성")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 17px; font-weight: bold; padding: 4px;")
        root.addWidget(title)

        # 1. BUY 주신호
        signal_box = QGroupBox("1. BUY 주신호")
        signal_layout = QHBoxLayout(signal_box)

        self.buy_signal_enabled = self._locked_checkbox("BUY")
        self.buy_signal_indicator = self._readonly_line()
        self.buy_signal_indicator.setText("OCR")

        self.buy_osc_threshold = self._readonly_line()
        self.buy_osc_threshold.setText("-1")

        self.buy_osc_compare = QComboBox()
        self.buy_osc_compare.addItems(["이하", "미만", "이상", "초과"])
        self.buy_osc_compare.setEnabled(False)

        self.buy_turn_direction = QComboBox()
        self.buy_turn_direction.addItems(["상승전환", "하락전환"])
        self.buy_turn_direction.setEnabled(False)

        self.buy_signal_bar = QComboBox()
        self.buy_signal_bar.addItems(["0봉", "1봉", "2봉", "3봉"])
        self.buy_signal_bar.setEnabled(False)

        signal_layout.addWidget(self.buy_signal_enabled)
        signal_layout.addWidget(QLabel("/"))
        signal_layout.addWidget(self.buy_signal_indicator)
        signal_layout.addWidget(self.buy_osc_threshold)
        signal_layout.addWidget(self.buy_osc_compare)
        signal_layout.addWidget(self.buy_turn_direction)
        signal_layout.addWidget(self.buy_signal_bar)
        root.addWidget(signal_box)

        # 2. 적용필터
        filter_box = QGroupBox("2. 적용필터")
        filter_grid = QGridLayout(filter_box)
        filter_grid.setHorizontalSpacing(8)
        filter_grid.setVerticalSpacing(6)

        headers = ["사용", "필터", "값/조건", "작용"]
        for col, text in enumerate(headers):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-weight: bold;")
            filter_grid.addWidget(label, 0, col)

        self.buy_filter_rows = []
        filter_specs = [
            ("RSI", "[ 45 ] 이하", "AND"),
            ("20이평 대비", "[ 5 ]봉 전 [ -0.2 ] %", "NOT"),
            ("시그널/MACD", "[ 0 ] 이하", "NOT"),
            ("이평배열", "[5] [10] [20] 정배열", "OR"),
            ("주가 20이평", "이상", "AND"),
        ]

        for row, (name, condition, logic) in enumerate(filter_specs, start=1):
            cb = self._locked_checkbox("")
            name_line = self._readonly_line()
            name_line.setText(name)
            cond_line = self._readonly_line()
            cond_line.setText(condition)
            logic_combo = QComboBox()
            logic_combo.addItems(["AND", "OR", "NOT"])
            logic_combo.setCurrentText(logic)
            logic_combo.setEnabled(False)

            filter_grid.addWidget(cb, row, 0)
            filter_grid.addWidget(name_line, row, 1)
            filter_grid.addWidget(cond_line, row, 2)
            filter_grid.addWidget(logic_combo, row, 3)
            self.buy_filter_rows.append((cb, name_line, cond_line, logic_combo))

        root.addWidget(filter_box)

        # 3. 매수방식
        method_box = QGroupBox("3. 매수방식")
        method_grid = QGridLayout(method_box)

        self.price_axis_single = self._locked_checkbox("단일호가")
        self.price_axis_multi = self._locked_checkbox("다중호가")
        self.price_axis_multi_count = self._readonly_line()
        self.price_axis_multi_count.setText("3 단계")

        self.time_axis_single = self._locked_checkbox("단일지점")
        self.time_axis_multi = self._locked_checkbox("다중지점")
        self.time_axis_multi_count = self._readonly_line()
        self.time_axis_multi_count.setText("3 회 / 30 초 내")

        method_grid.addWidget(QLabel("가격축"), 0, 0)
        method_grid.addWidget(self.price_axis_single, 0, 1)
        method_grid.addWidget(self.price_axis_multi, 0, 2)
        method_grid.addWidget(self.price_axis_multi_count, 0, 3)

        method_grid.addWidget(QLabel("시간축"), 1, 0)
        method_grid.addWidget(self.time_axis_single, 1, 1)
        method_grid.addWidget(self.time_axis_multi, 1, 2)
        method_grid.addWidget(self.time_axis_multi_count, 1, 3)

        self.buy_method_summary = QLabel("조합: 단일호가+단일지점 / 단일호가+다중지점 / 다중호가+단일지점 / 다중호가+다중지점")
        self.buy_method_summary.setStyleSheet("color: #555;")
        method_grid.addWidget(self.buy_method_summary, 2, 0, 1, 4)

        root.addWidget(method_box)

        # 4. 평단관리
        avg_box = QGroupBox("4. 평단관리")
        avg_grid = QGridLayout(avg_box)

        self.avg_round_increase = self._locked_checkbox("회차증가")
        self.avg_round_rule = self._readonly_line()
        self.avg_round_rule.setText("X3")

        self.avg_amount_basis = self._locked_checkbox("금액기준")
        self.avg_active_buy = self._locked_checkbox("능동매수")
        self.avg_near_ratio = self._readonly_line()
        self.avg_near_ratio.setText("근접비율 미설정")

        avg_grid.addWidget(self.avg_round_increase, 0, 0)
        avg_grid.addWidget(self.avg_round_rule, 0, 1)
        avg_grid.addWidget(self.avg_amount_basis, 0, 2)
        avg_grid.addWidget(self.avg_active_buy, 1, 0)
        avg_grid.addWidget(self.avg_near_ratio, 1, 1, 1, 2)

        root.addWidget(avg_box)

        # 5. 미체결정책
        cancel_box = QGroupBox("5. 미체결정책")
        cancel_grid = QGridLayout(cancel_box)

        self.cancel_time_enabled = self._locked_checkbox("시간 주문취소")
        self.cancel_time_value = self._readonly_line()
        self.cancel_time_value.setText("20 초 이후 주문취소")

        self.cancel_price_enabled = self._locked_checkbox("가격 이탈 주문취소")
        self.cancel_price_value = self._readonly_line()
        self.cancel_price_value.setText("주문가 대비 3% 상하 이탈")

        cancel_grid.addWidget(self.cancel_time_enabled, 0, 0)
        cancel_grid.addWidget(self.cancel_time_value, 0, 1)
        cancel_grid.addWidget(self.cancel_price_enabled, 1, 0)
        cancel_grid.addWidget(self.cancel_price_value, 1, 1)

        cancel_note = QLabel("주문취소은 매수 여부 판단이 아니라 BUY 가공 과정의 기술적 준비/종료 신호")
        cancel_note.setStyleSheet("color: #555;")
        cancel_grid.addWidget(cancel_note, 2, 0, 1, 2)

        root.addWidget(cancel_box)

        # 6. 완료조건
        complete_box = QGroupBox("6. 완료조건")
        complete_grid = QGridLayout(complete_box)

        self.complete_period_line = self._readonly_line()
        self.complete_period_line.setText("3 봉")

        complete_grid.addWidget(QLabel("완료조건 판정기간"), 0, 0)
        complete_grid.addWidget(self.complete_period_line, 0, 1)

        complete_headers = ["사용", "조건", "값", "작용"]
        for col, text in enumerate(complete_headers):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-weight: bold;")
            complete_grid.addWidget(label, 1, col)

        self.complete_rows = []
        complete_specs = [
            ("가격조건", "0.1 %", "AND"),
            ("수량충족률", "95 %", "AND"),
            ("20이평", "3봉 전 대비 0.5% 상승", "OR"),
        ]

        for row, (name, value, logic) in enumerate(complete_specs, start=2):
            cb = self._locked_checkbox("")
            name_line = self._readonly_line()
            name_line.setText(name)
            value_line = self._readonly_line()
            value_line.setText(value)
            logic_combo = QComboBox()
            logic_combo.addItems(["AND", "OR", "NOT"])
            logic_combo.setCurrentText(logic)
            logic_combo.setEnabled(False)

            complete_grid.addWidget(cb, row, 0)
            complete_grid.addWidget(name_line, row, 1)
            complete_grid.addWidget(value_line, row, 2)
            complete_grid.addWidget(logic_combo, row, 3)
            self.complete_rows.append((cb, name_line, value_line, logic_combo))

        complete_note = QLabel("판정기간 내 충족 이력 기준. 한 번 충족된 조건은 이후 깨져도 충족 이력으로 인정.")
        complete_note.setStyleSheet("color: #555;")
        complete_grid.addWidget(complete_note, 5, 0, 1, 4)

        root.addWidget(complete_box)

        # 7. 완료정책
        policy_box = QGroupBox("7. 완료정책")
        policy_layout = QHBoxLayout(policy_box)

        self.complete_policy_active_buy = self._locked_checkbox("능동매수")
        self.complete_policy_no_buy = self._locked_checkbox("매수안함")
        self.complete_policy_note = QLabel("완료조건 미충족 시 수행. 남은 리스크는 관제/마감/청산 계층에서 처리.")
        self.complete_policy_note.setStyleSheet("color: #555;")

        policy_layout.addWidget(self.complete_policy_active_buy)
        policy_layout.addWidget(self.complete_policy_no_buy)
        policy_layout.addWidget(self.complete_policy_note, 1)

        root.addWidget(policy_box)

        # 8. 신호 충돌 규칙
        conflict_box = QGroupBox("8. 신호 충돌 규칙")
        conflict_layout = QVBoxLayout(conflict_box)
        self.buy_conflict_note = QLabel(
            "신규 BUY/SELL 신호 발생 시 기존 BUY 가공, 주문취소, 완료조건, 완료정책 흐름을 종료하고 후행 신호를 우선 추종."
        )
        self.buy_conflict_note.setWordWrap(True)
        conflict_layout.addWidget(self.buy_conflict_note)

        root.addWidget(conflict_box)

        # STEP40A 호환 유지:
        # 기존 STEP38 _populate_fields()가 참조하는 위젯명을 유지한다.
        # 매수 구성 UI는 새 구조를 쓰되, 로딩 로직은 깨지지 않게 한다.
        self.buy_enabled_check = self.buy_signal_enabled
        self.buy_delay_line = self._readonly_line()
        self.buy_delay_line.setVisible(False)
        self.buy_status_line = self._readonly_line()
        self.buy_status_line.setVisible(False)

        root.addWidget(self.buy_delay_line)
        root.addWidget(self.buy_status_line)

    def _make_buy_edit_header(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        title = QLabel("매수설정")
        title.setStyleSheet("font-size: 26px; font-weight: bold; color: #1565C0; padding: 1px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;")
        row.addWidget(title)

        title_sep = QLabel("|")
        title_sep.setStyleSheet("font-size: 26px; font-weight: bold; color: #000000; padding: 2px 1px;")
        row.addWidget(title_sep)

        def make_label(text):
            label = QLabel(text)
            label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
            return label

        def make_line(text, width):
            line = QLineEdit()
            line.setText(text)
            line.setFixedWidth(width)
            line.setFixedHeight(34)
            line.setAlignment(Qt.AlignRight)
            line.setStyleSheet("font-size: 8pt; padding-right: 6px;")
            return line

        def make_combo(items, current, width):
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(34)
            combo.setStyleSheet("font-size: 9pt;")
            return combo

        row.addWidget(make_label("메인신호 : OCR"))
        row.addWidget(make_combo(["-", "+"], "-", 64))
        row.addWidget(make_line("1", 72))
        row.addWidget(make_combo(["이하", "이상"], "이하", 88))
        row.addWidget(make_combo(["상승", "하락"], "상승", 88))
        row.addWidget(make_line("0", 72))
        row.addWidget(make_label("봉"))
        row.addStretch(1)
        return row

    def _build_buy_edit_tab(self):
        self.buy_edit_tab = QWidget()
        outer = QVBoxLayout(self.buy_edit_tab)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        layout.addLayout(self._make_basic_settings_row_for_edit_tab())

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        layout.addLayout(self._make_buy_edit_header())

        top_grid = QGridLayout()
        top_grid.setColumnStretch(0, 1)
        top_grid.setColumnStretch(1, 1)
        top_grid.setColumnStretch(2, 1)

        first_column = QWidget()
        first_layout = QVBoxLayout(first_column)
        first_layout.setContentsMargins(0, 0, 0, 0)
        first_layout.setSpacing(8)
        first_layout.addWidget(self._make_buy_method_overview_controls(("base", "repeat", "price_compare")))
        first_layout.addWidget(self._make_buy_filter_overview_controls())
        first_layout.addWidget(self._make_buy_composite_filter_controls())
        first_layout.addStretch(1)
        top_grid.addWidget(first_column, 0, 0)

        second_third_column = QWidget()
        second_third_layout = QGridLayout(second_third_column)
        second_third_layout.setContentsMargins(0, 0, 0, 0)
        second_third_layout.setHorizontalSpacing(8)
        second_third_layout.setVerticalSpacing(8)
        second_third_layout.setColumnStretch(0, 1)
        second_third_layout.setColumnStretch(1, 1)
        second_third_layout.addWidget(self._make_buy_method_overview_controls(("situation", "additional", "cycle")), 0, 0)
        second_third_layout.addWidget(self._make_buy_avg_overview_controls(), 1, 0, 1, 2)
        top_grid.addWidget(second_third_column, 0, 1, 1, 2)
        layout.addLayout(top_grid)

        bottom_grid = QGridLayout()
        bottom_grid.setColumnStretch(0, 1)
        bottom_grid.setColumnStretch(1, 1)
        bottom_grid.setColumnStretch(2, 1)
        bottom_grid.addWidget(self._make_buy_cancel_overview_controls(), 0, 0)
        bottom_grid.addWidget(self._make_buy_complete_overview_controls(), 0, 1)
        bottom_grid.addWidget(self._make_buy_complete_policy_overview_controls(), 0, 2)
        layout.addLayout(bottom_grid)

        note = QLabel("매수 탭은 구성 탭의 매수설정만 분리 표시한 시험 배치입니다. 저장 기능은 현재 비활성입니다.")
        note.setAlignment(Qt.AlignCenter)
        note.setStyleSheet("color: #666; padding: 6px;")
        layout.addWidget(note)

        scroll.setWidget(page)
        page.adjustSize()
        outer.addWidget(scroll)
