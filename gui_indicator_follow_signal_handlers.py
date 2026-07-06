from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtWidgets import QSizePolicy


class IndicatorFollowSignalHandlersMixin:

    def _connect_main_dialog_buttons(self):
        """메인 다이얼로그 하단 버튼 시그널 연결."""
        self.reload_button.clicked.connect(self.load_rules)
        self.validate_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.validation_tab))
        self.close_button.clicked.connect(self.close)

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
