# -*- coding: utf-8 -*-
"""Process-local Production Recovery state and shared fail-closed gate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import threading
from typing import Any, Iterable, Mapping

from production_recovery_contract import (
    ACCOUNT_COLLECTING,
    ACCOUNT_COMPLETED,
    ACCOUNT_FAILED,
    ACCOUNT_NOT_STARTED,
    ACCOUNT_RECONCILING,
    ACCOUNT_REVIEW_REQUIRED,
    STOCK_FAILED,
    STOCK_NOT_APPLICABLE,
    STOCK_PENDING,
    STOCK_RESTORED,
    STOCK_REVIEW_REQUIRED,
    BrokerAccountSnapshot,
    RecoveryGateDecision,
    RecoverySessionIdentity,
    StockRecoveryResult,
    decide_account_recovery,
    decide_stock_recovery,
)


RECOVERY_NOT_STARTED = "RECOVERY_NOT_STARTED"
RECOVERY_IN_PROGRESS = "RECOVERY_IN_PROGRESS"
RECOVERY_ACCOUNT_REVIEW_REQUIRED = "RECOVERY_ACCOUNT_REVIEW_REQUIRED"
RECOVERY_ACCOUNT_FAILED = "RECOVERY_ACCOUNT_FAILED"
RECOVERY_STOCK_PENDING = "RECOVERY_STOCK_PENDING"
RECOVERY_STOCK_REVIEW_REQUIRED = "RECOVERY_STOCK_REVIEW_REQUIRED"
RECOVERY_STOCK_FAILED = "RECOVERY_STOCK_FAILED"
RECOVERY_IDENTITY_MISMATCH = "RECOVERY_IDENTITY_MISMATCH"
RECOVERY_STALE_SESSION = "RECOVERY_STALE_SESSION"
RECOVERY_CONTEXT_MISSING = "RECOVERY_CONTEXT_MISSING"
RECOVERY_COMPLETED = "RECOVERY_COMPLETED"

_ACCOUNT_STATUSES = {
    ACCOUNT_NOT_STARTED,
    ACCOUNT_COLLECTING,
    ACCOUNT_RECONCILING,
    ACCOUNT_REVIEW_REQUIRED,
    ACCOUNT_COMPLETED,
    ACCOUNT_FAILED,
}
_STOCK_STATUSES = {
    STOCK_PENDING,
    STOCK_RESTORED,
    STOCK_REVIEW_REQUIRED,
    STOCK_FAILED,
    STOCK_NOT_APPLICABLE,
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class RecoveryStockContext:
    stock_code: str
    stock_status: str
    review_required: bool
    reason_codes: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True)
class RecoveryAccountContext:
    identity: RecoverySessionIdentity
    account_status: str
    created_at: str
    updated_at: str
    stocks: tuple[RecoveryStockContext, ...]


class ProductionRecoveryStateRegistry:
    """The only mutation boundary for process-local Recovery state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._context: RecoveryAccountContext | None = None

    def snapshot(self) -> RecoveryAccountContext | None:
        with self._lock:
            return self._context

    def begin_recovery(
        self,
        identity: RecoverySessionIdentity,
    ) -> dict[str, Any]:
        if not isinstance(identity, RecoverySessionIdentity):
            return self._result(False, "RECOVERY_IDENTITY_REQUIRED")
        now = _now_text()
        with self._lock:
            if (
                self._context is not None
                and self._context.identity == identity
            ):
                return self._result(True, "UNCHANGED", self._context)
            self._context = RecoveryAccountContext(
                identity=identity,
                account_status=ACCOUNT_NOT_STARTED,
                created_at=now,
                updated_at=now,
                stocks=(),
            )
            return self._result(True, "RECOVERY_STARTED", self._context)

    def mark_collecting(
        self,
        identity: RecoverySessionIdentity,
    ) -> dict[str, Any]:
        return self._set_account_status(
            identity,
            ACCOUNT_COLLECTING,
            allowed_from={ACCOUNT_NOT_STARTED, ACCOUNT_COLLECTING},
        )

    def mark_reconciling(
        self,
        identity: RecoverySessionIdentity,
    ) -> dict[str, Any]:
        return self._set_account_status(
            identity,
            ACCOUNT_RECONCILING,
            allowed_from={ACCOUNT_COLLECTING, ACCOUNT_RECONCILING},
        )

    def set_stock_result(
        self,
        identity: RecoverySessionIdentity,
        *,
        stock_code: Any,
        stock_status: Any,
        review_required: bool = False,
        reason_codes: Iterable[Any] = (),
    ) -> dict[str, Any]:
        code = _text(stock_code).upper()
        status = _text(stock_status).upper()
        reasons = tuple(dict.fromkeys(_text(item) for item in reason_codes if _text(item)))
        if not code:
            return self._result(False, "STOCK_CODE_REQUIRED")
        if status not in _STOCK_STATUSES:
            return self._result(False, "INVALID_STOCK_STATUS")
        with self._lock:
            mismatch = self._identity_mismatch(identity)
            if mismatch:
                return self._result(False, mismatch, self._context)
            assert self._context is not None
            existing = {item.stock_code: item for item in self._context.stocks}
            previous = existing.get(code)
            if (
                previous is not None
                and previous.stock_status == STOCK_REVIEW_REQUIRED
                and status == STOCK_RESTORED
            ):
                return self._result(
                    False,
                    "REVIEW_REQUIRED_REQUIRES_NEW_RECOVERY",
                    self._context,
                )
            required = bool(review_required or status == STOCK_REVIEW_REQUIRED)
            if (
                previous is not None
                and previous.stock_status == status
                and previous.review_required == required
                and previous.reason_codes == reasons
            ):
                return self._result(True, "UNCHANGED", self._context)
            replacement = RecoveryStockContext(
                stock_code=code,
                stock_status=status,
                review_required=required,
                reason_codes=reasons,
                updated_at=_now_text(),
            )
            existing[code] = replacement
            stocks = tuple(existing[key] for key in sorted(existing))
            account_status = self._context.account_status
            if required and account_status not in {ACCOUNT_FAILED, ACCOUNT_COMPLETED}:
                account_status = ACCOUNT_REVIEW_REQUIRED
            self._context = replace(
                self._context,
                account_status=account_status,
                stocks=stocks,
                updated_at=_now_text(),
            )
            return self._result(True, "STOCK_RESULT_UPDATED", self._context)

    def complete_account(
        self,
        identity: RecoverySessionIdentity,
    ) -> dict[str, Any]:
        with self._lock:
            mismatch = self._identity_mismatch(identity)
            if mismatch:
                return self._result(False, mismatch, self._context)
            assert self._context is not None
            status = (
                ACCOUNT_REVIEW_REQUIRED
                if any(item.review_required for item in self._context.stocks)
                else ACCOUNT_COMPLETED
            )
            if self._context.account_status == status:
                return self._result(True, "UNCHANGED", self._context)
            self._context = replace(
                self._context,
                account_status=status,
                updated_at=_now_text(),
            )
            return self._result(True, "ACCOUNT_COMPLETED", self._context)

    def fail_account(
        self,
        identity: RecoverySessionIdentity,
    ) -> dict[str, Any]:
        return self._set_account_status(
            identity,
            ACCOUNT_FAILED,
            allowed_from=_ACCOUNT_STATUSES,
        )

    def invalidate(self, reason: Any = "") -> dict[str, Any]:
        with self._lock:
            changed = self._context is not None
            self._context = None
        return {
            "ok": True,
            "status": "INVALIDATED" if changed else "UNCHANGED",
            "reason": _text(reason),
            "context": None,
        }

    def _set_account_status(
        self,
        identity: RecoverySessionIdentity,
        status: str,
        *,
        allowed_from: set[str],
    ) -> dict[str, Any]:
        with self._lock:
            mismatch = self._identity_mismatch(identity)
            if mismatch:
                return self._result(False, mismatch, self._context)
            assert self._context is not None
            if self._context.account_status not in allowed_from:
                return self._result(False, "INVALID_ACCOUNT_TRANSITION", self._context)
            if self._context.account_status == status:
                return self._result(True, "UNCHANGED", self._context)
            self._context = replace(
                self._context,
                account_status=status,
                updated_at=_now_text(),
            )
            return self._result(True, "ACCOUNT_STATUS_UPDATED", self._context)

    def _identity_mismatch(self, identity: Any) -> str:
        if self._context is None:
            return RECOVERY_NOT_STARTED
        if not isinstance(identity, RecoverySessionIdentity):
            return RECOVERY_CONTEXT_MISSING
        current = self._context.identity
        if current.recovery_session_id != identity.recovery_session_id:
            return RECOVERY_STALE_SESSION
        if (
            current.login_session_id != identity.login_session_id
            or current.account_no != identity.account_no
            or current.trading_day != identity.trading_day
        ):
            return RECOVERY_IDENTITY_MISMATCH
        return ""

    @staticmethod
    def _result(
        ok: bool,
        status: str,
        context: RecoveryAccountContext | None = None,
    ) -> dict[str, Any]:
        return {"ok": ok, "status": status, "context": context}


production_recovery_registry = ProductionRecoveryStateRegistry()


def recovery_account_allows_isolated_stock_operation(
    context: RecoveryAccountContext | None,
) -> bool:
    """Return whether account reconciliation reached a stock-addressable state."""
    return bool(
        context is not None
        and context.account_status in {ACCOUNT_COMPLETED, ACCOUNT_REVIEW_REQUIRED}
    )


def recovery_stock_is_review_required(
    stock_code: Any,
    *,
    registry: ProductionRecoveryStateRegistry = production_recovery_registry,
) -> bool:
    code = _text(stock_code).upper()
    if not code:
        return False
    context = registry.snapshot()
    if context is None:
        return False
    for stock in context.stocks:
        if stock.stock_code != code:
            continue
        return bool(
            stock.review_required or stock.stock_status == STOCK_REVIEW_REQUIRED
        )
    return False


def reconcile_production_recovery_snapshot(
    *,
    identity: RecoverySessionIdentity,
    snapshot: BrokerAccountSnapshot,
    stock_runtime: Iterable[
        tuple[str, Mapping[str, Any] | None]
    ],
    runtime_orders: Iterable[Mapping[str, Any]],
    registry: ProductionRecoveryStateRegistry = production_recovery_registry,
) -> dict[str, Any]:
    """Reconcile one immutable Broker snapshot into the existing Registry."""
    if not isinstance(identity, RecoverySessionIdentity):
        return {"ok": False, "status": "RECOVERY_IDENTITY_REQUIRED", "stock_results": ()}
    if not isinstance(snapshot, BrokerAccountSnapshot):
        return {"ok": False, "status": "BROKER_SNAPSHOT_REQUIRED", "stock_results": ()}

    collecting = registry.mark_reconciling(identity)
    if collecting.get("ok") is not True:
        return {
            "ok": False,
            "status": str(collecting.get("status") or "RECOVERY_RECONCILE_BLOCKED"),
            "stock_results": (),
        }

    orders = tuple(item for item in runtime_orders if isinstance(item, Mapping))
    results: list[StockRecoveryResult] = []
    seen: set[str] = set()
    for raw_code, runtime_position in stock_runtime:
        code = _text(raw_code).upper()
        if not code or code in seen:
            continue
        seen.add(code)
        result = decide_stock_recovery(
            snapshot=snapshot,
            stock_code=code,
            runtime_position=runtime_position,
            runtime_orders=orders,
        )
        results.append(result)
        updated = registry.set_stock_result(
            identity,
            stock_code=code,
            stock_status=result.status,
            review_required=result.review_required,
            reason_codes=result.reason_codes,
        )
        if updated.get("ok") is not True:
            registry.fail_account(identity)
            return {
                "ok": False,
                "status": str(updated.get("status") or "RECOVERY_STOCK_UPDATE_FAILED"),
                "stock_results": tuple(results),
            }

    account_result = decide_account_recovery(
        identity=identity,
        snapshot=snapshot,
        stock_results=results,
    )
    if account_result.status == ACCOUNT_FAILED:
        registry.fail_account(identity)
    else:
        registry.complete_account(identity)
    return {
        "ok": account_result.status == ACCOUNT_COMPLETED,
        "status": account_result.status,
        "account_result": account_result,
        "stock_results": tuple(results),
    }


def check_production_recovery_gate(
    *,
    login_session_id: Any,
    account_no: Any,
    trading_day: Any,
    stock_code: Any,
    recovery_session_id: Any = "",
    caller_name: Any = "",
    registry: ProductionRecoveryStateRegistry = production_recovery_registry,
) -> RecoveryGateDecision:
    """Return one shared fail-closed decision without mutating the registry."""
    login = _text(login_session_id)
    account = _text(account_no)
    day = _text(trading_day)
    code = _text(stock_code).upper()
    requested_session = _text(recovery_session_id)
    caller = _text(caller_name)
    evidence = tuple(item for item in (f"caller={caller}" if caller else "",) if item)
    if not login or not account or not day or not code:
        return RecoveryGateDecision(
            False,
            RECOVERY_CONTEXT_MISSING,
            "",
            "",
            requested_session,
            evidence,
        )
    try:
        context = registry.snapshot()
    except Exception as exc:
        return RecoveryGateDecision(
            False,
            RECOVERY_CONTEXT_MISSING,
            "",
            "",
            requested_session,
            evidence + (f"registry_error={exc}",),
        )
    if context is None:
        return RecoveryGateDecision(
            False,
            RECOVERY_NOT_STARTED,
            ACCOUNT_NOT_STARTED,
            STOCK_PENDING,
            requested_session,
            evidence,
        )
    identity = context.identity
    if requested_session and requested_session != identity.recovery_session_id:
        return RecoveryGateDecision(
            False,
            RECOVERY_STALE_SESSION,
            context.account_status,
            STOCK_PENDING,
            identity.recovery_session_id,
            evidence,
        )
    if identity.trading_day != day:
        return RecoveryGateDecision(
            False,
            RECOVERY_STALE_SESSION,
            context.account_status,
            STOCK_PENDING,
            identity.recovery_session_id,
            evidence,
        )
    if identity.login_session_id != login or identity.account_no != account:
        return RecoveryGateDecision(
            False,
            RECOVERY_IDENTITY_MISMATCH,
            context.account_status,
            STOCK_PENDING,
            identity.recovery_session_id,
            evidence,
        )
    account_reasons = {
        ACCOUNT_NOT_STARTED: RECOVERY_NOT_STARTED,
        ACCOUNT_COLLECTING: RECOVERY_IN_PROGRESS,
        ACCOUNT_RECONCILING: RECOVERY_IN_PROGRESS,
        ACCOUNT_REVIEW_REQUIRED: RECOVERY_ACCOUNT_REVIEW_REQUIRED,
        ACCOUNT_FAILED: RECOVERY_ACCOUNT_FAILED,
    }
    if not recovery_account_allows_isolated_stock_operation(context):
        return RecoveryGateDecision(
            False,
            account_reasons.get(context.account_status, RECOVERY_CONTEXT_MISSING),
            context.account_status,
            STOCK_PENDING,
            identity.recovery_session_id,
            evidence,
        )
    stocks = {item.stock_code: item for item in context.stocks}
    stock = stocks.get(code)
    if stock is None:
        return RecoveryGateDecision(
            False,
            RECOVERY_STOCK_PENDING,
            context.account_status,
            STOCK_PENDING,
            identity.recovery_session_id,
            evidence,
        )
    stock_reasons = {
        STOCK_PENDING: RECOVERY_STOCK_PENDING,
        STOCK_REVIEW_REQUIRED: RECOVERY_STOCK_REVIEW_REQUIRED,
        STOCK_FAILED: RECOVERY_STOCK_FAILED,
        STOCK_NOT_APPLICABLE: RECOVERY_STOCK_FAILED,
    }
    if stock.stock_status != STOCK_RESTORED or stock.review_required:
        return RecoveryGateDecision(
            False,
            stock_reasons.get(stock.stock_status, RECOVERY_STOCK_FAILED),
            context.account_status,
            stock.stock_status,
            identity.recovery_session_id,
            evidence + stock.reason_codes,
        )
    return RecoveryGateDecision(
        True,
        RECOVERY_COMPLETED,
        context.account_status,
        stock.stock_status,
        identity.recovery_session_id,
        evidence,
    )
