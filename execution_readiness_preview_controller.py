# -*- coding: utf-8 -*-
"""Facade for the complete Execution Readiness preview flow.

This controller is the single preview-only entry point for GUI, CLI, console,
debug, and report consumers. It orchestrates the existing preview pipeline,
formatter, and GUI adapter without touching GUI widgets, runtime files, queues,
execution controllers, send-order components, commits, or logs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_readiness_full_preview_formatter import format_execution_readiness_preview
from execution_readiness_full_preview_orchestrator import run_execution_readiness_preview
from execution_readiness_gui_adapter import build_execution_readiness_view_model
from execution_readiness_input_builder import build_execution_readiness_inputs


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
RUNTIME_MANAGER_PREVIEW_MISSING = "RUNTIME_MANAGER_PREVIEW_MISSING"
RUNTIME_MANAGER_PREVIEW_FAILED = "RUNTIME_MANAGER_PREVIEW_FAILED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _status(value: Any) -> str:
    text = "" if value is None else str(value).strip().upper()
    if text in {STATUS_READY, STATUS_BLOCKED, STATUS_INVALID}:
        return text
    return STATUS_INVALID


def _unique_text(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _as_list(value):
            text = str(item)
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
    return result


def build_execution_readiness_preview(
    gate_result: Any,
    order_candidate: Any,
    queue_preview_result: Any,
) -> dict[str, Any]:
    """Build the full preview, formatted text, and GUI ViewModel."""
    preview_result = run_execution_readiness_preview(
        deepcopy(gate_result),
        deepcopy(order_candidate),
        deepcopy(queue_preview_result),
    )
    preview = _as_dict(preview_result)
    formatted_result = format_execution_readiness_preview(preview)
    formatted = _as_dict(formatted_result)
    view_model_result = build_execution_readiness_view_model(formatted)
    view_model = _as_dict(view_model_result)
    status = _status(preview.get("status"))

    return {
        "status": status,
        "completed": preview.get("completed") is True and status == STATUS_READY,
        "summary": str(preview.get("summary", "")),
        "preview_result": deepcopy(preview),
        "formatted_result": deepcopy(formatted),
        "view_model": deepcopy(view_model),
        "warnings": _unique_text(
            preview.get("warnings"),
            formatted.get("warnings"),
            view_model.get("warnings"),
        ),
        "issues": _unique_text(
            preview.get("issues"),
            formatted.get("issues"),
            view_model.get("issues"),
        ),
    }


def _blocked_from_inputs(input_builder_result: dict[str, Any]) -> dict[str, Any]:
    status = _status(input_builder_result.get("status"))
    return {
        "status": status,
        "completed": False,
        "summary": str(input_builder_result.get("summary", "")),
        "preview_result": None,
        "formatted_result": None,
        "view_model": None,
        "input_builder_result": deepcopy(input_builder_result),
        "input_status": status,
        "input_summary": str(input_builder_result.get("summary", "")),
        "warnings": _unique_text(input_builder_result.get("warnings")),
        "issues": _unique_text(input_builder_result.get("issues")),
    }


def _append_warning(result: dict[str, Any], warning: str) -> dict[str, Any]:
    extended = deepcopy(result)
    warnings = list(extended.get("warnings") or [])
    if warning not in warnings:
        warnings.append(warning)
    extended["warnings"] = warnings
    return extended


def _with_runtime_manager_preview(
    result: dict[str, Any],
    *,
    include_runtime_manager_preview: bool,
    runtime_manager: Any,
    runtime_confirmations: Any,
    preview_context: Any,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    if not include_runtime_manager_preview:
        return result

    if _status(result.get("status")) != STATUS_READY:
        return result

    run_dry_run = getattr(runtime_manager, "run_dry_run", None)
    if not callable(run_dry_run):
        return _append_warning(result, RUNTIME_MANAGER_PREVIEW_MISSING)

    context = _as_dict(preview_context)
    try:
        runtime_manager_preview = run_dry_run(
            deepcopy(inputs.get("order_candidate")),
            deepcopy(context.get("guard")),
            deepcopy(runtime_confirmations),
        )
    except Exception as exc:  # pragma: no cover - defensive extension boundary
        return _append_warning(result, f"{RUNTIME_MANAGER_PREVIEW_FAILED}: {exc}")

    extended = deepcopy(result)
    preview_result = _as_dict(extended.get("preview_result"))
    extensions = _as_dict(preview_result.get("extensions"))
    extensions["runtime_manager_preview"] = deepcopy(runtime_manager_preview)
    preview_result["extensions"] = extensions
    extended["preview_result"] = preview_result
    return extended


def build_execution_readiness_preview_from_context(
    *,
    order_id: Any = None,
    preview_context: Any = None,
    include_runtime_manager_preview: bool = False,
    runtime_manager: Any = None,
    runtime_confirmations: Any = None,
) -> dict[str, Any]:
    """Build the full preview through the input builder context entry point."""
    input_builder_result = build_execution_readiness_inputs(
        order_id=deepcopy(order_id),
        preview_context=deepcopy(preview_context),
    )
    inputs = _as_dict(input_builder_result)
    input_status = _status(inputs.get("status"))
    if input_status != STATUS_READY:
        return _blocked_from_inputs(inputs)

    result = build_execution_readiness_preview(
        inputs.get("gate_result"),
        inputs.get("order_candidate"),
        inputs.get("queue_preview_result"),
    )
    status = _status(result.get("status"))
    result_with_inputs = {
        **result,
        "status": status,
        "completed": result.get("completed") is True and status == STATUS_READY,
        "input_builder_result": deepcopy(inputs),
        "input_status": input_status,
        "input_summary": str(inputs.get("summary", "")),
        "warnings": _unique_text(inputs.get("warnings"), result.get("warnings")),
        "issues": _unique_text(inputs.get("issues"), result.get("issues")),
    }
    return _with_runtime_manager_preview(
        result_with_inputs,
        include_runtime_manager_preview=include_runtime_manager_preview,
        runtime_manager=runtime_manager,
        runtime_confirmations=runtime_confirmations,
        preview_context=preview_context,
        inputs=inputs,
    )
