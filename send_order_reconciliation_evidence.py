# -*- coding: utf-8 -*-
"""Append-only Kiwoom SendOrder reconciliation evidence journal."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import threading
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_EVIDENCE_PATH = (
    PROJECT_ROOT
    / "runtime"
    / "diagnostics"
    / "kiwoom_send_order_reconciliation"
    / "events.jsonl"
)
SCHEMA = "kiwoom_send_order_reconciliation_evidence_v1"
_WRITE_LOCK = threading.Lock()
_ALLOWED_SOURCES = {"kiwoom_order_tr", "kiwoom_message"}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()
def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "recorded": False,
        "reason": str(reason or "").strip(),
    }
    result.update(extra)
    return result


def inspect_send_order_reconciliation_evidence_storage(
    evidence_path: Path = DEFAULT_EVIDENCE_PATH,
) -> dict[str, Any]:
    """Read-only preflight for the append-only transport evidence destination."""

    target = Path(evidence_path)
    try:
        target_resolved = target.resolve(strict=False)
        runtime_root = (PROJECT_ROOT / "runtime").resolve(strict=False)
        under_runtime = (
            target_resolved == runtime_root
            or runtime_root in target_resolved.parents
        )
    except Exception:
        target_resolved = target
        under_runtime = False

    existing_ancestor = target.parent
    try:
        while not existing_ancestor.exists():
            parent = existing_ancestor.parent
            if parent == existing_ancestor:
                break
            existing_ancestor = parent
        ancestor_is_directory = existing_ancestor.is_dir()
        ancestor_writable = (
            ancestor_is_directory
            and os.access(existing_ancestor, os.W_OK)
        )
    except Exception:
        ancestor_is_directory = False
        ancestor_writable = False

    target_type_ok = True
    try:
        if target.exists():
            target_type_ok = target.is_file()
    except Exception:
        target_type_ok = False

    checks = {
        "canonical_runtime_path": under_runtime,
        "existing_ancestor_is_directory": ancestor_is_directory,
        "existing_ancestor_writable": ancestor_writable,
        "target_type_ok": target_type_ok,
    }
    missing = [
        name for name, available in checks.items() if available is not True
    ]
    return {
        "ready": not missing,
        "evidence_path": str(target_resolved),
        "existing_ancestor": str(existing_ancestor),
        "checks": checks,
        "missing_capabilities": missing,
    }


def build_send_order_reconciliation_evidence(
    raw_event: Any,
) -> dict[str, Any]:
    """Build one detached reconciliation record without mutating Production state."""

    if not isinstance(raw_event, dict):
        return _blocked("RAW_EVENT_INVALID")
    source = _clean(raw_event.get("source"))
    if source not in _ALLOWED_SOURCES:
        return _blocked("RAW_EVENT_SOURCE_UNSUPPORTED", source=source)
    request = raw_event.get("send_order_request")
    if not isinstance(request, dict):
        return _blocked("SEND_ORDER_REQUEST_EVIDENCE_MISSING", source=source)

    identity = {
        "order_id": _clean(request.get("order_id")),
        "dispatch_claim_id": _clean(request.get("dispatch_claim_id")),
        "send_order_attempt_id": _clean(request.get("send_order_attempt_id")),
        "execution_id": _clean(request.get("execution_id")),
        "signal_id": _clean(request.get("signal_id")),
    }
    missing = [
        key
        for key in ("order_id", "dispatch_claim_id", "send_order_attempt_id")
        if not identity[key]
    ]
    if missing:
        return _blocked(
            "SEND_ORDER_RECONCILIATION_IDENTITY_INCOMPLETE",
            missing_fields=missing,
        )

    event_session = _clean(raw_event.get("login_session_id"))
    request_session = _clean(request.get("login_session_id"))
    event_epoch = raw_event.get("connection_epoch")
    request_epoch = request.get("connection_epoch")
    if (
        not event_session
        or event_session != request_session
        or event_epoch != request_epoch
    ):
        return _blocked("SEND_ORDER_RECONCILIATION_SESSION_MISMATCH")

    broker_evidence = {
        "broker_order_no": _clean(raw_event.get("broker_order_no")),
        "order_no_error": _clean(raw_event.get("order_no_error")),
        "message": _clean(raw_event.get("message")),
        "trcode": _clean(raw_event.get("trcode")),
        "record_name": _clean(raw_event.get("record_name")),
        "prev_next": _clean(raw_event.get("prev_next")),
    }
    record = {
        "schema": SCHEMA,
        "recorded_at": datetime.now().astimezone().isoformat(
            sep=" ",
            timespec="milliseconds",
        ),
        "event_received_at": _clean(raw_event.get("received_at")),
        "source": source,
        "rqname": _clean(raw_event.get("rqname") or request.get("rqname")),
        "screen_no": _clean(
            raw_event.get("screen_no") or request.get("screen_no")
        ),
        "login_session_id": event_session,
        "connection_epoch": event_epoch,
        "market_route": _clean(request.get("market_route")).upper(),
        "order_type": request.get("order_type"),
        "code": _clean(request.get("code")),
        "identity": identity,
        "broker_evidence": broker_evidence,
        "request_context": {
            "rqname": _clean(request.get("rqname")),
            "screen_no": _clean(request.get("screen_no")),
            "market_route": _clean(request.get("market_route")).upper(),
            "order_type": request.get("order_type"),
            "code": _clean(request.get("code")),
            "order_id": identity["order_id"],
            "dispatch_claim_id": identity["dispatch_claim_id"],
            "send_order_attempt_id": identity["send_order_attempt_id"],
            "execution_id": identity["execution_id"],
            "signal_id": identity["signal_id"],
        },
    }
    return {
        "recorded": True,
        "record": record,
    }


def summarize_live_sor_reconciliation_evidence(
    evidence_path: Path = DEFAULT_EVIDENCE_PATH,
) -> dict[str, Any]:
    """Summarize observed live-SOR transport evidence without authorizing trading."""

    records = read_send_order_reconciliation_evidence(evidence_path)
    sor_records = [
        record
        for record in records
        if _clean(record.get("market_route")).upper() == "SOR"
    ]
    attempts: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in sor_records:
        identity = record.get("identity")
        if not isinstance(identity, dict):
            continue
        key = (
            _clean(identity.get("order_id")),
            _clean(identity.get("dispatch_claim_id")),
            _clean(identity.get("send_order_attempt_id")),
        )
        if not all(key):
            continue
        group = attempts.setdefault(
            key,
            {
                "order_id": key[0],
                "dispatch_claim_id": key[1],
                "send_order_attempt_id": key[2],
                "sources": set(),
                "broker_order_nos": set(),
                "record_count": 0,
            },
        )
        group["record_count"] += 1
        group["sources"].add(_clean(record.get("source")))
        broker_evidence = record.get("broker_evidence")
        if isinstance(broker_evidence, dict):
            broker_order_no = _clean(
                broker_evidence.get("broker_order_no")
            )
            if broker_order_no:
                group["broker_order_nos"].add(broker_order_no)

    normalized_attempts: list[dict[str, Any]] = []
    conflicted_attempts: list[dict[str, Any]] = []
    observed_attempts: list[dict[str, Any]] = []
    for group in attempts.values():
        sources = sorted(group["sources"])
        broker_order_nos = sorted(group["broker_order_nos"])
        item = {
            "order_id": group["order_id"],
            "dispatch_claim_id": group["dispatch_claim_id"],
            "send_order_attempt_id": group["send_order_attempt_id"],
            "record_count": group["record_count"],
            "sources": sources,
            "broker_order_nos": broker_order_nos,
            "has_order_tr": "kiwoom_order_tr" in sources,
            "has_message": "kiwoom_message" in sources,
            "broker_order_no_resolved": len(broker_order_nos) == 1,
        }
        normalized_attempts.append(item)
        if len(broker_order_nos) > 1:
            conflicted_attempts.append(item)
        elif item["has_order_tr"] and item["broker_order_no_resolved"]:
            observed_attempts.append(item)

    if conflicted_attempts:
        state = "CONFLICTED"
    elif observed_attempts:
        state = "TRANSPORT_EVIDENCE_OBSERVED"
    else:
        state = "NO_EVIDENCE"

    return {
        "state": state,
        "live_execution_authorized": False,
        "total_record_count": len(records),
        "sor_record_count": len(sor_records),
        "distinct_attempt_count": len(normalized_attempts),
        "observed_attempt_count": len(observed_attempts),
        "conflicted_attempt_count": len(conflicted_attempts),
        "attempts": sorted(
            normalized_attempts,
            key=lambda item: (
                item["order_id"],
                item["dispatch_claim_id"],
                item["send_order_attempt_id"],
            ),
        ),
    }


def record_send_order_reconciliation_evidence(
    raw_event: Any,
    *,
    evidence_path: Path = DEFAULT_EVIDENCE_PATH,
) -> dict[str, Any]:
    """Append one correlated broker evidence record. Fail closed, queue untouched."""

    built = build_send_order_reconciliation_evidence(raw_event)
    if built.get("recorded") is not True:
        return {
            **built,
            "evidence_path": str(evidence_path),
        }
    try:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            built["record"],
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        with _WRITE_LOCK:
            with evidence_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)
                handle.flush()
        return {
            "recorded": True,
            "evidence_path": str(evidence_path),
            "record": built["record"],
        }
    except Exception as exc:
        return _blocked(
            "SEND_ORDER_RECONCILIATION_EVIDENCE_WRITE_FAILED",
            evidence_path=str(evidence_path),
            error=str(exc),
        )


def read_send_order_reconciliation_evidence(
    evidence_path: Path = DEFAULT_EVIDENCE_PATH,
    *,
    order_id: str = "",
    dispatch_claim_id: str = "",
    send_order_attempt_id: str = "",
    broker_order_no: str = "",
) -> list[dict[str, Any]]:
    try:
        lines = evidence_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    wanted = {
        "order_id": _clean(order_id),
        "dispatch_claim_id": _clean(dispatch_claim_id),
        "send_order_attempt_id": _clean(send_order_attempt_id),
    }
    wanted_broker_order_no = _clean(broker_order_no)
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except Exception:
            continue
        if not isinstance(value, dict):
            continue
        identity = value.get("identity")
        if not isinstance(identity, dict):
            continue
        if any(
            expected and _clean(identity.get(key)) != expected
            for key, expected in wanted.items()
        ):
            continue
        if wanted_broker_order_no:
            broker_evidence = value.get("broker_evidence")
            if not isinstance(broker_evidence, dict):
                continue
            if (
                _clean(broker_evidence.get("broker_order_no"))
                != wanted_broker_order_no
            ):
                continue
        records.append(value)
    return records
