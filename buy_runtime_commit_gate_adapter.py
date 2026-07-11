# -*- coding: utf-8 -*-
"""Preview-only BUY Runtime Commit Gate adapter.

This module converts a BUY Runtime Commit Preview into a gate preview. It never
calls Runtime Commit Core, writes runtime files, writes queue files, commits
orders, calls SendOrder/Broker/Chejan, or updates GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


ADAPTER_TYPE = "BUY_RUNTIME_COMMIT_GATE_ADAPTER"
GATE_VERSION = "BUY_RUNTIME_COMMIT_GATE_PREVIEW_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _gate_id(*, preview_id: str, projection_hash: str, policy_hash: str, approved_rule_hash: str) -> str:
    digest = _stable_hash({
        "preview_id": preview_id,
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_GATE_{}".format(digest)


def _result(
    *,
    status: str,
    runtime_commit_gate_preview: dict[str, Any] | None = None,
    gate_summary: dict[str, Any] | None = None,
    gate_report: dict[str, Any] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "order_commit": False,
        "commit_execute": False,
        "runtime_commit_core_called": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "runtime_commit_gate_preview": deepcopy(runtime_commit_gate_preview)
        if isinstance(runtime_commit_gate_preview, dict)
        else None,
        "gate_summary": deepcopy(gate_summary) if isinstance(gate_summary, dict) else None,
        "gate_report": deepcopy(gate_report) if isinstance(gate_report, dict) else None,
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "evidence": deepcopy(evidence or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "issues": list(issues or []),
    }


def _report(
    *,
    gate: dict[str, Any],
    summary: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "title": "Runtime Commit Gate Preview",
        "gate_id": gate["gate_id"],
        "preview_id": gate["preview_id"],
        "sections": [
            {
                "title": "Commit Decision",
                "lines": [
                    "commit_allowed: {}".format(gate["commit_allowed"]),
                    "commit_execute: {}".format(gate["commit_execute"]),
                    "blocking_reason: {}".format(gate["blocking_reason"]),
                ],
            },
            {
                "title": "Candidate",
                "lines": [
                    "candidate_id: {}".format(gate["candidate_id"]),
                    "signal_id: {}".format(gate["signal_id"]),
                ],
            },
            {
                "title": "Projection",
                "lines": [
                    "projection_hash: {}".format(gate["projection_hash"]),
                    "policy_version: {}".format(summary.get("policy_version")),
                ],
            },
            {
                "title": "Changed Fields",
                "lines": list(gate["changed_fields"]),
            },
            {
                "title": "Hashes",
                "lines": [
                    "runtime_before_hash: {}".format(gate["runtime_before_hash"]),
                    "runtime_after_hash: {}".format(gate["runtime_after_hash"]),
                    "policy_hash: {}".format(gate["policy_hash"]),
                    "approved_rule_hash: {}".format(gate["approved_rule_hash"]),
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


def _extract_inputs(commit_preview_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    preview = _as_dict(commit_preview_result.get("runtime_commit_preview"))
    summary = _as_dict(commit_preview_result.get("runtime_commit_preview_summary"))
    report = _as_dict(commit_preview_result.get("runtime_commit_preview_report"))
    execution_snapshot = _as_dict(commit_preview_result.get("execution_snapshot")) or _as_dict(preview.get("execution_snapshot"))
    diagnostics = _as_list(commit_preview_result.get("diagnostics"))
    return preview, summary, report, execution_snapshot, diagnostics


def build_buy_runtime_commit_gate_preview(
    runtime_commit_preview_result: Any,
    gate_context: Any = None,
) -> dict[str, Any]:
    """Build a read-only Runtime Commit Gate Preview from commit preview."""
    commit_result = deepcopy(_as_dict(runtime_commit_preview_result))
    context = deepcopy(_as_dict(gate_context))
    if not commit_result:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "runtime_commit_preview_result is required"}],
            issues=["runtime_commit_preview_result is required"],
        )

    preview, summary, upstream_report, execution_snapshot, diagnostics = _extract_inputs(commit_result)
    evidence = _as_dict(commit_result.get("evidence"))
    upstream_status = _clean_text(commit_result.get("status")).upper()
    issues = _as_list(commit_result.get("issues"))
    validation_issues: list[str] = []

    preview_id = _clean_text(preview.get("preview_id"))
    projection_hash = _clean_text(preview.get("projection_hash"))
    candidate_id = _clean_text(preview.get("candidate_id"))
    signal_id = preview.get("signal_id")
    runtime_before_hash = _clean_text(preview.get("runtime_before_hash"))
    runtime_after_hash = _clean_text(preview.get("runtime_after_hash"))
    policy_hash = _clean_text(preview.get("policy_hash"))
    approved_rule_hash = _clean_text(preview.get("approved_rule_hash"))

    if not preview:
        validation_issues.append("MALFORMED_PREVIEW")
    if not preview_id:
        validation_issues.append("PREVIEW_ID_MISSING")
    if not projection_hash:
        validation_issues.append("PROJECTION_HASH_MISSING")
    if not candidate_id:
        validation_issues.append("CANDIDATE_ID_MISSING")
    if not execution_snapshot:
        validation_issues.append("EXECUTION_SNAPSHOT_MISSING")
    if not policy_hash:
        validation_issues.append("POLICY_HASH_MISSING")

    mapped_status = upstream_status
    if upstream_status == STATUS_READY:
        mapped_status = STATUS_READY
    elif upstream_status == STATUS_BLOCKED:
        mapped_status = STATUS_BLOCKED
    elif upstream_status == STATUS_INVALID:
        mapped_status = STATUS_INVALID
    else:
        mapped_status = STATUS_INVALID
        validation_issues.append("PREVIEW_STATUS_NOT_READY")

    if validation_issues and upstream_status == STATUS_READY:
        mapped_status = STATUS_INVALID

    blocking_reason = ""
    if mapped_status != STATUS_READY:
        blocking_reason = "; ".join(issues + validation_issues) or "runtime commit preview is not READY"

    if not preview_id:
        preview_id = _clean_text(context.get("preview_id"))
    if not projection_hash:
        projection_hash = _clean_text(context.get("projection_hash"))
    gate_id = _gate_id(
        preview_id=preview_id,
        projection_hash=projection_hash,
        policy_hash=policy_hash,
        approved_rule_hash=approved_rule_hash,
    ) if preview_id and projection_hash and policy_hash else ""

    changed_fields = _as_list(preview.get("changed_fields"))
    estimated_state = _as_dict(preview.get("estimated_runtime_state"))
    gate_preview = {
        "gate_version": GATE_VERSION,
        "gate_id": gate_id,
        "preview_id": preview_id,
        "candidate_id": candidate_id,
        "signal_id": deepcopy(signal_id),
        "projection_hash": projection_hash,
        "runtime_before_hash": runtime_before_hash,
        "runtime_after_hash": runtime_after_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "commit_allowed": mapped_status == STATUS_READY and not validation_issues,
        "commit_execute": False,
        "blocking_reason": blocking_reason,
        "changed_fields": deepcopy(changed_fields),
        "estimated_runtime_state": deepcopy(estimated_state),
        "execution_snapshot": deepcopy(execution_snapshot),
        "preview_only": True,
    }
    gate_summary = {
        "commit_allowed": gate_preview["commit_allowed"],
        "changed_fields_count": len(changed_fields),
        "current_buy_round": summary.get("current_buy_round", estimated_state.get("current_buy_round")),
        "executed_buy_rounds": summary.get("executed_buy_rounds", estimated_state.get("executed_buy_rounds")),
        "cumulative_budget": summary.get("cumulative_budget", estimated_state.get("cumulative_buy_budget")),
        "projection_hash": projection_hash,
        "policy_version": summary.get("policy_version", estimated_state.get("execution_policy_version")),
    }
    warnings = _as_list(context.get("warnings")) + _as_list(upstream_report.get("warnings"))
    gate_report = _report(
        gate=gate_preview,
        summary=gate_summary,
        diagnostics=diagnostics,
        warnings=warnings,
    )
    final_issues = issues + validation_issues
    return _result(
        status=mapped_status,
        runtime_commit_gate_preview=gate_preview,
        gate_summary=gate_summary,
        gate_report=gate_report,
        execution_snapshot=execution_snapshot,
        evidence=evidence,
        diagnostics=diagnostics + [
            {
                "stage": "runtime_commit_gate_preview",
                "ok": mapped_status == STATUS_READY,
                "reason": "gate preview ready" if mapped_status == STATUS_READY else blocking_reason,
            }
        ],
        issues=final_issues,
    )
