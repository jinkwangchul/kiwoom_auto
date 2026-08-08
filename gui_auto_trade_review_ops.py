# -*- coding: utf-8 -*-
"""
gui_auto_trade_review_ops.py

자동매매설정창의 검토관리 열기 처리 헬퍼.
"""

from __future__ import annotations

from gui_review_required_window import GlobalReviewRequiredWindow
def auto_trade_open_review_required_window(window) -> None:
    """검토관리창은 루틴별이 아니라 프로그램 전체 단위로 연다."""
    dialog = GlobalReviewRequiredWindow(parent=window)
    dialog.exec_()
    window.refresh_all()
