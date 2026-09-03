# -*- coding: utf-8 -*-
"""Pure execution provenance contract helpers.

The helpers in this module normalize and validate in-memory provenance data.
They never read or write Runtime files and never call a broker.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


PROVENANCE_CONTRACT_VERSION = 1
SINGLE_CHILD_KIND = "SINGLE_ORDER"
ALLOWED_CHILD_KINDS = {
    SINGLE_CHILD_KIND,
    "TIME_SLICE",
    "HOGA_LEVEL",
    "POINT_SLICE",
    "RATIO_SLICE",
    "CANCEL",
    "MODIFY",
}
SOURCE_ROUTINE_SIGNAL = "ROUTINE_SIGNAL"
SOURCE_OPERATION_COMMAND = "OPERATION_COMMAND"

_OPTION_GROUP_FIELDS = {
    "hoga": ("hoga_mode", "order_price_basis", "hoga_up", "hoga_down"),
    "point": ("point_mode", "point_value", "point_unit", "point_range", "point_count"),
    "ratio": (
        "ratio_left",
        "ratio_right",
        "ratio_direction",
        "ratio_value",
        "ratio_compare",
        "ratio_count",
    ),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def plan_generation(value: Any) -> int:
    """Read the optional generation field; legacy records belong to generation 0."""
    if value in (None, ""):
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("INVALID_PLAN_GENERATION")
    return value


def canonical_normalize(value: Any) -> Any:
    """Return a JSON-stable copy while removing absent optional values."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            normalized = canonical_normalize(value[key])
            if normalized in (None, "", {}, []):
                continue
            result[str(key)] = normalized
        return result
    if isinstance(value, (list, tuple)):
        return [canonical_normalize(item) for item in value]
    return deepcopy(value)


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        canonical_normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def option_snapshot_hash(option_snapshot: Any) -> str:
    """Hash semantic option content; approval timestamps are evidence only."""
    semantic = canonical_normalize(option_snapshot)
    if isinstance(semantic, dict):
        semantic.pop("approved_at", None)
    return stable_hash(semantic)


def build_option_snapshot(execution_request: Any, *, approved_at: str) -> dict[str, Any]:
    request = _as_dict(execution_request)
    intent = _as_dict(request.get("execution_intent"))
    routine = _as_dict(request.get("routine_provenance"))
    approved_options = _as_dict(intent.get("approved_execution_options"))
    policy_evidence = _as_dict(intent.get("execution_snapshot"))

    source_kind = _text(request.get("source_kind")) or SOURCE_ROUTINE_SIGNAL
    snapshot: dict[str, Any] = {
        "provenance_contract_version": PROVENANCE_CONTRACT_VERSION,
        "approved_at": approved_at,
        "routine_id": (
            routine.get("routine_id")
            or intent.get("routine_type")
            or routine.get("routine_type")
        ),
        "routine_name": (
            routine.get("routine_name")
            or intent.get("routine_name")
            or routine.get("routine_type")
        ),
        "routine_instance_id": (
            routine.get("routine_instance_id")
            or intent.get("routine_instance_id")
        ),
        "source_kind": source_kind,
        "side": intent.get("side") or _as_dict(request.get("request_preview")).get("side"),
        "execution_mode": intent.get("execution_mode") or "SINGLE",
        "policy_name": intent.get("policy_name"),
        "policy_version": intent.get("policy_version"),
        "policy_hash": policy_evidence.get("policy_hash"),
        "approved_rule_hash": policy_evidence.get("approved_rule_hash"),
        "runtime_state_hash": policy_evidence.get("runtime_state_hash"),
        "calculation_hash": policy_evidence.get("calculation_hash"),
        "buy_phase": intent.get("buy_phase"),
        "buy_round": intent.get("buy_round"),
        "unfilled_timeout_policy": intent.get("unfilled_timeout_policy"),
    }
    if source_kind == SOURCE_OPERATION_COMMAND:
        snapshot["source_command_id"] = request.get("source_command_id")
    else:
        snapshot["source_signal_id"] = request.get("source_signal_id")

    for group_name, fields in _OPTION_GROUP_FIELDS.items():
        group = {
            field: approved_options.get(field, intent.get(field))
            for field in fields
            if approved_options.get(field, intent.get(field)) not in (None, "")
        }
        if group:
            snapshot[group_name] = group
    return canonical_normalize(snapshot)


def build_process_plan(execution_request: Any) -> dict[str, Any]:
    request = _as_dict(execution_request)
    intent = _as_dict(request.get("execution_intent"))
    request_preview = _as_dict(request.get("request_preview"))
    multi_plan = (_as_dict(intent.get("multi_time_plan")) or _as_dict(intent.get("multi_hoga_plan"))
                  or _as_dict(intent.get("multi_ratio_plan")))
    return canonical_normalize(
        {
            "approved_budget": multi_plan.get("approved_round_budget", intent.get("budget")),
            "planned_total_quantity": intent.get(
                "planned_total_quantity",
                intent.get("quantity", request_preview.get("quantity")),
            ),
            "price_basis": intent.get("price_basis"),
            "execution_trade_date": (
                intent.get("execution_trade_date") or request.get("execution_trade_date")
            ),
        }
    )


def build_single_child(execution_request: Any) -> dict[str, Any]:
    request = _as_dict(execution_request)
    request_preview = _as_dict(request.get("request_preview"))
    action = _text(request_preview.get("order_action")).upper()
    child_kind = action if action in {"CANCEL", "MODIFY"} else SINGLE_CHILD_KIND
    return canonical_normalize(
        {
            "execution_id": request.get("execution_id"),
            "child_sequence_index": 1,
            "child_sequence_total": 1,
            "child_kind": child_kind,
            "child_plan": {
                "planned_quantity": request_preview.get("quantity"),
                "planned_price": request_preview.get("price"),
            },
        }
    )


def build_execution_child(execution_request: Any) -> dict[str, Any]:
    """Preserve a validated preplanned child, otherwise build the legacy child."""
    request = _as_dict(execution_request)
    child_plan = request.get("child_plan")
    has_preplanned_child = any(
        request.get(field) not in (None, "")
        for field in (
            "child_sequence_index",
            "child_sequence_total",
            "child_kind",
            "child_plan",
        )
    )
    if not has_preplanned_child:
        return build_single_child(request)
    return canonical_normalize(
        {
            "execution_id": request.get("execution_id"),
            "plan_generation": plan_generation(request.get("plan_generation")),
            "child_sequence_index": request.get("child_sequence_index"),
            "child_sequence_total": request.get("child_sequence_total"),
            "child_kind": request.get("child_kind"),
            "child_plan": deepcopy(child_plan) if isinstance(child_plan, dict) else child_plan,
        }
    )


def materialize_execution_intent_children(
    execution_intents: Any,
    *,
    source_signal_id: Any,
    execution_process_id: Any = None,
    plan_generation_value: Any = None,
) -> list[dict[str, Any]]:
    """Assign one process identity and unique child identities to a plan."""
    if not isinstance(execution_intents, list) or not execution_intents:
        raise ValueError("EXECUTION_INTENT_CHILDREN_REQUIRED")
    signal_id = _text(source_signal_id)
    if not signal_id:
        raise ValueError("SOURCE_SIGNAL_ID_REQUIRED")
    intents = [deepcopy(_as_dict(item)) for item in execution_intents]
    if any(not item for item in intents):
        raise ValueError("EXECUTION_INTENT_CHILD_INVALID")

    total = len(intents)
    indexes = {item.get("child_sequence_index") for item in intents}
    totals = {item.get("child_sequence_total") for item in intents}
    if total > 1 or any(item.get("child_kind") == "HOGA_LEVEL" for item in intents):
        if indexes != set(range(1, total + 1)) or totals != {total}:
            raise ValueError("EXECUTION_INTENT_CHILD_SEQUENCE_INVALID")

    first = intents[0]
    generations = {
        plan_generation(
            plan_generation_value
            if plan_generation_value not in (None, "")
            else item.get("plan_generation")
        )
        for item in intents
    }
    if len(generations) != 1:
        raise ValueError("EXECUTION_INTENT_PLAN_GENERATION_MISMATCH")
    generation = next(iter(generations))
    process_digest = stable_hash(
        {
            "source_signal_id": signal_id,
            "routine_type": first.get("routine_type"),
            "routine_instance_id": first.get("routine_instance_id"),
            "cycle_identity": first.get("cycle_identity"),
            "side": first.get("side"),
            "execution_mode": first.get("execution_mode"),
            "sell_method_set": first.get("sell_method_set"),
            "multi_hoga_plan": first.get("multi_hoga_plan"),
            "multi_time_plan": first.get("multi_time_plan"),
            "multi_ratio_plan": first.get("multi_ratio_plan"),
            "unfilled_timeout_policy": first.get("unfilled_timeout_policy"),
        }
    )[:24].upper()
    requested_process_id = _text(execution_process_id)
    process_id = requested_process_id or f"EXEC_PROCESS_{process_digest}"
    for index, intent in enumerate(intents, start=1):
        existing_signal_id = _text(intent.get("source_signal_id"))
        if existing_signal_id and existing_signal_id != signal_id:
            raise ValueError("EXECUTION_INTENT_SOURCE_SIGNAL_ID_MISMATCH")
        existing_process_id = _text(intent.get("execution_process_id"))
        if existing_process_id and existing_process_id != process_id:
            raise ValueError("EXECUTION_INTENT_PROCESS_ID_MISMATCH")
        intent["source_signal_id"] = signal_id
        intent["execution_process_id"] = process_id
        intent["plan_generation"] = generation
        intent["execution_id"] = (
            f"EXEC_CHILD_{stable_hash(process_id)[:16].upper()}"
            f"_G{generation:03d}_{index:03d}"
        )
    return intents


def build_execution_process_id(order_id: Any, request_hash: Any, snapshot_hash: Any) -> str:
    digest = stable_hash(
        {
            "order_id": _text(order_id),
            "request_hash": _text(request_hash),
            "option_snapshot_hash": _text(snapshot_hash),
        }
    )[:24].upper()
    return f"EXEC_PROCESS_{digest}"


def validate_process_record(value: Any) -> list[str]:
    record = _as_dict(value)
    if not record:
        return ["PROCESS_RECORD_MUST_BE_OBJECT"]
    issues: list[str] = []
    for field in ("execution_process_id", "option_snapshot_hash"):
        if not _text(record.get(field)):
            issues.append(f"MISSING_{field.upper()}")
    if record.get("provenance_contract_version") != PROVENANCE_CONTRACT_VERSION:
        issues.append("INVALID_PROVENANCE_CONTRACT_VERSION")
    snapshot = record.get("option_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        issues.append("MISSING_OPTION_SNAPSHOT")
    elif _text(record.get("option_snapshot_hash")) != option_snapshot_hash(snapshot):
        issues.append("OPTION_SNAPSHOT_HASH_MISMATCH")
    return issues


def validate_child_contract(value: Any) -> list[str]:
    child = _as_dict(value)
    if not child:
        return ["CHILD_CONTRACT_MUST_BE_OBJECT"]
    issues: list[str] = []
    try:
        plan_generation(child.get("plan_generation"))
    except ValueError:
        issues.append("INVALID_PLAN_GENERATION")
    for field in ("execution_process_id", "execution_id", "child_kind"):
        if not _text(child.get(field)):
            issues.append(f"MISSING_{field.upper()}")
    index = child.get("child_sequence_index")
    total = child.get("child_sequence_total")
    if not isinstance(index, int) or isinstance(index, bool) or index <= 0:
        issues.append("INVALID_CHILD_SEQUENCE_INDEX")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        issues.append("INVALID_CHILD_SEQUENCE_TOTAL")
    if isinstance(index, int) and isinstance(total, int) and index > total:
        issues.append("CHILD_SEQUENCE_INDEX_EXCEEDS_TOTAL")
    if _text(child.get("child_kind")) not in ALLOWED_CHILD_KINDS:
        issues.append("INVALID_CHILD_KIND")
    if not isinstance(child.get("child_plan"), dict):
        issues.append("CHILD_PLAN_MUST_BE_OBJECT")
    return issues


def validate_child_set(children: Any) -> list[str]:
    if not isinstance(children, list) or not children:
        return ["CHILDREN_MUST_BE_NONEMPTY_LIST"]
    issues: list[str] = []
    normalized = [_as_dict(child) for child in children]
    for child in normalized:
        issues.extend(validate_child_contract(child))
    process_ids = {_text(child.get("execution_process_id")) for child in normalized}
    if len(process_ids) != 1:
        issues.append("CHILD_PROCESS_ID_MISMATCH")
    by_generation: dict[int, list[dict[str, Any]]] = {}
    for child in normalized:
        try:
            generation = plan_generation(child.get("plan_generation"))
        except ValueError:
            continue
        by_generation.setdefault(generation, []).append(child)
    for generation, generation_children in by_generation.items():
        totals = {child.get("child_sequence_total") for child in generation_children}
        indexes = {child.get("child_sequence_index") for child in generation_children}
        if totals != {len(generation_children)}:
            issues.append(f"CHILD_SEQUENCE_TOTAL_MISMATCH_GENERATION_{generation}")
        if indexes != set(range(1, len(generation_children) + 1)):
            issues.append(f"CHILD_SEQUENCE_GAP_OR_DUPLICATE_GENERATION_{generation}")
    return issues


def same_payload(left: Any, right: Any) -> bool:
    return canonical_normalize(left) == canonical_normalize(right)
