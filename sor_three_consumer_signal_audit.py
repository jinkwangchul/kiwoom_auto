"""Compare caller-supplied V5, PRODUCTION, and MOCK SOR signal records.

Each input is a separate JSON object with consumer, origin_proof, stock,
timeframe, rules_hash, cutoff, market_data_identity, and events. Each event
has a YYYYMMDDHHMMSS time and BUY or SELL side. Provenance is supplied by the
caller; this tool cannot attest a broker, a running GUI, or live parity.

CLI: python sor_three_consumer_signal_audit.py V5.json PRODUCTION.json MOCK.json
Exit: 0 MATCH, 1 MISMATCH, 2 BLOCKED. No orders or GUI access are used.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


CONSUMERS = ("V5", "PRODUCTION", "MOCK")
CONTEXT = ("stock", "timeframe", "rules_hash", "cutoff", "market_data_identity")
FIELDS = frozenset(("consumer", "origin_proof", "events", *CONTEXT))
SCOPE = "READ_ONLY_RECORDED_SIGNAL_COMPARISON"


def _result(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, "scope": SCOPE, "broker_attested": False,
            "live_gui_parity": False, **details}


def _valid_time(value: Any) -> bool:
    if (not isinstance(value, str) or len(value) != 14
            or not value.isascii() or not value.isdigit()):
        return False
    try:
        datetime.strptime(value, "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def _valid_evidence(source: Any, consumer: str) -> bool:
    if not isinstance(source, dict) or set(source) != FIELDS:
        return False
    proof = source["origin_proof"]
    if (source["consumer"] != consumer or not isinstance(proof, str)
            or not proof or proof != proof.strip()
            or source["stock"] != "005930"
            or not isinstance(source["timeframe"], str)
            or not re.fullmatch(r"[A-Z][A-Z0-9]*", source["timeframe"])
            or not isinstance(source["rules_hash"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", source["rules_hash"])
            or not _valid_time(source["cutoff"])
            or source["market_data_identity"] != "005930_AL"
            or not isinstance(source["events"], list)):
        return False
    previous = ""
    for event in source["events"]:
        if (not isinstance(event, dict) or set(event) != {"time", "side"}
                or not _valid_time(event["time"])
                or event["time"] < previous or event["time"] > source["cutoff"]
                or event["side"] not in ("BUY", "SELL")):
            return False
        previous = event["time"]
    return True


def compare_three_consumer_signals(
    v5: Any, production: Any, mock: Any,
) -> dict[str, Any]:
    """Compare ordered signal events only after all three contexts validate."""
    sources = dict(zip(CONSUMERS, (v5, production, mock)))
    for consumer, source in sources.items():
        if source is None:
            return _result("BLOCKED", reason="MISSING_EVIDENCE", consumer=consumer)
        if not _valid_evidence(source, consumer):
            return _result("BLOCKED", reason="INVALID_EVIDENCE", consumer=consumer)

    if len({source["origin_proof"] for source in sources.values()}) != 3:
        return _result("BLOCKED", reason="SAME_EVIDENCE")

    for field in CONTEXT:
        values = {name: source[field] for name, source in sources.items()}
        if len(set(values.values())) != 1:
            return _result("BLOCKED", reason="CONTEXT_MISMATCH", field=field,
                           values=values)

    sequences = {name: source["events"] for name, source in sources.items()}
    for index in range(max(len(events) for events in sequences.values())):
        at_index = {name: dict(events[index]) if index < len(events) else None
                    for name, events in sequences.items()}
        if at_index["V5"] != at_index["PRODUCTION"] or at_index["V5"] != at_index["MOCK"]:
            return _result("MISMATCH", first_discrepancy={
                "index": index, "events": at_index})

    return _result("MATCH", event_count=len(v5["events"]))


def _unique_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in CONSUMERS:
        parser.add_argument(name.lower(), help=f"{name} evidence JSON file")
    args = parser.parse_args(argv)
    paths = [Path(getattr(args, name.lower())) for name in CONSUMERS]
    try:
        if len({path.resolve(strict=True) for path in paths}) != 3:
            result = _result("BLOCKED", reason="SAME_EVIDENCE")
        elif any(paths[i].samefile(paths[j]) for i in range(3) for j in range(i + 1, 3)):
            result = _result("BLOCKED", reason="SAME_EVIDENCE")
        else:
            sources = []
            for path in paths:
                with path.open("r", encoding="utf-8") as stream:
                    sources.append(json.load(stream, object_pairs_hook=_unique_object_keys))
            result = compare_three_consumer_signals(*sources)
    except FileNotFoundError:
        result = _result("BLOCKED", reason="MISSING_EVIDENCE")
    except (OSError, UnicodeError, ValueError):
        result = _result("BLOCKED", reason="INVALID_EVIDENCE")
    print(json.dumps(result, sort_keys=True))
    return {"MATCH": 0, "MISMATCH": 1, "BLOCKED": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
