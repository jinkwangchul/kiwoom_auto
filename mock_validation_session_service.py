# -*- coding: utf-8 -*-
"""Stock-scoped lifecycle and isolated ledgers for Mock Validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from mock_validation_contract import (
    ORDER_CANCEL_PENDING,
    ORDER_CANCELED,
    ORDER_CREATED,
    ORDER_FILLED,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    SESSION_ENDED,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    clean_text,
    deterministic_mock_identity,
    initial_session_document,
    new_mock_identity,
    now_text,
    transition_mock_order,
)
from mock_validation_repository import MockValidationRepository


class MockValidationSessionService:
    """Public Mock operation API; lifecycle scope is always one stock Session."""

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

    @staticmethod
    def _require_progression(document: dict[str, Any], instance_id: str) -> str:
        clean = MockValidationSessionService._require_instance(document, instance_id)
        if document["session"]["state"] != SESSION_RUNNING:
            raise MockValidationError("MOCK_SESSION_NOT_RUNNING")
        if document["review"].get("review_required") is True:
            raise MockValidationError("MOCK_SESSION_REVIEW_STOPPED")
        if document["instance_execution"][clean].get("progression_allowed") is not True:
            raise MockValidationError("MOCK_INSTANCE_PROGRESSION_BLOCKED")
        return clean

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
        validation_session_id: str | None = None,
        command_id: str | None = None,
        mock_tax_enabled: bool = True,
        mock_tax_rate: float = 0.002,
    ) -> dict[str, Any]:
        session_id = clean_text(validation_session_id) or new_mock_identity("MV")
        command = clean_text(command_id) or deterministic_mock_identity("MC", session_id, "CREATE")
        created_at = self._now()
        document = initial_session_document(
            validation_session_id=session_id,
            reference_snapshot=reference_snapshot,
            created_at=created_at,
            mock_tax_enabled=mock_tax_enabled,
            mock_tax_rate=mock_tax_rate,
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

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["session"]["state"] = SESSION_REVIEW_STOPPED
            for item in document["instance_execution"].values():
                item["state"] = SESSION_REVIEW_STOPPED
                item["progression_allowed"] = False
            document["review"] = {
                "review_required": True,
                "review_reason": clean_text(reason),
                "source_routine_instance_id": source_id,
                "occurred_at": occurred_at,
                "resolved_at": "",
                "resolution": "",
            }
            document["applied_commands"][command] = {
                "operation": "STOP_FOR_INSTANCE_ERROR", "applied_at": occurred_at, "entity_id": source_id,
            }
            return document

        result = self.repository.mutate_session(session_id, mutation, expected_revision=before["revision"])
        session = result["document"]["session"]
        self._event(
            session_id=session_id,
            stock_code=session["stock_code"],
            event_type="INSTANCE_ERROR",
            timestamp=occurred_at,
            command_id=command,
            routine_instance_id=source_id,
            reason_code=reason_code,
            payload={"reason": clean_text(reason)},
        )
        self._event(
            session_id=session_id,
            stock_code=session["stock_code"],
            event_type="SESSION_REVIEW_STOPPED",
            timestamp=occurred_at,
            command_id=command,
            routine_instance_id=source_id,
            reason_code=reason_code,
            payload={"source_routine_instance_id": source_id},
        )
        return {"stopped": True, "duplicate": False, **result}

    def reset_stock_session(self, session_id: str, *, command_id: str | None = None) -> dict[str, Any]:
        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"reset": False, "duplicate": True, "document": before, "command": duplicate}
        if before["session"]["state"] == SESSION_ENDED:
            raise MockValidationError("MOCK_ENDED_SESSION_IMMUTABLE")
        reset_at = self._now()
        operation_root = before.get("mock_operation_lifecycle")
        had_operation = (
            isinstance(operation_root, dict)
            and isinstance(operation_root.get("current"), dict)
        )

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["session"].update(
                {
                    "state": SESSION_WAITING,
                    "session_generation": int(document["session"]["session_generation"]) + 1,
                    "started_at": "",
                    "ended_at": "",
                    "start_identity": "",
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

    def set_mock_tax_enabled(
        self,
        session_id: str,
        *,
        enabled: bool,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist the Mock-only tax preference while the stock is waiting."""

        command = clean_text(command_id) or new_mock_identity("MC")
        before = self.repository.read_session(session_id)
        duplicate = self._command(before, command)
        if duplicate is not None:
            return {"changed": False, "duplicate": True, "document": before}
        if before["session"]["state"] != SESSION_WAITING:
            raise MockValidationError("MOCK_TAX_CHANGE_REQUIRES_WAITING")
        changed_at = self._now()

        def mutation(document: dict[str, Any]) -> dict[str, Any]:
            document["session"]["mock_tax_enabled"] = bool(enabled)
            document["applied_commands"][command] = {
                "operation": "SET_MOCK_TAX",
                "applied_at": changed_at,
                "entity_id": "ENABLED" if enabled else "DISABLED",
            }
            return document

        result = self.repository.mutate_session(
            session_id, mutation, expected_revision=before["revision"]
        )
        self._event(
            session_id=session_id,
            stock_code=result["document"]["session"]["stock_code"],
            event_type="MOCK_TAX_UPDATED",
            timestamp=changed_at,
            command_id=command,
            payload={
                "mock_tax_enabled": bool(enabled),
                "mock_tax_rate": result["document"]["session"]["mock_tax_rate"],
            },
        )
        return {"changed": bool(result.get("changed")), "duplicate": False, **result}

    def record_return_event(
        self,
        session_id: str,
        *,
        event_type: str,
        destination: str,
        command_id: str,
        reason_code: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append idempotent Mock-owned evidence for the explicit return saga."""

        if event_type not in {"RETURN_REQUESTED", "RETURN_FAILED", "RETURN_COMPLETED"}:
            raise MockValidationError("MOCK_RETURN_EVENT_TYPE_INVALID")
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
        )

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
