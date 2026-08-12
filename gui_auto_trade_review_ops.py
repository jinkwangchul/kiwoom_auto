# -*- coding: utf-8 -*-
"""
gui_auto_trade_review_ops.py

자동매매설정창의 검토관리 열기 처리 헬퍼.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt

from gui_review_required_window import GlobalReviewRequiredWindow
from gui_window_policy import persistent_feature_owner
def auto_trade_open_review_required_window(window) -> None:
    """검토관리창은 루틴별이 아니라 프로그램 전체 단위로 연다."""
    owner = persistent_feature_owner(window)
    owner_opener = getattr(owner, "open_review_required_window", None)
    if callable(owner_opener):
        owner_opener()
        window.review_required_window = getattr(owner, "review_required_window", None)
        return
    existing = getattr(window, "__dict__", {}).get("review_required_window")
    if existing is not None:
        try:
            if existing.isVisible():
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
        except RuntimeError:
            pass
    dialog = GlobalReviewRequiredWindow(parent=window)
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    window.review_required_window = dialog
    dialog.finished.connect(lambda _result: window.refresh_all())
    dialog.destroyed.connect(
        lambda _obj=None, target=dialog: (
            setattr(window, "review_required_window", None)
            if getattr(window, "review_required_window", None) is target
            else None
        )
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
