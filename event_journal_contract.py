# -*- coding: utf-8 -*-
"""Stable contract and operator templates for the Event Journal."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from string import Formatter
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "event_journal_v1"

DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH = 512
DIAGNOSTIC_TEXT_MAX_LENGTH = 2048
DIAGNOSTIC_IDENTITY_MAX_LENGTH = 256
DIAGNOSTIC_REDACTED = "[REDACTED]"
DIAGNOSTIC_TRACEBACK_REDACTED = "[TRACEBACK REDACTED]"
DIAGNOSTIC_RAW_JSON_REDACTED = "[RAW JSON REDACTED]"
DIAGNOSTIC_UNAVAILABLE = "[UNAVAILABLE]"

CATEGORIES = frozenset({"SYSTEM", "OPERATION", "SETTING", "SIGNAL", "ORDER", "FILL"})
LEGACY_CATEGORIES = frozenset({"WARNING", "ERROR"})
SEVERITIES = frozenset({"INFO", "NOTICE", "WARNING", "ERROR"})
RESULTS = frozenset(
    {
        "SUCCESS",
        "COMPLETED",
        "REQUESTED",
        "ACCEPTED",
        "BLOCKED",
        "REJECTED",
        "FAILED",
        "UNCERTAIN",
        "CANCELLED",
    }
)

EVENT_TYPE_CATEGORIES = {
    "APP_STARTED": "SYSTEM",
    "APP_STOPPED": "SYSTEM",
    "OPERATION_HOST_STARTED": "SYSTEM",
    "OPERATION_HOST_STOPPED": "SYSTEM",
    "LOGIN_SUCCEEDED": "SYSTEM",
    "CONNECTION_LOST": "SYSTEM",
    "ACCOUNT_CHANGED": "SYSTEM",
    "ACCOUNT_QUERY_REQUESTED": "SYSTEM",
    "ACCOUNT_QUERY_SUCCEEDED": "SYSTEM",
    "ACCOUNT_QUERY_FAILED": "SYSTEM",
    "ACCOUNT_AUTH_REQUIRED": "SYSTEM",
    "ACCOUNT_REQUERY_REQUESTED": "SYSTEM",
    "ACCOUNT_REQUERY_SUCCEEDED": "SYSTEM",
    "ACCOUNT_REQUERY_FAILED": "SYSTEM",
    "RECOVERY_COMPLETED": "SYSTEM",
    "RECOVERY_WARNING": "SYSTEM",
    "RECOVERY_FAILED": "SYSTEM",
    "OPERATOR_SYSTEM_DECISION": "SYSTEM",
    "OPERATION_STARTED": "OPERATION",
    "OPERATION_STOPPED": "OPERATION",
    "EMERGENCY_STOPPED": "OPERATION",
    "EMERGENCY_RELEASED": "OPERATION",
    "OPERATION_EXCLUDED": "SETTING",
    "OPERATION_EXCLUSION_RELEASED": "SETTING",
    "SETTING_CHANGED": "SETTING",
    "TRADING_TIME_CHANGED": "SETTING",
    "ATS_CHANGED": "SETTING",
    "ROUTINE_CHANGED": "SETTING",
    "ROUTINE_INSTANCE_CREATED": "SETTING",
    "ROUTINE_INSTANCE_DELETED": "SETTING",
    "REVIEW_RETURNED": "SETTING",
    "REVIEW_UNASSIGNED": "SETTING",
    "REVIEW_FORCE_RESET": "SETTING",
    "BUY_SIGNAL_DETECTED": "SIGNAL",
    "SELL_SIGNAL_DETECTED": "SIGNAL",
    "APPROVAL_BLOCKED": "ORDER",
    "POLICY_BLOCKED": "ORDER",
    "EXECUTION_BLOCKED": "ORDER",
    "ORDER_QUEUED": "ORDER",
    "SEND_ORDER_REQUEST_ACCEPTED": "ORDER",
    "SEND_ORDER_REQUEST_REJECTED": "ORDER",
    "SEND_ORDER_RESULT_UNCERTAIN": "ORDER",
    "BROKER_ORDER_ACCEPTED": "ORDER",
    "BROKER_ORDER_REJECTED": "ORDER",
    "ORDER_CANCELLED": "ORDER",
    "PARTIAL_FILL": "FILL",
    "FULL_FILL": "FILL",
    "AUTO_CLOSE_STARTED": "OPERATION",
    "EARLY_CLOSE_STARTED": "OPERATION",
    "LIQUIDATION_REQUESTED": "OPERATION",
    "MANUAL_ATS_LIQUIDATION": "OPERATION",
    "LIQUIDATION_COMPLETED": "OPERATION",
    "PNL_CYCLE_BOUNDARY_CREATED": "OPERATION",
    "OPERATOR_OPERATION_DECISION": "OPERATION",
    "OPERATOR_SETTING_DECISION": "SETTING",
    "OPERATOR_ORDER_DECISION": "ORDER",
    "INTEGRITY_WARNING": "SYSTEM",
    "RUNTIME_WARNING": "SYSTEM",
    "PROCESSING_ERROR": "SYSTEM",
}
EVENT_TYPES = frozenset(EVENT_TYPE_CATEGORIES)
LEGACY_EVENT_TYPE_CATEGORIES = {
    "RECOVERY_WARNING": "WARNING",
    "RECOVERY_FAILED": "ERROR",
    "INTEGRITY_WARNING": "WARNING",
    "RUNTIME_WARNING": "WARNING",
    "PROCESSING_ERROR": "ERROR",
}

EVENT_TYPE_LABELS = {
    "APP_STARTED": "프로그램 시작",
    "APP_STOPPED": "프로그램 종료",
    "OPERATION_HOST_STARTED": "운영 Host 시작",
    "OPERATION_HOST_STOPPED": "운영 Host 정지",
    "LOGIN_SUCCEEDED": "로그인 성공",
    "CONNECTION_LOST": "연결 해제",
    "ACCOUNT_CHANGED": "계좌 변경",
    "ACCOUNT_QUERY_REQUESTED": "계좌조회 요청",
    "ACCOUNT_QUERY_SUCCEEDED": "계좌조회 성공",
    "ACCOUNT_QUERY_FAILED": "계좌조회 실패",
    "ACCOUNT_AUTH_REQUIRED": "계좌인증 필요",
    "ACCOUNT_REQUERY_REQUESTED": "계좌 재조회 요청",
    "ACCOUNT_REQUERY_SUCCEEDED": "계좌 재조회 성공",
    "ACCOUNT_REQUERY_FAILED": "계좌 재조회 실패",
    "RECOVERY_COMPLETED": "Recovery 완료",
    "RECOVERY_WARNING": "Recovery 경고",
    "RECOVERY_FAILED": "Recovery 실패",
    "OPERATOR_SYSTEM_DECISION": "사용자 시스템 선택",
    "OPERATION_STARTED": "운영 시작",
    "OPERATION_STOPPED": "운영 중지",
    "EMERGENCY_STOPPED": "긴급정지",
    "EMERGENCY_RELEASED": "긴급정지 해제",
    "OPERATION_EXCLUDED": "운영제외",
    "OPERATION_EXCLUSION_RELEASED": "제외해제",
    "SETTING_CHANGED": "설정 변경",
    "TRADING_TIME_CHANGED": "운영시간 변경",
    "ATS_CHANGED": "ATS 변경",
    "ROUTINE_CHANGED": "루틴 변경",
    "ROUTINE_INSTANCE_CREATED": "루틴 인스턴스 생성",
    "ROUTINE_INSTANCE_DELETED": "루틴 인스턴스 삭제",
    "REVIEW_RETURNED": "검토관리 복귀",
    "REVIEW_UNASSIGNED": "검토관리 미지정",
    "REVIEW_FORCE_RESET": "검토관리 강제초기화",
    "BUY_SIGNAL_DETECTED": "매수 신호 발생",
    "SELL_SIGNAL_DETECTED": "매도 신호 발생",
    "APPROVAL_BLOCKED": "승인 차단",
    "POLICY_BLOCKED": "운영정책 차단",
    "EXECUTION_BLOCKED": "주문 실행 차단",
    "ORDER_QUEUED": "주문 준비 완료",
    "SEND_ORDER_REQUEST_ACCEPTED": "주문 요청",
    "SEND_ORDER_REQUEST_REJECTED": "주문 요청 실패",
    "SEND_ORDER_RESULT_UNCERTAIN": "주문 결과 불확실",
    "BROKER_ORDER_ACCEPTED": "Broker 주문 접수",
    "BROKER_ORDER_REJECTED": "Broker 주문 거부",
    "ORDER_CANCELLED": "주문 취소",
    "PARTIAL_FILL": "부분체결",
    "FULL_FILL": "전량체결",
    "AUTO_CLOSE_STARTED": "자동마감 시작",
    "EARLY_CLOSE_STARTED": "조기마감 시작",
    "LIQUIDATION_REQUESTED": "청산 요청",
    "MANUAL_ATS_LIQUIDATION": "ATS 즉시청산",
    "LIQUIDATION_COMPLETED": "청산 완료",
    "PNL_CYCLE_BOUNDARY_CREATED": "손익 주기 경계 생성",
    "OPERATOR_OPERATION_DECISION": "사용자 운영 선택",
    "OPERATOR_SETTING_DECISION": "사용자 설정 선택",
    "OPERATOR_ORDER_DECISION": "사용자 주문 선택",
    "INTEGRITY_WARNING": "무결성 경고",
    "RUNTIME_WARNING": "Runtime 경고",
    "PROCESSING_ERROR": "처리 오류",
}

SUMMARY_TEMPLATES = {
    "APP_STARTED": "자동매매 프로그램이 시작되었습니다.",
    "APP_STOPPED": "자동매매 프로그램이 정상 종료되었습니다.",
    "OPERATION_HOST_STARTED": "자동매매 Operation Host가 시작되었습니다.",
    "OPERATION_HOST_STOPPED": "자동매매 Operation Host가 정지되었습니다.",
    "LOGIN_SUCCEEDED": "Broker 로그인이 완료되었습니다.",
    "CONNECTION_LOST": "Broker 연결이 해제되었습니다.",
    "ACCOUNT_CHANGED": "운영 계좌가 {account_display}(으)로 변경되었습니다.",
    "ACCOUNT_QUERY_REQUESTED": "{account_display} 계좌정보 조회를 요청했습니다.",
    "ACCOUNT_QUERY_SUCCEEDED": "{account_display} 계좌정보 조회가 완료되었습니다.",
    "ACCOUNT_QUERY_FAILED": "{account_display} 계좌정보 조회에 실패했습니다.",
    "ACCOUNT_AUTH_REQUIRED": "{account_display} 계좌인증이 필요합니다.",
    "ACCOUNT_REQUERY_REQUESTED": "{account_display} 계좌정보 재조회를 요청했습니다.",
    "ACCOUNT_REQUERY_SUCCEEDED": "{account_display} 계좌정보 재조회가 완료되었습니다.",
    "ACCOUNT_REQUERY_FAILED": "{account_display} 계좌정보 재조회에 실패했습니다.",
    "RECOVERY_COMPLETED": "이전 운영 상태 복구가 완료되었습니다.",
    "RECOVERY_WARNING": "이전 운영 상태 복구 중 확인이 필요한 항목이 발견되었습니다.",
    "RECOVERY_FAILED": "이전 운영 상태 복구에 실패했습니다.",
    "OPERATOR_SYSTEM_DECISION": "사용자의 시스템 선택 결과가 기록되었습니다.",
    "OPERATION_STARTED": "{target} 자동매매 운영을 시작했습니다.",
    "OPERATION_STOPPED": "{target} 자동매매 운영을 중지했습니다.",
    "EMERGENCY_STOPPED": "자동매매 운영이 긴급정지되었습니다.",
    "EMERGENCY_RELEASED": "자동매매 긴급정지가 해제되었습니다.",
    "OPERATION_EXCLUDED": "{stock_name}이 운영 대상에서 제외되었습니다.",
    "OPERATION_EXCLUSION_RELEASED": "{stock_name}의 운영제외가 해제되었습니다.",
    "SETTING_CHANGED": "{target} 설정이 변경되었습니다.",
    "TRADING_TIME_CHANGED": "운영시간 설정이 변경되었습니다.",
    "ATS_CHANGED": "ATS 설정이 변경되었습니다.",
    "ROUTINE_CHANGED": "{stock_name}의 적용 루틴이 변경되었습니다.",
    "ROUTINE_INSTANCE_CREATED": "{routine} 루틴 인스턴스가 생성되었습니다.",
    "ROUTINE_INSTANCE_DELETED": "{routine} 루틴 인스턴스가 삭제되었습니다.",
    "REVIEW_RETURNED": "{stock_name} 검토종목이 기존 운영관계로 복귀되었습니다.",
    "REVIEW_UNASSIGNED": "{stock_name} 검토종목이 미지정으로 전환되었습니다.",
    "REVIEW_FORCE_RESET": "{stock_name} 검토종목이 강제초기화되었습니다.",
    "BUY_SIGNAL_DETECTED": "{stock_name}에서 매수 신호가 발생했습니다.",
    "SELL_SIGNAL_DETECTED": "{stock_name}에서 매도 신호가 발생했습니다.",
    "APPROVAL_BLOCKED": "{stock_name} 주문 후보가 승인 단계에서 차단되었습니다.",
    "POLICY_BLOCKED": "{stock_name} 주문이 운영정책에 의해 차단되었습니다.",
    "EXECUTION_BLOCKED": "{stock_name} 주문이 실행 준비 단계에서 차단되었습니다.",
    "ORDER_QUEUED": "{stock_name} 주문 실행 준비가 완료되었습니다.",
    "SEND_ORDER_REQUEST_ACCEPTED": "{stock_name} 주문을 Broker에 요청했습니다.",
    "SEND_ORDER_REQUEST_REJECTED": "{stock_name} 주문 요청을 전달하지 못했습니다.",
    "SEND_ORDER_RESULT_UNCERTAIN": "{stock_name} 주문 요청 결과를 확정하지 못했습니다.",
    "BROKER_ORDER_ACCEPTED": "{stock_name} 주문이 Broker에 접수되었습니다.",
    "BROKER_ORDER_REJECTED": "{stock_name} 주문이 Broker에서 거부되었습니다.",
    "ORDER_CANCELLED": "{stock_name} 주문이 취소되었습니다.",
    "PARTIAL_FILL": "{stock_name} 주문이 누적 {filled_qty}주 체결되었습니다.",
    "FULL_FILL": "{stock_name} 주문 체결이 완료되었습니다.",
    "AUTO_CLOSE_STARTED": "{target} 자동마감을 시작했습니다.",
    "EARLY_CLOSE_STARTED": "{target} 조기마감을 시작했습니다.",
    "LIQUIDATION_REQUESTED": "{stock_name} 청산을 요청했습니다.",
    "MANUAL_ATS_LIQUIDATION": "{stock_name} ATS 즉시청산 결과가 기록되었습니다.",
    "LIQUIDATION_COMPLETED": "{stock_name} 청산이 완료되었습니다.",
    "PNL_CYCLE_BOUNDARY_CREATED": "확정 가능한 PnL 주기 경계가 생성되었습니다.",
    "OPERATOR_OPERATION_DECISION": "사용자의 운영 선택 결과가 기록되었습니다.",
    "OPERATOR_SETTING_DECISION": "사용자의 설정 선택 결과가 기록되었습니다.",
    "OPERATOR_ORDER_DECISION": "사용자의 주문 선택 결과가 기록되었습니다.",
    "INTEGRITY_WARNING": "{target}에서 무결성 이상이 발견되었습니다.",
    "RUNTIME_WARNING": "{target} Runtime 상태에 확인이 필요한 항목이 있습니다.",
    "PROCESSING_ERROR": "{target} 처리 중 오류가 발생했습니다.",
}

REQUIRED_FIELDS = frozenset(
    {"schema_version", "event_id", "occurred_at", "category", "severity", "event_type", "summary"}
)
OPTIONAL_FIELDS = frozenset(
    {
        "app_session_id",
        "result",
        "source",
        "target_type",
        "target_id",
        "target_name",
        "stock_code",
        "stock_name",
        "routine",
        "signal_id",
        "order_id",
        "execution_id",
        "broker_order_no",
        "command_id",
        "reason_code",
        "reason_args",
        "details",
        "changes",
        "component",
        "operation",
        "exception_type",
        "exception_message",
        "correlation_id",
        "parent_event_id",
        "stack_fingerprint",
        "build_version",
    }
)
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

DIAGNOSTIC_IDENTITY_FIELDS = frozenset(
    {
        "component",
        "operation",
        "exception_type",
        "correlation_id",
        "parent_event_id",
        "stack_fingerprint",
        "build_version",
    }
)

_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:password|passwd|pwd|token|api_?key|secret|credential|credentials|authorization|authentication|auth)(?:$|_)",
    re.IGNORECASE,
)
_INLINE_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|credential|authorization|authentication|auth)\b\s*[:=]\s*([^\s,;]+)"
)
_WINDOWS_USER_PATH_PATTERN = re.compile(
    r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\r\n]+"
)
_POSIX_USER_PATH_PATTERN = re.compile(r"(?i)(?<![\w])/(?:home|Users)/[^/\s]+")
_RAW_TRACEBACK_PATTERN = re.compile(
    r"(?is)Traceback\s+\(most recent call last\):.*"
)
_ACCOUNT_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){8,12}(?!\d)")


def _safe_text(value: Any) -> str:
    try:
        return str(value or "")
    except Exception:
        return DIAGNOSTIC_UNAVAILABLE


def _looks_like_raw_json(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    return (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    )


def sanitize_diagnostic_text(
    value: Any,
    *,
    max_length: int = DIAGNOSTIC_TEXT_MAX_LENGTH,
    mask_account_numbers: bool = False,
) -> str:
    """Return bounded diagnostic text without credentials, paths, or raw dumps.

    This helper is deliberately fail-open for Production: malformed objects or
    sanitizer failures return a non-sensitive placeholder and never raise.
    """

    try:
        text = _safe_text(value).replace("\x00", "")
        text = _RAW_TRACEBACK_PATTERN.sub(DIAGNOSTIC_TRACEBACK_REDACTED, text)
        if _looks_like_raw_json(text):
            try:
                decoded = json.loads(text)
            except Exception:
                decoded = None
            if isinstance(decoded, (dict, list)):
                text = DIAGNOSTIC_RAW_JSON_REDACTED
        text = _INLINE_CREDENTIAL_PATTERN.sub(
            lambda match: f"{match.group(1)}={DIAGNOSTIC_REDACTED}",
            text,
        )
        text = _WINDOWS_USER_PATH_PATTERN.sub("<USER_HOME>", text)
        text = _POSIX_USER_PATH_PATTERN.sub("<USER_HOME>", text)
        if mask_account_numbers:
            text = _ACCOUNT_NUMBER_PATTERN.sub("[ACCOUNT REDACTED]", text)
        text = " ".join(text.split())
        limit = max(1, int(max_length))
        if len(text) > limit:
            suffix = "...[TRUNCATED]"
            text = text[: max(1, limit - len(suffix))] + suffix
        return text
    except Exception:
        return DIAGNOSTIC_UNAVAILABLE


def _is_sensitive_diagnostic_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if _CREDENTIAL_KEY_PATTERN.search(normalized):
        return True
    return normalized in {
        "raw_payload",
        "raw_response",
        "raw_json",
        "broker_payload",
        "broker_response",
        "broker_raw",
        "chejan_raw",
    }


def _sanitize_diagnostic_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > 12:
        return DIAGNOSTIC_UNAVAILABLE
    if _is_sensitive_diagnostic_key(key):
        return DIAGNOSTIC_REDACTED
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        field_name = _safe_text(
            value.get("field_key")
            or value.get("field")
            or value.get("setting_key")
            or ""
        )
        sensitive_change = _is_sensitive_diagnostic_key(field_name)
        for child_key, child_value in value.items():
            safe_key = sanitize_diagnostic_text(child_key, max_length=128)
            if sensitive_change and safe_key in {"before", "after", "value"}:
                sanitized[safe_key] = DIAGNOSTIC_REDACTED
            else:
                sanitized[safe_key] = _sanitize_diagnostic_value(
                    child_value,
                    key=safe_key,
                    depth=depth + 1,
                )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_diagnostic_value(child, key=key, depth=depth + 1)
            for child in value
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    mask_account = "account" in str(key or "").lower() or "계좌" in str(key or "")
    return sanitize_diagnostic_text(value, mask_account_numbers=mask_account)


def sanitize_event_record(record: Any) -> dict[str, Any]:
    """Return a recursively sanitized Event Journal record without raising."""

    try:
        if not isinstance(record, dict):
            return {}
        sanitized = {
            str(key): _sanitize_diagnostic_value(value, key=str(key))
            for key, value in record.items()
        }
        if "exception_message" in sanitized:
            sanitized["exception_message"] = sanitize_diagnostic_text(
                sanitized.get("exception_message"),
                max_length=DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH,
                mask_account_numbers=True,
            )
        for key in DIAGNOSTIC_IDENTITY_FIELDS:
            if key in sanitized:
                sanitized[key] = sanitize_diagnostic_text(
                    sanitized.get(key),
                    max_length=DIAGNOSTIC_IDENTITY_MAX_LENGTH,
                )
        return sanitized
    except Exception:
        return {}


def make_stack_fingerprint(
    *,
    exception_type: Any,
    module: Any,
    function: Any,
    line: Any,
) -> str:
    """Build a stable, path-free diagnostic fingerprint without raising."""

    try:
        module_text = sanitize_diagnostic_text(module, max_length=256)
        module_text = module_text.replace("\\", "/")
        if "/" in module_text:
            module_text = module_text.rsplit("/", 1)[-1]
        parts = (
            sanitize_diagnostic_text(exception_type, max_length=128),
            module_text,
            sanitize_diagnostic_text(function, max_length=128),
            sanitize_diagnostic_text(line, max_length=32),
        )
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    except Exception:
        return ""


def new_event_id() -> str:
    return str(uuid4())


def new_app_session_id() -> str:
    return str(uuid4())


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


def _template_arguments(template: str) -> frozenset[str]:
    names = {
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name
    }
    return frozenset(names)


def required_template_arguments(event_type: str) -> frozenset[str]:
    template = SUMMARY_TEMPLATES.get(str(event_type or ""), "")
    return _template_arguments(template)


def render_summary(event_type: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    template = SUMMARY_TEMPLATES.get(str(event_type or ""))
    if template is None:
        return {"rendered": False, "summary": "", "error": "unsupported event_type"}
    values = arguments if isinstance(arguments, dict) else {}
    if str(event_type or "") == "PARTIAL_FILL" and values.get("order_qty") not in (None, ""):
        template = "{stock_name} 주문이 누적 {filled_qty}/{order_qty}주 체결되었습니다."
    missing = sorted(name for name in _template_arguments(template) if values.get(name) in (None, ""))
    if missing:
        return {
            "rendered": False,
            "summary": "",
            "error": f"missing template arguments: {', '.join(missing)}",
        }
    try:
        summary = template.format_map(values)
    except Exception as exc:
        return {"rendered": False, "summary": "", "error": f"template render failed: {exc}"}
    return {"rendered": True, "summary": summary, "error": ""}


def _contains_unmasked_account(value: Any) -> bool:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    return len(digits) >= 8 and "*" not in text


def account_safety_issues(value: Any, *, key: str = "") -> list[str]:
    issues: list[str] = []
    normalized_key = str(key or "").strip().lower()
    account_key = "account" in normalized_key or "계좌" in normalized_key
    if account_key and not isinstance(value, (dict, list, tuple)) and _contains_unmasked_account(value):
        issues.append(f"unmasked account value is not allowed: {key or '<value>'}")
    if isinstance(value, dict):
        field_name = str(
            value.get("field_key")
            or value.get("field")
            or value.get("setting_key")
            or ""
        ).strip().lower()
        if "account" in field_name or "계좌" in field_name:
            for value_key in ("before", "after", "value"):
                if _contains_unmasked_account(value.get(value_key)):
                    issues.append(
                        f"unmasked account value is not allowed: {field_name}.{value_key}"
                    )
        for child_key, child_value in value.items():
            issues.extend(account_safety_issues(child_value, key=str(child_key)))
    elif isinstance(value, (list, tuple)):
        for child_value in value:
            issues.extend(account_safety_issues(child_value, key=key))
    return issues


def validate_event_record(record: Any, *, allow_legacy_categories: bool = False) -> list[str]:
    if not isinstance(record, dict):
        return ["event record must be an object"]
    issues: list[str] = []
    unknown = sorted(set(record) - ALLOWED_FIELDS)
    if unknown:
        issues.append(f"unsupported fields: {', '.join(unknown)}")
    missing = sorted(field for field in REQUIRED_FIELDS if record.get(field) in (None, ""))
    if missing:
        issues.append(f"missing required fields: {', '.join(missing)}")
    if record.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version is invalid")
    category = str(record.get("category") or "")
    allowed_categories = CATEGORIES | LEGACY_CATEGORIES if allow_legacy_categories else CATEGORIES
    if category not in allowed_categories:
        issues.append("category is invalid")
    severity = str(record.get("severity") or "")
    if severity not in SEVERITIES:
        issues.append("severity is invalid")
    event_type = str(record.get("event_type") or "")
    if event_type not in EVENT_TYPES:
        issues.append("event_type is invalid")
    elif category in allowed_categories:
        accepted_categories = {EVENT_TYPE_CATEGORIES[event_type]}
        if allow_legacy_categories and event_type in LEGACY_EVENT_TYPE_CATEGORIES:
            accepted_categories.add(LEGACY_EVENT_TYPE_CATEGORIES[event_type])
        if category not in accepted_categories:
            issues.append("category does not match event_type")
    result = record.get("result")
    if result not in (None, "") and str(result) not in RESULTS:
        issues.append("result is invalid")
    if parse_aware_timestamp(record.get("occurred_at")) is None:
        issues.append("occurred_at must be timezone-aware ISO 8601")
    if record.get("changes") is not None:
        changes = record.get("changes")
        if not isinstance(changes, list) or any(not isinstance(item, dict) for item in changes):
            issues.append("changes must be an array of objects")
    if record.get("reason_args") is not None and not isinstance(record.get("reason_args"), dict):
        issues.append("reason_args must be an object")
    for field in DIAGNOSTIC_IDENTITY_FIELDS:
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            issues.append(f"{field} must be a string")
    exception_message = record.get("exception_message")
    if exception_message is not None and not isinstance(exception_message, str):
        issues.append("exception_message must be a string")
    elif isinstance(exception_message, str) and len(exception_message) > DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH:
        issues.append(
            f"exception_message exceeds {DIAGNOSTIC_EXCEPTION_MESSAGE_MAX_LENGTH} characters"
        )
    if str(record.get("target_type") or "").upper() == "ACCOUNT":
        for field in ("target_id", "target_name"):
            if _contains_unmasked_account(record.get(field)):
                issues.append(f"{field} must not contain a raw account number")
    issues.extend(account_safety_issues(record))
    return issues


def normalize_legacy_event_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a display/read copy using the current functional category."""

    event_type = str(record.get("event_type") or "")
    legacy_category = LEGACY_EVENT_TYPE_CATEGORIES.get(event_type)
    if legacy_category is None or str(record.get("category") or "") != legacy_category:
        return dict(record)
    normalized = dict(record)
    normalized["category"] = EVENT_TYPE_CATEGORIES[event_type]
    return normalized


def event_target_display(record: dict[str, Any]) -> str:
    target_name = str(record.get("target_name") or "").strip()
    stock_name = str(record.get("stock_name") or "").strip()
    stock_code = str(record.get("stock_code") or "").strip()
    if stock_name and stock_code:
        return f"{stock_name} ({stock_code})"
    if target_name:
        return target_name
    if stock_name:
        return stock_name
    return "전체"
