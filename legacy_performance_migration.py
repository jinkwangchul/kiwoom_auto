"""Read-only planning and temp-only dry-run for legacy performance migration."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable, Iterable, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from performance_aggregator import CanonicalPerformanceAggregator
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    OWNERSHIP_UNRESOLVED,
    CanonicalStockPerformanceLedgerRepository,
)
from performance_metrics import CanonicalPerformanceMetricEngine
from stock_repository import is_valid_stock_code, normalize_stock_code


MIGRATION_SCHEMA_VERSION = "1.0"
MIGRATION_NAMESPACE = "KIWOOM_AUTO_LEGACY_PERFORMANCE_V1"
LEGACY_IMPORT_IDENTITY = "MIGRATION::LEGACY_IMPORT"
DEFAULT_LEGACY_TIMEZONE = timezone(timedelta(hours=9))

FULLY_RECOVERABLE = "FULLY_RECOVERABLE"
PARTIALLY_RECOVERABLE = "PARTIALLY_RECOVERABLE"
NOT_RECOVERABLE = "NOT_RECOVERABLE"

CANONICAL_IDENTITY_RECOVERABLE = "CANONICAL_IDENTITY_RECOVERABLE"
LEGACY_IDENTITY_RECOVERABLE = "LEGACY_IDENTITY_RECOVERABLE"
ECONOMIC_FACT_ONLY = "ECONOMIC_FACT_ONLY"

BOOTSTRAP_CURRENT_ASSIGNED = "BOOTSTRAP_CURRENT_ASSIGNED"
BOOTSTRAP_UNASSIGNED = "BOOTSTRAP_UNASSIGNED"


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest().upper()


def _target_uuid(kind: str, source_identity: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{MIGRATION_NAMESPACE}|{kind}|{source_identity}"))


def _relative(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _stock_code_from_path(path: Path, config: Mapping[str, Any] | None = None) -> str:
    configured = normalize_stock_code(_text((config or {}).get("code")))
    if is_valid_stock_code(configured):
        return configured
    candidate = normalize_stock_code(path.parent.name.split("_", 1)[0])
    return candidate if is_valid_stock_code(candidate) else ""


def _timestamp(value: object, legacy_timezone: timezone) -> tuple[str | None, bool]:
    text = _text(value)
    if not text:
        return None, False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    assumed_timezone = parsed.tzinfo is None or parsed.utcoffset() is None
    if assumed_timezone:
        parsed = parsed.replace(tzinfo=legacy_timezone)
    return parsed.isoformat(timespec="seconds"), assumed_timezone


def _decimal(value: object, *, nonnegative: bool = False) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or (nonnegative and result < 0):
        return None
    return result


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _source_paths(project_root: Path) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for stock_dir in sorted((project_root / "stocks").glob("*")):
        if not stock_dir.is_dir():
            continue
        for name in ("config.json", "orders.json"):
            path = stock_dir / name
            if path.is_file():
                paths.add(path)
    for name in ("realized_pnl.json", "fills.json", "order_executions.json"):
        path = project_root / "runtime" / name
        if path.is_file():
            paths.add(path)
    for instance_dir in sorted((project_root / "routine_instances").glob("*")):
        path = instance_dir / "instance.json"
        if path.is_file():
            paths.add(path)
    registry = project_root / "groups" / "registry.json"
    if registry.is_file():
        paths.add(registry)
        try:
            raw = _read_json(registry)
        except Exception:
            raw = {}
        if isinstance(raw, Mapping):
            for group_id in raw.get("group_ids", []):
                path = project_root / "groups" / _text(group_id) / "group.json"
                if path.is_file():
                    paths.add(path)
    return tuple(sorted(paths, key=lambda item: _relative(item, project_root)))


def collect_legacy_performance_source_hashes(project_root: Path | str) -> dict[str, str]:
    root = Path(project_root).resolve()
    return {_relative(path, root): _sha256(path) for path in _source_paths(root)}


def verify_legacy_performance_source_hashes(
    plan: Mapping[str, Any],
    project_root: Path | str,
) -> tuple[str, ...]:
    expected = dict(plan.get("source_files") or {})
    current = collect_legacy_performance_source_hashes(project_root)
    differences = {
        *(f"ADDED:{path}" for path in current.keys() - expected.keys()),
        *(f"REMOVED:{path}" for path in expected.keys() - current.keys()),
        *(
            f"CHANGED:{path}"
            for path in expected.keys() & current.keys()
            if expected[path] != current[path]
        ),
    }
    return tuple(sorted(differences))


def _load_identity_snapshots(project_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    instances: dict[str, dict[str, Any]] = {}
    for path in sorted((project_root / "routine_instances").glob("*/instance.json")):
        try:
            value = _read_json(path)
        except Exception:
            continue
        if isinstance(value, Mapping) and _text(value.get("instance_id")):
            instances[_text(value.get("instance_id"))] = dict(value)

    groups: dict[str, dict[str, Any]] = {}
    registry = project_root / "groups" / "registry.json"
    try:
        value = _read_json(registry)
    except Exception:
        value = {}
    if isinstance(value, Mapping):
        for group_id in value.get("group_ids", []):
            path = project_root / "groups" / _text(group_id) / "group.json"
            try:
                group = _read_json(path)
            except Exception:
                continue
            if isinstance(group, Mapping) and _text(group.get("group_id")):
                groups[_text(group.get("group_id"))] = dict(group)
    return instances, groups


def _assignment_record(
    *,
    stock_code: str,
    raw: object,
    occurrence: int,
    legacy_timezone: timezone,
) -> dict[str, Any]:
    raw_mapping = dict(raw) if isinstance(raw, Mapping) else {}
    raw_fingerprint = _fingerprint(raw)
    source_identity = f"stock:{stock_code}:assignment:{raw_fingerprint}:occurrence:{occurrence}"
    record = {
        "source_identity": source_identity,
        "source_fingerprint": raw_fingerprint,
        "stock_code": stock_code,
        "instance_id": _text(raw_mapping.get("instance_id")) or None,
        "instance_name_snapshot": _text(raw_mapping.get("instance_name")) or None,
        "definition_id": _text(raw_mapping.get("definition_id")) or None,
        "historical_group_id": _text(raw_mapping.get("group_id")) or None,
        "historical_group_name_snapshot": _text(raw_mapping.get("group_name")) or None,
        "raw": raw,
    }
    started_at, start_assumed = _timestamp(raw_mapping.get("registered_at"), legacy_timezone)
    ended_at, end_assumed = _timestamp(raw_mapping.get("unregistered_at"), legacy_timezone)
    record.update(
        {
            "started_at": started_at,
            "ended_at": ended_at,
            "open_in_legacy_history": ended_at is None,
            "legacy_timezone_assumed": start_assumed or end_assumed,
            "target_episode_id": _target_uuid("ASSIGNMENT_EPISODE", source_identity),
        }
    )
    reasons: list[str] = []
    if not isinstance(raw, Mapping):
        reasons.append("record is not an object")
    if not is_valid_stock_code(stock_code):
        reasons.append("stock_code is invalid")
    if not record["instance_id"]:
        reasons.append("instance_id is missing")
    if not started_at:
        reasons.append("registered_at is missing or invalid")
    if started_at and ended_at and datetime.fromisoformat(ended_at) < datetime.fromisoformat(started_at):
        reasons.append("unregistered_at precedes registered_at")
    if reasons:
        record.update(
            classification=NOT_RECOVERABLE,
            migration_action="REJECT",
            unresolved_reasons=reasons,
            target_episode_id=None,
        )
    else:
        unresolved: list[str] = []
        if not record["historical_group_id"]:
            unresolved.append("historical group_id is not recorded")
        if record["legacy_timezone_assumed"]:
            unresolved.append("legacy timestamp has no UTC offset; declared legacy timezone is required")
        record.update(
            classification=(PARTIALLY_RECOVERABLE if unresolved else FULLY_RECOVERABLE),
            migration_action=("CURRENT_BOOTSTRAP_SUPERSEDES_OPEN_HISTORY" if ended_at is None else "MIGRATE_CLOSED_EPISODE"),
            unresolved_reasons=unresolved,
        )
    return record


def _mark_assignment_overlaps(records: list[dict[str, Any]]) -> None:
    by_stock: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["classification"] != NOT_RECOVERABLE and record["ended_at"] is not None:
            by_stock[record["stock_code"]].append(record)
    for stock_records in by_stock.values():
        previous_end: datetime | None = None
        for record in sorted(stock_records, key=lambda item: (item["started_at"], item["source_identity"])):
            started = datetime.fromisoformat(record["started_at"])
            if previous_end is not None and started < previous_end:
                record.update(
                    classification=NOT_RECOVERABLE,
                    migration_action="REJECT",
                    unresolved_reasons=[*record["unresolved_reasons"], "assignment interval overlaps a prior record"],
                    target_episode_id=None,
                )
                continue
            previous_end = datetime.fromisoformat(record["ended_at"])


def _economic_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "quantity": _decimal(raw.get("sell_quantity", raw.get("quantity")), nonnegative=True),
        "realized_cost_basis": _decimal(raw.get("matched_cost_basis", raw.get("realized_cost_basis")), nonnegative=True),
        "gross_pnl": _decimal(raw.get("gross_realized_profit", raw.get("gross_pnl"))),
        "fee": _decimal(raw.get("fee"), nonnegative=True),
        "tax": _decimal(raw.get("tax"), nonnegative=True),
        "net_pnl": _decimal(raw.get("net_realized_profit", raw.get("net_pnl"))),
    }


def _performance_record(
    *,
    raw: object,
    occurrence: int,
    legacy_timezone: timezone,
) -> dict[str, Any]:
    value = dict(raw) if isinstance(raw, Mapping) else {}
    fingerprint = _fingerprint(raw)
    realization_id = _text(value.get("realization_id"))
    fill_id = _text(value.get("fill_id"))
    execution_identity = _text(value.get("execution_identity"))
    broker_order_no = _text(value.get("broker_order_no"))
    broker = _text(value.get("broker"))
    account = _text(value.get("account_number"))
    trade_date = _text(value.get("trade_date"))
    stock_code = normalize_stock_code(_text(value.get("stock_code")))
    realized_at, timezone_assumed = _timestamp(value.get("realized_at"), legacy_timezone)
    economics = _economic_fields(value)
    stable_legacy_identity = (
        f"realization:{realization_id}"
        if realization_id
        else (f"fill:{fill_id}:execution:{execution_identity}" if fill_id and execution_identity else "")
    )
    canonical_identity = all((broker, account, trade_date, broker_order_no, execution_identity))
    source_identity = (
        f"realized:{stable_legacy_identity}"
        if stable_legacy_identity
        else f"realized:fingerprint:{fingerprint}:occurrence:{occurrence}"
    )
    reasons: list[str] = []
    if not isinstance(raw, Mapping):
        reasons.append("record is not an object")
    if not is_valid_stock_code(stock_code):
        reasons.append("stock_code is invalid")
    try:
        date.fromisoformat(trade_date)
    except ValueError:
        reasons.append("trade_date is missing or invalid")
    if not realized_at:
        reasons.append("realized_at is missing or invalid")
    if economics["quantity"] is None or economics["quantity"] <= 0 or economics["quantity"] != economics["quantity"].to_integral_value():
        reasons.append("sell quantity is missing or invalid")
    if economics["realized_cost_basis"] is None:
        reasons.append("realized cost basis is missing or invalid")
    if economics["gross_pnl"] is None:
        reasons.append("gross realized PnL is missing or invalid")

    if reasons:
        classification = NOT_RECOVERABLE
        action = "REJECT"
    elif canonical_identity:
        classification = CANONICAL_IDENTITY_RECOVERABLE
        action = "IMPORT_CANONICAL_EVENT"
    elif stable_legacy_identity:
        classification = LEGACY_IDENTITY_RECOVERABLE
        action = "IMPORT_LEGACY_NAMESPACED_EVENT"
    else:
        classification = ECONOMIC_FACT_ONLY
        action = "REPORT_ONLY"

    return {
        "source_identity": source_identity,
        "source_fingerprint": fingerprint,
        "classification": classification,
        "migration_action": action,
        "unresolved_reasons": reasons + (["entry BUY lot and Episode ownership are not recorded"] if action.startswith("IMPORT_") else []),
        "stock_code": stock_code,
        "trade_date": trade_date,
        "realized_at": realized_at,
        "legacy_timezone_assumed": timezone_assumed,
        "realization_id": realization_id or None,
        "fill_id": fill_id or None,
        "broker": broker or None,
        "account_number": account or None,
        "broker_order_no": broker_order_no or None,
        "execution_identity": execution_identity or None,
        "quantity": _number(economics["quantity"]),
        "realized_cost_basis": _number(economics["realized_cost_basis"]),
        "gross_pnl": _number(economics["gross_pnl"]),
        "fee": _number(economics["fee"]),
        "tax": _number(economics["tax"]),
        "net_pnl": _number(economics["net_pnl"]),
        "ownership_status": OWNERSHIP_UNRESOLVED if action.startswith("IMPORT_") else None,
        "target_event_id": _target_uuid("PERFORMANCE_EVENT", source_identity) if action.startswith("IMPORT_") else None,
        "raw": raw,
    }


def build_legacy_performance_migration_plan(
    project_root: Path | str,
    *,
    bootstrap_at: str,
    legacy_timezone: timezone = DEFAULT_LEGACY_TIMEZONE,
) -> dict[str, Any]:
    """Read legacy sources and produce an immutable, deterministic migration plan."""
    root = Path(project_root).resolve()
    bootstrap_timestamp, bootstrap_assumed = _timestamp(bootstrap_at, legacy_timezone)
    if not bootstrap_timestamp or bootstrap_assumed:
        raise ValueError("bootstrap_at must be an offset-aware ISO-8601 timestamp")
    instances, groups = _load_identity_snapshots(root)
    assignment_records: list[dict[str, Any]] = []
    bootstraps: list[dict[str, Any]] = []
    source_errors: list[str] = []
    assignment_occurrences: Counter[str] = Counter()

    config_paths = sorted((root / "stocks").glob("*/config.json"))
    for config_path in config_paths:
        try:
            raw_config = _read_json(config_path)
            if not isinstance(raw_config, Mapping):
                raise ValueError("config root must be an object")
        except Exception as exc:
            source_errors.append(f"{_relative(config_path, root)}: {exc}")
            continue
        stock_code = _stock_code_from_path(config_path, raw_config)
        raw_history = raw_config.get("routine_assignment_history", [])
        if not isinstance(raw_history, list):
            source_errors.append(f"{_relative(config_path, root)}: routine_assignment_history is not a list")
            raw_history = []
        for raw in raw_history:
            fingerprint = _fingerprint(raw)
            occurrence_key = f"{stock_code}|{fingerprint}"
            assignment_occurrences[occurrence_key] += 1
            assignment_records.append(
                _assignment_record(
                    stock_code=stock_code,
                    raw=raw,
                    occurrence=assignment_occurrences[occurrence_key],
                    legacy_timezone=legacy_timezone,
                )
            )

        instance_id = _text(raw_config.get("assigned_routine_instance_id"))
        instance = instances.get(instance_id, {})
        group_id = _text(instance.get("group_id")) if instance_id else ""
        group = groups.get(group_id, {})
        current_source = {
            "stock_code": stock_code,
            "instance_id": instance_id or None,
            "instance_name": _text(raw_config.get("routine_instance_name")) or _text(instance.get("display_name")) or None,
            "definition_id": _text(raw_config.get("routine_definition_id")) or _text(instance.get("definition_id")) or None,
            "group_id": group_id or None,
            "group_name": _text(group.get("display_name")) or None,
        }
        current_fingerprint = _fingerprint(current_source)
        source_identity = f"stock:{stock_code}:current-bootstrap:{current_fingerprint}"
        bootstraps.append(
            {
                **current_source,
                "classification": "CURRENT_BOOTSTRAP_REQUIRED",
                "migration_action": BOOTSTRAP_CURRENT_ASSIGNED if instance_id else BOOTSTRAP_UNASSIGNED,
                "started_at": bootstrap_timestamp,
                "source_identity": source_identity,
                "source_fingerprint": current_fingerprint,
                "target_episode_id": _target_uuid("CURRENT_BOOTSTRAP_EPISODE", source_identity),
                "unresolved_reasons": (
                    ["current Instance metadata is unavailable; Group remains unresolved"]
                    if instance_id and not instance
                    else []
                ),
            }
        )

    _mark_assignment_overlaps(assignment_records)

    realized_path = root / "runtime" / "realized_pnl.json"
    realized_records: list[dict[str, Any]] = []
    performance_occurrences: Counter[str] = Counter()
    try:
        realized_document = _read_json(realized_path) if realized_path.is_file() else {"realizations": []}
        raw_realizations = realized_document.get("realizations", []) if isinstance(realized_document, Mapping) else []
        if not isinstance(raw_realizations, list):
            raise ValueError("realizations is not a list")
        for raw in raw_realizations:
            fingerprint = _fingerprint(raw)
            performance_occurrences[fingerprint] += 1
            realized_records.append(
                _performance_record(
                    raw=raw,
                    occurrence=performance_occurrences[fingerprint],
                    legacy_timezone=legacy_timezone,
                )
            )
    except Exception as exc:
        source_errors.append(f"runtime/realized_pnl.json: {exc}")
    _mark_performance_identity_collisions(realized_records)

    source_counts: dict[str, int] = {}
    for name, key in (("fills", "fills"), ("order_executions", "executions")):
        path = root / "runtime" / f"{name}.json"
        try:
            value = _read_json(path) if path.is_file() else {key: []}
            records = value.get(key, []) if isinstance(value, Mapping) else []
            source_counts[name] = len(records) if isinstance(records, list) else 0
        except Exception as exc:
            source_errors.append(f"runtime/{name}.json: {exc}")
            source_counts[name] = 0
    orders = 0
    for path in sorted((root / "stocks").glob("*/orders.json")):
        try:
            value = _read_json(path)
            records = value if isinstance(value, list) else value.get("orders", []) if isinstance(value, Mapping) else []
            orders += len(records) if isinstance(records, list) else 0
        except Exception as exc:
            source_errors.append(f"{_relative(path, root)}: {exc}")
    source_counts["orders"] = orders

    return {
        "migration_schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_kind": "LEGACY_PERFORMANCE_DRY_RUN",
        "project_root": str(root),
        "legacy_timezone": legacy_timezone.tzname(None),
        "bootstrap_at": bootstrap_timestamp,
        "source_files": collect_legacy_performance_source_hashes(root),
        "source_errors": sorted(source_errors),
        "source_counts": {
            "stocks": len(config_paths),
            "legacy_assignment_records": len(assignment_records),
            "current_assignments": sum(bool(item["instance_id"]) for item in bootstraps),
            "current_unassigned": sum(not item["instance_id"] for item in bootstraps),
            "realized_pnl_records": len(realized_records),
            "canonical_episode_records": _canonical_record_count(root / "assignment_episodes", "episodes.json", "episodes"),
            "canonical_performance_events": _canonical_record_count(root / "performance_ledger", "events.json", "events"),
            **source_counts,
        },
        "assignment_records": assignment_records,
        "current_bootstraps": bootstraps,
        "performance_records": realized_records,
    }


def _canonical_record_count(root: Path, file_name: str, key: str) -> int:
    count = 0
    if not root.is_dir():
        return 0
    for path in root.glob(f"*/{file_name}"):
        try:
            value = _read_json(path)
        except Exception:
            continue
        records = value.get(key, []) if isinstance(value, Mapping) else []
        count += len(records) if isinstance(records, list) else 0
    return count


def _mark_performance_identity_collisions(records: list[dict[str, Any]]) -> None:
    seen: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["classification"] not in {
            CANONICAL_IDENTITY_RECOVERABLE,
            LEGACY_IDENTITY_RECOVERABLE,
        }:
            continue
        identity = record["source_identity"]
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = record
            continue
        if previous["source_fingerprint"] == record["source_fingerprint"]:
            record.update(
                migration_action="NO_OP_DUPLICATE_SOURCE",
                target_event_id=previous["target_event_id"],
                unresolved_reasons=[*record["unresolved_reasons"], "exact duplicate legacy source record"],
            )
        else:
            record.update(
                classification=NOT_RECOVERABLE,
                migration_action="REJECT",
                target_event_id=None,
                ownership_status=None,
                unresolved_reasons=[*record["unresolved_reasons"], "stable legacy identity has conflicting payloads"],
            )


def _episode_target(record: Mapping[str, Any]) -> AssignmentEpisodeTarget:
    return AssignmentEpisodeTarget.assigned(
        instance_id=_text(record.get("instance_id")),
        group_id=_text(record.get("historical_group_id")) or None,
        definition_id=_text(record.get("definition_id")) or None,
        instance_name_snapshot=_text(record.get("instance_name_snapshot")) or None,
        group_name_snapshot=_text(record.get("historical_group_name_snapshot")) or None,
    )


def _bootstrap_target(record: Mapping[str, Any]) -> AssignmentEpisodeTarget:
    if record.get("migration_action") == BOOTSTRAP_UNASSIGNED:
        return AssignmentEpisodeTarget.unassigned()
    return AssignmentEpisodeTarget.assigned(
        instance_id=_text(record.get("instance_id")),
        group_id=_text(record.get("group_id")) or None,
        definition_id=_text(record.get("definition_id")) or None,
        instance_name_snapshot=_text(record.get("instance_name")) or None,
        group_name_snapshot=_text(record.get("group_name")) or None,
    )


def _performance_payload(record: Mapping[str, Any], bootstrap_at: str) -> dict[str, Any]:
    canonical = record["classification"] == CANONICAL_IDENTITY_RECOVERABLE
    source_identity = _text(record.get("source_identity"))
    if canonical:
        broker = record["broker"]
        account = record["account_number"]
        broker_order_no = record["broker_order_no"]
        execution_identity = record["execution_identity"]
    else:
        broker = LEGACY_IMPORT_IDENTITY
        account = LEGACY_IMPORT_IDENTITY
        broker_order_no = f"LEGACY_SOURCE::{source_identity}"
        execution_identity = f"LEGACY_FINGERPRINT::{record['source_fingerprint']}"
    lot_id = f"LEGACY_IMPORT_LOT::{record['source_fingerprint']}"
    allocation = {
        "entry_lot_id": lot_id,
        "entry_episode_id": OWNERSHIP_UNRESOLVED,
        "quantity": record["quantity"],
        "cost_basis": record["realized_cost_basis"],
        "gross_pnl": record["gross_pnl"],
        "net_pnl": record["net_pnl"],
    }
    return {
        "performance_event_id": record["target_event_id"],
        "stock_code": record["stock_code"],
        "broker": broker,
        "account_number": account,
        "trade_date": record["trade_date"],
        "broker_order_no": broker_order_no,
        "execution_identity": execution_identity,
        "fill_id": record["fill_id"],
        "realization_id": record["realization_id"],
        "realized_at": record["realized_at"],
        "quantity": record["quantity"],
        "realized_cost_basis": record["realized_cost_basis"],
        "gross_pnl": record["gross_pnl"],
        "fee": record["fee"],
        "tax": record["tax"],
        "net_pnl": record["net_pnl"],
        "exit_episode_id": OWNERSHIP_UNRESOLVED,
        "canonical_owner_policy": CANONICAL_OWNER_POLICY,
        "allocations": [allocation],
        "recorded_at": bootstrap_at,
    }


def run_legacy_performance_migration_dry_run(
    plan: Mapping[str, Any],
    dry_run_root: Path | str,
) -> dict[str, Any]:
    """Materialize the plan only below an explicit non-project dry-run root."""
    output_root = Path(dry_run_root).resolve()
    project_root = Path(_text(plan.get("project_root"))).resolve()
    if output_root == project_root or project_root in output_root.parents:
        raise ValueError("dry-run output must be outside the source project")
    if output_root.name in {"assignment_episodes", "performance_ledger", "stocks", "runtime"}:
        raise ValueError("dry-run root must be an isolated directory")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("dry-run root must be empty")
    output_root.mkdir(parents=True, exist_ok=True)

    closed_records = sorted(
        (
            item
            for item in plan.get("assignment_records", [])
            if item.get("migration_action") == "MIGRATE_CLOSED_EPISODE"
            and item.get("classification") != NOT_RECOVERABLE
        ),
        key=lambda item: (item["stock_code"], item["started_at"], item["source_identity"]),
    )
    bootstraps = sorted(plan.get("current_bootstraps", []), key=lambda item: item["stock_code"])
    episode_ids = deque([item["target_episode_id"] for item in closed_records + bootstraps])
    episodes = CanonicalAssignmentEpisodeRepository(
        output_root,
        episodes_root=output_root / "assignment_episodes",
        episode_id_factory=lambda: episode_ids.popleft(),
    )
    materialization_errors: list[str] = []
    for record in closed_records:
        opened = episodes.open_episode(
            record["stock_code"],
            _episode_target(record),
            started_at=record["started_at"],
            start_reason="LEGACY_ASSIGNMENT_IMPORT",
            source="LEGACY_MIGRATION",
        )
        if not opened.success:
            materialization_errors.append(f"{record['source_identity']}: {opened.error}")
            continue
        closed = episodes.close_open_episode(
            record["stock_code"],
            ended_at=record["ended_at"],
            end_reason="LEGACY_ASSIGNMENT_ENDED",
            source="LEGACY_MIGRATION",
        )
        if not closed.success:
            materialization_errors.append(f"{record['source_identity']}: {closed.error}")
    for record in bootstraps:
        opened = episodes.open_episode(
            record["stock_code"],
            _bootstrap_target(record),
            started_at=record["started_at"],
            start_reason=record["migration_action"],
            source="LEGACY_MIGRATION_BOOTSTRAP",
        )
        if not opened.success:
            materialization_errors.append(f"{record['source_identity']}: {opened.error}")

    event_records = sorted(
        (
            item
            for item in plan.get("performance_records", [])
            if item.get("migration_action") in {"IMPORT_CANONICAL_EVENT", "IMPORT_LEGACY_NAMESPACED_EVENT"}
        ),
        key=lambda item: (item["stock_code"], item["realized_at"], item["source_identity"]),
    )
    event_ids = deque([item["target_event_id"] for item in event_records])
    ledger = CanonicalStockPerformanceLedgerRepository(
        output_root,
        ledger_root=output_root / "performance_ledger",
        episode_repository=episodes,
        event_id_factory=lambda: event_ids.popleft(),
        now_factory=lambda: datetime.fromisoformat(_text(plan.get("bootstrap_at"))),
    )
    for record in event_records:
        result = ledger.append_event(_performance_payload(record, _text(plan.get("bootstrap_at"))))
        if not result.success:
            materialization_errors.append(f"{record['source_identity']}: {result.error}")

    aggregator = CanonicalPerformanceAggregator(ledger, episodes)
    reconciliation = aggregator.reconciliation()
    episode_count = sum(len(episodes.list_episodes(item["stock_code"])) for item in bootstraps)
    event_count = sum(len(ledger.list_events(code)) for code in {item["stock_code"] for item in event_records})
    source_gross = sum((Decimal(str(item["gross_pnl"])) for item in event_records), Decimal(0))
    source_net_known = sum((Decimal(str(item["net_pnl"])) for item in event_records if item["net_pnl"] is not None), Decimal(0))
    dry_result = {
        "episode_count": episode_count,
        "historical_episode_count": len(closed_records),
        "bootstrap_episode_count": len(bootstraps),
        "performance_event_count": event_count,
        "unresolved_event_count": len(event_records),
        "materialization_errors": materialization_errors,
        "assignment_reconciled": len(plan.get("assignment_records", [])) == sum(Counter(item["classification"] for item in plan.get("assignment_records", [])).values()),
        "performance_reconciled": len(plan.get("performance_records", [])) == sum(Counter(item["classification"] for item in plan.get("performance_records", [])).values()),
        "source_importable_gross_pnl": _number(source_gross),
        "dry_run_stock_lifetime_gross_pnl": reconciliation.stock_lifetime_gross_pnl,
        "source_importable_net_pnl_known_sum": _number(source_net_known),
        "stock_lifetime_reconciled": Decimal(str(reconciliation.stock_lifetime_gross_pnl)) == source_gross,
        "parent_difference_explained": (
            reconciliation.stock_reconciled
            and reconciliation.instance_reconciled
            and reconciliation.group_reconciled
            and Decimal(str(reconciliation.unresolved_gross_pnl)) == source_gross
        ),
        "performance_reconciliation": reconciliation.__dict__,
    }
    manifest = {
        "migration_schema_version": plan["migration_schema_version"],
        "migration_kind": plan["migration_kind"],
        "migration_timestamp": None,
        "legacy_timezone": plan["legacy_timezone"],
        "bootstrap_at": plan["bootstrap_at"],
        "source_files": plan["source_files"],
        "source_counts": plan["source_counts"],
        "source_errors": plan["source_errors"],
        "assignment_records": plan["assignment_records"],
        "current_bootstraps": plan["current_bootstraps"],
        "performance_records": plan["performance_records"],
        "dry_run_result": dry_result,
    }
    _write_verified_json(output_root / "migration_manifest.json", manifest)
    return {"manifest": manifest, "dry_run_root": str(output_root)}


def _write_verified_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.tmp"
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        if _read_json(temp) != value:
            raise RuntimeError("migration manifest staged read-back mismatch")
        os.replace(temp, path)
        if _read_json(path) != value:
            raise RuntimeError("migration manifest read-back mismatch")
    finally:
        temp.unlink(missing_ok=True)


def migration_readiness_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    assignments = Counter(item["classification"] for item in plan.get("assignment_records", []))
    performance = Counter(item["classification"] for item in plan.get("performance_records", []))
    imported = [item for item in plan.get("performance_records", []) if _text(item.get("migration_action")).startswith("IMPORT_")]
    blocking_reasons = list(plan.get("source_errors", []))
    return {
        "assignment": dict(assignments),
        "performance": dict(performance),
        "current_bootstrap_required": len(plan.get("current_bootstraps", [])),
        "planned_historical_episodes": sum(item.get("migration_action") == "MIGRATE_CLOSED_EPISODE" for item in plan.get("assignment_records", [])),
        "planned_performance_events": len(imported),
        "planned_unresolved_events": sum(item.get("ownership_status") == OWNERSHIP_UNRESOLVED for item in imported),
        "importable_gross_pnl": _number(sum((Decimal(str(item["gross_pnl"])) for item in imported), Decimal(0))),
        "importable_net_pnl_known_sum": _number(sum((Decimal(str(item["net_pnl"])) for item in imported if item["net_pnl"] is not None), Decimal(0))),
        "blocking_reasons": blocking_reasons,
        "ready_for_phase_8b_apply": not blocking_reasons,
    }


def planned_phase_8b_apply_paths(plan: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the only project-relative files a later approved apply may create."""
    episode_codes = {
        item["stock_code"]
        for item in plan.get("assignment_records", [])
        if item.get("migration_action") == "MIGRATE_CLOSED_EPISODE"
        and item.get("classification") != NOT_RECOVERABLE
    }
    episode_codes.update(item["stock_code"] for item in plan.get("current_bootstraps", []))
    event_codes = {
        item["stock_code"]
        for item in plan.get("performance_records", [])
        if item.get("migration_action") in {"IMPORT_CANONICAL_EVENT", "IMPORT_LEGACY_NAMESPACED_EVENT"}
    }
    paths = [f"assignment_episodes/{code}/episodes.json" for code in sorted(episode_codes)]
    paths.extend(f"performance_ledger/{code}/events.json" for code in sorted(event_codes))
    paths.append("migration_manifests/legacy_performance_v1.json")
    return tuple(paths)


def _phase_8b_data_paths(plan: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        path
        for path in planned_phase_8b_apply_paths(plan)
        if path != "migration_manifests/legacy_performance_v1.json"
    )


def _phase_8b_read_back(
    project_root: Path,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    episode_repository = CanonicalAssignmentEpisodeRepository(project_root)
    ledger_repository = CanonicalStockPerformanceLedgerRepository(
        project_root,
        episode_repository=episode_repository,
    )
    aggregator = CanonicalPerformanceAggregator(ledger_repository, episode_repository)
    episode_codes = sorted(
        path.split("/")[1]
        for path in _phase_8b_data_paths(plan)
        if path.startswith("assignment_episodes/")
    )
    event_codes = sorted(
        path.split("/")[1]
        for path in _phase_8b_data_paths(plan)
        if path.startswith("performance_ledger/")
    )
    episodes = tuple(
        episode
        for stock_code in episode_codes
        for episode in episode_repository.list_episodes(stock_code)
    )
    events = tuple(
        event
        for stock_code in event_codes
        for event in ledger_repository.list_events(stock_code)
    )
    open_episodes = tuple(episode for episode in episodes if episode.is_open)
    closed_episodes = tuple(episode for episode in episodes if not episode.is_open)
    unresolved_allocations = tuple(
        allocation
        for event in events
        for allocation in event.allocations
        if allocation.entry_episode_id == OWNERSHIP_UNRESOLVED
    )
    stock_aggregates = tuple(aggregator.aggregate_stock_lifetime(code) for code in event_codes)
    reconciliation = aggregator.reconciliation()
    metrics = CanonicalPerformanceMetricEngine()
    reject_ids = {
        _text(item.get("target_episode_id"))
        for item in plan.get("assignment_records", [])
        if item.get("migration_action") == "REJECT" and _text(item.get("target_episode_id"))
    }
    actual_episode_ids = {episode.episode_id for episode in episodes}
    return {
        "episode_document_count": len(episode_codes),
        "episode_count": len(episodes),
        "historical_closed_count": len(closed_episodes),
        "open_bootstrap_count": len(open_episodes),
        "open_stock_count": len({episode.stock_code for episode in open_episodes}),
        "max_open_episode_per_stock": max(
            (sum(item.stock_code == code for item in open_episodes) for code in episode_codes),
            default=0,
        ),
        "assigned_bootstrap_count": sum(bool(episode.instance_id) for episode in open_episodes),
        "unassigned_bootstrap_count": sum(not episode.instance_id for episode in open_episodes),
        "performance_event_document_count": len(event_codes),
        "performance_event_count": len(events),
        "unresolved_event_count": sum(
            all(allocation.entry_episode_id == OWNERSHIP_UNRESOLVED for allocation in event.allocations)
            for event in events
        ),
        "unresolved_allocation_count": len(unresolved_allocations),
        "realized_quantity": sum(value.realized_quantity for value in stock_aggregates),
        "realized_cost_basis": _number(
            sum((Decimal(str(value.realized_cost_basis)) for value in stock_aggregates), Decimal(0))
        ),
        "gross_pnl": _number(
            sum((Decimal(str(value.gross_pnl)) for value in stock_aggregates), Decimal(0))
        ),
        "net_pnl_complete": all(value.net_pnl_complete for value in stock_aggregates),
        "net_metric_statuses": {
            value.stock_code: metrics.calculate(value).net_profit_amount.status.value
            for value in stock_aggregates
        },
        "rejected_target_episode_present": bool(reject_ids & actual_episode_ids),
        "reconciliation": reconciliation.__dict__,
    }


def _validate_phase_8b_materialization(
    plan: Mapping[str, Any],
    dry_result: Mapping[str, Any],
    read_back: Mapping[str, Any] | None = None,
) -> None:
    readiness = migration_readiness_summary(plan)
    if not readiness["ready_for_phase_8b_apply"]:
        raise RuntimeError("migration plan is not ready for apply")
    expected_closed = int(readiness["planned_historical_episodes"])
    expected_bootstrap = int(readiness["current_bootstrap_required"])
    expected_events = int(readiness["planned_performance_events"])
    expected_unresolved = int(readiness["planned_unresolved_events"])
    expected_episodes = expected_closed + expected_bootstrap
    checks = {
        "materialization_errors": not dry_result.get("materialization_errors"),
        "episode_count": int(dry_result.get("episode_count", -1)) == expected_episodes,
        "historical_episode_count": int(dry_result.get("historical_episode_count", -1)) == expected_closed,
        "bootstrap_episode_count": int(dry_result.get("bootstrap_episode_count", -1)) == expected_bootstrap,
        "performance_event_count": int(dry_result.get("performance_event_count", -1)) == expected_events,
        "unresolved_event_count": int(dry_result.get("unresolved_event_count", -1)) == expected_unresolved,
        "assignment_reconciled": bool(dry_result.get("assignment_reconciled")),
        "performance_reconciled": bool(dry_result.get("performance_reconciled")),
        "stock_lifetime_reconciled": bool(dry_result.get("stock_lifetime_reconciled")),
        "parent_difference_explained": bool(dry_result.get("parent_difference_explained")),
    }
    if read_back is not None:
        expected_assigned = sum(
            item.get("migration_action") == BOOTSTRAP_CURRENT_ASSIGNED
            for item in plan.get("current_bootstraps", [])
        )
        expected_unassigned = sum(
            item.get("migration_action") == BOOTSTRAP_UNASSIGNED
            for item in plan.get("current_bootstraps", [])
        )
        checks.update(
            read_back_episode_count=int(read_back.get("episode_count", -1)) == expected_episodes,
            read_back_closed_count=int(read_back.get("historical_closed_count", -1)) == expected_closed,
            read_back_open_count=int(read_back.get("open_bootstrap_count", -1)) == expected_bootstrap,
            read_back_open_uniqueness=int(read_back.get("max_open_episode_per_stock", -1)) <= 1,
            read_back_assigned=int(read_back.get("assigned_bootstrap_count", -1)) == expected_assigned,
            read_back_unassigned=int(read_back.get("unassigned_bootstrap_count", -1)) == expected_unassigned,
            read_back_events=int(read_back.get("performance_event_count", -1)) == expected_events,
            read_back_unresolved_events=int(read_back.get("unresolved_event_count", -1)) == expected_unresolved,
            read_back_unresolved_allocations=int(read_back.get("unresolved_allocation_count", -1)) == expected_unresolved,
            rejected_episode_absent=not bool(read_back.get("rejected_target_episode_present")),
            stock_reconciled=bool((read_back.get("reconciliation") or {}).get("stock_reconciled")),
            instance_reconciled=bool((read_back.get("reconciliation") or {}).get("instance_reconciled")),
            group_reconciled=bool((read_back.get("reconciliation") or {}).get("group_reconciled")),
        )
    failed = tuple(name for name, valid in checks.items() if not valid)
    if failed:
        raise RuntimeError("migration validation failed: " + ", ".join(failed))


def _verify_completed_phase_8b_apply(
    plan: Mapping[str, Any],
    project_root: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if manifest.get("apply_state") != "COMPLETE":
        raise RuntimeError("migration manifest is not complete")
    if dict(manifest.get("source_files") or {}) != dict(plan.get("source_files") or {}):
        raise RuntimeError("completed migration source identity does not match the current plan")
    expected_paths = set(_phase_8b_data_paths(plan))
    target_files = dict(manifest.get("target_files") or {})
    if set(target_files) != expected_paths:
        raise RuntimeError("completed migration target list does not match the current plan")
    mismatches = tuple(
        path
        for path, expected_hash in sorted(target_files.items())
        if not (project_root / path).is_file() or _sha256(project_root / path) != expected_hash
    )
    if mismatches:
        raise RuntimeError("completed migration target verification failed: " + ", ".join(mismatches))
    read_back = _phase_8b_read_back(project_root, plan)
    dry_result = dict(manifest.get("dry_run_result") or {})
    _validate_phase_8b_materialization(plan, dry_result, read_back)
    return {
        "success": True,
        "changed": False,
        "no_op": True,
        "manifest_path": str(project_root / "migration_manifests" / "legacy_performance_v1.json"),
        "created_files": (),
        "read_back": read_back,
        "source_hash_differences": (),
    }


def apply_legacy_performance_migration(
    plan: Mapping[str, Any],
    project_root: Path | str,
    *,
    promote_replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
) -> dict[str, Any]:
    """Apply one verified Phase 8B migration without merging or overwriting targets."""
    root = Path(project_root).resolve()
    if root != Path(_text(plan.get("project_root"))).resolve():
        raise ValueError("migration plan project_root does not match apply target")
    source_differences = verify_legacy_performance_source_hashes(plan, root)
    if source_differences:
        raise RuntimeError("migration source hash changed: " + ", ".join(source_differences))

    manifest_path = root / "migration_manifests" / "legacy_performance_v1.json"
    if manifest_path.exists():
        value = _read_json(manifest_path)
        if not isinstance(value, Mapping):
            raise RuntimeError("migration manifest is invalid")
        return _verify_completed_phase_8b_apply(plan, root, value)

    data_paths = _phase_8b_data_paths(plan)
    existing = tuple(path for path in data_paths if (root / path).exists())
    if existing:
        raise RuntimeError("unexpected canonical migration target exists: " + ", ".join(existing))

    staging_root = root.parent / f".{root.name}.legacy-performance-staging-{uuid4().hex}"
    promoted: list[Path] = []
    created_directories: list[Path] = []
    try:
        dry_run = run_legacy_performance_migration_dry_run(plan, staging_root)
        dry_manifest = dict(dry_run["manifest"])
        dry_result = dict(dry_manifest["dry_run_result"])
        _validate_phase_8b_materialization(plan, dry_result)
        missing = tuple(path for path in data_paths if not (staging_root / path).is_file())
        if missing:
            raise RuntimeError("staged migration target missing: " + ", ".join(missing))
        target_hashes = {path: _sha256(staging_root / path) for path in data_paths}

        for relative_path in data_paths:
            source = staging_root / relative_path
            destination = root / relative_path
            if destination.exists():
                raise RuntimeError(f"canonical migration target appeared during apply: {relative_path}")
            missing_parents: list[Path] = []
            parent = destination.parent
            while parent != root and not parent.exists():
                missing_parents.append(parent)
                parent = parent.parent
            destination.parent.mkdir(parents=True, exist_ok=True)
            created_directories.extend(reversed(missing_parents))
            promote_replace(source, destination)
            promoted.append(destination)
            if _sha256(destination) != target_hashes[relative_path]:
                raise RuntimeError(f"promoted migration target hash mismatch: {relative_path}")

        read_back = _phase_8b_read_back(root, plan)
        _validate_phase_8b_materialization(plan, dry_result, read_back)
        completed_manifest = {
            **dry_manifest,
            "migration_kind": "LEGACY_PERFORMANCE_APPLY",
            "migration_timestamp": plan["bootstrap_at"],
            "apply_state": "COMPLETE",
            "target_files": target_hashes,
            "post_write_read_back": read_back,
        }
        staged_manifest = staging_root / "migration_manifests" / "legacy_performance_v1.json"
        _write_verified_json(staged_manifest, completed_manifest)
        if manifest_path.exists():
            raise RuntimeError("migration manifest appeared during apply")
        if not manifest_path.parent.exists():
            manifest_path.parent.mkdir(parents=True)
            created_directories.append(manifest_path.parent)
        promote_replace(staged_manifest, manifest_path)
        promoted.append(manifest_path)
        if _read_json(manifest_path) != completed_manifest:
            raise RuntimeError("completed migration manifest read-back mismatch")
        return {
            "success": True,
            "changed": True,
            "no_op": False,
            "manifest_path": str(manifest_path),
            "created_files": tuple(str(path.relative_to(root).as_posix()) for path in promoted),
            "read_back": read_back,
            "source_hash_differences": (),
        }
    except Exception:
        for path in reversed(promoted):
            path.unlink(missing_ok=True)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
