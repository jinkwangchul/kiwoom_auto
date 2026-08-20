# -*- coding: utf-8 -*-
"""Enforce stock invested-principal limits through canonical EARLY_CLOSE."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from buffer_response_candidate_selector import existing_close_exclusion_reason
from buffer_response_ownership_service import BufferResponseOwnershipService
from close_intent_service import CLOSE_INTENT_EARLY_CLOSE, apply_close_intent
from close_liquidation_transition_service import normalize_direct_close_policy_alias
from gui_operation_environment import read_operation_policy
from operation_command_service import MODE_EARLY_CLOSE, SCOPE_STOCK
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import production_recovery_registry
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
SOURCE_PREFIX = "STOCK_LIMIT:"


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _stock_code(value: object) -> str:
    return _text(value).lstrip("A")


def _result(reason: str = "", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "evaluated": False,
        "stock_code": "",
        "invested_principal": None,
        "buy_limit_amount": None,
        "overrun": False,
        "higher_priority_blocked": False,
        "existing_close_blocked": False,
        "early_close_requested": False,
        "already_applied": False,
        "reason": _text(reason),
        "command_id": "",
        "source": "",
    }
    result.update(updates)
    return result


def _read_json_object(path: Path) -> dict[str, object]:
    parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} root must be an object")
    return parsed


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _positive_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def stock_limit_event_id(position: Mapping[str, object]) -> str:
    position_id = _text(position.get("position_id"))
    identity_source = _text(position.get("last_fill_identity_source"))
    identity = _text(position.get("last_fill_identity"))
    if not position_id or not identity_source or not identity:
        return ""
    payload = f"{position_id}:{identity_source}:{identity}"
    return f"STOCK_LIMIT_EVENT_{uuid5(NAMESPACE_URL, payload)}"


def stock_limit_command_id(event_id: object) -> str:
    identity = _text(event_id)
    if not identity:
        return ""
    return str(uuid5(NAMESPACE_URL, f"kiwoom-auto:stock-limit:{identity}:EARLY_CLOSE"))


def stock_limit_command_source(event_id: object) -> str:
    identity = _text(event_id)
    return f"{SOURCE_PREFIX}{identity}" if identity else ""


def _recovery_allows_stock(
    recovery_context: object,
    *,
    account_no: str,
    trading_day: str,
    stock_code: str,
) -> tuple[bool, str]:
    if recovery_context is None:
        return False, "RECOVERY_UNAVAILABLE"
    identity = getattr(recovery_context, "identity", None)
    if (
        getattr(recovery_context, "account_status", None) != ACCOUNT_COMPLETED
        or identity is None
        or _text(getattr(identity, "account_no", "")) != account_no
        or _text(getattr(identity, "trading_day", "")) != trading_day
    ):
        return False, "RECOVERY_INCOMPLETE_OR_IDENTITY_MISMATCH"
    matches = tuple(
        item
        for item in tuple(getattr(recovery_context, "stocks", ()) or ())
        if _stock_code(getattr(item, "stock_code", "")) == stock_code
    )
    if len(matches) != 1:
        return False, "RECOVERY_STOCK_EVIDENCE_UNAVAILABLE"
    if (
        getattr(matches[0], "stock_status", None) != STOCK_RESTORED
        or bool(getattr(matches[0], "review_required", False))
    ):
        return False, "RECOVERY_STOCK_NOT_RESTORED"
    return True, ""


def _matching_open_position(
    document: Mapping[str, object], *, account_no: str, stock_code: str
) -> tuple[dict[str, object] | None, str]:
    positions = document.get("positions")
    if not isinstance(positions, list) or any(not isinstance(item, dict) for item in positions):
        return None, "POSITIONS_RUNTIME_INVALID"
    matches = [
        dict(item)
        for item in positions
        if _text(item.get("account_no")) == account_no
        and _stock_code(item.get("code") or item.get("stock_code")) == stock_code
    ]
    if len(matches) != 1:
        return None, "OPEN_POSITION_IDENTITY_UNAVAILABLE"
    position = matches[0]
    quantity = position.get("quantity")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
        or _text(position.get("position_status")).upper() != "OPEN"
    ):
        return None, "NO_OPEN_HOLDING"
    return position, ""


def _stock_orders(
    document: Mapping[str, object], *, account_no: str, stock_code: str
) -> tuple[list[dict[str, object]] | None, str]:
    orders = document.get("orders")
    if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
        return None, "ORDER_QUEUE_RUNTIME_INVALID"
    return [
        dict(item)
        for item in orders
        if _stock_code(item.get("code") or item.get("stock_code")) == stock_code
        and _text(item.get("account_no")) in {"", account_no}
    ], ""


def _early_close_policy(
    policy_reader: Callable[..., dict[str, object]],
) -> tuple[str, dict[str, object], str]:
    try:
        policy = policy_reader()
    except Exception as exc:
        return "", {}, _text(exc) or "EARLY_CLOSE_POLICY_READ_FAILED"
    early = policy.get("early_close") if isinstance(policy, Mapping) else None
    if not isinstance(early, Mapping):
        return "", {}, "EARLY_CLOSE_POLICY_UNAVAILABLE"
    method = normalize_direct_close_policy_alias(early.get("method"))
    if not method:
        return "", {}, "EARLY_CLOSE_METHOD_UNAVAILABLE"
    extra = {
        key: value
        for key, value in early.items()
        if key != "method" and _text(value)
    }
    return method, extra, ""


def _read_back_matches(
    state: Mapping[str, object], *, command_id: str, source: str, method: str
) -> bool:
    return (
        _text(state.get("operation_command_mode")).upper() == MODE_EARLY_CLOSE
        and _text(state.get("operation_command_id")) == command_id
        and _text(state.get("operation_command_source")) == source
        and _text(state.get("early_close_source")) == source
        and normalize_direct_close_policy_alias(state.get("early_close_method")) == method
        and _text(state.get("status")).upper()
        in {MODE_EARLY_CLOSE, "EARLY_CLOSING", "EARLY_CLOSED"}
    )


class StockLimitResponseService:
    def __init__(
        self,
        *,
        project_root: str | Path = PROJECT_ROOT,
        positions_path: str | Path = POSITIONS_PATH,
        order_queue_path: str | Path = ORDER_QUEUE_PATH,
        fills_path: str | Path = FILLS_PATH,
        ownership_service: BufferResponseOwnershipService | None = None,
        policy_reader: Callable[..., dict[str, object]] = read_operation_policy,
        close_backend: Callable[..., dict[str, Any]] = apply_close_intent,
    ) -> None:
        self.project_root = Path(project_root)
        self.positions_path = Path(positions_path)
        self.order_queue_path = Path(order_queue_path)
        self.fills_path = Path(fills_path)
        self.ownership = ownership_service or BufferResponseOwnershipService()
        self._policy_reader = policy_reader
        self._close_backend = close_backend

    def evaluate_stock(
        self,
        *,
        account_no: object,
        trading_day: object,
        stock_code: object,
        recovery_context: object,
    ) -> dict[str, object]:
        account = _text(account_no)
        day = _text(trading_day)
        code = _stock_code(stock_code)
        base = {"evaluated": True, "stock_code": code}
        if not account or not day or not code:
            return _result("ACCOUNT_OR_STOCK_IDENTITY_UNAVAILABLE", **base)
        recovery_ok, recovery_reason = _recovery_allows_stock(
            recovery_context,
            account_no=account,
            trading_day=day,
            stock_code=code,
        )
        if not recovery_ok:
            return _result(recovery_reason, **base)

        try:
            positions = _read_json_object(self.positions_path)
            queue = _read_json_object(self.order_queue_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "RUNTIME_READ_FAILED", **base)
        position, position_reason = _matching_open_position(
            positions, account_no=account, stock_code=code
        )
        if position is None:
            return _result(position_reason, **base)
        invested = _positive_decimal(position.get("cost_basis"))
        if invested is None:
            return _result("POSITION_COST_BASIS_INVALID", **base)

        record = StockRepository(project_root=self.project_root).find_by_code(code)
        if record is None:
            return _result("STOCK_RUNTIME_UNAVAILABLE", **base)
        stock_dir = self.project_root / record.stock_path
        try:
            config = _read_json_object(stock_dir / "config.json")
            state = _read_json_object(stock_dir / "state.json")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "STOCK_RUNTIME_READ_FAILED", **base)
        if config.get("buy_limit_enabled") is not True:
            return _result(
                "STOCK_LIMIT_DISABLED",
                **base,
                invested_principal=_json_number(invested),
            )
        limit = _positive_integer(config.get("buy_limit_amount"))
        if limit is None:
            return _result(
                "STOCK_LIMIT_WAITING_OR_INVALID",
                **base,
                invested_principal=_json_number(invested),
            )

        comparison = {
            **base,
            "invested_principal": _json_number(invested),
            "buy_limit_amount": limit,
            "overrun": invested > Decimal(limit),
        }
        if invested <= Decimal(limit):
            return _result("LIMIT_NOT_EXCEEDED", **comparison)

        active = self.ownership.active_owned_stock_codes(
            account_no=account,
            trading_day=day,
        )
        if active.get("ok") is not True:
            return _result(
                _text(active.get("reason")) or "HIGHER_PRIORITY_OWNERSHIP_UNAVAILABLE",
                **comparison,
                higher_priority_blocked=True,
            )
        if code in {_stock_code(item) for item in active.get("stock_codes", ())}:
            return _result(
                "BUFFER_RESPONSE_OWNS_STOCK",
                **comparison,
                higher_priority_blocked=True,
            )

        stock_orders, orders_reason = _stock_orders(
            queue, account_no=account, stock_code=code
        )
        if stock_orders is None:
            return _result(orders_reason, **comparison)
        event_id = stock_limit_event_id(position)
        command_id = stock_limit_command_id(event_id)
        source = stock_limit_command_source(event_id)
        identity = {**comparison, "command_id": command_id, "source": source}
        if not event_id or not command_id or not source:
            return _result("POSITION_LIFECYCLE_IDENTITY_UNAVAILABLE", **identity)

        method, extra_policy, policy_reason = _early_close_policy(self._policy_reader)
        if policy_reason:
            return _result(policy_reason, **identity)
        if _read_back_matches(state, command_id=command_id, source=source, method=method):
            return _result("ALREADY_APPLIED", **identity, already_applied=True)
        if _text(state.get("operation_command_id")) == command_id:
            return _result("DETERMINISTIC_COMMAND_EVIDENCE_CONFLICT", **identity)
        conflict = existing_close_exclusion_reason(
            {
                "stock_code": code,
                "is_auto_trade_target": True,
                "position": position,
                "state": state,
                "config": config,
                "orders": stock_orders,
            }
        )
        if conflict:
            return _result(
                f"EXISTING_CLOSE_CONFLICT:{conflict}",
                **identity,
                existing_close_blocked=True,
            )

        routine_instance_id = _text(config.get("assigned_routine_instance_id"))
        requested_at = _text(position.get("last_fill_at")) or datetime.now().isoformat(timespec="seconds")
        backend_result = self._close_backend(
            intent=CLOSE_INTENT_EARLY_CLOSE,
            target_scope=SCOPE_STOCK,
            target_id=str(stock_dir.resolve()),
            source=source,
            requested_policy=method,
            has_close_progress_quantity=True,
            extra_policy=extra_policy,
            stock_code=code,
            runtime_state=dict(state),
            runtime_routine_instance_id=routine_instance_id,
            current_policy="",
            current_started_at="",
            current_command_id=_text(state.get("operation_command_id")),
            command_id=command_id,
            requested_at=requested_at,
            project_root=self.project_root,
            queue_path=self.order_queue_path,
            fills_path=self.fills_path,
        )
        try:
            saved_state = _read_json_object(stock_dir / "state.json")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return _result(_text(exc) or "EARLY_CLOSE_READ_BACK_FAILED", **identity)
        if not isinstance(backend_result, Mapping) or backend_result.get("ok") is not True:
            return _result(
                _text(backend_result.get("reason") if isinstance(backend_result, Mapping) else "")
                or "EARLY_CLOSE_BACKEND_FAILED",
                **identity,
            )
        if not _read_back_matches(
            saved_state, command_id=command_id, source=source, method=method
        ):
            return _result("EARLY_CLOSE_READ_BACK_MISMATCH", **identity)
        return _result("", **identity, early_close_requested=True)


def evaluate_main_window_stock_limit_after_chejan(
    window: object,
    *,
    chejan_result: Mapping[str, object] | object,
    higher_priority_result: Mapping[str, object] | object,
) -> dict[str, object]:
    if not isinstance(chejan_result, Mapping):
        return _result("CHEJAN_RESULT_UNAVAILABLE")
    position_result = chejan_result.get("position_result")
    if not isinstance(position_result, Mapping) or position_result.get("position_committed") is not True:
        return _result("BUY_POSITION_COMMIT_NOT_CONFIRMED")
    if _text(position_result.get("side")).upper() != "BUY":
        return _result("NOT_BUY_POSITION_COMMIT")
    if not _higher_priority_settled(higher_priority_result):
        return _result(
            "HIGHER_PRIORITY_RESPONSE_NOT_SETTLED",
            stock_code=_stock_code(position_result.get("code")),
            higher_priority_blocked=True,
        )
    selected_reader = getattr(window, "selected_account_no", None)
    account_no = _text(selected_reader()) if callable(selected_reader) else ""
    recovery = production_recovery_registry.snapshot()
    trading_day = _text(getattr(getattr(recovery, "identity", None), "trading_day", ""))
    return StockLimitResponseService().evaluate_stock(
        account_no=account_no,
        trading_day=trading_day,
        stock_code=position_result.get("code"),
        recovery_context=recovery,
    )


def _higher_priority_settled(result: Mapping[str, object] | object) -> bool:
    return bool(
        isinstance(result, Mapping)
        and result.get("stable") is True
        and result.get("ingress_committed") is True
        and not (
            result.get("event_created") is True
            and result.get("policy_projected") is not True
        )
    )


def resume_main_window_stock_limit_responses(
    window: object,
    *,
    higher_priority_result: Mapping[str, object] | object,
) -> dict[str, object]:
    if not _higher_priority_settled(higher_priority_result):
        return {
            "evaluated_count": 0,
            "requested_count": 0,
            "results": (),
            "higher_priority_blocked": True,
            "reason": "HIGHER_PRIORITY_RESPONSE_NOT_SETTLED",
        }
    recovery = production_recovery_registry.snapshot()
    identity = getattr(recovery, "identity", None)
    account_no = _text(getattr(identity, "account_no", ""))
    trading_day = _text(getattr(identity, "trading_day", ""))
    service = StockLimitResponseService()
    results = []
    for stock in tuple(getattr(recovery, "stocks", ()) or ()):
        if (
            getattr(stock, "stock_status", None) != STOCK_RESTORED
            or bool(getattr(stock, "review_required", False))
        ):
            continue
        results.append(
            service.evaluate_stock(
                account_no=account_no,
                trading_day=trading_day,
                stock_code=getattr(stock, "stock_code", ""),
                recovery_context=recovery,
            )
        )
    return {
        "evaluated_count": len(results),
        "requested_count": sum(item.get("early_close_requested") is True for item in results),
        "results": tuple(results),
    }


__all__ = [
    "StockLimitResponseService",
    "evaluate_main_window_stock_limit_after_chejan",
    "resume_main_window_stock_limit_responses",
    "stock_limit_command_id",
    "stock_limit_command_source",
    "stock_limit_event_id",
]
