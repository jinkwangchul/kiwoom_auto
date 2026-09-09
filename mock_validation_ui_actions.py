# -*- coding: utf-8 -*-
"""Application actions that mutate only the isolated Mock Validation domain."""

from __future__ import annotations

from typing import Any

from mock_validation_contract import MockValidationError, new_mock_identity
from mock_validation_host import MockValidationHost
from mock_validation_operation_lifecycle import mock_validation_end_eligibility


class MockValidationUIActions:
    def __init__(self, host: MockValidationHost) -> None:
        self.host = host
        self.repository = host.repository
        self.sessions = host.session_service

    def create_waiting_session(
        self,
        reference_snapshot: dict[str, Any],
        *,
        effective_settings_by_instance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.sessions.create_stock_session(
            reference_snapshot=reference_snapshot,
            effective_settings_by_instance=effective_settings_by_instance,
        )
        self.host.sync_registration()
        self.host._publish_projection_if_changed()
        return result

    def start(self, stock_code: str) -> dict[str, Any]:
        return self.host.start_stock_operation(stock_code)

    def start_instance(
        self, stock_code: str, routine_instance_id: str
    ) -> dict[str, Any]:
        return self.host.start_instance_operation(stock_code, routine_instance_id)

    def early_close(self, stock_code: str, *, method: str) -> dict[str, Any]:
        return self.host.request_early_close(stock_code, method=method)

    def early_close_instance(
        self,
        stock_code: str,
        routine_instance_id: str,
        *,
        method: str,
        profit_percent: Any = None,
        loss_percent: Any = None,
    ) -> dict[str, Any]:
        return self.host.request_instance_early_close(
            stock_code,
            routine_instance_id,
            method=method,
            profit_percent=profit_percent,
            loss_percent=loss_percent,
        )

    def cancel_early_close_instance(
        self, stock_code: str, routine_instance_id: str
    ) -> dict[str, Any]:
        return self.host.cancel_instance_early_close(
            stock_code, routine_instance_id
        )

    def immediate_liquidation(
        self,
        stock_code: str,
        *,
        method: str,
    ) -> dict[str, Any]:
        return self.host.request_immediate_liquidation(stock_code, method=method)

    def immediate_liquidation_instance(
        self, stock_code: str, routine_instance_id: str, *, method: str
    ) -> dict[str, Any]:
        return self.host.request_instance_immediate_liquidation(
            stock_code, routine_instance_id, method=method
        )

    def individual_liquidation_instance(
        self,
        stock_code: str,
        routine_instance_id: str,
        *,
        method: str,
        minutes_before_regular_close: Any = "5",
    ) -> dict[str, Any]:
        return self.host.request_instance_individual_liquidation(
            stock_code,
            routine_instance_id,
            method=method,
            minutes_before_regular_close=minutes_before_regular_close,
        )

    def validation_stop_instance(
        self, stock_code: str, routine_instance_id: str
    ) -> dict[str, Any]:
        return self.host.stop_instance_validation(
            stock_code,
            routine_instance_id,
            source="OPERATOR_UI",
        )

    def instance_context_state(
        self, stock_code: str, routine_instance_id: str
    ) -> dict[str, Any]:
        return self.host.instance_context_state(stock_code, routine_instance_id)

    def set_instance_effective_settings(
        self,
        stock_code: str,
        routine_instance_id: str,
        **changes: Any,
    ) -> dict[str, Any]:
        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        result = self.sessions.set_instance_effective_settings(
            document["session"]["validation_session_id"],
            routine_instance_id=routine_instance_id,
            command_id=new_mock_identity("MC"),
            **changes,
        )
        self.host._publish_projection_if_changed()
        return result

    def set_instance_initial_buy(
        self,
        stock_code: str,
        routine_instance_id: str,
        *,
        mode: str,
        value: int,
        apply_policy: str,
        expected_validation_session_id: str,
        expected_revision: int,
        expected_operation_session_id: str = "",
        requested_at: str = "",
    ) -> dict[str, Any]:
        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        session_id = document["session"]["validation_session_id"]
        if session_id != str(expected_validation_session_id or "").strip():
            raise MockValidationError("MOCK_VALIDATION_SESSION_IDENTITY_MISMATCH")
        result = self.sessions.set_instance_initial_buy(
            session_id,
            routine_instance_id=routine_instance_id,
            mode=mode,
            value=value,
            apply_policy=apply_policy,
            expected_revision=expected_revision,
            expected_operation_session_id=expected_operation_session_id,
            requested_at=requested_at,
            command_id=new_mock_identity("MC"),
        )
        self.host._publish_projection_if_changed()
        return result

    def common_tax_settings(self) -> dict[str, Any]:
        return self.repository.read_settings()

    def set_common_tax_settings(
        self,
        *,
        enabled: bool,
        rate: int | float,
    ) -> dict[str, Any]:
        result = self.sessions.set_common_tax_settings(
            enabled=bool(enabled),
            rate=rate,
        )
        self.host._publish_projection_if_changed()
        return result

    def reset(self, stock_code: str) -> dict[str, Any]:
        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        if document["session"]["state"] != "REVIEW_STOPPED":
            raise MockValidationError("MOCK_RESET_REQUIRES_REVIEW_STOPPED")
        result = self.sessions.reset_stock_session(
            document["session"]["validation_session_id"],
            command_id=new_mock_identity("MC"),
        )
        self.host._publish_projection_if_changed()
        return result

    def reset_instance(
        self,
        stock_code: str,
        routine_instance_id: str,
    ) -> dict[str, Any]:
        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        result = self.sessions.reset_routine_instance(
            document["session"]["validation_session_id"],
            routine_instance_id=routine_instance_id,
            command_id=new_mock_identity("MC"),
        )
        self.host._publish_projection_if_changed()
        return result

    def unregister(
        self,
        stock_code: str,
        *,
        expected_validation_session_id: str = "",
    ) -> dict[str, Any]:
        """End only the Mock registration; Production state is never consulted."""
        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        expected_session_id = str(expected_validation_session_id or "").strip()
        if (
            expected_session_id
            and document["session"]["validation_session_id"] != expected_session_id
        ):
            return {
                "ok": False,
                "reason": "MOCK_CONTEXT_TARGET_STALE",
                "stage": "IDENTITY",
            }
        eligibility = mock_validation_end_eligibility(document)
        if eligibility.get("eligible") is not True:
            return {"ok": False, "reason": eligibility.get("reason"), "stage": "ELIGIBILITY"}
        attempt = new_mock_identity("MC")
        session_id = document["session"]["validation_session_id"]
        self.sessions.record_unregister_event(
            session_id,
            event_type="RETURN_REQUESTED",
            destination="MOCK_ONLY",
            command_id=attempt,
        )
        ended = self.sessions.end_stock_session(session_id, command_id=f"{attempt}:END")
        self.sessions.record_unregister_event(
            session_id,
            event_type="RETURN_COMPLETED",
            destination="MOCK_ONLY",
            command_id=attempt,
        )
        purge = self.repository.purge_ended_session_lifecycle(
            session_id,
            stock_code=document["session"]["stock_code"],
        )
        self.host.sync_registration()
        self.host._publish_projection_if_changed()
        return {
            "ok": True,
            "stage": "COMPLETED",
            "ended": ended,
            "purge": purge,
        }


__all__ = ["MockValidationUIActions"]
