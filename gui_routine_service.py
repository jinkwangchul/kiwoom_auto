# -*- coding: utf-8 -*-
"""
gui_routine_service.py

루틴 정합성 보정 Service 함수 모음.

현재 단계:
- 실제 config 수정/저장처럼 상태를 바꾸는 함수만 분리한다.
- UI, QMessageBox, QTableWidget에 의존하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gui_stock_data import (
    assigned_runtime_dirs_for_stock,
    normalize_stock_code,
)
from runtime_io import read_json_dict
from state_policy import real_trade_enabled
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
)
from gui_auto_trade_integrity import is_review_required_state
from stock_repository import (
    STOCK_CONFIG_EXPECTED_MISSING,
    STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
    StockConfigWriteResult,
    StockRepository,
)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stock_config_expected_fields(
    config: dict[str, object],
    field_keys: tuple[str, ...],
) -> dict[str, object]:
    return {
        key: config[key] if key in config else STOCK_CONFIG_EXPECTED_MISSING
        for key in field_keys
    }


def _invalid_stock_config_write_result(
    field_keys: tuple[str, ...],
) -> StockConfigWriteResult:
    return StockConfigWriteResult(
        ok=False,
        changed=False,
        field_keys=field_keys,
        conflict_detected=False,
        read_back_verified=False,
        reason_code=STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
    )


def _patch_canonical_stock_config(
    stock_dir: Path,
    patch: dict[str, object],
    *,
    expected_fields: dict[str, object],
) -> StockConfigWriteResult:
    target_dir = Path(stock_dir)
    stocks_dir = target_dir.parent
    stock_code = target_dir.name.partition("_")[0].strip()
    field_keys = tuple(patch.keys())
    if stocks_dir.name != "stocks" or not stock_code:
        return _invalid_stock_config_write_result(field_keys)
    repository = StockRepository(stocks_dir.parent)
    if repository.resolve_stock_dir(stock_code).resolve() != target_dir.resolve():
        return _invalid_stock_config_write_result(field_keys)
    return repository.patch_stock_config(
        stock_code,
        patch,
        expected_fields=expected_fields,
    )


def _patch_real_trade_enabled(
    stock_dir: Path,
    config: dict[str, object],
    enabled: bool,
    *,
    include_updated_at: bool,
) -> StockConfigWriteResult:
    changed_at = now_text()
    patch: dict[str, object] = {
        "real_trade_enabled": bool(enabled),
        "real_trade_policy_updated_at": changed_at,
    }
    if include_updated_at:
        patch["updated_at"] = changed_at
    return _patch_canonical_stock_config(
        stock_dir,
        patch,
        expected_fields=_stock_config_expected_fields(
            config,
            ("real_trade_enabled",),
        ),
    )


def default_config() -> dict[str, object]:
    return {
        "timeframe": "1m",
        "trade_amount_type": "QUANTITY",
        "buy_amount": 0,
        "buy_qty": 1,
        "buy_signal_bar": 1,
        "sell_signal_bar": 1,
        "buy_amount_mode": "ADD",
        "buy_amount_step": 1,
        "buy_amount_custom_steps": [],
        "max_buy_count": 3,
        "profit_hold_enabled": False,
        "profit_hold_percent": 0,
        "resell_condition": "NEXT_SELL_SIGNAL",
        "resell_profit_percent": 0,
        "allow_higher_rebuy": False,
        "daily_loss_limit": -3,
        "budget_limit": 1000000,
        "investment_type": "SHORT_TERM",
        "investment_period": 0,
        "start_time": "09:00",
        "end_buy_time": "13:30",
        "auto_start_enabled": False,
        "auto_start_time": "09:00",
        "operation_mode": "SCHEDULED",
        "real_trade_enabled": True,
    }


def ensure_single_real_trade_routine_for_stock(
    code: str,
    name: str,
    preferred_routine_name: str | None = None,
) -> str:
    """
    동일 종목 다중 루틴 등록 시 실주문 가능 루틴은 1개만 유지한다.

    Service 함수이다.
    config.json을 실제로 수정할 수 있다.
    """
    assigned = assigned_runtime_dirs_for_stock(code, name)
    if not assigned:
        return ""

    assigned_names = [routine_name for routine_name, _ in assigned]
    selected_routine = ""

    if preferred_routine_name and preferred_routine_name in assigned_names:
        selected_routine = preferred_routine_name
    else:
        for routine_name, stock_dir in assigned:
            config = read_json_dict(stock_dir / "config.json") or default_config()
            if real_trade_enabled(config):
                selected_routine = routine_name
                break

    if not selected_routine:
        return ""

    for routine_name, stock_dir in assigned:
        config = read_json_dict(stock_dir / "config.json") or default_config()
        next_enabled = routine_name == selected_routine
        if config.get("real_trade_enabled") != next_enabled:
            write_result = _patch_real_trade_enabled(
                stock_dir,
                config,
                next_enabled,
                include_updated_at=False,
            )
            if not write_result.ok:
                return ""

    return selected_routine


@dataclass(frozen=True)
class TradePermissionAvailability:
    allowed: bool
    reason_code: str
    stock_code: str
    requested_enabled: bool
    current_enabled: bool
    current_session_participant: bool
    review_required: bool
    config_available: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason_code,
            "reason_code": self.reason_code,
            "stock_code": self.stock_code,
            "requested_enabled": self.requested_enabled,
            "current_enabled": self.current_enabled,
            "current_session_participant": self.current_session_participant,
            "review_required": self.review_required,
            "config_available": self.config_available,
        }


def inspect_stock_real_trade_availability(
    window,
    stock_dir: Path,
    code: str,
) -> TradePermissionAvailability:
    return inspect_stock_real_trade_transition_availability(
        window,
        stock_dir,
        code,
        not real_trade_enabled(read_json_dict(Path(stock_dir) / "config.json")),
    )


def inspect_stock_real_trade_transition_availability(
    window,
    stock_dir: Path,
    code: str,
    enabled: bool,
) -> TradePermissionAvailability:
    """Inspect one permission transition without mutating config or runtime."""

    config = read_json_dict(Path(stock_dir) / "config.json")
    state = read_json_dict(stock_dir / "state.json")
    if not isinstance(state, dict):
        state = {}
    config_available = isinstance(config, dict) and bool(config)
    requested = bool(enabled)
    current_enabled = real_trade_enabled(config if config_available else default_config())
    clean_code = normalize_stock_code(code)
    participant_codes = {
        normalize_stock_code(value)
        for value in auto_trade_current_session_operation_participant_codes(window)
        if normalize_stock_code(value)
    }
    participant = bool(clean_code and clean_code in participant_codes)
    review_required = is_review_required_state(state)
    unchanged = bool(
        config_available
        and current_enabled == requested
        and config.get("real_trade_enabled") is requested
    )
    if not config_available:
        reason_code = "CONFIG_MISSING"
    elif participant:
        reason_code = "CURRENT_SESSION_PARTICIPANT"
    elif review_required:
        reason_code = "REVIEW_REQUIRED"
    elif unchanged:
        reason_code = "REALTRADE_STATE_UNCHANGED"
    else:
        reason_code = ""
    return TradePermissionAvailability(
        allowed=reason_code in {"", "REALTRADE_STATE_UNCHANGED"},
        reason_code=reason_code,
        stock_code=clean_code,
        requested_enabled=requested,
        current_enabled=current_enabled,
        current_session_participant=participant,
        review_required=review_required,
        config_available=config_available,
    )


def selected_stock_real_trade_target_enabled(
    selected: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...],
) -> bool:
    values = [
        real_trade_enabled(read_json_dict(Path(stock_dir) / "config.json") or default_config())
        for stock_dir, _code, _name in selected
    ]
    return bool(values) and not all(values)


def selected_stock_trade_permission_label(
    selected: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...],
) -> str:
    return (
        "실주문 전환"
        if selected_stock_real_trade_target_enabled(selected)
        else "감시전용 전환"
    )


def selected_stock_trade_permission_available(
    window,
    selected: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...],
) -> bool:
    targets = list(selected)
    if not targets:
        return False
    enabled = selected_stock_real_trade_target_enabled(targets)
    return all(
        inspect_stock_real_trade_transition_availability(
            window,
            stock_dir,
            code,
            enabled,
        ).allowed
        for stock_dir, code, _name in targets
    )


def set_stock_real_trade_enabled(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    enabled: bool,
) -> dict[str, object]:
    """Persist the existing real-trade permission field while the stock is stopped."""

    stock_dir = Path(stock_dir)
    availability = inspect_stock_real_trade_transition_availability(
        window,
        stock_dir,
        code,
        enabled,
    )
    if not availability.allowed:
        return {
            "ok": False,
            "changed": False,
            "allowed": False,
            "reason": availability.reason_code,
            "reason_code": availability.reason_code,
            "code": code,
            "name": name,
        }

    config = read_json_dict(stock_dir / "config.json")
    if not isinstance(config, dict) or not config:
        return {
            "ok": False,
            "changed": False,
            "allowed": False,
            "reason": "CONFIG_MISSING",
            "reason_code": "CONFIG_MISSING",
            "code": code,
            "name": name,
        }

    requested = bool(enabled)
    before = real_trade_enabled(config)
    if before == requested and config.get("real_trade_enabled") is requested:
        return {
            "ok": True,
            "changed": False,
            "allowed": True,
            "reason": "REALTRADE_STATE_UNCHANGED",
            "reason_code": "REALTRADE_STATE_UNCHANGED",
            "code": code,
            "name": name,
            "real_trade_enabled": requested,
        }

    write_result = _patch_real_trade_enabled(
        stock_dir,
        config,
        requested,
        include_updated_at=True,
    )
    if not write_result.ok:
        return {
            "ok": False,
            "changed": False,
            "allowed": True,
            "reason": write_result.reason_code,
            "reason_code": write_result.reason_code,
            "code": code,
            "name": name,
        }

    if requested:
        routine_name = str(
            config.get("routine_instance_name")
            or config.get("routine")
            or config.get("routine_name")
            or ""
        ).strip()
        ensure_single_real_trade_routine_for_stock(
            code,
            name,
            routine_name or None,
        )

    saved = read_json_dict(stock_dir / "config.json")
    if real_trade_enabled(saved) is not requested:
        return {
            "ok": False,
            "changed": False,
            "allowed": True,
            "reason": "READ_BACK_FAILED",
            "reason_code": "READ_BACK_FAILED",
            "code": code,
            "name": name,
        }

    return {
        "ok": True,
        "changed": write_result.changed,
        "allowed": True,
        "reason": "UPDATED",
        "reason_code": "UPDATED",
        "code": code,
        "name": name,
        "real_trade_enabled": requested,
    }


def execute_selected_stock_real_trade_command(
    window,
    selected: list[tuple[Path, str, str]] | tuple[tuple[Path, str, str], ...],
    enabled: bool,
) -> dict[str, object]:
    """Execute one Main/Settings permission command through the same owner."""

    targets: list[tuple[Path, str, str]] = []
    seen: set[str] = set()
    for stock_dir, code, name in selected:
        key = str(Path(stock_dir).resolve())
        if key in seen:
            continue
        seen.add(key)
        targets.append((Path(stock_dir), str(code), str(name)))
    if not targets:
        return {
            "ok": False,
            "changed": 0,
            "blocked": 0,
            "unchanged": 0,
            "allowed": False,
            "reason": "NO_SELECTION",
            "reason_code": "NO_SELECTION",
            "target_real_trade_enabled": bool(enabled),
            "changed_targets": (),
            "blocked_targets": (),
            "results": (),
        }

    changed_targets: list[str] = []
    blocked_targets: list[str] = []
    blocked_reason_codes: list[str] = []
    unchanged = 0
    results: list[dict[str, object]] = []
    for stock_dir, code, name in targets:
        result = set_stock_real_trade_enabled(
            window,
            stock_dir,
            code,
            name,
            enabled,
        )
        results.append(dict(result))
        label = f"{code} {name}".strip()
        if result.get("ok") is True:
            if result.get("changed") is True:
                changed_targets.append(label)
            else:
                unchanged += 1
        else:
            blocked_reason_codes.append(
                str(result.get("reason_code") or result.get("reason") or "BLOCKED")
            )
            blocked_targets.append(
                f"{label}({result.get('reason_code') or result.get('reason') or 'BLOCKED'})"
            )

    if blocked_targets and changed_targets:
        reason_code = "PARTIAL_BLOCKED"
    elif blocked_targets:
        unique_reasons = tuple(dict.fromkeys(blocked_reason_codes))
        reason_code = unique_reasons[0] if len(unique_reasons) == 1 else "BLOCKED"
    elif changed_targets:
        reason_code = "UPDATED"
    else:
        reason_code = "REALTRADE_STATE_UNCHANGED"
    return {
        "ok": not blocked_targets,
        "changed": len(changed_targets),
        "blocked": len(blocked_targets),
        "unchanged": unchanged,
        "allowed": not blocked_targets,
        "reason": reason_code,
        "reason_code": reason_code,
        "target_real_trade_enabled": bool(enabled),
        "changed_targets": tuple(changed_targets),
        "blocked_targets": tuple(blocked_targets),
        "results": tuple(results),
    }
