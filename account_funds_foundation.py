# -*- coding: utf-8 -*-
"""Memory-only account-funds projection and broker adapter contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import re
from typing import Any, Callable, Mapping, Protocol


UNREQUESTED = "UNREQUESTED"
LOADING = "LOADING"
READY = "READY"
FAILED = "FAILED"
DISCONNECTED = "DISCONNECTED"
STALE = "STALE"
ACCOUNT_AUTHENTICATION_REQUIRED = "ACCOUNT_AUTHENTICATION_REQUIRED"

ACCOUNT_FUNDS_STATUSES = frozenset(
    {UNREQUESTED, LOADING, READY, FAILED, DISCONNECTED, STALE}
)


def mask_account_id(account_id: object) -> str:
    """Return a stable UI-only account mask without exposing the full value."""
    clean = str(account_id or "").strip().replace("-", "")
    if not clean:
        return ""
    visible = clean[:4]
    return f"{visible}-****"


def normalize_money(value: object) -> int | None:
    """Normalize an optional integer money value without guessing invalid data."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a money value")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("money value must be an integer")
        return int(value)

    text = str(value).strip()
    if not text:
        return None
    compact = text.replace(",", "")
    if not re.fullmatch(r"[+-]?\d+", compact):
        raise ValueError("money value is not numeric")
    return int(compact)


def format_money(value: int | None) -> str:
    return "-" if value is None else f"{int(value):,}원"


@dataclass(frozen=True)
class AccountFundsSnapshot:
    account_id: str = ""
    account_display: str = ""
    status: str = UNREQUESTED
    fetched_at: str = ""
    deposit: int | None = None
    orderable_cash: int | None = None
    account_type: str = ""
    error: str = ""
    error_kind: str = ""


@dataclass(frozen=True)
class AccountFundsRequest:
    account_id: str
    request_id: int


AccountFundsCallback = Callable[[Mapping[str, Any]], None]


class AccountFundsAdapter(Protocol):
    """Future broker adapters normalize one account through this callback API."""

    def request_account_funds(
        self,
        account_id: str,
        *,
        request_id: int,
        callback: AccountFundsCallback,
    ) -> object:
        ...


class AccountFundsProjection:
    """Own the current process-local UI snapshot and reject stale responses."""

    def __init__(self) -> None:
        self._generation = 0
        self._snapshot = AccountFundsSnapshot()

    @property
    def snapshot(self) -> AccountFundsSnapshot:
        return self._snapshot

    def select_account(self, account_id: object, *, connected: bool) -> AccountFundsSnapshot:
        clean_account = str(account_id or "").strip()
        previous_account = self._snapshot.account_id
        self._generation += 1

        if not connected:
            status = DISCONNECTED
        elif not clean_account:
            status = UNREQUESTED
        elif previous_account and previous_account != clean_account:
            status = STALE
        elif self._snapshot.status == DISCONNECTED:
            status = UNREQUESTED
        else:
            status = self._snapshot.status

        if status in {READY, LOADING, FAILED} and clean_account == previous_account:
            return self._snapshot

        self._snapshot = AccountFundsSnapshot(
            account_id=clean_account,
            account_display=mask_account_id(clean_account),
            status=status,
        )
        return self._snapshot

    def begin_request(self) -> AccountFundsRequest | None:
        account_id = self._snapshot.account_id
        if not account_id or self._snapshot.status == DISCONNECTED:
            return None
        self._generation += 1
        request = AccountFundsRequest(account_id=account_id, request_id=self._generation)
        self._snapshot = replace(
            self._snapshot,
            status=LOADING,
            fetched_at="",
            deposit=None,
            orderable_cash=None,
            account_type="",
            error="",
        )
        return request

    def apply_result(
        self,
        request: AccountFundsRequest,
        payload: Mapping[str, Any] | None,
    ) -> bool:
        if (
            request.request_id != self._generation
            or request.account_id != self._snapshot.account_id
        ):
            return False

        result = dict(payload or {})
        payload_account = str(result.get("account_id") or request.account_id).strip()
        if payload_account != request.account_id:
            return False

        if result.get("ok") is not True:
            self._snapshot = AccountFundsSnapshot(
                account_id=request.account_id,
                account_display=mask_account_id(request.account_id),
                status=FAILED,
                fetched_at=_now_text(),
                error=str(result.get("error") or "account funds request failed"),
                error_kind=str(result.get("error_kind") or "").strip(),
            )
            return True

        try:
            deposit = normalize_money(result.get("deposit"))
            orderable_cash = normalize_money(result.get("orderable_cash"))
        except (TypeError, ValueError) as exc:
            self._snapshot = AccountFundsSnapshot(
                account_id=request.account_id,
                account_display=mask_account_id(request.account_id),
                status=FAILED,
                fetched_at=_now_text(),
                error=str(exc),
            )
            return True

        self._snapshot = AccountFundsSnapshot(
            account_id=request.account_id,
            account_display=mask_account_id(request.account_id),
            status=READY,
            fetched_at=str(result.get("fetched_at") or _now_text()),
            deposit=deposit,
            orderable_cash=orderable_cash,
            account_type=str(result.get("account_type") or "").strip(),
        )
        return True

    def fail_request(self, request: AccountFundsRequest, error: object) -> bool:
        return self.apply_result(request, {"ok": False, "error": str(error or "request failed")})


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")
