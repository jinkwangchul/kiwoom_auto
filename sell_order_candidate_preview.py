# -*- coding: utf-8 -*-
"""Preview-only SELL order candidate builder.

Phase 2 supports METHOD action candidates and separate COMPLETION action
candidates for market-selling remaining quantity. It never creates executable
order requests and never connects runtime, queue, execution, or SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from order_hoga_mapper import map_order_hoga_preview
from order_type_mapper import map_order_type_preview


PREVIEW_TYPE = "SELL_ORDER_CANDIDATE_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
ACTION_SOURCE = "METHOD"
ACTION_SOURCE_COMPLETION = "COMPLETION"
POLICY_MARKET_SELL_REMAINING = "MARKET_SELL_REMAINING"
SAFETY_FLAGS = ("execution_connected", "runtime_write", "send_order", "queue_write")

_SINGLE_HOGA_TERMS = {"SINGLE", "SINGLE_HOGA", "single", "\ub2e8\uc77c\ud638\uac00"}
_MULTI_HOGA_TERMS = {"MULTI", "MULTI_HOGA", "multi", "\ub2e4\uc911\ud638\uac00"}
_MARKET_TERMS = {"MARKET", "MARKET_ORDER", "\uc2dc\uc7a5\uac00"}
_ORDER_PRICE_TERMS = {"ORDER_PRICE", "\uc8fc\ubb38\uac00"}
_CURRENT_PRICE_TERMS = {"CURRENT_PRICE", "CLOSE", "\ud604\uc7ac\uac00"}
_NO_SPLIT_TERMS = {"", "NONE", "NO", "NOT_SELECTED", "NO_SELECTION", "none", "\uc120\ud0dd\uc5c6\uc74c"}
_MULTI_TIME_TERMS = {"MULTI_TIME", "\ub2e4\uc911\uc2dc\uac04"}
_MULTI_RATIO_TERMS = {"MULTI_RATIO", "\ub2e4\uc911\ube44\uc728"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).upper().replace("-", "_").replace(" ", "_")


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_side(signal: dict[str, Any]) -> str:
    for key in ("signal", "signal_type", "side", "action", "decision"):
        value = _norm(signal.get(key))
        if value:
            return value
    routine_signal = _as_dict(signal.get("routine_signal"))
    return _norm(routine_signal.get("signal"))


def _signal_id(signal: dict[str, Any]) -> str | None:
    for key in ("signal_id", "source_signal_id", "id"):
        value = _text(signal.get(key))
        if value:
            return value
    return None


def _method_snapshot(preview: dict[str, Any]) -> Any:
    if "method_snapshot" in preview:
        return preview.get("method_snapshot")
    return preview


def _method_set(preview: dict[str, Any]) -> str | None:
    value = _text(preview.get("method_set"))
    return value or None


def _looks_like_market_context(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(key in value for key in ("current_price", "holding_qty", "average_price", "order_price"))


def _symbol(signal: dict[str, Any], market: dict[str, Any]) -> str | None:
    for source in (market, signal):
        for key in ("symbol", "code", "stock_code", "ticker"):
            value = _text(source.get(key))
            if value:
                return value
    return None


def _current_price(market: dict[str, Any]) -> float | None:
    for key in ("current_price", "price", "latest_price", "close"):
        value = _number(market.get(key))
        if value is not None:
            return value
    return None


def _holding_qty(market: dict[str, Any]) -> float | None:
    for key in ("holding_qty", "holding_quantity", "available_qty", "available_quantity", "quantity"):
        value = _number(market.get(key))
        if value is not None:
            return value
    return None


def _order_price(market: dict[str, Any]) -> float | None:
    for key in ("order_price", "limit_price"):
        value = _number(market.get(key))
        if value is not None:
            return value
    return None


def _contains_term(value: Any, terms: set[str]) -> bool:
    text = _text(value)
    normalized = _norm(value)
    return text in terms or normalized in terms


def _is_single_hoga(value: Any) -> bool:
    return _contains_term(value, _SINGLE_HOGA_TERMS)


def _is_multi_hoga(value: Any) -> bool:
    return _contains_term(value, _MULTI_HOGA_TERMS)


def _split_policy(snapshot: dict[str, Any]) -> str | None:
    value = snapshot.get("perform2_title_combo")
    if value is None or _contains_term(value, _NO_SPLIT_TERMS):
        return None
    if _contains_term(value, _MULTI_TIME_TERMS):
        return "multi-time sell method is unsupported"
    if _contains_term(value, _MULTI_RATIO_TERMS):
        return "multi-ratio sell method is unsupported"
    return None


def _repeat_enabled(snapshot: dict[str, Any]) -> bool:
    for key in ("repeat_enabled", "repeat_check", "repeat_active"):
        if snapshot.get(key) is True:
            return True
    return False


def _safety_reasons(*containers: Any) -> list[str]:
    reasons: list[str] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        for flag in SAFETY_FLAGS:
            if container.get(flag) is True:
                reasons.append(f"safety flag must be false: {flag}")
    return reasons


def _candidate(
    *,
    status: str,
    signal: dict[str, Any],
    method_set: str | None,
    action_source: str,
    symbol: str | None,
    quantity: float | None,
    price: float | None,
    hoga: str | None,
    order_type: str | None,
    method_snapshot: dict[str, Any] | None,
    source_previews: dict[str, Any],
    reasons: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order": False,
        "status": status,
        "symbol": symbol,
        "side": "SELL",
        "signal_id": _signal_id(signal),
        "method_set": method_set,
        "action_source": action_source,
        "quantity": quantity,
        "price": price,
        "hoga": hoga,
        "order_type": order_type,
        "order_request_created": False,
        "candidate_created": False,
        "method_snapshot": deepcopy(method_snapshot),
        "source_previews": deepcopy(source_previews),
        "reasons": list(reasons),
        "warnings": list(warnings),
    }


def _hoga_and_price(snapshot: dict[str, Any], market: dict[str, Any]) -> tuple[str | None, float | None, list[str]]:
    reasons: list[str] = []
    single_value = snapshot.get("perform1_single_combo")
    if _contains_term(single_value, _MARKET_TERMS):
        return "MARKET", None, reasons
    if _contains_term(single_value, _CURRENT_PRICE_TERMS):
        price = _current_price(market)
        if price is None:
            reasons.append("current_price is required")
        return "LIMIT", price, reasons
    if _contains_term(single_value, _ORDER_PRICE_TERMS):
        price = _order_price(market)
        if price is None:
            reasons.append("order_price is required")
        return "LIMIT", price, reasons
    reasons.append("perform1_single_combo is unsupported")
    return None, None, reasons


def _mapped_order_type() -> tuple[str | None, list[str]]:
    mapped = map_order_type_preview({"order_intent": {"side": "SELL"}})
    return mapped.get("order_type"), _as_list(mapped.get("warnings"))


def _mapped_hoga(hoga: str | None) -> tuple[str | None, list[str]]:
    if hoga is None:
        return None, []
    mapped = map_order_hoga_preview({"order_intent": {"hoga": hoga}})
    return mapped.get("hoga"), _as_list(mapped.get("warnings"))


def _result(
    *,
    status: str,
    signal: dict[str, Any],
    method_preview: dict[str, Any],
    method_snapshot: dict[str, Any] | None,
    market: dict[str, Any],
    order: dict[str, Any],
    runtime: dict[str, Any],
    symbol: str | None,
    method_set: str | None,
    quantity: float | None,
    price: float | None,
    hoga: str | None,
    order_type: str | None,
    reasons: list[str],
    warnings: list[str],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order": False,
        "status": status,
        "symbol": symbol,
        "side": "SELL",
        "signal_id": _signal_id(signal),
        "method_set": method_set,
        "action_source": ACTION_SOURCE,
        "quantity": quantity,
        "price": price,
        "hoga": hoga,
        "order_type": order_type,
        "order_request_created": False,
        "candidate_created": False,
        "method_snapshot": deepcopy(method_snapshot),
        "source_previews": {
            "method_preview": deepcopy(method_preview),
        },
        "market_context_snapshot": deepcopy(market),
        "order_context_snapshot": deepcopy(order),
        "runtime_context_snapshot": deepcopy(runtime),
        "reasons": list(reasons),
        "warnings": list(warnings),
        "candidates": deepcopy(candidates or []),
    }


def _completion_candidate(
    *,
    completion: dict[str, Any] | None,
    signal: dict[str, Any],
    method: dict[str, Any],
    method_snapshot: dict[str, Any] | None,
    symbol: str | None,
) -> dict[str, Any] | None:
    if completion is None:
        return None

    reasons: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []
    quantity: float | None = _number(completion.get("remaining_qty"))
    hoga: str | None = None
    order_type: str | None = None

    invalid.extend(_safety_reasons(completion, _as_dict(completion.get("action_preview"))))

    completion_status = _text(completion.get("status"))
    if completion_status == STATUS_NOT_APPLICABLE:
        return None
    if completion_status == STATUS_INVALID:
        invalid.extend(_as_list(completion.get("reasons")) or ["completion preview is invalid"])
    elif completion_status == STATUS_BLOCKED:
        reasons.extend(_as_list(completion.get("reasons")) or ["completion preview is blocked"])
    elif completion_status != STATUS_READY:
        reasons.append(f"completion preview status is not READY: {completion_status or '<empty>'}")

    action = _as_dict(completion.get("action_preview"))
    if completion.get("policy") != POLICY_MARKET_SELL_REMAINING:
        reasons.append("completion policy must be MARKET_SELL_REMAINING")
    if action.get("action") != POLICY_MARKET_SELL_REMAINING:
        reasons.append("completion action must be MARKET_SELL_REMAINING")

    if quantity is None:
        reasons.append("completion remaining_qty is required")
    elif quantity <= 0:
        reasons.append("completion remaining_qty is not greater than 0")

    if not symbol:
        reasons.append("symbol is required")

    mapped_hoga, hoga_warnings = _mapped_hoga("MARKET")
    mapped_type, type_warnings = _mapped_order_type()
    hoga = mapped_hoga
    order_type = mapped_type
    warnings.extend(hoga_warnings + type_warnings)

    if invalid:
        status = STATUS_INVALID
    elif quantity is not None and quantity <= 0:
        status = STATUS_NOT_APPLICABLE
    elif reasons:
        status = STATUS_BLOCKED
    else:
        status = STATUS_READY

    return _candidate(
        status=status,
        signal=signal,
        method_set=_method_set(method) or _text(completion.get("method_set")) or None,
        action_source=ACTION_SOURCE_COMPLETION,
        symbol=symbol,
        quantity=quantity,
        price=None,
        hoga=hoga,
        order_type=order_type,
        method_snapshot=method_snapshot,
        source_previews={"completion": deepcopy(completion)},
        reasons=list(reasons + invalid),
        warnings=warnings,
    )


def build_sell_order_candidate_preview(
    sell_signal_preview: Any,
    method_preview: Any,
    completion_preview: Any = None,
    pending_preview: Any = None,
    market_context: Any = None,
    order_context: Any = None,
    runtime_context: Any = None,
) -> dict[str, Any]:
    """Build a METHOD-source SELL order candidate preview without side effects."""
    if _looks_like_market_context(completion_preview):
        old_market_context = completion_preview
        old_order_context = pending_preview
        old_runtime_context = market_context
        completion_preview = None
        pending_preview = None
        market_context = old_market_context
        order_context = old_order_context
        runtime_context = old_runtime_context

    signal = deepcopy(_as_dict(sell_signal_preview))
    method = deepcopy(_as_dict(method_preview))
    completion = deepcopy(completion_preview) if isinstance(completion_preview, dict) else None
    pending = deepcopy(pending_preview) if isinstance(pending_preview, dict) else None
    market = deepcopy(_as_dict(market_context))
    order = deepcopy(_as_dict(order_context))
    runtime = deepcopy(_as_dict(runtime_context))

    reasons: list[str] = []
    invalid: list[str] = []
    warnings: list[str] = []
    quantity: float | None = None
    price: float | None = None
    hoga: str | None = None
    order_type: str | None = None
    snapshot_copy: dict[str, Any] | None = None

    if _signal_side(signal) != "SELL":
        reasons.append("SELL signal is required")

    method_status = _text(method.get("status"))
    if method_status == STATUS_INVALID:
        invalid.extend(_as_list(method.get("reasons")) or ["method preview is invalid"])
    elif method_status != STATUS_READY:
        reasons.extend(_as_list(method.get("reasons")) or [f"method preview status is not READY: {method_status or '<empty>'}"])

    snapshot = _method_snapshot(method)
    if not isinstance(snapshot, dict):
        invalid.append("method_snapshot must be a dict")
    else:
        snapshot_copy = deepcopy(snapshot)
        invalid.extend(_safety_reasons(method, snapshot, signal, market, order, runtime))

        if _is_multi_hoga(snapshot.get("perform1_title_combo")):
            reasons.append("multi-hoga sell method is unsupported")
        elif not _is_single_hoga(snapshot.get("perform1_title_combo")):
            reasons.append("perform1_title_combo must be single-hoga")

        split_reason = _split_policy(snapshot)
        if split_reason:
            reasons.append(split_reason)

        if _repeat_enabled(snapshot):
            reasons.append("repeat sell method is unsupported")

    symbol = _symbol(signal, market)
    if not symbol:
        reasons.append("symbol is required")

    current_price = _current_price(market)
    if current_price is None:
        reasons.append("current_price is required")

    holding_qty = _holding_qty(market)
    if holding_qty is None or holding_qty <= 0:
        reasons.append("holding_qty must be greater than 0")
    else:
        quantity = holding_qty

    if not invalid and snapshot_copy is not None:
        raw_hoga, raw_price, price_reasons = _hoga_and_price(snapshot_copy, market)
        reasons.extend(price_reasons)
        mapped_hoga, hoga_warnings = _mapped_hoga(raw_hoga)
        hoga = mapped_hoga
        warnings.extend(hoga_warnings)
        order_type, order_type_warnings = _mapped_order_type()
        warnings.extend(order_type_warnings)
        price = raw_price
        if raw_hoga == "LIMIT" and price is None and "order_price is required" not in reasons and "current_price is required" not in reasons:
            reasons.append("LIMIT price is required")

    if invalid:
        status = STATUS_INVALID
    elif reasons:
        status = STATUS_BLOCKED
    else:
        status = STATUS_READY

    method_candidate = _candidate(
        status=status,
        signal=signal,
        method_set=_method_set(method),
        action_source=ACTION_SOURCE,
        symbol=symbol,
        quantity=quantity if status == STATUS_READY else quantity,
        price=price,
        hoga=hoga,
        order_type=order_type,
        method_snapshot=snapshot_copy,
        source_previews={"method_preview": deepcopy(method)},
        reasons=list(reasons + invalid),
        warnings=warnings,
    )

    candidates = [method_candidate]
    completion_candidate = _completion_candidate(
        completion=completion,
        signal=signal,
        method=method,
        method_snapshot=snapshot_copy,
        symbol=symbol,
    )
    if completion_candidate is not None:
        candidates.append(completion_candidate)

    if pending and pending.get("status") == STATUS_READY:
        warnings.append("pending_action_source_not_supported_in_phase_2")
        method_candidate["warnings"].append("pending_action_source_not_supported_in_phase_2")

    ready_sources = [candidate.get("action_source") for candidate in candidates if candidate.get("status") == STATUS_READY]
    if len(ready_sources) > 1:
        warnings.append("multiple_ready_action_sources")
        for candidate in candidates:
            candidate["warnings"].append("multiple_ready_action_sources")

    return _result(
        status=status,
        signal=signal,
        method_preview=method,
        method_snapshot=snapshot_copy,
        market=market,
        order=order,
        runtime=runtime,
        symbol=symbol,
        method_set=_method_set(method),
        quantity=quantity if status == STATUS_READY else quantity,
        price=price,
        hoga=hoga,
        order_type=order_type,
        reasons=list(reasons + invalid),
        warnings=warnings,
        candidates=candidates,
    )
