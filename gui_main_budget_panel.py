# -*- coding: utf-8 -*-
"""
gui_main_budget_panel.py

메인 관제창 예산 현황 표시 전용 헬퍼.

현재 범위:
- 시스템 전체예산과 canonical 가용 비율을 표시/저장한다.
- 완충 비율과 두 금액은 시스템 전체예산에서만 파생한다.
- 주문수량 산출, 매수 제한, 루틴/종목 배분 로직은 수행하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from PyQt5.QtCore import Qt

from gui_operation_environment import (
    read_system_budget_policy,
    validate_available_budget_percent,
    validate_system_total_budget,
    write_system_budget_policy,
)
from account_auto_trade_budget_consumption import (
    project_account_auto_trade_budget_consumption,
)
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED
from production_recovery_state_registry import production_recovery_registry
from gui_order_utils import DIRECTIONAL_NEGATIVE_COLOR

MAIN_TOTAL_BUDGET_PERCENT_OPTIONS = (100, 90, 80, 70, 60, 50, 40, 30, 20, 10)
BUDGET_AVAILABLE_WARNING_THRESHOLDS = (90, 80, 70, 60, 50, 40, 30, 20, 10)
PROJECT_ROOT = Path(__file__).resolve().parent
POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"


def _format_money(value: int) -> str:
    return f"{int(value):,}"


def _nonnegative_integer_amount(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("money value must be an integer amount")
    try:
        amount = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("money value must be an integer amount") from exc
    if amount < 0:
        raise ValueError("money value must not be negative")
    return amount


def set_metric_value_text(label, text: object) -> None:
    """Keep the fixed value slot while centering only its '-' projection."""
    display_text = str(text)
    label.setText(display_text)
    set_alignment = getattr(label, "setAlignment", None)
    if callable(set_alignment):
        set_alignment(
            Qt.AlignCenter
            if display_text.strip() == "-"
            else Qt.AlignRight | Qt.AlignVCenter
        )


def set_buffer_entry_value_style(window, entry_amount: object) -> None:
    """Highlight only a positive buffer entry; zero/unavailable stay neutral."""
    try:
        entered = entry_amount is not None and int(entry_amount) > 0
    except (TypeError, ValueError):
        entered = False
    style = f"color: {DIRECTIONAL_NEGATIVE_COLOR};" if entered else ""
    for attr_name in (
        "budget_buffer_entry_label",
        "budget_buffer_entry_ratio_label",
    ):
        label = getattr(window, attr_name, None)
        set_style_sheet = getattr(label, "setStyleSheet", None)
        if callable(set_style_sheet):
            set_style_sheet(style)
    response_badge_styler = getattr(
        window,
        "_apply_main_budget_buffer_response_badge_style",
        None,
    )
    if callable(response_badge_styler):
        response_badge_styler(entered)


def project_system_budget_amounts(
    total_budget: object,
    available_budget_percent: object,
) -> dict[str, object]:
    """Project system-wide available/buffer amounts without lower-level budgets."""
    total = validate_system_total_budget(total_budget)
    available_percent = validate_available_budget_percent(available_budget_percent)
    buffer_percent = 100 - available_percent
    # Keep the spendable side conservative and leave any integer remainder in
    # the buffer so the two projected amounts always add back to total_budget.
    available_amount = (total * available_percent) // 100
    buffer_amount = total - available_amount
    return {
        "total_budget": total,
        "available_budget_percent": available_percent,
        "buffer_budget_percent": buffer_percent,
        "available_budget": available_amount,
        "buffer_budget": buffer_amount,
        "buffer_enabled": buffer_percent > 0,
    }


def collect_main_budget_summary(*, policy_path: Path | None = None) -> dict[str, object]:
    """Load and project the canonical system-wide budget policy once."""
    budget = read_system_budget_policy(path=policy_path)
    return project_system_budget_amounts(
        budget["total_budget"],
        budget["available_budget_percent"],
    )


def _unavailable_consumption(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "consumed_amount": None,
        "reason": str(reason or "budget consumption unavailable"),
    }


def collect_main_account_budget_consumption(window) -> dict[str, object]:
    """Bind the current MainWindow Recovery identity to the read-only projection."""
    selected_account = getattr(window, "selected_account_no", None)
    if not callable(selected_account):
        return _unavailable_consumption("selected account reader is unavailable")
    try:
        account_no = str(selected_account() or "").strip()
    except Exception as exc:
        return _unavailable_consumption(f"selected account read failed: {exc}")
    if not account_no:
        return _unavailable_consumption("selected account is unavailable")

    context = production_recovery_registry.snapshot()
    window_identity = getattr(window, "_production_recovery_identity", None)
    if (
        context is None
        or window_identity is None
        or context.identity != window_identity
        or context.account_status != ACCOUNT_COMPLETED
        or context.identity.account_no != account_no
        or context.identity.trading_day != datetime.now().date().isoformat()
    ):
        return _unavailable_consumption("current account Recovery is not complete")

    api = getattr(window, "kiwoom_api", None)
    login_session_reader = getattr(api, "login_session_id", None)
    try:
        login_session_id = (
            str(login_session_reader() or "").strip()
            if callable(login_session_reader)
            else ""
        )
    except Exception:
        login_session_id = ""
    if login_session_id != context.identity.login_session_id:
        return _unavailable_consumption("login session does not match Recovery")

    reconciled_codes = {
        stock.stock_code
        for stock in context.stocks
        if stock.stock_status == STOCK_RESTORED and not stock.review_required
    }
    if len(reconciled_codes) != len(context.stocks):
        return _unavailable_consumption("Recovery stock scope is incomplete")
    return project_account_auto_trade_budget_consumption(
        account_no=account_no,
        positions_path=POSITIONS_PATH,
        order_queue_path=ORDER_QUEUE_PATH,
        recovery_complete=True,
        reconciled_stock_codes=reconciled_codes,
    )


def project_main_budget_activity(
    summary: dict[str, object],
    consumption: dict[str, object],
) -> dict[str, object]:
    """Project remaining/entry values from one verified occupied-capital total."""
    if consumption.get("available") is not True:
        return {
            "available": False,
            "remaining_amount": None,
            "remaining_ratio": None,
            "entry_amount": None,
            "entry_ratio": None,
            "policy_exceeded": False,
        }
    try:
        total_budget = _nonnegative_integer_amount(summary.get("total_budget"))
        available_budget = _nonnegative_integer_amount(summary.get("available_budget"))
        buffer_budget = _nonnegative_integer_amount(summary.get("buffer_budget"))
        consumed_amount = _nonnegative_integer_amount(consumption.get("consumed_amount"))
    except ValueError:
        return {
            "available": False,
            "remaining_amount": None,
            "remaining_ratio": None,
            "entry_amount": None,
            "entry_ratio": None,
            "policy_exceeded": False,
        }
    if available_budget <= 0:
        return {
            "available": False,
            "remaining_amount": None,
            "remaining_ratio": None,
            "entry_amount": None,
            "entry_ratio": None,
            "policy_exceeded": consumed_amount > total_budget,
        }

    remaining_amount = max(available_budget - consumed_amount, 0)
    remaining_ratio = (
        Decimal(remaining_amount) * Decimal("100") / Decimal(available_budget)
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    buffer_enabled = bool(summary.get("buffer_enabled", False))
    if buffer_enabled and buffer_budget > 0:
        entry_amount: int | None = max(consumed_amount - available_budget, 0)
        entry_ratio: Decimal | None = (
            Decimal(entry_amount) * Decimal("100") / Decimal(buffer_budget)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    else:
        entry_amount = None
        entry_ratio = None
    return {
        "available": True,
        "remaining_amount": remaining_amount,
        "remaining_ratio": remaining_ratio,
        "entry_amount": entry_amount,
        "entry_ratio": entry_ratio,
        "policy_exceeded": consumed_amount > total_budget,
    }


def project_main_budget_warning_transition(
    *,
    previous_available_remaining_ratio: object | None,
    previous_buffer_entered: bool | None,
    activity: dict[str, object],
    buffer_enabled: bool,
) -> dict[str, object]:
    """Project session-local warning crossings without changing budget state."""

    unavailable = {
        "available": False,
        "available_remaining_ratio": None,
        "buffer_entered": None,
        "available_threshold_crossed": None,
        "buffer_entry_started": False,
    }
    if activity.get("available") is not True:
        return unavailable
    try:
        current_ratio = Decimal(str(activity.get("remaining_ratio")))
    except Exception:
        return unavailable

    crossed_threshold: int | None = None
    if previous_available_remaining_ratio is not None:
        try:
            previous_ratio = Decimal(str(previous_available_remaining_ratio))
        except Exception:
            previous_ratio = current_ratio
        if current_ratio < previous_ratio:
            crossed = [
                threshold
                for threshold in BUDGET_AVAILABLE_WARNING_THRESHOLDS
                if current_ratio <= Decimal(threshold) < previous_ratio
            ]
            if crossed:
                crossed_threshold = min(crossed)
        elif current_ratio > previous_ratio:
            crossed = [
                threshold
                for threshold in BUDGET_AVAILABLE_WARNING_THRESHOLDS
                if previous_ratio < Decimal(threshold) <= current_ratio
            ]
            if crossed:
                crossed_threshold = max(crossed)

    buffer_entered: bool | None = None
    buffer_entry_started = False
    if buffer_enabled and activity.get("entry_amount") is not None:
        try:
            buffer_entered = _nonnegative_integer_amount(
                activity.get("entry_amount")
            ) > 0
        except ValueError:
            buffer_entered = None
        if (
            previous_buffer_entered is not None
            and previous_buffer_entered is False
            and buffer_entered is True
        ):
            buffer_entry_started = True

    return {
        "available": True,
        "available_remaining_ratio": current_ratio,
        "buffer_entered": buffer_entered,
        "available_threshold_crossed": crossed_threshold,
        "buffer_entry_started": buffer_entry_started,
    }


def _format_ratio(value: object) -> str:
    return f"{Decimal(str(value)):.1f}%"


def total_budget_from_orderable_cash(
    orderable_cash: object,
    percent: object,
    *,
    align_digits: bool = False,
) -> int:
    """Calculate one fixed total-budget amount from a point-in-time snapshot."""
    try:
        cash = _nonnegative_integer_amount(orderable_cash)
        ratio = int(str(percent).replace("%", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("orderable cash and percent must be integers") from exc
    if ratio not in MAIN_TOTAL_BUDGET_PERCENT_OPTIONS:
        raise ValueError("unsupported total-budget percentage")
    amount = (cash * ratio) // 100
    if ratio != 100 and align_digits:
        amount = round_money_to_two_significant_digits(amount)
    if amount > cash:
        raise ValueError("calculated total_budget exceeds current orderable cash")
    return validate_system_total_budget(amount)


def round_money_to_two_significant_digits(value: object) -> int:
    """Round a nonnegative integer half-up while preserving two leading digits."""
    amount = _nonnegative_integer_amount(value)
    digits = len(str(amount))
    if digits <= 2:
        return amount
    quantum = 10 ** (digits - 2)
    return ((amount + (quantum // 2)) // quantum) * quantum


def persist_main_total_budget(
    value: object,
    *,
    orderable_cash: object | None = None,
    policy_path: Path | None = None,
) -> dict[str, object]:
    """Persist a fixed total amount while preserving the canonical available ratio."""
    total_budget = validate_system_total_budget(value)
    if orderable_cash is not None:
        maximum = _nonnegative_integer_amount(orderable_cash)
        if total_budget > maximum:
            raise ValueError("total_budget exceeds current orderable cash")
    current = read_system_budget_policy(path=policy_path)
    saved = write_system_budget_policy(
        total_budget=total_budget,
        available_budget_percent=current["available_budget_percent"],
        path=policy_path,
    )
    return project_system_budget_amounts(
        saved["total_budget"],
        saved["available_budget_percent"],
    )


def persist_main_budget_percent(
    source: str,
    value: object,
    *,
    policy_path: Path | None = None,
) -> dict[str, object]:
    """Persist an edited ratio while keeping available percent canonical."""
    current = read_system_budget_policy(path=policy_path)
    if source == "available":
        available_percent = validate_available_budget_percent(value)
        if available_percent == 100:
            raise ValueError("가용 100%는 완충 0% 입력으로만 설정할 수 있습니다.")
    elif source == "buffer":
        buffer_text = str(value).strip()
        if not buffer_text.isdigit():
            raise ValueError("완충 비율은 정수여야 합니다.")
        buffer_percent = int(buffer_text)
        if buffer_percent < 0 or buffer_percent >= 100:
            raise ValueError("완충 비율은 0 이상 100 미만이어야 합니다.")
        available_percent = 100 - buffer_percent
    else:
        raise ValueError("unknown budget percent source")

    saved = write_system_budget_policy(
        total_budget=current["total_budget"],
        available_budget_percent=available_percent,
        path=policy_path,
    )
    return project_system_budget_amounts(
        saved["total_budget"],
        saved["available_budget_percent"],
    )


def update_main_budget_panel(window) -> None:
    """MainWindow의 예산 현황 QLabel들을 갱신한다."""
    summary = collect_main_budget_summary()
    amounts_confirmed = (
        getattr(window, "_main_budget_orderable_valid", None) is True
    )
    buffer_enabled = bool(summary.get("buffer_enabled", False))

    label_map = {
        "budget_total_label": _format_money(int(summary.get("total_budget", 0))),
        "budget_available_label": (
            _format_money(int(summary.get("available_budget", 0)))
            if amounts_confirmed
            else "-"
        ),
        "budget_reserve_label": (
            _format_money(int(summary.get("buffer_budget", 0)))
            if amounts_confirmed and buffer_enabled
            else "-"
        ),
    }

    for attr_name, text in label_map.items():
        label = getattr(window, attr_name, None)
        if label is not None:
            set_metric_value_text(label, text)

    activity = {
        "available": False,
        "remaining_amount": None,
        "remaining_ratio": None,
        "entry_amount": None,
        "entry_ratio": None,
    }
    if amounts_confirmed:
        activity = project_main_budget_activity(
            summary,
            collect_main_account_budget_consumption(window),
        )
    activity_text = {
        "budget_available_remaining_label": (
            _format_money(int(activity["remaining_amount"]))
            if activity.get("available") is True
            and activity.get("remaining_amount") is not None
            else "-"
        ),
        "budget_available_remaining_ratio_label": (
            _format_ratio(activity["remaining_ratio"])
            if activity.get("available") is True
            and activity.get("remaining_ratio") is not None
            else "-"
        ),
        "budget_buffer_entry_label": (
            _format_money(int(activity["entry_amount"]))
            if activity.get("available") is True
            and activity.get("entry_amount") is not None
            else "-"
        ),
        "budget_buffer_entry_ratio_label": (
            _format_ratio(activity["entry_ratio"])
            if activity.get("available") is True
            and activity.get("entry_ratio") is not None
            else "-"
        ),
    }
    for attr_name, text in activity_text.items():
        label = getattr(window, attr_name, None)
        if label is not None:
            set_metric_value_text(label, text)
    set_buffer_entry_value_style(
        window,
        activity.get("entry_amount") if activity.get("available") is True else None,
    )

    available_edit = getattr(window, "budget_available_percent_edit", None)
    if available_edit is not None:
        available_edit.setText(
            str(summary.get("available_budget_percent", 100))
            if buffer_enabled
            else "-"
        )
    buffer_edit = getattr(window, "budget_buffer_percent_edit", None)
    if buffer_edit is not None:
        buffer_edit.setText(
            str(summary.get("buffer_budget_percent", 0))
            if buffer_enabled
            else "-"
        )
    for attr_name in (
        "budget_available_percent_suffix_label",
        "budget_buffer_percent_suffix_label",
    ):
        suffix_label = getattr(window, attr_name, None)
        if suffix_label is not None:
            suffix_label.setText("% :" if buffer_enabled else " :")

    warning_handler = getattr(window, "handle_main_budget_warning_projection", None)
    if callable(warning_handler):
        warning_handler(activity=activity, buffer_enabled=buffer_enabled)
