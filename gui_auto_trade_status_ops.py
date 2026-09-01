# -*- coding: utf-8 -*-
"""
gui_auto_trade_status_ops.py

자동매매설정창의 상태 재판정/운영방식 변경 처리 헬퍼.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox
from gui_operation_ui_context import operation_dialog_parent, refresh_auto_trade_views
from gui_toast import show_toast
from event_journal_production import append_production_event

from gui_config_utils import default_config, default_state
from gui_schedule_utils import (
    schedule_change_log_text,
    schedule_config_updates,
)
from runtime_io import read_json_dict
from group_scope import load_group_scope
from runtime_stock_state_mutation import mutate_runtime_stock_state
from gui_order_utils import pending_order_side_quantities
from gui_ats_utils import manual_ats_active_now
from manual_ats_runtime import (
    clear_manual_ats_runtime_selection,
    manual_ats_runtime_selected_keys,
)
from auto_close_runtime_policy_snapshot import (
    AUTO_CLOSE_RUNTIME_STATUSES,
    auto_close_runtime_snapshot_metadata,
)
from close_intent_service import CLOSE_INTENT_AUTO_CLOSE, apply_close_intent
from state_policy import (
    auto_trade_status_display,
    normalize_operation_mode,
    normalized_hhmmss_or_empty,
    operation_mode_check_text,
    operation_mode_change_decision,
    operation_mode_display,
    operation_mode_recalculation_target_status,
    read_global_schedule,
    read_operation_policy,
    scheduled_status_for_now,
    start_status_by_operation_mode,
    status_after_operation_mode_change,
    validate_buy_time_range,
)
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_trade_started,
)
from gui_stock_data import normalize_stock_code
from stock_repository import (
    STOCK_CONFIG_EXPECTED_MISSING,
    STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
    STOCK_CONFIG_WRITE_NO_CHANGE,
    StockConfigWriteResult,
    StockRepository,
)
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    inspect_stock_review_state,
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_required_state,
)


PROJECT_ROOT = Path(__file__).resolve().parent
ROUTINES_DIR = PROJECT_ROOT / "routines"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"
LOGGER = logging.getLogger(__name__)
EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS = frozenset(
    {
        "RECOVERY_CONTEXT_MISSING",
        "RECOVERY_NOT_STARTED",
        "RECOVERY_IN_PROGRESS",
    }
)
OPERATION_EXCLUDED_CONFIG_KEY = "operation_excluded"
OPERATION_EXCLUSION_RUNNING_BLOCK_MESSAGE = (
    "운영 중에는 더블클릭으로 운영 대상을 변경할 수 없습니다. 우클릭 운영시작을 사용하세요."
)
OPERATION_EXCLUSION_REVIEW_BLOCK_MESSAGE = (
    "검토관리 종목은 일반 운영제외 기능으로 변경할 수 없습니다."
)


@dataclass(frozen=True)
class OperationExclusionAvailability:
    allowed: bool
    reason_code: str
    stock_code: str
    requested_excluded: bool
    current_excluded: bool
    review_required: bool
    current_running: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason_code,
            "reason_code": self.reason_code,
            "stock_code": self.stock_code,
            "excluded": self.requested_excluded,
            "requested_excluded": self.requested_excluded,
            "current_excluded": self.current_excluded,
            "review_required": self.review_required,
            "current_running": self.current_running,
        }


@dataclass(frozen=True)
class OperationExclusionCommandResult:
    ok: bool
    changed: bool
    allowed: bool
    reason_code: str
    stock_code: str
    requested_excluded: bool
    current_excluded: bool
    review_required: bool
    current_running: bool


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


def _no_change_stock_config_write_result(
    field_keys: tuple[str, ...],
) -> StockConfigWriteResult:
    return StockConfigWriteResult(
        ok=True,
        changed=False,
        field_keys=field_keys,
        conflict_detected=False,
        read_back_verified=True,
        reason_code=STOCK_CONFIG_WRITE_NO_CHANGE,
    )


def _patch_canonical_stock_config(
    stock_dir: Path,
    patch: dict[str, object],
    *,
    expected_fields: dict[str, object] | None = None,
) -> StockConfigWriteResult:
    target_dir = Path(stock_dir)
    stocks_dir = target_dir.parent
    stock_code = normalize_stock_code(target_dir.name.partition("_")[0])
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

def current_datetime() -> datetime:
    return datetime.now()


def _production_recovery_gate(window, code: str, caller_name: str):
    try:
        parent = window.parent()
    except Exception:
        return None
    checker = getattr(type(parent), "production_recovery_gate_for_stock", None)
    if not callable(checker):
        return None
    return checker(parent, code, caller_name=caller_name)


def append_changelog(change_type: str, filename: str, message: str) -> None:
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )
    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
    try:
        logs_dir = stock_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d')}.log"
        line = f"[{now_text()}] [{event_type}] {message}"
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return log_path
    except Exception:
        return None


def auto_trade_stock_operation_excluded(stock_dir: Path) -> bool:
    config = read_json_dict(Path(stock_dir) / "config.json")
    return is_operation_excluded(config)


def inspect_auto_trade_operation_exclusion_availability(
    window,
    target: tuple[Path, str, str],
    excluded: bool,
) -> OperationExclusionAvailability:
    """Read the canonical guard state for one direct exclusion request."""

    stock_dir, code, _name = target
    clean_code = normalize_stock_code(code)
    config = read_json_dict(Path(stock_dir) / "config.json")
    loaded_state = read_json_dict(Path(stock_dir) / "state.json")
    review_inspection = inspect_stock_review_state(
        Path(stock_dir),
        loaded_state=loaded_state,
    )
    state = review_inspection.state
    current_excluded = is_operation_excluded(config)
    review_required = review_inspection.review_required
    current_running = clean_code in set(
        auto_trade_current_session_operation_participant_codes(window)
    )
    running_getter = getattr(window, "running_registered_operation_targets", None)
    running_targets: tuple[object, ...] | None = None
    if callable(running_getter):
        try:
            running_targets = tuple(running_getter())
        except Exception:
            running_targets = None
    if running_targets is not None:
        if not current_running:
            target_path = Path(stock_dir).resolve()
            current_running = any(
                (
                    clean_code
                    and normalize_stock_code(running_code) == clean_code
                )
                or Path(running_dir).resolve() == target_path
                for running_dir, running_code, _running_name in running_targets
            )
    elif not current_running:
        current_running = auto_trade_setting_current_session_trade_started(
            window,
            auto_trade_setting_trade_started(state),
            clean_code,
        )

    requested_excluded = bool(excluded)
    if review_required:
        reason_code = "REVIEW_REQUIRED"
    elif requested_excluded and current_running:
        reason_code = "CURRENTLY_RUNNING"
    elif current_excluded == requested_excluded:
        reason_code = "ALREADY_EXCLUDED" if requested_excluded else "NOT_EXCLUDED"
    else:
        reason_code = ""
    return OperationExclusionAvailability(
        allowed=reason_code not in {"REVIEW_REQUIRED", "CURRENTLY_RUNNING"},
        reason_code=reason_code,
        stock_code=clean_code,
        requested_excluded=requested_excluded,
        current_excluded=current_excluded,
        review_required=review_required,
        current_running=current_running,
    )


def auto_trade_operation_exclusion_mutation_decision(
    window,
    target: tuple[Path, str, str],
    excluded: bool,
) -> dict[str, object]:
    """Compatibility projection of the canonical exclusion availability."""

    return inspect_auto_trade_operation_exclusion_availability(
        window,
        target,
        excluded,
    ).as_dict()


def _patch_auto_trade_stock_operation_excluded(
    stock_dir: Path,
    excluded: bool,
    *,
    expected_fields: dict[str, object] | None = None,
) -> StockConfigWriteResult:
    config_path = Path(stock_dir) / "config.json"
    config = read_json_dict(config_path)
    if not config_path.exists() or not isinstance(config, dict):
        return _invalid_stock_config_write_result(
            (OPERATION_EXCLUDED_CONFIG_KEY,)
        )
    if is_operation_excluded(config) == bool(excluded):
        return _no_change_stock_config_write_result(
            (OPERATION_EXCLUDED_CONFIG_KEY,)
        )
    if expected_fields is None:
        expected_fields = _stock_config_expected_fields(
            config,
            (OPERATION_EXCLUDED_CONFIG_KEY,),
        )
    return _patch_canonical_stock_config(
        stock_dir,
        {
            OPERATION_EXCLUDED_CONFIG_KEY: bool(excluded),
            "updated_at": now_text(),
        },
        expected_fields=expected_fields,
    )


def set_auto_trade_stock_operation_excluded(stock_dir: Path, excluded: bool) -> bool:
    """Low-level writer retained for Operation Start's established side effects."""

    return _patch_auto_trade_stock_operation_excluded(stock_dir, excluded).ok


def execute_auto_trade_stock_operation_exclusion(
    window,
    target: tuple[Path, str, str],
    excluded: bool,
) -> OperationExclusionCommandResult:
    """Revalidate and execute one ordinary operator exclusion command."""

    stock_dir, code, _name = target
    availability = inspect_auto_trade_operation_exclusion_availability(
        window,
        target,
        excluded,
    )
    if not availability.allowed:
        return OperationExclusionCommandResult(
            ok=False,
            changed=False,
            allowed=False,
            reason_code=availability.reason_code,
            stock_code=availability.stock_code,
            requested_excluded=availability.requested_excluded,
            current_excluded=availability.current_excluded,
            review_required=availability.review_required,
            current_running=availability.current_running,
        )
    if availability.current_excluded == availability.requested_excluded:
        return OperationExclusionCommandResult(
            ok=True,
            changed=False,
            allowed=True,
            reason_code=availability.reason_code,
            stock_code=availability.stock_code,
            requested_excluded=availability.requested_excluded,
            current_excluded=availability.current_excluded,
            review_required=availability.review_required,
            current_running=availability.current_running,
        )

    config = read_json_dict(Path(stock_dir) / "config.json") or default_config()
    write_result = _patch_auto_trade_stock_operation_excluded(
        stock_dir,
        excluded,
        expected_fields=_stock_config_expected_fields(
            config,
            (OPERATION_EXCLUDED_CONFIG_KEY,),
        ),
    )
    return OperationExclusionCommandResult(
        ok=write_result.ok,
        changed=bool(write_result.ok and write_result.changed),
        allowed=True,
        reason_code=(
            "UPDATED"
            if write_result.ok and write_result.changed
            else write_result.reason_code
        ),
        stock_code=normalize_stock_code(code),
        requested_excluded=bool(excluded),
        current_excluded=is_operation_excluded(
            read_json_dict(Path(stock_dir) / "config.json")
        ),
        review_required=availability.review_required,
        current_running=availability.current_running,
    )


def _apply_auto_trade_stock_operation_exclusion(
    window,
    target: tuple[Path, str, str],
    excluded: bool,
    *,
    notify: bool,
    refresh: bool,
) -> OperationExclusionCommandResult:
    message_parent_getter = getattr(window, "operation_message_parent", None)
    message_parent = message_parent_getter() if callable(message_parent_getter) else window
    stock_dir, code, name = target
    result = execute_auto_trade_stock_operation_exclusion(window, target, excluded)
    if not result.allowed:
        status_message = getattr(window, "statusBarMessage", None)
        if callable(status_message):
            status_message(
                OPERATION_EXCLUSION_REVIEW_BLOCK_MESSAGE
                if result.reason_code == "REVIEW_REQUIRED"
                else OPERATION_EXCLUSION_RUNNING_BLOCK_MESSAGE
            )
        return result
    if not result.ok:
        QMessageBox.critical(
            message_parent,
            "저장 오류",
            f"{code} {name} 운영 제외 설정 저장 중 오류가 발생했습니다."
            f"\n\n{result.reason_code}",
        )
        return result
    if not result.changed:
        return result

    label = "운영 제외" if excluded else "운영 제외 해제"
    toast_message = "운영종목에서 제외됐습니다." if excluded else "운영종목으로 전환됐습니다."
    append_production_event(
        "OPERATION_EXCLUDED" if excluded else "OPERATION_EXCLUSION_RELEASED",
        result="COMPLETED",
        source="AutoTradeSettingWindow.set_stock_operation_exclusion",
        template_args={"stock_name": str(name or code)},
        target_type="STOCK",
        target_id=str(code),
        target_name=str(name),
        stock_code=str(code),
        stock_name=str(name),
    )
    append_stock_log(stock_dir, "GUI", f"{label}: {code} {name}")
    append_changelog("UPDATE", "config.json", f"{label}: {code} {name}")
    if notify:
        window.statusBarMessage(f"{code} {name} {label}")
        show_toast(message_parent, toast_message)
    if refresh:
        refresh_auto_trade_views(window)
    return result


def auto_trade_set_stock_operation_exclusion(
    window,
    target: tuple[Path, str, str],
    excluded: bool,
    *,
    notify: bool = True,
    refresh: bool = True,
) -> bool:
    return _apply_auto_trade_stock_operation_exclusion(
        window,
        target,
        excluded,
        notify=notify,
        refresh=refresh,
    ).ok


def auto_trade_toggle_stock_operation_exclusion(
    window,
    target: tuple[Path, str, str],
    *,
    refresh: bool = True,
) -> bool:
    stock_dir, _code, _name = target
    config = read_json_dict(stock_dir / "config.json") or default_config()
    return auto_trade_set_stock_operation_exclusion(
        window,
        target,
        not is_operation_excluded(config),
        refresh=refresh,
    )


def _set_selected_stock_operation_exclusions(window, excluded: bool) -> None:
    message_parent_getter = getattr(window, "operation_message_parent", None)
    message_parent = message_parent_getter() if callable(message_parent_getter) else window
    selected = window.selected_stock_infos()
    if not selected:
        return

    succeeded: list[str] = []
    failed = 0
    seen: set[str] = set()
    for target in selected:
        stock_dir, code, name = target
        key = str(Path(stock_dir).resolve())
        if key in seen:
            continue
        seen.add(key)
        result = _apply_auto_trade_stock_operation_exclusion(
            window,
            target,
            excluded,
            notify=False,
            refresh=False,
        )
        if result.ok and result.changed:
            succeeded.append(name or code)
        elif not result.ok:
            failed += 1

    if succeeded:
        refresh_auto_trade_views(window)
        if excluded:
            one_message = f"{succeeded[0]}을 운영제외했습니다."
            many_message = f"{len(succeeded)}개 종목을 운영제외했습니다."
        else:
            one_message = f"{succeeded[0]}의 운영제외를 해제했습니다."
            many_message = f"{len(succeeded)}개 종목의 운영제외를 해제했습니다."
        if len(succeeded) == 1 and failed == 0:
            show_toast(message_parent, one_message)
        elif failed:
            show_toast(message_parent, f"{many_message} 실패 {failed}개")
        else:
            show_toast(message_parent, many_message)
    elif failed:
        show_toast(
            message_parent,
            "운영제외에 실패했습니다." if excluded else "운영제외 해제에 실패했습니다.",
        )


def auto_trade_set_selected_stock_operation_exclusions(window) -> None:
    _set_selected_stock_operation_exclusions(window, True)


def auto_trade_clear_selected_stock_operation_exclusions(window) -> None:
    _set_selected_stock_operation_exclusions(window, False)



def get_group_dirs() -> list[Path]:
    """Return project-root Group paths from the canonical discovery boundary."""
    from gui_routine_registry import get_group_dirs as registry_get_group_dirs

    return registry_get_group_dirs()




def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """종목 폴더명에서 코드/종목명을 추출한다."""
    text = str(folder_name).strip()
    if "_" in text:
        code, name = text.split("_", 1)
        return code.strip(), name.strip()
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""



def auto_trade_update_stock_status(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    new_status: str,
    extra_state: dict[str, object] | None = None,
    log_suffix: str = "",
) -> bool:
    state_path = stock_dir / "state.json"
    state = read_json_dict(state_path)
    before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
    previous_review_entered_at = str(
        state.get("review_entered_at", "") or ""
    ).strip()
    requested_review_entered_at = (
        str(extra_state.get("review_entered_at", "") or "").strip()
        if isinstance(extra_state, dict)
        else ""
    )
    new_status_key = str(new_status or "").strip().upper()
    updated_at = now_text()

    mutation_metadata = dict(extra_state or {})
    if new_status_key in {"REVIEW_REQUIRED", "REVIEW"}:
        if before_status not in {"REVIEW_REQUIRED", "REVIEW"}:
            mutation_metadata["review_entered_at"] = requested_review_entered_at or updated_at
        else:
            mutation_metadata["review_entered_at"] = previous_review_entered_at

    mutation_result = mutate_runtime_stock_state(
        stock_dir,
        new_status,
        mutation_metadata,
        updated_at=updated_at,
    )
    if not mutation_result.ok:
        if not bool(getattr(window, "_operation_start_batch_active", False)):
            QMessageBox.critical(
                window,
                "상태 저장 오류",
                f"{code} {name} 상태 저장 중 오류가 발생했습니다.",
            )
        append_stock_log(stock_dir, "ERROR", f"상태 저장 실패: {before_status} -> {new_status}")
        return False

    suppress_normal_log = (
        isinstance(extra_state, dict)
        and str(extra_state.get("operation_notice", "")).strip().upper()
        in {"NO_CLOSE_TARGET", "AUTO_CLOSE_NO_TARGET", "EARLY_CLOSE_NO_TARGET"}
        and not log_suffix
    )
    if not suppress_normal_log:
        suffix_text = f" / {log_suffix}" if log_suffix else ""
        append_stock_log(stock_dir, "GUI", f"자동매매 상태 변경: {before_status} -> {new_status}{suffix_text}")
    return True



def auto_trade_operation_policy_protected_status(window, status: object) -> bool:
    """운영방식/시간정책 자동 재판정에서 건드리면 안 되는 보호 상태.

    단순 status 값만으로 조기마감을 보호하지 않는다.
    조기마감 보호 여부는 state 메타값까지 함께 보는
    auto_trade_setting_should_preserve_raw_status()에서 판단한다.
    """
    current = str(status or "STOPPED").strip().upper() or "STOPPED"
    state = {"status": current}
    return (
        is_emergency_stopped_state(state)
        or is_review_required_state(state)
        or current in {
            "FORCE_CLOSE",
            "FORCE_LIQUIDATION",
        }
    )


def _operation_policy_status_read_back_matches(
    stock_dir: Path,
    expected_status: str,
    expected_metadata: dict[str, object],
) -> bool:
    saved_state = read_json_dict(stock_dir / "state.json")
    if not saved_state:
        append_stock_log(stock_dir, "ERROR", "운영정책 재판정 read-back 실패: state.json 읽기 실패")
        return False
    if str(saved_state.get("status", "")).strip().upper() != expected_status:
        append_stock_log(
            stock_dir,
            "ERROR",
            f"운영정책 재판정 read-back 실패: status={saved_state.get('status', '')} / expected={expected_status}",
        )
        return False
    if any(saved_state.get(key) != value for key, value in expected_metadata.items()):
        append_stock_log(stock_dir, "ERROR", "운영정책 재판정 read-back 실패: metadata 불일치")
        return False
    return True



def auto_trade_recalculate_stock_status_by_operation_policy(
    window,
    stock_dir: Path,
    code: str,
    name: str,
    reason: str,
    extra_state: dict[str, object] | None = None,
    silent_unchanged: bool = False,
) -> tuple[str, str, str]:
    """운영방식/현재시간 기준으로 상태를 중앙 재판정한다.

    반환값: (result, before_status, after_status)
    - changed: 상태 변경됨
    - unchanged: 재판정했지만 상태 동일
    - protected: 긴급정지/검토종목/조기마감 등 보호상태라 미변경
    - failed: 저장 실패
    """
    state_path = stock_dir / "state.json"
    if not state_path.exists():
        append_stock_log(stock_dir, "ERROR", f"운영정책 재판정 실패: Runtime 파일 없음 / {reason}")
        return "failed", "", ""
    state = read_json_dict(state_path)
    if not state:
        append_stock_log(stock_dir, "ERROR", f"운영정책 재판정 실패: Runtime 파일 손상 / {reason}")
        return "failed", "", ""
    before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
    start_requested = bool(extra_state and extra_state.get("trade_enabled") is True)

    if is_review_required_state(state):
        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 보호: 검토관리 우선 / {reason}",
        )
        return "protected", before_status, before_status

    operation_active_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}
    data_reasons = auto_trade_setting_data_inconsistency_reasons(state)
    if (
        not start_requested
        and before_status in operation_active_statuses
        and data_reasons
    ):
        marker = getattr(window, "mark_review_required", None)
        if not callable(marker):
            append_stock_log(
                stock_dir,
                "ERROR",
                f"운영 중 데이터 불일치 검토관리 전환 실패: mark_review_required 없음 / {reason}",
            )
            return "failed", before_status, before_status
        review_item = {
            "review_reasons": data_reasons,
            "current_price": state.get("current_price", 0),
            "pnl_rate_text": str(state.get("pnl_rate_text", "-") or "-"),
        }
        if marker(stock_dir, code, name, review_item, source="운영중"):
            return "protected", before_status, "REVIEW_REQUIRED"
        return "failed", before_status, before_status

    if bool(state.get("signal_probe_only", False)):
        probe_metadata = {
            "trade_enabled": True,
            "real_trade_enabled": False,
            "signal_probe_only": True,
            "operation_policy_recalculated_at": now_text(),
            "operation_policy_reason": reason,
            "operation_policy_mode": "SIGNAL_PROBE_ONLY",
        }
        needs_restore = before_status != "MONITORING" or any(
            state.get(key) != value for key, value in probe_metadata.items()
            if key in {"trade_enabled", "real_trade_enabled", "signal_probe_only"}
        )
        if needs_restore:
            if window.update_stock_status(
                stock_dir,
                code,
                name,
                "MONITORING",
                probe_metadata,
                "신호평가 전용 상태 보호",
            ):
                if _operation_policy_status_read_back_matches(
                    stock_dir,
                    "MONITORING",
                    probe_metadata,
                ):
                    return "protected", before_status, "MONITORING"
            return "failed", before_status, "MONITORING"

        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 보호: 신호평가 전용 상태 유지 / {reason}",
            )
        return "protected", before_status, before_status

    if auto_trade_setting_should_preserve_raw_status(state, before_status):
        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 보호상태 유지: {auto_trade_status_display(before_status)} / {reason}",
            )
        return "protected", before_status, before_status

    # 재시작/수동중지 상태에서는 시간 타이머가 상태를 자동으로 다시 켜면 안 된다.
    # 매매시작 버튼이 trade_enabled=True 메타를 전달한 경우에만 시간정책 재판정 진입을 허용한다.
    if not start_requested and not auto_trade_setting_trade_started(state):
        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 제외: 매매시작 전/재시작 중지 상태 / {reason}",
            )
        return "unchanged", before_status, before_status

    config = read_json_dict(stock_dir / "config.json")
    if not config:
        config = default_config()

    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    new_status = status_after_operation_mode_change(mode, config)
    if start_requested and extra_state:
        guarded_start_status = str(
            extra_state.get("start_policy_status") or ""
        ).strip().upper()
        if guarded_start_status in {"RUNNING", "MONITORING"}:
            # 공통 운영시작 Backend가 같은 시각으로 완료한 Guard 판정을
            # 상태/권한 단일 write에서도 그대로 사용한다.
            new_status = guarded_start_status

    recalculated_at = now_text()
    metadata = {
        "operation_policy_recalculated_at": recalculated_at,
        "operation_policy_reason": reason,
        "operation_policy_mode": mode,
    }
    operation_policy = read_operation_policy()
    auto_close_policy = operation_policy.get("auto_close", {})
    snapshot_metadata = auto_close_runtime_snapshot_metadata(
        state=state,
        before_status=before_status,
        after_status=new_status,
        auto_close_policy=(
            auto_close_policy if isinstance(auto_close_policy, dict) else {}
        ),
        captured_at=recalculated_at,
    )
    if (
        new_status in AUTO_CLOSE_RUNTIME_STATUSES
        and (
            before_status not in AUTO_CLOSE_RUNTIME_STATUSES
            or bool(snapshot_metadata)
        )
    ):
        recovery = _production_recovery_gate(
            window,
            code,
            "AUTO_CLOSE_TIME_POLICY",
        )
        if recovery is not None and recovery.allowed is not True:
            reason_code = str(
                getattr(recovery, "reason_code", "") or ""
            ).strip()
            evidence = tuple(getattr(recovery, "evidence", ()) or ())
            has_internal_error_evidence = any(
                str(item).startswith(("registry_error=", "gate_exception="))
                for item in evidence
            )
            if (
                reason_code in EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS
                and not has_internal_error_evidence
            ):
                return "protected", before_status, before_status
            try:
                parent = window.parent()
            except Exception:
                parent = None
            api = getattr(parent, "kiwoom_api", None)
            login_session_reader = getattr(api, "login_session_id", None)
            login_session_present = False
            if callable(login_session_reader):
                try:
                    login_session_present = bool(
                        str(login_session_reader() or "").strip()
                    )
                except Exception:
                    login_session_present = False
            account_reader = getattr(parent, "selected_account_no", None)
            account_selected = False
            if callable(account_reader):
                try:
                    account_selected = bool(str(account_reader() or "").strip())
                except Exception:
                    account_selected = False
            LOGGER.warning(
                "Auto-close blocked by Production Recovery: "
                "caller=%s routine_instance=%s stock=%s reason=%s evidence=%s "
                "login_session_present=%s account_selected=%s requested_at=%s",
                "AUTO_CLOSE_TIME_POLICY",
                str(config.get("assigned_routine_instance_id") or "").strip(),
                code,
                reason_code,
                evidence,
                login_session_present,
                account_selected,
                recalculated_at,
            )
            return "protected", before_status, before_status
        transition_result = apply_close_intent(
            intent=CLOSE_INTENT_AUTO_CLOSE,
            stock_dir=stock_dir,
            stock_code=code,
            stock_name=name,
            runtime_state=state,
            runtime_config=config,
            current_status=before_status,
            requested_status=new_status,
            metadata=snapshot_metadata,
            log_suffix="",
            status_writer=lambda *_args, **_kwargs: True,
            read_back_checker=lambda *_args, **_kwargs: True,
            queue_path=ORDER_QUEUE_PATH,
            fills_path=FILLS_PATH,
            dry_run=True,
        )
        transition = transition_result.get("transition")
        if transition_result.get("blocked"):
            append_stock_log(
                stock_dir,
                "BLOCKED",
                "자동마감 정책 전환 차단: "
                f"{getattr(transition, 'reason_code', transition_result.get('reason'))} / "
                f"evidence={getattr(transition, 'evidence_status', '')}",
            )
            return "protected", before_status, before_status
    metadata.update(snapshot_metadata)
    if extra_state:
        metadata.update(extra_state)

    def _write_recalculated_status(log_suffix: str) -> str:
        if new_status in AUTO_CLOSE_RUNTIME_STATUSES:
            result = apply_close_intent(
                intent=CLOSE_INTENT_AUTO_CLOSE,
                stock_dir=stock_dir,
                stock_code=code,
                stock_name=name,
                runtime_state=state,
                runtime_config=config,
                current_status=before_status,
                requested_status=new_status,
                metadata=metadata,
                log_suffix=log_suffix,
                status_writer=window.update_stock_status,
                read_back_checker=_operation_policy_status_read_back_matches,
                queue_path=ORDER_QUEUE_PATH,
                fills_path=FILLS_PATH,
            )
            if result.get("ok"):
                return "ok"
            if result.get("blocked"):
                transition = result.get("transition")
                append_stock_log(
                    stock_dir,
                    "BLOCKED",
                    "AUTO_CLOSE transition blocked: "
                    f"{result.get('reason')} / "
                    f"evidence={getattr(transition, 'evidence_status', '')}",
                )
                return "protected"
            return "failed"

        if window.update_stock_status(
            stock_dir,
            code,
            name,
            new_status,
            metadata,
            log_suffix,
        ):
            if _operation_policy_status_read_back_matches(
                stock_dir,
                new_status,
                metadata,
            ):
                return "ok"
        return "failed"

    if new_status == before_status:
        # 상태가 같아도 운영시작/정책 재판정 메타값은 반드시 저장한다.
        # 예: 감시/대기 -> 감시/대기 상태유지여도 trade_enabled=True가 저장되어야
        # 현황 컬럼이 즉시 켜지고 이후 시간정책 자동판정 대상이 된다.
        if extra_state or snapshot_metadata:
            log_suffix = (
                f"운영정책 재판정 상태유지/메타갱신: "
                f"{operation_mode_display(mode)} / {auto_trade_status_display(before_status)} / {reason}"
            )
            write_result = _write_recalculated_status(log_suffix)
            if write_result == "ok":
                return "unchanged", before_status, new_status
            if write_result == "protected":
                return "protected", before_status, before_status
            return "failed", before_status, new_status

        if not silent_unchanged:
            append_stock_log(
                stock_dir,
                "GUI",
                f"운영정책 재판정 상태유지: {auto_trade_status_display(before_status)} / {operation_mode_display(mode)} / {reason}",
            )
        return "unchanged", before_status, new_status

    log_suffix = (
        f"운영정책 재판정: {operation_mode_display(mode)} / "
        f"{auto_trade_status_display(before_status)} -> {auto_trade_status_display(new_status)} / {reason}"
    )
    write_result = _write_recalculated_status(log_suffix)
    if write_result == "ok":
        return "changed", before_status, new_status
    if write_result == "protected":
        return "protected", before_status, before_status
    return "failed", before_status, new_status



def auto_trade_recalculate_all_status_by_operation_policy(
    window,
    reason: str,
    silent_unchanged: bool = False,
    write_changelog_when_unchanged: bool = True,
) -> dict[str, int]:
    """전체 루틴 전체 종목을 운영방식/현재시간 기준으로 재판정한다."""
    result = {"changed": 0, "unchanged": 0, "protected": 0, "failed": 0}
    stock_dirs_getter = getattr(window, "all_runtime_stock_dirs", None)
    stock_dirs = (
        list(stock_dirs_getter())
        if callable(stock_dirs_getter)
        else list(load_group_scope().all_group_stock_dirs())
    )
    for stock_dir in stock_dirs:
        code, name = parse_stock_folder_name(stock_dir.name)
        status, _, _ = window.recalculate_stock_status_by_operation_policy(
            stock_dir,
            code,
            name,
            reason,
            silent_unchanged=silent_unchanged,
        )
        if status not in result:
            result[status] = 0
        result[status] += 1
    if write_changelog_when_unchanged or result.get("changed", 0) or result.get("failed", 0):
        append_changelog(
            "UPDATE",
            "state.json",
            f"전체 운영정책 재판정: {reason} / 변경 {result.get('changed', 0)}개 / 유지 {result.get('unchanged', 0)}개 / 보호 {result.get('protected', 0)}개 / 실패 {result.get('failed', 0)}개",
        )
    return result



def auto_trade_update_stock_operation_mode(window, stock_dir: Path, code: str, name: str, operation_mode: str, config_updates: dict[str, object] | None = None) -> bool:
    mode = normalize_operation_mode(operation_mode)
    config_path = stock_dir / "config.json"
    config = read_json_dict(config_path)
    if not config:
        config = default_config()

    before_config = dict(config)
    before_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    target_config = dict(config)
    if config_updates:
        target_config.update(config_updates)

    decision_config = target_config if mode == "SCHEDULED" else config
    decision_now = current_datetime()
    runtime_state = read_json_dict(stock_dir / "state.json")
    manual_ats_selected = bool(manual_ats_runtime_selected_keys(runtime_state))
    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(
        stock_dir,
        runtime_state,
    )
    pending_order_active = (
        buy_pending_qty == "?"
        or sell_pending_qty == "?"
        or int(buy_pending_qty) > 0
        or int(sell_pending_qty) > 0
    )
    runtime_status = str(runtime_state.get("status", "STOPPED")).strip().upper()
    close_or_liquidation_active = (
        bool(runtime_state.get("close_routine_final_sell_ordered", False))
        or (
            bool(runtime_state.get("liquidation_policy_forced", False))
            and runtime_status not in {"AUTO_CLOSED", "EARLY_CLOSED", "LIQUIDATED"}
        )
    )
    decision = operation_mode_change_decision(
        decision_config,
        mode,
        decision_now,
        ats_runtime_active=(
            manual_ats_active_now(config, runtime_state, decision_now)
            and auto_trade_setting_trade_started(runtime_state)
        ),
        runtime_status=runtime_status,
        pending_order_active=pending_order_active,
        close_or_liquidation_active=close_or_liquidation_active,
        runtime_state_available=bool(runtime_state),
    )
    if not decision["allowed"]:
        scheduled_end_time = str(decision.get("scheduled_end_time") or "")
        reason = str(decision["reason"])
        append_stock_log(
            stock_dir,
            "BLOCKED",
            f"운영방식 변경 차단: {reason} / "
            f"{operation_mode_display(before_mode)} -> {operation_mode_display(mode)} / "
            f"현재 {decision['current_time']} / 종료 {scheduled_end_time or '-'}",
        )
        return False

    target_fields: dict[str, object] = {"operation_mode": mode}
    if config_updates:
        for key in (
            "start_time",
            "trade_start_time",
            "end_buy_time",
            "buy_end_time",
        ):
            if key in config_updates:
                target_fields[key] = target_config.get(key)
        start_time = normalized_hhmmss_or_empty(
            target_config.get("start_time", target_config.get("trade_start_time", ""))
        )
        end_buy_time = normalized_hhmmss_or_empty(
            target_config.get("end_buy_time", target_config.get("buy_end_time", ""))
        )
        if start_time and end_buy_time:
            target_fields.update(
                {
                    "start_time": start_time,
                    "trade_start_time": start_time,
                    "end_buy_time": end_buy_time,
                    "buy_end_time": end_buy_time,
                }
            )

    semantic_field_keys = tuple(target_fields.keys())
    semantic_changed = any(
        key not in before_config or before_config.get(key) != value
        for key, value in target_fields.items()
    )
    if semantic_changed:
        target_fields["operation_mode_updated_at"] = now_text()
        write_result = _patch_canonical_stock_config(
            stock_dir,
            target_fields,
            expected_fields=_stock_config_expected_fields(
                before_config,
                semantic_field_keys,
            ),
        )
    else:
        write_result = _no_change_stock_config_write_result(semantic_field_keys)

    if not write_result.ok:
        append_stock_log(
            stock_dir,
            "ERROR",
            "운영방식 저장 실패: "
            f"{operation_mode_display(before_mode)} -> {operation_mode_display(mode)}"
            f" / {write_result.reason_code}",
        )
        return False

    saved_config = read_json_dict(config_path)
    if any(saved_config.get(key) != value for key, value in target_fields.items()):
        append_stock_log(
            stock_dir,
            "ERROR",
            f"운영방식 저장 read-back 실패: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)}",
        )
        return False

    if before_mode != mode and manual_ats_selected:
        if not clear_manual_ats_runtime_selection(stock_dir):
            append_stock_log(
                stock_dir,
                "ERROR",
                "운영방식 변경 후 ATS 설정 해제 실패: "
                f"{operation_mode_display(before_mode)} -> {operation_mode_display(mode)}",
            )
            return False

    tracked_keys = (
        "operation_mode",
        "start_time",
        "trade_start_time",
        "end_buy_time",
        "buy_end_time",
    )
    changes = [
        {
            "field_key": key,
            "before": config_value,
            "after": saved_config.get(key),
        }
        for key in tracked_keys
        for config_value in [
            before_config.get(key) if key != "operation_mode" else before_mode
        ]
        if config_value != saved_config.get(key)
    ]
    if changes:
        append_production_event(
            "TRADING_TIME_CHANGED",
            result="SUCCESS",
            source="STOCK_OPERATION_MODE_WRITER",
            template_args={},
            target_type="STOCK",
            target_id=str(code or "").strip(),
            target_name=str(name or "").strip(),
            stock_code=str(code or "").strip(),
            stock_name=str(name or "").strip(),
            changes=changes,
        )

    append_stock_log(stock_dir, "GUI", f"운영방식 변경: {operation_mode_display(before_mode)} -> {operation_mode_display(mode)}")
    return True



def auto_trade_set_selected_schedule_operation_mode(window) -> None:
    """
    하위 호환용: 선택 종목 개별 시간설정으로 연결한다.
    """
    window.set_selected_individual_schedule_time()


def handle_auto_trade_operation_mode_double_click(
    window,
    target: tuple[Path, str, str],
) -> dict[str, object]:
    """Apply the production operation-cell double-click contract to one target."""

    stock_dir, code, name = target
    config = read_json_dict(Path(stock_dir) / "config.json")
    if not isinstance(config, dict) or not config:
        QMessageBox.warning(
            operation_dialog_parent(window),
            "운영방식 변경",
            f"{code} {name}의 운영방식 설정을 읽을 수 없습니다.",
        )
        return {
            "requested": 1,
            "succeeded": 0,
            "failed": 1,
            "results": [
                {
                    "stock_code": code,
                    "stock_name": name,
                    "stock_dir": str(stock_dir),
                    "success": False,
                    "reason": "운영방식 설정을 읽을 수 없습니다.",
                }
            ],
        }

    current_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    if current_mode == "CONTINUOUS":
        global_schedule = read_global_schedule()
        return auto_trade_set_operation_mode_for_targets(
            window,
            [target],
            "SCHEDULED",
            schedule_config_updates(
                global_schedule["start_time"],
                global_schedule["end_buy_time"],
            ),
        )
    return auto_trade_set_operation_mode_for_targets(
        window,
        [target],
        "CONTINUOUS",
    )



def auto_trade_set_operation_mode_for_targets(
    window,
    selected: list[tuple[Path, str, str]],
    operation_mode: str,
    config_updates: dict[str, object] | None = None,
    *,
    finalize: bool = True,
) -> dict[str, object]:
    """Apply one operation-mode request to a fixed target snapshot."""
    targets = list(selected)
    dialog_parent = operation_dialog_parent(window)
    result: dict[str, object] = {
        "requested": len(targets),
        "succeeded": 0,
        "failed": 0,
        "results": [],
    }
    if not targets:
        QMessageBox.warning(
            dialog_parent,
            "선택 오류",
            "운영방식 변경은 종목을 1개 이상 선택해야 합니다.",
        )
        return result

    mode = normalize_operation_mode(operation_mode)
    display_mode = operation_mode_display(mode)
    routine_name = window.current_selected_routine_name()
    routine_context = routine_name or (
        "전체"
        if bool(getattr(window, "_all_stocks_scope_active", False))
        else "선택 종목"
    )

    target_results = result["results"]
    assert isinstance(target_results, list)
    for stock_dir, code, name in targets:
        changed = window.update_stock_operation_mode(
            stock_dir,
            code,
            name,
            mode,
            config_updates,
        )
        if not changed:
            target_results.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "stock_dir": str(stock_dir),
                    "success": False,
                    "reason": "운영방식 또는 시간 설정을 변경할 수 없습니다.",
                }
            )
            result["failed"] = int(result["failed"]) + 1
            continue

        status_result, before_status, new_status = (
            window.recalculate_stock_status_by_operation_policy(
                stock_dir,
                code,
                name,
                "운영방식/시간 설정 변경",
                {"operation_mode_status_applied_at": now_text()},
            )
        )
        changelog_parts = [f"대상: {code} {name}"]
        schedule_log_text = schedule_change_log_text(config_updates)
        if schedule_log_text:
            changelog_parts.append(schedule_log_text)
        if status_result == "changed":
            changelog_parts.append(
                f"상태재판정: {code} {name}({auto_trade_status_display(new_status)})"
            )
        elif status_result == "failed":
            changelog_parts.append(f"상태재판정실패: {code} {name}")
        elif status_result == "protected":
            changelog_parts.append(
                f"보호상태유지: {code} {name}({auto_trade_status_display(before_status)})"
            )

        append_changelog(
            "UPDATE",
            "config.json/state.json",
            f"종목별 운영방식 변경: {routine_context} -> {display_mode}: {' | '.join(changelog_parts)}",
        )
        target_results.append(
            {
                "stock_code": code,
                "stock_name": name,
                "stock_dir": str(stock_dir),
                "success": True,
                "reason": "",
                "status_result": status_result,
            }
        )
        result["succeeded"] = int(result["succeeded"]) + 1

    if finalize:
        auto_trade_finalize_operation_mode_result(window, result)
    return result


def auto_trade_finalize_operation_mode_result(
    window,
    result: dict[str, object],
) -> None:
    """Refresh UI adapters and present the existing operation-mode failure contract."""

    succeeded = int(result.get("succeeded", 0) or 0)
    if succeeded:
        refresh_auto_trade_views(window)

    failed = int(result.get("failed", 0) or 0)
    if not failed:
        return

    target_results = result.get("results")
    items = target_results if isinstance(target_results, list) else []
    failed_lines = [
        f"{item['stock_code']} {item['stock_name']}: {item['reason']}"
        for item in items
        if isinstance(item, dict) and not bool(item.get("success"))
    ]
    requested = int(result.get("requested", 0) or 0)
    if requested == 1:
        message = "선택한 종목을 변경할 수 없습니다."
    elif succeeded:
        message = (
            f"일부 종목의 운영방식/시간 설정을 변경하지 못했습니다.\n\n"
            f"성공 {succeeded}개 / 실패 {failed}개\n"
            + "\n".join(failed_lines[:10])
        )
    else:
        message = (
            "선택한 종목의 운영방식/시간 설정을 변경할 수 없습니다.\n\n"
            + "\n".join(failed_lines[:10])
        )
    QMessageBox.warning(
        operation_dialog_parent(window),
        "운영방식 변경",
        message,
    )


def auto_trade_apply_schedule_times_to_targets(
    window,
    selected: list[tuple[Path, str, str]],
    start_time: object,
    end_buy_time: object,
) -> dict[str, object]:
    """Apply one validated individual schedule request without UI side effects."""

    targets = list(selected)
    result: dict[str, object] = {
        "requested": len(targets),
        "succeeded": 0,
        "failed": 0,
        "results": [],
    }
    if not targets:
        return result

    valid, validation_reason = validate_buy_time_range(start_time, end_buy_time)
    normalized_start = normalized_hhmmss_or_empty(start_time)
    normalized_end = normalized_hhmmss_or_empty(end_buy_time)
    eligible: list[tuple[Path, str, str]] = []
    target_results = result["results"]
    assert isinstance(target_results, list)

    for stock_dir, code, name in targets:
        reason = ""
        if not valid:
            reason = validation_reason
        else:
            config = read_json_dict(Path(stock_dir) / "config.json") or default_config()
            if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "SCHEDULED":
                reason = "시간 설정은 시간운영 종목에만 적용할 수 있습니다."
        if reason:
            target_results.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "stock_dir": str(stock_dir),
                    "success": False,
                    "reason": reason,
                }
            )
            result["failed"] = int(result["failed"]) + 1
        else:
            eligible.append((stock_dir, code, name))

    if not eligible:
        return result

    applied = auto_trade_set_operation_mode_for_targets(
        window,
        eligible,
        "SCHEDULED",
        schedule_config_updates(normalized_start, normalized_end),
        finalize=False,
    )
    applied_results = applied.get("results")
    if isinstance(applied_results, list):
        target_results.extend(applied_results)
    result["succeeded"] = int(applied.get("succeeded", 0) or 0)
    result["failed"] = int(result["failed"]) + int(applied.get("failed", 0) or 0)
    return result


def auto_trade_reset_schedule_times_for_targets(
    window,
    selected: list[tuple[Path, str, str]],
) -> dict[str, object]:
    """Reset individual schedules from the canonical global schedule source."""

    global_schedule = read_global_schedule()
    return auto_trade_apply_schedule_times_to_targets(
        window,
        selected,
        global_schedule["start_time"],
        global_schedule["end_buy_time"],
    )


def auto_trade_set_selected_operation_mode(
    window,
    operation_mode: str,
    config_updates: dict[str, object] | None = None,
) -> dict[str, object]:
    selected = list(window.selected_stock_infos())
    return auto_trade_set_operation_mode_for_targets(
        window,
        selected,
        operation_mode,
        config_updates,
    )
