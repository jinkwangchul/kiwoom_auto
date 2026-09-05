"""Generic immutable Routine -> Main lifecycle decision contract."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from routine_main_facts import validate_routine_main_facts


ROUTINE_LIFECYCLE_COMMANDS = frozenset(
    {
        "WAIT",
        "IGNORE_SIGNAL",
        "ADMIT_SIGNAL",
        "BLOCK_PROCESS_DISPATCH",
        "REQUEST_CANCEL",
        "CREATE_GENERATION",
        "COMPLETE_BUY_ROUND",
        "COMPLETE_PROCESS",
        "CARRY_POSITION",
        "REQUEST_FINAL_LIQUIDATION",
        "STOP_AND_REVIEW",
    }
)
ASYNC_WAIT_REASONS = frozenset(
    {
        "CANCEL_EFFECT_PENDING",
        "CHEJAN_PENDING",
        "HOLDING_RECONCILIATION_PENDING",
        "POSITION_RECONCILIATION_PENDING",
    }
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def build_routine_lifecycle_decision(
    command: str,
    *,
    main_facts: dict[str, Any],
    routine_identity: dict[str, Any],
    stock_code: str,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = str(command or "").strip().upper()
    if normalized not in ROUTINE_LIFECYCLE_COMMANDS:
        raise ValueError("ROUTINE_LIFECYCLE_COMMAND_INVALID")
    valid, facts_reason = validate_routine_main_facts(main_facts)
    if not valid:
        raise ValueError(facts_reason)
    body = {
        "command": normalized,
        "stock_code": str(stock_code or "").strip().lstrip("A"),
        "routine_identity": deepcopy(routine_identity),
        "facts_revision": main_facts["revision"],
        "facts_snapshot_hash": main_facts["snapshot_hash"],
        "reason": str(reason or "").strip(),
        "evidence": deepcopy(evidence or {}),
        "payload": deepcopy(payload or {}),
    }
    if not body["stock_code"] or not body["routine_identity"]:
        raise ValueError("ROUTINE_LIFECYCLE_DECISION_IDENTITY_MISSING")
    body["decision_hash"] = _hash(body)
    return body


def validate_routine_lifecycle_decision(
    value: Any,
    *,
    main_facts: dict[str, Any],
) -> tuple[bool, str]:
    decision = value if isinstance(value, dict) else {}
    command = str(decision.get("command") or "").strip().upper()
    if command not in ROUTINE_LIFECYCLE_COMMANDS:
        return False, "ROUTINE_LIFECYCLE_COMMAND_INVALID"
    valid, reason = validate_routine_main_facts(main_facts)
    if not valid:
        return False, reason
    if (
        decision.get("facts_revision") != main_facts.get("revision")
        or decision.get("facts_snapshot_hash") != main_facts.get("snapshot_hash")
    ):
        return False, "ROUTINE_LIFECYCLE_FACTS_IDENTITY_MISMATCH"
    recorded = str(decision.get("decision_hash") or "").strip()
    body = {key: deepcopy(item) for key, item in decision.items() if key != "decision_hash"}
    if not recorded or recorded != _hash(body):
        return False, "ROUTINE_LIFECYCLE_DECISION_HASH_MISMATCH"
    if not str(decision.get("stock_code") or "").strip() or not isinstance(decision.get("routine_identity"), dict):
        return False, "ROUTINE_LIFECYCLE_DECISION_IDENTITY_MISSING"
    if command == "WAIT" and not str(decision.get("reason") or "").strip():
        return False, "ROUTINE_LIFECYCLE_WAIT_REASON_MISSING"
    return True, ""


def classify_lifecycle_uncertainty(reason: Any) -> str:
    normalized = str(reason or "").strip().upper()
    return "WAIT" if normalized in ASYNC_WAIT_REASONS or normalized.endswith("_PENDING") else "STOP_AND_REVIEW"
