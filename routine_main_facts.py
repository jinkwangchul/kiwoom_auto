"""Read-only Main -> routine facts snapshot boundary.

The snapshot is deliberately a value object.  It owns no writer and exposes
no repository path to a routine.  Main captures the ledgers/config projection
once, hashes that exact projection, and a routine decision consumes only the
returned payload for the lifetime of that decision.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent
_LEDGERS = {
    "signals": ("routine_signals.json", "signals"),
    "orders": ("order_queue.json", "orders"),
    "executions": ("order_executions.json", "executions"),
    "processes": ("order_executions.json", "processes"),
    "fills": ("fills.json", "fills"),
    "positions": ("positions.json", "positions"),
    "holdings": ("broker_holdings.json", "holdings"),
}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"facts source must be an object: {path.name}")
    return value


def _ledger(path: Path, key: str) -> list[dict[str, Any]]:
    root = _read_object(path)
    records = root.get(key, [])
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"facts ledger is malformed: {path.name}:{key}")
    return deepcopy(records)


@dataclass(frozen=True)
class RoutineMainFacts:
    revision: str
    captured_at: str
    snapshot_hash: str
    payload: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        result = deepcopy(dict(self.payload))
        result["revision"] = self.revision
        result["captured_at"] = self.captured_at
        result["snapshot_hash"] = self.snapshot_hash
        return result


def build_routine_main_facts_from_projection(
    projection: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> RoutineMainFacts:
    """Seal an already-read Main projection into one immutable fact identity."""
    content = {
        key: deepcopy(item)
        for key, item in dict(projection).items()
        if key not in {"revision", "captured_at", "snapshot_hash"}
    }
    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    snapshot_hash = _stable_hash(content)
    revision = _stable_hash({"captured_at": captured, "snapshot_hash": snapshot_hash})
    return RoutineMainFacts(
        revision=revision,
        captured_at=captured,
        snapshot_hash=snapshot_hash,
        payload=content,
    )


def capture_routine_main_facts(
    *,
    stock_dirs: Mapping[str, Path | str] | None = None,
    selected_account_no: str = "",
    allowed_stock_codes: tuple[str, ...] | list[str] | None = None,
    actionable_prices_by_code: Mapping[str, Any] | None = None,
    current_orderable_cash: Any = None,
    budget: Mapping[str, Any] | None = None,
    limits: Mapping[str, Any] | None = None,
    market: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
    project_root: Path | str = PROJECT_ROOT,
    now: datetime | None = None,
) -> RoutineMainFacts:
    """Capture all Main-owned evidence once without mutating any source."""
    root = Path(project_root)
    runtime = root / "runtime"
    roots_by_filename: dict[str, dict[str, Any]] = {}
    ledgers: dict[str, list[dict[str, Any]]] = {}
    for name, (filename, key) in _LEDGERS.items():
        if filename not in roots_by_filename:
            roots_by_filename[filename] = _read_object(runtime / filename)
        ledger_root = roots_by_filename[filename]
        records = ledger_root.get(key, [])
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise ValueError(f"facts ledger is malformed: {filename}:{key}")
        ledgers[name] = deepcopy(records)
    configs: dict[str, dict[str, Any]] = {}
    states: dict[str, dict[str, Any]] = {}
    for raw_code, raw_dir in (stock_dirs or {}).items():
        code = str(raw_code or "").strip()
        if not code:
            continue
        stock_dir = Path(raw_dir)
        configs[code] = _read_object(stock_dir / "config.json")
        states[code] = _read_object(stock_dir / "state.json")
    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    price_projection = deepcopy(dict(actionable_prices_by_code or {}))
    limit_projection = deepcopy(dict(limits or {}))
    if not limit_projection:
        limit_projection = {
            "by_stock_code": {
                code: {
                    key: deepcopy(config.get(key))
                    for key in (
                        "buy_limit_enabled", "buy_limit_amount",
                        "sell_limit_enabled", "sell_limit_amount",
                    )
                    if key in config
                }
                for code, config in configs.items()
            }
        }
    market_projection = deepcopy(dict(market or {}))
    market_projection.setdefault("actionable_prices_by_code", deepcopy(price_projection))
    review_projection = deepcopy(dict(review or {}))
    if not review_projection:
        review_projection = {
            "by_stock_code": {
                code: {
                    "review_required": state.get("review_required") is True,
                    "status": state.get("status"),
                    "review_reasons": deepcopy(state.get("review_reasons") or []),
                }
                for code, state in states.items()
            }
        }
    content = {
        **ledgers,
        "stock_configs": configs,
        "stock_states": states,
        "selected_account_no": str(selected_account_no or "").strip(),
        "allowed_stock_codes": [
            str(code or "").strip()
            for code in (allowed_stock_codes or ())
            if str(code or "").strip()
        ],
        "actionable_prices_by_code": price_projection,
        "current_orderable_cash": current_orderable_cash,
        "budget": deepcopy(
            dict(budget)
            if isinstance(budget, Mapping)
            else {"current_orderable_cash": current_orderable_cash}
        ),
        "limits": limit_projection,
        "market": market_projection,
        "review": review_projection,
    }
    return build_routine_main_facts_from_projection(
        content,
        now=datetime.fromisoformat(captured),
    )


def validate_routine_main_facts(value: Any) -> tuple[bool, str]:
    facts = value if isinstance(value, dict) else {}
    recorded = str(facts.get("snapshot_hash") or "").strip()
    revision = str(facts.get("revision") or "").strip()
    captured_at = str(facts.get("captured_at") or "").strip()
    if not recorded or not revision or not captured_at:
        return False, "MAIN_FACTS_IDENTITY_MISSING"
    content = {
        key: deepcopy(item)
        for key, item in facts.items()
        if key not in {"revision", "captured_at", "snapshot_hash"}
    }
    if _stable_hash(content) != recorded:
        return False, "MAIN_FACTS_HASH_MISMATCH"
    if _stable_hash({"captured_at": captured_at, "snapshot_hash": recorded}) != revision:
        return False, "MAIN_FACTS_REVISION_MISMATCH"
    return True, ""


def routine_main_facts_candidate_guard_hash(value: Any, stock_code: Any) -> str:
    """Hash facts that must still match before Candidate mutation.

    The signal ledger is intentionally excluded: enqueuing the evaluated signal
    is itself the expected mutation between evaluation and consumption. Signal
    ownership is arbitrated from the consumer's fresh full snapshot. All other
    lifecycle ledgers and the subject's config/state remain guarded.
    """
    valid, reason = validate_routine_main_facts(value)
    if not valid:
        raise ValueError(reason)
    code = str(stock_code or "").strip().lstrip("A")
    configs = value.get("stock_configs") if isinstance(value.get("stock_configs"), dict) else {}
    states = value.get("stock_states") if isinstance(value.get("stock_states"), dict) else {}
    prices = (
        value.get("actionable_prices_by_code")
        if isinstance(value.get("actionable_prices_by_code"), dict)
        else {}
    )
    allowed = {
        str(item or "").strip().lstrip("A")
        for item in (value.get("allowed_stock_codes") or [])
    }
    review = value.get("review") if isinstance(value.get("review"), dict) else {}
    review_by_code = (
        review.get("by_stock_code")
        if isinstance(review.get("by_stock_code"), dict)
        else {}
    )
    guarded = {
        "orders": deepcopy(value.get("orders") or []),
        "executions": deepcopy(value.get("executions") or []),
        "processes": deepcopy(value.get("processes") or []),
        "fills": deepcopy(value.get("fills") or []),
        "positions": deepcopy(value.get("positions") or []),
        "holdings": deepcopy(value.get("holdings") or []),
        "stock_config": deepcopy(configs.get(code) or {}),
        "stock_state": deepcopy(states.get(code) or {}),
        "selected_account_no": str(value.get("selected_account_no") or "").strip(),
        "allowed": code in allowed,
        "actionable_price": deepcopy(prices.get(code)),
        "current_orderable_cash": deepcopy(value.get("current_orderable_cash")),
        "review": deepcopy(review_by_code.get(code) or {}),
    }
    return _stable_hash(guarded)


def routine_main_facts_identity(value: Any, *, stock_code: Any = "") -> dict[str, str]:
    """Return the sealed identity carried by a routine evaluation result."""
    valid, reason = validate_routine_main_facts(value)
    if not valid:
        raise ValueError(reason)
    result = {
        "revision": str(value.get("revision") or ""),
        "snapshot_hash": str(value.get("snapshot_hash") or ""),
    }
    if str(stock_code or "").strip():
        result["candidate_guard_hash"] = routine_main_facts_candidate_guard_hash(
            value,
            stock_code,
        )
    return result


def records_from_routine_main_facts(
    value: Any,
    field: str,
    *,
    optional: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    valid, reason = validate_routine_main_facts(value)
    if not valid:
        return [], reason
    records = value.get(field)
    if records is None and optional:
        return [], ""
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return [], f"MAIN_FACTS_{str(field).upper()}_INVALID"
    return deepcopy(records), ""


def capture_routine_main_facts_for_subject(
    subject: Mapping[str, Any],
    *,
    project_root: Path | str = PROJECT_ROOT,
) -> RoutineMainFacts:
    """Resolve the subject stock in Main and capture its gate facts."""
    from stock_repository import StockRepository

    intent = subject.get("execution_intent")
    intent_dict = intent if isinstance(intent, dict) else {}
    code = str(subject.get("code") or intent_dict.get("code") or "").strip()
    if not code:
        raise ValueError("MAIN_FACTS_STOCK_CODE_MISSING")
    stock_dir = StockRepository(project_root=project_root).resolve_stock_dir(code)
    account_no = str(
        subject.get("account_no")
        or intent_dict.get("account_no")
        or ""
    ).strip()
    return capture_routine_main_facts(
        stock_dirs={code: stock_dir},
        selected_account_no=account_no,
        allowed_stock_codes=(code,),
        project_root=project_root,
    )
