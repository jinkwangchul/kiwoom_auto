"""Indicator Follow ownership of startup recovery classification."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from execution_provenance_contract import validate_child_set
from routine_main_facts import records_from_routine_main_facts


def _text(value: Any) -> str:
    return str(value or "").strip()


def _result(
    classification: str,
    *,
    signal: dict[str, Any],
    main_facts: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "classification": classification,
        "reason": reason,
        "signal_id": _text(signal.get("id")),
        "stock_code": _text(signal.get("code")).lstrip("A"),
        "routine_identity": dict(routine_identity),
        "rules_identity": rules_identity,
        "facts_revision": main_facts.get("revision"),
        "facts_snapshot_hash": main_facts.get("snapshot_hash"),
    }


def classify_startup_recovery(
    *,
    signal_id: str,
    main_facts: dict[str, Any],
    rules: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    """Classify one durable pending plan without exposing its strategy to Main."""
    del rules
    signals, error = records_from_routine_main_facts(main_facts, "signals")
    matches = [row for row in signals if _text(row.get("id")) == _text(signal_id)]
    signal = matches[0] if len(matches) == 1 else {"id": signal_id}
    if error or len(matches) != 1:
        return _result(
            "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
            routine_identity=routine_identity, rules_identity=rules_identity,
            reason=error or "STARTUP_SIGNAL_IDENTITY_INVALID",
        )
    if _text(signal.get("routine_instance_id")) != _text(
        routine_identity.get("routine_instance_id")
    ):
        return _result(
            "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
            routine_identity=routine_identity, rules_identity=rules_identity,
            reason="STARTUP_ROUTINE_IDENTITY_MISMATCH",
        )
    intents = signal.get("execution_intents")
    if not isinstance(intents, list) or not intents or any(
        not isinstance(intent, dict) for intent in intents
    ):
        return _result(
            "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
            routine_identity=routine_identity, rules_identity=rules_identity,
            reason="STARTUP_DEFERRED_PLAN_MISSING",
        )
    modes = {
        (_text(intent.get("execution_mode")).upper(), _text(intent.get("child_kind")).upper())
        for intent in intents
    }
    if modes not in ({("MULTI_TIME", "TIME_SLICE")}, {("MULTI_RATIO", "RATIO_SLICE")}):
        return _result(
            "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
            routine_identity=routine_identity, rules_identity=rules_identity,
            reason="STARTUP_DEFERRED_PLAN_MODE_INVALID",
        )
    if any(_text(intent.get("source_signal_id")) != _text(signal_id) for intent in intents):
        return _result(
            "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
            routine_identity=routine_identity, rules_identity=rules_identity,
            reason="STARTUP_DEFERRED_PLAN_SIGNAL_IDENTITY_MISMATCH",
        )
    process_ids = {_text(intent.get("execution_process_id")) for intent in intents}
    child_issues = validate_child_set(intents)
    if len(process_ids) != 1 or "" in process_ids or child_issues:
        return _result(
            "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
            routine_identity=routine_identity, rules_identity=rules_identity,
            reason="STARTUP_DEFERRED_CHILD_SET_INVALID",
        )
    if modes == {("MULTI_TIME", "TIME_SLICE")}:
        for intent in intents:
            child_plan = intent.get("child_plan")
            child_plan = child_plan if isinstance(child_plan, dict) else {}
            scheduled_at = _text(child_plan.get("scheduled_at"))
            try:
                datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            except ValueError:
                return _result(
                    "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
                    routine_identity=routine_identity, rules_identity=rules_identity,
                    reason="STARTUP_TIME_SLICE_SCHEDULE_INVALID",
                )
    else:
        plan_hashes = {
            json.dumps(
                intent.get("multi_ratio_plan")
                if isinstance(intent.get("multi_ratio_plan"), dict) else {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for intent in intents
        }
        if len(plan_hashes) != 1 or plan_hashes == {"{}"}:
            return _result(
                "REVIEW_REQUIRED", signal=signal, main_facts=main_facts,
                routine_identity=routine_identity, rules_identity=rules_identity,
                reason="STARTUP_RATIO_SLICE_PLAN_INVALID",
            )
    return _result(
        "PENDING_VALID", signal=signal, main_facts=main_facts,
        routine_identity=routine_identity, rules_identity=rules_identity,
    )


__all__ = ["classify_startup_recovery"]
