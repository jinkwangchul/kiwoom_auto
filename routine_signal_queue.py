# -*- coding: utf-8 -*-
"""routine_signal_queue.py

STEP 6-B: 루틴 신호 큐 저장 모듈.

역할:
- 루틴 evaluate()에서 나온 BUY / SELL 신호를 runtime/routine_signals.json에 저장한다.
- HOLD / SKIP / ERROR는 기본적으로 큐에 저장하지 않는다.
- 주문 실행, 예산 처리, 청산 처리 없음.

파일:
- runtime/routine_signals.json

저장 구조:
{
  "version": 1,
  "updated_at": "...",
  "signals": [
    {
      "id": "...",
      "created_at": "...",
      "routine": "<routine_name>",
      "code": "003550",
      "name": "LG",
      "signal": "BUY",
      "reason": "매수조건 충족",
      "status": "PENDING",
      "source": "routine_signal_probe"
    }
  ]
}

중복 방지:
- 같은 routine/code/signal/signal_index/tick_key 조합은 중복 저장하지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
QUEUE_PATH = RUNTIME_DIR / "routine_signals.json"

ALLOWED_QUEUE_SIGNALS = {"BUY", "SELL"}
STATUS_PENDING = "PENDING"
STATUS_PREVIEWED = "PREVIEWED"
STATUS_BLOCKED = "BLOCKED"
STATUS_READY = "READY"
STATUS_ORDER_QUEUED = "ORDER_QUEUED"
STATUS_DONE = "DONE"
STATUS_CANCELLED = "CANCELLED"
STATUS_EXPIRED = "EXPIRED"
STATUS_ERROR = "ERROR"

ALLOWED_SIGNAL_STATUSES = {
    STATUS_PENDING,
    STATUS_PREVIEWED,
    STATUS_BLOCKED,
    STATUS_READY,
    STATUS_ORDER_QUEUED,
    STATUS_DONE,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_ERROR,
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_queue() -> dict[str, Any]:
    if not QUEUE_PATH.exists():
        return {"version": 1, "updated_at": "", "signals": []}

    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "updated_at": "", "signals": []}

    if not isinstance(data, dict):
        return {"version": 1, "updated_at": "", "signals": []}

    signals = data.get("signals")
    if not isinstance(signals, list):
        data["signals"] = []

    data["version"] = data.get("version", 1)
    data["updated_at"] = data.get("updated_at", "")
    return data


def _write_queue(data: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_text()
    QUEUE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _replace_queue_atomically(data: dict[str, Any]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_text()
    tmp_path = QUEUE_PATH.with_name(f"{QUEUE_PATH.name}.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(QUEUE_PATH)


def _normalize_signal(value: Any) -> str:
    return str(value or "").strip().upper()


def _make_dedupe_key(record: dict[str, Any]) -> str:
    return "|".join(
        [
            str(record.get("routine", "")),
            str(record.get("code", "")),
            str(record.get("signal", "")),
            str(record.get("signal_index", "")),
            str(record.get("tick_key", "")),
        ]
    )


def update_signal_status(
    signal_id: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safely update one routine signal status by id."""
    clean_id = str(signal_id or "").strip()
    clean_status = str(status or "").strip().upper()

    if not clean_id:
        return {
            "ok": False,
            "reason": "signal_id is required",
            "signal_id": clean_id,
            "status": clean_status,
            "path": str(QUEUE_PATH),
        }

    if clean_status not in ALLOWED_SIGNAL_STATUSES:
        return {
            "ok": False,
            "reason": f"invalid status: {clean_status}",
            "signal_id": clean_id,
            "status": clean_status,
            "allowed_statuses": sorted(ALLOWED_SIGNAL_STATUSES),
            "path": str(QUEUE_PATH),
        }

    data = _read_queue()
    signals = data.get("signals", [])
    if not isinstance(signals, list):
        return {
            "ok": False,
            "reason": "queue signals must be a list",
            "signal_id": clean_id,
            "status": clean_status,
            "path": str(QUEUE_PATH),
        }

    for record in signals:
        if not isinstance(record, dict):
            continue
        if str(record.get("id", "")).strip() != clean_id:
            continue

        before_status = str(record.get("status", "") or "")
        updated_at = now_text()
        record["status"] = clean_status
        record["updated_at"] = updated_at
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if key in {"id", "created_at"}:
                    continue
                record[key] = value

        _replace_queue_atomically(data)
        return {
            "ok": True,
            "signal_id": clean_id,
            "before_status": before_status,
            "after_status": clean_status,
            "updated_at": updated_at,
            "path": str(QUEUE_PATH),
        }

    return {
        "ok": False,
        "reason": "signal id not found",
        "signal_id": clean_id,
        "status": clean_status,
        "path": str(QUEUE_PATH),
    }


def enqueue_routine_signal(
    result: dict[str, Any],
    *,
    routine: str,
    code: str,
    name: str,
    tick_key: str = "",
    source: str = "routine_signal_probe",
) -> dict[str, Any]:
    """BUY/SELL 신호를 큐에 저장한다.

    반환:
    - queued: 새로 저장
    - duplicate: 이미 존재
    - ignored: BUY/SELL 아님
    - error: 오류
    """
    signal = _normalize_signal(result.get("signal"))
    if signal not in ALLOWED_QUEUE_SIGNALS:
        return {
            "status": "ignored",
            "reason": f"큐 저장 대상 신호 아님: {signal}",
            "path": str(QUEUE_PATH),
        }

    data = _read_queue()
    signals = data.get("signals", [])
    if not isinstance(signals, list):
        signals = []
        data["signals"] = signals

    created_at = now_text()
    record = {
        "id": "",
        "created_at": created_at,
        "routine": routine,
        "code": code,
        "name": name,
        "signal": signal,
        "reason": str(result.get("reason", "") or ""),
        "matched_groups": result.get("matched_groups", []),
        "details": result.get("details", []),
        "signal_index": result.get("signal_index"),
        "delay_bar": result.get("delay_bar"),
        "tick_key": tick_key,
        "status": "PENDING",
        "source": source,
        "execution_enabled": False,
    }

    dedupe_key = _make_dedupe_key(record)
    for old in signals:
        if isinstance(old, dict) and _make_dedupe_key(old) == dedupe_key:
            return {
                "status": "duplicate",
                "reason": "동일 신호 이미 존재",
                "path": str(QUEUE_PATH),
                "id": old.get("id", ""),
            }

    record["id"] = f"{created_at.replace('-', '').replace(':', '').replace(' ', '_')}_{code}_{signal}_{len(signals)+1}"
    signals.append(record)
    _write_queue(data)

    return {
        "status": "queued",
        "reason": "신호 큐 저장 완료",
        "path": str(QUEUE_PATH),
        "id": record["id"],
    }


def summarize_queue() -> dict[str, Any]:
    data = _read_queue()
    signals = data.get("signals", [])
    if not isinstance(signals, list):
        signals = []

    summary: dict[str, Any] = {
        "path": str(QUEUE_PATH),
        "total": len(signals),
        "pending": 0,
        "buy": 0,
        "sell": 0,
    }

    for record in signals:
        if not isinstance(record, dict):
            continue
        if str(record.get("status", "")).upper() == "PENDING":
            summary["pending"] += 1
        signal = _normalize_signal(record.get("signal"))
        if signal == "BUY":
            summary["buy"] += 1
        elif signal == "SELL":
            summary["sell"] += 1

    return summary
