# -*- coding: utf-8 -*-

"""
gui_windows.py

MASTER_SPEC v1.1 Windows GUI Edition 기준
Windows GUI 창 클래스 정의 파일.

현재 단계:
- 메인 윈도우 안정 버전
- 자동매매 루틴 폴더 자동 탐색
- __pycache__ 제외
- routines/<루틴명>/routine.json 루틴 패키지를 우선 인식
- 키움 로그인, 주문, 실시간 수신 기능은 아직 연결하지 않음
- 수동등록/검색등록 검증 강화
- 신규 종목은 stock_library.json 검색 결과에서만 등록 허용
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QDate, QTime, QTimer, QItemSelectionModel, QRect
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QGridLayout,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from integrity_checker import (
    run_integrity_checks,
    write_invalid_items_log,
)
from gui_table_utils import next_sort_order
from gui_styles import (
    apply_plain_table_header,
    apply_selected_routine_label_style,
)
from gui_common_utils import safe_int_value, sanitize_path_part
from gui_stock_data import (
    active_routine_for_stock,
    stock_runtime_dir_for_routine,
)
from gui_order_utils import (
    pending_order_side_quantities,
    order_value,
    order_status_display,
    order_side_display,
    format_number_value,
    build_order_rows,
    build_order_timeline_text,
    filter_orders_by_range,
    build_grouped_order_timeline_text,
    settlement_summary_text,
    date_range_for_mode,
    filter_orders_by_dates,
    today_orders,
    build_current_status_rows,
    build_full_trade_export_text,
    order_sort_key,
)
from gui_order_status_window import OrderStatusWindow
from gui_log_view_window import LogViewWindow
from gui_integrity_check_window import IntegrityCheckWindow
from gui_blocked_report_window import (
    BlockedActionReportViewDialog,
    blocked_items_preview,
    latest_blocked_action_report_path,
    write_blocked_action_report,
)
from gui_schedule_utils import (
    schedule_config_updates,
    schedule_change_log_text,
    schedule_status_suffix,
)
from gui_config_utils import (
    default_config,
    default_state,
    default_orders,
)
from gui_config_window import show_deferred_config_message
from gui_force_unregister_dialog import ForceUnregisterConfirmDialog
from gui_search_stock_register_dialog import SearchStockRegisterDialog
from gui_auto_trade_utils import auto_trade_unregister_category
from gui_review_utils import (
    build_review_required_item,
    compact_time_text,
    pending_order_summary,
    review_required_for_start,
    review_reason_summary,
    safe_float_value,
)
from gui_routine_assign_utils import (
    build_routine_assign_result_lines,
    build_routine_assign_status_text,
    build_routine_unassign_result_lines,
    build_routine_unassign_status_text,
)
from gui_routine_guard import routine_action_guard_info
from gui_routine_policy import (
    routine_action_reasons_for_stock,
    classify_routine_assign_targets,
    can_unassign_active_routine_from_stock,
)
from gui_routine_service import ensure_single_real_trade_routine_for_stock
from gui_routine_registry import (
    get_routine_dirs as registry_get_routine_dirs,
    routine_display_name as registry_routine_display_name,
    read_routine_budget,
)
from runtime_io import (
    read_json_dict,
    read_orders_data,
    write_json_if_missing,
)
from gui_auto_trade_runtime import write_state_json
from state_policy import (
    auto_trade_status_color,
    auto_trade_status_display,
    auto_trade_status_dot,
    effective_schedule_times,
    minutes_from_hhmm,
    normalize_after_trade_end_status,
    normalize_operation_mode,
    normalized_hhmm_or_empty,
    normalized_hhmmss_or_empty,
    operation_mode_check_text,
    operation_mode_display,
    real_trade_enabled,
    trade_permission_display,
    operation_mode_recalculation_target_status,
    operation_text_and_color,
    read_global_schedule,
    schedule_override_enabled,
    scheduled_status_for_now,
    seconds_from_hhmmss,
    start_status_by_operation_mode,
    status_after_operation_mode_change,
    validate_buy_time_range,
    write_global_schedule,
)


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
STOCK_LIBRARY_PATH = PROJECT_ROOT / "stock_library.json"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
INVALID_ITEMS_LOG_PATH = PROJECT_ROOT / "invalid_items.log"
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
BLOCKED_ACTION_REPORT_DIR = PROJECT_ROOT / "reports" / "blocked_actions"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"
SORT_ROLE = Qt.UserRole + 100


class SortableTableWidgetItem(QTableWidgetItem):
    """화면 표시값과 정렬 기준값을 분리하는 표 아이템."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if other is not None else None
        if left is not None and right is not None:
            try:
                return left < right
            except Exception:
                return str(left) < str(right)
        return self.text() < (other.text() if other is not None else "")


class CenteredCheckBoxDelegate(QStyledItemDelegate):
    """체크박스를 셀 중앙에 그리는 전용 델리게이트."""

    def paint(self, painter, option, index) -> None:
        check_state = index.data(Qt.CheckStateRole)
        if check_state is None:
            super().paint(painter, option, index)
            return

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        check_option = QStyleOptionButton()
        check_option.state = QStyle.State_Enabled
        if check_state == Qt.Checked:
            check_option.state |= QStyle.State_On
        else:
            check_option.state |= QStyle.State_Off

        style = QApplication.style()
        indicator_rect = style.subElementRect(QStyle.SE_CheckBoxIndicator, check_option, None)
        check_option.rect = QRect(
            option.rect.x() + (option.rect.width() - indicator_rect.width()) // 2,
            option.rect.y() + (option.rect.height() - indicator_rect.height()) // 2,
            indicator_rect.width(),
            indicator_rect.height(),
        )
        style.drawControl(QStyle.CE_CheckBox, check_option, painter)


def get_routine_dirs() -> list[Path]:
    """루틴 패키지 레지스트리 기준 루틴 경로를 조회한다.

    신규 기준:
    - routines/<루틴명>/routine.json 을 루틴 원본으로 인정한다.
    - 기존 _루틴폴더/budget.json 은 gui_routine_registry 내부 fallback에서만 제한적으로 처리한다.
    - 이 함수는 기존 호출부 호환용 래퍼이며, 종목 폴더를 생성하거나 탐색하지 않는다.
    """
    return registry_get_routine_dirs()


def routine_display_name(routine_dir: Path) -> str:
    """루틴 패키지/경로를 화면 표시 루틴명으로 변환한다."""
    return registry_routine_display_name(routine_dir)

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def display_status_text_for_gui(raw_status: object) -> str:
    """GUI 표시용 상태명. state_policy 기준 6개 표시 상태로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"

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


def normalize_stock_code(code: str) -> str:
    """
    종목코드는 자동 보정하지 않고 앞뒤 공백만 제거한다.

    주의:
    - 930 -> 000930 같은 zfill 보정은 금지한다.
    - 사용자가 입력한 값이 그대로 6자리 숫자여야 한다.
    """
    return code.strip()


def is_valid_stock_code(code: str) -> bool:
    """
    종목코드 기본 형식 검증.
    """
    return code.isdigit() and len(code) == 6 and code != "000000"


def load_stock_library() -> list[dict[str, str]]:
    """
    stock_library.json 을 읽어 검색 등록용 종목 목록으로 변환한다.

    현재 단계에서는 키움 OpenAPI 검색식 연동 전이므로
    로컬 종목 라이브러리를 기준으로 종목명/종목코드/초성/부분코드 검색을 수행한다.
    """
    if not STOCK_LIBRARY_PATH.exists():
        return []

    try:
        data = json.loads(STOCK_LIBRARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    stocks: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        market = str(item.get("market", "")).strip()
        chosung = str(item.get("chosung", "")).strip()

        if not code or not name:
            continue

        stocks.append(
            {
                "code": code,
                "name": name,
                "market": market,
                "chosung": chosung,
            }
        )

    return stocks


def find_library_stock_by_code(code: str) -> dict[str, str] | None:
    """
    종목코드 기준으로 stock_library.json 의 종목을 찾는다.
    """
    normalized_code = normalize_stock_code(code)
    for stock in load_stock_library():
        if stock.get("code", "") == normalized_code:
            return stock
    return None


def validate_base_stock_record(
    code: str,
    name: str,
    line_no: int,
    seen_codes: set[str],
    seen_names: set[str],
) -> str:
    """
    기초종목.txt 에 이미 저장된 종목 1행의 표시용 검증 상태를 반환한다.

    중요:
    - 기존에 잘못 저장된 000000, 임의 종목명, 라이브러리 불일치 데이터를 정상으로 표시하지 않는다.
    - 등록 가능 여부는 stock_library.json 을 기준으로 판단한다.
    """
    errors: list[str] = []

    if not is_valid_stock_code(code):
        errors.append("종목코드 오류")

    if not name:
        errors.append("종목명 오류")

    if code in seen_codes:
        errors.append("중복 코드")

    if name and name in seen_names:
        errors.append("중복 종목명")

    library_stock = find_library_stock_by_code(code)
    if library_stock is None:
        errors.append("라이브러리 없음")
    else:
        library_name = library_stock.get("name", "").strip()
        if name != library_name:
            errors.append("라이브러리 불일치")

    if errors:
        return f"{line_no}행: " + ", ".join(errors)

    return "정상"



def single_routine_list(routines: list[str]) -> list[str]:
    """
    기초종목.txt 활성 루틴은 종목당 1개만 허용한다.

    루틴 폴더에 과거 종목 폴더가 남아 있어도,
    활성 연결은 기초종목.txt의 첫 번째 유효 루틴 1개만 사용한다.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for routine in routines:
        routine_name = str(routine).strip()
        if routine_name and routine_name not in seen:
            clean.append(routine_name)
            seen.add(routine_name)
    return clean[:1]


def normalize_base_stock_single_routine_file() -> bool:
    """
    기존 기초종목.txt에 루틴이 여러 개 저장되어 있으면 첫 번째 루틴만 남긴다.

    정책:
    - 동일 종목은 활성 루틴을 1개만 가진다.
    - 루틴 폴더의 기존 종목 runtime 폴더는 삭제하지 않는다.
    - 자동매매설정 창은 기초종목.txt의 단일 루틴 연결만 표시한다.
    """
    if not BASE_STOCK_PATH.exists():
        return False

    lines = BASE_STOCK_PATH.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    changed = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) < 2:
            new_lines.append(stripped)
            continue

        code, name = parts[0], parts[1]
        routines = single_routine_list([part for part in parts[2:] if part])
        row_values = [code, name] + routines
        next_line = ",".join(row_values)
        if next_line != stripped:
            changed = True
        new_lines.append(next_line)

    if changed:
        BASE_STOCK_PATH.write_text(
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )

    return changed


def read_base_stocks() -> list[dict[str, object]]:
    """
    기초종목.txt 를 읽어 종목 목록으로 변환한다.

    파일 형식:
    종목코드,종목명,등록루틴1,등록루틴2,...

    현재 기초종목.txt 에 등록일시 필드는 사용하지 않는다.
    """
    if not BASE_STOCK_PATH.exists():
        BASE_STOCK_PATH.write_text("", encoding="utf-8")

    stocks: list[dict[str, object]] = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()

    for line_no, raw_line in enumerate(BASE_STOCK_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            stocks.append(
                {
                    "code": "",
                    "name": line,
                    "routines": [],
                    "registered_at": "-",
                    "validation_status": f"형식오류 {line_no}행",
                }
            )
            continue

        code = parts[0]
        name = parts[1]
        routines = single_routine_list([part for part in parts[2:] if part])
        validation_status = validate_base_stock_record(code, name, line_no, seen_codes, seen_names)

        stocks.append(
            {
                "code": code,
                "name": name,
                "routines": routines,
                "registered_at": "-",
                "validation_status": validation_status,
            }
        )

        if code:
            seen_codes.add(code)
        if name:
            seen_names.add(name)

    return stocks


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


def ensure_single_real_trade_routine_for_all_stocks() -> None:
    """
    기존 데이터 마이그레이션용 보정.
    기초종목.txt에 연결된 각 종목마다 실주문 루틴이 최대 1개가 되도록 정리한다.
    """
    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        if code and name:
            ensure_single_real_trade_routine_for_stock(code, name)


def update_base_stock_routines(code: str, name: str, routines: list[str]) -> bool:
    """
    기초종목.txt 의 특정 종목 행에 루틴 목록을 반영한다.
    """
    if not BASE_STOCK_PATH.exists():
        return False

    lines = BASE_STOCK_PATH.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    updated = False

    clean_routines = single_routine_list(routines)

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) >= 2 and parts[0] == code and parts[1] == name:
            row_values = [code, name] + clean_routines
            new_lines.append(",".join(row_values))
            updated = True
        else:
            new_lines.append(stripped)

    if not updated:
        return False

    BASE_STOCK_PATH.write_text(
        "\n".join(new_lines) + ("\n" if new_lines else ""),
        encoding="utf-8",
    )
    return True



def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """
    종목 폴더명에서 종목코드와 종목명을 분리한다.
    예: 005930_삼성전자 -> ("005930", "삼성전자")
    """
    parts = folder_name.split("_", 1)
    if len(parts) != 2:
        return "", folder_name.strip()
    return parts[0].strip(), parts[1].strip()


def _routine_values_from_stock_record(stock: dict[str, object]) -> list[str]:
    """종목 레코드에서 루틴명 목록을 추출한다."""
    values: list[str] = []
    for key in ("routines", "routine", "routine_name", "assigned_routine", "active_routine"):
        value = stock.get(key)
        if isinstance(value, list):
            values.extend(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value or "").strip()
            if text:
                values.append(text)

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def get_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """중앙 stocks/ 기준으로 해당 루틴에 연결된 종목 폴더를 조회한다.

    더 이상 루틴 패키지 폴더 내부에서 종목 폴더를 탐색하지 않는다.
    즉, routines/지표추종매매/ 아래에 005930_삼성전자 같은 폴더가
    생기거나 표시 기준으로 사용되는 경로를 차단한다.
    """
    routine_name = routine_display_name(routine_dir)
    if not routine_name:
        return []

    result: list[Path] = []
    seen: set[str] = set()
    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        if not code or not name:
            continue
        if routine_name not in _routine_values_from_stock_record(stock):
            continue
        stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
        if stock_dir is None or not stock_dir.exists() or not stock_dir.is_dir():
            continue
        key = str(stock_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append(stock_dir)

    result.sort(key=lambda path: path.name)
    return result

def routine_status_display_text(raw_status: object) -> str:
    """루틴/리포트용 운영상태 표시명을 state_policy 기준으로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


class MainWindow(QMainWindow):
    """
    키움 자동매매 시스템 메인 윈도우
    """

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("키움 OpenAPI 자동매매 시스템 - v1.1 Windows GUI")
        self.resize(1200, 760)

        self.login_status_label = QLabel("로그인 상태: 미연결")
        self.account_label = QLabel("계좌번호: -")
        self.account_type_label = QLabel("계좌 구분: -")
        self.auto_status_label = QLabel("전체 자동매매 상태: 정지")
        self.buy_time_status_label = QLabel("매수 가능 상태: 확인 전")

        self.routine_table = QTableWidget()
        self.running_stock_table = QTableWidget()

        self.btn_stock_register = QPushButton("종목등록설정")
        self.btn_auto_trade_setting = QPushButton("자동매매설정")
        self.btn_stop_all = QPushButton("전체 자동매매 정지")
        self.btn_restart = QPushButton("재시작")
        self.btn_initialize = QPushButton("초기화")
        self.btn_log_view = QPushButton("로그 보기")
        self.btn_exit = QPushButton("종료")
        self.btn_emergency_stop = QPushButton("긴급정지")

        self._setup_ui()
        self._connect_close_option_checks()
        self._connect_events()
        normalize_base_stock_single_routine_file()
        self.refresh_all()

    def _setup_ui(self) -> None:
        central = QWidget()
        main_layout = QVBoxLayout()

        top_box = self._create_top_status_box()
        table_layout = self._create_table_area()
        button_layout = self._create_button_area()

        main_layout.addWidget(top_box)
        main_layout.addLayout(table_layout)
        main_layout.addLayout(button_layout)

        central.setLayout(main_layout)
        self.setCentralWidget(central)

        self.statusBar().showMessage("준비 완료")

    def _create_top_status_box(self) -> QGroupBox:
        box = QGroupBox("시스템 상태")
        layout = QGridLayout()

        layout.addWidget(self.login_status_label, 0, 0)
        layout.addWidget(self.account_label, 0, 1)
        layout.addWidget(self.account_type_label, 0, 2)

        layout.addWidget(self.auto_status_label, 1, 0)
        layout.addWidget(self.buy_time_status_label, 1, 1)
        layout.addWidget(self.btn_emergency_stop, 1, 2)

        self.btn_emergency_stop.setMinimumHeight(42)

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
            "종목코드",
            "종목명",
            "루틴명",
            "상태",
            "보유수량",
            "평균단가",
            "매수회차",
            "현재가",
            "평가손익",
            "마지막 주문시간",
        ]

        self.running_stock_table.setColumnCount(len(headers))
        self.running_stock_table.setHorizontalHeaderLabels(headers)
        self.running_stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.running_stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.running_stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)

    def _connect_events(self) -> None:
        self.btn_exit.clicked.connect(self.close)
        self.btn_emergency_stop.clicked.connect(self.on_emergency_stop_clicked)
        self.btn_stop_all.clicked.connect(self.on_stop_all_clicked)
        self.btn_stock_register.clicked.connect(self.open_stock_register_window)
        self.btn_auto_trade_setting.clicked.connect(self.open_auto_trade_setting_window)
        self.btn_restart.clicked.connect(self.not_implemented)
        self.btn_initialize.clicked.connect(self.not_implemented)
        self.btn_log_view.clicked.connect(self.not_implemented)

    def refresh_all(self) -> None:
        self.load_routine_table()
        self.load_running_stock_table()
        self.update_emergency_button_state()

    def load_routine_table(self) -> None:
        """
        routines/<루틴명>/routine.json 루틴 패키지를 기준으로 루틴을 인식한다.
        """
        routine_dirs = get_routine_dirs()

        self.routine_table.setRowCount(len(routine_dirs))

        for row, routine_dir in enumerate(routine_dirs):
            routine_name = routine_display_name(routine_dir)

            budget = read_routine_budget(routine_dir)
            total_budget = int(budget.get("total_budget", budget.get("budget", 0)) or 0)
            used_budget = int(budget.get("used_budget", 0) or 0)
            available_budget = int(budget.get("available_budget", max(total_budget - used_budget, 0)) or 0)

            stock_dirs = get_stock_dirs_in_routine(routine_dir)

            values = [
                routine_name,
                str(len(stock_dirs)),
                "0",
                str(len(stock_dirs)),
                "0",
                f"{total_budget:,}",
                f"{used_budget:,}",
                f"{available_budget:,}",
            ]

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.routine_table.setItem(row, col, item)

    def load_running_stock_table(self) -> None:
        self.running_stock_table.setRowCount(0)

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
        for stock_dir in self.all_runtime_stock_dirs():
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "")).strip().upper()
            if status in {"EMERGENCY_STOPPED", "EMERGENCY_STOP", "EMERGENCY"}:
                return True
        return False

    def update_emergency_button_state(self) -> None:
        if self.has_emergency_stopped_stock():
            self.btn_emergency_stop.setText("정지해제")
        else:
            self.btn_emergency_stop.setText("긴급정지")

    def emergency_review_reason_for_stock(self, stock_dir: Path) -> tuple[bool, str]:
        """정지해제 시 정상/검토관리 이동 기준을 판정한다."""
        state_path = stock_dir / "state.json"
        config_path = stock_dir / "config.json"
        orders_path = stock_dir / "orders.json"

        state = read_json_dict(state_path)
        config = read_json_dict(config_path)
        orders = read_orders_data(orders_path)

        if not state_path.exists() or not isinstance(state, dict):
            return True, "state.json 이상"
        if not config_path.exists() or not isinstance(config, dict):
            return True, "config.json 이상"
        if not orders_path.exists():
            return True, "orders.json 누락"

        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        if holding_qty > 0:
            return True, "긴급정지 해제 시 보유잔량 존재"

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            return True, "긴급정지 해제 시 미체결 매수 존재"
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            return True, "긴급정지 해제 시 미체결 매도 존재"
        if buy_pending_qty == "?" or sell_pending_qty == "?":
            return True, "미체결 수량 확인 필요"

        return False, "긴급정지 해제 무결성 정상"


    def update_runtime_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state: dict[str, object] | None = None,
        log_suffix: str = "",
    ) -> bool:
        """메인창 긴급정지/정지해제 전용 state.json 상태 저장."""
        state_path = stock_dir / "state.json"
        state = read_json_dict(state_path)
        if not isinstance(state, dict):
            state = default_state()

        before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        state["status"] = new_status
        state["updated_at"] = now_text()

        if extra_state:
            state.update(extra_state)

        if not write_state_json(stock_dir, state):
            QMessageBox.critical(
                self,
                "상태 저장 오류",
                f"{code} {name} 상태 저장 중 오류가 발생했습니다.",
            )
            append_stock_log(stock_dir, "ERROR", f"상태 저장 실패: {before_status} -> {new_status}")
            return False

        suffix_text = f" / {log_suffix}" if log_suffix else ""
        append_stock_log(stock_dir, "GUI", f"긴급정지 상태 변경: {before_status} -> {new_status}{suffix_text}")
        return True

    def execute_emergency_stop(self) -> None:
        changed_count = 0
        for stock_dir in self.all_runtime_stock_dirs():
            code, name = parse_stock_folder_name(stock_dir.name)
            ok = self.update_runtime_stock_status(
                stock_dir,
                code,
                name,
                "EMERGENCY_STOPPED",
                {
                    "emergency_stopped_at": now_text(),
                    "emergency_reason": "USER_EMERGENCY_STOP",
                },
                "사용자 긴급정지",
            )
            if ok:
                changed_count += 1

        append_changelog("UPDATE", "state.json", f"긴급정지 실행: {changed_count}개 종목")
        self.statusBar().showMessage(f"긴급정지 실행 완료: {changed_count}개 종목")
        self.refresh_all()
        QMessageBox.information(
            self,
            "긴급정지 완료",
            "긴급정지 처리 완료\n\n"
            f"대상 종목: {changed_count}개\n"
            "신규 매수/매도: 차단\n"
            "자동판단/자동청산: 중지\n"
            "보유 종목: 자동 매도하지 않음\n\n"
            "버튼은 정지해제로 전환됩니다.",
        )

    def release_emergency_stop(self) -> None:
        normal_count = 0
        review_count = 0
        for stock_dir in self.all_runtime_stock_dirs():
            code, name = parse_stock_folder_name(stock_dir.name)
            routine_name = self.routine_name_for_stock_dir(stock_dir)
            has_problem, reason = self.emergency_review_reason_for_stock(stock_dir)
            if has_problem:
                metadata = {
                    "review_required": True,
                    "review_status": "PENDING",
                    "review_location": "긴급정지해제",
                    "review_reason": reason,
                    "review_entered_at": now_text(),
                    "review_checked_at": now_text(),
                    "review_routine": routine_name,
                    "review_detail": f"{code} {name} / {reason}",
                }
                if self.update_runtime_stock_status(stock_dir, code, name, "REVIEW_REQUIRED", metadata, reason):
                    review_count += 1
            else:
                metadata = {
                    "emergency_released_at": now_text(),
                    "emergency_release_check": "PASSED",
                    "review_required": False,
                    "review_status": "",
                    "review_location": "",
                    "review_reason": "",
                    "review_detail": "",
                }
                if self.update_runtime_stock_status(stock_dir, code, name, "STOPPED", metadata, reason):
                    normal_count += 1

        append_changelog(
            "UPDATE",
            "state.json",
            f"긴급정지 해제 무결성 검사: 정상 {normal_count}개 / 검토관리 {review_count}개",
        )
        self.statusBar().showMessage(
            f"정지해제 완료: 정상 {normal_count}개 / 검토관리 {review_count}개"
        )
        self.refresh_all()
        QMessageBox.information(
            self,
            "정지해제 완료",
            "무결성 검사 완료\n\n"
            f"정상 → 감시/대기: {normal_count}개\n"
            f"검토관리 이동: {review_count}개\n\n"
            "상세 내용은 검토종목 관리창에서 확인하세요.",
        )

    def on_emergency_stop_clicked(self) -> None:
        """긴급정지 버튼은 확인 없이 즉시 실행한다.

        - 평상시: 즉시 긴급정지 실행 후 결과 브리핑 1회
        - 긴급정지 중: 즉시 정지해제/무결성검사 실행 후 결과 브리핑 1회
        """
        if self.has_emergency_stopped_stock():
            self.release_emergency_stop()
            return

        self.execute_emergency_stop()

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

    def open_stock_register_window(self) -> None:
        self.stock_register_window = StockRegisterWindow(self)
        self.stock_register_window.show()

    def open_auto_trade_setting_window(self) -> None:
        self.auto_trade_setting_window = AutoTradeSettingWindow(self)
        self.auto_trade_setting_window.show()

    def not_implemented(self) -> None:
        QMessageBox.information(
            self,
            "안내",
            "이 기능은 다음 단계에서 구현합니다.",
        )



class AutoTradeUnregisterConfirmDialog(QDialog):
    """자동매매설정 등록해제 가능/주의/불가 대상을 한 창에 표시한다."""

    def __init__(
        self,
        routine_name: str,
        immediate_items: list[dict[str, object]],
        force_items: list[dict[str, object]],
        blocked_items: list[dict[str, object]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("주의 종목 등록해제")
        self.resize(760, 560)
        self.force_items = force_items
        self.force_checkboxes: list[QCheckBox] = []

        main_layout = QVBoxLayout()

        summary_label = QLabel(
            f"즉시 등록해제 {len(immediate_items)}개 / 주의 등록해제 {len(force_items)}개 / 등록해제 불가 {len(blocked_items)}개"
        )
        summary_label.setMinimumHeight(44)
        main_layout.addWidget(summary_label)

        if blocked_items:
            blocked_title = QLabel("등록해제 불가")
            blocked_title.setStyleSheet("color: #d00000; font-weight: bold;")
            main_layout.addWidget(blocked_title)

            blocked_list = QListWidget()
            blocked_list.setMinimumHeight(110)
            for item in blocked_items:
                blocked_list.addItem(QListWidgetItem(self._blocked_line(item)))
            main_layout.addWidget(blocked_list)

        if force_items:
            force_title = QLabel("주의 등록해제")
            force_title.setStyleSheet("color: #b36b00; font-weight: bold;")
            main_layout.addWidget(force_title)

            force_box = QWidget()
            force_layout = QVBoxLayout()
            force_layout.setContentsMargins(0, 0, 0, 0)
            for item in force_items:
                checkbox = QCheckBox(self._force_line(item))
                checkbox.setChecked(False)
                self.force_checkboxes.append(checkbox)
                force_layout.addWidget(checkbox)
            force_box.setLayout(force_layout)
            main_layout.addWidget(force_box)

        if immediate_items:
            immediate_title = QLabel("즉시 등록해제 가능")
            immediate_title.setStyleSheet("font-weight: bold;")
            main_layout.addWidget(immediate_title)

            immediate_list = QListWidget()
            immediate_list.setMinimumHeight(100)
            for item in immediate_items:
                code = str(item.get("code", "")).strip()
                name = str(item.get("name", "")).strip()
                immediate_list.addItem(QListWidgetItem(f"{code} / {name}"))
            main_layout.addWidget(immediate_list)

        notice = QLabel(
            "※ 즉시 등록해제 가능 종목은 바로 처리됩니다.\n"
            "※ 주의 등록해제 종목은 체크한 항목만 처리됩니다.\n"
            "※ 등록해제 불가 종목은 처리불가 누적리포트에 기록됩니다."
        )
        notice.setStyleSheet("color: #555555;")
        main_layout.addWidget(notice)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_confirm = QPushButton("등록해제 실행")
        self.btn_cancel = QPushButton("취소")
        self.btn_confirm.setMinimumWidth(130)
        self.btn_cancel.setMinimumWidth(100)
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_confirm)
        button_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def _status_text(self, value: object) -> str:
        raw = str(value or "").strip()
        if not raw or raw == "-":
            return "-"
        try:
            return auto_trade_status_display(raw)
        except Exception:
            return "검토종목"

    def _reason_text(self, item: dict[str, object]) -> str:
        reasons = item.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        return ", ".join(str(reason) for reason in reasons if str(reason).strip()) or "-"

    def _blocked_line(self, item: dict[str, object]) -> str:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        return f"{code} / {name}"

    def _force_line(self, item: dict[str, object]) -> str:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        reason = self._reason_text(item)
        return f"{code} / {name} / {reason}"

    def selected_items(self) -> list[dict[str, object]]:
        selected: list[dict[str, object]] = []
        for index, checkbox in enumerate(self.force_checkboxes):
            if checkbox.isChecked() and index < len(self.force_items):
                selected.append(self.force_items[index])
        return selected



class RoutineUnassignConfirmDialog(QDialog):
    """루틴 해제 가능/불가 대상을 한 번에 보여주고 진행 여부를 확인한다."""

    def __init__(
        self,
        routine_name: str,
        removable_items: list[tuple[str, str]],
        blocked_items: list[dict[str, object]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("루틴 해제 확인")
        self.resize(760, 520)
        self.confirmed = False

        main_layout = QVBoxLayout()

        summary_label = QLabel(
            f"즉시 해제 가능 {len(removable_items)}개 / 해제 불가 {len(blocked_items)}개"
        )
        summary_label.setMinimumHeight(44)
        main_layout.addWidget(summary_label)

        if blocked_items:
            blocked_title = QLabel("해제 불가")
            blocked_title.setStyleSheet("color: #d00000; font-weight: bold;")
            main_layout.addWidget(blocked_title)

            blocked_list = QListWidget()
            blocked_list.setMinimumHeight(130)
            for item in blocked_items:
                code = str(item.get("code", "")).strip()
                name = str(item.get("name", "")).strip()
                current_routine = str(item.get("routine_name", "")).strip() or routine_name
                reasons = item.get("reasons", [])
                if not isinstance(reasons, list):
                    reasons = [str(reasons)]
                reason_text = ", ".join(str(reason) for reason in reasons if str(reason).strip()) or "-"
                display_status = str(item.get("display_status", "")).strip() or "-"
                line = f"{code} / {name}"
                blocked_list.addItem(QListWidgetItem(line))
            main_layout.addWidget(blocked_list)

        if removable_items:
            removable_title = QLabel("해제 가능")
            removable_title.setStyleSheet("font-weight: bold;")
            main_layout.addWidget(removable_title)

            removable_list = QListWidget()
            removable_list.setMinimumHeight(100)
            for code, name in removable_items:
                removable_list.addItem(QListWidgetItem(f"{code} / {name}"))
            main_layout.addWidget(removable_list)

        notice = QLabel(
            "※ 해제 가능 종목만 처리됩니다.\n"
            "※ 해제 불가 종목은 처리불가 누적리포트에 기록됩니다."
        )
        notice.setStyleSheet("color: #555555;")
        main_layout.addWidget(notice)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_confirm = QPushButton("해제 실행")
        self.btn_cancel = QPushButton("취소")
        self.btn_confirm.setMinimumWidth(120)
        self.btn_cancel.setMinimumWidth(100)
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_confirm)
        button_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)


class RoutineAssignWindow(QDialog):
    """
    매매루틴지정 창.

    역할:
    - 기초종목.txt 등록 종목 중 루틴 변경이 가능한 종목만 좌측에 표시한다.
    - 체크박스는 실제 처리 대상 표시용으로 유지한다.
    - 종목등록설정 창에서 전달된 종목 중 루틴 변경 가능한 종목은 자동 체크한다.
    - 루틴 지정/해제 실행 시점에도 삭제/등록해제 안전 규칙을 다시 검사한다.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        target_code: str = "",
        target_name: str = "",
        target_stocks: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)

        self.target_code = target_code.strip()
        self.target_name = target_name.strip()
        self.target_stocks = [
            (str(code).strip(), str(name).strip())
            for code, name in (target_stocks or [])
            if str(code).strip() and str(name).strip()
        ]
        if not self.target_stocks and (self.target_code or self.target_name):
            self.target_stocks = [(self.target_code, self.target_name)]

        self.setWindowTitle("매매루틴지정")
        self.resize(1060, 820)

        self.stock_search_input = QLineEdit()
        self.stock_search_input.setPlaceholderText("루틴 지정 가능 종목 검색")
        self.stock_table = QTableWidget()
        self.routine_table = QTableWidget()
        self.assigned_stock_table = QTableWidget()

        self.btn_apply = QPushButton("루틴 지정")
        self.btn_unassign = QPushButton("루틴 해제")
        self.btn_close = QPushButton("닫기")
        self.status_label = QLabel("")
        self.btn_unassign.setEnabled(False)
        self._updating_stock_checks = False
        self._updating_routine_checks = False
        self._updating_assigned_checks = False
        self._stock_selection_synced = False
        self._assigned_selection_synced = False
        self._stock_sort_column = -1
        self._stock_sort_order = Qt.AscendingOrder
        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder
        self._assigned_sort_column = -1
        self._assigned_sort_order = Qt.AscendingOrder

        self._setup_ui()
        self._connect_events()

        self.refresh_all()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()

        stock_panel = QWidget()
        stock_layout = QVBoxLayout()
        stock_header_layout = QHBoxLayout()
        self._setup_stock_table()
        stock_header_layout.addWidget(QLabel("루틴 지정 가능 종목"))
        stock_header_layout.addStretch(1)
        stock_header_layout.addWidget(QLabel("검색"))
        stock_header_layout.addWidget(self.stock_search_input)
        stock_layout.addLayout(stock_header_layout)
        stock_layout.addWidget(self.stock_table)
        stock_panel.setLayout(stock_layout)

        routine_panel = QWidget()
        routine_layout = QVBoxLayout()
        routine_header_layout = QHBoxLayout()
        self._setup_routine_table()
        routine_header_layout.addWidget(QLabel("자동매매 루틴"))
        routine_header_layout.addStretch(1)
        routine_header_layout.addWidget(self.btn_apply)
        routine_layout.addLayout(routine_header_layout)
        routine_layout.addWidget(self.routine_table)
        routine_panel.setLayout(routine_layout)

        top_layout.addWidget(stock_panel, 4)
        top_layout.addWidget(routine_panel, 2)

        assigned_panel = QWidget()
        assigned_layout = QVBoxLayout()
        assigned_header_layout = QHBoxLayout()
        assigned_footer_layout = QHBoxLayout()
        self._setup_assigned_stock_table()
        assigned_header_layout.addWidget(QLabel("선택 루틴 연결 종목"))
        assigned_header_layout.addStretch(1)
        assigned_header_layout.addWidget(self.btn_unassign)
        assigned_layout.addLayout(assigned_header_layout)
        assigned_layout.addWidget(self.assigned_stock_table)

        assigned_footer_layout.setContentsMargins(0, 6, 0, 0)
        assigned_footer_layout.addWidget(self.status_label, 1, Qt.AlignVCenter)
        assigned_footer_layout.addStretch(1)
        assigned_footer_layout.addWidget(self.btn_close, 0, Qt.AlignRight | Qt.AlignVCenter)
        assigned_layout.addLayout(assigned_footer_layout)
        assigned_panel.setLayout(assigned_layout)

        self.status_label.setMinimumHeight(34)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.btn_apply.setMinimumHeight(32)
        self.btn_unassign.setMinimumHeight(32)
        self.btn_close.setMinimumHeight(34)
        self.btn_close.setMinimumWidth(110)
        self.btn_apply.setMinimumWidth(110)
        self.btn_unassign.setMinimumWidth(90)
        self.assigned_stock_table.setMinimumHeight(300)
        self.stock_search_input.setMinimumWidth(240)

        main_layout.addLayout(top_layout, 3)
        main_layout.addWidget(assigned_panel, 4)
        self.setLayout(main_layout)

    def _configure_fixed_fit_columns(
        self,
        table: QTableWidget,
        fixed_widths: dict[int, int],
        stretch_column: int | None,
        min_section_width: int = 44,
    ) -> None:
        """
        정보 표시 영역과 컬럼 폭이 빈틈없이 맞도록 설정한다.
        """
        header = table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setCascadingSectionResizes(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(min_section_width)
        header.setDefaultAlignment(Qt.AlignCenter)

        old_handler = getattr(table, "_first_column_width_restore_handler", None)
        if old_handler is not None:
            try:
                header.sectionResized.disconnect(old_handler)
            except Exception:
                pass
            table._first_column_width_restore_handler = None

        for col in range(table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            if col in fixed_widths:
                header.resizeSection(col, fixed_widths[col])
                table.setColumnWidth(col, fixed_widths[col])

        if stretch_column is not None:
            header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.verticalHeader().setSectionsMovable(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)

    def _setup_stock_table(self) -> None:
        headers = ["선택", "종목코드", "종목명", "현재 루틴", "운영상태"]
        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.stock_table)
        self._configure_fixed_fit_columns(
            self.stock_table,
            fixed_widths={0: 42, 1: 105, 3: 150, 4: 120},
            stretch_column=2,
            min_section_width=34,
        )
        self.stock_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.stock_table))
        self.stock_table.horizontalHeader().setSectionsClickable(True)
        self.stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _setup_routine_table(self) -> None:
        headers = ["선택", "루틴명"]
        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.routine_table)
        self._configure_fixed_fit_columns(
            self.routine_table,
            fixed_widths={0: 44},
            stretch_column=1,
            min_section_width=34,
        )
        self.routine_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.routine_table))
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)

    def _setup_assigned_stock_table(self) -> None:
        headers = ["선택", "코드", "종목", "운영", "상태", "보유", "평단", "현재가", "미수", "미도", "수익률"]
        self.assigned_stock_table.setColumnCount(len(headers))
        self.assigned_stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.assigned_stock_table)
        self._configure_fixed_fit_columns(
            self.assigned_stock_table,
            fixed_widths={
                0: 46,   # 선택: 헤더 글자 + 체크박스
                1: 74,   # 코드
                2: 160,  # 종목: 기본 폭, 실제 남는 폭은 Stretch로 자동 보정
                3: 58,   # 운영
                4: 120,  # 상태
                5: 72,   # 보유: 주식수
                6: 92,   # 평단: 금액
                7: 92,   # 현재가: 금액
                8: 72,   # 미수: 주식수
                9: 72,   # 미도: 주식수
                10: 72,  # 수익률
            },
            stretch_column=2,
            min_section_width=34,
        )
        self.assigned_stock_table.setItemDelegateForColumn(0, CenteredCheckBoxDelegate(self.assigned_stock_table))
        self.assigned_stock_table.horizontalHeader().setSectionsClickable(True)
        self.assigned_stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.assigned_stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.assigned_stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.assigned_stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.assigned_stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _connect_events(self) -> None:
        self.stock_search_input.textChanged.connect(self.load_stock_table)
        self.stock_table.horizontalHeader().sectionClicked.connect(self.sort_stock_table_by_column)
        self.stock_table.itemChanged.connect(self.on_stock_check_changed)
        self.stock_table.itemClicked.connect(self.on_stock_item_clicked)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.customContextMenuRequested.connect(self.show_stock_table_context_menu)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_routine_table_by_column)
        self.routine_table.itemChanged.connect(self.on_routine_check_changed)
        self.routine_table.itemClicked.connect(self.on_routine_item_clicked)
        self.routine_table.itemSelectionChanged.connect(self.load_selected_routine_stocks)
        self.assigned_stock_table.horizontalHeader().sectionClicked.connect(self.sort_assigned_stock_table_by_column)
        self.assigned_stock_table.itemChanged.connect(self.on_assigned_stock_check_changed)
        self.assigned_stock_table.itemClicked.connect(self.on_assigned_stock_item_clicked)
        self.assigned_stock_table.itemSelectionChanged.connect(self.on_assigned_stock_selection_changed)
        self.assigned_stock_table.customContextMenuRequested.connect(self.show_assigned_stock_table_context_menu)
        self.btn_apply.clicked.connect(self.apply_routines_to_checked_stocks)
        self.btn_unassign.clicked.connect(self.unassign_checked_stocks_from_selected_routine)
        self.btn_close.clicked.connect(self.close)

    def refresh_all(self) -> None:
        self.load_stock_table()
        self.load_routine_table()
        self.assigned_stock_table.setRowCount(0)
        self.btn_unassign.setEnabled(False)

        if self.target_stocks:
            self.select_target_stocks()
        else:
            self.show_status("")

    def show_status(self, message: str, timeout_ms: int = 5000) -> None:
        display_message = f"※ {message}" if message else ""
        self.status_label.setText(display_message)
        parent = self.parent()
        if isinstance(parent, StockRegisterWindow):
            main_window = parent.parent()
            if isinstance(main_window, MainWindow):
                main_window.statusBar().showMessage(display_message, timeout_ms)

    def sort_stock_table_by_column(self, column: int) -> None:
        self._stock_sort_order = next_sort_order(self._stock_sort_column, column, self._stock_sort_order)
        self._stock_sort_column = column
        self.stock_table.sortItems(column, self._stock_sort_order)
        self.stock_table.horizontalHeader().setSortIndicator(column, self._stock_sort_order)

    def sort_routine_table_by_column(self, column: int) -> None:
        self._routine_sort_order = next_sort_order(self._routine_sort_column, column, self._routine_sort_order)
        self._routine_sort_column = column
        self.routine_table.sortItems(column, self._routine_sort_order)
        self.routine_table.horizontalHeader().setSortIndicator(column, self._routine_sort_order)
        self.load_selected_routine_stocks()

    def sort_assigned_stock_table_by_column(self, column: int) -> None:
        self._assigned_sort_order = next_sort_order(self._assigned_sort_column, column, self._assigned_sort_order)
        self._assigned_sort_column = column
        self.assigned_stock_table.sortItems(column, self._assigned_sort_order)
        self.assigned_stock_table.horizontalHeader().setSortIndicator(column, self._assigned_sort_order)

    def _apply_saved_stock_sort(self) -> None:
        if self._stock_sort_column >= 0 and self._stock_sort_column < self.stock_table.columnCount():
            self.stock_table.sortItems(self._stock_sort_column, self._stock_sort_order)
            self.stock_table.horizontalHeader().setSortIndicator(self._stock_sort_column, self._stock_sort_order)

    def _apply_saved_routine_sort(self) -> None:
        if self._routine_sort_column >= 0 and self._routine_sort_column < self.routine_table.columnCount():
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)
            self.routine_table.horizontalHeader().setSortIndicator(self._routine_sort_column, self._routine_sort_order)

    def _apply_saved_assigned_sort(self) -> None:
        if self._assigned_sort_column >= 0 and self._assigned_sort_column < self.assigned_stock_table.columnCount():
            self.assigned_stock_table.sortItems(self._assigned_sort_column, self._assigned_sort_order)
            self.assigned_stock_table.horizontalHeader().setSortIndicator(self._assigned_sort_column, self._assigned_sort_order)

    def load_stock_table(self) -> None:
        keyword_text = self.stock_search_input.text().strip().lower() if hasattr(self, "stock_search_input") else ""
        keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]
        stocks = read_base_stocks()

        # 이 창은 기초종목.txt 등록 종목 중 루틴 변경 가능한 종목만 표시한다.
        allowed_stocks: list[dict[str, object]] = []
        for stock in stocks:
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            if not code or not name:
                continue
            can_process, _ = routine_action_reasons_for_stock(code, name, allow_unassigned=True)
            if can_process:
                allowed_stocks.append(stock)
        stocks = allowed_stocks

        def stock_matches(stock: dict[str, object], keyword: str) -> bool:
            code = str(stock.get("code", "")).strip().lower()
            name = str(stock.get("name", "")).strip().lower()
            routines = stock.get("routines", [])
            routine_text = ",".join(str(item).strip().lower() for item in routines) if isinstance(routines, list) else str(routines).lower()
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            current_routine = routine_list[0] if routine_list else "미등록"
            operation_status = active_stock_register_status_display(
                str(stock.get("code", "")).strip(),
                str(stock.get("name", "")).strip(),
                current_routine,
            ).lower()
            validation = str(stock.get("validation_status", "")).strip().lower()
            searchable_values = [code, name, routine_text, operation_status, validation]
            return any(keyword in value for value in searchable_values)

        if keywords:
            filtered: list[dict[str, object]] = []
            added_keys: set[tuple[str, str]] = set()
            for keyword in keywords:
                for stock in stocks:
                    key = (str(stock.get("code", "")).strip(), str(stock.get("name", "")).strip())
                    if key in added_keys:
                        continue
                    if stock_matches(stock, keyword):
                        filtered.append(stock)
                        added_keys.add(key)
            stocks = filtered

        previously_checked = {
            (code, name)
            for code, name, _ in self.checked_stocks()
        } if self.stock_table.rowCount() else set()
        target_keys = set(self.target_stocks)
        checked_keys = previously_checked | target_keys

        self._updating_stock_checks = True
        self.stock_table.blockSignals(True)
        self.stock_table.setRowCount(len(stocks))

        for row, stock in enumerate(stocks):
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            current_routine = routine_list[0] if routine_list else "미등록"
            operation_status = active_stock_register_status_display(code, name, current_routine)

            check_item = QTableWidgetItem("")
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Checked if (code, name) in checked_keys else Qt.Unchecked)
            check_item.setTextAlignment(Qt.AlignCenter)
            self.stock_table.setItem(row, 0, check_item)

            values = [code, name, current_routine, operation_status]
            for offset, value in enumerate(values, start=1):
                if offset == 4:
                    if value == "미지정":
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignCenter)
                    elif value in ("미생성", "오류"):
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    else:
                        item = create_auto_trade_status_item(value)
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                else:
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)
                self.stock_table.setItem(row, offset, item)

        self._apply_saved_stock_sort()
        self.stock_table.clearSelection()
        self.stock_table.blockSignals(False)
        self._updating_stock_checks = False
        self.sync_routine_with_checked_stocks()

    def load_routine_table(self) -> None:
        routine_dirs = get_routine_dirs()

        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            self.routine_table.setColumnCount(2)
            self.routine_table.setHorizontalHeaderLabels(["선택", "루틴명"])
            self.routine_table.setRowCount(len(routine_dirs))

            for row, routine_dir in enumerate(routine_dirs):
                display_name = routine_display_name(routine_dir)

                check_item = QTableWidgetItem("")
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                check_item.setCheckState(Qt.Unchecked)
                check_item.setTextAlignment(Qt.AlignCenter)
                self.routine_table.setItem(row, 0, check_item)

                name_item = QTableWidgetItem(display_name)
                name_item.setTextAlignment(Qt.AlignCenter)
                self.routine_table.setItem(row, 1, name_item)

            apply_plain_table_header(self.routine_table)
            self._configure_fixed_fit_columns(
                self.routine_table,
                fixed_widths={0: 54},
                stretch_column=1,
                min_section_width=54,
            )
            self.routine_table.horizontalHeader().setSectionsClickable(True)
            self.routine_table.horizontalHeader().setSortIndicatorShown(True)
            self._apply_saved_routine_sort()
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False

    def stock_from_row(self, row: int) -> tuple[str, str, list[str]] | None:
        code_item = self.stock_table.item(row, 1)
        name_item = self.stock_table.item(row, 2)
        if code_item is None or name_item is None:
            return None

        code = code_item.text().strip()
        name = name_item.text().strip()
        stocks = read_base_stocks()
        stock_by_key = {
            (str(stock.get("code", "")).strip(), str(stock.get("name", "")).strip()): stock
            for stock in stocks
        }
        stock = stock_by_key.get((code, name), {})
        routines_raw = stock.get("routines", [])
        routines = [str(item).strip() for item in routines_raw] if isinstance(routines_raw, list) else []
        return code, name, routines

    def checked_stocks(self) -> list[tuple[str, str, list[str]]]:
        result: list[tuple[str, str, list[str]]] = []
        for row in range(self.stock_table.rowCount()):
            check_item = self.stock_table.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.Checked:
                continue

            stock = self.stock_from_row(row)
            if stock is not None:
                result.append(stock)

        return result

    def checked_stock_common_routine(self) -> str:
        checked = self.checked_stocks()
        if not checked:
            return ""

        common: str | None = None
        for _, _, routines in checked:
            routine_name = routines[0] if routines else ""
            if common is None:
                common = routine_name
            elif common != routine_name:
                return ""
        return common or ""

    def clear_routine_checks(self) -> None:
        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            for row in range(self.routine_table.rowCount()):
                item = self.routine_table.item(row, 0)
                if item is not None:
                    item.setCheckState(Qt.Unchecked)
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False

    def set_checked_routine_by_name(self, routine_name: str) -> None:
        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            for row in range(self.routine_table.rowCount()):
                check_item = self.routine_table.item(row, 0)
                name_item = self.routine_table.item(row, 1)
                if check_item is None or name_item is None:
                    continue
                checked = name_item.text().strip() == routine_name
                check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                if checked:
                    self.routine_table.selectRow(row)
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False
        self.load_selected_routine_stocks()

    def sync_routine_with_checked_stocks(self) -> None:
        """체크된 종목 수만 상태에 표시한다.

        좌측 종목 체크는 루틴 지정 대상 선택만 의미한다.
        우측 루틴 표는 새로 지정할 루틴을 사용자가 직접 선택해야 하므로,
        좌측 종목의 현재 루틴을 자동 체크하지 않는다.
        """
        checked = self.checked_stocks()
        if len(checked) == 1:
            code, name, _ = checked[0]
            self.show_status(f"루틴 지정 대상: {code} {name}")
        elif checked:
            self.show_status(f"루틴 지정 대상: {len(checked)}개")
        else:
            self.show_status("")

    def select_target_stock(self) -> None:
        self.select_target_stocks()

    def select_target_stocks(self) -> None:
        targets = set(self.target_stocks)
        if not targets:
            self.show_status("")
            return

        found_rows: list[int] = []
        found_stocks: list[tuple[str, str, list[str]]] = []
        self._updating_stock_checks = True
        self.stock_table.blockSignals(True)
        try:
            for row in range(self.stock_table.rowCount()):
                stock = self.stock_from_row(row)
                if stock is None:
                    continue
                code, name, _ = stock
                if (code, name) not in targets:
                    continue
                check_item = self.stock_table.item(row, 0)
                if check_item is not None:
                    check_item.setCheckState(Qt.Checked)
                    found_rows.append(row)
                    found_stocks.append(stock)
            self.stock_table.clearSelection()
            for row in found_rows:
                self.stock_table.selectRow(row)
        finally:
            self.stock_table.blockSignals(False)
            self._updating_stock_checks = False

        if not found_rows:
            self.show_status("선택 종목 중 루틴 지정 가능한 종목을 찾지 못했습니다.")
            return

        self.stock_table.scrollToItem(
            self.stock_table.item(found_rows[0], 1),
            QAbstractItemView.PositionAtCenter,
        )
        self.sync_routine_with_checked_stocks()

    def _set_stock_rows_checked(self, rows: set[int], checked: bool) -> None:
        self._updating_stock_checks = True
        self.stock_table.blockSignals(True)
        try:
            for row in rows:
                if row < 0 or row >= self.stock_table.rowCount():
                    continue
                check_item = self.stock_table.item(row, 0)
                if check_item is not None:
                    check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.stock_table.blockSignals(False)
            self._updating_stock_checks = False
        self.sync_routine_with_checked_stocks()

    def _set_all_stock_checks(self, checked: bool) -> None:
        self._set_stock_rows_checked(set(range(self.stock_table.rowCount())), checked)

    def on_stock_selection_changed(self) -> None:
        if self._updating_stock_checks:
            return

        selected_rows = {index.row() for index in self.stock_table.selectionModel().selectedRows()}
        if len(selected_rows) <= 1:
            self._stock_selection_synced = False
            return

        self._stock_selection_synced = True
        self._set_stock_rows_checked(selected_rows, True)

    def show_stock_table_context_menu(self, pos) -> None:
        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_all = menu.addAction("전체 해제")
        selected_action = menu.exec_(self.stock_table.viewport().mapToGlobal(pos))

        if selected_action == action_select_all:
            self._set_all_stock_checks(True)
        elif selected_action == action_clear_all:
            self._set_all_stock_checks(False)

    def on_stock_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.sync_routine_with_checked_stocks()
            return

        if self._stock_selection_synced:
            self._stock_selection_synced = False
            # 드래그/범위 선택 직후 발생하는 클릭 이벤트는 체크 토글로 해석하지 않는다.
            return

        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            selected_rows = {index.row() for index in self.stock_table.selectionModel().selectedRows()}
            if selected_rows:
                self._set_stock_rows_checked(selected_rows, True)
            return

        check_item = self.stock_table.item(item.row(), 0)
        if check_item is None:
            return
        next_state = Qt.Unchecked if check_item.checkState() == Qt.Checked else Qt.Checked
        check_item.setCheckState(next_state)

    def on_stock_check_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_stock_checks or item.column() != 0:
            return
        self.sync_routine_with_checked_stocks()

    def on_routine_check_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_routine_checks or item.column() != 0:
            return

        if item.checkState() != Qt.Checked:
            return

        self._updating_routine_checks = True
        self.routine_table.blockSignals(True)
        try:
            for row in range(self.routine_table.rowCount()):
                check_item = self.routine_table.item(row, 0)
                if check_item is not None and check_item is not item:
                    check_item.setCheckState(Qt.Unchecked)
            self.routine_table.selectRow(item.row())
        finally:
            self.routine_table.blockSignals(False)
            self._updating_routine_checks = False
        self.load_selected_routine_stocks()

    def on_routine_item_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        name_item = self.routine_table.item(row, 1)
        if name_item is None:
            return
        self.set_checked_routine_by_name(name_item.text().strip())

    def checked_routines(self) -> list[tuple[str, Path]]:
        routines: list[tuple[str, Path]] = []
        routine_dir_by_name = {routine_display_name(path): path for path in get_routine_dirs()}

        for row in range(self.routine_table.rowCount()):
            check_item = self.routine_table.item(row, 0)
            routine_item = self.routine_table.item(row, 1)
            if check_item is None or routine_item is None:
                continue

            if check_item.checkState() == Qt.Checked:
                routine_name = routine_item.text().strip()
                routine_dir = routine_dir_by_name.get(routine_name)
                if routine_dir is not None:
                    routines.append((routine_name, routine_dir))

        return routines

    def selected_routine_for_detail(self) -> tuple[str, Path] | None:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        row: int | None = selected_rows[0].row() if len(selected_rows) == 1 else None

        if row is None:
            for target_row in range(self.routine_table.rowCount()):
                check_item = self.routine_table.item(target_row, 0)
                if check_item is not None and check_item.checkState() == Qt.Checked:
                    row = target_row
                    break

        if row is None:
            return None

        routine_item = self.routine_table.item(row, 1)
        if routine_item is None:
            return None

        routine_name = routine_item.text().strip()
        routine_dir_by_name = {routine_display_name(path): path for path in get_routine_dirs()}
        routine_dir = routine_dir_by_name.get(routine_name)
        if routine_dir is None:
            return None

        return routine_name, routine_dir

    def assigned_stock_name_display(self, name: str) -> str:
        """선택 루틴 연결 종목 표의 종목명은 최대 12자까지만 표시한다."""
        clean_name = str(name).strip()
        if len(clean_name) <= 12:
            return clean_name
        return clean_name[:12]

    def load_selected_routine_stocks(self) -> None:
        selected_routine = self.selected_routine_for_detail()
        self.assigned_stock_table.blockSignals(True)
        self.assigned_stock_table.setRowCount(0)

        if selected_routine is None:
            self.assigned_stock_table.blockSignals(False)
            self.btn_unassign.setEnabled(False)
            return

        routine_name, routine_dir = selected_routine
        stocks = read_base_stocks()
        assigned = []

        for stock in stocks:
            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines] if isinstance(routines, list) else []
            if routine_name in routine_list:
                assigned.append(stock)

        self.assigned_stock_table.setRowCount(len(assigned))

        for row, stock in enumerate(assigned):
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            summary = self.runtime_assigned_stock_summary(routine_dir, code, name)
            status = summary["status"]

            check_item = QTableWidgetItem("")
            check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setTextAlignment(Qt.AlignCenter)
            self.assigned_stock_table.setItem(row, 0, check_item)

            display_name = self.assigned_stock_name_display(name)
            values = [
                code,
                display_name,
                summary["operation"],
                status,
                summary["holding_summary"],
                summary["avg_price"],
                summary["current_price"],
                summary["buy_pending_qty"],
                summary["sell_pending_qty"],
                summary["pnl_summary"],
            ]

            for offset, value in enumerate(values, start=1):
                if offset == 4 and value not in ("-", "오류"):
                    try:
                        item = create_auto_trade_status_item(value)
                    except Exception:
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignCenter)
                else:
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)

                if offset == 3:
                    operation_color = summary.get("operation_color", "#000000")
                    item.setForeground(QColor(operation_color))

                if offset == 2 and display_name != name:
                    item.setToolTip(name)

                self.assigned_stock_table.setItem(row, offset, item)

        self._apply_saved_assigned_sort()
        self.assigned_stock_table.blockSignals(False)
        self.btn_unassign.setEnabled(False)

    def runtime_status_text(self, routine_dir: Path, code: str, name: str) -> str:
        return self.runtime_assigned_stock_summary(routine_dir, code, name)["status"]

    def runtime_assigned_stock_summary(self, routine_dir: Path, code: str, name: str) -> dict[str, str]:
        routine_name = routine_display_name(routine_dir)
        stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)

        empty_summary = {
            "operation": "-",
            "operation_color": "#000000",
            "status": "-",
            "holding_qty": "-",
            "holding_summary": "-",
            "avg_price": "-",
            "current_price": "-",
            "buy_pending_qty": "-",
            "sell_pending_qty": "-",
            "pnl_rate": "-",
            "pnl_summary": "-",
        }

        if not state_path.exists():
            return empty_summary

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            error_summary = dict(empty_summary)
            error_summary["operation"] = "오류"
            error_summary["status"] = "오류"
            return error_summary

        try:
            config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        except Exception:
            config = {}

        # 운영 표시 기준:
        # - 텍스트는 수동/시간만 표시한다.
        # - 수동: 보라색
        # - 시간 + 개별시간: 파란색
        # - 시간 + 기본시간: 검정색
        # operation_mode의 원본은 config.json만 사용한다.
        # state.json의 operation_mode는 과거 호환 잔재이므로 표시/판정에 사용하지 않는다.
        raw_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))

        if raw_mode == "CONTINUOUS":
            operation = "수동"
            operation_color = "#8A2BE2"
        else:
            operation = "시간"
            try:
                operation_color = "#0066CC" if schedule_override_enabled(config) else "#000000"
            except Exception:
                operation_color = "#000000"

        raw_status = str(state.get("status", "-")).strip()
        status = display_status_text_for_gui(raw_status)

        # 표시 데이터 출처 통일 원칙:
        # - last_checked_price / last_checked_pnl_rate 는 안정성검사·검토관리용 스냅샷이다.
        # - 루틴지정창의 현재가/수익률 표시에는 사용하지 않는다.
        # - 키움 현재가 연동 전에는 state.json의 공식 current_price 값만 사용하고, 없으면 0 / - 로 표시한다.
        #   이렇게 해야 자동매매설정창과 루틴지정창이 서로 다른 임시/검사값을 보여주지 않는다.
        holding_qty = safe_int_value(state.get("holding_qty", 0))
        avg_price_value = safe_float_value(state.get("avg_price"), 0.0)
        current_price_value = safe_float_value(state.get("current_price"), 0.0)

        if holding_qty > 0 and avg_price_value > 0 and current_price_value > 0:
            pnl_rate = f"{((current_price_value - avg_price_value) / avg_price_value * 100):+.2f}"
        else:
            pnl_rate = "-"

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        buy_pending_text = f"{buy_pending_qty:,}" if isinstance(buy_pending_qty, int) else str(buy_pending_qty)
        sell_pending_text = f"{sell_pending_qty:,}" if isinstance(sell_pending_qty, int) else str(sell_pending_qty)

        holding_summary = f"{holding_qty:,}"

        return {
            "operation": operation,
            "operation_color": operation_color,
            "status": status,
            "holding_qty": str(holding_qty),
            "holding_summary": holding_summary,
            "avg_price": format_number_value(avg_price_value),
            "current_price": format_number_value(current_price_value),
            "buy_pending_qty": buy_pending_text,
            "sell_pending_qty": sell_pending_text,
            "pnl_rate": pnl_rate,
            "pnl_summary": pnl_rate,
        }

    def _checked_assigned_stock_count(self) -> int:
        checked_count = 0
        for row in range(self.assigned_stock_table.rowCount()):
            check_item = self.assigned_stock_table.item(row, 0)
            if check_item is not None and check_item.checkState() == Qt.Checked:
                checked_count += 1
        return checked_count

    def _set_assigned_rows_checked(self, rows: set[int], checked: bool) -> None:
        self._updating_assigned_checks = True
        self.assigned_stock_table.blockSignals(True)
        try:
            for row in rows:
                if row < 0 or row >= self.assigned_stock_table.rowCount():
                    continue
                check_item = self.assigned_stock_table.item(row, 0)
                if check_item is not None:
                    check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.assigned_stock_table.blockSignals(False)
            self._updating_assigned_checks = False
        self.btn_unassign.setEnabled(self._checked_assigned_stock_count() > 0)

    def _set_all_assigned_checks(self, checked: bool) -> None:
        self._set_assigned_rows_checked(set(range(self.assigned_stock_table.rowCount())), checked)

    def on_assigned_stock_check_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_assigned_checks or item.column() != 0:
            return
        self.btn_unassign.setEnabled(self._checked_assigned_stock_count() > 0)

    def on_assigned_stock_selection_changed(self) -> None:
        if self._updating_assigned_checks:
            return

        selected_rows = {index.row() for index in self.assigned_stock_table.selectionModel().selectedRows()}
        if len(selected_rows) <= 1:
            self._assigned_selection_synced = False
            return

        self._assigned_selection_synced = True
        self._set_assigned_rows_checked(selected_rows, True)

    def show_assigned_stock_table_context_menu(self, pos) -> None:
        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_all = menu.addAction("전체 해제")
        selected_action = menu.exec_(self.assigned_stock_table.viewport().mapToGlobal(pos))

        if selected_action == action_select_all:
            self._set_all_assigned_checks(True)
        elif selected_action == action_clear_all:
            self._set_all_assigned_checks(False)

    def on_assigned_stock_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.btn_unassign.setEnabled(self._checked_assigned_stock_count() > 0)
            return

        if self._assigned_selection_synced:
            self._assigned_selection_synced = False
            # 드래그/범위 선택 직후 발생하는 클릭 이벤트는 체크 토글로 해석하지 않는다.
            return

        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            selected_rows = {index.row() for index in self.assigned_stock_table.selectionModel().selectedRows()}
            if selected_rows:
                self._set_assigned_rows_checked(selected_rows, True)
            return

        check_item = self.assigned_stock_table.item(item.row(), 0)
        if check_item is None:
            return

        next_state = Qt.Unchecked if check_item.checkState() == Qt.Checked else Qt.Checked
        check_item.setCheckState(next_state)

    def apply_routines_to_checked_stocks(self) -> None:
        selected = self.checked_stocks()
        if not selected:
            self.show_status("루틴을 지정할 종목을 체크하세요.")
            return

        selected_routines = self.checked_routines()
        if not selected_routines:
            self.show_status("지정할 루틴을 체크하세요.")
            return

        if len(selected_routines) != 1:
            self.show_status("지정할 루틴은 1개만 선택하세요.")
            return

        selected_routine_name, selected_routine_dir = selected_routines[0]
        selected_routine_names = [selected_routine_name]
        applied_items: list[str] = []
        created_paths: list[str] = []
        blocked_items: list[dict[str, object]] = []
        skipped_items: list[str] = []

        for code, name, _ in selected:
            can_process, guard_info = routine_action_reasons_for_stock(code, name, allow_unassigned=True)
            if not can_process:
                blocked_items.append(guard_info)
                continue

            if not is_valid_stock_code(code):
                skipped_items.append(f"{code} {name}: 종목코드 오류")
                continue

            library_stock = find_library_stock_by_code(code)
            if library_stock is None or library_stock.get("name", "").strip() != name:
                skipped_items.append(f"{code} {name}: 라이브러리 불일치")
                continue

            final_routines = selected_routine_names

            if not update_base_stock_routines(code, name, final_routines):
                skipped_items.append(f"{code} {name}: 기초종목.txt 갱신 실패")
                continue

            stock_dir = stock_runtime_dir_for_routine(selected_routine_name, code, name)
            if stock_dir is not None:
                created_paths.append(str(stock_dir.relative_to(PROJECT_ROOT)))
            else:
                created_paths.append(f"stocks/{code}_{name}")
            ensure_single_real_trade_routine_for_stock(code, name, selected_routine_name)
            applied_items.append(f"{code},{name}({selected_routine_name})")

        report_path = write_blocked_action_report(
            "루틴 지정",
            blocked_items,
            target_routine=selected_routine_name,
        )

        if not applied_items:
            message = "루틴을 지정한 종목이 없습니다."
            if blocked_items:
                message += f"\n\n처리 불가: {len(blocked_items)}개"
                if report_path is not None:
                    message += f"\n리포트: {report_path}"
            if skipped_items:
                message += f"\n처리 제외: {len(skipped_items)}개"
            QMessageBox.information(self, "루틴 지정 결과", message)
            self.show_status("루틴을 지정한 종목이 없습니다.")
            return

        append_changelog(
            "UPDATE",
            "기초종목.txt",
            f"매매루틴 지정: {' / '.join(applied_items)} -> {', '.join(selected_routine_names)}",
        )

        if created_paths:
            append_changelog(
                "ADD",
                "종목별 저장 구조",
                f"종목 폴더 및 기본 파일 확인/생성: {' / '.join(created_paths)}",
            )

        self.load_stock_table()
        self.load_selected_routine_stocks()
        self.clear_routine_checks()

        parent = self.parent()
        if isinstance(parent, StockRegisterWindow):
            parent.refresh_stock_table()
            main_window = parent.parent()
            if isinstance(main_window, MainWindow):
                main_window.refresh_all()

        result_lines = [
            f"{len(applied_items)}개 종목이 {selected_routine_name}에 연결되었습니다."
        ]
        if blocked_items:
            result_lines.append(f"처리 불가: {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        if skipped_items:
            result_lines.append(f"처리 제외: {len(skipped_items)}개")

        QMessageBox.information(self, "루틴 지정 결과", "\n".join(result_lines))
        self.show_status(
            f"{len(applied_items)}개 종목이 {selected_routine_name}에 연결되었습니다."
        )

    def unassign_checked_stocks_from_selected_routine(self) -> None:
        selected_routine = self.selected_routine_for_detail()
        if selected_routine is None:
            self.show_status("해제할 루틴을 선택하세요.")
            return

        routine_name, _ = selected_routine
        checked_stocks: list[tuple[str, str]] = []

        for row in range(self.assigned_stock_table.rowCount()):
            check_item = self.assigned_stock_table.item(row, 0)
            code_item = self.assigned_stock_table.item(row, 1)
            name_item = self.assigned_stock_table.item(row, 2)
            if check_item is None or code_item is None or name_item is None:
                continue
            if check_item.checkState() == Qt.Checked:
                checked_stocks.append((code_item.text().strip(), name_item.text().strip()))

        if not checked_stocks:
            self.show_status("루틴 해제할 종목을 체크하세요.")
            return

        stock_lookup = {
            (str(stock.get("code", "")).strip(), str(stock.get("name", "")).strip()): stock
            for stock in read_base_stocks()
        }

        removable_items: list[tuple[str, str]] = []
        blocked_items: list[dict[str, object]] = []
        skipped_items: list[str] = []

        for code, name in checked_stocks:
            stock = stock_lookup.get((code, name))
            if not stock:
                skipped_items.append(f"{code} {name}: 기초종목.txt에서 종목을 찾지 못했습니다.")
                continue

            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines] if isinstance(routines, list) else []
            if routine_name not in routine_list:
                skipped_items.append(f"{code} {name}: 선택 루틴에 연결되어 있지 않음")
                continue

            can_process, guard_info = routine_action_reasons_for_stock(code, name, allow_unassigned=False)
            if not can_process:
                blocked_items.append(guard_info)
                continue

            removable_items.append((code, name))

        if not removable_items and not blocked_items:
            QMessageBox.information(
                self,
                "루틴 해제 결과",
                "루틴 해제할 수 있는 종목이 없습니다."
                + (f"\n처리 제외: {len(skipped_items)}개" if skipped_items else ""),
            )
            self.show_status("루틴 해제할 수 있는 종목이 없습니다.")
            return

        confirm_dialog = RoutineUnassignConfirmDialog(
            routine_name=routine_name,
            removable_items=removable_items,
            blocked_items=blocked_items,
            parent=self,
        )
        if confirm_dialog.exec_() != QDialog.Accepted:
            self.show_status("루틴 해제를 취소했습니다.")
            return

        removed_items: list[str] = []
        for code, name in removable_items:
            stock = stock_lookup.get((code, name))
            if not stock:
                skipped_items.append(f"{code} {name}: 기초종목.txt에서 종목을 찾지 못했습니다.")
                continue

            routines = stock.get("routines", [])
            routine_list = [str(item).strip() for item in routines] if isinstance(routines, list) else []
            new_routines = [item for item in routine_list if item != routine_name]

            if update_base_stock_routines(code, name, new_routines):
                ensure_single_real_trade_routine_for_stock(code, name)
                removed_items.append(f"{code},{name}")
            else:
                skipped_items.append(f"{code} {name}: 기초종목.txt 갱신 실패")

        report_path = write_blocked_action_report(
            "루틴 해제",
            blocked_items,
            target_routine=routine_name,
        )

        if removed_items:
            append_changelog(
                "UPDATE",
                "기초종목.txt",
                f"매매루틴 해제: {routine_name} -> {' / '.join(removed_items)}",
            )

        self.load_stock_table()
        self.load_selected_routine_stocks()
        self.clear_routine_checks()

        parent = self.parent()
        if isinstance(parent, StockRegisterWindow):
            parent.refresh_stock_table()
            main_window = parent.parent()
            if isinstance(main_window, MainWindow):
                main_window.refresh_all()

        result_lines = [
            f"{len(removed_items)}개 종목의 {routine_name} 연결이 해제되었습니다."
        ]
        if blocked_items:
            result_lines.append(f"해제 불가: {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        if skipped_items:
            result_lines.append(f"처리 제외: {len(skipped_items)}개")

        QMessageBox.information(self, "루틴 해제 결과", "\n".join(result_lines))
        self.show_status(
            f"{len(removed_items)}개 종목의 {routine_name} 연결이 해제되었습니다."
        )


def create_auto_trade_status_item(display_status: str) -> QTableWidgetItem:
    """
    상태 컬럼 표시용 아이템.
    내부 상태코드는 GUI 표시명으로 변환해 보여준다.
    SELL_ONLY도 화면에서는 감시/매도로 표시한다.
    """
    normalized_status = display_status_text_for_gui(display_status)

    item = SortableTableWidgetItem(f"{auto_trade_status_dot(normalized_status)} {normalized_status}")
    item.setToolTip(normalized_status)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setForeground(QColor(auto_trade_status_color(normalized_status)))
    return item

def yes_no_display(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"

    text_value = str(value).strip().lower()
    if text_value in ("true", "1", "yes", "y"):
        return "예"

    return "아니오"



def collect_global_review_required_rows() -> list[dict[str, object]]:
    """
    프로그램 전체 단위 검토관리 대상 목록을 수집한다.

    정책:
    - 검토관리창은 루틴별 창이 아니다.
    - 전체 루틴의 전체 종목 중 REVIEW_REQUIRED 상태만 통합 표시한다.
    - 과거 루틴 폴더에 남은 종목도 상태가 검토종목이면 표시해 운영자가 놓치지 않게 한다.
    """
    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for routine_dir in get_routine_dirs():
        routine_name = routine_display_name(routine_dir)
        for stock_dir in get_stock_dirs_in_routine(routine_dir):
            code, name = parse_stock_folder_name(stock_dir.name)
            state = read_json_dict(stock_dir / "state.json")
            raw_status = str(state.get("status", "STOPPED")).strip().upper()
            display_status = display_status_text_for_gui(raw_status)
            review_required = bool(state.get("review_required", False))

            if raw_status != "REVIEW_REQUIRED" and display_status != "검토종목" and not review_required:
                continue

            key = (routine_name, code, name)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            rows.append(
                {
                    "routine_name": routine_name,
                    "stock_dir": stock_dir,
                    "code": code,
                    "name": name,
                    "status": display_status,
                    "review_location": str(state.get("review_location", "") or "-").strip() or "-",
                    "review_reason": str(state.get("review_reason", "") or state.get("review_detail", "") or "-").strip() or "-",
                    "review_entered_at": str(state.get("review_entered_at", "") or state.get("review_checked_at", "") or "-").strip() or "-",
                    "review_status": str(state.get("review_status", "") or "PENDING").strip() or "PENDING",
                }
            )

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
        self.btn_refresh = QPushButton("새로고침")
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._connect_events()
        self.load_review_items()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.addWidget(self.summary_label)

        headers = [
            "종목코드",
            "종목명",
            "현재루틴",
            "현재상태",
            "검토위치",
            "검토원인",
            "발생시간",
            "처리상태",
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.table)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 130)
        self.table.setColumnWidth(5, 260)
        self.table.setColumnWidth(6, 160)
        self.table.setColumnWidth(7, 100)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_refresh.setMinimumWidth(100)
        self.btn_close.setMinimumWidth(100)
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _connect_events(self) -> None:
        self.btn_refresh.clicked.connect(self.load_review_items)
        self.btn_close.clicked.connect(self.close)

    def _set_item(self, row: int, col: int, text: object, align=Qt.AlignCenter) -> None:
        item = QTableWidgetItem(str(text if text is not None else "-"))
        item.setTextAlignment(align)
        self.table.setItem(row, col, item)

    def load_review_items(self) -> None:
        rows = collect_global_review_required_rows()
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            self._set_item(row_index, 0, row.get("code", "-"))
            self._set_item(row_index, 1, row.get("name", "-"), Qt.AlignLeft | Qt.AlignVCenter)
            self._set_item(row_index, 2, row.get("routine_name", "-"), Qt.AlignLeft | Qt.AlignVCenter)

            status_item = QTableWidgetItem(str(row.get("status", "검토종목")))
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QColor(auto_trade_status_color("검토종목")))
            self.table.setItem(row_index, 3, status_item)

            self._set_item(row_index, 4, row.get("review_location", "-"), Qt.AlignLeft | Qt.AlignVCenter)
            self._set_item(row_index, 5, row.get("review_reason", "-"), Qt.AlignLeft | Qt.AlignVCenter)
            self._set_item(row_index, 6, row.get("review_entered_at", "-"))
            self._set_item(row_index, 7, row.get("review_status", "PENDING"))

        self.summary_label.setText(f"검토종목: {len(rows)}개")




def default_operation_policy() -> dict[str, object]:
    """운영환경설정 기본값.

    현재 단계에서는 UI/저장 구조를 먼저 확정한다.
    실제 자동판정 엔진 연결은 후속 패치에서 단계적으로 반영한다.
    """
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {"enabled": False, "name": "추가시간1", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": False, "name": "추가시간2", "start_time": "15:40:00", "end_time": "19:50:00"},
            {"enabled": False, "name": "추가시간3", "start_time": "", "end_time": ""},
        ],
        "scheduled_operation": {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
            "after_buy_end_status": "감시/매도",
        },
        "manual_operation": {
            "use_regular_market": True,
            "use_extra_session_1": False,
            "use_extra_session_2": False,
            "use_extra_session_3": False,
            "enabled_status": "매수/매도",
            "disabled_status": "감시/대기",
            "use_liquidation_policy": False,
        },
        "auto_close": {
            "method": "루틴매도신호",
            "profit_percent": "",
            "loss_percent": "",
        },
        "early_close": {
            "method": "시장가",
            "profit_percent": "",
            "loss_percent": "",
        },
        "liquidation": {
            "minutes_before_regular_close": "5",
            "method": "이월",
        },
        "updated_at": "",
    }


def read_operation_policy() -> dict[str, object]:
    default = default_operation_policy()
    if not OPERATION_POLICY_PATH.exists():
        return default
    try:
        data = json.loads(OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default

    # 얕은 병합: 누락된 상위 항목은 기본값으로 보완한다.
    merged = default_operation_policy()
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)  # type: ignore[index]
        else:
            merged[key] = value
    return merged


def write_operation_policy(policy: dict[str, object]) -> None:
    policy = dict(policy)
    policy["updated_at"] = now_text()
    OPERATION_POLICY_PATH.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )



class TimeComboWidget(QWidget):
    """시/분 콤보박스로 시간을 선택하는 작은 위젯."""

    def __init__(self, default_time: str = "09:00:00", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hour_combo = QComboBox()
        self.minute_combo = QComboBox()
        self.hour_combo.addItems([f"{hour:02d}" for hour in range(24)])
        self.minute_combo.addItems([f"{minute:02d}" for minute in range(0, 60, 5)])
        self.hour_combo.setFixedWidth(68)
        self.minute_combo.setFixedWidth(68)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.hour_combo)
        layout.addWidget(QLabel("시"))
        layout.addWidget(self.minute_combo)
        layout.addWidget(QLabel("분"))
        self.setLayout(layout)
        self.set_time(default_time, default_time)

    def set_time(self, value: object, default_time: str = "09:00:00") -> None:
        normalized = normalized_hhmmss_or_empty(value) or normalized_hhmmss_or_empty(default_time) or "09:00:00"
        try:
            hour, minute, _second = [int(part) for part in normalized.split(":")]
        except Exception:
            hour, minute = 9, 0
        rounded_minute = int(minute / 5) * 5
        self.hour_combo.setCurrentText(f"{hour:02d}")
        self.minute_combo.setCurrentText(f"{rounded_minute:02d}")

    def time_text(self) -> str:
        return f"{int(self.hour_combo.currentText()):02d}:{int(self.minute_combo.currentText()):02d}:00"

class OperationEnvironmentSettingsDialog(QDialog):
    """스케줄매매관리 대체용 운영환경설정 UI.

    환경설정은 전체 기본값이며, 개별 종목 예외는 종목 우클릭 설정에서 처리한다.
    """

    CLOSE_METHODS = ["루틴매도신호", "시장가", "현재가", "익절/손절"]
    LIQUIDATION_METHODS = ["이월", "시장가", "현재가"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("환경설정")
        self.setStyleSheet("""
            QDialog, QWidget, QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton {
                font-family: '맑은 고딕';
                font-size: 9pt;
            }
            QGroupBox {
                font-family: '맑은 고딕';
                font-size: 10pt;
                font-weight: bold;
            }
            QComboBox {
                min-height: 24px;
            }
            QLineEdit {
                min-height: 24px;
            }
            QPushButton {
                min-height: 28px;
                min-width: 82px;
            }
        """)
        self.resize(1180, 720)
        self.policy = read_operation_policy()
        self.setStyleSheet(
            "QGroupBox { font-size: 15px; font-weight: bold; margin-top: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }"
            "QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton { font-size: 9pt; }"
            "QComboBox, QLineEdit { min-height: 30px; }"
        )

        self.regular_start = self._make_time_edit("09:00:00")
        self.regular_end = self._make_time_edit("15:20:00")
        self.scheduled_start = self._make_time_edit("09:00:00")
        self.scheduled_end_buy = self._make_time_edit("13:30:00")
        self.scheduled_after_status = QComboBox()
        self.scheduled_after_status.addItems(["감시/매도", "감시/대기"])
        self.scheduled_after_status.setMinimumWidth(110)

        self.extra_name: list[QLineEdit] = []
        self.extra_start: list[TimeComboWidget] = []
        self.extra_end: list[TimeComboWidget] = []

        self.manual_use_regular = QCheckBox("정규장 사용")
        self.manual_extra_checks = [QCheckBox(f"추가{i}") for i in range(1, 4)]
        self.manual_liquidation = QCheckBox("청산정책 적용용")

        
        self.auto_close_method = QComboBox()
        self.auto_close_method.addItems(["루틴매도신호", "시장가", "현재가", "익절/손절"])
        self.auto_close_method.setVisible(False)
        self.auto_close_signal = QCheckBox("루틴매도신호")
        self.auto_close_market = QCheckBox("시장가")
        self.auto_close_current = QCheckBox("현재가")
        self.auto_close_profit_loss = QCheckBox("익절/손절")
        self.auto_close_signal.setChecked(True)
        self.auto_close_options = [
            self.auto_close_signal,
            self.auto_close_market,
            self.auto_close_current,
            self.auto_close_profit_loss,
        ]
        for _cb in self.auto_close_options:
            _cb.setMinimumWidth(92)

        self.auto_close_method.setMinimumWidth(150)
        self.auto_close_method.addItems(self.CLOSE_METHODS)
        self.auto_close_method.setMinimumWidth(145)
        self.auto_profit = self._make_short_line()
        self.auto_loss = self._make_short_line()

        
        self.early_close_method = QComboBox()
        self.early_close_method.addItems(["루틴매도신호", "시장가", "현재가", "익절/손절"])
        self.early_close_method.setVisible(False)
        self.early_close_signal = QCheckBox("루틴매도신호")
        self.early_close_market = QCheckBox("시장가")
        self.early_close_current = QCheckBox("현재가")
        self.early_close_profit_loss = QCheckBox("익절/손절")
        self.early_close_market.setChecked(True)
        self.early_close_options = [
            self.early_close_signal,
            self.early_close_market,
            self.early_close_current,
            self.early_close_profit_loss,
        ]
        for _cb in self.early_close_options:
            _cb.setMinimumWidth(92)

        self.early_close_method.setMinimumWidth(150)
        self.early_close_method.addItems(self.CLOSE_METHODS)
        self.early_close_method.setMinimumWidth(145)
        self.early_profit = self._make_short_line()
        self.early_loss = self._make_short_line()

        self.liquidation_minutes = self._make_short_line("5")
        self.liquidation_checks: dict[str, QCheckBox] = {
            name: QCheckBox(name) for name in self.LIQUIDATION_METHODS
        }
        for checkbox in self.liquidation_checks.values():
            checkbox.clicked.connect(lambda _checked=False, cb=checkbox: self._select_liquidation_method(cb))

        self._setup_ui()
        self.load_policy_to_widgets()

    def _make_short_line(self, default: str = "") -> QLineEdit:
        line = QLineEdit(default)
        line.setMinimumWidth(70)
        return line


    def _make_time_edit(self, default_time: str) -> TimeComboWidget:
        return TimeComboWidget(default_time)

    def _set_time_edit(self, edit: TimeComboWidget, value: object, default_time: str) -> None:
        edit.set_time(value, default_time)

    def _time_edit_text(self, edit: TimeComboWidget) -> str:
        return edit.time_text()

    def _select_liquidation_method(self, selected: QCheckBox) -> None:
        for checkbox in self.liquidation_checks.values():
            checkbox.setChecked(checkbox is selected)

    def _current_liquidation_method(self) -> str:
        for name, checkbox in self.liquidation_checks.items():
            if checkbox.isChecked():
                return name
        return "이월"


    def _connect_close_option_checks(self) -> None:
        """마감 방식 체크박스는 보기 형태는 체크박스지만 실제 선택은 1개만 허용한다."""
        def bind(options):
            for cb in options:
                cb.clicked.connect(lambda checked, current=cb, all_options=options: self._exclusive_close_option(current, all_options))

        if hasattr(self, "auto_close_options"):
            bind(self.auto_close_options)
        if hasattr(self, "early_close_options"):
            bind(self.early_close_options)

    def _exclusive_close_option(self, current: QCheckBox, options: list[QCheckBox]) -> None:
        for cb in options:
            cb.setChecked(cb is current)
        current.setChecked(True)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(18, 14, 18, 14)

        # 1. 운영시간 설정
        operation_time_box = QGroupBox("1. 운영시간 설정")
        operation_time_layout = QGridLayout()
        operation_time_layout.setContentsMargins(16, 18, 16, 12)
        operation_time_layout.setHorizontalSpacing(18)
        operation_time_layout.setVerticalSpacing(8)

        regular_label = QLabel("정규장")
        regular_label.setStyleSheet("font-weight: bold;")
        operation_time_layout.addWidget(regular_label, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        operation_time_layout.addWidget(QLabel("시작"), 0, 1, Qt.AlignRight | Qt.AlignVCenter)
        operation_time_layout.addWidget(self.regular_start, 0, 2, Qt.AlignLeft | Qt.AlignVCenter)
        operation_time_layout.addWidget(QLabel("종료"), 0, 4, Qt.AlignRight | Qt.AlignVCenter)
        operation_time_layout.addWidget(self.regular_end, 0, 5, Qt.AlignLeft | Qt.AlignVCenter)

        extra_title = QLabel("추가 거래시간")
        extra_title.setStyleSheet("font-weight: bold;")
        operation_time_layout.addWidget(extra_title, 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
        headers = [
            (QLabel("구간명"), 1, 1, 1, 2),
            (QLabel("시작"), 1, 3, 1, 1),
            (QLabel("종료"), 1, 5, 1, 1),
        ]
        for header, row, col, rowspan, colspan in headers:
            header.setStyleSheet("font-weight: bold; color: #333333;")
            operation_time_layout.addWidget(header, row, col, rowspan, colspan, Qt.AlignCenter)

        for index in range(3):
            name = QLineEdit()
            name.setFixedWidth(190)
            start = self._make_time_edit("09:00:00")
            end = self._make_time_edit("15:20:00")
            self.extra_name.append(name)
            self.extra_start.append(start)
            self.extra_end.append(end)
            row = index + 2
            row_label = QLabel(f"추가{index + 1}")
            row_label.setStyleSheet("font-weight: bold;")
            operation_time_layout.addWidget(row_label, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
            operation_time_layout.addWidget(name, row, 1, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
            operation_time_layout.addWidget(start, row, 3, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
            operation_time_layout.addWidget(end, row, 5, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        operation_time_layout.setColumnMinimumWidth(0, 95)
        operation_time_layout.setColumnMinimumWidth(1, 60)
        operation_time_layout.setColumnMinimumWidth(2, 210)
        operation_time_layout.setColumnMinimumWidth(3, 120)
        operation_time_layout.setColumnMinimumWidth(4, 125)
        operation_time_layout.setColumnMinimumWidth(5, 120)
        operation_time_layout.setColumnMinimumWidth(6, 125)
        operation_time_layout.setColumnStretch(7, 1)
        operation_time_box.setLayout(operation_time_layout)
        layout.addWidget(operation_time_box)

        # 2. 시간운영 기본설정
        scheduled_box = QGroupBox("2. 시간운영 기본설정")
        scheduled_layout = QHBoxLayout()
        scheduled_layout.setContentsMargins(16, 12, 16, 10)
        scheduled_layout.setSpacing(14)
        scheduled_layout.addWidget(QLabel("기본 시작"))
        scheduled_layout.addWidget(self.scheduled_start)
        scheduled_layout.addSpacing(22)
        scheduled_layout.addWidget(QLabel("기본 매수종료"))
        scheduled_layout.addWidget(self.scheduled_end_buy)
        scheduled_layout.addSpacing(22)
        scheduled_layout.addWidget(QLabel("매수종료 후"))
        scheduled_layout.addWidget(self.scheduled_after_status)
        scheduled_layout.addStretch(1)
        scheduled_box.setLayout(scheduled_layout)
        layout.addWidget(scheduled_box)

        # 3. 수동운영 기본설정
        manual_box = QGroupBox("3. 수동운영 기본설정")
        manual_layout = QHBoxLayout()
        manual_layout.setContentsMargins(16, 12, 16, 10)
        manual_layout.setSpacing(14)
        manual_layout.addWidget(QLabel("사용시간"))
        self.manual_use_regular.setText("정규장")
        self.manual_use_regular.setFixedWidth(86)
        manual_layout.addWidget(self.manual_use_regular)
        for index, checkbox in enumerate(self.manual_extra_checks, start=1):
            checkbox.setText(f"추가{index}")
            checkbox.setFixedWidth(82)
            manual_layout.addWidget(checkbox)
        self.manual_liquidation.setText("청산정책 적용용")
        self.manual_liquidation.setMinimumWidth(150)
        manual_layout.addWidget(self.manual_liquidation)
        manual_layout.addStretch(1)
        manual_box.setLayout(manual_layout)
        layout.addWidget(manual_box)

        # 4, 5. 자동마감 / 조기마감 설정
        close_pair_layout = QHBoxLayout()
        close_pair_layout.setSpacing(14)

        auto_box = QGroupBox("4. 자동마감 설정")
        auto_layout = QGridLayout()
        auto_layout.setContentsMargins(16, 14, 16, 14)
        auto_layout.setHorizontalSpacing(12)
        auto_layout.setVerticalSpacing(8)
        auto_layout.addWidget(QLabel("방식"), 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_close_method, 0, 1)
        auto_layout.addWidget(QLabel("익절%"), 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_profit, 0, 3)
        auto_layout.addWidget(QLabel("손절%"), 0, 4, Qt.AlignRight | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_loss, 0, 5)
        auto_layout.setColumnStretch(6, 1)
        auto_box.setLayout(auto_layout)
        close_pair_layout.addWidget(auto_box, 1)

        early_box = QGroupBox("5. 조기마감 설정")
        early_layout = QGridLayout()
        early_layout.setContentsMargins(16, 14, 16, 14)
        early_layout.setHorizontalSpacing(12)
        early_layout.setVerticalSpacing(8)
        early_layout.addWidget(QLabel("방식"), 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        early_layout.addWidget(self.early_close_method, 0, 1)
        early_layout.addWidget(QLabel("익절%"), 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        early_layout.addWidget(self.early_profit, 0, 3)
        early_layout.addWidget(QLabel("손절%"), 0, 4, Qt.AlignRight | Qt.AlignVCenter)
        early_layout.addWidget(self.early_loss, 0, 5)
        early_layout.setColumnStretch(6, 1)
        early_box.setLayout(early_layout)
        close_pair_layout.addWidget(early_box, 1)
        layout.addLayout(close_pair_layout)

        # 6. 청산설정
        liquidation_box = QGroupBox("6. 청산설정")
        liquidation_layout = QHBoxLayout()
        liquidation_layout.setContentsMargins(16, 12, 16, 12)
        liquidation_layout.setSpacing(14)
        liquidation_layout.addWidget(QLabel("정규장 종료"))
        self.liquidation_minutes.setFixedWidth(60)
        liquidation_layout.addWidget(self.liquidation_minutes)
        liquidation_layout.addWidget(QLabel("분전"))
        liquidation_layout.addSpacing(24)
        liquidation_layout.addWidget(QLabel("처리방식"))
        for name in self.LIQUIDATION_METHODS:
            self.liquidation_checks[name].setFixedWidth(82)
            liquidation_layout.addWidget(self.liquidation_checks[name])
        liquidation_layout.addStretch(1)
        liquidation_box.setLayout(liquidation_layout)
        layout.addWidget(liquidation_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.button(QDialogButtonBox.Save).setMinimumWidth(110)
        buttons.button(QDialogButtonBox.Cancel).setMinimumWidth(110)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def load_policy_to_widgets(self) -> None:
        regular = self.policy.get("regular_market", {}) if isinstance(self.policy.get("regular_market"), dict) else {}
        self._set_time_edit(self.regular_start, regular.get("start_time", "09:00:00"), "09:00:00")
        self._set_time_edit(self.regular_end, regular.get("end_time", "15:20:00"), "15:20:00")

        extra_sessions = self.policy.get("extra_sessions", [])
        if not isinstance(extra_sessions, list):
            extra_sessions = []
        for index in range(3):
            item = extra_sessions[index] if index < len(extra_sessions) and isinstance(extra_sessions[index], dict) else {}
            self.extra_name[index].setText(str(item.get("name", f"추가시간{index + 1}")))
            self._set_time_edit(self.extra_start[index], item.get("start_time", "09:00:00"), "09:00:00")
            self._set_time_edit(self.extra_end[index], item.get("end_time", "15:20:00"), "15:20:00")

        scheduled = self.policy.get("scheduled_operation", {}) if isinstance(self.policy.get("scheduled_operation"), dict) else {}
        self._set_time_edit(self.scheduled_start, scheduled.get("default_start_time", "09:00:00"), "09:00:00")
        self._set_time_edit(self.scheduled_end_buy, scheduled.get("default_end_buy_time", "13:30:00"), "13:30:00")
        self.scheduled_after_status.setCurrentText(str(scheduled.get("after_buy_end_status", "감시/매도")))

        manual = self.policy.get("manual_operation", {}) if isinstance(self.policy.get("manual_operation"), dict) else {}
        self.manual_use_regular.setChecked(bool(manual.get("use_regular_market", True)))
        for index, checkbox in enumerate(self.manual_extra_checks, start=1):
            checkbox.setChecked(bool(manual.get(f"use_extra_session_{index}", False)))
        self.manual_liquidation.setChecked(bool(manual.get("use_liquidation_policy", False)))

        auto = self.policy.get("auto_close", {}) if isinstance(self.policy.get("auto_close"), dict) else {}
        self.auto_close_method.setCurrentText(str(auto.get("method", "루틴매도신호")))
        self.auto_profit.setText(str(auto.get("profit_percent", "")))
        self.auto_loss.setText(str(auto.get("loss_percent", "")))

        early = self.policy.get("early_close", {}) if isinstance(self.policy.get("early_close"), dict) else {}
        self.early_close_method.setCurrentText(str(early.get("method", "시장가")))
        self.early_profit.setText(str(early.get("profit_percent", "")))
        self.early_loss.setText(str(early.get("loss_percent", "")))

        liquidation = self.policy.get("liquidation", {}) if isinstance(self.policy.get("liquidation"), dict) else {}
        self.liquidation_minutes.setText(str(liquidation.get("minutes_before_regular_close", "5")))
        method = str(liquidation.get("method", "이월"))
        if method not in self.liquidation_checks:
            method = "이월"
        self._select_liquidation_method(self.liquidation_checks[method])

        self._sync_combo_to_close_checkboxes()

    def build_policy_from_widgets(self) -> dict[str, object]:
        return {
            "regular_market": {
                "start_time": self._time_edit_text(self.regular_start),
                "end_time": self._time_edit_text(self.regular_end),
            },
            "extra_sessions": [
                {
                    "name": self.extra_name[index].text().strip() or f"추가시간{index + 1}",
                    "start_time": self._time_edit_text(self.extra_start[index]),
                    "end_time": self._time_edit_text(self.extra_end[index]),
                }
                for index in range(3)
            ],
            "scheduled_operation": {
                "default_start_time": self._time_edit_text(self.scheduled_start),
                "default_end_buy_time": self._time_edit_text(self.scheduled_end_buy),
                "after_buy_end_status": self.scheduled_after_status.currentText(),
            },
            "manual_operation": {
                "use_regular_market": self.manual_use_regular.isChecked(),
                "use_extra_session_1": self.manual_extra_checks[0].isChecked(),
                "use_extra_session_2": self.manual_extra_checks[1].isChecked(),
                "use_extra_session_3": self.manual_extra_checks[2].isChecked(),
                "enabled_status": "매수/매도",
                "disabled_status": "감시/대기",
                "use_liquidation_policy": self.manual_liquidation.isChecked(),
            },
            "auto_close": {
                "method": self.auto_close_method.currentText(),
                "profit_percent": self.auto_profit.text().strip(),
                "loss_percent": self.auto_loss.text().strip(),
            },
            "early_close": {
                "method": self.early_close_method.currentText(),
                "profit_percent": self.early_profit.text().strip(),
                "loss_percent": self.early_loss.text().strip(),
            },
            "liquidation": {
                "minutes_before_regular_close": self.liquidation_minutes.text().strip(),
                "method": self._current_liquidation_method(),
            },
        }



    def _sync_combo_to_close_checkboxes(self) -> None:
        """기존 저장 콤보값을 체크박스 표시에 반영한다."""
        def apply(combo, options):
            idx = combo.currentIndex() if hasattr(combo, "currentIndex") else 0
            if idx < 0 or idx >= len(options):
                idx = 0
            for i, cb in enumerate(options):
                cb.setChecked(i == idx)

        if hasattr(self, "auto_close_options") and hasattr(self, "auto_close_method"):
            apply(self.auto_close_method, self.auto_close_options)
        if hasattr(self, "early_close_options") and hasattr(self, "early_close_method"):
            apply(self.early_close_method, self.early_close_options)

    def _sync_close_checkboxes_to_combo(self) -> None:
        """자동마감/조기마감 체크박스 표시값을 기존 저장 콤보값으로 동기화한다."""
        def choose(options, combo, default_index=0):
            checked_indexes = [idx for idx, cb in enumerate(options) if cb.isChecked()]
            if not checked_indexes:
                options[default_index].setChecked(True)
                checked_indexes = [default_index]
            selected = checked_indexes[0]
            for idx, cb in enumerate(options):
                cb.setChecked(idx == selected)
            if hasattr(combo, "setCurrentIndex"):
                combo.setCurrentIndex(selected)

        if hasattr(self, "auto_close_options") and hasattr(self, "auto_close_method"):
            choose(self.auto_close_options, self.auto_close_method, 0)
        if hasattr(self, "early_close_options") and hasattr(self, "early_close_method"):
            choose(self.early_close_options, self.early_close_method, 1)

    def accept(self) -> None:
        self._sync_close_checkboxes_to_combo()
        policy = self.build_policy_from_widgets()
        try:
            write_operation_policy(policy)
            append_changelog("UPDATE", "operation_policy.json", "환경설정 저장")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"환경설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "저장 완료", "환경설정을 저장했습니다.")
        super().accept()


class StockPolicyOverrideDialog(QDialog):
    """개별종목 예외설정 1차 UI.

    환경설정이 디폴트이고, 이 창은 해당 종목만 예외로 둘 때 사용한다.
    전체 리셋은 종목별 예외 설정값을 제거한다.
    """

    OVERRIDE_KEYS = (
        "policy_override_enabled",
        "operation_policy_override",
        "manual_operation_override",
        "scheduled_operation_override",
        "auto_close_override",
        "early_close_override",
        "liquidation_override",
    )

    def __init__(self, stock_dir: Path, code: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stock_dir = stock_dir
        self.code = code
        self.name = name
        self.config_path = stock_dir / "config.json"
        self.config = read_json_dict(self.config_path) or default_config()
        self.setWindowTitle(f"개별종목 설정 - {code} {name}")
        self.resize(520, 360)

        self.use_override = QCheckBox("이 종목만 개별설정 사용")
        self.memo = QTextEdit()
        self.memo.setPlaceholderText("개별 예외 사유 또는 메모")
        self.memo.setMinimumHeight(90)
        self.btn_reset_all = QPushButton("환경설정값으로 전체 리셋")
        self.btn_save = QPushButton("저장")
        self.btn_cancel = QPushButton("취소")

        self._setup_ui()
        self.load_config_to_widgets()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        info = QLabel(
            "기본값은 환경설정에서 변경합니다.\n"
            "선택 종목만 예외로 적용합니다.\n"
            "현재 1차 구현은 예외 사용 여부와 메모, 전체 리셋 흐름을 먼저 제공합니다."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(self.use_override)
        layout.addWidget(QLabel("개별설정 메모"))
        layout.addWidget(self.memo)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.btn_reset_all)
        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_save)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.btn_reset_all.clicked.connect(self.reset_all_to_global)
        self.btn_save.clicked.connect(self.save_override)
        self.btn_cancel.clicked.connect(self.reject)

    def load_config_to_widgets(self) -> None:
        self.use_override.setChecked(bool(self.config.get("policy_override_enabled", False)))
        self.memo.setPlainText(str(self.config.get("policy_override_memo", "")))

    def write_config(self) -> None:
        self.config["updated_at"] = now_text()
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def save_override(self) -> None:
        self.config["policy_override_enabled"] = self.use_override.isChecked()
        self.config["policy_override_memo"] = self.memo.toPlainText().strip()
        self.config["policy_override_updated_at"] = now_text()
        try:
            self.write_config()
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 저장")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 저장: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"개별종목 설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "저장 완료", "개별종목 설정을 저장했습니다.")
        self.accept()

    def reset_all_to_global(self) -> None:
        for key in self.OVERRIDE_KEYS:
            self.config.pop(key, None)
        self.config.pop("policy_override_memo", None)
        self.config["policy_override_enabled"] = False
        self.config["policy_override_reset_at"] = now_text()
        try:
            self.write_config()
            append_stock_log(self.stock_dir, "GUI", "개별종목 설정 전체 리셋")
            append_changelog("UPDATE", "config.json", f"개별종목 설정 전체 리셋: {self.code} {self.name}")
        except Exception as exc:
            QMessageBox.critical(self, "리셋 오류", f"개별종목 설정 리셋 중 오류가 발생했습니다.\n\n{exc}")
            return
        QMessageBox.information(self, "리셋 완료", "해당 종목의 개별설정을 환경설정값으로 전체 리셋했습니다.")
        self.accept()

class AutoTradeSettingWindow(QDialog):
    """
    자동매매설정 창.

    1차 구현 범위:
    - 자동매매 루틴 목록 표시
    - 선택 루틴의 종목별 저장 폴더 표시
    - state.json 기준 상태 요약 표시
    - 실제 자동매매 시작/정지/삭제/환경설정/주문상태/로그 기능은 다음 단계에서 구현
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("자동매매설정")
        self.resize(1180, 680)

        self.routine_table = QTableWidget()
        self.stock_table = QTableWidget()

        self.btn_start = QPushButton("감시시작")
        self.btn_stop = QPushButton("감시종료")
        self.btn_set_schedule = QPushButton("환경설정")
        self.btn_delete = QPushButton("등록해제")
        self.btn_config = QPushButton("환경설정")
        self.btn_order_view = QPushButton("주문상태 보기")
        self.btn_log_view = QPushButton("로그 보기")
        self.btn_review_view = QPushButton("검토관리")
        self.btn_refresh = QPushButton("안정성검사")
        self.btn_close = QPushButton("닫기")

        self._routine_sort_column = -1
        self._routine_sort_order = Qt.AscendingOrder
        self._stock_sort_column = -1
        self._stock_sort_order = Qt.AscendingOrder
        self._last_time_policy_minute_key = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._time_policy_timer = QTimer(self)
        self._time_policy_timer.setInterval(10_000)
        self._time_policy_timer.timeout.connect(self.on_time_policy_timer_tick)

        self._setup_ui()
        self._connect_events()

        # 자동매매설정 창 시작 시 직전 실행 상태를 이어받지 않는다.
        # 감시시작 버튼을 눌렀을 때만 현재 시간/운영방식 기준으로 재판정한다.
        self.reset_runtime_statuses_on_window_start()

        self.refresh_all()
        self._time_policy_timer.start()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()

        routine_box = QGroupBox("자동매매 루틴")
        routine_layout = QVBoxLayout()
        self._setup_routine_table()
        routine_layout.addWidget(self.routine_table)
        routine_box.setLayout(routine_layout)
        routine_box.setMaximumHeight(175)

        self.stock_box = QGroupBox()
        stock_layout = QVBoxLayout()
        self.selected_routine_label = QLabel("선택 루틴: -")
        apply_selected_routine_label_style(self.selected_routine_label)
        self.selected_routine_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.selected_routine_label.setMinimumHeight(28)
        self._setup_stock_table()
        stock_layout.addWidget(self.selected_routine_label)
        stock_layout.addWidget(self.stock_table)
        self.stock_box.setLayout(stock_layout)

        buttons = [
            self.btn_start,
            self.btn_stop,
            self.btn_set_schedule,
            self.btn_delete,
            self.btn_config,
            self.btn_order_view,
            self.btn_log_view,
            self.btn_review_view,
            self.btn_refresh,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        # v20.9.1g: 좌우 분할 구조를 상하 구조로 변경한다.
        # 루틴 목록은 상단 요약 영역으로 압축하고, 종목표는 하단 전체 폭을 사용한다.
        main_layout.addWidget(routine_box, 0)
        main_layout.addWidget(self.stock_box, 1)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_routine_table(self) -> None:
        headers = [
            "루틴명",
            "종목수",
            "총예산",
            "사용예산",
            "가용예산",
        ]

        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.routine_table)
        self.routine_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.routine_table.horizontalHeader().setStretchLastSection(True)
        self.routine_table.setColumnWidth(0, 220)
        self.routine_table.setColumnWidth(1, 90)
        self.routine_table.setColumnWidth(2, 140)
        self.routine_table.setColumnWidth(3, 140)
        self.routine_table.setColumnWidth(4, 140)
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.routine_table.setSortingEnabled(False)
        self.routine_table.horizontalHeader().setSectionsClickable(True)

    def _setup_stock_table(self) -> None:
        headers = [
            "코드",
            "종목",
            "운영",
            "상태",
            "보유",
            "평단",
            "매수",
            "매결",
            "도결",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        apply_plain_table_header(self.stock_table)
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.stock_table.horizontalHeader().setStretchLastSection(True)
        self.stock_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stock_table.setColumnWidth(0, 85)
        self.stock_table.setColumnWidth(1, 190)
        self.stock_table.setColumnWidth(2, 220)
        self.stock_table.setColumnWidth(3, 115)
        self.stock_table.setColumnWidth(4, 70)
        self.stock_table.setColumnWidth(5, 100)
        self.stock_table.setColumnWidth(6, 65)
        self.stock_table.setColumnWidth(7, 65)
        self.stock_table.setColumnWidth(8, 65)
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.stock_table.setSortingEnabled(False)
        self.stock_table.horizontalHeader().setSectionsClickable(True)

    def _connect_events(self) -> None:
        self.routine_table.itemSelectionChanged.connect(self.on_routine_selection_changed)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_routine_table_by_column)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.horizontalHeader().sectionClicked.connect(self.sort_stock_table_by_column)
        self.stock_table.itemDoubleClicked.connect(self.on_stock_table_item_double_clicked)
        self.stock_table.customContextMenuRequested.connect(self.on_stock_table_context_menu)
        self.btn_refresh.clicked.connect(self.run_current_routine_stability_check)
        self.btn_close.clicked.connect(self.close)
        self.btn_start.clicked.connect(self.start_selected_auto_trades)
        self.btn_stop.clicked.connect(self.stop_selected_auto_trades)
        self.btn_set_schedule.clicked.connect(self.open_operation_environment_settings)
        self.btn_delete.clicked.connect(self.unregister_selected_auto_trade_stocks)
        self.btn_config.clicked.connect(self.show_deferred_message)
        self.btn_order_view.clicked.connect(self.open_order_status_window)
        self.btn_log_view.clicked.connect(self.open_log_view_window)
        self.btn_review_view.clicked.connect(self.open_review_required_window)

    def sort_routine_table_by_column(self, column: int) -> None:
        """상단 루틴표 헤더 클릭 정렬."""
        if column < 0 or column >= self.routine_table.columnCount():
            return

        if self._routine_sort_column == column:
            self._routine_sort_order = (
                Qt.DescendingOrder
                if self._routine_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._routine_sort_column = column
            self._routine_sort_order = Qt.AscendingOrder

        selected_routine_name = self.current_selected_routine_name()
        self.routine_table.sortItems(column, self._routine_sort_order)
        if selected_routine_name:
            self.restore_routine_selection(selected_routine_name)

    def sort_stock_table_by_column(self, column: int) -> None:
        """하단 종목표 헤더 클릭 정렬."""
        if column < 0 or column >= self.stock_table.columnCount():
            return

        if self._stock_sort_column == column:
            self._stock_sort_order = (
                Qt.DescendingOrder
                if self._stock_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._stock_sort_column = column
            self._stock_sort_order = Qt.AscendingOrder

        selected_paths = set()
        for row in self.selected_stock_rows():
            item = self.stock_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole):
                selected_paths.add(str(item.data(Qt.UserRole)))

        self.stock_table.sortItems(column, self._stock_sort_order)

        if selected_paths:
            self.stock_table.clearSelection()
            for row in range(self.stock_table.rowCount()):
                item = self.stock_table.item(row, 0)
                if item is not None and str(item.data(Qt.UserRole)) in selected_paths:
                    self.stock_table.selectRow(row)
        self.update_action_buttons()

    def apply_auto_trade_table_sorts(self) -> None:
        """목록 갱신 후 기존 정렬 기준을 다시 적용한다."""
        if self._routine_sort_column >= 0:
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)
        if self._stock_sort_column >= 0:
            self.stock_table.sortItems(self._stock_sort_column, self._stock_sort_order)

    def refresh_all(self) -> None:
        # 자동매매설정 창 전체 갱신 전 하단 종목표 위치를 보존한다.
        # 시간변경/감시시작/감시종료 후 종목표가 맨 위로 튀는 문제를 막는다.
        selected_stock_paths, stock_scroll_value = self.capture_stock_table_view_state()

        normalize_base_stock_single_routine_file()
        ensure_single_real_trade_routine_for_all_stocks()
        selected_routine_name = self.current_selected_routine_name()
        self.load_routine_table()

        if selected_routine_name:
            self.restore_routine_selection(selected_routine_name)

        if self.current_selected_routine_dir() is None and self.routine_table.rowCount() > 0:
            self.routine_table.selectRow(0)

        self.load_selected_routine_stocks()
        self.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)
        self.update_action_buttons()

    def current_time_policy_minute_key(self) -> str:
        """시간정책 자동 재판정용 분 단위 키."""
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def on_time_policy_timer_tick(self) -> None:
        """분이 바뀐 경우에만 운영방식/시간정책을 자동 재판정한다.

        원칙:
        - 초 단위 반복 작업 금지
        - 상태 변화가 없으면 화면 갱신 금지
        - 변경 종목이 있을 때만 현재 창을 갱신
        - 긴급정지/검토종목/조기마감은 재판정 함수에서 보호
        """
        if not self.isVisible():
            return

        minute_key = self.current_time_policy_minute_key()
        if minute_key == self._last_time_policy_minute_key:
            return

        self._last_time_policy_minute_key = minute_key
        result = self.recalculate_all_status_by_operation_policy(
            "시간 경과 자동 재판정",
            silent_unchanged=True,
            write_changelog_when_unchanged=False,
        )
        changed_count = int(result.get("changed", 0) or 0)
        failed_count = int(result.get("failed", 0) or 0)
        if changed_count <= 0 and failed_count <= 0:
            return

        self.refresh_all()
        parent = self.parent()
        if isinstance(parent, MainWindow):
            parent.refresh_all()
        self.statusBarMessage(
            f"시간정책 자동반영: 변경 {changed_count}개"
            + (f" / 실패 {failed_count}개" if failed_count else "")
        )

    def closeEvent(self, event) -> None:
        """창을 닫을 때 시간정책 타이머를 정리한다."""
        try:
            self._time_policy_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def capture_stock_table_view_state(self) -> tuple[set[str], int]:
        """하단 종목표의 선택 종목 경로와 세로 스크롤 위치를 저장한다."""
        selected_paths: set[str] = set()
        try:
            for row in self.selected_stock_rows():
                item = self.stock_table.item(row, 0)
                if item is not None and item.data(Qt.UserRole):
                    selected_paths.add(str(item.data(Qt.UserRole)))
        except Exception:
            selected_paths = set()

        try:
            scroll_value = self.stock_table.verticalScrollBar().value()
        except Exception:
            scroll_value = 0

        return selected_paths, scroll_value

    def restore_stock_table_view_state(self, selected_paths: set[str], scroll_value: int) -> None:
        """하단 종목표의 선택 종목과 세로 스크롤 위치를 복원한다."""
        try:
            if selected_paths:
                self.stock_table.clearSelection()
                for row in range(self.stock_table.rowCount()):
                    item = self.stock_table.item(row, 0)
                    if item is not None and str(item.data(Qt.UserRole)) in selected_paths:
                        self.stock_table.selectRow(row)
        except Exception:
            pass

        try:
            scroll_bar = self.stock_table.verticalScrollBar()
            scroll_bar.setValue(min(max(0, scroll_value), scroll_bar.maximum()))
        except Exception:
            pass

    def reset_runtime_statuses_on_window_start(self) -> None:
        """
        자동매매설정 창 시작 시 직전 실행 상태를 안전하게 감시/대기 상태로 초기화한다.

        원칙:
        - 프로그램을 껐다 켜면 자동으로 매수/매도 또는 감시/매도를 이어가지 않는다.
        - 감시시작 버튼을 눌렀을 때만 현재 시간/운영방식 기준으로 재판정한다.
        - 검토종목/긴급정지 계열은 안전상태이므로 자동 해제하지 않는다.
        """
        reset_targets = {
            "RUNNING",
            "SELL_ONLY",
            "STARTED",
            "AUTO",
            "TRADING",
            "BUYING",
            "SELLING",
            "WATCHING",
            "WATCH",
            "WATCH_SELL",
            "BUY_SELL",
        }
        protected_statuses = {
            "REVIEW_REQUIRED",
            "REVIEW",
            "EMERGENCY_STOP",
            "EMERGENCY_STOPPED",
            "EMERGENCY",
        }

        changed_count = 0
        for routine_dir in get_routine_dirs():
            for stock_dir in get_stock_dirs_in_routine(routine_dir):
                state_path = stock_dir / "state.json"
                state = read_json_dict(state_path)
                if not state:
                    continue

                status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
                if status in protected_statuses:
                    continue
                if status not in reset_targets:
                    continue

                state["status"] = "STOPPED"
                state["updated_at"] = now_text()
                state["startup_reset_at"] = now_text()
                state["startup_reset_reason"] = "PROGRAM_RESTART_TO_MONITORING"

                if write_state_json(stock_dir, state):
                    changed_count += 1
                else:
                    continue

        if changed_count:
            append_changelog(
                "UPDATE",
                "state.json",
                f"자동매매설정 창 시작 시 직전 실행상태 감시/대기 초기화: {changed_count}개",
            )

    def selected_stock_rows(self) -> list[int]:
        return [index.row() for index in self.stock_table.selectionModel().selectedRows()]

    def has_selected_stock(self) -> bool:
        return len(self.selected_stock_rows()) >= 1

    def has_single_selected_stock(self) -> bool:
        return len(self.selected_stock_rows()) == 1

    def update_action_buttons(self) -> None:
        has_stock = self.has_selected_stock()
        single_stock = self.has_single_selected_stock()

        self.btn_start.setEnabled(has_stock)
        self.btn_stop.setEnabled(has_stock)
        self.btn_set_schedule.setEnabled(True)
        self.btn_delete.setEnabled(has_stock)
        self.btn_config.setEnabled(single_stock)
        self.btn_order_view.setEnabled(single_stock)
        self.btn_log_view.setEnabled(single_stock)
        self.btn_review_view.setEnabled(True)

    def on_stock_selection_changed(self) -> None:
        self.update_action_buttons()

    def operation_stock_dir_from_row(self, row: int) -> Path | None:
        code_item = self.stock_table.item(row, 0)
        if code_item is None:
            return None
        stock_dir_text = code_item.data(Qt.UserRole)
        if not stock_dir_text:
            return None
        stock_dir = Path(str(stock_dir_text))
        if not stock_dir.exists():
            return None
        return stock_dir

    def on_stock_table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """운영 칸 더블클릭 시 시간/수동을 빠르게 전환한다."""
        if item.column() != 2:
            return

        stock_dir = self.operation_stock_dir_from_row(item.row())
        if stock_dir is None:
            return

        self.stock_table.selectRow(item.row())
        config = read_json_dict(stock_dir / "config.json") or default_config()
        current_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))

        if current_mode == "CONTINUOUS":
            global_schedule = read_global_schedule()
            self.set_selected_operation_mode(
                "SCHEDULED",
                schedule_config_updates(
                    global_schedule["start_time"],
                    global_schedule["end_buy_time"],
                ),
            )
        else:
            self.set_selected_operation_mode("CONTINUOUS")

    def ensure_context_row_selected(self, row: int) -> None:
        """우클릭한 행이 기존 선택에 없으면 해당 행만 선택한다."""
        if row < 0 or row >= self.stock_table.rowCount():
            return

        if row not in self.selected_stock_rows():
            self.stock_table.clearSelection()
            self.stock_table.selectRow(row)

    def select_all_current_routine_stocks(self) -> None:
        self.stock_table.selectAll()
        self.update_action_buttons()
        self.statusBarMessage(f"현재 루틴 전체 종목 선택: {self.stock_table.rowCount()}개")

    def clear_current_routine_stock_selection(self) -> None:
        self.stock_table.clearSelection()
        self.update_action_buttons()
        self.statusBarMessage("현재 루틴 종목 선택 해제")

    def on_stock_table_context_menu(self, pos) -> None:
        """하단 종목표 우클릭 메뉴를 통합 제공한다."""
        item = self.stock_table.itemAt(pos)
        if item is not None:
            self.ensure_context_row_selected(item.row())

        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_selection = menu.addAction("전체 해제")
        menu.addSeparator()
        action_stock_policy = menu.addAction("개별종목 설정")
        action_individual = menu.addAction("시간 변경")
        action_global_reset = menu.addAction("기본 리셋")
        menu.addSeparator()
        action_unregister = menu.addAction("선택 종목 등록해제")

        has_selection = self.has_selected_stock()
        single_selection = len(self.selected_stock_infos()) == 1
        action_stock_policy.setEnabled(single_selection)
        action_individual.setEnabled(has_selection)
        action_global_reset.setEnabled(has_selection)
        action_unregister.setEnabled(has_selection)

        chosen = menu.exec_(self.stock_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        if chosen == action_select_all:
            self.select_all_current_routine_stocks()
        elif chosen == action_clear_selection:
            self.clear_current_routine_stock_selection()
        elif chosen == action_stock_policy:
            self.open_selected_stock_policy_settings()
        elif chosen == action_individual:
            self.set_selected_individual_schedule_time()
        elif chosen == action_global_reset:
            self.reset_selected_schedule_to_global()
        elif chosen == action_unregister:
            self.unregister_selected_auto_trade_stocks()

    def load_routine_table(self) -> None:
        routine_dirs = get_routine_dirs()
        self.routine_table.setSortingEnabled(False)
        self.routine_table.setRowCount(len(routine_dirs))

        for row, routine_dir in enumerate(routine_dirs):
            routine_name = routine_display_name(routine_dir)
            budget = read_routine_budget(routine_dir)
            stock_count = len(assigned_stock_dirs_in_routine(routine_dir))

            total_budget = int(budget.get('total_budget', budget.get('budget', 0)) or 0)
            used_budget = int(budget.get('used_budget', 0) or 0)
            available_budget = int(budget.get('available_budget', max(total_budget - used_budget, 0)) or 0)
            values = [
                routine_name,
                str(stock_count),
                f"{total_budget:,}",
                f"{used_budget:,}",
                f"{available_budget:,}",
            ]
            sort_values = [routine_name, stock_count, total_budget, used_budget, available_budget]

            for col, value in enumerate(values):
                item = SortableTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                item.setData(Qt.UserRole, str(routine_dir))
                item.setData(SORT_ROLE, sort_values[col])
                self.routine_table.setItem(row, col, item)

        self.routine_table.clearSelection()
        if self._routine_sort_column >= 0:
            self.routine_table.sortItems(self._routine_sort_column, self._routine_sort_order)

    def current_selected_routine_name(self) -> str:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        if not selected_rows:
            return ""

        row = selected_rows[0].row()
        item = self.routine_table.item(row, 0)
        if item is None:
            return ""

        return item.text().strip()

    def current_selected_routine_dir(self) -> Path | None:
        selected_rows = self.routine_table.selectionModel().selectedRows()
        if not selected_rows:
            return None

        row = selected_rows[0].row()
        item = self.routine_table.item(row, 0)
        if item is None:
            return None

        path_text = item.data(Qt.UserRole)
        if not path_text:
            return None

        path = Path(str(path_text))
        if not path.exists():
            return None

        return path

    def restore_routine_selection(self, routine_name: str) -> None:
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            if item and item.text().strip() == routine_name:
                self.routine_table.selectRow(row)
                return

    def on_routine_selection_changed(self) -> None:
        self.load_selected_routine_stocks()

    def load_selected_routine_stocks(self) -> None:
        routine_dir = self.current_selected_routine_dir()
        routine_name = self.current_selected_routine_name()

        selected_stock_paths, stock_scroll_value = self.capture_stock_table_view_state()

        if hasattr(self, "selected_routine_label"):
            if routine_dir is None or not routine_name:
                self.selected_routine_label.setText("선택 루틴: -")
            else:
                self.selected_routine_label.setText(f"선택 루틴: {routine_name}")

        self.stock_table.blockSignals(True)
        self.stock_table.setUpdatesEnabled(False)
        self.stock_table.setSortingEnabled(False)
        try:
            # v20.8.2: 상태 컬럼은 더 이상 셀 위젯을 사용하지 않는다.
            # 그래도 이전 버전에서 남은 셀 위젯이 있을 수 있으므로 먼저 제거한다.
            for row in range(self.stock_table.rowCount()):
                for col in range(self.stock_table.columnCount()):
                    self.stock_table.removeCellWidget(row, col)
            self.stock_table.clearContents()

            if routine_dir is None:
                self.stock_table.setRowCount(0)
                return

            stock_dirs = assigned_stock_dirs_in_routine(routine_dir)
            self.stock_table.setRowCount(len(stock_dirs))

            for row, stock_dir in enumerate(stock_dirs):
                code, name = parse_stock_folder_name(stock_dir.name)
                state = read_json_dict(stock_dir / "state.json")
                config = read_json_dict(stock_dir / "config.json")
                display_status = display_status_text_for_gui(state.get("status", "STOPPED"))
                operation_text, operation_color, operation_tooltip = operation_text_and_color(config)
                buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

                values = [
                    code,
                    name,
                    operation_text,
                    f"● {display_status}",
                    str(state.get("holding_qty", 0)),
                    f"{int(state.get('avg_price', 0)):,}",
                    str(state.get("buy_count", 0)),
                    str(buy_pending_qty),
                    str(sell_pending_qty),
                ]
                status_rank = {
                    "감시/대기": 0,
                    "매수/매도": 1,
                    "감시/매도": 2,
                    "조기마감": 3,
                    "긴급정지": 4,
                    "검토종목": 5,
                }.get(display_status, 99)
                sort_values = [
                    code,
                    name,
                    operation_text,
                    status_rank,
                    safe_int_value(state.get("holding_qty"), 0),
                    safe_int_value(state.get("avg_price"), 0),
                    safe_int_value(state.get("buy_count"), 0),
                    buy_pending_qty if isinstance(buy_pending_qty, int) else 10**12,
                    sell_pending_qty if isinstance(sell_pending_qty, int) else 10**12,
                ]

                for col, value in enumerate(values):
                    if col == 3:
                        item = create_auto_trade_status_item(display_status)
                    else:
                        item = SortableTableWidgetItem(value)
                        item.setToolTip(value)

                    item.setData(Qt.UserRole, str(stock_dir))
                    item.setData(SORT_ROLE, sort_values[col])

                    if col == 2:
                        item.setToolTip(operation_tooltip + "\n더블클릭: 시간/수동 전환\n우클릭: 시간 변경/기본 리셋")
                        item.setForeground(QColor(operation_color))
                    elif col in (7, 8) and value == "?":
                        item.setToolTip("미결 수량 확인 필요")
                        item.setForeground(QColor("#D32F2F"))

                    if col in (0, 2, 4, 5, 6, 7, 8):
                        item.setTextAlignment(Qt.AlignCenter)
                    elif col == 3:
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    else:
                        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

                    self.stock_table.setItem(row, col, item)

            self.stock_table.clearSelection()
        finally:
            if self._stock_sort_column >= 0:
                self.stock_table.sortItems(self._stock_sort_column, self._stock_sort_order)

            self.stock_table.setUpdatesEnabled(True)
            self.stock_table.blockSignals(False)

            self.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)

            self.stock_table.viewport().update()
            self.stock_table.repaint()

        self.update_action_buttons()

    def selected_stock_dir(self) -> Path | None:
        selected_rows = self.selected_stock_rows()
        if len(selected_rows) != 1:
            return None

        item = self.stock_table.item(selected_rows[0], 0)
        if item is None:
            return None

        path_text = item.data(Qt.UserRole)
        if not path_text:
            return None

        stock_dir = Path(str(path_text))
        if not stock_dir.exists():
            return None

        return stock_dir

    def selected_stock_info(self) -> tuple[Path, str, str] | None:
        selected_rows = self.selected_stock_rows()
        if len(selected_rows) != 1:
            return None

        row = selected_rows[0]
        code_item = self.stock_table.item(row, 0)
        name_item = self.stock_table.item(row, 1)
        stock_dir = self.selected_stock_dir()

        if code_item is None or name_item is None or stock_dir is None:
            return None

        return stock_dir, code_item.text().strip(), name_item.text().strip()

    def selected_stock_infos(self) -> list[tuple[Path, str, str]]:
        infos: list[tuple[Path, str, str]] = []
        seen_rows: set[int] = set()

        for row in self.selected_stock_rows():
            if row in seen_rows:
                continue
            seen_rows.add(row)

            code_item = self.stock_table.item(row, 0)
            name_item = self.stock_table.item(row, 1)
            path_item = self.stock_table.item(row, 0)

            if code_item is None or name_item is None or path_item is None:
                continue

            path_text = path_item.data(Qt.UserRole)
            if not path_text:
                continue

            stock_dir = Path(str(path_text))
            if not stock_dir.exists():
                continue

            infos.append((stock_dir, code_item.text().strip(), name_item.text().strip()))

        return infos

    def int_state_value(self, state: dict[str, object], key: str) -> int:
        try:
            return int(state.get(key, 0) or 0)
        except Exception:
            return 0

    def resume_status_after_pause(self, state: dict[str, object]) -> tuple[str, dict[str, object], str]:
        """
        일시중지 후 재시작 정책.

        확정 정책:
        - 일시중지 기간 동안 매수/매도 신호가 1건이라도 확인되면 REVIEW_REQUIRED.
        - 매수/매도 신호가 모두 0건으로 확인된 경우에만 RUNNING 재시작 허용.
        - 신호 발생 여부를 아직 확인할 수 없는 경우도 안전하게 REVIEW_REQUIRED.

        현재 단계에서는 실제 루틴 신호 재계산 루프가 아직 연결되지 않았으므로,
        향후 신호 검증 모듈이 state.json에 기록할 아래 필드를 기준으로 판정한다.
        - pause_signal_check_status: CHECKED / UNCHECKED / FAILED
        - missed_buy_signal_count
        - missed_sell_signal_count
        """
        missed_buy = self.int_state_value(state, "missed_buy_signal_count")
        missed_sell = self.int_state_value(state, "missed_sell_signal_count")
        check_status = str(state.get("pause_signal_check_status", "UNCHECKED")).strip().upper()

        metadata: dict[str, object] = {
            "review_checked_at": now_text(),
            "missed_buy_signal_count": missed_buy,
            "missed_sell_signal_count": missed_sell,
        }

        if missed_buy > 0 or missed_sell > 0:
            metadata.update(
                {
                    "review_required": True,
                    "review_reason": "SIGNAL_OCCURRED_DURING_PAUSE",
                }
            )
            return "REVIEW_REQUIRED", metadata, "일시중지 중 매수/매도 신호 발생"

        if check_status == "CHECKED":
            metadata.update(
                {
                    "review_required": False,
                    "review_reason": "",
                    "resumed_at": now_text(),
                    "ignore_signals_before": now_text(),
                }
            )
            return "RUNNING", metadata, "일시중지 중 매수/매도 신호 없음"

        metadata.update(
            {
                "review_required": True,
                "review_reason": "PAUSE_SIGNAL_CHECK_UNAVAILABLE",
            }
        )
        return "REVIEW_REQUIRED", metadata, "일시중지 중 신호 발생 여부 확인 필요"

    def pre_start_review_check(self, routine_name: str, stock_dir: Path, code: str, name: str) -> dict[str, object]:
        """
        자동매매 시작 전 사전점검.

        프로그램이 먼저 점검하고, 문제 없는 종목만 RUNNING으로 전환한다.
        문제 소지가 있는 종목은 REVIEW_REQUIRED로 전환한 뒤 검토관리창에서 HTS 검토 후 처리한다.
        """
        item = build_review_required_item(routine_name, stock_dir, code, name)
        state = read_json_dict(stock_dir / "state.json")
        before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

        # PAUSED 상태는 일시중지 기간 중 신호 검토 정책을 추가 반영한다.
        if before_status == "PAUSED":
            new_status, metadata, reason = self.resume_status_after_pause(state)
            if new_status == "REVIEW_REQUIRED":
                forced = [reason]
                item = build_review_required_item(routine_name, stock_dir, code, name, forced)
                item["resume_metadata"] = metadata
                return item

        return item

    def mark_review_required(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        item: dict[str, object],
    ) -> bool:
        reasons = unique_review_reasons(list(item.get("review_reasons", [])))
        reason_text = " / ".join(reasons) if reasons else "수동 검토 필요"
        metadata = {
            "review_required": True,
            "review_status": "PENDING",
            "review_reason": reason_text,
            "review_checked_at": now_text(),
            "missed_buy_signal_count": safe_int_value(item.get("missed_buy_signal_count"), 0),
            "missed_sell_signal_count": safe_int_value(item.get("missed_sell_signal_count"), 0),
            "last_checked_price": safe_float_value(item.get("current_price"), 0.0),
            "last_checked_pnl_rate": str(item.get("pnl_rate_text", "-")),
        }
        resume_metadata = item.get("resume_metadata")
        if isinstance(resume_metadata, dict):
            metadata.update(resume_metadata)

        return self.update_stock_status(stock_dir, code, name, "REVIEW_REQUIRED", metadata, reason_text)

    def update_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state: dict[str, object] | None = None,
        log_suffix: str = "",
    ) -> bool:
        state_path = stock_dir / "state.json"
        state = read_json_dict(state_path)
        before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

        state["status"] = new_status
        state["updated_at"] = now_text()

        if extra_state:
            state.update(extra_state)

        if not write_state_json(stock_dir, state):
            QMessageBox.critical(
                self,
                "상태 저장 오류",
                f"{code} {name} 상태 저장 중 오류가 발생했습니다.",
            )
            append_stock_log(stock_dir, "ERROR", f"상태 저장 실패: {before_status} -> {new_status}")
            return False

        suffix_text = f" / {log_suffix}" if log_suffix else ""
        append_stock_log(stock_dir, "GUI", f"자동매매 상태 변경: {before_status} -> {new_status}{suffix_text}")
        return True

    def operation_policy_protected_status(self, status: object) -> bool:
        """운영방식/시간정책 자동 재판정에서 건드리면 안 되는 보호 상태."""
        current = str(status or "STOPPED").strip().upper() or "STOPPED"
        return current in {
            "EMERGENCY_STOPPED",
            "EMERGENCY_STOP",
            "EMERGENCY",
            "REVIEW_REQUIRED",
            "REVIEW",
            "EARLY_CLOSE",
            "EARLY_CLOSING",
            "EARLY_CLOSED",
            "FORCE_CLOSE",
            "FORCE_LIQUIDATION",
        }

    def recalculate_stock_status_by_operation_policy(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        reason: str,
        extra_state: dict[str, object] | None = None,
        silent_unchanged: bool = False,
    ) -> tuple[str, str, str]:
        """운영방식/현재시간 기준으로 상태를 중앙 재판정한다.

        반환값: (result, before_status, after_status)
        - changed: 상태 변경됨
        - unchanged: 재판정했지만 상태 동일
        - protected: 긴급정지/검토종목/조기마감 등 보호상태라 미변경
        - failed: 저장 실패
        """
        state = read_json_dict(stock_dir / "state.json")
        before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

        if self.operation_policy_protected_status(before_status):
            if not silent_unchanged:
                append_stock_log(
                    stock_dir,
                    "GUI",
                    f"운영정책 재판정 보호상태 유지: {auto_trade_status_display(before_status)} / {reason}",
                )
            return "protected", before_status, before_status

        config = read_json_dict(stock_dir / "config.json")
        if not config:
            config = default_config()

        mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        new_status = status_after_operation_mode_change(mode, config)

        metadata = {
            "operation_policy_recalculated_at": now_text(),
            "operation_policy_reason": reason,
            "operation_policy_mode": mode,
        }
        if extra_state:
            metadata.update(extra_state)

        if new_status == before_status:
            if not silent_unchanged:
                append_stock_log(
                    stock_dir,
                    "GUI",
                    f"운영정책 재판정 상태유지: {auto_trade_status_display(before_status)} / {operation_mode_display(mode)} / {reason}",
                )
            return "unchanged", before_status, new_status

        log_suffix = (
            f"운영정책 재판정: {operation_mode_display(mode)} / "
            f"{auto_trade_status_display(before_status)} -> {auto_trade_status_display(new_status)} / {reason}"
        )
        if self.update_stock_status(stock_dir, code, name, new_status, metadata, log_suffix):
            return "changed", before_status, new_status
        return "failed", before_status, new_status

    def recalculate_all_status_by_operation_policy(
        self,
        reason: str,
        silent_unchanged: bool = False,
        write_changelog_when_unchanged: bool = True,
    ) -> dict[str, int]:
        """전체 루틴 전체 종목을 운영방식/현재시간 기준으로 재판정한다."""
        result = {"changed": 0, "unchanged": 0, "protected": 0, "failed": 0}
        for routine_dir in get_routine_dirs():
            for stock_dir in get_stock_dirs_in_routine(routine_dir):
                code, name = parse_stock_folder_name(stock_dir.name)
                status, _, _ = self.recalculate_stock_status_by_operation_policy(
                    stock_dir,
                    code,
                    name,
                    reason,
                    silent_unchanged=silent_unchanged,
                )
                if status not in result:
                    result[status] = 0
                result[status] += 1
        if write_changelog_when_unchanged or result.get("changed", 0) or result.get("failed", 0):
            append_changelog(
                "UPDATE",
                "state.json",
                f"전체 운영정책 재판정: {reason} / 변경 {result.get('changed', 0)}개 / 유지 {result.get('unchanged', 0)}개 / 보호 {result.get('protected', 0)}개 / 실패 {result.get('failed', 0)}개",
            )
        return result

    def update_stock_operation_mode(self, stock_dir: Path, code: str, name: str, operation_mode: str, config_updates: dict[str, object] | None = None) -> bool:
        mode = normalize_operation_mode(operation_mode)
        config_path = stock_dir / "config.json"
        config = read_json_dict(config_path)
        if not config:
            config = default_config()

        before_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        config["operation_mode"] = mode
        if config_updates:
            config.update(config_updates)

            start_time = normalized_hhmmss_or_empty(
                config.get("start_time", config.get("trade_start_time", ""))
            )
            end_buy_time = normalized_hhmmss_or_empty(
                config.get("end_buy_time", config.get("buy_end_time", ""))
            )
            if start_time and end_buy_time:
                config["start_time"] = start_time
                config["trade_start_time"] = start_time
                config["end_buy_time"] = end_buy_time
                config["buy_end_time"] = end_buy_time

        config["operation_mode_updated_at"] = now_text()

        try:
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "운영방식 저장 오류",
                f"{code} {name} 운영방식 저장 중 오류가 발생했습니다.\n\n{exc}",
            )
            append_stock_log(stock_dir, "ERROR", f"운영방식 저장 실패: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)} / {exc}")
            return False

        append_stock_log(stock_dir, "GUI", f"운영방식 변경: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)}")
        return True

    def unregister_selected_auto_trade_stocks(self) -> None:
        """
        자동매매설정 창에서 선택 종목을 현재 루틴에서 등록해제한다.

        정책:
        - 기초종목.txt의 루틴 연결만 제거한다. 종목 자체는 기초종목에 남긴다.
        - 루틴 runtime 폴더, config.json, logs는 유지한다.
        - 정지/감시중 + 보유·미체결 없음은 즉시 등록해제한다.
        - 정지/감시중 + 보유 또는 현재 미체결 있음은 확인창에서 체크한 항목만 등록해제하고 state/orders 현재 흔적을 초기화한다.
        - 매수/매도, 매도만 등 매매 가능 상태는 등록해제 불가로 표시만 한다.
        """
        selected = self.selected_stock_infos()
        routine_name = self.current_selected_routine_name()

        if not selected or not routine_name:
            QMessageBox.warning(self, "선택 오류", "등록해제할 종목을 1개 이상 선택하세요.")
            return

        immediate_items: list[dict[str, object]] = []
        force_items: list[dict[str, object]] = []
        blocked_items: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()

        for stock_dir, code, name in selected:
            key = (code, name)
            if key in seen:
                continue
            seen.add(key)
            item = auto_trade_unregister_category(routine_name, stock_dir, code, name)
            category = str(item.get("category", "blocked"))
            if category == "immediate":
                immediate_items.append(item)
            elif category == "force":
                force_items.append(item)
            else:
                blocked_items.append(item)

        selected_force_items: list[dict[str, object]] = []
        if immediate_items or force_items or blocked_items:
            dialog = AutoTradeUnregisterConfirmDialog(
                routine_name=routine_name,
                immediate_items=immediate_items,
                force_items=force_items,
                blocked_items=blocked_items,
                parent=self,
            )
            if dialog.exec_() != QDialog.Accepted:
                return
            selected_force_items = dialog.selected_items()

        process_items = immediate_items + selected_force_items
        if not process_items:
            QMessageBox.information(self, "등록해제 없음", "등록해제 처리할 종목이 선택되지 않았습니다.")
            return

        reset_failed_items: list[str] = []
        completed_items: list[str] = []
        force_keys = {(str(item.get("code", "")), str(item.get("name", ""))) for item in selected_force_items}

        for item in process_items:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            if not code or not name:
                continue

            if (code, name) in force_keys:
                for runtime_routine_name, stock_dir in item.get("runtime_dirs", []):
                    if not reset_runtime_state_for_force_unregister(stock_dir):
                        reset_failed_items.append(f"{code} {name} / {runtime_routine_name}")
                        continue
                    append_stock_log(
                        stock_dir,
                        "FORCE_ROUTINE_UNREGISTER_RESET",
                        "자동매매설정 등록해제로 state.json과 orders.json 현재 표시/판단값 초기화",
                    )

            if reset_failed_items:
                continue

            if update_base_stock_routines(code, name, []):
                completed_items.append(f"{code},{name}")

        if reset_failed_items:
            preview_text = "\n".join(reset_failed_items[:10])
            if len(reset_failed_items) > 10:
                preview_text += f"\n... 외 {len(reset_failed_items) - 10}개"
            QMessageBox.warning(
                self,
                "상태 초기화 오류",
                "일부 종목의 state.json/orders.json 초기화에 실패했습니다.\n"
                "해당 종목은 루틴 등록해제를 완료하지 않았습니다.\n\n"
                f"{preview_text}",
            )
            return

        if not completed_items:
            QMessageBox.information(self, "등록해제 없음", "기초종목.txt에서 등록해제할 종목을 찾지 못했습니다.")
            return

        report_path = write_blocked_action_report(
            "자동매매설정 등록해제",
            blocked_items,
            target_routine=routine_name,
        )

        append_changelog(
            "UPDATE",
            "기초종목.txt",
            f"자동매매설정 창 루틴 등록해제: {' / '.join(completed_items)} / 루틴 runtime 폴더 유지",
        )

        self.statusBar_message(f"루틴 등록해제 완료: {len(completed_items)}개")
        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()
        self.refresh_all()

        result_lines = [f"등록해제 완료: {len(completed_items)}개"]
        if blocked_items:
            result_lines.append(f"등록해제 불가: {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        QMessageBox.information(self, "등록해제 결과", "\n".join(result_lines))

    def statusBar_message(self, message: str, timeout_ms: int = 7000) -> None:
        parent = self.parent()
        if isinstance(parent, MainWindow):
            parent.statusBar().showMessage(message, timeout_ms)


    def open_operation_environment_settings(self) -> None:
        """스케줄매매관리 대체: 운영환경설정 창을 연다."""
        dialog = OperationEnvironmentSettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.statusBarMessage("환경설정 저장 완료")
            self.refresh_all()

    def open_selected_stock_policy_settings(self) -> None:
        """종목 우클릭용 개별종목 설정 창."""
        selected = self.single_selected_stock_info()
        if selected is None:
            QMessageBox.warning(self, "선택 오류", "개별종목 설정은 종목 1개를 선택한 상태에서 사용할 수 있습니다.")
            return
        stock_dir, code, name = selected
        dialog = StockPolicyOverrideDialog(stock_dir, code, name, self)
        if dialog.exec_() == QDialog.Accepted:
            self.refresh_all()

    def set_global_schedule_time(self) -> None:
        """하위 호환용: 스케줄매매관리 창을 연다."""
        self.open_schedule_trade_management_window()

    def open_schedule_trade_management_window(self) -> None:
        dialog = ScheduleTradeManagementDialog(self)
        dialog.exec_()
        self.refresh_all()

    def set_selected_individual_schedule_time(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "시간을 변경할 종목을 1개 이상 선택하세요.")
            return

        first_config = read_json_dict(selected[0][0] / "config.json")
        if not first_config:
            first_config = default_config()
        start_time, end_buy_time, _ = effective_schedule_times(first_config)

        dialog = ScheduleOperationDialog(self, start_time, end_buy_time, len(selected))
        dialog.setWindowTitle("종목 시간 예외 설정")
        if dialog.exec_() != QDialog.Accepted:
            return

        self.set_selected_operation_mode(
            "SCHEDULED",
            schedule_config_updates(
                dialog.start_time(),
                dialog.end_buy_time(),
            ),
        )

    def reset_selected_schedule_to_global(self) -> None:
        selected = self.selected_stock_infos()
        if not selected:
            QMessageBox.warning(self, "선택 오류", "기본 시간으로 리셋할 종목을 1개 이상 선택하세요.")
            return

        global_schedule = read_global_schedule()
        self.set_selected_operation_mode(
            "SCHEDULED",
            schedule_config_updates(
                global_schedule["start_time"],
                global_schedule["end_buy_time"],
            ),
        )

    def set_selected_schedule_operation_mode(self) -> None:
        """
        하위 호환용: 선택 종목 개별 시간설정으로 연결한다.
        """
        self.set_selected_individual_schedule_time()

    def set_selected_operation_mode(self, operation_mode: str, config_updates: dict[str, object] | None = None) -> None:
        selected = self.selected_stock_infos()
        routine_name = self.current_selected_routine_name()

        if not selected or not routine_name:
            QMessageBox.warning(self, "선택 오류", "운영방식을 변경할 종목을 1개 이상 선택하세요.")
            return

        mode = normalize_operation_mode(operation_mode)
        display_mode = operation_mode_display(mode)
        completed: list[str] = []
        status_changed: list[str] = []
        protected: list[str] = []

        for stock_dir, code, name in selected:
            if not self.update_stock_operation_mode(stock_dir, code, name, mode, config_updates):
                continue

            completed.append(f"{code} {name}")

            result, before_status, new_status = self.recalculate_stock_status_by_operation_policy(
                stock_dir,
                code,
                name,
                "운영방식/시간 설정 변경",
                {"operation_mode_status_applied_at": now_text()},
            )
            if result == "changed":
                status_changed.append(f"{code} {name}({auto_trade_status_display(new_status)})")
            elif result == "protected":
                protected.append(f"{code} {name}({auto_trade_status_display(before_status)})")

        if completed:
            changelog_parts = [f"대상: {' / '.join(completed)}"]
            schedule_log_text = schedule_change_log_text(config_updates)
            if schedule_log_text:
                changelog_parts.append(schedule_log_text)
            if status_changed:
                changelog_parts.append(f"상태재판정: {' / '.join(status_changed)}")
            if protected:
                changelog_parts.append(f"보호상태유지: {' / '.join(protected)}")

            append_changelog(
                "UPDATE",
                "config.json/state.json",
                f"종목별 운영방식 변경: {routine_name} -> {display_mode}: {' | '.join(changelog_parts)}",
            )

        self.refresh_all()
        self.stock_table.viewport().update()
        self.stock_table.repaint()

        status_text = f"운영방식 변경 완료: {display_mode} {len(completed)}개"
        schedule_suffix = schedule_status_suffix(config_updates)
        if schedule_suffix:
            status_text += schedule_suffix
        if status_changed:
            status_text += f" / 상태재판정 {len(status_changed)}개"
        if protected:
            status_text += f" / 보호상태유지 {len(protected)}개"
        self.statusBarMessage(status_text)

    def set_selected_stocks_buy_end(self) -> None:
        """선택 종목을 SELL_ONLY 상태로 전환한다. 화면 표시는 '감시/매도'로 한다."""
        selected = self.selected_stock_infos()
        routine_name = self.current_selected_routine_name()

        if not selected or not routine_name:
            QMessageBox.warning(self, "선택 오류", "매수종료할 종목을 1개 이상 선택하세요.")
            return

        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []
        allowed_statuses = {"RUNNING", "MONITORING"}

        for stock_dir, code, name in selected:
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if status in allowed_statuses:
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")

        if not targets:
            message = "매수종료 전환 대상 없음"
            if skipped:
                message += f": 제외 {len(skipped)}개"
            self.statusBarMessage(message)
            return

        preview = "\n".join(f"- {code} {name}" for _, code, name in targets[:8])
        if len(targets) > 8:
            preview += f"\n- 외 {len(targets) - 8}개"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("매수종료 확인")
        box.setText(
            "선택 종목을 매수종료 상태로 전환합니다.\n\n"
            "신규매수는 중단되고 보유분 매도 조건만 계속 관리됩니다.\n\n"
            f"대상:\n{preview}\n\n"
            "계속하시겠습니까?"
        )
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()
        if box.clickedButton() != proceed_button:
            self.statusBarMessage("매수종료 전환 취소")
            return

        completed: list[str] = []
        for stock_dir, code, name in targets:
            metadata = {
                "buy_end_requested_at": now_text(),
                "buy_end_reason": "USER_CONTEXT_MENU",
            }
            if self.update_stock_status(stock_dir, code, name, "SELL_ONLY", metadata, "상태 칼럼 우클릭 매수종료"):
                completed.append(f"{code} {name}")

        if completed:
            changelog_message = f"선택종목 매수종료 전환: {routine_name} -> {' / '.join(completed)}"
            if skipped:
                changelog_message += f" / 제외: {' / '.join(skipped)}"
            append_changelog("UPDATE", "state.json", changelog_message)

        self.refresh_all()
        self.stock_table.viewport().update()
        self.stock_table.repaint()

        message = f"매수종료 전환 완료: {len(completed)}개"
        if skipped:
            message += f" / 제외 {len(skipped)}개"
        self.statusBarMessage(message)

    def run_current_routine_stability_check(self) -> None:
        """현재 선택 루틴의 종목을 자동매매 투입 전 기준으로 점검한다.

        역할:
        - 새로고침 대체 기능이다.
        - 상태를 덮어써서 맞추지 않는다.
        - 문제가 있는 종목은 검토종목으로 이동한다.
        - 정상 종목은 상태를 변경하지 않는다.
        """
        routine_dir = self.current_selected_routine_dir()
        routine_name = self.current_selected_routine_name()

        if routine_dir is None or not routine_name:
            QMessageBox.warning(self, "선택 오류", "안정성검사할 루틴을 선택하세요.")
            return

        stock_dirs = assigned_stock_dirs_in_routine(routine_dir)
        if not stock_dirs:
            self.statusBarMessage("안정성검사 대상 종목 없음")
            return

        normal_count = 0
        review_count = 0
        protected_count = 0
        failed_count = 0

        for stock_dir in stock_dirs:
            code, name = parse_stock_folder_name(stock_dir.name)
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"

            if self.operation_policy_protected_status(status):
                protected_count += 1
                continue

            try:
                review_item = self.pre_start_review_check(routine_name, stock_dir, code, name)
                if review_required_for_start(review_item):
                    if self.mark_review_required(stock_dir, code, name, review_item):
                        review_count += 1
                    else:
                        failed_count += 1
                else:
                    normal_count += 1
            except Exception as exc:
                review_item = build_review_required_item(
                    routine_name,
                    stock_dir,
                    code,
                    name,
                    [f"안정성검사 실패: {exc}"],
                )
                if self.mark_review_required(stock_dir, code, name, review_item):
                    review_count += 1
                else:
                    failed_count += 1

        append_changelog(
            "CHECK",
            "state.json",
            (
                f"안정성검사: {routine_name} -> "
                f"정상 {normal_count}개 / 검토관리 {review_count}개 / "
                f"운영중 {protected_count}개 / 실패 {failed_count}개"
            ),
        )

        self.refresh_all()
        self.stock_table.viewport().update()
        self.stock_table.repaint()

        message = (
            "안정성검사 완료\n\n"
            f"정상: {normal_count}개\n"
            f"운영중: {protected_count}개\n"
            f"검토관리 이동: {review_count}개"
        )
        if failed_count:
            message += f"\n처리 실패: {failed_count}개"
        message += "\n\n상세 내용은 검토종목 관리창에서 확인하세요."

        QMessageBox.information(self, "안정성검사 완료", message)
        self.statusBarMessage(
            f"안정성검사 완료: 정상 {normal_count}개 / 운영중 {protected_count}개 / 검토관리 {review_count}개"
        )

    def split_start_targets(
        self,
        selected: list[tuple[Path, str, str]],
    ) -> tuple[list[tuple[Path, str, str]], list[str]]:
        """
        감시시작 대상과 제외 대상을 분리한다.

        정책:
        - STOPPED: 감시종료/정지 상태이므로 감시시작 가능
        - MONITORING/WATCHING/WATCH/WATCH_BUY: 화면상 감시/대기지만 주문 비활성 상태이므로
          감시시작 버튼으로 현재 시간/운영방식에 맞게 재판정 가능
        - RUNNING/SELL_ONLY/REVIEW_REQUIRED/EMERGENCY 계열은 보호 상태로 제외
        """
        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []

        start_allowed_statuses = {
            "STOPPED",
            "STOP",
            "WAIT",
            "WAIT_BUY",
            "WAIT_SELL",
            "MONITORING",
            "WATCHING",
            "WATCH",
            "WATCH_BUY",
        }

        for stock_dir, code, name in selected:
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if status in start_allowed_statuses:
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")

        return targets, skipped

    def split_stop_targets(
        self,
        selected: list[tuple[Path, str, str]],
    ) -> tuple[list[tuple[Path, str, str]], list[str]]:
        """
        감시종료 대상과 제외 대상을 분리한다.

        이미 정지(STOPPED) 상태인 종목은 감시종료 대상에서 제외한다.
        """
        targets: list[tuple[Path, str, str]] = []
        skipped: list[str] = []

        for stock_dir, code, name in selected:
            state = read_json_dict(stock_dir / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if status == "STOPPED":
                skipped.append(f"{code} {name}(감시/대기)")
            else:
                targets.append((stock_dir, code, name))

        return targets, skipped

    def stop_warning_items(self, selected: list[tuple[Path, str, str]]) -> list[str]:
        """
        감시종료 전 주의가 필요한 보유/미체결 종목을 반환한다.
        """
        items: list[str] = []
        for stock_dir, code, name in selected:
            state = read_json_dict(stock_dir / "state.json")
            holding_qty = safe_int_value(state.get("holding_qty"), 0)
            pending_exists, pending_qty = pending_order_summary(stock_dir, state)
            if holding_qty > 0 or pending_exists:
                parts: list[str] = []
                if holding_qty > 0:
                    parts.append(f"보유 {holding_qty:,}주")
                if pending_exists:
                    parts.append(f"미체결 {pending_qty:,}주")
                items.append(f"{code} {name}({', '.join(parts)})")
        return items

    def confirm_stop_if_position_or_pending_exists(self, selected: list[tuple[Path, str, str]]) -> bool:
        warning_items = self.stop_warning_items(selected)
        if not warning_items:
            return True

        preview = "\n".join(f"- {item}" for item in warning_items[:8])
        if len(warning_items) > 8:
            preview += f"\n- 외 {len(warning_items) - 8}개"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("감시종료 확인")
        box.setText(
            "보유수량 또는 미체결 주문이 있는 종목이 포함되어 있습니다.\n\n"
            "감시종료는 해당 종목의 감시와 주문을 모두 중단하는 동작입니다.\n"
            "보유분 매도 조건을 계속 관리하려면 '감시/매도' 상태가 더 안전할 수 있습니다.\n\n"
            f"대상:\n{preview}\n\n"
            "그래도 감시종료를 실행하시겠습니까?"
        )
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()
        return box.clickedButton() == proceed_button

    def start_selected_auto_trades(self) -> None:
        selected = self.selected_stock_infos()
        routine_name = self.current_selected_routine_name()

        if not selected or not routine_name:
            QMessageBox.warning(self, "선택 오류", "감시를 시작할 종목을 1개 이상 선택하세요.")
            return

        start_targets, skipped = self.split_start_targets(selected)
        if not start_targets:
            if skipped:
                self.statusBarMessage(f"감시시작 대상 없음: 이미 감시 중/보호 상태 {len(skipped)}개 제외")
            else:
                self.statusBarMessage("감시시작 대상 없음")
            return

        completed: list[str] = []
        review_required: list[str] = []

        for stock_dir, code, name in start_targets:
            review_item = self.pre_start_review_check(routine_name, stock_dir, code, name)

            if review_required_for_start(review_item):
                if self.mark_review_required(stock_dir, code, name, review_item):
                    review_required.append(f"{code} {name}")
                continue

            config = read_json_dict(stock_dir / "config.json")
            if not config:
                config = default_config()

            operation_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
            start_status = status_after_operation_mode_change(operation_mode, config)
            status_display = auto_trade_status_display(start_status)
            mode_display = operation_mode_display(operation_mode)
            trade_permission_text, _, _ = trade_permission_display(config)

            metadata = {
                "review_required": False,
                "review_status": "",
                "review_reason": "",
                "resumed_at": now_text(),
                "ignore_signals_before": now_text(),
                # operation_mode는 config.json만 원본으로 사용한다.
                # state.json에는 저장하지 않는다.
                "real_trade_enabled": real_trade_enabled(config),
                "start_policy_status": start_status,
                "start_policy_checked_at": now_text(),
            }
            result, _, applied_status = self.recalculate_stock_status_by_operation_policy(
                stock_dir,
                code,
                name,
                "감시시작",
                metadata,
            )
            if result in ("changed", "unchanged"):
                completed.append(f"{code} {name}({mode_display}/{trade_permission_text}/{auto_trade_status_display(applied_status)})")

        if completed or review_required:
            changelog_parts: list[str] = []
            if completed:
                changelog_parts.append(f"시작: {' / '.join(completed)}")
            if review_required:
                changelog_parts.append(f"검토종목: {' / '.join(review_required)}")
            if skipped:
                changelog_parts.append(f"제외: {' / '.join(skipped)}")

            append_changelog(
                "UPDATE",
                "state.json",
                f"감시시작 전 안정성검사 및 operation_mode 반영: {routine_name} -> {' | '.join(changelog_parts)}",
            )

        self.refresh_all()
        self.stock_table.viewport().update()
        self.stock_table.repaint()

        message = (
            "안정성검사 완료\n\n"
            f"정상: {len(completed)}개\n"
            f"운영중: {len(skipped)}개\n"
            f"검토관리 이동: {len(review_required)}개"
        )
        message += "\n\n상세 내용은 검토종목 관리창에서 확인하세요."

        QMessageBox.information(self, "안정성검사 완료", message)
        self.statusBarMessage(
            f"안정성검사 완료: 정상 {len(completed)}개 / "
            f"운영중 {len(skipped)}개 / 검토관리 {len(review_required)}개"
        )
        if review_required:
            self.open_review_required_window()


    def stop_selected_auto_trades(self) -> None:
        selected = self.selected_stock_infos()
        routine_name = self.current_selected_routine_name()

        if not selected or not routine_name:
            QMessageBox.warning(self, "선택 오류", "감시를 종료할 종목을 1개 이상 선택하세요.")
            return

        stop_targets, skipped = self.split_stop_targets(selected)
        if not stop_targets:
            self.statusBarMessage("감시종료 대상 없음: 이미 감시/대기 상태")
            return

        if not self.confirm_stop_if_position_or_pending_exists(stop_targets):
            self.statusBarMessage("감시종료 취소: 보유/미체결 종목 확인 미승인")
            return

        completed: list[str] = []
        for stock_dir, code, name in stop_targets:
            metadata = {
                "review_required": False,
                "review_reason": "",
            }
            if self.update_stock_status(stock_dir, code, name, "STOPPED", metadata, "감시종료"):
                completed.append(f"{code} {name}")

        if completed:
            changelog_message = f"감시종료 상태 변경: {routine_name} -> {' / '.join(completed)}"
            if skipped:
                changelog_message += f" / 제외: {' / '.join(skipped)}"
            append_changelog(
                "UPDATE",
                "state.json",
                changelog_message,
            )

        self.refresh_all()
        self.stock_table.viewport().update()
        self.stock_table.repaint()
        message = f"감시종료 완료: {len(completed)}개"
        if skipped:
            message += f" / 제외 {len(skipped)}개"
        self.statusBarMessage(message)

    def open_review_required_window(self) -> None:
        """검토관리창은 루틴별이 아니라 프로그램 전체 단위로 연다."""
        dialog = GlobalReviewRequiredWindow(parent=self)
        dialog.exec_()
        self.refresh_all()

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        parent = self.parent()
        if isinstance(parent, MainWindow):
            parent.statusBar().showMessage(message, timeout_ms)
        self.setWindowTitle(f"자동매매설정 - {message}")

    def open_order_status_window(self) -> None:
        selected = self.selected_stock_info()
        routine_name = self.current_selected_routine_name()

        if selected is None or not routine_name:
            QMessageBox.warning(
                self,
                "선택 오류",
                "주문상태를 확인할 종목을 1개 선택하세요.",
            )
            return

        try:
            stock_dir, code, name = selected
            dialog = OrderStatusWindow(
                stock_dir=stock_dir,
                routine_name=routine_name,
                stock_code=code,
                stock_name=name,
                parent=self,
            )
            dialog.exec_()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "주문상태 보기 오류",
                f"주문상태 창을 여는 중 오류가 발생했습니다.\n\n{exc}",
            )

    def open_log_view_window(self) -> None:
        selected = self.selected_stock_info()
        routine_name = self.current_selected_routine_name()

        if selected is None or not routine_name:
            QMessageBox.warning(
                self,
                "선택 오류",
                "로그를 확인할 종목을 1개 선택하세요.",
            )
            return

        try:
            stock_dir, code, name = selected
            dialog = LogViewWindow(
                stock_dir=stock_dir,
                routine_name=routine_name,
                stock_code=code,
                stock_name=name,
                parent=self,
            )
            dialog.exec_()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "로그 보기 오류",
                f"로그 보기 창을 여는 중 오류가 발생했습니다.\n\n{exc}",
            )

    def show_deferred_message(self) -> None:
        show_deferred_config_message(self)



RUNNING_STATUS_VALUES = {
    "RUNNING",
    "SELL_ONLY",
    "STARTED",
    "AUTO",
    "TRADING",
    "WATCHING",
    "BUYING",
    "SELLING",
    "EMERGENCY_STOPPED",
}



def stock_runtime_status_for_routine(routine_name: str, code: str, name: str) -> str:
    """
    루틴별 종목 state.json 기준 자동매매 상태를 반환한다.
    """
    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
    if stock_dir is None:
        return "대기"

    state_path = stock_dir / "state.json"
    if not state_path.exists():
        return "감시/대기"

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "오류"

    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    return display_status_text_for_gui(raw_status)


def pending_routine_names_for_stock(
    code: str,
    name: str,
    assigned_routines: list[str],
) -> list[str]:
    """구형 루틴폴더 내부 잔여 종목폴더 기준 등록대기 판정은 폐기한다.

    현재 기준에서 루틴 연결의 진실 원본은 중앙 stocks/config.json 계열과
    기초 종목의 routines 값이다. routines/<루틴명>/ 패키지 내부에 종목폴더를
    만들거나 탐색하지 않으므로 등록대기 목록은 반환하지 않는다.
    """
    return []


def base_stock_routine_assignments() -> dict[tuple[str, str], set[str]]:
    """
    기초종목.txt 기준 종목-루틴 연결 정보를 반환한다.

    자동매매설정 창은 루틴 폴더에 남은 종목 폴더만으로 종목을 표시하지 않고,
    기초종목.txt 에 실제 연결된 종목만 표시한다.
    """
    result: dict[tuple[str, str], set[str]] = {}
    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        routines = stock.get("routines", [])
        if not code or not name:
            continue
        if isinstance(routines, list):
            routine_set = {str(routine).strip() for routine in routines if str(routine).strip()}
        else:
            routine_text = str(routines).strip()
            routine_set = {routine_text} if routine_text else set()
        result[(code, name)] = routine_set
    return result


def is_stock_assigned_to_routine(code: str, name: str, routine_name: str) -> bool:
    """
    기초종목.txt 기준으로 종목이 해당 루틴에 연결되어 있는지 확인한다.
    """
    assignments = base_stock_routine_assignments()
    return routine_name in assignments.get((code, name), set())


def assigned_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """
    자동매매설정 표시용 루틴 종목 폴더 목록을 반환한다.

    루틴 폴더 안에 물리 폴더가 남아 있어도 기초종목.txt 에 연결 정보가 없으면
    자동매매설정 창에는 표시하지 않는다.
    """
    routine_name = routine_display_name(routine_dir)
    result: list[Path] = []
    for stock_dir in get_stock_dirs_in_routine(routine_dir):
        code, name = parse_stock_folder_name(stock_dir.name)
        if is_stock_assigned_to_routine(code, name, routine_name):
            result.append(stock_dir)
    return result


def stock_runtime_dirs_for_stock(code: str, name: str) -> list[tuple[str, Path]]:
    """중앙 stocks/ 기준으로 해당 종목의 배정 루틴 폴더를 반환한다.

    더 이상 _루틴폴더 또는 routines/<루틴명>/ 내부 종목폴더를 조회하지 않는다.
    """
    active_routines = base_stock_routine_assignments().get((code, name), set())
    result: list[tuple[str, Path]] = []

    for routine_name in sorted(active_routines):
        stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
        if stock_dir is not None and stock_dir.exists() and stock_dir.is_dir():
            result.append((routine_name, stock_dir))

    return result


def runtime_delete_block_reasons(stock_dir: Path) -> list[str]:
    """
    종목 삭제 차단 사유를 runtime 상태 기준으로 반환한다.
    """
    reasons: list[str] = []
    state = read_json_dict(stock_dir / "state.json")
    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    if raw_status and raw_status != "STOPPED":
        reasons.append(auto_trade_status_display(raw_status))

    try:
        holding_qty = int(state.get("holding_qty", 0) or 0)
    except Exception:
        holding_qty = 0
    if holding_qty > 0:
        reasons.append(f"보유 {holding_qty}")

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    pending_parts: list[str] = []
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        pending_parts.append(f"매수미결 {buy_pending_qty}")
    elif buy_pending_qty == "?":
        pending_parts.append("매수미결 확인필요")

    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        pending_parts.append(f"매도미결 {sell_pending_qty}")
    elif sell_pending_qty == "?":
        pending_parts.append("매도미결 확인필요")

    reasons.extend(pending_parts)
    return reasons


def routine_status_display_text(routine_name: str, status: str) -> str:
    """
    루틴별 상태 표시 문구를 반환한다.

    v20.9.1a:
    - 등록 루틴 컬럼에서도 감시중/운영중/매도만 등 상태 차이를 숨기지 않는다.
    - 대기 상태만 삭제보호용 표시 목적에 맞춰 등록대기로 표시한다.
    """
    normalized = str(status or "").strip()

    if normalized in ("운영", "운영중"):
        return f"{routine_name}(운영중)"

    if normalized == "대기":
        return f"{routine_name}(등록대기)"

    if normalized:
        return f"{routine_name}({normalized})"

    return f"{routine_name}(상태없음)"


def routine_status_color(status: str) -> str:
    """
    루틴별 상태 점 색상을 반환한다.

    자동매매설정 창 상태 색상과 같은 팔레트를 사용해 상태별 식별성을 맞춘다.
    """
    normalized = display_status_text_for_gui(status)
    if normalized == "대기":
        return auto_trade_status_color("등록대기")
    if normalized == "운영":
        normalized = "운영중"
    return auto_trade_status_color(normalized)


def create_routine_status_widget(status_lines: list[tuple[str, str]]) -> QWidget:
    """
    등록 루틴 셀에 넣을 상태 위젯을 생성한다.
    색상 점과 루틴명을 분리해 시인성을 높인다.
    """
    container = QWidget()
    layout = QVBoxLayout()
    layout.setContentsMargins(12, 5, 12, 5)
    layout.setSpacing(5)

    if not status_lines:
        label = QLabel("-")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #555555;")
        layout.addWidget(label)
    else:
        for routine_name, status in status_lines:
            line_widget = QWidget()
            line_layout = QHBoxLayout()
            line_layout.setContentsMargins(0, 0, 0, 0)
            line_layout.setSpacing(9)

            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(
                "border-radius: 6px;"
                "border: 1px solid #555555;"
                f"background-color: {routine_status_color(status)};"
            )

            text_label = QLabel(routine_status_display_text(routine_name, status))
            text_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            text_label.setStyleSheet("font-weight: 600; padding: 1px 0px;")

            line_layout.addWidget(dot)
            line_layout.addWidget(text_label, 1)
            line_widget.setLayout(line_layout)
            layout.addWidget(line_widget)

    container.setLayout(layout)
    return container


def has_running_routine(code: str, name: str, routines: list[str]) -> tuple[bool, list[str]]:
    """
    선택 종목에 운영중 루틴이 있는지 확인한다.
    """
    running_routines: list[str] = []

    for routine_name in routines:
        status = stock_runtime_status_for_routine(routine_name, code, name)
        if status not in ("감시/대기", "대기"):
            running_routines.append(f"{routine_name}({status})")

    return bool(running_routines), running_routines


def stock_register_unavailable_reason(code: str, name: str) -> tuple[str, str, list[str], list[tuple[str, Path]]]:
    """
    종목등록설정 삭제/등록해제 정책에 따라 선택 종목을 분류한다.

    반환값:
    - category: immediate / force / blocked
    - title: 화면 표시용 종목명
    - reasons: 사유 목록
    - runtime_dirs: 해당 종목의 루틴 runtime 폴더 목록
    """
    runtime_dirs = stock_runtime_dirs_for_stock(code, name)
    title = f"{code} {name}"

    if not runtime_dirs:
        return "immediate", title, ["루틴 연결 없음"], []

    force_reasons: list[str] = []
    blocked_reasons: list[str] = []

    allowed_statuses = {"STOPPED", "STOP", "MONITORING", "WATCHING", ""}
    blocked_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}

    for routine_name, stock_dir in runtime_dirs:
        state = read_json_dict(stock_dir / "state.json")
        raw_status = str(state.get("status", "STOPPED")).strip().upper()
        display_status = display_status_text_for_gui(raw_status)

        try:
            holding_qty = int(state.get("holding_qty", 0) or 0)
        except Exception:
            holding_qty = 0

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)

        routine_prefix = f"{routine_name}: "

        if raw_status in blocked_statuses:
            blocked_reasons.append(f"{routine_prefix}{display_status} 상태")
            continue

        if raw_status not in allowed_statuses:
            blocked_reasons.append(f"{routine_prefix}{display_status or '상태확인필요'} 상태")
            continue

        if buy_pending_qty == "?" or sell_pending_qty == "?":
            blocked_reasons.append(f"{routine_prefix}미체결 확인 필요")
            continue

        pending_parts: list[str] = []
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            pending_parts.append(f"매수미결 {buy_pending_qty}")
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            pending_parts.append(f"매도미결 {sell_pending_qty}")

        if holding_qty > 0 or pending_parts:
            force_reason = f"{routine_prefix}{display_status}"
            details: list[str] = []
            if holding_qty > 0:
                details.append(f"보유 {holding_qty}")
            details.extend(pending_parts)
            if details:
                force_reason += f" / {', '.join(details)}"
            force_reasons.append(force_reason)

    if blocked_reasons:
        return "blocked", title, blocked_reasons, runtime_dirs

    if force_reasons:
        return "force", title, force_reasons, runtime_dirs

    return "immediate", title, ["정지/감시중, 보유·미체결 없음"], runtime_dirs


def reset_runtime_orders_for_force_unregister(stock_dir: Path) -> bool:
    """
    강제 등록해제 시 자동매매설정 표에 남는 매결/도결/미체결 흔적을 제거한다.

    정책:
    - 현재 화면과 판단 기준이 되는 orders.json 은 빈 주문 목록으로 초기화한다.
    - 기존 주문 기록은 즉시 삭제하지 않고 orders_archive.json 에 보존한다.
    - config.json, logs 폴더, 루틴 종목 폴더는 건드리지 않는다.
    """
    orders_path = stock_dir / "orders.json"
    archive_path = stock_dir / "orders_archive.json"

    current_orders = read_orders_data(orders_path)

    try:
        if current_orders:
            archive_data = read_json_dict(archive_path)
            archives = archive_data.get("archives", [])
            if not isinstance(archives, list):
                archives = []

            archives.append(
                {
                    "archived_at": now_text(),
                    "reason": "강제 등록해제 상태초기화로 orders.json 현재 표시/판단 흔적 초기화",
                    "orders": current_orders,
                }
            )

            archive_path.write_text(
                json.dumps({"archives": archives}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        orders_path.write_text(json.dumps(default_orders(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def reset_runtime_state_for_force_unregister(stock_dir: Path) -> bool:
    """
    강제 등록해제 시 runtime 폴더와 설정/로그는 유지하되,
    현재 운영상태(state.json)와 화면 판단용 주문 흔적(orders.json)을 초기화한다.
    """
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
    if not state:
        state = default_state()

    reset_values = {
        "status": "STOPPED",
        "trade_set_status": "WAIT_BUY",
        "current_set_no": 1,
        "current_round": 0,
        "avg_price": 0,
        "holding_qty": 0,
        "holding_amount": 0,
        "buy_count": 0,
        "last_buy_price": 0,
        "last_buy_time": "",
        "last_sell_time": "",
        "pending_order": False,
        "pending_qty": 0,
        "remaining_qty": 0,
        "unfilled_qty": 0,
        "buy_pending_qty": 0,
        "sell_pending_qty": 0,
        "paused_at": "",
        "resumed_at": "",
        "review_required": False,
        "review_reason": "",
        "missed_buy_signal_count": 0,
        "missed_sell_signal_count": 0,
        "pause_signal_check_status": "UNCHECKED",
        "ignore_signals_before": "",
        "updated_at": now_text(),
    }
    state.update(reset_values)

    try:
        if not reset_runtime_orders_for_force_unregister(stock_dir):
            return False
        if not write_state_json(stock_dir, state):
            return False
        return True
    except Exception:
        return False


def active_stock_register_status_display(code: str, name: str, routine_name: str) -> str:
    """
    종목등록설정 창의 운영상태 표시용 문구를 반환한다.

    원칙:
    - 루틴 미등록 종목은 미지정으로 표시한다.
    - 루틴 등록 종목은 자동매매설정 창과 동일하게 state.json 상태를 사용자 표시명으로 변환한다.
    - SELL_ONLY 등 내부값은 화면에 직접 노출하지 않는다.
    """
    routine_name = str(routine_name).strip()
    if not routine_name or routine_name == "미등록":
        return "미지정"

    stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
    if stock_dir is None:
        return "미생성"

    state_path = stock_dir / "state.json"
    if not state_path.exists():
        return auto_trade_status_display("STOPPED")

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return "오류"

    return auto_trade_status_display(state.get("status", "STOPPED"))


class StockRegisterWindow(QDialog):
    """
    종목등록설정 창.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("종목등록설정")
        self.resize(860, 560)

        self.stock_search_input = QLineEdit()
        self.stock_search_input.setPlaceholderText("목록 필터: 코드, 종목명, 루틴명, 상태")
        self.stock_table = QTableWidget()

        self.btn_search_register = QPushButton("검색식등록")
        self.btn_search_register.setEnabled(False)
        self.btn_search_register.setToolTip("키움 조건검색식 연동 단계에서 구현 예정입니다.")
        self.btn_manual_register = QPushButton("수동등록")
        self.btn_manual_register.setToolTip("종목 라이브러리에서 직접 선택 등록합니다.")
        self.btn_routine_assign = QPushButton("매매루틴지정")
        self.btn_integrity_check = QPushButton("무결성검증")
        self.btn_blocked_report = QPushButton("처리불가 리포트")
        self.btn_delete_stock = QPushButton("선택 종목 삭제")
        self.btn_delete_stock.setEnabled(False)
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._connect_events()
        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        button_layout = QHBoxLayout()

        self._setup_stock_table()

        buttons = [
            self.btn_search_register,
            self.btn_manual_register,
            self.btn_routine_assign,
            self.btn_integrity_check,
            self.btn_blocked_report,
            self.btn_delete_stock,
            self.btn_close,
        ]

        for button in buttons:
            button.setMinimumHeight(34)
            button_layout.addWidget(button)

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("기초종목.txt 등록 종목 관리"))
        header_layout.addStretch(1)
        header_layout.addWidget(QLabel("검색"))
        header_layout.addWidget(self.stock_search_input)
        self.stock_search_input.setMinimumWidth(360)

        main_layout.addLayout(header_layout)
        main_layout.addWidget(self.stock_table)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _setup_stock_table(self) -> None:
        headers = [
            "종목코드",
            "종목명",
            "등록 루틴",
            "운영상태",
            "검증상태",
        ]

        self.stock_table.setColumnCount(len(headers))
        self.stock_table.setHorizontalHeaderLabels(headers)
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.stock_table.horizontalHeader().setStretchLastSection(True)
        self.stock_table.setShowGrid(True)
        self.stock_table.setColumnWidth(0, 105)
        self.stock_table.setColumnWidth(1, 165)
        self.stock_table.setColumnWidth(2, 250)
        self.stock_table.setColumnWidth(3, 120)
        self.stock_table.setColumnWidth(4, 120)
        self.stock_table.setWordWrap(False)
        self.stock_table.verticalHeader().setDefaultSectionSize(42)
        self.stock_table.setStyleSheet(
            "QHeaderView::section { border-bottom: 1px solid #c8c8c8; }"
            "QTableWidget { gridline-color: #d6d6d6; }"
        )
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.stock_table.setSortingEnabled(True)
        self.stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.stock_table.setContextMenuPolicy(Qt.CustomContextMenu)

    def _connect_events(self) -> None:
        self.btn_close.clicked.connect(self.close)
        self.btn_manual_register.clicked.connect(self.open_manual_register_dialog)
        self.btn_routine_assign.clicked.connect(self.open_routine_assign_window)
        self.btn_integrity_check.clicked.connect(self.open_integrity_check_window)
        self.btn_blocked_report.clicked.connect(self.open_latest_blocked_report)
        self.btn_delete_stock.clicked.connect(self.delete_selected_stock)
        self.stock_search_input.textChanged.connect(self.refresh_stock_table)
        self.stock_table.itemSelectionChanged.connect(self.on_stock_selection_changed)
        self.stock_table.itemClicked.connect(self.on_stock_table_item_clicked)
        self.stock_table.itemDoubleClicked.connect(self.open_routine_assign_for_stock)
        self.stock_table.customContextMenuRequested.connect(self.show_stock_table_context_menu)


    def on_stock_selection_changed(self) -> None:
        selected_rows = self.stock_table.selectionModel().selectedRows()
        self.btn_delete_stock.setEnabled(len(selected_rows) >= 1)

    def on_stock_table_item_clicked(self, item: QTableWidgetItem) -> None:
        """
        종목등록설정 창에서 종목 행을 1회 클릭했을 때의 보조 처리.

        itemClicked 시그널 연결은 유지하되, 실제 삭제 버튼 활성화 여부는
        현재 선택 상태를 기준으로 다시 계산한다.
        더블클릭으로 매매루틴지정 창을 여는 기존 동작은 변경하지 않는다.
        """
        self.on_stock_selection_changed()

    def show_stock_table_context_menu(self, position) -> None:
        """
        종목등록설정 창 종목표 우클릭 메뉴를 표시한다.
        """
        row = self.stock_table.rowAt(position.y())
        if row >= 0 and not self.stock_table.selectionModel().isRowSelected(row):
            self.stock_table.clearSelection()
            self.stock_table.selectRow(row)

        selected_count = len(self.selected_registered_stocks())
        menu = QMenu(self)

        action_assign = menu.addAction("선택 종목 루틴 지정")
        action_unassign = menu.addAction("선택 종목 루틴 해제")
        menu.addSeparator()
        action_delete = menu.addAction("선택 종목 삭제")
        menu.addSeparator()
        action_select_all = menu.addAction("전체 종목 선택")
        action_select_unassigned = menu.addAction("미등록 종목 선택")
        action_clear = menu.addAction("선택 해제")

        has_selected = selected_count > 0
        action_assign.setEnabled(has_selected)
        action_unassign.setEnabled(has_selected)
        action_delete.setEnabled(has_selected)

        selected_action = menu.exec_(self.stock_table.viewport().mapToGlobal(position))
        if selected_action is None:
            return

        if selected_action == action_assign:
            self.confirm_open_routine_assign_from_context_menu()
        elif selected_action == action_unassign:
            self.unassign_selected_stock_routines()
        elif selected_action == action_delete:
            self.delete_selected_stock()
        elif selected_action == action_select_all:
            self.select_all_visible_stocks()
        elif selected_action == action_select_unassigned:
            self.select_unassigned_visible_stocks()
        elif selected_action == action_clear:
            self.stock_table.clearSelection()
            self.on_stock_selection_changed()

    def confirm_open_routine_assign_from_context_menu(self) -> None:
        """
        종목등록설정 창 우클릭 루틴 지정 진입 전 확인창을 표시한다.

        우클릭으로 선택한 전체 종목 수와 실제 자동 체크 대상 수를 분리해서 안내한다.
        매매루틴지정 창에는 루틴 지정 가능 종목만 자동 체크 대상으로 전달되므로,
        확인창에서도 "선택 종목 전체를 넘긴다"는 식의 오해 소지가 없도록 표시한다.
        """
        selected_stocks = self.selected_registered_stocks()
        if not selected_stocks:
            QMessageBox.warning(
                self,
                "선택 오류",
                "루틴 지정할 종목을 1개 이상 선택하세요.",
            )
            return

        assignable_stocks, blocked_items = classify_routine_assign_targets(selected_stocks)
        selected_count = len(selected_stocks)
        assignable_count = len(assignable_stocks)
        blocked_count = len(blocked_items)

        def build_stock_preview(stocks: list[tuple[str, str]], limit: int = 10) -> str:
            if not stocks:
                return "- 없음"
            lines = [f"- {code} {name}" for code, name in stocks[:limit]]
            if len(stocks) > limit:
                lines.append(f"- ... 외 {len(stocks) - limit}개")
            return "\n".join(lines)

        def blocked_reason_text(item: dict[str, object]) -> str:
            reason = str(item.get("reason", "")).strip()
            status = str(item.get("status_display", item.get("status", ""))).strip()
            holding_qty = int(item.get("holding_qty", 0) or 0)
            pending_qty = int(item.get("pending_qty", 0) or 0)

            details: list[str] = []
            if reason:
                details.append(reason)
            if status:
                details.append(f"상태: {status}")
            if holding_qty:
                details.append(f"보유: {holding_qty}")
            if pending_qty:
                details.append(f"미체결: {pending_qty}")
            return " / ".join(details) if details else "루틴 지정 제한"

        blocked_preview_lines: list[str] = []
        for item in blocked_items[:10]:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            blocked_preview_lines.append(f"- {code} {name} ({blocked_reason_text(item)})")
        if blocked_count > 10:
            blocked_preview_lines.append(f"- ... 외 {blocked_count - 10}개")
        blocked_preview = "\n".join(blocked_preview_lines) if blocked_preview_lines else "- 없음"

        if assignable_count <= 0:
            QMessageBox.information(
                self,
                "루틴 지정 대상 없음",
                f"선택 종목: {selected_count}개\n\n"
                f"[루틴 지정 가능 종목: 0개]\n"
                "- 없음\n\n"
                f"[루틴 지정 제한 종목: {blocked_count}개]\n"
                f"{blocked_preview}\n\n"
                "루틴 지정 가능한 종목이 없어 매매루틴지정 창을 열지 않습니다.",
            )
            return

        message = (
            f"선택 종목: {selected_count}개\n\n"
            f"[루틴 지정 가능 종목: {assignable_count}개]\n"
            f"{build_stock_preview(assignable_stocks)}\n\n"
            f"[루틴 지정 제한 종목: {blocked_count}개]\n"
            f"{blocked_preview}"
        )

        if blocked_count > 0:
            message += "\n\n확인 후 창을 열면 제한 종목은 처리불가 리포트에 기록됩니다."

        message += "\n\n매매루틴지정 창을 여시겠습니까?"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("루틴 지정 확인")
        box.setText(message)
        open_button = box.addButton("열기", QMessageBox.YesRole)
        box.addButton("취소", QMessageBox.NoRole)
        box.setDefaultButton(open_button)
        box.exec_()

        if box.clickedButton() != open_button:
            return

        self.confirm_and_open_routine_assign(selected_stocks)

    def select_all_visible_stocks(self) -> None:
        """현재 화면에 표시된 모든 종목 행을 선택한다."""
        self.stock_table.clearSelection()
        selection_model = self.stock_table.selectionModel()
        if selection_model is None:
            return
        for row in range(self.stock_table.rowCount()):
            index = self.stock_table.model().index(row, 0)
            selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.on_stock_selection_changed()

    def select_unassigned_visible_stocks(self) -> None:
        """현재 화면에서 등록 루틴이 미등록인 종목만 선택한다."""
        self.stock_table.clearSelection()
        selection_model = self.stock_table.selectionModel()
        if selection_model is None:
            return
        for row in range(self.stock_table.rowCount()):
            routine_item = self.stock_table.item(row, 2)
            routine_text = routine_item.text().strip() if routine_item is not None else ""
            if routine_text != "미등록":
                continue
            index = self.stock_table.model().index(row, 0)
            selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.on_stock_selection_changed()

    def unassign_selected_stock_routines(self) -> None:
        """
        선택 종목의 루틴 연결만 해제한다.
        종목 자체와 runtime 폴더는 삭제하지 않는다.
        """
        selected_stocks = self.selected_registered_stocks()
        if not selected_stocks:
            QMessageBox.warning(self, "선택 오류", "루틴 해제할 종목을 1개 이상 선택하세요.")
            return

        allowed: list[tuple[str, str, str]] = []
        skipped_unassigned: list[str] = []
        blocked_items: list[dict[str, object]] = []

        for code, name in selected_stocks:
            can_unassign, routine_name, reasons = can_unassign_active_routine_from_stock(code, name)
            title = f"{code} {name}"
            if not routine_name and reasons and "등록 루틴이 없습니다." in reasons:
                skipped_unassigned.append(title)
                continue
            if can_unassign:
                allowed.append((code, name, routine_name))
            else:
                info = routine_action_guard_info(code, name)
                info["reasons"] = reasons
                blocked_items.append(info)

        if not allowed and not blocked_items:
            if skipped_unassigned:
                QMessageBox.information(self, "루틴 해제 없음", "선택 종목은 이미 미등록 상태입니다.")
            else:
                QMessageBox.information(self, "루틴 해제 없음", "루틴 해제할 종목이 없습니다.")
            return

        first_routine_name = allowed[0][2] if allowed else ""
        if not first_routine_name and blocked_items:
            first_routine_name = str(blocked_items[0].get("routine_name", "")).strip()

        confirm_dialog = RoutineUnassignConfirmDialog(
            routine_name=first_routine_name or "선택 루틴",
            removable_items=[(code, name) for code, name, _ in allowed],
            blocked_items=blocked_items,
            parent=self,
        )
        if confirm_dialog.exec_() != QDialog.Accepted:
            return

        removed_items: list[str] = []
        for code, name, routine_name in allowed:
            if update_base_stock_routines(code, name, []):
                ensure_single_real_trade_routine_for_stock(code, name)
                removed_items.append(f"{code},{name}({routine_name})")

        report_path = write_blocked_action_report("루틴 해제", blocked_items)

        if removed_items:
            append_changelog(
                "UPDATE",
                "기초종목.txt",
                f"종목등록설정 루틴 해제: {' / '.join(removed_items)} / runtime 폴더 유지",
            )

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        result_lines = [f"루틴 해제 완료: {len(removed_items)}개"]
        if blocked_items:
            result_lines.append(f"해제 불가: {len(blocked_items)}개")
            if report_path is not None:
                result_lines.append(f"리포트: {report_path.name}")
        if skipped_unassigned:
            result_lines.append(f"이미 미등록: {len(skipped_unassigned)}개")

        QMessageBox.information(self, "루틴 해제 결과", "\n".join(result_lines))


    def delete_selected_stock(self) -> None:
        selected_rows = self.stock_table.selectionModel().selectedRows()

        if not selected_rows:
            QMessageBox.warning(
                self,
                "선택 오류",
                "삭제할 종목을 1개 이상 선택하세요.",
            )
            return

        selected_stocks: list[tuple[str, str]] = []
        invalid_rows: list[int] = []

        for index in selected_rows:
            selected_row = index.row()
            code_item = self.stock_table.item(selected_row, 0)
            name_item = self.stock_table.item(selected_row, 1)

            if code_item is None or name_item is None:
                invalid_rows.append(selected_row + 1)
                continue

            code = code_item.text().strip()
            name = name_item.text().strip()

            if not code or not name:
                invalid_rows.append(selected_row + 1)
                continue

            selected_stocks.append((code, name))

        if invalid_rows:
            QMessageBox.warning(
                self,
                "삭제 오류",
                "선택한 종목 중 정보를 읽을 수 없는 행이 있습니다.\n\n"
                f"문제 행: {', '.join(str(row) for row in invalid_rows)}",
            )
            return

        if not selected_stocks:
            QMessageBox.warning(
                self,
                "선택 오류",
                "삭제할 종목 정보를 찾지 못했습니다.",
            )
            return

        immediate_items: list[dict[str, object]] = []
        force_items: list[dict[str, object]] = []
        blocked_items: list[dict[str, object]] = []

        # 같은 종목 행이 중복 선택되는 경우를 방어한다.
        seen_stocks: set[tuple[str, str]] = set()
        unique_stocks: list[tuple[str, str]] = []
        for code, name in selected_stocks:
            key = (code, name)
            if key in seen_stocks:
                continue
            seen_stocks.add(key)
            unique_stocks.append(key)

        for code, name in unique_stocks:
            category, title, reasons, runtime_dirs = stock_register_unavailable_reason(code, name)
            item = {
                "code": code,
                "name": name,
                "title": title,
                "reasons": reasons,
                "runtime_dirs": runtime_dirs,
            }
            if category == "immediate":
                immediate_items.append(item)
            elif category == "force":
                force_items.append(item)
            else:
                blocked_items.append(item)

        selected_force_items: list[dict[str, object]] = []
        blocked_report_items: list[dict[str, object]] = []
        for item in blocked_items:
            code = str(item.get("code", "")).strip()
            name = str(item.get("name", "")).strip()
            info = routine_action_guard_info(code, name)
            info["reasons"] = item.get("reasons", [])
            blocked_report_items.append(info)
        blocked_report_path = write_blocked_action_report("종목 삭제", blocked_report_items)

        if force_items or blocked_items:
            dialog = ForceUnregisterConfirmDialog(
                self,
                force_items=force_items,
                blocked_items=blocked_items,
                immediate_count=len(immediate_items),
            )
            dialog_result = dialog.exec_()
            if dialog_result == QDialog.Accepted:
                selected_force_items = dialog.selected_items()
            else:
                selected_force_items = []
                if not immediate_items and not force_items and blocked_items:
                    return

        process_items = immediate_items + selected_force_items

        if not process_items:
            if blocked_items or force_items:
                QMessageBox.information(
                    self,
                    "등록해제 없음",
                    "등록해제 처리할 종목이 선택되지 않았습니다.",
                )
            return

        stock_path = PROJECT_ROOT / "기초종목.txt"

        if not stock_path.exists():
            QMessageBox.warning(
                self,
                "삭제 오류",
                "기초종목.txt 파일이 없습니다.",
            )
            return

        force_targets = {(str(item["code"]), str(item["name"])) for item in selected_force_items}
        delete_targets = {(str(item["code"]), str(item["name"])) for item in process_items}
        lines = stock_path.read_text(encoding="utf-8").splitlines()

        new_lines: list[str] = []
        deleted_items: list[tuple[str, str]] = []

        for line in lines:
            stripped = line.strip()

            if not stripped:
                continue

            parts = [part.strip() for part in stripped.split(",")]

            if len(parts) >= 2 and (parts[0], parts[1]) in delete_targets:
                deleted_items.append((parts[0], parts[1]))
                continue

            new_lines.append(stripped)

        if not deleted_items:
            QMessageBox.information(
                self,
                "삭제 대상 없음",
                "기초종목.txt에서 선택한 종목을 찾지 못했습니다.",
            )
            return

        reset_failed_items: list[str] = []
        for item in selected_force_items:
            code = str(item.get("code", ""))
            name = str(item.get("name", ""))
            for routine_name, stock_dir in item.get("runtime_dirs", []):
                if not reset_runtime_state_for_force_unregister(stock_dir):
                    reset_failed_items.append(f"{code} {name} / {routine_name}")
                    continue
                append_stock_log(
                    stock_dir,
                    "FORCE_UNREGISTER_RESET",
                    "강제 등록해제로 state.json과 orders.json 현재 표시/판단값 초기화",
                )

        if reset_failed_items:
            preview_text = "\n".join(reset_failed_items[:10])
            if len(reset_failed_items) > 10:
                preview_text += f"\n... 외 {len(reset_failed_items) - 10}개"
            QMessageBox.warning(
                self,
                "상태 초기화 오류",
                "일부 종목의 state.json 초기화에 실패했습니다.\n"
                "기초종목.txt 등록해제는 아직 저장하지 않았습니다.\n\n"
                f"{preview_text}",
            )
            return

        stock_path.write_text(
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )

        try:
            deleted_text = " / ".join(f"{code},{name}" for code, name in deleted_items)
            force_text = " / ".join(f"{code},{name}" for code, name in sorted(force_targets))
            message = f"선택 종목 등록 해제: {deleted_text} / 루틴 runtime 폴더 유지"
            if force_text:
                message += f" / 강제 등록해제 상태/주문표시 초기화: {force_text}"
            append_changelog("UPDATE", "기초종목.txt", message)
        except Exception:
            pass

        self.refresh_stock_table()
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_all"):
            parent.refresh_all()

        blocked_count = len(blocked_items)
        force_skipped_count = len(force_items) - len(selected_force_items)
        info_lines = [
            f"등록해제 완료: {len(deleted_items)}개",
        ]
        if selected_force_items:
            info_lines.append(f"강제 등록해제 및 상태/주문표시 초기화: {len(selected_force_items)}개")
        if force_skipped_count > 0:
            info_lines.append(f"선택하지 않아 유지: {force_skipped_count}개")
        if blocked_count > 0:
            info_lines.append(f"등록해제 불가: {blocked_count}개")
            if blocked_report_path is not None:
                info_lines.append(f"처리불가 리포트 저장")

        result_message = " / ".join(info_lines)
        if isinstance(parent, MainWindow):
            parent.statusBar().showMessage(result_message, 7000)
        else:
            QMessageBox.information(self, "등록해제 결과", result_message)

    def selected_registered_stocks(self) -> list[tuple[str, str]]:
        """현재 화면에서 선택된 종목을 종목코드/종목명 기준으로 반환한다."""
        selected_rows = self.stock_table.selectionModel().selectedRows()
        selected: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for index in selected_rows:
            row = index.row()
            code_item = self.stock_table.item(row, 0)
            name_item = self.stock_table.item(row, 1)
            if code_item is None or name_item is None:
                continue

            code = code_item.text().strip()
            name = name_item.text().strip()
            key = (code, name)
            if not code or not name or key in seen:
                continue

            selected.append(key)
            seen.add(key)

        return selected

    def refresh_stock_table(self) -> None:
        stocks = read_base_stocks()
        keyword_text = self.stock_search_input.text().strip().lower() if hasattr(self, "stock_search_input") else ""
        keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]

        def stock_matches(stock: dict[str, object], keyword: str) -> bool:
            code = str(stock.get("code", "")).strip().lower()
            name = str(stock.get("name", "")).strip().lower()
            validation = str(stock.get("validation_status", "")).strip().lower()
            routines = stock.get("routines", [])
            routine_text = ",".join(str(item).strip().lower() for item in routines) if isinstance(routines, list) else str(routines).lower()
            routine_list = [str(item).strip() for item in routines if str(item).strip()] if isinstance(routines, list) else []
            registered_routine = routine_list[0] if routine_list else "미등록"
            operation_status = active_stock_register_status_display(code, name, registered_routine).lower()

            searchable_values = [
                code,
                name,
                validation,
                routine_text,
                operation_status,
            ]
            return any(keyword in value for value in searchable_values)

        if keywords:
            filtered: list[dict[str, object]] = []
            added_keys: set[tuple[str, str]] = set()

            for keyword in keywords:
                for stock in stocks:
                    key = (
                        str(stock.get("code", "")).strip(),
                        str(stock.get("name", "")).strip(),
                    )
                    if key in added_keys:
                        continue

                    if stock_matches(stock, keyword):
                        filtered.append(stock)
                        added_keys.add(key)

            stocks = filtered

        sort_column = self.stock_table.horizontalHeader().sortIndicatorSection()
        sort_order = self.stock_table.horizontalHeader().sortIndicatorOrder()

        self.stock_table.blockSignals(True)
        self.stock_table.setSortingEnabled(False)
        self.stock_table.setRowCount(len(stocks))

        for row, stock in enumerate(stocks):
            code = str(stock.get("code", "")).strip()
            name = str(stock.get("name", "")).strip()
            routines = stock.get("routines", [])

            if isinstance(routines, list):
                routine_list = [str(item).strip() for item in routines if str(item).strip()]
            else:
                routine_text_raw = str(routines).strip()
                routine_list = [routine_text_raw] if routine_text_raw else []

            # 등록 루틴 컬럼은 기초종목.txt에 실제 연결된 활성 루틴만 표시한다.
            # 루틴 폴더에 남아 있는 과거 runtime 폴더나 상태값은 이 창에서 표시하지 않는다.
            # 종목당 활성 루틴 1개 정책에 따라 첫 번째 루틴만 표시하고, 루틴이 없으면 미등록으로 표시한다.
            registered_routine = routine_list[0] if routine_list else "미등록"
            routine_tooltip = registered_routine
            operation_status = active_stock_register_status_display(code, name, registered_routine)

            values = [
                code,
                name,
                registered_routine,
                operation_status,
                str(stock.get("validation_status", "정상")),
            ]

            for col, value in enumerate(values):
                if col == 3:
                    if value == "미지정":
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignCenter)
                    elif value in ("미생성", "오류"):
                        item = QTableWidgetItem(value)
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    else:
                        item = create_auto_trade_status_item(value)
                        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                else:
                    item = QTableWidgetItem(value)
                    item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(routine_tooltip if col == 2 else value)
                self.stock_table.setItem(row, col, item)

        self.stock_table.resizeRowsToContents()
        self.stock_table.setSortingEnabled(True)
        if 0 <= sort_column < self.stock_table.columnCount():
            self.stock_table.sortItems(sort_column, sort_order)
        self.stock_table.blockSignals(False)
        self.stock_table.clearSelection()
        self.btn_delete_stock.setEnabled(False)

    def open_search_register_dialog(self) -> None:
        """
        검색식등록은 현재 단계에서 비활성화한다.
        이 메서드는 실수로 호출되어도 메시지창이나 검색창을 띄우지 않는다.
        """
        return

    def open_manual_register_dialog(self) -> None:
        """
        수동등록 버튼은 종목 라이브러리에서 직접 선택 등록한다.
        임의 종목코드/종목명 직접 입력 방식은 제공하지 않는다.
        """
        dialog = SearchStockRegisterDialog(self, title="수동등록")
        dialog.exec_()
        self.refresh_stock_table()

    def confirm_and_open_routine_assign(self, selected_stocks: list[tuple[str, str]]) -> None:
        """
        매매루틴지정 창을 연다.

        창 진입은 종목 선택/상태와 무관하게 허용한다.
        선택 종목이 있으면 루틴 변경 가능한 종목만 자동 체크 대상으로 넘기고,
        불가능한 종목은 창 진입을 막지 않고 처리불가 리포트만 남긴다.
        """
        auto_check_stocks: list[tuple[str, str]] = []
        blocked_items: list[dict[str, object]] = []

        if selected_stocks:
            auto_check_stocks, blocked_items = classify_routine_assign_targets(selected_stocks)
            report_path = write_blocked_action_report("루틴 지정 사전검사", blocked_items)

            if blocked_items:
                message = (
                    f"선택 종목 중 루틴 지정 불가: {len(blocked_items)}개"
                    " / 매매루틴지정 창은 열립니다."
                )
                if report_path is not None:
                    message += " / 처리불가 리포트 저장"

                parent = self.parent()
                if parent is not None and hasattr(parent, "statusBar"):
                    parent.statusBar().showMessage(message, 7000)
                else:
                    self.show_status(message) if hasattr(self, "show_status") else None

        dialog = RoutineAssignWindow(self, target_stocks=auto_check_stocks)
        dialog.exec_()
        self.refresh_stock_table()

    def open_routine_assign_window(self) -> None:
        selected_stocks = self.selected_registered_stocks()
        self.confirm_and_open_routine_assign(selected_stocks)

    def open_routine_assign_for_stock(self, item: QTableWidgetItem) -> None:
        """
        종목 행 더블클릭 시 해당 종목을 루틴 지정 사전 검사 후 매매루틴지정 창으로 넘긴다.
        """
        row = item.row()
        code_item = self.stock_table.item(row, 0)
        name_item = self.stock_table.item(row, 1)

        if code_item is None or name_item is None:
            return

        code = code_item.text().strip()
        name = name_item.text().strip()

        if not code or not name:
            return

        self.confirm_and_open_routine_assign([(code, name)])

    def open_latest_blocked_report(self) -> None:
        report_path = latest_blocked_action_report_path()
        if report_path is None:
            QMessageBox.information(
                self,
                "처리불가 리포트",
                "저장된 처리불가 리포트가 없습니다.",
            )
            return

        dialog = BlockedActionReportViewDialog(report_path, self)
        dialog.exec_()

    def open_integrity_check_window(self) -> None:
        dialog = IntegrityCheckWindow(self)
        dialog.exec_()
        self.refresh_stock_table()

    def is_duplicate_stock(self, code: str, name: str) -> bool:
        stocks = read_base_stocks()
        normalized_name = name.strip()

        for stock in stocks:
            existing_code = str(stock.get("code", "")).strip()
            existing_name = str(stock.get("name", "")).strip()

            if existing_code == code or existing_name == normalized_name:
                return True

        return False

    def not_implemented(self) -> None:
        QMessageBox.information(
            self,
            "안내",
            "이 기능은 다음 단계에서 구현합니다.",
        )


# ====================== gui_schedule_window 호환 복구 클래스 ======================
# 주의:
# - 현재 gui_schedule_window.py가 손상되어 gui_windows.py에서 요구하는 클래스가 누락된 상태를 복구한다.
# - 실제 운영환경설정은 gui_windows.py 쪽 OperationEnvironmentSettingsDialog가 담당한다.
# - 이 클래스들은 import 오류 방지 및 기존 버튼 호출 호환용이다.


class ScheduleOperationDialog(QDialog):
    """종목별 개별 시간 예외 설정창.

    전역 기본값은 운영환경설정에서 관리하고,
    이 창의 값은 선택 종목의 개별 예외시간으로 저장한다.
    """

    def __init__(self, *args, **kwargs) -> None:
        parent = kwargs.get("parent", None)
        start_time = kwargs.get("start_time", "09:30:00")
        end_buy_time = kwargs.get("end_buy_time", "13:30:00")
        selected_count = kwargs.get("selected_count", 1)

        if args:
            if isinstance(args[0], QWidget):
                parent = args[0]
                if len(args) > 1:
                    start_time = args[1]
                if len(args) > 2:
                    end_buy_time = args[2]
                if len(args) > 3:
                    selected_count = args[3]
            else:
                start_time = args[0]
                if len(args) > 1:
                    end_buy_time = args[1]
                if len(args) > 2:
                    selected_count = args[2]

        super().__init__(parent)
        self.setWindowTitle("종목 시간 예외 설정")
        self.resize(420, 230)
        self._selected_count = int(selected_count or 1)

        start_h, start_m = self._split_hhmm(start_time, "09:30:00")
        end_h, end_m = self._split_hhmm(end_buy_time, "13:30:00")

        main_layout = QVBoxLayout()

        notice = QLabel(
            f"선택된 {self._selected_count}종목 개별시간적용"
        )
        notice.setMinimumHeight(28)
        main_layout.addWidget(notice)

        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(6)
        form_layout.setVerticalSpacing(8)

        self.start_hour_combo = self._make_hour_combo(start_h)
        self.start_minute_combo = self._make_minute_combo(start_m)
        self.end_hour_combo = self._make_hour_combo(end_h)
        self.end_minute_combo = self._make_minute_combo(end_m)

        form_layout.addWidget(QLabel("시작"), 0, 0)
        form_layout.addWidget(self.start_hour_combo, 0, 1)
        form_layout.addWidget(QLabel("시"), 0, 2)
        form_layout.addWidget(self.start_minute_combo, 0, 3)
        form_layout.addWidget(QLabel("분"), 0, 4)

        form_layout.addWidget(QLabel("매수종료"), 1, 0)
        form_layout.addWidget(self.end_hour_combo, 1, 1)
        form_layout.addWidget(QLabel("시"), 1, 2)
        form_layout.addWidget(self.end_minute_combo, 1, 3)
        form_layout.addWidget(QLabel("분"), 1, 4)
        form_layout.setColumnStretch(5, 1)
        main_layout.addLayout(form_layout)

        guide = QLabel(
            "※ 기본값은 환경설정에서 변경"
        )
        guide.setStyleSheet("color: #555555;")
        main_layout.addWidget(guide)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_apply = QPushButton("적용")
        self.btn_cancel = QPushButton("취소")
        self.btn_apply.setMinimumWidth(82)
        self.btn_cancel.setMinimumWidth(82)
        self.btn_apply.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_apply)
        button_layout.addWidget(self.btn_cancel)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    def _make_hour_combo(self, value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems([f"{hour:02d}" for hour in range(24)])
        combo.setCurrentText(str(value).zfill(2))
        combo.setFixedWidth(58)
        return combo

    def _make_minute_combo(self, value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems([f"{minute:02d}" for minute in range(60)])
        combo.setCurrentText(str(value).zfill(2))
        combo.setFixedWidth(58)
        return combo

    def _split_hhmm(self, value: object, default: str) -> tuple[str, str]:
        text = str(value or "").strip() or default
        parts = text.split(":")
        try:
            hour = int(parts[0]) if len(parts) >= 1 else int(default.split(":")[0])
            minute = int(parts[1]) if len(parts) >= 2 else int(default.split(":")[1])
        except Exception:
            hour = int(default.split(":")[0])
            minute = int(default.split(":")[1])
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return f"{hour:02d}", f"{minute:02d}"

    def start_time(self) -> str:
        return f"{self.start_hour_combo.currentText()}:{self.start_minute_combo.currentText()}:00"

    def end_buy_time(self) -> str:
        return f"{self.end_hour_combo.currentText()}:{self.end_minute_combo.currentText()}:00"

    def accept(self) -> None:
        start_text = self.start_time()
        end_text = self.end_buy_time()
        if start_text >= end_text:
            QMessageBox.warning(
                self,
                "시간 설정 오류",
                "시작 시간은 매수종료 시간보다 빨라야 합니다.",
            )
            return
        super().accept()

class ScheduleTradeManagementDialog(QDialog):
    """기존 스케줄매매관리창 호환용 임시 클래스."""

    def __init__(self, *args, **kwargs) -> None:
        parent = kwargs.get("parent", None)
        if parent is None and args:
            parent = args[0] if isinstance(args[0], QWidget) else None
        super().__init__(parent)
        self.setWindowTitle("운영환경설정 안내")
        self.resize(560, 260)

        layout = QVBoxLayout()
        label = QLabel(
            "스케줄매매관리는 운영환경설정으로 통합되었습니다.\n\n"
            "자동매매설정 창의 '운영환경설정' 버튼을 사용하세요."
        )
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

        btn_close = QPushButton("닫기")
        btn_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_close)
        layout.addLayout(row)

        self.setLayout(layout)

