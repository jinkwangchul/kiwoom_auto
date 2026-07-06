from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class HogaTotalDisplay(QLabel):
    def __init__(self, up_line, down_line, width):
        super().__init__()
        self.up_line = up_line
        self.down_line = down_line
        self.setFixedWidth(width)
        self.setFixedHeight(30)
        self.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setStyleSheet("font-size: 8pt;")

        self.refresh()

    def _count_from(self, line):
        try:
            return int(line.text().strip() or "0")
        except ValueError:
            return 0

    def refresh(self):
        total = self._count_from(self.up_line) + 1 + self._count_from(self.down_line)
        text = f"| {total}호가"
        if super().text() != text:
            super().setText(text)


class DetailToggleCheckBox(QCheckBox):
    def __init__(self, text):
        super().__init__(text)
        self._detail_widgets = []
        self._exclusive_peer = None

    def set_detail_widget(self, widget):
        self._detail_widgets = [widget]

    def add_detail_widget(self, widget):
        if widget not in self._detail_widgets:
            self._detail_widgets.append(widget)

    def set_exclusive_peer(self, peer):
        self._exclusive_peer = peer


class ModeSwitchComboBox(QComboBox):
    def set_sync_callback(self, callback):
        self.currentIndexChanged.connect(callback)


class IndicatorFollowBuyMethodControlsMixin:
    def _make_buy_method_overview_controls(self, sections=None):
        box = QGroupBox("주신호대응설정")
        box.setTitle("")
        box.setStyleSheet(
            "QGroupBox { font-weight: bold; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(6, 14, 8, 8)
        layout.setSpacing(4)

        def make_combo(items, current, width, combo_cls=QComboBox):
            combo = combo_cls()
            combo.addItems(items)
            combo.setCurrentText(current)
            combo.setFixedWidth(width)
            combo.setFixedHeight(30)
            combo.setStyleSheet("font-size: 8pt;")
            return combo

        def make_line(text, width, align=Qt.AlignRight):
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

        section_set = set(sections or ("base", "repeat", "price_compare", "situation", "additional"))

        if "base" in section_set:
            self._build_buy_base_section(layout, make_combo, make_line, make_label)
        if "repeat" in section_set:
            self._build_repeat_buy_section(layout, make_combo, make_line, make_label)
        if "price_compare" in section_set:
            self._build_price_compare_section(layout, make_combo, make_line, make_label)
        if {"repeat", "price_compare"}.issubset(section_set):
            self._bind_buy_base_price_compare_local_state()
        if "situation" in section_set:
            self._build_situation_response_section(layout, make_combo, make_line, make_label)
        if "additional" in section_set:
            self._build_additional_section(layout, make_combo, make_line, make_label)
        if "cycle" in section_set:
            layout.addWidget(self._make_buy_avg_overview_controls(("cycle", "flat")))
        self._connect_buy_method_signals()
        self._update_all_buy_method_states()
        layout.addStretch(1)
        return box

    def _build_buy_base_section(self, layout, make_combo, make_line, make_label):
        self.buy_base_setting_label = QLabel("▶기본매수설정")
        self.buy_base_setting_label.setFixedHeight(26)
        self.buy_base_setting_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.buy_base_setting_label.setStyleSheet("font-size: 9pt; font-weight: bold;")
        layout.addWidget(self.buy_base_setting_label, 0, Qt.AlignLeft)

        hoga_row = QHBoxLayout()
        hoga_row.setContentsMargins(16, 0, 0, 0)
        hoga_row.setSpacing(4)
        layout.addLayout(hoga_row)

        self.buy_base_hoga_combo = make_combo(["단일호가", "다중호가"], "다중호가", 116, ModeSwitchComboBox)
        hoga_row.addWidget(self.buy_base_hoga_combo)
        hoga_row.addWidget(make_label("|", 8, Qt.AlignCenter))

        self.buy_base_hoga_stack = QStackedWidget()
        self.buy_base_hoga_stack.setFixedHeight(30)
        hoga_row.addWidget(self.buy_base_hoga_stack)

        self.buy_base_single_widget = QWidget()
        single_layout = QHBoxLayout(self.buy_base_single_widget)
        single_layout.setContentsMargins(0, 0, 0, 0)
        single_layout.setSpacing(4)
        self.buy_base_order_combo = make_combo(["주문가", "시장가"], "주문가", 100)
        single_layout.addWidget(self.buy_base_order_combo)
        single_layout.addStretch(1)
        self.buy_base_hoga_stack.addWidget(self.buy_base_single_widget)

        self.buy_base_multi_widget = QWidget()
        multi_layout = QHBoxLayout(self.buy_base_multi_widget)
        multi_layout.setContentsMargins(0, 0, 0, 0)
        multi_layout.setSpacing(4)
        self.buy_base_up_line = make_line("0", 34)
        self.buy_base_down_line = make_line("2", 34)
        self.buy_base_total_label = HogaTotalDisplay(self.buy_base_up_line, self.buy_base_down_line, 70)
        multi_layout.addWidget(make_label("상향", 42))
        multi_layout.addWidget(self.buy_base_up_line)
        multi_layout.addWidget(make_label("/ 기본가 1 / 하향", 132))
        multi_layout.addWidget(self.buy_base_down_line)
        multi_layout.addWidget(self.buy_base_total_label)
        multi_layout.addStretch(1)
        self.buy_base_hoga_stack.addWidget(self.buy_base_multi_widget)
        hoga_row.addStretch(1)

        # 이 UI가 여러 경로에서 생성될 수 있으므로 호가 합산은 self 참조만
        # 사용하지 않고 생성 시점의 로컬 위젯 참조로도 직접 묶는다.
        # self.buy_base_*가 뒤쪽 생성 위젯으로 덮여도 현재 화면의 합산 라벨이
        # 정상 갱신되도록 한다.
        hoga_combo = self.buy_base_hoga_combo
        hoga_stack = self.buy_base_hoga_stack
        hoga_up_line = self.buy_base_up_line
        hoga_down_line = self.buy_base_down_line
        hoga_total_label = self.buy_base_total_label

        def update_hoga_total_local(*_args):
            hoga_total_label.refresh()

        def update_hoga_mode_local(*_args):
            index = hoga_combo.currentIndex()
            hoga_stack.setCurrentIndex(index if index >= 0 else 0)
            update_hoga_total_local()

        hoga_up_line.textChanged.connect(update_hoga_total_local)
        hoga_down_line.textChanged.connect(update_hoga_total_local)
        hoga_combo.currentIndexChanged.connect(update_hoga_mode_local)
        if not hasattr(self, "_buy_hoga_state_updaters"):
            self._buy_hoga_state_updaters = []
        self._buy_hoga_state_updaters.append(update_hoga_mode_local)
        update_hoga_mode_local()

        time_row = QHBoxLayout()
        time_row.setContentsMargins(16, 0, 0, 0)
        time_row.setSpacing(4)
        layout.addLayout(time_row)

        self.buy_base_time_mode_combo = make_combo(["선택없음", "다중시간", "다중비율"], "다중시간", 116)
        time_row.addWidget(self.buy_base_time_mode_combo)
        time_row.addWidget(make_label("|", 8, Qt.AlignCenter))

        self.buy_base_time_stack = QStackedWidget()
        self.buy_base_time_stack.setFixedHeight(30)
        time_row.addWidget(self.buy_base_time_stack)

        self.buy_base_time_none_widget = QWidget()
        none_layout = QHBoxLayout(self.buy_base_time_none_widget)
        none_layout.setContentsMargins(0, 0, 0, 0)
        none_layout.setSpacing(4)
        none_layout.addWidget(make_label("-", 20, Qt.AlignCenter))
        none_layout.addStretch(1)
        self.buy_base_time_stack.addWidget(self.buy_base_time_none_widget)

        self.buy_base_time_widget = QWidget()
        time_detail_layout = QHBoxLayout(self.buy_base_time_widget)
        time_detail_layout.setContentsMargins(0, 0, 0, 0)
        time_detail_layout.setSpacing(4)
        self.buy_base_time_value_line = make_line("30", 34)
        self.buy_base_time_unit_combo = make_combo(["분", "초", "봉"], "초", 60)
        self.buy_base_time_range_combo = make_combo(["이내", "간격"], "이내", 76)
        self.buy_base_time_count_line = make_line("3", 30)
        self.buy_base_time_order_combo = make_combo(["주문가", "현재가"], "주문가", 92)
        time_detail_layout.addWidget(self.buy_base_time_value_line)
        time_detail_layout.addWidget(self.buy_base_time_unit_combo)
        time_detail_layout.addWidget(self.buy_base_time_range_combo)
        time_detail_layout.addWidget(self.buy_base_time_count_line)
        time_detail_layout.addWidget(make_label("회", 18))
        time_detail_layout.addWidget(self.buy_base_time_order_combo)
        time_detail_layout.addStretch(1)
        self.buy_base_time_stack.addWidget(self.buy_base_time_widget)

        self.buy_base_ratio_widget = QWidget()
        ratio_layout = QHBoxLayout(self.buy_base_ratio_widget)
        ratio_layout.setContentsMargins(0, 0, 0, 0)
        ratio_layout.setSpacing(4)
        self.buy_base_ratio_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
        self.buy_base_ratio_right_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
        self.buy_base_ratio_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        self.buy_base_ratio_value_line = make_line("0.15", 46)
        self.buy_base_ratio_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        self.buy_base_ratio_count_line = make_line("3", 30)
        ratio_layout.addWidget(self.buy_base_ratio_left_combo)
        ratio_layout.addWidget(make_label("대비", 36))
        ratio_layout.addWidget(self.buy_base_ratio_right_combo)
        ratio_layout.addWidget(self.buy_base_ratio_direction_combo)
        ratio_layout.addWidget(self.buy_base_ratio_value_line)
        ratio_layout.addWidget(make_label("%", 14))
        ratio_layout.addWidget(self.buy_base_ratio_compare_combo)
        ratio_layout.addWidget(make_label("/", 8, Qt.AlignCenter))
        ratio_layout.addWidget(self.buy_base_ratio_count_line)
        ratio_layout.addWidget(make_label("회", 18))
        ratio_layout.addStretch(1)
        self.buy_base_time_stack.addWidget(self.buy_base_ratio_widget)
        time_row.addStretch(1)

        time_mode_combo = self.buy_base_time_mode_combo
        time_stack = self.buy_base_time_stack

        def update_time_mode_local(*_args):
            index = time_mode_combo.currentIndex()
            time_stack.setCurrentIndex(index if index >= 0 else 0)
            time_stack.updateGeometry()
            time_stack.update()

        time_mode_combo.currentIndexChanged.connect(update_time_mode_local)
        if not hasattr(self, "_buy_time_mode_state_updaters"):
            self._buy_time_mode_state_updaters = []
        self._buy_time_mode_state_updaters.append(update_time_mode_local)
        update_time_mode_local()

    def _build_repeat_buy_section(self, layout, make_combo, make_line, make_label):
        self.buy_repeat_setting_label = QLabel("▶반복매수설정")
        self.buy_repeat_setting_label.setFixedHeight(26)
        self.buy_repeat_setting_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.buy_repeat_setting_label.setStyleSheet("font-size: 9pt; font-weight: bold;")
        layout.addWidget(self.buy_repeat_setting_label, 0, Qt.AlignLeft)

        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(10, 4, 0, 0)
        apply_row.setSpacing(4)
        layout.addLayout(apply_row)

        self.buy_base_apply_all_check = DetailToggleCheckBox("기본매수설정을 전체매수에 적용")
        self.buy_base_apply_all_check.setStyleSheet("font-size: 9pt;")
        apply_row.addWidget(self.buy_base_apply_all_check)
        apply_row.addStretch(1)

        self.buy_base_detail_row_widget = QWidget()
        base_detail_row = QHBoxLayout(self.buy_base_detail_row_widget)
        base_detail_row.setContentsMargins(16, 0, 0, 0)
        base_detail_row.setSpacing(4)
        layout.addWidget(self.buy_base_detail_row_widget)

        self.buy_base_detail_mode_combo = make_combo(
            ["회차기준", "예산기준", "능동매수"], "회차기준", 116, ModeSwitchComboBox
        )
        base_detail_row.addWidget(self.buy_base_detail_mode_combo)
        base_detail_row.addWidget(make_label("|", 8, Qt.AlignCenter))

        self.buy_base_detail_stack = QStackedWidget()
        self.buy_base_detail_stack.setFixedHeight(30)
        base_detail_row.addWidget(self.buy_base_detail_stack)

        self.buy_base_round_detail_widget = QWidget()
        round_detail_layout = QHBoxLayout(self.buy_base_round_detail_widget)
        round_detail_layout.setContentsMargins(0, 0, 0, 0)
        round_detail_layout.setSpacing(4)
        self.buy_base_round_operator_combo = make_combo(["+", "x"], "+", 54)
        self.buy_base_round_budget_line = make_line("0.5", 46)
        round_detail_layout.addWidget(make_label("직전매수회차", 96))
        round_detail_layout.addWidget(self.buy_base_round_operator_combo)
        round_detail_layout.addWidget(self.buy_base_round_budget_line)
        round_detail_layout.addWidget(make_label("x초회예산", 86))
        round_detail_layout.addStretch(1)
        self.buy_base_detail_stack.addWidget(self.buy_base_round_detail_widget)

        self.buy_base_budget_detail_widget = QWidget()
        budget_detail_layout = QHBoxLayout(self.buy_base_budget_detail_widget)
        budget_detail_layout.setContentsMargins(0, 0, 0, 0)
        budget_detail_layout.setSpacing(4)
        self.buy_base_budget_ratio_line = make_line("0.5", 46)
        budget_detail_layout.addWidget(make_label("직전예산", 66))
        budget_detail_layout.addWidget(make_label("x", 14, Qt.AlignCenter))
        budget_detail_layout.addWidget(self.buy_base_budget_ratio_line)
        budget_detail_layout.addStretch(1)
        self.buy_base_detail_stack.addWidget(self.buy_base_budget_detail_widget)

        self.buy_base_active_detail_widget = QWidget()
        active_detail_layout = QHBoxLayout(self.buy_base_active_detail_widget)
        active_detail_layout.setContentsMargins(0, 0, 0, 0)
        active_detail_layout.setSpacing(4)
        self.buy_base_active_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        self.buy_base_active_ratio_line = make_line("0.45", 46)
        self.buy_base_active_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        active_detail_layout.addWidget(make_label("매수가", 48))
        active_detail_layout.addWidget(make_label("대비", 36))
        active_detail_layout.addWidget(make_label("평단가", 48))
        active_detail_layout.addWidget(self.buy_base_active_direction_combo)
        active_detail_layout.addWidget(self.buy_base_active_ratio_line)
        active_detail_layout.addWidget(make_label("%", 14))
        active_detail_layout.addWidget(self.buy_base_active_compare_combo)
        active_detail_layout.addStretch(1)
        self.buy_base_detail_stack.addWidget(self.buy_base_active_detail_widget)
        base_detail_row.addStretch(1)

    def _build_price_compare_section(self, layout, make_combo, make_line, make_label):
        price_compare_row = QHBoxLayout()
        price_compare_row.setContentsMargins(10, 4, 0, 0)
        price_compare_row.setSpacing(4)
        layout.addLayout(price_compare_row)

        self.buy_price_compare_check = DetailToggleCheckBox("주가비교매수")
        self.buy_price_compare_check.setStyleSheet("font-size: 9pt;")
        price_compare_row.addWidget(self.buy_price_compare_check)
        price_compare_row.addStretch(1)

        self.buy_price_compare_detail_row_widget = QWidget()
        price_compare_detail_row = QHBoxLayout(self.buy_price_compare_detail_row_widget)
        price_compare_detail_row.setContentsMargins(16, 0, 0, 0)
        price_compare_detail_row.setSpacing(4)
        layout.addWidget(self.buy_price_compare_detail_row_widget)

        self.buy_price_compare_left_label = make_label("평단가", 48)
        self.buy_price_compare_condition_combo = make_combo(["=<", "<"], "=<", 54)
        self.buy_price_compare_right_label = make_label("주문가", 48)
        self.buy_price_compare_mode_combo = make_combo(
            ["회차기준", "예산기준"], "회차기준", 116, ModeSwitchComboBox
        )
        price_compare_detail_row.addWidget(self.buy_price_compare_left_label)
        price_compare_detail_row.addWidget(self.buy_price_compare_condition_combo)
        price_compare_detail_row.addWidget(self.buy_price_compare_right_label)
        price_compare_detail_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        price_compare_detail_row.addWidget(self.buy_price_compare_mode_combo)
        price_compare_detail_row.addWidget(make_label("|", 8, Qt.AlignCenter))

        self.buy_price_compare_detail_stack = QStackedWidget()
        self.buy_price_compare_detail_stack.setFixedHeight(30)
        price_compare_detail_row.addWidget(self.buy_price_compare_detail_stack)

        self.buy_price_compare_round_widget = QWidget()
        price_round_layout = QHBoxLayout(self.buy_price_compare_round_widget)
        price_round_layout.setContentsMargins(0, 0, 0, 0)
        price_round_layout.setSpacing(4)
        self.buy_price_compare_round_operator_combo = make_combo(["+", "x"], "+", 54)
        self.buy_price_compare_round_budget_line = make_line("0.5", 46)
        price_round_layout.addWidget(make_label("직전매수회차", 96))
        price_round_layout.addWidget(self.buy_price_compare_round_operator_combo)
        price_round_layout.addWidget(self.buy_price_compare_round_budget_line)
        price_round_layout.addWidget(make_label("x초회예산", 86))
        price_round_layout.addStretch(1)
        self.buy_price_compare_detail_stack.addWidget(self.buy_price_compare_round_widget)

        self.buy_price_compare_budget_widget = QWidget()
        price_budget_layout = QHBoxLayout(self.buy_price_compare_budget_widget)
        price_budget_layout.setContentsMargins(0, 0, 0, 0)
        price_budget_layout.setSpacing(4)
        self.buy_price_compare_budget_ratio_line = make_line("0.5", 46)
        price_budget_layout.addWidget(make_label("직전예산", 66))
        price_budget_layout.addWidget(make_label("x", 14, Qt.AlignCenter))
        price_budget_layout.addWidget(self.buy_price_compare_budget_ratio_line)
        price_budget_layout.addStretch(1)
        self.buy_price_compare_detail_stack.addWidget(self.buy_price_compare_budget_widget)
        price_compare_detail_row.addStretch(1)

        self.buy_price_compare_above_row_widget = QWidget()
        price_compare_above_row = QHBoxLayout(self.buy_price_compare_above_row_widget)
        price_compare_above_row.setContentsMargins(16, 0, 0, 0)
        price_compare_above_row.setSpacing(4)
        layout.addWidget(self.buy_price_compare_above_row_widget)

        self.buy_price_compare_above_left_label = make_label("평단가", 48)
        self.buy_price_compare_above_condition_combo = make_combo([">", ">="], ">", 54)
        self.buy_price_compare_above_right_label = make_label("주문가", 48)
        self.buy_price_compare_above_mode_combo = make_combo(
            ["회차기준", "예산기준", "능동매수"], "회차기준", 116, ModeSwitchComboBox
        )
        price_compare_above_row.addWidget(self.buy_price_compare_above_left_label)
        price_compare_above_row.addWidget(self.buy_price_compare_above_condition_combo)
        price_compare_above_row.addWidget(self.buy_price_compare_above_right_label)
        price_compare_above_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        price_compare_above_row.addWidget(self.buy_price_compare_above_mode_combo)
        price_compare_above_row.addWidget(make_label("|", 8, Qt.AlignCenter))

        self.buy_price_compare_above_detail_stack = QStackedWidget()
        self.buy_price_compare_above_detail_stack.setFixedHeight(30)
        price_compare_above_row.addWidget(self.buy_price_compare_above_detail_stack)

        self.buy_price_compare_above_round_widget = QWidget()
        price_above_round_layout = QHBoxLayout(self.buy_price_compare_above_round_widget)
        price_above_round_layout.setContentsMargins(0, 0, 0, 0)
        price_above_round_layout.setSpacing(4)
        self.buy_price_compare_above_round_operator_combo = make_combo(["+", "x"], "+", 54)
        self.buy_price_compare_above_round_budget_line = make_line("0.5", 46)
        price_above_round_layout.addWidget(make_label("직전매수회차", 96))
        price_above_round_layout.addWidget(self.buy_price_compare_above_round_operator_combo)
        price_above_round_layout.addWidget(self.buy_price_compare_above_round_budget_line)
        price_above_round_layout.addWidget(make_label("x초회예산", 86))
        price_above_round_layout.addStretch(1)
        self.buy_price_compare_above_detail_stack.addWidget(self.buy_price_compare_above_round_widget)

        self.buy_price_compare_above_budget_widget = QWidget()
        price_above_budget_layout = QHBoxLayout(self.buy_price_compare_above_budget_widget)
        price_above_budget_layout.setContentsMargins(0, 0, 0, 0)
        price_above_budget_layout.setSpacing(4)
        self.buy_price_compare_above_budget_ratio_line = make_line("0.5", 46)
        price_above_budget_layout.addWidget(make_label("직전예산", 66))
        price_above_budget_layout.addWidget(make_label("x", 14, Qt.AlignCenter))
        price_above_budget_layout.addWidget(self.buy_price_compare_above_budget_ratio_line)
        price_above_budget_layout.addStretch(1)
        self.buy_price_compare_above_detail_stack.addWidget(self.buy_price_compare_above_budget_widget)

        self.buy_price_compare_above_active_widget = QWidget()
        price_above_active_layout = QHBoxLayout(self.buy_price_compare_above_active_widget)
        price_above_active_layout.setContentsMargins(0, 0, 0, 0)
        price_above_active_layout.setSpacing(4)
        self.buy_price_compare_above_active_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        self.buy_price_compare_above_active_ratio_line = make_line("0.45", 46)
        self.buy_price_compare_above_active_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        price_above_active_layout.addWidget(make_label("매수가", 48))
        price_above_active_layout.addWidget(make_label("대비", 36))
        price_above_active_layout.addWidget(make_label("평단가", 48))
        price_above_active_layout.addWidget(self.buy_price_compare_above_active_direction_combo)
        price_above_active_layout.addWidget(self.buy_price_compare_above_active_ratio_line)
        price_above_active_layout.addWidget(make_label("%", 14))
        price_above_active_layout.addWidget(self.buy_price_compare_above_active_compare_combo)
        price_above_active_layout.addStretch(1)
        self.buy_price_compare_above_detail_stack.addWidget(self.buy_price_compare_above_active_widget)
        price_compare_above_row.addStretch(1)

    def _bind_buy_base_price_compare_local_state(self):
        """현재 생성된 주신호대응설정 UI 인스턴스의 배타/활성 상태를 로컬 참조로 묶는다.

        이 매수방식 UI는 두 경로에서 생성될 수 있으므로 self.buy_* 참조만 사용하면
        두 번째 생성 위젯이 첫 번째 화면의 참조를 덮어쓴다. 따라서 기본매수설정 적용과
        주가비교매수의 배타 동작, 세부행 활성화, 스택 전환은 생성 시점의 위젯을 캡처한
        로컬 updater가 담당한다.
        """
        base_check = self.buy_base_apply_all_check
        base_detail_row = self.buy_base_detail_row_widget
        base_mode_combo = self.buy_base_detail_mode_combo
        base_detail_stack = self.buy_base_detail_stack

        price_check = self.buy_price_compare_check
        price_detail_row = self.buy_price_compare_detail_row_widget
        price_above_row = self.buy_price_compare_above_row_widget
        price_mode_combo = self.buy_price_compare_mode_combo
        price_detail_stack = self.buy_price_compare_detail_stack
        price_condition_combo = self.buy_price_compare_condition_combo
        price_above_condition_combo = self.buy_price_compare_above_condition_combo
        price_above_mode_combo = self.buy_price_compare_above_mode_combo
        price_above_detail_stack = self.buy_price_compare_above_detail_stack

        syncing = {"active": False}
        boundary_syncing = {"active": False}

        def set_row_enabled(row_widget, enabled):
            row_widget.setEnabled(enabled)
            for child in row_widget.findChildren(QWidget):
                child.setEnabled(enabled)
            row_widget.updateGeometry()
            row_widget.update()

        def update_base_mode_local(*_args):
            index = base_mode_combo.currentIndex()
            base_detail_stack.setCurrentIndex(index if index >= 0 else 0)
            base_detail_row.updateGeometry()
            base_detail_row.update()

        def update_price_mode_local(*_args):
            index = price_mode_combo.currentIndex()
            price_detail_stack.setCurrentIndex(index if index >= 0 else 0)
            price_detail_row.updateGeometry()
            price_detail_row.update()

        def update_price_above_mode_local(*_args):
            index = price_above_mode_combo.currentIndex()
            price_above_detail_stack.setCurrentIndex(index if index >= 0 else 0)
            price_above_row.updateGeometry()
            price_above_row.update()

        def set_combo_items_preserving(combo, allowed_items, fallback_item):
            current = combo.currentText().strip()
            next_value = current if current in allowed_items else fallback_item
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItems(allowed_items)
                combo.setCurrentText(next_value)
            finally:
                combo.blockSignals(False)

        def update_price_compare_boundary_local(source=None):
            if boundary_syncing["active"]:
                return
            boundary_syncing["active"] = True
            try:
                top_value = price_condition_combo.currentText().strip()
                bottom_value = price_above_condition_combo.currentText().strip()

                if source == "top":
                    if top_value == "=<":
                        set_combo_items_preserving(price_above_condition_combo, [">"], ">")
                    else:
                        set_combo_items_preserving(price_above_condition_combo, [">", ">="], bottom_value if bottom_value in [">", ">="] else ">")
                elif source == "bottom":
                    if bottom_value == ">=":
                        set_combo_items_preserving(price_condition_combo, ["<"], "<")
                    else:
                        set_combo_items_preserving(price_condition_combo, ["=<", "<"], top_value if top_value in ["=<", "<"] else "=<")
                else:
                    if top_value == "=<":
                        set_combo_items_preserving(price_above_condition_combo, [">"], ">")
                    elif bottom_value == ">=":
                        set_combo_items_preserving(price_condition_combo, ["<"], "<")
                    else:
                        set_combo_items_preserving(price_condition_combo, ["=<", "<"], top_value if top_value in ["=<", "<"] else "=<")
                        set_combo_items_preserving(price_above_condition_combo, [">", ">="], bottom_value if bottom_value in [">", ">="] else ">")
            finally:
                boundary_syncing["active"] = False

        def update_exclusive_local(source=None):
            if syncing["active"]:
                return
            syncing["active"] = True
            try:
                if source == "base" and base_check.isChecked():
                    price_check.setChecked(False)
                elif source == "price_compare" and price_check.isChecked():
                    base_check.setChecked(False)
            finally:
                syncing["active"] = False

            set_row_enabled(base_detail_row, base_check.isChecked())
            price_enabled = price_check.isChecked()
            set_row_enabled(price_detail_row, price_enabled)
            set_row_enabled(price_above_row, price_enabled)
            update_base_mode_local()
            update_price_mode_local()
            update_price_above_mode_local()

        base_check.toggled.connect(lambda _checked: update_exclusive_local("base"))
        price_check.toggled.connect(lambda _checked: update_exclusive_local("price_compare"))
        base_mode_combo.currentIndexChanged.connect(update_base_mode_local)
        price_mode_combo.currentIndexChanged.connect(update_price_mode_local)
        price_above_mode_combo.currentIndexChanged.connect(update_price_above_mode_local)
        price_condition_combo.currentIndexChanged.connect(lambda *_args: update_price_compare_boundary_local("top"))
        price_above_condition_combo.currentIndexChanged.connect(lambda *_args: update_price_compare_boundary_local("bottom"))

        if not hasattr(self, "_buy_base_price_compare_state_updaters"):
            self._buy_base_price_compare_state_updaters = []
        self._buy_base_price_compare_state_updaters.append(update_exclusive_local)
        update_price_compare_boundary_local()
        update_exclusive_local()

    def _build_additional_section(self, layout, make_combo, make_line, make_label):
        self.buy_additional_setting_label = QLabel("▶추가기능설정")
        self.buy_additional_setting_label.setFixedHeight(26)
        self.buy_additional_setting_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.buy_additional_setting_label.setStyleSheet("font-size: 9pt; font-weight: bold;")
        layout.addWidget(self.buy_additional_setting_label, 0, Qt.AlignLeft)

        # 추가기능설정은 다른 상태제어와 분리된 독립 위젯으로 구성한다.
        self.buy_price_compare_skip_row_widget = QWidget()
        price_compare_skip_row = QHBoxLayout(self.buy_price_compare_skip_row_widget)
        price_compare_skip_row.setContentsMargins(16, 0, 0, 0)
        price_compare_skip_row.setSpacing(4)
        layout.addWidget(self.buy_price_compare_skip_row_widget)

        self.buy_price_compare_skip_check = QCheckBox("직전회차주문가 대비 현재주문가")
        self.buy_price_compare_skip_check.setFixedHeight(30)
        self.buy_price_compare_skip_check.setStyleSheet("font-size: 8pt;")
        self.buy_price_compare_skip_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        self.buy_price_compare_skip_ratio_line = make_line("0.5", 46)
        self.buy_price_compare_skip_compare_combo = make_combo(["이하", "이상", "이내", "이탈"], "이하", 76)
        price_compare_skip_row.addWidget(self.buy_price_compare_skip_check)
        price_compare_skip_row.addWidget(self.buy_price_compare_skip_direction_combo)
        price_compare_skip_row.addWidget(self.buy_price_compare_skip_ratio_line)
        price_compare_skip_row.addWidget(make_label("%", 14))
        price_compare_skip_row.addWidget(self.buy_price_compare_skip_compare_combo)
        price_compare_skip_row.addWidget(make_label("매수안함", 66))
        price_compare_skip_row.addStretch(1)

        self.buy_additional_active_row_widget = QWidget()
        additional_active_row = QHBoxLayout(self.buy_additional_active_row_widget)
        additional_active_row.setContentsMargins(16, 0, 0, 0)
        additional_active_row.setSpacing(4)
        layout.addWidget(self.buy_additional_active_row_widget)

        self.buy_additional_active_check = QCheckBox("마지막+1 회차")
        self.buy_additional_active_check.setFixedWidth(138)
        self.buy_additional_active_check.setFixedHeight(30)
        self.buy_additional_active_check.setStyleSheet("font-size: 8pt;")
        self.buy_additional_active_method_combo = make_combo(["시장가", "현재가", "능동"], "시장가", 100)
        additional_active_row.addWidget(self.buy_additional_active_check)
        additional_active_row.addWidget(self.buy_additional_active_method_combo)
        additional_active_row.addWidget(make_label("마감매수", 66))
        additional_active_row.addStretch(1)

        self.buy_additional_active_detail_row_widget = QWidget()
        additional_active_detail_row = QHBoxLayout(self.buy_additional_active_detail_row_widget)
        additional_active_detail_row.setContentsMargins(36, 0, 0, 0)
        additional_active_detail_row.setSpacing(4)
        layout.addWidget(self.buy_additional_active_detail_row_widget)

        self.buy_additional_active_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        self.buy_additional_active_ratio_line = make_line("0.45", 46)
        self.buy_additional_active_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        self.buy_additional_active_price_label = make_label("매수가", 48)
        self.buy_additional_active_vs_label = make_label("대비", 36)
        self.buy_additional_active_avg_label = make_label("평단가", 48)
        self.buy_additional_active_percent_label = make_label("%", 14)
        additional_active_detail_row.addWidget(self.buy_additional_active_price_label)
        additional_active_detail_row.addWidget(self.buy_additional_active_vs_label)
        additional_active_detail_row.addWidget(self.buy_additional_active_avg_label)
        additional_active_detail_row.addWidget(self.buy_additional_active_direction_combo)
        additional_active_detail_row.addWidget(self.buy_additional_active_ratio_line)
        additional_active_detail_row.addWidget(self.buy_additional_active_percent_label)
        additional_active_detail_row.addWidget(self.buy_additional_active_compare_combo)
        additional_active_detail_row.addStretch(1)
        self._buy_additional_active_detail_widgets = (
            self.buy_additional_active_price_label,
            self.buy_additional_active_vs_label,
            self.buy_additional_active_avg_label,
            self.buy_additional_active_direction_combo,
            self.buy_additional_active_ratio_line,
            self.buy_additional_active_percent_label,
            self.buy_additional_active_compare_combo,
        )

        # 중요: 이 매수방식 UI는 현재 두 위치에서 생성될 수 있다.
        # self.buy_additional_active_* 이름은 두 번째 생성 시 덮어써지므로,
        # 마지막+1 회차 활성화는 반드시 생성 시점의 로컬 위젯 참조에 묶는다.
        additional_active_check = self.buy_additional_active_check
        additional_active_method_combo = self.buy_additional_active_method_combo
        additional_active_detail_row_widget = self.buy_additional_active_detail_row_widget
        additional_active_detail_widgets = self._buy_additional_active_detail_widgets

        def update_additional_active_state_local(*_args):
            enabled = (
                additional_active_check.isChecked()
                and additional_active_method_combo.currentText().strip() == "능동"
            )

            for widget in additional_active_detail_widgets:
                widget.setEnabled(enabled)

            additional_active_detail_row_widget.updateGeometry()
            additional_active_detail_row_widget.update()

        additional_active_check.toggled.connect(update_additional_active_state_local)
        additional_active_method_combo.currentIndexChanged.connect(update_additional_active_state_local)

        if not hasattr(self, "_buy_additional_active_state_updaters"):
            self._buy_additional_active_state_updaters = []
        self._buy_additional_active_state_updaters.append(update_additional_active_state_local)
        update_additional_active_state_local()

    def _build_situation_response_section(self, layout, make_combo, make_line, make_label):
        self.buy_situation_response_label = QLabel("▶상황변화대응")
        self.buy_situation_response_label.setFixedHeight(26)
        self.buy_situation_response_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.buy_situation_response_label.setStyleSheet("font-size: 9pt; font-weight: bold;")
        layout.addWidget(self.buy_situation_response_label, 0, Qt.AlignLeft)

        situation_response_row_widget = QWidget()
        situation_response_row = QHBoxLayout(situation_response_row_widget)
        situation_response_row.setContentsMargins(16, 0, 0, 0)
        situation_response_row.setSpacing(4)
        layout.addWidget(situation_response_row_widget)

        situation_type_combo = make_combo(["미체결", "가격비교"], "가격비교", 116)
        situation_detail_stack = QStackedWidget()
        situation_detail_stack.setFixedHeight(30)

        unfilled_widget = QWidget()
        unfilled_layout = QHBoxLayout(unfilled_widget)
        unfilled_layout.setContentsMargins(0, 0, 0, 0)
        unfilled_layout.setSpacing(4)
        unfilled_scope_combo = make_combo(["매회", "일괄"], "매회", 66)
        unfilled_time_line = make_line("10", 34)
        unfilled_unit_combo = make_combo(["분", "초", "봉"], "초", 60)
        unfilled_order_cancel_label = make_label("후 주문취소", 86)
        unfilled_layout.addWidget(unfilled_scope_combo)
        unfilled_layout.addWidget(make_label("기준", 36))
        unfilled_layout.addWidget(unfilled_time_line)
        unfilled_layout.addWidget(unfilled_unit_combo)
        unfilled_layout.addWidget(unfilled_order_cancel_label)
        unfilled_layout.addStretch(1)
        situation_detail_stack.addWidget(unfilled_widget)

        price_compare_widget = QWidget()
        price_compare_layout = QHBoxLayout(price_compare_widget)
        price_compare_layout.setContentsMargins(0, 0, 0, 0)
        price_compare_layout.setSpacing(4)
        price_left_combo = make_combo(["주문가", "현재가", "평단가"], "주문가", 92)
        price_right_combo = make_combo(["주문가", "현재가", "평단가"], "현재가", 92)
        price_direction_combo = make_combo(["상향", "하향", "상하"], "상향", 76)
        price_ratio_line = make_line("0.15", 46)
        price_compare_combo = make_combo(["이상", "이하", "이내", "이탈"], "이상", 76)
        price_action_combo = make_combo(["매수리셋", "일괄취소"], "일괄취소", 100)
        price_compare_layout.addWidget(price_left_combo)
        price_compare_layout.addWidget(make_label("대비", 36))
        price_compare_layout.addWidget(price_right_combo)
        price_compare_layout.addWidget(price_direction_combo)
        price_compare_layout.addWidget(price_ratio_line)
        price_compare_layout.addWidget(make_label("%", 14))
        price_compare_layout.addWidget(price_compare_combo)
        price_compare_layout.addWidget(price_action_combo)
        price_compare_layout.addStretch(1)
        situation_detail_stack.addWidget(price_compare_widget)

        situation_response_row.addWidget(situation_type_combo)
        situation_response_row.addWidget(make_label("|", 8, Qt.AlignCenter))
        situation_response_row.addWidget(situation_detail_stack)
        situation_response_row.addStretch(1)

        # 기존 외부 참조 호환용 속성은 유지하되, 상태 전환은 생성 시점의
        # 로컬 위젯 참조로 처리한다. 동일 UI 중복 생성 시 self 참조 덮어쓰기 방지.
        self.buy_situation_response_type_combo = situation_type_combo
        self.buy_situation_response_detail_stack = situation_detail_stack
        self.buy_situation_response_unfilled_scope_combo = unfilled_scope_combo
        self.buy_situation_response_unfilled_time_line = unfilled_time_line
        self.buy_situation_response_unfilled_unit_combo = unfilled_unit_combo
        self.buy_situation_response_unfilled_order_cancel_label = unfilled_order_cancel_label
        self.buy_situation_response_left_combo = price_left_combo
        self.buy_situation_response_right_combo = price_right_combo
        self.buy_situation_response_direction_combo = price_direction_combo
        self.buy_situation_response_ratio_line = price_ratio_line
        self.buy_situation_response_compare_combo = price_compare_combo
        self.buy_situation_response_action_combo = price_action_combo

        def update_situation_detail_local(*_args):
            if situation_type_combo.currentText().strip() == "미체결":
                situation_detail_stack.setCurrentIndex(0)
            else:
                situation_detail_stack.setCurrentIndex(1)
            situation_response_row_widget.updateGeometry()
            situation_response_row_widget.update()

        situation_type_combo.currentIndexChanged.connect(update_situation_detail_local)
        if not hasattr(self, "_buy_situation_response_updaters"):
            self._buy_situation_response_updaters = []
        self._buy_situation_response_updaters.append(update_situation_detail_local)
        update_situation_detail_local()

    def _connect_buy_method_signals(self):
        pass
        # 호가 합산/전환은 _build_buy_base_section()에서 생성 시점의
        # 로컬 위젯 참조에 직접 연결한다.
        # 기본매수 세부모드/주가비교매수/상호배타 상태는
        # _bind_buy_base_price_compare_local_state()에서 생성 시점의 로컬 참조에 직접 연결한다.
        # 동일 UI 중복 생성 시 self 참조가 덮어써지는 문제를 막기 위함이다.

        # 마지막+1 회차 능동 세부설정은 _build_additional_section()에서
        # 생성 시점의 로컬 위젯 참조에 직접 연결한다.
        # 이 파일의 UI가 두 번 생성되는 경우 self 참조가 뒤쪽 위젯으로 덮여
        # 앞쪽 화면이 갱신되지 않는 문제를 막기 위함이다.

    def _update_hoga_total(self, *_args):
        for updater in getattr(self, "_buy_hoga_state_updaters", []):
            updater()

    def _update_hoga_mode(self, *_args):
        self._update_hoga_total()

    def _update_time_mode(self, *_args):
        for updater in getattr(self, "_buy_time_mode_state_updaters", []):
            updater()

    def _update_apply_all_enabled(self, *_args):
        # 호환용 진입점. 실제 갱신은 각 UI 생성 시점에 캡처한
        # 로컬 updater들이 담당한다.
        for updater in getattr(self, "_buy_base_price_compare_state_updaters", []):
            updater()

    def _update_additional_active_state(self, *_args):
        # 호환용 진입점. 실제 갱신은 각 UI 생성 시점에 캡처한
        # 로컬 updater들이 담당한다. self.buy_additional_active_* 참조는
        # 두 번째 UI 생성 시 덮어써질 수 있으므로 여기서 직접 쓰지 않는다.
        for updater in getattr(self, "_buy_additional_active_state_updaters", []):
            updater()

    def _update_situation_response_state(self, *_args):
        for updater in getattr(self, "_buy_situation_response_updaters", []):
            updater()

    def _update_all_buy_method_states(self):
        self._update_hoga_mode()
        self._update_time_mode()
        self._update_apply_all_enabled()
        self._update_additional_active_state()
        self._update_situation_response_state()
