# -*- coding: utf-8 -*-
"""Fail-closed authority for Production live-SOR reconciliation validation.

This module never sends an order. It can inspect already-recorded broker
evidence, issue a session-bound validation certificate after an explicitly
confirmed controlled validation, and verify that certificate for the live SOR
execution gate.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from runtime_atomic_writer import STATUS_OK, write_json_atomic
from send_order_reconciliation_evidence import (
    DEFAULT_EVIDENCE_PATH,
    read_send_order_reconciliation_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "1.0"
AUTHORIZATION_CONTRACT = "LIVE_SOR_RECONCILIATION_AUTHORIZATION_V1"
DEFAULT_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
DEFAULT_CERTIFICATE_PATH = (
    PROJECT_ROOT
    / "runtime"
    / "diagnostics"
    / "kiwoom_send_order_reconciliation"
    / "live_sor_validation_certificate.json"
)
_ALLOWED_QUEUE_STATUSES = {
    "FILLED",
}
_SOR_ORDER_TYPES = {11, 12, 13, 15}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _account_fingerprint(account_no: Any) -> str:
    account = _clean(account_no)
    return _stable_hash({"account_no": account}) if account else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except OSError:
        return False


def _identity(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    return {
        "order_id": _clean(source.get("order_id")),
        "dispatch_claim_id": _clean(source.get("dispatch_claim_id")),
        "send_order_attempt_id": _clean(source.get("send_order_attempt_id")),
    }


def _queue_order(queue_path: Path, identity: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    data = _read_json(queue_path)
    orders = data.get("orders")
    if not isinstance(orders, list):
        return {}, ["queue orders are unavailable"]
    matches = []
    for item in orders:
        if not isinstance(item, dict):
            continue
        if all(
            _clean(item.get(field)) == expected
            for field, expected in identity.items()
        ):
            matches.append(item)
    if len(matches) != 1:
        return {}, [f"queue identity match count is {len(matches)}"]
    return deepcopy(matches[0]), []


def inspect_live_sor_validation_candidate(
    queue_path: str | Path,
    identity: Any,
    *,
    evidence_path: str | Path = DEFAULT_EVIDENCE_PATH,
    expected_login_session_id: str = "",
    expected_connection_epoch: int = 0,
    expected_account_no: str = "",
    expected_account_fingerprint: str = "",
) -> dict[str, Any]:
    """Inspect one completed SOR attempt without writing or authorizing it."""

    ids = _identity(identity)
    reasons: list[str] = []
    if not all(ids.values()):
        reasons.append("full order/claim/attempt identity is required")

    queue_record, queue_reasons = _queue_order(Path(queue_path), ids)
    reasons.extend(queue_reasons)

    queue_account_no = _clean(queue_record.get("account_no")) if queue_record else ""
    expected_account = _clean(expected_account_no)
    expected_fingerprint = (
        _clean(expected_account_fingerprint)
        or _account_fingerprint(expected_account)
    )
    account_fingerprint = _account_fingerprint(queue_account_no)
    if not queue_account_no:
        reasons.append("queue account_no is required")
    elif expected_account and queue_account_no != expected_account:
        reasons.append("queue account does not match current account")
    elif expected_fingerprint and account_fingerprint != expected_fingerprint:
        reasons.append("queue account fingerprint does not match current account")

    if queue_record:
        if _clean(queue_record.get("market_route")).upper() != "SOR":
            reasons.append("queue market_route is not SOR")
        status = _clean(queue_record.get("status")).upper()
        if status not in _ALLOWED_QUEUE_STATUSES:
            reasons.append(f"queue status is not validation-complete: {status or 'MISSING'}")
        if queue_record.get("actual_order_sent") is not True:
            reasons.append("queue does not confirm actual_order_sent")
        if queue_record.get("manual_reconciliation_required") is True:
            reasons.append("manual reconciliation is still required")
        if queue_record.get("chejan_reconciliation_required") is True:
            reasons.append("Chejan reconciliation is still required")

    broker_order_no = _clean(queue_record.get("broker_order_no")) if queue_record else ""
    if not broker_order_no:
        reasons.append("queue broker_order_no is required")

    transport_records = (
        read_send_order_reconciliation_evidence(
            Path(evidence_path),
            order_id=ids["order_id"],
            dispatch_claim_id=ids["dispatch_claim_id"],
            send_order_attempt_id=ids["send_order_attempt_id"],
        )
        if all(ids.values())
        else []
    )
    if not transport_records:
        reasons.append("correlated SendOrder transport evidence is missing")

    sources = {
        _clean(item.get("source"))
        for item in transport_records
        if isinstance(item, dict)
    }
    if "kiwoom_order_tr" not in sources:
        reasons.append("kiwoom_order_tr evidence is missing")
    if "kiwoom_message" not in sources:
        reasons.append("kiwoom_message evidence is missing")

    transport_routes = {
        _clean(item.get("market_route")).upper()
        for item in transport_records
        if isinstance(item, dict) and _clean(item.get("market_route"))
    }
    if transport_routes != {"SOR"}:
        reasons.append(
            "correlated transport market_route is not exclusively SOR"
        )
    transport_order_types = {
        _int(item.get("order_type"))
        for item in transport_records
        if isinstance(item, dict) and _int(item.get("order_type")) > 0
    }
    if len(transport_order_types) != 1 or not transport_order_types.issubset(
        _SOR_ORDER_TYPES
    ):
        reasons.append(
            "correlated transport order_type is not a single supported SOR type"
        )
    queue_code = _clean(queue_record.get("code")) if queue_record else ""
    transport_codes = {
        _clean(item.get("code"))
        for item in transport_records
        if isinstance(item, dict) and _clean(item.get("code"))
    }
    if not queue_code:
        reasons.append("queue stock code is required")
    elif transport_codes != {queue_code}:
        reasons.append("queue and transport stock code do not match")

    transport_order_nos = sorted(
        {
            _clean(
                (item.get("broker_evidence") or {}).get("broker_order_no")
            )
            for item in transport_records
            if isinstance(item, dict)
            and isinstance(item.get("broker_evidence"), dict)
            and _clean((item.get("broker_evidence") or {}).get("broker_order_no"))
        }
    )
    if len(transport_order_nos) != 1:
        reasons.append(
            f"transport broker_order_no count is {len(transport_order_nos)}"
        )
    elif broker_order_no and transport_order_nos[0] != broker_order_no:
        reasons.append("queue and transport broker_order_no do not match")

    sessions = {
        _clean(item.get("login_session_id"))
        for item in transport_records
        if isinstance(item, dict) and _clean(item.get("login_session_id"))
    }
    epochs = {
        _int(item.get("connection_epoch"))
        for item in transport_records
        if isinstance(item, dict) and _int(item.get("connection_epoch")) > 0
    }
    if len(sessions) != 1:
        reasons.append(f"transport login session count is {len(sessions)}")
    if len(epochs) != 1:
        reasons.append(f"transport connection epoch count is {len(epochs)}")

    session_id = next(iter(sessions), "")
    connection_epoch = next(iter(epochs), 0)
    expected_session = _clean(expected_login_session_id)
    expected_epoch = _int(expected_connection_epoch)
    if expected_session and session_id != expected_session:
        reasons.append("transport login session does not match current session")
    if expected_epoch > 0 and connection_epoch != expected_epoch:
        reasons.append("transport connection epoch does not match current session")

    chejan_events = (
        queue_record.get("chejan_events")
        if isinstance(queue_record.get("chejan_events"), list)
        else []
    )
    matching_chejan = []
    for item in chejan_events:
        if not isinstance(item, dict):
            continue
        normalized = item.get("normalized_event")
        if not isinstance(normalized, dict):
            continue
        if _clean(item.get("broker_order_no")) != broker_order_no:
            continue
        if _clean(item.get("event_type")).upper() != "FULL_FILL":
            continue
        if _clean(normalized.get("event_type")).upper() != "FULL_FILL":
            continue
        if normalized.get("is_sor") is not True:
            continue
        if _clean(normalized.get("account_no")) != queue_account_no:
            continue
        if _clean(normalized.get("code")) != queue_code:
            continue
        if _int(normalized.get("filled_quantity")) <= 0:
            continue
        if normalized.get("remaining_quantity") is None:
            continue
        if _int(normalized.get("remaining_quantity")) != 0:
            continue
        matching_chejan.append(item)
    if not matching_chejan:
        reasons.append("matching Chejan is_sor=true evidence is missing")

    evidence_payload = {
        "identity": ids,
        "broker_order_no": broker_order_no,
        "login_session_id": session_id,
        "connection_epoch": connection_epoch,
        "account_fingerprint": account_fingerprint,
        "queue_status": _clean(queue_record.get("status")).upper(),
        "transport_records": transport_records,
        "matching_chejan_events": matching_chejan,
    }
    ready = not reasons
    return {
        "ready": ready,
        "state": "VALIDATION_EVIDENCE_READY" if ready else "BLOCKED",
        "authorization_contract": AUTHORIZATION_CONTRACT,
        "identity": ids,
        "broker_order_no": broker_order_no,
        "login_session_id": session_id,
        "connection_epoch": connection_epoch,
        "account_fingerprint": account_fingerprint,
        "transport_record_count": len(transport_records),
        "chejan_event_count": len(matching_chejan),
        "evidence_digest": _stable_hash(evidence_payload) if ready else "",
        "blocked_reasons": reasons,
        "write_performed": False,
        "send_order_called": False,
        "broker_api_called": False,
    }


def issue_live_sor_validation_certificate(
    queue_path: str | Path,
    identity: Any,
    *,
    evidence_path: str | Path = DEFAULT_EVIDENCE_PATH,
    certificate_path: str | Path = DEFAULT_CERTIFICATE_PATH,
    expected_login_session_id: str,
    expected_connection_epoch: int,
    expected_account_no: str,
    expected_server_type: str,
    controlled_validation_confirmed: bool,
    approved_by: str,
) -> dict[str, Any]:
    """Persist one session-bound certificate after explicit controlled validation."""

    if controlled_validation_confirmed is not True:
        return {
            "issued": False,
            "blocked_reasons": ["controlled validation confirmation is required"],
        }
    approver = _clean(approved_by)
    if not approver:
        return {
            "issued": False,
            "blocked_reasons": ["approved_by is required"],
        }
    current_session_id = _clean(expected_login_session_id)
    current_connection_epoch = _int(expected_connection_epoch)
    if not current_session_id:
        return {
            "issued": False,
            "blocked_reasons": ["current login session is required"],
        }
    if current_connection_epoch <= 0:
        return {
            "issued": False,
            "blocked_reasons": ["current connection epoch is required"],
        }
    current_account = _clean(expected_account_no)
    if not current_account:
        return {
            "issued": False,
            "blocked_reasons": ["current account is required"],
        }
    current_server_type = _clean(expected_server_type).upper()
    if current_server_type != "REAL":
        return {
            "issued": False,
            "blocked_reasons": [
                "Production live SOR validation requires REAL server"
            ],
        }

    target = Path(certificate_path)
    if _same_path(target, DEFAULT_CERTIFICATE_PATH):
        canonical_inputs = (
            _same_path(queue_path, DEFAULT_QUEUE_PATH)
            and _same_path(evidence_path, DEFAULT_EVIDENCE_PATH)
        )
        if not canonical_inputs:
            return {
                "issued": False,
                "blocked_reasons": [
                    "Production SOR certificate requires canonical queue and evidence paths"
                ],
            }

    candidate = inspect_live_sor_validation_candidate(
        queue_path,
        identity,
        evidence_path=evidence_path,
        expected_login_session_id=expected_login_session_id,
        expected_connection_epoch=expected_connection_epoch,
        expected_account_no=current_account,
    )
    if candidate.get("ready") is not True:
        return {
            "issued": False,
            "candidate": candidate,
            "blocked_reasons": list(candidate.get("blocked_reasons") or []),
        }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "authorization_contract": AUTHORIZATION_CONTRACT,
        "status": "AUTHORIZED",
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "approved_by": approver,
        "login_session_id": candidate["login_session_id"],
        "connection_epoch": candidate["connection_epoch"],
        "account_fingerprint": candidate["account_fingerprint"],
        "server_type": current_server_type,
        "identity": deepcopy(candidate["identity"]),
        "broker_order_no": candidate["broker_order_no"],
        "evidence_digest": candidate["evidence_digest"],
        "transport_record_count": candidate["transport_record_count"],
        "chejan_event_count": candidate["chejan_event_count"],
    }
    payload["certificate_hash"] = _stable_hash(payload)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "issued": False,
            "blocked_reasons": [f"certificate directory unavailable: {exc}"],
        }
    write_result = write_json_atomic(target, payload)
    issued = (
        write_result.get("status") == STATUS_OK
        and write_result.get("written") is True
    )
    return {
        "issued": issued,
        "certificate_path": str(target),
        "certificate": payload if issued else {},
        "write_result": write_result,
        "candidate": candidate,
        "blocked_reasons": [] if issued else [str(write_result.get("error") or "certificate write failed")],
    }


def inspect_live_sor_validation_certificate(
    certificate_path: str | Path = DEFAULT_CERTIFICATE_PATH,
    *,
    queue_path: str | Path = DEFAULT_QUEUE_PATH,
    evidence_path: str | Path = DEFAULT_EVIDENCE_PATH,
    expected_login_session_id: str = "",
    expected_connection_epoch: int = 0,
    expected_account_no: str = "",
    expected_account_fingerprint: str = "",
    expected_server_type: str = "",
) -> dict[str, Any]:
    """Verify one certificate against current session and its source evidence."""

    path = Path(certificate_path)
    value = _read_json(path)
    reasons: list[str] = []
    if not value:
        reasons.append("live SOR validation certificate is missing or unreadable")
    if value and value.get("schema_version") != SCHEMA_VERSION:
        reasons.append("live SOR validation certificate schema mismatch")
    if value and value.get("authorization_contract") != AUTHORIZATION_CONTRACT:
        reasons.append("live SOR authorization contract mismatch")
    if value and value.get("status") != "AUTHORIZED":
        reasons.append("live SOR validation certificate is not authorized")

    if value:
        recorded_hash = _clean(value.get("certificate_hash"))
        hash_source = deepcopy(value)
        hash_source.pop("certificate_hash", None)
        if not recorded_hash or recorded_hash != _stable_hash(hash_source):
            reasons.append("live SOR validation certificate hash mismatch")

    session_id = _clean(value.get("login_session_id"))
    epoch = _int(value.get("connection_epoch"))
    expected_session = _clean(expected_login_session_id)
    expected_epoch = _int(expected_connection_epoch)
    expected_account = _clean(expected_account_no)
    expected_fingerprint = (
        _clean(expected_account_fingerprint)
        or _account_fingerprint(expected_account)
    )
    recorded_account_fingerprint = _clean(value.get("account_fingerprint"))
    recorded_server_type = _clean(value.get("server_type")).upper()
    current_server_type = _clean(expected_server_type).upper()
    if expected_session and session_id != expected_session:
        reasons.append("live SOR certificate login session mismatch")
    if expected_epoch > 0 and epoch != expected_epoch:
        reasons.append("live SOR certificate connection epoch mismatch")
    if not expected_fingerprint:
        reasons.append(
            "current account fingerprint is required to validate live SOR certificate"
        )
    elif recorded_account_fingerprint != expected_fingerprint:
        reasons.append("live SOR certificate account mismatch")
    if recorded_server_type != "REAL":
        reasons.append("live SOR certificate server type is not REAL")
    if current_server_type != "REAL":
        reasons.append("current server type is not REAL")
    elif recorded_server_type != current_server_type:
        reasons.append("live SOR certificate server type mismatch")
    if not session_id:
        reasons.append("live SOR certificate login session is missing")
    if epoch <= 0:
        reasons.append("live SOR certificate connection epoch is invalid")
    if not _clean(value.get("evidence_digest")):
        reasons.append("live SOR certificate evidence digest is missing")

    source_candidate: dict[str, Any] = {}
    if value:
        source_candidate = inspect_live_sor_validation_candidate(
            queue_path,
            value.get("identity"),
            evidence_path=evidence_path,
            expected_login_session_id=expected_session or session_id,
            expected_connection_epoch=expected_epoch or epoch,
            expected_account_no=expected_account,
            expected_account_fingerprint=expected_fingerprint,
        )
        if source_candidate.get("ready") is not True:
            reasons.append("live SOR source reconciliation evidence is no longer valid")
            reasons.extend(
                str(reason)
                for reason in source_candidate.get("blocked_reasons", [])
                if str(reason).strip()
            )
        else:
            if (
                _clean(source_candidate.get("evidence_digest"))
                != _clean(value.get("evidence_digest"))
            ):
                reasons.append("live SOR source evidence digest mismatch")
            if (
                _clean(source_candidate.get("broker_order_no"))
                != _clean(value.get("broker_order_no"))
            ):
                reasons.append("live SOR source broker_order_no mismatch")

    valid = not reasons
    return {
        "valid": valid,
        "state": "AUTHORIZED" if valid else "UNVERIFIED",
        "authorization_contract": AUTHORIZATION_CONTRACT,
        "certificate_path": str(path),
        "certificate_hash": _clean(value.get("certificate_hash")) if valid else "",
        "evidence_digest": _clean(value.get("evidence_digest")) if valid else "",
        "login_session_id": session_id,
        "connection_epoch": epoch,
        "account_fingerprint": recorded_account_fingerprint,
        "server_type": recorded_server_type,
        "blocked_reasons": reasons,
        "source_candidate": source_candidate,
        "certificate": value if valid else {},
    }
