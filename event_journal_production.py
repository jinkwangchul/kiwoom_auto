# -*- coding: utf-8 -*-
"""Fail-open Production adapter for the existing Event Journal writer."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
import logging
import sys
import threading
from typing import Any

from event_journal_contract import make_stack_fingerprint, new_app_session_id
from event_journal_writer import EventJournalWriter, expected_category


LOGGER = logging.getLogger(__name__)

_APP_SESSION_ID = new_app_session_id()
_WRITER = EventJournalWriter()
_GLOBAL_EXCEPTION_HOOKS_INSTALLED = False
_PREVIOUS_SYS_EXCEPTHOOK = None
_PREVIOUS_THREADING_EXCEPTHOOK = None
_MAX_PARENT_CORRELATIONS = 4096
_LATEST_EVENT_BY_CORRELATION: OrderedDict[str, str] = OrderedDict()
_PARENT_CACHE_LOCK = threading.RLock()


def app_session_id() -> str:
    """Return the process-lifetime journal correlation id."""

    return _APP_SESSION_ID


def append_production_event(
    event_type: str,
    *,
    severity: str = "INFO",
    result: str | None = None,
    source: str,
    template_args: dict[str, Any] | None = None,
    occurred_at: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append after a Production mutation without affecting that mutation."""

    try:
        correlation_id = str(fields.get("correlation_id") or "").strip()
        if correlation_id and not str(fields.get("parent_event_id") or "").strip():
            with _PARENT_CACHE_LOCK:
                parent_event_id = _LATEST_EVENT_BY_CORRELATION.get(correlation_id, "")
            if parent_event_id:
                fields["parent_event_id"] = parent_event_id

        result = _WRITER.append_event(
            event_type=event_type,
            occurred_at=occurred_at or datetime.now().astimezone().isoformat(timespec="microseconds"),
            category=expected_category(event_type),
            severity=severity,
            template_args=template_args or {},
            app_session_id=_APP_SESSION_ID,
            result=result,
            source=source,
            **fields,
        )
        if result.get("appended") is True and correlation_id:
            event = result.get("event")
            event_id = str(event.get("event_id") or "").strip() if isinstance(event, dict) else ""
            if event_id:
                with _PARENT_CACHE_LOCK:
                    _LATEST_EVENT_BY_CORRELATION[correlation_id] = event_id
                    _LATEST_EVENT_BY_CORRELATION.move_to_end(correlation_id)
                    while len(_LATEST_EVENT_BY_CORRELATION) > _MAX_PARENT_CORRELATIONS:
                        _LATEST_EVENT_BY_CORRELATION.popitem(last=False)
        return result
    except Exception as exc:
        LOGGER.warning("Event Journal append failed for %s: %s", event_type, exc)
        return {
            "appended": False,
            "write_failed": True,
            "error": str(exc),
        }


def append_owner_event_once(
    owner: object,
    token: str,
    event_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Suppress duplicate lifecycle outcomes in process memory only."""

    marker_name = "_event_journal_emitted_tokens"
    tokens = getattr(owner, marker_name, None)
    if not isinstance(tokens, set):
        tokens = set()
        setattr(owner, marker_name, tokens)
    clean_token = str(token or "").strip()
    if clean_token in tokens:
        return {"appended": False, "duplicate": True, "event_type": event_type}
    tokens.add(clean_token)
    return append_production_event(event_type, **kwargs)


def observe_owner_failure_transition(
    owner: object,
    scope: str,
    *,
    active: bool,
    signature: str = "",
    event_type: str = "RUNTIME_WARNING",
    **kwargs: Any,
) -> dict[str, Any]:
    """Append once when one operational scope enters a confirmed failure state.

    The state is process-local projection state only.  A successful observation
    clears the scope so a later, genuinely new failure transition can be
    recorded without turning polling refreshes into journal noise.
    """

    marker_name = "_event_journal_failure_transitions"
    states = getattr(owner, marker_name, None)
    if not isinstance(states, dict):
        states = {}
        setattr(owner, marker_name, states)
    clean_scope = str(scope or "").strip()
    if not clean_scope:
        return {"appended": False, "error": "failure transition scope is required"}
    if not active:
        states.pop(clean_scope, None)
        return {"appended": False, "cleared": True, "scope": clean_scope}

    state_signature = (
        str(event_type or "").strip(),
        str(signature or "").strip(),
    )
    if states.get(clean_scope) == state_signature:
        return {
            "appended": False,
            "duplicate": True,
            "scope": clean_scope,
            "event_type": event_type,
        }
    states[clean_scope] = state_signature
    return append_production_event(event_type, **kwargs)


def observe_production_exception(
    exc_type: Any,
    exc_value: Any,
    exc_traceback: Any,
    *,
    component: str,
    operation: str,
    source: str,
    target_type: str = "COMPONENT",
    target_id: str = "",
    target_name: str = "",
    reason_code: str = "UNHANDLED_EXCEPTION",
    owner: object | None = None,
    failure_scope: str = "",
    details: dict[str, Any] | None = None,
    build_version: str = "",
    signal_id: str = "",
    order_id: str = "",
    execution_id: str = "",
    command_id: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Project one exception into a sanitized, fail-open diagnostic event."""

    try:
        exception_name = str(getattr(exc_type, "__name__", "") or exc_type or "Exception")
        exception_message = str(exc_value or exception_name)
        module_name = ""
        function_name = ""
        line_number: int | str = ""
        traceback_cursor = exc_traceback
        while traceback_cursor is not None:
            frame = getattr(traceback_cursor, "tb_frame", None)
            if frame is not None:
                module_name = str(frame.f_globals.get("__name__", "") or "")
                function_name = str(getattr(frame.f_code, "co_name", "") or "")
                line_number = int(getattr(traceback_cursor, "tb_lineno", 0) or 0)
            traceback_cursor = getattr(traceback_cursor, "tb_next", None)

        fingerprint = make_stack_fingerprint(
            exception_type=exception_name,
            module=module_name,
            function=function_name,
            line=line_number,
        )
        clean_target_name = str(target_name or target_id or component or "Production")
        event_fields: dict[str, Any] = {
            "severity": "ERROR",
            "result": "FAILED",
            "source": source,
            "template_args": {"target": clean_target_name},
            "target_type": target_type,
            "target_id": target_id or component,
            "target_name": clean_target_name,
            "reason_code": reason_code,
            "component": component,
            "operation": operation,
            "exception_type": exception_name,
            "exception_message": exception_message,
            "stack_fingerprint": fingerprint,
            "details": {
                "module": module_name,
                "function": function_name,
                "line": line_number,
                **(details if isinstance(details, dict) else {}),
            },
        }
        if str(build_version or "").strip():
            event_fields["build_version"] = str(build_version).strip()
        domain_identities = {
            "signal_id": str(signal_id or "").strip(),
            "order_id": str(order_id or "").strip(),
            "execution_id": str(execution_id or "").strip(),
            "command_id": str(command_id or "").strip(),
        }
        event_fields.update({key: value for key, value in domain_identities.items() if value})
        clean_correlation_id = str(correlation_id or "").strip()
        if clean_correlation_id:
            event_fields["correlation_id"] = clean_correlation_id
        if owner is not None and str(failure_scope or "").strip():
            return observe_owner_failure_transition(
                owner,
                failure_scope,
                active=True,
                signature=f"{reason_code}:{fingerprint}",
                event_type="PROCESSING_ERROR",
                **event_fields,
            )
        return append_production_event("PROCESSING_ERROR", **event_fields)
    except Exception as observer_exc:
        LOGGER.warning("Production exception observer failed: %s", observer_exc)
        return {
            "appended": False,
            "observer_failed": True,
            "error": str(observer_exc),
        }


def reset_event_parent_cache_for_tests() -> None:
    """Clear only the process-local best-effort parent projection."""

    with _PARENT_CACHE_LOCK:
        _LATEST_EVENT_BY_CORRELATION.clear()


def install_global_exception_observers() -> dict[str, Any]:
    """Install one fail-open wrapper around the existing process/thread hooks."""

    global _GLOBAL_EXCEPTION_HOOKS_INSTALLED
    global _PREVIOUS_SYS_EXCEPTHOOK
    global _PREVIOUS_THREADING_EXCEPTHOOK

    if _GLOBAL_EXCEPTION_HOOKS_INSTALLED:
        return {"installed": False, "duplicate": True}

    previous_sys_hook = sys.excepthook
    previous_thread_hook = getattr(threading, "excepthook", None)

    def observed_sys_excepthook(exc_type, exc_value, exc_traceback) -> None:
        try:
            observe_production_exception(
                exc_type,
                exc_value,
                exc_traceback,
                component="python_process",
                operation="sys.excepthook",
                source="event_journal_production.install_global_exception_observers",
                target_id="main_process",
                target_name="메인 프로세스",
                reason_code="UNHANDLED_PROCESS_EXCEPTION",
            )
        except Exception as observer_exc:
            LOGGER.warning("sys.excepthook observer failed: %s", observer_exc)
        finally:
            previous_sys_hook(exc_type, exc_value, exc_traceback)

    def observed_threading_excepthook(args) -> None:
        try:
            thread = getattr(args, "thread", None)
            observe_production_exception(
                getattr(args, "exc_type", Exception),
                getattr(args, "exc_value", None),
                getattr(args, "exc_traceback", None),
                component="python_thread",
                operation="threading.excepthook",
                source="event_journal_production.install_global_exception_observers",
                target_id="worker_thread",
                target_name="작업 Thread",
                reason_code="UNHANDLED_THREAD_EXCEPTION",
                details={"thread_name": str(getattr(thread, "name", "") or "")},
            )
        except Exception as observer_exc:
            LOGGER.warning("threading.excepthook observer failed: %s", observer_exc)
        finally:
            if callable(previous_thread_hook):
                previous_thread_hook(args)

    _PREVIOUS_SYS_EXCEPTHOOK = previous_sys_hook
    _PREVIOUS_THREADING_EXCEPTHOOK = previous_thread_hook
    sys.excepthook = observed_sys_excepthook
    if callable(previous_thread_hook):
        threading.excepthook = observed_threading_excepthook
    _GLOBAL_EXCEPTION_HOOKS_INSTALLED = True
    return {
        "installed": True,
        "sys_hook_wrapped": True,
        "threading_hook_wrapped": callable(previous_thread_hook),
    }
