from __future__ import annotations

from stock_code_contract import normalize_stock_code


class CanonicalParticipantOwnerStub:
    def __init__(self, stock_codes=()) -> None:
        self._participants = {
            normalize_stock_code(code)
            for code in stock_codes
            if normalize_stock_code(code)
        }

    def current_session_operation_participant_stock_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._participants))

    def is_current_session_operation_participant(self, stock_code: object) -> bool:
        code = normalize_stock_code(stock_code)
        return bool(code and code in self._participants)

    def register_current_session_operation_participants(
        self,
        stock_codes,
    ) -> tuple[str, ...]:
        registered = {
            normalize_stock_code(code)
            for code in stock_codes
            if normalize_stock_code(code)
        }
        self._participants.update(registered)
        return tuple(sorted(registered))

    def retire_current_session_operation_participants(
        self,
        stock_codes,
    ) -> dict[str, tuple[str, ...]]:
        requested = tuple(
            sorted(
                {
                    normalize_stock_code(code)
                    for code in stock_codes
                    if normalize_stock_code(code)
                }
            )
        )
        before = self.current_session_operation_participant_stock_codes()
        requested_set = set(requested)
        removed = tuple(code for code in before if code in requested_set)
        self._participants.difference_update(removed)
        return {
            "before": before,
            "requested": requested,
            "removed": removed,
            "remaining": self.current_session_operation_participant_stock_codes(),
        }


def participant_owner(stock_codes=()) -> CanonicalParticipantOwnerStub:
    return CanonicalParticipantOwnerStub(stock_codes)


def attach_participant_owner(context, stock_codes=()) -> CanonicalParticipantOwnerStub:
    owner = participant_owner(stock_codes)
    context._main_monitoring_auto_trade_operation_host = owner
    return owner


def participant_codes(context) -> tuple[str, ...]:
    owner = getattr(context, "_main_monitoring_auto_trade_operation_host", context)
    return tuple(owner.current_session_operation_participant_stock_codes())
