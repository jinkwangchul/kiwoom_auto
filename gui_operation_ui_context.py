# -*- coding: utf-8 -*-
"""Resolve the visible QWidget used by shared GUI operation helpers."""

from __future__ import annotations

from math import isfinite

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


def sync_auto_trade_monitoring_universe(operation_context) -> dict[str, object]:
    """Run the explicit registered-stock MarketData sync for one semantic change."""

    owner = persistent_feature_owner(operation_context)
    candidates = [operation_context]
    if owner is not None and owner is not operation_context:
        candidates.append(owner)

    for candidate in candidates:
        sync = _explicit_callback(
            candidate,
            "sync_monitoring_universe_for_current_session",
        )
        if sync is None:
            host_getter = getattr(
                candidate,
                "main_monitoring_auto_trade_operation_host",
                None,
            )
            host = host_getter() if callable(host_getter) else None
            sync = getattr(host, "sync_monitoring_universe_for_current_session", None)
        if not callable(sync):
            continue
        try:
            result = sync()
        except Exception as exc:
            return {
                "ok": False,
                "changed": False,
                "reason_code": "REALTIME_MONITORING_UNIVERSE_SYNC_FAILED",
                "error": str(exc),
            }
        return dict(result) if isinstance(result, dict) else {
            "ok": True,
            "changed": False,
            "reason_code": "REALTIME_MONITORING_UNIVERSE_SYNC_COMPLETED",
        }

    return {
        "ok": False,
        "changed": False,
        "reason_code": "REALTIME_MONITORING_UNIVERSE_SYNC_UNAVAILABLE",
    }


def actionable_current_price(operation_context, stock_code: object) -> float | None:
    """Project the canonical tick-only actionable price from an operation owner."""

    code = str(stock_code or "").strip().upper().lstrip("A")
    if not code:
        return None

    owner = persistent_feature_owner(operation_context)
    candidates = [operation_context]
    if owner is not None and owner is not operation_context:
        candidates.append(owner)

    for candidate in candidates:
        state_getter = getattr(
            candidate,
            "fresh_monitoring_market_information_state",
            None,
        )
        if not callable(state_getter):
            host_getter = getattr(
                candidate,
                "main_monitoring_auto_trade_operation_host",
                None,
            )
            host = host_getter() if callable(host_getter) else None
            state_getter = getattr(
                host,
                "fresh_monitoring_market_information_state",
                None,
            )
        if not callable(state_getter):
            continue
        try:
            state = state_getter(code)
            price = getattr(state, "last_price", None)
            number = float(price)
        except Exception:
            continue
        if not isinstance(price, bool) and isfinite(number) and number > 0:
            return number
    return None
