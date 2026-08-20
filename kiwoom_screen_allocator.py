# -*- coding: utf-8 -*-
"""Project-local Kiwoom screen namespace and process-local lease allocator."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SCREEN_REGISTRY_PATH = PROJECT_ROOT / "screen_registry.json"

ACCOUNT_TR = "ACCOUNT_TR"
CONDITION = "CONDITION"
MARKET_TR = "MARKET_TR"
REALTIME = "REALTIME"
ORDER = "ORDER"
ADMIN_GUI = "ADMIN_GUI"

SCREEN_POOL_EXHAUSTED = "SCREEN_POOL_EXHAUSTED"
SCREEN_OUT_OF_RANGE = "SCREEN_OUT_OF_RANGE"
SCREEN_ALREADY_LEASED = "SCREEN_ALREADY_LEASED"

PURPOSE_RANGE_KEYS: dict[str, str] = {
    ACCOUNT_TR: "1000-1999",
    CONDITION: "2000-2999",
    MARKET_TR: "3000-3999",
    REALTIME: "4000-4999",
    ORDER: "5000-5999",
    ADMIN_GUI: "9000-9999",
}


@dataclass(frozen=True)
class ScreenRange:
    start: int
    end: int

    def contains(self, screen_no: str) -> bool:
        if len(str(screen_no)) != 4 or not str(screen_no).isdigit():
            return False
        value = int(str(screen_no))
        return self.start <= value <= self.end

    def first(self) -> str:
        return f"{self.start:04d}"

    def iter_screens(self):
        for value in range(self.start, self.end + 1):
            yield f"{value:04d}"


@dataclass(frozen=True)
class ScreenLease:
    purpose: str
    screen_no: str
    owner: str


class ScreenAllocationError(Exception):
    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind


def _parse_range_key(key: str) -> ScreenRange:
    start_text, end_text = str(key).split("-", 1)
    return ScreenRange(start=int(start_text), end=int(end_text))


def load_project_screen_ranges(
    registry_path: str | Path = SCREEN_REGISTRY_PATH,
) -> dict[str, ScreenRange]:
    data = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    ranges = data.get("ranges")
    if not isinstance(ranges, dict):
        raise ValueError("screen_registry.json ranges must be an object")
    resolved: dict[str, ScreenRange] = {}
    for purpose, range_key in PURPOSE_RANGE_KEYS.items():
        if range_key not in ranges:
            raise ValueError(f"screen_registry.json is missing {range_key}")
        resolved[purpose] = _parse_range_key(range_key)
    return resolved


def project_order_default_screen_no(
    registry_path: str | Path = SCREEN_REGISTRY_PATH,
) -> str:
    return load_project_screen_ranges(registry_path)[ORDER].first()


class KiwoomScreenAllocator:
    """Process-local lease allocator over the project screen namespace."""

    def __init__(
        self,
        registry_path: str | Path = SCREEN_REGISTRY_PATH,
        *,
        ranges: dict[str, ScreenRange] | None = None,
    ) -> None:
        self._ranges = dict(ranges or load_project_screen_ranges(registry_path))
        self._leases_by_screen: dict[str, ScreenLease] = {}
        self._leases_by_owner: dict[tuple[str, str], ScreenLease] = {}

    @property
    def ranges(self) -> dict[str, ScreenRange]:
        return dict(self._ranges)

    def default_order_screen_no(self) -> str:
        return self._ranges[ORDER].first()

    def claim(
        self,
        purpose: str,
        owner: str,
        *,
        requested_screen_no: str | None = None,
    ) -> ScreenLease:
        clean_purpose = str(purpose or "").strip().upper()
        clean_owner = str(owner or "").strip()
        if clean_purpose not in self._ranges:
            raise ScreenAllocationError(SCREEN_OUT_OF_RANGE, "unknown screen purpose")
        if not clean_owner:
            raise ScreenAllocationError(SCREEN_OUT_OF_RANGE, "screen owner is required")

        existing = self._leases_by_owner.get((clean_purpose, clean_owner))
        if existing is not None:
            return existing

        screen_range = self._ranges[clean_purpose]
        requested = str(requested_screen_no or "").strip()
        if requested:
            if not screen_range.contains(requested):
                raise ScreenAllocationError(
                    SCREEN_OUT_OF_RANGE,
                    f"screen_no {requested} is outside {clean_purpose}",
                )
            if requested in self._leases_by_screen:
                raise ScreenAllocationError(
                    SCREEN_ALREADY_LEASED,
                    f"screen_no {requested} is already leased",
                )
            return self._record_lease(clean_purpose, clean_owner, requested)

        for screen_no in screen_range.iter_screens():
            if screen_no not in self._leases_by_screen:
                return self._record_lease(clean_purpose, clean_owner, screen_no)
        raise ScreenAllocationError(
            SCREEN_POOL_EXHAUSTED,
            f"{clean_purpose} screen pool is exhausted",
        )

    def release(self, owner: str, screen_no: str) -> None:
        clean_owner = str(owner or "").strip()
        clean_screen = str(screen_no or "").strip()
        lease = self._leases_by_screen.get(clean_screen)
        if lease is None or lease.owner != clean_owner:
            return
        self._leases_by_screen.pop(clean_screen, None)
        self._leases_by_owner.pop((lease.purpose, lease.owner), None)

    def is_leased(self, screen_no: str) -> bool:
        return str(screen_no or "").strip() in self._leases_by_screen

    def _record_lease(
        self,
        purpose: str,
        owner: str,
        screen_no: str,
    ) -> ScreenLease:
        lease = ScreenLease(purpose=purpose, owner=owner, screen_no=screen_no)
        self._leases_by_screen[screen_no] = lease
        self._leases_by_owner[(purpose, owner)] = lease
        return lease
