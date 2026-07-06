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

class IndicatorFollowControlTabMixin:
    def _build_control_tab(self):
        """
        STEP41:
        첫 진입 페이지를 BUY/SELL 상하단 구성 브리핑 + 직접 설정 가능한 컨트롤 패널 형태로 구성한다.
        '법전' 문구는 UI 공식 용어로 사용하지 않는다.
        """
        self.control_tab = QWidget()
        outer = QVBoxLayout(self.control_tab)

        scroll = QScrollArea()
        self.control_scroll = scroll
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page = QWidget()
        self.control_page = page
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignTop)

        # 호환용 상태 카드: 기존 _populate_fields / refresh_preview가 참조하는 status 객체 유지
        status_box = QGroupBox("루틴 상태")
        status_grid = QGridLayout(status_box)

        self.card_routine = self._make_panel_card("루틴", "대기", None)
        self.card_buy = self._make_panel_card("매수", "대기", None)
        self.card_sell = self._make_panel_card("매도", "대기", None)
        self.card_profit = self._make_panel_card("수익률 매도", "대기", None)
        self.card_advanced = self._make_panel_card("확장", "잠금", None)
        self.card_validation = self._make_panel_card("검증", "대기", None)

        status_grid.addWidget(self.card_routine["frame"], 0, 0)
        status_grid.addWidget(self.card_buy["frame"], 0, 1)
        status_grid.addWidget(self.card_sell["frame"], 0, 2)
        status_grid.addWidget(self.card_profit["frame"], 0, 3)
        status_grid.addWidget(self.card_validation["frame"], 0, 4)

        layout.addWidget(status_box)
        status_box.hide()

        # BASIC 구성
        self.basic_box = basic_box = QGroupBox("")
        basic_box.setObjectName("sectionBasicBox")
        basic_box.setStyleSheet(
            "QGroupBox#sectionBasicBox {"
            "border: 1px solid #8A98A8;"
            "border-radius: 2px;"
            "margin-top: 2px;"
            "background: transparent;"
            "}"
        )
        basic_layout = QVBoxLayout(basic_box)
        basic_layout.setContentsMargins(10, 8, 10, 10)
        basic_layout.setSpacing(4)
        basic_box.setMinimumHeight(52)
        basic_box.setMaximumHeight(88)
        basic_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        basic_row = QHBoxLayout()
        basic_row.setContentsMargins(0, 0, 0, 0)
        basic_row.setSpacing(10)
        basic_row.setAlignment(Qt.AlignVCenter)

        self.basic_title = basic_title = QLabel("▶ 기본설정")
        basic_title.setFixedHeight(30)
        basic_title.setMinimumWidth(132)
        basic_title.setAlignment(Qt.AlignCenter)
        basic_title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #2E6B3A; padding: 0px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;")
        basic_row.addWidget(basic_title)

        basic_title_sep = QLabel("|")
        basic_title_sep.setFixedHeight(30)
        basic_title_sep.setAlignment(Qt.AlignCenter)
        basic_title_sep.setStyleSheet("font-size: 13pt; font-weight: bold; color: #000000; padding: 0px 1px;")
        basic_row.addWidget(basic_title_sep)

        basic_signal_basis_label = QLabel("신호검출기준")
        basic_signal_basis_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        basic_row.addWidget(basic_signal_basis_label)

        self.basic_signal_interval_combo = QComboBox()
        self.basic_signal_interval_combo.addItems(["1", "3", "5", "10", "15", "30", "60", "120", "240"])
        self.basic_signal_interval_combo.setCurrentText("5")
        self.basic_signal_interval_combo.setFixedWidth(60)
        self.basic_signal_interval_combo.setFixedHeight(30)
        self.basic_signal_interval_combo.setStyleSheet("font-size: 9pt;")
        self.basic_signal_interval_combo.setLayoutDirection(Qt.RightToLeft)
        basic_row.addWidget(self.basic_signal_interval_combo)

        minute_label = QLabel("분봉 |")
        minute_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        basic_row.addWidget(minute_label)

        duplicate_label = QLabel("중복신호처리")
        duplicate_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        basic_row.addWidget(duplicate_label)

        self.basic_duplicate_signal_combo = QComboBox()
        self.basic_duplicate_signal_combo.addItems(["후행신호 우선", "선행신호 우선"])
        self.basic_duplicate_signal_combo.setCurrentText("후행신호 우선")
        self.basic_duplicate_signal_combo.setFixedWidth(150)
        self.basic_duplicate_signal_combo.setFixedHeight(30)
        self.basic_duplicate_signal_combo.setStyleSheet("font-size: 9pt;")
        basic_row.addWidget(self.basic_duplicate_signal_combo)

        separator_label = QLabel("|")
        separator_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        basic_row.addWidget(separator_label)

        error_label = QLabel("오류발생")
        error_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        basic_row.addWidget(error_label)

        self.basic_error_policy_combo = QComboBox()
        self.basic_error_policy_combo.addItems(["매매중지", "매매지속"])
        self.basic_error_policy_combo.setCurrentText("매매중지")
        self.basic_error_policy_combo.setFixedWidth(108)
        self.basic_error_policy_combo.setFixedHeight(30)
        self.basic_error_policy_combo.setStyleSheet("font-size: 9pt;")
        basic_row.addWidget(self.basic_error_policy_combo)

        self.control_full_view_button = QPushButton("전체보기")
        self.control_full_view_button.setFixedWidth(90)
        self.control_full_view_button.setFixedHeight(30)
        self.control_full_view_button.setStyleSheet("font-size: 9pt; padding: 1px 4px;")
        self.control_full_view_button.clicked.connect(lambda: self._apply_control_section_mode("all"))
        basic_row.addWidget(self.control_full_view_button)

        basic_row.addStretch(1)
        self.basic_header_widget = QWidget()
        self.basic_header_widget.setLayout(basic_row)
        self.basic_header_widget.setFixedHeight(44)
        self.basic_header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        basic_layout.addWidget(self.basic_header_widget)

        layout.addWidget(basic_box)

        # BUY 구성
        self.buy_box = buy_box = QGroupBox("")
        buy_box.setObjectName("sectionBuyBox")
        buy_box.setStyleSheet(
            "QGroupBox#sectionBuyBox {"
            "border: 1px solid #8A98A8;"
            "border-radius: 2px;"
            "margin-top: 4px;"
            "background: transparent;"
            "}"
        )
        buy_layout = QVBoxLayout(buy_box)
        buy_layout.setContentsMargins(10, 8, 10, 10)
        buy_layout.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.setAlignment(Qt.AlignVCenter)

        self.buy_title = buy_title = QLabel("매수설정")
        buy_title.setFixedHeight(30)
        buy_title.setMinimumWidth(132)
        buy_title.setAlignment(Qt.AlignCenter)
        buy_title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #1565C0; padding: 0px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;")
        header_row.addWidget(buy_title)

        buy_title_sep = QLabel("|")
        buy_title_sep.setFixedHeight(30)
        buy_title_sep.setAlignment(Qt.AlignCenter)
        buy_title_sep.setStyleSheet("font-size: 13pt; font-weight: bold; color: #000000; padding: 0px 1px;")
        header_row.addWidget(buy_title_sep)

        buy_group_label = QLabel("● 신호검출필터조합 :")
        buy_group_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        header_row.addWidget(buy_group_label)

        self.buy_signal_expr_line = QLineEdit()
        self.buy_signal_expr_line.setText("A and B and C and D")
        self.buy_signal_expr_line.setFixedWidth(620)
        self.buy_signal_expr_line.setFixedHeight(34)
        self.buy_signal_expr_line.setAlignment(Qt.AlignCenter)
        self.buy_signal_expr_line.setStyleSheet(
            "font-size: 9pt; font-weight: bold; padding: 1px 4px;"
        )
        self._buy_signal_expr_selection = (-1, 0)
        self._buy_expr_current_operator = "AND"

        def _remember_buy_expr_selection():
            start_pos = self.buy_signal_expr_line.selectionStart()
            selected_len = len(self.buy_signal_expr_line.selectedText())
            if start_pos >= 0 and selected_len > 0:
                self._buy_signal_expr_selection = (start_pos, selected_len)

        self.buy_signal_expr_line.selectionChanged.connect(_remember_buy_expr_selection)
        header_row.addWidget(self.buy_signal_expr_line)

        def _make_buy_expr_button(text, width=42):
            button = QPushButton(text)
            button.setFixedWidth(width)
            button.setFixedHeight(32)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet("font-size: 8pt; padding: 1px 1px;")
            return button

        def _buy_expr_tokens():
            expr = self.buy_signal_expr_line.text().strip()
            if not expr:
                return []
            tokens = expr.replace("(", " ( ").replace(")", " ) ").split()
            normalized = []
            for token in tokens:
                upper = token.upper()
                if upper in {"A", "B", "C", "D", "AND", "OR", "NOT"}:
                    normalized.append(upper)
                else:
                    normalized.append(token)
            return normalized

        def _format_buy_expr_token(token):
            upper = token.upper()
            if upper in {"AND", "OR", "NOT"}:
                return upper.lower()
            if upper in {"A", "B", "C", "D"}:
                return upper
            return token

        def _set_buy_expr_tokens(tokens):
            expr = " ".join(_format_buy_expr_token(token) for token in tokens)
            expr = expr.replace("( ", "(").replace(" )", ")")
            self.buy_signal_expr_line.setText(expr.strip())

        def _append_buy_expr(token):
            tokens = _buy_expr_tokens()
            last = tokens[-1] if tokens else None

            condition_tokens = {"A", "B", "C", "D"}
            op_tokens = {"AND", "OR", "NOT"}
            max_conditions = 10
            max_operators = 9

            def _condition_count():
                return sum(1 for item in tokens if item in condition_tokens)

            def _operator_count():
                return sum(1 for item in tokens if item in op_tokens)

            if token in op_tokens:
                if not tokens:
                    return
                if last in op_tokens:
                    tokens[-1] = token
                    _set_buy_expr_tokens(tokens)
                    return
                if last == "(":
                    return
                if _operator_count() >= max_operators:
                    return
                if last in condition_tokens or last == ")":
                    tokens.append(token)
                    _set_buy_expr_tokens(tokens)
                return

            if token in condition_tokens:
                # 매수 조합식은 동일 조건 반복 입력을 허용한다.
                # 예: (A and D) or (C and D)
                if _condition_count() >= max_conditions:
                    return

                current_op = getattr(self, "_buy_expr_current_operator", "AND")
                if current_op not in op_tokens:
                    current_op = "AND"

                if not tokens or last == "(":
                    tokens.append(token)
                    _set_buy_expr_tokens(tokens)
                    return

                if last in op_tokens:
                    tokens.append(token)
                    _set_buy_expr_tokens(tokens)
                    return

                if last in condition_tokens or last == ")":
                    if _operator_count() >= max_operators:
                        return
                    tokens.extend([current_op, token])
                    _set_buy_expr_tokens(tokens)
                    return

        def _find_buy_expr_operator_index(tokens):
            """현재 커서/선택 위치에 가까운 연산자 토큰을 찾는다.

            선택 영역에 연산자가 있으면 그 연산자를 우선 변경하고,
            선택이 없으면 커서 앞쪽의 가장 가까운 연산자를 변경한다.
            못 찾으면 마지막 연산자를 변경한다.
            """
            if not tokens:
                return None

            op_tokens = {"AND", "OR", "NOT"}
            expr = self.buy_signal_expr_line.text()
            cursor_pos = self.buy_signal_expr_line.cursorPosition()
            selection_start = self.buy_signal_expr_line.selectionStart()
            selected_len = len(self.buy_signal_expr_line.selectedText()) if selection_start >= 0 else 0
            selection_end = selection_start + selected_len if selection_start >= 0 else -1

            search_pos = 0
            op_positions = []
            for index, token in enumerate(tokens):
                display_token = _format_buy_expr_token(token)
                pos = expr.lower().find(display_token.lower(), search_pos)
                if pos < 0:
                    pos = search_pos
                end = pos + len(display_token)
                if token in op_tokens:
                    op_positions.append((index, pos, end))
                search_pos = end

            if selection_start >= 0 and selected_len > 0:
                for index, pos, end in op_positions:
                    if not (end <= selection_start or pos >= selection_end):
                        return index

            previous = [item for item in op_positions if item[1] <= cursor_pos]
            if previous:
                return previous[-1][0]
            if op_positions:
                return op_positions[-1][0]
            return None

        def _cycle_buy_expr_operator():
            order = ["AND", "OR", "NOT"]
            tokens = _buy_expr_tokens()
            target_index = _find_buy_expr_operator_index(tokens)

            if target_index is not None:
                current = tokens[target_index]
            else:
                current = getattr(self, "_buy_expr_current_operator", "AND")

            try:
                next_op = order[(order.index(current) + 1) % len(order)]
            except ValueError:
                next_op = "AND"

            self._buy_expr_current_operator = next_op
            self.buy_expr_operator_button.setText(next_op)

            if target_index is not None:
                tokens[target_index] = next_op
                _set_buy_expr_tokens(tokens)
                self.buy_signal_expr_line.setFocus()

        def _is_valid_buy_expr_fragment(fragment):
            tokens = [token.upper() if token.upper() in {"A", "B", "C", "D", "AND", "OR", "NOT"} else token for token in fragment.replace("(", " ( ").replace(")", " ) ").split()]
            if not tokens:
                return False

            condition_tokens = {"A", "B", "C", "D"}
            op_tokens = {"AND", "OR", "NOT"}

            if tokens[0] in op_tokens or tokens[-1] in op_tokens:
                return False

            prev = None
            depth = 0
            for token in tokens:
                if token == "(":
                    depth += 1
                    if prev in condition_tokens or prev == ")":
                        return False
                elif token == ")":
                    depth -= 1
                    if depth < 0:
                        return False
                    if prev in op_tokens or prev == "(" or prev is None:
                        return False
                elif token in condition_tokens:
                    if prev in condition_tokens or prev == ")":
                        return False
                elif token in op_tokens:
                    if prev is None or prev in op_tokens or prev == "(":
                        return False
                else:
                    return False
                prev = token

            return depth == 0

        def _wrap_selected_buy_expr():
            line = self.buy_signal_expr_line
            expr = line.text()

            start_pos = line.selectionStart()
            selected = line.selectedText()

            if start_pos < 0 or not selected:
                saved_start, saved_len = getattr(self, "_buy_signal_expr_selection", (-1, 0))
                if saved_start >= 0 and saved_len > 0:
                    start_pos = saved_start
                    selected = expr[saved_start:saved_start + saved_len]

            if start_pos < 0 or not selected or not selected.strip():
                return

            selected_text = selected.strip()
            end_pos = start_pos + len(selected)

            if selected_text.startswith("(") and selected_text.endswith(")"):
                inner_text = selected_text[1:-1].strip()
                updated = expr[:start_pos] + inner_text + expr[end_pos:]
                line.setText(updated)
                line.setFocus()
                line.setCursorPosition(start_pos + len(inner_text))
                self._buy_signal_expr_selection = (-1, 0)
                return

            if "(" in selected_text or ")" in selected_text:
                return

            if not _is_valid_buy_expr_fragment(selected_text):
                return

            wrapped = expr[:start_pos] + "(" + selected_text + ")" + expr[end_pos:]
            line.setText(wrapped)
            line.setFocus()
            line.setCursorPosition(start_pos + len(selected_text) + 2)
            self._buy_signal_expr_selection = (-1, 0)

        for token, width in [
            ("A", 26), ("/", 6), ("B", 26), ("/", 6), ("C", 26),
            ("/", 6), ("D", 26),
        ]:
            if token == "/":
                sep = QLabel("/")
                sep.setStyleSheet("font-size: 9pt;")
                header_row.addWidget(sep)
            else:
                btn = _make_buy_expr_button(token, width)
                btn.clicked.connect(lambda _, t=token: _append_buy_expr(t))
                header_row.addWidget(btn)

        sep = QLabel("/")
        sep.setStyleSheet("font-size: 9pt;")
        header_row.addWidget(sep)

        self.buy_expr_operator_button = _make_buy_expr_button("AND", 48)
        self.buy_expr_operator_button.clicked.connect(_cycle_buy_expr_operator)
        header_row.addWidget(self.buy_expr_operator_button)

        buy_wrap_button = _make_buy_expr_button("()", 34)
        buy_wrap_button.pressed.connect(_wrap_selected_buy_expr)
        header_row.addWidget(buy_wrap_button)

        buy_clear_button = _make_buy_expr_button("지움", 44)
        buy_clear_button.clicked.connect(lambda: self.buy_signal_expr_line.clear())
        header_row.addWidget(buy_clear_button)

        header_row.addStretch(1)
        self.buy_header_widget = QWidget()
        self.buy_header_widget.setLayout(header_row)
        self.buy_header_widget.setFixedHeight(44)
        self.buy_header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        buy_layout.addWidget(self.buy_header_widget)

        buy_grid = QGridLayout()
        buy_grid.setColumnStretch(0, 1)
        buy_grid.setColumnStretch(1, 1)
        buy_grid.setColumnStretch(2, 1)
        buy_grid.setHorizontalSpacing(8)
        buy_grid.setVerticalSpacing(6)
        self.buy_overview_filter = self._make_buy_filter_overview_controls()
        self.buy_overview_method = self._make_buy_method_overview_controls(("base", "repeat", "price_compare"))
        self.buy_overview_method_extra = self._make_buy_method_overview_controls(("situation", "additional", "cycle"))
        self.buy_overview_finish = self._make_buy_avg_overview_controls(("exit", "close"))

        buy_col1_widget = QWidget()
        buy_col1_layout = QVBoxLayout(buy_col1_widget)
        buy_col1_layout.setContentsMargins(0, 0, 0, 0)
        buy_col1_layout.setSpacing(4)
        buy_col1_layout.addWidget(self.buy_overview_method)
        buy_col1_layout.addStretch(1)

        buy_col2_widget = QWidget()
        buy_col2_layout = QVBoxLayout(buy_col2_widget)
        buy_col2_layout.setContentsMargins(0, 0, 0, 0)
        buy_col2_layout.setSpacing(4)
        buy_col2_layout.addWidget(self.buy_overview_method_extra)
        buy_col2_layout.addStretch(1)

        buy_col3_widget = QWidget()
        buy_col3_layout = QVBoxLayout(buy_col3_widget)
        buy_col3_layout.setContentsMargins(0, 0, 0, 0)
        buy_col3_layout.setSpacing(4)
        buy_col3_layout.addWidget(self.buy_overview_finish)
        buy_col3_layout.addStretch(1)

        # 매수 신호검출조건은 상단 1개 박스로 통합 배치한다.
        # 매수 설정 하단은 1열/2열/3열 컬럼 위젯으로 직접 묶어 정렬한다.
        buy_grid.addWidget(self.buy_overview_filter, 0, 0, 1, 3)
        buy_grid.addWidget(buy_col1_widget, 1, 0, Qt.AlignTop)
        buy_grid.addWidget(buy_col2_widget, 1, 1, Qt.AlignTop)
        buy_grid.addWidget(buy_col3_widget, 1, 2, Qt.AlignTop)

        self.buy_detail_widget = QWidget()
        self.buy_detail_widget.setLayout(buy_grid)
        buy_layout.addWidget(self.buy_detail_widget)

        self.buy_detail_expanded = False
        self.buy_detail_widget.setVisible(False)
        self._buy_collapsed_height = 88
        buy_box.setMinimumHeight(self._buy_collapsed_height)
        buy_box.setMaximumHeight(self._buy_collapsed_height)
        buy_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.buy_detail_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        buy_title.setCursor(Qt.PointingHandCursor)

        layout.addWidget(buy_box)

        # SELL 구성
        self.sell_box = sell_box = QGroupBox("")
        sell_box.setObjectName("sectionSellBox")
        sell_box.setStyleSheet(
            "QGroupBox#sectionSellBox {"
            "border: 1px solid #8A98A8;"
            "border-radius: 2px;"
            "margin-top: 8px;"
            "background: transparent;"
            "}"
        )
        sell_layout = QVBoxLayout(sell_box)
        sell_layout.setContentsMargins(10, 8, 10, 10)
        sell_layout.setSpacing(6)

        sell_header_row = QHBoxLayout()
        sell_header_row.setContentsMargins(0, 0, 0, 0)
        sell_header_row.setSpacing(8)
        sell_header_row.setAlignment(Qt.AlignVCenter)

        self.sell_title = sell_title = QLabel("매도설정")
        sell_title.setFixedHeight(30)
        sell_title.setMinimumWidth(132)
        sell_title.setAlignment(Qt.AlignCenter)
        sell_title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #C62828; padding: 0px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;")
        sell_header_row.addWidget(sell_title)

        sell_title_sep = QLabel("|")
        sell_title_sep.setFixedHeight(30)
        sell_title_sep.setAlignment(Qt.AlignCenter)
        sell_title_sep.setStyleSheet("font-size: 13pt; font-weight: bold; color: #000000; padding: 0px 1px;")
        sell_header_row.addWidget(sell_title_sep)

        sell_group_label = QLabel("● 신호검출조건 :")
        sell_group_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        sell_header_row.addWidget(sell_group_label)

        self.sell_signal_expr_line = QLineEdit()
        self.sell_signal_expr_line.setText("A and B and C")
        self.sell_signal_expr_line.setFixedWidth(300)
        self.sell_signal_expr_line.setFixedHeight(34)
        self.sell_signal_expr_line.setAlignment(Qt.AlignCenter)
        self.sell_signal_expr_line.setStyleSheet(
            "font-size: 9pt; font-weight: bold; padding: 1px 4px;"
        )
        self._sell_signal_expr_selection = (-1, 0)
        self._sell_expr_current_operator = "AND"

        def _remember_sell_expr_selection():
            start_pos = self.sell_signal_expr_line.selectionStart()
            selected_len = len(self.sell_signal_expr_line.selectedText())
            if start_pos >= 0 and selected_len > 0:
                self._sell_signal_expr_selection = (start_pos, selected_len)

        self.sell_signal_expr_line.selectionChanged.connect(_remember_sell_expr_selection)
        sell_header_row.addWidget(self.sell_signal_expr_line)

        def _make_expr_button(text, width=42):
            button = QPushButton(text)
            button.setFixedWidth(width)
            button.setFixedHeight(32)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet("font-size: 8pt; padding: 1px 1px;")
            return button

        def _sell_expr_tokens():
            expr = self.sell_signal_expr_line.text().strip()
            if not expr:
                return []
            tokens = expr.replace("(", " ( ").replace(")", " ) ").split()
            normalized = []
            for token in tokens:
                upper = token.upper()
                if upper in {"A", "B", "C", "AND", "OR", "NOT"}:
                    normalized.append(upper)
                else:
                    normalized.append(token)
            return normalized

        def _format_sell_expr_token(token):
            upper = token.upper()
            if upper in {"AND", "OR", "NOT"}:
                return upper.lower()
            if upper in {"A", "B", "C"}:
                return upper
            return token

        def _set_sell_expr_tokens(tokens):
            expr = " ".join(_format_sell_expr_token(token) for token in tokens)
            expr = expr.replace("( ", "(").replace(" )", ")")
            self.sell_signal_expr_line.setText(expr.strip())

        def _append_sell_expr(token):
            tokens = _sell_expr_tokens()
            last = tokens[-1] if tokens else None

            condition_tokens = {"A", "B", "C"}
            op_tokens = {"AND", "OR", "NOT"}

            if token in op_tokens:
                if not tokens:
                    return
                if last in op_tokens:
                    tokens[-1] = token
                    _set_sell_expr_tokens(tokens)
                    return
                if last == "(":
                    return
                if last in condition_tokens or last == ")":
                    tokens.append(token)
                    _set_sell_expr_tokens(tokens)
                return

            if token in condition_tokens:
                if token in tokens:
                    return
                current_op = getattr(self, "_sell_expr_current_operator", "AND")
                if current_op not in op_tokens:
                    current_op = "AND"

                if not tokens or last == "(":
                    tokens.append(token)
                    _set_sell_expr_tokens(tokens)
                    return

                if last in op_tokens:
                    tokens.append(token)
                    _set_sell_expr_tokens(tokens)
                    return

                if last in condition_tokens or last == ")":
                    tokens.extend([current_op, token])
                    _set_sell_expr_tokens(tokens)
                    return

        def _find_sell_expr_operator_index(tokens):
            """현재 커서/선택 위치에 가까운 AND/OR/NOT 연산자 토큰을 찾는다."""
            if not tokens:
                return None

            op_tokens = {"AND", "OR", "NOT"}
            expr = self.sell_signal_expr_line.text()
            cursor_pos = self.sell_signal_expr_line.cursorPosition()
            selection_start = self.sell_signal_expr_line.selectionStart()
            selected_len = len(self.sell_signal_expr_line.selectedText()) if selection_start >= 0 else 0
            selection_end = selection_start + selected_len if selection_start >= 0 else -1

            search_pos = 0
            op_positions = []
            for index, token in enumerate(tokens):
                display_token = _format_sell_expr_token(token)
                pos = expr.lower().find(display_token.lower(), search_pos)
                if pos < 0:
                    pos = search_pos
                end = pos + len(display_token)
                if token in op_tokens:
                    op_positions.append((index, pos, end))
                search_pos = end

            if selection_start >= 0 and selected_len > 0:
                for index, pos, end in op_positions:
                    if not (end <= selection_start or pos >= selection_end):
                        return index

            previous = [item for item in op_positions if item[1] <= cursor_pos]
            if previous:
                return previous[-1][0]
            if op_positions:
                return op_positions[-1][0]
            return None

        def _cycle_sell_expr_operator():
            order = ["AND", "OR", "NOT"]
            tokens = _sell_expr_tokens()
            target_index = _find_sell_expr_operator_index(tokens)

            if target_index is not None:
                current = tokens[target_index]
            else:
                current = getattr(self, "_sell_expr_current_operator", "AND")

            try:
                next_op = order[(order.index(current) + 1) % len(order)]
            except ValueError:
                next_op = "AND"

            self._sell_expr_current_operator = next_op
            self.sell_expr_operator_button.setText(next_op)

            if target_index is not None:
                tokens[target_index] = next_op
                _set_sell_expr_tokens(tokens)
                self.sell_signal_expr_line.setFocus()

        def _is_valid_sell_expr_fragment(fragment):
            tokens = [
                token.upper() if token.upper() in {"A", "B", "C", "AND", "OR", "NOT"} else token
                for token in fragment.replace("(", " ( ").replace(")", " ) ").split()
            ]
            if not tokens:
                return False

            condition_tokens = {"A", "B", "C"}
            op_tokens = {"AND", "OR", "NOT"}

            if tokens[0] in op_tokens or tokens[-1] in op_tokens:
                return False

            prev = None
            depth = 0
            for token in tokens:
                if token == "(":
                    depth += 1
                    if prev in condition_tokens or prev == ")":
                        return False
                elif token == ")":
                    depth -= 1
                    if depth < 0:
                        return False
                    if prev in op_tokens or prev == "(" or prev is None:
                        return False
                elif token in condition_tokens:
                    if prev in condition_tokens or prev == ")":
                        return False
                elif token in op_tokens:
                    if prev is None or prev in op_tokens or prev == "(":
                        return False
                else:
                    return False
                prev = token

            return depth == 0

        def _wrap_selected_sell_expr():
            line = self.sell_signal_expr_line
            expr = line.text()

            start_pos = line.selectionStart()
            selected = line.selectedText()

            if start_pos < 0 or not selected:
                saved_start, saved_len = getattr(self, "_sell_signal_expr_selection", (-1, 0))
                if saved_start >= 0 and saved_len > 0:
                    start_pos = saved_start
                    selected = expr[saved_start:saved_start + saved_len]

            if start_pos < 0 or not selected or not selected.strip():
                return

            selected_text = selected.strip()
            end_pos = start_pos + len(selected)

            if selected_text.startswith("(") and selected_text.endswith(")"):
                inner_text = selected_text[1:-1].strip()
                updated = expr[:start_pos] + inner_text + expr[end_pos:]
                line.setText(updated)
                line.setFocus()
                line.setCursorPosition(start_pos + len(inner_text))
                self._sell_signal_expr_selection = (-1, 0)
                return

            if "(" in selected_text or ")" in selected_text:
                return

            if not _is_valid_sell_expr_fragment(selected_text):
                return

            wrapped = expr[:start_pos] + "(" + selected_text + ")" + expr[end_pos:]
            line.setText(wrapped)
            line.setFocus()
            line.setCursorPosition(start_pos + len(selected_text) + 2)
            self._sell_signal_expr_selection = (-1, 0)

        for token, width in [
            ("A", 26), ("/", 6), ("B", 26), ("/", 6), ("C", 26),
        ]:
            if token == "/":
                sep = QLabel("/")
                sep.setStyleSheet("font-size: 9pt;")
                sell_header_row.addWidget(sep)
            else:
                btn = _make_expr_button(token, width)
                btn.clicked.connect(lambda _, t=token: _append_sell_expr(t))
                sell_header_row.addWidget(btn)

        sep = QLabel("/")
        sep.setStyleSheet("font-size: 9pt;")
        sell_header_row.addWidget(sep)

        self.sell_expr_operator_button = _make_expr_button("AND", 48)
        self.sell_expr_operator_button.clicked.connect(_cycle_sell_expr_operator)
        sell_header_row.addWidget(self.sell_expr_operator_button)

        wrap_button = _make_expr_button("()", 34)
        wrap_button.pressed.connect(_wrap_selected_sell_expr)
        sell_header_row.addWidget(wrap_button)

        clear_button = _make_expr_button("지움", 44)
        clear_button.clicked.connect(lambda: self.sell_signal_expr_line.clear())
        sell_header_row.addWidget(clear_button)

        sell_header_row.addSpacing(300)

        method_select_label = QLabel("● 매도방식지정 :")
        method_select_label.setStyleSheet("font-size: 9pt; font-weight: normal; padding: 2px 1px;")
        sell_header_row.addWidget(method_select_label)

        self.sell_method_select_a_check = QCheckBox("설정 A")
        self.sell_method_select_b_check = QCheckBox("설정 B")
        self.sell_method_select_c_check = QCheckBox("설정 C")
        self.sell_method_select_a_check.setChecked(True)

        for check in [
            self.sell_method_select_a_check,
            self.sell_method_select_b_check,
            self.sell_method_select_c_check,
        ]:
            check.setFixedHeight(32)
            check.setStyleSheet("font-size: 9pt; font-weight: normal;")
            sell_header_row.addWidget(check)

        self._sell_method_select_guard = False

        def _sync_sell_method_select(source_check=None):
            if self._sell_method_select_guard:
                return
            checks = [
                self.sell_method_select_a_check,
                self.sell_method_select_b_check,
                self.sell_method_select_c_check,
            ]
            if any(check.isChecked() for check in checks):
                return
            self._sell_method_select_guard = True
            try:
                if source_check is not None:
                    source_check.setChecked(True)
                else:
                    self.sell_method_select_a_check.setChecked(True)
            finally:
                self._sell_method_select_guard = False

        for check in [
            self.sell_method_select_a_check,
            self.sell_method_select_b_check,
            self.sell_method_select_c_check,
        ]:
            check.toggled.connect(lambda _, c=check: _sync_sell_method_select(c))

        sell_header_row.addStretch(1)

        self.sell_header_widget = QWidget()
        self.sell_header_widget.setLayout(sell_header_row)
        self.sell_header_widget.setFixedHeight(44)
        self.sell_header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        sell_layout.addWidget(self.sell_header_widget)

        sell_grid = QGridLayout()
        sell_grid.setColumnStretch(0, 1)
        sell_grid.setColumnStretch(1, 1)
        sell_grid.setColumnStretch(2, 1)
        sell_grid.setVerticalSpacing(12)
        def _setup_sell_signal_condition_box(box, title, checked=True):
            box.setTitle(title)
            box.setMinimumHeight(158)
            box.setContentsMargins(0, 0, 0, 0)
            box.layout().setContentsMargins(8, 14, 8, 8)
            box.setStyleSheet(
                "QGroupBox { font-weight: bold; } "
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 2px 4px; font-size: 26px; font-weight: bold; }"
            )

            return box

        self.sell_signal_condition_1 = _setup_sell_signal_condition_box(
            self._make_sell_signal_condition_1_overview_controls(),
            "신호검출조건 A",
            True,
        )
        self.sell_signal_condition_2 = _setup_sell_signal_condition_box(
            self._make_sell_signal_condition_2_overview_controls(),
            "신호검출조건 B",
            False,
        )
        self.sell_signal_condition_3 = _setup_sell_signal_condition_box(
            self._make_sell_signal_condition_3_overview_controls(),
            "신호검출조건 C",
            False,
        )
        self.sell_overview_scenario = self._make_sell_scenario_overview_controls()

        sell_grid.addWidget(self.sell_signal_condition_1, 0, 0)
        sell_grid.addWidget(self.sell_signal_condition_2, 0, 1)
        sell_grid.addWidget(self.sell_signal_condition_3, 0, 2)
        sell_grid.addWidget(self.sell_overview_scenario, 1, 0, 1, 3)

        self.sell_detail_widget = QWidget()
        self.sell_detail_widget.setLayout(sell_grid)
        sell_layout.addWidget(self.sell_detail_widget)

        self.sell_detail_expanded = False
        self.sell_detail_widget.setVisible(False)
        self._sell_collapsed_height = 88
        sell_box.setMinimumHeight(self._sell_collapsed_height)
        sell_box.setMaximumHeight(self._sell_collapsed_height)
        sell_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.sell_detail_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sell_title.setCursor(Qt.PointingHandCursor)

        layout.addWidget(sell_box)

        # 구성탭 표시모드: 기본설정은 항상 표시하고, 매수/매도 상세만 접기/펼치기 대상으로 둔다.
        # 내부 설정 컨트롤은 삭제/변경하지 않고 외곽 표시상태만 제어한다.
        # 토글 동작은 제목 박스(매수설정/매도설정 QLabel) 클릭에만 반응한다.
        # 신호검출조건 입력창, A/B/C/D, AND/OR/NOT, 괄호, 지움 버튼은 토글 대상이 아니다.
        # 창 최대화 상태에서는 매수/매도 전체 영역을 펼쳐 스크롤로 확인한다.
        self._control_section_mode = "summary"
        basic_title.setCursor(Qt.ArrowCursor)

        self._control_header_click_modes = {}

        def _register_control_header_click(mode, widgets):
            for widget in widgets:
                if widget is None:
                    continue
                self._control_header_click_modes[widget] = mode
                widget.setCursor(Qt.PointingHandCursor)
                widget.installEventFilter(self)

        _register_control_header_click(
            "buy",
            [
                buy_title,
            ],
        )
        _register_control_header_click(
            "sell",
            [
                sell_title,
            ],
        )

        scroll.setWidget(page)
        self._apply_control_section_mode("summary", force=True)
        self._sync_control_page_size()
        outer.addWidget(scroll)

        self.tabs.addTab(self.control_tab, "구성")

    def eventFilter(self, obj, event):
        """구성탭 헤더 클릭 처리.

        매수/매도 제목 박스만 토글 대상으로 삼고,
        신호검출조건 입력창/연산자/괄호/지움 등 실제 설정 컨트롤은 건드리지 않는다.
        """
        if event.type() == QEvent.MouseButtonPress:
            mode_map = getattr(self, "_control_header_click_modes", {})
            mode = mode_map.get(obj)
            if mode and event.button() == Qt.LeftButton:
                self._toggle_control_section_mode(mode)
                event.accept()
                return True

        sync_handlers = getattr(self, "_direction_compare_sync_handlers", {})
        sync_handler = sync_handlers.get(obj)
        if sync_handler is not None and event.type() in (
            QEvent.MouseButtonRelease,
            QEvent.KeyRelease,
            QEvent.FocusOut,
            QEvent.Hide,
        ):
            QTimer.singleShot(0, sync_handler)

        return super().eventFilter(obj, event)

    def _set_control_section_expanded(self, box, detail_widget, expanded, collapsed_height):
        """구성탭 섹션 높이/표시 상태를 한 곳에서 제어한다.

        내부 상세 위젯 내용은 변경하지 않고, 외곽 섹션의 펼침/접힘만 처리한다.
        """
        detail_widget.setVisible(expanded)
        if expanded:
            box.setMinimumHeight(collapsed_height)
            box.setMaximumHeight(16777215)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            detail_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        else:
            box.setMinimumHeight(collapsed_height)
            box.setMaximumHeight(collapsed_height)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            detail_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        box.updateGeometry()
        detail_widget.updateGeometry()

    def _sync_control_page_size(self):
        """구성탭 페이지 폭/높이 동기화.

        내부 설정 컨트롤은 변경하지 않고, QScrollArea 안쪽 page 컨테이너만
        현재 창 폭과 표시 중인 섹션 높이에 맞춘다.
        이전 단계에서 setFixedHeight 된 값이 남아 있으면 다음 펼침/접힘 계산이
        꼬일 수 있으므로 높이 제한을 먼저 풀고 다시 계산한다.
        """
        if not hasattr(self, "control_page"):
            return

        if hasattr(self, "control_scroll"):
            viewport_width = self.control_scroll.viewport().width()
            if viewport_width <= 0:
                viewport_width = self.width() - 32

            # 다이얼로그가 2360px 고정 기준이므로, 첫 진입 때 viewport 계산이
            # 늦어져도 구성탭 섹션이 좁게 압축되지 않도록 최소 폭을 보장한다.
            min_page_width = max(900, self.minimumWidth() - 80)
            page_width = max(min_page_width, viewport_width - 2)
            self.control_page.setFixedWidth(page_width)
            self.control_scroll.setWidgetResizable(False)

        # 높이 재계산 전 이전 fixedHeight 영향을 제거한다.
        self.control_page.setMinimumHeight(0)
        self.control_page.setMaximumHeight(16777215)
        self.control_page.adjustSize()

        page_height = self.control_page.sizeHint().height()
        if page_height > 0:
            self.control_page.setFixedHeight(page_height)

        self.control_page.updateGeometry()
        if hasattr(self, "control_scroll"):
            self.control_scroll.updateGeometry()

    def _defer_sync_control_page_size(self):
        """레이아웃 변경 직후 1회 지연 갱신.

        Qt가 위젯 숨김/표시를 실제 배치에 반영한 뒤 page 크기를 다시 맞춘다.
        내부 설정 컨트롤의 내용·순서·옵션은 변경하지 않는다.
        """
        if hasattr(self, "control_page"):
            QTimer.singleShot(0, self._sync_control_page_size)

    def _fit_dialog_height_to_control_mode(self, mode=None):
        """구성탭 표시모드에 맞춰 다이얼로그 높이를 조정한다.

        하단의 큰 빈 공간은 QTabWidget/다이얼로그가 기존 1280px 높이를
        계속 유지해서 생긴다. 내부 설정 컨트롤은 변경하지 않고,
        요약/개별/전체 모드에 맞게 창 높이만 조정한다.
        """
        if self.isMaximized() or not hasattr(self, "control_page"):
            return

        mode = mode or getattr(self, "_control_section_mode", "summary")

        self._sync_control_page_size()
        page_height = max(0, self.control_page.sizeHint().height())

        # 탭/버튼/창 테두리 여유분. 실제 내용 높이는 control_page 기준이다.
        chrome_extra = 125
        if mode == "summary":
            target_height = page_height + chrome_extra
            target_height = max(360, min(target_height, 460))
        elif mode in ("buy", "sell"):
            target_height = page_height + chrome_extra
            target_height = max(720, min(target_height, 1180))
        else:  # all
            target_height = 1280

        self.resize(self.width(), int(target_height))

    def _defer_fit_dialog_height_to_control_mode(self, mode=None):
        if hasattr(self, "control_page"):
            QTimer.singleShot(0, lambda m=mode: self._fit_dialog_height_to_control_mode(m))

    def _toggle_control_section_mode(self, mode):
        """구성탭 제목/헤더 클릭용 토글.

        매수/매도는 서로 자동으로 접지 않는다.
        각 영역은 다시 누르기 전까지 현재 펼침 상태를 유지한다.
        """
        buy_expanded = bool(getattr(self, "buy_detail_expanded", False))
        sell_expanded = bool(getattr(self, "sell_detail_expanded", False))

        if mode == "buy":
            buy_expanded = not buy_expanded
        elif mode == "sell":
            sell_expanded = not sell_expanded
        else:
            buy_expanded = False
            sell_expanded = False

        if buy_expanded and sell_expanded:
            next_mode = "all"
        elif buy_expanded:
            next_mode = "buy"
        elif sell_expanded:
            next_mode = "sell"
        else:
            next_mode = "summary"

        self._apply_control_section_mode(next_mode, force=True)

    def _set_control_section_title_states(self, buy_expanded, sell_expanded):
        """구성탭 제목 화살표 표시만 담당한다.

        기본설정은 항상 표시되는 영역이므로 클릭 기능 없이 펼침 화살표로 고정한다.
        매수/매도 내부 설정 컨트롤은 건드리지 않는다.
        """
        if hasattr(self, "basic_title"):
            # 기본설정은 접기/펼치기 대상이 아니므로 초기/요약 상태의 방향을 유지한다.
            # 매수/매도 접힘 표시와 방향을 맞추기 위해 항상 ▶ 로 표기한다.
            self.basic_title.setText("▶ 기본설정")
        if hasattr(self, "buy_title"):
            self.buy_title.setText(("▼ " if buy_expanded else "▶ ") + "매수설정")
        if hasattr(self, "sell_title"):
            self.sell_title.setText(("▼ " if sell_expanded else "▶ ") + "매도설정")

    def _set_control_full_view_button_state(self, mode):
        """전체보기 버튼 연결을 한 곳에서만 갱신한다."""
        if not hasattr(self, "control_full_view_button"):
            return

        if mode == "all":
            button_text = "전체접기"
            target_mode = "summary"
        else:
            button_text = "전체보기"
            target_mode = "all"

        self.control_full_view_button.setText(button_text)
        try:
            self.control_full_view_button.clicked.disconnect()
        except TypeError:
            pass
        self.control_full_view_button.clicked.connect(
            lambda checked=False, m=target_mode: self._apply_control_section_mode(m, force=True)
        )

    def _apply_control_section_mode(self, mode, force=False):
        """
        구성탭 전용 표시모드.
        - summary: 기본설정은 표시, 매수/매도는 제목 라인만 표시
        - buy: 기본설정 표시 + 매수 상세 표시
        - sell: 기본설정 표시 + 매도 상세 표시
        - all: 기본설정 표시 + 매수/매도 상세 표시(전체보기/동시 펼침)
        """
        if not hasattr(self, "buy_detail_widget") or not hasattr(self, "sell_detail_widget"):
            return

        if mode not in ("summary", "buy", "sell", "all"):
            mode = "summary"

        current_mode = getattr(self, "_control_section_mode", "summary")
        if not force and mode == current_mode:
            mode = "summary"

        self._control_section_mode = mode

        buy_expanded = mode in ("buy", "all")
        sell_expanded = mode in ("sell", "all")

        self.buy_detail_expanded = buy_expanded
        self.sell_detail_expanded = sell_expanded

        self._set_control_section_expanded(
            self.buy_box,
            self.buy_detail_widget,
            buy_expanded,
            self._buy_collapsed_height,
        )
        self._set_control_section_expanded(
            self.sell_box,
            self.sell_detail_widget,
            sell_expanded,
            self._sell_collapsed_height,
        )

        # 제목/전체보기 버튼 상태는 전용 헬퍼에서만 갱신한다.
        self._set_control_section_title_states(buy_expanded, sell_expanded)
        self._set_control_full_view_button_state(mode)

        if hasattr(self, "control_scroll"):
            self.control_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarAsNeeded if mode == "all" else Qt.ScrollBarAlwaysOff
            )

        # 페이지 폭은 스크롤 영역에 맞추고, 높이는 현재 보이는 내용만큼만 잡는다.
        self._sync_control_page_size()
        self._defer_sync_control_page_size()
        self._defer_fit_dialog_height_to_control_mode(mode)

        self.basic_box.updateGeometry()
        self.buy_box.updateGeometry()
        self.sell_box.updateGeometry()

        # 개별 섹션을 펼칠 때 해당 영역이 바로 보이도록 위치를 맞춘다.
        if hasattr(self, "control_scroll"):
            if mode == "buy":
                self.control_scroll.ensureWidgetVisible(self.buy_box, 0, 0)
            elif mode == "sell":
                self.control_scroll.ensureWidgetVisible(self.sell_box, 0, 0)
            elif mode == "summary":
                self.control_scroll.verticalScrollBar().setValue(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "control_scroll"):
            self._sync_control_page_size()
            self._defer_sync_control_page_size()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "control_scroll"):
            self._sync_control_page_size()
            self._defer_sync_control_page_size()
            self._defer_fit_dialog_height_to_control_mode(getattr(self, "_control_section_mode", "summary"))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "buy_detail_widget"):
            if self.isMaximized():
                self._apply_control_section_mode("all", force=True)
            elif getattr(self, "_control_section_mode", "summary") == "all":
                self._apply_control_section_mode("summary", force=True)
            else:
                self._defer_fit_dialog_height_to_control_mode(getattr(self, "_control_section_mode", "summary"))
