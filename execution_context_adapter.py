# -*- coding: utf-8 -*-
"""Adapter from execution runtime dry-run results to ExecutionContext.

This module records dry-run outcomes into the in-memory ExecutionContext only.
It never creates runtime files, writes runtime files, commits storage, commits
queues, calls SendOrder, or connects to GUI components.
"""

from __future__ import annotations

from typing import Any

from execution_context import ExecutionContext


ADAPTER_TYPE = "EXECUTION_CONTEXT_ADAPTER"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _result(
    *,
    status: str,
    context_write: bool,
    session_id: str | None = None,
    session_summary: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "context_write": context_write,
        "runtime_write": False,
        "session_id": session_id,
        "session_summary": session_summary,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _extract_identifiers(dry_run_result: dict[str, Any]) -> dict[str, str]:
    catalog = _as_dict(_as_dict(dry_run_result.get("catalog_orchestrator")).get("catalog_preview"))
    commit_plan = _as_dict(_as_dict(dry_run_result.get("commit_plan")).get("commit_plan"))
    planned_records = _as_dict(commit_plan.get("planned_records"))
    execution_record = _as_dict(planned_records.get("execution"))
    lock_record = _as_dict(planned_records.get("lock"))
    pipeline = _as_dict(_as_dict(dry_run_result.get("execution_preview")).get("pipeline"))
    execution_request = _as_dict(_as_dict(pipeline.get("execution_request_preview")).get("execution_request"))

    execution_id = _first_text(
        catalog.get("execution_id"),
        execution_record.get("execution_id"),
        execution_request.get("execution_id"),
        lock_record.get("execution_id"),
    )
    order_id = _first_text(
        catalog.get("order_id"),
        execution_record.get("order_id"),
        execution_request.get("order_id"),
        lock_record.get("order_id"),
    )
    request_hash = _first_text(
        catalog.get("request_hash"),
        execution_record.get("request_hash"),
        execution_request.get("request_hash"),
        lock_record.get("request_hash"),
    )
    lock_id = _first_text(
        catalog.get("lock_id"),
        lock_record.get("lock_id"),
        execution_record.get("lock_id"),
        execution_request.get("lock_id"),
    )
    session_id = _first_text(
        dry_run_result.get("session_id"),
        f"SESSION_{execution_id}" if execution_id else "",
    )
    return {
        "session_id": session_id,
        "execution_id": execution_id,
        "order_id": order_id,
        "request_hash": request_hash,
        "lock_id": lock_id,
    }


def _missing_identifier_issues(ids: dict[str, str]) -> list[str]:
    required = {
        "session_id": "MISSING_SESSION_ID",
        "execution_id": "MISSING_EXECUTION_ID",
        "order_id": "MISSING_ORDER_ID",
        "request_hash": "MISSING_REQUEST_HASH",
        "lock_id": "MISSING_LOCK_ID",
    }
    return [issue for key, issue in required.items() if not ids.get(key)]


def adapt_runtime_dry_run_to_context(context: Any, dry_run_result: Any) -> dict[str, Any]:
    """Record a dry-run result into an ExecutionContext in memory only."""
    if not isinstance(context, ExecutionContext):
        return _result(
            status="INVALID",
            context_write=False,
            issues=["CONTEXT_MUST_BE_EXECUTION_CONTEXT"],
        )

    if not isinstance(dry_run_result, dict):
        dry_run = {}
        status = "INVALID"
        issues = ["MALFORMED_DRY_RUN_RESULT"]
        warnings: list[Any] = []
    else:
        dry_run = dry_run_result
        raw_status = dry_run.get("status")
        status = raw_status if raw_status in {"READY", "BLOCKED", "INVALID"} else "INVALID"
        issues = _as_list(dry_run.get("issues"))
        warnings = _as_list(dry_run.get("warnings"))
        if raw_status not in {"READY", "BLOCKED", "INVALID"}:
            issues = issues + ["INVALID_DRY_RUN_STATUS"]

    ids = _extract_identifiers(dry_run)
    missing = _missing_identifier_issues(ids)
    final_status = "INVALID" if missing else status
    final_issues = list(issues) + missing

    if missing:
        fallback = _text(dry_run.get("session_id")) or "SESSION_INVALID_DRY_RUN"
        ids = {
            "session_id": ids.get("session_id") or fallback,
            "execution_id": ids.get("execution_id") or "INVALID_EXECUTION",
            "order_id": ids.get("order_id") or "INVALID_ORDER",
            "request_hash": ids.get("request_hash") or "INVALID_REQUEST_HASH",
            "lock_id": ids.get("lock_id") or "INVALID_LOCK",
        }

    try:
        session = context.create_session(
            session_id=ids["session_id"],
            execution_id=ids["execution_id"],
            order_id=ids["order_id"],
            request_hash=ids["request_hash"],
            lock_id=ids["lock_id"],
            metadata={
                "source": "execution_runtime_dry_run",
                "dry_run_status": status,
                "preview_only": True,
            },
        )
        if final_status == "READY":
            session = context.mark_session_ready(session.session_id)
        elif final_status == "BLOCKED":
            reason = _first_text(final_issues[0] if final_issues else "", dry_run.get("blocked_reason"), "DRY_RUN_BLOCKED")
            session = context.mark_session_blocked(session.session_id, reason)
        else:
            reason = _first_text(final_issues[0] if final_issues else "", "DRY_RUN_INVALID")
            session = context.mark_session_invalid(session.session_id, reason)
    except Exception as exc:
        return _result(
            status="INVALID",
            context_write=False,
            session_id=ids.get("session_id"),
            issues=final_issues + [f"CONTEXT_ADAPTATION_FAILED: {exc}"],
            warnings=warnings,
        )

    return _result(
        status=final_status,
        context_write=True,
        session_id=session.session_id,
        session_summary=session.summary(),
        issues=final_issues,
        warnings=warnings,
    )
