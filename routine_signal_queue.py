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

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable
from uuid import uuid4


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

_QUEUE_THREAD_LOCK = threading.RLock()
_LOCK_POLL_SECONDS = 0.02
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _empty_queue() -> dict[str, Any]:
    return {"version": 1, "updated_at": "", "signals": []}


def _read_queue() -> dict[str, Any]:
    """Keep the existing tolerant contract for read-only callers."""
    data, error = _read_queue_strict(QUEUE_PATH)
    if error:
        return _empty_queue()
    return data


def _read_queue_strict(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return _empty_queue(), None

    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}, "routine signal queue is empty; manual review required"
        data = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, f"failed to read routine signal queue: {exc}; manual review required"

    if not isinstance(data, dict):
        return {}, "routine signal queue root must be an object; manual review required"
    if not isinstance(data.get("signals"), list):
        return {}, "routine signal queue signals must be a list; manual review required"
    if any(not isinstance(item, dict) for item in data["signals"]):
        return {}, "routine signal queue signals must contain only objects; manual review required"

    normalized = deepcopy(data)
    normalized["version"] = normalized.get("version", 1)
    normalized["updated_at"] = normalized.get("updated_at", "")
    return normalized, None


def _sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class _QueueFileLock:
    def __init__(self, queue_path: Path, timeout_sec: float) -> None:
        self.lock_path = queue_path.with_name(f"{queue_path.name}.lock")
        self.timeout_sec = timeout_sec
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_QueueFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+b")
        started = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - started) * 1000)
                return self
            except OSError:
                if time.monotonic() - started >= self.timeout_sec:
                    self.wait_ms = int((time.monotonic() - started) * 1000)
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError("routine signal queue lock timeout")
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


def _write_json_temp(path: Path, data: dict[str, Any]) -> Path:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return tmp_path


def _cleanup_temp(path: Path | None) -> None:
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _metadata(
    *,
    lock_acquired: bool,
    lock_wait_ms: int = 0,
    before_sha256: str = "",
    after_sha256: str = "",
    backup_path: str = "",
    committed: bool = False,
    verified: bool = False,
) -> dict[str, Any]:
    return {
        "file_write": committed,
        "signal_write": committed,
        "signal_committed": committed,
        "post_write_verified": verified,
        "lock_acquired": lock_acquired,
        "lock_wait_ms": lock_wait_ms,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "backup_path": backup_path,
    }


def _failure(operation: str, reason: str, fields: dict[str, Any]) -> dict[str, Any]:
    result = dict(fields)
    if operation == "enqueue":
        result.update({"status": "error", "reason": reason})
    else:
        result.update({"ok": False, "reason": reason})
    result["path"] = str(QUEUE_PATH)
    result["manual_review_required"] = True
    return result


Mutation = Callable[[dict[str, Any]], tuple[bool, dict[str, Any]]]


def _mutate_queue(
    operation: str,
    fields: dict[str, Any],
    mutation: Mutation,
) -> dict[str, Any]:
    try:
        with _QUEUE_THREAD_LOCK:
            with _QueueFileLock(QUEUE_PATH, _DEFAULT_LOCK_TIMEOUT_SECONDS) as lock:
                data, read_error = _read_queue_strict(QUEUE_PATH)
                before_sha256 = _sha256_file(QUEUE_PATH)
                if read_error:
                    result = _failure(operation, read_error, fields)
                    result.update(
                        _metadata(
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                            before_sha256=before_sha256,
                            after_sha256=before_sha256,
                        )
                    )
                    return result

                working = deepcopy(data)
                try:
                    changed, result = mutation(working)
                except Exception as exc:
                    result = _failure(operation, f"routine signal mutation failed: {exc}", fields)
                    result.update(
                        _metadata(
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                            before_sha256=before_sha256,
                            after_sha256=before_sha256,
                        )
                    )
                    return result

                if not changed:
                    result.update(
                        _metadata(
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                            before_sha256=before_sha256,
                            after_sha256=before_sha256,
                        )
                    )
                    return result

                working["updated_at"] = now_text()
                backup_path = ""
                if QUEUE_PATH.exists():
                    backup = QUEUE_PATH.with_name(f"{QUEUE_PATH.name}.bak")
                    try:
                        shutil.copy2(QUEUE_PATH, backup)
                        backup_path = str(backup)
                    except OSError as exc:
                        failed = _failure(operation, f"failed to create queue backup: {exc}", fields)
                        failed.update(
                            _metadata(
                                lock_acquired=True,
                                lock_wait_ms=lock.wait_ms,
                                before_sha256=before_sha256,
                                after_sha256=before_sha256,
                            )
                        )
                        return failed

                tmp_path: Path | None = None
                replaced = False
                try:
                    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path = _write_json_temp(QUEUE_PATH, working)
                    os.replace(tmp_path, QUEUE_PATH)
                    replaced = True
                except Exception as exc:
                    failed = _failure(operation, f"failed to write routine signal queue: {exc}", fields)
                    failed.update(
                        _metadata(
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                            before_sha256=before_sha256,
                            after_sha256=_sha256_file(QUEUE_PATH),
                            backup_path=backup_path,
                            committed=replaced,
                        )
                    )
                    return failed
                finally:
                    _cleanup_temp(tmp_path)

                after_sha256 = _sha256_file(QUEUE_PATH)
                post_data, post_error = _read_queue_strict(QUEUE_PATH)
                if post_error or post_data != working:
                    reason = post_error or "routine signal queue did not match expected data after write"
                    failed = _failure(operation, f"post-write verification failed: {reason}", fields)
                    failed.update(
                        _metadata(
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                            before_sha256=before_sha256,
                            after_sha256=after_sha256,
                            backup_path=backup_path,
                            committed=True,
                            verified=False,
                        )
                    )
                    return failed

                result.update(
                    _metadata(
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                        before_sha256=before_sha256,
                        after_sha256=after_sha256,
                        backup_path=backup_path,
                        committed=True,
                        verified=True,
                    )
                )
                return result
    except TimeoutError as exc:
        result = _failure(operation, str(exc), fields)
        result.update(_metadata(lock_acquired=False))
        return result


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
    """Safely update exactly one routine signal status by id."""
    clean_id = str(signal_id or "").strip()
    clean_status = str(status or "").strip().upper()
    fields = {"signal_id": clean_id, "status": clean_status}

    if not clean_id:
        return _failure("update", "signal_id is required", fields) | _metadata(lock_acquired=False)
    if clean_status not in ALLOWED_SIGNAL_STATUSES:
        result = _failure("update", f"invalid status: {clean_status}", fields)
        result["allowed_statuses"] = sorted(ALLOWED_SIGNAL_STATUSES)
        result.update(_metadata(lock_acquired=False))
        return result

    def mutation(data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        matches = [
            record
            for record in data["signals"]
            if str(record.get("id", "")).strip() == clean_id
        ]
        if len(matches) != 1:
            reason = "signal id not found" if not matches else "signal id matched multiple records"
            return False, _failure("update", reason, fields)

        record = matches[0]
        before_status = str(record.get("status", "") or "")
        updated_at = now_text()
        record["status"] = clean_status
        record["updated_at"] = updated_at
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                if key not in {"id", "created_at"}:
                    record[key] = value

        return True, {
            "ok": True,
            "signal_id": clean_id,
            "before_status": before_status,
            "after_status": clean_status,
            "updated_at": updated_at,
            "path": str(QUEUE_PATH),
        }

    return _mutate_queue("update", fields, mutation)


def enqueue_routine_signal(
    result: dict[str, Any],
    *,
    routine: str,
    code: str,
    name: str,
    tick_key: str = "",
    source: str = "routine_signal_probe",
) -> dict[str, Any]:
    """Store one BUY/SELL signal while preserving the existing return contract."""
    signal = _normalize_signal(result.get("signal"))
    if signal not in ALLOWED_QUEUE_SIGNALS:
        ignored = {
            "status": "ignored",
            "reason": f"큐 저장 대상 신호 아님: {signal}",
            "path": str(QUEUE_PATH),
        }
        ignored.update(_metadata(lock_acquired=False))
        return ignored

    fields: dict[str, Any] = {}

    def mutation(data: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        signals = data["signals"]
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
            "status": STATUS_PENDING,
            "source": source,
            "execution_enabled": False,
        }

        dedupe_key = _make_dedupe_key(record)
        for old in signals:
            if _make_dedupe_key(old) == dedupe_key:
                return False, {
                    "status": "duplicate",
                    "reason": "동일 신호 이미 존재",
                    "path": str(QUEUE_PATH),
                    "id": old.get("id", ""),
                }

        base_id = (
            f"{created_at.replace('-', '').replace(':', '').replace(' ', '_')}"
            f"_{code}_{signal}_{len(signals) + 1}"
        )
        existing_ids = {str(item.get("id", "")) for item in signals}
        record["id"] = base_id if base_id not in existing_ids else f"{base_id}_{uuid4().hex[:8]}"
        signals.append(record)
        return True, {
            "status": "queued",
            "reason": "신호 큐 저장 완료",
            "path": str(QUEUE_PATH),
            "id": record["id"],
        }

    return _mutate_queue("enqueue", fields, mutation)


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
        if str(record.get("status", "")).upper() == STATUS_PENDING:
            summary["pending"] += 1
        signal = _normalize_signal(record.get("signal"))
        if signal == "BUY":
            summary["buy"] += 1
        elif signal == "SELL":
            summary["sell"] += 1
    return summary
