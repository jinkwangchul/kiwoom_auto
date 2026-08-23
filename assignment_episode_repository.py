"""Canonical, append-preserving Stock assignment episode repository."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable
from uuid import UUID, uuid4

from stock_repository import is_valid_stock_code, normalize_stock_code


EPISODE_SCHEMA_VERSION = "1.0"
ASSIGNED = "ASSIGNED"
UNASSIGNED = "UNASSIGNED"
OWNERSHIP_KINDS = frozenset({ASSIGNED, UNASSIGNED})
PROJECT_ROOT = Path(__file__).resolve().parent

_WRITE_LOCK = threading.RLock()


def _nullable_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("episode timestamp is required")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("episode timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("episode timestamp must include a UTC offset")
    return parsed.isoformat(timespec="seconds")


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class AssignmentEpisodeTarget:
    ownership_kind: str
    instance_id: str | None = None
    group_id: str | None = None
    definition_id: str | None = None
    instance_name_snapshot: str | None = None
    group_name_snapshot: str | None = None

    @classmethod
    def assigned(
        cls,
        *,
        instance_id: str,
        group_id: str | None,
        definition_id: str | None,
        instance_name_snapshot: str | None,
        group_name_snapshot: str | None,
    ) -> "AssignmentEpisodeTarget":
        return cls(
            ownership_kind=ASSIGNED,
            instance_id=_nullable_text(instance_id),
            group_id=_nullable_text(group_id),
            definition_id=_nullable_text(definition_id),
            instance_name_snapshot=_nullable_text(instance_name_snapshot),
            group_name_snapshot=_nullable_text(group_name_snapshot),
        ).validated()

    @classmethod
    def unassigned(cls) -> "AssignmentEpisodeTarget":
        return cls(ownership_kind=UNASSIGNED)

    def validated(self) -> "AssignmentEpisodeTarget":
        kind = str(self.ownership_kind or "").strip().upper()
        if kind not in OWNERSHIP_KINDS:
            raise ValueError("unsupported ownership_kind")
        if kind == UNASSIGNED:
            if any(
                _nullable_text(value)
                for value in (
                    self.instance_id,
                    self.group_id,
                    self.definition_id,
                    self.instance_name_snapshot,
                    self.group_name_snapshot,
                )
            ):
                raise ValueError("UNASSIGNED episode cannot carry assignment identity")
            return AssignmentEpisodeTarget(ownership_kind=UNASSIGNED)
        instance_id = _nullable_text(self.instance_id)
        if not instance_id:
            raise ValueError("ASSIGNED episode requires instance_id")
        return AssignmentEpisodeTarget(
            ownership_kind=ASSIGNED,
            instance_id=instance_id,
            group_id=_nullable_text(self.group_id),
            definition_id=_nullable_text(self.definition_id),
            instance_name_snapshot=_nullable_text(self.instance_name_snapshot),
            group_name_snapshot=_nullable_text(self.group_name_snapshot),
        )

    def identity_key(self) -> tuple[str, str, str, str]:
        target = self.validated()
        return (
            target.ownership_kind,
            target.instance_id or "",
            target.group_id or "",
            target.definition_id or "",
        )


@dataclass(frozen=True)
class AssignmentEpisode:
    episode_id: str
    stock_code: str
    ownership_kind: str
    instance_id: str | None
    group_id: str | None
    definition_id: str | None
    instance_name_snapshot: str | None
    group_name_snapshot: str | None
    started_at: str
    ended_at: str | None
    sequence: int
    start_reason: str
    end_reason: str | None
    source: str
    end_source: str | None = None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def target(self) -> AssignmentEpisodeTarget:
        return AssignmentEpisodeTarget(
            ownership_kind=self.ownership_kind,
            instance_id=self.instance_id,
            group_id=self.group_id,
            definition_id=self.definition_id,
            instance_name_snapshot=self.instance_name_snapshot,
            group_name_snapshot=self.group_name_snapshot,
        ).validated()

    @classmethod
    def from_dict(cls, value: object) -> "AssignmentEpisode":
        if not isinstance(value, dict):
            raise ValueError("episode must be an object")
        try:
            sequence = int(value.get("sequence"))
        except (TypeError, ValueError) as exc:
            raise ValueError("episode sequence must be an integer") from exc
        episode = cls(
            episode_id=str(value.get("episode_id") or "").strip(),
            stock_code=normalize_stock_code(str(value.get("stock_code") or "")),
            ownership_kind=str(value.get("ownership_kind") or "").strip().upper(),
            instance_id=_nullable_text(value.get("instance_id")),
            group_id=_nullable_text(value.get("group_id")),
            definition_id=_nullable_text(value.get("definition_id")),
            instance_name_snapshot=_nullable_text(value.get("instance_name_snapshot")),
            group_name_snapshot=_nullable_text(value.get("group_name_snapshot")),
            started_at=_timestamp(str(value.get("started_at") or "")),
            ended_at=(
                _timestamp(str(value.get("ended_at") or ""))
                if _nullable_text(value.get("ended_at"))
                else None
            ),
            sequence=sequence,
            start_reason=str(value.get("start_reason") or "").strip(),
            end_reason=_nullable_text(value.get("end_reason")),
            source=str(value.get("source") or "").strip(),
            end_source=_nullable_text(value.get("end_source")),
        )
        episode.validate()
        return episode

    def validate(self) -> None:
        try:
            UUID(self.episode_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("episode_id must be a UUID") from exc
        if not is_valid_stock_code(self.stock_code):
            raise ValueError("stock_code is invalid")
        if self.sequence <= 0:
            raise ValueError("episode sequence must be positive")
        if not self.start_reason:
            raise ValueError("episode start_reason is required")
        if not self.source:
            raise ValueError("episode source is required")
        self.target()
        if self.ended_at is not None:
            if _timestamp_value(self.ended_at) < _timestamp_value(self.started_at):
                raise ValueError("ended_at cannot precede started_at")
            if not self.end_reason:
                raise ValueError("closed episode requires end_reason")
            if not self.end_source:
                raise ValueError("closed episode requires end_source")
        elif self.end_reason or self.end_source:
            raise ValueError("open episode cannot carry end metadata")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EpisodeMutationResult:
    success: bool
    changed: bool = False
    no_op: bool = False
    opened_episode: AssignmentEpisode | None = None
    closed_episode: AssignmentEpisode | None = None
    error_code: str = ""
    error: str = ""


def _validate_episode_sequence(
    stock_code: str,
    episodes: tuple[AssignmentEpisode, ...],
) -> None:
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    open_count = 0
    previous: AssignmentEpisode | None = None
    for episode in episodes:
        episode.validate()
        if episode.stock_code != stock_code:
            raise ValueError("episode stock_code does not match document")
        if episode.episode_id in seen_ids:
            raise ValueError("duplicate episode_id")
        if episode.sequence in seen_sequences:
            raise ValueError("duplicate episode sequence")
        if previous is not None:
            if episode.sequence <= previous.sequence:
                raise ValueError("episode sequence must be strictly increasing")
            if previous.ended_at is None:
                raise ValueError("open episode must be the final episode")
            if _timestamp_value(episode.started_at) < _timestamp_value(previous.ended_at):
                raise ValueError("assignment episodes cannot overlap")
        seen_ids.add(episode.episode_id)
        seen_sequences.add(episode.sequence)
        open_count += int(episode.is_open)
        previous = episode
    if open_count > 1:
        raise ValueError("stock cannot have more than one open episode")


class CanonicalAssignmentEpisodeRepository:
    def __init__(
        self,
        project_root: Path | str = PROJECT_ROOT,
        *,
        episodes_root: Path | str | None = None,
        episode_id_factory: Callable[[], object] = uuid4,
    ) -> None:
        self.project_root = Path(project_root)
        self.episodes_root = (
            Path(episodes_root)
            if episodes_root is not None
            else self.project_root / "assignment_episodes"
        )
        self._episode_id_factory = episode_id_factory

    def document_path(self, stock_code: str) -> Path:
        code = self._stock_code(stock_code)
        return self.episodes_root / code / "episodes.json"

    def list_episodes(self, stock_code: str) -> tuple[AssignmentEpisode, ...]:
        code = self._stock_code(stock_code)
        path = self.document_path(code)
        if not path.exists():
            return ()
        return self._read_document(path, expected_stock_code=code)

    def get_open_episode(self, stock_code: str) -> AssignmentEpisode | None:
        return next((episode for episode in self.list_episodes(stock_code) if episode.is_open), None)

    def get_episode(
        self,
        episode_id: str,
        *,
        stock_code: str | None = None,
    ) -> AssignmentEpisode | None:
        clean_id = str(episode_id or "").strip()
        if not clean_id:
            return None
        if stock_code:
            return next(
                (episode for episode in self.list_episodes(stock_code) if episode.episode_id == clean_id),
                None,
            )
        if not self.episodes_root.is_dir():
            return None
        found: AssignmentEpisode | None = None
        for directory in sorted(self.episodes_root.iterdir(), key=lambda path: path.name):
            if not directory.is_dir() or not is_valid_stock_code(directory.name):
                continue
            candidate = self.get_episode(clean_id, stock_code=directory.name)
            if candidate is None:
                continue
            if found is not None:
                raise ValueError("episode_id is not globally unique")
            found = candidate
        return found

    def open_episode(
        self,
        stock_code: str,
        target: AssignmentEpisodeTarget,
        *,
        started_at: str | datetime,
        start_reason: str,
        source: str,
    ) -> EpisodeMutationResult:
        code = self._stock_code(stock_code)
        with _WRITE_LOCK:
            try:
                episodes = self.list_episodes(code)
                if any(episode.is_open for episode in episodes):
                    return EpisodeMutationResult(False, error_code="OPEN_EPISODE_EXISTS", error="open episode already exists")
                opened = self._new_episode(
                    code,
                    target,
                    started_at=started_at,
                    sequence=(episodes[-1].sequence + 1 if episodes else 1),
                    start_reason=start_reason,
                    source=source,
                )
                self._write_document(code, episodes + (opened,))
                return EpisodeMutationResult(True, changed=True, opened_episode=opened)
            except Exception as exc:
                return EpisodeMutationResult(False, error_code="EPISODE_OPEN_FAILED", error=str(exc))

    def close_open_episode(
        self,
        stock_code: str,
        *,
        ended_at: str | datetime,
        end_reason: str,
        source: str,
    ) -> EpisodeMutationResult:
        code = self._stock_code(stock_code)
        with _WRITE_LOCK:
            try:
                episodes = self.list_episodes(code)
                open_episode = next((episode for episode in episodes if episode.is_open), None)
                if open_episode is None:
                    return EpisodeMutationResult(False, error_code="OPEN_EPISODE_MISSING", error="open episode does not exist")
                closed = self._closed_episode(open_episode, ended_at, end_reason, source)
                updated = tuple(closed if item.episode_id == closed.episode_id else item for item in episodes)
                self._write_document(code, updated)
                return EpisodeMutationResult(True, changed=True, closed_episode=closed)
            except Exception as exc:
                return EpisodeMutationResult(False, error_code="EPISODE_CLOSE_FAILED", error=str(exc))

    def transition_episode(
        self,
        stock_code: str,
        target: AssignmentEpisodeTarget,
        *,
        changed_at: str | datetime,
        start_reason: str,
        end_reason: str,
        source: str,
    ) -> EpisodeMutationResult:
        code = self._stock_code(stock_code)
        clean_target = target.validated()
        with _WRITE_LOCK:
            try:
                episodes = self.list_episodes(code)
                open_episode = next((episode for episode in episodes if episode.is_open), None)
                if open_episode is not None and open_episode.target().identity_key() == clean_target.identity_key():
                    return EpisodeMutationResult(
                        True,
                        no_op=True,
                        opened_episode=open_episode,
                    )
                changed_timestamp = _timestamp(changed_at)
                closed: AssignmentEpisode | None = None
                updated = list(episodes)
                if open_episode is not None:
                    closed = self._closed_episode(open_episode, changed_timestamp, end_reason, source)
                    updated[-1] = closed
                opened = self._new_episode(
                    code,
                    clean_target,
                    started_at=changed_timestamp,
                    sequence=(updated[-1].sequence + 1 if updated else 1),
                    start_reason=start_reason,
                    source=source,
                )
                final = tuple(updated) + (opened,)
                self._write_document(code, final)
                return EpisodeMutationResult(
                    True,
                    changed=True,
                    opened_episode=opened,
                    closed_episode=closed,
                )
            except Exception as exc:
                return EpisodeMutationResult(False, error_code="EPISODE_TRANSITION_FAILED", error=str(exc))

    def _stock_code(self, value: str) -> str:
        code = normalize_stock_code(str(value or ""))
        if not is_valid_stock_code(code):
            raise ValueError("stock_code is invalid")
        return code

    def _new_episode(
        self,
        stock_code: str,
        target: AssignmentEpisodeTarget,
        *,
        started_at: str | datetime,
        sequence: int,
        start_reason: str,
        source: str,
    ) -> AssignmentEpisode:
        clean_target = target.validated()
        episode = AssignmentEpisode(
            episode_id=str(self._episode_id_factory()),
            stock_code=stock_code,
            ownership_kind=clean_target.ownership_kind,
            instance_id=clean_target.instance_id,
            group_id=clean_target.group_id,
            definition_id=clean_target.definition_id,
            instance_name_snapshot=clean_target.instance_name_snapshot,
            group_name_snapshot=clean_target.group_name_snapshot,
            started_at=_timestamp(started_at),
            ended_at=None,
            sequence=sequence,
            start_reason=str(start_reason or "").strip(),
            end_reason=None,
            source=str(source or "").strip(),
            end_source=None,
        )
        episode.validate()
        return episode

    @staticmethod
    def _closed_episode(
        episode: AssignmentEpisode,
        ended_at: str | datetime,
        end_reason: str,
        source: str,
    ) -> AssignmentEpisode:
        if not episode.is_open:
            raise ValueError("closed episode is immutable")
        closed = replace(
            episode,
            ended_at=_timestamp(ended_at),
            end_reason=str(end_reason or "").strip(),
            end_source=str(source or "").strip(),
        )
        closed.validate()
        return closed

    def _read_document(
        self,
        path: Path,
        *,
        expected_stock_code: str,
    ) -> tuple[AssignmentEpisode, ...]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"failed to read canonical assignment episodes: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("episode document must be an object")
        if str(data.get("schema_version") or "") != EPISODE_SCHEMA_VERSION:
            raise ValueError("unsupported episode schema_version")
        stock_code = self._stock_code(str(data.get("stock_code") or ""))
        if stock_code != expected_stock_code:
            raise ValueError("episode document stock_code mismatch")
        raw_episodes = data.get("episodes")
        if not isinstance(raw_episodes, list):
            raise ValueError("episode document episodes must be a list")
        episodes = tuple(AssignmentEpisode.from_dict(item) for item in raw_episodes)
        _validate_episode_sequence(stock_code, episodes)
        return episodes

    def _document(
        self,
        stock_code: str,
        episodes: tuple[AssignmentEpisode, ...],
    ) -> dict[str, Any]:
        _validate_episode_sequence(stock_code, episodes)
        return {
            "schema_version": EPISODE_SCHEMA_VERSION,
            "stock_code": stock_code,
            "episodes": [episode.to_dict() for episode in episodes],
        }

    def _write_document(
        self,
        stock_code: str,
        episodes: tuple[AssignmentEpisode, ...],
    ) -> None:
        path = self.document_path(stock_code)
        data = self._document(stock_code, episodes)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            staged = self._read_document(temp_path, expected_stock_code=stock_code)
            if staged != episodes:
                raise RuntimeError("staged episode read-back does not match")
            os.replace(temp_path, path)
            verified = self._read_document(path, expected_stock_code=stock_code)
            if verified != episodes:
                raise RuntimeError("episode read-back does not match")
        finally:
            temp_path.unlink(missing_ok=True)
