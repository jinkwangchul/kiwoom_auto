# -*- coding: utf-8 -*-
"""
gui_config_window.py

환경설정 기능 안내/진입점.

현재 단계에서는 실제 환경설정 편집 기능이 아직 구현되지 않았으므로,
기존 gui_windows.py 내부 안내 메시지를 독립 모듈로 분리한다.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QMessageBox, QWidget


def show_deferred_config_message(parent: QWidget | None = None) -> None:
    """
    환경설정 기능이 아직 구현되지 않았음을 안내한다.
    기존 동작을 유지하기 위한 분리 함수이다.
    """
    QMessageBox.information(
        parent,
        "다음 단계 구현",
        "현재 단계에서는 자동매매설정 목록 표시만 구현되었습니다.",
    )
