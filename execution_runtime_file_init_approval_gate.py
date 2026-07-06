# -*- coding: utf-8 -*-
"""Preview-only approval gate for execution runtime file initialization.

This gate only decides whether a previously built file-init preview may move
toward a future initialization commit. It never creates files, creates
directories, writes runtime data, calls commit services, commits queues, calls
SendOrder, or connects to GUI/real execution.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from execution_runtime_file_init_preview import PREVIEW_TYPE


GATE_TYPE = "EXECUTION_RUNTIME_FILE_INIT_APPROVAL_GATE"
STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_SKIPPED = "SKIPPED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _project_runtime_root() -> Path:
    return (Path(__file__).resolve().parent / "runtime").resolve(strict=False)


def _under_project_runtime(path_value: Any) -> bool:
    path_text = _text(path_value)
    if not path_text:
        return False
    target = Path(path_text).resolve(strict=False)
    try:
        target.relative_to(_project_runtime_root())
    except ValueError:
        return False
    return True


def _required_confirmations(
    *,
    manual_runtime_file_init_confirmed: bool,
    manual_project_runtime_path_confirmed: bool,
) -> dict[str, bool]:
    return {
        "manual_runtime_file_init_confirmed": manual_runtime_file_init_confirmed is True,
        "manual_project_runtime_path_confirmed": manual_project_runtime_path_confirmed is True,
    }


def _result(
    *,
    status: str,
    init_commit_allowed: bool,
    required_confirmations: dict[str, bool],
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "gate_type": GATE_TYPE,
        "status": status,
        "init_commit_allowed": init_commit_allowed,
        "preview_only": True,
        "runtime_write": False,
        "required_confirmations": deepcopy(required_confirmations),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def approve_execution_runtime_file_init(
    file_init_preview_result: Any,
    *,
    manual_runtime_file_init_confirmed: bool = False,
    manual_project_runtime_path_confirmed: bool = False,
) -> dict[str, Any]:
    """Approve or block a future runtime file-init commit without side effects."""
    confirmations = _required_confirmations(
        manual_runtime_file_init_confirmed=manual_runtime_file_init_confirmed,
        manual_project_runtime_path_confirmed=manual_project_runtime_path_confirmed,
    )
    preview = _as_dict(file_init_preview_result)
    if not preview:
        return _result(
            status=STATUS_INVALID,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=["MALFORMED_FILE_INIT_PREVIEW_RESULT"],
        )
    if preview.get("preview_type") != PREVIEW_TYPE:
        return _result(
            status=STATUS_INVALID,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=["INVALID_FILE_INIT_PREVIEW_TYPE"],
        )
    if preview.get("preview_only") is not True:
        return _result(
            status=STATUS_INVALID,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=["PREVIEW_ONLY_REQUIRED"],
        )
    if preview.get("runtime_write") is not False:
        return _result(
            status=STATUS_INVALID,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=["RUNTIME_WRITE_MUST_BE_FALSE"],
        )

    preview_status = preview.get("status")
    if preview_status == "SKIPPED":
        return _result(
            status=STATUS_SKIPPED,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=_as_list(preview.get("issues")),
            warnings=_as_list(preview.get("warnings")),
        )
    if preview_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=_as_list(preview.get("issues")) or ["FILE_INIT_PREVIEW_INVALID"],
            warnings=_as_list(preview.get("warnings")),
        )
    if preview_status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=_as_list(preview.get("issues")) or ["FILE_INIT_PREVIEW_BLOCKED"],
            warnings=_as_list(preview.get("warnings")),
        )
    if preview_status != "READY":
        return _result(
            status=STATUS_INVALID,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=["INVALID_FILE_INIT_PREVIEW_STATUS"],
            warnings=_as_list(preview.get("warnings")),
        )

    targets = _as_dict(preview.get("targets"))
    project_runtime_target = (
        _under_project_runtime(targets.get("order_executions"))
        or _under_project_runtime(targets.get("order_locks"))
    )
    issues: list[str] = []
    if not confirmations["manual_runtime_file_init_confirmed"]:
        issues.append("MANUAL_RUNTIME_FILE_INIT_CONFIRMATION_REQUIRED")
    if project_runtime_target and not confirmations["manual_project_runtime_path_confirmed"]:
        issues.append("MANUAL_PROJECT_RUNTIME_PATH_CONFIRMATION_REQUIRED")

    if issues:
        return _result(
            status=STATUS_BLOCKED,
            init_commit_allowed=False,
            required_confirmations=confirmations,
            issues=issues,
            warnings=_as_list(preview.get("warnings")),
        )

    return _result(
        status=STATUS_APPROVED,
        init_commit_allowed=True,
        required_confirmations=confirmations,
        warnings=_as_list(preview.get("warnings")),
    )
