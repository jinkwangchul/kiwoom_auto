# -*- coding: utf-8 -*-
"""Adapter from BUY order candidate draft preview to execution preview input.

The adapter is read-only. It does not enqueue orders, create ORDER_QUEUED,
commit runtime state, call SendOrder/Broker/Chejan, or update GUI state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ADAPTER_TYPE = "BUY_CANDIDATE_EXECUTION_PREVIEW_ADAPTER"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
EXPECTED_CANDIDATE_VERSION = "BUY_ORDER_CANDIDATE_DRAFT_V1"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _result(
    *,
    status: str,
    order_candidate_input: dict[str, Any] | None = None,
    execution_preview_context: dict[str, Any] | None = None,
    buy_candidate_preview: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "preview_only": True,
        "queue_write": False,
        "runtime_write": False,
        "order_management_connected": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "order_candidate_input": deepcopy(order_candidate_input) if isinstance(order_candidate_input, dict) else None,
        "execution_preview_context": deepcopy(execution_preview_context)
        if isinstance(execution_preview_context, dict)
        else None,
        "buy_candidate_preview": deepcopy(buy_candidate_preview) if isinstance(buy_candidate_preview, dict) else None,
        "evidence": deepcopy(evidence or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "issues": list(issues or []),
    }


def _blocked_from_preview(preview: dict[str, Any], status: str) -> dict[str, Any]:
    evidence = _as_dict(preview.get("evidence"))
    diagnostics = _as_list(preview.get("diagnostics"))
    issues = _as_list(_as_dict(preview.get("execution_policy_result")).get("issues"))
    if not issues:
        issues = [f"buy candidate preview status is {status}"]
    return _result(
        status=STATUS_BLOCKED if status == STATUS_BLOCKED else STATUS_INVALID,
        buy_candidate_preview=preview,
        evidence=evidence,
        diagnostics=diagnostics,
        issues=issues,
    )


def build_execution_preview_input_from_buy_candidate(
    buy_candidate_preview: Any,
    preview_context: Any = None,
) -> dict[str, Any]:
    """Build a read-only execution preview input from a BUY candidate draft preview."""
    preview = deepcopy(_as_dict(buy_candidate_preview))
    context = deepcopy(_as_dict(preview_context))
    status = str(preview.get("status") or "").strip().upper()
    if not preview:
        return _result(
            status=STATUS_INVALID,
            issues=["buy_candidate_preview must be a non-empty dict"],
            diagnostics=[{"stage": "input", "ok": False, "reason": "missing buy_candidate_preview"}],
        )
    if status in {STATUS_BLOCKED, STATUS_INVALID}:
        return _blocked_from_preview(preview, status)
    if status != STATUS_READY:
        return _result(
            status=STATUS_INVALID,
            buy_candidate_preview=preview,
            evidence=_as_dict(preview.get("evidence")),
            diagnostics=_as_list(preview.get("diagnostics")),
            issues=[f"unsupported buy candidate preview status: {status or '<missing>'}"],
        )

    draft = _as_dict(preview.get("order_candidate_draft"))
    if not draft:
        return _result(
            status=STATUS_INVALID,
            buy_candidate_preview=preview,
            evidence=_as_dict(preview.get("evidence")),
            diagnostics=_as_list(preview.get("diagnostics")),
            issues=["order_candidate_draft is required for READY preview"],
        )
    if draft.get("candidate_version") != EXPECTED_CANDIDATE_VERSION:
        return _result(
            status=STATUS_INVALID,
            buy_candidate_preview=preview,
            evidence=_as_dict(preview.get("evidence")),
            diagnostics=_as_list(preview.get("diagnostics")),
            issues=["unsupported order candidate draft version"],
        )

    candidate_id = _clean_text(draft.get("candidate_id"))
    symbol = _clean_text(draft.get("symbol"))
    side = _clean_text(draft.get("side")).upper()
    if not candidate_id:
        issue = "candidate_id is required"
    elif not symbol:
        issue = "symbol is required"
    elif side != "BUY":
        issue = "side must be BUY"
    else:
        issue = ""
    if issue:
        return _result(
            status=STATUS_INVALID,
            buy_candidate_preview=preview,
            evidence=_as_dict(preview.get("evidence")),
            diagnostics=_as_list(preview.get("diagnostics")),
            issues=[issue],
        )

    execution_snapshot = deepcopy(_as_dict(draft.get("execution_snapshot")))
    order_candidate_input = {
        "input_version": "EXECUTION_PREVIEW_INPUT_FROM_BUY_CANDIDATE_V1",
        "candidate_id": candidate_id,
        "source": ADAPTER_TYPE,
        "symbol": symbol,
        "code": symbol,
        "side": "BUY",
        "order_type": draft.get("order_type"),
        "price": deepcopy(draft.get("price")),
        "budget": deepcopy(draft.get("budget")),
        "quantity_policy": draft.get("quantity_policy"),
        "next_buy_round": deepcopy(draft.get("next_buy_round")),
        "is_last_round": deepcopy(draft.get("is_last_round")),
        "hoga_mode": draft.get("hoga_mode"),
        "hoga_up": deepcopy(draft.get("hoga_up")),
        "hoga_down": deepcopy(draft.get("hoga_down")),
        "source_signal_id": draft.get("source_signal_id"),
        "policy_version": draft.get("policy_version"),
        "execution_snapshot": execution_snapshot,
        "preview_only": True,
    }
    execution_preview_context = {
        "context_version": "BUY_CANDIDATE_EXECUTION_PREVIEW_CONTEXT_V1",
        "ready_for_execution_preview": True,
        "order_candidate_input": deepcopy(order_candidate_input),
        "candidate_id": candidate_id,
        "execution_snapshot": execution_snapshot,
        "evidence": deepcopy(_as_dict(preview.get("evidence"))),
        "diagnostics": _as_list(preview.get("diagnostics")),
        "extra_context": context,
    }
    return _result(
        status=STATUS_READY,
        order_candidate_input=order_candidate_input,
        execution_preview_context=execution_preview_context,
        buy_candidate_preview=preview,
        evidence=_as_dict(preview.get("evidence")),
        diagnostics=_as_list(preview.get("diagnostics")),
    )
