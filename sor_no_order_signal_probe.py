"""Explicit, in-process direct SOR signal observation with no order path.

Call ``capture_direct_sor_signal`` with the running MarketDataHost and a
read-only observation of the actual operation cycle. Importing this module
does not start capture, a queue, a GUI, or a broker connection.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from math import isfinite
from pathlib import Path
import re
import sys
from typing import Any

from sor_routine_signal_evidence import _hash, build_source_marker


_ROOT = Path(__file__).resolve().parent
_KST = timedelta(hours=9)
_FRAMES = {1, 3, 5, 10, 15, 30, 60, 120, 240}
_NO_ORDER = {"execution_enabled": False, "queue_connected": False,
             "send_order_connected": False}


def _blocked(reason: str) -> dict[str, Any]:
    return {"status": "BLOCKED", "signal": "SKIP", "reason": reason, **_NO_ORDER}


def _time(value: Any) -> datetime | None:
    try:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
        return instant if isinstance(instant, datetime) and instant.utcoffset() == _KST else None
    except (TypeError, ValueError, OverflowError):
        return None


def capture_direct_sor_signal(
    *, market_host: Any, stock_code: str, routine_instance_id: str,
    observed_cycle: dict[str, Any], observed_at: datetime,
    session_windows: Any = None,
) -> dict[str, Any]:
    """Observe one actual completed direct _AL supply; never enter execution.

    ``observed_cycle`` must be the caller's read-only snapshot of the actual
    operation cycle. An unknown or missing cycle cannot select a side.
    """
    try:
        host_module = sys.modules.get("gui_market_data_host")
        if (host_module is None or type(market_host) is not
                getattr(host_module, "MarketDataHost", None)):
            return _blocked("ACTUAL_MARKET_DATA_HOST_REQUIRED")
        if not isinstance(stock_code, str) or not re.fullmatch(r"[0-9]{6}", stock_code):
            return _blocked("INVALID_STOCK")
        if not isinstance(routine_instance_id, str) or not routine_instance_id:
            return _blocked("ROUTINE_INSTANCE_MISSING")
        instant = _time(observed_at)
        if instant is None or not timedelta(0) <= datetime.now(instant.tzinfo) - instant <= timedelta(seconds=30):
            return _blocked("OBSERVATION_TIME_INVALID")
        if (not isinstance(observed_cycle, dict)
                or observed_cycle.get("status") not in {"resolved", "unresolved"}
                or type(observed_cycle.get("holding_qty")) is not int
                or observed_cycle["holding_qty"] < 0):
            return _blocked("ACTUAL_CYCLE_UNRESOLVED")
        if observed_cycle["holding_qty"] and (
                type(observed_cycle.get("avg_price")) not in (int, float)
                or not isfinite(observed_cycle["avg_price"])
                or observed_cycle["avg_price"] <= 0):
            return _blocked("ACTUAL_CYCLE_PRICE_UNPROVEN")

        from routine_instance_registry import routine_definition_by_id, routine_instance_by_id
        from routine_package_contract import EVALUATION_ROLE, load_routine_module
        from stock_repository import StockRepository
        from candle_timeframe_aggregation import read_canonical_bar_minutes

        stock_dir = StockRepository(project_root=_ROOT).resolve_stock_dir(stock_code)
        stock_config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
        if (not isinstance(stock_config, dict)
                or stock_config.get("assigned_routine_instance_id") != routine_instance_id
                or str(stock_config.get("calculation_market_source", "SOR")).upper() != "SOR"):
            return _blocked("COMMITTED_SOR_ASSIGNMENT_UNPROVEN")
        instance = routine_instance_by_id(routine_instance_id, project_root=_ROOT)
        if (instance is None or not instance.enabled or instance.rules_path is None
                or not instance.persisted):
            return _blocked("COMMITTED_ROUTINE_INSTANCE_UNPROVEN")
        definition = routine_definition_by_id(instance.definition_id, project_root=_ROOT)
        if (definition is None or not definition.package_enabled
                or definition.package_dir.resolve() != (_ROOT / "routines" / "지표추종매매").resolve()):
            return _blocked("PURE_ROUTINE_MODULE_UNPROVEN")
        rules = json.loads(instance.rules_path.read_text(encoding="utf-8"))
        if not isinstance(rules, dict) or not rules:
            return _blocked("COMMITTED_RULES_INVALID")
        interval = read_canonical_bar_minutes(rules)
        if interval not in _FRAMES:
            return _blocked("DIRECT_TIMEFRAME_UNSUPPORTED")
        routine_module = load_routine_module(definition, EVALUATION_ROLE)
        selector = getattr(routine_module, "evaluate_signal_selection", None)
        request_reader = getattr(routine_module, "market_bar_projection_request", None)
        if not callable(selector) or not callable(request_reader):
            return _blocked("PURE_ROUTINE_SELECTOR_UNAVAILABLE")
        request = request_reader(deepcopy(rules))
        if not isinstance(request, dict):
            return _blocked("ROUTINE_PROJECTION_INVALID")
        request = {**request, "projection": "COMPLETED_TIMEFRAME"}
        source = market_host.project_routine_candles(
            stock_code=stock_code, rules=deepcopy(rules),
            projection_request=request, as_of=instant,
            session_windows=session_windows, consumer_scope="SOR_CALCULATION_ONLY",
        )
        if not isinstance(source, dict) or source.get("available") is not True:
            return _blocked("DIRECT_SOR_SOURCE_UNAVAILABLE")
        candles = source.get("candles")
        receipt = source.get("direct_source_receipt")
        if (not isinstance(candles, list) or not candles
                or source.get("projection") != "COMPLETED_TIMEFRAME"
                or source.get("complete") is not True
                or source.get("forming_included") is not False
                or not isinstance(receipt, dict)):
            return _blocked("DIRECT_SOR_PROOF_UNAVAILABLE")
        last_bar = _time(candles[-1].get("bar_time")) if isinstance(candles[-1], dict) else None
        receipt_time = _time(receipt.get("source_as_of"))
        if (last_bar is None or receipt_time is None
                or last_bar > instant or last_bar.date() != instant.date()
                or receipt_time != instant):
            return _blocked("DIRECT_SOR_TIME_UNPROVEN")
        session = type(market_host)._current_display_session_identity(market_host)
        if (not isinstance(session, tuple) or len(session) != 2
                or type(session[0]) is not int or session[0] <= 0
                or not isinstance(session[1], str) or not session[1]
                or receipt.get("connection_epoch") != session[0]
                or receipt.get("session_sha256") != hashlib.sha256(
                    session[1].encode("utf-8")).hexdigest()):
            return _blocked("BROKER_SESSION_CHANGED")
        preliminary = build_source_marker(
            stock=stock_code, rules=rules, candles=candles, source=source, side="NONE",
        )
        if preliminary is None or "receipt_hash" not in preliminary:
            return _blocked("DIRECT_SOR_PROOF_INVALID")
        cycle = deepcopy(observed_cycle)
        context = {"cycle": cycle, "routine_instance_id": routine_instance_id,
                   "code": stock_code, "rules": deepcopy(rules), "probe_only": True}
        preview = selector(deepcopy(candles), deepcopy(rules), context)
        if not isinstance(preview, dict) or "signal" not in preview:
            return _blocked("PURE_SIGNAL_INVALID")
        raw_side = preview["signal"]
        if raw_side not in {"BUY", "SELL", "NONE", None}:
            return _blocked("PURE_SIGNAL_INVALID")
        side = "NONE" if raw_side is None else raw_side
        marker = build_source_marker(
            stock=stock_code, rules=rules, candles=candles, source=source, side=side,
        )
        if marker is None or {k: v for k, v in marker.items() if k != "side"} != {
                k: v for k, v in preliminary.items() if k != "side"}:
            return _blocked("DIRECT_SOR_PROOF_INVALID")
        if type(market_host)._current_display_session_identity(market_host) != session:
            return _blocked("BROKER_SESSION_CHANGED")
        activation = _time(preview.get("signal_activation_bar_time")) if side != "NONE" else None
        if side != "NONE" and activation != last_bar:
            return _blocked("SIGNAL_ACTIVATION_UNPROVEN")
        output = {
            "status": "OBSERVED", "signal": "SKIP",
            "reason": "SOR_CALCULATION_ONLY_NO_ORDER", "code": stock_code,
            "sor_preview_signal": side, "sor_preview_evidence": marker,
            "sor_preview_activation_time": activation.isoformat() if activation else None,
            "sor_preview_cycle_hash": _hash(cycle),
            "sor_preview_cycle_status": cycle["status"],
            "sor_preview_routine_instance_id": routine_instance_id,
            "sor_preview_observed_at": instant.isoformat(),
            "sor_preview_evaluated_at": datetime.now(instant.tzinfo).isoformat(),
            **_NO_ORDER,
        }
        from sor_m3_production_capture import enqueue_preview
        if not enqueue_preview(output):
            return _blocked("EVIDENCE_CAPTURE_UNAVAILABLE")
        return output
    except Exception:
        return _blocked("DIRECT_SOR_CAPTURE_EXCEPTION")
