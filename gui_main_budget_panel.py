# -*- coding: utf-8 -*-
"""
gui_main_budget_panel.py

메인 관제창 예산 현황 표시 전용 헬퍼.

현재 범위:
- UI 표시용 합계 계산만 수행한다.
- 예산 저장, 주문수량 산출, 매수 제한, 루틴/종목 배분 로직은 수행하지 않는다.
"""

from __future__ import annotations

from typing import Any

from gui_base_stock_service import read_base_stocks
from gui_routine_registry import get_routine_dirs, read_routine_budget


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _format_money(value: int) -> str:
    return f"{int(value):,}"


def _assigned_stock_count() -> int:
    count = 0
    for stock in read_base_stocks():
        routines = stock.get("routines", [])
        if isinstance(routines, list):
            if any(str(item).strip() for item in routines):
                count += 1
        elif str(routines or "").strip():
            count += 1
    return count


def collect_main_budget_summary() -> dict[str, object]:
    """관제창 예산 현황판에 표시할 합계 정보를 만든다."""
    routine_dirs = get_routine_dirs()

    total_budget = 0
    used_budget = 0
    available_budget = 0
    budget_error_count = 0

    for routine_dir in routine_dirs:
        try:
            budget = read_routine_budget(routine_dir)
            total_budget += int(budget.get("total_budget", 0))
            used_budget += int(budget.get("used_budget", 0))
            available_budget += int(budget.get("available_budget", 0))
        except Exception:
            budget_error_count += 1

    if total_budget <= 0:
        usage_rate_text = "-"
        status_text = "예산 미설정"
    else:
        usage_rate = (used_budget / total_budget) * 100
        usage_rate_text = f"{usage_rate:.1f}%"
        if available_budget < 0:
            status_text = "초과"
        elif budget_error_count > 0:
            status_text = "확인필요"
        else:
            status_text = "정상"

    return {
        "routine_count": len(routine_dirs),
        "assigned_stock_count": _assigned_stock_count(),
        "total_budget": total_budget,
        "used_budget": used_budget,
        "available_budget": available_budget,
        "usage_rate_text": usage_rate_text,
        "status_text": status_text,
    }


def update_main_budget_panel(window) -> None:
    """MainWindow의 예산 현황 QLabel들을 갱신한다."""
    summary = collect_main_budget_summary()

    label_map = {
        "budget_total_label": _format_money(int(summary.get("total_budget", 0))),
        "budget_used_label": _format_money(int(summary.get("used_budget", 0))),
        "budget_available_label": _format_money(int(summary.get("available_budget", 0))),
        "budget_usage_rate_label": str(summary.get("usage_rate_text", "-")),
        "budget_routine_count_label": str(summary.get("routine_count", 0)),
        "budget_stock_count_label": str(summary.get("assigned_stock_count", 0)),
        "budget_status_label": str(summary.get("status_text", "확인 전")),
    }

    for attr_name, text in label_map.items():
        label = getattr(window, attr_name, None)
        if label is not None:
            label.setText(text)
