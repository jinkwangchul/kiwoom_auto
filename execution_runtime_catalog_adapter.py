# -*- coding: utf-8 -*-
"""Adapter for Execution Runtime Catalog Orchestrator preview results.

This module converts the independent runtime catalog orchestrator result into
a simple preview payload for future readiness/GUI display layers. It performs
no filesystem writes and does not connect to execution, queue, GUI, or SendOrder
components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ADAPTER_TYPE = "EXECUTION_RUNTIME_CATALOG_ADAPTER_PREVIEW"
DISPLAY_STATUS = {
    "READY": "Execution runtime catalog ready",
    "BLOCKED": "Execution runtime catalog blocked",
    "INVALID": "Execution runtime catalog invalid",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _status(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text in DISPLAY_STATUS else "INVALID"


def _summary_lines(
    *,
    status: str,
    display_status: str,
    execution_id: Any,
    order_id: Any,
    request_hash: Any,
    lock_id: Any,
    issues: list[Any],
) -> list[str]:
    lines = [
        "Execution Runtime Catalog Preview",
        f"Status: {status}",
        f"Display: {display_status}",
        f"Execution ID: {execution_id or 'None'}",
        f"Order ID: {order_id or 'None'}",
        f"Request Hash: {request_hash or 'None'}",
        f"Lock ID: {lock_id or 'None'}",
    ]
    if issues:
        lines.append("Issues: " + ", ".join(str(issue) for issue in issues))
    else:
        lines.append("Issues: None")
    return lines


def adapt_execution_runtime_catalog_for_readiness(
    catalog_orchestrator_result: Any,
) -> dict[str, Any]:
    """Convert a catalog orchestrator result into a display-ready payload."""
    orchestrator = _as_dict(catalog_orchestrator_result)
    catalog_preview = _as_dict(orchestrator.get("catalog_preview"))
    malformed = not isinstance(catalog_orchestrator_result, dict)
    missing_catalog = isinstance(catalog_orchestrator_result, dict) and not isinstance(
        orchestrator.get("catalog_preview"), dict
    )

    status = _status(orchestrator.get("status"))
    issues = _as_list(orchestrator.get("issues"))
    warnings = _as_list(orchestrator.get("warnings"))
    if malformed:
        issues.append("MALFORMED_ORCHESTRATOR_RESULT")
    if missing_catalog:
        issues.append("MISSING_CATALOG_PREVIEW")
    if malformed or missing_catalog:
        status = "INVALID"

    display_status = DISPLAY_STATUS[status]
    execution_id = catalog_preview.get("execution_id")
    order_id = catalog_preview.get("order_id")
    request_hash = catalog_preview.get("request_hash")
    lock_id = catalog_preview.get("lock_id")
    runtime_targets = (
        deepcopy(catalog_preview.get("runtime_targets"))
        if isinstance(catalog_preview.get("runtime_targets"), dict)
        else {}
    )

    return {
        "adapter_type": ADAPTER_TYPE,
        "preview_only": True,
        "runtime_write": False,
        "status": status,
        "display_status": display_status,
        "execution_id": execution_id,
        "order_id": order_id,
        "request_hash": request_hash,
        "lock_id": lock_id,
        "runtime_targets": runtime_targets,
        "issues": issues,
        "warnings": warnings,
        "summary_lines": _summary_lines(
            status=status,
            display_status=display_status,
            execution_id=execution_id,
            order_id=order_id,
            request_hash=request_hash,
            lock_id=lock_id,
            issues=issues,
        ),
    }
