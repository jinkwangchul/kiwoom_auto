"""Write JSON reports for rule apply commit results.

This module records commit/validation outcomes only. It does not commit rules,
restore backups, connect GUI actions, or call order execution paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


REPORT_TYPE_RULE_APPLY_COMMIT = "rule_apply_commit"
REPORT_STAGE_WRITTEN = "RULE_COMMIT_REPORT_WRITTEN"
REPORT_STAGE_BLOCKED = "RULE_COMMIT_REPORT_BLOCKED"

_REPORT_FIELDS = (
    "commit_id",
    "stage",
    "ok",
    "committed",
    "commit_accepted",
    "rules_path",
    "backup_path",
    "pre_file_sha256",
    "post_file_sha256",
    "pre_rules_hash",
    "post_rules_hash",
    "apply_preview_hash",
    "apply_preview_hash_algorithm",
    "applied_patches",
    "skipped_patches",
    "final_diff",
    "post_validation",
    "unexpected_changes",
    "manual_restore_required",
    "rollback_attempted",
    "write_completed",
    "blocked_reasons",
    "warnings",
)

_LIST_DEFAULT_FIELDS = {
    "applied_patches",
    "skipped_patches",
    "final_diff",
    "unexpected_changes",
    "blocked_reasons",
    "warnings",
}

_BOOL_DEFAULT_FALSE_FIELDS = {
    "manual_restore_required",
    "rollback_attempted",
}


def _now() -> datetime:
    return datetime.now().astimezone()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": REPORT_STAGE_BLOCKED,
        "report_path": None,
        "report_type": REPORT_TYPE_RULE_APPLY_COMMIT,
        "blocked_reasons": [reason],
        "warnings": warnings or [],
    }


def _safe_filename_part(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._") or "unknown"


def _report_filename(report: dict[str, Any], created: datetime) -> str:
    stamp = created.strftime("%Y%m%d_%H%M%S")
    commit_id = report.get("commit_id")
    if commit_id:
        suffix = _safe_filename_part(commit_id)
    else:
        suffix = _stable_hash(report)[:12]
    return f"rule_commit_{stamp}_{suffix}.json"


def _extract_report(commit_result: dict[str, Any], created: datetime) -> dict[str, Any]:
    report: dict[str, Any] = {
        "report_type": REPORT_TYPE_RULE_APPLY_COMMIT,
        "created_at": created.isoformat(),
    }

    for field in _REPORT_FIELDS:
        if field in commit_result:
            report[field] = deepcopy(commit_result.get(field))
        elif field in _LIST_DEFAULT_FIELDS:
            report[field] = []
        elif field in _BOOL_DEFAULT_FALSE_FIELDS:
            report[field] = False
        elif field == "post_validation":
            report[field] = {}
        else:
            report[field] = None

    if report["commit_accepted"] is None:
        report["commit_accepted"] = bool(report.get("ok") and report.get("committed"))
    if report["write_completed"] is None:
        report["write_completed"] = bool(report.get("committed"))

    post_validation = report.get("post_validation")
    if isinstance(post_validation, dict):
        report["unexpected_changes"] = deepcopy(post_validation.get("unexpected_changes", report["unexpected_changes"]))

    for field in _LIST_DEFAULT_FIELDS:
        if not isinstance(report.get(field), list):
            report[field] = []
    if not isinstance(report.get("post_validation"), dict):
        report["post_validation"] = {}

    return report


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def write_rule_commit_report(
    commit_result: dict[str, Any],
    report_dir: str | Path,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a sanitized JSON report for a rule apply commit result."""
    _context_copy = deepcopy(context) if isinstance(context, dict) else {}
    if not report_dir:
        return _blocked("report_dir is required")
    if not isinstance(commit_result, dict):
        return _blocked("commit_result must be a dict")

    target_dir = Path(report_dir)
    created = _now()
    report = _extract_report(commit_result, created)
    report_path = target_dir / _report_filename(report, created)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(report_path, report)
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _blocked(f"failed to write rule commit report: {exc}")

    if loaded != report:
        return _blocked("written report reload mismatch")

    return {
        "ok": True,
        "stage": REPORT_STAGE_WRITTEN,
        "report_path": str(report_path),
        "report_type": REPORT_TYPE_RULE_APPLY_COMMIT,
        "warnings": [],
    }
