# -*- coding: utf-8 -*-
"""
gui_review_required_window.py

검토관리창 및 검토관리 관련 공통 헬퍼.
- 검토관리 대상 수집
- 검토관리창 UI
- 복구/삭제/새로고침
- 검토관리 관련 변경 로그

주의:
- 자동매매설정창 본체와 ATS/환경설정 로직은 포함하지 않는다.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_common_utils import safe_int_value
from gui_order_utils import (
    format_number_value,
    pending_order_side_quantities,
)
from gui_review_utils import safe_float_value
from gui_styles import apply_plain_table_header
from gui_table_utils import next_sort_order
from runtime_io import read_json_dict
from stock_repository import repository as stock_repository_factory
from gui_auto_trade_runtime import write_state_json
from state_policy import auto_trade_status_display


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
ARCHIVED_STOCKS_DIR = PROJECT_ROOT / "archived_stocks"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"


def get_routine_dirs() -> list[Path]:
    """
    호환용 루틴 path 조회.

    신규 기준은 routines/*/routine.json이며, 기존 _루틴폴더/budget.json은
    gui_routine_registry의 fallback 정책에만 맡긴다.
    이 함수는 더 이상 루틴폴더 내부 종목폴더를 전제로 하지 않는다.
    """
    try:
        from gui_routine_registry import get_routine_dirs as registry_get_routine_dirs
        return registry_get_routine_dirs()
    except Exception:
        return []


def routine_display_name(routine_dir: Path) -> str:
    """호환용 루틴 표시명 반환. 신규 루틴 패키지 routine.json을 우선한다."""
    try:
        from gui_routine_registry import routine_display_name as registry_routine_display_name
        return registry_routine_display_name(Path(routine_dir))
    except Exception:
        return Path(routine_dir).name.lstrip("_").strip()

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def unique_review_reasons(reasons) -> list[str]:
    """검토 사유 목록에서 빈값/중복을 제거하고 입력 순서를 유지한다."""
    result: list[str] = []
    seen: set[str] = set()

    for reason in reasons:
        text = str(reason).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)

    return result


def is_review_required_state(state: dict[str, object] | None) -> bool:
    """검토관리 전용 분리 판정.

    자동매매설정 창에서는 이 조건에 걸린 종목을 절대 표시하지 않는다.
    검토관리 창에서는 이 조건에 걸린 종목만 표시한다.
    """
    if not isinstance(state, dict):
        return False

    raw_status = str(state.get("status", "")).strip().upper()
    if raw_status in {"REVIEW_REQUIRED", "REVIEW"}:
        return True

    if bool(state.get("review_required", False)):
        return True

    try:
        return auto_trade_status_display(raw_status) == "검토종목"
    except Exception:
        return False


def is_review_required_stock_dir(stock_dir: Path) -> bool:
    """runtime 폴더 기준 검토관리 전용 종목 여부."""
    try:
        state = read_json_dict(stock_dir / "state.json")
    except Exception:
        return False
    return is_review_required_state(state)


def append_changelog(change_type: str, filename: str, message: str) -> None:
    """
    GUI 조작으로 발생한 변경사항을 PROJECT_CHANGELOG.txt 에 기록한다.
    """
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )

    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
    """
    종목별 logs/YYYYMMDD.log 에 GUI 조작 및 상태 변경 내역을 기록한다.

    주의:
    - 실제 키움 주문/체결 로그가 아니라 관리자 GUI 조작 로그이다.
    - logs 폴더가 없으면 생성한다.
    - 기록 실패는 GUI 흐름을 막지 않도록 조용히 무시한다.
    """
    try:
        logs_dir = stock_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d')}.log"
        line = f"[{now_text()}] [{event_type}] {message}"
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return log_path
    except Exception:
        return None


def update_base_stock_routines(code: str, name: str, routines: list[str]) -> bool:
    """
    중앙 stocks/config.json 기준으로 종목의 루틴 연결을 갱신한다.

    과거 기초종목.txt 갱신 방식은 루틴 패키지 전환 이후 사용하지 않는다.
    """
    try:
        repo = stock_repository_factory()
        return repo.update_stock_routine(code, name, routines)
    except Exception:
        return False

def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """
    종목 폴더명에서 종목코드와 종목명을 분리한다.
    예: 005930_삼성전자 -> ("005930", "삼성전자")
    """
    parts = folder_name.split("_", 1)
    if len(parts) != 2:
        return "", folder_name.strip()
    return parts[0].strip(), parts[1].strip()


def get_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """
    호환용 종목 조회.

    과거에는 루틴폴더 아래 종목폴더를 조회했지만, 현재 기준 종목 원본은
    중앙 stocks/이며 루틴 연결은 각 종목 config.json의 routine 값으로 판단한다.
    """
    routine_name = routine_display_name(routine_dir)
    try:
        repo = stock_repository_factory()
        result: list[Path] = []
        for record in repo.list_stocks():
            if str(record.routine or "").strip() != routine_name:
                continue
            stock_dir = repo.resolve_stock_dir(record.code, record.name)
            if stock_dir.exists() and stock_dir.is_dir():
                result.append(stock_dir)
        return sorted(result, key=lambda path: path.name)
    except Exception:
        return []

def auto_trade_setting_data_inconsistency_reasons(state: dict[str, object] | None) -> list[str]:
    """운영 중/재시작/안정성검사 공통 내부 데이터 불일치 판정.

    주의:
    - holding_qty/current_qty/qty 계열은 수량으로 본다.
    - holding_amount 계열은 수량이 아니라 보유금액/평가금액 계열로 본다.
    - 보유수량 0인데 평단 또는 보유금액이 남아 있으면 비정상으로 본다.
    """
    if not isinstance(state, dict):
        return ["state.json 형식 이상"]

    reasons: list[str] = []

    def present(key: str) -> bool:
        return key in state and state.get(key) not in (None, "")

    def number_value(key: str, default: float = 0.0) -> tuple[float, bool]:
        if not present(key):
            return default, False
        value = state.get(key)
        try:
            if isinstance(value, str):
                value = value.replace(",", "").strip()
            return float(value), True
        except Exception:
            reasons.append(f"{key} 숫자 형식 오류")
            return default, True

    qty_keys = [
        "holding_qty",
        "current_qty",
        "current_quantity",
        "qty",
        "balance_qty",
        "position_qty",
    ]
    amount_keys = [
        "holding_amount",
        "holding_value",
        "holding_eval_amount",
        "position_amount",
        "stock_value",
    ]
    avg_keys = [
        "avg_price",
        "average_price",
        "avg_buy_price",
        "buy_avg_price",
        "average_buy_price",
    ]

    qty_values: dict[str, float] = {}
    amount_values: dict[str, float] = {}
    avg_values: dict[str, float] = {}

    for key in qty_keys:
        value, exists = number_value(key)
        if exists:
            qty_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    for key in amount_keys:
        value, exists = number_value(key)
        if exists:
            amount_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    for key in avg_keys:
        value, exists = number_value(key)
        if exists:
            avg_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    primary_qty = qty_values.get("holding_qty", 0.0)
    if primary_qty == 0:
        positive_qtys = [value for value in qty_values.values() if value > 0]
        if positive_qtys:
            primary_qty = max(positive_qtys)

    primary_avg = avg_values.get("avg_price", 0.0)
    if primary_avg == 0:
        positive_avgs = [value for value in avg_values.values() if value > 0]
        if positive_avgs:
            primary_avg = max(positive_avgs)

    primary_amount = amount_values.get("holding_amount", 0.0)
    if primary_amount == 0:
        positive_amounts = [value for value in amount_values.values() if value > 0]
        if positive_amounts:
            primary_amount = max(positive_amounts)

    positive_qty_pairs = {key: value for key, value in qty_values.items() if value > 0}
    if len(set(positive_qty_pairs.values())) > 1:
        reasons.append("보유수량 필드 불일치")

    if primary_qty <= 0 and primary_avg > 0:
        reasons.append("보유 0인데 평단 존재")
    if primary_qty <= 0 and primary_amount > 0:
        reasons.append("보유 0인데 보유금액 존재")
    if primary_qty > 0 and primary_avg <= 0:
        reasons.append("보유 존재인데 평단 없음")

    return unique_review_reasons(reasons)


def auto_trade_setting_server_mismatch_detected(state: dict[str, object] | None) -> bool:
    """키움 서버 정보와 프로그램 내부 정보 불일치/서버 불안 표시 여부.

    실제 키움 연동 단계에서 아래 플래그 중 하나가 저장되면 현황을 빨강으로 표시한다.
    빨강은 자동 검토관리 이동이 아니라 즉시 운영정지/안정성검사 대상이라는 뜻이다.
    """
    if not isinstance(state, dict):
        return False

    if auto_trade_setting_data_inconsistency_reasons(state):
        return True

    bool_keys = {
        "server_mismatch",
        "kiwoom_mismatch",
        "server_data_mismatch",
        "kiwoom_data_mismatch",
        "data_mismatch",
        "server_unstable",
        "kiwoom_server_unstable",
    }
    for key in bool_keys:
        value = state.get(key)
        if isinstance(value, bool) and value:
            return True
        if str(value or "").strip().lower() in {"true", "1", "yes", "y", "on"}:
            return True

    status_keys = {
        "kiwoom_sync_status",
        "server_sync_status",
        "reconciliation_status",
        "server_status",
    }
    danger_values = {"MISMATCH", "UNSTABLE", "ERROR", "FAILED", "FAIL", "UNKNOWN"}
    for key in status_keys:
        if str(state.get(key, "")).strip().upper() in danger_values:
            return True

    return False


def collect_global_review_required_rows() -> list[dict[str, object]]:
    """
    프로그램 전체 단위 검토관리 대상 목록을 중앙 stocks/ 기준으로 수집한다.

    정책:
    - 검토관리의 진실 원본은 stocks/<종목>/state.json 이다.
    - 루틴 패키지 폴더나 구형 _루틴폴더 내부 종목폴더는 조회하지 않는다.
    - 루틴명은 stocks/<종목>/config.json의 연결값을 우선하고, 없으면 state의 review_routine을 보조로 표시한다.
    """
    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    try:
        repo = stock_repository_factory()
        records = repo.list_stocks()
    except Exception:
        records = []

    for record in records:
        code = str(record.code or "").strip()
        name = str(record.name or "").strip()
        stock_dir = repo.resolve_stock_dir(code, name)
        state = read_json_dict(stock_dir / "state.json")
        if not is_review_required_state(state):
            continue

        routine_name = str(record.routine or state.get("review_routine", "") or "-").strip() or "-"
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = safe_float_value(state.get("avg_price"), 0.0)
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

        if buy_pending_qty == "?" or sell_pending_qty == "?":
            return_availability = "미해결"
        elif holding_qty > 0 or safe_int_value(buy_pending_qty, 0) > 0 or safe_int_value(sell_pending_qty, 0) > 0:
            return_availability = "미해결"
        elif avg_price > 0 and holding_qty <= 0:
            return_availability = "미해결"
        elif auto_trade_setting_server_mismatch_detected(state):
            return_availability = "미해결"
        else:
            return_availability = "해결"

        key = (routine_name, code, name)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        rows.append({
            "routine_name": routine_name,
            "stock_dir": stock_dir,
            "code": code,
            "name": name,
            "review_location": str(state.get("review_location", "") or "-").strip() or "-",
            "review_reason": str(state.get("review_reason", "") or state.get("review_detail", "") or "-").strip() or "-",
            "review_entered_at": str(state.get("review_entered_at", "") or state.get("review_checked_at", "") or "-").strip() or "-",
            "last_checked_at": str(state.get("review_checked_at", "") or state.get("updated_at", "") or "-").strip() or "-",
            "holding_qty": holding_qty,
            "avg_price": avg_price,
            "buy_pending_qty": buy_pending_qty,
            "sell_pending_qty": sell_pending_qty,
            "return_availability": return_availability,
        })

    rows.sort(key=lambda row: (str(row.get("review_entered_at", "")), str(row.get("routine_name", "")), str(row.get("code", ""))))
    return rows

class GlobalReviewRequiredWindow(QDialog):
    """프로그램 전체 단위 검토종목 통합 관리창."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("검토종목 관리")
        self.resize(1100, 620)

        self.summary_label = QLabel("검토종목: 0개")
        self.table = QTableWidget()
        self.btn_return = QPushButton("복귀")
        self.btn_unassign = QPushButton("미지정")
        self.btn_delete = QPushButton("삭제")
        self.btn_refresh = QPushButton("새로고침")
        self.btn_close = QPushButton("닫기")
        self._review_sort_column = -1
        self._review_sort_order = Qt.AscendingOrder

        self._setup_ui()
        self._connect_events()
        self.load_review_items()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.addWidget(self.summary_label)

        headers = [
            "코드",
            "종목",
            "위치",
            "상태",
            "사유",
            "검출",
            "보유",
            "미수",
            "미도",
            "발생시간",
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        self.table.setColumnWidth(0, 75)    # 코드
        self.table.setColumnWidth(1, 180)   # 종목
        self.table.setColumnWidth(2, 140)   # 위치
        self.table.setColumnWidth(3, 75)    # 상태
        self.table.setColumnWidth(4, 350)   # 사유
        self.table.setColumnWidth(5, 130)   # 검출
        self.table.setColumnWidth(6, 100)    # 보유
        self.table.setColumnWidth(7, 75)    # 미수
        self.table.setColumnWidth(8, 75)    # 미도
        self.table.setColumnWidth(9, 180)   # 발생시간
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_return.setMinimumWidth(90)
        self.btn_unassign.setMinimumWidth(90)
        self.btn_delete.setMinimumWidth(90)
        self.btn_refresh.setMinimumWidth(100)
        self.btn_close.setMinimumWidth(100)
        buttons.addWidget(self.btn_return)
        buttons.addWidget(self.btn_unassign)
        buttons.addWidget(self.btn_delete)
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _connect_events(self) -> None:
        self.btn_return.clicked.connect(self.return_selected_items_to_auto_list)
        self.btn_unassign.clicked.connect(self.unassign_selected_review_items)
        self.btn_delete.clicked.connect(self.delete_selected_review_items)
        self.btn_refresh.clicked.connect(self.load_review_items)
        self.btn_close.clicked.connect(self.close)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_review_table_by_column)
        self.table.customContextMenuRequested.connect(self.show_review_table_context_menu)

    def sort_review_table_by_column(self, column: int) -> None:
        """검토관리 표 헤더 클릭 정렬."""
        self._review_sort_order = next_sort_order(
            self._review_sort_column,
            column,
            self._review_sort_order,
        )
        self._review_sort_column = column
        self.table.sortItems(column, self._review_sort_order)
        self.table.horizontalHeader().setSortIndicator(column, self._review_sort_order)

    def _apply_saved_review_sort(self) -> None:
        if 0 <= self._review_sort_column < self.table.columnCount():
            self.table.sortItems(self._review_sort_column, self._review_sort_order)
            self.table.horizontalHeader().setSortIndicator(
                self._review_sort_column,
                self._review_sort_order,
            )

    def show_review_table_context_menu(self, position) -> None:
        """검토관리 표 우클릭 메뉴."""
        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_all = menu.addAction("전체 해제")
        selected_action = menu.exec_(self.table.viewport().mapToGlobal(position))

        if selected_action == action_select_all:
            self.table.selectAll()
        elif selected_action == action_clear_all:
            self.table.clearSelection()

    def _set_item(
        self,
        row: int,
        col: int,
        text: object,
        align=Qt.AlignCenter,
        tooltip: str = "",
    ) -> None:
        item = QTableWidgetItem(str(text if text is not None else "-"))
        item.setTextAlignment(align)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, col, item)

    def _review_row_tooltip(self, row: dict[str, object]) -> str:
        """검토관리 종목 행에 표시할 상세 툴팁."""
        code = str(row.get("code", "-") or "-").strip() or "-"
        name = str(row.get("name", "-") or "-").strip() or "-"
        routine = str(row.get("routine_name", "-") or "-").strip() or "-"
        location = str(row.get("review_location", "-") or "-").strip() or "-"
        holding_qty = str(row.get("holding_qty", "-") or "-").strip() or "-"
        avg_price = format_number_value(row.get("avg_price", 0))
        buy_pending_qty = str(row.get("buy_pending_qty", "-") or "-").strip() or "-"
        sell_pending_qty = str(row.get("sell_pending_qty", "-") or "-").strip() or "-"
        return_availability = str(row.get("return_availability", "-") or "-").strip() or "-"
        reason = str(row.get("review_reason", "-") or "-").strip() or "-"
        entered_at = str(row.get("review_entered_at", "-") or "-").strip() or "-"
        return (
            f"코드: {code}\n"
            f"종목명: {name}\n"
            f"현재위치: {routine}\n"
            f"검토위치: {location}\n"
            f"보유: {holding_qty}\n"
            f"평단: {avg_price}\n"
            f"미수: {buy_pending_qty}\n"
            f"미도: {sell_pending_qty}\n"
            f"상태: {return_availability}\n"
            f"사유: {reason}\n"
            f"발생시간: {entered_at}"
        )


    def _central_review_rows(self) -> list[dict[str, object]]:
        """
        검토관리창 표시 전용 중앙 stocks 수집기.

        버튼 카운트와 창 내부 목록이 달라지는 문제를 막기 위해
        load_review_items()에서 외부/구형 수집 함수를 거치지 않고
        중앙 stocks/state.json을 직접 스캔한다.
        """
        rows: list[dict[str, object]] = []

        try:
            repo = stock_repository_factory()
            stocks_dir = repo.stocks_dir
        except Exception:
            stocks_dir = PROJECT_ROOT / "stocks"

        if not stocks_dir.exists():
            return rows

        seen: set[tuple[str, str]] = set()

        for stock_dir in sorted(stocks_dir.iterdir(), key=lambda p: p.name):
            if not stock_dir.is_dir():
                continue

            folder_name = stock_dir.name
            parts = folder_name.split("_", 1)
            if len(parts) == 2:
                code, name = parts[0].strip(), parts[1].strip()
            else:
                code, name = folder_name.strip(), folder_name.strip()

            if not code or not code[:6].isdigit():
                continue

            state = read_json_dict(stock_dir / "state.json")
            if not isinstance(state, dict):
                continue

            if not is_review_required_state(state):
                continue

            config = read_json_dict(stock_dir / "config.json")
            if not isinstance(config, dict):
                config = {}

            routine_name = (
                str(config.get("routine", "") or "").strip()
                or str(config.get("routine_name", "") or "").strip()
                or str(config.get("active_routine", "") or "").strip()
                or str(state.get("review_routine", "") or "").strip()
                or "-"
            )

            holding_qty = safe_int_value(state.get("holding_qty"), 0)
            avg_price = safe_float_value(state.get("avg_price"), 0.0)
            buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

            if buy_pending_qty == "?" or sell_pending_qty == "?":
                return_availability = "미해결"
            elif holding_qty > 0 or safe_int_value(buy_pending_qty, 0) > 0 or safe_int_value(sell_pending_qty, 0) > 0:
                return_availability = "미해결"
            elif avg_price > 0 and holding_qty <= 0:
                return_availability = "미해결"
            else:
                try:
                    return_availability = "미해결" if auto_trade_setting_server_mismatch_detected(state) else "해결"
                except Exception:
                    return_availability = "해결"

            key = (code, name)
            if key in seen:
                continue
            seen.add(key)

            rows.append(
                {
                    "routine_name": routine_name,
                    "stock_dir": stock_dir,
                    "code": code,
                    "name": name,
                    "review_location": str(state.get("review_location", "") or "-").strip() or "-",
                    "review_reason": str(state.get("review_reason", "") or state.get("review_detail", "") or "-").strip() or "-",
                    "review_entered_at": str(state.get("review_entered_at", "") or state.get("review_checked_at", "") or "-").strip() or "-",
                    "last_checked_at": str(state.get("review_checked_at", "") or state.get("updated_at", "") or "-").strip() or "-",
                    "holding_qty": holding_qty,
                    "avg_price": avg_price,
                    "buy_pending_qty": buy_pending_qty,
                    "sell_pending_qty": sell_pending_qty,
                    "return_availability": return_availability,
                }
            )

        rows.sort(key=lambda row: (str(row.get("review_entered_at", "")), str(row.get("routine_name", "")), str(row.get("code", ""))))
        return rows

    def load_review_items(self) -> None:
        rows = self._central_review_rows()
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            tooltip = self._review_row_tooltip(row)
            self._set_item(row_index, 0, row.get("code", "-"), tooltip=tooltip)
            self._set_item(row_index, 1, row.get("name", "-"), Qt.AlignLeft | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 2, row.get("routine_name", "-"), tooltip=tooltip)
            self._set_item(row_index, 3, row.get("return_availability", "-"), tooltip=tooltip)
            self._set_item(row_index, 4, row.get("review_reason", "-"), Qt.AlignLeft | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 5, row.get("review_location", "-"), Qt.AlignLeft | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 6, row.get("holding_qty", "-"), Qt.AlignRight | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 7, row.get("buy_pending_qty", "-"), Qt.AlignRight | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 8, row.get("sell_pending_qty", "-"), Qt.AlignRight | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 9, row.get("review_entered_at", "-"), tooltip=tooltip)

            first_item = self.table.item(row_index, 0)
            if first_item is not None:
                first_item.setData(Qt.UserRole, str(row.get("stock_dir", "")))
                first_item.setData(Qt.UserRole + 1, str(row.get("code", "")))
                first_item.setData(Qt.UserRole + 2, str(row.get("name", "")))

        self._apply_saved_review_sort()
        self.summary_label.setText(f"검토종목: {len(rows)}개")

    def selected_stock_dirs(self) -> list[tuple[Path, str, str]]:
        """검토관리창에서 선택된 종목의 runtime 폴더를 반환한다."""
        result: list[tuple[Path, str, str]] = []
        seen: set[str] = set()

        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is None:
                continue

            stock_dir_text = str(item.data(Qt.UserRole) or "").strip()
            code = str(item.data(Qt.UserRole + 1) or item.text() or "").strip()
            name = str(item.data(Qt.UserRole + 2) or "").strip()
            if not stock_dir_text:
                continue

            stock_dir = Path(stock_dir_text)
            key = str(stock_dir.resolve()) if stock_dir.exists() else stock_dir_text
            if key in seen:
                continue
            seen.add(key)
            result.append((stock_dir, code, name))

        return result

    def _review_exit_block_reason(self, stock_dir: Path, state: dict[str, object]) -> str:
        """복귀/미지정 전 필요한 최소 무결성 조건을 확인한다."""
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = safe_float_value(state.get("avg_price"), 0.0)
        if holding_qty > 0:
            return "보유수량 존재"
        if avg_price > 0 and holding_qty <= 0:
            return "보유 0인데 평단 존재"

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        if buy_pending_qty == "?" or sell_pending_qty == "?":
            return "미체결 수량 확인 필요"
        if safe_int_value(buy_pending_qty, 0) > 0:
            return "미수/매수 미체결 존재"
        if safe_int_value(sell_pending_qty, 0) > 0:
            return "미도/매도 미체결 존재"

        if auto_trade_setting_server_mismatch_detected(state):
            return "서버/프로그램 정보 불일치"

        return ""

    def _clear_review_state(self, state: dict[str, object]) -> None:
        """검토관리 해제 공통 메타 정리."""
        state["review_required"] = False
        state["review_status"] = ""
        state["review_location"] = ""
        state["review_reason"] = ""
        state["review_detail"] = ""
        state["review_entered_at"] = ""
        state["review_checked_at"] = ""
        state["review_routine"] = ""
        state["updated_at"] = now_text()

    def _refresh_after_review_action(self) -> None:
        self.load_review_items()
        parent = self.parent()
        if hasattr(parent, "refresh_all"):
            try:
                parent.refresh_all()
            except Exception:
                pass

    def return_selected_items_to_auto_list(self) -> None:
        """검토관리 종목을 원래 루틴에 남긴 채 감시/대기 상태로 복귀한다."""
        targets = self.selected_stock_dirs()
        if not targets:
            QMessageBox.information(self, "복귀", "복귀할 검토종목을 선택하세요.")
            return

        changed = 0
        blocked: list[str] = []
        failed = 0

        for stock_dir, code, name in targets:
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            if not isinstance(state, dict):
                failed += 1
                continue

            block_reason = self._review_exit_block_reason(stock_dir, state)
            if block_reason:
                blocked.append(f"{code} {name}: {block_reason}")
                continue

            before_status = str(state.get("status", "") or "REVIEW_REQUIRED")
            self._clear_review_state(state)
            state["status"] = "MONITORING"
            state["trade_enabled"] = False
            state["buy_enabled"] = False
            state["startup_reset_reason"] = ""

            if write_state_json(stock_dir, state):
                append_stock_log(stock_dir, "GUI", f"검토관리 복귀: {before_status} -> MONITORING")
                changed += 1
            else:
                failed += 1

        self._refresh_after_review_action()
        message = f"복귀 완료: {changed}개"
        if blocked:
            preview = "\n".join(f"- {item}" for item in blocked[:8])
            if len(blocked) > 8:
                preview += f"\n- 외 {len(blocked) - 8}개"
            message += f"\n\n복귀 불가:\n{preview}"
        if failed:
            message += f"\n\n실패: {failed}개"
        QMessageBox.information(self, "복귀 완료", message)

    def unassign_selected_review_items(self) -> None:
        """무결성 문제가 해소된 검토관리 종목을 미지정으로 전환한다."""
        targets = self.selected_stock_dirs()
        if not targets:
            QMessageBox.information(self, "미지정", "미지정으로 전환할 검토종목을 선택하세요.")
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("미지정 확인")
        box.setText(
            "선택한 검토종목을 미지정으로 전환하시겠습니까?\n\n"
            "미지정은 무결성 문제가 해소된 종목만 가능합니다.\n"
            "종목은 유지하고 루틴 연결만 해제합니다."
        )
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()
        if box.clickedButton() != proceed_button:
            return

        changed = 0
        blocked: list[str] = []
        failed = 0

        for stock_dir, code, name in targets:
            state_path = stock_dir / "state.json"
            state = read_json_dict(state_path)
            if not isinstance(state, dict):
                failed += 1
                continue

            block_reason = self._review_exit_block_reason(stock_dir, state)
            if block_reason:
                blocked.append(f"{code} {name}: {block_reason}")
                continue

            before_status = str(state.get("status", "") or "REVIEW_REQUIRED")
            self._clear_review_state(state)
            state["status"] = "STOPPED"
            state["trade_enabled"] = False
            state["buy_enabled"] = False
            state["active_routine"] = ""
            state["routine_name"] = ""

            try:
                update_base_stock_routines(code, name, [])
                if not write_state_json(stock_dir, state):
                    failed += 1
                    continue
                append_stock_log(stock_dir, "GUI", f"검토관리 미지정 전환: {before_status} -> STOPPED")
                changed += 1
            except Exception:
                failed += 1

        if changed:
            append_changelog("UPDATE", "기초종목.txt/state.json", f"검토관리 미지정 전환: {changed}개")
        self._refresh_after_review_action()

        message = f"미지정 전환 완료: {changed}개"
        if blocked:
            preview = "\n".join(f"- {item}" for item in blocked[:8])
            if len(blocked) > 8:
                preview += f"\n- 외 {len(blocked) - 8}개"
            message += f"\n\n미지정 불가:\n{preview}"
        if failed:
            message += f"\n\n실패: {failed}개"
        QMessageBox.information(self, "미지정 완료", message)

    def delete_selected_review_items(self) -> None:
        """검토관리 종목을 시스템에서 삭제한다."""
        targets = self.selected_stock_dirs()
        if not targets:
            QMessageBox.information(self, "삭제", "삭제할 검토종목을 선택하세요.")
            return

        preview = "\n".join(f"- {code} {name}" for _, code, name in targets[:8])
        if len(targets) > 8:
            preview += f"\n- 외 {len(targets) - 8}개"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("검토종목 삭제 확인")
        box.setText(
            f"삭제 대상: {len(targets)}건\n\n"
            f"{preview}\n\n"
            "삭제 후 복구할 수 없습니다."
        )
        proceed_button = box.addButton("삭제", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()
        if box.clickedButton() != proceed_button:
            return

        deleted = 0
        failed = 0
        target_keys = {(code, name) for _, code, name in targets}

        try:
            if BASE_STOCK_PATH.exists():
                new_lines: list[str] = []
                for raw_line in BASE_STOCK_PATH.read_text(encoding="utf-8").splitlines():
                    parts = [part.strip() for part in raw_line.strip().split(",")]
                    if len(parts) >= 2 and (parts[0], parts[1]) in target_keys:
                        continue
                    if raw_line.strip():
                        new_lines.append(raw_line.strip())
                BASE_STOCK_PATH.write_text(
                    "\n".join(new_lines) + ("\n" if new_lines else ""),
                    encoding="utf-8",
                )
        except Exception:
            failed += len(targets)

        archive_root = ARCHIVED_STOCKS_DIR
        archive_root.mkdir(exist_ok=True)
        timestamp = now_text().replace("-", "").replace(":", "").replace(" ", "_")

        try:
            repo = stock_repository_factory()
        except Exception:
            repo = None

        for stock_dir, code, name in targets:
            try:
                target_dir = stock_dir
                if repo is not None:
                    target_dir = repo.resolve_stock_dir(code, name)
                if not target_dir.exists() or not target_dir.is_dir():
                    failed += 1
                    continue

                try:
                    if repo is not None:
                        repo.update_stock_routine(code, name, [])
                except Exception:
                    pass

                archive_dir = archive_root / f"{target_dir.name}_{timestamp}"
                suffix = 1
                while archive_dir.exists():
                    suffix += 1
                    archive_dir = archive_root / f"{target_dir.name}_{timestamp}_{suffix}"
                target_dir.rename(archive_dir)
                deleted += 1
            except Exception:
                failed += 1

        if deleted:
            append_changelog("DELETE", "stocks/archive", f"검토관리 종목 archive 이동: {deleted}개")
        self._refresh_after_review_action()

        message = f"삭제 완료: {deleted}개"
        if failed:
            message += f" / 실패 {failed}개"
        QMessageBox.information(self, "삭제 완료", message)

