# -*- coding: utf-8 -*-
"""Stock-scoped lifecycle and isolated ledgers for Mock Validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from mock_validation_contract import (
    INSTANCE_ERROR,
    INSTANCE_VALIDATION_STOPPED,
    MOCK_BUDGET_POLICY_IMMEDIATE,
    MOCK_BUDGET_POLICY_NEXT_CYCLE,
    MOCK_BUDGET_POLICY_PRE_OPERATION,
    MOCK_BUDGET_STATE_APPLIED,
    MOCK_BUDGET_STATE_WAIT_FIRST_BUY,
    MOCK_BUDGET_STATE_WAIT_SELL,
    ORDER_CANCEL_PENDING,
    ORDER_CANCELED,
    ORDER_CREATED,
    ORDER_FILLED,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    SESSION_ENDED,
    SESSION_CLOSING,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    initial_session_document,
    instance_initial_buy_adjustment,
    instance_effective_settings,
    mock_instance_pre_start_editable,
    new_mock_identity,
    now_text,
    payload_hash,
    transition_mock_order,
    validate_instance_effective_settings,
    validate_instance_initial_buy_adjustment,
)
from mock_validation_repository import MockValidationRepository


def mock_instance_error_recovery_reset_allowed(
    execution: dict[str, Any], operation: dict[str, Any] | None
) -> bool:
    return (
        clean_text(execution.get("state")) == INSTANCE_ERROR
        and execution.get("progression_allowed") is False
        and isinstance(operation, dict)
        and clean_text(operation.get("state")) == SESSION_RUNNING
    )


def mock_routine_instance_unregister_eligibility(
    document: dict[str, Any], routine_instance_id: str
) -> dict[str, Any]:
    """Read-only eligibility for removing one Mock Routine registration."""

    instance_id = clean_text(routine_instance_id)
    execution = document.get("instance_execution")
    if not isinstance(execution, dict) or instance_id not in execution:
        return {"eligible": False, "reason": "MOCK_CONTEXT_TARGET_STALE"}
    if document.get("review", {}).get("review_required") is True:
        return {"eligible": False, "reason": "MOCK_REVIEW_UNRESOLVED"}
    if any(
        clean_text(item.get("routine_instance_id")) == instance_id
        and clean_text(item.get("state"))
        in {ORDER_CREATED, ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}
        for item in document.get("orders", ())
        if isinstance(item, dict)
    ):
        return {"eligible": False, "reason": "MOCK_ACTIVE_EXECUTION"}
    if any(
        clean_text(item.get("routine_instance_id")) == instance_id
        and int(item.get("holding_qty", 0) or 0) > 0
        for item in document.get("positions", ())
        if isinstance(item, dict)
    ):
        return {"eligible": False, "reason": "MOCK_POSITION_REMAINS"}
    lifecycle = document.get("mock_operation_lifecycle")
    current = lifecycle.get("current") if isinstance(lifecycle, dict) else None
    if isinstance(current, dict) and clean_text(current.get("state")) in {
        SESSION_RUNNING,
        SESSION_CLOSING,
    }:
        return {"eligible": False, "reason": "MOCK_OPERATION_ACTIVE"}
    operations = (
        lifecycle.get("instance_operations") if isinstance(lifecycle, dict) else None
    )
    operation = operations.get(instance_id) if isinstance(operations, dict) else None
    if (
        clean_text(execution[instance_id].get("state"))
        in {SESSION_RUNNING, SESSION_CLOSING}
        or (
            isinstance(operation, dict)
            and clean_text(operation.get("state"))
            in {SESSION_RUNNING, SESSION_CLOSING}
        )
    ):
        return {"eligible": False, "reason": "MOCK_INSTANCE_OPERATION_ACTIVE"}
    return {"eligible": True, "reason": ""}


class MockValidationSessionService:
    """Mock operation API with stock registration and Instance-local execution."""

    def __init__(
        self,
        repository: MockValidationRepository,
        *,
        now_factory: Callable[[], str] = now_text,
    ) -> None:
        self.repository = repository
        self._now = now_factory

    @staticmethod
    def _command(document: dict[str, Any], command_id: str) -> dict[str, Any] | None:
        value = document.get("applied_commands", {}).get(command_id)
        return deepcopy(value) if isinstance(value, dict) else None

    @staticmethod
    def _instance_ids(document: dict[str, Any]) -> set[str]:
        return set(document["instance_execution"])

    @staticmethod
    def _require_instance(document: dict[str, Any], instance_id: str) -> str:
        clean = clean_text(instance_id)
        if clean not in document["instance_execution"]:
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION")
        return clean

    def stop_active_instances_for_application_boundary(
        self,
        *,
        source: str,
    ) -> dict[str, Any]:
        """Reuse Validation Stop for active Instances at shutdown/restart."""

        boundary_source = clean_text(source)
        stopped: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for stock_code, session_id in sorted(self.repository.current_session_ids().items()):
            try:
                document = self.repository.read_session(session_id)
            except Exception as exc:
                errors.append(
                    {
                        "stock_code": clean_text(stock_code),
                        "validation_session_id": clean_text(session_id),
                        "routine_instance_id": "",
                        "reason": str(exc) or type(exc).__name__,
                    }
                )
                continue
            for instance_id in sorted(document.get("instance_execution", {})):
                execution = document["instance_execution"].get(instance_id, {})
                if execution.get("state") == INSTANCE_ERROR:
                    # ERROR recovery/reset remains an operator-owned contract;
                    # application restart must not consume or rewrite it.
                    continue
                lifecycle = document.get("mock_operation_lifecycle")
                operations = (
                    lifecycle.get("instance_operations")
                    if isinstance(lifecycle, dict)
                    else None
                )
                operation = operations.get(instance_id) if isinstance(operations, dict) else None
                operation_state = (
                    clean_text(operation.get("state"))
                    if isinstance(operation, dict)
                    else ""
                )
                if (
                    execution.get("state") not in {SESSION_RUNNING, SESSION_CLOSING}
                    and operation_state not in {SESSION_RUNNING, SESSION_CLOSING}
                ):
                    continue
                operation_id = (
                    clean_text(operation.get("operation_session_id"))
                    if isinstance(operation, dict)
                    else clean_text(execution.get("operation_session_id"))
                )
                command_id = deterministic_mock_identity(
                    "MC",
                    session_id,
                    instance_id,
                    operation_id or document.get("revision", 0),
                    boundary_source,
                    "APPLICATION_BOUNDARY_VALIDATION_STOP",
                )
                try:
                    result = self.stop_instance_validation(
                        session_id,
                        routine_instance_id=instance_id,
                        command_id=command_id,
                        source=boundary_source,
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "stock_code": clean_text(stock_code),
                            "validation_session_id": clean_text(session_id),
                            "routine_instance_id": clean_text(instance_id),
                            "reason": str(exc) or type(exc).__name__,
                        }
                    )
                    continue
                stopped.append(
                    {
                        "stock_code": clean_text(stock_code),
                        "validation_session_id": clean_text(session_id),
                        "routine_instance_id": clean_text(instance_id),
                        "operation_session_id": operation_id,
                        "result": result,
                    }
                )
                document = self.repository.read_session(session_id)
        return {
            "source": boundary_source,
            "stopped": tuple(stopped),
            "errors": tuple(errors),
        }

    @staticmethod
    def _require_progression(document: dict[str, Any], instance_id: str) -> str:
        clean = MockValidationSessionService._require_instance(document, instance_id)
        if document["session"]["state"] != SESSION_RUNNING:
            raise MockValidationError("MOCK_SESSION_NOT_RUNNING")
        if document["instance_execution"][clean].get("progression_allowed") is not True:
            raise MockValidationError("MOCK_INSTANCE_PROGRESSION_BLOCKED")
        return clean

    @staticmethod
    def _instance_operation(
        document: dict[str, Any], instance_id: str
    ) -> dict[str, Any] | None:
        lifecycle = document.get("mock_operation_lifecycle")
        if not isinstance(lifecycle, dict):
            return None
        operations = lifecycle.get("instance_operations")
        operation = operations.get(instance_id) if isinstance(operations, dict) else None
        if not isinstance(operation, dict):
            operation = lifecycle.get("current")
        return operation if isinstance(operation, dict) else None

    def _event(
        self,
        *,
        session_id: str,
        stock_code: str,
        event_type: str,
        timestamp: str,
        command_id: str,
        routine_instance_id: str = "",
        reason_code: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": deterministic_mock_identity("ME", session_id, command_id, event_type),
            "validation_session_id": session_id,
            "stock_code": stock_code,
            "routine_instance_id": clean_text(routine_instance_id),
            "event_type": event_type,
            "timestamp": timestamp,
            "reason_code": clean_text(reason_code),
            "payload": deepcopy(payload) if isinstance(payload, dict) else {},
        }
        return self.repository.append_event(event)

    def create_stock_session(
        self,
        *,
        reference_snapshot: dict[str, Any],
        effective_settings_by_instance: dict[str, Any] | None = None,
        validation_session_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = clean_text(validation_session_id) or new_mock_identity("MV")
        command = clean_text(command_id) or deterministic_mock_identity("MC", session_id, "CREATE")
        created_at = self._now()
        settings = self.repository.read_settings()
        document = initial_session_document(
            validation_session_id=session_id,
            reference_snapshot=reference_snapshot,
            created_at=created_at,
            mock_tax_enabled=settings["mock_tax_enabled"],
            mock_tax_rate=settings["mock_tax_rate"],
            effective_settings_by_instance=effective_settings_by_instance,
        )
        document["applied_commands"][command] = {
            "operation": "CREATE_SESSION",
            "applied_at": created_at,
            "entity_id": session_id,
        }
        result = self.repository.create_session(document)
        persisted = result["document"]
        self._event(
            session_id=session_id,
            stock_code=persisted["session"]["stock_code"],
            event_type="SESSION_CREATED",
            timestamp=persisted["session"]["created_at"],
            command_id=command,
            payload={"instance_count": len(persisted["instance_execution"])},
        )
        return result

    def set_instance_effective_settings(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        initial_buy: dict[str, Any] | None = None,
        operation_schedule: dict[str, Any] | None = None,
        operation_mode: str | None = None,
        manual_ats: dict[str, Any] | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one admitted Instance setting through the Mock repository only."""

        before = self.repository.read_session(session_id)
        instance_id = self._require_instance(before, routine_instance_id)
        if not mock_instance_pre_start_editable(before, instance_id):
            raise MockValidationError("MOCK_INSTANCE_SETTINGS_REQUIRE_WAITING")
        current = instance_effective_settings(before, instance_id)
        candidate = deepcopy(current)
        if initial_buy is not None:
            candidate["initial_buy"] = deepcopy(initial_buy)
        if operation_schedule is not None:
            candidate["operation_schedule"] = deepcopy(operation_schedule)
        if operation_mode is not None:
            candidate["operation_mode"] = clean_text(operation_mode).upper()
        if manual_ats is not None:
            candidate["manual_ats"] = deepcopy(manual_ats)
        checked = validate_instance_effective_settings(candidate)
        if checked == current:
            return {"changed": False, "document": before, "revision": before["revision"]}
        command = clean_text(command_id) or new_mock_identity("MC")
        frozen_hash = before["reference_snapshot"]["snapshot_hash"]

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            if document["reference_snapshot"]["snapshot_hash"] != frozen_hash:
                raise MockValidationError("MOCK_REFERENCE_IDENTITY_MISMATCH")
            settings_by_instance = document.setdefault(
                "effective_settings_by_instance",
                {
                    candidate_id: instance_effective_settings(document, candidate_id)
                    for candidate_id in document["instance_execution"]
                },
            )
            settings_by_instance[instance_id] = deepcopy(checked)
            document["applied_commands"][command] = {
                "operation": "SET_INSTANCE_EFFECTIVE_SETTINGS",
                "applied_at": self._now(),
                "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )
        saved = instance_effective_settings(result["document"], instance_id)
        if saved != checked:
            raise MockValidationError("MOCK_INSTANCE_SETTINGS_READ_BACK_MISMATCH")
        if result["document"]["reference_snapshot"]["snapshot_hash"] != frozen_hash:
            raise MockValidationError("MOCK_REFERENCE_IDENTITY_MISMATCH")
        return result

    def set_instance_initial_buy(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        mode: str,
        value: int,
        apply_policy: str,
        expected_revision: int,
        expected_operation_session_id: str = "",
        requested_at: str = "",
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one Mock start-budget edit with Production-parity timing."""

        before = self.repository.read_session(session_id)
        if int(before.get("revision", -1)) != int(expected_revision):
            raise MockValidationError("MOCK_INSTANCE_SETTINGS_REVISION_CONFLICT")
        instance_id = self._require_instance(before, routine_instance_id)
        requested_mode = clean_text(mode).upper()
        policy = clean_text(apply_policy).upper()
        try:
            requested_value = int(value)
        except (TypeError, ValueError) as exc:
            raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_VALUE_INVALID") from exc
        if requested_mode not in {"QUANTITY", "AMOUNT"}:
            raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_MODE_INVALID")
        if requested_value <= 0 or requested_value > 99_999_999:
            raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_VALUE_INVALID")
        current = instance_effective_settings(before, instance_id)
        if current["initial_buy"]["mode"] != requested_mode:
            raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_MODE_CHANGED")
        execution_state = clean_text(
            before["instance_execution"][instance_id].get("state")
        ).upper()
        operation = self._instance_operation(before, instance_id)
        operation_id = (
            clean_text(operation.get("operation_session_id"))
            if isinstance(operation, dict)
            else ""
        )
        if mock_instance_pre_start_editable(before, instance_id):
            if policy != MOCK_BUDGET_POLICY_PRE_OPERATION:
                raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_APPLY_POLICY_INVALID")
            if clean_text(expected_operation_session_id):
                raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
        elif execution_state == SESSION_RUNNING:
            if policy not in {
                MOCK_BUDGET_POLICY_IMMEDIATE,
                MOCK_BUDGET_POLICY_NEXT_CYCLE,
            }:
                raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_APPLY_POLICY_INVALID")
            if (
                not operation_id
                or clean_text(operation.get("state")).upper() != SESSION_RUNNING
                or operation_id != clean_text(expected_operation_session_id)
            ):
                raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
        else:
            raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_CHANGE_STATE_INVALID")

        checked = validate_instance_effective_settings(
            {
                **current,
                "initial_buy": {
                    "mode": requested_mode,
                    "value": requested_value,
                },
            }
        )
        command = clean_text(command_id) or new_mock_identity("MC")
        confirmed_at = self._now()
        request_timestamp = clean_text(requested_at) or confirmed_at
        adjustment = None
        if execution_state == SESSION_RUNNING:
            adjustment = validate_instance_initial_buy_adjustment(
                {
                    "version": 1,
                    "request_id": command,
                    "routine_instance_id": instance_id,
                    "operation_session_id": operation_id,
                    "mode": requested_mode,
                    "requested_value": requested_value,
                    "previous_value": int(current["initial_buy"]["value"]),
                    "apply_policy": policy,
                    "state": (
                        MOCK_BUDGET_STATE_WAIT_FIRST_BUY
                        if policy == MOCK_BUDGET_POLICY_IMMEDIATE
                        else MOCK_BUDGET_STATE_WAIT_SELL
                    ),
                    "requested_at": request_timestamp,
                    "confirmed_at": confirmed_at,
                }
            )
        frozen_hash = before["reference_snapshot"]["snapshot_hash"]

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            if document["reference_snapshot"]["snapshot_hash"] != frozen_hash:
                raise MockValidationError("MOCK_REFERENCE_IDENTITY_MISMATCH")
            if instance_effective_settings(document, instance_id) != current:
                raise MockValidationError("MOCK_INSTANCE_SETTINGS_REVISION_CONFLICT")
            current_operation = self._instance_operation(document, instance_id)
            current_operation_id = (
                clean_text(current_operation.get("operation_session_id"))
                if isinstance(current_operation, dict)
                else ""
            )
            if current_operation_id != operation_id:
                raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
            settings_by_instance = document.setdefault(
                "effective_settings_by_instance",
                {
                    candidate_id: instance_effective_settings(
                        document, candidate_id
                    )
                    for candidate_id in document["instance_execution"]
                },
            )
            settings_by_instance[instance_id] = deepcopy(checked)
            adjustments = document.setdefault(
                "initial_buy_adjustments_by_instance", {}
            )
            if adjustment is None:
                adjustments.pop(instance_id, None)
            else:
                adjustments[instance_id] = deepcopy(adjustment)
            document["applied_commands"][command] = {
                "operation": "SET_INSTANCE_INITIAL_BUY",
                "applied_at": confirmed_at,
                "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )
        saved = instance_effective_settings(result["document"], instance_id)
        if saved != checked:
            raise MockValidationError("MOCK_INSTANCE_SETTINGS_READ_BACK_MISMATCH")
        if result["document"]["reference_snapshot"]["snapshot_hash"] != frozen_hash:
            raise MockValidationError("MOCK_REFERENCE_IDENTITY_MISMATCH")
        return {
            **result,
            "adjustment": instance_initial_buy_adjustment(
                result["document"], instance_id
            ),
        }

    def transition_instance_initial_buy_for_signal(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        signal: str,
        signal_id: str,
        observed_at: str,
    ) -> dict[str, Any]:
        """Advance one active adjustment only for an accepted standard signal."""

        normalized_signal = clean_text(signal).upper()
        clean_signal_id = clean_text(signal_id)
        if normalized_signal not in {"BUY", "SELL"} or not clean_signal_id:
            return {"changed": False, "reason": "SIGNAL_NOT_ACCEPTED"}
        before = self.repository.read_session(session_id)
        instance_id = self._require_instance(before, routine_instance_id)
        adjustment = instance_initial_buy_adjustment(before, instance_id)
        if adjustment is None:
            return {"changed": False, "reason": "NO_ACTIVE_ADJUSTMENT"}
        operation = self._instance_operation(before, instance_id)
        if (
            not isinstance(operation, dict)
            or clean_text(operation.get("operation_session_id"))
            != adjustment["operation_session_id"]
            or clean_text(operation.get("state")).upper() != SESSION_RUNNING
        ):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
        previous_state = adjustment["state"]
        next_state = previous_state
        if previous_state == MOCK_BUDGET_STATE_WAIT_SELL and normalized_signal == "SELL":
            next_state = MOCK_BUDGET_STATE_WAIT_FIRST_BUY
        elif previous_state == MOCK_BUDGET_STATE_WAIT_FIRST_BUY and normalized_signal == "BUY":
            next_state = MOCK_BUDGET_STATE_APPLIED
        if next_state == previous_state:
            return {
                "changed": False,
                "before": previous_state,
                "after": next_state,
                "reason": "SIGNAL_DOES_NOT_ADVANCE_STATE",
            }
        timestamp = clean_text(observed_at) or self._now()
        next_adjustment = deepcopy(adjustment)
        next_adjustment.update(
            {
                "state": next_state,
                "last_transition_at": timestamp,
                "last_transition_signal": normalized_signal,
                "last_transition_signal_id": clean_signal_id,
            }
        )
        if next_state == MOCK_BUDGET_STATE_WAIT_FIRST_BUY:
            next_adjustment.update(
                {"sell_observed_at": timestamp, "sell_signal_id": clean_signal_id}
            )
        else:
            next_adjustment.update(
                {"applied_at": timestamp, "applied_signal_id": clean_signal_id}
            )
        checked = validate_instance_initial_buy_adjustment(next_adjustment)
        transition_command = deterministic_mock_identity(
            "MC", session_id, instance_id, adjustment["request_id"], clean_signal_id
        )

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            current = instance_initial_buy_adjustment(document, instance_id)
            if current != adjustment:
                raise MockValidationError("MOCK_INSTANCE_INITIAL_BUY_ADJUSTMENT_STALE")
            document["initial_buy_adjustments_by_instance"][instance_id] = deepcopy(
                checked
            )
            document["applied_commands"][transition_command] = {
                "operation": "TRANSITION_INSTANCE_INITIAL_BUY",
                "applied_at": timestamp,
                "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )
        return {
            "changed": True,
            "before": previous_state,
            "after": next_state,
            "document": result["document"],
        }

    def start_stock_mock_session(self, session_id: str, *, command_id: str | None = None) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"started": False, "duplicate": True, "document": before, "command": duplicate}
        if before["session"]["state"] != SESSION_WAITING:
            raise MockValidationError("MOCK_SESSION_START_STATE_INVALID")
        started_at = self._now()
        start_identity = deterministic_mock_identity("MS", session_id, before["session"]["session_generation"], command)

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["session"].update(
                {"state": SESSION_RUNNING, "started_at": started_at, "ended_at": "", "start_identity": start_identity}
            )
            for item in document["instance_execution"].values():
                if item.get("state") in {INSTANCE_ERROR, INSTANCE_VALIDATION_STOPPED}:
                    continue
                item.update({"state": SESSION_RUNNING, "started_at": started_at, "progression_allowed": True})
            document["applied_commands"][command] = {
                "operation": "START_SESSION", "applied_at": started_at, "entity_id": start_identity,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        self._event(
            session_id=session_id,
            stock_code=result["document"]["session"]["stock_code"],
            event_type="SESSION_STARTED",
            timestamp=started_at,
            command_id=command,
            payload={"start_identity": start_identity},
        )
        return {"started": True, "duplicate": False, **result}

    def stop_for_instance_error(
        self,
        session_id: str,
        *,
        source_routine_instance_id: str,
        reason_code: str,
        reason: str,
        command_id: str | None = None,
        source_category: str = "MOCK_SESSION_SERVICE",
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        source_id = self._require_instance(before, source_routine_instance_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"stopped": False, "duplicate": True, "document": before, "command": duplicate}
        if before["session"]["state"] == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_SESSION_IMMUTABLE")
        occurred_at = self._now()
        before_state = clean_text(before["instance_execution"][source_id].get("state"))
        operation = self._instance_operation(before, source_id)
        operation_identity = (
            clean_text(operation.get("operation_session_id"))
            if isinstance(operation, dict)
            else ""
        ) or clean_text(before["session"].get("start_identity"))

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            item = document["instance_execution"][source_id]
            item.update(
                {
                    "state": INSTANCE_ERROR,
                    "progression_allowed": False,
                    "error_code": clean_text(reason_code) or "MOCK_INSTANCE_ERROR",
                    "error_reason": clean_text(reason),
                    "error_occurred_at": occurred_at,
                    "error_cleared_at": "",
                }
            )
            document["applied_commands"][command] = {
                "operation": "STOP_FOR_INSTANCE_ERROR", "applied_at": occurred_at, "entity_id": source_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        session = result["document"]["session"]
        after_state = clean_text(
            result["document"]["instance_execution"][source_id].get("state")
        )
        self._event(
            session_id=session_id,
            stock_code=session["stock_code"],
            event_type="INSTANCE_ERROR",
            timestamp=occurred_at,
            command_id=command,
            routine_instance_id=source_id,
            reason_code=reason_code,
            payload={
                "reason": clean_text(reason),
                "before_state": before_state,
                "after_state": after_state,
                "operation_identity": operation_identity,
                "source_category": clean_text(source_category) or "MOCK_SESSION_SERVICE",
                "before_revision": int(before.get("revision", 0) or 0),
                "after_revision": int(result["document"].get("revision", 0) or 0),
            },
        )
        return {"stopped": True, "duplicate": False, **result}

    def stop_instance_validation(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        command_id: str | None = None,
        source: str = "OPERATOR_UI",
        requested_at: str = "",
    ) -> dict[str, Any]:
        """Freeze one Mock Instance after cancelling all of its live orders."""

        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        instance_id = self._require_instance(before, routine_instance_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {
                "stopped": False,
                "duplicate": True,
                "document": before,
                "command": duplicate,
            }
        if before["session"]["state"] == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_SESSION_IMMUTABLE")
        before_state = clean_text(before["instance_execution"][instance_id].get("state"))
        lifecycle_before = before.get("mock_operation_lifecycle")
        operations_before = (
            lifecycle_before.get("instance_operations")
            if isinstance(lifecycle_before, dict)
            else None
        )
        operation_before = (
            deepcopy(operations_before.get(instance_id))
            if isinstance(operations_before, dict)
            and isinstance(operations_before.get(instance_id), dict)
            else None
        )
        boundary_source = clean_text(source) in {
            "APPLICATION_SHUTDOWN",
            "APPLICATION_RESTART_RECOVERY",
        }
        operation_before_state = (
            clean_text(operation_before.get("state"))
            if isinstance(operation_before, dict)
            else ""
        )
        if (
            before_state not in {SESSION_RUNNING, SESSION_CLOSING, INSTANCE_ERROR}
            and not (
                boundary_source
                and operation_before_state in {SESSION_RUNNING, SESSION_CLOSING}
            )
        ):
            raise MockValidationError("MOCK_INSTANCE_VALIDATION_STOP_STATE_INVALID")
        requested = clean_text(requested_at) or self._now()
        completed_at = self._now()
        cancellable_states = {
            ORDER_CREATED,
            ORDER_OPEN,
            ORDER_PARTIAL_FILL,
            ORDER_CANCEL_PENDING,
        }
        target_order_ids = tuple(
            clean_text(item.get("mock_order_id"))
            for item in before.get("orders", ())
            if item.get("routine_instance_id") == instance_id
            and item.get("state") in cancellable_states
        )
        target_order_states = {
            clean_text(item.get("mock_order_id")): clean_text(item.get("state"))
            for item in before.get("orders", ())
            if clean_text(item.get("mock_order_id")) in target_order_ids
        }
        position_before = next(
            deepcopy(item)
            for item in before["positions"]
            if item.get("routine_instance_id") == instance_id
        )
        pnl_before = next(
            deepcopy(item)
            for item in before["pnl"]
            if item.get("routine_instance_id") == instance_id
        )

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            for index, order in enumerate(document["orders"]):
                if (
                    order.get("routine_instance_id") != instance_id
                    or order.get("state") not in cancellable_states
                ):
                    continue
                transitioned = order
                if transitioned["state"] in {ORDER_OPEN, ORDER_PARTIAL_FILL}:
                    transitioned = transition_mock_order(
                        transitioned,
                        ORDER_CANCEL_PENDING,
                        occurred_at=completed_at,
                    )
                if transitioned["state"] in {ORDER_CREATED, ORDER_CANCEL_PENDING}:
                    transitioned = transition_mock_order(
                        transitioned,
                        ORDER_CANCELED,
                        occurred_at=completed_at,
                    )
                transitioned["resting"] = False
                transitioned["reserved_budget"] = 0
                document["orders"][index] = transitioned

            execution = document["instance_execution"][instance_id]
            execution.update(
                {
                    "state": INSTANCE_VALIDATION_STOPPED,
                    "progression_allowed": False,
                }
            )
            lifecycle = document.get("mock_operation_lifecycle")
            current = lifecycle.get("current") if isinstance(lifecycle, dict) else None
            operations = (
                lifecycle.get("instance_operations")
                if isinstance(lifecycle, dict)
                else None
            )
            operation = operations.get(instance_id) if isinstance(operations, dict) else None
            if isinstance(operation, dict):
                operation.update(
                    {
                        "state": INSTANCE_VALIDATION_STOPPED,
                        "ended_at": completed_at,
                        "outcome": INSTANCE_VALIDATION_STOPPED,
                        "close_source": "VALIDATION_STOP",
                        "close_reason": "사용자 검증정지",
                    }
                )
            sibling_active = isinstance(operations, dict) and any(
                key != instance_id
                and isinstance(item, dict)
                and item.get("state") in {SESSION_RUNNING, SESSION_CLOSING}
                for key, item in operations.items()
            )
            if (
                boundary_source
                and not sibling_active
                and isinstance(current, dict)
                and current.get("state") in {SESSION_RUNNING, SESSION_CLOSING}
            ):
                current.update(
                    {
                        "state": INSTANCE_VALIDATION_STOPPED,
                        "ended_at": completed_at,
                        "outcome": INSTANCE_VALIDATION_STOPPED,
                        "close_source": clean_text(source),
                        "close_reason": "프로그램 경계 모의검증 종료",
                    }
                )
            stock_operation_active = isinstance(current, dict) and current.get("state") in {
                SESSION_RUNNING,
                SESSION_CLOSING,
            }
            if not stock_operation_active and not sibling_active:
                document["session"].update(
                    {"state": SESSION_WAITING, "started_at": "", "start_identity": ""}
                )
            document["applied_commands"][command] = {
                "operation": "VALIDATION_STOP_INSTANCE",
                "applied_at": completed_at,
                "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )
        session = result["document"]["session"]
        evidence = {
            "requested_at": requested,
            "completed_at": completed_at,
            "before_state": before_state,
            "after_state": INSTANCE_VALIDATION_STOPPED,
            "cancelled_order_count": len(target_order_ids),
            "cancelled_order_ids": list(target_order_ids),
            "remaining_holding_qty": position_before.get("holding_qty", 0),
            "position_snapshot": position_before,
            "pnl_snapshot": pnl_before,
            "source": clean_text(source) or "OPERATOR_UI",
            "result": "COMPLETED",
        }
        if evidence["source"] in {
            "APPLICATION_SHUTDOWN",
            "APPLICATION_RESTART_RECOVERY",
        }:
            evidence["operation_snapshot"] = operation_before
        self._event(
            session_id=session_id,
            stock_code=session["stock_code"],
            event_type="VALIDATION_STOP_REQUESTED",
            timestamp=requested,
            command_id=f"{command}:REQUESTED",
            routine_instance_id=instance_id,
            payload={
                "requested_at": requested,
                "before_state": before_state,
                "source": evidence["source"],
            },
        )
        for order_id in target_order_ids:
            if target_order_states.get(order_id) in {ORDER_OPEN, ORDER_PARTIAL_FILL}:
                self._event(
                    session_id=session_id,
                    stock_code=session["stock_code"],
                    event_type="VIRTUAL_ORDER_CANCEL_PENDING",
                    timestamp=completed_at,
                    command_id=f"{command}:ORDER:{order_id}:PENDING",
                    routine_instance_id=instance_id,
                    payload={
                        "mock_order_id": order_id,
                        "source": "VALIDATION_STOP",
                    },
                )
            self._event(
                session_id=session_id,
                stock_code=session["stock_code"],
                event_type="VIRTUAL_ORDER_CANCELED",
                timestamp=completed_at,
                command_id=f"{command}:ORDER:{order_id}:CANCELED",
                routine_instance_id=instance_id,
                payload={
                    "mock_order_id": order_id,
                    "source": "VALIDATION_STOP",
                },
            )
        self._event(
            session_id=session_id,
            stock_code=session["stock_code"],
            event_type="VALIDATION_STOP_COMPLETED",
            timestamp=completed_at,
            command_id=f"{command}:COMPLETED",
            routine_instance_id=instance_id,
            payload=evidence,
        )
        return {
            "stopped": True,
            "duplicate": False,
            "cancelled_order_count": len(target_order_ids),
            "evidence": evidence,
            **result,
        }

    def reset_routine_instance(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Reset one Mock Instance without changing sibling or Production state."""

        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        instance_id = self._require_instance(before, routine_instance_id)
        lifecycle = before.get("mock_operation_lifecycle")
        operation = None
        if isinstance(lifecycle, dict) and "instance_operations" in lifecycle:
            operations = lifecycle.get("instance_operations")
            if not isinstance(operations, dict):
                raise MockValidationError("MOCK_INSTANCE_OPERATION_LEDGER_INVALID")
            operation = operations.get(instance_id)
            if operation is not None and (
                not isinstance(operation, dict)
                or clean_text(operation.get("routine_instance_id")) != instance_id
            ):
                raise MockValidationError("MOCK_INSTANCE_OPERATION_IDENTITY_MISMATCH")
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {
                "reset": False,
                "duplicate": True,
                "document": before,
                "command": duplicate,
            }
        if before["session"]["state"] == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_SESSION_IMMUTABLE")
        execution = before["instance_execution"][instance_id]
        if execution.get("state") == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_INSTANCE_IMMUTABLE")
        error_recovery_reset = mock_instance_error_recovery_reset_allowed(
            execution, operation
        )
        if (
            execution.get("state") in {SESSION_RUNNING, SESSION_CLOSING}
            or (
                isinstance(operation, dict)
                and operation.get("state") in {SESSION_RUNNING, SESSION_CLOSING}
                and not error_recovery_reset
            )
        ):
            raise MockValidationError("MOCK_INSTANCE_OPERATION_ACTIVE")
        reset_at = self._now()
        cancellable_states = {
            ORDER_CREATED,
            ORDER_OPEN,
            ORDER_PARTIAL_FILL,
            ORDER_CANCEL_PENDING,
        }
        target_order_ids = tuple(
            clean_text(item.get("mock_order_id"))
            for item in before.get("orders", ())
            if error_recovery_reset
            and item.get("routine_instance_id") == instance_id
            and item.get("state") in cancellable_states
        )
        target_order_states = {
            clean_text(item.get("mock_order_id")): clean_text(item.get("state"))
            for item in before.get("orders", ())
            if clean_text(item.get("mock_order_id")) in target_order_ids
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            if error_recovery_reset:
                for index, order in enumerate(document["orders"]):
                    if (
                        order.get("routine_instance_id") != instance_id
                        or order.get("state") not in cancellable_states
                    ):
                        continue
                    transitioned = order
                    if transitioned["state"] in {ORDER_OPEN, ORDER_PARTIAL_FILL}:
                        transitioned = transition_mock_order(
                            transitioned,
                            ORDER_CANCEL_PENDING,
                            occurred_at=reset_at,
                        )
                    if transitioned["state"] in {
                        ORDER_CREATED,
                        ORDER_CANCEL_PENDING,
                    }:
                        transitioned = transition_mock_order(
                            transitioned,
                            ORDER_CANCELED,
                            occurred_at=reset_at,
                        )
                    transitioned["resting"] = False
                    transitioned["reserved_budget"] = 0
                    document["orders"][index] = transitioned
            document["orders"] = [
                item
                for item in document["orders"]
                if item.get("routine_instance_id") != instance_id
            ]
            document["fills"] = [
                item
                for item in document["fills"]
                if item.get("routine_instance_id") != instance_id
            ]
            position = next(
                item
                for item in document["positions"]
                if item.get("routine_instance_id") == instance_id
            )
            position.update(
                {
                    "holding_qty": 0,
                    "available_qty": 0,
                    "average_price": 0,
                    "realized_cost_basis": 0,
                    "updated_at": reset_at,
                }
            )
            pnl = next(
                item
                for item in document["pnl"]
                if item.get("routine_instance_id") == instance_id
            )
            pnl.update(
                {
                    "realized_pnl": 0,
                    "unrealized_pnl": 0,
                    "gross_pnl": 0,
                    "commission": 0,
                    "mock_tax": 0,
                    "net_pnl": 0,
                    "updated_at": reset_at,
                }
            )
            execution = document["instance_execution"][instance_id]
            execution.update(
                {
                    "state": SESSION_WAITING,
                    "started_at": "",
                    "progression_allowed": False,
                    "operation_session_id": "",
                    "operation_started_at": "",
                    "error_code": "",
                    "error_reason": "",
                    "error_occurred_at": "",
                    "error_cleared_at": reset_at,
                }
            )
            document["cycle_state_by_instance"][instance_id] = {}
            document["progression_by_instance"][instance_id] = {}
            adjustments = document.setdefault(
                "initial_buy_adjustments_by_instance", {}
            )
            adjustments.pop(instance_id, None)
            lifecycle = document.get("mock_operation_lifecycle")
            current = lifecycle.get("current") if isinstance(lifecycle, dict) else None
            if isinstance(current, dict):
                liquidation = current.get("liquidation_by_instance")
                if isinstance(liquidation, dict):
                    liquidation.pop(instance_id, None)
            if isinstance(lifecycle, dict):
                operations = lifecycle.get("instance_operations")
                if isinstance(operations, dict):
                    operations.pop(instance_id, None)
                    instance_active = any(
                        isinstance(item, dict)
                        and item.get("state") in {SESSION_RUNNING, SESSION_CLOSING}
                        for item in operations.values()
                    )
                    stock_active = isinstance(current, dict) and current.get("state") in {
                        SESSION_RUNNING,
                        SESSION_CLOSING,
                    }
                    if not instance_active and not stock_active:
                        document["session"].update(
                            {"state": SESSION_WAITING, "started_at": "", "start_identity": ""}
                        )
            document["applied_commands"][command] = {
                "operation": "RESET_INSTANCE",
                "applied_at": reset_at,
                "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )
        for order_id in target_order_ids:
            if target_order_states.get(order_id) in {ORDER_OPEN, ORDER_PARTIAL_FILL}:
                self._event(
                    session_id=session_id,
                    stock_code=result["document"]["session"]["stock_code"],
                    event_type="VIRTUAL_ORDER_CANCEL_PENDING",
                    timestamp=reset_at,
                    command_id=f"{command}:ORDER:{order_id}:PENDING",
                    routine_instance_id=instance_id,
                    payload={"mock_order_id": order_id, "source": "ERROR_RESET"},
                )
            self._event(
                session_id=session_id,
                stock_code=result["document"]["session"]["stock_code"],
                event_type="VIRTUAL_ORDER_CANCELED",
                timestamp=reset_at,
                command_id=f"{command}:ORDER:{order_id}:CANCELED",
                routine_instance_id=instance_id,
                payload={"mock_order_id": order_id, "source": "ERROR_RESET"},
            )
        self._event(
            session_id=session_id,
            stock_code=result["document"]["session"]["stock_code"],
            event_type="INSTANCE_RESET",
            timestamp=reset_at,
            command_id=command,
            routine_instance_id=instance_id,
            payload=(
                {
                    "error_recovery_reset": True,
                    "cancelled_order_count": len(target_order_ids),
                    "cancelled_order_ids": list(target_order_ids),
                }
                if error_recovery_reset
                else None
            ),
        )
        return {"reset": True, "duplicate": False, **result}

    def reset_stock_session(self, session_id: str, *, command_id: str | None = None) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"reset": False, "duplicate": True, "document": before, "command": duplicate}
        if before["session"]["state"] == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_SESSION_IMMUTABLE")
        reset_at = self._now()
        settings = self.repository.read_settings()
        operation_root = before.get("mock_operation_lifecycle")
        had_operation = (
            isinstance(operation_root, dict)
            and isinstance(operation_root.get("current"), dict)
        )

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["initial_buy_adjustments_by_instance"] = {}
            document["session"].update(
                {
                    "state": SESSION_WAITING,
                    "session_generation": int(document["session"]["session_generation"]) + 1,
                    "started_at": "",
                    "ended_at": "",
                    "start_identity": "",
                    "mock_tax_enabled": settings["mock_tax_enabled"],
                    "mock_tax_rate": settings["mock_tax_rate"],
                }
            )
            document["orders"] = []
            document["fills"] = []
            for item in document["positions"]:
                item.update({"holding_qty": 0, "available_qty": 0, "average_price": 0, "realized_cost_basis": 0, "updated_at": reset_at})
            for item in document["pnl"]:
                item.update({"realized_pnl": 0, "unrealized_pnl": 0, "gross_pnl": 0, "commission": 0, "mock_tax": 0, "net_pnl": 0, "updated_at": reset_at})
            document["review"] = {
                "review_required": False, "review_reason": "", "source_routine_instance_id": "",
                "occurred_at": "", "resolved_at": reset_at, "resolution": "SESSION_RESET",
            }
            for instance_id, item in document["instance_execution"].items():
                item.update({
                    "state": SESSION_WAITING,
                    "started_at": "",
                    "progression_allowed": False,
                    "operation_session_id": "",
                    "operation_started_at": "",
                    "error_code": "",
                    "error_reason": "",
                    "error_occurred_at": "",
                    "error_cleared_at": reset_at,
                })
                document["cycle_state_by_instance"][instance_id] = {}
                document["progression_by_instance"][instance_id] = {}
            lifecycle = document.get("mock_operation_lifecycle")
            if isinstance(lifecycle, dict):
                current = lifecycle.get("current")
                if isinstance(current, dict):
                    reset_record = deepcopy(current)
                    reset_record.update({
                        "state": "RESET",
                        "outcome": "RESET",
                        "ended_at": reset_at,
                        "reset_at": reset_at,
                    })
                    if not any(
                        item.get("operation_session_id") == reset_record.get("operation_session_id")
                        for item in lifecycle.get("history", [])
                        if isinstance(item, dict)
                    ):
                        lifecycle.setdefault("history", []).append(reset_record)
                lifecycle["current"] = None
                lifecycle.setdefault("commands", {})[command] = {
                    "operation": "OPERATION_RESET",
                    "applied_at": reset_at,
                    "entity_id": str(document["session"]["session_generation"]),
                }
            document["applied_commands"][command] = {
                "operation": "RESET_SESSION", "applied_at": reset_at,
                "entity_id": str(document["session"]["session_generation"]),
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        self._event(
            session_id=session_id,
            stock_code=result["document"]["session"]["stock_code"],
            event_type="SESSION_RESET",
            timestamp=reset_at,
            command_id=command,
            payload={"session_generation": result["document"]["session"]["session_generation"]},
        )
        if had_operation:
            self._event(
                session_id=session_id,
                stock_code=result["document"]["session"]["stock_code"],
                event_type="OPERATION_RESET",
                timestamp=reset_at,
                command_id=command,
                payload={"session_generation": result["document"]["session"]["session_generation"]},
            )
        return {"reset": True, "duplicate": False, **result}

    def sync_common_tax_to_waiting_session(
        self,
        session_id: str,
        *,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        common = dict(settings or self.repository.read_settings())
        before = self.repository.read_session(session_id)
        if before["session"]["state"] != SESSION_WAITING:
            return {"changed": False, "skipped": True, "document": before}

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["session"]["mock_tax_enabled"] = bool(
                common["mock_tax_enabled"]
            )
            document["session"]["mock_tax_rate"] = common["mock_tax_rate"]
            return document

        return self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )

    def set_common_tax_settings(
        self,
        *,
        enabled: bool,
        rate: int | float,
    ) -> dict[str, Any]:
        changed_at = self._now()
        saved = self.repository.write_settings(
            mock_tax_enabled=bool(enabled),
            mock_tax_rate=rate,
            updated_at=changed_at,
        )
        settings = saved["document"]
        synchronized: list[str] = []
        preserved: list[str] = []
        for stock_code, session_id in sorted(
            self.repository.current_session_ids().items()
        ):
            result = self.sync_common_tax_to_waiting_session(
                session_id,
                settings=settings,
            )
            target = synchronized if result.get("skipped") is not True else preserved
            target.append(stock_code)
        return {
            "changed": bool(saved.get("changed")),
            "settings": settings,
            "waiting_synchronized": tuple(synchronized),
            "active_preserved": tuple(preserved),
        }

    def record_unregister_event(
        self,
        session_id: str,
        *,
        event_type: str,
        destination: str,
        command_id: str,
        reason_code: str = "",
        payload: dict[str, Any] | None = None,
        routine_instance_id: str = "",
    ) -> dict[str, Any]:
        """Append idempotent Mock-owned evidence for registration removal."""

        if event_type not in {"RETURN_REQUESTED", "RETURN_COMPLETED"}:
            raise MockValidationError("MOCK_UNREGISTER_EVENT_TYPE_INVALID")
        document = self.repository.read_session(session_id)
        timestamp = self._now()
        details = {"destination": clean_text(destination)}
        if isinstance(payload, dict):
            details.update(deepcopy(payload))
        return self._event(
            session_id=session_id,
            stock_code=document["session"]["stock_code"],
            event_type=event_type,
            timestamp=timestamp,
            command_id=clean_text(command_id),
            reason_code=clean_text(reason_code),
            payload=details,
            routine_instance_id=clean_text(routine_instance_id),
        )

    def unregister_routine_instance(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove one eligible Instance from an existing Mock stock container."""

        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        instance_id = self._require_instance(before, routine_instance_id)
        eligibility = mock_routine_instance_unregister_eligibility(before, instance_id)
        if eligibility.get("eligible") is not True:
            return {
                "ok": False,
                "reason": eligibility.get("reason"),
                "stage": "ELIGIBILITY",
            }
        if len(before["instance_execution"]) <= 1:
            return {
                "ok": False,
                "reason": "MOCK_LAST_ROUTINE_REQUIRES_STOCK_UNREGISTER",
                "stage": "BOUNDARY",
            }
        changed_at = self._now()

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            snapshot = document["reference_snapshot"]
            snapshot["routine_instances"] = [
                item
                for item in snapshot["routine_instances"]
                if clean_text(item.get("routine_instance_id")) != instance_id
            ]
            snapshot.pop("snapshot_hash", None)
            snapshot["snapshot_hash"] = payload_hash(snapshot)
            document["session"]["reference_snapshot_hash"] = snapshot["snapshot_hash"]
            for key in (
                "effective_settings_by_instance",
                "initial_buy_adjustments_by_instance",
                "instance_execution",
                "cycle_state_by_instance",
                "progression_by_instance",
            ):
                value = document.get(key)
                if isinstance(value, dict):
                    value.pop(instance_id, None)
            for key in ("orders", "fills", "positions", "pnl"):
                document[key] = [
                    item
                    for item in document.get(key, ())
                    if clean_text(item.get("routine_instance_id")) != instance_id
                ]
            lifecycle = document.get("mock_operation_lifecycle")
            if isinstance(lifecycle, dict):
                operations = lifecycle.get("instance_operations")
                removed_operation_ids: set[str] = set()
                if isinstance(operations, dict):
                    removed = operations.pop(instance_id, None)
                    if isinstance(removed, dict):
                        removed_operation_ids.add(
                            clean_text(removed.get("operation_session_id"))
                        )
                history = lifecycle.get("history")
                if isinstance(history, list):
                    lifecycle["history"] = [
                        item
                        for item in history
                        if clean_text(item.get("routine_instance_id")) != instance_id
                    ]
                commands = lifecycle.get("commands")
                if isinstance(commands, dict) and removed_operation_ids:
                    lifecycle["commands"] = {
                        key: value
                        for key, value in commands.items()
                        if clean_text(value.get("entity_id")) not in removed_operation_ids
                    }
            document["applied_commands"][command] = {
                "operation": "UNREGISTER_ROUTINE_INSTANCE",
                "applied_at": changed_at,
                "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(
            session_id,
            mutation,
            expected_revision=before["revision"],
        )
        return {
            "ok": True,
            "removed": True,
            "routine_instance_id": instance_id,
            **result,
        }

    def end_stock_session(self, session_id: str, *, command_id: str | None = None) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None and before["session"]["state"] == SESSION_ENDED:
            history = self.repository.read_history(session_id)
            return {"ended": False, "duplicate": True, "document": before, "history": history}
        if before["session"]["state"] == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_SESSION_IMMUTABLE")
        ended_at = self._now()

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["session"].update({"state": SESSION_ENDED, "ended_at": ended_at})
            for item in document["instance_execution"].values():
                item.update({"state": SESSION_ENDED, "progression_allowed": False})
            document["applied_commands"][command] = {
                "operation": "END_SESSION", "applied_at": ended_at, "entity_id": session_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        self._event(
            session_id=session_id,
            stock_code=result["document"]["session"]["stock_code"],
            event_type="SESSION_ENDED",
            timestamp=ended_at,
            command_id=command,
        )
        archive = self.repository.archive_session(session_id, archived_at=ended_at)
        return {"ended": True, "duplicate": False, **result, **archive}

    def create_order(
        self,
        session_id: str,
        *,
        routine_instance_id: str,
        side: str,
        order_type: str,
        requested_qty: int,
        requested_price: int | float | None,
        generation: int = 0,
        child_identity: str = "",
        mock_order_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        instance_id = self._require_progression(before, routine_instance_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            entity_id = duplicate.get("entity_id")
            order = next((item for item in before["orders"] if item.get("mock_order_id") == entity_id), None)
            return {"created": False, "duplicate": True, "order": deepcopy(order), "document": before}
        order_id = clean_text(mock_order_id) or new_mock_identity("MO")
        created_at = self._now()
        quantity = int(requested_qty)
        order = {
            "mock_order_id": order_id,
            "validation_session_id": session_id,
            "routine_instance_id": instance_id,
            "stock_code": before["session"]["stock_code"],
            "side": clean_text(side).upper(),
            "order_type": clean_text(order_type).upper(),
            "requested_qty": quantity,
            "requested_price": requested_price,
            "remaining_qty": quantity,
            "filled_qty": 0,
            "state": ORDER_CREATED,
            "created_at": created_at,
            "updated_at": created_at,
            "canceled_at": "",
            "generation": int(generation),
            "child_identity": clean_text(child_identity),
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["orders"].append(order)
            document["applied_commands"][command] = {
                "operation": "CREATE_ORDER", "applied_at": created_at, "entity_id": order_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        return {"created": True, "duplicate": False, "order": deepcopy(order), **result}

    def transition_order(
        self,
        session_id: str,
        mock_order_id: str,
        next_state: str,
        *,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"changed": False, "duplicate": True, "document": before}
        target_index = next((index for index, item in enumerate(before["orders"]) if item.get("mock_order_id") == mock_order_id), None)
        if target_index is None:
            raise MockValidationError("MOCK_ORDER_NOT_FOUND")
        self._require_progression(before, before["orders"][target_index]["routine_instance_id"])
        occurred_at = self._now()

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["orders"][target_index] = transition_mock_order(
                document["orders"][target_index], next_state, occurred_at=occurred_at,
            )
            document["applied_commands"][command] = {
                "operation": "TRANSITION_ORDER", "applied_at": occurred_at, "entity_id": mock_order_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        return {"duplicate": False, **result}

    def append_fill(
        self,
        session_id: str,
        *,
        mock_order_id: str,
        qty: int,
        price: int | float,
        market_snapshot_identity: str,
        mock_fill_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            entity_id = duplicate.get("entity_id")
            fill = next((item for item in before["fills"] if item.get("mock_fill_id") == entity_id), None)
            return {"appended": False, "duplicate": True, "fill": deepcopy(fill), "document": before}
        order_index = next((index for index, item in enumerate(before["orders"]) if item.get("mock_order_id") == mock_order_id), None)
        if order_index is None:
            raise MockValidationError("MOCK_ORDER_NOT_FOUND")
        order = before["orders"][order_index]
        instance_id = self._require_progression(before, order["routine_instance_id"])
        if order.get("state") not in {ORDER_OPEN, ORDER_PARTIAL_FILL, ORDER_CANCEL_PENDING}:
            raise MockValidationError("MOCK_ORDER_NOT_FILLABLE")
        fill_qty = int(qty)
        if fill_qty <= 0 or fill_qty > int(order["remaining_qty"]):
            raise MockValidationError("MOCK_FILL_EXCEEDS_REMAINING")
        filled_at = self._now()
        fill_id = clean_text(mock_fill_id) or new_mock_identity("MF")
        sequence = 1 + sum(1 for item in before["fills"] if item.get("mock_order_id") == mock_order_id)
        fill = {
            "mock_fill_id": fill_id,
            "mock_order_id": mock_order_id,
            "validation_session_id": session_id,
            "routine_instance_id": instance_id,
            "stock_code": before["session"]["stock_code"],
            "side": order["side"],
            "qty": fill_qty,
            "price": price,
            "filled_at": filled_at,
            "market_snapshot_identity": clean_text(market_snapshot_identity),
            "fill_sequence": sequence,
        }

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            current = document["orders"][order_index]
            current["filled_qty"] = int(current["filled_qty"]) + fill_qty
            current["remaining_qty"] = int(current["requested_qty"]) - int(current["filled_qty"])
            current["state"] = ORDER_FILLED if current["remaining_qty"] == 0 else ORDER_PARTIAL_FILL
            current["updated_at"] = filled_at
            document["fills"].append(fill)
            document["applied_commands"][command] = {
                "operation": "APPEND_FILL", "applied_at": filled_at, "entity_id": fill_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        return {"appended": True, "duplicate": False, "fill": deepcopy(fill), **result}

    def set_instance_position(
        self,
        session_id: str,
        routine_instance_id: str,
        *,
        holding_qty: int,
        available_qty: int,
        average_price: int | float,
        realized_cost_basis: int | float,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        return self._set_instance_ledger(
            session_id, routine_instance_id, "positions",
            {
                "holding_qty": holding_qty, "available_qty": available_qty,
                "average_price": average_price, "realized_cost_basis": realized_cost_basis,
            }, command_id=command_id,
        )

    def set_instance_pnl(
        self,
        session_id: str,
        routine_instance_id: str,
        *,
        realized_pnl: int | float,
        unrealized_pnl: int | float,
        gross_pnl: int | float,
        commission: int | float,
        mock_tax: int | float,
        net_pnl: int | float,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        return self._set_instance_ledger(
            session_id, routine_instance_id, "pnl",
            {
                "realized_pnl": realized_pnl, "unrealized_pnl": unrealized_pnl,
                "gross_pnl": gross_pnl, "commission": commission,
                "mock_tax": mock_tax, "net_pnl": net_pnl,
            }, command_id=command_id,
        )

    def _set_instance_ledger(
        self,
        session_id: str,
        routine_instance_id: str,
        ledger: str,
        values: dict[str, Any],
        *,
        command_id: str | None,
    ) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        instance_id = self._require_progression(before, routine_instance_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"changed": False, "duplicate": True, "document": before}
        updated_at = self._now()

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            record = next(item for item in document[ledger] if item["routine_instance_id"] == instance_id)
            record.update(deepcopy(values))
            record["updated_at"] = updated_at
            document["applied_commands"][command] = {
                "operation": f"SET_{ledger.upper()}", "applied_at": updated_at, "entity_id": instance_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        return {"duplicate": False, **result}


__all__ = ["MockValidationSessionService"]
