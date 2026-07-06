# -*- coding: utf-8 -*-
"""
gui_routine_assign_utils.py

매매루틴 지정/해제 결과 메시지 생성 유틸.
이 파일에는 창 클래스나 PyQt 위젯을 두지 않는다.
"""

from __future__ import annotations


def _clean_text(value: object, default: str = "-") -> str:
    text = str(value if value is not None else "").strip()
    return text or default


def _item_label(item: object) -> str:
    if isinstance(item, dict):
        code = _clean_text(item.get("code", ""))
        name = _clean_text(item.get("name", ""))
        reason = item.get("reason", item.get("reasons", ""))
        if isinstance(reason, list):
            reason_text = ", ".join(_clean_text(part, "") for part in reason if _clean_text(part, ""))
        else:
            reason_text = _clean_text(reason, "")
        base = f"{code} {name}".strip()
        return f"{base}: {reason_text}" if reason_text else base

    if isinstance(item, (list, tuple)):
        if len(item) >= 2:
            return f"{_clean_text(item[0])} {_clean_text(item[1])}".strip()
        if len(item) == 1:
            return _clean_text(item[0])

    return _clean_text(item)


def _preview_lines(title: str, items: list[object], limit: int = 8) -> list[str]:
    if not items:
        return []
    lines = [f"{title}: {len(items)}개"]
    for item in items[:limit]:
        lines.append(f"- {_item_label(item)}")
    if len(items) > limit:
        lines.append(f"- 외 {len(items) - limit}개")
    return lines


def build_routine_assign_status_text(applied_count: int = 0, blocked_count: int = 0, skipped_count: int = 0) -> str:
    parts = [f"지정 {int(applied_count)}개"]
    if blocked_count:
        parts.append(f"불가 {int(blocked_count)}개")
    if skipped_count:
        parts.append(f"제외 {int(skipped_count)}개")
    return " / ".join(parts)


def build_routine_unassign_status_text(removed_count: int = 0, blocked_count: int = 0, skipped_count: int = 0) -> str:
    parts = [f"해제 {int(removed_count)}개"]
    if blocked_count:
        parts.append(f"불가 {int(blocked_count)}개")
    if skipped_count:
        parts.append(f"제외 {int(skipped_count)}개")
    return " / ".join(parts)


def build_routine_assign_result_lines(
    routine_name: str,
    applied_items: list[object] | None = None,
    blocked_items: list[object] | None = None,
    skipped_items: list[object] | None = None,
    report_name: str | None = None,
) -> list[str]:
    applied_items = applied_items or []
    blocked_items = blocked_items or []
    skipped_items = skipped_items or []

    lines = [f"{len(applied_items)}개 종목이 {routine_name}에 연결되었습니다."]
    lines += _preview_lines("처리 불가", blocked_items)
    lines += _preview_lines("처리 제외", skipped_items)
    if report_name:
        lines.append(f"리포트: {report_name}")
    return lines


def build_routine_unassign_result_lines(
    routine_name: str,
    removed_items: list[object] | None = None,
    blocked_items: list[object] | None = None,
    skipped_items: list[object] | None = None,
    report_name: str | None = None,
) -> list[str]:
    removed_items = removed_items or []
    blocked_items = blocked_items or []
    skipped_items = skipped_items or []

    lines = [f"{len(removed_items)}개 종목의 {routine_name} 연결이 해제되었습니다."]
    lines += _preview_lines("해제 불가", blocked_items)
    lines += _preview_lines("처리 제외", skipped_items)
    if report_name:
        lines.append(f"리포트: {report_name}")
    return lines
