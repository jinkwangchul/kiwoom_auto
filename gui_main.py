# -*- coding: utf-8 -*-

"""
gui_main.py

키움 OpenAPI 자동매매 시스템 GUI 실행 파일.
""" 

from __future__ import annotations

import sys
import traceback

from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QApplication, QMessageBox

from event_journal_production import (
    install_global_exception_observers,
    observe_production_exception,
)
from gui_windows import MainWindow


def center_main_window_on_active_screen(
    app: QApplication,
    window: MainWindow,
) -> bool:
    screen = app.screenAt(QCursor.pos())
    if screen is None:
        screen = window.screen()
    if screen is None:
        screen = app.primaryScreen()
    if screen is None:
        return False

    frame_geometry = window.frameGeometry()
    frame_geometry.moveCenter(screen.availableGeometry().center())
    window.move(frame_geometry.topLeft())
    return True


def main() -> int:
    install_global_exception_observers()
    app = QApplication(sys.argv)

    try:
        window = MainWindow()
        window.show()
        center_main_window_on_active_screen(app, window)
        return app.exec_()

    except Exception as exc:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        observe_production_exception(
            exc_type,
            exc_value,
            exc_traceback,
            component="main_gui",
            operation="main",
            source="gui_main.main",
            target_id="main_window",
            target_name="메인 GUI",
            reason_code="GUI_MAIN_FAILED",
        )
        error_text = traceback.format_exc()
        print(error_text)

        QMessageBox.critical(
            None,
            "GUI 실행 오류",
            f"GUI 실행 중 오류가 발생했습니다.\n\n{exc}\n\n"
            "자세한 내용은 PowerShell 출력 내용을 확인하세요.",
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
