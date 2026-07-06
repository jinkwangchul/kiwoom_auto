# -*- coding: utf-8 -*-
"""File service for indicator-follow rule approval sessions.

This service stores only approval decisions and fingerprint metadata. It never
applies rules, writes rules.json, or connects to any engine/runtime pipeline.
"""

from __future__ import annotations

import importlib.util
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_DECISIONS = {
    "PENDING",
    "APPROVED",
    "REJECTED",
    "DEFERRED",
    "APPLIED_PREVIEW_ONLY",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": stage,
        "saved": False,
        "exists": False,
        "session": None,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _load_mapper_module():
    project_root = Path(__file__).resolve().parent
    mapper_path = next((project_root / "routines").glob("*/routine_rule_mapper.py"))
    spec = importlib.util.spec_from_file_location("routine_rule_mapper_for_session_file", mapper_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"failed to load mapper: {mapper_path}")
    spec.loader.exec_module(module)
    return module


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _validate_session_for_save(session: Any) -> tuple[dict[str, Any], list[str] | None]:
    if not isinstance(session, dict):
        return {}, ["session must be a dict"]
    fingerprint = session.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        return {}, ["session.fingerprint is required"]
    decisions = session.get("decisions")
    if not isinstance(decisions, dict):
        return {}, ["session.decisions must be a dict"]
    candidate_types = session.get("candidate_types")
    if not isinstance(candidate_types, dict):
        return {}, ["session.candidate_types must be a dict"]
    normalized_decisions: dict[str, str] = {}
    for path, decision in decisions.items():
        decision_text = str(decision)
        if decision_text not in ALLOWED_DECISIONS:
            return {}, [f"unknown approval decision for {path}: {decision_text}"]
        normalized_decisions[str(path)] = decision_text
    normalized_types = {str(path): str(candidate_type) for path, candidate_type in candidate_types.items()}
    if set(normalized_decisions.keys()) != set(normalized_types.keys()):
        return {}, ["session decisions and candidate_types paths must match"]
    payload = {
        "mode": "indicator_follow_rule_approval_session",
        "version": 1,
        "routine": str(session.get("routine") or "지표추종매매"),
        "routine_key": str(session.get("routine_key") or "indicator_follow"),
        "saved_at": _now_iso(),
        "fingerprint": fingerprint,
        "decisions": normalized_decisions,
        "candidate_types": normalized_types,
        "warnings": list(session.get("warnings", [])) if isinstance(session.get("warnings"), list) else [],
    }
    return payload, None


def save_rule_approval_session(session: dict[str, Any], session_path: str | Path) -> dict[str, Any]:
    """Save approval-session decisions to the explicit session_path only."""
    if not session_path:
        return _blocked("session_path", "session_path is required")
    payload, errors = _validate_session_for_save(deepcopy(session))
    if errors:
        return _blocked("session_validation", errors[0])
    target_path = Path(session_path)
    try:
        _write_json_atomic(target_path, payload)
    except Exception as exc:
        return _blocked("write_session", f"failed to write approval session: {exc}")
    return {
        "ok": True,
        "saved": True,
        "stage": "approval_session_saved",
        "session_path": str(target_path),
        "session": deepcopy(payload),
        "blocked_reasons": [],
        "warnings": [],
    }


def load_rule_approval_session(session_path: str | Path) -> dict[str, Any]:
    """Load an approval-session file from the explicit session_path only."""
    if not session_path:
        return _blocked("session_path", "session_path is required")
    target_path = Path(session_path)
    if not target_path.exists():
        return {
            "ok": True,
            "exists": False,
            "stage": "session_not_found",
            "session_path": str(target_path),
            "session": None,
            "blocked_reasons": [],
            "warnings": [],
        }
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _blocked("read_session", f"failed to read approval session JSON: {exc}")
    if not isinstance(data, dict):
        return _blocked("session_structure", "approval session root must be a dict")
    return {
        "ok": True,
        "exists": True,
        "stage": "approval_session_loaded",
        "session_path": str(target_path),
        "session": data,
        "blocked_reasons": [],
        "warnings": [],
    }


def restore_saved_rule_approval_session(
    saved_session: dict[str, Any],
    current_rules: dict[str, Any],
    preview_result: dict[str, Any],
) -> dict[str, Any]:
    """Restore a saved session through the mapper's fingerprint validation."""
    if not isinstance(saved_session, dict):
        return {
            "ok": False,
            "restore_status": "BLOCKED",
            "stage": "saved_session",
            "session": None,
            "blocked_reasons": ["saved_session must be a dict"],
            "warnings": [],
        }
    try:
        mapper = _load_mapper_module()
        restored = mapper.restore_rule_approval_session_for_preview(
            deepcopy(saved_session),
            deepcopy(current_rules) if isinstance(current_rules, dict) else {},
            deepcopy(preview_result) if isinstance(preview_result, dict) else {},
        )
    except Exception as exc:
        return {
            "ok": False,
            "restore_status": "BLOCKED",
            "stage": "restore_session",
            "session": None,
            "blocked_reasons": [f"failed to restore approval session: {exc}"],
            "warnings": [],
        }
    return {
        "ok": True,
        "restore_status": restored.get("restore_status"),
        "stage": "approval_session_restored",
        "session": restored,
        "blocked_reasons": [],
        "warnings": list(restored.get("warnings", [])) if isinstance(restored.get("warnings"), list) else [],
    }
