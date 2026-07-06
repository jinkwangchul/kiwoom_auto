# -*- coding: utf-8 -*-

"""
gui_main.py

키움 OpenAPI 자동매매 시스템 GUI 실행 파일.
""" 

from __future__ import annotations

import sys
import traceback

from PyQt5.QtWidgets import QApplication, QMessageBox

from gui_windows import MainWindow


def main() -> int:
    app = QApplication(sys.argv)

    try:
        window = MainWindow()
        window.show()
        return app.exec_()

    except Exception as exc:
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