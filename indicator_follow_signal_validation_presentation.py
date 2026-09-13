# -*- coding: utf-8 -*-
"""Pure presentation adapters for Indicator Follow Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from typing import Any, Mapping

from routines.지표추종매매.routine_validation_replay import ValidationReplayEntry


@dataclass(frozen=True, slots=True)
class SignalValidationFilterRow:
    side: str
    condition: str
    setting: str
    actual: str
    result: str
    row_kind: str = "condition"

    def to_cells(self) -> tuple[str, ...]:
        return (self.side, self.condition, self.setting, self.actual, self.result)


def _text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
        return "-"
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def _result(value: Any, *, enabled: Any = True) -> str:
    if enabled is False or str(enabled).strip().lower() == "false":
        return "미사용"
    if value is True or str(value).strip().lower() == "true":
        return "통과"
    if value is False or str(value).strip().lower() == "false":
        return "실패"
    return "-"


_OPERATOR_LABELS = {
    "TURN_UP": "상승전환",
    "TURN_DOWN": "하락전환",
    "CROSS_UP": "상향돌파",
    "CROSS_DOWN": "하향돌파",
    "DISABLED": "미사용",
    "PASS": "통과",
    "FAIL": "실패",
}


def _operator_text(value: Any) -> str:
    text = _text(value)
    return _OPERATOR_LABELS.get(text.strip().upper(), text)


def _operator_text_in(value: Any) -> str:
    text = _text(value)
    for source, translated in _OPERATOR_LABELS.items():
        text = text.replace(source, translated)
    return text.replace("_", " ")


def _operand_value(value: Any) -> str:
    return _text(value.get("value")) if isinstance(value, Mapping) else "-"


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


def _state(ui_state: Mapping[str, Any], *path: str) -> dict[str, Any]:
    value: Any = ui_state
    for name in path:
        if not isinstance(value, Mapping):
            return {}
        value = value.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def _field(state: Mapping[str, Any], name: str, default: str = "-") -> str:
    value = state.get(name)
    return default if value is None or value == "" else str(value)


def _buy_settings(ui_state: Mapping[str, Any]) -> dict[str, str]:
    state = _state(ui_state, "buy_ui", "signal_filter")
    return {
        "A": (
            f"OCR {_field(state, 'buy_ocr_sign_combo', '')}"
            f"{_field(state, 'buy_ocr_value_line')} "
            f"{_field(state, 'buy_ocr_compare_combo')} + "
            f"{_field(state, 'buy_ocr_turn_combo')}전환"
        ),
        "B": (
            f"볼린저밴드 {_field(state, 'buy_bollinger_direction_combo')} "
            f"{_field(state, 'buy_bollinger_value_line')}% "
            f"{_field(state, 'buy_bollinger_compare_combo')}"
        ),
        "C": (
            f"현재가 {_field(state, 'buy_ma_value_line')}이평 "
            f"{_field(state, 'buy_ma_direction_combo')}"
            f"{_field(state, 'buy_ma_compare_combo')}"
        ),
        "D": (
            f"RSI({_field(state, 'buy_rsi_period_line')}) "
            f"{_field(state, 'buy_rsi_value_line')} "
            f"{_field(state, 'buy_rsi_compare_combo')}"
        ),
    }


def _enabled_text(enabled: Any, text: str) -> str:
    return text if enabled is True else f"{text} (미사용)"


def _sell_settings(ui_state: Mapping[str, Any]) -> dict[str, str]:
    groups = _state(ui_state, "sell_ui", "signal_conditions")
    a = _state(groups, "condition_a")
    b = _state(groups, "condition_b")
    c = _state(groups, "condition_c")
    def gap(group: Mapping[str, Any]) -> str:
        return (
            f"{_field(group, 'gap_left_combo')} 대비 "
            f"{_field(group, 'gap_right_combo')} "
            f"{_field(group, 'gap_direction_combo')} "
            f"{_field(group, 'gap_value_line')}% "
            f"{_field(group, 'gap_compare_combo')}"
        )
    return {
        "A·OCR": (
            f"OCR {_field(a, 'ocr_sign_combo', '')}{_field(a, 'ocr_value_line')} "
            f"{_field(a, 'ocr_compare_combo')} + {_field(a, 'ocr_direction_combo')}전환"
        ),
        "A·가격비교": gap(a),
        "A·RSI": (
            f"RSI({_field(a, 'rsi_period_line')}) {_field(a, 'rsi_value_line')} "
            f"{_field(a, 'rsi_compare_combo')}"
        ),
        "B·가격박스": (
            f"가격박스 {_field(b, 'price_box_direction_combo')} "
            f"{_field(b, 'price_box_value_line')}% {_field(b, 'price_box_compare_combo')}"
        ),
        "B·볼린저밴드": (
            f"볼린저밴드 {_field(b, 'bollinger_direction_combo')} "
            f"{_field(b, 'bollinger_value_line')}% {_field(b, 'bollinger_compare_combo')}"
        ),
        "B·가격비교": gap(b),
        "C·가격비교": gap(c),
        "C·MACD": (
            f"{_field(c, 'macd_kind_combo')} {_field(c, 'macd_sign_combo', '')}"
            f"{_field(c, 'macd_value_line')} {_field(c, 'macd_compare_combo')}"
        ),
        "C·이평배열": (
            f"{_field(c, 'array_first_period_combo')}"
            f"{_field(c, 'array_first_compare_combo')}"
            f"{_field(c, 'array_second_period_combo')}"
            f"{_field(c, 'array_second_compare_combo')}"
            f"{_field(c, 'array_third_period_combo')}"
        ),
    }


def _buy_detail_values(entry: ValidationReplayEntry) -> dict[str, tuple[str, str]]:
    by_type: dict[str, Mapping[str, str]] = {}
    for detail in entry.details:
        fields = _parse_detail_fields(detail)
        if fields is not None:
            by_type[str(fields.get("filter_type") or "").upper()] = fields
    mapping = {
        "A": "OCR",
        "B": "BOLLINGER",
        "C": "MOVING_AVERAGE",
        "D": "RSI",
    }
    values: dict[str, tuple[str, str]] = {}
    for letter, filter_type in mapping.items():
        fields = by_type.get(filter_type)
        if fields is None:
            values[letter] = ("-", "-")
            continue
        enabled = fields.get("enabled", True)
        if filter_type == "OCR":
            actual = (
                _operator_text_in(fields.get("condition_details") or "-")
                .replace("OSC", "OCR")
                .replace("|", " / ")
            )
        elif filter_type == "BOLLINGER":
            close_price = _text(fields.get("close_price"))
            bollinger_value = _text(fields.get("bollinger_value"))
            actual = "-" if close_price == bollinger_value == "-" else (
                f"현재가 {close_price} / 밴드 {bollinger_value}"
            )
        elif filter_type == "MOVING_AVERAGE":
            current_value = _text(fields.get("current_value"))
            ma_value = _text(fields.get("ma_value"))
            actual = "-" if current_value == ma_value == "-" else (
                f"현재가 {current_value} / 이평 {ma_value}"
            )
        else:
            actual = _text(fields.get("evaluated_value"))
        values[letter] = (actual, _result(fields.get("passed"), enabled=enabled))
    return values


_SELL_CONDITION_PATH = re.compile(
    r"sell\.signals\.ui_condition_([abc])\.groups\[0\]\.conditions\[\d+\]$"
)
_SELL_GROUP_PATH = re.compile(
    r"sell\.signals\.ui_condition_([abc])\.groups\[0\]$"
)
_SELL_ROW_IDS = {
    "A·OCR": ("A", ("OCR_0", "OCR_1")),
    "A·가격비교": ("A", ("GAP_0",)),
    "A·RSI": ("A", ("RSI_0",)),
    "B·가격박스": ("B", ("PRICE_BOX_0",)),
    "B·볼린저밴드": ("B", ("BOLLINGER_0",)),
    "B·가격비교": ("B", ("GAP_0",)),
    "C·가격비교": ("C", ("GAP_0",)),
    "C·MACD": ("C", ("MACD_0",)),
    "C·이평배열": ("C", ("ARRAY_0", "ARRAY_1")),
}


def _condition_actual(payload: Mapping[str, Any]) -> str:
    left = _operand_value(payload.get("left_operand"))
    right = _operand_value(payload.get("right_operand"))
    operator = _operator_text(payload.get("operator"))
    if operator == "미사용" or left == "-":
        return "-"
    return f"{left} {operator}" if right == "-" else f"{left} {operator} {right}"


def _sell_filter_enabled(
    ui_state: Mapping[str, Any],
    condition_name: str,
) -> bool:
    letter, label = condition_name.split("·", 1)
    condition = _state(
        ui_state,
        "sell_ui",
        "signal_conditions",
        f"condition_{letter.lower()}",
    )
    key = {
        "OCR": "ocr_check",
        "가격비교": "gap_check",
        "RSI": "rsi_check",
        "가격박스": "price_box_check",
        "볼린저밴드": "bollinger_check",
        "MACD": "macd_check",
        "이평배열": "array_check",
    }[label]
    return condition.get(key) is not False


def _trace_status(payloads: list[Mapping[str, Any]]) -> str:
    if not payloads:
        return "미평가"
    for payload in payloads:
        operands = [payload.get("left_operand"), payload.get("right_operand")]
        if any(
            isinstance(operand, Mapping)
            and operand.get("value") is None
            and str(operand.get("kind") or "").lower() != "none"
            for operand in operands
        ):
            return "데이터부족"
    results = [payload.get("final_result") for payload in payloads]
    if any(value is False for value in results):
        return "실패"
    if results and all(value is True for value in results):
        return "통과"
    return "미평가"


def _price_actual(
    payload: Mapping[str, Any],
    group: Mapping[str, Any],
) -> str:
    basis_label = _field(group, "gap_left_combo")
    comparison_label = _field(group, "gap_right_combo")
    basis_value = _operand_value(payload.get("right_operand"))
    comparison_value = _operand_value(payload.get("left_operand"))
    percent_text = "-"
    try:
        basis = float(basis_value.replace(",", ""))
        comparison = float(comparison_value.replace(",", ""))
        if basis > 0:
            percent_text = f"{(comparison - basis) / basis * 100.0:+.4f}%"
    except (AttributeError, TypeError, ValueError):
        pass
    average_note = " / 검증용 추정평단" if basis_label == "평단가" or comparison_label == "평단가" else ""
    return (
        f"기준 {basis_label} {basis_value} / 비교 {comparison_label} "
        f"{comparison_value} / 변동률 {percent_text}{average_note}"
    )


def _sell_trace_values(
    entry: ValidationReplayEntry,
    ui_state: Mapping[str, Any],
) -> dict[str, tuple[str, str]]:
    trace = entry.trace if isinstance(entry.trace, dict) else {}
    payloads_by_id: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for payload in trace.get("conditions", []):
        if not isinstance(payload, Mapping):
            continue
        match = _SELL_CONDITION_PATH.fullmatch(str(payload.get("path") or ""))
        expression_id = str(payload.get("expression_id") or "").strip().upper()
        if match is None or not expression_id:
            continue
        payloads_by_id.setdefault((match.group(1).upper(), expression_id), []).append(payload)

    values: dict[str, tuple[str, str]] = {}
    groups = _state(ui_state, "sell_ui", "signal_conditions")
    for condition_name, (letter, expression_ids) in _SELL_ROW_IDS.items():
        if not _sell_filter_enabled(ui_state, condition_name):
            values[condition_name] = ("-", "미사용")
            continue
        payloads = [
            payload
            for expression_id in expression_ids
            for payload in payloads_by_id.get((letter, expression_id), [])
        ]
        if len(payloads) != len(expression_ids):
            values[condition_name] = (
                "-" if not payloads else " / ".join(_condition_actual(item) for item in payloads),
                "미평가" if not payloads else "근거연결오류",
            )
            continue
        if condition_name.endswith("가격비교"):
            group = _state(groups, f"condition_{letter.lower()}")
            actual = _price_actual(payloads[0], group)
        elif condition_name == "A·OCR":
            actual = " / ".join(
                f"{'전환' if item.get('operator') in {'TURN_UP', 'TURN_DOWN'} else '임계값'}: {_condition_actual(item)}"
                for item in payloads
            )
        elif condition_name == "C·이평배열":
            actual = " / ".join(
                f"비교 {index + 1}: {_condition_actual(item)}"
                for index, item in enumerate(payloads)
            )
        else:
            actual = " / ".join(_condition_actual(item) for item in payloads)
        values[condition_name] = (actual or "-", _trace_status(payloads))
    return values


def _condition_enabled(ui_state: Mapping[str, Any], side: str, letter: str) -> bool:
    if side == "BUY":
        state = _state(ui_state, "buy_ui", "signal_filter")
        names = {
            "A": "buy_ocr_enabled",
            "B": "buy_bollinger_enabled",
            "C": "buy_ma_enabled",
            "D": "buy_rsi_enabled",
        }
        return state.get(names[letter], True) is not False
    condition = _state(
        ui_state,
        "sell_ui",
        "signal_conditions",
        f"condition_{letter.lower()}",
    )
    names = {
        "A": ("ocr_check", "rsi_check"),
        "B": ("price_box_check", "bollinger_check"),
        "C": ("macd_check", "array_check"),
    }
    return any(condition.get(name) is True for name in names[letter])


def _expression(ui_state: Mapping[str, Any], side: str) -> str:
    basic = _state(ui_state, "basic")
    return _field(
        basic,
        "buy_signal_expr_line" if side == "BUY" else "sell_signal_expr_line",
    )


def filter_rows_for_entry(
    entry: ValidationReplayEntry,
    ui_state: Mapping[str, Any],
) -> tuple[SignalValidationFilterRow, ...]:
    """Present immutable evaluator evidence without re-evaluating a signal."""
    if not isinstance(entry, ValidationReplayEntry):
        raise TypeError("entry must be ValidationReplayEntry")
    if not isinstance(ui_state, Mapping):
        raise TypeError("ui_state must be a mapping")
    side = entry.evaluation_side
    settings = _buy_settings(ui_state) if side == "BUY" else _sell_settings(ui_state)
    values = (
        _buy_detail_values(entry)
        if side == "BUY"
        else _sell_trace_values(entry, ui_state)
    )
    rows: list[SignalValidationFilterRow] = []
    for condition_name in settings:
        actual, result = values.get(condition_name, ("-", "미평가"))
        if side == "BUY" and not _condition_enabled(ui_state, side, condition_name):
            result = "미사용"
        rows.append(SignalValidationFilterRow(
            side,
            condition_name,
            settings[condition_name],
            actual,
            result,
        ))

    if side == "SELL":
        trace = entry.trace if isinstance(entry.trace, dict) else {}
        group_results: dict[str, Any] = {}
        for payload in trace.get("groups", []):
            if not isinstance(payload, Mapping):
                continue
            match = _SELL_GROUP_PATH.fullmatch(str(payload.get("path") or ""))
            if match is not None:
                group_results[match.group(1).upper()] = payload.get("result")
        for letter in "ABC":
            group_value = group_results.get(letter)
            rows.append(SignalValidationFilterRow(
                side,
                f"그룹 {letter}",
                "개별 필터 논리식",
                "-",
                "미평가" if group_value is None else _result(group_value),
                "summary",
            ))

        aggregation = next(
            (
                item.get("payload")
                for item in reversed(trace.get("aggregations", []))
                if isinstance(item, Mapping)
                and str(item.get("side") or "").upper() == "SELL"
                and isinstance(item.get("payload"), Mapping)
            ),
            {},
        )
        expression_values = aggregation.get("ui_expression_values", {})
        actual_expression = (
            " / ".join(
                f"{name}={'통과' if value is True else '실패'}"
                for name, value in sorted(expression_values.items())
            )
            if isinstance(expression_values, Mapping)
            else ""
        )
        expression_result = aggregation.get("ui_expression_result")
        rows.append(SignalValidationFilterRow(
            side,
            "적용 조합식",
            _expression(ui_state, side),
            actual_expression or "-",
            "미평가" if expression_result is None else _result(expression_result),
            "summary",
        ))
    else:
        rows.append(SignalValidationFilterRow(
            side,
            "적용 조합식",
            _expression(ui_state, side),
            "",
            "",
            "summary",
        ))
    occurred = entry.signal == side
    rows.append(SignalValidationFilterRow(
        side,
        f"최종 {side} 판정",
        "",
        "",
        "발생" if occurred else "미발생",
        "summary",
    ))
    return tuple(rows)


def build_signal_validation_filter_rows(
    buy_entry: ValidationReplayEntry | None,
    sell_entry: ValidationReplayEntry | None,
    ui_state: Mapping[str, Any],
) -> tuple[SignalValidationFilterRow, ...]:
    rows: list[SignalValidationFilterRow] = []
    for expected_side, entry in (("BUY", buy_entry), ("SELL", sell_entry)):
        if entry is None:
            continue
        if not isinstance(entry, ValidationReplayEntry) or entry.evaluation_side != expected_side:
            raise TypeError(f"{expected_side.lower()}_entry must match its side")
        rows.extend(filter_rows_for_entry(entry, ui_state))
    return tuple(rows)
