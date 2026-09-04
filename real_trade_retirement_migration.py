# -*- coding: utf-8 -*-
"""One-time, allowlisted migration for retiring legacy real-trade mode fields."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from runtime_stock_state_mutation import (
    RUNTIME_STOCK_STATE_EXPECTED_MISSING,
    delete_runtime_stock_state_fields,
)
from stock_repository import (
    STOCK_CONFIG_DELETE_FIELD,
    StockRepository,
)


MIGRATION_NAME = "REAL_TRADE_RETIREMENT_R2"
RESULT_MIGRATED = "MIGRATED"
RESULT_ALREADY_MIGRATED = "ALREADY_MIGRATED"
RESULT_PREVIEW_STALE = "PREVIEW_STALE"
RESULT_APPLICATION_ACTIVE = "APPLICATION_ACTIVE"
RESULT_APPLICATION_CHECK_FAILED = "APPLICATION_CHECK_FAILED"
RESULT_TARGET_NOT_APPROVED = "TARGET_NOT_APPROVED"
RESULT_BLOCKED_OBLIGATION = "BLOCKED_OBLIGATION"
RESULT_DATA_CONFLICT = "DATA_CONFLICT"
RESULT_PARTIAL_MIGRATION = "PARTIAL_MIGRATION"

CONFIG_LEGACY_FIELDS = ("real_trade_enabled", "real_trade_policy_updated_at")
STATE_LEGACY_FIELD = "real_trade_enabled"

CURRENT_SCHEMA = "CURRENT_SCHEMA"
MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
DATA_INVALID = "DATA_INVALID"
LEGACY_DENIAL_PRESENT = "LEGACY_DENIAL_PRESENT"

R6_CLEANUP_NAME = "REAL_TRADE_RETIREMENT_R6"
RESULT_CLEANUP_READY = "CLEANUP_READY"
RESULT_CLEANUP_COMPLETE = "CLEANUP_COMPLETE"
RESULT_CLEANUP_ALREADY_COMPLETE = "CLEANUP_ALREADY_COMPLETE"


@dataclass(frozen=True)
class MigrationTargetSpec:
    stock_code: str
    stock_name: str
    config_raw_sha256: str
    state_raw_sha256: str
    config_canonical_before: str
    config_canonical_after: str
    state_canonical_before: str
    state_canonical_after: str
    assigned_routine_instance_id: str
    instance_enabled: bool | None
    operation_excluded: bool
    status: str


@dataclass(frozen=True)
class LegacySchemaCompatibilityResult:
    status: str
    reason_code: str
    stock_code: str
    stock_dir: str
    legacy_fields: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.status == CURRENT_SCHEMA


@dataclass(frozen=True)
class R6CleanupInventoryExpectation:
    stock_count: int = 18
    config_true: int = 15
    config_false: int = 0
    config_malformed: int = 0
    config_timestamp_present: int = 15
    state_true: int = 6
    state_false: int = 0
    state_malformed: int = 0


APPROVED_TARGET_SPECS = (
    MigrationTargetSpec(
        stock_code="005070",
        stock_name="코스모신소재",
        config_raw_sha256="92F9E60C87CAD0FE4ADEC59988B522F33FF2522113FE694BF313C5AFBB8C4CF2",
        state_raw_sha256="F82BB79581C8FE7430474280CA9EC2CCACE032F755C8A8B14D91E3A5F05A2996",
        config_canonical_before="D0CDB58E871AD775D6A569E13626A7A773AE8702D072F66D5575D4D391C4C4C1",
        config_canonical_after="3AC14A79D99844E083C89D39EFDA77797774F73AEA272F09CED53F2A57B55B22",
        state_canonical_before="721CE35239D5AE7795BDF90CB1C40CF9D9B1FADACD0ED83E0EEEA5835624C63D",
        state_canonical_after="BD2307205072755E908243D52B5E99D1EA7256D26D7BA43D1E83DEB66098C67D",
        assigned_routine_instance_id="",
        instance_enabled=None,
        operation_excluded=False,
        status="MONITORING",
    ),
    MigrationTargetSpec(
        stock_code="012210",
        stock_name="삼미금속",
        config_raw_sha256="0522364C8BA27B10C10A8C6BCDB50EBE07359240D5C95F7453FFDDAFEC9BBF7C",
        state_raw_sha256="F48008948DF6FCE86E2AE56F1985312E8F00952A89BF86E3EA4ED08298010577",
        config_canonical_before="61D93B8085F5A8C6B4EB2504F33D4540D0B0C78045C0F1AE7C89077FD0F38863",
        config_canonical_after="5EB2D469B73EEF8C662899D844A2ECC3A360E4BC3F7D3230BE6232754833D521",
        state_canonical_before="659B3F26C355CF21DCDD78813E601B3969A286A7B2926626E41A3F006F315CF5",
        state_canonical_after="EBFC7B8343D9E1786CEC0B6110786A8B02555BAD610A1B95C760AB8A08E72097",
        assigned_routine_instance_id="",
        instance_enabled=None,
        operation_excluded=True,
        status="MONITORING",
    ),
    MigrationTargetSpec(
        stock_code="032680",
        stock_name="소프트센",
        config_raw_sha256="92282B4A0056EFA90191BB25D39D3D2820428329243DC42D4A0B9F818345D8F4",
        state_raw_sha256="8514E55705B1BC8E391E2E76A361E79794855FA40943F34F90833E9D8A6AF978",
        config_canonical_before="7F5F372CDDDC504BA43536E93E5B1F60DECC5C8D6AC302AD6253DF80CFB97345",
        config_canonical_after="EBCC3F078648F0E5C1B2ED394481DD56F26918A6B8E5B91DA022470300DEA420",
        state_canonical_before="92E286EE4EFCD33C376AF87FDA164E11E09D8DCD842384B1B64FA7A76699A820",
        state_canonical_after="92E286EE4EFCD33C376AF87FDA164E11E09D8DCD842384B1B64FA7A76699A820",
        assigned_routine_instance_id="38d63d8c-40b6-41d4-a094-afe56ac39df9",
        instance_enabled=False,
        operation_excluded=False,
        status="STOPPED",
    ),
)
APPROVED_TARGET_CODES = frozenset(spec.stock_code for spec in APPROVED_TARGET_SPECS)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _read_json(path: Path) -> tuple[dict[str, object] | None, bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, b""
    return (value if isinstance(value, dict) else None), raw


def _stock_code_from_dir(stock_dir: Path) -> str:
    return str(stock_dir.name.split("_", 1)[0] or "").strip().upper()


def inspect_real_trade_schema_compatibility(
    stock_dir: str | Path,
    *,
    expected_stock_code: str = "",
    project_root: str | Path | None = None,
) -> LegacySchemaCompatibilityResult:
    """Inspect retired-field presence without interpreting it as permission.

    The current schema is defined only by field absence.  This function is
    read-only and deliberately does not repair legacy or malformed data.
    """

    target_dir = Path(stock_dir)
    stock_code = _stock_code_from_dir(target_dir)
    expected_code = str(expected_stock_code or "").strip().upper()
    evidence: list[str] = []
    if not stock_code or (expected_code and stock_code != expected_code):
        return LegacySchemaCompatibilityResult(
            DATA_INVALID,
            DATA_INVALID,
            expected_code or stock_code,
            str(target_dir),
            evidence=("STOCK_IDENTITY_CONFLICT",),
        )

    config, _config_raw = _read_json(target_dir / "config.json")
    state, _state_raw = _read_json(target_dir / "state.json")
    if config is None or state is None:
        return LegacySchemaCompatibilityResult(
            DATA_INVALID,
            DATA_INVALID,
            stock_code,
            str(target_dir),
            evidence=(
                "CONFIG_INVALID" if config is None else "STATE_INVALID",
            ),
        )

    legacy_fields: list[str] = []
    denial_present = False
    false_compatible = {"", "0", "false", "off", "no", "disabled"}
    for field in CONFIG_LEGACY_FIELDS:
        if field not in config:
            continue
        legacy_fields.append(f"config.{field}")
        value = config.get(field)
        if field == "real_trade_enabled" and (
            value is False
            or str(value or "").strip().lower() in false_compatible
        ):
            denial_present = True
    if STATE_LEGACY_FIELD in state:
        legacy_fields.append(f"state.{STATE_LEGACY_FIELD}")
        value = state.get(STATE_LEGACY_FIELD)
        if value is False or str(value or "").strip().lower() in false_compatible:
            denial_present = True

    root = Path(project_root) if project_root is not None else target_dir.parent.parent
    guard_path = root / "runtime" / "real_trade_guard.json"
    if guard_path.exists():
        guard, _guard_raw = _read_json(guard_path)
        if guard is None:
            return LegacySchemaCompatibilityResult(
                DATA_INVALID,
                DATA_INVALID,
                stock_code,
                str(target_dir),
                evidence=("REAL_TRADE_GUARD_INVALID",),
            )
        if "real_trade_enabled" in guard:
            legacy_fields.append("runtime.real_trade_guard.real_trade_enabled")
            value = guard.get("real_trade_enabled")
            if value is False or str(value or "").strip().lower() in false_compatible:
                denial_present = True

    if legacy_fields:
        if denial_present:
            evidence.append(LEGACY_DENIAL_PRESENT)
        evidence.extend(legacy_fields)
        return LegacySchemaCompatibilityResult(
            MIGRATION_REQUIRED,
            MIGRATION_REQUIRED,
            stock_code,
            str(target_dir),
            tuple(legacy_fields),
            tuple(evidence),
        )
    return LegacySchemaCompatibilityResult(
        CURRENT_SCHEMA,
        CURRENT_SCHEMA,
        stock_code,
        str(target_dir),
    )


def inspect_stock_code_real_trade_schema(
    project_root: str | Path,
    stock_code: str,
) -> LegacySchemaCompatibilityResult:
    root = Path(project_root)
    code = str(stock_code or "").strip().upper()
    matches = sorted(path for path in (root / "stocks").glob(f"{code}_*") if path.is_dir())
    if not code or len(matches) != 1:
        return LegacySchemaCompatibilityResult(
            DATA_INVALID,
            DATA_INVALID,
            code,
            "",
            evidence=("STOCK_IDENTITY_CONFLICT",),
        )
    return inspect_real_trade_schema_compatibility(
        matches[0],
        expected_stock_code=code,
        project_root=root,
    )


def _record_stock_code(record: object) -> str:
    if not isinstance(record, dict):
        return ""
    for key in ("stock_code", "code", "symbol"):
        value = str(record.get(key) or "").strip().upper()
        if value.startswith("A") and len(value) == 7:
            value = value[1:]
        if value:
            return value
    return ""


def _records(path: Path, key: str) -> list[dict[str, object]] | None:
    data, _raw = _read_json(path)
    if data is None:
        return None
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return None
    return list(value)


def _default_application_active() -> bool:
    if os.name != "nt":
        return False
    process_id = os.getpid()
    command = (
        "$self="
        + str(process_id)
        + "; @(Get-CimInstance Win32_Process | Where-Object { "
        "$_.ProcessId -ne $self -and ((($_.Name -match '^(python|pythonw|kiwoom).*') "
        "-and ($_.CommandLine -match '[\\\\/]gui_main\\.py(?:\\s|$)')) "
        "-or ($_.Name -match '^kiwoom')) }).Count"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError("APPLICATION_PROCESS_CHECK_FAILED")
    return int(completed.stdout.strip() or "0") > 0


class RealTradeRetirementMigration:
    def __init__(
        self,
        project_root: str | Path,
        *,
        target_specs: Iterable[MigrationTargetSpec] = APPROVED_TARGET_SPECS,
        application_active_check: Callable[[], bool] = _default_application_active,
    ) -> None:
        self.project_root = Path(project_root)
        self.target_specs = tuple(target_specs)
        self.application_active_check = application_active_check
        self.repository = StockRepository(self.project_root)

    @property
    def migration_id(self) -> str:
        identity = [
            (spec.stock_code, spec.config_raw_sha256, spec.state_raw_sha256)
            for spec in self.target_specs
        ]
        return "RTRM-" + _canonical_sha256(identity)[:20]

    def _target_dir(self, spec: MigrationTargetSpec) -> Path | None:
        matches = sorted((self.project_root / "stocks").glob(f"{spec.stock_code}_*"))
        return matches[0] if len(matches) == 1 and matches[0].is_dir() else None

    def _application_guard(self) -> str:
        try:
            return RESULT_APPLICATION_ACTIVE if self.application_active_check() else ""
        except Exception:
            return RESULT_APPLICATION_CHECK_FAILED

    def _obligation_blockers(
        self,
        spec: MigrationTargetSpec,
        stock_dir: Path,
        state: dict[str, object],
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        if int(state.get("holding_qty", 0) or 0) != 0:
            blockers.append("HOLDING_EXISTS")
        if float(state.get("avg_price", 0) or 0) != 0:
            blockers.append("POSITION_MISMATCH")
        if state.get("review_required") is True:
            blockers.append("REVIEW_REQUIRED")
        if state.get("early_close_requested_at") or state.get("auto_close_requested_at"):
            blockers.append("CLOSE_LIQUIDATION_ACTIVE")

        stock_orders = _records(stock_dir / "orders.json", "orders")
        if stock_orders is None:
            blockers.append("ORDERS_INVALID")
        elif stock_orders:
            blockers.append("PENDING_ORDER")

        runtime_sources = (
            ("order_queue.json", "orders", "QUEUE_ACTIVE"),
            ("order_executions.json", "executions", "EXECUTION_ACTIVE"),
            ("order_locks.json", "locks", "LOCK_ACTIVE"),
        )
        for filename, key, reason in runtime_sources:
            records = _records(self.project_root / "runtime" / filename, key)
            if records is None:
                blockers.append(f"{reason}_RUNTIME_INVALID")
            elif any(_record_stock_code(item) == spec.stock_code for item in records):
                blockers.append(reason)

        for filename, key in (
            ("positions.json", "positions"),
            ("broker_holdings.json", "holdings"),
        ):
            records = _records(self.project_root / "runtime" / filename, key)
            if records is None:
                blockers.append("POSITION_RUNTIME_INVALID")
                continue
            for item in records:
                if _record_stock_code(item) != spec.stock_code:
                    continue
                quantity = int(
                    item.get("holding_qty", item.get("quantity", item.get("qty", 0)))
                    or 0
                )
                if quantity != 0:
                    blockers.append("POSITION_EXISTS")

        broker_data, _raw = _read_json(
            self.project_root / "runtime" / "broker_holdings.json"
        )
        if broker_data is not None:
            reviews = broker_data.get("production_recovery_reviews", [])
            if isinstance(reviews, list) and any(
                isinstance(item, dict)
                and _record_stock_code(item) == spec.stock_code
                and str(item.get("status") or "").upper() not in {"RESOLVED", "CLOSED"}
                for item in reviews
            ):
                blockers.append("RECOVERY_REQUIRED")

        mock_data, _raw = _read_json(
            self.project_root / "mock_validation" / "runtime" / "current_sessions.json"
        )
        current_by_stock = (
            mock_data.get("current_by_stock", {}) if isinstance(mock_data, dict) else {}
        )
        if isinstance(current_by_stock, dict) and spec.stock_code in current_by_stock:
            blockers.append("MOCK_SESSION_ACTIVE")
        return tuple(dict.fromkeys(blockers))

    def _target_preflight(self, spec: MigrationTargetSpec) -> dict[str, object]:
        stock_dir = self._target_dir(spec)
        if stock_dir is None:
            return {"ok": False, "reason_code": RESULT_DATA_CONFLICT}
        config, config_raw = _read_json(stock_dir / "config.json")
        state, state_raw = _read_json(stock_dir / "state.json")
        if config is None or state is None:
            return {"ok": False, "reason_code": RESULT_DATA_CONFLICT}

        config_canonical = _canonical_sha256(config)
        state_canonical = _canonical_sha256(state)
        config_fields_present = all(field in config for field in CONFIG_LEGACY_FIELDS)
        config_fields_missing = all(
            field not in config for field in CONFIG_LEGACY_FIELDS
        )
        config_phase = (
            "BEFORE"
            if _sha256_bytes(config_raw) == spec.config_raw_sha256
            and config_canonical == spec.config_canonical_before
            and config_fields_present
            else "AFTER"
            if config_canonical == spec.config_canonical_after
            and config_fields_missing
            else "INVALID"
        )
        state_field_present = STATE_LEGACY_FIELD in state
        state_phase = (
            "BEFORE"
            if _sha256_bytes(state_raw) == spec.state_raw_sha256
            and state_canonical == spec.state_canonical_before
            and state_field_present
            else "AFTER"
            if state_canonical == spec.state_canonical_after
            and not state_field_present
            else "INVALID"
        )
        if config_phase == "INVALID" or state_phase == "INVALID":
            return {
                "ok": False,
                "reason_code": RESULT_PREVIEW_STALE,
                "stock_code": spec.stock_code,
                "config_sha256": _sha256_bytes(config_raw),
                "state_sha256": _sha256_bytes(state_raw),
            }

        assignment = str(config.get("assigned_routine_instance_id") or "")
        if (
            assignment != spec.assigned_routine_instance_id
            or bool(config.get("operation_excluded", False)) != spec.operation_excluded
            or str(state.get("status") or "").upper() != spec.status
        ):
            return {"ok": False, "reason_code": RESULT_DATA_CONFLICT}
        if assignment:
            instance, _raw = _read_json(
                self.project_root / "routine_instances" / assignment / "instance.json"
            )
            if instance is None or instance.get("enabled") is not spec.instance_enabled:
                return {"ok": False, "reason_code": RESULT_DATA_CONFLICT}

        blockers = self._obligation_blockers(spec, stock_dir, state)
        if blockers:
            return {
                "ok": False,
                "reason_code": RESULT_BLOCKED_OBLIGATION,
                "stock_code": spec.stock_code,
                "blockers": blockers,
            }
        return {
            "ok": True,
            "stock_code": spec.stock_code,
            "stock_dir": str(stock_dir),
            "config_phase": config_phase,
            "state_phase": state_phase,
            "config_sha256": _sha256_bytes(config_raw),
            "state_sha256": _sha256_bytes(state_raw),
            "config": config,
            "state": state,
        }

    def preflight(self) -> dict[str, object]:
        guard_reason = self._application_guard()
        if guard_reason:
            return {"ok": False, "reason_code": guard_reason}
        requested_codes = tuple(spec.stock_code for spec in self.target_specs)
        if (
            not requested_codes
            or len(set(requested_codes)) != len(requested_codes)
            or any(code not in APPROVED_TARGET_CODES for code in requested_codes)
        ):
            return {"ok": False, "reason_code": RESULT_TARGET_NOT_APPROVED}
        evaluations = [self._target_preflight(spec) for spec in self.target_specs]
        failed = next((item for item in evaluations if item.get("ok") is not True), None)
        return {
            "ok": failed is None,
            "reason_code": str(failed.get("reason_code")) if failed else "READY",
            "migration_id": self.migration_id,
            "targets": evaluations,
        }

    def dry_run(self) -> dict[str, object]:
        preflight = self.preflight()
        return {
            **preflight,
            "dry_run": True,
            "planned_changes": tuple(
                {
                    "stock_code": spec.stock_code,
                    "config_delete_fields": CONFIG_LEGACY_FIELDS,
                    "state_delete_fields": (STATE_LEGACY_FIELD,),
                }
                for spec in self.target_specs
            ),
        }

    def migrate(self) -> dict[str, object]:
        preflight = self.preflight()
        if preflight.get("ok") is not True:
            return preflight
        executed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        results: list[dict[str, object]] = []
        for spec, evaluation in zip(self.target_specs, preflight["targets"]):
            stock_dir = Path(str(evaluation["stock_dir"]))
            config_before, config_raw = _read_json(stock_dir / "config.json")
            state_before, state_raw = _read_json(stock_dir / "state.json")
            if config_before is None or state_before is None:
                return {
                    "ok": False,
                    "reason_code": RESULT_PARTIAL_MIGRATION,
                    "migration_id": self.migration_id,
                    "results": results,
                }

            config_result = None
            if evaluation["config_phase"] == "BEFORE":
                config_result = self.repository.patch_stock_config(
                    spec.stock_code,
                    {field: STOCK_CONFIG_DELETE_FIELD for field in CONFIG_LEGACY_FIELDS},
                    name=spec.stock_name,
                    expected_fields={
                        "real_trade_enabled": False,
                        "real_trade_policy_updated_at": config_before.get(
                            "real_trade_policy_updated_at"
                        ),
                    },
                )
                if not config_result.ok:
                    return {
                        "ok": False,
                        "reason_code": RESULT_PARTIAL_MIGRATION,
                        "migration_id": self.migration_id,
                        "failed_stock_code": spec.stock_code,
                        "failed_stage": "CONFIG",
                        "writer_reason": config_result.reason_code,
                        "results": results,
                    }

            state_expected = (
                {STATE_LEGACY_FIELD: False}
                if evaluation["state_phase"] == "BEFORE"
                else {STATE_LEGACY_FIELD: RUNTIME_STOCK_STATE_EXPECTED_MISSING}
            )
            state_result = delete_runtime_stock_state_fields(
                stock_dir,
                (STATE_LEGACY_FIELD,),
                expected_file_sha256=_sha256_bytes(state_raw),
                expected_fields=state_expected,
            )
            if not state_result.ok:
                return {
                    "ok": False,
                    "reason_code": RESULT_PARTIAL_MIGRATION,
                    "migration_id": self.migration_id,
                    "failed_stock_code": spec.stock_code,
                    "failed_stage": "STATE",
                    "writer_reason": state_result.reason_code,
                    "results": results,
                }

            config_after, config_after_raw = _read_json(stock_dir / "config.json")
            state_after, state_after_raw = _read_json(stock_dir / "state.json")
            preservation_ok = (
                isinstance(config_after, dict)
                and isinstance(state_after, dict)
                and _canonical_sha256(config_after) == spec.config_canonical_after
                and _canonical_sha256(state_after) == spec.state_canonical_after
            )
            if not preservation_ok:
                return {
                    "ok": False,
                    "reason_code": RESULT_PARTIAL_MIGRATION,
                    "migration_id": self.migration_id,
                    "failed_stock_code": spec.stock_code,
                    "failed_stage": "READBACK",
                    "results": results,
                }
            results.append(
                {
                    "stock_code": spec.stock_code,
                    "config_changed": bool(config_result and config_result.changed),
                    "state_changed": state_result.changed,
                    "config_before_sha256": _sha256_bytes(config_raw),
                    "config_after_sha256": _sha256_bytes(config_after_raw),
                    "state_before_sha256": _sha256_bytes(state_raw),
                    "state_after_sha256": _sha256_bytes(state_after_raw),
                    "deleted_config_fields": CONFIG_LEGACY_FIELDS,
                    "deleted_state_fields": state_result.deleted_fields,
                    "preservation_ok": preservation_ok,
                }
            )

        changed = any(
            item["config_changed"] or item["state_changed"] for item in results
        )
        return {
            "ok": True,
            "reason_code": RESULT_MIGRATED if changed else RESULT_ALREADY_MIGRATED,
            "migration_id": self.migration_id,
            "executed_at": executed_at,
            "results": results,
        }


class RealTradeRetirementCleanup:
    """R6 active-data cleanup using only existing canonical delete writers."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        expectation: R6CleanupInventoryExpectation = R6CleanupInventoryExpectation(),
        application_active_check: Callable[[], bool] = _default_application_active,
    ) -> None:
        self.project_root = Path(project_root)
        self.expectation = expectation
        self.application_active_check = application_active_check
        self.repository = StockRepository(self.project_root)

    def _application_guard(self) -> str:
        try:
            return RESULT_APPLICATION_ACTIVE if self.application_active_check() else ""
        except Exception:
            return RESULT_APPLICATION_CHECK_FAILED

    def _stock_dirs(self) -> tuple[Path, ...]:
        stocks_dir = self.project_root / "stocks"
        if not stocks_dir.exists():
            return ()
        return tuple(
            sorted(
                path
                for path in stocks_dir.iterdir()
                if path.is_dir()
                and (
                    (path / "config.json").exists()
                    or (path / "state.json").exists()
                )
            )
        )

    def _runtime_stock_codes(self, filename: str, key: str) -> tuple[str, ...] | None:
        records = _records(self.project_root / "runtime" / filename, key)
        if records is None:
            return None
        return tuple(filter(None, (_record_stock_code(item) for item in records)))

    def _mock_current_stock_codes(self) -> tuple[str, ...]:
        data, _raw = _read_json(
            self.project_root / "mock_validation" / "runtime" / "current_sessions.json"
        )
        if data is None:
            return ()
        current = data.get("current_by_stock", {})
        if not isinstance(current, dict):
            return ()
        return tuple(str(code or "").strip().upper() for code in current)

    def _inventory_allowed(self, inventory: dict[str, int]) -> bool:
        expected = self.expectation
        if inventory["stock_count"] != expected.stock_count:
            return False
        if inventory["config_malformed"] > expected.config_malformed:
            return False
        if inventory["state_malformed"] > expected.state_malformed:
            return False
        if inventory["config_true"] > expected.config_true:
            return False
        if inventory["config_false"] > expected.config_false:
            return False
        if inventory["config_timestamp_present"] > expected.config_timestamp_present:
            return False
        if inventory["state_true"] > expected.state_true:
            return False
        if inventory["state_false"] > expected.state_false:
            return False
        return True

    def preview(self) -> dict[str, object]:
        guard_reason = self._application_guard()
        if guard_reason:
            return {"ok": False, "reason_code": guard_reason}

        stock_dirs = self._stock_dirs()
        codes = tuple(_stock_code_from_dir(path) for path in stock_dirs)
        if not codes or any(not code for code in codes) or len(set(codes)) != len(codes):
            return {"ok": False, "reason_code": RESULT_PREVIEW_STALE}

        runtime_sources: dict[str, tuple[str, ...] | None] = {
            "queue": self._runtime_stock_codes("order_queue.json", "orders"),
            "execution": self._runtime_stock_codes("order_executions.json", "executions"),
            "lock": self._runtime_stock_codes("order_locks.json", "locks"),
            "position": self._runtime_stock_codes("positions.json", "positions"),
            "broker_holding": self._runtime_stock_codes(
                "broker_holdings.json", "holdings"
            ),
        }
        if any(value is None for value in runtime_sources.values()):
            return {
                "ok": False,
                "reason_code": RESULT_PREVIEW_STALE,
                "evidence": ("RUNTIME_SOURCE_INVALID",),
            }
        runtime_sets = {
            key: set(value or ()) for key, value in runtime_sources.items()
        }
        mock_current = set(self._mock_current_stock_codes())

        inventory = {
            "stock_count": len(stock_dirs),
            "config_true": 0,
            "config_false": 0,
            "config_missing": 0,
            "config_malformed": 0,
            "config_timestamp_present": 0,
            "state_true": 0,
            "state_false": 0,
            "state_missing": 0,
            "state_malformed": 0,
        }
        targets: list[dict[str, object]] = []
        all_blockers: list[str] = []
        for stock_dir, code in zip(stock_dirs, codes):
            config, config_raw = _read_json(stock_dir / "config.json")
            state, state_raw = _read_json(stock_dir / "state.json")
            if config is None or state is None:
                return {
                    "ok": False,
                    "reason_code": RESULT_PREVIEW_STALE,
                    "stock_code": code,
                    "evidence": ("CONFIG_OR_STATE_INVALID",),
                }

            config_value = config.get("real_trade_enabled", STOCK_CONFIG_DELETE_FIELD)
            if config_value is STOCK_CONFIG_DELETE_FIELD:
                inventory["config_missing"] += 1
            elif isinstance(config_value, bool):
                inventory["config_true" if config_value else "config_false"] += 1
            else:
                inventory["config_malformed"] += 1
            if "real_trade_policy_updated_at" in config:
                inventory["config_timestamp_present"] += 1

            state_value = state.get(STATE_LEGACY_FIELD, STOCK_CONFIG_DELETE_FIELD)
            if state_value is STOCK_CONFIG_DELETE_FIELD:
                inventory["state_missing"] += 1
            elif isinstance(state_value, bool):
                inventory["state_true" if state_value else "state_false"] += 1
            else:
                inventory["state_malformed"] += 1

            assignment = str(config.get("assigned_routine_instance_id") or "")
            instance_enabled: bool | None = None
            if assignment:
                instance, _instance_raw = _read_json(
                    self.project_root / "routine_instances" / assignment / "instance.json"
                )
                if instance is None or not isinstance(instance.get("enabled"), bool):
                    return {
                        "ok": False,
                        "reason_code": RESULT_PREVIEW_STALE,
                        "stock_code": code,
                        "evidence": ("ROUTINE_INSTANCE_INVALID",),
                    }
                instance_enabled = bool(instance.get("enabled"))

            blockers: list[str] = []
            if int(state.get("holding_qty", 0) or 0) != 0:
                blockers.append("HOLDING_EXISTS")
            if state.get("review_required") is True:
                blockers.append("REVIEW_REQUIRED")
            for source, code_set in runtime_sets.items():
                if code in code_set:
                    blockers.append(f"{source.upper()}_ACTIVE")
            if code in mock_current:
                blockers.append("MOCK_SESSION_ACTIVE")
            all_blockers.extend(f"{code}:{reason}" for reason in blockers)

            preserved_config = {
                key: value
                for key, value in config.items()
                if key not in CONFIG_LEGACY_FIELDS
            }
            preserved_state = {
                key: value for key, value in state.items() if key != STATE_LEGACY_FIELD
            }
            targets.append(
                {
                    "stock_code": code,
                    "stock_name": stock_dir.name.split("_", 1)[1]
                    if "_" in stock_dir.name
                    else "",
                    "stock_dir": str(stock_dir),
                    "config_sha256": _sha256_bytes(config_raw),
                    "state_sha256": _sha256_bytes(state_raw),
                    "config_legacy_fields": tuple(
                        field for field in CONFIG_LEGACY_FIELDS if field in config
                    ),
                    "state_legacy_fields": (
                        (STATE_LEGACY_FIELD,) if STATE_LEGACY_FIELD in state else ()
                    ),
                    "assigned_routine_instance_id": assignment,
                    "instance_enabled": instance_enabled,
                    "operation_excluded": bool(config.get("operation_excluded", False)),
                    "status": str(state.get("status") or ""),
                    "holding_qty": int(state.get("holding_qty", 0) or 0),
                    "blockers": tuple(blockers),
                    "preserved_config_sha256": _canonical_sha256(preserved_config),
                    "preserved_state_sha256": _canonical_sha256(preserved_state),
                }
            )

        if not self._inventory_allowed(inventory):
            return {
                "ok": False,
                "reason_code": RESULT_PREVIEW_STALE,
                "inventory": inventory,
            }
        if all_blockers:
            return {
                "ok": False,
                "reason_code": RESULT_BLOCKED_OBLIGATION,
                "inventory": inventory,
                "blockers": tuple(all_blockers),
                "targets": tuple(targets),
            }

        identity = {
            "migration_name": R6_CLEANUP_NAME,
            "inventory": inventory,
            "targets": tuple(
                {
                    "stock_code": item["stock_code"],
                    "config_sha256": item["config_sha256"],
                    "state_sha256": item["state_sha256"],
                    "assignment": item["assigned_routine_instance_id"],
                    "instance_enabled": item["instance_enabled"],
                    "operation_excluded": item["operation_excluded"],
                    "status": item["status"],
                }
                for item in targets
            ),
        }
        preview_id = "RTRC-" + _canonical_sha256(identity)[:24]
        return {
            "ok": True,
            "reason_code": RESULT_CLEANUP_READY,
            "preview_id": preview_id,
            "inventory": inventory,
            "target_config_count": sum(
                bool(item["config_legacy_fields"]) for item in targets
            ),
            "target_state_count": sum(
                bool(item["state_legacy_fields"]) for item in targets
            ),
            "targets": tuple(targets),
        }

    def apply(self, *, expected_preview_id: str) -> dict[str, object]:
        preview = self.preview()
        if preview.get("ok") is not True:
            return preview
        if str(expected_preview_id or "").strip() != preview.get("preview_id"):
            return {
                "ok": False,
                "reason_code": RESULT_PREVIEW_STALE,
                "preview_id": preview.get("preview_id"),
            }

        executed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        results: list[dict[str, object]] = []
        for target in preview["targets"]:
            stock_dir = Path(str(target["stock_dir"]))
            code = str(target["stock_code"])
            name = str(target["stock_name"])
            config_before, config_raw = _read_json(stock_dir / "config.json")
            state_before, state_raw = _read_json(stock_dir / "state.json")
            if config_before is None or state_before is None:
                return {
                    "ok": False,
                    "reason_code": RESULT_PARTIAL_MIGRATION,
                    "failed_stock_code": code,
                    "failed_stage": "READ_BEFORE",
                    "results": tuple(results),
                }
            if (
                _sha256_bytes(config_raw) != target["config_sha256"]
                or _sha256_bytes(state_raw) != target["state_sha256"]
            ):
                return {
                    "ok": False,
                    "reason_code": RESULT_PREVIEW_STALE,
                    "failed_stock_code": code,
                    "results": tuple(results),
                }

            config_fields = tuple(
                field for field in CONFIG_LEGACY_FIELDS if field in config_before
            )
            config_changed = False
            if config_fields:
                config_result = self.repository.patch_stock_config(
                    code,
                    {field: STOCK_CONFIG_DELETE_FIELD for field in config_fields},
                    name=name,
                    expected_fields={field: config_before[field] for field in config_fields},
                )
                if not config_result.ok:
                    return {
                        "ok": False,
                        "reason_code": RESULT_PARTIAL_MIGRATION,
                        "failed_stock_code": code,
                        "failed_stage": "CONFIG",
                        "writer_reason": config_result.reason_code,
                        "results": tuple(results),
                    }
                config_changed = config_result.changed

            state_result = delete_runtime_stock_state_fields(
                stock_dir,
                (STATE_LEGACY_FIELD,),
                expected_file_sha256=_sha256_bytes(state_raw),
                expected_fields={
                    STATE_LEGACY_FIELD: state_before[STATE_LEGACY_FIELD]
                    if STATE_LEGACY_FIELD in state_before
                    else RUNTIME_STOCK_STATE_EXPECTED_MISSING
                },
            )
            if not state_result.ok:
                return {
                    "ok": False,
                    "reason_code": RESULT_PARTIAL_MIGRATION,
                    "failed_stock_code": code,
                    "failed_stage": "STATE",
                    "writer_reason": state_result.reason_code,
                    "results": tuple(results),
                }

            config_after, config_after_raw = _read_json(stock_dir / "config.json")
            state_after, state_after_raw = _read_json(stock_dir / "state.json")
            preserved_config = {
                key: value
                for key, value in (config_after or {}).items()
                if key not in CONFIG_LEGACY_FIELDS
            }
            preserved_state = {
                key: value
                for key, value in (state_after or {}).items()
                if key != STATE_LEGACY_FIELD
            }
            preservation_ok = (
                config_after is not None
                and state_after is not None
                and not any(field in config_after for field in CONFIG_LEGACY_FIELDS)
                and STATE_LEGACY_FIELD not in state_after
                and _canonical_sha256(preserved_config)
                == target["preserved_config_sha256"]
                and _canonical_sha256(preserved_state)
                == target["preserved_state_sha256"]
            )
            if not preservation_ok:
                return {
                    "ok": False,
                    "reason_code": RESULT_PARTIAL_MIGRATION,
                    "failed_stock_code": code,
                    "failed_stage": "READBACK",
                    "results": tuple(results),
                }
            results.append(
                {
                    "stock_code": code,
                    "config_changed": config_changed,
                    "state_changed": state_result.changed,
                    "config_before_sha256": _sha256_bytes(config_raw),
                    "config_after_sha256": _sha256_bytes(config_after_raw),
                    "state_before_sha256": _sha256_bytes(state_raw),
                    "state_after_sha256": _sha256_bytes(state_after_raw),
                    "deleted_config_fields": config_fields,
                    "deleted_state_fields": state_result.deleted_fields,
                    "preservation_ok": True,
                }
            )

        changed = any(
            item["config_changed"] or item["state_changed"] for item in results
        )
        return {
            "ok": True,
            "reason_code": (
                RESULT_CLEANUP_COMPLETE if changed else RESULT_CLEANUP_ALREADY_COMPLETE
            ),
            "preview_id": preview["preview_id"],
            "executed_at": executed_at,
            "results": tuple(results),
        }


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    migration = RealTradeRetirementMigration(arguments.project_root)
    result = migration.migrate() if arguments.apply else migration.dry_run()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(_main())
