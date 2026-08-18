# -*- coding: utf-8 -*-
"""Resolve the visible QWidget used by shared GUI operation helpers."""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget

from gui_window_policy import persistent_feature_owner


def _explicit_callback(owner, name: str):
    class_callback = getattr(type(owner), name, None)
    if callable(class_callback):
        callback = getattr(owner, name, None)
        return callback if callable(callback) else None
    namespace = getattr(owner, "__dict__", None)
    callback = namespace.get(name) if isinstance(namespace, dict) else None
    return callback if callable(callback) else None


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


def refresh_auto_trade_views(operation_context) -> None:
    """Request one owner-coordinated projection refresh, with a local fallback."""

    refresh_views = _explicit_callback(
        operation_context,
        "refresh_auto_trade_assignment_views",
    )
    if refresh_views is not None:
        refresh_views()
        return

    owner = persistent_feature_owner(operation_context)
    refresh_views = _explicit_callback(owner, "refresh_auto_trade_assignment_views")
    if refresh_views is not None:
        refresh_views()
        return

    refresh_local = getattr(operation_context, "refresh_all", None)
    if callable(refresh_local):
        refresh_local()
    refresh_owner = getattr(owner, "refresh_all", None)
    if callable(refresh_owner) and owner is not operation_context:
        refresh_owner()
