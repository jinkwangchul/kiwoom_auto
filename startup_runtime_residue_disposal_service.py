# -*- coding: utf-8 -*-
"""Archive and replace confirmed non-execution startup Runtime residue."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from startup_runtime_initializer import (
    STATUS_INITIALIZED,
    initialize_pristine_startup_runtime,
)


CLASSIFICATION_DEVELOPMENT_RESIDUE = "DEVELOPMENT_RESIDUE"
CLASSIFICATION_OPERATION_EVIDENCE = "OPERATION_EVIDENCE"
CLASSIFICATION_UNKNOWN = "UNKNOWN"


def _read_object(path: Path) -> tuple[dict[str, Any], str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{path.name} read failed: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path.name} root must be an object"
    return data, ""


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_startup_runtime_residue(
    runtime_dir: str | Path,
) -> dict[str, Any]:
    root = Path(runtime_dir)
    queue_path = root / "order_queue.json"
    signals_path = root / "routine_signals.json"
    queue, queue_error = _read_object(queue_path)
    signals, signals_error = _read_object(signals_path)
    issues = [item for item in (queue_error, signals_error) if item]
    orders = queue.get("orders")
    signal_items = signals.get("signals")
    if issues or not isinstance(orders, list) or not isinstance(signal_items, list):
        return {
            "classification": CLASSIFICATION_UNKNOWN,
            "safe_to_dispose": False,
            "issues": issues or ["runtime list schema is invalid"],
        }

    operation_markers: list[str] = []
    for order in orders:
        if not isinstance(order, dict):
            operation_markers.append("non-object order record")
            continue
        status = str(order.get("status", "") or "").upper()
        if status in {
            "DISPATCH_CLAIMED",
            "SEND_ATTEMPTED",
            "SEND_CALL_IN_PROGRESS",
            "SEND_UNCERTAIN",
            "SEND_CALL_ACCEPTED",
            "BROKER_ACCEPTED",
            "PARTIALLY_FILLED",
            "FILLED",
        }:
            operation_markers.append(f"{order.get('id')}: execution status {status}")
        if any(
            str(order.get(field, "") or "").strip()
            for field in (
                "broker_order_no",
                "execution_id",
                "lock_id",
                "chejan_events",
            )
        ):
            operation_markers.append(f"{order.get('id')}: broker/execution identity")
        if order.get("execution_enabled") is True:
            operation_markers.append(f"{order.get('id')}: execution enabled")
        try:
            quantity = int(order.get("quantity", 0) or 0)
        except Exception:
            quantity = 0
        if quantity > 0:
            operation_markers.append(f"{order.get('id')}: positive quantity")

    for signal in signal_items:
        if not isinstance(signal, dict):
            operation_markers.append("non-object signal record")
            continue
        preview = signal.get("preview_summary")
        preview = preview if isinstance(preview, dict) else {}
        manager = signal.get("order_manager_result")
        manager = manager if isinstance(manager, dict) else {}
        if signal.get("execution_enabled") is True:
            operation_markers.append(f"{signal.get('id')}: execution enabled")
        if preview.get("send_order_called") is True:
            operation_markers.append(f"{signal.get('id')}: SendOrder called")
        if manager.get("order_executor_called") is True:
            operation_markers.append(f"{signal.get('id')}: executor called")
        if str(signal.get("status", "") or "").upper() not in {"BLOCKED", "EXPIRED"}:
            operation_markers.append(
                f"{signal.get('id')}: non-terminal dry-run status "
                f"{signal.get('status')}"
            )

    test_markers = (
        "test",
        "manual_verification",
        "temporary verification",
        "sample-candles",
        "adapter_save_probe",
    )
    serialized = json.dumps(
        {"orders": orders, "signals": signal_items},
        ensure_ascii=False,
    ).lower()
    has_development_marker = any(marker in serialized for marker in test_markers)

    if operation_markers:
        classification = CLASSIFICATION_OPERATION_EVIDENCE
        safe = False
    elif not has_development_marker:
        classification = CLASSIFICATION_UNKNOWN
        safe = False
    else:
        classification = CLASSIFICATION_DEVELOPMENT_RESIDUE
        safe = True

    return {
        "classification": classification,
        "safe_to_dispose": safe,
        "issues": operation_markers,
        "order_count": len(orders),
        "signal_count": len(signal_items),
        "hashes": {
            queue_path.name: _hash(queue_path),
            signals_path.name: _hash(signals_path),
        },
        "evidence": {
            "all_orders_zero_quantity": not any(
                int(order.get("quantity", 0) or 0) > 0
                for order in orders
                if isinstance(order, dict)
            ),
            "all_orders_execution_disabled": all(
                order.get("execution_enabled") is not True
                for order in orders
                if isinstance(order, dict)
            ),
            "all_signals_execution_disabled": all(
                signal.get("execution_enabled") is not True
                for signal in signal_items
                if isinstance(signal, dict)
            ),
            "development_marker_found": has_development_marker,
        },
    }


def dispose_confirmed_startup_runtime_residue(
    runtime_dir: str | Path,
    archive_dir: str | Path,
    *,
    confirmed_inspection: dict[str, Any],
    manual_disposal_confirmed: bool = False,
) -> dict[str, Any]:
    """Archive confirmed residue, remove it, and initialize canonical evidence."""
    if manual_disposal_confirmed is not True:
        return {"status": "BLOCKED", "issues": ["MANUAL_DISPOSAL_CONFIRMATION_REQUIRED"]}
    if confirmed_inspection.get("classification") != CLASSIFICATION_DEVELOPMENT_RESIDUE:
        return {"status": "BLOCKED", "issues": ["DEVELOPMENT_RESIDUE_NOT_CONFIRMED"]}
    if confirmed_inspection.get("safe_to_dispose") is not True:
        return {"status": "BLOCKED", "issues": ["RUNTIME_RESIDUE_NOT_SAFE"]}

    root = Path(runtime_dir)
    archive = Path(archive_dir)
    targets = [root / "order_queue.json", root / "routine_signals.json"]
    expected_hashes = confirmed_inspection.get("hashes")
    expected_hashes = expected_hashes if isinstance(expected_hashes, dict) else {}
    for path in targets:
        if not path.exists() or _hash(path) != expected_hashes.get(path.name):
            return {"status": "BLOCKED", "issues": [f"EVIDENCE_CHANGED: {path.name}"]}
    if archive.exists():
        return {"status": "BLOCKED", "issues": ["ARCHIVE_TARGET_ALREADY_EXISTS"]}

    archive.mkdir(parents=True, exist_ok=False)
    for path in targets:
        shutil.copy2(path, archive / path.name)
    manifest = {
        "classification": CLASSIFICATION_DEVELOPMENT_RESIDUE,
        "source_runtime_dir": str(root.resolve()),
        "hashes": expected_hashes,
        "order_count": confirmed_inspection.get("order_count", 0),
        "signal_count": confirmed_inspection.get("signal_count", 0),
        "evidence": confirmed_inspection.get("evidence", {}),
    }
    (archive / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for path in targets:
        path.unlink()
    initialized = initialize_pristine_startup_runtime(root)
    if initialized.get("status") != STATUS_INITIALIZED:
        for path in targets:
            if not path.exists():
                shutil.copy2(archive / path.name, path)
        return {
            "status": "ERROR",
            "issues": ["INITIALIZATION_FAILED"],
            "initializer": initialized,
            "archive_dir": str(archive),
        }

    return {
        "status": "DISPOSED_AND_INITIALIZED",
        "archive_dir": str(archive),
        "initializer": initialized,
        "runtime_write": True,
    }
