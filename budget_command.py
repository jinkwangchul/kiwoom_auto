# -*- coding: utf-8 -*-
"""Application commands for starting-budget UI actions.

The command layer owns the distinction between changing the budget mode and
changing the budget value.  Persistence remains injectable so the existing
canonical stock-config writer and its conflict contract stay in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gui_auto_trade_policy import auto_trade_start_budget_current_running
from gui_common_utils import safe_int_value
from gui_main_table_loader import (
    main_stock_fresh_market_information_state,
    main_stock_resolved_starting_budget,
)
from gui_operation_environment import starting_budget_defaults
from gui_review_utils import safe_float_value
from gui_window_policy import persistent_feature_owner
from runtime_io import read_json_dict
from stock_repository import STOCK_CONFIG_EXPECTED_MISSING


BUDGET_MODE_QUANTITY = "QUANTITY"
BUDGET_MODE_AMOUNT = "AMOUNT"
BUDGET_MODE_CHANGE = "BUDGET_MODE_CHANGE"
BUDGET_VALUE_CHANGE = "BUDGET_VALUE_CHANGE"
CURRENT_PRICE_UNAVAILABLE = "CURRENT_PRICE_UNAVAILABLE"
START_BUDGET_MUTATION_BLOCKED = "START_BUDGET_MUTATION_BLOCKED"


@dataclass(frozen=True)
class BudgetModeChangeRequest:
    """Canonical input for a ``주수``/``금액`` mode transition."""

    config_path: Path
    target_mode: str


@dataclass(frozen=True)
class BudgetValueChangeRequest:
    """Canonical input for the shared starting-budget dialog entry."""

    config_path: Path


def normalize_budget_mode(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return (
        BUDGET_MODE_AMOUNT
        if normalized == BUDGET_MODE_AMOUNT
        else BUDGET_MODE_QUANTITY
    )


def stock_projection_for_config_path(config_path: Path) -> dict[str, object]:
    target = Path(config_path)
    return {
        "stock_path": str(target.parent),
        "code": target.parent.name.partition("_")[0].strip(),
        "name": target.parent.name.partition("_")[2].strip(),
    }


def _canonical_budget_host(window):
    if callable(
        getattr(window, "main_monitoring_auto_trade_operation_host", None)
    ):
        return window
    owner = persistent_feature_owner(window)
    return owner if owner is not None else window


def fresh_budget_current_price(window, config_path: Path) -> float | None:
    """Read the same fresh-session market source used by Main projection."""

    candidate = _canonical_budget_host(window)
    fresh_state = main_stock_fresh_market_information_state(
        candidate,
        stock_projection_for_config_path(Path(config_path)),
    )
    current_price = safe_float_value(
        getattr(fresh_state, "last_price", None)
        if fresh_state is not None
        else None,
        0.0,
    )
    return current_price if current_price > 0 else None


def inspect_budget_value_entry(
    window,
    request: BudgetValueChangeRequest | Path,
) -> dict[str, object]:
    """Return read-only availability for the shared value-edit entry."""

    config_path = (
        request.config_path
        if isinstance(request, BudgetValueChangeRequest)
        else Path(request)
    )
    current_price = fresh_budget_current_price(window, Path(config_path))
    if current_price is None:
        return {
            "allowed": False,
            "reason": CURRENT_PRICE_UNAVAILABLE,
            "current_price": None,
        }
    return {
        "allowed": True,
        "reason": "",
        "current_price": current_price,
    }


def _default_mode_value(
    window,
    config_path: Path,
    target_mode: str,
) -> int:
    defaults = starting_budget_defaults()
    if target_mode == BUDGET_MODE_QUANTITY:
        return max(1, safe_int_value(defaults.get("quantity"), 1))

    config = read_json_dict(Path(config_path))
    if not isinstance(config, dict):
        config = {}
    amount_config = {
        **config,
        "trade_amount_type": BUDGET_MODE_AMOUNT,
        "buy_amount": 0,
    }
    amount = main_stock_resolved_starting_budget(
        _canonical_budget_host(window),
        stock_projection_for_config_path(Path(config_path)),
        amount_config,
        policy={"starting_budget_defaults": defaults},
    )
    return max(0, safe_int_value(amount, 0))


def execute_budget_mode_change(
    window,
    request: BudgetModeChangeRequest,
    *,
    writer: Callable[..., dict[str, object]],
    fresh_price_reader: Callable[[object, Path], object] | None = None,
    current_running_reader: Callable[[object, str, dict[str, object], dict[str, object]], object]
    | None = None,
    default_value_reader: Callable[[object, Path, str], object] | None = None,
) -> dict[str, object]:
    """Execute one mode transition through the existing canonical writer.

    The optional readers are dependency seams for the two UI hosts and tests;
    the default readers remain canonical and read-only.
    """

    config_path = Path(request.config_path)
    target_mode = normalize_budget_mode(request.target_mode)
    config = read_json_dict(config_path)
    if not isinstance(config, dict):
        config = {}
    current_mode = normalize_budget_mode(config.get("trade_amount_type"))
    base_result = {
        "command": BUDGET_MODE_CHANGE,
        "allowed": True,
        "changed": False,
        "current_mode": current_mode,
        "target_mode": target_mode,
        "current_running": False,
    }
    if current_mode == target_mode:
        return {
            **base_result,
            "reason": "BUDGET_MODE_UNCHANGED",
            "reason_code": "BUDGET_MODE_UNCHANGED",
        }

    price_reader = fresh_price_reader or (
        lambda _window, path: fresh_budget_current_price(_window, path)
    )
    current_price = safe_float_value(price_reader(window, config_path), 0.0)
    if current_price <= 0:
        return {
            **base_result,
            "allowed": False,
            "reason": CURRENT_PRICE_UNAVAILABLE,
            "reason_code": CURRENT_PRICE_UNAVAILABLE,
        }

    stock_code = config_path.parent.name.partition("_")[0].strip()
    state = read_json_dict(config_path.parent / "state.json")
    if not isinstance(state, dict):
        state = {}
    running_reader = current_running_reader or (
        lambda host, code, current_config, current_state: (
            auto_trade_start_budget_current_running(
                host,
                code,
                current_config,
                current_state,
            )
        )
    )
    current_running = bool(running_reader(window, stock_code, config, state))
    if current_running:
        return {
            **base_result,
            "allowed": False,
            "current_running": True,
            "reason": START_BUDGET_MUTATION_BLOCKED,
            "reason_code": START_BUDGET_MUTATION_BLOCKED,
            "current_price": current_price,
        }

    value_reader = default_value_reader or (
        lambda host, path, mode: _default_mode_value(host, path, mode)
    )
    next_value = max(
        0,
        safe_int_value(value_reader(window, config_path, target_mode), 0),
    )
    expected_mode = (
        config["trade_amount_type"]
        if "trade_amount_type" in config
        else STOCK_CONFIG_EXPECTED_MISSING
    )
    result = writer(
        config_path,
        mode=target_mode,
        value=next_value,
        expected_fields={"trade_amount_type": expected_mode},
    )
    if not isinstance(result, dict):
        result = {
            "allowed": False,
            "changed": False,
            "reason": "INVALID_BUDGET_MODE_RESULT",
        }
    final_result = {
        **base_result,
        **result,
        "current_mode": current_mode,
        "target_mode": target_mode,
        "current_running": current_running,
        "current_price": current_price,
        "value": next_value,
    }
    final_result["reason_code"] = str(
        final_result.get("reason_code")
        or final_result.get("reason")
        or ""
    )
    return final_result
