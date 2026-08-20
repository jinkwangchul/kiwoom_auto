# -*- coding: utf-8 -*-
"""Process-local market-data mode and per-minute canonical authority."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import timedelta

from candle_timeframe_aggregation import MARKET_BUCKET_ANCHOR, SEOUL_TIMEZONE, parse_market_datetime


TR_PRIMARY_SHADOWING = "TR_PRIMARY_SHADOWING"
REALTIME_ELIGIBLE = "REALTIME_ELIGIBLE"
REALTIME_PRIMARY = "REALTIME_PRIMARY"
TR_RECONCILING = "TR_RECONCILING"

REALTIME_AUTHORITY = "REALTIME"
TR_RECONCILIATION_AUTHORITY = "TR_RECONCILIATION"

TR_PRIMARY_REFRESH = "TR_PRIMARY_REFRESH"
REALTIME_PRIMARY_SKIP = "REALTIME_PRIMARY_SKIP"
REALTIME_RECONCILIATION = "REALTIME_RECONCILIATION"

NORMAL_TR_REFRESH = "NORMAL_TR_REFRESH"
REALTIME_RECONCILIATION_REQUEST = "REALTIME_RECONCILIATION"


@dataclass(frozen=True)
class MarketDataModeSnapshot:
    stock_code: str
    mode: str
    connection_epoch: int
    login_session_id: str
    last_full_match_minute: str = ""
    last_realtime_commit_minute: str = ""
    reconciliation_minute: str = ""
    last_reason: str = ""


class MarketDataAuthority:
    """Bounded, process-local ownership state. Nothing here is persisted."""

    MAX_AUTHORITY_MINUTES = 512

    def __init__(self) -> None:
        self._session_identity: tuple[int, str] = (0, "")
        self._states: dict[str, MarketDataModeSnapshot] = {}
        self._minute_authority: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._realtime_commits: OrderedDict[tuple[str, str], None] = OrderedDict()

    @property
    def session_identity(self) -> tuple[int, str]:
        return self._session_identity

    def reset(self, connection_epoch: int = 0, login_session_id: str = "") -> None:
        self._session_identity = (int(connection_epoch or 0), str(login_session_id or ""))
        self._states.clear()
        self._minute_authority.clear()
        self._realtime_commits.clear()

    def ensure_session(self, connection_epoch: int, login_session_id: str) -> bool:
        identity = (int(connection_epoch or 0), str(login_session_id or ""))
        if identity == self._session_identity:
            return False
        self.reset(*identity)
        return True

    def sync_targets(self, stock_codes: tuple[str, ...]) -> None:
        targets = {str(code or "").strip() for code in stock_codes if str(code or "").strip()}
        for code in tuple(self._states):
            if code not in targets:
                self._states.pop(code, None)
        for code in sorted(targets):
            self._states.setdefault(code, self._initial_state(code))

    def snapshot(self, stock_code: str) -> MarketDataModeSnapshot:
        code = str(stock_code or "").strip()
        return self._states.get(code, self._initial_state(code))

    def mode(self, stock_code: str) -> str:
        return self.snapshot(stock_code).mode

    def observe_comparison(
        self,
        stock_code: str,
        minute_key: str,
        *,
        status: str,
        price_match: bool,
        volume_compared: bool,
        volume_match: bool | None,
        eligible_context: bool,
    ) -> MarketDataModeSnapshot:
        code = str(stock_code or "").strip()
        current = self.snapshot(code)
        # Project policy: one current-session full-volume MATCH creates eligibility.
        full_match = bool(
            status == "MATCH"
            and price_match is True
            and volume_compared is True
            and volume_match is True
            and eligible_context
            and current.mode in {TR_PRIMARY_SHADOWING, REALTIME_ELIGIBLE}
        )
        if full_match:
            updated = replace(
                current,
                mode=REALTIME_ELIGIBLE,
                last_full_match_minute=str(minute_key or ""),
                last_reason="CURRENT_SESSION_FULL_VOLUME_MATCH",
            )
        elif status in {"MISMATCH", "PARTIAL_VOLUME_UNVERIFIED"}:
            updated = replace(
                current,
                mode=TR_PRIMARY_SHADOWING,
                last_full_match_minute="",
                last_reason=status,
            )
        else:
            updated = current
        self._states[code] = updated
        return updated

    def promote_at_cycle_boundary(
        self,
        stock_code: str,
        *,
        readiness_valid: bool,
        unresolved_pending: bool,
        refresh_inflight: bool,
    ) -> MarketDataModeSnapshot:
        code = str(stock_code or "").strip()
        current = self.snapshot(code)
        if current.mode != REALTIME_ELIGIBLE:
            return current
        if not readiness_valid or unresolved_pending or refresh_inflight:
            return current
        updated = replace(current, mode=REALTIME_PRIMARY, last_reason="PROMOTED_AT_OPERATION_CYCLE")
        self._states[code] = updated
        return updated

    def force_tr_primary(self, stock_code: str, reason: str) -> MarketDataModeSnapshot:
        code = str(stock_code or "").strip()
        current = self.snapshot(code)
        updated = replace(
            current,
            mode=TR_PRIMARY_SHADOWING,
            last_full_match_minute="",
            reconciliation_minute="",
            last_reason=str(reason or "TR_PRIMARY_REQUIRED"),
        )
        self._states[code] = updated
        return updated

    def begin_reconciliation(self, stock_code: str, minute_key: str, reason: str) -> bool:
        code = str(stock_code or "").strip()
        minute = str(minute_key or "").strip()
        if not code or not minute:
            return False
        if not self.claim_authority(code, minute, TR_RECONCILIATION_AUTHORITY):
            return self.authority(code, minute) == TR_RECONCILIATION_AUTHORITY
        current = self.snapshot(code)
        self._states[code] = replace(
            current,
            mode=TR_RECONCILING,
            last_full_match_minute="",
            reconciliation_minute=minute,
            last_reason=str(reason or "TR_RECONCILIATION_REQUIRED"),
        )
        return True

    def finish_reconciliation(self, stock_code: str, minute_key: str, *, repaired: bool) -> None:
        code = str(stock_code or "").strip()
        current = self.snapshot(code)
        if current.reconciliation_minute != str(minute_key or "").strip():
            return
        self._states[code] = replace(
            current,
            mode=TR_PRIMARY_SHADOWING if repaired else TR_RECONCILING,
            reconciliation_minute="" if repaired else current.reconciliation_minute,
            last_reason="RECONCILIATION_COMPLETED" if repaired else "RECONCILIATION_INCOMPLETE",
        )

    def claim_authority(self, stock_code: str, minute_key: str, source: str) -> bool:
        key = (str(stock_code or "").strip(), str(minute_key or "").strip())
        if not all(key) or source not in {REALTIME_AUTHORITY, TR_RECONCILIATION_AUTHORITY}:
            return False
        existing = self._minute_authority.get(key)
        if existing is not None:
            return existing == source
        self._minute_authority[key] = source
        self._trim(self._minute_authority)
        return True

    def authority(self, stock_code: str, minute_key: str) -> str:
        return self._minute_authority.get(
            (str(stock_code or "").strip(), str(minute_key or "").strip()),
            "",
        )

    def replace_with_reconciliation_authority(self, stock_code: str, minute_key: str) -> None:
        key = (str(stock_code or "").strip(), str(minute_key or "").strip())
        if all(key):
            self._minute_authority[key] = TR_RECONCILIATION_AUTHORITY
            self._minute_authority.move_to_end(key)
            self._trim(self._minute_authority)

    def mark_realtime_committed(self, stock_code: str, minute_key: str) -> None:
        code = str(stock_code or "").strip()
        minute = str(minute_key or "").strip()
        key = (code, minute)
        self._realtime_commits[key] = None
        self._trim(self._realtime_commits)
        current = self.snapshot(code)
        self._states[code] = replace(
            current,
            last_realtime_commit_minute=minute,
            last_reason="REALTIME_CANONICAL_COMMITTED",
        )

    def realtime_committed(self, stock_code: str, minute_key: str) -> bool:
        return (str(stock_code or "").strip(), str(minute_key or "").strip()) in self._realtime_commits

    @staticmethod
    def expected_completed_minute(cycle_minute_key: str) -> str:
        parsed = parse_market_datetime(cycle_minute_key)
        if parsed is None:
            return ""
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SEOUL_TIMEZONE)
        expected = parsed.replace(second=0, microsecond=0) - timedelta(minutes=1)
        if expected.date() != parsed.date() or expected.time() < MARKET_BUCKET_ANCHOR:
            return ""
        return expected.strftime("%Y-%m-%d %H:%M")

    def _initial_state(self, stock_code: str) -> MarketDataModeSnapshot:
        return MarketDataModeSnapshot(
            stock_code=stock_code,
            mode=TR_PRIMARY_SHADOWING,
            connection_epoch=self._session_identity[0],
            login_session_id=self._session_identity[1],
        )

    def _trim(self, mapping: OrderedDict) -> None:
        while len(mapping) > self.MAX_AUTHORITY_MINUTES:
            mapping.popitem(last=False)
