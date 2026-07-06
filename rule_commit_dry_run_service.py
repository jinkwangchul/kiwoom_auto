"""Dry-run rule commit flow inside an isolated workspace.

This module reads actual rules/session inputs, copies them into a temp workspace,
and runs commit/report/rollback only against workspace files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import rule_apply_commit_service
import rule_approval_session_file_service
import rule_commit_report_service


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _load_json_dict(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"{label} file does not exist"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"failed to read {label} JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{label} JSON root must be a dict"
    return data, None


def _load_mapper_module():
    project_root = Path(__file__).resolve().parent
    mapper_path = next((project_root / "routines").glob("*/routine_rule_mapper.py"))
    spec = spec_from_file_location("routine_rule_mapper_for_dry_run", mapper_path)
    module = module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"failed to load mapper: {mapper_path}")
    spec.loader.exec_module(module)
    return module


def _blocked(
    blocked_stage: str,
    reason: str,
    *,
    source_rules_path: Any = None,
    source_session_path: Any = None,
    workspace: Any = None,
    pre_actual_file_sha256: str | None = None,
    post_actual_file_sha256: str | None = None,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "stage": "RULE_COMMIT_DRY_RUN_BLOCKED",
        "blocked_stage": blocked_stage,
        "dry_run": True,
        "source_rules_path": str(source_rules_path) if source_rules_path else None,
        "source_session_path": str(source_session_path) if source_session_path else None,
        "workspace": str(workspace) if workspace else None,
        "pre_actual_file_sha256": pre_actual_file_sha256,
        "post_actual_file_sha256": post_actual_file_sha256,
        "actual_rules_unchanged": (
            pre_actual_file_sha256 == post_actual_file_sha256
            if pre_actual_file_sha256 and post_actual_file_sha256
            else None
        ),
        "blocked_reasons": [reason],
        "warnings": warnings or [],
    }
    if extra:
        result.update(extra)
    return result


def _cleanup_workspace(workspace: Path) -> dict[str, Any]:
    try:
        shutil.rmtree(workspace)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "DRY_RUN_CLEANUP_FAILED",
            "workspace": str(workspace),
            "blocked_reasons": [f"failed to cleanup workspace: {exc}"],
            "warnings": [],
        }
    return {
        "ok": True,
        "stage": "DRY_RUN_CLEANUP",
        "workspace": str(workspace),
        "removed": True,
        "blocked_reasons": [],
        "warnings": [],
    }


def _finalize_blocked(
    result: dict[str, Any],
    workspace: Path | None,
    source_rules_path: Path | None,
    pre_actual_file_sha256: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    if source_rules_path and source_rules_path.exists():
        post_actual_file_sha256 = _file_sha256(source_rules_path)
        result["post_actual_file_sha256"] = post_actual_file_sha256
        result["actual_rules_unchanged"] = pre_actual_file_sha256 == post_actual_file_sha256
    if workspace is not None:
        result["workspace"] = str(workspace)
        if context.get("preserve_workspace_on_failure", True) is False:
            cleanup_result = _cleanup_workspace(workspace)
            result["cleanup_result"] = cleanup_result
            if cleanup_result.get("ok") is not True:
                result.setdefault("warnings", []).extend(cleanup_result.get("blocked_reasons", []))
    return result


def _copy_file_exact(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def run_rule_commit_dry_run(
    rules_path: str | Path,
    session_path: str | Path,
    workspace_dir: str | Path,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a full rule commit dry-run against copied workspace files only."""
    context_copy = deepcopy(context) if isinstance(context, dict) else {}
    dry_run_id = str(context_copy.get("dry_run_id") or uuid.uuid4())
    warnings: list[str] = []

    if not rules_path:
        return _blocked("rules_path", "rules_path is required")
    if not session_path:
        return _blocked("session_path", "session_path is required", source_rules_path=rules_path)
    if not workspace_dir:
        return _blocked(
            "workspace",
            "workspace_dir is required",
            source_rules_path=rules_path,
            source_session_path=session_path,
        )

    source_rules_path = Path(rules_path)
    source_session_path = Path(session_path)
    workspace = Path(workspace_dir)
    temp_rules_path = workspace / "rules.json"
    temp_session_path = workspace / "approval_session.json"
    report_dir = workspace / "reports" / "rule_commits"

    actual_rules, rules_error = _load_json_dict(source_rules_path, "rules")
    if rules_error:
        return _blocked(
            "read_rules",
            rules_error,
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
        )
    assert actual_rules is not None
    pre_actual_file_sha256 = _file_sha256(source_rules_path)
    actual_rules_hash = _stable_hash(actual_rules)

    if not source_session_path.exists():
        return _blocked(
            "copy_session",
            "session file does not exist",
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
            pre_actual_file_sha256=pre_actual_file_sha256,
            post_actual_file_sha256=_file_sha256(source_rules_path),
        )

    if workspace.exists() and context_copy.get("allow_existing_workspace") is not True:
        return _blocked(
            "workspace",
            "workspace already exists",
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
            pre_actual_file_sha256=pre_actual_file_sha256,
            post_actual_file_sha256=_file_sha256(source_rules_path),
        )
    if workspace.exists() and temp_rules_path.exists():
        return _blocked(
            "workspace",
            "workspace rules.json already exists",
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
            pre_actual_file_sha256=pre_actual_file_sha256,
            post_actual_file_sha256=_file_sha256(source_rules_path),
        )

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "reports" / "rule_commits").mkdir(parents=True, exist_ok=True)
        _copy_file_exact(source_rules_path, temp_rules_path)
        _copy_file_exact(source_session_path, temp_session_path)
    except Exception as exc:
        result = _blocked(
            "workspace",
            f"failed to prepare dry-run workspace: {exc}",
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
            pre_actual_file_sha256=pre_actual_file_sha256,
        )
        return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

    if _file_sha256(temp_rules_path) != pre_actual_file_sha256:
        result = _blocked(
            "copy_rules",
            "copied rules SHA256 mismatch",
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
            pre_actual_file_sha256=pre_actual_file_sha256,
        )
        return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

    try:
        mapper = _load_mapper_module()
        preview_result = context_copy.get("preview_result")
        if not isinstance(preview_result, dict):
            ui_state = context_copy.get("ui_state")
            if not isinstance(ui_state, dict):
                result = _blocked(
                    "preview",
                    "preview_result or ui_state is required",
                    source_rules_path=source_rules_path,
                    source_session_path=source_session_path,
                    workspace=workspace,
                    pre_actual_file_sha256=pre_actual_file_sha256,
                )
                return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)
            preview_result = mapper.build_engine_rules_preview_from_ui_state(deepcopy(ui_state), deepcopy(actual_rules))

        temp_rules, temp_load_error = _load_json_dict(temp_rules_path, "temp rules")
        if temp_load_error:
            result = _blocked(
                "read_temp_rules",
                temp_load_error,
                source_rules_path=source_rules_path,
                source_session_path=source_session_path,
                workspace=workspace,
                pre_actual_file_sha256=pre_actual_file_sha256,
            )
            return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)
        assert temp_rules is not None

        session_load = rule_approval_session_file_service.load_rule_approval_session(temp_session_path)
        if session_load.get("ok") is not True or session_load.get("exists") is not True:
            result = _blocked(
                "load_session",
                str((session_load.get("blocked_reasons") or ["session file missing"])[0]),
                source_rules_path=source_rules_path,
                source_session_path=source_session_path,
                workspace=workspace,
                pre_actual_file_sha256=pre_actual_file_sha256,
                extra={"session_load": session_load},
            )
            return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)
        saved_session = session_load.get("session")
        if not isinstance(saved_session, dict):
            result = _blocked(
                "load_session",
                "saved approval session is not a dict",
                source_rules_path=source_rules_path,
                source_session_path=source_session_path,
                workspace=workspace,
                pre_actual_file_sha256=pre_actual_file_sha256,
                extra={"session_load": session_load},
            )
            return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

        commit_preview = mapper.build_rule_commit_preview(
            temp_rules,
            preview_result,
            saved_session,
            {"approval_session_dirty": context_copy.get("approval_session_dirty", False) is True},
        )
        if commit_preview.get("commit_allowed") is not True:
            result = _blocked(
                "commit_preview",
                str((commit_preview.get("blocked_reasons") or ["commit preview is not allowed"])[0]),
                source_rules_path=source_rules_path,
                source_session_path=source_session_path,
                workspace=workspace,
                pre_actual_file_sha256=pre_actual_file_sha256,
                extra={"commit_preview": commit_preview},
            )
            return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

        commit_gate = mapper.evaluate_rule_commit_gate_from_saved_session(
            temp_rules,
            preview_result,
            temp_session_path,
            {
                "expected_rules_hash": _stable_hash(temp_rules),
                "approval_session_dirty": context_copy.get("approval_session_dirty", False) is True,
                "manual_rule_commit_confirmed": context_copy.get("manual_rule_commit_confirmed", True) is True,
            },
        )
        if commit_gate.get("commit_allowed") is not True:
            result = _blocked(
                "commit_gate",
                str((commit_gate.get("blocked_reasons") or ["commit gate is not allowed"])[0]),
                source_rules_path=source_rules_path,
                source_session_path=source_session_path,
                workspace=workspace,
                pre_actual_file_sha256=pre_actual_file_sha256,
                extra={"commit_preview": commit_preview, "commit_gate": commit_gate},
            )
            return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

        pipeline_preview = mapper.build_rule_pipeline_preview(temp_rules, preview_result, saved_session)
        apply_preview = pipeline_preview.get("apply_preview", {})
        commit_result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
            temp_rules_path,
            apply_preview,
            commit_gate,
            {
                "allowed_rules_path": str(temp_rules_path.resolve()),
                "expected_file_sha256": _file_sha256(temp_rules_path),
                "expected_rules_hash": _stable_hash(temp_rules),
            },
        )
        if commit_result.get("ok") is not True:
            result = _blocked(
                "commit_executor",
                str((commit_result.get("blocked_reasons") or ["commit executor is not allowed"])[0]),
                source_rules_path=source_rules_path,
                source_session_path=source_session_path,
                workspace=workspace,
                pre_actual_file_sha256=pre_actual_file_sha256,
                extra={
                    "commit_preview": commit_preview,
                    "commit_gate": commit_gate,
                    "commit_result": commit_result,
                },
            )
            return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

        report_result: dict[str, Any] = {"ok": True, "stage": "RULE_COMMIT_REPORT_SKIPPED", "warnings": []}
        if context_copy.get("write_report", True) is True:
            report_result = rule_commit_report_service.write_rule_commit_report(commit_result, report_dir)
            if report_result.get("ok") is not True:
                result = _blocked(
                    "commit_report",
                    str((report_result.get("blocked_reasons") or ["commit report failed"])[0]),
                    source_rules_path=source_rules_path,
                    source_session_path=source_session_path,
                    workspace=workspace,
                    pre_actual_file_sha256=pre_actual_file_sha256,
                    extra={
                        "commit_preview": commit_preview,
                        "commit_gate": commit_gate,
                        "commit_result": commit_result,
                        "report_result": report_result,
                    },
                )
                return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

        rollback_result: dict[str, Any] = {"ok": True, "stage": "RULE_ROLLBACK_SKIPPED", "warnings": []}
        rollback_verified = False
        if context_copy.get("run_rollback_verification", True) is True:
            rollback_result = rule_apply_commit_service.restore_rules_from_backup(
                temp_rules_path,
                commit_result.get("backup_path"),
                {
                    "allowed_rules_path": str(temp_rules_path.resolve()),
                    "expected_current_file_sha256": commit_result.get("post_file_sha256"),
                },
            )
            if rollback_result.get("ok") is not True:
                result = _blocked(
                    "rollback",
                    str((rollback_result.get("blocked_reasons") or ["rollback failed"])[0]),
                    source_rules_path=source_rules_path,
                    source_session_path=source_session_path,
                    workspace=workspace,
                    pre_actual_file_sha256=pre_actual_file_sha256,
                    extra={
                        "commit_preview": commit_preview,
                        "commit_gate": commit_gate,
                        "commit_result": commit_result,
                        "report_result": report_result,
                        "rollback_result": rollback_result,
                    },
                )
                return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)
            restored_rules, restored_error = _load_json_dict(temp_rules_path, "restored temp rules")
            if restored_error:
                result = _blocked(
                    "rollback_verify",
                    restored_error,
                    source_rules_path=source_rules_path,
                    source_session_path=source_session_path,
                    workspace=workspace,
                    pre_actual_file_sha256=pre_actual_file_sha256,
                    extra={
                        "commit_preview": commit_preview,
                        "commit_gate": commit_gate,
                        "commit_result": commit_result,
                        "report_result": report_result,
                        "rollback_result": rollback_result,
                    },
                )
                return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)
            assert restored_rules is not None
            rollback_verified = _stable_hash(restored_rules) == actual_rules_hash
            if not rollback_verified:
                result = _blocked(
                    "rollback_verify",
                    "rollback stable hash does not match original actual rules hash",
                    source_rules_path=source_rules_path,
                    source_session_path=source_session_path,
                    workspace=workspace,
                    pre_actual_file_sha256=pre_actual_file_sha256,
                    extra={
                        "commit_preview": commit_preview,
                        "commit_gate": commit_gate,
                        "commit_result": commit_result,
                        "report_result": report_result,
                        "rollback_result": rollback_result,
                    },
                )
                return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

    except Exception as exc:
        result = _blocked(
            "dry_run",
            f"failed to run rule commit dry-run: {exc}",
            source_rules_path=source_rules_path,
            source_session_path=source_session_path,
            workspace=workspace,
            pre_actual_file_sha256=pre_actual_file_sha256,
        )
        return _finalize_blocked(result, workspace, source_rules_path, pre_actual_file_sha256, context_copy)

    post_actual_file_sha256 = _file_sha256(source_rules_path)
    result = {
        "ok": True,
        "stage": "RULE_COMMIT_DRY_RUN",
        "dry_run": True,
        "dry_run_id": dry_run_id,
        "source_rules_path": str(source_rules_path),
        "source_session_path": str(source_session_path),
        "workspace": str(workspace),
        "temp_rules_path": str(temp_rules_path),
        "temp_session_path": str(temp_session_path),
        "pre_actual_file_sha256": pre_actual_file_sha256,
        "post_actual_file_sha256": post_actual_file_sha256,
        "actual_rules_unchanged": pre_actual_file_sha256 == post_actual_file_sha256,
        "commit_preview": commit_preview,
        "commit_gate": commit_gate,
        "commit_result": commit_result,
        "report_result": report_result,
        "rollback_result": rollback_result,
        "rollback_verified": rollback_verified,
        "cleanup_result": {"ok": True, "stage": "DRY_RUN_CLEANUP_SKIPPED", "removed": False},
        "blocked_reasons": [],
        "warnings": warnings,
    }

    if context_copy.get("preserve_workspace_on_success", False) is not True:
        cleanup_result = _cleanup_workspace(workspace)
        result["cleanup_result"] = cleanup_result
        if cleanup_result.get("ok") is not True:
            result["warnings"].extend(cleanup_result.get("blocked_reasons", []))

    return result
