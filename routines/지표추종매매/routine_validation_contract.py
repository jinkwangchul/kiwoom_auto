# -*- coding: utf-8 -*-
"""Pure data contracts for indicator-follow validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


def _canonical_rules_json(rules: Mapping[str, Any]) -> str:
    if not isinstance(rules, Mapping):
        raise TypeError("rules must be a mapping")
    return json.dumps(
        rules,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class ValidationStockRef:
    """Selection-only stock identity for Validation."""

    code: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", str(self.code or "").strip())
        object.__setattr__(self, "name", str(self.name or "").strip())


@dataclass(frozen=True, slots=True, init=False)
class ValidationSettingsSnapshot:
    """Immutable canonical copy of the rules currently edited in the UI."""

    canonical_json: str
    rules_hash: str

    def __init__(self, rules: Mapping[str, Any]) -> None:
        canonical_json = _canonical_rules_json(rules)
        rules_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        object.__setattr__(self, "canonical_json", canonical_json)
        object.__setattr__(self, "rules_hash", rules_hash)

    @classmethod
    def from_rules(cls, rules: Mapping[str, Any]) -> "ValidationSettingsSnapshot":
        return cls(rules)

    def to_dict(self) -> dict[str, Any]:
        rules = json.loads(self.canonical_json)
        if not isinstance(rules, dict):
            raise ValueError("canonical rules must decode to an object")
        return rules


@dataclass(frozen=True, slots=True)
class ValidationRequest:
    stock: ValidationStockRef
    settings_snapshot: ValidationSettingsSnapshot
    timeframe_minutes: int

    def __post_init__(self) -> None:
        if not isinstance(self.stock, ValidationStockRef):
            raise TypeError("stock must be ValidationStockRef")
        if not isinstance(self.settings_snapshot, ValidationSettingsSnapshot):
            raise TypeError("settings_snapshot must be ValidationSettingsSnapshot")
        if (
            isinstance(self.timeframe_minutes, bool)
            or not isinstance(self.timeframe_minutes, int)
            or self.timeframe_minutes <= 0
        ):
            raise ValueError("timeframe_minutes must be a positive integer")


@dataclass(frozen=True, slots=True)
class ValidationAvailability:
    allowed: bool
    reason: str | None = None
