# -*- coding: utf-8 -*-
"""Mock-safe adapter for the existing read-only stock instance chart UI."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
import weakref

from PyQt5 import sip
from PyQt5.QtCore import Qt

from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_AMBER_TEXT_COLOR,
    AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR,
    auto_trade_setting_status_color,
)
from gui_stock_instance_chart_window import StockInstanceChartWindow
from mock_validation_contract import SESSION_ENDED, clean_text, payload_hash
from mock_validation_ui_projection import mock_instance_projection
from stock_instance_day_projection import (
    chart_candle_projection_request,
    chart_market_session_projection,
    chart_operation_method_badge_label,
)


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


def _bar_minutes(
    document: dict[str, Any],
    instance_id: str,
    rules_override: dict[str, Any] | None = None,
) -> int | None:
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
    rules = (
        rules_override
        if isinstance(rules_override, dict)
        else instance.get("rules_snapshot") if isinstance(instance, dict) else {}
    )
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


def _current_operation(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
    lifecycle = document.get("mock_operation_lifecycle")
    operations = lifecycle.get("instance_operations") if isinstance(lifecycle, dict) else None
    operation = operations.get(instance_id) if isinstance(operations, dict) else None
    return operation if isinstance(operation, dict) else {}


def _current_operation_identity(document: dict[str, Any], instance_id: str) -> str:
    return clean_text(
        _current_operation(document, instance_id).get("operation_session_id")
    )


def _operation_header_display_from_projection(
    display: dict[str, Any],
) -> dict[str, Any]:
    display_contract = display.get("display_contract")
    display_contract = display_contract if isinstance(display_contract, dict) else {}
    liquidation = display_contract.get("liquidation")
    liquidation = liquidation if isinstance(liquidation, dict) else {}
    return {
        "status": clean_text(display.get("display_status")) or "감시/대기",
        "status_active": display.get("status_cell_active") is True,
        "method": "루틴",
        "method_active": display.get("method_cell_active") is True,
        "liquidation": clean_text(liquidation.get("display_text")) or "-",
        "liquidation_has_policy": display.get("liquidation_has_policy") is True,
        "liquidation_cell_active": display.get("liquidation_cell_active") is True,
    }


def _fresh_operation_header_display(window: Any, target: Any) -> dict[str, Any] | None:
    document = _target_document(window, target)
    if document is None:
        return None
    instance_id = clean_text(getattr(target, "routine_instance_id", ""))
    pnl_rows = document.get("pnl")
    pnl = next(
        (
            row
            for row in pnl_rows
            if isinstance(row, dict)
            and clean_text(row.get("routine_instance_id")) == instance_id
        ),
        {},
    ) if isinstance(pnl_rows, list) else {}
    host = getattr(window, "mock_validation_host", None)
    now_getter = getattr(host, "_now", None)
    if not callable(now_getter):
        return None
    display = mock_instance_projection(
        document,
        instance_id,
        current_price=pnl.get("mark_price"),
        as_of=now_getter(),
    )
    return _operation_header_display_from_projection(display)


def _signal_markers(
    document: dict[str, Any],
    *,
    instance_id: str,
    rules: dict[str, Any],
    trade_date: str,
    events: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operation_identity = _current_operation_identity(document, instance_id)
    rules_hash = payload_hash(rules)
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not operation_identity or not isinstance(events, list):
        return [], []
    for event in events:
        if (
            not isinstance(event, dict)
            or clean_text(event.get("event_type")) != "ROUTINE_EVALUATED"
            or clean_text(event.get("stock_code")) != clean_text(document["session"].get("stock_code"))
            or clean_text(event.get("routine_instance_id")) != instance_id
        ):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        side = clean_text(payload.get("signal")).upper()
        bar_time = clean_text(payload.get("signal_bar_time"))
        if (
            side not in {"BUY", "SELL"}
            or clean_text(payload.get("operation_identity")) != operation_identity
            or clean_text(payload.get("rules_hash")) != rules_hash
            or not bar_time
            or clean_text(payload.get("signal_trade_date")) != trade_date
            or payload.get("signal_bar_close") in (None, "")
        ):
            continue
        key = (side, bar_time, rules_hash)
        unique.setdefault(
            key,
            {
                "signal_bar_time": bar_time,
                "signal_bar_close": payload["signal_bar_close"],
                "signal_timeframe_minutes": payload.get("signal_timeframe_minutes"),
                "signal_input_hash": clean_text(payload.get("signal_input_hash")),
                "evaluation_cycle_id": clean_text(payload.get("evaluation_cycle_id")),
                "operation_identity": operation_identity,
                "rules_hash": rules_hash,
                "source_event_id": clean_text(event.get("event_id")),
            },
        )
    buy_markers: list[dict[str, Any]] = []
    sell_markers: list[dict[str, Any]] = []
    for key in sorted(unique, key=lambda item: (item[1], item[0])):
        (buy_markers if key[0] == "BUY" else sell_markers).append(unique[key])
    return buy_markers, sell_markers


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
    rules = host._operation_rules(document, instance_id)
    settings = host._operation_effective_settings(document, instance_id)
    now = host._now()
    routine_projection_request = host.routine_adapter.market_bar_projection_request(
        rules
    )
    candle_supply = host._instance_candle_projection(
        document,
        instance_id=instance_id,
        rules=rules,
        settings=settings,
        now=now,
        projection_request=chart_candle_projection_request(
            routine_projection_request
        ),
    )
    candles = _chart_candles(candle_supply.get("candles"))
    buy_markers, sell_markers = _signal_markers(
        document,
        instance_id=instance_id,
        rules=rules,
        trade_date=trade_date,
        events=host.repository.read_events(document["session"]["validation_session_id"]),
    )
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
    display = mock_instance_projection(
        document,
        instance_id,
        current_price=pnl.get("mark_price"),
        as_of=now,
    )
    operation = _current_operation(document, instance_id)
    manual_ats = settings.get("manual_ats")
    manual_ats = manual_ats if isinstance(manual_ats, dict) else {}
    market_projection = chart_market_session_projection(
        stock_code,
        host.project_root,
    )
    bar_minutes = _bar_minutes(document, instance_id, rules)
    return {
        "stock_code": stock_code,
        "stock_name": document["session"].get("stock_name", ""),
        "trade_date": trade_date,
        "instance_id": instance_id,
        "instance_name": clean_text(instance.get("routine_instance_name")) or instance_id,
        "operation_session_id": clean_text(operation.get("operation_session_id")),
        "rules_hash": payload_hash(rules),
        "bar_minutes": bar_minutes,
        "operation_title_display": "Mock Validation",
        "chart_domain_label": "모의",
        "chart_operation_method_label": chart_operation_method_badge_label(
            settings.get("operation_mode"),
            ats_selected=bool(manual_ats.get("selected_sessions")),
        ),
        "chart_bar_label": f"{bar_minutes}분봉" if bar_minutes else "-",
        "projection_status": (
            "VALID"
            if candle_supply.get("available") is True and candles
            else "NOT_READY"
        ),
        "candles": candles,
        "buy_signal_markers": buy_markers,
        "sell_signal_markers": sell_markers,
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
        **market_projection,
        "candle_availability": deepcopy(candle_supply),
        "completeness": deepcopy(candle_supply.get("completeness", {})),
        "freshness": deepcopy(candle_supply.get("freshness", {})),
        "source_identity": clean_text(candle_supply.get("source_identity")),
        "current_price": pnl.get("mark_price"),
        "operation_header_display": _operation_header_display_from_projection(display),
        "diagnostics": {
            "raw_candle_count": int(
                candle_supply.get("available_minute_candles", len(candles)) or 0
            ),
            "completed_candle_count": len(candles),
            "candle_availability_state": clean_text(
                candle_supply.get("availability_state")
            ),
            "issues": [],
        },
    }


class MockInstanceQuickChartWindow(StockInstanceChartWindow):
    """Existing chart presentation with every Production operation hook disabled."""

    def __init__(self, window: Any, target: Any) -> None:
        self._mock_owner = window
        self._mock_target = target
        super().__init__(
            target.stock_code,
            trade_date=self._current_trade_date(window, target),
            parent=window,
            projection_provider=lambda code, day: _projection(
                window, target, code, day
            ),
        )

    @staticmethod
    def _current_trade_date(window: Any, target: Any) -> str:
        document = _target_document(window, target)
        instance_id = clean_text(getattr(target, "routine_instance_id", ""))
        operation = (
            _current_operation(document, instance_id)
            if isinstance(document, dict)
            else {}
        )
        trade_date = clean_text(operation.get("trading_date"))
        if trade_date:
            return trade_date
        host = getattr(window, "mock_validation_host", None)
        now = host._now() if host is not None and callable(getattr(host, "_now", None)) else datetime.now().astimezone()
        return now.date().isoformat()

    def _find_operation_cycle_signal(self):
        return None

    def _operation_stock_context(self):
        return None

    def _build_stock_operation_adapter(self):
        return None

    def _early_close_is_excluded(self) -> bool:
        return True

    def _update_operation_header_info(self) -> None:
        header = _fresh_operation_header_display(self._mock_owner, self._mock_target)
        if header is None:
            header = self.last_projection.get("operation_header_display", {})
        header = header if isinstance(header, dict) else {}
        if isinstance(self.last_projection, dict):
            self.last_projection["operation_header_display"] = deepcopy(header)
        self._apply_mock_operation_header(header)

    def _apply_mock_operation_header(self, header: dict[str, Any]) -> None:
        labels = getattr(self, "operation_info_labels", {})
        status = clean_text(header.get("status")) or "감시/대기"
        status_active = header.get("status_active") is True
        method_active = header.get("method_active") is True
        liquidation_cell_active = header.get("liquidation_cell_active") is True
        values = {
            "status": (
                status,
                auto_trade_setting_status_color(status)
                if status_active
                else AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR,
            ),
            "method": (
                clean_text(header.get("method")) or "루틴",
                "#111827" if method_active else AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR,
            ),
            "liquidation": (
                clean_text(header.get("liquidation")) or "-",
                AUTO_TRADE_SETTING_AMBER_TEXT_COLOR
                if liquidation_cell_active
                else AUTO_TRADE_SETTING_INACTIVE_TEXT_COLOR,
            ),
        }
        for key, (value, color) in values.items():
            label = labels.get(key)
            if label is not None:
                label.setText(f"· {value}")
                label.setStyleSheet(f"color: {color};")
        self._update_header_badges(
            status_text=status,
            status_active=status_active,
        )

    def _update_operation_button_state(self) -> None:
        self._update_operation_header_info()
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
