# -*- coding: utf-8 -*-
"""Thin OPW00001 adapter for the memory-only account-funds projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from account_funds_foundation import normalize_money


AccountFundsCallback = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class _QueuedRequest:
    account_id: str
    request_id: int
    callback: AccountFundsCallback


class KiwoomAccountFundsAdapter:
    """Normalize OPW00001 and coalesce repeated requests per account."""

    ACCOUNT_TYPE_DISPLAY = {
        "REAL": "실계좌",
        "SIMULATION": "모의투자",
    }

    def __init__(self, api: object) -> None:
        self._api = api
        self._active_account = ""
        self._inflight: dict[str, _QueuedRequest] = {}
        self._queued: dict[str, _QueuedRequest] = {}

    def set_active_account(self, account_id: object) -> None:
        self._active_account = str(account_id or "").strip()
        self._queued = {
            account: request
            for account, request in self._queued.items()
            if account == self._active_account
        }

    def request_account_funds(
        self,
        account_id: str,
        *,
        request_id: int,
        callback: AccountFundsCallback,
    ) -> dict[str, Any]:
        request = _QueuedRequest(
            account_id=str(account_id or "").strip(),
            request_id=int(request_id),
            callback=callback,
        )
        if not request.account_id:
            callback({"ok": False, "error": "account is required"})
            return {"ok": False, "status": "ACCOUNT_UNAVAILABLE"}
        if request.account_id in self._inflight:
            self._queued[request.account_id] = request
            return {
                "ok": True,
                "status": "COALESCED",
                "account_id": request.account_id,
                "request_id": request.request_id,
            }
        return self._start(request)

    def _start(self, request: _QueuedRequest) -> dict[str, Any]:
        requester = getattr(self._api, "request_account_funds_snapshot", None)
        if not callable(requester):
            request.callback({"ok": False, "error": "Kiwoom account funds API is unavailable"})
            return {"ok": False, "status": "ADAPTER_UNAVAILABLE"}

        self._inflight[request.account_id] = request
        try:
            result = requester(
                request.account_id,
                request_id=request.request_id,
                callback=lambda payload, current=request: self._finish(current, payload),
            )
        except Exception as exc:
            self._inflight.pop(request.account_id, None)
            request.callback({"ok": False, "error": str(exc)})
            self._start_queued_if_current(request.account_id)
            return {"ok": False, "status": "REQUEST_FAILED"}
        return dict(result) if isinstance(result, Mapping) else {"ok": True, "status": "REQUESTED"}

    def _finish(self, request: _QueuedRequest, payload: object) -> None:
        current = self._inflight.get(request.account_id)
        if current != request:
            return
        self._inflight.pop(request.account_id, None)
        normalized = self._normalize_payload(request, payload)
        request.callback(normalized)
        self._start_queued_if_current(request.account_id)

    def _start_queued_if_current(self, account_id: str) -> None:
        queued = self._queued.pop(account_id, None)
        if queued is None or account_id != self._active_account:
            return
        self._start(queued)

    def _normalize_payload(
        self,
        request: _QueuedRequest,
        payload: object,
    ) -> dict[str, Any]:
        result = dict(payload) if isinstance(payload, Mapping) else {}
        if result.get("ok") is not True:
            return {
                "ok": False,
                "account_id": request.account_id,
                "request_id": request.request_id,
                "error": str(result.get("error") or "account funds request failed"),
            }
        if str(result.get("account_id") or "").strip() != request.account_id:
            return {
                "ok": False,
                "account_id": request.account_id,
                "request_id": request.request_id,
                "error": "account funds response account mismatch",
            }
        try:
            deposit = normalize_money(result.get("raw_deposit"))
            orderable_cash = normalize_money(result.get("raw_orderable_cash"))
            if deposit is None or orderable_cash is None:
                raise ValueError("required account funds field is empty")
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "account_id": request.account_id,
                "request_id": request.request_id,
                "error": str(exc),
            }

        account_type = self.ACCOUNT_TYPE_DISPLAY.get(
            str(result.get("account_type") or "").strip().upper(),
            "",
        )
        return {
            "ok": True,
            "account_id": request.account_id,
            "request_id": request.request_id,
            "deposit": deposit,
            "orderable_cash": orderable_cash,
            "account_type": account_type,
            "fetched_at": str(result.get("fetched_at") or ""),
        }
