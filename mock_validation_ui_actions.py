# -*- coding: utf-8 -*-
"""Application actions for Mock Validation, with Production commands injected."""

from __future__ import annotations

from typing import Any, Callable

from mock_validation_contract import MockValidationError, new_mock_identity
from mock_validation_host import MockValidationHost
from mock_validation_operation_lifecycle import mock_validation_end_eligibility


def _success(value: Any) -> bool:
    if value is True:
        return True
    if not isinstance(value, dict):
        return bool(
            getattr(value, "ok", False)
            or getattr(value, "success", False)
            or getattr(value, "allowed", False)
        )
    if value.get("ok") is True or value.get("success") is True or value.get("allowed") is True:
        return True
    status = str(value.get("status") or "").strip().upper()
    return status in {"OK", "SUCCESS", "COMPLETED", "UNCHANGED", "NOOP"}


class MockValidationUIActions:
    def __init__(self, host: MockValidationHost) -> None:
        self.host = host
        self.repository = host.repository
        self.sessions = host.session_service

    def create_waiting_session(self, reference_snapshot: dict[str, Any]) -> dict[str, Any]:
        result = self.sessions.create_stock_session(reference_snapshot=reference_snapshot)
        self.host.sync_registration()
        self.host._publish_projection_if_changed()
        return result

    def start(self, stock_code: str) -> dict[str, Any]:
        return self.host.start_stock_operation(stock_code)

    def early_close(self, stock_code: str) -> dict[str, Any]:
        return self.host.request_early_close(stock_code)

    def immediate_liquidation(self, stock_code: str) -> dict[str, Any]:
        return self.host.request_immediate_liquidation(stock_code)

    def set_tax(self, stock_code: str, enabled: bool) -> dict[str, Any]:
        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        result = self.sessions.set_mock_tax_enabled(
            document["session"]["validation_session_id"],
            enabled=bool(enabled),
            command_id=new_mock_identity("MC"),
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

    def return_to_production(
        self,
        stock_code: str,
        *,
        destination: str,
        preflight: Callable[[], Any],
        execute: Callable[[], Any],
    ) -> dict[str, Any]:
        """Execute the safe saga without importing a Production mutation module."""

        document = self.host.current_session(stock_code)
        if document is None:
            raise MockValidationError("MOCK_CURRENT_SESSION_NOT_FOUND")
        eligibility = mock_validation_end_eligibility(document)
        if eligibility.get("eligible") is not True:
            return {"ok": False, "reason": eligibility.get("reason"), "stage": "ELIGIBILITY"}
        preflight_result = preflight()
        if not _success(preflight_result):
            return {"ok": False, "reason": "PRODUCTION_RETURN_PREFLIGHT_FAILED", "stage": "PREFLIGHT", "result": preflight_result}
        attempt = new_mock_identity("MC")
        session_id = document["session"]["validation_session_id"]
        self.sessions.record_return_event(
            session_id,
            event_type="RETURN_REQUESTED",
            destination=destination,
            command_id=attempt,
        )
        try:
            production_result = execute()
        except Exception as exc:
            self.sessions.record_return_event(
                session_id,
                event_type="RETURN_FAILED",
                destination=destination,
                command_id=attempt,
                reason_code="PRODUCTION_RETURN_COMMAND_FAILED",
                payload={"error": str(exc)},
            )
            self.host._publish_projection_if_changed()
            return {"ok": False, "reason": "PRODUCTION_RETURN_COMMAND_FAILED", "stage": "COMMAND", "error": str(exc)}
        if not _success(production_result):
            self.sessions.record_return_event(
                session_id,
                event_type="RETURN_FAILED",
                destination=destination,
                command_id=attempt,
                reason_code="PRODUCTION_RETURN_COMMAND_REJECTED",
                payload={"result": str(production_result)},
            )
            self.host._publish_projection_if_changed()
            return {"ok": False, "reason": "PRODUCTION_RETURN_COMMAND_REJECTED", "stage": "COMMAND", "result": production_result}
        ended = self.sessions.end_stock_session(session_id, command_id=f"{attempt}:END")
        self.sessions.record_return_event(
            session_id,
            event_type="RETURN_COMPLETED",
            destination=destination,
            command_id=attempt,
        )
        self.host.sync_registration()
        self.host._publish_projection_if_changed()
        return {"ok": True, "stage": "COMPLETED", "production_result": production_result, "ended": ended}


__all__ = ["MockValidationUIActions"]
