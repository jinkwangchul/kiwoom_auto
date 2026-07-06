# -*- coding: utf-8 -*-
"""
gui_blocked_report_window.py

처리불가 리포트 생성/저장/조회 창.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui_common_utils import sanitize_path_part
from state_policy import auto_trade_status_display


PROJECT_ROOT = Path(__file__).resolve().parent
BLOCKED_ACTION_REPORT_DIR = PROJECT_ROOT / "reports" / "blocked_actions"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def report_status_display_text(raw_status: object) -> str:
    """처리불가 리포트의 운영상태 표시명을 state_policy 기준으로 통일한다."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


def blocked_action_report_text(
    action_name: str,
    blocked_items: list[dict[str, object]],
    target_routine: str = "",
) -> str:
    """처리불가 누적 리포트에 추가할 1회 발생 블록을 생성한다."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"[{now_text()}]")
    lines.append(f"작업종류: {action_name}")
    if target_routine:
        lines.append(f"대상 루틴: {target_routine}")
    lines.append(f"처리불가 종목 수: {len(blocked_items)}개")
    lines.append("-" * 70)

    for index, item in enumerate(blocked_items, start=1):
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        routine_name = str(item.get("routine_name", "")).strip() or "미등록"
        display_status = report_status_display_text(item.get("display_status", ""))
        holding_qty = item.get("holding_qty", 0)
        buy_pending_qty = item.get("buy_pending_qty", 0)
        sell_pending_qty = item.get("sell_pending_qty", 0)
        reasons = item.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        stock_dir = item.get("stock_dir")
        state_path = str(Path(stock_dir) / "state.json") if stock_dir else "-"
        orders_path = str(Path(stock_dir) / "orders.json") if stock_dir else "-"

        lines.append(f"{index}. {code} {name}")
        lines.append(f"   - 차단 사유: {', '.join(str(reason) for reason in reasons) if reasons else '-'}")
        lines.append(f"   - 현재 루틴: {routine_name}")
        lines.append(f"   - 운영상태: {display_status}")
        lines.append(f"   - 보유수량: {holding_qty}")
        lines.append(f"   - 현재 미체결: 매수 {buy_pending_qty} / 매도 {sell_pending_qty}")
        lines.append("   - 권장 조치: HTS에서 보유수량과 현재 미체결 주문을 확인한 뒤 감시종료 또는 검토관리 절차로 상태를 정리하세요.")
        lines.append(f"   - state.json: {state_path}")
        lines.append(f"   - orders.json: {orders_path}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_blocked_action_report(
    action_name: str,
    blocked_items: list[dict[str, object]],
    target_routine: str = "",
) -> Path | None:
    """처리불가 종목 리포트를 1일 1파일 누적 방식으로 저장한다."""
    if not blocked_items:
        return None

    try:
        BLOCKED_ACTION_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        today_text = datetime.now().strftime("%Y%m%d")
        report_path = BLOCKED_ACTION_REPORT_DIR / f"{today_text}_처리불가_누적리포트.txt"

        entry_text = blocked_action_report_text(
            action_name,
            blocked_items,
            target_routine=target_routine,
        )

        if report_path.exists():
            with report_path.open("a", encoding="utf-8") as file:
                file.write("\n")
                file.write(entry_text)
        else:
            header_lines = [
                "처리불가 누적 리포트",
                f"작성일자: {datetime.now().strftime('%Y-%m-%d')}",
                "작성방식: 1일 1파일 누적 기록",
                "",
            ]
            with report_path.open("w", encoding="utf-8") as file:
                file.write("\n".join(header_lines))
                file.write(entry_text)

        return report_path
    except Exception:
        return None


def latest_blocked_action_report_path() -> Path | None:
    """가장 최근 처리불가 리포트 경로를 반환한다."""
    if not BLOCKED_ACTION_REPORT_DIR.exists():
        return None
    reports = [path for path in BLOCKED_ACTION_REPORT_DIR.glob("*.txt") if path.is_file()]
    if not reports:
        return None
    reports.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[0]


def blocked_items_preview(blocked_items: list[dict[str, object]], limit: int = 10) -> str:
    """메시지창에 표시할 처리 불가 목록 요약을 만든다."""
    lines: list[str] = []
    for item in blocked_items[:limit]:
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        display_status = str(item.get("display_status", "")).strip() or "-"
        reasons = item.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        lines.append(f"{code} {name} / 상태: {display_status} / 사유: {', '.join(str(reason) for reason in reasons)}")
    if len(blocked_items) > limit:
        lines.append(f"... 외 {len(blocked_items) - limit}개")
    return "\n".join(lines)


class BlockedActionReportViewDialog(QDialog):
    """저장된 처리불가 리포트를 읽기 전용으로 표시한다."""

    def __init__(self, report_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report_path = report_path
        self.setWindowTitle("처리불가 리포트")
        self.resize(820, 620)

        main_layout = QVBoxLayout()
        self.path_label = QLabel(str(report_path))
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.btn_close = QPushButton("닫기")

        try:
            content = report_path.read_text(encoding="utf-8")
        except Exception as exc:
            content = f"리포트를 읽는 중 오류가 발생했습니다.\n\n{exc}"

        self.text_edit.setPlainText(content)
        self.btn_close.clicked.connect(self.close)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_close)

        main_layout.addWidget(QLabel("리포트 파일"))
        main_layout.addWidget(self.path_label)
        main_layout.addWidget(self.text_edit)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)
