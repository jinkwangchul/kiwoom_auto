# -*- coding: utf-8 -*-
"""Read-only contracts for collecting and validating broker recovery evidence.

This module owns no Runtime file and performs no mutation. Broker snapshots and
recovery decisions remain in memory until a later approved Production caller
connects them to existing reconciliation boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable, Mapping

from stock_code_contract import normalize_broker_stock_code


ACCOUNT_NOT_STARTED = "NOT_STARTED"
ACCOUNT_COLLECTING = "COLLECTING"
ACCOUNT_RECONCILING = "RECONCILING"
ACCOUNT_REVIEW_REQUIRED = "REVIEW_REQUIRED"
ACCOUNT_COMPLETED = "COMPLETED"
ACCOUNT_FAILED = "FAILED"

STOCK_PENDING = "PENDING"
STOCK_RESTORED = "RESTORED"
STOCK_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STOCK_FAILED = "FAILED"
STOCK_NOT_APPLICABLE = "NOT_APPLICABLE"

RECOVERY_GATE_ALLOWED = "RECOVERY_COMPLETED"
RECOVERY_GATE_ACCOUNT_INCOMPLETE = "ACCOUNT_RECOVERY_INCOMPLETE"
RECOVERY_GATE_STOCK_INCOMPLETE = "STOCK_RECOVERY_INCOMPLETE"
RECOVERY_GATE_IDENTITY_MISMATCH = "RECOVERY_IDENTITY_MISMATCH"
RECOVERY_GATE_STALE = "STALE_RECOVERY_SESSION"
RECOVERY_GATE_UNKNOWN = "UNKNOWN_RECOVERY_STATE"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_trading_day(value: Any) -> str:
    text = _text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def normalize_stock_code(value: Any) -> str:
    return normalize_broker_stock_code(value)


def decimal_value(value: Any, *, absolute: bool = False) -> Decimal:
    text = _text(value).replace(",", "")
    if not text:
        raise ValueError("numeric value is required")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric value is invalid") from exc
    if not number.is_finite():
        raise ValueError("numeric value must be finite")
    return abs(number) if absolute else number


def integer_value(value: Any, *, absolute: bool = False) -> int:
    number = decimal_value(value, absolute=absolute)
    if number != number.to_integral_value():
        raise ValueError("integer value is required")
    return int(number)


@dataclass(frozen=True)
class RecoverySessionIdentity:
    recovery_session_id: str
    login_session_id: str
    account_no: str
    trading_day: str
    requested_at: str


@dataclass(frozen=True)
class BrokerHoldingSnapshotItem:
    account_no: str
    stock_code: str
    stock_name: str
    holding_quantity: int
    available_quantity: int
    average_price: Decimal
    current_price: Decimal | None
    evaluation_amount: Decimal | None
    profit_loss: Decimal | None
    profit_rate: Decimal | None


@dataclass(frozen=True)
class BrokerOpenOrderSnapshotItem:
    account_no: str
    broker_order_no: str
    original_order_no: str
    stock_code: str
    order_side: str
    order_type: str
    order_price: Decimal
    order_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    order_status: str
    order_time: str


@dataclass(frozen=True)
class BrokerSnapshotPart:
    kind: str
    account_no: str
    trading_day: str
    requested_at: str
    completed_at: str
    request_id: str
    recovery_session_id: str
    is_complete: bool
    items: tuple[BrokerHoldingSnapshotItem | BrokerOpenOrderSnapshotItem, ...]
    source: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    account_no: str
    trading_day: str
    requested_at: str
    completed_at: str
    request_id: str
    recovery_session_id: str
    is_complete: bool
    holdings: tuple[BrokerHoldingSnapshotItem, ...]
    open_orders: tuple[BrokerOpenOrderSnapshotItem, ...]
    source: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class StockRecoveryResult:
    stock_code: str
    status: str
    holding_matched: bool
    order_matched: bool
    review_required: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountRecoveryResult:
    status: str
    recovery_session_id: str
    account_no: str
    trading_day: str
    stock_results: tuple[StockRecoveryResult, ...]
    review_required: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryGateDecision:
    allowed: bool
    reason_code: str
    account_status: str
    stock_status: str
    recovery_identity: str
    evidence: tuple[str, ...] = ()


def create_recovery_session_identity(
    *,
    login_session_id: Any,
    account_no: Any,
    trading_day: Any,
    requested_at: Any,
) -> RecoverySessionIdentity:
    login_session = _text(login_session_id)
    account = _text(account_no)
    day = _parse_trading_day(trading_day)
    requested = _text(requested_at)
    if not login_session:
        raise ValueError("login_session_id is required")
    if not account:
        raise ValueError("account_no is required")
    if not day:
        raise ValueError("trading_day must be YYYY-MM-DD")
    if _parse_datetime(requested) is None:
        raise ValueError("requested_at must be ISO-8601")
    identity_hash = _canonical_hash(
        {
            "login_session_id": login_session,
            "account_no": account,
            "trading_day": day,
            "requested_at": requested,
            "source": "KIWOOM_OPENAPI_RECOVERY",
        }
    )
    return RecoverySessionIdentity(
        recovery_session_id=f"RECOVERY_SESSION_{identity_hash}",
        login_session_id=login_session,
        account_no=account,
        trading_day=day,
        requested_at=requested,
    )


def recovery_request_id(identity: RecoverySessionIdentity, kind: str) -> str:
    clean_kind = _text(kind).upper()
    if clean_kind not in {"HOLDINGS", "OPEN_ORDERS", "ACCOUNT"}:
        raise ValueError("unsupported recovery request kind")
    digest = _canonical_hash(
        {
            "recovery_session_id": identity.recovery_session_id,
            "kind": clean_kind,
        }
    )
    return f"RECOVERY_{clean_kind}_{digest}"


def _optional_decimal(value: Any, *, absolute: bool = False) -> Decimal | None:
    if not _text(value):
        return None
    return decimal_value(value, absolute=absolute)


def parse_holding_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    account_no: str,
) -> tuple[tuple[BrokerHoldingSnapshotItem, ...], tuple[str, ...]]:
    items: list[BrokerHoldingSnapshotItem] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        try:
            code = normalize_stock_code(raw.get("종목번호"))
            if not code:
                raise ValueError("stock_code is required")
            if code in seen:
                raise ValueError(f"duplicate stock_code: {code}")
            seen.add(code)
            item = BrokerHoldingSnapshotItem(
                account_no=account_no,
                stock_code=code,
                stock_name=_text(raw.get("종목명")),
                holding_quantity=integer_value(raw.get("보유수량"), absolute=True),
                available_quantity=integer_value(raw.get("매매가능수량"), absolute=True),
                average_price=decimal_value(raw.get("매입가"), absolute=True),
                current_price=_optional_decimal(raw.get("현재가"), absolute=True),
                evaluation_amount=_optional_decimal(raw.get("평가금액"), absolute=True),
                profit_loss=_optional_decimal(raw.get("평가손익")),
                profit_rate=_optional_decimal(raw.get("수익률(%)")),
            )
            if item.available_quantity > item.holding_quantity:
                raise ValueError("available_quantity exceeds holding_quantity")
            items.append(item)
        except (TypeError, ValueError) as exc:
            errors.append(f"holdings[{index}]: {exc}")
    return tuple(items), tuple(errors)


def _normalize_order_side(raw_side: Any, raw_type: Any) -> str:
    side = _text(raw_side)
    order_type = _text(raw_type)
    if side in {"1", "SELL", "매도"} or "매도" in order_type:
        return "SELL"
    if side in {"2", "BUY", "매수"} or "매수" in order_type:
        return "BUY"
    return "UNKNOWN"


def _normalize_order_type(value: Any) -> str:
    text = _text(value)
    if "취소" in text:
        return "CANCEL"
    if "정정" in text:
        return "MODIFY"
    return "NEW"


def parse_open_order_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    account_no: str,
) -> tuple[tuple[BrokerOpenOrderSnapshotItem, ...], tuple[str, ...]]:
    items: list[BrokerOpenOrderSnapshotItem] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        try:
            row_account = _text(raw.get("계좌번호"))
            if row_account and row_account != account_no:
                raise ValueError("account_no mismatch")
            broker_order_no = _text(raw.get("주문번호"))
            if not broker_order_no:
                raise ValueError("broker_order_no is required")
            if broker_order_no in seen:
                raise ValueError(f"duplicate broker_order_no: {broker_order_no}")
            seen.add(broker_order_no)
            quantity = integer_value(raw.get("주문수량"), absolute=True)
            unfilled = integer_value(raw.get("미체결수량"), absolute=True)
            if unfilled > quantity:
                raise ValueError("unfilled_quantity exceeds order_quantity")
            side = _normalize_order_side(raw.get("매매구분"), raw.get("주문구분"))
            if side == "UNKNOWN":
                raise ValueError("order_side is unknown")
            item = BrokerOpenOrderSnapshotItem(
                account_no=account_no,
                broker_order_no=broker_order_no,
                original_order_no=_text(raw.get("원주문번호")),
                stock_code=normalize_stock_code(raw.get("종목코드")),
                order_side=side,
                order_type=_normalize_order_type(raw.get("주문구분")),
                order_price=decimal_value(raw.get("주문가격") or "0", absolute=True),
                order_quantity=quantity,
                filled_quantity=quantity - unfilled,
                unfilled_quantity=unfilled,
                order_status=_text(raw.get("주문상태")),
                order_time=_text(raw.get("시간")),
            )
            if not item.stock_code:
                raise ValueError("stock_code is required")
            items.append(item)
        except (TypeError, ValueError) as exc:
            errors.append(f"open_orders[{index}]: {exc}")
    return tuple(items), tuple(errors)


def build_snapshot_part(
    *,
    identity: RecoverySessionIdentity,
    kind: str,
    rows: Iterable[Mapping[str, Any]],
    completed_at: Any,
    source: str,
    collection_complete: bool,
    collection_errors: Iterable[Any] = (),
) -> BrokerSnapshotPart:
    clean_kind = _text(kind).upper()
    errors = [_text(item) for item in collection_errors if _text(item)]
    completed = _text(completed_at)
    if _parse_datetime(completed) is None:
        errors.append("completed_at must be ISO-8601")
    if clean_kind == "HOLDINGS":
        items, parse_errors = parse_holding_rows(rows, account_no=identity.account_no)
    elif clean_kind == "OPEN_ORDERS":
        items, parse_errors = parse_open_order_rows(rows, account_no=identity.account_no)
    else:
        raise ValueError("unsupported snapshot part kind")
    errors.extend(parse_errors)
    if not collection_complete:
        errors.append("broker snapshot collection is incomplete")
    return BrokerSnapshotPart(
        kind=clean_kind,
        account_no=identity.account_no,
        trading_day=identity.trading_day,
        requested_at=identity.requested_at,
        completed_at=completed,
        request_id=recovery_request_id(identity, clean_kind),
        recovery_session_id=identity.recovery_session_id,
        is_complete=not errors,
        items=items,
        source=_text(source),
        errors=tuple(dict.fromkeys(errors)),
    )


def combine_account_snapshot(
    identity: RecoverySessionIdentity,
    holdings: BrokerSnapshotPart,
    open_orders: BrokerSnapshotPart,
) -> BrokerAccountSnapshot:
    errors: list[str] = []
    for part, expected_kind in (
        (holdings, "HOLDINGS"),
        (open_orders, "OPEN_ORDERS"),
    ):
        if part.kind != expected_kind:
            errors.append(f"{expected_kind.lower()} snapshot kind mismatch")
        if part.account_no != identity.account_no:
            errors.append(f"{expected_kind.lower()} account mismatch")
        if part.trading_day != identity.trading_day:
            errors.append(f"{expected_kind.lower()} trading day mismatch")
        if part.recovery_session_id != identity.recovery_session_id:
            errors.append(f"{expected_kind.lower()} recovery session mismatch")
        if not part.is_complete:
            errors.extend(part.errors or (f"{expected_kind.lower()} snapshot incomplete",))
    holding_items = tuple(
        item for item in holdings.items if isinstance(item, BrokerHoldingSnapshotItem)
    )
    order_items = tuple(
        item for item in open_orders.items if isinstance(item, BrokerOpenOrderSnapshotItem)
    )
    completed_candidates = [
        value
        for value in (holdings.completed_at, open_orders.completed_at)
        if _parse_datetime(value) is not None
    ]
    completed_at = max(completed_candidates) if completed_candidates else ""
    return BrokerAccountSnapshot(
        account_no=identity.account_no,
        trading_day=identity.trading_day,
        requested_at=identity.requested_at,
        completed_at=completed_at,
        request_id=recovery_request_id(identity, "ACCOUNT"),
        recovery_session_id=identity.recovery_session_id,
        is_complete=not errors,
        holdings=holding_items,
        open_orders=order_items,
        source="KIWOOM_OPENAPI",
        errors=tuple(dict.fromkeys(errors)),
    )


def _runtime_quantity(record: Mapping[str, Any]) -> int:
    for field in ("quantity", "holding_quantity", "holding_qty"):
        if _text(record.get(field)):
            return integer_value(record.get(field), absolute=True)
    return 0


def _runtime_available_quantity(record: Mapping[str, Any]) -> int | None:
    for field in ("available_quantity", "available_qty"):
        if _text(record.get(field)):
            return integer_value(record.get(field), absolute=True)
    return None


def _runtime_average_price(record: Mapping[str, Any]) -> Decimal:
    for field in ("average_price", "avg_price"):
        if _text(record.get(field)):
            return decimal_value(record.get(field), absolute=True)
    return Decimal("0")


def _first_present(record: Mapping[str, Any], *fields: str) -> Any:
    for field in fields:
        if field in record and record.get(field) is not None:
            return record.get(field)
    return None


def _active_runtime_orders(
    orders: Iterable[Mapping[str, Any]],
    *,
    account_no: str,
    stock_code: str,
) -> tuple[Mapping[str, Any], ...]:
    terminal = {
        "FILLED",
        "CANCELLED",
        "PARTIAL_CANCELLED",
        "BROKER_REJECTED",
        "SEND_CALL_REJECTED",
        "BLOCKED",
        "BLOCKED_POLICY",
    }
    return tuple(
        order
        for order in orders
        if _text(order.get("account_no")) == account_no
        and normalize_stock_code(order.get("code") or order.get("stock_code"))
        == stock_code
        and _text(order.get("status")).upper() not in terminal
    )


def decide_stock_recovery(
    *,
    snapshot: BrokerAccountSnapshot,
    stock_code: Any,
    runtime_position: Mapping[str, Any] | None,
    runtime_orders: Iterable[Mapping[str, Any]],
) -> StockRecoveryResult:
    """Compare one stock using only immutable Broker and Runtime snapshots."""
    code = normalize_stock_code(stock_code)
    if not code:
        return StockRecoveryResult(
            "",
            STOCK_FAILED,
            False,
            False,
            True,
            ("INVALID_STOCK_CODE",),
        )
    if not snapshot.is_complete:
        return StockRecoveryResult(
            code,
            STOCK_FAILED,
            False,
            False,
            True,
            ("INCOMPLETE_BROKER_SNAPSHOT",),
        )

    reasons: list[str] = []
    broker_holdings = [
        item for item in snapshot.holdings if item.stock_code == code
    ]
    position = dict(runtime_position) if isinstance(runtime_position, Mapping) else {}
    if position:
        if _text(position.get("account_no")) != snapshot.account_no:
            reasons.append("ACCOUNT_MISMATCH")
        if normalize_stock_code(position.get("code") or position.get("stock_code")) != code:
            reasons.append("RUNTIME_POSITION_IDENTITY_MISMATCH")

    try:
        runtime_qty = _runtime_quantity(position)
        runtime_available = _runtime_available_quantity(position)
        runtime_avg = _runtime_average_price(position)
    except ValueError:
        return StockRecoveryResult(
            code,
            STOCK_FAILED,
            False,
            False,
            True,
            ("DAMAGED_RUNTIME",),
        )

    broker_holding = broker_holdings[0] if len(broker_holdings) == 1 else None
    if len(broker_holdings) > 1:
        reasons.append("DUPLICATE_BROKER_HOLDING")
    elif broker_holding is None and runtime_qty > 0:
        reasons.append("RUNTIME_ONLY_HOLDING")
    elif broker_holding is not None and broker_holding.holding_quantity > 0 and runtime_qty == 0:
        reasons.append("BROKER_ONLY_HOLDING")
    elif broker_holding is not None:
        if broker_holding.holding_quantity != runtime_qty:
            reasons.append("HOLDING_QUANTITY_MISMATCH")
        if (
            runtime_available is not None
            and broker_holding.available_quantity != runtime_available
        ):
            reasons.append("AVAILABLE_QUANTITY_MISMATCH")
        if runtime_qty > 0 and broker_holding.average_price != runtime_avg:
            reasons.append("AVERAGE_PRICE_MISMATCH")
    holding_matched = not any(
        reason
        in {
            "DUPLICATE_BROKER_HOLDING",
            "RUNTIME_ONLY_HOLDING",
            "BROKER_ONLY_HOLDING",
            "HOLDING_QUANTITY_MISMATCH",
            "AVAILABLE_QUANTITY_MISMATCH",
            "AVERAGE_PRICE_MISMATCH",
        }
        for reason in reasons
    )

    broker_orders = [
        item for item in snapshot.open_orders if item.stock_code == code
    ]
    active_orders = _active_runtime_orders(
        runtime_orders,
        account_no=snapshot.account_no,
        stock_code=code,
    )
    runtime_by_broker_no: dict[str, list[Mapping[str, Any]]] = {}
    for order in active_orders:
        broker_no = _text(order.get("broker_order_no"))
        if broker_no:
            runtime_by_broker_no.setdefault(broker_no, []).append(order)

    broker_numbers = {item.broker_order_no for item in broker_orders}
    for broker_order in broker_orders:
        matches = runtime_by_broker_no.get(broker_order.broker_order_no, [])
        if not matches:
            reasons.append("BROKER_ONLY_ORDER")
            continue
        if len(matches) > 1:
            reasons.append("DUPLICATE_ORDER_RISK")
            continue
        runtime_order = matches[0]
        runtime_side = _text(runtime_order.get("side") or runtime_order.get("order_side")).upper()
        if runtime_side and runtime_side != broker_order.order_side:
            reasons.append("ORDER_IDENTITY_CONFLICT")
        try:
            runtime_order_qty = integer_value(
                _first_present(runtime_order, "quantity", "order_quantity"),
                absolute=True,
            )
            runtime_unfilled = integer_value(
                _first_present(
                    runtime_order,
                    "remaining_quantity",
                    "unfilled_quantity",
                ),
                absolute=True,
            )
        except ValueError:
            reasons.append("DAMAGED_RUNTIME")
            continue
        if (
            runtime_order_qty != broker_order.order_quantity
            or runtime_unfilled != broker_order.unfilled_quantity
        ):
            reasons.append("ORDER_IDENTITY_CONFLICT")
        runtime_original = _text(runtime_order.get("original_order_no"))
        if (
            runtime_original
            and runtime_original != broker_order.original_order_no
        ):
            reasons.append("ORDER_IDENTITY_CONFLICT")

    for runtime_order in active_orders:
        broker_no = _text(runtime_order.get("broker_order_no"))
        if not broker_no or broker_no not in broker_numbers:
            reasons.append("RUNTIME_ONLY_ORDER")

    order_reason_codes = {
        "BROKER_ONLY_ORDER",
        "RUNTIME_ONLY_ORDER",
        "ORDER_IDENTITY_CONFLICT",
        "DUPLICATE_ORDER_RISK",
        "DAMAGED_RUNTIME",
    }
    order_matched = not any(reason in order_reason_codes for reason in reasons)
    unique_reasons = tuple(dict.fromkeys(reasons))
    status = STOCK_RESTORED if not unique_reasons else STOCK_REVIEW_REQUIRED
    return StockRecoveryResult(
        code,
        status,
        holding_matched,
        order_matched,
        bool(unique_reasons),
        unique_reasons,
    )


def decide_account_recovery(
    *,
    identity: RecoverySessionIdentity,
    snapshot: BrokerAccountSnapshot,
    stock_results: Iterable[StockRecoveryResult],
) -> AccountRecoveryResult:
    results = tuple(stock_results)
    reasons: list[str] = []
    if snapshot.recovery_session_id != identity.recovery_session_id:
        reasons.append("RECOVERY_IDENTITY_MISMATCH")
    if snapshot.account_no != identity.account_no:
        reasons.append("ACCOUNT_MISMATCH")
    if snapshot.trading_day != identity.trading_day:
        reasons.append("STALE_RECOVERY_SESSION")
    if not snapshot.is_complete:
        reasons.append("INCOMPLETE_BROKER_SNAPSHOT")
    if reasons or any(result.status == STOCK_FAILED for result in results):
        status = ACCOUNT_FAILED
    elif any(result.status == STOCK_REVIEW_REQUIRED for result in results):
        status = ACCOUNT_REVIEW_REQUIRED
    elif all(result.status == STOCK_RESTORED for result in results):
        status = ACCOUNT_COMPLETED
    else:
        status = ACCOUNT_RECONCILING
    return AccountRecoveryResult(
        status=status,
        recovery_session_id=identity.recovery_session_id,
        account_no=identity.account_no,
        trading_day=identity.trading_day,
        stock_results=results,
        review_required=status == ACCOUNT_REVIEW_REQUIRED,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def decide_recovery_gate(
    *,
    identity: RecoverySessionIdentity | None,
    expected_login_session_id: Any,
    expected_account_no: Any,
    expected_trading_day: Any,
    expected_recovery_session_id: Any,
    account_status: Any,
    stock_status: Any,
    evidence: Iterable[Any] = (),
) -> RecoveryGateDecision:
    account_state = _text(account_status).upper()
    stock_state = _text(stock_status).upper()
    identity_text = _text(expected_recovery_session_id)
    evidence_items = tuple(_text(item) for item in evidence if _text(item))
    if identity is None:
        return RecoveryGateDecision(
            False,
            RECOVERY_GATE_UNKNOWN,
            account_state,
            stock_state,
            identity_text,
            evidence_items,
        )
    if (
        identity.login_session_id != _text(expected_login_session_id)
        or identity.account_no != _text(expected_account_no)
        or identity.recovery_session_id != identity_text
    ):
        return RecoveryGateDecision(
            False,
            RECOVERY_GATE_IDENTITY_MISMATCH,
            account_state,
            stock_state,
            identity.recovery_session_id,
            evidence_items,
        )
    if identity.trading_day != _parse_trading_day(expected_trading_day):
        return RecoveryGateDecision(
            False,
            RECOVERY_GATE_STALE,
            account_state,
            stock_state,
            identity.recovery_session_id,
            evidence_items,
        )
    if account_state != ACCOUNT_COMPLETED:
        return RecoveryGateDecision(
            False,
            RECOVERY_GATE_ACCOUNT_INCOMPLETE,
            account_state,
            stock_state,
            identity.recovery_session_id,
            evidence_items,
        )
    if stock_state != STOCK_RESTORED:
        return RecoveryGateDecision(
            False,
            RECOVERY_GATE_STOCK_INCOMPLETE,
            account_state,
            stock_state,
            identity.recovery_session_id,
            evidence_items,
        )
    return RecoveryGateDecision(
        True,
        RECOVERY_GATE_ALLOWED,
        account_state,
        stock_state,
        identity.recovery_session_id,
        evidence_items,
    )
