from __future__ import annotations

import sys
from dataclasses import dataclass

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class PreviewStock:
    code: str
    name: str
    operation: str
    price: str
    profit_rate: str
    holding: str
    round_text: str
    order_status: str
    routine: str
    initial_budget: str
    stock_limit: str
    used_budget: str
    remaining_budget: str
    increase_method: str
    tone: str


PREVIEW_STOCKS = (
    PreviewStock(
        "003550",
        "LG",
        "운영중",
        "79,500",
        "+3.24%",
        "84주",
        "2 / 4회",
        "매수 대기",
        "지표추종매매",
        "50만",
        "250만",
        "125만",
        "125만",
        "비율 증가",
        "normal",
    ),
    PreviewStock(
        "005930",
        "삼성전자",
        "일시정지",
        "94,200",
        "-1.12%",
        "20주",
        "1 / 3회",
        "없음",
        "지표추종매매",
        "100만",
        "300만",
        "188만",
        "112만",
        "동일 금액",
        "paused",
    ),
    PreviewStock(
        "005380",
        "현대차",
        "운영중",
        "284,500",
        "+0.68%",
        "5주",
        "1 / 4회",
        "주문 접수",
        "지표추종매매",
        "75만",
        "350만",
        "142만",
        "208만",
        "비율 증가",
        "normal",
    ),
    PreviewStock(
        "035420",
        "NAVER",
        "검토필요",
        "221,000",
        "-2.40%",
        "12주",
        "2 / 3회",
        "주문 차단",
        "지표추종매매",
        "80만",
        "300만",
        "265만",
        "35만",
        "비율 증가",
        "review",
    ),
    PreviewStock(
        "028260",
        "삼성물산",
        "대기",
        "151,800",
        "+0.00%",
        "0주",
        "0 / 4회",
        "신호 대기",
        "지표추종매매",
        "50만",
        "200만",
        "0",
        "200만",
        "동일 금액",
        "waiting",
    ),
)


class StockOperationRow(QFrame):
    def __init__(self, stock: PreviewStock, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stockOperationRow")
        self.setProperty("tone", stock.tone)
        self.setFixedHeight(82)
        self.setMinimumWidth(0)

        layout = QGridLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)

        identity = QWidget()
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(7)
        dot = QLabel("●")
        dot.setObjectName("statusDot")
        dot.setProperty("tone", stock.tone)
        name = QLabel(f"{stock.name}  {stock.code}")
        name.setObjectName("stockName")
        identity_layout.addWidget(dot)
        identity_layout.addWidget(name)
        identity_layout.addStretch(1)

        operation = self._combo(
            ("운영중", "일시정지", "대기", "검토필요"), stock.operation, 82
        )
        operation.setObjectName("operationCombo")

        layout.addWidget(identity, 0, 0)
        layout.addWidget(operation, 0, 1)
        layout.addWidget(self._value("현재", stock.price), 0, 2)
        layout.addWidget(
            self._value(
                "손익",
                stock.profit_rate,
                "profitPositive" if stock.profit_rate.startswith("+") else "profitNegative",
            ),
            0,
            3,
        )
        layout.addWidget(self._value("보유", stock.holding), 0, 4)
        layout.addWidget(self._value("진행", stock.round_text), 0, 5)
        layout.addWidget(self._value("주문", stock.order_status), 0, 6)

        routine = self._combo(
            ("지표추종매매", "등록확인루틴"), stock.routine, 150
        )
        increase = self._combo(
            ("비율 증가", "동일 금액"), stock.increase_method, 88
        )
        control = QPushButton("제어")
        control.setObjectName("rowControlButton")
        control.setFixedWidth(62)

        layout.addWidget(routine, 1, 0)
        layout.addWidget(self._money_edit("최초", stock.initial_budget), 1, 1, 1, 2)
        layout.addWidget(self._money_edit("한도", stock.stock_limit), 1, 3)
        layout.addWidget(self._value("사용", stock.used_budget), 1, 4)
        layout.addWidget(self._value("잔여", stock.remaining_budget), 1, 5)
        layout.addWidget(increase, 1, 6)
        layout.addWidget(control, 0, 7, 2, 1)

        layout.setColumnMinimumWidth(0, 154)
        layout.setColumnMinimumWidth(1, 82)
        layout.setColumnMinimumWidth(2, 78)
        layout.setColumnMinimumWidth(3, 92)
        layout.setColumnMinimumWidth(4, 88)
        layout.setColumnMinimumWidth(5, 92)
        layout.setColumnMinimumWidth(6, 100)
        layout.setColumnStretch(0, 2)
        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(4, 1)
        layout.setColumnStretch(5, 1)
        layout.setColumnStretch(6, 1)

    @staticmethod
    def _value(caption: str, value: str, object_name: str = "rowValue") -> QLabel:
        label = QLabel(f"{caption}  {value}")
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        return label

    @staticmethod
    def _combo(items: tuple[str, ...], current: str, width: int) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setCurrentText(current)
        combo.setFixedWidth(width)
        combo.setFixedHeight(27)
        return combo

    @staticmethod
    def _money_edit(caption: str, value: str) -> QLineEdit:
        edit = QLineEdit(f"{caption}  {value}")
        edit.setObjectName("inlineMoneyEdit")
        edit.setAlignment(Qt.AlignRight)
        edit.setFixedHeight(27)
        edit.setMinimumWidth(0)
        edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        return edit


class MainMonitoringPreview(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("키움 OpenAPI 자동매매 시스템 - 메인 관제창 UI 시안")
        self.resize(1120, 720)
        self.setMinimumSize(1040, 680)

        root = QWidget()
        root.setObjectName("monitorPreviewRoot")
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(7)

        main_layout.addLayout(self._top_area())
        main_layout.addWidget(self._filter_bar())
        main_layout.addWidget(self._stock_console(), 1)
        main_layout.addLayout(self._bottom_actions())

        self.setCentralWidget(root)
        self.statusBar().showMessage("UI 배치 확인용 시안 · 실제 데이터 및 주문 기능과 연결되지 않음")
        self._apply_style(root)

    def _top_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(7)

        system = QGroupBox("시스템 상태")
        system_grid = QGridLayout(system)
        system_grid.setContentsMargins(9, 7, 9, 7)
        system_grid.setHorizontalSpacing(10)
        system_grid.setVerticalSpacing(5)
        system_grid.addWidget(self._status_label("연결", "키움 연결"), 0, 0)
        system_grid.addWidget(self._status_label("계좌", "8139-****"), 0, 1)
        system_grid.addWidget(self._status_label("구분", "모의투자"), 0, 2)
        system_grid.addWidget(self._status_label("자동매매", "운영 중"), 1, 0)
        system_grid.addWidget(self._status_label("매수", "가능"), 1, 1)
        emergency = QPushButton("긴급정지")
        emergency.setObjectName("dangerButton")
        emergency.setFixedWidth(92)
        system_grid.addWidget(emergency, 1, 2)

        funds = QGroupBox("계좌 자금")
        funds_grid = QGridLayout(funds)
        funds_grid.setContentsMargins(9, 7, 9, 7)
        funds_grid.addWidget(QLabel("총 예수금"), 0, 0)
        funds_grid.addWidget(self._fund_value("18,420,000"), 0, 1)
        funds_grid.addWidget(QLabel("주문 가능"), 1, 0)
        funds_grid.addWidget(self._fund_value("12,360,000"), 1, 1)

        limits = QGroupBox("운영 한도")
        limits_grid = QGridLayout(limits)
        limits_grid.setContentsMargins(9, 7, 9, 7)
        limits_grid.setHorizontalSpacing(8)
        limits_grid.setVerticalSpacing(5)
        limits_grid.addWidget(self._metric("전체", "1,000만"), 0, 0)
        limits_grid.addWidget(self._metric("사용", "521만"), 0, 1)
        limits_grid.addWidget(self._metric("가용", "479만"), 0, 2)
        limits_grid.addWidget(self._metric("사용률", "52.1%"), 1, 0)
        limits_grid.addWidget(self._metric("운영", "3종목"), 1, 1)
        limits_grid.addWidget(self._metric("상태", "정상"), 1, 2)

        for box in (system, funds, limits):
            box.setFixedHeight(104)

        layout.addWidget(system, 5)
        layout.addWidget(funds, 3)
        layout.addWidget(limits, 4)
        return layout

    def _filter_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("filterBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(9, 5, 9, 5)
        layout.setSpacing(8)

        title = QLabel("종목 운영 콘솔")
        title.setObjectName("consoleTitle")
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addWidget(QLabel("루틴"))
        layout.addWidget(self._filter_combo(("전체", "지표추종매매")))
        layout.addWidget(QLabel("운영"))
        layout.addWidget(self._filter_combo(("전체", "운영중", "일시정지", "검토필요")))
        layout.addWidget(QLabel("주문"))
        layout.addWidget(self._filter_combo(("전체", "대기", "접수", "차단")))
        layout.addStretch(1)
        search = QLineEdit()
        search.setPlaceholderText("종목명 또는 코드 검색")
        search.setFixedWidth(190)
        layout.addWidget(search)
        return bar

    def _stock_console(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("stockConsole")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        body = QWidget()
        body.setObjectName("stockConsoleBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        for stock in PREVIEW_STOCKS:
            layout.addWidget(StockOperationRow(stock))
        layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _bottom_actions(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(7)
        labels = (
            "종목등록설정",
            "자동매매설정",
            "전체 자동매매 정지",
            "운영 재개",
            "초기화",
            "로그 보기",
            "검토관리종목",
            "종료",
        )
        for text in labels:
            button = QPushButton(text)
            button.setMinimumHeight(31)
            if text == "전체 자동매매 정지":
                button.setObjectName("warningButton")
            elif text == "운영 재개":
                button.setObjectName("successButton")
            elif text == "종료":
                button.setObjectName("secondaryButton")
            layout.addWidget(button)
        return layout

    @staticmethod
    def _status_label(caption: str, value: str) -> QLabel:
        label = QLabel(f"{caption}  {value}")
        label.setObjectName("statusValue")
        return label

    @staticmethod
    def _fund_value(value: str) -> QLabel:
        label = QLabel(value)
        label.setObjectName("fundValue")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    @staticmethod
    def _metric(caption: str, value: str) -> QLabel:
        label = QLabel(f"{caption}  {value}")
        label.setObjectName("metricValue")
        label.setAlignment(Qt.AlignCenter)
        return label

    @staticmethod
    def _filter_combo(items: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setFixedHeight(27)
        return combo

    @staticmethod
    def _apply_style(root: QWidget) -> None:
        root.setStyleSheet(
            """
            QWidget#monitorPreviewRoot {
                background: #f3f5f8;
                color: #202833;
                font-family: "Malgun Gothic", "Segoe UI";
                font-size: 9pt;
            }
            QWidget#monitorPreviewRoot QGroupBox {
                background: #ffffff;
                border: 1px solid #d5dae2;
                border-radius: 5px;
                margin-top: 11px;
                padding: 6px;
                color: #1c2735;
                font-weight: 600;
            }
            QWidget#monitorPreviewRoot QGroupBox::title {
                subcontrol-origin: margin;
                left: 9px;
                padding: 0 4px;
            }
            QWidget#monitorPreviewRoot QLabel {
                background: transparent;
            }
            QWidget#monitorPreviewRoot QLabel#statusValue,
            QWidget#monitorPreviewRoot QLabel#metricValue {
                color: #172231;
                font-weight: 600;
            }
            QWidget#monitorPreviewRoot QLabel#fundValue {
                color: #0d1b2a;
                font-size: 12pt;
                font-weight: 700;
            }
            QWidget#monitorPreviewRoot QFrame#filterBar {
                background: #ffffff;
                border: 1px solid #d5dae2;
                border-radius: 4px;
            }
            QWidget#monitorPreviewRoot QLabel#consoleTitle {
                color: #162235;
                font-size: 10pt;
                font-weight: 700;
            }
            QWidget#monitorPreviewRoot QComboBox,
            QWidget#monitorPreviewRoot QLineEdit {
                min-height: 25px;
                background: #ffffff;
                border: 1px solid #cbd2dc;
                border-radius: 3px;
                padding: 1px 7px;
            }
            QWidget#monitorPreviewRoot QLineEdit#inlineMoneyEdit {
                background: transparent;
                border-color: transparent;
                color: #263445;
                font-weight: 600;
            }
            QWidget#monitorPreviewRoot QLineEdit#inlineMoneyEdit:focus {
                background: #ffffff;
                border-color: #5d7fa8;
            }
            QWidget#monitorPreviewRoot QScrollArea#stockConsole {
                background: transparent;
                border: 0;
            }
            QWidget#monitorPreviewRoot QWidget#stockConsoleBody {
                background: transparent;
            }
            QWidget#monitorPreviewRoot QFrame#stockOperationRow {
                background: #ffffff;
                border: 1px solid #d6dbe3;
                border-radius: 4px;
            }
            QWidget#monitorPreviewRoot QFrame#stockOperationRow:hover {
                border-color: #8fa5bf;
                background: #fbfcfe;
            }
            QWidget#monitorPreviewRoot QLabel#stockName {
                color: #101a28;
                font-size: 10pt;
                font-weight: 700;
            }
            QWidget#monitorPreviewRoot QLabel#statusDot[tone="normal"] {
                color: #16824a;
            }
            QWidget#monitorPreviewRoot QLabel#statusDot[tone="paused"],
            QWidget#monitorPreviewRoot QLabel#statusDot[tone="waiting"] {
                color: #8b98a8;
            }
            QWidget#monitorPreviewRoot QLabel#statusDot[tone="review"] {
                color: #d04a3a;
            }
            QWidget#monitorPreviewRoot QLabel#rowValue {
                color: #354253;
                font-weight: 600;
            }
            QWidget#monitorPreviewRoot QLabel#profitPositive {
                color: #d43c32;
                font-weight: 700;
            }
            QWidget#monitorPreviewRoot QLabel#profitNegative {
                color: #2463b5;
                font-weight: 700;
            }
            QWidget#monitorPreviewRoot QPushButton {
                min-height: 28px;
                padding: 3px 9px;
                background: #edf1f5;
                border: 1px solid #c7ced8;
                border-radius: 4px;
                color: #263445;
                font-weight: 600;
            }
            QWidget#monitorPreviewRoot QPushButton:hover {
                background: #e2e8ef;
            }
            QWidget#monitorPreviewRoot QPushButton#rowControlButton {
                background: #f7f8fa;
                color: #344254;
            }
            QWidget#monitorPreviewRoot QPushButton#dangerButton {
                background: #c9362b;
                border-color: #aa2d24;
                color: #ffffff;
            }
            QWidget#monitorPreviewRoot QPushButton#warningButton {
                background: #d96b24;
                border-color: #be5b1e;
                color: #ffffff;
            }
            QWidget#monitorPreviewRoot QPushButton#successButton {
                background: #258451;
                border-color: #1e7044;
                color: #ffffff;
            }
            QWidget#monitorPreviewRoot QPushButton#secondaryButton {
                background: #f8f9fb;
                color: #536173;
            }
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    window = MainMonitoringPreview()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
