# -*- coding: utf-8 -*-
"""Preview orchestrator for Execution Runtime Catalog validation.

This layer only chains the catalog preview builder and validator. It never
connects to full preview orchestration, GUI, queue commit, runtime write, or
SendOrder components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_catalog_preview import build_execution_runtime_catalog_preview
from execution_runtime_catalog_validator import validate_execution_runtime_catalog_preview


ORCHESTRATOR_TYPE = "EXECUTION_RUNTIME_CATALOG_ORCHESTRATOR_PREVIEW"


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _final_status(catalog_preview: dict[str, Any], validation: dict[str, Any]) -> str:
    catalog_status = catalog_preview.get("status")
    if catalog_status == "INVALID":
        return "INVALID"
    if validation.get("valid") is not True:
        return "INVALID"
    if catalog_status == "BLOCKED":
        return "BLOCKED"
    if catalog_status == "READY":
        return "READY"
    return "INVALID"


def run_execution_runtime_catalog_orchestrator_preview(
    *,
    execution_request_preview: Any = None,
    lock_preview: Any = None,
    request_hash_preview: Any = None,
    queue_write_preview_result: Any = None,
    order_candidate: Any = None,
) -> dict[str, Any]:
    """Build and validate a Runtime Catalog Preview without side effects."""
    catalog_preview = build_execution_runtime_catalog_preview(
        execution_request_preview=execution_request_preview,
        lock_preview=lock_preview,
        request_hash_preview=request_hash_preview,
        queue_write_preview_result=queue_write_preview_result,
        order_candidate=order_candidate,
    )
    validation = validate_execution_runtime_catalog_preview(catalog_preview)
    status = _final_status(catalog_preview, validation)
    issues = _unique(
        list(catalog_preview.get("issues") or []) + list(validation.get("issues") or [])
    )
    warnings = _unique(
        list(catalog_preview.get("warnings") or []) + list(validation.get("warnings") or [])
    )

    return {
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "catalog_preview": deepcopy(catalog_preview),
        "validation": deepcopy(validation),
        "issues": issues,
        "warnings": warnings,
    }
