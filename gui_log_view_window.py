# -*- coding: utf-8 -*-
"""
gui_log_view_window.py

종목별 logs 폴더 내용을 표시하는 로그 보기 창.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LogViewWindow(QDialog):
    """
    로그 보기 창 v20.7.

    선택한 루틴/종목 폴더의 logs 폴더를 읽어
    일별 로그 파일 내용을 표시한다.
    """

    def __init__(
        self,
        stock_dir: Path,
        routine_name: str,
        stock_code: str,
        stock_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.stock_dir = stock_dir
        self.routine_name = routine_name
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.logs_dir = stock_dir / "logs"

        self.setWindowTitle(f"로그 보기 - {stock_code} {stock_name}")
        self.resize(980, 640)

        self.summary_label = QLabel("")
        self.date_combo = QComboBox()
        self.log_text = QTextEdit()
        self.btn_refresh = QPushButton("새로고침")
        self.btn_close = QPushButton("닫기")

        self._setup_ui()
        self._connect_events()
        self.refresh_logs()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        button_layout = QHBoxLayout()

        title_label = QLabel(
            f"루틴: {self.routine_name}  |  종목: {self.stock_code} {self.stock_name}"
        )
        title_label.setWordWrap(True)

        self.summary_label.setWordWrap(True)
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(460)
        self.log_text.setStyleSheet(
            "background-color: white;"
            "border: 1px solid #d0d0d0;"
            "padding: 6px;"
            "font-family: Consolas, Malgun Gothic, monospace;"
        )
        self.date_combo.setMinimumWidth(180)

        top_layout.addWidget(QLabel("날짜"))
        top_layout.addWidget(self.date_combo)
        top_layout.addStretch(1)

        button_layout.addStretch(1)
        button_layout.addWidget(self.btn_refresh)
        button_layout.addWidget(self.btn_close)

        main_layout.addWidget(title_label)
        main_layout.addWidget(self.summary_label)
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.log_text)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _connect_events(self) -> None:
        self.date_combo.currentIndexChanged.connect(self.load_selected_log)
        self.btn_refresh.clicked.connect(self.refresh_logs)
        self.btn_close.clicked.connect(self.close)

    def log_files(self) -> list[Path]:
        if not self.logs_dir.exists():
            return []

        files = [
            path for path in self.logs_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".log"
        ]
        files.sort(key=lambda path: path.name)
        return files

    def log_display_name(self, path: Path) -> str:
        stem = path.stem.strip()
        if len(stem) == 8 and stem.isdigit():
            return f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
        return stem

    def refresh_logs(self) -> None:
        previous_value = self.date_combo.currentData()
        files = self.log_files()

        self.date_combo.blockSignals(True)
        self.date_combo.clear()

        if files:
            self.date_combo.addItem("전체 로그", "__ALL__")
            for file_path in reversed(files):
                self.date_combo.addItem(self.log_display_name(file_path), str(file_path))

            restore_index = 0
            if previous_value:
                for index in range(self.date_combo.count()):
                    if self.date_combo.itemData(index) == previous_value:
                        restore_index = index
                        break
            self.date_combo.setCurrentIndex(restore_index)
        else:
            self.date_combo.addItem("로그 없음", "__NONE__")

        self.date_combo.blockSignals(False)
        self.load_selected_log()

    def load_selected_log(self) -> None:
        selected_value = self.date_combo.currentData()
        files = self.log_files()

        if not self.logs_dir.exists():
            self.summary_label.setText(f"로그 폴더가 없습니다: {self.logs_dir}")
            self.log_text.setPlainText("로그 폴더가 아직 생성되지 않았습니다.")
            return

        if not files:
            self.summary_label.setText(f"로그 파일: 0개  |  위치: {self.logs_dir}")
            self.log_text.setPlainText("표시할 로그 파일이 없습니다.")
            return

        if selected_value == "__ALL__":
            parts: list[str] = []
            for file_path in files:
                parts.append(f"===== {self.log_display_name(file_path)} / {file_path.name} =====")
                parts.append(self.read_log_file(file_path))
                parts.append("")
            self.summary_label.setText(
                f"로그 파일: {len(files)}개  |  선택: 전체 로그  |  위치: {self.logs_dir}"
            )
            self.log_text.setPlainText("\n".join(parts).rstrip())
            return

        selected_path = Path(str(selected_value)) if selected_value else None
        if selected_path is None or not selected_path.exists():
            self.summary_label.setText(f"로그 파일: {len(files)}개  |  선택 파일을 찾을 수 없음")
            self.log_text.setPlainText("선택한 로그 파일을 찾을 수 없습니다. 새로고침을 실행하세요.")
            return

        self.summary_label.setText(
            f"로그 파일: {len(files)}개  |  선택: {self.log_display_name(selected_path)}  |  위치: {selected_path}"
        )
        self.log_text.setPlainText(self.read_log_file(selected_path))

    def read_log_file(self, file_path: Path) -> str:
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = file_path.read_text(encoding="cp949")
            except Exception as exc:
                return f"로그 파일을 읽을 수 없습니다.\n\n{exc}"
        except Exception as exc:
            return f"로그 파일을 읽을 수 없습니다.\n\n{exc}"

        return text.rstrip() if text.strip() else "로그 내용이 없습니다."
