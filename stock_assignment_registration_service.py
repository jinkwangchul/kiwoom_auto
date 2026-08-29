"""Idempotent boundary for assigning an unassigned Stock to an Instance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from runtime_io import read_json_dict
from stock_repository import StockRepository, normalize_stock_code


_LOCK = RLock()
_IN_FLIGHT: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class StockRegistrationResult:
    success: bool
    changed: bool = False
    status: str = ""
    error: str = ""


def register_unassigned_stock_to_instance(
    project_root: Path | str,
    code: str,
    name: str,
    *,
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
            if current == target:
                return StockRegistrationResult(True, changed=False, status="ALREADY_CURRENT")
            if current:
                return StockRegistrationResult(False, status="CURRENT_ASSIGNED_ELSEWHERE")
            updated = repository.update_stock_routine_instance(
                clean_code,
                name,
                instance_id=target,
                instance_name=instance_name,
                definition_id=definition_id,
                routine_type=routine_type,
            )
            if not updated:
                linkage = getattr(repository, "last_assignment_linkage_result", None)
                return StockRegistrationResult(
                    False,
                    status="REGISTRATION_FAILED",
                    error=str(getattr(linkage, "error", "") or ""),
                )
            verified = repository.find_by_code(clean_code)
            if verified is None or verified.assigned_routine_instance_id != target:
                return StockRegistrationResult(False, status="READ_BACK_FAILED")
            return StockRegistrationResult(True, changed=True, status="REGISTERED")
        finally:
            _IN_FLIGHT.discard(key)
