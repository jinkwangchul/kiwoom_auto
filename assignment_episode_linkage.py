"""Atomic Production linkage between Stock assignment and canonical episodes."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterator
from uuid import UUID, uuid4

import stock_repository as stock_repository_module
from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from gui_routine_registry import scan_group_records
from routine_instance_repository import RoutineInstanceRepository
from stock_repository import (
    ROUTINE_ASSIGNMENT_HISTORY_KEY,
    STOCK_CONFIG_DELETE_FIELD,
    STOCK_CONFIG_EXPECTED_MISSING,
    STOCK_CONFIG_WRITE_FIELD_CONFLICT,
    StockAssignmentMutationResult,
    StockConfigWriteResult,
    StockRepository,
    is_valid_stock_code,
    normalize_stock_code,
    read_json_dict,
)


ASSIGNMENT_TRANSACTION_SCHEMA_VERSION = "1.0"
ASSIGNMENT_TRANSACTION_PREPARED = "PREPARED"
ASSIGNMENT_TRANSACTION_EPISODE_APPLIED = "EPISODE_APPLIED"
ASSIGNMENT_TRANSACTION_CONFIG_APPLIED = "CONFIG_APPLIED"
ASSIGNMENT_TRANSACTION_COMMITTED = "COMMITTED"
ASSIGNMENT_TRANSACTION_ABORTED = "ABORTED"
ASSIGNMENT_TRANSACTION_ROLLED_BACK = "ROLLED_BACK"
ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

ASSIGNMENT_TRANSACTION_ACTIVE_STATES = frozenset(
    {
        ASSIGNMENT_TRANSACTION_PREPARED,
        ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
        ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
        ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
    }
)
ASSIGNMENT_TRANSACTION_TERMINAL_STATES = frozenset(
    {
        ASSIGNMENT_TRANSACTION_COMMITTED,
        ASSIGNMENT_TRANSACTION_ABORTED,
        ASSIGNMENT_TRANSACTION_ROLLED_BACK,
    }
)
ASSIGNMENT_TRANSACTION_STATES = (
    ASSIGNMENT_TRANSACTION_ACTIVE_STATES | ASSIGNMENT_TRANSACTION_TERMINAL_STATES
)
ASSIGNMENT_TRANSACTION_OPERATIONS = frozenset({"ASSIGN", "REASSIGN", "UNASSIGN"})

ASSIGNMENT_CONFIG_FIELDS = (
    "routine",
    "routine_name",
    "assigned_routine",
    "active_routine",
    "routines",
    "assigned_routine_instance_id",
    "routine_instance_name",
    "routine_definition_id",
    "routine_type",
    ROUTINE_ASSIGNMENT_HISTORY_KEY,
)

ASSIGNMENT_TRANSACTION_OK = "OK"
ASSIGNMENT_TRANSACTION_FIELD_CONFLICT = STOCK_CONFIG_WRITE_FIELD_CONFLICT
ASSIGNMENT_TRANSACTION_PREPARE_FAILED = "PREPARE_FAILED"
ASSIGNMENT_TRANSACTION_EPISODE_FAILED = "EPISODE_FAILED"
ASSIGNMENT_TRANSACTION_CONFIG_FAILED = "CONFIG_FAILED"
ASSIGNMENT_TRANSACTION_JOURNAL_FAILED = "JOURNAL_FAILED"
ASSIGNMENT_TRANSACTION_VERIFICATION_FAILED = "VERIFICATION_FAILED"
ASSIGNMENT_TRANSACTION_RECONCILIATION_NEEDED = "RECONCILIATION_REQUIRED"

_MISSING_FINGERPRINT = "MISSING"
_ASSIGNMENT_LOCKS_GUARD = threading.Lock()
_ASSIGNMENT_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True)
class AssignmentTransactionResult:
    success: bool
    changed: bool = False
    transaction_id: str = ""
    journal_state: str = ""
    error_code: str = ""
    error: str = ""
    reconciliation_required: bool = False


@dataclass(frozen=True)
class AssignmentReconciliationResult:
    transaction_id: str
    stock_code: str
    previous_state: str
    terminal_state: str
    classification: str
    config_identity: dict[str, str] | None
    episode_identity: dict[str, str] | None
    review_required: bool
    reason: str = ""


@dataclass(frozen=True)
class AssignmentJournalScanIssue:
    journal_path: str
    stock_code: str
    transaction_id: str
    reason: str


@dataclass(frozen=True)
class AssignmentJournalScanResult:
    records: tuple[dict[str, Any], ...]
    issues: tuple[AssignmentJournalScanIssue, ...]


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def assignment_target_from_config(
    project_root: Path | str,
    config: dict[str, Any],
) -> AssignmentEpisodeTarget:
    instance_id = str(config.get("assigned_routine_instance_id") or "").strip()
    if not instance_id:
        return AssignmentEpisodeTarget.unassigned()
    root = Path(project_root)
    instance = RoutineInstanceRepository(root).get_instance(instance_id)
    if instance is None:
        raise ValueError(f"assigned RoutineInstance does not exist: {instance_id}")
    group_id = str(instance.group_id or "").strip()
    if not group_id:
        raise ValueError(f"assigned RoutineInstance has no group_id: {instance_id}")
    group = next(
        (item for item in scan_group_records(project_root=root) if item.group_id == group_id),
        None,
    )
    if group is None:
        raise ValueError(f"assigned Logical Group does not exist: {group_id}")
    return AssignmentEpisodeTarget.assigned(
        instance_id=instance.instance_id,
        group_id=group.group_id,
        definition_id=instance.definition_id,
        instance_name_snapshot=instance.display_name,
        group_name_snapshot=group.display_name,
    )


def ensure_current_assignment_episode(
    project_root: Path | str,
    stock_code: str,
    config: dict[str, Any],
    *,
    observed_at: str | datetime,
    source: str,
    repository: CanonicalAssignmentEpisodeRepository | None = None,
):
    """Return the exact open Episode, bootstrapping only at observation time."""
    root = Path(project_root)
    repo = repository or CanonicalAssignmentEpisodeRepository(root)
    target = assignment_target_from_config(root, config)
    current = repo.get_open_episode(stock_code)
    if current is None:
        reason = (
            "BOOTSTRAP_CURRENT_ASSIGNED"
            if target.ownership_kind == "ASSIGNED"
            else "BOOTSTRAP_UNASSIGNED"
        )
        result = repo.open_episode(
            stock_code,
            target,
            started_at=observed_at,
            start_reason=reason,
            source=source,
        )
        if not result.success or result.opened_episode is None:
            raise RuntimeError(result.error or "assignment Episode bootstrap failed")
        return result.opened_episode, True
    if current.target().identity_key() != target.identity_key():
        raise RuntimeError(
            "current Stock assignment and open canonical Episode do not match"
        )
    return current, False


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _history_time(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    if not text:
        raise ValueError("assignment changed_at is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _episode_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("assignment changed_at is required")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone()


def _file_fingerprint(path: Path) -> str:
    if not path.exists():
        return _MISSING_FINGERPRINT
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _target_identity(target: AssignmentEpisodeTarget) -> dict[str, str]:
    clean = target.validated()
    return {
        "ownership_kind": clean.ownership_kind,
        "instance_id": clean.instance_id or "",
        "group_id": clean.group_id or "",
        "definition_id": clean.definition_id or "",
    }


def _identity_key(identity: dict[str, Any] | None) -> tuple[str, str, str, str] | None:
    if identity is None:
        return None
    target = _target_from_identity(identity)
    return target.identity_key()


def _target_from_identity(identity: dict[str, Any]) -> AssignmentEpisodeTarget:
    if not isinstance(identity, dict):
        raise ValueError("assignment identity must be an object")
    kind = str(identity.get("ownership_kind") or "").strip().upper()
    if kind == "UNASSIGNED":
        return AssignmentEpisodeTarget.unassigned()
    if kind != "ASSIGNED":
        raise ValueError("assignment identity ownership_kind is invalid")
    return AssignmentEpisodeTarget.assigned(
        instance_id=str(identity.get("instance_id") or "").strip(),
        group_id=str(identity.get("group_id") or "").strip() or None,
        definition_id=str(identity.get("definition_id") or "").strip() or None,
        instance_name_snapshot=None,
        group_name_snapshot=None,
    )


def _episode_identity(
    repository: CanonicalAssignmentEpisodeRepository,
    stock_code: str,
) -> dict[str, str] | None:
    episode = repository.get_open_episode(stock_code)
    return _target_identity(episode.target()) if episode is not None else None


def _canonical_stock_code(stock_code: str) -> str:
    code = normalize_stock_code(str(stock_code or ""))
    if not is_valid_stock_code(code):
        raise ValueError("stock_code is invalid")
    return code


def _assignment_lock(stock_code: str) -> threading.RLock:
    code = _canonical_stock_code(stock_code)
    with _ASSIGNMENT_LOCKS_GUARD:
        return _ASSIGNMENT_LOCKS.setdefault(code, threading.RLock())


@contextmanager
def assignment_transaction_lock(stock_code: str) -> Iterator[None]:
    """Serialize assignment transactions for one canonical Stock identity."""

    lock = _assignment_lock(stock_code)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


class AssignmentTransactionJournalRepository:
    """Durable evidence for the Assignment/Episode multi-file boundary."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        episodes_repository: CanonicalAssignmentEpisodeRepository | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.episodes_repository = episodes_repository or CanonicalAssignmentEpisodeRepository(
            self.project_root
        )
        self.transactions_root = self.episodes_repository.episodes_root / "_transactions"

    def document_path(self, stock_code: str, transaction_id: str) -> Path:
        code = _canonical_stock_code(stock_code)
        clean_id = self._transaction_id(transaction_id)
        return self.transactions_root / code / f"{clean_id}.json"

    def create_prepared(
        self,
        *,
        transaction_id: str,
        stock_code: str,
        operation: str,
        before_assignment_identity: dict[str, Any],
        target_assignment_identity: dict[str, Any],
        before_config_fingerprint: str,
        before_episode_identity: dict[str, Any] | None,
        before_episode_fingerprint: str,
        reason: str,
        source: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        clean_id = self._transaction_id(transaction_id)
        code = _canonical_stock_code(stock_code)
        clean_operation = str(operation or "").strip().upper()
        if clean_operation not in ASSIGNMENT_TRANSACTION_OPERATIONS:
            raise ValueError("assignment transaction operation is invalid")
        timestamp = str(created_at or _now_text()).strip()
        data: dict[str, Any] = {
            "schema_version": ASSIGNMENT_TRANSACTION_SCHEMA_VERSION,
            "transaction_id": clean_id,
            "stock_code": code,
            "operation": clean_operation,
            "state": ASSIGNMENT_TRANSACTION_PREPARED,
            "created_at": timestamp,
            "updated_at": timestamp,
            "before_assignment_identity": _target_identity(
                _target_from_identity(before_assignment_identity)
            ),
            "target_assignment_identity": _target_identity(
                _target_from_identity(target_assignment_identity)
            ),
            "before_config_fingerprint": str(before_config_fingerprint or ""),
            "after_config_fingerprint": "",
            "before_episode_identity": (
                _target_identity(_target_from_identity(before_episode_identity))
                if before_episode_identity is not None
                else None
            ),
            "after_episode_identity": None,
            "before_episode_fingerprint": str(before_episode_fingerprint or ""),
            "after_episode_fingerprint": "",
            "reason": str(reason or "").strip(),
            "source": str(source or "").strip(),
            "failure_stage": "",
        }
        self._validate(data)
        path = self.document_path(code, clean_id)
        if path.exists():
            raise FileExistsError(f"assignment transaction journal already exists: {clean_id}")
        self._write(path, data)
        return data

    def get(self, stock_code: str, transaction_id: str) -> dict[str, Any] | None:
        path = self.document_path(stock_code, transaction_id)
        if not path.exists():
            return None
        return self._read(path)

    def transition(
        self,
        stock_code: str,
        transaction_id: str,
        state: str,
        **evidence: Any,
    ) -> dict[str, Any]:
        path = self.document_path(stock_code, transaction_id)
        current = self._read(path)
        target_state = str(state or "").strip().upper()
        self._validate_transition(str(current.get("state") or ""), target_state)
        updated = deepcopy(current)
        updated["state"] = target_state
        updated["updated_at"] = _now_text()
        allowed_evidence = {
            "after_config_fingerprint",
            "after_episode_identity",
            "after_episode_fingerprint",
            "failure_stage",
            "reason",
        }
        unknown = set(evidence) - allowed_evidence
        if unknown:
            raise ValueError(f"unsupported assignment journal evidence: {sorted(unknown)}")
        for key, value in evidence.items():
            updated[key] = deepcopy(value)
        self._validate(updated)
        self._write(path, updated)
        return updated

    def scan_incomplete(self) -> AssignmentJournalScanResult:
        if not self.transactions_root.is_dir():
            return AssignmentJournalScanResult((), ())
        records: list[dict[str, Any]] = []
        issues: list[AssignmentJournalScanIssue] = []
        for path in sorted(self.transactions_root.glob("*.json"), key=lambda item: item.name):
            issues.append(
                AssignmentJournalScanIssue(
                    journal_path=str(path),
                    stock_code="",
                    transaction_id=path.stem,
                    reason="assignment journal is outside a canonical stock directory",
                )
            )
        for stock_dir in sorted(self.transactions_root.iterdir(), key=lambda item: item.name):
            if not stock_dir.is_dir():
                continue
            stock_code = normalize_stock_code(stock_dir.name)
            for path in sorted(stock_dir.glob("*.json"), key=lambda item: item.name):
                try:
                    record = self._read(path)
                except Exception as exc:
                    issues.append(
                        AssignmentJournalScanIssue(
                            journal_path=str(path),
                            stock_code=(
                                stock_code if is_valid_stock_code(stock_code) else ""
                            ),
                            transaction_id=path.stem,
                            reason=str(exc),
                        )
                    )
                    continue
                if str(record.get("state") or "") in ASSIGNMENT_TRANSACTION_ACTIVE_STATES:
                    records.append(record)
        return AssignmentJournalScanResult(tuple(records), tuple(issues))

    def list_incomplete(self) -> tuple[dict[str, Any], ...]:
        return self.scan_incomplete().records

    @staticmethod
    def _transaction_id(value: str) -> str:
        clean = str(value or "").strip()
        try:
            return str(UUID(clean))
        except (ValueError, AttributeError) as exc:
            raise ValueError("assignment transaction_id must be a UUID") from exc

    @staticmethod
    def _validate_transition(current: str, target: str) -> None:
        if current not in ASSIGNMENT_TRANSACTION_STATES:
            raise ValueError("current assignment journal state is invalid")
        if target not in ASSIGNMENT_TRANSACTION_STATES:
            raise ValueError("target assignment journal state is invalid")
        if current in ASSIGNMENT_TRANSACTION_TERMINAL_STATES:
            raise ValueError("terminal assignment journal cannot transition")
        allowed = {
            ASSIGNMENT_TRANSACTION_PREPARED: {
                ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
                ASSIGNMENT_TRANSACTION_COMMITTED,
                ASSIGNMENT_TRANSACTION_ABORTED,
                ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            },
            ASSIGNMENT_TRANSACTION_EPISODE_APPLIED: {
                ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
                ASSIGNMENT_TRANSACTION_COMMITTED,
                ASSIGNMENT_TRANSACTION_ROLLED_BACK,
                ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            },
            ASSIGNMENT_TRANSACTION_CONFIG_APPLIED: {
                ASSIGNMENT_TRANSACTION_COMMITTED,
                ASSIGNMENT_TRANSACTION_ROLLED_BACK,
                ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            },
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED: {
                ASSIGNMENT_TRANSACTION_COMMITTED,
                ASSIGNMENT_TRANSACTION_ABORTED,
                ASSIGNMENT_TRANSACTION_ROLLED_BACK,
            },
        }
        if target not in allowed[current]:
            raise ValueError(f"invalid assignment journal transition: {current} -> {target}")

    def _validate(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise ValueError("assignment transaction journal must be an object")
        if str(data.get("schema_version") or "") != ASSIGNMENT_TRANSACTION_SCHEMA_VERSION:
            raise ValueError("unsupported assignment transaction journal schema")
        self._transaction_id(str(data.get("transaction_id") or ""))
        _canonical_stock_code(str(data.get("stock_code") or ""))
        if str(data.get("operation") or "") not in ASSIGNMENT_TRANSACTION_OPERATIONS:
            raise ValueError("assignment journal operation is invalid")
        if str(data.get("state") or "") not in ASSIGNMENT_TRANSACTION_STATES:
            raise ValueError("assignment journal state is invalid")
        _target_from_identity(data.get("before_assignment_identity"))
        _target_from_identity(data.get("target_assignment_identity"))
        before_episode = data.get("before_episode_identity")
        after_episode = data.get("after_episode_identity")
        if before_episode is not None:
            _target_from_identity(before_episode)
        if after_episode is not None:
            _target_from_identity(after_episode)
        for key in (
            "created_at",
            "updated_at",
            "before_config_fingerprint",
            "after_config_fingerprint",
            "before_episode_fingerprint",
            "after_episode_fingerprint",
            "reason",
            "source",
            "failure_stage",
        ):
            if not isinstance(data.get(key), str):
                raise ValueError(f"assignment journal {key} must be text")

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"failed to read assignment transaction journal: {exc}") from exc
        self._validate(data)
        return data

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        _atomic_write_json(path, data)
        persisted = self._read(path)
        if persisted != data:
            raise RuntimeError("assignment transaction journal read-back does not match")


def _read_config_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to read Stock config: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Stock config must be an object")
    return data


def _field_expectations(
    config: dict[str, Any],
    fields: tuple[str, ...] = ASSIGNMENT_CONFIG_FIELDS,
) -> dict[str, Any]:
    return {
        key: deepcopy(config[key]) if key in config else STOCK_CONFIG_EXPECTED_MISSING
        for key in fields
    }


def _field_patch(
    config: dict[str, Any],
    fields: tuple[str, ...] = ASSIGNMENT_CONFIG_FIELDS,
) -> dict[str, Any]:
    return {
        key: deepcopy(config[key]) if key in config else STOCK_CONFIG_DELETE_FIELD
        for key in fields
    }


def _assignment_operation(
    before_target: AssignmentEpisodeTarget,
    target: AssignmentEpisodeTarget,
) -> str:
    if target.ownership_kind == "UNASSIGNED":
        return "UNASSIGN"
    if before_target.ownership_kind == "UNASSIGNED":
        return "ASSIGN"
    return "REASSIGN"


def _build_assignment_after_config(
    before_config: dict[str, Any],
    target: AssignmentEpisodeTarget,
    *,
    routine_type: str,
    changed_at: str,
) -> dict[str, Any]:
    after = deepcopy(before_config)
    before_instance_id = str(after.get("assigned_routine_instance_id") or "").strip()
    if before_instance_id and before_instance_id != (target.instance_id or ""):
        StockRepository._close_assignment_history(
            after,
            instance_id=before_instance_id,
            instance_name=str(after.get("routine_instance_name") or ""),
            definition_id=str(after.get("routine_definition_id") or ""),
            routine_type=str(after.get("routine_type") or ""),
            changed_at=changed_at,
        )

    if target.ownership_kind == "ASSIGNED":
        clean_routine_type = str(routine_type or "").strip()
        if not clean_routine_type:
            raise ValueError("assigned transaction requires routine_type")
        StockRepository._open_assignment_history(
            after,
            instance_id=target.instance_id or "",
            instance_name=target.instance_name_snapshot or "",
            definition_id=target.definition_id or "",
            routine_type=clean_routine_type,
            changed_at=changed_at,
        )
        after.update(
            {
                "routine": clean_routine_type,
                "routine_name": clean_routine_type,
                "assigned_routine": clean_routine_type,
                "active_routine": clean_routine_type,
                "routines": [clean_routine_type],
                "assigned_routine_instance_id": target.instance_id or "",
                "routine_instance_name": target.instance_name_snapshot or "",
                "routine_definition_id": target.definition_id or "",
                "routine_type": clean_routine_type,
            }
        )
    else:
        after.update(
            {
                "routine": "",
                "routine_name": "",
                "assigned_routine": "",
                "active_routine": "",
                "routines": [],
                "assigned_routine_instance_id": "",
                "routine_instance_name": "",
                "routine_definition_id": "",
                "routine_type": "",
            }
        )
    after["updated_at"] = changed_at
    return after


def conditional_compensate_assignment_config(
    stock_repository: StockRepository,
    stock_code: str,
    stock_name: str,
    *,
    before_config: dict[str, Any],
    transaction_config: dict[str, Any],
) -> StockConfigWriteResult:
    """Restore only this transaction's assignment fields when they still match."""

    return stock_repository.patch_stock_config(
        stock_code,
        _field_patch(before_config),
        name=stock_name,
        expected_fields=_field_expectations(transaction_config),
    )


def _conditional_compensate_episode(
    episode_path: Path,
    *,
    expected_transaction_fingerprint: str,
    before_payload: bytes | None,
) -> bool:
    if _file_fingerprint(episode_path) != expected_transaction_fingerprint:
        return False
    try:
        if before_payload is None:
            episode_path.unlink(missing_ok=True)
            if episode_path.parent.is_dir() and not any(episode_path.parent.iterdir()):
                episode_path.parent.rmdir()
            return not episode_path.exists()
        _atomic_write_bytes(episode_path, before_payload)
        return episode_path.read_bytes() == before_payload
    except Exception:
        return False


def _dependencies_available(project_root: Path, identity: dict[str, Any] | None) -> bool:
    if identity is None:
        return True
    target = _target_from_identity(identity)
    if target.ownership_kind == "UNASSIGNED":
        return True
    instance = RoutineInstanceRepository(project_root).get_instance(target.instance_id or "")
    if instance is None:
        return False
    if str(instance.group_id or "").strip() != str(target.group_id or "").strip():
        return False
    if str(instance.definition_id or "").strip() != str(target.definition_id or "").strip():
        return False
    return any(
        group.group_id == target.group_id
        for group in scan_group_records(project_root=project_root)
    )


def execute_assignment_transaction_foundation(
    project_root: Path | str,
    stock_code: str,
    *,
    stock_name: str = "",
    target_instance_id: str = "",
    target_routine_type: str = "",
    expected_instance_id: str | None = None,
    changed_at: str | datetime,
    reason: str,
    source: str,
    stock_repository: StockRepository | None = None,
    episode_repository: CanonicalAssignmentEpisodeRepository | None = None,
    journal_repository: AssignmentTransactionJournalRepository | None = None,
) -> AssignmentTransactionResult:
    """Execute the canonical Assignment transaction boundary."""

    root = Path(project_root)
    code = _canonical_stock_code(stock_code)
    transaction_id = ""
    stock_repo = stock_repository or StockRepository(root)
    episode_repo = episode_repository or CanonicalAssignmentEpisodeRepository(root)
    journal_repo = journal_repository or AssignmentTransactionJournalRepository(
        root,
        episodes_repository=episode_repo,
    )
    config_path = stock_repo.resolve_stock_dir(code, stock_name) / "config.json"
    episode_path = episode_repo.document_path(code)

    with assignment_transaction_lock(code):
        try:
            before_config = _read_config_object(config_path)
            before_target = assignment_target_from_config(root, before_config)
            actual_instance_id = str(before_target.instance_id or "").strip()
            if expected_instance_id is not None:
                expected = str(expected_instance_id or "").strip()
                if actual_instance_id != expected:
                    return AssignmentTransactionResult(
                        False,
                        error_code=ASSIGNMENT_TRANSACTION_FIELD_CONFLICT,
                        error=(
                            "assignment identity changed before transaction: "
                            f"expected={expected or '<unassigned>'} "
                            f"actual={actual_instance_id or '<unassigned>'}"
                        ),
                    )
            target_instance = str(target_instance_id or "").strip()
            target_record = (
                RoutineInstanceRepository(root).get_instance(target_instance)
                if target_instance
                else None
            )
            target = (
                assignment_target_from_config(
                    root,
                    {"assigned_routine_instance_id": target_instance},
                )
                if target_instance
                else AssignmentEpisodeTarget.unassigned()
            )
            resolved_routine_type = (
                str(getattr(target_record, "source_routine_name", "") or "").strip()
                or str(target_routine_type or "").strip()
            )
            operation = _assignment_operation(before_target, target)
            history_time = _history_time(changed_at)
            episode_changed_at = _episode_time(changed_at)
            after_config = _build_assignment_after_config(
                before_config,
                target,
                routine_type=resolved_routine_type,
                changed_at=history_time,
            )
            config_before_fingerprint = _file_fingerprint(config_path)
            episode_before_payload = episode_path.read_bytes() if episode_path.exists() else None
            episode_before_fingerprint = _file_fingerprint(episode_path)
            before_episode_identity = _episode_identity(episode_repo, code)
            transaction_id = str(uuid4())
            journal_repo.create_prepared(
                transaction_id=transaction_id,
                stock_code=code,
                operation=operation,
                before_assignment_identity=_target_identity(before_target),
                target_assignment_identity=_target_identity(target),
                before_config_fingerprint=config_before_fingerprint,
                before_episode_identity=before_episode_identity,
                before_episode_fingerprint=episode_before_fingerprint,
                reason=reason,
                source=source,
            )
        except Exception as exc:
            return AssignmentTransactionResult(
                False,
                transaction_id=transaction_id,
                error_code=ASSIGNMENT_TRANSACTION_PREPARE_FAILED,
                error=str(exc),
            )

        before_key = before_target.identity_key()
        target_key = target.identity_key()
        if before_episode_identity is not None and _identity_key(before_episode_identity) != before_key:
            journal_repo.transition(
                code,
                transaction_id,
                ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
                failure_stage="PREPARE_IDENTITY_CHECK",
                reason="config and open episode identities differ before mutation",
            )
            return AssignmentTransactionResult(
                False,
                transaction_id=transaction_id,
                journal_state=ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
                error_code=ASSIGNMENT_TRANSACTION_RECONCILIATION_NEEDED,
                error="config and open episode identities differ before mutation",
                reconciliation_required=True,
            )

        if before_episode_identity is None:
            opened = episode_repo.open_episode(
                code,
                before_target,
                started_at=episode_changed_at,
                start_reason="BOOTSTRAP_CURRENT_ASSIGNED"
                if before_target.ownership_kind == "ASSIGNED"
                else "BOOTSTRAP_UNASSIGNED",
                source=source,
            )
            if not opened.success:
                journal_repo.transition(
                    code,
                    transaction_id,
                    ASSIGNMENT_TRANSACTION_ABORTED,
                    failure_stage="EPISODE_BOOTSTRAP",
                    reason=opened.error or "episode bootstrap failed",
                )
                return AssignmentTransactionResult(
                    False,
                    transaction_id=transaction_id,
                    journal_state=ASSIGNMENT_TRANSACTION_ABORTED,
                    error_code=ASSIGNMENT_TRANSACTION_EPISODE_FAILED,
                    error=opened.error,
                )

        episode_ready_fingerprint = _file_fingerprint(episode_path)
        transition = episode_repo.transition_episode(
            code,
            target,
            changed_at=episode_changed_at,
            start_reason=reason,
            end_reason=reason,
            source=source,
        )
        if not transition.success:
            compensated = _conditional_compensate_episode(
                episode_path,
                expected_transaction_fingerprint=episode_ready_fingerprint,
                before_payload=episode_before_payload,
            )
            state = (
                ASSIGNMENT_TRANSACTION_ABORTED
                if compensated
                else ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
            )
            journal_repo.transition(
                code,
                transaction_id,
                state,
                failure_stage="EPISODE_APPLY",
                reason=transition.error or "episode transition failed",
            )
            return AssignmentTransactionResult(
                False,
                transaction_id=transaction_id,
                journal_state=state,
                error_code=ASSIGNMENT_TRANSACTION_EPISODE_FAILED,
                error=transition.error,
                reconciliation_required=state
                == ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            )

        episode_after_fingerprint = _file_fingerprint(episode_path)
        episode_after_identity = _episode_identity(episode_repo, code)
        try:
            journal_repo.transition(
                code,
                transaction_id,
                ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
                after_episode_identity=episode_after_identity,
                after_episode_fingerprint=episode_after_fingerprint,
            )
        except Exception as exc:
            compensated = _conditional_compensate_episode(
                episode_path,
                expected_transaction_fingerprint=episode_after_fingerprint,
                before_payload=episode_before_payload,
            )
            terminal_state = (
                ASSIGNMENT_TRANSACTION_ABORTED
                if compensated
                else ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
            )
            journal_state = ASSIGNMENT_TRANSACTION_PREPARED
            try:
                journal_repo.transition(
                    code,
                    transaction_id,
                    terminal_state,
                    failure_stage="EPISODE_JOURNAL",
                    reason=str(exc),
                )
                journal_state = terminal_state
            except Exception:
                pass
            return AssignmentTransactionResult(
                False,
                transaction_id=transaction_id,
                journal_state=journal_state,
                error_code=ASSIGNMENT_TRANSACTION_JOURNAL_FAILED,
                error=str(exc),
                reconciliation_required=(
                    not compensated
                    or journal_state in ASSIGNMENT_TRANSACTION_ACTIVE_STATES
                ),
            )

        assignment_patch = _field_patch(after_config)
        config_result = stock_repo.patch_stock_config(
            code,
            assignment_patch,
            name=stock_name,
            expected_fields=_field_expectations(before_config),
        )
        if not config_result.ok:
            episode_compensated = _conditional_compensate_episode(
                episode_path,
                expected_transaction_fingerprint=episode_after_fingerprint,
                before_payload=episode_before_payload,
            )
            current_config_identity: dict[str, str] | None = None
            current_episode_identity: dict[str, str] | None = None
            try:
                current_config_identity = _target_identity(
                    assignment_target_from_config(root, _read_config_object(config_path))
                )
                current_episode_identity = _episode_identity(episode_repo, code)
            except Exception:
                pass
            clean_rollback = (
                episode_compensated
                and _identity_key(current_config_identity) == before_key
                and _identity_key(current_episode_identity)
                == _identity_key(before_episode_identity)
            )
            state = (
                ASSIGNMENT_TRANSACTION_ROLLED_BACK
                if clean_rollback
                else ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
            )
            journal_repo.transition(
                code,
                transaction_id,
                state,
                failure_stage="CONFIG_APPLY",
                reason=config_result.reason_code,
            )
            return AssignmentTransactionResult(
                False,
                transaction_id=transaction_id,
                journal_state=state,
                error_code=(
                    ASSIGNMENT_TRANSACTION_FIELD_CONFLICT
                    if config_result.reason_code == STOCK_CONFIG_WRITE_FIELD_CONFLICT
                    else ASSIGNMENT_TRANSACTION_CONFIG_FAILED
                ),
                error=config_result.reason_code,
                reconciliation_required=state
                == ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            )

        try:
            journal_repo.transition(
                code,
                transaction_id,
                ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
                after_config_fingerprint=config_result.after_fingerprint,
            )
        except Exception as exc:
            return AssignmentTransactionResult(
                False,
                changed=True,
                transaction_id=transaction_id,
                journal_state=ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
                error_code=ASSIGNMENT_TRANSACTION_JOURNAL_FAILED,
                error=str(exc),
                reconciliation_required=True,
            )

        try:
            persisted_config = _read_config_object(config_path)
            persisted_target = assignment_target_from_config(root, persisted_config)
            persisted_episode_identity = _episode_identity(episode_repo, code)
            history_matches = persisted_config.get(
                ROUTINE_ASSIGNMENT_HISTORY_KEY,
                STOCK_CONFIG_EXPECTED_MISSING,
            ) == after_config.get(
                ROUTINE_ASSIGNMENT_HISTORY_KEY,
                STOCK_CONFIG_EXPECTED_MISSING,
            )
            verified = (
                persisted_target.identity_key() == target_key
                and _identity_key(persisted_episode_identity) == target_key
                and history_matches
            )
        except Exception:
            verified = False

        if not verified:
            config_compensation = conditional_compensate_assignment_config(
                stock_repo,
                code,
                stock_name,
                before_config=before_config,
                transaction_config=after_config,
            )
            episode_compensated = _conditional_compensate_episode(
                episode_path,
                expected_transaction_fingerprint=episode_after_fingerprint,
                before_payload=episode_before_payload,
            )
            clean_rollback = False
            if config_compensation.ok and episode_compensated:
                try:
                    compensated_config_identity = _target_identity(
                        assignment_target_from_config(
                            root,
                            _read_config_object(config_path),
                        )
                    )
                    compensated_episode_identity = _episode_identity(episode_repo, code)
                    clean_rollback = (
                        _identity_key(compensated_config_identity) == before_key
                        and _identity_key(compensated_episode_identity)
                        == _identity_key(before_episode_identity)
                    )
                except Exception:
                    clean_rollback = False
            state = (
                ASSIGNMENT_TRANSACTION_ROLLED_BACK
                if clean_rollback
                else ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
            )
            journal_repo.transition(
                code,
                transaction_id,
                state,
                failure_stage="FINAL_VERIFY",
                reason="assignment transaction final verification failed",
            )
            return AssignmentTransactionResult(
                False,
                transaction_id=transaction_id,
                journal_state=state,
                error_code=ASSIGNMENT_TRANSACTION_VERIFICATION_FAILED,
                error="assignment transaction final verification failed",
                reconciliation_required=state
                == ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            )

        try:
            journal_repo.transition(
                code,
                transaction_id,
                ASSIGNMENT_TRANSACTION_COMMITTED,
                after_config_fingerprint=_file_fingerprint(config_path),
                after_episode_identity=persisted_episode_identity,
                after_episode_fingerprint=_file_fingerprint(episode_path),
            )
        except Exception as exc:
            return AssignmentTransactionResult(
                False,
                changed=True,
                transaction_id=transaction_id,
                journal_state=ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
                error_code=ASSIGNMENT_TRANSACTION_JOURNAL_FAILED,
                error=str(exc),
                reconciliation_required=True,
            )
        return AssignmentTransactionResult(
            True,
            changed=bool(
                config_result.changed
                or transition.changed
                or before_episode_identity is None
            ),
            transaction_id=transaction_id,
            journal_state=ASSIGNMENT_TRANSACTION_COMMITTED,
            error_code=ASSIGNMENT_TRANSACTION_OK,
        )


def _remember_stock_assignment_result(
    stock_repository: StockRepository,
    result: StockAssignmentMutationResult,
) -> StockAssignmentMutationResult:
    stock_repository.last_assignment_transaction_result = result
    stock_repository.last_assignment_linkage_result = result
    return result


def unassign_stock_routine(
    project_root: Path | str,
    stock_code: str,
    stock_name: str,
    routines: list[str],
    *,
    expected_instance_id: str | None = None,
    stock_repository: StockRepository | None = None,
) -> StockAssignmentMutationResult:
    """Remove a Stock assignment through the canonical application transaction."""

    repository = stock_repository or StockRepository(Path(project_root))
    clean_routines: list[str] = []
    seen: set[str] = set()
    for routine in routines:
        routine_name = str(routine or "").strip()
        if routine_name and routine_name not in seen:
            clean_routines.append(routine_name)
            seen.add(routine_name)
    if clean_routines:
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(
                False,
                reason_code="INVALID_UNASSIGN_REQUEST",
                error="instance assignment requires update_stock_routine_instance()",
            ),
        )

    path = repository.resolve_stock_dir(stock_code, stock_name)
    if not path.exists():
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(False, reason_code="STOCK_NOT_FOUND"),
        )

    config_path = path / "config.json"
    config = read_json_dict(config_path)
    if not config_path.is_file() or not isinstance(config, dict):
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(False, reason_code="CONFIG_NOT_FOUND"),
        )
    before_assignment = stock_repository_module._routine_assignment(config)
    before_instance_id = before_assignment["routine_instance_id"]
    expected = before_instance_id if expected_instance_id is None else expected_instance_id
    transaction = execute_assignment_transaction_foundation(
        repository.project_root,
        normalize_stock_code(stock_code),
        stock_name=stock_name,
        target_instance_id="",
        target_routine_type="",
        expected_instance_id=expected,
        changed_at=stock_repository_module.now_text(),
        reason="STOCK_ASSIGNMENT_REMOVED",
        source="STOCK_REPOSITORY",
        stock_repository=repository,
    )
    result = StockAssignmentMutationResult(
        transaction.success,
        changed=transaction.changed,
        reason_code=transaction.error_code
        or ("OK" if transaction.success else "TRANSACTION_FAILED"),
        transaction_id=transaction.transaction_id,
        assignment_before=before_instance_id,
        assignment_after="",
        reconciliation_required=transaction.reconciliation_required,
        error=transaction.error,
    )
    _remember_stock_assignment_result(repository, result)
    if not result.ok:
        return result
    saved = read_json_dict(config_path)
    saved_assignment = stock_repository_module._routine_assignment(saved)
    if saved_assignment["routine_instance_id"]:
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(
                False,
                changed=result.changed,
                reason_code="READ_BACK_FAILED",
                transaction_id=result.transaction_id,
                assignment_before=before_instance_id,
                assignment_after=saved_assignment["routine_instance_id"],
                reconciliation_required=True,
                error="assignment identity was not cleared",
            ),
        )
    if result.changed:
        stock_repository_module._append_routine_changed(
            code=stock_code,
            name=stock_name,
            before=before_assignment,
            after=saved_assignment,
        )
    return result


def assign_stock_routine(
    project_root: Path | str,
    stock_code: str,
    stock_name: str,
    *,
    instance_id: str,
    instance_name: str,
    definition_id: str,
    routine_type: str,
    expected_instance_id: str | None = None,
    stock_repository: StockRepository | None = None,
) -> StockAssignmentMutationResult:
    """Assign or reassign a Stock through the canonical application transaction."""

    repository = stock_repository or StockRepository(Path(project_root))
    clean_instance_id = str(instance_id or "").strip()
    clean_instance_name = str(instance_name or "").strip()
    clean_definition_id = str(definition_id or "").strip()
    clean_routine_type = str(routine_type or "").strip()
    if not all(
        (
            clean_instance_id,
            clean_instance_name,
            clean_definition_id,
            clean_routine_type,
        )
    ):
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(False, reason_code="INVALID_TARGET"),
        )

    path = repository.resolve_stock_dir(stock_code, stock_name)
    if not path.exists():
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(False, reason_code="STOCK_NOT_FOUND"),
        )
    config_path = path / "config.json"
    config = read_json_dict(config_path)
    if not config_path.is_file() or not isinstance(config, dict):
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(False, reason_code="CONFIG_NOT_FOUND"),
        )
    before_assignment = stock_repository_module._routine_assignment(config)
    before_instance_id = before_assignment["routine_instance_id"]
    expected = before_instance_id if expected_instance_id is None else expected_instance_id
    transaction = execute_assignment_transaction_foundation(
        repository.project_root,
        normalize_stock_code(stock_code),
        stock_name=stock_name,
        target_instance_id=clean_instance_id,
        target_routine_type=clean_routine_type,
        expected_instance_id=expected,
        changed_at=stock_repository_module.now_text(),
        reason="STOCK_ASSIGNMENT_CHANGED",
        source="STOCK_REPOSITORY",
        stock_repository=repository,
    )
    result = StockAssignmentMutationResult(
        transaction.success,
        changed=transaction.changed,
        reason_code=transaction.error_code
        or ("OK" if transaction.success else "TRANSACTION_FAILED"),
        transaction_id=transaction.transaction_id,
        assignment_before=before_instance_id,
        assignment_after=clean_instance_id,
        reconciliation_required=transaction.reconciliation_required,
        error=transaction.error,
    )
    _remember_stock_assignment_result(repository, result)
    if not result.ok:
        return result
    saved = read_json_dict(config_path)
    saved_assignment = stock_repository_module._routine_assignment(saved)
    if saved_assignment["routine_instance_id"] != clean_instance_id:
        return _remember_stock_assignment_result(
            repository,
            StockAssignmentMutationResult(
                False,
                changed=result.changed,
                reason_code="READ_BACK_FAILED",
                transaction_id=result.transaction_id,
                assignment_before=before_instance_id,
                assignment_after=saved_assignment["routine_instance_id"],
                reconciliation_required=True,
                error="assignment identity read-back mismatch",
            ),
        )
    if result.changed:
        stock_repository_module._append_routine_changed(
            code=stock_code,
            name=stock_name,
            before=before_assignment,
            after=saved_assignment,
        )
    return result


def _reconciliation_terminal_state(
    journal_state: str,
    *,
    config_identity: dict[str, Any] | None,
    episode_identity: dict[str, Any] | None,
    before_assignment_identity: dict[str, Any],
    before_episode_identity: dict[str, Any] | None,
    target_assignment_identity: dict[str, Any],
) -> tuple[str, str, bool]:
    config_key = _identity_key(config_identity)
    episode_key = _identity_key(episode_identity)
    before_config_key = _identity_key(before_assignment_identity)
    before_episode_key = _identity_key(before_episode_identity)
    target_key = _identity_key(target_assignment_identity)
    if config_key == target_key and episode_key == target_key:
        return ASSIGNMENT_TRANSACTION_COMMITTED, "CONSISTENT_TARGET", False
    if config_key == before_config_key and episode_key == before_episode_key:
        state = (
            ASSIGNMENT_TRANSACTION_ABORTED
            if journal_state == ASSIGNMENT_TRANSACTION_PREPARED
            else ASSIGNMENT_TRANSACTION_ROLLED_BACK
        )
        return state, "NO_MUTATION", False
    return (
        ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
        "CONFIG_EPISODE_MISMATCH",
        True,
    )


def reconcile_incomplete_assignment_transactions(
    project_root: Path | str,
    *,
    episode_repository: CanonicalAssignmentEpisodeRepository | None = None,
    journal_repository: AssignmentTransactionJournalRepository | None = None,
) -> tuple[AssignmentReconciliationResult, ...]:
    """Classify unfinished journals without choosing a winning persistence side."""

    root = Path(project_root)
    episode_repo = episode_repository or CanonicalAssignmentEpisodeRepository(root)
    journal_repo = journal_repository or AssignmentTransactionJournalRepository(
        root,
        episodes_repository=episode_repo,
    )
    stock_repo = StockRepository(root)
    results: list[AssignmentReconciliationResult] = []
    scan = journal_repo.scan_incomplete()
    for issue in scan.issues:
        results.append(
            AssignmentReconciliationResult(
                transaction_id=issue.transaction_id,
                stock_code=issue.stock_code,
                previous_state="INVALID",
                terminal_state=ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
                classification="INVALID_JOURNAL",
                config_identity=None,
                episode_identity=None,
                review_required=True,
                reason=issue.reason,
            )
        )

    journals_by_stock: dict[str, list[dict[str, Any]]] = {}
    for journal in scan.records:
        journals_by_stock.setdefault(str(journal["stock_code"]), []).append(journal)

    for code in sorted(journals_by_stock):
        stock_journals = journals_by_stock[code]
        if len(stock_journals) > 1:
            with assignment_transaction_lock(code):
                for journal in stock_journals:
                    transaction_id = str(journal["transaction_id"])
                    previous_state = str(journal["state"])
                    reason = (
                        f"multiple non-terminal assignment journals for {code}: "
                        f"{len(stock_journals)}"
                    )
                    terminal_state = ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
                    if previous_state != terminal_state:
                        try:
                            journal_repo.transition(
                                code,
                                transaction_id,
                                terminal_state,
                                failure_stage="STARTUP_RECONCILIATION",
                                reason=reason,
                            )
                        except Exception as exc:
                            terminal_state = previous_state
                            reason = f"{reason}; journal terminalization failed: {exc}"
                    results.append(
                        AssignmentReconciliationResult(
                            transaction_id=transaction_id,
                            stock_code=code,
                            previous_state=previous_state,
                            terminal_state=terminal_state,
                            classification="DUPLICATE_NON_TERMINAL_JOURNAL",
                            config_identity=None,
                            episode_identity=None,
                            review_required=True,
                            reason=reason,
                        )
                    )
            continue

        journal = stock_journals[0]
        transaction_id = str(journal["transaction_id"])
        previous_state = str(journal["state"])
        config_identity: dict[str, str] | None = None
        episode_identity: dict[str, str] | None = None
        classification = ""
        reason = ""
        review_required = False
        with assignment_transaction_lock(code):
            try:
                before_identity = journal["before_assignment_identity"]
                before_episode_identity = journal.get("before_episode_identity")
                target_identity = journal["target_assignment_identity"]
                if not _dependencies_available(root, before_identity) or not _dependencies_available(
                    root, target_identity
                ):
                    raise LookupError("required RoutineInstance or Group does not exist")
                if (
                    before_episode_identity is not None
                    and _identity_key(before_episode_identity) != _identity_key(before_identity)
                ):
                    raise ValueError("journal before config and episode identities differ")
                config_path = stock_repo.resolve_stock_dir(code) / "config.json"
                config = _read_config_object(config_path)
                config_identity = _target_identity(assignment_target_from_config(root, config))
                episode_identity = _episode_identity(episode_repo, code)
                terminal_state, classification, review_required = (
                    _reconciliation_terminal_state(
                        previous_state,
                        config_identity=config_identity,
                        episode_identity=episode_identity,
                        before_assignment_identity=before_identity,
                        before_episode_identity=before_episode_identity,
                        target_assignment_identity=target_identity,
                    )
                )
                reason = classification
            except LookupError as exc:
                terminal_state = ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
                classification = "MISSING_DEPENDENCY"
                review_required = True
                reason = str(exc)
            except Exception as exc:
                terminal_state = ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED
                classification = "INVALID_PERSISTENCE_EVIDENCE"
                review_required = True
                reason = str(exc)

            if previous_state != terminal_state:
                try:
                    journal_repo.transition(
                        code,
                        transaction_id,
                        terminal_state,
                        failure_stage=(
                            "STARTUP_RECONCILIATION" if review_required else ""
                        ),
                        reason=reason,
                    )
                except Exception as exc:
                    terminal_state = previous_state
                    classification = "JOURNAL_TERMINALIZATION_FAILED"
                    review_required = True
                    reason = str(exc)
            results.append(
                AssignmentReconciliationResult(
                    transaction_id=transaction_id,
                    stock_code=code,
                    previous_state=previous_state,
                    terminal_state=terminal_state,
                    classification=classification,
                    config_identity=config_identity,
                    episode_identity=episode_identity,
                    review_required=review_required,
                    reason=reason,
                )
            )
    return tuple(results)
