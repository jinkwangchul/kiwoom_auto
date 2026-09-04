from PyQt5 import sip
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui_indicator_follow_buy_method_controls import (
    IndicatorFollowBuyMethodControlsMixin,
    sync_buy_direction_comparator,
)


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
            sync_buy_direction_comparator(cycle_ratio_direction_combo, cycle_ratio_compare_combo)

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
        cancel_batch_index = cycle_price_action_combo.findText("일괄취소")
        cancel_batch_item = (
            cycle_price_action_combo.model().item(cancel_batch_index)
            if cancel_batch_index >= 0 and hasattr(cycle_price_action_combo.model(), "item")
            else None
        )
        if cancel_batch_item is not None:
            cancel_batch_item.setEnabled(False)
            cancel_batch_item.setToolTip("CYCLE_OPTION_EXECUTION_NOT_CONNECTED")
        cycle_price_action_combo.setToolTip(
            "일괄취소는 CYCLE_OPTION_EXECUTION_NOT_CONNECTED 상태로 예약되어 있습니다."
        )
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
            sync_buy_direction_comparator(cycle_price_direction_combo, cycle_price_compare_combo)

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
            self.buy_cycle_column_widget = cycle_column

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
            sync_buy_direction_comparator(exit_price_direction_combo, exit_price_compare_combo)

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
