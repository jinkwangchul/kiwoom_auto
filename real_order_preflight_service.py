# -*- coding: utf-8 -*-
"""Manual REAL preflight service.

This module previews and commits the manual transition from EXECUTABLE to
REAL_READY. It does not connect GUI buttons, timers, queues, or execution
request flows.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any
from datetime import datetime

from execution_queue_writer import mutate_order_queue, preserve_queue_mutation_result


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_COMMIT_REQUIRED = "REAL_PREFLIGHT_COMMIT_REQUIRED"
NEXT_STAGE_EXECUTION_PREVIEW_REQUIRED = "EXECUTION_PREVIEW_REQUIRED"
REAL_PREFLIGHT_REASON_READY = "실주문 사전검사 통과"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().upper() in {"TRUE", "YES", "Y", "1", "ON"}


def _quantity(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "real_preflight_preview": False,
        "preflight_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [reason],
        "warnings": [],
        "send_order_called": False,
    }


def _commit_blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "real_preflight_committed": False,
        "preflight_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "blocked_reasons": [reason],
        "warnings": [],
        "send_order_called": False,
    }


def _preview_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("operator_confirmed_for_real_preflight") is True
        or ctx.get("manual_real_preflight_confirmed") is True
    )


def _commit_confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    return (
        ctx.get("manual_real_preflight_commit_confirmed") is True
        or ctx.get("operator_confirmed_for_real_preflight_commit") is True
    )


def _expected_revision(context: Any) -> int | None:
    value = _as_dict(context).get("expected_revision")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def preview_real_order_preflight(order: Any, guard: Any, context: Any = None) -> dict[str, Any]:
    """Preview whether one order can be manually promoted to REAL_READY."""
    order_dict = _as_dict(order)
    guard_dict = _as_dict(guard)

    status = _clean_text(order_dict.get("status")).upper()
    if status != "EXECUTABLE":
        return _blocked("status", "order.status must be EXECUTABLE")

    if order_dict.get("execution_enabled") is not True:
        return _blocked("execution_enabled", "order.execution_enabled must be true")

    approval_status = _clean_text(order_dict.get("approval_status")).upper()
    if approval_status != "APPROVED":
        return _blocked("approval_status", "order.approval_status must be APPROVED")

    policy_status = _clean_text(order_dict.get("policy_status")).upper()
    if policy_status != "EXECUTABLE":
        return _blocked("policy_status", "order.policy_status must be EXECUTABLE")

    qty = _quantity(order_dict.get("quantity"))
    if qty is None or qty <= 0:
        return _blocked("quantity", "order.quantity must be greater than 0")

    side = _clean_text(order_dict.get("side")).upper()
    if side not in {"BUY", "SELL"}:
        return _blocked("side", "order.side must be BUY or SELL")

    order_type = _clean_text(order_dict.get("order_type"))
    if not order_type:
        return _blocked("order_type", "order.order_type is required")

    code = _clean_text(order_dict.get("code"))
    if not code:
        return _blocked("code", "order.code is required")

    source_signal_id = _clean_text(order_dict.get("source_signal_id"))
    if not source_signal_id:
        return _blocked("source_signal_id", "order.source_signal_id is required")

    if not _truthy(guard_dict.get("real_trade_enabled")):
        return _blocked("guard", "guard.real_trade_enabled is not true")

    if not _truthy(guard_dict.get("kiwoom_logged_in")):
        return _blocked("guard", "guard.kiwoom_logged_in is not true")

    if not _truthy(guard_dict.get("account_selected")):
        return _blocked("guard", "guard.account_selected is not true")

    if not _clean_text(guard_dict.get("account_no")):
        return _blocked("guard", "guard.account_no is required")

    if not _truthy(guard_dict.get("operator_confirmed")):
        return _blocked("guard", "guard.operator_confirmed is not true")

    if not _preview_confirmed(context):
        return _blocked("operator_confirmation", "manual real preflight confirmation is required")

    return {
        "real_preflight_preview": True,
        "preflight_stage": "real_preflight_preview_created",
        "next_stage": NEXT_STAGE_COMMIT_REQUIRED,
        "preview_only": True,
        "no_write": True,
        "order_id": _clean_text(order_dict.get("id")),
        "source_signal_id": source_signal_id,
        "code": code,
        "side": side,
        "quantity": qty,
        "order_type": order_type,
        "blocked_reasons": [],
        "warnings": [],
        "send_order_called": False,
    }


def commit_real_order_preflight(
    preflight_preview_result: Any,
    queue_path: str | Path | None,
    guard_path: str | Path | None = None,
    preview_queue_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Commit EXECUTABLE to REAL_READY through the canonical queue writer."""
    preview = _as_dict(preflight_preview_result)
    if preview.get("real_preflight_preview") is not True:
        return _commit_blocked("preflight_preview", "preflight_preview_result.real_preflight_preview is not true")

    if preview.get("next_stage") != NEXT_STAGE_COMMIT_REQUIRED:
        return _commit_blocked("preflight_preview", "preflight_preview_result.next_stage is not REAL_PREFLIGHT_COMMIT_REQUIRED")

    if queue_path is None or not str(queue_path).strip():
        return _commit_blocked("queue_path", "queue_path is required")

    if not _commit_confirmed(context):
        return _commit_blocked("operator_confirmation", "manual real preflight commit confirmation is required")

    snapshot = _as_dict(preview_queue_snapshot)
    snapshot_sha256 = ""
    if snapshot:
        snapshot_sha256 = _clean_text(snapshot.get("sha256")).upper()
        if not snapshot_sha256:
            return _commit_blocked("stale_preview", "preview_queue_snapshot.sha256 is required")

    order_id = _clean_text(preview.get("order_id"))
    if not order_id:
        return _commit_blocked("order_id", "preflight_preview_result.order_id is required")

    target_path = Path(queue_path)
    audit: dict[str, Any] = {"before_sha256": None, "after_sha256": None}

    def blocked(stage: str, reason: str) -> dict[str, Any]:
        return {"blocked": _commit_blocked(stage, reason)}

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        current_sha256 = _sha256_file(target_path)
        audit["before_sha256"] = current_sha256
        if snapshot and current_sha256 != snapshot_sha256:
            return blocked(
                "stale_preview",
                "queue file changed after real preflight preview; rerun REAL Preflight",
            )

        matches = [
            order
            for order in data["orders"]
            if _clean_text(order.get("id")) == order_id
        ]
        if len(matches) != 1:
            reason = "target order not found" if not matches else "target order identity matched multiple records"
            return blocked("order", reason)

        target_order = matches[0]
        bindings = (
            ("source_signal_id", _clean_text),
            ("code", _clean_text),
            ("side", lambda value: _clean_text(value).upper()),
            ("quantity", _quantity),
            ("order_type", _clean_text),
        )
        for field, normalize in bindings:
            if normalize(target_order.get(field)) != normalize(preview.get(field)):
                return blocked("preview_binding", f"preflight preview {field} does not match target order")

        validation = preview_real_order_preflight(
            target_order,
            {
                "real_trade_enabled": True,
                "kiwoom_logged_in": True,
                "account_selected": True,
                "account_no": "COMMIT_REVALIDATION",
                "operator_confirmed": True,
            },
            {"manual_real_preflight_confirmed": True},
        )
        if validation.get("real_preflight_preview") is not True:
            reasons = validation.get("blocked_reasons") if isinstance(validation.get("blocked_reasons"), list) else []
            reason = reasons[0] if reasons else "target order is not eligible for REAL preflight"
            return blocked(str(validation.get("preflight_stage", "order")), reason)

        updated_data = deepcopy(data)
        updated_matches = [
            order
            for order in updated_data["orders"]
            if _clean_text(order.get("id")) == order_id
        ]
        if len(updated_matches) != 1:
            return blocked("order", "target order identity changed during mutation")

        updated_order = updated_matches[0]
        before_status = _clean_text(updated_order.get("status")).upper()
        now = _now_text()
        updated_order["status"] = "REAL_READY"
        updated_order["real_preflight_status"] = "REAL_READY"
        updated_order["real_preflight_reason"] = REAL_PREFLIGHT_REASON_READY
        updated_order["real_preflight_checked_at"] = now
        updated_order["updated_at"] = now
        return {
            "data": updated_data,
            "result": {
                "order_id": order_id,
                "before_status": before_status,
                "after_status": "REAL_READY",
                "execution_enabled": updated_order.get("execution_enabled"),
                "real_preflight_status": updated_order.get("real_preflight_status"),
                "real_preflight_reason": updated_order.get("real_preflight_reason"),
                "before_sha256": current_sha256,
            },
        }

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        matches = [
            order
            for order in after_data.get("orders", [])
            if isinstance(order, dict) and _clean_text(order.get("id")) == order_id
        ]
        if len(matches) != 1:
            return {"write_stage": "real_ready_verify", "blocked_reasons": ["REAL_READY target must exist exactly once"]}
        order = matches[0]
        if _clean_text(order.get("status")).upper() != "REAL_READY":
            return {"write_stage": "real_ready_verify", "blocked_reasons": ["target status is not REAL_READY"]}
        if _clean_text(order.get("real_preflight_status")).upper() != "REAL_READY":
            return {"write_stage": "real_ready_verify", "blocked_reasons": ["real_preflight_status is not REAL_READY"]}
        audit["after_sha256"] = _sha256_file(target_path)
        result = mutation.get("result")
        if isinstance(result, dict):
            result["after_sha256"] = audit["after_sha256"]
        return None

    mutation_result = mutate_order_queue(
        target_path,
        mutate,
        operation_name="real_order_preflight",
        success_stage="real_ready_committed",
        next_stage=NEXT_STAGE_EXECUTION_PREVIEW_REQUIRED,
        backup=backup,
        context=context,
        expected_revision=_expected_revision(context),
        verify=verify,
    )

    if audit["after_sha256"] is None and target_path.exists():
        audit["after_sha256"] = _sha256_file(target_path)

    if mutation_result.get("committed") is not True or mutation_result.get("post_write_verified") is not True:
        stage = _clean_text(mutation_result.get("preflight_stage") or mutation_result.get("write_stage")) or "write_queue"
        reasons = mutation_result.get("blocked_reasons") if isinstance(mutation_result.get("blocked_reasons"), list) else []
        result = _commit_blocked(stage, reasons[0] if reasons else "queue mutation failed")
        result.update({key: value for key, value in mutation_result.items() if key not in result})
        result.update(
            {
                "order_id": order_id,
                "order_queue_path": str(target_path),
                "guard_path": str(guard_path) if guard_path is not None else None,
                "before_sha256": audit["before_sha256"],
                "after_sha256": audit["after_sha256"],
            }
        )
        return preserve_queue_mutation_result(result, mutation_result)

    result = {
        "real_preflight_committed": True,
        "preflight_stage": "real_ready_committed",
        "next_stage": NEXT_STAGE_EXECUTION_PREVIEW_REQUIRED,
        "changed": mutation_result.get("changed") is True,
        "order_id": order_id,
        "before_status": mutation_result.get("before_status"),
        "after_status": mutation_result.get("after_status"),
        "execution_enabled": mutation_result.get("execution_enabled"),
        "real_preflight_status": mutation_result.get("real_preflight_status"),
        "real_preflight_reason": mutation_result.get("real_preflight_reason"),
        "order_queue_path": str(target_path),
        "guard_path": str(guard_path) if guard_path is not None else None,
        "backup_path": mutation_result.get("backup_path"),
        "before_sha256": audit["before_sha256"],
        "after_sha256": audit["after_sha256"],
        "send_order_called": False,
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update({key: value for key, value in mutation_result.items() if key not in result})
    return preserve_queue_mutation_result(result, mutation_result)
