# -*- coding: utf-8 -*-
"""Upper Production coordinator for durable buffer-response ownership claims.

This module persists only the ingress checkpoint and ownership claim.  After
their verified commit it may delegate to the intent-specific Production
boundary; it does not implement close/cancel/order logic itself.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
import weakref

from account_auto_trade_budget_consumption import (
    project_account_auto_trade_budget_consumption,
)
from buffer_response_candidate_selector import (
    select_buffer_owned_early_close_escalation_candidate,
    select_buffer_response_candidate,
)
from buffer_response_early_close_dispatcher import (
    buffer_response_command_source,
    deterministic_buffer_early_close_command_id,
    dispatch_main_window_buffer_early_close,
)
from buffer_response_immediate_liquidation_preparer import (
    prepare_main_window_buffer_immediate_liquidation,
    resume_main_window_buffer_immediate_liquidation_events,
)
from buffer_response_immediate_liquidation_dispatcher import (
    dispatch_main_window_buffer_immediate_market_close,
    dispatch_ready_main_window_buffer_immediate_preparations,
)
from buffer_response_ingress_state_service import (
    BufferResponseIngressStateService,
    build_stable_buffer_observation,
    collect_confirmed_contributing_buy_ids,
)
from buffer_response_ownership_service import (
    BATCH_SCHEMA_VERSION,
    LEGACY_BATCH_SCHEMA_VERSION,
    RESPONSE_INTENT_EARLY_CLOSE,
    RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    STATUS_OWNED,
    BufferResponseOwnershipService,
)
from buffer_response_policy_projection import project_buffer_response_policy
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
)
from gui_main_budget_panel import (
    collect_main_budget_summary,
    project_main_budget_activity,
)
from pnl_ui_refresh import project_current_stock_pnl_snapshot
from operation_close_completion_evaluator import evaluate_operation_close_completion
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import production_recovery_registry
from runtime_io import read_json_dict
from stock_repository import StockRepository


PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
_INTEGRATION_READY_WINDOW_IDS: set[int] = set()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _stock_code(value: object) -> str:
    return _text(value).lstrip("A")


def _result(reason: str = "", **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "observed": False,
        "stable": False,
        "baseline_initialized": False,
        "event_created": False,
        "event_id": None,
        "event_sequence": 0,
        "policy_projected": False,
        "candidate_selected": False,
        "selected_stock_code": "",
        "claimed_response_intent": "",
        "ownership_claimed": False,
        "ownership_existing": False,
        "ingress_committed": False,
        "crash_bridge_recovered": 0,
        "reason": _text(reason),
    }
    result.update(updates)
    return result


def _checkpoint_for(
    snapshot: Mapping[str, object], account_no: str, trading_day: str
) -> Mapping[str, object] | None:
    checkpoints = snapshot.get("checkpoints")
    if not isinstance(checkpoints, Mapping):
        return None
    matches = [
        value
        for value in checkpoints.values()
        if isinstance(value, Mapping)
        and value.get("account_no") == account_no
        and value.get("trading_day") == trading_day
    ]
    return matches[0] if len(matches) == 1 else None


def _source_evidence(
    observation: Mapping[str, object], preview: Mapping[str, object]
) -> dict[str, object]:
    return {
        "observation_id": observation["observation_id"],
        "observed_at": observation["observed_at"],
        "previous_entry_amount": preview["previous_entry_amount"],
        "current_entry_amount": observation["confirmed_entry_amount"],
        "new_contributing_buy_ids": list(preview["new_contributing_buy_ids"]),
        "contributing_buy_ids": list(observation["contributing_buy_ids"]),
        "confirmed_evidence": deepcopy(observation["evidence"]),
    }


class BufferResponseCoordinator:
    """Compose stable ingress, policy, selector, and ownership without execution."""

    def __init__(
        self,
        *,
        ingress_service: BufferResponseIngressStateService | None = None,
        ownership_service: BufferResponseOwnershipService | None = None,
        policy_projector: Callable[..., dict[str, object]] = project_buffer_response_policy,
        candidate_selector: Callable[..., dict[str, object]] = select_buffer_response_candidate,
        escalation_selector: Callable[..., dict[str, object]] = (
            select_buffer_owned_early_close_escalation_candidate
        ),
        completion_projector: Callable[..., dict[str, object]] = (
            evaluate_operation_close_completion
        ),
    ) -> None:
        self.ingress = ingress_service or BufferResponseIngressStateService()
        self.ownership = ownership_service or BufferResponseOwnershipService()
        self._policy_projector = policy_projector
        self._candidate_selector = candidate_selector
        self._escalation_selector = escalation_selector
        self._completion_projector = completion_projector

    def reconcile_completion_and_escalate(
        self,
        *,
        account_no: object,
        trading_day: object,
        budget_activity: Mapping[str, object] | object,
        settings_surface: object,
        pnl_by_stock: Mapping[str, Mapping[str, object]] | object,
        candidates: Iterable[Mapping[str, object]] | object,
        completion_projection: Mapping[str, object] | object,
    ) -> dict[str, object]:
        """Finalize durable completions, then promote at most one owned event."""

        account = _text(account_no)
        day = _text(trading_day)
        result: dict[str, object] = {
            "ok": False,
            "reason": "",
            "completion_changed_count": 0,
            "completion_results": (),
            "policy_projection": None,
            "selected_event_id": "",
            "selected_stock_code": "",
            "escalation_changed": False,
            "escalation_result": None,
        }
        if not account or not day:
            result["reason"] = "OWNERSHIP_ACCOUNT_OR_DAY_UNAVAILABLE"
            return result

        completion_results: list[dict[str, object]] = []
        ownership_read = self.ownership.read_snapshot()
        snapshot = ownership_read.get("snapshot")
        if ownership_read.get("ok") is not True or not isinstance(snapshot, Mapping):
            result["reason"] = _text(ownership_read.get("reason")) or "OWNERSHIP_UNAVAILABLE"
            return result
        if snapshot.get("schema_version") != BATCH_SCHEMA_VERSION:
            result["reason"] = "OWNERSHIP_SCHEMA_NOT_ESCALATABLE"
            return result

        events = snapshot.get("events")
        if not isinstance(events, Mapping):
            result["reason"] = "OWNERSHIP_EVENTS_INVALID"
            return result
        owned_event_ids = sorted(
            _text(event_id)
            for event_id, event in events.items()
            if isinstance(event, Mapping)
            and event.get("account_no") == account
            and event.get("trading_day") == day
            and event.get("status") == STATUS_OWNED
        )
        for event_id in owned_event_ids:
            current = self.ownership.read_snapshot()
            current_snapshot = current.get("snapshot")
            if current.get("ok") is not True or not isinstance(current_snapshot, Mapping):
                result["reason"] = _text(current.get("reason")) or "OWNERSHIP_UNAVAILABLE"
                return result
            current_events = current_snapshot.get("events")
            current_event = (
                current_events.get(event_id)
                if isinstance(current_events, Mapping)
                else None
            )
            if not isinstance(current_event, Mapping) or current_event.get("status") != STATUS_OWNED:
                continue
            completed = self.ownership.mark_completed(
                event_id=event_id,
                completion_projection=completion_projection,
                expected_revision=current_snapshot["revision"],
            )
            completion_results.append(completed)
            if completed.get("ok") is True and completed.get("changed") is True:
                result["completion_changed_count"] = (
                    int(result["completion_changed_count"]) + 1
                )
        result["completion_results"] = tuple(completion_results)

        refreshed = self.ownership.read_snapshot()
        refreshed_snapshot = refreshed.get("snapshot")
        if refreshed.get("ok") is not True or not isinstance(refreshed_snapshot, Mapping):
            result["reason"] = _text(refreshed.get("reason")) or "OWNERSHIP_UNAVAILABLE"
            return result
        refreshed_events = refreshed_snapshot.get("events")
        if not isinstance(refreshed_events, Mapping):
            result["reason"] = "OWNERSHIP_EVENTS_INVALID"
            return result
        active_events = {
            _text(event_id): event
            for event_id, event in refreshed_events.items()
            if isinstance(event, Mapping)
            and event.get("account_no") == account
            and event.get("trading_day") == day
            and event.get("status") == STATUS_OWNED
        }
        if any(
            _text(event.get("response_intent")).upper()
            == RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
            for event in active_events.values()
        ):
            result["ok"] = True
            result["reason"] = "ACTIVE_IMMEDIATE_OWNERSHIP_PENDING"
            return result

        if not isinstance(budget_activity, Mapping) or budget_activity.get("available") is not True:
            result["reason"] = "BUFFER_BUDGET_ACTIVITY_UNAVAILABLE"
            return result
        entry_amount = budget_activity.get("entry_amount")
        if isinstance(entry_amount, bool) or entry_amount is None:
            result["reason"] = "BUFFER_ENTRY_PROJECTION_UNAVAILABLE"
            return result
        try:
            no_buffer_entry = float(entry_amount) <= 0
        except (TypeError, ValueError):
            result["reason"] = "BUFFER_ENTRY_PROJECTION_UNAVAILABLE"
            return result
        if no_buffer_entry:
            result["ok"] = True
            result["reason"] = "BUFFER_NOT_ENTERED"
            return result

        policy = self._policy_projector(
            settings_surface=settings_surface,
            pnl_by_stock=pnl_by_stock,
            budget_activity=budget_activity,
        )
        result["policy_projection"] = policy
        if policy.get("available") is not True or policy.get("applicable") is not True:
            result["reason"] = _text(policy.get("reason")) or "BUFFER_RESPONSE_POLICY_UNAVAILABLE"
            return result
        if policy.get("configured_response_mode") != "BUFFER_ENTRY_THRESHOLD":
            result["ok"] = True
            result["reason"] = "NOT_INTERVAL_CLOSE_MODE"
            return result
        if policy.get("effective_response") != RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED:
            result["ok"] = True
            result["reason"] = "BUFFER_BELOW_CONFIGURED_THRESHOLD"
            return result

        command_evidence = {
            event_id: {
                "stock_code": _stock_code(event.get("selected_stock_code")),
                "source": buffer_response_command_source(event_id),
                "command_id": deterministic_buffer_early_close_command_id(event_id),
            }
            for event_id, event in active_events.items()
            if _text(event.get("response_intent")).upper()
            == RESPONSE_INTENT_EARLY_CLOSE
        }
        selected = self._escalation_selector(
            policy_projection=policy,
            candidates=candidates,
            owned_command_evidence=command_evidence,
        )
        if selected.get("selectable") is not True:
            result["ok"] = True
            result["reason"] = _text(selected.get("reason")) or "NO_ESCALATION_CANDIDATE"
            result["escalation_result"] = selected
            return result
        selected_event_id = _text(selected.get("selected_event_id"))
        promoted = self.ownership.promote_owned_early_close_to_immediate(
            event_id=selected_event_id,
            expected_revision=refreshed_snapshot["revision"],
        )
        result["escalation_result"] = promoted
        result["selected_event_id"] = selected_event_id
        result["selected_stock_code"] = _stock_code(selected.get("selected_stock_code"))
        result["escalation_changed"] = promoted.get("changed") is True
        result["ok"] = promoted.get("ok") is True
        result["reason"] = _text(promoted.get("reason"))
        return result

    def _reconcile_crash_bridge(
        self, *, account_no: str, trading_day: str
    ) -> dict[str, object]:
        ingress_read = self.ingress.read_snapshot()
        ownership_read = self.ownership.read_snapshot()
        if ingress_read.get("ok") is not True:
            return {"ok": False, "reason": ingress_read.get("reason"), "recovered": 0}
        if ownership_read.get("ok") is not True:
            return {"ok": False, "reason": ownership_read.get("reason"), "recovered": 0}

        ownership_snapshot = ownership_read.get("snapshot")
        if not isinstance(ownership_snapshot, Mapping):
            return {"ok": False, "reason": "ownership snapshot is unavailable", "recovered": 0}
        if ownership_snapshot.get("schema_version") not in {
            LEGACY_BATCH_SCHEMA_VERSION,
            BATCH_SCHEMA_VERSION,
        }:
            return {"ok": True, "reason": "", "recovered": 0}
        events = ownership_snapshot.get("events")
        if not isinstance(events, Mapping):
            return {"ok": False, "reason": "ownership events are unavailable", "recovered": 0}
        relevant = sorted(
            (
                event
                for event in events.values()
                if isinstance(event, Mapping)
                and event.get("account_no") == account_no
                and event.get("trading_day") == trading_day
                and isinstance(event.get("event_sequence"), int)
            ),
            key=lambda event: int(event["event_sequence"]),
        )

        recovered = 0
        for event in relevant:
            ingress_read = self.ingress.read_snapshot()
            if ingress_read.get("ok") is not True:
                return {"ok": False, "reason": ingress_read.get("reason"), "recovered": recovered}
            ingress_snapshot = ingress_read.get("snapshot")
            if not isinstance(ingress_snapshot, Mapping):
                return {"ok": False, "reason": "ingress snapshot is unavailable", "recovered": recovered}
            checkpoint = _checkpoint_for(ingress_snapshot, account_no, trading_day)
            if checkpoint is None:
                return {
                    "ok": False,
                    "reason": "ownership exists without an ingress baseline checkpoint",
                    "recovered": recovered,
                }
            checkpoint_sequence = int(checkpoint["last_event_sequence"])
            event_sequence = int(event["event_sequence"])
            if event_sequence < checkpoint_sequence:
                continue
            if event_sequence == checkpoint_sequence:
                continue
            recovery = self.ingress.recover_claimed_event_checkpoint(
                claimed_event=event,
                expected_revision=ingress_snapshot["revision"],
            )
            if recovery.get("ok") is not True:
                return {"ok": False, "reason": recovery.get("reason"), "recovered": recovered}
            if recovery.get("changed") is True:
                recovered += 1
        return {"ok": True, "reason": "", "recovered": recovered}

    def process_stable_observation(
        self,
        *,
        observation: Mapping[str, object],
        budget_activity: Mapping[str, object],
        settings_surface: object,
        pnl_by_stock: Mapping[str, Mapping[str, object]] | object,
        candidates: Iterable[Mapping[str, object]] | object,
    ) -> dict[str, object]:
        account_no = _text(observation.get("account_no"))
        trading_day = _text(observation.get("trading_day"))
        result = _result(observed=True, stable=True)

        bridge = self._reconcile_crash_bridge(
            account_no=account_no,
            trading_day=trading_day,
        )
        result["crash_bridge_recovered"] = int(bridge.get("recovered", 0) or 0)
        if bridge.get("ok") is not True:
            result["reason"] = _text(bridge.get("reason")) or "CRASH_BRIDGE_FAILED"
            return result

        preview = self.ingress.preview_observation(observation)
        if preview.get("ok") is not True:
            result["reason"] = _text(preview.get("reason"))
            return result
        result["baseline_initialized"] = bool(preview.get("baseline"))
        result["event_created"] = bool(preview.get("event_required"))
        result["event_id"] = preview.get("event_id")
        result["event_sequence"] = int(preview.get("event_sequence", 0) or 0)

        if preview.get("event_required") is not True:
            committed = self.ingress.commit_stable_observation(
                observation=observation,
                expected_revision=preview["expected_revision"],
            )
            result["ingress_committed"] = committed.get("ok") is True
            result["reason"] = (
                "BASELINE_INITIALIZED"
                if preview.get("baseline") is True and committed.get("ok") is True
                else _text(committed.get("reason"))
            )
            return result

        policy = self._policy_projector(
            settings_surface=settings_surface,
            pnl_by_stock=pnl_by_stock,
            budget_activity=budget_activity,
        )
        result["policy_projected"] = policy.get("available") is True and policy.get("applicable") is True
        if result["policy_projected"] is not True:
            committed = self.ingress.commit_unclaimed_event_observation(
                observation=observation,
                expected_revision=preview["expected_revision"],
            )
            result["ingress_committed"] = committed.get("ok") is True
            result["reason"] = (
                _text(policy.get("reason"))
                if committed.get("ok") is True
                else _text(committed.get("reason"))
            )
            return result

        if (
            policy.get("configured_response_mode") == "BUFFER_ENTRY_THRESHOLD"
            and policy.get("effective_response")
            == RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
        ):
            committed = self.ingress.commit_unclaimed_event_observation(
                observation=observation,
                expected_revision=preview["expected_revision"],
            )
            result["ingress_committed"] = committed.get("ok") is True
            result["reason"] = (
                "INTERVAL_ESCALATION_REUSES_EXISTING_OWNERSHIP"
                if committed.get("ok") is True
                else _text(committed.get("reason"))
            )
            return result

        active = self.ownership.active_owned_stock_codes(
            account_no=account_no,
            trading_day=trading_day,
        )
        if active.get("ok") is not True:
            result["reason"] = _text(active.get("reason"))
            return result
        selected = self._candidate_selector(
            policy_projection=policy,
            candidates=candidates,
            already_buffer_selected=active.get("stock_codes", ()),
        )
        if selected.get("selectable") is not True:
            committed = self.ingress.commit_unclaimed_event_observation(
                observation=observation,
                expected_revision=preview["expected_revision"],
            )
            result["ingress_committed"] = committed.get("ok") is True
            result["reason"] = (
                _text(selected.get("reason"))
                if committed.get("ok") is True
                else _text(committed.get("reason"))
            )
            return result

        result["candidate_selected"] = True
        proposed_code = _stock_code(selected.get("selected_stock_code"))
        proposed_intent = _text(policy.get("effective_response"))
        ownership_read = self.ownership.read_snapshot()
        if ownership_read.get("ok") is not True:
            result["reason"] = _text(ownership_read.get("reason"))
            return result
        ownership_snapshot = ownership_read.get("snapshot")
        if not isinstance(ownership_snapshot, Mapping):
            result["reason"] = "ownership snapshot is unavailable"
            return result
        claimed = self.ownership.claim_batch_event_candidate(
            account_no=account_no,
            trading_day=trading_day,
            event_sequence=preview["event_sequence"],
            source_evidence=_source_evidence(observation, preview),
            selected_stock_code=proposed_code,
            response_intent=proposed_intent,
            detected_at=observation["observed_at"],
            expected_revision=ownership_snapshot["revision"],
        )
        if claimed.get("ok") is not True:
            result["reason"] = _text(claimed.get("reason"))
            return result

        claimed_event = claimed.get("event")
        ownership_verify = self.ownership.read_snapshot()
        verified_snapshot = ownership_verify.get("snapshot")
        verified_events = (
            verified_snapshot.get("events")
            if isinstance(verified_snapshot, Mapping)
            else None
        )
        if (
            ownership_verify.get("ok") is not True
            or not isinstance(verified_events, Mapping)
            or verified_events.get(claimed.get("event_id")) != claimed_event
        ):
            result["reason"] = "ownership claim read-back verification failed"
            return result

        actual_code = _stock_code(claimed.get("selected_stock_code"))
        result["selected_stock_code"] = actual_code
        result["claimed_response_intent"] = _text(claimed.get("response_intent"))
        result["ownership_claimed"] = claimed.get("changed") is True
        result["ownership_existing"] = claimed.get("changed") is not True
        ingress_commit = self.ingress.commit_event_observation(
            observation=observation,
            claimed_event=claimed_event,
            expected_revision=preview["expected_revision"],
        )
        if ingress_commit.get("ok") is not True:
            result["reason"] = _text(ingress_commit.get("reason"))
            return result
        ingress_verify = self.ingress.read_snapshot()
        ingress_snapshot = ingress_verify.get("snapshot")
        checkpoint = (
            _checkpoint_for(ingress_snapshot, account_no, trading_day)
            if isinstance(ingress_snapshot, Mapping)
            else None
        )
        if (
            ingress_verify.get("ok") is not True
            or checkpoint is None
            or checkpoint.get("last_confirmed_observation_id")
            != observation.get("observation_id")
            or checkpoint.get("last_event_sequence") != preview.get("event_sequence")
        ):
            result["reason"] = "ingress checkpoint read-back verification failed"
            return result
        result["ingress_committed"] = True
        result["reason"] = ""
        return result


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_runtime_source(path: Path, list_key: str) -> tuple[dict[str, object], str]:
    raw = path.read_bytes()
    parsed = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(parsed, dict):
        raise ValueError(f"{path.name} root must be an object")
    values = parsed.get(list_key)
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise ValueError(f"{path.name}.{list_key} must be a list of objects")
    return parsed, hashlib.sha256(raw).hexdigest().upper()


def _runtime_evidence(
    *, recovery_session_id: str, queue: Mapping[str, object], hashes: Mapping[str, str]
) -> dict[str, object]:
    revision = queue.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("order queue revision is invalid")
    return {
        "recovery_session_id": recovery_session_id,
        "queue_revision": revision,
        "order_queue_sha256": hashes["order_queue"],
        "positions_sha256": hashes["positions"],
        "fills_sha256": hashes["fills"],
    }


def _read_runtime_bundle(
    *, order_queue_path: Path, positions_path: Path, fills_path: Path
) -> dict[str, object]:
    queue, queue_hash = _read_runtime_source(order_queue_path, "orders")
    positions, positions_hash = _read_runtime_source(positions_path, "positions")
    fills, fills_hash = _read_runtime_source(fills_path, "fills")
    return {
        "queue": queue,
        "positions": positions,
        "fills": fills,
        "hashes": {
            "order_queue": queue_hash,
            "positions": positions_hash,
            "fills": fills_hash,
        },
    }


def _production_candidate_inputs(
    window: object,
    *,
    account_no: str,
    reconciled_stock_codes: set[str],
    positions_snapshot: Mapping[str, object],
    order_queue_snapshot: Mapping[str, object],
    project_root: Path,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    participants = {
        _stock_code(code)
        for code in auto_trade_current_session_operation_participant_codes(window)
        if _stock_code(code)
    }
    positions = positions_snapshot.get("positions")
    orders = order_queue_snapshot.get("orders")
    if not isinstance(positions, list) or not isinstance(orders, list):
        raise ValueError("candidate Runtime snapshot is invalid")
    repository = StockRepository(project_root=project_root)
    directories: dict[str, Path] = {}
    for stock_dir in repository.list_stock_dirs():
        code, _name = repository.parse_stock_folder(stock_dir)
        directories.setdefault(_stock_code(code), stock_dir)

    candidates: list[dict[str, object]] = []
    for position in positions:
        if not isinstance(position, Mapping):
            raise ValueError("position candidate is invalid")
        if _text(position.get("account_no")) != account_no:
            continue
        code = _stock_code(position.get("code") or position.get("stock_code"))
        if (
            code not in reconciled_stock_codes
            or code not in participants
            or _text(position.get("position_status")).upper() != "OPEN"
            or int(position.get("quantity") or 0) <= 0
        ):
            continue
        stock_dir = directories.get(code)
        state = read_json_dict(stock_dir / "state.json") if stock_dir else None
        config = read_json_dict(stock_dir / "config.json") if stock_dir else None
        stock_orders = [
            dict(order)
            for order in orders
            if isinstance(order, Mapping)
            and _stock_code(order.get("code") or order.get("stock_code")) == code
            and _text(order.get("account_no")) in {"", account_no}
        ]
        candidates.append(
            {
                "stock_code": code,
                "stock_dir": str(stock_dir or ""),
                "routine_instance_id": _text(
                    (config or {}).get("routine_instance_id")
                    or (state or {}).get("routine_instance_id")
                ),
                "is_auto_trade_target": True,
                "position": dict(position),
                "state": state if state else None,
                "config": config if config else None,
                "orders": stock_orders,
            }
        )
    codes = [_stock_code(candidate["stock_code"]) for candidate in candidates]
    pnl = project_current_stock_pnl_snapshot(codes, project_root=project_root)
    return pnl, candidates


def collect_main_window_stable_buffer_context(
    window: object,
    *,
    project_root: str | Path = PROJECT_ROOT,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
) -> dict[str, object]:
    """Collect one double-read stable Production observation without mutation."""

    try:
        selected_reader = getattr(window, "selected_account_no", None)
        account_no = _text(selected_reader()) if callable(selected_reader) else ""
        if not account_no:
            raise ValueError("selected account is unavailable")
        if getattr(window, "_main_budget_orderable_valid", None) is not True:
            raise ValueError("orderable amount and budget validation are incomplete")
        recovery = production_recovery_registry.snapshot()
        window_identity = getattr(window, "_production_recovery_identity", None)
        if (
            recovery is None
            or window_identity is None
            or recovery.identity != window_identity
            or recovery.account_status != ACCOUNT_COMPLETED
            or recovery.identity.account_no != account_no
            or recovery.identity.trading_day != datetime.now().date().isoformat()
        ):
            raise ValueError("current account Recovery is not complete")
        login_reader = getattr(getattr(window, "kiwoom_api", None), "login_session_id", None)
        login_session_id = _text(login_reader()) if callable(login_reader) else ""
        if login_session_id != recovery.identity.login_session_id:
            raise ValueError("login session does not match Recovery")
        reconciled = {
            _stock_code(item.stock_code)
            for item in recovery.stocks
            if item.stock_status == STOCK_RESTORED and not item.review_required
        }
        if len(reconciled) != len(recovery.stocks):
            raise ValueError("Recovery stock scope is incomplete")

        root = Path(project_root)
        queue_path = Path(order_queue_path)
        position_path = Path(positions_path)
        fill_path = Path(fills_path)
        before = _read_runtime_bundle(
            order_queue_path=queue_path,
            positions_path=position_path,
            fills_path=fill_path,
        )
        evidence_before = _runtime_evidence(
            recovery_session_id=recovery.identity.recovery_session_id,
            queue=before["queue"],
            hashes=before["hashes"],
        )
        consumption = project_account_auto_trade_budget_consumption(
            account_no=account_no,
            positions_path=position_path,
            order_queue_path=queue_path,
            recovery_complete=True,
            reconciled_stock_codes=reconciled,
        )
        contributors = collect_confirmed_contributing_buy_ids(
            account_no=account_no,
            positions_snapshot=before["positions"],
            order_queue_snapshot=before["queue"],
            fills_snapshot=before["fills"],
            reconciled_stock_codes=reconciled,
        )
        summary = collect_main_budget_summary()
        activity = project_main_budget_activity(summary, consumption)
        if activity.get("available") is not True or activity.get("entry_amount") is None:
            raise ValueError("buffer budget activity is unavailable")
        if contributors.get("available") is not True:
            raise ValueError(_text(contributors.get("reason")) or "BUY contributor evidence is unavailable")
        pnl_by_stock, candidates = _production_candidate_inputs(
            window,
            account_no=account_no,
            reconciled_stock_codes=reconciled,
            positions_snapshot=before["positions"],
            order_queue_snapshot=before["queue"],
            project_root=root,
        )
        after = _read_runtime_bundle(
            order_queue_path=queue_path,
            positions_path=position_path,
            fills_path=fill_path,
        )
        evidence_after = _runtime_evidence(
            recovery_session_id=recovery.identity.recovery_session_id,
            queue=after["queue"],
            hashes=after["hashes"],
        )
        built = build_stable_buffer_observation(
            account_no=account_no,
            trading_day=recovery.identity.trading_day,
            confirmed_entry_amount=activity["entry_amount"],
            contributing_buy_ids=contributors["contributing_buy_ids"],
            evidence_before=evidence_before,
            evidence_after=evidence_after,
            observed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        if built.get("available") is not True:
            raise ValueError(_text(built.get("reason")) or "stable observation is unavailable")
        return {
            "available": True,
            "reason": "",
            "observation": built["observation"],
            "budget_activity": activity,
            "pnl_by_stock": pnl_by_stock,
            "candidates": candidates,
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": _text(exc) or "stable Production observation is unavailable",
            "observation": None,
            "budget_activity": None,
            "pnl_by_stock": None,
            "candidates": None,
        }


def main_window_buffer_response_integration_ready(window: object) -> bool:
    """Reject incomplete/test-only Qt shells before touching Production readers."""

    return id(window) in _INTEGRATION_READY_WINDOW_IDS


def register_main_window_buffer_response_integration(window: object) -> None:
    """Mark a fully initialized MainWindow as eligible for the upper hook."""

    window_id = id(window)
    _INTEGRATION_READY_WINDOW_IDS.add(window_id)
    try:
        weakref.finalize(window, _INTEGRATION_READY_WINDOW_IDS.discard, window_id)
    except TypeError:
        pass


def _reconcile_collected_main_window_buffer_response(
    window: object,
    *,
    coordinator: BufferResponseCoordinator,
    collected: Mapping[str, object],
) -> dict[str, object]:
    observation = collected.get("observation")
    if not isinstance(observation, Mapping):
        return {"ok": False, "reason": "STABLE_OBSERVATION_UNAVAILABLE"}
    try:
        completion_projection = coordinator._completion_projector()
    except Exception as exc:
        completion_projection = {
            "evaluated": False,
            "blocked": True,
            "reason": _text(exc) or "CLOSE_COMPLETION_PROJECTION_FAILED",
        }
    lifecycle = coordinator.reconcile_completion_and_escalate(
        account_no=observation.get("account_no"),
        trading_day=observation.get("trading_day"),
        budget_activity=collected.get("budget_activity"),
        settings_surface=getattr(
            window, "_main_buffer_response_settings_surface", None
        ),
        pnl_by_stock=collected.get("pnl_by_stock"),
        candidates=collected.get("candidates"),
        completion_projection=completion_projection,
    )
    lifecycle["preparation"] = None
    lifecycle["dispatch"] = None
    lifecycle["preparation_resume"] = None
    lifecycle["resume_dispatch"] = None
    event_id = _text(lifecycle.get("selected_event_id"))
    if lifecycle.get("escalation_changed") is True and event_id:
        preparation = prepare_main_window_buffer_immediate_liquidation(
            window,
            event_id=event_id,
            ownership_service=coordinator.ownership,
            ingress_service=coordinator.ingress,
        )
        lifecycle["preparation"] = preparation
        if (
            isinstance(preparation, Mapping)
            and preparation.get("state") == "READY_FOR_IMMEDIATE_LIQUIDATION"
            and preparation.get("ready_for_liquidation") is True
        ):
            lifecycle["dispatch"] = dispatch_main_window_buffer_immediate_market_close(
                window,
                event_id=event_id,
                preparation_result=preparation,
                ownership_service=coordinator.ownership,
                ingress_service=coordinator.ingress,
            )
    elif lifecycle.get("reason") == "ACTIVE_IMMEDIATE_OWNERSHIP_PENDING":
        # Crash-safe reuse of the already established Recovery resume path.
        # This never selects another target; it only resumes the same durable
        # IMMEDIATE ownership and the dispatcher remains command-idempotent.
        lifecycle["preparation_resume"] = (
            resume_main_window_buffer_immediate_liquidation_events(
                window,
                ownership_service=coordinator.ownership,
                ingress_service=coordinator.ingress,
            )
        )
        lifecycle["resume_dispatch"] = (
            dispatch_ready_main_window_buffer_immediate_preparations(
                window,
                preparation_resume_result=lifecycle["preparation_resume"],
                ownership_service=coordinator.ownership,
                ingress_service=coordinator.ingress,
            )
        )
    return lifecycle


def reconcile_main_window_buffer_response_cycle(window: object) -> dict[str, object]:
    """Reuse one existing operation-cycle callback; create no timer or polling loop."""

    if not main_window_buffer_response_integration_ready(window):
        return {"ok": False, "reason": "BUFFER_RESPONSE_INTEGRATION_NOT_READY"}
    collected = collect_main_window_stable_buffer_context(window)
    if collected.get("available") is not True:
        return {
            "ok": False,
            "reason": _text(collected.get("reason")),
        }
    coordinator = getattr(window, "_buffer_response_coordinator", None)
    if not isinstance(coordinator, BufferResponseCoordinator):
        coordinator = BufferResponseCoordinator()
        setattr(window, "_buffer_response_coordinator", coordinator)
    return _reconcile_collected_main_window_buffer_response(
        window,
        coordinator=coordinator,
        collected=collected,
    )


def coordinate_main_window_buffer_response(
    window: object,
    *,
    chejan_result: Mapping[str, object] | object,
) -> dict[str, object]:
    """Run after the existing Chejan pipeline has finished all required writes."""

    if not isinstance(chejan_result, Mapping) or chejan_result.get("recorded") is not True:
        return _result("CHEJAN_PIPELINE_NOT_COMMITTED")
    if chejan_result.get("manual_reconciliation_required") is True:
        return _result("CHEJAN_RECONCILIATION_INCOMPLETE")
    collected = collect_main_window_stable_buffer_context(window)
    if collected.get("available") is not True:
        return _result(
            _text(collected.get("reason")),
            observed=True,
            stable=False,
        )
    coordinator = getattr(window, "_buffer_response_coordinator", None)
    if not isinstance(coordinator, BufferResponseCoordinator):
        coordinator = BufferResponseCoordinator()
        setattr(window, "_buffer_response_coordinator", coordinator)
    ownership_lifecycle = _reconcile_collected_main_window_buffer_response(
        window,
        coordinator=coordinator,
        collected=collected,
    )
    surface = getattr(window, "_main_buffer_response_settings_surface", None)
    result = coordinator.process_stable_observation(
        observation=collected["observation"],
        budget_activity=collected["budget_activity"],
        settings_surface=surface,
        pnl_by_stock=collected["pnl_by_stock"],
        candidates=collected["candidates"],
    )
    result["ownership_lifecycle"] = ownership_lifecycle
    result["early_close_dispatch"] = None
    result["immediate_liquidation_preparation"] = None
    result["immediate_liquidation_resume"] = None
    result["immediate_liquidation_dispatch"] = None
    result["immediate_liquidation_resume_dispatch"] = None
    if (
        result.get("ingress_committed") is True
        and result.get("claimed_response_intent") == "EARLY_CLOSE"
        and result.get("event_id")
    ):
        result["early_close_dispatch"] = dispatch_main_window_buffer_early_close(
            window,
            event_id=result["event_id"],
            ownership_service=coordinator.ownership,
        )
    if (
        result.get("ingress_committed") is True
        and result.get("claimed_response_intent")
        == "IMMEDIATE_LIQUIDATION_REQUIRED"
        and result.get("event_id")
    ):
        result["immediate_liquidation_preparation"] = (
            prepare_main_window_buffer_immediate_liquidation(
                window,
                event_id=result["event_id"],
                ownership_service=coordinator.ownership,
                ingress_service=coordinator.ingress,
            )
        )
        preparation = result["immediate_liquidation_preparation"]
        if (
            isinstance(preparation, Mapping)
            and preparation.get("state") == "READY_FOR_IMMEDIATE_LIQUIDATION"
            and preparation.get("ready_for_liquidation") is True
        ):
            result["immediate_liquidation_dispatch"] = (
                dispatch_main_window_buffer_immediate_market_close(
                    window,
                    event_id=result["event_id"],
                    preparation_result=preparation,
                    ownership_service=coordinator.ownership,
                    ingress_service=coordinator.ingress,
                )
            )
    elif result.get("ingress_committed") is True:
        lifecycle_resume = ownership_lifecycle.get("preparation_resume")
        if lifecycle_resume is not None:
            result["immediate_liquidation_resume"] = lifecycle_resume
            result["immediate_liquidation_resume_dispatch"] = (
                ownership_lifecycle.get("resume_dispatch")
            )
        else:
            result["immediate_liquidation_resume"] = (
                resume_main_window_buffer_immediate_liquidation_events(
                    window,
                    ownership_service=coordinator.ownership,
                    ingress_service=coordinator.ingress,
                )
            )
            result["immediate_liquidation_resume_dispatch"] = (
                dispatch_ready_main_window_buffer_immediate_preparations(
                    window,
                    preparation_resume_result=result["immediate_liquidation_resume"],
                    ownership_service=coordinator.ownership,
                    ingress_service=coordinator.ingress,
                )
            )
    result["chejan_pipeline_stage"] = _text(chejan_result.get("stage"))
    return result


__all__ = [
    "BufferResponseCoordinator",
    "collect_main_window_stable_buffer_context",
    "coordinate_main_window_buffer_response",
    "main_window_buffer_response_integration_ready",
    "reconcile_main_window_buffer_response_cycle",
    "register_main_window_buffer_response_integration",
]
