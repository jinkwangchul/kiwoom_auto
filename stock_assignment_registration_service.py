"""Idempotent boundary for assigning an unassigned Stock to an Instance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from runtime_io import read_json_dict
from stock_repository import StockRepository, normalize_stock_code
from assignment_authorization_service import (
    ASSIGNMENT_INTENT_ASSIGN,
    execute_assignment_change,
)


_LOCK = RLock()
_IN_FLIGHT: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class StockRegistrationResult:
    success: bool
    changed: bool = False
    status: str = ""
    error: str = ""
    reason_code: str = ""
    transaction_id: str = ""
    assignment_before: str = ""
    assignment_after: str = ""
    reconciliation_required: bool = False


def register_unassigned_stock_to_instance(
    project_root: Path | str,
    code: str,
    name: str,
    *,
    operation_owner: object | None = None,
    instance_id: str,
    instance_name: str,
    definition_id: str,
    routine_type: str,
) -> StockRegistrationResult:
    root = Path(project_root).resolve(strict=False)
    clean_code = normalize_stock_code(code)
    key = (str(root), clean_code)
    with _LOCK:
        if key in _IN_FLIGHT:
            return StockRegistrationResult(False, status="REGISTRATION_IN_FLIGHT")
        _IN_FLIGHT.add(key)
        try:
            repository = StockRepository(root)
            stock = repository.find_by_code(clean_code)
            if stock is None:
                return StockRegistrationResult(False, status="STOCK_NOT_REGISTERED")
            config_path = root / stock.stock_path / "config.json"
            current = str(
                read_json_dict(config_path).get("assigned_routine_instance_id", "") or ""
            ).strip()
            target = str(instance_id or "").strip()
            if current and current != target:
                return StockRegistrationResult(False, status="CURRENT_ASSIGNED_ELSEWHERE")
            assignment = execute_assignment_change(
                operation_owner,
                root,
                clean_code,
                name,
                instance_id=target,
                instance_name=instance_name,
                definition_id=definition_id,
                routine_type=routine_type,
                expected_instance_id=current,
                intent=ASSIGNMENT_INTENT_ASSIGN,
            )
            if not assignment.ok:
                return StockRegistrationResult(
                    False,
                    status="REGISTRATION_FAILED",
                    error=assignment.error,
                    reason_code=assignment.reason_code,
                    transaction_id=assignment.transaction_id,
                    assignment_before=assignment.assignment_before,
                    assignment_after=assignment.assignment_after,
                    reconciliation_required=assignment.reconciliation_required,
                )
            verified = repository.find_by_code(clean_code)
            if verified is None or verified.assigned_routine_instance_id != target:
                return StockRegistrationResult(False, status="READ_BACK_FAILED")
            return StockRegistrationResult(
                True,
                changed=assignment.changed,
                status="REGISTERED" if assignment.changed else "ALREADY_CURRENT",
                reason_code=assignment.reason_code,
                transaction_id=assignment.transaction_id,
                assignment_before=assignment.assignment_before,
                assignment_after=assignment.assignment_after,
            )
        finally:
            _IN_FLIGHT.discard(key)
