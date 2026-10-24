"""Automatic, bounded no-order observation of accepted direct SOR bars."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from candle_timeframe_aggregation import SUPPORTED_BAR_MINUTES, read_canonical_bar_minutes
from routine_main_facts import validate_routine_main_facts
from sor_no_order_signal_probe import capture_prepared_direct_sor_signal


_ROOT = Path(__file__).resolve().parent
_KST = timedelta(hours=9)


def _prepare(event: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any] | None:
    """Read committed rules and project the already captured actual cycle."""
    try:
        from routine_instance_registry import routine_definition_by_id, routine_instance_by_id
        from routine_package_contract import EVALUATION_ROLE, load_routine_module
        from stock_repository import StockRepository

        valid, _reason = validate_routine_main_facts(facts)
        observed = datetime.fromisoformat(event["observed_at"])
        captured = datetime.fromisoformat(facts["captured_at"])
        if (not valid or observed.utcoffset() != _KST
                or not timedelta(0) <= observed - captured <= timedelta(seconds=30)):
            return None
        code = event["stock_code"]
        frame = event["timeframe_minutes"]
        if (not isinstance(code, str) or len(code) != 6 or not code.isascii()
                or not code.isdigit() or type(frame) is not int
                or frame not in SUPPORTED_BAR_MINUTES):
            return None
        stock_dir = StockRepository(project_root=_ROOT).resolve_stock_dir(code)
        config_path = stock_dir / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        instance_id = config.get("assigned_routine_instance_id")
        if (not isinstance(instance_id, str) or not instance_id
                or str(config.get("calculation_market_source", "SOR")).upper() != "SOR"
                or facts.get("stock_configs", {}).get(code, {}).get(
                    "assigned_routine_instance_id") != instance_id):
            return None
        instance = routine_instance_by_id(instance_id, project_root=_ROOT)
        if (instance is None or not instance.enabled or not instance.persisted
                or instance.rules_path is None):
            return None
        definition = routine_definition_by_id(instance.definition_id, project_root=_ROOT)
        if (definition is None or not definition.package_enabled
                or definition.package_dir.resolve() !=
                (_ROOT / "routines" / "지표추종매매").resolve()):
            return None
        rules_bytes = instance.rules_path.read_bytes()
        rules = json.loads(rules_bytes)
        if (not isinstance(rules, dict) or not rules
                or read_canonical_bar_minutes(rules) != frame):
            return None
        module = load_routine_module(definition, EVALUATION_ROLE)
        selector = getattr(module, "evaluate_signal_selection", None)
        request_reader = getattr(module, "market_bar_projection_request", None)
        cycle_reader = getattr(module, "project_cycle_context", None)
        if not all(callable(item) for item in (selector, request_reader, cycle_reader)):
            return None
        request = request_reader(rules)
        if not isinstance(request, dict):
            return None
        cycle = cycle_reader(
            code=code, routine_instance_id=instance_id,
            order_queue=facts["orders"], fills=facts["fills"],
            positions=facts["positions"], signals=facts["signals"],
        )
        if (not isinstance(cycle, dict) or cycle.get("status") != "resolved"
                or type(cycle.get("holding_qty")) is not int):
            return None
        return {
            "event": event, "instance_id": instance_id, "rules": rules,
            "facts_revision": facts["revision"],
            "rules_bytes": rules_bytes, "rules_path": instance.rules_path,
            "config_path": config_path, "request": {**request, "projection": "COMPLETED_TIMEFRAME"},
            "selector": selector, "cycle": cycle,
        }
    except Exception:
        return None


class NoOrderSorMonitor(QObject):
    """Never owns the broker, operation timer, queue, or execution boundary."""

    prepared = pyqtSignal(object)
    finished = pyqtSignal(object)

    def __init__(self, market_host: Any, parent: QObject) -> None:
        super().__init__(parent)
        self._market_host = market_host
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sor-no-order")
        self._facts: dict[str, Any] | None = None
        self._pending: set[tuple[Any, ...]] = set()
        self._waiting: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._enqueued: OrderedDict[tuple[Any, ...], None] = OrderedDict()
        self._closed = False
        self.last_result: dict[str, Any] = {"status": "BLOCKED", "reason": "ACTUAL_CYCLE_MISSING"}
        market_host.direct_sor_completed.connect(self.on_direct_completed)
        self.prepared.connect(self._on_prepared)
        self.finished.connect(self._on_finished)

    def accept_main_facts(self, facts: dict[str, Any]) -> None:
        if not self._closed and isinstance(facts, dict):
            self._facts = facts  # Main supplied a sealed, read-only capture.
            self._start_next()

    def on_direct_completed(self, event: object) -> None:
        if self._closed or not isinstance(event, dict):
            return
        key = tuple(event.get(name) for name in (
            "stock_code", "timeframe_minutes", "bar_time",
            "connection_epoch", "login_session_id"))
        try:
            observed = datetime.fromisoformat(event["observed_at"])
            if (observed.utcoffset() != _KST or not timedelta(0) <=
                    datetime.now(observed.tzinfo) - observed <= timedelta(seconds=30)):
                return
        except (KeyError, TypeError, ValueError):
            return
        if key in self._pending or key in self._waiting:
            return
        if len(self._waiting) >= 32:
            self.last_result = {"status": "BLOCKED", "reason": "OBSERVATION_BACKLOG_FULL"}
            return
        self._waiting[key] = dict(event)
        self._start_next()

    def _start_next(self) -> None:
        if self._closed or self._facts is None or self._pending or not self._waiting:
            return
        key, event = self._waiting.popitem(last=False)
        self._pending.add(key)
        try:
            self._executor.submit(self._prepare_task, event, self._facts, key)
        except Exception:
            self._pending.discard(key)

    def _prepare_task(self, event: dict[str, Any], facts: dict[str, Any],
                      key: tuple[Any, ...]) -> None:
        prepared = _prepare(event, facts)
        if not self._closed:
            try:
                self.prepared.emit((key, prepared))
            except RuntimeError:
                pass

    def _on_prepared(self, item: object) -> None:
        key, prepared = item
        if self._closed or prepared is None:
            self._pending.discard(key)
            self.last_result = {"status": "BLOCKED", "reason": "ACTUAL_CYCLE_OR_ASSIGNMENT_UNPROVEN"}
            self._start_next()
            return
        event = prepared["event"]
        host = self._market_host
        session = (event["connection_epoch"], event["login_session_id"])
        try:
            if (not isinstance(prepared.get("facts_revision"), str)
                    or not prepared["facts_revision"]
                    or self._facts is None
                    or self._facts.get("revision") != prepared["facts_revision"]
                    or host._shutting_down or host._current_display_session_identity() != session
                    or event["stock_code"] not in host._monitoring_target_stock_codes):
                raise ValueError("STALE_OR_CANCELLED_EVENT")
            observed = datetime.fromisoformat(event["observed_at"])
            if not timedelta(0) <= datetime.now(observed.tzinfo) - observed <= timedelta(seconds=30):
                raise ValueError("STALE_OR_CANCELLED_EVENT")
            source = host.project_routine_candles(
                stock_code=event["stock_code"], rules=prepared["rules"],
                projection_request=prepared["request"], as_of=observed,
                consumer_scope="SOR_CALCULATION_ONLY",
            )
            if (host._current_display_session_identity() != session
                    or not isinstance(source, dict)
                    or not isinstance(source.get("candles"), list)
                    or not source["candles"]
                    or source["candles"][-1].get("bar_time") != event["bar_time"]):
                raise ValueError("SOURCE_OR_SESSION_CHANGED")
            self._executor.submit(self._finish_task, key, prepared, source, session)
        except Exception:
            self._pending.discard(key)
            self.last_result = {"status": "BLOCKED", "reason": "SOURCE_OR_SESSION_CHANGED"}
            self._start_next()

    def _finish_task(self, key: tuple[Any, ...], prepared: dict[str, Any],
                     source: dict[str, Any], session: tuple[int, str]) -> None:
        event = prepared["event"]
        try:
            config = json.loads(prepared["config_path"].read_text(encoding="utf-8"))
            if (config.get("assigned_routine_instance_id") != prepared["instance_id"]
                    or prepared["rules_path"].read_bytes() != prepared["rules_bytes"]):
                result = {"status": "BLOCKED", "reason": "ASSIGNMENT_OR_RULES_CHANGED"}
            else:
                result = capture_prepared_direct_sor_signal(
                    stock_code=event["stock_code"],
                    routine_instance_id=prepared["instance_id"],
                    observed_cycle=prepared["cycle"],
                    observed_at=datetime.fromisoformat(event["observed_at"]),
                    rules=prepared["rules"], selector=prepared["selector"],
                    source=source, session_identity=session, enqueue=False,
                )
        except Exception:
            result = {"status": "BLOCKED", "reason": "DIRECT_SOR_CAPTURE_EXCEPTION"}
        if not self._closed:
            try:
                self.finished.emit((key, prepared["facts_revision"], result))
            except RuntimeError:
                pass

    def _on_finished(self, item: object) -> None:
        key, facts_revision, result = item
        self._pending.discard(key)
        if result.get("status") == "OBSERVED":
            try:
                host = self._market_host
                current = (not self._closed and not host._shutting_down
                           and self._facts is not None
                           and self._facts.get("revision") == facts_revision
                           and host._current_display_session_identity() == (key[3], key[4])
                           and key[0] in host._monitoring_target_stock_codes)
            except Exception:
                current = False
            if not current:
                result = {"status": "BLOCKED", "reason": "STALE_OR_CANCELLED_EVENT"}
            else:
                marker = result.get("sor_preview_evidence")
                hashes = ("source_identity", "receipt_hash", "input_hash", "rules_hash")
                dedup_key = (*key, *(marker[name] for name in hashes)) if (
                    isinstance(marker, dict)
                    and all(isinstance(marker.get(name), str) for name in hashes)) else None
                try:
                    if dedup_key in self._enqueued:
                        accepted = True
                    else:
                        from sor_m3_production_capture import enqueue_preview
                        accepted = enqueue_preview(result)
                        if accepted and dedup_key is not None:
                            self._enqueued[dedup_key] = None
                            if len(self._enqueued) > 512:
                                self._enqueued.popitem(last=False)
                except Exception:
                    accepted = False
                if not accepted:
                    result = {"status": "BLOCKED", "reason": "EVIDENCE_CAPTURE_UNAVAILABLE"}
        self.last_result = result
        self._start_next()

    def shutdown(self) -> None:
        self._closed = True
        self._facts = None
        self._pending.clear()
        self._waiting.clear()
        self._enqueued.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
