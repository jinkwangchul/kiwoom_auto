"""Canonical mutation boundary for Stock assignment ownership episodes."""

from __future__ import annotations

from datetime import datetime

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
    EpisodeMutationResult,
)


def transition_assignment_episode(
    repository: CanonicalAssignmentEpisodeRepository,
    stock_code: str,
    target: AssignmentEpisodeTarget,
    *,
    changed_at: str | datetime,
    start_reason: str,
    end_reason: str,
    source: str,
) -> EpisodeMutationResult:
    """Close the current episode and open its successor in one file commit."""

    return repository.transition_episode(
        stock_code,
        target,
        changed_at=changed_at,
        start_reason=start_reason,
        end_reason=end_reason,
        source=source,
    )
