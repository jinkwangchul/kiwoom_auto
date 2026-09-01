# -*- coding: utf-8 -*-
"""Canonical startup and execution Runtime file schemas.

This module defines in-memory default structures for Runtime evidence files. It
does not create directories, write files, or connect to execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ORDER_QUEUE_SCHEMA: dict[str, Any] = {
    "version": 1,
    "revision": 0,
    "updated_at": None,
    "orders": [],
}

FILLS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "fills": [],
}

POSITIONS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "positions": [],
}

BROKER_HOLDINGS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "holdings": [],
}

ORDER_EXECUTIONS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "processes": [],
    "executions": [],
}

ORDER_LOCKS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "locks": [],
}

ROUTINE_SIGNALS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": "",
    "signals": [],
}


def default_order_queue_data() -> dict[str, Any]:
    """Return a fresh default order queue Runtime structure."""
    return deepcopy(ORDER_QUEUE_SCHEMA)


def default_fills_data() -> dict[str, Any]:
    """Return a fresh default Fill Runtime structure."""
    return deepcopy(FILLS_SCHEMA)


def default_positions_data() -> dict[str, Any]:
    """Return a fresh default Position Runtime structure."""
    return deepcopy(POSITIONS_SCHEMA)


def default_broker_holdings_data() -> dict[str, Any]:
    """Return a fresh default Broker Holdings Runtime structure."""
    return deepcopy(BROKER_HOLDINGS_SCHEMA)


def default_order_executions_data() -> dict[str, Any]:
    """Return a fresh default order_executions runtime structure."""
    return deepcopy(ORDER_EXECUTIONS_SCHEMA)


def default_order_locks_data() -> dict[str, Any]:
    """Return a fresh default order_locks runtime structure."""
    return deepcopy(ORDER_LOCKS_SCHEMA)


def default_routine_signals_data() -> dict[str, Any]:
    """Return a fresh default routine signal queue Runtime structure."""
    return deepcopy(ROUTINE_SIGNALS_SCHEMA)
