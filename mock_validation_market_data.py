# -*- coding: utf-8 -*-
"""Immutable, process-local market snapshots for Mock Validation.

This module performs no Broker I/O and owns no Production persistence.  The
Kiwoom adapter emits orderbook snapshots into it; a later phase may consume
them for virtual matching.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Mapping

from mock_validation_contract import (
    MockValidationError,
    normalized_stock_code,
    payload_hash,
)
from kiwoom_realtime_fids import (
    REALTIME_ASK_PRICE_FIDS,
    REALTIME_ASK_QTY_FIDS,
    REALTIME_BID_PRICE_FIDS,
    REALTIME_BID_QTY_FIDS,
    REALTIME_ORDERBOOK_TIME_FID,
    REALTIME_ORDERBOOK_TYPE,
    REALTIME_TOTAL_ASK_QTY_FID,
    REALTIME_TOTAL_BID_QTY_FID,
)


FRESH = "FRESH"
STALE = "STALE"
SESSION_INVALID = "SESSION_INVALID"
NO_DATA = "NO_DATA"


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _price(value: Any) -> int | float | None:
    number = _number(value)
    if number is None or number == 0:
        return None
    number = abs(number)
    return int(number) if float(number).is_integer() else number


def _quantity(value: Any) -> int | float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return number


def _aware_datetime(value: datetime | str | None) -> datetime | None:
    observed = value
    if observed is None:
        observed = datetime.now().astimezone()
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed)
        except ValueError:
            return None
    if not isinstance(observed, datetime) or observed.tzinfo is None:
        return None
    return observed


def _mapping_value(values: Mapping[Any, Any], fid: int) -> Any:
    if fid in values:
        return values[fid]
    return values.get(str(fid))


@dataclass(frozen=True)
class MockOrderbookLevel:
    level: int
    price: int | float | None
    quantity: int | float | None


@dataclass(frozen=True)
class MockOrderbookSnapshot:
    stock_code: str
    real_type: str
    quote_time_raw: str
    received_at: str
    connection_epoch: int
    login_session_id: str
    receive_sequence: int
    asks: tuple[MockOrderbookLevel, ...]
    bids: tuple[MockOrderbookLevel, ...]
    total_ask_qty: int | float | None
    total_bid_qty: int | float | None
    content_hash: str
    snapshot_identity: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MockTradeSnapshot:
    stock_code: str
    current_price: int | float
    execution_price: int | float
    execution_qty: int | float | None
    execution_qty_signed: int | float | None
    trade_side: str
    execution_time: str
    market_datetime: str
    received_at: str
    connection_epoch: int
    login_session_id: str
    receive_sequence: int
    snapshot_identity: str


@dataclass(frozen=True)
class MockMarketSnapshot:
    stock_code: str
    orderbook: MockOrderbookSnapshot
    trade: MockTradeSnapshot | None
    snapshot_identity: str


@dataclass(frozen=True)
class MockMarketFreshness:
    stock_code: str
    status: str
    age_seconds: float | None
    snapshot_identity: str
    reason: str


def normalize_mock_orderbook_snapshot(
    *,
    stock_code: Any,
    raw_values: Mapping[Any, Any],
    connection_epoch: Any,
    login_session_id: Any,
    receive_sequence: Any,
    received_at: datetime | str | None = None,
) -> MockOrderbookSnapshot | None:
    """Normalize one official 10-level orderbook event.

    Prices use absolute Kiwoom wire values.  Zero, empty, negative quantity,
    and malformed numeric values become unavailable (``None``); all ten level
    slots remain present and ordered from level 1 through level 10.
    """

    if not isinstance(raw_values, Mapping):
        return None
    try:
        code = normalized_stock_code(stock_code)
        epoch = int(connection_epoch)
        sequence = int(receive_sequence)
    except (MockValidationError, TypeError, ValueError):
        return None
    session_id = str(login_session_id or "").strip()
    observed = _aware_datetime(received_at)
    if epoch <= 0 or sequence <= 0 or not session_id or observed is None:
        return None

    asks = tuple(
        MockOrderbookLevel(
            level=index + 1,
            price=_price(_mapping_value(raw_values, price_fid)),
            quantity=_quantity(_mapping_value(raw_values, qty_fid)),
        )
        for index, (price_fid, qty_fid) in enumerate(
            zip(REALTIME_ASK_PRICE_FIDS, REALTIME_ASK_QTY_FIDS)
        )
    )
    bids = tuple(
        MockOrderbookLevel(
            level=index + 1,
            price=_price(_mapping_value(raw_values, price_fid)),
            quantity=_quantity(_mapping_value(raw_values, qty_fid)),
        )
        for index, (price_fid, qty_fid) in enumerate(
            zip(REALTIME_BID_PRICE_FIDS, REALTIME_BID_QTY_FIDS)
        )
    )
    normalized_content = {
        "quote_time_raw": str(
            _mapping_value(raw_values, REALTIME_ORDERBOOK_TIME_FID) or ""
        ).strip(),
        "asks": [asdict(level) for level in asks],
        "bids": [asdict(level) for level in bids],
        "total_ask_qty": _quantity(
            _mapping_value(raw_values, REALTIME_TOTAL_ASK_QTY_FID)
        ),
        "total_bid_qty": _quantity(
            _mapping_value(raw_values, REALTIME_TOTAL_BID_QTY_FID)
        ),
    }
    content_hash = payload_hash(normalized_content)
    received_text = observed.isoformat(timespec="microseconds")
    identity = "MOB-" + payload_hash(
        {
            "stock_code": code,
            "connection_epoch": epoch,
            "login_session_id": session_id,
            "receive_sequence": sequence,
            "received_at": received_text,
            "content_hash": content_hash,
        }
    )
    return MockOrderbookSnapshot(
        stock_code=code,
        real_type=REALTIME_ORDERBOOK_TYPE,
        quote_time_raw=normalized_content["quote_time_raw"],
        received_at=received_text,
        connection_epoch=epoch,
        login_session_id=session_id,
        receive_sequence=sequence,
        asks=asks,
        bids=bids,
        total_ask_qty=normalized_content["total_ask_qty"],
        total_bid_qty=normalized_content["total_bid_qty"],
        content_hash=content_hash,
        snapshot_identity=identity,
    )


def normalize_mock_trade_snapshot(payload: Mapping[str, Any]) -> MockTradeSnapshot | None:
    """Copy the existing realtime-shadow tick payload into the Mock domain."""

    if not isinstance(payload, Mapping):
        return None
    try:
        code = normalized_stock_code(payload.get("stock_code"))
        epoch = int(payload.get("connection_epoch"))
        sequence = int(payload.get("receive_sequence"))
    except (MockValidationError, TypeError, ValueError):
        return None
    session_id = str(payload.get("login_session_id") or "").strip()
    price = _price(payload.get("current_price"))
    observed = _aware_datetime(str(payload.get("received_at") or ""))
    if epoch <= 0 or sequence <= 0 or not session_id or price is None or observed is None:
        return None
    signed_quantity = _number(payload.get("trade_volume_raw"))
    quantity = (
        abs(signed_quantity)
        if signed_quantity is not None
        else _quantity(payload.get("trade_volume_abs"))
    )
    trade_side = (
        "BUY"
        if signed_quantity is not None and signed_quantity > 0
        else "SELL"
        if signed_quantity is not None and signed_quantity < 0
        else "UNKNOWN"
    )
    normalized = {
        "stock_code": code,
        "current_price": price,
        "execution_price": price,
        "execution_qty": quantity,
        "execution_qty_signed": signed_quantity,
        "trade_side": trade_side,
        "execution_time": str(payload.get("execution_time_raw") or "").strip(),
        "market_datetime": str(payload.get("market_datetime") or "").strip(),
        "received_at": observed.isoformat(timespec="microseconds"),
        "connection_epoch": epoch,
        "login_session_id": session_id,
        "receive_sequence": sequence,
    }
    return MockTradeSnapshot(
        **normalized,
        snapshot_identity="MTR-" + payload_hash(normalized),
    )


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


class MockValidationMarketDataStore:
    """In-memory, stock-shared snapshots guarded by Broker session identity."""

    def __init__(self) -> None:
        self._active = False
        self._connection_epoch = 0
        self._login_session_id = ""
        self._targets: frozenset[str] = frozenset()
        self._orderbooks: dict[str, MockOrderbookSnapshot] = {}
        self._trades: dict[str, MockTradeSnapshot] = {}

    def apply_registration_snapshot(self, snapshot: Any) -> None:
        active = _field(snapshot, "active", False) is True
        try:
            epoch = int(_field(snapshot, "connection_epoch", 0) or 0)
        except (TypeError, ValueError):
            epoch = 0
        session_id = str(_field(snapshot, "login_session_id", "") or "").strip()
        targets: set[str] = set()
        for raw_code in tuple(_field(snapshot, "target_stock_codes", ()) or ()):
            try:
                targets.add(normalized_stock_code(raw_code))
            except MockValidationError:
                continue
        identity_changed = (
            epoch != self._connection_epoch
            or session_id != self._login_session_id
        )
        if identity_changed or not active:
            self._orderbooks.clear()
            self._trades.clear()
        else:
            self._orderbooks = {
                code: value for code, value in self._orderbooks.items() if code in targets
            }
            self._trades = {
                code: value for code, value in self._trades.items() if code in targets
            }
        self._active = bool(active and epoch > 0 and session_id)
        self._connection_epoch = epoch
        self._login_session_id = session_id
        self._targets = frozenset(targets if self._active else ())

    def accept_orderbook(self, snapshot: MockOrderbookSnapshot) -> bool:
        if not isinstance(snapshot, MockOrderbookSnapshot):
            return False
        if not self._matches_active_session(snapshot):
            return False
        previous = self._orderbooks.get(snapshot.stock_code)
        if previous is not None:
            if snapshot.receive_sequence < previous.receive_sequence:
                return False
            if snapshot.receive_sequence == previous.receive_sequence:
                return snapshot.snapshot_identity == previous.snapshot_identity
        self._orderbooks[snapshot.stock_code] = snapshot
        return True

    def accept_trade(self, payload: Mapping[str, Any]) -> bool:
        snapshot = normalize_mock_trade_snapshot(payload)
        if snapshot is None or not self._matches_active_session(snapshot):
            return False
        previous = self._trades.get(snapshot.stock_code)
        if previous is not None:
            if snapshot.receive_sequence < previous.receive_sequence:
                return False
            if snapshot.receive_sequence == previous.receive_sequence:
                return snapshot.snapshot_identity == previous.snapshot_identity
        self._trades[snapshot.stock_code] = snapshot
        return True

    def _matches_active_session(self, snapshot: Any) -> bool:
        return bool(
            self._active
            and snapshot.stock_code in self._targets
            and snapshot.connection_epoch == self._connection_epoch
            and snapshot.login_session_id == self._login_session_id
        )

    def latest_orderbook(self, stock_code: Any) -> MockOrderbookSnapshot | None:
        try:
            code = normalized_stock_code(stock_code)
        except MockValidationError:
            return None
        snapshot = self._orderbooks.get(code)
        return snapshot if snapshot is not None and self._matches_active_session(snapshot) else None

    def latest_trade(self, stock_code: Any) -> MockTradeSnapshot | None:
        try:
            code = normalized_stock_code(stock_code)
        except MockValidationError:
            return None
        snapshot = self._trades.get(code)
        return snapshot if snapshot is not None and self._matches_active_session(snapshot) else None

    def market_snapshot(self, stock_code: Any) -> MockMarketSnapshot | None:
        orderbook = self.latest_orderbook(stock_code)
        if orderbook is None:
            return None
        trade = self.latest_trade(orderbook.stock_code)
        identity = "MMK-" + payload_hash(
            {
                "stock_code": orderbook.stock_code,
                "orderbook": orderbook.snapshot_identity,
                "trade": trade.snapshot_identity if trade is not None else "",
            }
        )
        return MockMarketSnapshot(
            stock_code=orderbook.stock_code,
            orderbook=orderbook,
            trade=trade,
            snapshot_identity=identity,
        )

    def freshness(
        self,
        stock_code: Any,
        *,
        now: datetime,
        max_age_seconds: float,
    ) -> MockMarketFreshness:
        try:
            code = normalized_stock_code(stock_code)
            threshold = float(max_age_seconds)
        except (MockValidationError, TypeError, ValueError):
            return MockMarketFreshness(str(stock_code or ""), SESSION_INVALID, None, "", "INVALID_ARGUMENT")
        if not self._active or not self._login_session_id or self._connection_epoch <= 0:
            return MockMarketFreshness(code, SESSION_INVALID, None, "", "REGISTRATION_INACTIVE")
        if code not in self._targets:
            return MockMarketFreshness(code, NO_DATA, None, "", "STOCK_NOT_REGISTERED")
        snapshot = self.latest_orderbook(code)
        if snapshot is None:
            return MockMarketFreshness(code, NO_DATA, None, "", "ORDERBOOK_UNAVAILABLE")
        current = _aware_datetime(now)
        observed = _aware_datetime(snapshot.received_at)
        if current is None or observed is None or threshold < 0:
            return MockMarketFreshness(code, SESSION_INVALID, None, snapshot.snapshot_identity, "TIME_ARGUMENT_INVALID")
        age = (current - observed).total_seconds()
        if age < 0 or age > threshold:
            return MockMarketFreshness(code, STALE, age, snapshot.snapshot_identity, "ORDERBOOK_AGE_EXCEEDED")
        return MockMarketFreshness(code, FRESH, age, snapshot.snapshot_identity, "CURRENT_SESSION_ORDERBOOK")


__all__ = [
    "FRESH",
    "NO_DATA",
    "SESSION_INVALID",
    "STALE",
    "MockMarketFreshness",
    "MockMarketSnapshot",
    "MockOrderbookLevel",
    "MockOrderbookSnapshot",
    "MockTradeSnapshot",
    "MockValidationMarketDataStore",
    "normalize_mock_orderbook_snapshot",
    "normalize_mock_trade_snapshot",
]
