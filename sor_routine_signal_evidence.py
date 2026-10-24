"""Read-only SOR routine output evidence for the three-consumer audit.

Markers bind an actual routine output to its admitted candle supply. They
repeat the source's provenance claim; they do not attest a broker or GUI.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_SOURCE_PROOF = "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION"
_MARKER_FIELDS = frozenset({
    "stock", "timeframe", "rules_hash", "evaluation_time",
    "market_data_identity", "source_proof", "input_hash", "side",
})


def _valid_stock(stock: Any) -> bool:
    return isinstance(stock, str) and re.fullmatch(r"[0-9]{6}", stock) is not None


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _bar_time(candle: Any) -> datetime | None:
    if not isinstance(candle, dict):
        return None
    try:
        value = datetime.fromisoformat(str(
            candle.get("bar_time") or candle.get("time") or ""
        ))
    except ValueError:
        return None
    return value if value.utcoffset() == timedelta(hours=9) else None


def _valid_cutoff(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{14}", value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def build_source_marker(
    *, stock: str, rules: Any, candles: Any, source: Any, side: Any,
) -> dict[str, str] | None:
    """Bind one actual BUY/SELL/NONE output to the exact admitted AL rows."""
    if (not _valid_stock(stock) or not isinstance(rules, dict) or not rules
            or not isinstance(source, dict) or source.get("available") is not True
            or source.get("stock_code") != stock
            or not isinstance(source.get("candles"), (list, tuple))
            or list(source["candles"]) != list(candles)
            or source.get("market_source") != "INTEGRATED"
            or source.get("market_data_identity") != f"{stock}_AL"
            or source.get("source_proof") != _SOURCE_PROOF
            or source.get("calculation_only") is not True
            or source.get("execution_enabled") is not False
            or source.get("queue_connected") is not False
            or source.get("send_order_connected") is not False
            or not isinstance(candles, (list, tuple)) or not candles):
        return None
    from candle_timeframe_aggregation import SUPPORTED_BAR_MINUTES, read_canonical_bar_minutes

    try:
        bar = rules.get("bar") if isinstance(rules.get("bar"), dict) else {}
        if "bar_minutes" not in bar and "bar_minutes" not in rules:
            return None
        interval = read_canonical_bar_minutes(rules)
        if interval not in SUPPORTED_BAR_MINUTES or source.get("timeframe_minutes") != interval:
            return None
        if interval == 1 and source.get("calculation_source") != "BROKER_AL_CONFIRMED_M1":
            return None
        if interval > 1 and (source.get("direct_history_source")
                              != "BROKER_AL_CONFIRMED_DIRECT"
                              or source.get("calculation_source")
                              != "BROKER_AL_CONFIRMED_DIRECT"
                              or source.get("projection") != "COMPLETED_TIMEFRAME"
                              or source.get("complete") is not True
                              or not isinstance(source.get("source_identity"), str)
                              or not re.fullmatch(r"[0-9a-f]{64}", source["source_identity"])
                              or source.get("forming_included") is True):
            return None
        receipt = source.get("direct_source_receipt")
        if interval > 1 or (interval == 1 and receipt is not None):
            if (not isinstance(receipt, dict)
                    or receipt.get("stock_code") != stock
                    or receipt.get("timeframe_key") != f"M{interval}"
                    or not isinstance(receipt.get("source_content_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", receipt["source_content_sha256"])
                    or (interval > 1 and receipt["source_content_sha256"] != source["source_identity"])
                    or type(receipt.get("connection_epoch")) is not int
                    or receipt["connection_epoch"] <= 0
                    or not isinstance(receipt.get("session_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", receipt["session_sha256"])
                    or not isinstance(receipt.get("source_as_of"), str)
                    or _bar_time({"bar_time": receipt["source_as_of"]}) is None):
                return None
        times = [_bar_time(row) for row in candles]
        if (any(value is None for value in times)
                or any(row.get("timeframe_minutes") != interval for row in candles)
                or times != sorted(set(times))):
            return None
        if (interval > 1 or receipt is not None) and any(
            (interval > 1 and (
                row.get("market_data_identity") != f"{stock}_AL"
                or row.get("market_source") != "INTEGRATED"
                or row.get("display_only") is not True))
            or row.get("is_complete") is not True
            or any(type(row.get(key)) not in (int, float) for key in
                   ("open", "high", "low", "close", "volume"))
            for row in candles
        ):
            return None
        side_text = str(side or "NONE").strip().upper()
        if side_text not in {"BUY", "SELL", "NONE"}:
            return None
        direct_rows = ([{
            "time": time.strftime("%Y%m%d%H%M%S"),
            **{key: (float(row[key]) if interval == 1 else row[key])
               for key in ("open", "high", "low", "close", "volume")},
        } for time, row in zip(times, candles)] if interval > 1 or receipt is not None else None)
        if interval == 1 and receipt is not None:
            as_of = _bar_time({"bar_time": receipt["source_as_of"]})
            if (source.get("projection") != "COMPLETED_TIMEFRAME"
                    or source.get("forming_included") is True
                    or as_of is None or times[-1] + timedelta(minutes=1) > as_of):
                return None
        marker = {
            "stock": stock, "timeframe": f"M{interval}", "rules_hash": _hash(rules),
            "evaluation_time": times[-1].strftime("%Y%m%d%H%M%S"),
            "market_data_identity": f"{stock}_AL", "source_proof": _SOURCE_PROOF,
            "input_hash": _hash(candles if direct_rows is None else direct_rows),
            "side": side_text,
        }
        if direct_rows is not None:
            marker["source_identity"] = (source["source_identity"] if interval > 1
                                         else receipt["source_content_sha256"])
            marker["receipt_hash"] = _hash(direct_rows[-1])
            marker["source_session_sha256"] = receipt["session_sha256"]
            marker["source_connection_epoch"] = receipt["connection_epoch"]
            if interval == 1:
                marker["direct_m1_source_proof"] = "BROKER_SESSION_BOUND_COMPLETED_M1"
        return marker
    except (TypeError, ValueError, OverflowError):
        return None


def _valid_marker(marker: Any) -> bool:
    direct_fields = _MARKER_FIELDS | {
        "source_identity", "receipt_hash", "source_session_sha256",
        "source_connection_epoch", "direct_m1_source_proof",
    }
    return bool(
        isinstance(marker, dict) and set(marker) in (_MARKER_FIELDS, direct_fields)
        and _valid_stock(marker["stock"]) and marker["timeframe"] == "M1"
        and marker["market_data_identity"] == f"{marker['stock']}_AL"
        and marker["source_proof"] == _SOURCE_PROOF
        and marker["side"] in {"BUY", "SELL", "NONE"}
        and _valid_cutoff(marker["evaluation_time"])
        and all(isinstance(marker[key], str)
                and re.fullmatch(r"[0-9a-f]{64}", marker[key])
                for key in ("rules_hash", "input_hash"))
        and (set(marker) == _MARKER_FIELDS or (
            all(isinstance(marker[key], str)
                and re.fullmatch(r"[0-9a-f]{64}", marker[key])
                for key in ("source_identity", "receipt_hash", "source_session_sha256"))
            and type(marker["source_connection_epoch"]) is int
            and marker["source_connection_epoch"] > 0
            and marker["direct_m1_source_proof"] == "BROKER_SESSION_BOUND_COMPLETED_M1"))
    )


def _extract(marked: list[tuple[Any, Any]], *, consumer: str,
             cutoff: str, proof_data: Any) -> dict[str, Any] | None:
    if not marked or not _valid_cutoff(cutoff):
        return None
    first = marked[0][0]
    previous = ""
    events = []
    for marker, side in marked:
        if (not _valid_marker(marker) or side != marker["side"]
                or marker["side"] not in {"BUY", "SELL", "NONE"}
                or marker["evaluation_time"] <= previous
                or marker["evaluation_time"] > cutoff
                or any(marker[key] != first[key] for key in (
                    "stock", "timeframe", "rules_hash", "market_data_identity",
                    "source_proof"))):
            return None
        previous = marker["evaluation_time"]
        if side in {"BUY", "SELL"}:
            events.append({"time": previous, "side": side})
    if previous != cutoff or not events:
        return None
    return {
        "consumer": consumer,
        "origin_proof": f"{consumer}_{'ROUTINE_EVALUATED' if consumer == 'MOCK' else 'ROUTINE_OUTPUT'}:{_hash(proof_data)}",
        "stock": first["stock"], "timeframe": "M1",
        "rules_hash": first["rules_hash"], "cutoff": cutoff,
        "market_data_identity": first["market_data_identity"], "source_proof": _SOURCE_PROOF,
        "events": events,
    }


def extract_production_evidence(outputs: Any, *, cutoff: str,
                                stock: str = "005930") -> dict[str, Any] | None:
    """Adapt captured calculation-only outputs from routine_signal_probe."""
    if not _valid_stock(stock) or not isinstance(outputs, list) or not outputs:
        return None
    marked = []
    for output in outputs:
        if (not isinstance(output, dict) or output.get("code") != stock
                or output.get("signal") != "SKIP"
                or output.get("reason") != "SOR_CALCULATION_ONLY_NO_ORDER"
                or output.get("execution_enabled") is not False
                or output.get("queue_connected") is not False
                or output.get("send_order_connected") is not False):
            return None
        side = str(output.get("sor_preview_signal") or "NONE").strip().upper()
        marked.append((output.get("sor_preview_evidence"), side))
    return _extract(marked, consumer="PRODUCTION", cutoff=cutoff,
                    proof_data=outputs)


def extract_mock_evidence(journal: Any, *, cutoff: str,
                          stock: str = "005930") -> dict[str, Any] | None:
    """Adapt an existing Mock Validation ROUTINE_EVALUATED journal."""
    if (not _valid_stock(stock) or not isinstance(journal, dict)
            or journal.get("schema_version") != "mock_validation_event_v1"
            or not isinstance(journal.get("validation_session_id"), str)
            or not journal["validation_session_id"].startswith("MV-")
            or not isinstance(journal.get("events"), list)):
        return None
    selected = [event for event in journal["events"]
                if isinstance(event, dict)
                and event.get("event_type") == "ROUTINE_EVALUATED"]
    marked = []
    event_ids = set()
    cycle_ids = set()
    for event in selected:
        payload = event.get("payload")
        if (not isinstance(payload, dict) or event.get("stock_code") != stock
                or event.get("validation_session_id") != journal["validation_session_id"]
                or not isinstance(event.get("event_id"), str)
                or not event["event_id"].startswith("ME-")
                or event["event_id"] in event_ids
                or not isinstance(payload.get("evaluation_cycle_id"), str)
                or not payload["evaluation_cycle_id"].startswith("MC-")
                or payload["evaluation_cycle_id"] in cycle_ids):
            return None
        marker = payload.get("sor_source_evidence")
        if (not _valid_marker(marker) or payload.get("rules_hash") != marker["rules_hash"]
                or payload.get("signal") != marker["side"]):
            return None
        timestamp = _bar_time({"bar_time": event.get("timestamp")})
        if timestamp is None or timestamp.strftime("%Y%m%d%H%M%S") < marker["evaluation_time"]:
            return None
        event_ids.add(event["event_id"])
        cycle_ids.add(payload["evaluation_cycle_id"])
        marked.append((marker, marker["side"]))
    return _extract(marked, consumer="MOCK", cutoff=cutoff,
                    proof_data=[journal["validation_session_id"], selected])


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def main(argv: list[str] | None = None) -> int:
    """Print evidence for redirection to the three-consumer audit CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("consumer", choices=("PRODUCTION", "MOCK"))
    parser.add_argument("source", type=Path)
    parser.add_argument("cutoff", help="last evaluated M1 bar, YYYYMMDDHHMMSS")
    parser.add_argument("--stock", default="005930", help="six-digit stock code (default: 005930)")
    args = parser.parse_args(argv)
    try:
        with args.source.open("r", encoding="utf-8") as stream:
            source = json.load(stream, object_pairs_hook=_unique_keys)
        evidence = (
            extract_production_evidence(source, cutoff=args.cutoff, stock=args.stock)
            if args.consumer == "PRODUCTION"
            else extract_mock_evidence(source, cutoff=args.cutoff, stock=args.stock)
        )
    except (OSError, UnicodeError, ValueError, TypeError):
        evidence = None
    if evidence is None:
        print(json.dumps({"status": "BLOCKED", "reason": "SOURCE_EVIDENCE_UNPROVEN"}))
        return 2
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
