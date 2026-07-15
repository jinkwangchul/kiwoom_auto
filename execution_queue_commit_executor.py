# -*- coding: utf-8 -*-
"""Queue Commit Executor v1.

This executor is the first layer in this chain that may write a queue file.
The only allowed target shape is an existing ``runtime/order_queue.json`` file.
It never writes execution runtime files, rules.json, GUI state, SendOrder,
Broker, or Kiwoom.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from execution_queue_writer import commit_legacy_order_queued_record, preserve_queue_mutation_result


EXECUTOR_TYPE = "EXECUTION_QUEUE_COMMIT_EXECUTOR"
STATUS_COMMITTED = "COMMITTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"
NEXT_STAGE_REVIEW_REQUIRED = "QUEUE_COMMITTED_REVIEW_REQUIRED"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result(
    *,
    status: str,
    commit_id: str | None = None,
    commit_report: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    queue_write: bool = False,
    queue_commit_called: bool = False,
) -> dict[str, Any]:
    return {
        "executor_type": EXECUTOR_TYPE,
        "status": status,
        "commit_id": commit_id,
        "commit_report": deepcopy(commit_report) if isinstance(commit_report, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "runtime_write": False,
        "queue_write": queue_write,
        "queue_commit_called": queue_commit_called,
        "send_order_called": False,
    }


def _safe_queue_path(queue_path: Any) -> tuple[Path | None, str | None]:
    if not _text(queue_path):
        return None, "queue_path is required"
    path = Path(queue_path).resolve()
    if path.name != "order_queue.json" or path.parent.name != "runtime":
        return None, "queue_path must resolve to runtime/order_queue.json"
    if not path.parent.exists():
        return None, "runtime directory does not exist"
    if not path.exists():
        return None, "runtime/order_queue.json must already exist"
    return path, None


def _read_queue(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"failed to read order_queue json: {exc}"
    if not isinstance(data, dict):
        return {}, "order_queue root must be an object"
    if not isinstance(data.get("version", 1), int):
        return {}, "order_queue version must be an integer"
    if not isinstance(data.get("orders"), list):
        return {}, "order_queue orders must be a list"
    if any(not isinstance(item, dict) for item in data.get("orders", [])):
        return {}, "order_queue orders must contain only objects"
    return data, None


def _duplicate_reason(orders: list[Any], record: dict[str, Any]) -> str | None:
    order_id = _text(record.get("order_id"))
    request_hash = _text(record.get("request_hash"))
    lock_id = _text(record.get("lock_id"))
    for item in orders:
        current = _as_dict(item)
        if order_id and _text(current.get("order_id") or current.get("id")) == order_id:
            return "duplicate order_id"
        if request_hash and _text(current.get("request_hash")) == request_hash:
            return "duplicate request_hash"
        if lock_id and _text(current.get("lock_id")) == lock_id:
            return "duplicate lock_id"
    return None


def _build_record(commit_contract: dict[str, Any], commit_plan: dict[str, Any], commit_id: str) -> dict[str, Any]:
    order_contract = _as_dict(commit_plan.get("order_contract"))
    order_id = _text(commit_contract.get("order_id") or order_contract.get("order_id") or order_contract.get("id"))
    source_signal_id = _text(commit_contract.get("source_signal_id") or order_contract.get("source_signal_id"))
    record = {
        "id": _text(commit_contract.get("id")) or f"ORDER_QUEUED_{order_id}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_commit_executor",
        "commit_id": commit_id,
        "source_order_id": _text(commit_contract.get("source_order_id") or order_contract.get("source_order_id") or order_id),
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "code": _text(commit_contract.get("code") or order_contract.get("code")),
        "side": _text(commit_contract.get("side") or order_contract.get("side")).upper(),
        "quantity": deepcopy(commit_contract.get("quantity", order_contract.get("quantity"))),
        "price": deepcopy(commit_contract.get("price", order_contract.get("price"))),
        "request_hash": _text(commit_contract.get("request_hash") or order_contract.get("request_hash")),
        "lock_id": _text(commit_contract.get("lock_id") or order_contract.get("lock_id")),
        "execution_id": _text(commit_contract.get("execution_id") or order_contract.get("execution_id")),
        "queue_contract_version": _text(commit_contract.get("queue_contract_version")) or "preview-1",
        "send_order_called": False,
        "execution_enabled": False,
        "created_at": _now_text(),
        "updated_at": _now_text(),
    }
    return {key: value for key, value in record.items() if value not in ("", None)}


def _real_ready_contract_issues(
    commit_contract: dict[str, Any],
    commit_plan: dict[str, Any],
) -> list[str]:
    order_contract = _as_dict(commit_plan.get("order_contract"))
    if not order_contract:
        return ["commit_plan.order_contract is required"]

    issues: list[str] = []
    if _text(order_contract.get("status")).upper() != "REAL_READY":
        issues.append("commit_plan.order_contract.status is not REAL_READY")
    if order_contract.get("execution_enabled") is not True:
        issues.append("commit_plan.order_contract.execution_enabled is not true")
    if order_contract.get("preview_only") is not True:
        issues.append("commit_plan.order_contract.preview_only is not true")

    for field in ("order_id", "source_signal_id"):
        commit_value = _text(commit_contract.get(field))
        order_value = _text(order_contract.get(field))
        if not commit_value or not order_value:
            issues.append(f"{field} is required in commit and REAL_READY order contracts")
        elif commit_value != order_value:
            issues.append(f"commit_contract.{field} does not match REAL_READY order contract")
    return issues


def _verify_queue_item(path: Path, record: dict[str, Any], after_hash: str) -> bool:
    if _sha256_path(path) != after_hash:
        return False
    data, issue = _read_queue(path)
    if issue is not None:
        return False
    for item in data.get("orders", []):
        current = _as_dict(item)
        if _text(current.get("order_id")) == _text(record.get("order_id")) and _text(current.get("commit_id")) == _text(record.get("commit_id")):
            return current.get("status") == "ORDER_QUEUED" and current.get("send_order_called") is False
    return False


def execute_queue_commit_from_dry_run(
    dry_run_result: Any,
    queue_path: Any,
    manual_confirmation: bool = False,
) -> dict[str, Any]:
    """Commit a DRY_RUN_READY queue contract to an existing queue file."""
    dry_result = _as_dict(dry_run_result)
    if not dry_result:
        return _result(status=STATUS_INVALID, issues=["dry_run_result must be a dict"])
    if dry_result.get("status") == "INVALID":
        return _result(status=STATUS_INVALID, issues=["dry_run_result.status is INVALID"], warnings=_as_list(dry_result.get("warnings")))
    if dry_result.get("status") != "DRY_RUN_READY":
        return _result(status=STATUS_BLOCKED, issues=["dry_run_result.status is not DRY_RUN_READY"], warnings=_as_list(dry_result.get("warnings")))
    if dry_result.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["dry_run_result.preview_only is not true"])
    if manual_confirmation is not True:
        return _result(status=STATUS_BLOCKED, issues=["manual_confirmation is required"], warnings=_as_list(dry_result.get("warnings")))

    dry_run = _as_dict(dry_result.get("dry_run"))
    commit_contract = _as_dict(dry_run.get("commit_contract"))
    commit_plan = _as_dict(dry_run.get("commit_plan"))
    if not commit_contract:
        return _result(status=STATUS_INVALID, issues=["commit_contract is required"])
    if not commit_plan:
        return _result(status=STATUS_INVALID, issues=["commit_plan is required"])

    real_ready_issues = _real_ready_contract_issues(commit_contract, commit_plan)
    if real_ready_issues:
        return _result(
            status=STATUS_INVALID,
            issues=real_ready_issues,
            warnings=_as_list(dry_result.get("warnings")),
        )

    target_path, path_issue = _safe_queue_path(queue_path)
    if path_issue is not None or target_path is None:
        return _result(status=STATUS_INVALID, issues=[path_issue or "queue_path is invalid"])

    commit_id = f"QUEUE_COMMIT_{uuid4().hex}"
    record = _build_record(commit_contract, commit_plan, commit_id)
    if not _text(record.get("order_id")):
        return _result(status=STATUS_INVALID, issues=["record.order_id is required"], commit_id=commit_id)

    before_hash = _sha256_path(target_path)
    commit_report: dict[str, Any] = {
        "commit_id": commit_id,
        "queue_path": str(target_path),
        "backup_path": None,
        "source_order_id": record.get("source_order_id"),
        "order_id": record.get("order_id"),
        "source_signal_id": record.get("source_signal_id"),
        "code": record.get("code"),
        "side": record.get("side"),
        "quantity": record.get("quantity"),
        "price": record.get("price"),
        "request_hash": record.get("request_hash"),
        "lock_id": record.get("lock_id"),
        "execution_id": record.get("execution_id"),
        "before_hash": before_hash,
        "after_hash": None,
        "committed_at": None,
        "committed_record": deepcopy(record),
        "committed": False,
        "changed": False,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
        "execution_enabled": False,
        "next_stage": "BLOCKED",
        "rollback_attempted": False,
        "rollback_succeeded": False,
        "restored_from_backup": False,
        "manual_restore_required": False,
    }

    writer_result = commit_legacy_order_queued_record(
        record,
        target_path,
        backup=True,
        context={"manual_queue_write_confirmed": True},
    )
    commit_report["backup_path"] = writer_result.get("backup_path")
    commit_report["after_hash"] = _sha256_path(target_path) if target_path.exists() else None
    commit_report["writer_result"] = deepcopy(writer_result)
    commit_report = preserve_queue_mutation_result(commit_report, writer_result)
    if writer_result.get("committed") is True and writer_result.get("post_write_verified") is True:
        commit_report.update(
            {
                "committed_at": _now_text(),
                "committed": True,
                "changed": True,
                "preview_only": False,
                "queue_write": True,
                "queue_commit_called": True,
                "next_stage": NEXT_STAGE_REVIEW_REQUIRED,
            }
        )
        result = _result(
            status=STATUS_COMMITTED,
            commit_id=commit_id,
            commit_report=commit_report,
            issues=[],
            warnings=_as_list(dry_result.get("warnings")),
            queue_write=True,
            queue_commit_called=True,
        )
        return preserve_queue_mutation_result(result, writer_result)

    reasons = list(writer_result.get("blocked_reasons") or [])
    stage = _text(writer_result.get("write_stage"))
    status = STATUS_BLOCKED
    if stage in {"read_queue", "legacy_record"}:
        status = STATUS_INVALID
    if writer_result.get("committed") is True:
        status = STATUS_ERROR
        reasons = reasons or ["POST_WRITE_VERIFICATION_FAILED"]
        commit_report["manual_restore_required"] = True
    result = _result(
        status=status,
        commit_id=commit_id,
        commit_report=commit_report,
        issues=reasons or ["QUEUE_COMMIT_WRITE_FAILED"],
        warnings=_as_list(dry_result.get("warnings")),
        queue_write=bool(writer_result.get("queue_write")),
        queue_commit_called=True,
    )
    return preserve_queue_mutation_result(result, writer_result)
