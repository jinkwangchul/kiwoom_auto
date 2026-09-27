# -*- coding: utf-8 -*-
"""Read-only production market-session metadata for Indicator Follow validation."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from candle_timeframe_aggregation import (
    MAXIMUM_MINUTE_CANDLES,
    aggregate_minute_candles,
    candle_session_windows,
    required_minute_candles,
)
from gui_ats_utils import manual_ats_selected_keys_and_source
import runtime_io
import state_policy
import stock_repository


PRODUCTION_SESSION_READY = "PRODUCTION_SESSION_READY"
PRODUCTION_SESSION_UNAVAILABLE = "PRODUCTION_SESSION_UNAVAILABLE"


def production_calculation_cache_identity(
    stock_code: str,
    market_data_identity: str,
    target_minutes: int,
    production_contract: dict[str, Any],
    minute_candles: list[dict[str, Any]],
    history_target: int,
    source_request_id: str,
) -> tuple[object, ...]:
    """Bind derived signal data to the complete source and session contract."""
    source_json = json.dumps(
        minute_candles, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(",", ":"),
    )
    return (
        str(stock_code),
        str(market_data_identity),
        "M1",
        f"M{target_minutes}",
        json.dumps(
            production_contract, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ),
        len(minute_candles),
        str(minute_candles[0].get("time") or "") if minute_candles else "",
        str(minute_candles[-1].get("time") or "") if minute_candles else "",
        int(history_target),
        str(source_request_id),
        hashlib.sha256(source_json.encode("utf-8")).hexdigest(),
    )


def production_minute_source_plan(
    target_minutes: int,
    history_target_bars: int,
    warmup_bars: int,
) -> dict[str, Any]:
    """Size a read-only M1 request without silently weakening Production's cap."""
    if (
        isinstance(history_target_bars, bool)
        or not isinstance(history_target_bars, int)
        or history_target_bars <= 0
        or isinstance(warmup_bars, bool)
        or not isinstance(warmup_bars, int)
        or warmup_bars < 0
    ):
        raise ValueError("history target and warmup must be valid bar counts")
    requirement = required_minute_candles(
        target_minutes,
        history_target_bars + warmup_bars,
    )
    return {
        "ready": bool(requirement["within_limit"]),
        "status": (
            "PRODUCTION_MINUTE_SOURCE_READY"
            if requirement["within_limit"]
            else "FILTER_SIGNAL_BACKTEST_BLOCKED"
        ),
        "source_timeframe_key": "M1",
        "target_timeframe_key": f"M{target_minutes}",
        "requested_source_count": int(requirement["required_minute_candles"]),
        "maximum_source_count": MAXIMUM_MINUTE_CANDLES,
        "reason": str(requirement["reason"]),
    }


def production_minute_source_retry_plan(
    current_source_count: int,
    projected_target_bars: int,
    required_target_bars: int,
) -> dict[str, Any]:
    """Size a bounded retry from observed production-session projection density."""
    values = (current_source_count, projected_target_bars, required_target_bars)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("source and target counts must be integers")
    if current_source_count <= 0 or projected_target_bars < 0 or required_target_bars <= 0:
        raise ValueError("source and target counts are out of range")

    if projected_target_bars >= required_target_bars:
        return {
            "ready": True,
            "retry": False,
            "status": "PRODUCTION_MINUTE_SOURCE_READY",
            "requested_source_count": current_source_count,
            "maximum_source_count": MAXIMUM_MINUTE_CANDLES,
            "projected_target_bars": projected_target_bars,
            "required_target_bars": required_target_bars,
            "reason": "",
        }

    if current_source_count >= MAXIMUM_MINUTE_CANDLES:
        return {
            "ready": False,
            "retry": False,
            "status": "FILTER_SIGNAL_BACKTEST_BLOCKED",
            "requested_source_count": current_source_count,
            "maximum_source_count": MAXIMUM_MINUTE_CANDLES,
            "projected_target_bars": projected_target_bars,
            "required_target_bars": required_target_bars,
            "reason": "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT",
        }

    if projected_target_bars <= 0:
        candidate = current_source_count * 2
    else:
        candidate = math.ceil(
            current_source_count
            * required_target_bars
            / projected_target_bars
        )
    candidate = min(
        MAXIMUM_MINUTE_CANDLES,
        max(current_source_count + 1, int(candidate)),
    )
    return {
        "ready": False,
        "retry": True,
        "status": "PRODUCTION_MINUTE_SOURCE_RETRY",
        "requested_source_count": candidate,
        "maximum_source_count": MAXIMUM_MINUTE_CANDLES,
        "projected_target_bars": projected_target_bars,
        "required_target_bars": required_target_bars,
        "reason": "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT",
    }


def project_production_calculation_candles(
    minute_candles: list[dict[str, Any]],
    target_minutes: int,
    production_contract: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Use Production's session-anchored aggregator for completed signal bars."""
    if (
        not isinstance(production_contract, dict)
        or production_contract.get("status") != PRODUCTION_SESSION_READY
        or production_contract.get("ready") is not True
    ):
        return {
            "ready": False,
            "candles": [],
            "forming_bar_status": "FORMING_BAR_PARITY_NOT_VERIFIED",
            "reason": "PRODUCTION_SESSION_UNAVAILABLE",
        }
    windows = production_contract.get("session_windows")
    if not isinstance(windows, list) or not windows:
        return {
            "ready": False,
            "candles": [],
            "forming_bar_status": "FORMING_BAR_PARITY_NOT_VERIFIED",
            "reason": "PRODUCTION_SESSION_WINDOWS_UNAVAILABLE",
        }
    projected = aggregate_minute_candles(
        minute_candles,
        target_minutes,
        now=as_of,
        session_windows=windows,
    )
    completed = [
        {
            "time": datetime.fromisoformat(str(candle["bar_time"]))
            .strftime("%Y%m%d%H%M%S"),
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle["volume"],
            "session": candle["session"],
            "is_complete": True,
        }
        for candle in projected
        if candle.get("is_complete") is True
    ]
    return {
        "ready": bool(completed),
        "candles": completed,
        "forming_bar_status": "FORMING_BAR_PARITY_NOT_VERIFIED",
        "reason": "" if completed else "PRODUCTION_COMPLETED_CANDLES_UNAVAILABLE",
    }


def unavailable_production_session_contract(reason: object) -> dict[str, Any]:
    """Return the stable fail-closed shape used when production metadata is unavailable."""
    return {
        "status": PRODUCTION_SESSION_UNAVAILABLE,
        "readiness": "UNAVAILABLE",
        "selected_ats": [],
        "selection_source": "none",
        "session_windows": [],
        "ready": False,
        "production_equivalent": False,
        "reason": str(reason or "PRODUCTION_SESSION_UNAVAILABLE"),
    }


def _clock_minutes(value: object) -> int | None:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 60 + minute


def production_session_contract(
    config: dict[str, object] | None,
    state: dict[str, object] | None,
    operation_policy: dict[str, object] | None,
) -> dict[str, Any]:
    """Project production ATS selection and candle windows without mutating inputs."""
    if not all(isinstance(value, dict) for value in (config, state, operation_policy)):
        return unavailable_production_session_contract(
            "PRODUCTION_SESSION_INPUT_INVALID"
        )
    if str(config.get("operation_mode") or "").strip().upper() not in {
        "SCHEDULED", "CONTINUOUS",
    }:
        return unavailable_production_session_contract(
            "PRODUCTION_OPERATION_MODE_UNAVAILABLE"
        )
    regular_policy = operation_policy.get("regular_market")
    if (
        not isinstance(regular_policy, dict)
        or _clock_minutes(regular_policy.get("start_time")) is None
        or _clock_minutes(regular_policy.get("end_time")) is None
    ):
        return unavailable_production_session_contract(
            "PRODUCTION_REGULAR_SESSION_POLICY_UNAVAILABLE"
        )

    try:
        selected_ats, selection_source = manual_ats_selected_keys_and_source(
            config,
            state,
        )
        extra_policy = operation_policy.get("extra_sessions")
        for selected_key in selected_ats:
            if (
                not str(selected_key).startswith("extra")
                or not str(selected_key)[5:].isdigit()
                or not isinstance(extra_policy, list)
                or int(str(selected_key)[5:]) < 1
                or int(str(selected_key)[5:]) > len(extra_policy)
                or not isinstance(extra_policy[int(str(selected_key)[5:]) - 1], dict)
            ):
                return unavailable_production_session_contract(
                    "PRODUCTION_SELECTED_ATS_POLICY_UNAVAILABLE"
                )
        raw_windows = candle_session_windows(operation_policy, selected_ats)
    except Exception:
        return unavailable_production_session_contract(
            "PRODUCTION_SESSION_PROJECTION_FAILED"
        )

    session_windows: list[dict[str, str]] = []
    for raw_window in raw_windows:
        if not isinstance(raw_window, dict):
            return unavailable_production_session_contract(
                "PRODUCTION_SESSION_WINDOW_INVALID"
            )
        name = str(raw_window.get("name") or "").strip()
        start_time = str(raw_window.get("start_time") or "").strip()
        end_time = str(raw_window.get("end_time") or "").strip()
        start_minutes = _clock_minutes(start_time)
        end_minutes = _clock_minutes(end_time)
        if (
            not name
            or start_minutes is None
            or end_minutes is None
            or start_minutes >= end_minutes
        ):
            return unavailable_production_session_contract(
                "PRODUCTION_SESSION_WINDOW_INVALID"
            )
        session_windows.append({
            "name": name,
            "start_time": start_time,
            "end_time": end_time,
        })

    if not session_windows:
        return unavailable_production_session_contract(
            "PRODUCTION_SESSION_WINDOWS_UNAVAILABLE"
        )

    return {
        "status": PRODUCTION_SESSION_READY,
        "readiness": "READY",
        "selected_ats": [str(value) for value in selected_ats],
        "selection_source": str(selection_source or "none"),
        "session_windows": session_windows,
        "ready": True,
        "production_equivalent": True,
        "reason": "",
    }


def production_session_contract_for_stock(
    stock_code: object,
    stock_name: object = "",
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Read registered Stock metadata only; never create repository paths or files."""
    try:
        stock_repo = stock_repository.StockRepository(
            Path(project_root) if project_root is not None else None
        )
        record = stock_repo.find_by_code(str(stock_code or "").strip())
        if record is None:
            return unavailable_production_session_contract(
                "PRODUCTION_STOCK_UNREGISTERED"
            )
        stock_dir = stock_repo.resolve_stock_dir(
            record.code,
            record.name or str(stock_name or "").strip(),
        )
        config_path = stock_dir / "config.json"
        state_path = stock_dir / "state.json"
        if not config_path.is_file() or not state_path.is_file():
            return unavailable_production_session_contract(
                "PRODUCTION_STOCK_METADATA_UNAVAILABLE"
            )
        config = runtime_io.read_json_dict(config_path)
        state = runtime_io.read_json_dict(state_path)
        if not config or not state:
            return unavailable_production_session_contract(
                "PRODUCTION_STOCK_METADATA_INVALID"
            )
        policy_path = Path(state_policy.OPERATION_POLICY_PATH)
        if not policy_path.is_file():
            return unavailable_production_session_contract(
                "PRODUCTION_OPERATION_POLICY_UNAVAILABLE"
            )
        raw_policy = runtime_io.read_json_dict(policy_path)
        regular_policy = raw_policy.get("regular_market")
        if (
            not isinstance(regular_policy, dict)
            or "start_time" not in regular_policy
            or "end_time" not in regular_policy
        ):
            return unavailable_production_session_contract(
                "PRODUCTION_OPERATION_POLICY_INCOMPLETE"
            )
        operation_policy = state_policy.read_operation_policy()
        return production_session_contract(config, state, operation_policy)
    except Exception:
        return unavailable_production_session_contract(
            "PRODUCTION_SESSION_READ_FAILED"
        )
