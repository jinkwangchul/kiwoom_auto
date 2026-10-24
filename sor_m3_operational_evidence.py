"""Read bounded, independently recorded direct M3 decisions; never run consumers.

Usage: python sor_m3_operational_evidence.py --v5 accepted.jsonl
       --production captured_probe_outputs.jsonl --mock captured_mock_events.jsonl
       --cutoff YYYYMMDDHHMMSS
The GUI must load this code in a later authorized session. A source cache
marker proves cache consistency, not independent broker merge attestation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from candle_timeframe_aggregation import SUPPORTED_BAR_MINUTES
from sor_routine_signal_evidence import _SOURCE_PROOF, _valid_cutoff, _valid_stock

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 4 * 1024 * 1024
_MAX_RECORDS = 256


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def build_v5_records(pool: Any, cache_marker: Any, snapshot: Any,
                     rows: Any, entries: Any, *, generation: int,
                     request_generation: int, window_key: int = 0,
                     capture_kind: str = "MARKING") -> list[dict[str, Any]]:
    """Run on the replay worker; return source-bound evaluated direct minute rows."""
    try:
        source = pool["candles"]
        stock = snapshot.stock.code
        timeframe = pool.get("timeframe_key")
        minutes = pool.get("timeframe_minutes")
        if (not _valid_stock(stock) or pool["stock"].code != stock
                or type(minutes) is not int or minutes not in SUPPORTED_BAR_MINUTES
                or timeframe != f"M{minutes}"
                or snapshot.timeframe_key != timeframe
                or snapshot.timeframe_minutes != minutes
                or pool.get("market_source") != "INTEGRATED"
                or pool.get("market_data_identity") != f"{stock}_AL"
                or not isinstance(cache_marker, dict)
                or cache_marker.get("canonical_stock_code") != stock
                or cache_marker.get("market_source") != "INTEGRATED"
                or cache_marker.get("market_data_identity") != f"{stock}_AL"
                or not isinstance(source, list) or not source
                or not isinstance(rows, list) or not rows
                or not isinstance(entries, (list, tuple))
                or not isinstance(cache_marker.get("candle_content_sha256"), str)
                or not _HEX.fullmatch(cache_marker["candle_content_sha256"])
                or _digest(source) != cache_marker["candle_content_sha256"]
                or not isinstance(snapshot.settings_hash, str)
                or not _HEX.fullmatch(snapshot.settings_hash)
                or not snapshot.historical_request_id
                or (pool.get("request_id")
                    and pool["request_id"] != snapshot.historical_request_id)
                or type(generation) is not int
                or type(request_generation) is not int
                or type(window_key) is not int
                or capture_kind not in {"MARKING", "NORMAL_REPLAY"}):
            return []
        direct_m1 = cache_marker.get("direct_m1_receipt") if minutes == 1 else None
        if minutes == 1:
            if (not isinstance(direct_m1, dict)
                    or direct_m1.get("stock_code") != stock
                    or direct_m1.get("timeframe_key") != "M1"
                    or direct_m1.get("source_content_sha256") != cache_marker["candle_content_sha256"]
                    or type(direct_m1.get("connection_epoch")) is not int
                    or direct_m1["connection_epoch"] <= 0
                    or not isinstance(direct_m1.get("session_sha256"), str)
                    or not _HEX.fullmatch(direct_m1["session_sha256"])):
                return []
            source_as_of = datetime.fromisoformat(direct_m1["source_as_of"])
            if (source_as_of.utcoffset() != timedelta(hours=9)
                    or any(datetime.strptime(row["time"], "%Y%m%d%H%M%S").replace(
                        tzinfo=source_as_of.tzinfo) + timedelta(minutes=1) > source_as_of
                        for row in source)):
                return []
        pool_positions = {row["time"]: i for i, row in enumerate(source)}
        first_position = pool_positions.get(rows[0]["time"], -1)
        if (len(pool_positions) != len(source) or first_position < 0
                or first_position + len(rows) > len(source)
                or any(source[first_position + i] != row
                       for i, row in enumerate(rows))):
            return []
        start = snapshot.evaluated_start_index
        end = snapshot.evaluated_end_index
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end < len(rows)):
            return []
        decisions: dict[int, set[str]] = {i: set() for i in range(start, end + 1)}
        signal_times: dict[int, str | None] = {}
        for entry in entries:
            if entry.signal not in (None, "BUY", "SELL"):
                return []
            if entry.signal is not None and start <= entry.evaluation_index <= end:
                if entry.evaluation_time != rows[entry.evaluation_index]["time"]:
                    return []
                decisions[entry.evaluation_index].add(entry.signal)
                signal_times[entry.evaluation_index] = entry.signal_time
        if any(len(sides) > 1 for sides in decisions.values()):
            return []
        normalized = []
        for row in rows:
            if (set(row) != {"time", "open", "high", "low", "close", "volume"}
                    or not _valid_cutoff(row["time"])
                    or any(type(row[key]) not in (int, float) for key in
                           ("open", "high", "low", "close", "volume"))):
                return []
            normalized.append(row)
        # Canonical JSON array prefixes can be hashed in one pass on the worker.
        # Re-serializing every historical prefix adds hundreds of milliseconds.
        prefix_state = hashlib.sha256()
        prefix_state.update(b"[")
        prefix_hashes = {}
        receipt_hashes = {}
        first_record = max(start, end - 23)
        for i, row in enumerate(normalized[:end + 1]):
            if i:
                prefix_state.update(b",")
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode("utf-8")
            prefix_state.update(encoded)
            if i >= first_record:
                closed = prefix_state.copy()
                closed.update(b"]")
                prefix_hashes[i] = closed.hexdigest()
                receipt_hashes[i] = hashlib.sha256(encoded).hexdigest()
        return [{"origin": "V5", "capture_kind": capture_kind,
                 "source_verified": True,
                 "verification_kind": ("BROKER_SESSION_BOUND_DIRECT_M1_CONTENT_MATCH"
                                       if minutes == 1 else "CACHE_SIDECAR_CONTENT_MATCH_ONLY"),
                 "stock": stock, "timeframe": timeframe, "rules_hash": snapshot.settings_hash,
                 "evaluation_time": rows[i]["time"],
                 "activation_time": rows[i]["time"],
                 "signal_time": signal_times.get(i),
                 "market_data_identity": f"{stock}_AL", "source_proof": _SOURCE_PROOF,
                 "source_identity": cache_marker["candle_content_sha256"],
                 **({"direct_m1_source_proof": "BROKER_SESSION_BOUND_COMPLETED_M1"}
                    if minutes == 1 else {}),
                 **({"source_session_sha256": direct_m1["session_sha256"],
                     "source_connection_epoch": direct_m1["connection_epoch"]}
                    if minutes == 1 else {}),
                 "input_hash": prefix_hashes[i],
                 "receipt_hash": receipt_hashes[i],
                 "side": next(iter(decisions[i]), "NONE"),
                 "historical_request_id": snapshot.historical_request_id,
                 "generation": generation, "request_generation": request_generation,
                 "window_key": window_key}
                for i in range(first_record, end + 1)]
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return []


def _marker(value: Any, cutoff: str, *, timeframe: str = "M3",
            stock: str | None = None) -> bool:
    return bool(
        timeframe in {f"M{minutes}" for minutes in SUPPORTED_BAR_MINUTES}
        and
        isinstance(value, dict)
        and _valid_stock(value.get("stock"))
        and (stock is None or value["stock"] == stock)
        and value.get("timeframe") == timeframe
        and value.get("market_data_identity") == f"{value['stock']}_AL"
        and value.get("source_proof") == _SOURCE_PROOF
        and (timeframe != "M1" or value.get("direct_m1_source_proof")
             == "BROKER_SESSION_BOUND_COMPLETED_M1")
        and value.get("side") in {"BUY", "SELL", "NONE"}
        and _valid_cutoff(value.get("evaluation_time"))
        and value["evaluation_time"] <= cutoff
        and all(isinstance(value.get(key), str) and _HEX.fullmatch(value[key])
                for key in ("rules_hash", "input_hash", "receipt_hash", "source_identity"))
    )


def _blocked(reason: str, **fields: Any) -> dict[str, Any]:
    return {"status": "BLOCKED", "reason": reason, "comparison": "UNAVAILABLE",
            "live_parity": False, "order_cycle_status": "UNKNOWN", **fields}


def _qualified_time(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() == timedelta(hours=9):
            return stamp
    except (TypeError, ValueError):
        pass
    return None


def _observation_time(value: Any) -> str | None:
    stamp = _qualified_time(value)
    return stamp.strftime("%Y%m%d%H%M%S") if stamp else None


def compare_records(v5: Any, production: Any, mock: Any, *, cutoff: str,
                    timeframe: str = "M3", stock: str = "005930") -> dict[str, Any]:
    """Compare recorded decisions only; matching selections cannot prove live parity."""
    if not _valid_stock(stock):
        return _blocked("INVALID_STOCK")
    if not _valid_cutoff(cutoff):
        return _blocked("INVALID_CUTOFF")
    if timeframe not in {f"M{minutes}" for minutes in SUPPORTED_BAR_MINUTES}:
        return _blocked("DIRECT_PERIOD_SOURCE_SCHEMA_UNPROVEN")
    if not isinstance(v5, list) or not v5:
        return _blocked("V5_ACCEPTED_DIRECT_M1_MISSING" if timeframe == "M1"
                        else "V5_ACCEPTED_M3_MISSING")
    if not isinstance(production, list) or not production:
        return _blocked("PRODUCTION_PREVIEW_DIRECT_M1_MISSING" if timeframe == "M1"
                        else "PRODUCTION_PREVIEW_M3_MISSING")
    captured_mock = isinstance(mock, list)
    if captured_mock:
        mock_events = mock
        mock_session = None
    elif isinstance(mock, dict) and mock.get("schema_version") == "mock_validation_event_v1":
        mock_events = mock.get("events")
        mock_session = mock.get("validation_session_id")
    else:
        mock_events = None
        mock_session = None
    if (not isinstance(mock_events, list) or not mock_events
            or (not captured_mock and (not isinstance(mock_session, str)
                                       or not mock_session.startswith("MV-")))):
        return _blocked("MOCK_SESSION_JOURNAL_MISSING")

    selected: dict[str, dict[str, dict[str, Any]]] = {}
    selected_records: dict[str, dict[str, dict[str, Any]]] = {}
    for origin in ("V5", "PRODUCTION", "MOCK"):
        records = (v5 if origin == "V5" else production if origin == "PRODUCTION"
                   else [event for event in mock_events
                         if isinstance(event, dict)
                         and event.get("event_type") == "ROUTINE_EVALUATED"
                         and event.get("stock_code") == stock])
        if len(records) > _MAX_RECORDS:
            return _blocked(f"{origin}_RECORD_BOUND_EXCEEDED")
        by_time: dict[str, dict[str, Any]] = {}
        by_record: dict[str, dict[str, Any]] = {}
        ids: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                return _blocked(f"{origin}_RECORD_INVALID")
            marker = (record if origin == "V5" else
                      record.get("sor_preview_evidence") if origin == "PRODUCTION" else
                      record.get("payload", {}).get("sor_source_evidence")
                      if isinstance(record.get("payload"), dict) else None)
            if (isinstance(marker, dict) and _valid_stock(marker.get("stock"))
                    and marker.get("market_data_identity") == f"{marker['stock']}_AL"
                    and marker.get("timeframe") in {
                        f"M{minutes}" for minutes in SUPPORTED_BAR_MINUTES}
                    and (origin != "PRODUCTION" or record.get("code") == marker["stock"])
                    and (origin != "MOCK" or record.get("stock_code") == marker["stock"])
                    and (marker["stock"] != stock or marker["timeframe"] != timeframe)):
                continue
            if not _marker(marker, cutoff, timeframe=timeframe, stock=stock):
                return _blocked(f"{origin}_M3_MARKER_UNPROVEN" if timeframe == "M3"
                                else f"{origin}_FRAME_MARKER_UNPROVEN")
            t = marker["evaluation_time"]
            if t in by_time:
                return _blocked(f"{origin}_DUPLICATE_EVALUATION")
            if origin == "V5":
                if (record.get("origin") != "V5"
                        or record.get("source_verified") is not True
                        or record.get("verification_kind")
                        != ("BROKER_SESSION_BOUND_DIRECT_M1_CONTENT_MATCH" if timeframe == "M1"
                            else "CACHE_SIDECAR_CONTENT_MATCH_ONLY")
                        or (timeframe == "M1" and (
                            not isinstance(marker.get("source_session_sha256"), str)
                            or not _HEX.fullmatch(marker["source_session_sha256"])
                            or type(marker.get("source_connection_epoch")) is not int
                            or marker["source_connection_epoch"] <= 0))
                        or type(record.get("generation")) is not int
                        or type(record.get("request_generation")) is not int
                        or type(record.get("window_key")) is not int
                        or not record.get("historical_request_id")
                        or record.get("activation_time") != t
                        or record.get("capture_kind", "MARKING")
                        not in {"MARKING", "NORMAL_REPLAY"}):
                    return _blocked("V5_ACCEPTED_SOURCE_UNPROVEN")
            elif origin == "PRODUCTION":
                if (record.get("code") != stock or record.get("signal") != "SKIP"
                        or not isinstance(marker.get("source_session_sha256"), str)
                        or not _HEX.fullmatch(marker["source_session_sha256"])
                        or type(marker.get("source_connection_epoch")) is not int
                        or marker["source_connection_epoch"] <= 0
                        or record.get("reason") != "SOR_CALCULATION_ONLY_NO_ORDER"
                        or any(record.get(key) is not False for key in
                               ("execution_enabled", "queue_connected", "send_order_connected"))
                        or str(record.get("sor_preview_signal") or "NONE").upper()
                        != marker["side"]
                        or (timeframe == "M1" and (
                            (_observation_time(record.get("sor_preview_activation_time"))
                             if marker["side"] in {"BUY", "SELL"} else
                             record.get("sor_preview_activation_time"))
                            != (t if marker["side"] in {"BUY", "SELL"} else None)))
                        or not (record.get("sor_preview_routine_instance_id")
                                or (record.get("origin") == "PRODUCTION"
                                    and isinstance(record.get(
                                        "sor_preview_routine_instance_sha256"), str)
                                    and _HEX.fullmatch(record[
                                        "sor_preview_routine_instance_sha256"])
                                    and record.get("sor_preview_cycle_status")
                                    in {"resolved", "unresolved", "UNKNOWN"}))
                        or (_observation_time(record.get("sor_preview_observed_at")) or "") < t
                        or not isinstance(record.get("sor_preview_cycle_hash"), str)
                        or not _HEX.fullmatch(record["sor_preview_cycle_hash"])):
                    return _blocked("PRODUCTION_PREVIEW_OR_CYCLE_UNPROVEN")
            else:
                payload = record.get("payload")
                if captured_mock and mock_session is None:
                    mock_session = record.get("validation_session_id")
                event_id = record.get("event_id")
                cycle_id = payload.get("evaluation_cycle_id") if isinstance(payload, dict) else None
                signal_instant = _qualified_time(payload.get("signal_bar_time")) if isinstance(payload, dict) else None
                activation_time = record.get("activation_time")
                observed_instant = _qualified_time(record.get("timestamp"))
                observed_time = observed_instant.strftime("%Y%m%d%H%M%S") if observed_instant else None
                if (not isinstance(mock_session, str)
                        or not mock_session.startswith("MV-")
                        or record.get("validation_session_id") != mock_session
                        or not isinstance(marker.get("source_session_sha256"), str)
                        or not _HEX.fullmatch(marker["source_session_sha256"])
                        or type(marker.get("source_connection_epoch")) is not int
                        or marker["source_connection_epoch"] <= 0
                        or not isinstance(event_id, str) or not event_id.startswith("ME-")
                        or event_id in ids
                        or not isinstance(cycle_id, str) or not cycle_id.startswith("MC-")
                        or cycle_id in ids
                        or payload.get("rules_hash") != marker["rules_hash"]
                        or payload.get("signal") != marker["side"]
                        or (observed_time or "") < t
                        or (marker["side"] in {"BUY", "SELL"} and (
                            not signal_instant
                            or signal_instant.strftime("%Y%m%d%H%M%S")
                               > t
                            or (timeframe == "M1" and
                                signal_instant.strftime("%Y%m%d%H%M%S") != t)
                            or signal_instant > observed_instant
                            or ((captured_mock or "activation_time" in record)
                                and _qualified_time(activation_time) != signal_instant)))
                        or (marker["side"] == "NONE" and (
                            payload.get("signal_bar_time") is not None
                            or ((captured_mock or "activation_time" in record)
                                and activation_time is not None)))
                        or not payload.get("operation_identity")
                        or not isinstance(record.get("routine_instance_id"), str)
                        or not record["routine_instance_id"]
                        or (captured_mock and record.get("origin") != "MOCK")):
                    return _blocked("MOCK_JOURNAL_BINDING_UNPROVEN")
                ids.update((event_id, cycle_id))
            by_time[t] = marker
            by_record[t] = record
        if not by_time or max(by_time) != cutoff:
            return _blocked(f"{origin}_CUTOFF_MISSING")
        selected[origin] = by_time
        selected_records[origin] = by_record

    times = sorted(set().union(*(set(items) for items in selected.values())))
    if any(set(items) != set(times) for items in selected.values()):
        return _blocked("EVALUATION_COVERAGE_MISMATCH",
                        first_missing_time=next(
                            t for t in times if any(t not in items for items in selected.values())))
    for t in times:
        markers = [selected[origin][t] for origin in ("V5", "PRODUCTION", "MOCK")]
        if any(any(item[key] != markers[0][key] for key in
                   ("stock", "timeframe", "market_data_identity", "source_proof",
                    "rules_hash", "receipt_hash", "input_hash"))
               for item in markers[1:]):
            return _blocked("SOURCE_RULES_OR_PREFIX_MISMATCH", first_differing_time=t)
        if any(selected["PRODUCTION"][t][key] != selected["MOCK"][t][key]
               for key in ("source_identity", "source_session_sha256",
                           "source_connection_epoch")):
            return _blocked("PRODUCTION_MOCK_SOURCE_RECEIPT_MISMATCH",
                            first_differing_time=t)
        if timeframe == "M1" and any(
                selected[origin][t][key] != selected["V5"][t][key]
                for origin in ("PRODUCTION", "MOCK")
                for key in ("source_session_sha256", "source_connection_epoch")):
            return _blocked("V5_DIRECT_M1_SESSION_RECEIPT_MISMATCH",
                            first_differing_time=t)
    first = next((t for t in times if len({selected[origin][t]["side"] for origin
                                          in selected}) > 1), None)
    for t in times:
        production_record = selected_records["PRODUCTION"][t]
        mock_record = selected_records["MOCK"][t]
        production_instance = (production_record.get("sor_preview_routine_instance_sha256")
                               or hashlib.sha256(str(production_record.get(
                                   "sor_preview_routine_instance_id", "")).encode("utf-8")).hexdigest())
        if production_instance != hashlib.sha256(mock_record["routine_instance_id"].encode("utf-8")).hexdigest():
            return _blocked("ROUTINE_INSTANCE_MISMATCH", first_differing_time=t)
    if first is not None:
        return {"status": "MISMATCH", "reason": "INDEPENDENT_DECISION_MISMATCH",
                "comparison": "MISMATCH", "live_parity": False, "order_cycle_status": "UNKNOWN",
                "matched_bars": len(times),
                "first_difference": {"time": first, "sides": {
                    origin: selected[origin][first]["side"] for origin in selected}}}
    return {"status": "BLOCKED", "reason": "V5_OPERATIONAL_CYCLE_LINK_MISSING",
            "comparison": "MATCH", "live_parity": False, "order_cycle_status": "UNKNOWN",
            "matched_bars": len(times), "cutoff": cutoff,
            "rules_hash": selected["V5"][cutoff]["rules_hash"]}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("file bound exceeded")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("file bound exceeded")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line, object_pairs_hook=_unique_pairs)
            for line in lines[-_MAX_RECORDS:]]


def _read_production(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("file bound exceeded")
    raw = path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("["):
        value = json.loads(raw, object_pairs_hook=_unique_pairs)
        if not isinstance(value, list) or len(value) > _MAX_RECORDS:
            raise ValueError("production array bound exceeded")
        return value
    return [json.loads(line, object_pairs_hook=_unique_pairs)
            for line in raw.splitlines()[-_MAX_RECORDS:]]


def _read_mock(path: Path) -> Any:
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("file bound exceeded")
    raw = path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        try:
            journal = json.loads(raw, object_pairs_hook=_unique_pairs)
            if isinstance(journal, dict) and journal.get("schema_version") == "mock_validation_event_v1":
                return journal
        except ValueError:
            pass
    return [json.loads(line, object_pairs_hook=_unique_pairs)
            for line in raw.splitlines()[-_MAX_RECORDS:]]


def _latest_v5_batch(records: list[dict[str, Any]], cutoff: str,
                     timeframe: str = "M3", stock: str = "005930") -> list[dict[str, Any]]:
    latest = next((row for row in reversed(records)
                   if isinstance(row, dict) and row.get("evaluation_time") == cutoff
                   and row.get("timeframe") == timeframe
                   and row.get("stock") == stock), None)
    if latest is None:
        return []
    identity = tuple(latest.get(key) for key in
                     ("stock", "timeframe", "window_key", "generation", "request_generation",
                      "historical_request_id", "source_identity", "capture_kind"))
    return [row for row in records if isinstance(row, dict) and
            tuple(row.get(key) for key in
                  ("stock", "timeframe", "window_key", "generation", "request_generation",
                   "historical_request_id", "source_identity", "capture_kind")) == identity
            and row.get("evaluation_time", "") <= cutoff]


def _latest_production_batch(records: list[dict[str, Any]],
                             cutoff: str, times: set[str], stock: str = "005930") -> list[dict[str, Any]]:
    return [row for row in records if isinstance(row, dict)
            and isinstance(row.get("sor_preview_evidence"), dict)
            and (row.get("code") == stock
                 or row["sor_preview_evidence"].get("stock") == stock)
            and row["sor_preview_evidence"].get("evaluation_time") in times]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v5", required=True, type=Path)
    parser.add_argument("--production", required=True, type=Path)
    parser.add_argument("--mock", required=True, type=Path)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--timeframe", default="M3", help="exact chart frame (default: M3)")
    parser.add_argument("--stock", default="005930", help="six-digit stock code (default: 005930)")
    args = parser.parse_args(argv)
    sources = []
    result = None
    for path, reader, reason in (
        (args.v5, _read_jsonl, "V5_ACCEPTED_M3_FILE_UNAVAILABLE"),
        (args.production, _read_production, "PRODUCTION_PREVIEW_M3_FILE_UNAVAILABLE"),
        (args.mock, _read_mock, "MOCK_SESSION_JOURNAL_FILE_UNAVAILABLE"),
    ):
        try:
            sources.append(reader(path))
        except (OSError, UnicodeError, ValueError, TypeError):
            result = _blocked(reason)
            break
    if result is None:
        sources[0] = _latest_v5_batch(sources[0], args.cutoff, args.timeframe, args.stock)
        sources[1] = _latest_production_batch(
            sources[1], args.cutoff,
            {row["evaluation_time"] for row in sources[0]}, args.stock)
        result = compare_records(*sources, cutoff=args.cutoff,
                                 timeframe=args.timeframe, stock=args.stock)
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 1 if result["status"] == "MISMATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
