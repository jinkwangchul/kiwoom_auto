# -*- coding: utf-8 -*-
"""Standalone contract for Diagnostic / Decision Trace records."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4


SCHEMA_VERSION = "decision_trace_v1"

ENVIRONMENTS = frozenset({"LIVE", "BACKTEST"})
TRACE_LEVELS = frozenset({"NORMAL", "DIAGNOSTIC", "BACKTEST"})
STAGES = frozenset({"DECISION", "APPROVAL", "POLICY", "EXECUTION", "EXCEPTION"})
STAGE_RESULTS = frozenset({"COMPLETED", "PASSED", "BLOCKED", "SKIPPED", "FAILED", "ERROR"})
FINAL_DECISIONS = frozenset({"BUY", "SELL", "NONE"})
EVALUATION_STATUSES = frozenset({"COMPLETED", "SKIPPED", "FAILED"})

DATASET_REF_FIELDS = frozenset(
    {
        "dataset_id",
        "source",
        "data_hash",
        "timeframe",
        "timezone",
        "bar_time",
        "bar_index",
        "bar_count",
        "input_window_hash",
    }
)
RULE_REF_FIELDS = frozenset(
    {
        "routine_definition_id",
        "routine_instance_id",
        "routine_type",
        "rules_version",
        "rules_hash",
        "settings_hash",
        "engine_bundle_hash",
    }
)
COMMON_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "trace_record_id",
        "trace_id",
        "recorded_at",
        "environment",
        "trace_level",
        "stage",
        "stage_result",
    }
)
DECISION_REQUIRED_FIELDS = frozenset(
    {
        "decision_at",
        "dataset_ref",
        "rule_ref",
        "conditions",
        "groups",
        "final_decision",
        "evaluation_status",
    }
)
OPTIONAL_FIELDS = frozenset(
    {
        "app_session_id",
        "backtest_run_id",
        "stock_code",
        "stock_name",
        "routine_instance_id",
        "signal_id",
        "order_id",
        "execution_id",
        "broker_order_no",
        "command_id",
        "event_journal_event_id",
        "reason_code",
        "reason",
        "details",
        "indicator_snapshots",
        "position_context",
        "cycle_context",
        "buy_aggregation",
        "sell_aggregation",
    }
)
ALLOWED_FIELDS = COMMON_REQUIRED_FIELDS | DECISION_REQUIRED_FIELDS | OPTIONAL_FIELDS
POSITION_CONTEXT_FIELDS = frozenset({"holding_qty", "average_price", "position_state"})
CYCLE_CONTEXT_FIELDS = frozenset(
    {
        "cycle_identity",
        "cycle_active",
        "confirmed_buy_round",
        "cumulative_filled_buy_amount",
        "pending_buy_rounds",
        "partial_sell",
        "remaining_budget",
    }
)
AGGREGATION_FIELDS = frozenset(
    {"logic", "group_refs", "active_group_refs", "matched_group_refs", "result"}
)
INDICATOR_SNAPSHOT_FIELDS = frozenset(
    {"indicator", "period", "index", "current", "previous", "previous2", "reason"}
)


def new_trace_id() -> str:
    return str(uuid4())


def new_trace_record_id() -> str:
    return str(uuid4())


def _is_uuid4(value: Any) -> bool:
    try:
        parsed = UUID(str(value or ""))
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == str(value).lower()


def parse_aware_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _missing_fields(value: Any, fields: frozenset[str]) -> list[str]:
    if not isinstance(value, dict):
        return sorted(fields)
    return sorted(key for key in fields if value.get(key) in (None, ""))


def _validate_ref(value: Any, fields: frozenset[str], name: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    missing = _missing_fields(value, fields)
    return [f"{name} missing fields: {', '.join(missing)}"] if missing else []


def _validate_condition_ref(value: Any, name: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    issues: list[str] = []
    if not str(value.get("rules_hash") or "").strip():
        issues.append(f"{name}.rules_hash is required")
    if not str(value.get("path") or "").strip():
        issues.append(f"{name}.path is required")
    return issues


def _validate_operand(value: Any, name: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    issues: list[str] = []
    for key in ("source", "key"):
        if not str(value.get(key) or "").strip():
            issues.append(f"{name}.{key} is required")
    index = value.get("index")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
        issues.append(f"{name}.index must be an integer or null")
    if "value" not in value:
        issues.append(f"{name}.value is required")
    elif value.get("value") is None and not str(value.get("reason") or "").strip():
        issues.append(f"{name}.reason is required when value is null")
    return issues


def _validate_condition(value: Any, index: int) -> list[str]:
    name = f"conditions[{index}]"
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    issues = _validate_condition_ref(value.get("condition_ref"), f"{name}.condition_ref")
    for key in ("condition_type", "operator"):
        if not str(value.get(key) or "").strip():
            issues.append(f"{name}.{key} is required")
    if not isinstance(value.get("negated"), bool):
        issues.append(f"{name}.negated must be boolean")
    issues.extend(_validate_operand(value.get("left_operand"), f"{name}.left_operand"))
    issues.extend(_validate_operand(value.get("right_operand"), f"{name}.right_operand"))
    for key in ("raw_result", "final_result"):
        if not isinstance(value.get(key), bool):
            issues.append(f"{name}.{key} must be boolean")
    return issues


def _validate_group(value: Any, index: int) -> list[str]:
    name = f"groups[{index}]"
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    issues = _validate_condition_ref(value.get("group_ref"), f"{name}.group_ref")
    if not isinstance(value.get("enabled"), bool):
        issues.append(f"{name}.enabled must be boolean")
    if not str(value.get("logic") or "").strip():
        issues.append(f"{name}.logic is required")
    refs = value.get("condition_refs")
    if not isinstance(refs, list):
        issues.append(f"{name}.condition_refs must be an array")
    else:
        for ref_index, ref in enumerate(refs):
            issues.extend(_validate_condition_ref(ref, f"{name}.condition_refs[{ref_index}]"))
    if not isinstance(value.get("result"), bool):
        issues.append(f"{name}.result must be boolean")
    return issues


def _validate_limited_object(value: Any, name: str, allowed: frozenset[str]) -> list[str]:
    if not isinstance(value, dict):
        return [f"{name} must be an object"]
    unknown = sorted(set(value) - allowed)
    return [f"{name} has unsupported fields: {', '.join(unknown)}"] if unknown else []


def _validate_aggregation(value: Any, name: str) -> list[str]:
    issues = _validate_limited_object(value, name, AGGREGATION_FIELDS)
    if not isinstance(value, dict):
        return issues
    if not str(value.get("logic") or "").strip():
        issues.append(f"{name}.logic is required")
    refs = value.get("group_refs")
    if refs is None:
        refs = value.get("active_group_refs")
    if not isinstance(refs, list):
        issues.append(f"{name}.group_refs or active_group_refs must be an array")
    for field in ("group_refs", "active_group_refs", "matched_group_refs"):
        field_refs = value.get(field)
        if field_refs is None:
            continue
        if not isinstance(field_refs, list):
            issues.append(f"{name}.{field} must be an array")
            continue
        for index, ref in enumerate(field_refs):
            issues.extend(_validate_condition_ref(ref, f"{name}.{field}[{index}]"))
    if not isinstance(value.get("result"), bool):
        issues.append(f"{name}.result must be boolean")
    return issues


def _validate_indicator_snapshot(value: Any, index: int) -> list[str]:
    name = f"indicator_snapshots[{index}]"
    issues = _validate_limited_object(value, name, INDICATOR_SNAPSHOT_FIELDS)
    if not isinstance(value, dict):
        return issues
    if not str(value.get("indicator") or "").strip():
        issues.append(f"{name}.indicator is required")
    indicator_index = value.get("index")
    if not isinstance(indicator_index, int) or isinstance(indicator_index, bool):
        issues.append(f"{name}.index must be an integer")
    if "current" not in value:
        issues.append(f"{name}.current is required")
    elif value.get("current") is None and not str(value.get("reason") or "").strip():
        issues.append(f"{name}.reason is required when current is null")
    return issues


def normal_record_policy_issues(record: dict[str, Any]) -> list[str]:
    if record.get("trace_level") != "NORMAL":
        return []
    stage = record.get("stage")
    result = record.get("stage_result")
    if stage == "DECISION":
        return [] if record.get("final_decision") in {"BUY", "SELL"} else ["NORMAL excludes NONE decisions"]
    if stage in {"APPROVAL", "POLICY", "EXECUTION"}:
        return [] if result == "BLOCKED" else [f"NORMAL only records blocked {stage.lower()} stages"]
    if stage == "EXCEPTION":
        return [] if result in {"FAILED", "ERROR"} else ["NORMAL exception result must be FAILED or ERROR"]
    return ["stage is not allowed by NORMAL policy"]


def validate_trace_record(record: Any) -> list[str]:
    if not isinstance(record, dict):
        return ["trace record must be an object"]
    issues: list[str] = []
    unknown = sorted(set(record) - ALLOWED_FIELDS)
    if unknown:
        issues.append(f"unsupported fields: {', '.join(unknown)}")
    missing = _missing_fields(record, COMMON_REQUIRED_FIELDS)
    if missing:
        issues.append(f"missing required fields: {', '.join(missing)}")
    if record.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version is invalid")
    if not _is_uuid4(record.get("trace_id")):
        issues.append("trace_id must be UUID4")
    if not _is_uuid4(record.get("trace_record_id")):
        issues.append("trace_record_id must be UUID4")
    environment = str(record.get("environment") or "")
    level = str(record.get("trace_level") or "")
    stage = str(record.get("stage") or "")
    stage_result = str(record.get("stage_result") or "")
    if environment not in ENVIRONMENTS:
        issues.append("environment is invalid")
    if level not in TRACE_LEVELS:
        issues.append("trace_level is invalid")
    if stage not in STAGES:
        issues.append("stage is invalid")
    if stage_result not in STAGE_RESULTS:
        issues.append("stage_result is invalid")
    if environment == "LIVE" and level == "BACKTEST":
        issues.append("LIVE cannot use BACKTEST trace level")
    if environment == "BACKTEST":
        if level != "BACKTEST":
            issues.append("BACKTEST environment requires BACKTEST trace level")
        if not str(record.get("backtest_run_id") or "").strip():
            issues.append("backtest_run_id is required for BACKTEST")
    if parse_aware_timestamp(record.get("recorded_at")) is None:
        issues.append("recorded_at must be timezone-aware ISO 8601")

    if stage == "DECISION":
        missing = _missing_fields(record, DECISION_REQUIRED_FIELDS)
        if missing:
            issues.append(f"DECISION missing fields: {', '.join(missing)}")
        if parse_aware_timestamp(record.get("decision_at")) is None:
            issues.append("decision_at must be timezone-aware ISO 8601")
        issues.extend(_validate_ref(record.get("dataset_ref"), DATASET_REF_FIELDS, "dataset_ref"))
        issues.extend(_validate_ref(record.get("rule_ref"), RULE_REF_FIELDS, "rule_ref"))
        if record.get("final_decision") not in FINAL_DECISIONS:
            issues.append("final_decision is invalid")
        if record.get("evaluation_status") not in EVALUATION_STATUSES:
            issues.append("evaluation_status is invalid")
        conditions = record.get("conditions")
        if not isinstance(conditions, list):
            issues.append("conditions must be an array")
        else:
            for index, condition in enumerate(conditions):
                issues.extend(_validate_condition(condition, index))
        groups = record.get("groups")
        if not isinstance(groups, list):
            issues.append("groups must be an array")
        else:
            for index, group in enumerate(groups):
                issues.extend(_validate_group(group, index))

    if record.get("position_context") is not None:
        issues.extend(_validate_limited_object(record.get("position_context"), "position_context", POSITION_CONTEXT_FIELDS))
    if record.get("cycle_context") is not None:
        issues.extend(_validate_limited_object(record.get("cycle_context"), "cycle_context", CYCLE_CONTEXT_FIELDS))
    for aggregation in ("buy_aggregation", "sell_aggregation"):
        if record.get(aggregation) is not None:
            issues.extend(_validate_aggregation(record.get(aggregation), aggregation))
    indicators = record.get("indicator_snapshots")
    if indicators is not None:
        if not isinstance(indicators, list):
            issues.append("indicator_snapshots must be an array")
        else:
            for index, indicator in enumerate(indicators):
                issues.extend(_validate_indicator_snapshot(indicator, index))
    issues.extend(normal_record_policy_issues(record))
    return issues


def build_trace_record(
    *,
    trace_id: str,
    recorded_at: str,
    environment: str,
    trace_level: str,
    stage: str,
    stage_result: str,
    trace_record_id: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    record = {
        "schema_version": SCHEMA_VERSION,
        "trace_record_id": str(trace_record_id or new_trace_record_id()),
        "trace_id": str(trace_id or ""),
        "recorded_at": str(recorded_at or ""),
        "environment": str(environment or "").upper(),
        "trace_level": str(trace_level or "").upper(),
        "stage": str(stage or "").upper(),
        "stage_result": str(stage_result or "").upper(),
    }
    record.update({key: value for key, value in fields.items() if value is not None})
    return record


def resolve_live_trace_mode(
    *,
    diagnostic_enabled: bool = False,
    stock_scope: str = "",
    routine_scope: str = "",
    minutes: int | None = None,
    activated_at: datetime | None = None,
) -> dict[str, Any]:
    now = activated_at or datetime.now().astimezone()
    if now.tzinfo is None or now.utcoffset() is None:
        return {"accepted": False, "trace_level": "NORMAL", "issues": ["activated_at must be timezone-aware"]}
    if not diagnostic_enabled:
        return {"accepted": True, "trace_level": "NORMAL", "expires_at": None, "issues": []}
    stock = str(stock_scope or "").strip()
    routine = str(routine_scope or "").strip()
    if not stock and not routine:
        return {"accepted": False, "trace_level": "NORMAL", "expires_at": None, "issues": ["DIAGNOSTIC requires stock or routine scope"]}
    duration = 30 if minutes is None else minutes
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0 or duration > 120:
        return {"accepted": False, "trace_level": "NORMAL", "expires_at": None, "issues": ["diagnostic minutes must be between 1 and 120"]}
    expires = now + timedelta(minutes=duration)
    return {
        "accepted": True,
        "trace_level": "DIAGNOSTIC",
        "stock_scope": stock,
        "routine_scope": routine,
        "activated_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "minutes": duration,
        "issues": [],
    }


def live_trace_mode_expired(mode: dict[str, Any], *, now: datetime | None = None) -> bool:
    if mode.get("trace_level") != "DIAGNOSTIC":
        return False
    expires = parse_aware_timestamp(mode.get("expires_at"))
    current = now or datetime.now().astimezone()
    return expires is None or current >= expires
