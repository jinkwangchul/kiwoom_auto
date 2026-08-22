"""Shared UI-only Group and RoutineInstance title display contract."""

from __future__ import annotations


TREE_TITLE_DISPLAY_CHARS = 10
TREE_TITLE_PREFIX_CHARS = 9


def tree_title_text(display_name: object) -> str:
    text = str(display_name or "").strip()
    if len(text) <= TREE_TITLE_DISPLAY_CHARS:
        return text
    return f"{text[:TREE_TITLE_PREFIX_CHARS]}..."


def tree_title_tooltip(display_name: object) -> str:
    text = str(display_name or "").strip()
    return text if len(text) > TREE_TITLE_DISPLAY_CHARS else ""


def tree_title_slot_width(font_metrics, *, padding: int = 12) -> int:
    samples = (
        "가" * TREE_TITLE_DISPLAY_CHARS,
        ("가" * TREE_TITLE_PREFIX_CHARS) + "...",
    )
    text_width = max(
        max(
            font_metrics.horizontalAdvance(sample),
            font_metrics.boundingRect(sample).width(),
        )
        for sample in samples
    )
    return text_width + max(0, int(padding))
