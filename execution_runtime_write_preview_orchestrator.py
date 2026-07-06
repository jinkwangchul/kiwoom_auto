# -*- coding: utf-8 -*-
"""Orchestrator for Execution Runtime Write Preview validation.

This layer only chains write preview construction and validation. It does not
create runtime files, write files, create directories, commit queues, or call
execution/order components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_write_preview import build_execution_runtime_write_preview
from execution_runtime_write_preview_validator import validate_execution_runtime_write_preview


ORCHESTRATOR_TYPE = "EXECUTION_RUNTIME_WRITE_PREVIEW_ORCHESTRATOR"


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _final_status(write_preview: dict[str, Any], validation: dict[str, Any]) -> str:
    preview_status = write_preview.get("status")
    if preview_status == "INVALID":
        return "INVALID"
    if validation.get("valid") is not True:
        return "INVALID"
    if preview_status == "BLOCKED":
        return "BLOCKED"
    if preview_status == "READY":
        return "READY"
    return "INVALID"


def run_execution_runtime_write_preview_orchestrator(
    catalog_preview: Any = None,
    *,
    catalog_orchestrator_result: Any = None,
    existing_order_executions_data: Any = None,
    existing_order_locks_data: Any = None,
) -> dict[str, Any]:
    """Build and validate an Execution Runtime Write Preview without side effects."""
    write_preview = build_execution_runtime_write_preview(
        catalog_preview,
        catalog_orchestrator_result=catalog_orchestrator_result,
        existing_order_executions_data=existing_order_executions_data,
        existing_order_locks_data=existing_order_locks_data,
    )
    validation = validate_execution_runtime_write_preview(write_preview)
    status = _final_status(write_preview, validation)
    issues = _unique(list(write_preview.get("issues") or []) + list(validation.get("issues") or []))
    warnings = _unique(list(write_preview.get("warnings") or []) + list(validation.get("warnings") or []))

    return {
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "orchestrator_type": ORCHESTRATOR_TYPE,
        "write_preview": deepcopy(write_preview),
        "validation": deepcopy(validation),
        "issues": issues,
        "warnings": warnings,
    }
