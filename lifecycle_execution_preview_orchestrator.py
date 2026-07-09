# -*- coding: utf-8 -*-
"""Preview-only execution preview orchestrator.

This module is a single entry point that calls the already-implemented
Execution Preview Phase1 + Phase2 chain in order:

    Execution Transaction Contract
    -> Execution Engine Preview
    -> Broker Adapter Contract Preview
    -> Order Router Contract Preview
    -> SendOrder Contract Preview
    -> SendOrder Call Preview
    -> SendOrder Result Review Preview
    -> Execution Final Approval Preview
    -> Execution Dispatcher Preview
    -> Execution Commit Preview
    -> Execution Runtime Apply Preview

It does NOT create any new detailed preview module or policy. It only invokes
the existing modules in sequence, records each step result, stops the chain
when a step becomes BLOCKED or INVALID, and aggregates the existing modules'
status and safety flags into a summary and final decision.

All safety flags are fixed to False and preview_only is fixed to True.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from lifecycle_execution_transaction_contract import (
    STATUS_READY as TRANSACTION_READY,
    build_execution_transaction_contract,
)
from lifecycle_execution_engine_preview import (
    STATUS_ENGINE_PREVIEW_READY as ENGINE_READY,
    build_execution_engine_preview,
)
from lifecycle_execution_broker_adapter_contract_preview import (
    STATUS_READY as ADAPTER_READY,
    build_execution_broker_adapter_contract_preview,
)
from lifecycle_execution_order_router_contract_preview import (
    STATUS_READY as ROUTER_READY,
    build_execution_order_router_contract_preview,
)
from lifecycle_execution_sendorder_contract_preview import (
    STATUS_READY as SENDORDER_CONTRACT_READY,
    build_execution_sendorder_contract_preview,
)
from lifecycle_execution_sendorder_call_preview import (
    STATUS_READY as SENDORDER_CALL_READY,
    build_execution_sendorder_call_preview,
)
from lifecycle_execution_sendorder_result_review_preview import (
    STATUS_READY as RESULT_REVIEW_READY,
    build_execution_sendorder_result_review_preview,
)
from lifecycle_execution_final_approval_preview import (
    STATUS_READY as FINAL_APPROVAL_READY,
    build_execution_final_approval_preview,
)
from lifecycle_execution_dispatcher_preview import (
    STATUS_READY as DISPATCHER_READY,
    build_execution_dispatcher_preview,
)
from lifecycle_execution_commit_preview import (
    STATUS_READY as COMMIT_READY,
    build_execution_commit_preview,
)
from lifecycle_execution_runtime_apply_preview import (
    STATUS_READY as RUNTIME_APPLY_READY,
    build_execution_runtime_apply_preview,
)


PREVIEW_TYPE = "LIFECYCLE_EXECUTION_PREVIEW_ORCHESTRATOR"
STATUS_READY = "ORCHESTRATOR_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# Ordered chain definition: (step_name, build_func_name, ready_status)
# build_func_name is resolved from module globals at call time so that
# unittest.mock.patch on this module's attributes takes effect.
_STEPS = [
    ("execution_transaction_contract", "build_execution_transaction_contract", TRANSACTION_READY),
    ("execution_engine_preview", "build_execution_engine_preview", ENGINE_READY),
    ("broker_adapter_contract_preview", "build_execution_broker_adapter_contract_preview", ADAPTER_READY),
    ("order_router_contract_preview", "build_execution_order_router_contract_preview", ROUTER_READY),
    ("sendorder_contract_preview", "build_execution_sendorder_contract_preview", SENDORDER_CONTRACT_READY),
    ("sendorder_call_preview", "build_execution_sendorder_call_preview", SENDORDER_CALL_READY),
    ("sendorder_result_review_preview", "build_execution_sendorder_result_review_preview", RESULT_REVIEW_READY),
    ("execution_final_approval_preview", "build_execution_final_approval_preview", FINAL_APPROVAL_READY),
    ("execution_dispatcher_preview", "build_execution_dispatcher_preview", DISPATCHER_READY),
    ("execution_commit_preview", "build_execution_commit_preview", COMMIT_READY),
    ("execution_runtime_apply_preview", "build_execution_runtime_apply_preview", RUNTIME_APPLY_READY),
]


SAFETY_FLAGS = (
    "execution_allowed",
    "execution_started",
    "execution_completed",
    "dispatch_allowed",
    "dispatch_started",
    "dispatch_completed",
    "send_order_called",
    "send_order_result_recorded",
    "recorder_called",
    "chejan_called",
    "runtime_write",
    "position_write",
    "balance_write",
    "audit_write",
    "file_write_called",
    "gui_update_called",
    "backup_created",
    "rollback_executed",
)


def _build_step_record(
    step_index: int,
    step_name: str,
    result: dict[str, Any],
    ready_status: str,
) -> dict[str, Any]:
    status = _text(result.get("status")).upper()
    return {
        "step_index": step_index,
        "step_name": step_name,
        "status": status,
        "preview_only": result.get("preview_only") is True,
        "completed": status == ready_status,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
    }


def _build_orchestrator_summary(
    steps: list[dict[str, Any]],
    final_status: str,
) -> dict[str, Any]:
    completed_steps = sum(1 for step in steps if step["completed"])
    blocked_steps = [step for step in steps if step["blocked"]]
    invalid_steps = [step for step in steps if step["invalid"]]

    blocked_step = blocked_steps[0]["step_name"] if blocked_steps else ""
    invalid_step = invalid_steps[0]["step_name"] if invalid_steps else ""

    return {
        "total_steps": len(steps),
        "completed_steps": completed_steps,
        "blocked_step": blocked_step,
        "invalid_step": invalid_step,
        "final_status": final_status,
        "preview_only": True,
    }


def _build_final_orchestrator_decision(
    steps: list[dict[str, Any]],
    final_status: str,
) -> dict[str, Any]:
    all_ready = final_status == STATUS_READY and bool(steps) and all(
        step["completed"] for step in steps
    )
    return {
        "approved": all_ready,
        "execution_allowed": False,
        "runtime_write": False,
        "send_order_called": False,
        "preview_only": True,
    }


def _result(
    *,
    status: str,
    steps: list[dict[str, Any]],
    failed_step: str,
    orchestrator_summary: dict[str, Any],
    final_orchestrator_decision: dict[str, Any],
    step_results: dict[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "status": status,
        "preview_only": True,
        "execution_allowed": False,
        "execution_started": False,
        "execution_completed": False,
        "dispatch_allowed": False,
        "dispatch_started": False,
        "dispatch_completed": False,
        "send_order_called": False,
        "send_order_result_recorded": False,
        "recorder_called": False,
        "chejan_called": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "gui_update_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "orchestrator_steps": list(steps),
        "failed_step": failed_step,
        "orchestrator_summary": deepcopy(orchestrator_summary),
        "final_orchestrator_decision": deepcopy(final_orchestrator_decision),
        "step_results": deepcopy(step_results),
        "generated_at": now or _now_text(),
    }


def build_execution_preview_orchestrator(
    readiness_gate_preview: Any,
    orchestrator_context: Any = None,
) -> dict[str, Any]:
    """Run the full Execution Preview chain in order and aggregate results.

    Stops calling subsequent steps when a step becomes BLOCKED or INVALID.
    Aggregates existing modules' status and safety flags into a summary and
    final decision. Never creates new preview modules or policies, never
    writes runtime files, modifies routines/*/rules.json, writes SQLite,
    calls SendOrder, connects brokers/Chejan, updates GUI, or commits Git.
    """
    context = deepcopy(_as_dict(orchestrator_context))
    now = _text(context.get("generated_at")) or _now_text()

    steps: list[dict[str, Any]] = []
    step_results: dict[str, Any] = {}

    current_input: Any = deepcopy(_as_dict(readiness_gate_preview))
    failed_step = ""
    final_status = STATUS_READY

    for index, (step_name, build_func_name, _ready_status) in enumerate(_STEPS, start=1):
        build_func = globals()[build_func_name]
        result = build_func(current_input, context)
        result = _as_dict(result)
        step_record = _build_step_record(index, step_name, result, _ready_status)
        steps.append(step_record)
        step_results[step_name] = deepcopy(result)

        status = step_record["status"]
        if status == STATUS_BLOCKED:
            failed_step = step_name
            final_status = STATUS_BLOCKED
            break
        if status == STATUS_INVALID:
            failed_step = step_name
            final_status = STATUS_INVALID
            break

        # Feed this step's result into the next step.
        current_input = result

    orchestrator_summary = _build_orchestrator_summary(steps, final_status)
    final_orchestrator_decision = _build_final_orchestrator_decision(steps, final_status)

    return _result(
        status=final_status,
        steps=steps,
        failed_step=failed_step,
        orchestrator_summary=orchestrator_summary,
        final_orchestrator_decision=final_orchestrator_decision,
        step_results=step_results,
        now=now,
    )
