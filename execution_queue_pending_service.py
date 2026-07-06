# -*- coding: utf-8 -*-
"""Queue pending candidate contract builder.

This module only builds an in-memory queue pending candidate after Execution
Candidate. Queue pending is not ORDER_QUEUED creation, does not write runtime
files, and does not call SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


QUEUE_CONTRACT_VERSION = "preview-1"
NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_QUEUE_WRITER_REQUIRED = "QUEUE_WRITER_REQUIRED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "queue_pending": False,
        "queue_pending_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _queue_pending_id(candidate_id: str) -> str:
    return f"QUEUE_PENDING_{candidate_id}"


def build_execution_queue_pending(
    candidate_result: Any,
    context: Any = None,
) -> dict[str, Any]:
    """Build a queue pending candidate contract without side effects."""
    candidate = _as_dict(candidate_result)

    if candidate.get("candidate") is not True:
        return _blocked("candidate", "candidate_result.candidate is not true")

    if candidate.get("candidate_stage") != "candidate_created":
        return _blocked("candidate", "candidate_result.candidate_stage is not candidate_created")

    if candidate.get("next_stage") != "QUEUE_PENDING":
        return _blocked("candidate", "candidate_result.next_stage is not QUEUE_PENDING")

    candidate_id = _clean_text(candidate.get("candidate_id"))
    order_id = _clean_text(candidate.get("order_id"))
    source_signal_id = _clean_text(candidate.get("source_signal_id"))
    request_hash_preview = _clean_text(candidate.get("request_hash_preview"))
    lock_preview = _as_dict(candidate.get("lock_preview"))
    execution_request_preview = _as_dict(candidate.get("execution_request_preview"))
    execution_request = _as_dict(execution_request_preview.get("execution_request"))

    if not candidate_id:
        return _blocked("candidate", "candidate_id is required")

    if not order_id:
        return _blocked("candidate", "order_id is required")

    if not source_signal_id:
        return _blocked("candidate", "source_signal_id is required")

    if not request_hash_preview:
        return _blocked("candidate", "request_hash_preview is required")

    if not _clean_text(lock_preview.get("lock_id")):
        return _blocked("candidate", "lock_preview.lock_id is required")

    if not execution_request_preview:
        return _blocked("candidate", "execution_request_preview is required")

    if not execution_request:
        return _blocked("candidate", "execution_request_preview.execution_request is required")

    if not _clean_text(execution_request.get("execution_id")):
        return _blocked("candidate", "execution_request.execution_id is required")

    if not _clean_text(execution_request.get("request_hash")):
        return _blocked("candidate", "execution_request.request_hash is required")

    if not _clean_text(execution_request.get("lock_id")):
        return _blocked("candidate", "execution_request.lock_id is required")

    return {
        "queue_pending": True,
        "queue_pending_stage": "queue_pending_created",
        "queue_pending_id": _queue_pending_id(candidate_id),
        "created_from_candidate_id": candidate_id,
        "queue_contract_version": QUEUE_CONTRACT_VERSION,
        "next_stage": NEXT_STAGE_QUEUE_WRITER_REQUIRED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [],
        "warnings": [],
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "request_hash_preview": request_hash_preview,
        "lock_preview": deepcopy(lock_preview),
        "execution_request_preview": deepcopy(execution_request_preview),
    }
