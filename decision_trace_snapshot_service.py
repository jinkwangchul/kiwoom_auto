# -*- coding: utf-8 -*-
"""Content-addressed rules/settings snapshots and engine bundle identity."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import threading
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_EVIDENCE_ROOT = PROJECT_ROOT / "runtime" / "diagnostics" / "decision_trace" / "evidence"
SNAPSHOT_SCHEMA_VERSION = "decision_trace_snapshot_v1"

RULE_EXCLUDED_KEYS = frozenset(
    {
        "indicator_follow_ui_state",
        "description",
        "note",
        "warnings",
        "created_at",
        "updated_at",
        "selected",
        "selection_state",
        "preview",
        "preview_only",
        "pending_metadata",
    }
)
SETTINGS_ALLOWED_KEYS = frozenset(
    {
        "enabled",
        "trade_amount_type",
        "buy_qty",
        "buy_amount",
        "buy_limit_enabled",
        "buy_limit_amount",
        "max_buy_rounds",
        "starting_budget_type",
        "starting_quantity",
        "starting_amount",
        "total_budget",
    }
)
DEFAULT_ENGINE_BUNDLE_FILES = (
    "routines/지표추종매매/routine.py",
    "routines/지표추종매매/routine_macd_engine.py",
    "engines/indicator_engine.py",
    "engines/condition_engine.py",
    "engines/signal_result.py",
)

_SNAPSHOT_LOCK = threading.RLock()


def _normalize_number(value: int | float | Decimal) -> int | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers are not supported")
    try:
        decimal = Decimal(str(value)).normalize()
    except InvalidOperation as exc:
        raise ValueError("invalid numeric value") from exc
    if decimal == decimal.to_integral_value():
        return int(decimal)
    return float(decimal)


def normalize_canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float, Decimal)):
        return _normalize_number(value)
    if isinstance(value, dict):
        return {str(key): normalize_canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [normalize_canonical_value(item) for item in value]
    raise ValueError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    normalized = normalize_canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def normalize_rules(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("rules must be an object")

    def visit(item: Any) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                text = str(key)
                lowered = text.lower()
                if lowered in RULE_EXCLUDED_KEYS or lowered.startswith("preview_") or lowered.startswith("pending_"):
                    continue
                result[text] = visit(child)
            return result
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        return normalize_canonical_value(item)

    return normalize_canonical_value(visit(value))


def normalize_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("settings must be an object")
    selected = {key: value[key] for key in SETTINGS_ALLOWED_KEYS if key in value}
    return normalize_canonical_value(selected)


def compute_engine_bundle_identity(
    bundle_files: Iterable[str | Path] = DEFAULT_ENGINE_BUNDLE_FILES,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    root = Path(project_root)
    entries: list[dict[str, str]] = []
    for item in bundle_files:
        path = Path(item)
        if path.is_absolute():
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = path.name
        else:
            relative = path.as_posix()
            path = root / path
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"module": relative, "source_hash": source_hash})
    entries.sort(key=lambda entry: entry["module"])
    return {"engine_bundle_hash": content_hash(entries), "files": entries}


def _default_now() -> str:
    return datetime.now().astimezone().isoformat()


class DecisionTraceSnapshotService:
    def __init__(
        self,
        evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
        *,
        now_factory: Callable[[], str] = _default_now,
    ) -> None:
        self.evidence_root = Path(evidence_root)
        self._now_factory = now_factory

    def save_rules(self, rules: dict[str, Any]) -> dict[str, Any]:
        payload = normalize_rules(rules)
        return self._save("rules", payload)

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        payload = normalize_settings(settings)
        return self._save("settings", payload)

    def _save(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        identity = content_hash(payload)
        path = self.evidence_root / kind / f"{identity}.json"
        document = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "hash": identity,
            "created_at": self._now_factory(),
            "payload": payload,
        }
        try:
            with _SNAPSHOT_LOCK:
                if path.exists():
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(existing, dict) or existing.get("hash") != identity or existing.get("payload") != payload:
                        return self._result(identity, path, integrity_error=True, error="existing snapshot content mismatch")
                    return self._result(identity, path, duplicate=True, payload=payload)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8", newline="") as handle:
                    json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
        except Exception as exc:
            return self._result(identity, path, write_failed=True, error=str(exc), payload=payload)
        return self._result(identity, path, saved=True, payload=payload)

    @staticmethod
    def _result(
        identity: str,
        path: Path,
        *,
        saved: bool = False,
        duplicate: bool = False,
        integrity_error: bool = False,
        write_failed: bool = False,
        error: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "saved": saved,
            "duplicate": duplicate,
            "integrity_error": integrity_error,
            "write_failed": write_failed,
            "hash": identity,
            "path": str(path),
            "payload": payload,
            "error": error,
        }
