"""Production CURRENT_PRICE authority without merging KRX and NXT identities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from candle_timeframe_aggregation import SEOUL_TIMEZONE, parse_market_datetime
from stock_code_contract import is_broker_action_stock_code, normalize_stock_code


KRX_CURRENT_PRICE_SOURCE = "KRX"
NXT_CURRENT_PRICE_SOURCE = "NXT"

KRX_REGULAR_AUTHORITY = "KRX_0900_1530"
NXT_MORNING_AUTHORITY = "NXT_0800_0850"
NXT_AFTER_HOURS_AUTHORITY = "NXT_1540_2000"


@dataclass(frozen=True)
class ProductionCurrentPriceAuthority:
    market_source: str
    authority_window: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class ProductionCurrentPriceEvidence:
    canonical_stock_code: str
    broker_code_identity: str
    market_source: str
    source_real_type: str
    current_price: int | float
    market_datetime: str
    received_at: str
    receive_sequence: int
    connection_epoch: int
    login_session_id: str
    authority_window: str


def _seoul_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(SEOUL_TIMEZONE)
    if current.tzinfo is None:
        return current.replace(tzinfo=SEOUL_TIMEZONE)
    return current.astimezone(SEOUL_TIMEZONE)


def _window(
    current: datetime,
    *,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    market_source: str,
    authority_window: str,
) -> ProductionCurrentPriceAuthority:
    starts_at = current.replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0,
    )
    ends_at = current.replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
    )
    return ProductionCurrentPriceAuthority(
        market_source=market_source,
        authority_window=authority_window,
        starts_at=starts_at,
        ends_at=ends_at,
    )


def production_current_price_authority(
    *,
    now_dt: datetime | None = None,
    nxt_available: bool | None = None,
) -> ProductionCurrentPriceAuthority | None:
    """Resolve exactly one Production CURRENT_PRICE source for the current window."""

    current = _seoul_datetime(now_dt)
    minute = current.hour * 60 + current.minute
    if 8 * 60 <= minute < 8 * 60 + 50:
        if nxt_available is not True:
            return None
        return _window(
            current,
            start_hour=8,
            start_minute=0,
            end_hour=8,
            end_minute=50,
            market_source=NXT_CURRENT_PRICE_SOURCE,
            authority_window=NXT_MORNING_AUTHORITY,
        )
    if 9 * 60 <= minute < 15 * 60 + 30:
        return _window(
            current,
            start_hour=9,
            start_minute=0,
            end_hour=15,
            end_minute=30,
            market_source=KRX_CURRENT_PRICE_SOURCE,
            authority_window=KRX_REGULAR_AUTHORITY,
        )
    if 15 * 60 + 40 <= minute < 20 * 60:
        if nxt_available is not True:
            return None
        return _window(
            current,
            start_hour=15,
            start_minute=40,
            end_hour=20,
            end_minute=0,
            market_source=NXT_CURRENT_PRICE_SOURCE,
            authority_window=NXT_AFTER_HOURS_AUTHORITY,
        )
    return None


def _positive_price(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(numeric) or numeric <= 0:
        return None
    return int(numeric) if numeric.is_integer() else numeric


def select_production_current_price_evidence(
    *,
    canonical_stock_code: str,
    connection_epoch: int,
    login_session_id: str,
    nxt_available: bool | None,
    krx_state: object | None,
    nxt_state: object | None,
    now_dt: datetime | None = None,
) -> ProductionCurrentPriceEvidence | None:
    """Validate the authorized source and project one immutable price evidence."""

    code = normalize_stock_code(canonical_stock_code)
    session_id = str(login_session_id or "").strip()
    try:
        epoch = int(connection_epoch or 0)
    except (TypeError, ValueError):
        return None
    if not is_broker_action_stock_code(code) or epoch <= 0 or not session_id:
        return None

    current = _seoul_datetime(now_dt)
    authority = production_current_price_authority(
        now_dt=current,
        nxt_available=nxt_available,
    )
    if authority is None:
        return None

    if authority.market_source == KRX_CURRENT_PRICE_SOURCE:
        state = krx_state
        if state is None:
            return None
        state_code = str(getattr(state, "stock_code", "") or "").strip()
        broker_identity = str(
            getattr(state, "broker_code_identity", "") or state_code
        ).strip()
        market_source = str(
            getattr(state, "market_source", "") or KRX_CURRENT_PRICE_SOURCE
        ).strip()
        source_real_type = "주식체결"
        market_datetime_raw = getattr(state, "last_market_datetime", None)
        price_raw = getattr(state, "last_price", None)
        received_at = str(getattr(state, "last_received_at", "") or "").strip()
        sequence_raw = getattr(state, "last_receive_sequence", 0)
        if (
            state_code != code
            or broker_identity != code
            or market_source != KRX_CURRENT_PRICE_SOURCE
        ):
            return None
    else:
        state = nxt_state
        if state is None:
            return None
        state_code = str(
            getattr(state, "canonical_stock_code", "") or ""
        ).strip()
        broker_identity = str(
            getattr(state, "broker_code_identity", "") or ""
        ).strip()
        market_source = str(getattr(state, "market_source", "") or "").strip()
        source_real_type = str(
            getattr(state, "source_real_type", "") or ""
        ).strip()
        market_datetime_raw = getattr(state, "last_market_datetime", None)
        price_raw = getattr(state, "last_price", None)
        received_at = str(getattr(state, "updated_at", "") or "").strip()
        sequence_raw = getattr(state, "receive_sequence", 0)
        if (
            nxt_available is not True
            or state_code != code
            or broker_identity != f"{code}_NX"
            or market_source != NXT_CURRENT_PRICE_SOURCE
            or source_real_type != "ECN주식체결"
        ):
            return None

    try:
        state_epoch = int(getattr(state, "connection_epoch", 0) or 0)
    except (TypeError, ValueError):
        return None
    state_identity = (
        state_epoch,
        str(getattr(state, "login_session_id", "") or "").strip(),
    )
    if state_identity != (epoch, session_id):
        return None

    market_datetime = parse_market_datetime(market_datetime_raw)
    if market_datetime is None:
        return None
    if market_datetime.tzinfo is None:
        market_datetime = market_datetime.replace(tzinfo=SEOUL_TIMEZONE)
    else:
        market_datetime = market_datetime.astimezone(SEOUL_TIMEZONE)
    if market_datetime.date() != current.date():
        return None
    if market_datetime > current:
        return None
    if not (authority.starts_at <= market_datetime < authority.ends_at):
        return None

    price = _positive_price(price_raw)
    try:
        sequence = int(sequence_raw or 0)
    except (TypeError, ValueError):
        return None
    if price is None or sequence <= 0 or not received_at:
        return None

    return ProductionCurrentPriceEvidence(
        canonical_stock_code=code,
        broker_code_identity=broker_identity,
        market_source=market_source,
        source_real_type=source_real_type,
        current_price=price,
        market_datetime=market_datetime.isoformat(),
        received_at=received_at,
        receive_sequence=sequence,
        connection_epoch=epoch,
        login_session_id=session_id,
        authority_window=authority.authority_window,
    )
