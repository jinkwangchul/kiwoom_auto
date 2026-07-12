# -*- coding: utf-8 -*-
"""Production runtime logical-target allowlist.

This module resolves pre-registered logical runtime targets to files under a
caller-provided runtime root. It performs validation only: it never creates
directories, reads or writes runtime files, commits queues, calls SendOrder, or
connects to GUI/real execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping


ALLOWLIST_TYPE = "EXECUTION_RUNTIME_ALLOWLIST"
STATUS_ALLOWED = "ALLOWED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

OPERATION_RESOLVE = "resolve"
OPERATION_PREVIEW = "preview"
OPERATION_READ = "read"
OPERATION_WRITE = "write"

WINDOWS_RESERVED_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


@dataclass(frozen=True)
class RuntimeAllowlistEntry:
    logical_target: str
    relative_path: str
    file_name: str
    allowed_operations: tuple[str, ...] = (OPERATION_RESOLVE, OPERATION_PREVIEW, OPERATION_READ)
    write_enabled: bool = False
    description: str = ""


@dataclass(frozen=True)
class RuntimeAllowlistDecision:
    allowlist_type: str
    status: str
    allowed: bool
    logical_target: str
    operation: str
    relative_path: str
    resolved_path: str
    normalized_path: str
    runtime_root: str
    reason: str
    blocked_reason: str
    registered: bool
    path_under_runtime_root: bool
    file_name_matches: bool
    operation_allowed: bool
    write_enabled: bool
    preview_only: bool = True
    runtime_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowlist_type": self.allowlist_type,
            "status": self.status,
            "allowed": self.allowed,
            "logical_target": self.logical_target,
            "operation": self.operation,
            "relative_path": self.relative_path,
            "resolved_path": self.resolved_path,
            "normalized_path": self.normalized_path,
            "runtime_root": self.runtime_root,
            "reason": self.reason,
            "blocked_reason": self.blocked_reason,
            "registered": self.registered,
            "path_under_runtime_root": self.path_under_runtime_root,
            "file_name_matches": self.file_name_matches,
            "operation_allowed": self.operation_allowed,
            "write_enabled": self.write_enabled,
            "preview_only": self.preview_only,
            "runtime_write": self.runtime_write,
        }


DEFAULT_RUNTIME_ROOT = (Path(__file__).resolve().parent / "runtime").resolve(strict=False)

DEFAULT_RUNTIME_ALLOWLIST: dict[str, RuntimeAllowlistEntry] = {
    "order_executions": RuntimeAllowlistEntry(
        logical_target="order_executions",
        relative_path="order_executions.json",
        file_name="order_executions.json",
        allowed_operations=(OPERATION_RESOLVE, OPERATION_PREVIEW, OPERATION_READ),
        write_enabled=False,
        description="Pilot logical target for future execution runtime records.",
    ),
}


def get_runtime_allowlist_entry(
    logical_target: Any,
    registry: Mapping[str, RuntimeAllowlistEntry] | None = None,
) -> RuntimeAllowlistEntry | None:
    target = "" if logical_target is None else str(logical_target)
    if not target or _logical_target_issue(target):
        return None
    return dict(registry or DEFAULT_RUNTIME_ALLOWLIST).get(target)


def is_runtime_target_allowed(
    logical_target: Any,
    *,
    runtime_root: str | Path | None = None,
    operation: Any = OPERATION_RESOLVE,
    registry: Mapping[str, RuntimeAllowlistEntry] | None = None,
) -> bool:
    return validate_runtime_target(
        logical_target,
        runtime_root=runtime_root,
        operation=operation,
        registry=registry,
    ).allowed


def validate_runtime_target(
    logical_target: Any,
    *,
    runtime_root: str | Path | None = None,
    operation: Any = OPERATION_RESOLVE,
    registry: Mapping[str, RuntimeAllowlistEntry] | None = None,
) -> RuntimeAllowlistDecision:
    """Validate a logical runtime target without touching runtime files."""
    root = _runtime_root(runtime_root)
    target = "" if logical_target is None else str(logical_target)
    op = _clean_text(operation).lower()

    if not target:
        return _blocked(
            logical_target=target,
            operation=op,
            runtime_root=root,
            blocked_reason="MISSING_LOGICAL_TARGET",
            status=STATUS_INVALID,
        )
    target_issue = _logical_target_issue(target)
    if target_issue:
        return _blocked(
            logical_target=target,
            operation=op,
            runtime_root=root,
            blocked_reason=target_issue,
            status=STATUS_INVALID,
        )
    if not op:
        return _blocked(
            logical_target=target,
            operation=op,
            runtime_root=root,
            blocked_reason="MISSING_OPERATION",
            status=STATUS_INVALID,
        )

    entry = get_runtime_allowlist_entry(target, registry)
    if entry is None:
        return _blocked(
            logical_target=target,
            operation=op,
            runtime_root=root,
            blocked_reason="UNREGISTERED_LOGICAL_TARGET",
            registered=False,
        )

    relative_issue = _relative_path_issue(entry.relative_path)
    if relative_issue:
        return _blocked(
            logical_target=target,
            operation=op,
            runtime_root=root,
            entry=entry,
            blocked_reason=relative_issue,
            registered=True,
            status=STATUS_INVALID,
        )

    candidate = root / entry.relative_path
    resolved = candidate.resolve(strict=False)
    path_under_root = _is_relative_to(resolved, root)
    file_name_matches = candidate.name == entry.file_name
    operation_allowed = op in entry.allowed_operations
    write_enabled = entry.write_enabled is True

    blocked: list[str] = []
    if not path_under_root:
        blocked.append("RUNTIME_ROOT_ESCAPE_BLOCKED")
    if not file_name_matches:
        blocked.append("FILE_NAME_MISMATCH")
    if not operation_allowed:
        blocked.append("OPERATION_NOT_ALLOWLISTED")
    if op == OPERATION_WRITE and not write_enabled:
        blocked.append("RUNTIME_WRITE_DISABLED")
    symlink_issue = _symlink_escape_issue(root, candidate)
    if symlink_issue:
        blocked.append(symlink_issue)

    if blocked:
        return _decision(
            status=STATUS_BLOCKED,
            allowed=False,
            logical_target=target,
            operation=op,
            runtime_root=root,
            entry=entry,
            resolved_path=resolved,
            blocked_reason=";".join(blocked),
            registered=True,
            path_under_runtime_root=path_under_root,
            file_name_matches=file_name_matches,
            operation_allowed=operation_allowed,
            write_enabled=write_enabled,
        )

    return _decision(
        status=STATUS_ALLOWED,
        allowed=True,
        logical_target=target,
        operation=op,
        runtime_root=root,
        entry=entry,
        resolved_path=resolved,
        reason="REGISTERED_RUNTIME_TARGET_ALLOWED",
        registered=True,
        path_under_runtime_root=True,
        file_name_matches=True,
        operation_allowed=True,
        write_enabled=write_enabled,
    )


def resolve_runtime_target(
    logical_target: Any,
    runtime_root: str | Path | None = None,
    *,
    operation: Any = OPERATION_RESOLVE,
    registry: Mapping[str, RuntimeAllowlistEntry] | None = None,
) -> dict[str, Any]:
    """Resolve a logical target to a normalized path decision."""
    return validate_runtime_target(
        logical_target,
        runtime_root=runtime_root,
        operation=operation,
        registry=registry,
    ).to_dict()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _runtime_root(value: str | Path | None) -> Path:
    if value is None:
        return DEFAULT_RUNTIME_ROOT
    return Path(value).resolve(strict=False)


def _logical_target_issue(value: str) -> str:
    if value != value.strip():
        return "LOGICAL_TARGET_WHITESPACE"
    if any(sep in value for sep in ("/", "\\")):
        return "LOGICAL_TARGET_MUST_NOT_BE_PATH"
    if ":" in value:
        return "LOGICAL_TARGET_MUST_NOT_BE_ABSOLUTE_PATH"
    if value in (".", "..") or ".." in value.split("."):
        return "LOGICAL_TARGET_TRAVERSAL_BLOCKED"
    return ""


def _relative_path_issue(value: str) -> str:
    text = "" if value is None else str(value)
    if not text or not text.strip():
        return "ALLOWLIST_RELATIVE_PATH_MISSING"
    normalized = text.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if normalized.startswith("/") or Path(text).is_absolute() or PureWindowsPath(text).is_absolute():
        return "ALLOWLIST_RELATIVE_PATH_ABSOLUTE_BLOCKED"
    if any(part == ".." for part in parts):
        return "ALLOWLIST_RELATIVE_PATH_TRAVERSAL_BLOCKED"
    if any(part in ("", ".") for part in parts):
        return "ALLOWLIST_RELATIVE_PATH_INVALID_SEGMENT"
    if any(part.endswith((".", " ")) for part in parts):
        return "ALLOWLIST_RELATIVE_PATH_TRAILING_DOT_SPACE_BLOCKED"
    if any(":" in part for part in parts):
        return "ALLOWLIST_RELATIVE_PATH_ALTERNATE_DATA_STREAM_BLOCKED"
    if any(part.split(".", 1)[0].upper() in WINDOWS_RESERVED_DEVICE_NAMES for part in parts):
        return "ALLOWLIST_RELATIVE_PATH_RESERVED_DEVICE_BLOCKED"
    return ""


def _symlink_escape_issue(root: Path, candidate: Path) -> str:
    root_resolved = root.resolve(strict=False)
    current = root_resolved
    try:
        parts = candidate.relative_to(root).parts
    except ValueError:
        return "RUNTIME_ROOT_ESCAPE_BLOCKED"
    for part in parts:
        current = current / part
        if current.exists() and current.is_symlink():
            if not _is_relative_to(current.resolve(strict=True), root_resolved):
                return "SYMLINK_ESCAPE_BLOCKED"
    return ""


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _blocked(
    *,
    logical_target: str,
    operation: str,
    runtime_root: Path,
    blocked_reason: str,
    entry: RuntimeAllowlistEntry | None = None,
    registered: bool = False,
    status: str = STATUS_BLOCKED,
) -> RuntimeAllowlistDecision:
    resolved = runtime_root / entry.relative_path if entry is not None else Path()
    return _decision(
        status=status,
        allowed=False,
        logical_target=logical_target,
        operation=operation,
        runtime_root=runtime_root,
        entry=entry,
        resolved_path=resolved,
        blocked_reason=blocked_reason,
        registered=registered,
        path_under_runtime_root=False,
        file_name_matches=False,
        operation_allowed=False,
        write_enabled=False,
    )


def _decision(
    *,
    status: str,
    allowed: bool,
    logical_target: str,
    operation: str,
    runtime_root: Path,
    entry: RuntimeAllowlistEntry | None,
    resolved_path: Path,
    blocked_reason: str = "",
    reason: str = "",
    registered: bool,
    path_under_runtime_root: bool,
    file_name_matches: bool,
    operation_allowed: bool,
    write_enabled: bool,
) -> RuntimeAllowlistDecision:
    relative_path = entry.relative_path if entry is not None else ""
    normalized = str(resolved_path).replace("\\", "/") if str(resolved_path) else ""
    return RuntimeAllowlistDecision(
        allowlist_type=ALLOWLIST_TYPE,
        status=status,
        allowed=allowed,
        logical_target=logical_target,
        operation=operation,
        relative_path=relative_path,
        resolved_path=str(resolved_path) if str(resolved_path) else "",
        normalized_path=normalized,
        runtime_root=str(runtime_root),
        reason=reason,
        blocked_reason=blocked_reason,
        registered=registered,
        path_under_runtime_root=path_under_runtime_root,
        file_name_matches=file_name_matches,
        operation_allowed=operation_allowed,
        write_enabled=write_enabled,
    )
