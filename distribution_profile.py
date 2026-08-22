"""Fail-closed application distribution profile helpers."""

from __future__ import annotations

import os


DISTRIBUTION_PROFILE_ENV = "KIWOOM_AUTO_DISTRIBUTION_PROFILE"
DISTRIBUTION_PROFILE_DEVELOPER = "developer"
DISTRIBUTION_PROFILE_BETA = "beta"
DISTRIBUTION_PROFILE_PRODUCTION = "production"
_VALID_DISTRIBUTION_PROFILES = frozenset(
    {
        DISTRIBUTION_PROFILE_DEVELOPER,
        DISTRIBUTION_PROFILE_BETA,
        DISTRIBUTION_PROFILE_PRODUCTION,
    }
)


def distribution_profile() -> str:
    value = str(os.environ.get(DISTRIBUTION_PROFILE_ENV, "") or "").strip().lower()
    if value not in _VALID_DISTRIBUTION_PROFILES:
        return DISTRIBUTION_PROFILE_PRODUCTION
    return value


def group_packing_enabled() -> bool:
    return distribution_profile() == DISTRIBUTION_PROFILE_DEVELOPER
