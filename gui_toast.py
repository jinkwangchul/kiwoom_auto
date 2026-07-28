# -*- coding: utf-8 -*-

from __future__ import annotations

from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)


class ToastMessage(QFrame):
    """Reusable, non-modal notification anchored to a parent widget."""

    def __init__(
        self,
        parent: QWidget,
        message: str,
        duration_ms: int,
        position: str = "center",
    ) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self._position = position
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setObjectName("commonToastMessage")

        self._label = QLabel(str(message), self)
        self._label.setObjectName("commonToastText")
        self._label.setAlignment(Qt.AlignCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.addWidget(self._label)

        self.setStyleSheet(
            """
            QFrame#commonToastMessage {
                background-color: rgba(31, 41, 55, 235);
                border: 1px solid rgba(55, 65, 81, 235);
                border-radius: 7px;
            }
            QLabel#commonToastText {
                color: #ffffff;
                font-weight: 600;
            }
            """
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(duration_ms)))
        self._timer.timeout.connect(self.close)

        parent.installEventFilter(self)

    def show_at_parent_center(self) -> None:
        self._position = "center"
        self.show_at_parent_position()

    def show_at_parent_position(self) -> None:
        self.adjustSize()
        self._move_to_parent_position()
        self.show()
        self.raise_()
        if self._timer.interval() > 0:
            self._timer.start()

    def _move_to_parent_center(self) -> None:
        self._position = "center"
        self._move_to_parent_position()

    def _move_to_parent_position(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return

        parent_rect = parent.frameGeometry()
        if self._position == "bottom_right":
            margin = 18
            x = parent_rect.right() - self.width() - margin + 1
            y = parent_rect.bottom() - self.height() - margin + 1
        else:
            x = parent_rect.left() + (parent_rect.width() - self.width()) // 2
            y = parent_rect.top() + (parent_rect.height() - self.height()) // 2
        self.move(x, y)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if (
            watched is self.parentWidget()
            and event.type() in (QEvent.Move, QEvent.Resize)
            and self.isVisible()
        ):
            self._move_to_parent_position()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: object) -> None:
        parent = self.parentWidget()
        if parent is not None:
            parent.removeEventFilter(self)
        super().closeEvent(event)

    def message(self) -> str:
        return self._label.text()

    def duration_ms(self) -> int:
        return self._timer.interval()


def show_toast(
    parent: QWidget,
    message: str,
    duration_ms: int = 2000,
    position: str = "center",
) -> ToastMessage:
    if position not in {"center", "bottom_right"}:
        raise ValueError(f"Unsupported toast position: {position}")

    previous = getattr(parent, "_common_toast_message", None)
    if isinstance(previous, ToastMessage):
        previous.close()

    toast = ToastMessage(parent, message, duration_ms, position=position)
    parent._common_toast_message = toast

    def clear_reference() -> None:
        if getattr(parent, "_common_toast_message", None) is toast:
            parent._common_toast_message = None

    toast.destroyed.connect(clear_reference)
    toast.show_at_parent_position()
    return toast
