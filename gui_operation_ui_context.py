# -*- coding: utf-8 -*-
"""Resolve the visible QWidget used by shared GUI operation helpers."""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget


def operation_dialog_parent(operation_context):
    getter = getattr(operation_context, "operation_message_parent", None)
    if callable(getter):
        parent = getter()
        if isinstance(parent, QWidget):
            return parent
    if isinstance(operation_context, QWidget):
        return operation_context
    parent_getter = getattr(operation_context, "parent", None)
    parent = parent_getter() if callable(parent_getter) else None
    return parent if isinstance(parent, QWidget) else operation_context
