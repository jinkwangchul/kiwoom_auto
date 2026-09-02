# -*- coding: utf-8 -*-
"""Fail-open Production observation boundary for routine decisions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from decision_trace_contract import (
    build_trace_record,
    live_trace_mode_expired,
    new_trace_id,
    resolve_live_trace_mode,
)
from decision_trace_snapshot_service import (
    DecisionTraceSnapshotService,
    compute_engine_bundle_identity,
)
from decision_trace_writer import DecisionTraceWriter
from market_evidence_store import MarketEvidenceStore
from routine_instance_registry import routine_definition_by_id, routine_instance_by_id
from routine_package_contract import routine_trace_contract


LOGGER = logging.getLogger(__name__)

ENV_TRACE_ENABLED = "KIWOOM_DIAGNOSTIC_TRACE"
ENV_SCOPE_STOCK = "KIWOOM_DIAGNOSTIC_SCOPE_STOCK"
ENV_SCOPE_ROUTINE = "KIWOOM_DIAGNOSTIC_SCOPE_ROUTINE"
ENV_TRACE_MINUTES = "KIWOOM_DIAGNOSTIC_MINUTES"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _aware_now() -> datetime:
    return datetime.now().astimezone()


def _bar_timestamp(candle: Any) -> str:
    if not isinstance(candle, dict):
        return ""
    for key in ("timestamp", "datetime", "time", "date", "bar_time"):
        value = candle.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _condition_ref(path: str, rules_hash: str) -> dict[str, str]:
    return {"rules_hash": rules_hash, "path": str(path or "")}


class LiveDiagnosticModeProvider:
    """Read a restart-scoped Diagnostic activation once per process/service."""

    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        activated_at: datetime | None = None,
    ) -> None:
        source = os.environ if environ is None else environ
        minutes_text = str(source.get(ENV_TRACE_MINUTES, "") or "").strip()
        try:
            minutes = int(minutes_text) if minutes_text else None
        except ValueError:
            minutes = 0
        self._mode = resolve_live_trace_mode(
            diagnostic_enabled=_truthy(source.get(ENV_TRACE_ENABLED)),
            stock_scope=str(source.get(ENV_SCOPE_STOCK, "") or ""),
            routine_scope=str(source.get(ENV_SCOPE_ROUTINE, "") or ""),
            minutes=minutes,
            activated_at=activated_at or _aware_now(),
        )

    def mode_for(
        self,
        *,
        stock_code: str,
        routine_instance_id: str,
        routine_name: str,
        now: datetime | None = None,
    ) -> str:
        if not self._mode.get("accepted") or self._mode.get("trace_level") != "DIAGNOSTIC":
            return "NORMAL"
        if live_trace_mode_expired(self._mode, now=now or _aware_now()):
            return "NORMAL"
        stock_scope = str(self._mode.get("stock_scope") or "").strip()
        routine_scope = str(self._mode.get("routine_scope") or "").strip()
        if stock_scope and stock_scope != str(stock_code or "").strip():
            return "NORMAL"
        routine_values = {str(routine_instance_id or "").strip(), str(routine_name or "").strip()}
        if routine_scope and routine_scope not in routine_values:
            return "NORMAL"
        return "DIAGNOSTIC"


class _EngineIdentityCache:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self._lock = threading.RLock()
        self._cache: dict[tuple[tuple[str, int, int], ...], dict[str, Any]] = {}

    def get(self, bundle_files: tuple[Path, ...]) -> dict[str, Any]:
        signature: list[tuple[str, int, int]] = []
        for path in bundle_files:
            resolved = Path(path).resolve()
            stat = resolved.stat()
            signature.append((str(resolved), stat.st_size, stat.st_mtime_ns))
        frozen = tuple(signature)
        with self._lock:
            if frozen not in self._cache:
                self._cache[frozen] = compute_engine_bundle_identity(
                    bundle_files,
                    project_root=self.project_root,
                )
            return deepcopy(self._cache[frozen])


class DecisionObservationCollector:
    """In-memory collector passed through the existing routine context."""

    def __init__(
        self,
        *,
        trace_id: str,
        trace_level: str,
        initial_rules: Any = None,
        engine_bundle_files: tuple[Path, ...] = (),
        rules_excluded_keys: tuple[str, ...] = (),
    ) -> None:
        self.trace_id = trace_id
        self.trace_level = trace_level
        self.capture_details = trace_level == "DIAGNOSTIC"
        self.effective_rules = initial_rules if isinstance(initial_rules, dict) else None
        self.engine_bundle_files = tuple(engine_bundle_files)
        self.rules_excluded_keys = tuple(rules_excluded_keys)
        self.conditions: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.indicator_snapshots: list[dict[str, Any]] = []
        self.aggregations: dict[str, dict[str, Any]] = {}

    def set_effective_rules(self, rules: Any) -> None:
        if isinstance(rules, dict):
            self.effective_rules = rules

    def observe_condition(self, observation: Any) -> None:
        if not self.capture_details or not isinstance(observation, dict):
            return
        self.conditions.append(deepcopy(observation))
        for snapshot in observation.get("indicator_snapshots", []):
            if isinstance(snapshot, dict):
                identity = (
                    snapshot.get("indicator"),
                    snapshot.get("period"),
                    snapshot.get("index"),
                )
                if not any(
                    (item.get("indicator"), item.get("period"), item.get("index")) == identity
                    for item in self.indicator_snapshots
                ):
                    self.indicator_snapshots.append(deepcopy(snapshot))

    def observe_group(self, observation: Any) -> None:
        if self.capture_details and isinstance(observation, dict):
            self.groups.append(deepcopy(observation))

    def observe_aggregation(self, side: str, observation: Any) -> None:
        if self.capture_details and isinstance(observation, dict):
            self.aggregations[str(side or "").strip().upper()] = deepcopy(observation)


class ProductionDecisionTraceObserver:
    """Persist an already-computed routine result without affecting that result."""

    def __init__(
        self,
        *,
        writer: DecisionTraceWriter | None = None,
        snapshot_service: DecisionTraceSnapshotService | None = None,
        market_store: MarketEvidenceStore | None = None,
        mode_provider: LiveDiagnosticModeProvider | None = None,
        project_root: Path | None = None,
        now_factory: Callable[[], datetime] = _aware_now,
        trace_id_factory: Callable[[], str] = new_trace_id,
    ) -> None:
        root = Path(project_root) if project_root is not None else Path(__file__).resolve().parent
        self.project_root = root
        self.writer = writer or DecisionTraceWriter()
        self.snapshot_service = snapshot_service or DecisionTraceSnapshotService()
        self.market_store = market_store or MarketEvidenceStore()
        self.mode_provider = mode_provider or LiveDiagnosticModeProvider()
        self._engine_identity = _EngineIdentityCache(root)
        self._now_factory = now_factory
        self._trace_id_factory = trace_id_factory

    def begin(
        self,
        *,
        stock_code: str,
        routine_instance_id: str,
        routine_name: str,
        definition_id: str = "",
        initial_rules: Any = None,
    ) -> DecisionObservationCollector:
        level = self.mode_provider.mode_for(
            stock_code=stock_code,
            routine_instance_id=routine_instance_id,
            routine_name=routine_name,
            now=self._now_factory(),
        )
        engine_files: tuple[Path, ...] = ()
        excluded_keys: tuple[str, ...] = ()
        try:
            instance = routine_instance_by_id(
                routine_instance_id,
                project_root=self.project_root,
            )
            resolved_definition_id = str(
                definition_id or getattr(instance, "definition_id", "") or ""
            ).strip()
            definition = routine_definition_by_id(
                resolved_definition_id,
                project_root=self.project_root,
            )
            if definition is not None:
                engine_files, excluded_keys = routine_trace_contract(definition)
        except Exception:
            pass
        return DecisionObservationCollector(
            trace_id=self._trace_id_factory(),
            trace_level=level,
            initial_rules=initial_rules,
            engine_bundle_files=engine_files,
            rules_excluded_keys=excluded_keys,
        )

    def append_decision(
        self,
        collector: DecisionObservationCollector,
        *,
        result: dict[str, Any],
        candles: list[dict[str, Any]],
        context: dict[str, Any],
        routine_name: str,
        code: str,
        name: str,
        signal_id: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        final_decision = str(result.get("signal") or "").strip().upper()
        if final_decision not in {"BUY", "SELL"}:
            final_decision = "NONE"
        if collector.trace_level == "NORMAL" and final_decision == "NONE":
            return {"status": "skipped", "reason": "NORMAL_NONE", "elapsed_ms": 0.0}
        try:
            rules = collector.effective_rules
            if not isinstance(rules, dict):
                return self._failed("rules unavailable", started)
            market = self.market_store.save_window(candles)
            if not market.get("saved") and not market.get("duplicate"):
                return self._failed(f"market evidence unavailable: {market.get('error')}", started)
            if collector.rules_excluded_keys:
                rules_saved = self.snapshot_service.save_rules(
                    rules,
                    excluded_keys=collector.rules_excluded_keys,
                )
            else:
                rules_saved = self.snapshot_service.save_rules(rules)
            if not rules_saved.get("saved") and not rules_saved.get("duplicate"):
                return self._failed(f"rules snapshot unavailable: {rules_saved.get('error')}", started)
            settings_saved = self.snapshot_service.save_settings(context.get("stock_config", {}))
            if not settings_saved.get("saved") and not settings_saved.get("duplicate"):
                return self._failed(f"settings snapshot unavailable: {settings_saved.get('error')}", started)
            engine_identity = self._engine_identity.get(collector.engine_bundle_files)
            rules_hash = str(rules_saved.get("hash") or "")
            conditions = self._finalize_conditions(collector.conditions, rules_hash)
            groups = self._finalize_groups(collector.groups, rules_hash)
            aggregations = {
                side: self._finalize_aggregation(value, rules_hash)
                for side, value in collector.aggregations.items()
            }
            decision_at = self._now_factory().isoformat()
            input_hash = str(market.get("input_window_hash") or "")
            bar_index = result.get("signal_index")
            if not isinstance(bar_index, int) or isinstance(bar_index, bool):
                bar_index = len(candles) - 1
            bar_time = _bar_timestamp(candles[bar_index]) if candles and -len(candles) <= bar_index < len(candles) else ""
            if not bar_time and candles:
                bar_time = _bar_timestamp(candles[-1])
            timeframe = self._timeframe(rules)
            evaluation_status = self._evaluation_status(result)
            record = build_trace_record(
                trace_id=collector.trace_id,
                recorded_at=decision_at,
                environment="LIVE",
                trace_level=collector.trace_level,
                stage="DECISION",
                stage_result=evaluation_status,
                decision_at=decision_at,
                dataset_ref={
                    "dataset_id": f"market-evidence:{input_hash}",
                    "source": str(context.get("market_data_source") or "STOCK_CANDLE_FILE"),
                    "data_hash": input_hash,
                    "timeframe": timeframe,
                    "timezone": str(context.get("timezone") or "Asia/Seoul"),
                    "bar_time": bar_time,
                    "bar_index": bar_index,
                    "bar_count": len(candles),
                    "input_window_hash": input_hash,
                },
                rule_ref={
                    "routine_definition_id": str(
                        context.get("stock_config", {}).get("routine_definition_id")
                        or context.get("routine_type")
                        or routine_name
                    ),
                    "routine_instance_id": str(context.get("routine_instance_id") or ""),
                    "routine_type": str(context.get("routine_type") or ""),
                    "rules_version": str(rules.get("rules_version") or rules.get("schema_version") or "unknown"),
                    "rules_hash": rules_hash,
                    "settings_hash": str(settings_saved.get("hash") or ""),
                    "engine_bundle_hash": str(engine_identity.get("engine_bundle_hash") or ""),
                },
                conditions=conditions,
                groups=groups,
                final_decision=final_decision,
                evaluation_status=evaluation_status,
                stock_code=code,
                stock_name=name,
                routine_instance_id=str(context.get("routine_instance_id") or ""),
                signal_id=str(signal_id or "").strip() or None,
                reason=str(result.get("reason") or ""),
                details=deepcopy(result.get("details")) if isinstance(result.get("details"), list) else None,
                indicator_snapshots=deepcopy(collector.indicator_snapshots) if collector.capture_details else None,
                position_context=self._position_context(context),
                cycle_context=self._cycle_context(context),
                buy_aggregation=aggregations.get("BUY"),
                sell_aggregation=aggregations.get("SELL"),
            )
            appended = self.writer.append_record(record)
            if not appended.get("appended") and not appended.get("duplicate"):
                return self._failed(
                    f"trace append failed: {appended.get('error') or appended.get('issues')}",
                    started,
                )
            if signal_id:
                try:
                    from decision_trace_correlation import default_trace_correlation_resolver

                    default_trace_correlation_resolver().register(
                        trace_id=collector.trace_id,
                        trace_level=collector.trace_level,
                        signal_id=signal_id,
                    )
                except Exception:
                    pass
            return {
                "status": "appended" if appended.get("appended") else "duplicate",
                "trace_id": collector.trace_id,
                "record": record,
                "writer_result": appended,
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            }
        except Exception as exc:
            LOGGER.exception("Decision Trace observation failed open")
            return self._failed(f"{type(exc).__name__}: {exc}", started)

    @staticmethod
    def _timeframe(rules: dict[str, Any]) -> str:
        bar = rules.get("bar") if isinstance(rules.get("bar"), dict) else {}
        minutes = bar.get("bar_minutes", rules.get("bar_minutes", 1))
        try:
            value = int(minutes)
        except (TypeError, ValueError):
            value = 1
        return f"{max(value, 1)}m"

    @staticmethod
    def _evaluation_status(result: dict[str, Any]) -> str:
        signal = str(result.get("signal") or "").strip().upper()
        reason = str(result.get("reason") or "")
        if signal == "ERROR" or reason.startswith("evaluate 예외") or "import 실패" in reason:
            return "FAILED"
        if reason in {"봉데이터 부족", "루틴 비활성", "감시 대상 아님"}:
            return "SKIPPED"
        return "COMPLETED"

    @staticmethod
    def _finalize_conditions(items: list[dict[str, Any]], rules_hash: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in items:
            value = {key: deepcopy(child) for key, child in item.items() if key != "indicator_snapshots"}
            value["condition_ref"] = _condition_ref(str(item.get("path") or ""), rules_hash)
            value.pop("path", None)
            result.append(value)
        return result

    @staticmethod
    def _finalize_groups(items: list[dict[str, Any]], rules_hash: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in items:
            value = deepcopy(item)
            value["group_ref"] = _condition_ref(str(value.pop("path", "")), rules_hash)
            value["condition_refs"] = [
                _condition_ref(str(path), rules_hash) for path in value.pop("condition_paths", [])
            ]
            result.append(value)
        return result

    @staticmethod
    def _finalize_aggregation(value: dict[str, Any], rules_hash: str) -> dict[str, Any]:
        return {
            "logic": str(value.get("logic") or "OR"),
            "active_group_refs": [
                _condition_ref(str(path), rules_hash) for path in value.get("active_group_paths", [])
            ],
            "matched_group_refs": [
                _condition_ref(str(path), rules_hash) for path in value.get("matched_group_paths", [])
            ],
            "result": bool(value.get("result")),
        }

    @staticmethod
    def _position_context(context: dict[str, Any]) -> dict[str, Any] | None:
        cycle = context.get("cycle")
        if not isinstance(cycle, dict):
            return None
        return {
            "holding_qty": cycle.get("holding_qty"),
            "average_price": cycle.get("avg_price"),
            "position_state": "OPEN" if (cycle.get("holding_qty") or 0) > 0 else "FLAT",
        }

    @staticmethod
    def _cycle_context(context: dict[str, Any]) -> dict[str, Any] | None:
        cycle = context.get("cycle")
        if not isinstance(cycle, dict):
            return None
        source_map = {
            "cycle_identity": "cycle_identity",
            "cycle_active": "active",
            "confirmed_buy_round": "confirmed_buy_round",
            "cumulative_filled_buy_amount": "cumulative_filled_buy_amount",
            "pending_buy_rounds": "pending_buy_rounds",
            "partial_sell": "partial_sell",
            "remaining_budget": "remaining_budget",
        }
        return {target: deepcopy(cycle.get(source)) for target, source in source_map.items() if source in cycle}

    @staticmethod
    def _failed(reason: str, started: float) -> dict[str, Any]:
        LOGGER.warning("Decision Trace observation skipped: %s", reason)
        return {
            "status": "write_failed",
            "reason": reason,
            "elapsed_ms": (time.perf_counter() - started) * 1000.0,
        }
