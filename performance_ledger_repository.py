"""Append-only Stock performance history with canonical Episode ownership."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import unicodedata
from typing import Any, Callable, Mapping
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from assignment_episode_repository import AssignmentEpisode, CanonicalAssignmentEpisodeRepository
from stock_repository import is_valid_stock_code, normalize_stock_code


PERFORMANCE_LEDGER_SCHEMA_VERSION = "1.0"
CANONICAL_OWNER_POLICY = "ENTRY_EPISODE"
OWNERSHIP_UNRESOLVED = "OWNERSHIP_UNRESOLVED"
PROJECT_ROOT = Path(__file__).resolve().parent

_WRITE_LOCK = threading.RLock()


def _text(value: object) -> str:
    return str(value or "").strip()


def _nullable_text(value: object) -> str | None:
    return _text(value) or None


def _normalized_identity(value: object, *, remove_account_separators: bool = False) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).upper()
    if remove_account_separators:
        return "".join(char for char in text if not char.isspace() and char != "-")
    return " ".join(text.split())


def _trade_date(value: object) -> str:
    text = _text(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("trade_date must be ISO-8601 YYYY-MM-DD") from exc
    return parsed.isoformat()


def _timestamp(value: object) -> str:
    text = _text(value)
    if not text:
        raise ValueError("realized_at is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("realized_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("realized_at must include a UTC offset")
    return parsed.isoformat(timespec="seconds")


def _decimal(value: object, field: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{field} must be numeric")
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    if nonnegative and number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _nullable_decimal(value: object, field: str, *, nonnegative: bool = False) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value, field, nonnegative=nonnegative)


def _json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("numeric value is outside JSON range")
    return round(number, 6)


def _uuid_text(value: object, field: str) -> str:
    text = _text(value)
    try:
        return str(UUID(text))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def canonical_performance_event_key(
    *,
    broker: object,
    account_number: object,
    trade_date: object,
    broker_order_no: object,
    execution_identity: object,
) -> str:
    """Return a stable key for one broker execution after canonical normalization."""
    identity = {
        "broker": _normalized_identity(broker),
        "account_number": _normalized_identity(account_number, remove_account_separators=True),
        "trade_date": _trade_date(trade_date),
        "broker_order_no": _normalized_identity(broker_order_no),
        "execution_identity": _normalized_identity(execution_identity),
    }
    missing = [name for name, value in identity.items() if not value]
    if missing:
        raise ValueError(f"performance identity requires {', '.join(missing)}")
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


@dataclass(frozen=True)
class PerformanceAllocation:
    allocation_id: str
    entry_lot_id: str
    entry_episode_id: str
    quantity: int
    cost_basis: int | float
    gross_pnl: int | float
    net_pnl: int | float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceEvent:
    schema_version: str
    performance_event_id: str
    performance_event_key: str
    stock_code: str
    broker: str
    account_number: str
    trade_date: str
    broker_order_no: str
    execution_identity: str
    fill_id: str | None
    realization_id: str | None
    realized_at: str
    quantity: int
    realized_cost_basis: int | float
    gross_pnl: int | float
    fee: int | float | None
    tax: int | float | None
    net_pnl: int | float | None
    exit_episode_id: str
    canonical_owner_policy: str
    allocations: tuple[PerformanceAllocation, ...]
    recorded_at: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["allocations"] = [allocation.to_dict() for allocation in self.allocations]
        return data


@dataclass(frozen=True)
class PerformanceLedgerMutationResult:
    success: bool
    changed: bool = False
    no_op: bool = False
    conflict: bool = False
    event: PerformanceEvent | None = None
    error_code: str = ""
    error: str = ""


class CanonicalStockPerformanceLedgerRepository:
    def __init__(
        self,
        project_root: Path | str = PROJECT_ROOT,
        *,
        ledger_root: Path | str | None = None,
        episode_repository: CanonicalAssignmentEpisodeRepository | None = None,
        event_id_factory: Callable[[], object] = uuid4,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.ledger_root = (
            Path(ledger_root)
            if ledger_root is not None
            else self.project_root / "performance_ledger"
        )
        self.episode_repository = episode_repository or CanonicalAssignmentEpisodeRepository(self.project_root)
        self._event_id_factory = event_id_factory
        self._now_factory = now_factory or (lambda: datetime.now().astimezone())

    def document_path(self, stock_code: str) -> Path:
        code = self._stock_code(stock_code)
        return self.ledger_root / code / "events.json"

    def list_events(
        self,
        stock_code: str,
        *,
        episode_lookup: Mapping[str, AssignmentEpisode] | None = None,
    ) -> tuple[PerformanceEvent, ...]:
        code = self._stock_code(stock_code)
        path = self.document_path(code)
        if not path.exists():
            return ()
        return self._read_document(
            path,
            expected_stock_code=code,
            episode_lookup=episode_lookup,
        )

    def get_event(
        self,
        performance_event_id: str,
        *,
        stock_code: str | None = None,
    ) -> PerformanceEvent | None:
        clean_id = _text(performance_event_id)
        if not clean_id:
            return None
        return self._find_unique(
            lambda event: event.performance_event_id == clean_id,
            stock_code=stock_code,
            duplicate_error="performance_event_id is not globally unique",
        )

    def get_event_by_key(
        self,
        performance_event_key: str,
        *,
        stock_code: str | None = None,
    ) -> PerformanceEvent | None:
        clean_key = _text(performance_event_key).upper()
        if not clean_key:
            return None
        return self._find_unique(
            lambda event: event.performance_event_key == clean_key,
            stock_code=stock_code,
            duplicate_error="performance_event_key is not globally unique",
        )

    def list_allocations(self, stock_code: str) -> tuple[PerformanceAllocation, ...]:
        return tuple(
            allocation
            for event in self.list_events(stock_code)
            for allocation in event.allocations
        )

    def validate_event(self, event: Mapping[str, Any] | PerformanceEvent) -> PerformanceEvent:
        value = event.to_dict() if isinstance(event, PerformanceEvent) else dict(event)
        return self._event_from_dict(value, require_persisted_identity=True)

    def append_event(self, event: Mapping[str, Any]) -> PerformanceLedgerMutationResult:
        with _WRITE_LOCK:
            try:
                candidate = self._prepare_event(dict(event))
                existing = self.get_event_by_key(candidate.performance_event_key)
                if existing is not None:
                    if self._economic_payload(existing) == self._economic_payload(candidate):
                        return PerformanceLedgerMutationResult(
                            True,
                            no_op=True,
                            event=existing,
                        )
                    return PerformanceLedgerMutationResult(
                        False,
                        conflict=True,
                        event=existing,
                        error_code="PERFORMANCE_EVENT_CONFLICT",
                        error="same performance_event_key has a different economic payload",
                    )
                events = self.list_events(candidate.stock_code)
                self._write_document(candidate.stock_code, events + (candidate,))
                return PerformanceLedgerMutationResult(True, changed=True, event=candidate)
            except Exception as exc:
                return PerformanceLedgerMutationResult(
                    False,
                    error_code="PERFORMANCE_EVENT_APPEND_FAILED",
                    error=str(exc),
                )

    def _prepare_event(self, value: dict[str, Any]) -> PerformanceEvent:
        supplied_key = _text(value.get("performance_event_key"))
        value["schema_version"] = PERFORMANCE_LEDGER_SCHEMA_VERSION
        value["performance_event_key"] = canonical_performance_event_key(
            broker=value.get("broker"),
            account_number=value.get("account_number"),
            trade_date=value.get("trade_date"),
            broker_order_no=value.get("broker_order_no"),
            execution_identity=value.get("execution_identity"),
        )
        if supplied_key and supplied_key.upper() != value["performance_event_key"]:
            raise ValueError("provided performance_event_key does not match canonical identity")
        value["performance_event_id"] = _text(value.get("performance_event_id")) or str(self._event_id_factory())
        value["recorded_at"] = _text(value.get("recorded_at")) or self._now_factory().isoformat(timespec="seconds")
        raw_allocations = value.get("allocations")
        if not isinstance(raw_allocations, list):
            raise ValueError("allocations must be a list")
        prepared_allocations: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_allocations):
            if not isinstance(raw, Mapping):
                raise ValueError("allocation must be an object")
            allocation = dict(raw)
            allocation["allocation_id"] = _text(allocation.get("allocation_id")) or str(
                uuid5(
                    NAMESPACE_URL,
                    "|".join(
                        (
                            "PERFORMANCE_ALLOCATION_V1",
                            value["performance_event_key"],
                            _text(allocation.get("entry_lot_id")),
                            _text(allocation.get("entry_episode_id")),
                            str(index),
                        )
                    ),
                )
            )
            prepared_allocations.append(allocation)
        value["allocations"] = prepared_allocations
        return self._event_from_dict(value, require_persisted_identity=True)

    def _event_from_dict(
        self,
        value: object,
        *,
        require_persisted_identity: bool,
        episode_lookup: Mapping[str, AssignmentEpisode] | None = None,
    ) -> PerformanceEvent:
        if not isinstance(value, Mapping):
            raise ValueError("performance event must be an object")
        schema_version = _text(value.get("schema_version"))
        if schema_version != PERFORMANCE_LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported performance event schema_version")
        event_id = _uuid_text(value.get("performance_event_id"), "performance_event_id")
        stock_code = self._stock_code(_text(value.get("stock_code")))
        broker = _normalized_identity(value.get("broker"))
        account_number = _normalized_identity(value.get("account_number"), remove_account_separators=True)
        trade_date = _trade_date(value.get("trade_date"))
        broker_order_no = _normalized_identity(value.get("broker_order_no"))
        execution_identity = _normalized_identity(value.get("execution_identity"))
        expected_key = canonical_performance_event_key(
            broker=broker,
            account_number=account_number,
            trade_date=trade_date,
            broker_order_no=broker_order_no,
            execution_identity=execution_identity,
        )
        event_key = _text(value.get("performance_event_key")).upper()
        if not event_key or event_key != expected_key:
            raise ValueError("performance_event_key does not match canonical identity")
        realized_at = _timestamp(value.get("realized_at"))
        if datetime.fromisoformat(realized_at).date().isoformat() != trade_date:
            raise ValueError("trade_date does not match realized_at")
        quantity_decimal = _decimal(value.get("quantity"), "quantity", nonnegative=True)
        if quantity_decimal <= 0 or quantity_decimal != quantity_decimal.to_integral_value():
            raise ValueError("quantity must be a positive integer")
        quantity = int(quantity_decimal)
        realized_cost_basis = _decimal(value.get("realized_cost_basis"), "realized_cost_basis", nonnegative=True)
        gross_pnl = _decimal(value.get("gross_pnl"), "gross_pnl")
        fee = _nullable_decimal(value.get("fee"), "fee", nonnegative=True)
        tax = _nullable_decimal(value.get("tax"), "tax", nonnegative=True)
        net_pnl = _nullable_decimal(value.get("net_pnl"), "net_pnl")
        exit_episode_id = self._episode_reference(
            value.get("exit_episode_id"),
            stock_code,
            field="exit_episode_id",
            episode_lookup=episode_lookup,
        )
        owner_policy = _text(value.get("canonical_owner_policy"))
        if owner_policy != CANONICAL_OWNER_POLICY:
            raise ValueError("canonical_owner_policy must be ENTRY_EPISODE")
        raw_allocations = value.get("allocations")
        if not isinstance(raw_allocations, list) or not raw_allocations:
            raise ValueError("performance event requires at least one allocation")
        allocations = tuple(
            self._allocation_from_dict(
                item,
                stock_code,
                episode_lookup=episode_lookup,
            )
            for item in raw_allocations
        )
        self._validate_allocations(
            allocations,
            quantity=quantity,
            realized_cost_basis=realized_cost_basis,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
        )
        recorded_at = _timestamp(value.get("recorded_at"))
        event = PerformanceEvent(
            schema_version=schema_version,
            performance_event_id=event_id,
            performance_event_key=event_key,
            stock_code=stock_code,
            broker=broker,
            account_number=account_number,
            trade_date=trade_date,
            broker_order_no=broker_order_no,
            execution_identity=execution_identity,
            fill_id=_nullable_text(value.get("fill_id")),
            realization_id=_nullable_text(value.get("realization_id")),
            realized_at=realized_at,
            quantity=quantity,
            realized_cost_basis=_json_number(realized_cost_basis),
            gross_pnl=_json_number(gross_pnl),
            fee=_json_number(fee) if fee is not None else None,
            tax=_json_number(tax) if tax is not None else None,
            net_pnl=_json_number(net_pnl) if net_pnl is not None else None,
            exit_episode_id=exit_episode_id,
            canonical_owner_policy=owner_policy,
            allocations=allocations,
            recorded_at=recorded_at,
        )
        if require_persisted_identity:
            _uuid_text(event.performance_event_id, "performance_event_id")
        return event

    def _allocation_from_dict(
        self,
        value: object,
        stock_code: str,
        episode_lookup: Mapping[str, AssignmentEpisode] | None = None,
    ) -> PerformanceAllocation:
        if not isinstance(value, Mapping):
            raise ValueError("allocation must be an object")
        quantity_value = _decimal(value.get("quantity"), "allocation.quantity", nonnegative=True)
        if quantity_value <= 0 or quantity_value != quantity_value.to_integral_value():
            raise ValueError("allocation.quantity must be a positive integer")
        entry_lot_id = _text(value.get("entry_lot_id"))
        if not entry_lot_id:
            raise ValueError("allocation.entry_lot_id is required")
        return PerformanceAllocation(
            allocation_id=_uuid_text(value.get("allocation_id"), "allocation_id"),
            entry_lot_id=entry_lot_id,
            entry_episode_id=self._episode_reference(
                value.get("entry_episode_id"),
                stock_code,
                field="entry_episode_id",
                episode_lookup=episode_lookup,
            ),
            quantity=int(quantity_value),
            cost_basis=_json_number(_decimal(value.get("cost_basis"), "allocation.cost_basis", nonnegative=True)),
            gross_pnl=_json_number(_decimal(value.get("gross_pnl"), "allocation.gross_pnl")),
            net_pnl=(
                _json_number(net)
                if (net := _nullable_decimal(value.get("net_pnl"), "allocation.net_pnl")) is not None
                else None
            ),
        )

    def _episode_reference(
        self,
        value: object,
        stock_code: str,
        *,
        field: str,
        episode_lookup: Mapping[str, AssignmentEpisode] | None = None,
    ) -> str:
        reference = _text(value)
        if reference == OWNERSHIP_UNRESOLVED:
            return reference
        episode_id = _uuid_text(reference, field)
        episode = (
            episode_lookup.get(episode_id)
            if episode_lookup is not None
            else self.episode_repository.get_episode(episode_id, stock_code=stock_code)
        )
        if episode is None:
            raise ValueError(f"{field} does not reference an existing canonical episode")
        if episode.stock_code != stock_code:
            raise ValueError(f"{field} stock_code mismatch")
        return episode_id

    @staticmethod
    def _validate_allocations(
        allocations: tuple[PerformanceAllocation, ...],
        *,
        quantity: int,
        realized_cost_basis: Decimal,
        gross_pnl: Decimal,
        net_pnl: Decimal | None,
    ) -> None:
        allocation_ids = [item.allocation_id for item in allocations]
        lot_ids = [item.entry_lot_id for item in allocations]
        if len(set(allocation_ids)) != len(allocation_ids):
            raise ValueError("duplicate allocation_id")
        if len(set(lot_ids)) != len(lot_ids):
            raise ValueError("duplicate entry_lot_id in one performance event")
        if sum(item.quantity for item in allocations) != quantity:
            raise ValueError("allocation quantity reconciliation failed")
        if sum(Decimal(str(item.cost_basis)) for item in allocations) != realized_cost_basis:
            raise ValueError("allocation cost_basis reconciliation failed")
        if sum(Decimal(str(item.gross_pnl)) for item in allocations) != gross_pnl:
            raise ValueError("allocation gross_pnl reconciliation failed")
        allocation_nets = [item.net_pnl for item in allocations]
        if net_pnl is None:
            if any(value is not None for value in allocation_nets):
                raise ValueError("allocation net_pnl requires Event net_pnl evidence")
        elif all(value is None for value in allocation_nets):
            # Event-level fee/tax evidence can establish total net PnL without
            # proving an exact allocation across multiple FIFO entry lots.
            pass
        elif any(value is None for value in allocation_nets):
            raise ValueError("allocation net_pnl must be either fully exact or fully unavailable")
        elif sum(Decimal(str(value)) for value in allocation_nets) != net_pnl:
            raise ValueError("allocation net_pnl reconciliation failed")

    def _read_document(
        self,
        path: Path,
        *,
        expected_stock_code: str,
        episode_lookup: Mapping[str, AssignmentEpisode] | None = None,
    ) -> tuple[PerformanceEvent, ...]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"failed to read canonical performance ledger: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("performance ledger document must be an object")
        if _text(data.get("schema_version")) != PERFORMANCE_LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported performance ledger schema_version")
        if self._stock_code(_text(data.get("stock_code"))) != expected_stock_code:
            raise ValueError("performance ledger stock_code mismatch")
        raw_events = data.get("events")
        if not isinstance(raw_events, list):
            raise ValueError("performance ledger events must be a list")
        events = tuple(
            self._event_from_dict(
                item,
                require_persisted_identity=True,
                episode_lookup=episode_lookup,
            )
            for item in raw_events
        )
        self._validate_event_sequence(expected_stock_code, events)
        return events

    @staticmethod
    def _validate_event_sequence(stock_code: str, events: tuple[PerformanceEvent, ...]) -> None:
        ids = [event.performance_event_id for event in events]
        keys = [event.performance_event_key for event in events]
        if any(event.stock_code != stock_code for event in events):
            raise ValueError("performance event stock_code does not match document")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate performance_event_id")
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate performance_event_key")

    def _document(self, stock_code: str, events: tuple[PerformanceEvent, ...]) -> dict[str, object]:
        self._validate_event_sequence(stock_code, events)
        return {
            "schema_version": PERFORMANCE_LEDGER_SCHEMA_VERSION,
            "stock_code": stock_code,
            "events": [event.to_dict() for event in events],
        }

    def _write_document(self, stock_code: str, events: tuple[PerformanceEvent, ...]) -> None:
        path = self.document_path(stock_code)
        document = self._document(stock_code, events)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            staged = self._read_document(temp, expected_stock_code=stock_code)
            if staged != events:
                raise RuntimeError("staged performance ledger read-back does not match")
            os.replace(temp, path)
            verified = self._read_document(path, expected_stock_code=stock_code)
            if verified != events:
                raise RuntimeError("performance ledger read-back does not match")
        finally:
            temp.unlink(missing_ok=True)

    def _find_unique(
        self,
        predicate: Callable[[PerformanceEvent], bool],
        *,
        stock_code: str | None,
        duplicate_error: str,
    ) -> PerformanceEvent | None:
        if stock_code:
            return next((event for event in self.list_events(stock_code) if predicate(event)), None)
        if not self.ledger_root.is_dir():
            return None
        found: PerformanceEvent | None = None
        for directory in sorted(self.ledger_root.iterdir(), key=lambda path: path.name):
            if not directory.is_dir() or not is_valid_stock_code(directory.name):
                continue
            candidate = next((event for event in self.list_events(directory.name) if predicate(event)), None)
            if candidate is None:
                continue
            if found is not None:
                raise ValueError(duplicate_error)
            found = candidate
        return found

    @staticmethod
    def _economic_payload(event: PerformanceEvent) -> dict[str, object]:
        allocations = sorted(
            (
                {
                    "entry_lot_id": item.entry_lot_id,
                    "entry_episode_id": item.entry_episode_id,
                    "quantity": item.quantity,
                    "cost_basis": item.cost_basis,
                    "gross_pnl": item.gross_pnl,
                    "net_pnl": item.net_pnl,
                }
                for item in event.allocations
            ),
            key=lambda item: (str(item["entry_lot_id"]), str(item["entry_episode_id"])),
        )
        return {
            "stock_code": event.stock_code,
            "broker": event.broker,
            "account_number": event.account_number,
            "trade_date": event.trade_date,
            "broker_order_no": event.broker_order_no,
            "execution_identity": event.execution_identity,
            "fill_id": event.fill_id,
            "realization_id": event.realization_id,
            "realized_at": event.realized_at,
            "quantity": event.quantity,
            "realized_cost_basis": event.realized_cost_basis,
            "gross_pnl": event.gross_pnl,
            "fee": event.fee,
            "tax": event.tax,
            "net_pnl": event.net_pnl,
            "exit_episode_id": event.exit_episode_id,
            "canonical_owner_policy": event.canonical_owner_policy,
            "allocations": allocations,
        }

    @staticmethod
    def _stock_code(value: str) -> str:
        code = normalize_stock_code(value)
        if not is_valid_stock_code(code):
            raise ValueError("stock_code is invalid")
        return code
