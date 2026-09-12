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

from ats_session_contract import PROGRAM_SESSION_ID, VALID_SESSION_KEYS

from candle_timeframe_aggregation import candle_session_windows
from gui_ats_utils import (
    auto_trade_operation_activation_phase,
    auto_trade_operation_session_phase,
)
from close_liquidation_transition_service import (
    regular_end_pending_order_cancel_boundary_seconds,
)
from mock_validation_contract import (
    INSTANCE_ERROR,
    INSTANCE_VALIDATION_STOPPED,
    ORDER_CANCEL_PENDING,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    SESSION_CLOSING,
    SESSION_ENDED,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    instance_effective_settings,
    mock_instance_active_effective_settings,
    mock_instance_pre_start_editable,
    new_mock_identity,
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
    CLOSE_PROFIT_LOSS,
    CLOSE_ROUTINE,
    MockOperationLifecycleCoordinator,
    OPERATION_CLOSING,
    instance_operation_state,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import (
    MockValidationSessionService,
    mock_instance_error_recovery_reset_allowed,
)
from mock_validation_ui_projection import mock_monitoring_tree_projection
from mock_validation_virtual_execution import MockExecutionPolicy, MockVirtualExecutionEngine
from routine_instance_registry import routine_instance_by_id


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
    if text in {"MARKET", "MARKET_ORDER", "시장가", "시장가즉시"}:
        return CLOSE_MARKET
    if text in {"CURRENT_PRICE", "현재가", "현재가즉시"}:
        return CLOSE_CURRENT_PRICE
    if text in {"CARRYOVER", "LONG_HOLD", "이월", "장기보유"}:
        return CLOSE_CARRYOVER
    if text in {"ROUTINE", "ROUTINE_SIGNAL", "루틴", "루틴마감", "루틴매도신호"}:
        return CLOSE_ROUTINE
    if text in {"PROFIT_LOSS", "PROFIT/LOSS", "손/익절", "익절/손절"}:
        return CLOSE_PROFIT_LOSS
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
        candles_provider: Callable[..., Any] | None = None,
        candle_observation_updater: Callable[[object], Any] | None = None,
        max_buffered_evidence_per_stock: int = 10000,
        program_session_id: str | None = None,
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
        self._candles_provider = candles_provider
        self._candle_observation_updater = candle_observation_updater
        self.max_buffered_evidence_per_stock = max(1, int(max_buffered_evidence_per_stock))
        self._program_session_id = clean_text(
            program_session_id or PROGRAM_SESSION_ID
        )
        self.session_service = MockValidationSessionService(
            self.repository,
            now_factory=lambda: self._now().isoformat(timespec="microseconds"),
            program_session_id=self._program_session_id,
        )
        self._restart_recovery_result = (
            self.session_service.stop_active_instances_for_application_boundary(
                source="APPLICATION_RESTART_RECOVERY"
            )
        )
        self._restart_recovery_blocked_session_ids = frozenset(
            clean_text(item.get("validation_session_id"))
            for item in self._restart_recovery_result.get("errors", ())
            if clean_text(item.get("validation_session_id"))
        )
        self.market_store = MockValidationMarketDataStore()
        self.engine = MockVirtualExecutionEngine(self.repository, now_factory=self._now)
        self.routine_adapter = MockIndicatorFollowRoutineAdapter(
            self.repository, self.engine, now_factory=self._now
        )
        self.lifecycle = MockOperationLifecycleCoordinator(
            self.repository,
            self.engine,
            now_factory=self._now,
            program_session_id=self._program_session_id,
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

    def server_authenticated(self) -> bool:
        """Fresh-read the broker login fact without mirroring Production state."""

        checker = getattr(self.api, "is_connected", None)
        if not callable(checker):
            return False
        try:
            return checker() is True
        except Exception:
            return False

    def _require_server_authenticated(self) -> None:
        if not self.server_authenticated():
            raise MockValidationError("SERVER_NOT_CONNECTED")

    def _latest_applied_rules(
        self,
        routine_instance_id: str,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instance = routine_instance_by_id(
            str(routine_instance_id or "").strip(),
            project_root=self.project_root,
        )
        if instance is None or instance.rules_path is None:
            reference_rules = self._operation_rules(document or {}, routine_instance_id)
            if reference_rules:
                return reference_rules
            raise MockValidationError("MOCK_ROUTINE_RULES_SNAPSHOT_MISSING")
        try:
            rules = json.loads(Path(instance.rules_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MockValidationError("MOCK_ROUTINE_RULES_SNAPSHOT_MISSING") from exc
        if not isinstance(rules, dict):
            raise MockValidationError("MOCK_ROUTINE_RULES_SNAPSHOT_MISSING")
        return deepcopy(rules)

    @staticmethod
    def _operation_rules(document: dict[str, Any], instance_id: str) -> dict[str, Any]:
        operation = instance_operation_state(document, instance_id)
        snapshot = (
            operation.get("operation_policy_snapshot")
            if isinstance(operation, dict)
            else None
        )
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        direct = snapshot.get("mock_instance_rules_snapshot")
        if isinstance(direct, dict):
            return deepcopy(direct)
        by_instance = snapshot.get("mock_rules_snapshot_by_instance")
        if isinstance(by_instance, dict) and isinstance(by_instance.get(instance_id), dict):
            return deepcopy(by_instance[instance_id])
        reference = next(
            (
                item
                for item in document.get("reference_snapshot", {}).get("routine_instances", ())
                if isinstance(item, dict)
                and item.get("routine_instance_id") == instance_id
            ),
            {},
        )
        rules = reference.get("rules_snapshot") if isinstance(reference, dict) else {}
        return deepcopy(rules) if isinstance(rules, dict) else {}

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
        if callable(self._candle_observation_updater):
            try:
                self._candle_observation_updater(())
            except Exception:
                pass
        self._connected = False
        self._disposed = True
        self._buffers.clear()

    def shutdown(self) -> dict[str, Any]:
        if self._disposed:
            return {"source": "APPLICATION_SHUTDOWN", "stopped": (), "errors": ()}
        result = self.session_service.stop_active_instances_for_application_boundary(
            source="APPLICATION_SHUTDOWN"
        )
        self._restart_recovery_blocked_session_ids = frozenset(
            {
                *self._restart_recovery_blocked_session_ids,
                *(
                    clean_text(item.get("validation_session_id"))
                    for item in result.get("errors", ())
                    if clean_text(item.get("validation_session_id"))
                ),
            }
        )
        self.dispose()
        return result

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
        if callable(self._candle_observation_updater):
            requirements: list[dict[str, Any]] = []
            for session_id in self.current_session_ids().values():
                try:
                    document = self.repository.read_session(session_id)
                except Exception:
                    continue
                code = str(document.get("session", {}).get("stock_code") or "").strip()
                for reference in document.get("reference_snapshot", {}).get("routine_instances", ()):
                    if not isinstance(reference, dict):
                        continue
                    rules = reference.get("rules_snapshot")
                    if not isinstance(rules, dict):
                        continue
                    requirements.append({
                        "stock_code": code,
                        "rules": rules,
                        "projection_request": self.routine_adapter.market_bar_projection_request(rules),
                    })
            self._candle_observation_updater(requirements or targets)
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

    def _selected_candle_sessions(self, settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        policy = self._operation_policy_provider()
        policy = policy if isinstance(policy, dict) else {}
        selected = set(
            settings.get("manual_ats", {}).get("selected_sessions", ())
            if isinstance(settings.get("manual_ats"), dict)
            else ()
        )
        return candle_session_windows(policy, selected)

    def _instance_candle_projection(
        self,
        document: dict[str, Any],
        *,
        instance_id: str,
        rules: dict[str, Any],
        settings: dict[str, Any],
        now: datetime,
        projection_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not callable(self._candles_provider):
            return {"available": False, "candles": [], "availability_state": "SOURCE_UNAVAILABLE", "reason": "봉데이터 부족"}
        request = (
            deepcopy(projection_request)
            if isinstance(projection_request, dict)
            else self.routine_adapter.market_bar_projection_request(rules)
        )
        stock_code = document["session"]["stock_code"]
        value = self._candles_provider(
            stock_code=stock_code,
            rules=rules,
            projection_request=request,
            as_of=now,
            session_windows=self._selected_candle_sessions(settings),
            consumer_scope="MOCK",
        )
        if isinstance(value, dict):
            return deepcopy(value)
        if isinstance(value, (list, tuple)):
            return {"available": bool(value), "candles": [deepcopy(item) for item in value if isinstance(item, dict)], "reason": "" if value else "봉데이터 부족"}
        return {"available": False, "candles": [], "reason": "봉데이터 부족"}

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
                and current.get("instance_execution", {})
                .get(item.get("routine_instance_id"), {})
                .get("state")
                not in {INSTANCE_ERROR, INSTANCE_VALIDATION_STOPPED}
            ]
            for order in live_orders:
                command = deterministic_mock_identity(
                    "MC",
                    current["session"]["validation_session_id"],
                    order["mock_order_id"],
                    evidence.content_identity,
                    evidence.kind,
                )
                instance_state = (
                    current.get("instance_execution", {})
                    .get(order.get("routine_instance_id"), {})
                    .get("state")
                )
                order_allow_closing = allow_closing or instance_state == SESSION_CLOSING
                if evidence.kind == "ORDERBOOK":
                    self.engine.process_orderbook(
                        current["session"]["validation_session_id"],
                        order["mock_order_id"],
                        market=self._market_for_orderbook(evidence.payload),
                        policy=policy,
                        command_id=command,
                        allow_closing=order_allow_closing,
                    )
                else:
                    self.engine.process_trade(
                        current["session"]["validation_session_id"],
                        order["mock_order_id"],
                        trade=evidence.payload,
                        policy=policy,
                        command_id=command,
                        allow_closing=order_allow_closing,
                    )

    def _review_error(
        self,
        document: dict[str, Any],
        reason: str,
        *,
        routine_instance_ids: tuple[str, ...] | None = None,
    ) -> None:
        """Isolate an execution error to its affected Mock Instance(s)."""

        session_id = document["session"]["validation_session_id"]
        if document["session"].get("state") == SESSION_ENDED:
            return
        instance_ids = list(
            routine_instance_ids
            if routine_instance_ids is not None
            else tuple(sorted(document.get("instance_execution", {})))
        )
        if not instance_ids:
            return
        for instance_id in instance_ids:
            command = deterministic_mock_identity(
                "MC", session_id, instance_id, reason, "HOST_INSTANCE_ERROR"
            )
            self.session_service.stop_for_instance_error(
                session_id,
                source_routine_instance_id=instance_id,
                reason_code=reason,
                reason=reason,
                command_id=command,
                source_category="MOCK_HOST",
            )

    def _operation_snapshot(self) -> dict[str, Any]:
        source = self._operation_policy_provider()
        policy = deepcopy(source) if isinstance(source, dict) else {}
        review_policy = policy.get("review_policy")
        review_policy = review_policy if isinstance(review_policy, dict) else {}
        policy["long_hold_enabled"] = bool(
            review_policy.get(
                "long_term_holding_enabled",
                policy.get("long_hold_enabled", True),
            )
        )
        return policy

    @staticmethod
    def _enabled_extra_session_keys(policy: dict[str, Any]) -> tuple[str, ...]:
        sessions = policy.get("extra_sessions")
        if not isinstance(sessions, list):
            return VALID_SESSION_KEYS
        return tuple(
            key
            for index, key in enumerate(VALID_SESSION_KEYS)
            if index >= len(sessions)
            or not isinstance(sessions[index], dict)
            or bool(sessions[index].get("enabled", True))
        )

    def _instance_effective_settings(
        self,
        document: dict[str, Any],
        instance_id: str,
        *,
        operation_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = instance_effective_settings(document, instance_id)
        raw_by_instance = document.get("effective_settings_by_instance")
        raw = (
            raw_by_instance.get(instance_id)
            if isinstance(raw_by_instance, dict)
            else None
        )
        if (
            isinstance(raw, dict)
            and str(raw.get("operation_mode") or "").strip().upper() == "MANUAL_ATS"
            and "manual_ats" not in raw
        ):
            policy = (
                operation_policy
                if isinstance(operation_policy, dict)
                else self._operation_policy_provider()
            )
            settings["manual_ats"]["selected_sessions"] = list(
                self._enabled_extra_session_keys(
                    policy if isinstance(policy, dict) else {}
                )
            )
        return settings

    def _instance_lifecycle_start_available(
        self,
        document: dict[str, Any],
        instance_id: str,
    ) -> bool:
        execution = document.get("instance_execution", {}).get(instance_id)
        if not isinstance(execution, dict):
            return False
        operation = instance_operation_state(document, instance_id)
        operation_state = operation.get("state", "") if operation else ""
        session_state = document["session"].get("state", "")
        root = document.get("mock_operation_lifecycle")
        stock_operation = root.get("current") if isinstance(root, dict) else None
        stock_operation_active = (
            isinstance(stock_operation, dict)
            and stock_operation.get("state") in {"RUNNING", "CLOSING"}
        )
        ended = session_state == SESSION_ENDED or execution.get("state") == SESSION_ENDED
        return bool(
            not ended
            and document["session"]["validation_session_id"]
            not in self._restart_recovery_blocked_session_ids
            and not stock_operation_active
            and (
                mock_instance_pre_start_editable(document, instance_id)
                or (
                    session_state == SESSION_RUNNING
                    and execution.get("state")
                    in {SESSION_WAITING, INSTANCE_VALIDATION_STOPPED}
                )
            )
            and operation_state not in {"RUNNING", "CLOSING", "ENDED"}
        )

    def _instance_start_admission(
        self,
        document: dict[str, Any],
        instance_id: str,
        now: datetime,
        *,
        operation_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._instance_lifecycle_start_available(document, instance_id):
            return {"allowed": True, "reason": "", "session_phase": {}}
        policy = (
            operation_policy
            if isinstance(operation_policy, dict)
            else self._operation_snapshot()
        )
        settings = self._instance_effective_settings(
            document,
            instance_id,
            operation_policy=policy,
        )
        config, state, ats_reader = self._mock_operation_phase_inputs(settings, policy)
        session_phase = auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now,
            operation_policy_reader=lambda: policy,
            ats_session_reader=ats_reader,
        )
        final_ended = (
            str(session_phase.get("phase") or "").strip().upper()
            == "FINAL_SESSION_ENDED"
        )
        return {
            "allowed": not final_ended,
            "reason": "FINAL_SESSION_ENDED" if final_ended else "",
            "session_phase": session_phase,
        }

    def start_stock_operation(self, stock_code: Any, *, as_of: datetime | None = None) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        if (
            document["session"]["validation_session_id"]
            in self._restart_recovery_blocked_session_ids
        ):
            raise MockValidationError("MOCK_RESTART_RECOVERY_BLOCKED")
        self.session_service.sync_common_tax_to_waiting_session(
            document["session"]["validation_session_id"]
        )
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        operation_policy = self._operation_snapshot()
        rules_snapshot_by_instance = {
            instance_id: self._latest_applied_rules(instance_id, document)
            for instance_id in document["instance_execution"]
        }
        result = self.lifecycle.start_stock_operation(
            document["session"]["validation_session_id"],
            trading_date=now.date(),
            as_of=now,
            operation_policy_snapshot={
                **operation_policy,
                "mock_tax_enabled": document["session"]["mock_tax_enabled"],
                "mock_tax_rate": document["session"]["mock_tax_rate"],
                "mock_rules_snapshot_by_instance": rules_snapshot_by_instance,
                "mock_effective_settings_by_instance": {
                    instance_id: self._instance_effective_settings(
                        document,
                        instance_id,
                        operation_policy=operation_policy,
                    )
                    for instance_id in document["instance_execution"]
                },
            },
            command_id=deterministic_mock_identity(
                "MC", document["session"]["validation_session_id"], now.date(), "OPERATION_START"
            ),
        )
        self._publish_projection_if_changed()
        return result

    def start_instance_operation(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        if (
            document["session"]["validation_session_id"]
            in self._restart_recovery_blocked_session_ids
        ):
            raise MockValidationError("MOCK_RESTART_RECOVERY_BLOCKED")
        now = as_of or self._now()
        operation_policy = self._operation_snapshot()
        start_admission = self._instance_start_admission(
            document,
            routine_instance_id,
            now,
            operation_policy=operation_policy,
        )
        if start_admission.get("allowed") is not True:
            raise MockValidationError(
                str(start_admission.get("reason") or "FINAL_SESSION_ENDED")
            )
        if document["session"].get("state") == SESSION_WAITING:
            self.session_service.sync_common_tax_to_waiting_session(
                document["session"]["validation_session_id"]
            )
            document = self.current_session(stock_code)
            if document is None:
                raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        session_id = document["session"]["validation_session_id"]
        rules_snapshot = self._latest_applied_rules(routine_instance_id, document)
        result = self.lifecycle.start_instance_operation(
            session_id,
            routine_instance_id=routine_instance_id,
            trading_date=now.date(),
            as_of=now,
            operation_policy_snapshot={
                **operation_policy,
                "mock_tax_enabled": document["session"]["mock_tax_enabled"],
                "mock_tax_rate": document["session"]["mock_tax_rate"],
                "mock_instance_rules_snapshot": rules_snapshot,
                "mock_instance_effective_settings": self._instance_effective_settings(
                    document,
                    routine_instance_id,
                    operation_policy=operation_policy,
                ),
            },
            command_id=deterministic_mock_identity(
                "MC", session_id, routine_instance_id,
                document.get("revision", 0),
                now.isoformat(timespec="microseconds"), "INSTANCE_OPERATION_START",
            ),
        )
        self._publish_projection_if_changed()
        return result

    def request_early_close(
        self, stock_code: Any, *, method: str = CLOSE_MARKET, as_of: datetime | None = None
    ) -> dict[str, Any]:
        self._require_server_authenticated()
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

    def request_instance_early_close(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        method: str = CLOSE_MARKET,
        profit_percent: Any = None,
        loss_percent: Any = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        self._require_server_authenticated()
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        session_id = document["session"]["validation_session_id"]
        result = self.lifecycle.request_instance_early_close(
            session_id,
            routine_instance_id=routine_instance_id,
            method=method,
            profit_percent=profit_percent,
            loss_percent=loss_percent,
            reason="사용자 Instance 조기마감",
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", session_id, routine_instance_id,
                document.get("revision", 0),
                now.isoformat(timespec="microseconds"), "INSTANCE_EARLY_CLOSE",
            ),
        )
        self._publish_projection_if_changed()
        return result

    def cancel_instance_early_close(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        session_id = document["session"]["validation_session_id"]
        result = self.lifecycle.cancel_instance_early_close(
            session_id,
            routine_instance_id=routine_instance_id,
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", session_id, routine_instance_id,
                document.get("revision", 0),
                now.isoformat(timespec="microseconds"),
                "INSTANCE_EARLY_CLOSE_CANCEL",
            ),
        )
        self._publish_projection_if_changed()
        return result

    def return_instance_early_close_to_auto(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        session_id = document["session"]["validation_session_id"]
        result = self.lifecycle.return_instance_early_close_to_auto(
            session_id,
            routine_instance_id=routine_instance_id,
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", session_id, routine_instance_id,
                document.get("revision", 0),
                now.isoformat(timespec="microseconds"),
                "INSTANCE_EARLY_AUTO_RETURN",
            ),
        )
        self._publish_projection_if_changed()
        return result

    def request_immediate_liquidation(
        self,
        stock_code: Any,
        *,
        method: str = CLOSE_MARKET,
        as_of: datetime | None = None,
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
            method=method,
            reason="사용자 즉시청산",
        )
        self._publish_projection_if_changed()
        return result

    def request_instance_immediate_liquidation(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        method: str = CLOSE_MARKET,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        session_id = document["session"]["validation_session_id"]
        result = self.lifecycle.request_instance_immediate_liquidation(
            session_id,
            routine_instance_id=routine_instance_id,
            method=method,
            reason="사용자 Instance 즉시청산",
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", session_id, routine_instance_id,
                document.get("revision", 0),
                now.isoformat(timespec="microseconds"),
                "INSTANCE_IMMEDIATE_LIQUIDATION",
            ),
        )
        self._publish_projection_if_changed()
        return result

    def request_instance_manual_ats_liquidation(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        method: str,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Request Mock-owned immediate liquidation in an active selected ATS session."""

        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        session_id = document["session"]["validation_session_id"]
        instance_id = str(routine_instance_id or "").strip()
        operation = instance_operation_state(document, instance_id)
        command_id = deterministic_mock_identity(
            "MC",
            session_id,
            instance_id,
            document.get("revision", 0),
            now.isoformat(timespec="microseconds"),
            "INSTANCE_MANUAL_ATS_LIQUIDATION",
        )

        def blocked(reason_code: str) -> None:
            self.lifecycle.record_instance_action_block(
                session_id,
                routine_instance_id=instance_id,
                event_type="EXECUTION_PLAN_BLOCKED",
                reason_code=reason_code,
                as_of=now,
                command_id=command_id,
                payload={
                    "requested_action": "MANUAL_ATS_LIQUIDATION",
                    "requested_method": str(method or "").strip(),
                },
            )
            raise MockValidationError(reason_code)

        if not isinstance(operation, dict) or operation.get("state") not in {
            "RUNNING",
            "CLOSING",
        }:
            blocked("MOCK_INSTANCE_OPERATION_NOT_STARTED")
        snapshot = operation.get("operation_policy_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        settings = self._operation_effective_settings(document, instance_id)
        mode = str(settings.get("operation_mode") or "").strip().upper()
        _market_active, ats_active = self._mock_market_session_phase(
            settings,
            now,
            operation_policy=snapshot,
        )
        if mode != "CONTINUOUS" or not ats_active:
            blocked("MOCK_ATS_SESSION_INACTIVE")
        position = next(
            (
                item
                for item in document.get("positions", ())
                if isinstance(item, dict)
                and str(item.get("routine_instance_id") or "").strip()
                == instance_id
            ),
            {},
        )
        if int(position.get("holding_qty", 0) or 0) <= 0:
            blocked("NO_HOLDING")
        result = self.lifecycle.request_instance_immediate_liquidation(
            session_id,
            routine_instance_id=instance_id,
            method=method,
            reason="사용자 ATS 적극청산",
            as_of=now,
            command_id=command_id,
        )
        self._publish_projection_if_changed()
        return result

    def request_instance_individual_liquidation(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        method: str,
        minutes_before_regular_close: Any = "5",
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        self._require_server_authenticated()
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = as_of or self._now()
        session_id = document["session"]["validation_session_id"]
        result = self.lifecycle.request_instance_individual_liquidation(
            session_id,
            routine_instance_id=routine_instance_id,
            method=method,
            minutes_before_regular_close=minutes_before_regular_close,
            reason="사용자 Instance 개별청산",
            as_of=now,
            command_id=deterministic_mock_identity(
                "MC", session_id, routine_instance_id,
                document.get("revision", 0),
                now.isoformat(timespec="microseconds"),
                "INSTANCE_INDIVIDUAL_LIQUIDATION",
            ),
        )
        self._publish_projection_if_changed()
        return result

    def stop_instance_validation(
        self,
        stock_code: Any,
        routine_instance_id: str,
        *,
        command_id: str | None = None,
        source: str = "OPERATOR_UI",
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        now = self._now().isoformat(timespec="microseconds")
        result = self.session_service.stop_instance_validation(
            document["session"]["validation_session_id"],
            routine_instance_id=routine_instance_id,
            command_id=command_id or new_mock_identity("MC"),
            source=source,
            requested_at=now,
        )
        self._publish_projection_if_changed()
        return result

    def instance_context_state(
        self, stock_code: Any, routine_instance_id: str
    ) -> dict[str, Any]:
        document = self.current_session(stock_code)
        if document is None:
            return {"current": False}
        instance_id = str(routine_instance_id or "").strip()
        execution = document.get("instance_execution", {}).get(instance_id)
        if not isinstance(execution, dict):
            return {"current": False}
        operation = instance_operation_state(document, instance_id)
        operation_state = operation.get("state", "") if operation else ""
        session_state = document["session"].get("state", "")
        ended = session_state == SESSION_ENDED or execution.get("state") == SESSION_ENDED
        error_recovery_reset = mock_instance_error_recovery_reset_allowed(
            execution, operation
        )
        early_close_cancelable = bool(
            operation
            and str(operation.get("close_source") or "").strip().upper() == "EARLY"
            and str(operation.get("close_method") or "").strip()
            and not operation.get("final_sell_evidence")
            and not operation.get("processed_cycles")
        )
        lifecycle_can_start = self._instance_lifecycle_start_available(
            document, instance_id
        )
        start_admission = {"allowed": True, "reason": "", "session_phase": {}}
        if lifecycle_can_start:
            start_admission = self._instance_start_admission(
                document,
                instance_id,
                self._now(),
            )
        return {
            "current": True,
            "validation_session_id": document["session"]["validation_session_id"],
            "routine_instance_id": instance_id,
            "state": execution.get("state", ""),
            "operation_state": operation_state,
            "can_start": lifecycle_can_start,
            "start_admission_allowed": start_admission.get("allowed") is True,
            "start_block_reason": str(start_admission.get("reason") or ""),
            "start_session_phase": deepcopy(
                start_admission.get("session_phase") or {}
            ),
            "operation_mode": self._instance_effective_settings(
                document, instance_id
            ).get("operation_mode", ""),
            "can_early_close": (
                operation_state == "RUNNING"
                and not str((operation or {}).get("close_method") or "").strip()
            ),
            "can_early_close_cancel": early_close_cancelable,
            "can_immediate": operation_state in {"RUNNING", "CLOSING"},
            "can_validation_stop": execution.get("state") in {
                SESSION_RUNNING,
                SESSION_CLOSING,
                INSTANCE_ERROR,
            },
            "can_chart": not ended,
            "can_reset": (
                not ended
                and (
                    (
                        execution.get("state")
                        not in {SESSION_RUNNING, SESSION_CLOSING}
                        and operation_state not in {"RUNNING", "CLOSING"}
                    )
                    or error_recovery_reset
                )
            ),
        }

    @staticmethod
    def _operation(document: dict[str, Any]) -> dict[str, Any] | None:
        root = document.get("mock_operation_lifecycle")
        current = root.get("current") if isinstance(root, dict) else None
        return current if isinstance(current, dict) else None

    @staticmethod
    def _instance_regular_end_reached(
        operation: dict[str, Any],
        now: datetime,
    ) -> bool:
        snapshot = operation.get("operation_policy_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        regular = snapshot.get("regular_market")
        regular = regular if isinstance(regular, dict) else {}
        end_seconds = _clock_seconds(regular.get("end_time"))
        if end_seconds is None:
            return False
        return now.hour * 3600 + now.minute * 60 + now.second >= end_seconds

    @staticmethod
    def _instance_pending_order_cancel_boundary_reached(
        operation: dict[str, Any],
        now: datetime,
    ) -> bool:
        snapshot = operation.get("operation_policy_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        regular = snapshot.get("regular_market")
        regular = regular if isinstance(regular, dict) else {}
        end_seconds = _clock_seconds(regular.get("end_time"))
        if end_seconds is None:
            return False
        boundary_seconds = regular_end_pending_order_cancel_boundary_seconds(
            end_seconds,
            snapshot,
        )
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        return now_seconds >= boundary_seconds

    def _instance_final_close_boundary_reached(
        self,
        document: dict[str, Any],
        instance_id: str,
        operation: dict[str, Any],
        now: datetime,
    ) -> bool:
        if str(operation.get("close_source") or "").strip().upper() == "IMMEDIATE":
            snapshot = operation.get("operation_policy_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            settings = self._operation_effective_settings(document, instance_id)
            mode = str(settings.get("operation_mode") or "").strip().upper()
            _market_active, ats_active = self._mock_market_session_phase(
                settings,
                now,
                operation_policy=snapshot,
            )
            if mode == "CONTINUOUS" and ats_active:
                return False
        return self._instance_regular_end_reached(operation, now)

    def _trigger_due_close(self, document: dict[str, Any], now: datetime) -> None:
        root = document.get("mock_operation_lifecycle")
        instance_operations = (
            root.get("instance_operations", {}) if isinstance(root, dict) else {}
        )
        session_id = document["session"]["validation_session_id"]
        positions = {
            str(item.get("routine_instance_id") or "").strip(): item
            for item in document.get("positions", ())
            if isinstance(item, dict)
        }
        for instance_id in sorted(instance_operations):
            operation = instance_operations.get(instance_id)
            if not isinstance(operation, dict) or operation.get("state") not in {
                "RUNNING",
                "CLOSING",
            }:
                continue
            snapshot = operation.get("operation_policy_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            settings = self._operation_effective_settings(document, instance_id)
            regular = snapshot.get("regular_market")
            regular = regular if isinstance(regular, dict) else {}
            end_seconds = _clock_seconds(regular.get("end_time"))
            liquidation = snapshot.get("liquidation")
            liquidation = liquidation if isinstance(liquidation, dict) else {}
            individual = operation.get("individual_liquidation_time_snapshot")
            individual = individual if isinstance(individual, dict) else {}
            try:
                individual_minutes = str(
                    individual.get("minutes_before_regular_close") or ""
                ).strip()
                minutes = max(
                    0,
                    int(
                        individual_minutes
                        if individual and individual_minutes
                        else liquidation.get("minutes_before_regular_close", 0)
                    ),
                )
            except (TypeError, ValueError):
                raise MockValidationError("MOCK_LIQUIDATION_MINUTES_INVALID")
            method = _close_method(
                individual.get("method")
                if individual
                else liquidation.get("method", "시장가")
            )
            now_seconds = now.hour * 3600 + now.minute * 60 + now.second
            mode = str(
                snapshot.get("operation_mode")
                or settings.get("operation_mode")
                or ""
            ).strip().upper()
            position = positions.get(instance_id)
            holding_qty = (
                int(position.get("holding_qty", 0) or 0)
                if isinstance(position, dict)
                else 0
            )
            instance_live_orders = [
                item
                for item in document.get("orders", ())
                if isinstance(item, dict)
                and str(item.get("routine_instance_id") or "").strip() == instance_id
                and item.get("state") in _LIVE_ORDER_STATES
            ]
            pending_cleanup_reached = (
                self._instance_pending_order_cancel_boundary_reached(operation, now)
            )
            _market_active, ats_active = self._mock_market_session_phase(
                settings,
                now,
                operation_policy=snapshot,
            )
            manual_ats_liquidation_active = bool(
                mode == "CONTINUOUS"
                and ats_active
                and str(operation.get("close_source") or "").strip().upper()
                == "IMMEDIATE"
            )
            liquidation_admitted = bool(
                end_seconds is not None
                and now_seconds >= max(0, end_seconds - minutes * 60)
                and holding_qty > 0
                and not manual_ats_liquidation_active
                and not (
                    str(operation.get("close_source") or "").strip().upper()
                    == "EARLY"
                    and str(operation.get("close_method") or "").strip().upper()
                    == CLOSE_CARRYOVER
                )
                and (
                    mode == "SCHEDULED"
                    or bool(str(operation.get("close_method") or "").strip())
                )
                and (
                    method != CLOSE_CARRYOVER
                    or (pending_cleanup_reached and not instance_live_orders)
                )
            )
            if (
                liquidation_admitted
                and str(operation.get("close_source") or "").strip().upper()
                != "LIQUIDATION"
            ):
                self.lifecycle.request_instance_liquidation_boundary(
                    session_id,
                    routine_instance_id=instance_id,
                    method=method,
                    reason="Mock 청산 절대경계 도달",
                    as_of=now,
                    command_id=deterministic_mock_identity(
                        "MC",
                        session_id,
                        instance_id,
                        operation.get("operation_session_id"),
                        "LIQUIDATION_BOUNDARY",
                    ),
                )
                continue
            if (
                operation.get("state") != "RUNNING"
                or str(operation.get("close_method") or "").strip()
            ):
                continue
            phase = self._mock_operation_activation_phase(
                settings, now, operation_policy=snapshot
            )
            if phase.get("projection_phase") != "FINAL_END":
                continue
            if mode == "CONTINUOUS":
                manual_ats = settings.get("manual_ats")
                selected_sessions = (
                    manual_ats.get("selected_sessions", ())
                    if isinstance(manual_ats, dict)
                    else ()
                )
                self.lifecycle.complete_instance_normal_termination(
                    session_id,
                    routine_instance_id=instance_id,
                    as_of=now,
                    lifecycle_cycle_id=deterministic_mock_identity(
                        "MC",
                        session_id,
                        instance_id,
                        operation.get("operation_session_id"),
                        "NORMAL_CONTINUOUS_FINAL",
                    ),
                    ats_continuation=bool(tuple(selected_sessions)),
                )
                continue
            if mode != "SCHEDULED":
                continue
            auto_close = snapshot.get("auto_close")
            auto_close = auto_close if isinstance(auto_close, dict) else {}
            if snapshot.get("auto_close_enabled", True) is False:
                continue
            if holding_qty <= 0:
                continue
            method = _close_method(auto_close.get("method", "루틴매도신호"))
            self.lifecycle.request_instance_auto_close(
                session_id,
                routine_instance_id=instance_id,
                method=method,
                profit_percent=auto_close.get("profit_percent"),
                loss_percent=auto_close.get("loss_percent"),
                reason="자동마감 시간정책 도달",
                as_of=now,
                command_id=deterministic_mock_identity(
                    "MC",
                    session_id,
                    instance_id,
                    operation.get("operation_session_id"),
                    "AUTO_CLOSE",
                ),
            )

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
        long_hold = True
        has_holding = any(
            int(item.get("holding_qty", 0) or 0) > 0
            for item in document.get("positions", ())
            if isinstance(item, dict)
        )
        auto_enabled = snapshot.get("auto_close_enabled", True) is not False
        if auto_enabled and has_holding and now_seconds >= max(0, end_seconds - minutes * 60) and now_seconds < end_seconds:
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

    @staticmethod
    def _seconds_in_window(now_seconds: int, start: Any, end: Any) -> bool:
        start_seconds = _clock_seconds(start)
        end_seconds = _clock_seconds(end)
        if start_seconds is None or end_seconds is None or start_seconds == end_seconds:
            return False
        if start_seconds < end_seconds:
            return start_seconds <= now_seconds < end_seconds
        return now_seconds >= start_seconds or now_seconds < end_seconds

    @staticmethod
    def _mock_operation_phase_inputs(
        settings: dict[str, Any],
        operation_policy: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], Callable[[str], dict[str, Any]]]:
        schedule = settings.get("operation_schedule")
        schedule = schedule if isinstance(schedule, dict) else {}
        config = {
            "operation_mode": settings.get("operation_mode"),
            "start_time": schedule.get("start_time"),
            "end_buy_time": schedule.get("end_buy_time"),
        }
        manual_ats = settings.get("manual_ats")
        selected = list(
            manual_ats.get("selected_sessions", ())
            if isinstance(manual_ats, dict)
            else ()
        )
        state = {"manual_ats_selection": {"selected_sessions": selected}}
        sessions = operation_policy.get("extra_sessions")
        sessions = sessions if isinstance(sessions, list) else []

        def ats_reader(key: str) -> dict[str, Any]:
            try:
                index = VALID_SESSION_KEYS.index(key)
            except ValueError:
                return {}
            session = sessions[index] if index < len(sessions) else None
            return deepcopy(session) if isinstance(session, dict) else {}

        return config, state, ats_reader

    def _mock_operation_activation_phase(
        self,
        settings: dict[str, Any],
        now: datetime,
        *,
        operation_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = (
            operation_policy
            if isinstance(operation_policy, dict)
            else self._operation_policy_provider()
        )
        policy = policy if isinstance(policy, dict) else {}
        config, state, ats_reader = self._mock_operation_phase_inputs(
            settings, policy
        )
        session_phase = auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now,
            operation_policy_reader=lambda: policy,
            ats_session_reader=ats_reader,
        )
        return auto_trade_operation_activation_phase(
            config,
            state,
            now_dt=now,
            session_phase=session_phase,
            operation_policy_reader=lambda: policy,
        )

    def _mock_market_session_phase(
        self,
        settings: dict[str, Any],
        now: datetime,
        *,
        operation_policy: dict[str, Any] | None = None,
    ) -> tuple[bool, bool]:
        phase = self._mock_operation_activation_phase(
            settings, now, operation_policy=operation_policy
        )
        return (
            phase.get("actual_trading_session_active") is True,
            phase.get("ats_session_active") is True,
        )

    def _routine_close_market_open(
        self,
        settings: dict[str, Any],
        now: datetime,
        operation_policy: dict[str, Any],
    ) -> bool:
        if str(settings.get("operation_mode") or "").strip().upper() != "SCHEDULED":
            return self._mock_market_session_phase(
                settings, now, operation_policy=operation_policy
            )[0]
        regular = operation_policy.get("regular_market")
        regular = regular if isinstance(regular, dict) else {}
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        return self._seconds_in_window(
            now_seconds,
            regular.get("start_time", "09:00:00"),
            regular.get("end_time", "15:20:00"),
        )

    def _mock_market_session_open(
        self,
        settings: dict[str, Any],
        now: datetime,
    ) -> bool:
        return self._mock_market_session_phase(settings, now)[0]

    def _operation_effective_settings(
        self, document: dict[str, Any], instance_id: str
    ) -> dict[str, Any]:
        active = mock_instance_active_effective_settings(document, instance_id)
        if isinstance(active, dict):
            return active
        return self._instance_effective_settings(document, instance_id)

    def _process_stock(self, document: dict[str, Any], now: datetime) -> dict[str, Any]:
        stock_code = document["session"]["stock_code"]
        reason = self._integrity_errors.pop(stock_code, "")
        if reason:
            self._review_error(document, reason)
            return self.repository.read_session(
                document["session"]["validation_session_id"]
            )
        state = document["session"]["state"]
        if state in {SESSION_WAITING, SESSION_REVIEW_STOPPED, SESSION_ENDED}:
            # Keep the transport buffer bounded, but never advance an order in
            # a state whose stock-level progression is stopped.
            self._drain_market_evidence(document, None)
            return document
        policy = self._policy()
        self._drain_market_evidence(document, policy)
        session_id = document["session"]["validation_session_id"]
        market = self.market_store.market_snapshot(stock_code)
        if state == SESSION_RUNNING:
            current = self.repository.read_session(session_id)
            root = current.get("mock_operation_lifecycle")
            operations = (
                root.get("instance_operations", {})
                if isinstance(root, dict)
                else {}
            )
            for instance_id in sorted(operations):
                operation = instance_operation_state(current, instance_id)
                if (
                    not operation
                    or operation.get("state") not in {"RUNNING", "CLOSING"}
                    or not self._instance_pending_order_cancel_boundary_reached(
                        operation,
                        now,
                    )
                ):
                    continue
                snapshot = operation.get("operation_policy_snapshot")
                snapshot = snapshot if isinstance(snapshot, dict) else {}
                settings = self._operation_effective_settings(current, instance_id)
                mode = str(
                    snapshot.get("operation_mode")
                    or settings.get("operation_mode")
                    or ""
                ).strip().upper()
                close_source = str(
                    operation.get("close_source") or ""
                ).strip().upper()
                if mode == "CONTINUOUS" and close_source not in {
                    "EARLY",
                    "AUTO",
                    "LIQUIDATION",
                }:
                    continue
                has_live_orders = any(
                    isinstance(item, dict)
                    and str(item.get("routine_instance_id") or "").strip()
                    == instance_id
                    and item.get("state") in _LIVE_ORDER_STATES
                    for item in current.get("orders", ())
                )
                if not has_live_orders:
                    continue
                cleanup_result = (
                    self.lifecycle.process_instance_regular_end_pending_cleanup(
                        session_id,
                        routine_instance_id=instance_id,
                        lifecycle_cycle_id=deterministic_mock_identity(
                            "MC",
                            session_id,
                            instance_id,
                            now.replace(microsecond=0).isoformat(),
                            "REGULAR_END_PENDING_CLEANUP",
                        ),
                        as_of=now,
                    )
                )
                current = cleanup_result.get("document", current)
            self._trigger_due_close(
                self.repository.read_session(session_id), now
            )
            current = self.repository.read_session(session_id)
            if policy is not None:
                second_identity = now.replace(microsecond=0).isoformat()
                for instance_id in sorted(document.get("instance_execution", {})):
                    execution = current.get("instance_execution", {}).get(instance_id, {})
                    if execution.get("progression_allowed") is not True:
                        continue
                    settings = self._operation_effective_settings(current, instance_id)
                    rules = self._operation_rules(current, instance_id)
                    candle_projection = self._instance_candle_projection(
                        current,
                        instance_id=instance_id,
                        rules=rules,
                        settings=settings,
                        now=now,
                    )
                    operation = instance_operation_state(current, instance_id) or {}
                    operation_policy = operation.get("operation_policy_snapshot")
                    operation_policy = (
                        operation_policy if isinstance(operation_policy, dict) else {}
                    )
                    if (
                        operation.get("state") == "RUNNING"
                        and operation.get("close_pending") is True
                        and operation.get("close_method") == CLOSE_PROFIT_LOSS
                    ):
                        trade = market.trade if market is not None else None
                        profit_loss_result = (
                            self.lifecycle.activate_instance_profit_loss_if_triggered(
                                session_id,
                                routine_instance_id=instance_id,
                                as_of=now,
                                current_price=(
                                    getattr(trade, "current_price", None)
                                    if trade is not None
                                    else None
                                ),
                            )
                        )
                        current = profit_loss_result.get("document", current)
                        operation = instance_operation_state(current, instance_id) or {}
                        if operation.get("state") == OPERATION_CLOSING:
                            continue
                    market_open, _ats_active = self._mock_market_session_phase(
                        settings, now, operation_policy=operation_policy
                    )
                    routine_close = (
                        operation.get("state") == "RUNNING"
                        and operation.get("close_pending") is True
                        and operation.get("close_method") == CLOSE_ROUTINE
                    )
                    if routine_close:
                        market_open = self._routine_close_market_open(
                            settings, now, operation_policy
                        )
                    if not market_open:
                        continue
                    schedule = settings["operation_schedule"]
                    now_seconds = now.hour * 3600 + now.minute * 60 + now.second
                    new_buy_allowed = (
                        routine_close
                        or settings.get("operation_mode") != "SCHEDULED"
                        or self._seconds_in_window(
                            now_seconds,
                            schedule["start_time"],
                            schedule["end_buy_time"],
                        )
                    )
                    try:
                        evaluation_cycle_id = deterministic_mock_identity(
                            "MC", session_id, instance_id, second_identity, "ROUTINE"
                        )
                        evaluation_result = self.routine_adapter.evaluate_cycle(
                            session_id,
                            routine_instance_id=instance_id,
                            candles=candle_projection.get("candles", ()),
                            market=market,
                            policy=policy,
                            evaluation_cycle_id=evaluation_cycle_id,
                            evaluated_at=now,
                            new_buy_allowed=new_buy_allowed,
                            candle_availability=candle_projection,
                            document=current,
                        )
                        current = evaluation_result.get("document", current)
                        final_sell_result = self.lifecycle.record_instance_routine_final_sell(
                            session_id,
                            routine_instance_id=instance_id,
                            as_of=now,
                            evaluation_cycle_id=evaluation_cycle_id,
                            result=evaluation_result,
                            document=current,
                        )
                        current = final_sell_result.get("document", current)
                        completion_result = self.lifecycle.complete_instance_routine_close_if_ready(
                            session_id,
                            routine_instance_id=instance_id,
                            as_of=now,
                            lifecycle_cycle_id=deterministic_mock_identity(
                                "MC", session_id, instance_id, second_identity,
                                "ROUTINE_CLOSE_COMPLETION",
                            ),
                            document=current,
                        )
                        current = completion_result.get("document", current)
                    except Exception as exc:
                        reason = str(exc) or type(exc).__name__
                        self._review_error(
                            current,
                            reason,
                            routine_instance_ids=(instance_id,),
                        )
                        current = self.repository.read_session(session_id)
            if policy is not None:
                refreshed = current
                root = refreshed.get("mock_operation_lifecycle")
                operations = (
                    root.get("instance_operations", {})
                    if isinstance(root, dict)
                    else {}
                )
                for instance_id in sorted(operations):
                    operation = instance_operation_state(current, instance_id)
                    if not operation or operation.get("state") != OPERATION_CLOSING:
                        continue
                    lifecycle_result = self.lifecycle.process_instance_operation_cycle(
                        session_id,
                        routine_instance_id=instance_id,
                        lifecycle_cycle_id=deterministic_mock_identity(
                            "MC", session_id, instance_id,
                            now.replace(microsecond=0).isoformat(),
                            "INSTANCE_LIFECYCLE",
                        ),
                        as_of=now,
                        market=market,
                        policy=policy,
                        final_close_boundary=self._instance_final_close_boundary_reached(
                            current,
                            instance_id,
                            operation,
                            now,
                        ),
                        pending_order_cleanup_boundary=(
                            self._instance_pending_order_cancel_boundary_reached(
                                operation,
                                now,
                            )
                        ),
                    )
                    current = lifecycle_result.get("document", current)
            return current
        if state == SESSION_CLOSING and policy is not None:
            current = self.repository.read_session(session_id)
            root = current.get("mock_operation_lifecycle")
            operations = (
                root.get("instance_operations", {})
                if isinstance(root, dict)
                else {}
            )
            if isinstance(operations, dict) and operations:
                for instance_id in sorted(operations):
                    operation = instance_operation_state(current, instance_id)
                    if not operation or operation.get("state") != OPERATION_CLOSING:
                        continue
                    lifecycle_result = self.lifecycle.process_instance_operation_cycle(
                        session_id,
                        routine_instance_id=instance_id,
                        lifecycle_cycle_id=deterministic_mock_identity(
                            "MC", session_id, instance_id,
                            now.replace(microsecond=0).isoformat(),
                            "INSTANCE_LIFECYCLE",
                        ),
                        as_of=now,
                        market=market,
                        policy=policy,
                        final_close_boundary=self._instance_final_close_boundary_reached(
                            current,
                            instance_id,
                            operation,
                            now,
                        ),
                        pending_order_cleanup_boundary=(
                            self._instance_pending_order_cancel_boundary_reached(
                                operation,
                                now,
                            )
                        ),
                    )
                    current = lifecycle_result.get("document", current)
                return current
            operation = self._operation(document)
            snapshot = operation.get("operation_policy_snapshot", {}) if operation else {}
            regular = snapshot.get("regular_market", {}) if isinstance(snapshot, dict) else {}
            end_seconds = _clock_seconds(regular.get("end_time")) if isinstance(regular, dict) else None
            now_seconds = now.hour * 3600 + now.minute * 60 + now.second
            result = self.lifecycle.process_mock_operation_cycle(
                session_id,
                lifecycle_cycle_id=deterministic_mock_identity(
                    "MC", session_id, now.replace(microsecond=0).isoformat(), "LIFECYCLE"
                ),
                as_of=now,
                market=market,
                policy=policy,
                final_close_boundary=bool(end_seconds is not None and now_seconds >= end_seconds),
            )
            return result.get("document", document)
        return document

    def process_due_cycles(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        if self._disposed:
            return {"processed": 0, "errors": (), "reason": "MOCK_HOST_DISPOSED"}
        if self._processing:
            return {"processed": 0, "errors": (), "reason": "MOCK_HOST_REENTRY_BLOCKED"}
        self._processing = True
        errors: list[tuple[str, str]] = []
        processed = 0
        projected_documents: list[dict[str, Any]] = []
        try:
            self.sync_registration()
            now = as_of or self._now()
            documents, read_errors = self._current_sessions_with_errors()
            errors.extend(read_errors)
            for document in documents:
                stock_code = document["session"]["stock_code"]
                if (
                    document["session"]["validation_session_id"]
                    in self._restart_recovery_blocked_session_ids
                ):
                    errors.append((stock_code, "MOCK_RESTART_RECOVERY_BLOCKED"))
                    projected_documents.append(document)
                    continue
                try:
                    projected_documents.append(self._process_stock(document, now))
                    processed += 1
                except Exception as exc:
                    reason = str(exc) or type(exc).__name__
                    errors.append((stock_code, reason))
                    try:
                        session_id = document["session"]["validation_session_id"]
                        self._review_error(self.repository.read_session(session_id), reason)
                        projected_documents.append(self.repository.read_session(session_id))
                    except Exception:
                        projected_documents.append(document)
            self._cycle_projection_documents = tuple(projected_documents)
            try:
                self._publish_projection_if_changed()
            finally:
                self._cycle_projection_documents = None
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
                "instance_execution": document.get("instance_execution"),
                "effective_settings_by_instance": document.get(
                    "effective_settings_by_instance"
                ),
                "cycle_state_by_instance": document.get("cycle_state_by_instance"),
                "progression_by_instance": document.get("progression_by_instance"),
                "positions": document.get("positions"),
                "pnl": document.get("pnl"),
                "operation": document.get("mock_operation_lifecycle"),
            })
        return payload_hash(sessions)

    def ui_projection_hash(
        self, *, documents: tuple[dict[str, Any], ...] | None = None,
    ) -> str:
        """Hash only the current user-visible Mock monitoring projection."""

        now = self._now()
        trees: list[dict[str, Any]] = []
        source = documents
        if source is None:
            source = getattr(self, "_cycle_projection_documents", None)
        if source is None:
            source = self.current_sessions()
        for document in source:
            stock_code = document["session"]["stock_code"]
            trade = self.market_store.latest_trade(stock_code)
            tree = mock_monitoring_tree_projection(
                document,
                current_price=(
                    getattr(trade, "current_price", None)
                    if trade is not None
                    else None
                ),
                as_of=now,
            )
            tree.pop("revision", None)
            for child in tree.get("children", ()):
                if not isinstance(child, dict):
                    continue
                child.pop("cycle_state", None)
                child.pop("progression", None)
                child.pop("rules_snapshot", None)
            trees.append(tree)
        trees.sort(key=lambda tree: tree.get("stock_code", ""))
        return payload_hash(trees)

    def _publish_projection_if_changed(
        self, *, documents: tuple[dict[str, Any], ...] | None = None,
    ) -> bool:
        current = (
            self.ui_projection_hash()
            if documents is None
            else self.ui_projection_hash(documents=documents)
        )
        if current == self._projection_hash:
            return False
        self._projection_hash = current
        if callable(self._projection_changed):
            self._projection_changed()
        return True


__all__ = ["MockMarketEvidence", "MockValidationHost"]
