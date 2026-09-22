# -*- coding: utf-8 -*-
"""Pure presentation adapters for Indicator Follow Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import shlex
from typing import Any, Mapping

from routines.지표추종매매.routine_validation_replay import ValidationReplayEntry


@dataclass(frozen=True, slots=True)
class SignalValidationEvidenceRecord:
    label: str
    actual: str
    condition_keys: tuple[str, ...]


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
            f"{_field(state, 'buy_bollinger_sign_combo', '')}"
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
            values[letter] = ("-", "미평가")
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
            threshold = _text(fields.get("threshold"))
            band_name = {
                "BOLLINGER_UPPER": "상단밴드",
                "BOLLINGER_LOWER": "하단밴드",
            }.get(_text(fields.get("compare_target")), "밴드")
            actual = "-" if close_price == bollinger_value == "-" else (
                f"현재가 {close_price} / {band_name} {bollinger_value}"
                + ("" if threshold == "-" else f" / 기준 {threshold}")
            )
        elif filter_type == "MOVING_AVERAGE":
            current_value = _text(fields.get("current_value"))
            ma_value = _text(fields.get("ma_value"))
            actual = "-" if current_value == ma_value == "-" else (
                f"현재가 {current_value} / 이평 {ma_value}"
            )
        else:
            actual = _text(fields.get("evaluated_value"))
        reason = str(fields.get("reason") or "").strip().lower()
        if str(enabled).strip().lower() == "false" or reason == "disabled":
            result = "미사용"
        elif reason == "insufficient_data":
            result = "데이터부족"
        else:
            result = _result(fields.get("passed"), enabled=enabled)
        values[letter] = (actual, result)
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
            and str(operand.get("source") or "").lower() != "none"
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


_BUY_EVIDENCE_FILTERS = {
    "A": ("ocr", "OCR"),
    "B": ("bollinger", "볼린저밴드"),
    "C": ("moving_average", "이동평균"),
    "D": ("rsi", "RSI"),
}
_SELL_EVIDENCE_FILTERS = {
    "OCR": "OCR",
    "GAP": "가격비교",
    "RSI": "RSI",
    "PRICE_BOX": "가격박스",
    "BOLLINGER": "볼린저밴드",
    "MACD": "MACD",
    "ARRAY": "이평배열",
}
_OPERAND_LABELS = {
    "AVG_PRICE": "추정평단",
    "CLOSE": "종가",
    "OSC": "OCR",
    "MACD": "MACD",
    "SIGNAL": "Signal",
    "RSI": "RSI",
}


_CHART_OPERATOR_LABELS = {
    "TURN_UP": "상승전환",
    "TURN_DOWN": "하락전환",
    "CROSS_UP": "상향돌파",
    "CROSS_DOWN": "하향돌파",
    ">=": "이상",
    "GTE": "이상",
    ">": "초과",
    "GT": "초과",
    "<=": "이하",
    "LTE": "이하",
    "<": "미만",
    "LT": "미만",
    "=": "같음",
    "==": "같음",
    "EQ": "같음",
}

_CHART_OPERAND_LABELS = {
    "AVG_PRICE": "추정평단",
    "CLOSE": "현재가",
    "CURRENT_PRICE": "현재가",
    "ORDER_PRICE": "주문가",
    "OSC": "OCR",
    "OCR": "OCR",
    "MACD": "MACD선",
    "SIGNAL": "시그널선",
    "RSI": "RSI",
    "BOLLINGER_UPPER": "볼린저 상단",
    "BOLLINGER_LOWER": "볼린저 하단",
    "PRICE_BOX_UPPER": "가격박스 상단",
    "PRICE_BOX_MIDDLE": "가격박스 중단",
    "PRICE_BOX_LOWER": "가격박스 하단",
    "VIRTUAL_FILL_PRICE": "가상체결가",
}

def chart_operator_label(value: Any) -> str:
    text = str(value or "").strip()
    return _CHART_OPERATOR_LABELS.get(text.upper(), text.replace("_", " "))

def chart_operand_label(value: Any) -> str:
    token = str(value or "").strip().upper()
    if token.startswith("MA") and token[2:].isdigit():
        return f"{token[2:]}이평"
    return _CHART_OPERAND_LABELS.get(token, token or "지표")

def chart_compare_mode_label(value: Any) -> str:
    token = str(value or "").strip().upper()
    return {
        "GTE": "이상", ">=": "이상",
        "LTE": "이하", "<=": "이하",
        "GT": "초과", ">": "초과",
        "LT": "미만", "<": "미만",
    }.get(token, chart_operator_label(token))


def _number_text(value: Any) -> str:
    if isinstance(value, bool) or value in (None, ""):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "-"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def _operand_label(operand: Any) -> str:
    if not isinstance(operand, Mapping):
        return "값"
    key = str(operand.get("key") or "").strip().upper()
    if key.startswith("MA") and key[2:].isdigit():
        return f"{key[2:]}이평"
    return _OPERAND_LABELS.get(key, key or "값")


def _operand_actual(operand: Any) -> str:
    return _number_text(operand.get("value")) if isinstance(operand, Mapping) else "-"


def _condition_actual_evidence(payload: Mapping[str, Any]) -> str:
    left_operand = payload.get("left_operand")
    right_operand = payload.get("right_operand")
    left_label = _operand_label(left_operand)
    right_label = _operand_label(right_operand)
    left_value = _operand_actual(left_operand)
    right_value = _operand_actual(right_operand)
    operator = str(payload.get("operator") or "").strip().upper()
    snapshots = [
        value for value in payload.get("indicator_snapshots", [])
        if isinstance(value, Mapping)
    ]

    if operator == "PERCENT_GAP":
        percent_text = "-"
        try:
            basis = float(str(right_value).replace(",", ""))
            comparison = float(str(left_value).replace(",", ""))
            if basis > 0:
                percent_text = f"{(comparison - basis) / basis * 100.0:+.2f}%"
        except (TypeError, ValueError):
            pass
        return (
            f"{right_label} {right_value} / {left_label} {left_value} / {percent_text}"
        )

    if operator in {"TURN_UP", "TURN_DOWN"} and snapshots:
        snapshot = snapshots[0]
        return " / ".join((
            f"현재 {_number_text(snapshot.get('current'))}",
            f"이전 {_number_text(snapshot.get('previous'))}",
            f"이전2 {_number_text(snapshot.get('previous2'))}",
        ))

    if operator in {"CROSS_UP", "CROSS_DOWN"} and len(snapshots) >= 2:
        left_snapshot, right_snapshot = snapshots[:2]
        return " / ".join((
            f"{left_label} 현재 {_number_text(left_snapshot.get('current'))}",
            f"이전 {_number_text(left_snapshot.get('previous'))}",
            f"{right_label} 현재 {_number_text(right_snapshot.get('current'))}",
            f"이전 {_number_text(right_snapshot.get('previous'))}",
        ))

    if operator in {"TREND_UP", "TREND_DOWN", "ZERO_CROSS_UP", "ZERO_CROSS_DOWN"} and snapshots:
        snapshot = snapshots[0]
        return " / ".join((
            f"현재 {_number_text(snapshot.get('current'))}",
            f"이전 {_number_text(snapshot.get('previous'))}",
        ))

    right_source = str(
        right_operand.get("source") if isinstance(right_operand, Mapping) else ""
    ).strip().lower()
    right_key = str(
        right_operand.get("key") if isinstance(right_operand, Mapping) else ""
    ).strip().lower()
    if right_source == "indicator" and right_value != "-":
        return f"{left_label} {left_value} / {right_label} {right_value}"
    if right_source == "literal" or right_key in {"value", "none", ""}:
        return left_value
    return left_value if right_value == "-" else f"{left_value} / {right_value}"


def _expression_survivors(
    expression_ast: Any,
    values: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Project recorded booleans through the canonical AST without re-evaluation."""
    normalized_values = {
        str(name or "").strip().upper(): value is True
        for name, value in values.items()
    }

    def visit(node: Any) -> tuple[bool, list[str]]:
        if not isinstance(node, Mapping):
            return False, []
        node_type = str(node.get("type") or "").strip().lower()
        if node_type == "identifier":
            name = str(node.get("name") or "").strip().upper()
            passed = normalized_values.get(name, False)
            return passed, [name] if passed else []
        if node_type != "binary":
            return False, []
        left_passed, left_survivors = visit(node.get("left"))
        right_passed, right_survivors = visit(node.get("right"))
        operator = str(node.get("operator") or "").strip().upper()
        if operator == "AND":
            passed = left_passed and right_passed
            return passed, left_survivors + right_survivors if passed else []
        if operator == "OR":
            passed = left_passed or right_passed
            survivors = []
            if left_passed:
                survivors.extend(left_survivors)
            if right_passed:
                survivors.extend(right_survivors)
            return passed, survivors
        if operator == "NOT":
            passed = left_passed and not right_passed
            return passed, left_survivors if passed else []
        return False, []

    passed, survivors = visit(expression_ast)
    return passed, tuple(survivors)


def canonical_validation_condition_key(condition: Any) -> str:
    if not isinstance(condition, Mapping):
        return ""
    detached = {
        key: value
        for key, value in condition.items()
        if key not in {"description", "expression_id"}
    }
    return json.dumps(
        detached,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_condition_key(condition: Any) -> str:
    return canonical_validation_condition_key(condition)


def _trace_payloads(entry: ValidationReplayEntry) -> list[Mapping[str, Any]]:
    trace = entry.trace if isinstance(entry.trace, dict) else {}
    return [
        payload for payload in trace.get("conditions", [])
        if isinstance(payload, Mapping)
    ]


def _trace_groups(entry: ValidationReplayEntry) -> list[Mapping[str, Any]]:
    trace = entry.trace if isinstance(entry.trace, dict) else {}
    return [
        payload for payload in trace.get("groups", [])
        if isinstance(payload, Mapping)
    ]


def _side_aggregation(entry: ValidationReplayEntry) -> Mapping[str, Any]:
    trace = entry.trace if isinstance(entry.trace, dict) else {}
    return next(
        (
            item.get("payload")
            for item in reversed(trace.get("aggregations", []))
            if isinstance(item, Mapping)
            and str(item.get("side") or "").strip().upper() == entry.evaluation_side
            and isinstance(item.get("payload"), Mapping)
        ),
        {},
    )


def _rule_at_path(rules: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    value: Any = rules
    for name, index_text in re.findall(r"([^.\[]+)(?:\[(\d+)\])?", str(path or "")):
        if not isinstance(value, Mapping):
            return {}
        value = value.get(name)
        if index_text:
            if not isinstance(value, list):
                return {}
            index = int(index_text)
            if not 0 <= index < len(value):
                return {}
            value = value[index]
    return value if isinstance(value, Mapping) else {}


def _payload_evidence_key(payload: Mapping[str, Any]) -> str:
    detached = {
        key: value
        for key, value in payload.items()
        if key not in {"path", "description", "expression_id", "raw_result", "final_result"}
    }
    return json.dumps(
        detached,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _filter_name_from_payload(payload: Mapping[str, Any]) -> str:
    condition_type = str(payload.get("condition_type") or "").strip().upper()
    key = str(
        payload.get("left_operand", {}).get("key")
        if isinstance(payload.get("left_operand"), Mapping)
        else ""
    ).strip().upper()
    token = condition_type or key
    if token in {"OSC", "OCR"}:
        return "OCR"
    if token == "MA" or token.startswith("MA"):
        return "이동평균"
    if token.startswith("BB_") or "BOLLINGER" in token:
        return "볼린저밴드"
    if "PRICE_BOX" in token:
        return "가격박스"
    if token == "PERCENT_GAP":
        return "가격비교"
    return _OPERAND_LABELS.get(token, token or "조건")


def _selected_group_payloads(
    group_payload: Mapping[str, Any],
    condition_payloads: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if group_payload.get("result") is not True:
        return []
    by_path = {
        str(payload.get("path") or ""): payload
        for payload in condition_payloads
    }
    paths = [str(value or "") for value in group_payload.get("condition_paths", [])]
    candidates = [by_path[path] for path in paths if path in by_path]
    expression_ast = group_payload.get("condition_expression")
    expression_values = group_payload.get("expression_values")
    if isinstance(expression_ast, Mapping) and isinstance(expression_values, Mapping):
        passed, survivors = _expression_survivors(expression_ast, expression_values)
        if not passed:
            return []
        surviving_ids = set(survivors)
        return [
            payload for payload in candidates
            if str(payload.get("expression_id") or "").strip().upper() in surviving_ids
            and payload.get("negated") is not True
        ]
    logic = str(group_payload.get("logic") or "AND").strip().upper()
    if logic == "OR":
        return [
            payload for payload in candidates
            if payload.get("final_result") is True and payload.get("negated") is not True
        ]
    return [
        payload for payload in candidates
        if payload.get("final_result") is True and payload.get("negated") is not True
    ]


def _buy_evidence_records(
    entry: ValidationReplayEntry,
    rules: Mapping[str, Any],
) -> list[tuple[str, str, tuple[str, ...]]]:
    records: list[tuple[str, str, tuple[str, ...]]] = []
    filters = _state(rules, "buy", "filters")
    composite = _state(filters, "composite")
    expression = composite.get("expression")
    values = _buy_detail_values(entry)
    boolean_values = {
        letter: values.get(letter, ("-", "미평가"))[1] == "통과"
        for letter in _BUY_EVIDENCE_FILTERS
    }
    if isinstance(expression, Mapping):
        passed, survivors = _expression_survivors(expression.get("ast"), boolean_values)
        if passed:
            for letter in survivors:
                if letter not in _BUY_EVIDENCE_FILTERS:
                    continue
                filter_key, label = _BUY_EVIDENCE_FILTERS[letter]
                actual, result = values.get(letter, ("-", "미평가"))
                if result != "통과" or actual == "-":
                    continue
                config = _state(filters, filter_key)
                condition_keys = tuple(
                    key for condition in config.get("conditions", [])
                    if (key := _canonical_condition_key(condition))
                ) or (f"buy-filter:{filter_key}",)
                records.append((label, actual, condition_keys))

    condition_payloads = _trace_payloads(entry)
    group_payloads = _trace_groups(entry)
    aggregation = _side_aggregation(entry)
    matched_paths = {
        str(path or "") for path in aggregation.get("matched_group_paths", [])
    }
    for group_payload in group_payloads:
        path = str(group_payload.get("path") or "")
        if not path.startswith("buy.groups[") or path not in matched_paths:
            continue
        for payload in _selected_group_payloads(group_payload, condition_payloads):
            rule = _rule_at_path(rules, str(payload.get("path") or ""))
            key = _canonical_condition_key(rule) or _payload_evidence_key(payload)
            records.append((
                _filter_name_from_payload(payload),
                _condition_actual_evidence(payload),
                (key,),
            ))
    return records


def _sell_evidence_records(
    entry: ValidationReplayEntry,
    rules: Mapping[str, Any],
) -> list[tuple[str, str, tuple[str, ...]]]:
    aggregation = _side_aggregation(entry)
    expression = aggregation.get("ui_signal_expression")
    expression_values = aggregation.get("ui_expression_values")
    if not isinstance(expression, Mapping) or not isinstance(expression_values, Mapping):
        return []
    passed, survivors = _expression_survivors(expression.get("ast"), expression_values)
    if not passed:
        return []
    identifier_map = expression.get("identifier_map")
    if not isinstance(identifier_map, Mapping):
        return []

    condition_payloads = _trace_payloads(entry)
    group_payloads = _trace_groups(entry)
    groups_by_path = {
        str(group.get("path") or ""): group for group in group_payloads
    }
    matched_paths = {
        str(path or "") for path in aggregation.get("matched_group_paths", [])
    }
    signals = _state(rules, "sell", "signals")
    records: list[tuple[str, str, tuple[str, ...]]] = []
    for identifier in survivors:
        signal_name = str(identifier_map.get(identifier) or "").strip()
        signal = _state(signals, signal_name)
        groups = signal.get("groups") if isinstance(signal.get("groups"), list) else []
        for group_index, group in enumerate(groups):
            if not isinstance(group, Mapping):
                continue
            group_path = f"sell.signals.{signal_name}.groups[{group_index}]"
            if group_path not in matched_paths:
                continue
            group_payload = groups_by_path.get(group_path)
            if not isinstance(group_payload, Mapping):
                continue
            selected = _selected_group_payloads(group_payload, condition_payloads)
            selected_by_prefix: dict[str, list[Mapping[str, Any]]] = {}
            for payload in selected:
                expression_id = str(payload.get("expression_id") or "").strip().upper()
                prefix = expression_id.rsplit("_", 1)[0]
                selected_by_prefix.setdefault(prefix, []).append(payload)
            rule_conditions = {
                str(condition.get("expression_id") or "").strip().upper(): condition
                for condition in group.get("conditions", [])
                if isinstance(condition, Mapping)
            }
            for prefix, payloads in selected_by_prefix.items():
                label = _SELL_EVIDENCE_FILTERS.get(prefix, prefix or "조건")
                if prefix == "GAP":
                    actual = _condition_actual_evidence(payloads[0])
                elif prefix == "OCR":
                    actual = " / ".join(_condition_actual_evidence(item) for item in payloads)
                elif prefix == "ARRAY":
                    actual = " / ".join(
                        _condition_actual_evidence(item) for item in payloads
                    )
                else:
                    actual = " / ".join(_condition_actual_evidence(item) for item in payloads)
                condition_keys = tuple(
                    _canonical_condition_key(rule_conditions.get(
                        str(payload.get("expression_id") or "").strip().upper(),
                        {},
                    )) or _payload_evidence_key(payload)
                    for payload in payloads
                )
                records.append((label, actual, condition_keys))
    return records


def signal_evidence_records_for_entry(
    entry: ValidationReplayEntry,
    settings_rules: Mapping[str, Any],
) -> tuple[SignalValidationEvidenceRecord, ...]:
    """Return structured final-decision Evidence without re-evaluating a signal."""
    if not isinstance(entry, ValidationReplayEntry):
        raise TypeError("entry must be ValidationReplayEntry")
    if not isinstance(settings_rules, Mapping):
        raise TypeError("settings_rules must be a mapping")
    if entry.signal != entry.evaluation_side:
        return ()
    records = (
        _buy_evidence_records(entry, settings_rules)
        if entry.evaluation_side == "BUY"
        else _sell_evidence_records(entry, settings_rules)
    )
    accepted: list[SignalValidationEvidenceRecord] = []
    seen_evidence: set[tuple[str, str, frozenset[str], str]] = set()
    for label, actual, condition_keys in records:
        normalized_keys = tuple(value for value in condition_keys if value)
        key_set = frozenset(normalized_keys)
        evidence_key = (label, actual, key_set, entry.signal_time)
        if evidence_key in seen_evidence:
            continue
        seen_evidence.add(evidence_key)
        accepted.append(SignalValidationEvidenceRecord(
            label=label,
            actual=actual,
            condition_keys=normalized_keys,
        ))
    return tuple(accepted)


def signal_evidence_lines_for_entry(
    entry: ValidationReplayEntry,
    settings_rules: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return only operator-visible evidence for one already-produced signal."""
    return tuple(
        f"▪ {record.label} {record.actual}"
        for record in signal_evidence_records_for_entry(entry, settings_rules)
    )


def signal_evidence_tooltip(
    entry: ValidationReplayEntry,
    settings_rules: Mapping[str, Any],
) -> str:
    if entry.signal != entry.evaluation_side:
        return ""
    text = str(entry.evaluation_time or "").strip()
    time_text = (
        f"{text[4:6]}/{text[6:8]} {text[8:10]}:{text[10:12]}"
        if len(text) == 14 and text.isdigit()
        else text or "-"
    )
    return "\n".join((
        f"{entry.evaluation_side} · {time_text}",
        *signal_evidence_lines_for_entry(entry, settings_rules),
    ))


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
