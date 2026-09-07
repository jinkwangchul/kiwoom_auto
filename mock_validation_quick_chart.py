# -*- coding: utf-8 -*-
"""Mock-safe adapter for the existing read-only stock instance chart UI."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
import weakref

from PyQt5 import sip
from PyQt5.QtCore import Qt

from gui_stock_instance_chart_window import StockInstanceChartWindow
from mock_validation_contract import SESSION_ENDED, clean_text


_OPEN_MOCK_INSTANCE_CHARTS: dict[
    tuple[str, str], StockInstanceChartWindow
] = {}


def _target_document(window: Any, target: Any) -> dict[str, Any] | None:
    host = getattr(window, "mock_validation_host", None)
    if host is None:
        return None
    document = host.current_session(getattr(target, "stock_code", ""))
    if not isinstance(document, dict):
        return None
    session = document.get("session")
    instance_id = clean_text(getattr(target, "routine_instance_id", ""))
    execution = document.get("instance_execution", {}).get(instance_id)
    if (
        not isinstance(session, dict)
        or session.get("validation_session_id")
        != clean_text(getattr(target, "validation_session_id", ""))
        or session.get("stock_code") != clean_text(getattr(target, "stock_code", ""))
        or not isinstance(execution, dict)
        or execution.get("state") == SESSION_ENDED
    ):
        return None
    return document


def _bar_minutes(document: dict[str, Any], instance_id: str) -> int | None:
    instances = document.get("reference_snapshot", {}).get("routine_instances", ())
    instance = next(
        (
            item
            for item in instances
            if isinstance(item, dict)
            and clean_text(item.get("routine_instance_id")) == instance_id
        ),
        {},
    )
    rules = instance.get("rules_snapshot") if isinstance(instance, dict) else {}
    bar = rules.get("bar") if isinstance(rules, dict) else {}
    value = bar.get("bar_minutes") if isinstance(bar, dict) else None
    if value in (None, "") and isinstance(rules, dict):
        value = rules.get("bar_minutes")
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _chart_candles(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bar_time = (
            row.get("bar_time")
            or row.get("market_datetime")
            or row.get("datetime")
            or row.get("timestamp")
        )
        close = row.get("close", row.get("current_price"))
        if bar_time in (None, "") or close in (None, ""):
            continue
        result.append(
            {
                "bar_time": bar_time,
                "open": row.get("open", close),
                "high": row.get("high", close),
                "low": row.get("low", close),
                "close": close,
                "volume": row.get("volume", 0),
            }
        )
    return result


def _projection(window: Any, target: Any, stock_code: str, trade_date: str) -> dict[str, Any]:
    document = _target_document(window, target)
    if document is None:
        raise RuntimeError("MOCK_CONTEXT_TARGET_STALE")
    instance_id = clean_text(target.routine_instance_id)
    reference_instances = document["reference_snapshot"].get("routine_instances", ())
    instance = next(
        item
        for item in reference_instances
        if isinstance(item, dict)
        and clean_text(item.get("routine_instance_id")) == instance_id
    )
    host = window.mock_validation_host
    candles = _chart_candles(host._candles_provider(document))
    position = next(
        item
        for item in document["positions"]
        if item.get("routine_instance_id") == instance_id
    )
    pnl = next(
        item
        for item in document["pnl"]
        if item.get("routine_instance_id") == instance_id
    )
    holding = int(position.get("holding_qty", 0) or 0)
    cost_basis = float(position.get("realized_cost_basis", 0) or 0)
    net_pnl = float(pnl.get("net_pnl", 0) or 0)
    return {
        "stock_code": stock_code,
        "stock_name": document["session"].get("stock_name", ""),
        "trade_date": trade_date,
        "instance_id": instance_id,
        "instance_name": clean_text(instance.get("routine_instance_name")) or instance_id,
        "bar_minutes": _bar_minutes(document, instance_id),
        "operation_title_display": "Mock Validation",
        "candles": candles,
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "actual_fill_markers": [],
        "execution_process_rails": [],
        "average_price": position.get("average_price", 0),
        "average_price_visible": holding > 0,
        "cumulative_pnl": net_pnl,
        "cumulative_return_rate": (
            (net_pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0
        ),
        "cumulative_return_available": cost_basis > 0,
        "pnl_available": True,
        "nxt_available": False,
        "diagnostics": {"raw_candle_count": len(candles), "issues": []},
    }


class MockInstanceQuickChartWindow(StockInstanceChartWindow):
    """Existing chart presentation with every Production operation hook disabled."""

    def __init__(self, window: Any, target: Any) -> None:
        self._mock_owner = window
        self._mock_target = target
        super().__init__(
            target.stock_code,
            trade_date=datetime.now().astimezone().date().isoformat(),
            parent=window,
            projection_provider=lambda code, day: _projection(
                window, target, code, day
            ),
        )
        self.setObjectName("mockInstanceQuickChartWindow")

    def _find_live_price_operation_host(self):
        return None

    def _find_operation_cycle_signal(self):
        return None

    def _find_bar_committed_signal(self):
        return None

    def _operation_stock_context(self):
        return None

    def _build_stock_operation_adapter(self):
        return None

    def _early_close_is_excluded(self) -> bool:
        return True

    def _update_operation_button_state(self) -> None:
        labels = getattr(self, "operation_info_labels", {})
        for key, value in (("status", "모의"), ("method", "-"), ("liquidation", "-")):
            label = labels.get(key)
            if label is not None:
                label.setText(value)
                label.setStyleSheet("color: #6B7280;")
        for name in ("early_close_button", "immediate_liquidation_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(False)


def open_mock_instance_quick_chart(window: Any, target: Any):
    """Open one Mock Instance chart after a fresh identity check."""

    document = _target_document(window, target)
    if document is None:
        return None
    key = (
        clean_text(target.validation_session_id),
        clean_text(target.routine_instance_id),
    )
    existing = _OPEN_MOCK_INSTANCE_CHARTS.get(key)
    if existing is not None:
        try:
            reusable = not sip.isdeleted(existing) and existing.isVisible()
        except (RuntimeError, TypeError):
            reusable = False
    else:
        reusable = False
    if reusable:
        existing.show()
        existing.raise_()
        existing.activateWindow()
        existing.refresh_projection()
        return existing
    if existing is not None and _OPEN_MOCK_INSTANCE_CHARTS.get(key) is existing:
        _OPEN_MOCK_INSTANCE_CHARTS.pop(key, None)
    dialog = MockInstanceQuickChartWindow(window, target)
    dialog.setAttribute(Qt.WA_DeleteOnClose, True)
    dialog_ref = weakref.ref(dialog)

    def clear(
        _destroyed: object | None = None,
        *,
        registry_key: tuple[str, str] = key,
        expected_ref: weakref.ReferenceType[StockInstanceChartWindow] = dialog_ref,
    ) -> None:
        current = _OPEN_MOCK_INSTANCE_CHARTS.get(registry_key)
        if current is expected_ref():
            _OPEN_MOCK_INSTANCE_CHARTS.pop(registry_key, None)

    try:
        _OPEN_MOCK_INSTANCE_CHARTS[key] = dialog
        dialog.destroyed.connect(clear)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    except BaseException:
        if _OPEN_MOCK_INSTANCE_CHARTS.get(key) is dialog:
            _OPEN_MOCK_INSTANCE_CHARTS.pop(key, None)
        raise
    return dialog


__all__ = [
    "MockInstanceQuickChartWindow",
    "open_mock_instance_quick_chart",
]
