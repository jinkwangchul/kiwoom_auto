import json

from PyQt5.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class IndicatorFollowDataTabsMixin:
    def _build_advanced_tab(self):
        self.advanced_tab = QWidget()
        layout = QVBoxLayout(self.advanced_tab)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "고급/확장 설정\n\n"
            "현재 잠금:\n"
            "- 다중매수\n"
            "- 다중호가\n"
            "- 다중지점\n"
            "- 지속매수\n"
            "- 평단 중심 매수강도\n"
            "- 능동매수\n"
            "- 루틴 주문취소\n\n"
            "위 항목은 개념 확정 후 별도 설정 화면으로 연결합니다."
        )
        layout.addWidget(text, 1)

        self.tabs.addTab(self.advanced_tab, "고급")

    def _build_validation_tab(self):
        self.validation_tab = QWidget()
        layout = QVBoxLayout(self.validation_tab)

        box = QGroupBox("검증 결과")
        form = QFormLayout(box)

        self.validation_signal_line = self._readonly_line()
        self.validation_execution_line = self._readonly_line()
        self.validation_sell_line = self._readonly_line()
        self.validation_buy_line = self._readonly_line()

        form.addRow("신호 구조", self.validation_signal_line)
        form.addRow("실주문 실행", self.validation_execution_line)
        form.addRow("매도 구조", self.validation_sell_line)
        form.addRow("매수 확장", self.validation_buy_line)

        layout.addWidget(box)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setVisible(False)
        layout.addWidget(self.preview_text)

        self.developer_button = QPushButton("개발자 정보 보기/숨기기")
        self.developer_button.clicked.connect(
            lambda: self.preview_text.setVisible(not self.preview_text.isVisible())
        )
        layout.addWidget(self.developer_button)

        layout.addStretch(1)
        self.tabs.addTab(self.validation_tab, "검증")

    def load_rules(self):
        if not self.rules_path.exists():
            self.rules_data = {}
            self.setWindowTitle(f"{getattr(self, 'routine_name', '') or 'Routine'} \uc124\uc815")
            QMessageBox.warning(self, "rules.json 없음", f"rules.json을 찾을 수 없습니다.\n{self.rules_path}")
            self._clear_fields()
            return

        try:
            with self.rules_path.open("r", encoding="utf-8") as f:
                self.rules_data = json.load(f)
        except Exception as exc:
            self.rules_data = {}
            self.setWindowTitle(f"{getattr(self, 'routine_name', '') or 'Routine'} \uc124\uc815")
            QMessageBox.critical(self, "로드 실패", f"rules.json 로드 실패\n{exc}")
            self._clear_fields()
            return

        self._populate_fields()
        self.refresh_preview()

    def _clear_fields(self):
        self._set_card_status(self.card_routine, "로드 실패", "error")
        self._set_card_status(self.card_buy, "확인 불가", "error")
        self._set_card_status(self.card_sell, "확인 불가", "error")
        self._set_card_status(self.card_profit, "확인 불가", "error")
        self._set_card_status(self.card_advanced, "잠금", "locked")
        self._set_card_status(self.card_validation, "오류", "error")

        self.preview_text.clear()

    def _populate_fields(self):
        data = self.rules_data

        routine_name = data.get("routine_name") or data.get("name") or getattr(self, "routine_name", "") or (self.routine_path.name if getattr(self, "routine_path", None) else "Routine")
        self.title_label.setText(str(routine_name))
        self.setWindowTitle(f"{routine_name} 설정")

        principle = data.get("principle", {}) if isinstance(data.get("principle", {}), dict) else {}

        enabled = bool(data.get("enabled", True))
        signal_only = bool(data.get("signal_only", principle.get("signal_only", True)))
        execution_enabled = bool(data.get("execution_enabled", principle.get("execution_enabled", False)))

        buy = data.get("buy", {}) if isinstance(data.get("buy", {}), dict) else {}
        buy_enabled = bool(buy.get("enabled", True))
        buy_delay = buy.get("delay_bar", "")

        sell = data.get("sell", {}) if isinstance(data.get("sell", {}), dict) else {}
        sell_enabled = bool(sell.get("enabled", True))
        sell_logic = str(sell.get("signal_logic", "OR")).upper()
        if sell_logic not in ("OR", "AND"):
            sell_logic = "OR"

        signals = sell.get("signals", {}) if isinstance(sell.get("signals", {}), dict) else {}

        macd_sell = signals.get("macd_sell", {}) if isinstance(signals.get("macd_sell", {}), dict) else {}
        macd_sell_enabled = bool(macd_sell.get("enabled", True))
        macd_sell_delay = macd_sell.get("delay_bar", sell.get("delay_bar", ""))

        profit_sell = signals.get("profit_rate_sell", {}) if isinstance(signals.get("profit_rate_sell", {}), dict) else {}
        profit_sell_enabled = bool(profit_sell.get("enabled", False))
        target = (
            profit_sell.get("target_profit_rate")
            if profit_sell.get("target_profit_rate") is not None
            else profit_sell.get("profit_rate_percent", None)
        )
        basis = profit_sell.get("basis", "average_price")

        # 컨트롤 패널 상태
        self._set_card_status(self.card_routine, "활성" if enabled else "비활성", "active" if enabled else "inactive")
        self._set_card_status(self.card_buy, "활성" if buy_enabled else "비활성", "active" if buy_enabled else "inactive")
        self._set_card_status(self.card_sell, "활성" if sell_enabled else "비활성", "active" if sell_enabled else "inactive")
        self._set_card_status(self.card_profit, "활성" if profit_sell_enabled else "비활성", "active" if profit_sell_enabled else "inactive")
        self._set_card_status(self.card_advanced, "잠금", "locked")
        self._set_card_status(self.card_validation, "정상", "active")

        # 매수 탭
        self.buy_enabled_check.setChecked(buy_enabled)
        self.buy_delay_line.setText(str(buy_delay))
        self.buy_status_line.setText("기본 매수 구조 사용" if buy_enabled else "매수 비활성")

        # 매도 탭
        self.sell_enabled_check.setChecked(sell_enabled)
        self.sell_logic_combo.setCurrentText(sell_logic)

        self.macd_sell_enabled_check.setChecked(macd_sell_enabled)
        self.macd_sell_delay_line.setText(str(macd_sell_delay))
        self.macd_sell_status_line.setText("사용" if macd_sell_enabled else "비활성")

        self.profit_sell_enabled_check.setChecked(profit_sell_enabled)
        if target is None:
            self.target_profit_line.setText("미설정")
        else:
            self.target_profit_line.setText(f"{target} %")
        self.profit_basis_line.setText("평단 대비 현재가" if basis == "average_price" else str(basis))

        # 검증 탭
        self.validation_signal_line.setText("BUY / SELL / signal=None")
        self.validation_execution_line.setText("비활성" if not execution_enabled else "활성")
        self.validation_sell_line.setText(f"{sell_logic} 결합")
        self.validation_buy_line.setText("확장 잠금")

        if not signal_only:
            self._set_card_status(self.card_validation, "확인 필요", "locked")
            self.validation_signal_line.setText("signal_only 비활성 확인 필요")

        if execution_enabled:
            self._set_card_status(self.card_validation, "주의", "locked")
            self.validation_execution_line.setText("활성 - 실주문 전 확인 필요")

    def refresh_preview(self):
        if not self.rules_data:
            self.preview_text.setPlainText("개발자 정보 없음")
            return

        data = self.rules_data
        sell = data.get("sell", {}) if isinstance(data.get("sell", {}), dict) else {}
        signals = sell.get("signals", {}) if isinstance(sell.get("signals", {}), dict) else {}

        preview = {
            "rules_path": str(self.rules_path),
            "routine_name": self.title_label.text(),
            "rules_version": data.get("rules_version") or data.get("version") or data.get("schema_version") or "",
            "enabled": self.card_routine["status"].text(),
            "buy": {
                "status": self.card_buy["status"].text(),
                "delay_bar": self.buy_delay_line.text(),
            },
            "sell": {
                "status": self.card_sell["status"].text(),
                "signal_logic": self.sell_logic_combo.currentText(),
                "macd_sell": self.card_sell["status"].text(),
                "profit_rate_sell": self.card_profit["status"].text(),
            },
            "advanced": self.card_advanced["status"].text(),
            "validation": self.card_validation["status"].text(),
            "raw_sell_keys": list(signals.keys()),
        }

        self.preview_text.setPlainText(json.dumps(preview, ensure_ascii=False, indent=2))

