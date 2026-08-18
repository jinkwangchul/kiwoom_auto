# -*- coding: utf-8 -*-
"""Qt ownership helpers for long-lived feature windows.

Persistent feature windows are top-level, modeless windows.  They still retain
the MainWindow (or another stable application owner) as a logical owner for
Production callbacks and orderly application shutdown.  Short-lived message
and confirmation dialogs do not use this module; their real QWidget parent
continues to define modality and Z-order.
"""

from __future__ import annotations

import weakref

from PyQt5 import sip
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget


_OWNER_REF_ATTR = "_persistent_feature_owner_ref"
_WINDOWS_ATTR = "_persistent_feature_windows"


def persistent_feature_owner(window: QWidget | None) -> QWidget | None:
    """Return the logical owner, falling back to an existing Qt parent."""

    if window is None:
        return None
    owner_ref = getattr(window, _OWNER_REF_ATTR, None)
    if callable(owner_ref):
        try:
            owner = owner_ref()
        except Exception:
            owner = None
        if owner is not None:
            try:
                if not sip.isdeleted(owner):
                    return owner
            except (RuntimeError, TypeError):
                pass
    parent_getter = getattr(window, "parent", None)
    if callable(parent_getter):
        try:
            return parent_getter()
        except (RuntimeError, TypeError):
            return None
    return None


def persistent_feature_root(owner: QWidget | None) -> QWidget | None:
    """Resolve the stable lifecycle root without relying on Qt parenthood."""

    current = owner
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        logical_owner = persistent_feature_owner(current)
        if logical_owner is None:
            return current
        current = logical_owner
    return current


def configure_persistent_feature_window(
    window: QWidget,
    owner: QWidget | None,
) -> QWidget | None:
    """Make ``window`` independent while retaining a weak logical owner."""

    root = persistent_feature_root(owner)
    setattr(
        window,
        _OWNER_REF_ATTR,
        weakref.ref(root) if root is not None else (lambda: None),
    )
    window.setWindowFlag(Qt.Window, True)
    window.setWindowModality(Qt.NonModal)
    window.setAttribute(Qt.WA_DeleteOnClose, True)

    if root is not None:
        registry = getattr(root, _WINDOWS_ATTR, None)
        if not isinstance(registry, weakref.WeakSet):
            registry = weakref.WeakSet()
            setattr(root, _WINDOWS_ATTR, registry)
        registry.add(window)
    return root


def close_persistent_feature_windows(owner: QWidget) -> None:
    """Close live feature windows when their logical application owner exits."""

    registry = getattr(owner, _WINDOWS_ATTR, None)
    if not isinstance(registry, weakref.WeakSet):
        return
    windows = list(registry)
    for window in windows:
        if window is owner:
            continue
        try:
            if sip.isdeleted(window):
                registry.discard(window)
                continue
            if window.close():
                registry.discard(window)
        except RuntimeError:
            try:
                if sip.isdeleted(window):
                    registry.discard(window)
            except RuntimeError:
                registry.discard(window)
            continue
