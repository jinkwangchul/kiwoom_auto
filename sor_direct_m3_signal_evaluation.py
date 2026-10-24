"""Read-only, bounded calculation comparison on verified direct SOR M3 candles.

This is retrospective computation, not live broker or lifecycle attestation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / "runtime/signal_validation_v2_history_cache/005930_AL_3_5900.json"
DEFAULT_RULES = ROOT / "routine_instances/5fb57335-5c6f-4ab6-9534-bdbfd24e1f97/rules.json"
SEOUL = timezone(timedelta(hours=9))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "BLOCKED", "reason": reason,
            "scope": "DIRECT_M3_RETROSPECTIVE_CALCULATION", "broker_attested": False,
            "live_parity": False, **extra}


def _stamp(value: str) -> datetime:
    if not isinstance(value, str) or len(value) != 14 or not value.isascii() or not value.isdigit():
        raise ValueError("invalid YYYYMMDDHHMMSS timestamp")
    return datetime.strptime(value, "%Y%m%d%H%M%S")


def _signal_record(time_value: str, payload: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    side = payload.get("signal")
    if side not in ("BUY", "SELL"):
        return None
    source_index = payload.get("signal_source_index", payload.get("signal_index"))
    if type(source_index) is not int or not 0 <= source_index < len(candles):
        raise ValueError("signal source index is invalid")
    return {"evaluation_time": time_value, "activation_time": time_value,
            "side": side, "signal_time": candles[source_index]["time"],
            "source_index": source_index, "reason": payload.get("reason"),
            "delay_bar": payload.get("delay_bar"),
            "matched_groups": payload.get("matched_groups"), "details": payload.get("details")}


def audit_direct_m3(cache_path: Path, rules_path: Path, *, cutoff: str,
                    as_of: str | None = None, evaluation_bars: int = 12,
                    max_direct_bars: int = 1500) -> dict[str, Any]:
    """Evaluate actual direct M3 prefixes through three separate pure consumers."""
    if (type(evaluation_bars) is not int or not 1 <= evaluation_bars <= 24
            or type(max_direct_bars) is not int or not 1 <= max_direct_bars <= 2000):
        return _blocked("INVALID_BOUNDS")
    try:
        cache_path, rules_path = Path(cache_path), Path(rules_path)
        source = json.loads(cache_path.read_text(encoding="utf-8"))
        marker = json.loads(cache_path.with_name(cache_path.name + ".market-source.json").read_text(encoding="utf-8"))
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        if not isinstance(source, dict) or not isinstance(marker, dict) or not isinstance(rules, dict):
            return _blocked("SOURCE_INVALID_JSON_OBJECT")
        candles = source.get("candles")
        if (source.get("stock", {}).get("code") != "005930"
                or source.get("timeframe_key") != "M3" or source.get("timeframe_minutes") != 3
                or source.get("market_data_identity") != "005930_AL"
                or source.get("market_source") != "INTEGRATED"
                or source.get("signal_entries_by_settings_hash") != {}
                or marker.get("canonical_stock_code") != "005930"
                or marker.get("market_data_identity") != "005930_AL"
                or marker.get("market_source") != "INTEGRATED"
                or rules.get("bar", {}).get("bar_minutes") != 3
                or not isinstance(candles, list) or not candles):
            return _blocked("SOURCE_IDENTITY_OR_TIMEFRAME_MISMATCH")
        content_hash = hashlib.sha256(_canonical(candles)).hexdigest()
        if marker.get("candle_content_sha256") != content_hash:
            return _blocked("SOURCE_CANDLE_SHA_MISMATCH")
        times = [row["time"] for row in candles]
        if (any(set(row) != {"time", "open", "high", "low", "close", "volume"} for row in candles)
                or times != sorted(set(times)) or any(_stamp(t) is None for t in times)):
            return _blocked("SOURCE_INVALID_M3_ROWS")
        from indicator_follow_validation_history_contract import required_validation_history_context_bars
        from routines.지표추종매매.routine_validation_contract import (
            ValidationRequest, ValidationSettingsSnapshot, ValidationStockRef)
        from routines.지표추종매매.routine_validation_historical import ValidationHistoricalSnapshot
        from routines.지표추종매매.routine_validation_replay import (
            ValidationHistoricalReplay, project_validation_candles)
        from routines.지표추종매매.routine_validation_session import ValidationSession
        from routines.지표추종매매.routine import evaluate_signal_selection
        from mock_validation_indicator_follow_adapter import _pure_routine_functions, _pure_signal_selection

        settings = ValidationSettingsSnapshot(rules)
        warmup = required_validation_history_context_bars(rules)
        cutoff_dt = _stamp(cutoff)
        now_dt = _stamp(as_of) if as_of is not None else datetime.now(SEOUL).replace(tzinfo=None)
        if cutoff_dt + timedelta(minutes=3) > now_dt:
            return _blocked("UNCLOSED_CUTOFF")
        if cutoff not in times:
            return _blocked("CUTOFF_NOT_FOUND")
        end = times.index(cutoff) + 1
        direct = candles[max(0, end - min(max_direct_bars, warmup + evaluation_bars)):end]
        if len(direct) < warmup + evaluation_bars:
            return _blocked("INSUFFICIENT_DIRECT_M3_CONTEXT", required=warmup + evaluation_bars,
                            available=len(direct))
        first = len(direct) - evaluation_bars
        stock = ValidationStockRef("005930", str(source["stock"].get("name") or ""))
        rows = [{"체결시간": c["time"], "시가": c["open"], "고가": c["high"],
                 "저가": c["low"], "현재가": c["close"], "거래량": c["volume"]}
                for c in direct]
        historical = ValidationHistoricalSnapshot(
            stock=stock, timeframe_minutes=3, timeframe_key="M3",
            requested_count=len(rows), request_id=f"DIRECT-M3-CACHE:{content_hash}",
            rows=rows, market_data_identity="005930_AL", market_source="INTEGRATED",
            request_as_of=now_dt.replace(tzinfo=SEOUL).isoformat())
        projected, dropped = project_validation_candles(historical)
        if dropped or projected != direct:
            return _blocked("V5_DIRECT_M3_PROJECTION_MISMATCH", dropped_rows=dropped)
        clock = time.perf_counter
        started = clock()
        replay = ValidationHistoricalReplay(ValidationSession(ValidationRequest(stock, settings, 3)))
        try:
            entries = replay.scan_signal_entries(historical, start_index=first)
        except Exception as exc:
            return _blocked("V5_SCAN_ERROR", error=f"{type(exc).__name__}: {exc}",
                            rules_hash=settings.rules_hash, candle_content_sha256=content_hash)
        v5_ms = round((clock() - started) * 1000, 2)
        raw_by_time: dict[str, list[dict[str, Any]]] = {c["time"]: [] for c in direct[first:]}
        seen_raw: set[tuple[str, str, str | None]] = set()
        for entry in entries:
            if entry.signal:
                identity = (entry.evaluation_time, entry.signal, entry.signal_time)
                if identity in seen_raw:
                    continue
                seen_raw.add(identity)
                raw_by_time[entry.evaluation_time].append({
                    "evaluation_time": entry.evaluation_time, "activation_time": entry.evaluation_time,
                    "side": entry.signal, "signal_time": entry.signal_time,
                    "source_index": entry.signal_index, "reason": entry.reason,
                    "delay_bar": entry.delay_bar, "matched_groups": entry.matched_groups,
                    "details": entry.details})
        production: dict[str, list[dict[str, Any]]] = {}
        started = clock()
        for index in range(first, len(direct)):
            prefix = direct[:index + 1]
            selected = evaluate_signal_selection(prefix, rules, {
                "_indicator_follow_validation_current_price": prefix[-1]["close"]})
            event = _signal_record(prefix[-1]["time"], selected, prefix)
            production[prefix[-1]["time"]] = [event] if event else []
        production_ms = round((clock() - started) * 1000, 2)
        mock_evaluator = _pure_routine_functions()[0]
        mock_select = _pure_signal_selection()
        mock: dict[str, list[dict[str, Any]]] = {}
        started = clock()
        for index in range(first, len(direct)):
            prefix = direct[:index + 1]
            selected = mock_select(prefix, rules, {
                "_indicator_follow_validation_current_price": prefix[-1]["close"]},
                evaluator=mock_evaluator)
            event = _signal_record(prefix[-1]["time"], selected, prefix)
            mock[prefix[-1]["time"]] = [event] if event else []
        mock_ms = round((clock() - started) * 1000, 2)
        streams = {"V5_RAW": raw_by_time, "PRODUCTION_SELECTION": production,
                   "MOCK_SELECTION": mock}
        first_mismatch = None
        conflicts = []
        for t in raw_by_time:
            raw = raw_by_time[t]
            if len(raw) > 1:
                conflicts.append(t)
            comparison = {name: [{key: e[key] for key in ("evaluation_time", "activation_time", "side", "signal_time")}
                                 for e in events[t]] for name, events in streams.items()}
            production_keys = comparison["PRODUCTION_SELECTION"]
            mock_keys = comparison["MOCK_SELECTION"]
            if len(raw) > 1:
                different = production_keys != mock_keys or any(key not in comparison["V5_RAW"]
                                                               for key in production_keys)
            else:
                different = comparison["V5_RAW"] != production_keys or production_keys != mock_keys
            if first_mismatch is None and different:
                first_mismatch = {"time": t, "records": {name: events[t] for name, events in streams.items()}}
        counts = {name: {"evaluations": evaluation_bars,
                         "events": sum(len(events) for events in stream.values()),
                         "elapsed_ms": ms,
                         "signal_keys": [{"evaluation_time": event["evaluation_time"],
                                          "activation_time": event["activation_time"],
                                          "side": event["side"], "signal_time": event["signal_time"]}
                                         for events in stream.values() for event in events]}
                  for (name, stream), ms in zip(streams.items(), (v5_ms, production_ms, mock_ms))}
        return {"status": "MISMATCH" if first_mismatch else "BLOCKED",
                "reason": "COMPUTATIONAL_SIGNAL_MISMATCH" if first_mismatch else "HISTORICAL_LIFECYCLE_STATE_UNAVAILABLE",
                "scope": "DIRECT_M3_RETROSPECTIVE_CALCULATION", "broker_attested": False,
                "live_parity": False, "source": {"direct_m3_rows": len(candles),
                    "candle_content_sha256": content_hash, "market_data_identity": "005930_AL"},
                "rules_hash": settings.rules_hash,
                "window": {"cutoff": cutoff, "first_evaluation_time": direct[first]["time"],
                           "evaluated_bars": evaluation_bars, "direct_context_bars": len(direct),
                           "required_preceding_bars": warmup},
                "consumers": counts, "first_mismatch": first_mismatch,
                "v5_dual_side_times": conflicts,
                "lifecycle_state": "UNKNOWN_HOLDINGS_TRADE_CYCLE_PRICE_MOCK_SESSION"}
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return _blocked("SOURCE_OR_EVALUATION_ERROR", error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return _blocked("CONSUMER_EVALUATION_ERROR", error=f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--cutoff", required=True, help="last selected closed M3 YYYYMMDDHHMMSS")
    parser.add_argument("--as-of", help="observation time YYYYMMDDHHMMSS, Seoul")
    parser.add_argument("--evaluation-bars", type=int, default=12)
    parser.add_argument("--max-direct-bars", type=int, default=1500)
    args = parser.parse_args()
    result = audit_direct_m3(args.cache, args.rules, cutoff=args.cutoff, as_of=args.as_of,
                             evaluation_bars=args.evaluation_bars,
                             max_direct_bars=args.max_direct_bars)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["status"] == "MISMATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
