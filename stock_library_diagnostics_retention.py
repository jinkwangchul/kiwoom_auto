# -*- coding: utf-8 -*-
"""Retention safety boundary for Stock Library diagnostic snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Iterable
import uuid

from stock_library_master_diagnostics import DIAGNOSTIC_SCHEMA_VERSION


ACTION_KEEP = "KEEP"
ACTION_PROTECTED = "PROTECTED"
ACTION_ROTATE_CANDIDATE = "ROTATE_CANDIDATE"
ACTION_SKIP_UNCERTAIN = "SKIP_UNCERTAIN"
PURGE_EXISTING_NORMAL_DIAGNOSTICS = (
    "PURGE_EXISTING_NORMAL_STOCK_LIBRARY_DIAGNOSTICS"
)

EXECUTION_DELETED = "DELETED"
EXECUTION_SKIPPED_CHANGED = "SKIPPED_CHANGED"
EXECUTION_SKIPPED_PROTECTED = "SKIPPED_PROTECTED"
EXECUTION_SKIPPED_UNSAFE = "SKIPPED_UNSAFE"
EXECUTION_FAILED_IO = "FAILED_IO"
EXECUTION_DRY_RUN_ONLY = "DRY_RUN_ONLY"

AUTOMATIC_RUN_COMPLETED = "COMPLETED"
AUTOMATIC_RUN_NO_CANDIDATES = "NO_CANDIDATES"
AUTOMATIC_RUN_ALREADY_ATTEMPTED = "ALREADY_ATTEMPTED"
AUTOMATIC_RUN_INVALID_SESSION = "INVALID_SESSION"
AUTOMATIC_RUN_FAILED = "FAILED"

DIAGNOSTIC_TYPE = DIAGNOSTIC_SCHEMA_VERSION
DIAGNOSTIC_SOURCE = "KIWOOM_OPENAPI_MASTER"
PLAN_SCHEMA_VERSION = "stock_library_diagnostic_retention_plan_v1"
AUTHORIZATION_SCHEMA_VERSION = "production_diagnostics_retention_authorization_v1"
AUTHORIZATION_PURPOSE = "PRODUCTION_STOCK_LIBRARY_DIAGNOSTICS_RETENTION"
AUTOMATIC_AUTHORITY_SCHEMA_VERSION = (
    "automatic_stock_library_diagnostics_retention_authority_v1"
)
AUTOMATIC_AUTHORITY_PURPOSE = "AUTOMATIC_STOCK_LIBRARY_INCIDENT_RETENTION_7_DAY"
AUTOMATIC_RETENTION_AGE_DAYS = 7
DEFAULT_AUTHORIZATION_TTL_SECONDS = 600
MIN_AUTHORIZATION_TTL_SECONDS = 300
MAX_AUTHORIZATION_TTL_SECONDS = 900
_FILE_NAME_PATTERN = re.compile(
    r"^stock_library_invalid_codes_e(?P<epoch>0|[1-9][0-9]*)_"
    r"(?P<session_suffix>[0-9a-f]{10})\.json$"
)
_TEMP_SUFFIXES = (".tmp", ".partial", ".part")
_SUMMARY_COUNT_FIELDS = (
    "raw_count",
    "normalized_count",
    "valid_count",
    "invalid_count",
    "invalid_unique_count",
    "invalid_master_name_found",
    "invalid_master_name_missing",
    "raw_invalid_token_count",
    "duplicate_count",
)
_SUMMARY_MAP_FIELDS = ("invalid_by_reason", "raw_invalid_by_reason")


@dataclass(frozen=True)
class DiagnosticFileSignature:
    size: int
    mtime_ns: int
    sha256: str
    stable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "stable": self.stable,
        }


@dataclass(frozen=True)
class StockLibraryDiagnosticScanEntry:
    path: str
    size: int
    mtime_ns: int
    epoch: int | None
    session_suffix: str
    diagnostic_type: str
    parsed_payload_version: str
    captured_at: str
    invalid_count: int | None
    current: bool
    open_or_active_target: bool
    temporary: bool
    signature: DiagnosticFileSignature
    scan_status: str
    classification_reason: str
    semantic_hash: str


@dataclass(frozen=True)
class StockLibraryDiagnosticScanResult:
    root: str
    current_session_known: bool
    entries: tuple[StockLibraryDiagnosticScanEntry, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class StockLibraryDiagnosticRetentionPolicy:
    retention_age_days: int = 7
    max_total_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.retention_age_days < 0:
            raise ValueError("retention_age_days must be non-negative")
        if self.max_total_bytes is not None and self.max_total_bytes < 0:
            raise ValueError("max_total_bytes must be non-negative")


DEFAULT_RETENTION_POLICY = StockLibraryDiagnosticRetentionPolicy()


@dataclass(frozen=True)
class ProductionDiagnosticsRetentionAuthorization:
    version: str
    authorization_id: str
    diagnostics_root: str
    plan_signature: str
    candidate_count: int
    candidate_bytes: int
    issued_at: str
    expires_at: str
    current_session_id: str
    current_connection_epoch: int | None
    purpose: str
    one_shot: bool
    authorization_signature: str


@dataclass(frozen=True)
class AutomaticStockLibraryDiagnosticsRetentionAuthority:
    version: str
    authority_id: str
    diagnostics_root: str
    plan_signature: str
    candidate_count: int
    candidate_bytes: int
    issued_at: str
    expires_at: str
    current_session_id: str
    current_connection_epoch: int
    retention_age_days: int
    purpose: str
    one_shot: bool
    authority_signature: str


@dataclass
class _AuthorizationState:
    authorization: ProductionDiagnosticsRetentionAuthorization
    consumed: bool = False


@dataclass
class _AutomaticAuthorityState:
    authority: AutomaticStockLibraryDiagnosticsRetentionAuthority
    consumed: bool = False


_AUTHORIZATION_STATES: dict[str, _AuthorizationState] = {}
_AUTHORIZATION_LOCK = threading.Lock()
_AUTOMATIC_AUTHORITY_STATES: dict[str, _AutomaticAuthorityState] = {}
_AUTOMATIC_AUTHORITY_LOCK = threading.Lock()
_AUTOMATIC_ATTEMPTED_SESSION_KEYS: set[tuple[str, int, str]] = set()
_AUTOMATIC_ATTEMPT_LOCK = threading.Lock()


def _plan_integrity_signature(plan: dict[str, object]) -> str:
    payload = dict(plan)
    payload.pop("plan_signature", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_temporary_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered.endswith(_TEMP_SUFFIXES) or lowered.endswith("~")


def _session_suffix(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:10]


def _parse_aware_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _read_stable_bytes(path: Path) -> tuple[bytes, DiagnosticFileSignature]:
    before = path.stat()
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            chunks.append(chunk)
    after = path.stat()
    stable = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    return b"".join(chunks), DiagnosticFileSignature(
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=digest.hexdigest(),
        stable=stable,
    )


def _invalid_entry(
    *,
    relative_path: str,
    signature: DiagnosticFileSignature,
    reason: str,
    temporary: bool = False,
) -> StockLibraryDiagnosticScanEntry:
    return StockLibraryDiagnosticScanEntry(
        path=relative_path,
        size=signature.size,
        mtime_ns=signature.mtime_ns,
        epoch=None,
        session_suffix="",
        diagnostic_type="UNKNOWN",
        parsed_payload_version="",
        captured_at="",
        invalid_count=None,
        current=False,
        open_or_active_target=False,
        temporary=temporary,
        signature=signature,
        scan_status="UNCERTAIN",
        classification_reason=reason,
        semantic_hash="",
    )


def _validate_payload(
    payload: object,
    *,
    epoch: int,
    session_suffix: str,
) -> tuple[bool, str, str, int | None, str, str]:
    if not isinstance(payload, dict):
        return False, "PAYLOAD_NOT_OBJECT", "", None, "", ""
    version = str(payload.get("schema_version") or "")
    if version != DIAGNOSTIC_SCHEMA_VERSION:
        return False, "PAYLOAD_VERSION_UNSUPPORTED", version, None, "", ""
    if str(payload.get("source") or "") != DIAGNOSTIC_SOURCE:
        return False, "PAYLOAD_SOURCE_UNEXPECTED", version, None, "", ""
    payload_epoch = _nonnegative_int(payload.get("connection_epoch"))
    session_id = str(payload.get("login_session_id") or "")
    if payload_epoch != epoch or not session_id or _session_suffix(session_id) != session_suffix:
        return False, "PAYLOAD_SESSION_IDENTITY_MISMATCH", version, None, "", ""
    captured_at = str(payload.get("captured_at") or "")
    if _parse_aware_timestamp(captured_at) is None:
        return False, "PAYLOAD_CAPTURE_TIME_INVALID", version, None, "", ""
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return False, "PAYLOAD_SUMMARY_INVALID", version, None, captured_at, ""
    counts = {field: _nonnegative_int(summary.get(field)) for field in _SUMMARY_COUNT_FIELDS}
    if any(value is None for value in counts.values()):
        return False, "PAYLOAD_SUMMARY_COUNT_INVALID", version, None, captured_at, ""
    if any(not isinstance(summary.get(field), dict) for field in _SUMMARY_MAP_FIELDS):
        return False, "PAYLOAD_SUMMARY_MAP_INVALID", version, None, captured_at, ""
    invalid_items = payload.get("invalid_items")
    markets = payload.get("markets")
    if not isinstance(invalid_items, list) or not all(
        isinstance(item, dict) for item in invalid_items
    ):
        return False, "PAYLOAD_DIAGNOSTIC_COLLECTION_INVALID", version, None, captured_at, ""
    invalid_count = counts["invalid_count"]
    assert invalid_count is not None
    if markets is not None and not isinstance(markets, dict):
        return False, "PAYLOAD_DIAGNOSTIC_COLLECTION_INVALID", version, None, captured_at, ""
    if invalid_count == 0 and not isinstance(markets, dict):
        return False, "PAYLOAD_NORMAL_LEGACY_MARKETS_MISSING", version, None, captured_at, ""
    if len(invalid_items) != invalid_count:
        return False, "PAYLOAD_INVALID_COUNT_MISMATCH", version, None, captured_at, ""
    semantic_payload = dict(payload)
    semantic_payload.pop("login_session_id", None)
    semantic_payload.pop("captured_at", None)
    semantic_hash = hashlib.sha256(
        json.dumps(
            semantic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        True,
        "VALID_STOCK_LIBRARY_DIAGNOSTIC",
        version,
        invalid_count,
        captured_at,
        semantic_hash,
    )


def _scan_regular_file(
    path: Path,
    *,
    root: Path,
    current_epoch: int | None,
    current_suffix: str,
    explicitly_protected: frozenset[Path],
) -> StockLibraryDiagnosticScanEntry:
    relative_path = path.relative_to(root).as_posix()
    temporary = _is_temporary_name(path.name)
    data, signature = _read_stable_bytes(path)
    if not signature.stable:
        return _invalid_entry(
            relative_path=relative_path,
            signature=signature,
            reason="FILE_CHANGED_DURING_SCAN",
            temporary=temporary,
        )
    if temporary:
        return _invalid_entry(
            relative_path=relative_path,
            signature=signature,
            reason="TEMPORARY_OR_PARTIAL_FILE",
            temporary=True,
        )
    match = _FILE_NAME_PATTERN.fullmatch(path.name)
    if match is None or path.parent != root:
        return _invalid_entry(
            relative_path=relative_path,
            signature=signature,
            reason="UNKNOWN_DIAGNOSTIC_TYPE",
        )
    epoch = int(match.group("epoch"))
    suffix = match.group("session_suffix")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        valid, reason, version, invalid_count, captured_at, semantic_hash = (
            False,
            "PAYLOAD_PARSE_FAILED",
            "",
            None,
            "",
            "",
        )
    else:
        valid, reason, version, invalid_count, captured_at, semantic_hash = _validate_payload(
            payload,
            epoch=epoch,
            session_suffix=suffix,
        )
    current = current_epoch == epoch and bool(current_suffix) and current_suffix == suffix
    return StockLibraryDiagnosticScanEntry(
        path=relative_path,
        size=signature.size,
        mtime_ns=signature.mtime_ns,
        epoch=epoch,
        session_suffix=suffix,
        diagnostic_type=DIAGNOSTIC_TYPE,
        parsed_payload_version=version,
        captured_at=captured_at,
        invalid_count=invalid_count,
        current=current,
        open_or_active_target=path in explicitly_protected,
        temporary=False,
        signature=signature,
        scan_status="VALID" if valid else "UNCERTAIN",
        classification_reason=reason,
        semantic_hash=semantic_hash,
    )


def _walk_files_without_following_symlinks(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        retained_directories: list[str] = []
        for name in dir_names:
            candidate = directory_path / name
            if candidate.is_symlink():
                paths.append(candidate)
            else:
                retained_directories.append(name)
        dir_names[:] = retained_directories
        paths.extend(directory_path / name for name in file_names)
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def scan_stock_library_diagnostics(
    root: str | Path,
    *,
    current_session_id: str | None = None,
    current_connection_epoch: int | None = None,
    protected_paths: Iterable[str | Path] = (),
) -> StockLibraryDiagnosticScanResult:
    """Scan diagnostics without following links or changing filesystem state."""

    root_path = Path(root)
    issues: list[str] = []
    try:
        if root_path.is_symlink():
            return StockLibraryDiagnosticScanResult(str(root_path), False, (), ("ROOT_IS_SYMLINK",))
        root_resolved = root_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return StockLibraryDiagnosticScanResult(str(root_path), False, (), ("ROOT_UNAVAILABLE",))
    if not root_resolved.is_dir():
        return StockLibraryDiagnosticScanResult(str(root_resolved), False, (), ("ROOT_NOT_DIRECTORY",))

    current_session_known = current_session_id is not None
    current_suffix = ""
    current_epoch: int | None = None
    if current_session_id:
        if (
            isinstance(current_connection_epoch, bool)
            or not isinstance(current_connection_epoch, int)
            or current_connection_epoch < 0
        ):
            current_session_known = False
            issues.append("CURRENT_SESSION_IDENTITY_INCOMPLETE")
        else:
            current_epoch = current_connection_epoch
            current_suffix = _session_suffix(current_session_id)

    protected: set[Path] = set()
    for item in protected_paths:
        try:
            resolved = Path(item).resolve(strict=False)
        except (OSError, RuntimeError):
            issues.append("PROTECTED_PATH_INVALID")
            continue
        if _inside_root(resolved, root_resolved):
            protected.add(resolved)
        else:
            issues.append("PROTECTED_PATH_OUTSIDE_ROOT")

    entries: list[StockLibraryDiagnosticScanEntry] = []
    for path in _walk_files_without_following_symlinks(root_resolved):
        relative_path = path.relative_to(root_resolved).as_posix()
        try:
            if path.is_symlink():
                stat = path.lstat()
                entries.append(
                    _invalid_entry(
                        relative_path=relative_path,
                        signature=DiagnosticFileSignature(stat.st_size, stat.st_mtime_ns, "", True),
                        reason="SYMLINK_NOT_FOLLOWED",
                    )
                )
                continue
            resolved = path.resolve(strict=True)
            if not _inside_root(resolved, root_resolved):
                stat = path.stat()
                entries.append(
                    _invalid_entry(
                        relative_path=relative_path,
                        signature=DiagnosticFileSignature(stat.st_size, stat.st_mtime_ns, "", True),
                        reason="PATH_OUTSIDE_ROOT",
                    )
                )
                continue
            if not resolved.is_file():
                continue
            entries.append(
                _scan_regular_file(
                    resolved,
                    root=root_resolved,
                    current_epoch=current_epoch,
                    current_suffix=current_suffix,
                    explicitly_protected=frozenset(protected),
                )
            )
        except (OSError, RuntimeError) as exc:
            issues.append(f"SCAN_FAILED:{relative_path}:{type(exc).__name__}")

    entries.sort(key=lambda entry: (entry.mtime_ns, entry.path))
    return StockLibraryDiagnosticScanResult(
        root=str(root_resolved),
        current_session_known=current_session_known,
        entries=tuple(entries),
        issues=tuple(issues),
    )


def _entry_age_days(entry: StockLibraryDiagnosticScanEntry, now: datetime) -> float | None:
    captured = _parse_aware_timestamp(entry.captured_at)
    if captured is None:
        return None
    seconds = max(0.0, (now - captured).total_seconds())
    return seconds / 86400.0


def _entry_result(
    entry: StockLibraryDiagnosticScanEntry,
    *,
    action: str,
    reason: str,
    age_days: float | None,
) -> dict[str, object]:
    return {
        "path": entry.path,
        "action": action,
        "reason": reason,
        "size": entry.size,
        "mtime_ns": entry.mtime_ns,
        "age_days": age_days,
        "epoch": entry.epoch,
        "session_suffix": entry.session_suffix,
        "diagnostic_type": entry.diagnostic_type,
        "parsed_payload_version": entry.parsed_payload_version,
        "captured_at": entry.captured_at,
        "invalid_count": entry.invalid_count,
        "current": entry.current,
        "open_or_active_target": entry.open_or_active_target,
        "temporary": entry.temporary,
        "semantic_hash": entry.semantic_hash,
        "signature": entry.signature.as_dict(),
    }


def plan_stock_library_diagnostic_retention(
    root: str | Path,
    *,
    policy: StockLibraryDiagnosticRetentionPolicy = DEFAULT_RETENTION_POLICY,
    current_session_id: str | None = None,
    current_connection_epoch: int | None = None,
    protected_paths: Iterable[str | Path] = (),
    now: datetime | None = None,
) -> dict[str, object]:
    """Return a deterministic retention plan; no mutation operation exists here."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current_time = current_time.astimezone(timezone.utc)
    scan = scan_stock_library_diagnostics(
        root,
        current_session_id=current_session_id,
        current_connection_epoch=current_connection_epoch,
        protected_paths=protected_paths,
    )

    planned_entries: list[dict[str, object]] = []
    for entry in scan.entries:
        age_days = _entry_age_days(entry, current_time)
        if entry.temporary:
            action, reason = ACTION_PROTECTED, "TEMPORARY_OR_PARTIAL_FILE"
        elif entry.current:
            action, reason = ACTION_PROTECTED, "CURRENT_SESSION"
        elif entry.open_or_active_target:
            action, reason = ACTION_PROTECTED, "EXPLICIT_ACTIVE_TARGET"
        elif entry.scan_status != "VALID":
            action, reason = ACTION_SKIP_UNCERTAIN, entry.classification_reason
        elif not scan.current_session_known:
            action, reason = ACTION_SKIP_UNCERTAIN, "CURRENT_SESSION_UNKNOWN"
        elif age_days is not None and age_days <= policy.retention_age_days:
            action, reason = ACTION_KEEP, "RECENT_AGE"
        else:
            action, reason = (
                ACTION_ROTATE_CANDIDATE,
                "CLOSED_INCIDENT_OUTSIDE_RETENTION"
                if entry.invalid_count is not None and entry.invalid_count > 0
                else "CLOSED_NORMAL_OUTSIDE_RETENTION",
            )
        planned_entries.append(
            _entry_result(entry, action=action, reason=reason, age_days=age_days)
        )

    totals = {
        action: {
            "files": sum(entry["action"] == action for entry in planned_entries),
            "bytes": sum(int(entry["size"]) for entry in planned_entries if entry["action"] == action),
        }
        for action in (
            ACTION_KEEP,
            ACTION_PROTECTED,
            ACTION_ROTATE_CANDIDATE,
            ACTION_SKIP_UNCERTAIN,
        )
    }
    semantic_groups: dict[str, list[StockLibraryDiagnosticScanEntry]] = {}
    for entry in scan.entries:
        if entry.semantic_hash:
            semantic_groups.setdefault(entry.semantic_hash, []).append(entry)
    semantic_duplicate_files = sum(max(0, len(group) - 1) for group in semantic_groups.values())
    semantic_duplicate_bytes = sum(
        sum(entry.size for entry in group) - max(entry.size for entry in group)
        for group in semantic_groups.values()
        if len(group) > 1
    )
    total_bytes = sum(entry.size for entry in scan.entries)
    size_cap = policy.max_total_bytes
    plan: dict[str, object] = {
        "plan_version": PLAN_SCHEMA_VERSION,
        "dry_run": True,
        "mutation_supported": False,
        "root": scan.root,
        "policy": {
            "retention_age_days": policy.retention_age_days,
            "max_total_bytes": size_cap,
        },
        "scan_issues": list(scan.issues),
        "current_session_known": scan.current_session_known,
        "total_files": len(scan.entries),
        "total_bytes": total_bytes,
        "keep_files": totals[ACTION_KEEP]["files"],
        "keep_bytes": totals[ACTION_KEEP]["bytes"],
        "protected_files": totals[ACTION_PROTECTED]["files"],
        "protected_bytes": totals[ACTION_PROTECTED]["bytes"],
        "rotate_candidate_files": totals[ACTION_ROTATE_CANDIDATE]["files"],
        "rotate_candidate_bytes": totals[ACTION_ROTATE_CANDIDATE]["bytes"],
        "skip_uncertain_files": totals[ACTION_SKIP_UNCERTAIN]["files"],
        "skip_uncertain_bytes": totals[ACTION_SKIP_UNCERTAIN]["bytes"],
        "estimated_reclaimable_bytes": totals[ACTION_ROTATE_CANDIDATE]["bytes"],
        "size_cap_exceeded": size_cap is not None and total_bytes > size_cap,
        "size_cap_excess_bytes": max(0, total_bytes - size_cap) if size_cap is not None else 0,
        "semantic_payload_count": len(semantic_groups),
        "semantic_duplicate_files": semantic_duplicate_files,
        "semantic_duplicate_bytes": semantic_duplicate_bytes,
        "entries": planned_entries,
    }
    plan["plan_signature"] = _plan_integrity_signature(plan)
    return plan


def plan_existing_normal_stock_library_diagnostics_purge(
    root: str | Path,
    *,
    current_session_id: str | None,
    current_connection_epoch: int | None = None,
    protected_paths: Iterable[str | Path] = (),
    writer_active: bool | None,
) -> dict[str, object]:
    """Plan one explicit purge of recognized, non-incident legacy snapshots."""

    if writer_active is not False:
        raise ValueError("PURGE_REQUIRES_CONFIRMED_INACTIVE_WRITER")
    scan = scan_stock_library_diagnostics(
        root,
        current_session_id=current_session_id,
        current_connection_epoch=current_connection_epoch,
        protected_paths=protected_paths,
    )
    planned_entries: list[dict[str, object]] = []
    for entry in scan.entries:
        age_days = None
        if entry.temporary:
            action, reason = ACTION_PROTECTED, "TEMPORARY_OR_PARTIAL_FILE"
        elif entry.current:
            action, reason = ACTION_PROTECTED, "CURRENT_SESSION"
        elif entry.open_or_active_target:
            action, reason = ACTION_PROTECTED, "EXPLICIT_ACTIVE_TARGET"
        elif entry.scan_status != "VALID":
            action, reason = ACTION_SKIP_UNCERTAIN, entry.classification_reason
        elif entry.invalid_count is not None and entry.invalid_count > 0:
            action, reason = ACTION_PROTECTED, "INCIDENT_INVALID_CODES"
        elif not scan.current_session_known:
            action, reason = ACTION_SKIP_UNCERTAIN, "CURRENT_SESSION_UNKNOWN"
        elif entry.invalid_count == 0:
            action, reason = (
                ACTION_ROTATE_CANDIDATE,
                "EXISTING_NORMAL_DIAGNOSTIC_PURGE",
            )
        else:
            action, reason = ACTION_SKIP_UNCERTAIN, "INVALID_COUNT_UNKNOWN"
        planned_entries.append(
            _entry_result(entry, action=action, reason=reason, age_days=age_days)
        )

    def total(action: str, field: str) -> int:
        if field == "files":
            return sum(entry["action"] == action for entry in planned_entries)
        return sum(
            int(entry["size"])
            for entry in planned_entries
            if entry["action"] == action
        )

    plan: dict[str, object] = {
        "plan_version": PLAN_SCHEMA_VERSION,
        "operation": PURGE_EXISTING_NORMAL_DIAGNOSTICS,
        "dry_run": True,
        "mutation_supported": False,
        "root": scan.root,
        "policy": {
            "operation": PURGE_EXISTING_NORMAL_DIAGNOSTICS,
            "writer_active": False,
        },
        "scan_issues": list(scan.issues),
        "current_session_known": scan.current_session_known,
        "total_files": len(planned_entries),
        "total_bytes": sum(int(entry["size"]) for entry in planned_entries),
        "keep_files": 0,
        "keep_bytes": 0,
        "protected_files": total(ACTION_PROTECTED, "files"),
        "protected_bytes": total(ACTION_PROTECTED, "bytes"),
        "rotate_candidate_files": total(ACTION_ROTATE_CANDIDATE, "files"),
        "rotate_candidate_bytes": total(ACTION_ROTATE_CANDIDATE, "bytes"),
        "skip_uncertain_files": total(ACTION_SKIP_UNCERTAIN, "files"),
        "skip_uncertain_bytes": total(ACTION_SKIP_UNCERTAIN, "bytes"),
        "estimated_reclaimable_bytes": total(ACTION_ROTATE_CANDIDATE, "bytes"),
        "size_cap_exceeded": False,
        "size_cap_excess_bytes": 0,
        "semantic_payload_count": 0,
        "semantic_duplicate_files": 0,
        "semantic_duplicate_bytes": 0,
        "entries": planned_entries,
    }
    plan["plan_signature"] = _plan_integrity_signature(plan)
    return plan


def format_stock_library_diagnostic_retention_report(plan: dict[str, object]) -> str:
    """Format a compact human-readable dry-run report."""

    policy = plan.get("policy")
    retention_age_days = (
        policy.get("retention_age_days", 0) if isinstance(policy, dict) else 0
    )
    return "\n".join(
        (
            f"RETENTION_AGE: {retention_age_days} days",
            f"SCAN: {plan.get('total_files', 0)} files / {plan.get('total_bytes', 0)} bytes",
            f"KEEP: {plan.get('keep_files', 0)} files / {plan.get('keep_bytes', 0)} bytes",
            f"PROTECTED: {plan.get('protected_files', 0)} files / {plan.get('protected_bytes', 0)} bytes",
            "ROTATE_CANDIDATE: "
            f"{plan.get('rotate_candidate_files', 0)} files / {plan.get('rotate_candidate_bytes', 0)} bytes",
            "SKIP_UNCERTAIN: "
            f"{plan.get('skip_uncertain_files', 0)} files / {plan.get('skip_uncertain_bytes', 0)} bytes",
            f"ESTIMATED_RECLAIMABLE: {plan.get('estimated_reclaimable_bytes', 0)} bytes",
        )
    )


def _validated_plan_entries(
    plan: dict[str, object],
    *,
    root: Path,
) -> list[dict[str, object]]:
    if not isinstance(plan, dict):
        raise ValueError("PLAN_NOT_OBJECT")
    if plan.get("plan_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("PLAN_VERSION_UNSUPPORTED")
    if plan.get("dry_run") is not True or plan.get("mutation_supported") is not False:
        raise ValueError("PLAN_ORIGIN_INVALID")
    try:
        planned_root = Path(str(plan.get("root") or "")).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("PLAN_ROOT_UNAVAILABLE") from None
    if planned_root != root:
        raise ValueError("PLAN_ROOT_MISMATCH")

    entries = plan.get("entries")
    if not isinstance(entries, list):
        raise ValueError("PLAN_ENTRIES_INVALID")
    known_actions = {
        ACTION_KEEP,
        ACTION_PROTECTED,
        ACTION_ROTATE_CANDIDATE,
        ACTION_SKIP_UNCERTAIN,
    }
    seen_paths: set[str] = set()
    candidates = 0
    candidate_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("PLAN_ENTRY_INVALID")
        relative_path = entry.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("PLAN_ENTRY_PATH_INVALID")
        relative = Path(relative_path)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError("PLAN_ENTRY_PATH_UNSAFE")
        normalized_path = os.path.normcase(str(relative))
        if normalized_path in seen_paths:
            raise ValueError("DUPLICATE_PLAN_PATH")
        seen_paths.add(normalized_path)

        action = entry.get("action")
        if action not in known_actions:
            raise ValueError("PLAN_ENTRY_ACTION_INVALID")
        signature = entry.get("signature")
        if not isinstance(signature, dict):
            raise ValueError("PLAN_ENTRY_SIGNATURE_MISSING")
        size = signature.get("size")
        mtime_ns = signature.get("mtime_ns")
        sha256 = signature.get("sha256")
        stable = signature.get("stable")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or isinstance(mtime_ns, bool)
            or not isinstance(mtime_ns, int)
            or mtime_ns < 0
            or not isinstance(sha256, str)
            or not isinstance(stable, bool)
        ):
            raise ValueError("PLAN_ENTRY_SIGNATURE_INVALID")
        if action == ACTION_ROTATE_CANDIDATE:
            if not stable or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
                raise ValueError("PLAN_CANDIDATE_SIGNATURE_INVALID")
            candidates += 1
            candidate_bytes += size

    if plan.get("total_files") != len(entries):
        raise ValueError("PLAN_TOTAL_COUNT_MISMATCH")
    if plan.get("rotate_candidate_files") != candidates:
        raise ValueError("PLAN_CANDIDATE_COUNT_MISMATCH")
    if plan.get("rotate_candidate_bytes") != candidate_bytes:
        raise ValueError("PLAN_CANDIDATE_BYTES_MISMATCH")
    signature = plan.get("plan_signature")
    if not isinstance(signature, str) or not hmac.compare_digest(
        signature,
        _plan_integrity_signature(plan),
    ):
        raise ValueError("PLAN_SIGNATURE_MISMATCH")
    return entries


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _authorization_signature(
    *,
    version: str,
    authorization_id: str,
    diagnostics_root: str,
    plan_signature: str,
    candidate_count: int,
    candidate_bytes: int,
    issued_at: str,
    expires_at: str,
    current_session_id: str,
    current_connection_epoch: int | None,
    purpose: str,
    one_shot: bool,
) -> str:
    payload = {
        "version": version,
        "authorization_id": authorization_id,
        "diagnostics_root": diagnostics_root,
        "plan_signature": plan_signature,
        "candidate_count": candidate_count,
        "candidate_bytes": candidate_bytes,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "current_session_id": current_session_id,
        "current_connection_epoch": current_connection_epoch,
        "purpose": purpose,
        "one_shot": one_shot,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _expected_authorization_signature(
    authorization: ProductionDiagnosticsRetentionAuthorization,
) -> str:
    return _authorization_signature(
        version=authorization.version,
        authorization_id=authorization.authorization_id,
        diagnostics_root=authorization.diagnostics_root,
        plan_signature=authorization.plan_signature,
        candidate_count=authorization.candidate_count,
        candidate_bytes=authorization.candidate_bytes,
        issued_at=authorization.issued_at,
        expires_at=authorization.expires_at,
        current_session_id=authorization.current_session_id,
        current_connection_epoch=authorization.current_connection_epoch,
        purpose=authorization.purpose,
        one_shot=authorization.one_shot,
    )


def create_production_diagnostics_retention_authorization(
    plan: dict[str, object],
    *,
    root: str | Path,
    current_session_id: str | None,
    current_connection_epoch: int | None = None,
    ttl_seconds: int = DEFAULT_AUTHORIZATION_TTL_SECONDS,
) -> ProductionDiagnosticsRetentionAuthorization:
    """Issue an in-memory, one-shot capability for one exact retention plan."""

    root_path = Path(root)
    try:
        if root_path.is_symlink():
            raise ValueError("AUTHORIZATION_ROOT_IS_SYMLINK")
        root_resolved = root_path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("AUTHORIZATION_ROOT_UNAVAILABLE") from None
    if not root_resolved.is_dir():
        raise ValueError("AUTHORIZATION_ROOT_NOT_DIRECTORY")
    _validated_plan_entries(plan, root=root_resolved)
    if current_session_id is None:
        raise ValueError("AUTHORIZATION_CURRENT_SESSION_UNKNOWN")
    if current_session_id and (
        isinstance(current_connection_epoch, bool)
        or not isinstance(current_connection_epoch, int)
        or current_connection_epoch < 0
    ):
        raise ValueError("AUTHORIZATION_CURRENT_SESSION_IDENTITY_INCOMPLETE")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds < MIN_AUTHORIZATION_TTL_SECONDS
        or ttl_seconds > MAX_AUTHORIZATION_TTL_SECONDS
    ):
        raise ValueError("AUTHORIZATION_TTL_OUT_OF_RANGE")

    issued = _utc_now()
    expires = datetime.fromtimestamp(issued.timestamp() + ttl_seconds, timezone.utc)
    authorization_id = uuid.uuid4().hex
    fields = {
        "version": AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": authorization_id,
        "diagnostics_root": str(root_resolved),
        "plan_signature": str(plan["plan_signature"]),
        "candidate_count": int(plan["rotate_candidate_files"]),
        "candidate_bytes": int(plan["rotate_candidate_bytes"]),
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "current_session_id": current_session_id,
        "current_connection_epoch": current_connection_epoch,
        "purpose": AUTHORIZATION_PURPOSE,
        "one_shot": True,
    }
    authorization = ProductionDiagnosticsRetentionAuthorization(
        **fields,
        authorization_signature=_authorization_signature(**fields),
    )
    with _AUTHORIZATION_LOCK:
        _AUTHORIZATION_STATES[authorization_id] = _AuthorizationState(authorization)
    return authorization


def _authorization_state_name(
    authorization: ProductionDiagnosticsRetentionAuthorization,
) -> str:
    with _AUTHORIZATION_LOCK:
        state = _AUTHORIZATION_STATES.get(authorization.authorization_id)
        if state is None or state.authorization is not authorization:
            return "UNKNOWN"
        if state.consumed:
            return "CONSUMED"
    expires = _parse_aware_timestamp(authorization.expires_at)
    if expires is None or _utc_now() >= expires:
        return "EXPIRED"
    return "ISSUED"


def format_production_diagnostics_retention_authorization_report(
    authorization: ProductionDiagnosticsRetentionAuthorization,
) -> str:
    """Format non-secret authorization metadata for operator review."""

    return "\n".join(
        (
            f"AUTHORIZATION_ID: {authorization.authorization_id}",
            f"ROOT: {authorization.diagnostics_root}",
            f"PLAN_SIGNATURE: {authorization.plan_signature}",
            f"CANDIDATES: {authorization.candidate_count}",
            f"BYTES: {authorization.candidate_bytes}",
            f"ISSUED_AT: {authorization.issued_at}",
            f"EXPIRES_AT: {authorization.expires_at}",
            f"ONE_SHOT: {authorization.one_shot}",
            f"STATE: {_authorization_state_name(authorization)}",
        )
    )


def _validate_authorization(
    authorization: object,
    *,
    plan: dict[str, object],
    root: Path,
    current_session_id: str | None,
    current_connection_epoch: int | None,
) -> _AuthorizationState:
    if not isinstance(authorization, ProductionDiagnosticsRetentionAuthorization):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_REQUIRED")
    with _AUTHORIZATION_LOCK:
        state = _AUTHORIZATION_STATES.get(authorization.authorization_id)
        if state is None or state.authorization is not authorization:
            raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_UNKNOWN")
        if state.consumed:
            raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_CONSUMED")
    if authorization.version != AUTHORIZATION_SCHEMA_VERSION:
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_VERSION_INVALID")
    if authorization.purpose != AUTHORIZATION_PURPOSE or authorization.one_shot is not True:
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_PURPOSE_INVALID")
    if not hmac.compare_digest(
        authorization.authorization_signature,
        _expected_authorization_signature(authorization),
    ):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_SIGNATURE_INVALID")
    if authorization.diagnostics_root != str(root):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_ROOT_MISMATCH")
    if authorization.plan_signature != plan.get("plan_signature"):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_PLAN_MISMATCH")
    if authorization.candidate_count != plan.get("rotate_candidate_files"):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_COUNT_MISMATCH")
    if authorization.candidate_bytes != plan.get("rotate_candidate_bytes"):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_BYTES_MISMATCH")
    issued = _parse_aware_timestamp(authorization.issued_at)
    expires = _parse_aware_timestamp(authorization.expires_at)
    now = _utc_now()
    if issued is None or expires is None or now < issued or now >= expires:
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_EXPIRED")
    if (
        authorization.current_session_id != current_session_id
        or authorization.current_connection_epoch != current_connection_epoch
    ):
        raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_SESSION_MISMATCH")
    return state


def _consume_authorization(
    authorization: ProductionDiagnosticsRetentionAuthorization,
) -> None:
    with _AUTHORIZATION_LOCK:
        state = _AUTHORIZATION_STATES.get(authorization.authorization_id)
        if state is None or state.authorization is not authorization:
            raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_UNKNOWN")
        if state.consumed:
            raise PermissionError("PRODUCTION_DIAGNOSTICS_AUTHORIZATION_CONSUMED")
        state.consumed = True


def _is_production_diagnostics_root(root: Path) -> bool:
    production_root = (Path(__file__).resolve().parent / "runtime" / "diagnostics").resolve(
        strict=False
    )
    return root == production_root


def _automatic_authority_signature(
    *,
    version: str,
    authority_id: str,
    diagnostics_root: str,
    plan_signature: str,
    candidate_count: int,
    candidate_bytes: int,
    issued_at: str,
    expires_at: str,
    current_session_id: str,
    current_connection_epoch: int,
    retention_age_days: int,
    purpose: str,
    one_shot: bool,
) -> str:
    payload = {
        "version": version,
        "authority_id": authority_id,
        "diagnostics_root": diagnostics_root,
        "plan_signature": plan_signature,
        "candidate_count": candidate_count,
        "candidate_bytes": candidate_bytes,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "current_session_id": current_session_id,
        "current_connection_epoch": current_connection_epoch,
        "retention_age_days": retention_age_days,
        "purpose": purpose,
        "one_shot": one_shot,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _expected_automatic_authority_signature(
    authority: AutomaticStockLibraryDiagnosticsRetentionAuthority,
) -> str:
    return _automatic_authority_signature(
        version=authority.version,
        authority_id=authority.authority_id,
        diagnostics_root=authority.diagnostics_root,
        plan_signature=authority.plan_signature,
        candidate_count=authority.candidate_count,
        candidate_bytes=authority.candidate_bytes,
        issued_at=authority.issued_at,
        expires_at=authority.expires_at,
        current_session_id=authority.current_session_id,
        current_connection_epoch=authority.current_connection_epoch,
        retention_age_days=authority.retention_age_days,
        purpose=authority.purpose,
        one_shot=authority.one_shot,
    )


def _validate_automatic_retention_plan(plan: dict[str, object]) -> None:
    policy = plan.get("policy")
    if not isinstance(policy, dict) or policy != {
        "retention_age_days": AUTOMATIC_RETENTION_AGE_DAYS,
        "max_total_bytes": None,
    }:
        raise PermissionError("AUTOMATIC_RETENTION_POLICY_MISMATCH")
    if plan.get("operation") is not None:
        raise PermissionError("AUTOMATIC_RETENTION_OPERATION_INVALID")


def create_automatic_stock_library_diagnostics_retention_authority(
    plan: dict[str, object],
    *,
    root: str | Path,
    current_session_id: str,
    current_connection_epoch: int,
) -> AutomaticStockLibraryDiagnosticsRetentionAuthority:
    """Issue a process-local capability for one exact automatic seven-day plan."""

    root_path = Path(root)
    try:
        if root_path.is_symlink():
            raise PermissionError("AUTOMATIC_RETENTION_ROOT_IS_SYMLINK")
        root_resolved = root_path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PermissionError("AUTOMATIC_RETENTION_ROOT_UNAVAILABLE") from None
    if not root_resolved.is_dir() or not _is_production_diagnostics_root(root_resolved):
        raise PermissionError("AUTOMATIC_RETENTION_ROOT_NOT_CANONICAL")
    _validated_plan_entries(plan, root=root_resolved)
    _validate_automatic_retention_plan(plan)
    session_id = str(current_session_id or "").strip()
    if not session_id:
        raise PermissionError("AUTOMATIC_RETENTION_SESSION_UNKNOWN")
    if (
        isinstance(current_connection_epoch, bool)
        or not isinstance(current_connection_epoch, int)
        or current_connection_epoch < 0
    ):
        raise PermissionError("AUTOMATIC_RETENTION_SESSION_IDENTITY_INCOMPLETE")

    issued = _utc_now()
    expires = datetime.fromtimestamp(
        issued.timestamp() + DEFAULT_AUTHORIZATION_TTL_SECONDS,
        timezone.utc,
    )
    fields = {
        "version": AUTOMATIC_AUTHORITY_SCHEMA_VERSION,
        "authority_id": uuid.uuid4().hex,
        "diagnostics_root": str(root_resolved),
        "plan_signature": str(plan["plan_signature"]),
        "candidate_count": int(plan["rotate_candidate_files"]),
        "candidate_bytes": int(plan["rotate_candidate_bytes"]),
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "current_session_id": session_id,
        "current_connection_epoch": current_connection_epoch,
        "retention_age_days": AUTOMATIC_RETENTION_AGE_DAYS,
        "purpose": AUTOMATIC_AUTHORITY_PURPOSE,
        "one_shot": True,
    }
    authority = AutomaticStockLibraryDiagnosticsRetentionAuthority(
        **fields,
        authority_signature=_automatic_authority_signature(**fields),
    )
    with _AUTOMATIC_AUTHORITY_LOCK:
        _AUTOMATIC_AUTHORITY_STATES[authority.authority_id] = _AutomaticAuthorityState(
            authority
        )
    return authority


def _validate_automatic_authority(
    authority: object,
    *,
    plan: dict[str, object],
    root: Path,
    current_session_id: str | None,
    current_connection_epoch: int | None,
) -> _AutomaticAuthorityState:
    if not isinstance(
        authority,
        AutomaticStockLibraryDiagnosticsRetentionAuthority,
    ):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_REQUIRED")
    with _AUTOMATIC_AUTHORITY_LOCK:
        state = _AUTOMATIC_AUTHORITY_STATES.get(authority.authority_id)
        if state is None or state.authority is not authority:
            raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_UNKNOWN")
        if state.consumed:
            raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_CONSUMED")
    if (
        authority.version != AUTOMATIC_AUTHORITY_SCHEMA_VERSION
        or authority.purpose != AUTOMATIC_AUTHORITY_PURPOSE
        or authority.one_shot is not True
        or authority.retention_age_days != AUTOMATIC_RETENTION_AGE_DAYS
    ):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_PURPOSE_INVALID")
    if not hmac.compare_digest(
        authority.authority_signature,
        _expected_automatic_authority_signature(authority),
    ):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_SIGNATURE_INVALID")
    _validate_automatic_retention_plan(plan)
    if authority.diagnostics_root != str(root):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_ROOT_MISMATCH")
    if authority.plan_signature != plan.get("plan_signature"):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_PLAN_MISMATCH")
    if authority.candidate_count != plan.get("rotate_candidate_files"):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_COUNT_MISMATCH")
    if authority.candidate_bytes != plan.get("rotate_candidate_bytes"):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_BYTES_MISMATCH")
    issued = _parse_aware_timestamp(authority.issued_at)
    expires = _parse_aware_timestamp(authority.expires_at)
    now = _utc_now()
    if issued is None or expires is None or now < issued or now >= expires:
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_EXPIRED")
    if (
        authority.current_session_id != current_session_id
        or authority.current_connection_epoch != current_connection_epoch
    ):
        raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_SESSION_MISMATCH")
    return state


def _consume_automatic_authority(
    authority: AutomaticStockLibraryDiagnosticsRetentionAuthority,
) -> None:
    with _AUTOMATIC_AUTHORITY_LOCK:
        state = _AUTOMATIC_AUTHORITY_STATES.get(authority.authority_id)
        if state is None or state.authority is not authority:
            raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_UNKNOWN")
        if state.consumed:
            raise PermissionError("AUTOMATIC_RETENTION_AUTHORITY_CONSUMED")
        state.consumed = True


def _execution_entry(
    entry: dict[str, object],
    *,
    status: str,
    reason: str,
    observed_signature: DiagnosticFileSignature | None = None,
    error: OSError | None = None,
) -> dict[str, object]:
    planned_signature = entry["signature"]
    assert isinstance(planned_signature, dict)
    return {
        "path": entry["path"],
        "planned_action": entry["action"],
        "execution_status": status,
        "reason": reason,
        "planned_signature": dict(planned_signature),
        "observed_signature": (
            observed_signature.as_dict() if observed_signature is not None else None
        ),
        "size": (
            observed_signature.size
            if observed_signature is not None
            else int(planned_signature["size"])
        ),
        "error_type": type(error).__name__ if error is not None else "",
        "error_message": str(error) if error is not None else "",
    }


def _unlink_file(path: Path) -> None:
    path.unlink()


def execute_stock_library_diagnostic_retention(
    plan: dict[str, object],
    *,
    root: str | Path,
    current_session_id: str | None = None,
    current_connection_epoch: int | None = None,
    protected_paths: Iterable[str | Path] = (),
    dry_run: bool = False,
    authorization: ProductionDiagnosticsRetentionAuthorization | None = None,
    automatic_authority: AutomaticStockLibraryDiagnosticsRetentionAuthority | None = None,
) -> dict[str, object]:
    """Execute validated candidates one by one, or preview them without mutation."""

    root_path = Path(root)
    try:
        if root_path.is_symlink():
            raise ValueError("EXECUTION_ROOT_IS_SYMLINK")
        root_resolved = root_path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("EXECUTION_ROOT_UNAVAILABLE") from None
    if not root_resolved.is_dir():
        raise ValueError("EXECUTION_ROOT_NOT_DIRECTORY")
    entries = _validated_plan_entries(plan, root=root_resolved)
    production_root = _is_production_diagnostics_root(root_resolved)
    if authorization is not None and automatic_authority is not None:
        raise PermissionError("DIAGNOSTICS_RETENTION_AUTHORITY_AMBIGUOUS")
    manual_authorization_state: _AuthorizationState | None = None
    automatic_authority_state: _AutomaticAuthorityState | None = None
    if authorization is not None:
        manual_authorization_state = _validate_authorization(
            authorization,
            plan=plan,
            root=root_resolved,
            current_session_id=current_session_id,
            current_connection_epoch=current_connection_epoch,
        )
    elif automatic_authority is not None:
        automatic_authority_state = _validate_automatic_authority(
            automatic_authority,
            plan=plan,
            root=root_resolved,
            current_session_id=current_session_id,
            current_connection_epoch=current_connection_epoch,
        )
    elif production_root and not dry_run:
        raise PermissionError("PRODUCTION_DIAGNOSTICS_EXECUTION_FORBIDDEN")
    current_session_known = current_session_id is not None
    current_suffix = ""
    current_epoch: int | None = None
    if current_session_id:
        if (
            isinstance(current_connection_epoch, bool)
            or not isinstance(current_connection_epoch, int)
            or current_connection_epoch < 0
        ):
            current_session_known = False
        else:
            current_suffix = _session_suffix(current_session_id)
            current_epoch = current_connection_epoch

    protected: set[Path] = set()
    for item in protected_paths:
        try:
            resolved = Path(item).resolve(strict=False)
        except (OSError, RuntimeError):
            raise ValueError("EXECUTION_PROTECTED_PATH_INVALID") from None
        if not _inside_root(resolved, root_resolved):
            raise ValueError("EXECUTION_PROTECTED_PATH_OUTSIDE_ROOT")
        protected.add(resolved)

    results: list[dict[str, object]] = []
    authorization_consumed_for_run = False
    for entry in entries:
        if entry["action"] != ACTION_ROTATE_CANDIDATE:
            continue
        relative_path = str(entry["path"])
        target = root_resolved / Path(relative_path)
        planned_signature = entry["signature"]
        assert isinstance(planned_signature, dict)

        if target.is_symlink():
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_UNSAFE,
                    reason="SYMLINK_TARGET_FORBIDDEN",
                )
            )
            continue
        try:
            resolved = target.resolve(strict=True)
        except (OSError, RuntimeError):
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_MISSING_OR_UNAVAILABLE",
                )
            )
            continue
        if not _inside_root(resolved, root_resolved) or resolved.parent != root_resolved:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_UNSAFE,
                    reason="TARGET_OUTSIDE_CANONICAL_ROOT",
                )
            )
            continue
        if _is_temporary_name(resolved.name) or resolved in protected:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_PROTECTED,
                    reason=(
                        "TEMPORARY_OR_PARTIAL_FILE"
                        if _is_temporary_name(resolved.name)
                        else "EXPLICIT_ACTIVE_TARGET"
                    ),
                )
            )
            continue
        if not current_session_known:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_PROTECTED,
                    reason="CURRENT_SESSION_UNKNOWN",
                )
            )
            continue
        if _FILE_NAME_PATTERN.fullmatch(resolved.name) is None:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_FILENAME_CHANGED",
                )
            )
            continue

        try:
            observed = _scan_regular_file(
                resolved,
                root=root_resolved,
                current_epoch=current_epoch,
                current_suffix=current_suffix,
                explicitly_protected=frozenset(protected),
            )
        except (OSError, RuntimeError):
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_READ_FAILED",
                )
            )
            continue
        observed_signature = observed.signature
        if observed.current:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_PROTECTED,
                    reason="CURRENT_SESSION",
                    observed_signature=observed_signature,
                )
            )
            continue
        if observed.open_or_active_target or observed.temporary:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_PROTECTED,
                    reason=(
                        "EXPLICIT_ACTIVE_TARGET"
                        if observed.open_or_active_target
                        else "TEMPORARY_OR_PARTIAL_FILE"
                    ),
                    observed_signature=observed_signature,
                )
            )
            continue
        if observed.scan_status != "VALID":
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason=observed.classification_reason,
                    observed_signature=observed_signature,
                )
            )
            continue
        planned_invalid_count = entry.get("invalid_count")
        if (
            observed.invalid_count is not None
            and observed.invalid_count > 0
            and (
                isinstance(planned_invalid_count, bool)
                or not isinstance(planned_invalid_count, int)
                or planned_invalid_count <= 0
            )
        ):
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_PROTECTED,
                    reason="INCIDENT_INVALID_CODES",
                    observed_signature=observed_signature,
                )
            )
            continue
        signature_matches = (
            observed_signature.stable
            and observed_signature.size == planned_signature["size"]
            and observed_signature.mtime_ns == planned_signature["mtime_ns"]
            and observed_signature.sha256 == planned_signature["sha256"]
        )
        if not signature_matches:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_SIGNATURE_CHANGED",
                    observed_signature=observed_signature,
                )
            )
            continue
        if dry_run:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_DRY_RUN_ONLY,
                    reason="VALIDATED_CANDIDATE_PREVIEW",
                    observed_signature=observed_signature,
                )
            )
            continue

        try:
            final_stat = resolved.stat()
        except FileNotFoundError:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_MISSING_BEFORE_UNLINK",
                    observed_signature=observed_signature,
                )
            )
            continue
        except OSError as exc:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_FAILED_IO,
                    reason="UNLINK_FAILED",
                    observed_signature=observed_signature,
                    error=exc,
                )
            )
            continue
        if (
            final_stat.st_size != observed_signature.size
            or final_stat.st_mtime_ns != observed_signature.mtime_ns
        ):
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_CHANGED_BEFORE_UNLINK",
                    observed_signature=observed_signature,
                )
            )
            continue
        if (
            manual_authorization_state is not None
            and not authorization_consumed_for_run
        ):
            assert authorization is not None
            _consume_authorization(authorization)
            authorization_consumed_for_run = True
        elif (
            automatic_authority_state is not None
            and not authorization_consumed_for_run
        ):
            assert automatic_authority is not None
            _consume_automatic_authority(automatic_authority)
            authorization_consumed_for_run = True
        try:
            _unlink_file(resolved)
        except FileNotFoundError:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_SKIPPED_CHANGED,
                    reason="TARGET_MISSING_BEFORE_UNLINK",
                    observed_signature=observed_signature,
                )
            )
        except OSError as exc:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_FAILED_IO,
                    reason="UNLINK_FAILED",
                    observed_signature=observed_signature,
                    error=exc,
                )
            )
        else:
            results.append(
                _execution_entry(
                    entry,
                    status=EXECUTION_DELETED,
                    reason="VALIDATED_CANDIDATE_DELETED",
                    observed_signature=observed_signature,
                )
            )

    def count(status: str) -> int:
        return sum(result["execution_status"] == status for result in results)

    def byte_count(status: str) -> int:
        return sum(
            int(result["size"])
            for result in results
            if result["execution_status"] == status
        )

    return {
        "dry_run": bool(dry_run),
        "root": str(root_resolved),
        "planned_candidates": int(plan["rotate_candidate_files"]),
        "planned_bytes": int(plan["rotate_candidate_bytes"]),
        "deleted_files": count(EXECUTION_DELETED),
        "deleted_bytes": byte_count(EXECUTION_DELETED),
        "dry_run_only": count(EXECUTION_DRY_RUN_ONLY),
        "dry_run_bytes": byte_count(EXECUTION_DRY_RUN_ONLY),
        "skipped_changed": count(EXECUTION_SKIPPED_CHANGED),
        "skipped_protected": count(EXECUTION_SKIPPED_PROTECTED),
        "skipped_unsafe": count(EXECUTION_SKIPPED_UNSAFE),
        "failed_files": count(EXECUTION_FAILED_IO),
        "failed_bytes": byte_count(EXECUTION_FAILED_IO),
        "reclaimed_bytes": byte_count(EXECUTION_DELETED),
        "entries": results,
    }


def format_stock_library_diagnostic_retention_execution_report(
    result: dict[str, object],
) -> str:
    """Format aggregate executor outcomes without expanding target filenames."""

    return "\n".join(
        (
            f"PLANNED: {result.get('planned_candidates', 0)} files / {result.get('planned_bytes', 0)} bytes",
            f"DELETED: {result.get('deleted_files', 0)} files / {result.get('deleted_bytes', 0)} bytes",
            f"DRY_RUN_ONLY: {result.get('dry_run_only', 0)} files / {result.get('dry_run_bytes', 0)} bytes",
            f"SKIPPED_CHANGED: {result.get('skipped_changed', 0)} files",
            f"SKIPPED_PROTECTED: {result.get('skipped_protected', 0)} files",
            f"SKIPPED_UNSAFE: {result.get('skipped_unsafe', 0)} files",
            f"FAILED_IO: {result.get('failed_files', 0)} files / {result.get('failed_bytes', 0)} bytes",
            f"RECLAIMED_BYTES: {result.get('reclaimed_bytes', 0)}",
        )
    )


class StockLibraryDiagnosticsAutomaticRetention:
    """Run the canonical seven-day retention flow once per authenticated session."""

    def __init__(
        self,
        root: str | Path,
        *,
        event_writer: Callable[..., object] | None = None,
        policy: StockLibraryDiagnosticRetentionPolicy = DEFAULT_RETENTION_POLICY,
    ) -> None:
        if policy != StockLibraryDiagnosticRetentionPolicy(
            retention_age_days=AUTOMATIC_RETENTION_AGE_DAYS,
            max_total_bytes=None,
        ):
            raise ValueError("AUTOMATIC_RETENTION_POLICY_MUST_BE_SEVEN_DAYS")
        self.root = Path(root)
        self.event_writer = event_writer
        self.policy = policy

    @staticmethod
    def _empty_result(
        *,
        status: str,
        attempted: bool,
        session_id: str,
        connection_epoch: int | None,
    ) -> dict[str, object]:
        return {
            "status": status,
            "attempted": attempted,
            "current_session_id": session_id,
            "current_connection_epoch": connection_epoch,
            "retention_days": AUTOMATIC_RETENTION_AGE_DAYS,
            "candidate_count": 0,
            "candidate_bytes": 0,
            "deleted_count": 0,
            "deleted_bytes": 0,
            "skipped_changed": 0,
            "skipped_protected": 0,
            "skipped_unsafe": 0,
            "failed_io": 0,
            "event_recorded": False,
        }

    def _record_result_event(self, result: dict[str, object]) -> bool:
        writer = self.event_writer
        if not callable(writer):
            return False
        failed = result.get("status") == AUTOMATIC_RUN_FAILED
        partial = int(result.get("failed_io", 0) or 0) > 0
        event_type = (
            "STOCK_LIBRARY_DIAGNOSTICS_RETENTION_FAILED"
            if failed
            else "STOCK_LIBRARY_DIAGNOSTICS_RETENTION_COMPLETED"
        )
        reason_code = (
            "AUTOMATIC_RETENTION_FAILED"
            if failed
            else "AUTOMATIC_RETENTION_PARTIAL_IO"
            if partial
            else "AUTOMATIC_RETENTION_NO_CANDIDATES"
            if result.get("status") == AUTOMATIC_RUN_NO_CANDIDATES
            else "AUTOMATIC_RETENTION_COMPLETED"
        )
        details = {
            "attempted": bool(result.get("attempted")),
            "candidate_count": int(result.get("candidate_count", 0) or 0),
            "candidate_bytes": int(result.get("candidate_bytes", 0) or 0),
            "deleted_count": int(result.get("deleted_count", 0) or 0),
            "deleted_bytes": int(result.get("deleted_bytes", 0) or 0),
            "skipped_changed": int(result.get("skipped_changed", 0) or 0),
            "skipped_protected": int(result.get("skipped_protected", 0) or 0),
            "skipped_unsafe": int(result.get("skipped_unsafe", 0) or 0),
            "failed_io": int(result.get("failed_io", 0) or 0),
            "retention_days": AUTOMATIC_RETENTION_AGE_DAYS,
            "connection_epoch": result.get("current_connection_epoch"),
            "login_session_id": result.get("current_session_id"),
        }
        if failed:
            details["error_type"] = str(result.get("error_type") or "")
        try:
            writer(
                event_type,
                result="FAILED" if failed else "COMPLETED",
                severity="WARNING" if failed or partial else "INFO",
                source=(
                    "stock_library_diagnostics_retention."
                    "StockLibraryDiagnosticsAutomaticRetention"
                ),
                target_type="STOCK_LIBRARY_DIAGNOSTICS",
                target_id="runtime/diagnostics",
                target_name="Stock Library diagnostics",
                reason_code=reason_code,
                details=details,
            )
        except Exception:
            return False
        return True

    def run_for_session(
        self,
        *,
        current_session_id: str,
        current_connection_epoch: int,
        now: datetime | None = None,
    ) -> dict[str, object]:
        session_id = str(current_session_id or "").strip()
        valid_epoch = (
            not isinstance(current_connection_epoch, bool)
            and isinstance(current_connection_epoch, int)
            and current_connection_epoch >= 0
        )
        if not session_id or not valid_epoch:
            return self._empty_result(
                status=AUTOMATIC_RUN_INVALID_SESSION,
                attempted=False,
                session_id=session_id,
                connection_epoch=(
                    current_connection_epoch if valid_epoch else None
                ),
            )

        try:
            root_key = str(self.root.resolve(strict=False))
        except (OSError, RuntimeError):
            root_key = str(self.root)
        attempt_key = (root_key, current_connection_epoch, session_id)
        with _AUTOMATIC_ATTEMPT_LOCK:
            if attempt_key in _AUTOMATIC_ATTEMPTED_SESSION_KEYS:
                return self._empty_result(
                    status=AUTOMATIC_RUN_ALREADY_ATTEMPTED,
                    attempted=False,
                    session_id=session_id,
                    connection_epoch=current_connection_epoch,
                )
            _AUTOMATIC_ATTEMPTED_SESSION_KEYS.add(attempt_key)

        result = self._empty_result(
            status=AUTOMATIC_RUN_FAILED,
            attempted=True,
            session_id=session_id,
            connection_epoch=current_connection_epoch,
        )
        try:
            plan = plan_stock_library_diagnostic_retention(
                self.root,
                policy=self.policy,
                current_session_id=session_id,
                current_connection_epoch=current_connection_epoch,
                now=now,
            )
            result["candidate_count"] = int(plan["rotate_candidate_files"])
            result["candidate_bytes"] = int(plan["rotate_candidate_bytes"])
            if result["candidate_count"] == 0:
                result["status"] = AUTOMATIC_RUN_NO_CANDIDATES
            else:
                authority = (
                    create_automatic_stock_library_diagnostics_retention_authority(
                        plan,
                        root=self.root,
                        current_session_id=session_id,
                        current_connection_epoch=current_connection_epoch,
                    )
                )
                execution = execute_stock_library_diagnostic_retention(
                    plan,
                    root=self.root,
                    current_session_id=session_id,
                    current_connection_epoch=current_connection_epoch,
                    automatic_authority=authority,
                )
                result.update(
                    {
                        "status": AUTOMATIC_RUN_COMPLETED,
                        "deleted_count": int(execution["deleted_files"]),
                        "deleted_bytes": int(execution["deleted_bytes"]),
                        "skipped_changed": int(execution["skipped_changed"]),
                        "skipped_protected": int(execution["skipped_protected"]),
                        "skipped_unsafe": int(execution["skipped_unsafe"]),
                        "failed_io": int(execution["failed_files"]),
                    }
                )
        except Exception as exc:
            result["status"] = AUTOMATIC_RUN_FAILED
            result["error_type"] = type(exc).__name__
        result["event_recorded"] = self._record_result_event(result)
        return result
