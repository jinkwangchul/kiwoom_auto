"""Generic Main executor for validated routine lifecycle decisions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from routine_lifecycle_decision import validate_routine_lifecycle_decision
from routine_signal_consumer import (
    block_signal_pre_dispatch_orders,
    enqueue_replanned_execution_intents,
)
from routine_signal_queue import STATUS_CANCELLED, STATUS_DONE, STATUS_PENDING, STATUS_PREVIEWED, update_signal_status


def _text(value: Any) -> str:
    return str(value or "").strip()


def _proposal(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    proposal = payload.get("proposal")
    return proposal if isinstance(proposal, dict) else {}


def _signal_id(proposal: dict[str, Any]) -> str:
    signal = proposal.get("signal")
    signal = signal if isinstance(signal, dict) else {}
    return _text(
        proposal.get("source_signal_id")
        or proposal.get("id")
        or signal.get("id")
    )


def _create_generation(decision: dict[str, Any]) -> dict[str, Any]:
    proposal = _proposal(decision)
    signal = proposal.get("signal")
    signal = deepcopy(signal) if isinstance(signal, dict) else {}
    intents = proposal.get("execution_intents")
    intents = [deepcopy(item) for item in intents] if isinstance(intents, list) else []
    signal_id = _signal_id(proposal)
    if not signal_id or not signal or not intents or any(not isinstance(item, dict) for item in intents):
        return {"ok": False, "reason": "CREATE_GENERATION_PAYLOAD_INVALID"}
    metadata = {
        "execution_intent": deepcopy(intents[0]),
        "execution_intents": deepcopy(intents),
        "routine_lifecycle_decision": {
            "decision_hash": decision.get("decision_hash"),
            "facts_revision": decision.get("facts_revision"),
            "phase": decision.get("payload", {}).get("phase"),
        },
    }
    persisted = update_signal_status(signal_id, STATUS_PENDING, metadata=metadata)
    if persisted.get("ok") is not True:
        return {"ok": False, "reason": persisted.get("reason") or "GENERATION_SIGNAL_UPDATE_FAILED"}
    if intents[0].get("deferred_dispatch") is True:
        return {"ok": True, "deferred": True, "orders_created": 0, "executable_order_ids": []}
    result = enqueue_replanned_execution_intents(signal, intents, apply_approval=True)
    if result.get("ok") is not True:
        return result
    completed = update_signal_status(
        signal_id,
        STATUS_PREVIEWED,
        metadata={"routine_lifecycle_decision_hash": decision.get("decision_hash")},
    )
    if completed.get("ok") is not True:
        result["ok"] = False
        result["reason"] = completed.get("reason") or "GENERATION_COMPLETION_UPDATE_FAILED"
    return result


def _execute_write_contract(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    contract = payload.get("write_contract")
    contract = contract if isinstance(contract, dict) else {}
    signal_id = _text(contract.get("signal_id"))
    status = _text(contract.get("status")).upper()
    metadata = contract.get("metadata")
    if not signal_id or status not in {STATUS_PENDING, STATUS_PREVIEWED, STATUS_DONE, STATUS_CANCELLED} or not isinstance(metadata, dict):
        return {"ok": False, "reason": "ROUTINE_WRITE_CONTRACT_INVALID"}
    return update_signal_status(signal_id, status, metadata=deepcopy(metadata))


def execute_routine_lifecycle_decision(
    decision: dict[str, Any],
    *,
    main_facts: dict[str, Any],
    cancel_requester: Any = None,
    review_marker: Any = None,
    stock_entry: Any = None,
) -> dict[str, Any]:
    """Validate one command against its exact facts revision, then use Main writers."""
    valid, reason = validate_routine_lifecycle_decision(decision, main_facts=main_facts)
    if not valid:
        command = "STOP_AND_REVIEW"
        decision = {
            **(decision if isinstance(decision, dict) else {}),
            "command": command,
            "reason": reason,
        }
    else:
        command = _text(decision.get("command")).upper()
    proposal = _proposal(decision)
    if command == "WAIT":
        return {"ok": True, "mutated": False, "waiting": True, "reason": decision.get("reason")}
    if command == "ADMIT_SIGNAL":
        return {"ok": True, "mutated": False, "admitted": True}
    if command == "IGNORE_SIGNAL":
        result = update_signal_status(
            _signal_id(proposal), STATUS_CANCELLED,
            metadata={"routine_lifecycle_decision_hash": decision.get("decision_hash"),
                      "reason_code": decision.get("reason")},
        )
        return {**result, "mutated": result.get("ok") is True}
    if command == "BLOCK_PROCESS_DISPATCH":
        result = block_signal_pre_dispatch_orders(_signal_id(proposal))
        return {"ok": result.get("committed") is True, "mutated": result.get("committed") is True, **result}
    if command == "REQUEST_CANCEL":
        if not callable(cancel_requester):
            return {"ok": False, "mutated": False, "reason": "CANCEL_WRITER_UNAVAILABLE"}
        request = proposal.get("execution_request")
        preview = request.get("request_preview") if isinstance(request, dict) else {}
        preview = preview if isinstance(preview, dict) else {}
        result = cancel_requester(
            _text(proposal.get("order_queued_id") or proposal.get("id") or proposal.get("order_id")),
            expected_account_no=_text(proposal.get("account_no") or preview.get("account_no")),
            expected_code=_text(proposal.get("code") or preview.get("code")),
            expected_side=_text(proposal.get("side") or preview.get("side")),
            expected_broker_order_no=_text(proposal.get("broker_order_no")),
            cancel_evidence={
                "trigger": "ROUTINE_LIFECYCLE_DECISION",
                "decision_hash": decision.get("decision_hash"),
                "facts_revision": decision.get("facts_revision"),
            },
        )
        return {**(result if isinstance(result, dict) else {}), "mutated": bool(result)}
    if command == "CREATE_GENERATION":
        result = _create_generation(decision)
        return {**result, "mutated": result.get("ok") is True}
    if command == "REQUEST_FINAL_LIQUIDATION":
        if _proposal(decision).get("execution_intents"):
            result = _create_generation(decision)
        else:
            result = _execute_write_contract(decision)
        return {**result, "mutated": result.get("ok") is True}
    if command in {"COMPLETE_BUY_ROUND", "COMPLETE_PROCESS", "CARRY_POSITION"}:
        result = _execute_write_contract(decision)
        return {**result, "mutated": result.get("ok") is True}
    if command == "STOP_AND_REVIEW":
        if not callable(review_marker) or stock_entry is None:
            return {"ok": False, "mutated": False, "reason": reason or "REVIEW_WRITER_UNAVAILABLE"}
        review = {
            "review_reasons": [_text(decision.get("reason")) or "ROUTINE_LIFECYCLE_CONTRACT_INVALID"],
            "review_location": "ROUTINE_LIFECYCLE_DECISION",
            "decision_hash": decision.get("decision_hash"),
            "facts_revision": decision.get("facts_revision"),
        }
        marked = review_marker(
            stock_entry.stock_dir, stock_entry.stock_code, stock_entry.stock_name,
            review, source="ROUTINE_LIFECYCLE_DECISION",
        )
        return {"ok": bool(marked), "mutated": bool(marked), "review_created": bool(marked)}
    return {"ok": False, "mutated": False, "reason": "ROUTINE_LIFECYCLE_COMMAND_UNHANDLED"}
