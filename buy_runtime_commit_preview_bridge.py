# -*- coding: utf-8 -*-
"""Preview-only bridge from BUY runtime projection to runtime commit preview.

This module prepares an in-memory Runtime Commit Preview from a READY BUY
runtime projection preview. It does not call Runtime Commit Core, write runtime
files, enqueue orders, call SendOrder/Broker/Chejan, or update GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


BRIDGE_TYPE = "BUY_RUNTIME_COMMIT_PREVIEW_BRIDGE"
PREVIEW_VERSION = "BUY_RUNTIME_COMMIT_PREVIEW_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _preview_id(*, candidate_id: str, projection_hash: str, runtime_after_hash: str, policy_hash: str) -> str:
    digest = _stable_hash({
        "candidate_id": candidate_id,
        "projection_hash": projection_hash,
        "runtime_after_hash": runtime_after_hash,
        "policy_hash": policy_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_PREVIEW_{}".format(digest)


def _result(
    *,
    status: str,
    runtime_commit_preview: dict[str, Any] | None = None,
    runtime_commit_preview_summary: dict[str, Any] | None = None,
    runtime_commit_preview_report: dict[str, Any] | None = None,
    runtime_patch_preview: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "bridge_type": BRIDGE_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "commit_executed": False,
        "runtime_commit_core_called": False,
        "order_management_connected": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "runtime_commit_preview": deepcopy(runtime_commit_preview) if isinstance(runtime_commit_preview, dict) else None,
        "runtime_commit_preview_summary": deepcopy(runtime_commit_preview_summary)
        if isinstance(runtime_commit_preview_summary, dict)
        else None,
        "runtime_commit_preview_report": deepcopy(runtime_commit_preview_report)
        if isinstance(runtime_commit_preview_report, dict)
        else None,
        "runtime_patch_preview": deepcopy(runtime_patch_preview) if isinstance(runtime_patch_preview, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "evidence": deepcopy(evidence or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "issues": list(issues or []),
    }


def _blocked_from_projection(projection_preview: dict[str, Any], status: str) -> dict[str, Any]:
    mapped_status = STATUS_BLOCKED if status == STATUS_BLOCKED else STATUS_INVALID
    issue = "runtime projection preview status is {}".format(status or "<missing>")
    return _result(
        status=mapped_status,
        runtime_patch_preview=_as_dict(projection_preview.get("runtime_patch_preview")) or None,
        execution_snapshot=_as_dict(projection_preview.get("execution_snapshot")),
        evidence=_as_dict(projection_preview.get("evidence")),
        diagnostics=_as_list(projection_preview.get("diagnostics")),
        issues=_as_list(projection_preview.get("issues")) or [issue],
    )


def _projection_hash(projection_preview: dict[str, Any]) -> str:
    explicit = _clean_text(projection_preview.get("projection_hash"))
    if explicit:
        return explicit
    payload = {
        "runtime_projection": _as_dict(projection_preview.get("runtime_projection")),
        "runtime_patch_preview": _as_dict(projection_preview.get("runtime_patch_preview")),
        "after_state_candidate": _as_dict(projection_preview.get("after_state_candidate")),
    }
    if not payload["runtime_projection"]:
        return ""
    return _stable_hash(payload)


def _report(
    *,
    preview: dict[str, Any],
    summary: dict[str, Any],
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "title": "BUY Runtime Commit Preview",
        "preview_id": preview["preview_id"],
        "sections": [
            {
                "title": "Candidate",
                "lines": [
                    "candidate_id: {}".format(preview["candidate_id"]),
                    "signal_id: {}".format(preview["signal_id"]),
                ],
            },
            {
                "title": "Projection",
                "lines": [
                    "runtime_target: {}".format(preview["runtime_target"]),
                    "projection_hash: {}".format(preview["projection_hash"]),
                ],
            },
            {
                "title": "Changed Fields",
                "lines": list(preview["changed_fields"]),
            },
            {
                "title": "Before",
                "state": deepcopy(before_state),
            },
            {
                "title": "After",
                "state": deepcopy(after_state),
            },
            {
                "title": "Hash",
                "lines": [
                    "runtime_before_hash: {}".format(preview["runtime_before_hash"]),
                    "runtime_after_hash: {}".format(preview["runtime_after_hash"]),
                    "policy_hash: {}".format(preview["policy_hash"]),
                    "approved_rule_hash: {}".format(preview["approved_rule_hash"]),
                ],
            },
            {
                "title": "Warnings",
                "lines": list(warnings),
            },
            {
                "title": "Diagnostics",
                "items": deepcopy(diagnostics),
            },
        ],
        "summary": deepcopy(summary),
        "preview_only": True,
    }


def build_buy_runtime_commit_preview(
    runtime_projection_preview: Any,
    preview_context: Any = None,
) -> dict[str, Any]:
    """Build Runtime Commit Preview from a BUY runtime projection preview."""
    projection_preview = deepcopy(_as_dict(runtime_projection_preview))
    context = deepcopy(_as_dict(preview_context))
    if not projection_preview:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "runtime_projection_preview is required"}],
            issues=["runtime_projection_preview is required"],
        )

    status = _clean_text(projection_preview.get("status")).upper()
    if status in {STATUS_BLOCKED, STATUS_INVALID}:
        return _blocked_from_projection(projection_preview, status)
    if status != STATUS_READY:
        return _result(
            status=STATUS_INVALID,
            runtime_patch_preview=_as_dict(projection_preview.get("runtime_patch_preview")) or None,
            execution_snapshot=_as_dict(projection_preview.get("execution_snapshot")),
            evidence=_as_dict(projection_preview.get("evidence")),
            diagnostics=_as_list(projection_preview.get("diagnostics")),
            issues=["runtime projection preview status is not READY"],
        )

    runtime_projection = _as_dict(projection_preview.get("runtime_projection"))
    runtime_patch = _as_dict(projection_preview.get("runtime_patch_preview"))
    before_state = _as_dict(projection_preview.get("before_state"))
    after_state = _as_dict(projection_preview.get("after_state_candidate"))
    execution_snapshot = _as_dict(projection_preview.get("execution_snapshot"))
    evidence = _as_dict(projection_preview.get("evidence"))
    diagnostics = _as_list(projection_preview.get("diagnostics"))
    issues: list[str] = []

    projection_hash = _projection_hash(projection_preview)
    runtime_before_hash = _clean_text(
        runtime_projection.get("before_state_hash") or runtime_patch.get("runtime_state_hash_before")
    )
    runtime_after_hash = _clean_text(
        runtime_projection.get("after_state_candidate_hash")
        or runtime_patch.get("runtime_state_hash_after_candidate")
    )
    candidate_id = _clean_text(runtime_projection.get("candidate_id") or runtime_patch.get("source_candidate_id"))
    signal_id = runtime_projection.get("source_signal_id", runtime_patch.get("source_signal_id"))
    policy_hash = _clean_text(runtime_patch.get("policy_hash") or execution_snapshot.get("policy_hash"))
    approved_rule_hash = _clean_text(execution_snapshot.get("approved_rule_hash"))

    if not runtime_projection:
        issues.append("MALFORMED_PROJECTION")
    if not projection_hash:
        issues.append("PROJECTION_HASH_MISSING")
    if not runtime_before_hash:
        issues.append("RUNTIME_BEFORE_HASH_MISSING")
    if not runtime_after_hash:
        issues.append("RUNTIME_AFTER_HASH_MISSING")
    if not candidate_id:
        issues.append("CANDIDATE_ID_MISSING")
    if not execution_snapshot:
        issues.append("EXECUTION_SNAPSHOT_MISSING")
    if not runtime_patch:
        issues.append("RUNTIME_PATCH_PREVIEW_MISSING")
    if not after_state:
        issues.append("AFTER_STATE_CANDIDATE_MISSING")
    changes = _as_dict(runtime_patch.get("changes"))
    if runtime_patch and not isinstance(runtime_patch.get("changes"), dict):
        issues.append("RUNTIME_PATCH_CHANGES_MALFORMED")

    if issues:
        return _result(
            status=STATUS_INVALID,
            runtime_patch_preview=runtime_patch or None,
            execution_snapshot=execution_snapshot,
            evidence=evidence,
            diagnostics=diagnostics + [
                {"stage": "runtime_commit_preview_validation", "ok": False, "reason": issue}
                for issue in issues
            ],
            issues=issues,
        )

    changed_fields = list(runtime_projection.get("changed_fields") or sorted(changes))
    preview_id = _preview_id(
        candidate_id=candidate_id,
        projection_hash=projection_hash,
        runtime_after_hash=runtime_after_hash,
        policy_hash=policy_hash,
    )
    runtime_target = _clean_text(runtime_projection.get("target") or runtime_patch.get("target"))
    commit_preview = {
        "preview_version": PREVIEW_VERSION,
        "preview_id": preview_id,
        "runtime_target": runtime_target,
        "projection_hash": projection_hash,
        "runtime_before_hash": runtime_before_hash,
        "runtime_after_hash": runtime_after_hash,
        "candidate_id": candidate_id,
        "signal_id": deepcopy(signal_id),
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "changed_fields": deepcopy(changed_fields),
        "patch_count": len(changes),
        "estimated_runtime_state": deepcopy(after_state),
        "execution_snapshot": deepcopy(execution_snapshot),
        "preview_only": True,
        "commit_allowed": False,
        "commit_executed": False,
    }
    summary = {
        "changed_fields_count": len(changed_fields),
        "current_buy_round": after_state.get("current_buy_round"),
        "executed_buy_rounds": after_state.get("executed_buy_rounds"),
        "cumulative_budget": after_state.get("cumulative_buy_budget"),
        "is_last_round": after_state.get("is_last_buy_round"),
        "policy_version": after_state.get("execution_policy_version"),
        "projection_hash": projection_hash,
    }
    warnings = _as_list(context.get("warnings"))
    report = _report(
        preview=commit_preview,
        summary=summary,
        before_state=before_state,
        after_state=after_state,
        diagnostics=diagnostics,
        warnings=warnings,
    )
    return _result(
        status=STATUS_READY,
        runtime_commit_preview=commit_preview,
        runtime_commit_preview_summary=summary,
        runtime_commit_preview_report=report,
        runtime_patch_preview=runtime_patch,
        execution_snapshot=execution_snapshot,
        evidence=evidence,
        diagnostics=diagnostics + [{"stage": "runtime_commit_preview", "ok": True, "reason": "preview ready"}],
    )
