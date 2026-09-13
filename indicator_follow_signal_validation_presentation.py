# -*- coding: utf-8 -*-
"""Pure presentation adapters for Indicator Follow Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any, Mapping

from routines.지표추종매매.routine_validation_replay import ValidationReplayEntry


@dataclass(frozen=True, slots=True)
class SignalValidationFilterRow:
    side: str
    label: str
    actual: str
    comparison: str
    operation: str
    result: str
    reason: str

    def to_cells(self) -> tuple[str, ...]:
        return (
            self.side,
            self.label,
            self.actual,
            self.comparison,
            self.operation,
            self.result,
            self.reason,
        )


def _text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
        return "-"
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _result(value: Any) -> str:
    if value is True or str(value).strip().lower() == "true":
        return "PASS"
    if value is False or str(value).strip().lower() == "false":
        return "FAIL"
    return "-"


def _operand_value(value: Any) -> str:
    return _text(value.get("value")) if isinstance(value, Mapping) else "-"


def _trace_reason(payload: Mapping[str, Any]) -> str:
    if str(payload.get("operator") or "").upper() == "DISABLED":
        return "condition disabled"
    reasons: list[str] = []
    for name in ("left_operand", "right_operand"):
        operand = payload.get(name)
        if not isinstance(operand, Mapping) or operand.get("value") is not None:
            continue
        reason = str(operand.get("reason") or "").strip()
        if reason and reason not in reasons and reason != "operator has no right operand":
            reasons.append(reason)
    return "; ".join(reasons) or "-"


def _belongs_to_side(path: Any, side: str) -> bool:
    text = str(path or "").strip().lower()
    if text.startswith("buy."):
        return side == "BUY"
    if text.startswith("sell."):
        return side == "SELL"
    return True


def _parse_detail_fields(detail: str) -> dict[str, str] | None:
    try:
        tokens = shlex.split(str(detail or ""), posix=True)
    except ValueError:
        return None
    fields: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key:
            fields[key] = value
    return fields if fields.get("filter_type") else None


def _detail_row(side: str, fields: Mapping[str, str]) -> SignalValidationFilterRow:
    actual = next(
        (_text(fields.get(name)) for name in ("evaluated_value", "current_value", "close_price") if name in fields),
        "-",
    )
    comparison = next(
        (_text(fields.get(name)) for name in ("threshold", "ma_value", "bollinger_value", "value") if name in fields),
        "-",
    )
    operation = _text(fields.get("operator") or fields.get("logic"))
    reason = _text(fields.get("reason"))
    if str(fields.get("enabled") or "").lower() == "false":
        reason = "disabled"
    return SignalValidationFilterRow(
        side=side,
        label=_text(fields.get("filter_type")),
        actual=actual,
        comparison=comparison,
        operation=operation,
        result=_result(fields.get("passed")),
        reason=reason,
    )


def filter_rows_for_entry(entry: ValidationReplayEntry) -> tuple[SignalValidationFilterRow, ...]:
    """Adapt one evaluator observation to deterministic read-only table rows."""
    if not isinstance(entry, ValidationReplayEntry):
        raise TypeError("entry must be ValidationReplayEntry")
    side = entry.evaluation_side
    trace = entry.trace
    rows: list[SignalValidationFilterRow] = []

    conditions = trace.get("conditions", []) if isinstance(trace, dict) else []
    if isinstance(conditions, list):
        for payload in conditions:
            if not isinstance(payload, Mapping) or not _belongs_to_side(payload.get("path"), side):
                continue
            condition_type = _text(payload.get("condition_type"))
            path = str(payload.get("path") or "").strip()
            label = condition_type if not path else f"{condition_type} ({path})"
            rows.append(SignalValidationFilterRow(
                side=side,
                label=label,
                actual=_operand_value(payload.get("left_operand")),
                comparison=_operand_value(payload.get("right_operand")),
                operation=_text(payload.get("operator")),
                result=_result(payload.get("final_result")),
                reason=_trace_reason(payload),
            ))

    groups = trace.get("groups", []) if isinstance(trace, dict) else []
    if isinstance(groups, list):
        for payload in groups:
            if not isinstance(payload, Mapping) or not _belongs_to_side(payload.get("path"), side):
                continue
            path = str(payload.get("path") or "").strip()
            name = _text(payload.get("group_name"))
            label = name if not path else f"{name} ({path})"
            reason = "disabled" if payload.get("enabled") is False else "-"
            rows.append(SignalValidationFilterRow(
                side=side,
                label=label,
                actual="-",
                comparison="-",
                operation=_text(payload.get("logic")),
                result=_result(payload.get("result")),
                reason=reason,
            ))

    aggregations = trace.get("aggregations", []) if isinstance(trace, dict) else []
    if isinstance(aggregations, list):
        for item in aggregations:
            if not isinstance(item, Mapping) or str(item.get("side") or "").upper() != side:
                continue
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                continue
            matched = payload.get("matched_group_paths")
            reason = (
                "matched=" + ", ".join(str(value) for value in matched)
                if isinstance(matched, list) and matched
                else "-"
            )
            rows.append(SignalValidationFilterRow(
                side=side,
                label="최종 집계",
                actual="-",
                comparison="-",
                operation=_text(payload.get("logic")),
                result=_result(payload.get("result")),
                reason=reason,
            ))

    for detail in entry.details:
        fields = _parse_detail_fields(detail)
        if fields is not None:
            rows.append(_detail_row(side, fields))
    return tuple(rows)


def build_signal_validation_filter_rows(
    buy_entry: ValidationReplayEntry | None,
    sell_entry: ValidationReplayEntry | None,
) -> tuple[SignalValidationFilterRow, ...]:
    rows: list[SignalValidationFilterRow] = []
    for expected_side, entry in (("BUY", buy_entry), ("SELL", sell_entry)):
        if entry is None:
            continue
        if not isinstance(entry, ValidationReplayEntry) or entry.evaluation_side != expected_side:
            raise TypeError(f"{expected_side.lower()}_entry must match its side")
        rows.extend(filter_rows_for_entry(entry))
    return tuple(rows)
