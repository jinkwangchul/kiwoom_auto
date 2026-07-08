# -*- coding: utf-8 -*-
"""SendOrder result recorder v1 for explicit temp/test execution files only."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


RECORDER_TYPE = "SEND_ORDER_RESULT_RECORDER_V1"
STATUS_RECORDED = "RECORDED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"

PROJECT_ROOT = Path(__file__).resolve().parent
PROJECT_ORDER_EXECUTIONS_PATH = (PROJECT_ROOT / "runtime" / "order_executions.json").resolve()


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
    record_report: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    record_called: bool = False,
    runtime_write: bool = False,
) -> dict[str, Any]:
    return {
        "recorder_type": RECORDER_TYPE,
        "status": status,
        "record_report": deepcopy(record_report) if isinstance(record_report, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "record_called": bool(record_called),
        "runtime_write": bool(runtime_write),
        "queue_write": False,
        "chejan_called": False,
    }


def _safe_record_path(record_path: Any) -> tuple[Path | None, str | None]:
    if not _text(record_path):
        return None, "record_path is required"
    try:
        path = Path(record_path).resolve()
    except Exception as exc:
        return None, f"record_path is invalid: {exc}"
    if path == PROJECT_ORDER_EXECUTIONS_PATH:
        return None, "project runtime/order_executions.json is not allowed"
    if path.name != "order_executions.json":
        return None, "record_path must resolve to order_executions.json"
    if not path.parent.exists():
        return None, "record_path parent directory does not exist"
    if not path.exists():
        return None, "record_path must already exist"
    return path, None


def _read_executions(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"failed to read order_executions json: {exc}"
    if not isinstance(data, dict):
        return {}, "order_executions root must be an object"
    if not isinstance(data.get("version", 1), int):
        return {}, "order_executions version must be an integer"
    if not isinstance(data.get("executions"), list):
        return {}, "order_executions executions must be a list"
    if any(not isinstance(item, dict) for item in data.get("executions", [])):
        return {}, "order_executions executions must contain only objects"
    return data, None


def _duplicate_reason(executions: list[Any], record: dict[str, Any]) -> str | None:
    dispatch_id = _text(record.get("dispatch_id"))
    order_id = _text(record.get("order_id"))
    for item in executions:
        current = _as_dict(item)
        if dispatch_id and _text(current.get("dispatch_id")) == dispatch_id:
            return "duplicate dispatch_id"
        if order_id and _text(current.get("order_id")) == order_id:
            return "duplicate order_id"
    return None


def _build_record(record_contract: dict[str, Any], record_id: str) -> dict[str, Any]:
    record = deepcopy(record_contract)
    record["record_id"] = record_id
    record["status"] = "SEND_ORDER_RESULT_RECORDED"
    record["recorded_by"] = RECORDER_TYPE
    record["recorded_at"] = _now_text()
    record["record_called"] = True
    record["runtime_write"] = True
    record["queue_write"] = False
    record["chejan_called"] = False
    return record


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _restore_backup(backup_path: Path, record_path: Path) -> bool:
    try:
        shutil.copy2(backup_path, record_path)
        return True
    except Exception:
        return False


def _verify_record(path: Path, record: dict[str, Any], after_hash: str) -> bool:
    if _sha256_path(path) != after_hash:
        return False
    data, issue = _read_executions(path)
    if issue is not None:
        return False
    for item in data.get("executions", []):
        current = _as_dict(item)
        if _text(current.get("record_id")) == _text(record.get("record_id")):
            return (
                _text(current.get("dispatch_id")) == _text(record.get("dispatch_id"))
                and _text(current.get("order_id")) == _text(record.get("order_id"))
                and current.get("status") == "SEND_ORDER_RESULT_RECORDED"
            )
    return False


def record_send_order_result(
    recorder_contract_result: Any,
    record_path: Any,
    manual_confirmation: bool = False,
) -> dict[str, Any]:
    """Record a RECORD_READY SendOrder result contract into an explicit temp file."""
    contract_result = _as_dict(recorder_contract_result)
    if not contract_result:
        return _result(status=STATUS_INVALID, issues=["recorder_contract_result must be a dict"])
    if contract_result.get("status") == STATUS_INVALID:
        return _result(status=STATUS_INVALID, issues=["recorder_contract_result.status is INVALID"], warnings=_as_list(contract_result.get("warnings")))
    if contract_result.get("status") != "RECORD_READY":
        return _result(status=STATUS_BLOCKED, issues=["recorder_contract_result.status is not RECORD_READY"], warnings=_as_list(contract_result.get("warnings")))
    if manual_confirmation is not True:
        return _result(status=STATUS_BLOCKED, issues=["manual_confirmation is required"], warnings=_as_list(contract_result.get("warnings")))

    record_contract = _as_dict(contract_result.get("record_contract"))
    if not record_contract:
        return _result(status=STATUS_INVALID, issues=["record_contract is required"])

    target_path, path_issue = _safe_record_path(record_path)
    if path_issue is not None or target_path is None:
        return _result(status=STATUS_INVALID, issues=[path_issue or "record_path is invalid"])

    data, read_issue = _read_executions(target_path)
    if read_issue is not None:
        return _result(status=STATUS_INVALID, issues=[read_issue])

    record_id = f"SEND_ORDER_RECORD_{uuid4().hex}"
    record = _build_record(record_contract, record_id)
    duplicate = _duplicate_reason(data["executions"], record)
    if duplicate:
        return _result(status=STATUS_BLOCKED, issues=[duplicate], warnings=_as_list(contract_result.get("warnings")))

    before_hash = _sha256_path(target_path)
    backup_path = target_path.with_name(f"{target_path.name}.{record_id}.bak")
    report: dict[str, Any] = {
        "record_id": record_id,
        "record_path": str(target_path),
        "backup_path": str(backup_path),
        "dispatch_id": record.get("dispatch_id"),
        "order_id": record.get("order_id"),
        "source_order_id": record.get("source_order_id"),
        "source_signal_id": record.get("source_signal_id"),
        "code": record.get("code"),
        "side": record.get("side"),
        "quantity": record.get("quantity"),
        "price": record.get("price"),
        "hoga": record.get("hoga"),
        "send_order_return_code": record.get("send_order_return_code"),
        "send_order_status": record.get("send_order_status"),
        "before_hash": before_hash,
        "after_hash": None,
        "recorded_at": None,
        "recorded": False,
        "changed": False,
        "runtime_write": False,
        "queue_write": False,
        "chejan_called": False,
        "rollback_attempted": False,
        "rollback_succeeded": False,
        "restored_from_backup": False,
        "manual_restore_required": False,
        "record": deepcopy(record),
    }

    try:
        shutil.copy2(target_path, backup_path)
        updated = deepcopy(data)
        updated["version"] = updated.get("version", 1)
        updated["updated_at"] = _now_text()
        updated["executions"].append(deepcopy(record))
        _write_json_atomic(target_path, updated)
        after_hash = _sha256_path(target_path)
        report["after_hash"] = after_hash

        if not _verify_record(target_path, record, after_hash):
            report["rollback_attempted"] = True
            restored = _restore_backup(backup_path, target_path)
            report["rollback_succeeded"] = restored
            report["restored_from_backup"] = restored
            report["manual_restore_required"] = not restored
            issues = ["POST_WRITE_VERIFICATION_FAILED"]
            if not restored:
                issues.append("MANUAL_RESTORE_REQUIRED")
            return _result(
                status=STATUS_ERROR,
                record_report=report,
                issues=issues,
                warnings=_as_list(contract_result.get("warnings")),
                record_called=True,
                runtime_write=False,
            )

        report.update(
            {
                "recorded_at": _now_text(),
                "recorded": True,
                "changed": True,
                "runtime_write": True,
            }
        )
        return _result(
            status=STATUS_RECORDED,
            record_report=report,
            issues=[],
            warnings=_as_list(contract_result.get("warnings")),
            record_called=True,
            runtime_write=True,
        )
    except Exception as exc:
        report["rollback_attempted"] = backup_path.exists()
        if backup_path.exists():
            restored = _restore_backup(backup_path, target_path)
            report["rollback_succeeded"] = restored
            report["restored_from_backup"] = restored
            report["manual_restore_required"] = not restored
        return _result(
            status=STATUS_ERROR,
            record_report=report,
            issues=[f"SEND_ORDER_RESULT_RECORD_FAILED: {exc}"] + (["MANUAL_RESTORE_REQUIRED"] if report["manual_restore_required"] else []),
            warnings=_as_list(contract_result.get("warnings")),
            record_called=True,
            runtime_write=False,
        )
