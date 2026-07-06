# -*- coding: utf-8 -*-
"""Execution candidate contract builder.

This module only builds an in-memory candidate dict after Preview and Approval.
Candidate creation is not queue creation, does not create ORDER_QUEUED, does
not write runtime files, and does not call SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_QUEUE_PENDING = "QUEUE_PENDING"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "candidate": False,
        "candidate_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "blocked_reasons": [reason],
    }


def _pipeline_from_preview(preview_result: dict[str, Any]) -> dict[str, Any]:
    direct_pipeline = _as_dict(preview_result.get("pipeline"))
    if direct_pipeline:
        return direct_pipeline

    pipeline_result = _as_dict(preview_result.get("pipeline_result"))
    return _as_dict(pipeline_result.get("pipeline"))


def _summary_from_preview(preview_result: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(preview_result.get("summary"))


def _extract_order_id(preview_result: dict[str, Any], execution_request_preview: dict[str, Any]) -> str:
    summary = _summary_from_preview(preview_result)
    execution_request = _as_dict(execution_request_preview.get("execution_request"))
    return _clean_text(summary.get("order_id") or execution_request.get("order_id"))


def _extract_source_signal_id(execution_request_preview: dict[str, Any]) -> str:
    execution_request = _as_dict(execution_request_preview.get("execution_request"))
    return _clean_text(execution_request.get("source_signal_id"))


def _extract_request_hash(
    preview_result: dict[str, Any],
    request_hash_preview: dict[str, Any],
    execution_request_preview: dict[str, Any],
) -> str:
    summary = _summary_from_preview(preview_result)
    execution_request = _as_dict(execution_request_preview.get("execution_request"))
    return _clean_text(
        summary.get("request_hash")
        or request_hash_preview.get("request_hash")
        or execution_request.get("request_hash")
    )


def _candidate_id(order_id: str, request_hash: str) -> str:
    source = order_id or request_hash
    return f"EXEC_CANDIDATE_{source}"


def build_execution_candidate(
    preview_result: Any,
    approval_result: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Build an execution candidate contract without side effects."""
    preview = _as_dict(preview_result)
    approval = _as_dict(approval_result)

    if approval.get("approved") is not True:
        return _blocked("approval", "approval_result.approved is not true")

    if approval.get("next_stage") != "EXECUTION_CANDIDATE":
        return _blocked("approval", "approval_result.next_stage is not EXECUTION_CANDIDATE")

    if preview.get("ok") is not True:
        return _blocked("preview_result", "preview_result.ok is not true")

    pipeline = _pipeline_from_preview(preview)
    request_hash_preview = _as_dict(pipeline.get("request_hash_preview"))
    lock_preview = _as_dict(pipeline.get("lock_preview"))
    execution_request_preview = _as_dict(pipeline.get("execution_request_preview"))

    if not request_hash_preview:
        return _blocked("preview_result", "request_hash_preview is required")

    if not lock_preview:
        return _blocked("preview_result", "lock_preview is required")

    if not execution_request_preview:
        return _blocked("preview_result", "execution_request_preview is required")

    order_id = _extract_order_id(preview, execution_request_preview)
    source_signal_id = _extract_source_signal_id(execution_request_preview)
    request_hash = _extract_request_hash(preview, request_hash_preview, execution_request_preview)

    return {
        "candidate": True,
        "candidate_stage": "candidate_created",
        "candidate_id": _candidate_id(order_id, request_hash),
        "next_stage": NEXT_STAGE_QUEUE_PENDING,
        "blocked_reasons": [],
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "request_hash_preview": request_hash,
        "lock_preview": deepcopy(lock_preview),
        "execution_request_preview": deepcopy(execution_request_preview),
    }
