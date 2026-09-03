# -*- coding: utf-8 -*-
"""Main-window owned orchestration for the isolated Mock Validation domain.

Realtime callbacks only enqueue immutable evidence.  The caller's existing
one-second timer invokes :meth:`process_due_cycles`; it is never a market
sampling timer.  Every accepted trade is drained in arrival order.
"""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from mock_validation_contract import (
    ORDER_CANCEL_PENDING,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    SESSION_CLOSING,
    SESSION_ENDED,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    deterministic_mock_identity,
    normalized_stock_code,
    payload_hash,
)
from mock_validation_indicator_follow_adapter import MockIndicatorFollowRoutineAdapter
from mock_validation_market_data import (
    MockMarketSnapshot,
    MockOrderbookSnapshot,
    MockTradeSnapshot,
    MockValidationMarketDataStore,
    normalize_mock_trade_snapshot,
)
from mock_validation_operation_lifecycle import (
    CLOSE_CARRYOVER,
    CLOSE_CURRENT_PRICE,
    CLOSE_MARKET,
    MockOperationLifecycleCoordinator,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import MockExecutionPolicy, MockVirtualExecutionEngine


_LIVE_ORDER_STATES = {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}
_ACTIVE_SESSION_STATES = {
    SESSION_WAITING,
    SESSION_RUNNING,
    SESSION_CLOSING,
    SESSION_REVIEW_STOPPED,
}


@dataclass(frozen=True)
class MockMarketEvidence:
    arrival_sequence: int
    kind: str
    stock_code: str
    market_sequence: int
    received_at: str
    connection_epoch: int
    login_session_id: str
    content_identity: str
    payload: MockOrderbookSnapshot | MockTradeSnapshot


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    try:
        parsed = datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _clock_seconds(text: Any) -> int | None:
    parts = str(text or "").strip().split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 3600 + minute * 60 + second


def _close_method(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    if text in {"MARKET", "MARKET_ORDER", "시장가"}:
        return CLOSE_MARKET
    if text in {"CURRENT_PRICE", "현재가", "현재가즉시"}:
        return CLOSE_CURRENT_PRICE
    if text in {"CARRYOVER", "LONG_HOLD", "이월", "장기보유"}:
        return CLOSE_CARRYOVER
    raise MockValidationError("MOCK_CLOSE_POLICY_METHOD_UNSUPPORTED")


class MockValidationHost:
    """Stock-isolated Mock runtime host owned by one MainWindow."""

    def __init__(
        self,
        api: Any,
        *,
        project_root: str | Path,
        repository: MockValidationRepository | None = None,
        now_factory: Callable[[], datetime] | None = None,
        projection_changed: Callable[[], None] | None = None,
        operation_policy_provider: Callable[[], dict[str, Any]] | None = None,
        candles_provider: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
        max_buffered_evidence_per_stock: int = 10000,
    ) -> None:
        self.api = api
        self.project_root = Path(project_root).resolve()
        self.repository = repository or MockValidationRepository(
            self.project_root / "mock_validation",
            project_root=self.project_root,
        )
        self._now = now_factory or (lambda: datetime.now().astimezone())
        self._projection_changed = projection_changed
        self._operation_policy_provider = operation_policy_provider or self._read_operation_policy
        self._candles_provider = candles_provider or self._read_candles
        self.max_buffered_evidence_per_stock = max(1, int(max_buffered_evidence_per_stock))
        self.session_service = MockValidationSessionService(
            self.repository,
            now_factory=lambda: self._now().isoformat(timespec="microseconds"),
        )
        self.market_store = MockValidationMarketDataStore()
        self.engine = MockVirtualExecutionEngine(self.repository, now_factory=self._now)
        self.routine_adapter = MockIndicatorFollowRoutineAdapter(
            self.repository, self.engine, now_factory=self._now
        )
        self.lifecycle = MockOperationLifecycleCoordinator(
            self.repository, self.engine, now_factory=self._now
        )
        self._buffers: dict[str, deque[MockMarketEvidence]] = defaultdict(deque)
        self._arrival_by_stock: dict[str, int] = defaultdict(int)
        self._last_received: dict[tuple[str, str], tuple[int, str]] = {}
        self._last_processed_arrival: dict[str, int] = defaultdict(int)
        self._integrity_errors: dict[str, str] = {}
        self._processing = False
        self._connected = False
        self._disposed = False
        self._projection_hash = ""
        self.connect()
        self.sync_registration()
        self._publish_projection_if_changed()

    def _read_operation_policy(self) -> dict[str, Any]:
        try:
            value = json.loads((self.project_root / "operation_policy.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return deepcopy(value) if isinstance(value, dict) else {}

    @staticmethod
    def _read_candles(document: dict[str, Any]) -> list[dict[str, Any]]:
        reference = document.get("reference_snapshot", {}).get("stock_identity_reference", {})
        path_text = reference.get("stock_path", "") if isinstance(reference, dict) else ""
        if not str(path_text or "").strip():
            return []
        path = Path(str(path_text)) / "candles.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(value, list):
            return [deepcopy(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            rows = value.get("candles")
            if isinstance(rows, list):
                return [deepcopy(item) for item in rows if isinstance(item, dict)]
        return []

    def connect(self) -> None:
        if self._connected or self._disposed or self.api is None:
            return
        for name, callback in (
            ("mock_orderbook_received", self.accept_orderbook),
            ("realtime_shadow_tick_received", self.accept_trade),
            ("login_state_changed", self._on_login_state_changed),
        ):
            signal = getattr(self.api, name, None)
            connector = getattr(signal, "connect", None)
            if callable(connector):
                connector(callback)
        self._connected = True

    def dispose(self) -> None:
        if self._disposed:
            return
        if self._connected and self.api is not None:
            for name, callback in (
                ("mock_orderbook_received", self.accept_orderbook),
                ("realtime_shadow_tick_received", self.accept_trade),
                ("login_state_changed", self._on_login_state_changed),
            ):
                signal = getattr(self.api, name, None)
                disconnect = getattr(signal, "disconnect", None)
                if callable(disconnect):
                    try:
                        disconnect(callback)
                    except (TypeError, RuntimeError):
                        pass
        clear = getattr(self.api, "clear_mock_orderbook_registration", None)
        if callable(clear):
            try:
                clear(reason="MOCK_HOST_DISPOSED")
            except Exception:
                pass
        self._connected = False
        self._disposed = True
        self._buffers.clear()

    def _on_login_state_changed(self, _payload: Any = None) -> None:
        self._buffers.clear()
        self._last_received.clear()
        self._last_processed_arrival.clear()
        self.sync_registration()

    def current_session_ids(self) -> dict[str, str]:
        return self.repository.current_session_ids()

    def _current_sessions_with_errors(
        self,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[tuple[str, str], ...]]:
        documents: list[dict[str, Any]] = []
        errors: list[tuple[str, str]] = []
        for stock_code, session_id in sorted(self.current_session_ids().items()):
            try:
                documents.append(self.repository.read_session(session_id))
            except Exception as exc:
                errors.append((stock_code, str(exc) or type(exc).__name__))
        return tuple(documents), tuple(errors)

    def current_sessions(self) -> tuple[dict[str, Any], ...]:
        documents, _errors = self._current_sessions_with_errors()
        return documents

    def current_stock_codes(self) -> frozenset[str]:
        return frozenset(self.current_session_ids())

    def current_session(self, stock_code: Any) -> dict[str, Any] | None:
        try:
            session_id = self.repository.current_session_id(normalized_stock_code(stock_code))
        except MockValidationError:
            return None
        return self.repository.read_session(session_id) if session_id else None

    def sync_registration(self) -> dict[str, Any]:
        targets = tuple(sorted(self.current_stock_codes()))
        sync = getattr(self.api, "sync_mock_orderbook_registration", None)
        if not callable(sync):
            self.market_store.apply_registration_snapshot({"active": False})
            return {"ok": False, "active": False, "reason_code": "MOCK_BROKER_API_UNAVAILABLE"}
        try:
            result = sync(targets)
        except Exception as exc:
            self.market_store.apply_registration_snapshot({"active": False})
            return {"ok": False, "active": False, "reason_code": "MOCK_REGISTRATION_FAILED", "error": str(exc)}
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        self.market_store.apply_registration_snapshot(snapshot or {"active": False})
        return result if isinstance(result, dict) else {"ok": False, "active": False}

    def _append_evidence(
        self,
        *,
        kind: str,
        stock_code: str,
        market_sequence: int,
        received_at: str,
        connection_epoch: int,
        login_session_id: str,
        content_identity: str,
        payload: MockOrderbookSnapshot | MockTradeSnapshot,
    ) -> bool:
        if stock_code not in self.current_stock_codes():
            return False
        key = (stock_code, kind)
        previous = self._last_received.get(key)
        if previous is not None:
            previous_sequence, previous_identity = previous
            if market_sequence < previous_sequence:
                self._integrity_errors[stock_code] = "MOCK_EVIDENCE_SEQUENCE_REGRESSION"
                return False
            if market_sequence == previous_sequence:
                if content_identity != previous_identity:
                    self._integrity_errors[stock_code] = "MOCK_EVIDENCE_SEQUENCE_CONFLICT"
                return content_identity == previous_identity
        self._last_received[key] = (market_sequence, content_identity)
        self._arrival_by_stock[stock_code] += 1
        evidence = MockMarketEvidence(
            arrival_sequence=self._arrival_by_stock[stock_code],
            kind=kind,
            stock_code=stock_code,
            market_sequence=market_sequence,
            received_at=received_at,
            connection_epoch=connection_epoch,
            login_session_id=login_session_id,
            content_identity=content_identity,
            payload=payload,
        )
        queue = self._buffers[stock_code]
        queue.append(evidence)
        if len(queue) > self.max_buffered_evidence_per_stock:
            self._integrity_errors[stock_code] = "MOCK_EVIDENCE_BACKLOG_OVERFLOW"
        return True

    def _sequence_acceptance(
        self,
        stock_code: str,
        kind: str,
        market_sequence: int,
        content_identity: str,
    ) -> str:
        """Inspect broker sequence before the latest-value store can discard it."""

        previous = self._last_received.get((stock_code, kind))
        if previous is None:
            return "NEW"
        previous_sequence, previous_identity = previous
        if market_sequence < previous_sequence:
            self._integrity_errors[stock_code] = "MOCK_EVIDENCE_SEQUENCE_REGRESSION"
            return "INVALID"
        if market_sequence == previous_sequence:
            if content_identity != previous_identity:
                self._integrity_errors[stock_code] = "MOCK_EVIDENCE_SEQUENCE_CONFLICT"
                return "INVALID"
            return "DUPLICATE"
        return "NEW"

    def accept_orderbook(self, snapshot: Any) -> bool:
        if not isinstance(snapshot, MockOrderbookSnapshot):
            return False
        acceptance = self._sequence_acceptance(
            snapshot.stock_code,
            "ORDERBOOK",
            snapshot.receive_sequence,
            snapshot.snapshot_identity,
        )
        if acceptance != "NEW":
            return False
        if not self.market_store.accept_orderbook(snapshot):
            return False
        return self._append_evidence(
            kind="ORDERBOOK",
            stock_code=snapshot.stock_code,
            market_sequence=snapshot.receive_sequence,
            received_at=snapshot.received_at,
            connection_epoch=snapshot.connection_epoch,
            login_session_id=snapshot.login_session_id,
            content_identity=snapshot.snapshot_identity,
            payload=snapshot,
        )

    def accept_trade(self, payload: Any) -> bool:
        if not isinstance(payload, Mapping):
            return False
        snapshot = normalize_mock_trade_snapshot(payload)
        if snapshot is None:
            return False
        acceptance = self._sequence_acceptance(
            snapshot.stock_code,
            "TRADE",
            snapshot.receive_sequence,
            snapshot.snapshot_identity,
        )
        if acceptance != "NEW":
            return False
        if not self.market_store.accept_trade(payload):
            return False
        return self._append_evidence(
            kind="TRADE",
            stock_code=snapshot.stock_code,
            market_sequence=snapshot.receive_sequence,
            received_at=snapshot.received_at,
            connection_epoch=snapshot.connection_epoch,
            login_session_id=snapshot.login_session_id,
            content_identity=snapshot.snapshot_identity,
            payload=snapshot,
        )

    def buffered_evidence(self, stock_code: Any) -> tuple[MockMarketEvidence, ...]:
        try:
            code = normalized_stock_code(stock_code)
        except MockValidationError:
            return ()
        return tuple(self._buffers.get(code, ()))

    def _policy(self) -> MockExecutionPolicy | None:
        snapshot_getter = getattr(self.api, "mock_orderbook_registration_snapshot", None)
        snapshot = snapshot_getter() if callable(snapshot_getter) else None
        if snapshot is None:
            return None
        active = getattr(snapshot, "active", None)
        epoch = getattr(snapshot, "connection_epoch", 0)
        session_id = getattr(snapshot, "login_session_id", "")
        if isinstance(snapshot, dict):
            active = snapshot.get("active")
            epoch = snapshot.get("connection_epoch", 0)
            session_id = snapshot.get("login_session_id", "")
        if active is not True or int(epoch or 0) <= 0 or not str(session_id or "").strip():
            return None
        return MockExecutionPolicy(int(epoch), str(session_id), 2.0, 2.0)

    def _market_for_orderbook(self, snapshot: MockOrderbookSnapshot) -> MockMarketSnapshot:
        trade = self.market_store.latest_trade(snapshot.stock_code)
        return MockMarketSnapshot(
            snapshot.stock_code,
            snapshot,
            trade,
            "MMK-" + payload_hash({
                "orderbook": snapshot.snapshot_identity,
                "trade": trade.snapshot_identity if trade is not None else "",
            }),
        )

    def _drain_market_evidence(
        self,
        document: dict[str, Any],
        policy: MockExecutionPolicy | None,
    ) -> None:
        stock_code = document["session"]["stock_code"]
        queue = self._buffers.get(stock_code)
        if not queue:
            return
        expected = self._last_processed_arrival[stock_code] + 1
        allow_closing = document["session"]["state"] == SESSION_CLOSING
        while queue:
            evidence = queue.popleft()
            if evidence.arrival_sequence != expected:
                raise MockValidationError("MOCK_EVIDENCE_ARRIVAL_GAP")
            expected += 1
            self._last_processed_arrival[stock_code] = evidence.arrival_sequence
            if policy is None:
                continue
            current = self.repository.read_session(document["session"]["validation_session_id"])
            live_orders = [
                deepcopy(item)
                for item in current.get("orders", ())
                if item.get("state") in _LIVE_ORDER_STATES
            ]
            for order in live_orders:
                command = deterministic_mock_identity(
                    "MC",
                    current["session"]["validation_session_id"],
                    order["mock_order_id"],
                    evidence.content_identity,
                    evidence.kind,
                )
                if evidence.kind == "ORDERBOOK":
                    self.engine.process_orderbook(
                        current["session"]["validation_session_id"],
                        order["mock_order_id"],
                        market=self._market_for_orderbook(evidence.payload),
                        policy=policy,
                        command_id=command,
                        allow_closing=allow_closing,
                    )
                else:
                    self.engine.process_trade(
                        current["session"]["validation_session_id"],
                        order["mock_order_id"],
                        trade=evidence.payload,
                        policy=policy,
                        command_id=command,
                        allow_closing=allow_closing,
                    )

    def _review_error(self, document: dict[str, Any], reason: str) -> None:
        session_id = document["session"]["validation_session_id"]
        if document["session"].get("state") in {SESSION_REVIEW_STOPPED, SESSION_ENDED}:
            return
        instance_ids = sorted(document.get("instance_execution", {}))
        if not instance_ids:
            return
        command = deterministic_mock_identity("MC", session_id, reason, "HOST_REVIEW")
        self.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id=instance_ids[0],
            reason_code=reason,
            reason=reason,
            command_id=command,
        )

    def _operation_snapshot(self) -> dict[str, Any]:
        source = self._operation_policy_provider()
        policy = deepcopy(source) if isinstance(source, dict) else {}
        review = policy.get("review_policy")
        policy["long_hold_enabled"] = bool(
            isinstance(review, dict) and review.get("long_term_holding_enabled") is True
        )
        return policy

    def start_stock_operation(self, stock_code: Any, *, as_of: datetime | None = None) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        result = self.lifecycle.start_stock_operation(
            document["session"]["validation_session_id"],
            trading_date=now.date(),
            as_of=now,
            operation_policy_snapshot={
                **self._operation_snapshot(),
                "mock_tax_enabled": document["session"]["mock_tax_enabled"],
                "mock_tax_rate": document["session"]["mock_tax_rate"],
            },
            command_id=deterministic_mock_identity(
                "MC", document["session"]["validation_session_id"], now.date(), "OPERATION_START"
            ),
        )
        self._publish_projection_if_changed()
        return result

    def request_early_close(
        self, stock_code: Any, *, method: str = CLOSE_MARKET, as_of: datetime | None = None
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        result = self.lifecycle.request_early_close(
            document["session"]["validation_session_id"],
            method=method,
            reason="사용자 조기마감",
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", document["session"]["validation_session_id"], now.date(), "EARLY_CLOSE"
            ),
        )
        self._publish_projection_if_changed()
        return result

    def request_immediate_liquidation(
        self, stock_code: Any, *, as_of: datetime | None = None
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        root = document.get("mock_operation_lifecycle", {})
        current = root.get("current") if isinstance(root, dict) else None
        operation_id = current.get("operation_session_id", "") if isinstance(current, dict) else ""
        result = self.lifecycle.request_immediate_liquidation(
            document["session"]["validation_session_id"],
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", document["session"]["validation_session_id"], operation_id, "IMMEDIATE_LIQUIDATION"
            ),
            method=CLOSE_MARKET,
            reason="사용자 즉시청산",
        )
        self._publish_projection_if_changed()
        return result

    @staticmethod
    def _operation(document: dict[str, Any]) -> dict[str, Any] | None:
        root = document.get("mock_operation_lifecycle")
        current = root.get("current") if isinstance(root, dict) else None
        return current if isinstance(current, dict) else None

    def _trigger_due_close(self, document: dict[str, Any], now: datetime) -> None:
        operation = self._operation(document)
        if operation is None or operation.get("state") != "RUNNING":
            return
        snapshot = operation.get("operation_policy_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        regular = snapshot.get("regular_market")
        regular = regular if isinstance(regular, dict) else {}
        end_seconds = _clock_seconds(regular.get("end_time"))
        if end_seconds is None:
            return
        liquidation = snapshot.get("liquidation")
        liquidation = liquidation if isinstance(liquidation, dict) else {}
        method = _close_method(liquidation.get("method", "시장가"))
        try:
            minutes = max(0, int(liquidation.get("minutes_before_regular_close", 0) or 0))
        except (TypeError, ValueError):
            raise MockValidationError("MOCK_LIQUIDATION_MINUTES_INVALID")
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        session_id = document["session"]["validation_session_id"]
        long_hold = bool(snapshot.get("long_hold_enabled", False))
        auto_enabled = snapshot.get("auto_close_enabled", True) is not False
        if auto_enabled and now_seconds >= max(0, end_seconds - minutes * 60) and now_seconds < end_seconds:
            self.lifecycle.request_auto_close(
                session_id,
                method=method,
                reason="자동마감 시각 도달",
                as_of=now,
                command_id=deterministic_mock_identity("MC", session_id, operation["trading_date"], "AUTO_CLOSE"),
                long_hold_enabled=long_hold,
            )
        elif now_seconds >= end_seconds:
            self.lifecycle.request_normal_close(
                session_id,
                method=method,
                reason="정규 운영 종료시각 도달",
                as_of=now,
                command_id=deterministic_mock_identity("MC", session_id, operation["trading_date"], "NORMAL_CLOSE"),
                long_hold_enabled=long_hold,
            )

    def _process_stock(self, document: dict[str, Any], now: datetime) -> None:
        stock_code = document["session"]["stock_code"]
        reason = self._integrity_errors.pop(stock_code, "")
        if reason:
            self._review_error(document, reason)
            return
        state = document["session"]["state"]
        if state in {SESSION_WAITING, SESSION_REVIEW_STOPPED, SESSION_ENDED}:
            # Keep the transport buffer bounded, but never advance an order in
            # a state whose stock-level progression is stopped.
            self._drain_market_evidence(document, None)
            return
        policy = self._policy()
        self._drain_market_evidence(document, policy)
        session_id = document["session"]["validation_session_id"]
        market = self.market_store.market_snapshot(stock_code)
        if state == SESSION_RUNNING:
            if policy is not None:
                candles = self._candles_provider(document)
                second_identity = now.replace(microsecond=0).isoformat()
                for instance_id in sorted(document.get("instance_execution", {})):
                    self.routine_adapter.evaluate_cycle(
                        session_id,
                        routine_instance_id=instance_id,
                        candles=candles,
                        market=market,
                        policy=policy,
                        evaluation_cycle_id=deterministic_mock_identity(
                            "MC", session_id, instance_id, second_identity, "ROUTINE"
                        ),
                        evaluated_at=now,
                    )
            refreshed = self.repository.read_session(session_id)
            self._trigger_due_close(refreshed, now)
            return
        if state == SESSION_CLOSING and policy is not None:
            operation = self._operation(document)
            snapshot = operation.get("operation_policy_snapshot", {}) if operation else {}
            regular = snapshot.get("regular_market", {}) if isinstance(snapshot, dict) else {}
            end_seconds = _clock_seconds(regular.get("end_time")) if isinstance(regular, dict) else None
            now_seconds = now.hour * 3600 + now.minute * 60 + now.second
            self.lifecycle.process_mock_operation_cycle(
                session_id,
                lifecycle_cycle_id=deterministic_mock_identity(
                    "MC", session_id, now.replace(microsecond=0).isoformat(), "LIFECYCLE"
                ),
                as_of=now,
                market=market,
                policy=policy,
                final_close_boundary=bool(end_seconds is not None and now_seconds >= end_seconds),
            )

    def process_due_cycles(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        if self._disposed:
            return {"processed": 0, "errors": (), "reason": "MOCK_HOST_DISPOSED"}
        if self._processing:
            return {"processed": 0, "errors": (), "reason": "MOCK_HOST_REENTRY_BLOCKED"}
        self._processing = True
        errors: list[tuple[str, str]] = []
        processed = 0
        try:
            self.sync_registration()
            now = as_of or self._now()
            documents, read_errors = self._current_sessions_with_errors()
            errors.extend(read_errors)
            for document in documents:
                stock_code = document["session"]["stock_code"]
                try:
                    self._process_stock(document, now)
                    processed += 1
                except Exception as exc:
                    reason = str(exc) or type(exc).__name__
                    errors.append((stock_code, reason))
                    try:
                        self._review_error(self.repository.read_session(document["session"]["validation_session_id"]), reason)
                    except Exception:
                        pass
            self._publish_projection_if_changed()
            return {"processed": processed, "errors": tuple(errors), "reason": ""}
        finally:
            self._processing = False

    def projection_hash(self) -> str:
        sessions = []
        for document in self.current_sessions():
            sessions.append({
                "revision": document.get("revision"),
                "session": document.get("session"),
                "review": document.get("review"),
                "positions": document.get("positions"),
                "pnl": document.get("pnl"),
                "operation": self._operation(document),
            })
        return payload_hash(sessions)

    def _publish_projection_if_changed(self) -> bool:
        current = self.projection_hash()
        if current == self._projection_hash:
            return False
        self._projection_hash = current
        if callable(self._projection_changed):
            self._projection_changed()
        return True


__all__ = ["MockMarketEvidence", "MockValidationHost"]
