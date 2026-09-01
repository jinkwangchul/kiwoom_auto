from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QLabel, QLineEdit, QPushButton, QVBoxLayout


class IndicatorFollowCommonWidgetsMixin:
    def _section_title(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; padding: 2px 0px;")
        return label

    def _make_panel_card(self, title, status, callback=None):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setMinimumHeight(86)

        layout = QVBoxLayout(frame)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_label = QLabel(status)
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")

        layout.addWidget(title_label)
        layout.addWidget(status_label)

        button = None
        if callback is not None:
            button = QPushButton("열기")
            button.clicked.connect(callback)
            layout.addWidget(button)
        else:
            spacer = QLabel("")
            layout.addWidget(spacer)

        return {
            "frame": frame,
            "title": title_label,
            "status": status_label,
            "button": button,
        }

    def _set_card_status(self, card, text, kind="normal"):
        card["status"].setText(text)

        if kind == "active":
            color = "#0a7a2f"
        elif kind == "inactive":
            color = "#777"
        elif kind == "locked":
            color = "#a65f00"
        elif kind == "error":
            color = "#b00020"
        else:
            color = "#333"

        card["status"].setStyleSheet(
            f"font-size: 16px; font-weight: bold; padding: 4px; color: {color};"
        )

    def _readonly_line(self):
        line = QLineEdit()
        line.setReadOnly(True)
        line.setFrame(False)
        line.setStyleSheet("background: transparent; border: none; padding: 1px;")
        return line
