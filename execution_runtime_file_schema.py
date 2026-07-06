# -*- coding: utf-8 -*-
"""Execution runtime file schemas.

This module defines in-memory default structures for future runtime files. It
does not create directories, write files, or connect to execution components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ORDER_EXECUTIONS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "executions": [],
}

ORDER_LOCKS_SCHEMA: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "locks": [],
}


def default_order_executions_data() -> dict[str, Any]:
    """Return a fresh default order_executions runtime structure."""
    return deepcopy(ORDER_EXECUTIONS_SCHEMA)


def default_order_locks_data() -> dict[str, Any]:
    """Return a fresh default order_locks runtime structure."""
    return deepcopy(ORDER_LOCKS_SCHEMA)
