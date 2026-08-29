# -*- coding: utf-8 -*-
"""Persistent tooltips for clipped stock names in item views."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt5.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, Qt
from PyQt5.QtWidgets import QAbstractItemView, QApplication, QToolTip


TOOLTIP_POINT_SIZE = 12
_PERSISTENT_TOOLTIP_MSEC = 2_000_000_000


class PersistentStockNameToolTipFilter(QObject):
    """Keep a clipped stock-name tooltip visible until its cell is left."""

    def __init__(
        self,
        view: QAbstractItemView,
        columns: Iterable[int],
        *,
        accept_index: Callable[[QModelIndex], bool] | None = None,
        accept_position: Callable[[QModelIndex, object], bool] | None = None,
        source_column: int | None = None,
        tooltip_point_size: float | None = None,
        tooltip_resolver: Callable[[QModelIndex, str], str] | None = None,
    ) -> None:
        super().__init__(view)
        self._view = view
        self._columns = frozenset(int(column) for column in columns)
        self._accept_index = accept_index
        self._accept_position = accept_position
        self._source_column = source_column
        self._tooltip_resolver = tooltip_resolver
        self._tooltip_point_size = (
            float(tooltip_point_size)
            if tooltip_point_size is not None
            else float(TOOLTIP_POINT_SIZE)
        )
        self._active_index = QPersistentModelIndex()
        self._active_text = ""

        viewport = view.viewport()
        self._viewport = viewport
        view.setMouseTracking(True)
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)
        self._apply_tooltip_font()

    def _apply_tooltip_font(self) -> None:
        marker = "/* persistent-stock-name-tooltip */"
        current = self._view.styleSheet()
        if marker in current:
            return
        point_size = f"{self._tooltip_point_size:g}"
        suffix = f"\n{marker}\nQToolTip {{ font-size: {point_size}pt; }}"
        self._view.setStyleSheet(current + suffix)

    def _tooltip_text(self, index: QModelIndex, pos=None) -> str:
        if not index.isValid() or index.column() not in self._columns:
            return ""
        if self._accept_index is not None and not self._accept_index(index):
            return ""
        if (
            pos is not None
            and self._accept_position is not None
            and not self._accept_position(index, pos)
        ):
            return ""
        source_index = self._source_index(index)
        text = str(source_index.data(Qt.ToolTipRole) or "").strip()
        if self._tooltip_resolver is not None:
            try:
                text = str(self._tooltip_resolver(source_index, text) or "").strip()
            except Exception:
                pass
        return text

    def _source_index(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid() or self._source_column is None:
            return index
        return index.sibling(index.row(), self._source_column)

    def _show(self, index: QModelIndex, global_pos, pos=None) -> None:
        text = self._tooltip_text(index, pos)
        if not text:
            self._hide()
            return
        QToolTip.showText(
            global_pos,
            text,
            self._view.viewport(),
            self._view.visualRect(index),
            _PERSISTENT_TOOLTIP_MSEC,
        )
        self._active_index = QPersistentModelIndex(self._source_index(index))
        self._active_text = text

    def _hide(self) -> None:
        if self._active_index.isValid() or self._active_text:
            QToolTip.hideText()
            for widget in QApplication.topLevelWidgets():
                if widget.objectName() == "qtooltip_label":
                    widget.hide()
        self._active_index = QPersistentModelIndex()
        self._active_text = ""

    def eventFilter(self, watched, event) -> bool:
        if watched is not self._viewport:
            return False

        event_type = event.type()
        if event_type == QEvent.ToolTip:
            index = self._view.indexAt(event.pos())
            text = self._tooltip_text(index, event.pos())
            if text:
                self._show(index, event.globalPos(), event.pos())
                event.accept()
                return True
            self._hide()
            return False

        if event_type == QEvent.MouseMove and self._active_index.isValid():
            index = self._view.indexAt(event.pos())
            source_index = self._source_index(index)
            text = self._tooltip_text(index, event.pos())
            if (
                QPersistentModelIndex(source_index) != self._active_index
                or not text
            ):
                if text:
                    self._show(
                        index,
                        self._view.viewport().mapToGlobal(event.pos()),
                        event.pos(),
                    )
                else:
                    self._hide()
            elif not QToolTip.isVisible() and self._active_text:
                self._show(
                    index,
                    self._view.viewport().mapToGlobal(event.pos()),
                    event.pos(),
                )
            return False

        if event_type in {QEvent.Leave, QEvent.Hide}:
            self._hide()
        return False


def install_persistent_stock_name_tooltips(
    view: QAbstractItemView,
    columns: Iterable[int],
    *,
    accept_index: Callable[[QModelIndex], bool] | None = None,
    accept_position: Callable[[QModelIndex, object], bool] | None = None,
    source_column: int | None = None,
    tooltip_point_size: float | None = None,
    tooltip_resolver: Callable[[QModelIndex, str], str] | None = None,
) -> PersistentStockNameToolTipFilter:
    return PersistentStockNameToolTipFilter(
        view,
        columns,
        accept_index=accept_index,
        accept_position=accept_position,
        source_column=source_column,
        tooltip_point_size=tooltip_point_size,
        tooltip_resolver=tooltip_resolver,
    )
