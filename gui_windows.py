# -*- coding: utf-8 -*-

"""
gui_windows.py

MASTER_SPEC v1.1 Windows GUI Edition 기준
Windows GUI 창 클래스 정의 파일.

현재 단계:
- 메인 윈도우 안정 버전
- 자동매매 루틴 폴더 자동 탐색
- __pycache__ 제외
- budget.json 이 있는 폴더만 루틴으로 인정
- 키움 로그인, 주문, 실시간 수신 기능은 아직 연결하지 않음
- 수동등록/검색등록 검증 강화
- 신규 종목은 stock_library.json 검색 결과에서만 등록 허용
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QComboBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from gui_stock_register_window import StockRegisterWindow
from gui_review_required_window import GlobalReviewRequiredWindow
from gui_main_emergency_ops import (
    has_emergency_stopped_stock as emergency_has_emergency_stopped_stock,
    update_emergency_button_state as emergency_update_emergency_button_state,
    emergency_review_reason_for_stock as emergency_review_reason_for_stock_impl,
    update_runtime_stock_status as emergency_update_runtime_stock_status,
    execute_emergency_stop as emergency_execute_emergency_stop,
    release_emergency_stop as emergency_release_emergency_stop,
    on_emergency_stop_clicked as emergency_on_emergency_stop_clicked,
)
from gui_main_table_loader import (
    main_sort_routine_table_by_column,
    main_sort_running_table_by_column,
    main_apply_routine_sort,
    main_apply_running_sort,
    main_load_routine_table,
    main_load_running_stock_table,
)
from gui_main_budget_panel import update_main_budget_panel
from runtime_io import read_json_dict
from gui_auto_trade_setting_window import (
    AutoTradeSettingWindow,
    get_routine_dirs,
    get_stock_dirs_in_routine,
    handle_kiwoom_raw_chejan_event,
    is_review_required_state,
    normalize_base_stock_single_routine_file,
    routine_display_name,
)
from gui_routine_registry import routine_record_by_name
from kiwoom_api import KiwoomApi
from operator_reconciliation_service import assess_startup_recovery


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"


def append_base_stock(code: str, name: str) -> None:
    """
    기초종목.txt 에 종목 1개를 추가한다.
    """
    existing_text = BASE_STOCK_PATH.read_text(encoding="utf-8") if BASE_STOCK_PATH.exists() else ""
    prefix = "" if not existing_text or existing_text.endswith("\n") else "\n"

    with BASE_STOCK_PATH.open("a", encoding="utf-8") as file:
        file.write(f"{prefix}{code},{name}\n")


def routine_dir_by_display_name() -> dict[str, Path]:
    """
    GUI 표시 루틴명 기준으로 루틴 폴더를 찾는다.
    """
    return {routine_display_name(path): path for path in get_routine_dirs()}


class MainWindow(QMainWindow):
    """
    키움 자동매매 시스템 메인 윈도우
    """

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("키움 OpenAPI 자동매매 시스템 - v1.1 Windows GUI")
        self.resize(1120, 720)
        try:
            self.kiwoom_api = KiwoomApi(parent=self)
        except Exception as exc:
            self.kiwoom_api = None
            self.kiwoom_api_unavailable_reason = str(exc)
        else:
            self.kiwoom_api_unavailable_reason = self.kiwoom_api.unavailable_reason()
            login_state_changed = getattr(self.kiwoom_api, "login_state_changed", None)
            if login_state_changed is not None:
                login_state_changed.connect(self.on_kiwoom_login_state_changed)
            raw_chejan_received = getattr(self.kiwoom_api, "raw_chejan_received", None)
            if raw_chejan_received is not None:
                raw_chejan_received.connect(self.on_kiwoom_raw_chejan_received)

        self.login_status_label = QLabel("로그인 상태: 미연결")
        self.btn_kiwoom_login = QPushButton("키움 로그인")
        self.account_label = QLabel("계좌번호: -")
        self.account_combo = QComboBox()
        self.account_combo.setEnabled(False)
        self.account_type_label = QLabel("계좌 구분: -")
        self.auto_status_label = QLabel("전체 자동매매 상태: 정지")
        self.buy_time_status_label = QLabel("매수 가능 상태: 확인 전")

        # 관제창 예산 현황 표시 전용 QLabel
        # 실제 예산 저장/주문수량 계산/매수 제한 로직은 아직 연결하지 않는다.
        self.budget_total_label = QLabel("0")
        self.budget_used_label = QLabel("0")
        self.budget_available_label = QLabel("0")
        self.budget_usage_rate_label = QLabel("-")
        self.budget_routine_count_label = QLabel("0")
        self.budget_stock_count_label = QLabel("0")
        self.budget_status_label = QLabel("확인 전")

        self.routine_table = QTableWidget()
        self.running_stock_table = QTableWidget()
        self._main_routine_sort_column = -1
        self._main_routine_sort_order = Qt.AscendingOrder
        self._main_running_sort_column = -1
        self._main_running_sort_order = Qt.AscendingOrder
        self._startup_recovery_result: dict[str, object] = {}
        self._startup_recovery_approved = False
        self._startup_recovery_approved_snapshot = ""

        self.btn_stock_register = QPushButton("종목등록설정")
        self.btn_auto_trade_setting = QPushButton("자동매매설정")
        self.btn_stop_all = QPushButton("전체 자동매매 정지")
        self.btn_restart = QPushButton("운영 재개")
        self.btn_initialize = QPushButton("초기화")
        self.btn_log_view = QPushButton("로그 보기")
        self.btn_review_required = QPushButton("검토관리종목")
        self.btn_exit = QPushButton("종료")
        self.btn_emergency_stop = QPushButton("긴급정지")

        self._setup_ui()
        self._connect_events()
        normalize_base_stock_single_routine_file()
        self.refresh_startup_recovery_status()
        self.refresh_all()

    def _setup_ui(self) -> None:
        central = QWidget()
        main_layout = QVBoxLayout()

        top_box = self._create_top_status_box()
        budget_box = self._create_budget_status_box()
        table_layout = self._create_table_area()
        button_layout = self._create_button_area()

        main_layout.addWidget(top_box)
        main_layout.addWidget(budget_box)
        main_layout.addLayout(table_layout)
        main_layout.addLayout(button_layout)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

        self.statusBar().showMessage("준비 완료")

    def _create_top_status_box(self) -> QGroupBox:
        box = QGroupBox("시스템 상태")
        layout = QGridLayout()

        layout.addWidget(self.login_status_label, 0, 0)
        layout.addWidget(self.btn_kiwoom_login, 0, 1)
        layout.addWidget(self.account_label, 0, 2)
        layout.addWidget(self.account_combo, 0, 3)
        layout.addWidget(self.account_type_label, 0, 4)

        layout.addWidget(self.auto_status_label, 1, 0)
        layout.addWidget(self.buy_time_status_label, 1, 1)
        layout.addWidget(self.btn_emergency_stop, 1, 2)

        self.btn_emergency_stop.setMinimumHeight(42)

        box.setLayout(layout)
        return box

    def _create_budget_status_box(self) -> QGroupBox:
        """관제창 예산 현황 UI.

        현재는 표시 전용이다.
        예산 저장, 주문수량 산출, 매수 제한, 루틴/종목 배분은 이후 단계에서 검토한다.
        """
        box = QGroupBox("예산 현황")
        layout = QGridLayout()

        layout.addWidget(QLabel("전체예산"), 0, 0)
        layout.addWidget(self.budget_total_label, 0, 1)
        layout.addWidget(QLabel("사용예산"), 0, 2)
        layout.addWidget(self.budget_used_label, 0, 3)
        layout.addWidget(QLabel("가용예산"), 0, 4)
        layout.addWidget(self.budget_available_label, 0, 5)

        layout.addWidget(QLabel("사용률"), 1, 0)
        layout.addWidget(self.budget_usage_rate_label, 1, 1)
        layout.addWidget(QLabel("루틴수"), 1, 2)
        layout.addWidget(self.budget_routine_count_label, 1, 3)
        layout.addWidget(QLabel("연결종목"), 1, 4)
        layout.addWidget(self.budget_stock_count_label, 1, 5)
        layout.addWidget(QLabel("예산상태"), 1, 6)
        layout.addWidget(self.budget_status_label, 1, 7)

        value_labels = [
            self.budget_total_label,
            self.budget_used_label,
            self.budget_available_label,
            self.budget_usage_rate_label,
            self.budget_routine_count_label,
            self.budget_stock_count_label,
            self.budget_status_label,
        ]
        for label in value_labels:
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumWidth(90)

        box.setLayout(layout)
        return box

    def _create_table_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        routine_box = QGroupBox("등록된 자동매매 루틴")
        routine_layout = QVBoxLayout()
        self._setup_routine_table()
        routine_layout.addWidget(self.routine_table)
        routine_box.setLayout(routine_layout)

        running_box = QGroupBox("실행 중 자동매매 종목")
        running_layout = QVBoxLayout()
        self._setup_running_stock_table()
        running_layout.addWidget(self.running_stock_table)
        running_box.setLayout(running_layout)

        layout.addWidget(routine_box, 2)
        layout.addWidget(running_box, 3)

        return layout

    def _create_button_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        buttons = [
            self.btn_stock_register,
            self.btn_auto_trade_setting,
            self.btn_stop_all,
            self.btn_restart,
            self.btn_initialize,
            self.btn_log_view,
            self.btn_review_required,
            self.btn_exit,
        ]

        for button in buttons:
            button.setMinimumHeight(36)
            layout.addWidget(button)

        return layout

    def _setup_routine_table(self) -> None:
        headers = [
            "루틴명",
            "등록",
            "실행",
            "정지",
            "오류",
            "총예산",
            "사용예산",
            "가용예산",
        ]

        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)

        self.routine_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.routine_table.horizontalHeader().setStretchLastSection(True)
        
        self.routine_table.setColumnWidth(0, 180)
        self.routine_table.setColumnWidth(1, 70)
        self.routine_table.setColumnWidth(2, 70)
        self.routine_table.setColumnWidth(3, 70)
        self.routine_table.setColumnWidth(4, 70)
        self.routine_table.setColumnWidth(5, 120)
        self.routine_table.setColumnWidth(6, 120)
        self.routine_table.setColumnWidth(7, 120)

        self.routine_table.horizontalHeader().setSectionsClickable(True)
        self.routine_table.horizontalHeader().setSortIndicatorShown(True)
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)

    def _setup_running_stock_table(self) -> None:
        headers = [
            "코드",
            "종목",
            "루틴",
            "운영",
            "현황",
            "상태",
            "보유",
            "평단",
            "미수",
            "미도",
        ]

        self.running_stock_table.setColumnCount(len(headers))
        self.running_stock_table.setHorizontalHeaderLabels(headers)
        self.running_stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.running_stock_table.horizontalHeader().setStretchLastSection(True)
        self.running_stock_table.setColumnWidth(0, 75)
        self.running_stock_table.setColumnWidth(1, 130)
        self.running_stock_table.setColumnWidth(2, 140)
        self.running_stock_table.setColumnWidth(3, 75)
        self.running_stock_table.setColumnWidth(4, 55)
        self.running_stock_table.setColumnWidth(5, 100)
        self.running_stock_table.setColumnWidth(6, 80)
        self.running_stock_table.setColumnWidth(7, 90)
        self.running_stock_table.setColumnWidth(8, 65)
        self.running_stock_table.setColumnWidth(9, 65)
        self.running_stock_table.horizontalHeader().setSectionsClickable(True)
        self.running_stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.running_stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.running_stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.running_stock_table.setSelectionMode(QAbstractItemView.SingleSelection)

    def _connect_events(self) -> None:
        self.btn_exit.clicked.connect(self.close)
        self.btn_kiwoom_login.clicked.connect(self.login_kiwoom_manually)
        self.btn_emergency_stop.clicked.connect(self.on_emergency_stop_clicked)
        self.btn_stop_all.clicked.connect(self.on_stop_all_clicked)
        self.btn_stock_register.clicked.connect(self.open_stock_register_window)
        self.btn_auto_trade_setting.clicked.connect(self.open_auto_trade_setting_window)
        self.btn_restart.clicked.connect(self.review_startup_recovery)
        self.btn_initialize.clicked.connect(self.not_implemented)
        self.btn_log_view.clicked.connect(self.not_implemented)
        self.btn_review_required.clicked.connect(self.open_review_required_window)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_main_routine_table_by_column)
        self.routine_table.itemDoubleClicked.connect(self.open_routine_settings_from_main_table)
        self.running_stock_table.horizontalHeader().sectionClicked.connect(self.sort_main_running_table_by_column)

    def startup_recovery_stock_state_paths(self) -> list[Path]:
        return [stock_dir / "state.json" for stock_dir in self.all_runtime_stock_dirs()]

    def refresh_startup_recovery_status(self) -> dict[str, object]:
        result = assess_startup_recovery(
            stock_state_paths=self.startup_recovery_stock_state_paths(),
        )
        self._startup_recovery_result = result
        status = str(result.get("status") or "INVALID_RUNTIME")
        if (
            self._startup_recovery_approved
            and self._startup_recovery_approved_snapshot != result.get("snapshot_hash")
        ):
            self._startup_recovery_approved = False
            self._startup_recovery_approved_snapshot = ""

        if self._startup_recovery_approved:
            self.auto_status_label.setText("전체 자동매매 상태: 운영 재개 승인")
            self.btn_restart.setText("운영 재개 확인 완료")
        else:
            labels = {
                "RESUME_READY": "재개 가능",
                "REVIEW_REQUIRED": "검토 필요",
                "BLOCKED_RECOVERY": "복구 차단",
                "INVALID_RUNTIME": "Runtime 손상",
            }
            self.auto_status_label.setText(
                f"전체 자동매매 상태: {labels.get(status, status)}"
            )
            self.btn_restart.setText("운영 재개")
        return result

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        if refresh:
            self.refresh_startup_recovery_status()
        return bool(
            self._startup_recovery_approved
            and self._startup_recovery_approved_snapshot
            and self._startup_recovery_approved_snapshot
            == self._startup_recovery_result.get("snapshot_hash")
        )

    def startup_recovery_block_reason(self) -> str:
        result = self._startup_recovery_result
        status = str(result.get("status") or "INVALID_RUNTIME")
        for key in ("invalid_reasons", "blocked_reasons", "review_reasons"):
            reasons = result.get(key)
            if isinstance(reasons, list) and reasons:
                return f"{status}: {reasons[0]}"
        return f"{status}: 운영 재개 확인이 필요합니다."

    def _startup_recovery_detail_text(self, result: dict[str, object]) -> str:
        counts = result.get("runtime_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines = [
            f"판정: {result.get('status', 'INVALID_RUNTIME')}",
            f"Queue 주문: {counts.get('orders', 0)}",
            f"Fill: {counts.get('fills', 0)}",
            f"Position: {counts.get('positions', 0)}",
            f"Broker Holdings: {counts.get('broker_holdings', 0)}",
            f"Runtime Lock: {counts.get('locks', 0)}",
            f"Reconciliation: "
            f"{result.get('operator_reconciliation', {}).get('summary', {}).get('total', 0)}",
        ]
        for title, key in (
            ("손상", "invalid_reasons"),
            ("차단", "blocked_reasons"),
            ("검토", "review_reasons"),
        ):
            reasons = result.get(key)
            if isinstance(reasons, list) and reasons:
                lines.append("")
                lines.append(f"{title}:")
                lines.extend(f"- {reason}" for reason in reasons[:12])
                if len(reasons) > 12:
                    lines.append(f"- 외 {len(reasons) - 12}개")
        return "\n".join(lines)

    def review_startup_recovery(self) -> None:
        result = self.refresh_startup_recovery_status()
        status = str(result.get("status") or "INVALID_RUNTIME")
        detail = self._startup_recovery_detail_text(result)

        if result.get("operator_approval_allowed") is not True:
            QMessageBox.warning(
                self,
                "운영 재개 차단",
                detail + "\n\nRuntime evidence를 먼저 검토·복구해야 합니다.",
            )
            if result.get("operator_reconciliation", {}).get("summary", {}).get("total", 0):
                self.open_review_required_window()
            return

        message = detail + "\n\n현재 evidence를 기준으로 자동매매 운영을 재개하시겠습니까?"
        if QMessageBox.question(
            self,
            "Startup Recovery",
            message,
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            self.statusBar().showMessage("운영 재개 승인이 취소되었습니다.")
            return

        self._startup_recovery_approved = True
        self._startup_recovery_approved_snapshot = str(result.get("snapshot_hash") or "")
        self.refresh_startup_recovery_status()
        window = getattr(self, "auto_trade_setting_window", None)
        refresh_controls = getattr(window, "update_startup_recovery_controls", None)
        if callable(refresh_controls):
            refresh_controls()
        self.statusBar().showMessage(f"운영 재개 승인 완료: {status}")

    def login_kiwoom_manually(self) -> None:
        api = getattr(self, "kiwoom_api", None)
        if api is None:
            reason = getattr(self, "kiwoom_api_unavailable_reason", "") or "KiwoomApi is not initialized"
            message = f"키움 로그인 사용불가: {reason}"
            self.login_status_label.setText(message)
            self.statusBar().showMessage(message)
            return

        try:
            if not api.is_available():
                reason = api.unavailable_reason() or getattr(self, "kiwoom_api_unavailable_reason", "") or "kiwoom api unavailable"
                message = f"키움 로그인 사용불가: {reason}"
                self.login_status_label.setText(message)
                self.statusBar().showMessage(message)
                return
            if api.is_connected():
                message = "로그인 상태: 연결됨"
                self.login_status_label.setText(message)
                self.statusBar().showMessage(message)
                return

            result = api.login()
        except Exception as exc:
            message = f"키움 로그인 요청 실패: {exc}"
            self.login_status_label.setText(message)
            self.statusBar().showMessage(message)
            return

        status = str(result.get("status", ""))
        if status == "login_requested":
            message = "로그인 요청됨"
        elif result.get("connected"):
            message = "로그인 상태: 연결됨"
        else:
            reason = result.get("error") or result.get("message") or status or "unknown error"
            message = f"키움 로그인 요청 실패: {reason}"

        self.login_status_label.setText(message)
        self.refresh_kiwoom_accounts()
        self.statusBar().showMessage(message)

    def on_kiwoom_login_state_changed(self, state) -> None:
        state = state if isinstance(state, dict) else {}
        connected = bool(state.get("connected", False))
        message = str(state.get("message", "") or "")
        if connected:
            label_text = "로그인 상태: 연결됨"
            status_message = message or label_text
        else:
            label_text = "로그인 상태: 실패"
            status_message = message or label_text

        self.login_status_label.setText(label_text)
        self.refresh_kiwoom_accounts()
        self.statusBar().showMessage(status_message)

    def kiwoom_account_numbers(self) -> list[str]:
        api = getattr(self, "kiwoom_api", None)
        getter = getattr(api, "account_numbers", None)
        if not callable(getter):
            return []
        try:
            raw_accounts = getter()
        except Exception:
            return []

        accounts: list[str] = []
        seen: set[str] = set()
        for value in raw_accounts if isinstance(raw_accounts, list) else []:
            account = str(value or "").strip()
            if not account or account in seen:
                continue
            accounts.append(account)
            seen.add(account)
        return accounts

    def refresh_kiwoom_accounts(self) -> list[str]:
        combo = getattr(self, "account_combo", None)
        if combo is None:
            return []

        current = self.selected_account_no()
        accounts = self.kiwoom_account_numbers()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(accounts)
            combo.setEnabled(bool(accounts))
            if len(accounts) == 1:
                combo.setCurrentIndex(0)
            elif current and current in accounts:
                combo.setCurrentIndex(accounts.index(current))
            else:
                combo.setCurrentIndex(-1)
        finally:
            combo.blockSignals(False)
        return accounts

    def selected_account_no(self) -> str:
        combo = getattr(self, "account_combo", None)
        if combo is None or not combo.isEnabled():
            return ""
        account = str(combo.currentText() or "").strip()
        return account if account in self.kiwoom_account_numbers() else ""

    def refresh_all(self) -> None:
        self.load_routine_table()
        self.load_running_stock_table()
        self.update_budget_panel()
        self.update_emergency_button_state()
        self.update_review_required_button_text()

    def update_budget_panel(self) -> None:
        update_main_budget_panel(self)

    def review_required_stock_count(self) -> int:
        """관제창에서 제외된 검토관리 대상 종목 수를 계산한다."""
        count = 0
        seen: set[str] = set()
        for stock_dir in self.all_runtime_stock_dirs():
            key = str(stock_dir.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                state = read_json_dict(stock_dir / "state.json")
            except Exception:
                state = {}
            if is_review_required_state(state):
                count += 1
        return count

    def update_review_required_button_text(self) -> None:
        if not hasattr(self, "btn_review_required"):
            return
        count = self.review_required_stock_count()
        self.btn_review_required.setText(f"검토관리종목({count})" if count else "검토관리종목")

    def sort_main_routine_table_by_column(self, column: int) -> None:
        main_sort_routine_table_by_column(self, column)

    def sort_main_running_table_by_column(self, column: int) -> None:
        main_sort_running_table_by_column(self, column)

    def _apply_main_routine_sort(self) -> None:
        main_apply_routine_sort(self)

    def _apply_main_running_sort(self) -> None:
        main_apply_running_sort(self)

    def load_routine_table(self) -> None:
        main_load_routine_table(self)

    def load_running_stock_table(self) -> None:
        main_load_running_stock_table(self)

    def all_runtime_stock_dirs(self) -> list[Path]:
        """전체 루틴의 종목 runtime 폴더를 중복 없이 조회한다."""
        stock_dirs: list[Path] = []
        seen: set[str] = set()
        for routine_dir in get_routine_dirs():
            for stock_dir in get_stock_dirs_in_routine(routine_dir):
                key = str(stock_dir.resolve())
                if key in seen:
                    continue
                seen.add(key)
                stock_dirs.append(stock_dir)
        return stock_dirs

    def routine_name_for_stock_dir(self, stock_dir: Path) -> str:
        """종목 runtime 폴더 기준 루틴 표시명을 반환한다."""
        try:
            return routine_display_name(stock_dir.parent)
        except Exception:
            return str(stock_dir.parent.name).lstrip("_") or "루틴확인필요"

    def has_emergency_stopped_stock(self) -> bool:
        return emergency_has_emergency_stopped_stock(self)

    def update_emergency_button_state(self) -> None:
        emergency_update_emergency_button_state(self)

    def emergency_review_reason_for_stock(self, stock_dir: Path) -> tuple[bool, str]:
        return emergency_review_reason_for_stock_impl(stock_dir)


    def update_runtime_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state: dict[str, object] | None = None,
        log_suffix: str = "",
    ) -> bool:
        return emergency_update_runtime_stock_status(
            self,
            stock_dir,
            code,
            name,
            new_status,
            extra_state,
            log_suffix,
        )

    def execute_emergency_stop(self) -> None:
        emergency_execute_emergency_stop(self)

    def release_emergency_stop(self) -> None:
        emergency_release_emergency_stop(self)

    def on_emergency_stop_clicked(self) -> None:
        emergency_on_emergency_stop_clicked(self)

    def on_stop_all_clicked(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("전체 자동매매 정지")
        box.setText(
            "전체 자동매매를 정지하시겠습니까?\n\n"
            "보유 종목은 자동 매도하지 않습니다."
        )
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()

        if box.clickedButton() == proceed_button:
            self.statusBar().showMessage("전체 자동매매 정지 요청됨")
            QMessageBox.information(
                self,
                "전체 자동매매 정지",
                "현재 단계에서는 실제 자동매매가 연결되어 있지 않습니다.\n"
                "GUI 버튼 동작만 확인했습니다.",
            )

    def open_routine_settings_from_main_table(self, item=None) -> None:
        """
        메인 관제창 좌측 '등록된 자동매매 루틴' 표에서 루틴 설정창을 연다.

        STEP37 범위:
        - 루틴 행 더블클릭 시 Registry 메타 기반 설정창 호출
        - 기존 루틴 지정창 연결 방식은 사용하지 않음
        - rules.json 저장 없음
        - HOLD/CANCEL/BUY 확장/실주문 연결 없음
        """
        row = item.row() if item is not None else self.routine_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "루틴 설정", "설정을 열 루틴을 선택하세요.")
            return

        routine_item = self.routine_table.item(row, 0)
        if routine_item is None:
            QMessageBox.warning(self, "루틴 설정", "선택한 행에서 루틴명을 확인하지 못했습니다.")
            return

        routine_name = routine_item.text().strip()
        if not routine_name:
            QMessageBox.warning(self, "루틴 설정", "루틴명이 비어 있습니다.")
            return

        routine_record = routine_record_by_name(routine_name)
        if routine_record is None:
            QMessageBox.warning(
                self,
                "\ub8e8\ud2f4 \uc124\uc815",
                f"\uc120\ud0dd\ud55c \ub8e8\ud2f4\uc744 Registry\uc5d0\uc11c \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.\\n\ub8e8\ud2f4\uba85: {routine_name}",
            )
            return

        settings_ui = str(routine_record.settings_ui or "").strip().lower()
        if settings_ui != "indicator_follow":
            QMessageBox.information(
                self,
                "\ub8e8\ud2f4 \uc124\uc815",
                f"\uc120\ud0dd\ud55c \ub8e8\ud2f4\uc758 \uc124\uc815\ucc3d\uc774 \uc544\uc9c1 \uc5f0\uacb0\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.\\n\ub8e8\ud2f4\uba85: {routine_record.name}",
            )
            return

        rules_path = routine_record.rules_path
        if not rules_path.exists():
            QMessageBox.warning(
                self,
                "rules.json \uc5c6\uc74c",
                f"\uc120\ud0dd\ud55c \ub8e8\ud2f4\uc758 rules.json\uc744 \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\\n{rules_path}",
            )
            return

        try:
            from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
        except Exception as exc:
            QMessageBox.critical(
                self,
                "\uc124\uc815\ucc3d \ub85c\ub4dc \uc2e4\ud328",
                "gui_indicator_follow_routine_settings_dialog.py \ud30c\uc77c\uc744 \ubd88\ub7ec\uc624\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.\\n"
                f"{exc}",
            )
            return

        dialog = IndicatorFollowRoutineSettingsDialog(
            rules_path=rules_path,
            routine_path=routine_record.path,
            routine_name=routine_record.name,
            parent=self,
        )
        dialog.exec_()

    def open_stock_register_window(self) -> None:
        self.stock_register_window = StockRegisterWindow(self)
        self.stock_register_window.show()

    def open_auto_trade_setting_window(self) -> None:
        self.auto_trade_setting_window = AutoTradeSettingWindow(self)
        self.auto_trade_setting_window.show()

    def on_kiwoom_raw_chejan_received(self, raw_event: dict[str, object]) -> None:
        self.last_chejan_record_result = handle_kiwoom_raw_chejan_event(
            raw_event,
            {
                "kiwoom_api_live_event": True,
                "live_event_source": "KiwoomApi.raw_chejan_received",
            },
        )
        window = getattr(self, "auto_trade_setting_window", None)
        if window is not None:
            setattr(window, "last_chejan_record_result", self.last_chejan_record_result)

    def open_review_required_window(self) -> None:
        self.review_required_window = GlobalReviewRequiredWindow(self)
        self.review_required_window.show()

    def not_implemented(self) -> None:
        QMessageBox.information(
            self,
            "안내",
            "이 기능은 다음 단계에서 구현합니다.",
        )
