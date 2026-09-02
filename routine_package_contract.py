"""Minimal metadata-driven contract adapter for routine packages.

The common platform resolves code locations and validates generic gate results.
It never interprets a routine's rule schema or strategy provenance.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from routine_instance_registry import (
    RoutineDefinitionRecord,
    routine_definition_by_id,
    routine_instance_by_id,
)


PROJECT_ROOT = Path(__file__).resolve().parent
EVALUATION_ROLE = "evaluation"
SETTINGS_ROLE = "settings"
RULE_MAPPER_ROLE = "rule_mapper"
RULE_COMMIT_VALIDATOR_ROLE = "rule_commit_validator"
EXECUTION_ADMISSION_ROLE = "execution_admission"
FINAL_SAFETY_ROLE = "final_safety"


class RoutineContractError(RuntimeError):
    pass


def stable_rules_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _definition_locators(definition: RoutineDefinitionRecord) -> dict[str, Any]:
    value = getattr(definition, "locators", None)
    return value if isinstance(value, dict) else {}


def routine_locator(
    definition: RoutineDefinitionRecord,
    role: str,
) -> dict[str, Any]:
    locator = _definition_locators(definition).get(str(role or "").strip())
    if not isinstance(locator, dict):
        raise RoutineContractError(
            f"routine locator is unavailable: {definition.definition_id}:{role}"
        )
    result = deepcopy(locator)
    if not str(
        result.get("file")
        or result.get("project_file")
        or result.get("module")
        or ""
    ).strip():
        raise RoutineContractError(
            f"routine locator source is unavailable: {definition.definition_id}:{role}"
        )
    return result


def _package_file(definition: RoutineDefinitionRecord, value: Any) -> Path:
    relative = Path(str(value or "").strip())
    if not relative.name or relative.is_absolute():
        raise RoutineContractError("routine locator file must be package-relative")
    package_root = definition.package_dir.resolve()
    path = (package_root / relative).resolve()
    try:
        path.relative_to(package_root)
    except ValueError as exc:
        raise RoutineContractError("routine locator escapes its package") from exc
    if not path.is_file():
        raise RoutineContractError(f"routine locator file does not exist: {path}")
    return path


def _project_root_for_definition(definition: RoutineDefinitionRecord) -> Path:
    package_root = definition.package_dir.resolve()
    if package_root.parent.name == "routines":
        return package_root.parent.parent
    raise RoutineContractError("routine package is outside the routines root")


def _project_file(definition: RoutineDefinitionRecord, value: Any) -> Path:
    relative = Path(str(value or "").strip())
    if not relative.name or relative.is_absolute():
        raise RoutineContractError("routine locator project_file must be project-relative")
    root = _project_root_for_definition(definition)
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RoutineContractError("routine locator escapes its project") from exc
    if not path.is_file():
        raise RoutineContractError(f"routine locator project_file does not exist: {path}")
    return path


def load_routine_module(
    definition: RoutineDefinitionRecord,
    role: str,
):
    locator = routine_locator(definition, role)
    module_name = str(locator.get("module") or "").strip()
    if module_name:
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            raise RoutineContractError(
                f"routine module import failed: {definition.definition_id}:{role}"
            ) from exc

    if str(locator.get("project_file") or "").strip():
        path = _project_file(definition, locator.get("project_file"))
        import_root = _project_root_for_definition(definition)
    else:
        path = _package_file(definition, locator.get("file"))
        import_root = definition.package_dir.resolve()
    unique_name = (
        f"routine_contract_{definition.definition_id}_{role}_"
        f"{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]}"
    )
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        raise RoutineContractError(f"routine module spec failed: {path}")
    module = importlib.util.module_from_spec(spec)
    package_text = str(import_root)
    inserted = package_text not in sys.path
    if inserted:
        sys.path.insert(0, package_text)
    try:
        # Execute the declared source directly.  Capability validation is a
        # read boundary and must not leave __pycache__ artifacts behind in a
        # freshly installed package when registration later rolls back.
        code = compile(path.read_bytes(), str(path), "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise RoutineContractError(
            f"routine module load failed: {definition.definition_id}:{role}"
        ) from exc
    finally:
        if inserted:
            try:
                sys.path.remove(package_text)
            except ValueError:
                pass
    return module


def load_routine_callable(
    definition: RoutineDefinitionRecord,
    role: str,
    *,
    callable_key: str = "callable",
):
    locator = routine_locator(definition, role)
    name = str(locator.get(callable_key) or "").strip()
    if not name:
        raise RoutineContractError(
            f"routine callable is unavailable: {definition.definition_id}:{role}:{callable_key}"
        )
    target = getattr(load_routine_module(definition, role), name, None)
    if not callable(target):
        raise RoutineContractError(
            f"routine callable is invalid: {definition.definition_id}:{role}:{name}"
        )
    return target


def definition_for_instance(
    instance_id: str,
    *,
    project_root: Path | str = PROJECT_ROOT,
) -> tuple[Any, RoutineDefinitionRecord]:
    instance = routine_instance_by_id(instance_id, project_root=project_root)
    if instance is None:
        raise RoutineContractError("routine instance is unavailable")
    definition = routine_definition_by_id(
        instance.definition_id,
        project_root=project_root,
    )
    if definition is None:
        raise RoutineContractError("routine definition is unavailable")
    return instance, definition


def _read_effective_rules(instance: Any) -> tuple[dict[str, Any], str]:
    rules_path = getattr(instance, "rules_path", None)
    if rules_path is None:
        raise RoutineContractError("routine effective rules are unavailable")
    try:
        rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RoutineContractError("routine effective rules are unavailable") from exc
    if not isinstance(rules, dict):
        raise RoutineContractError("routine effective rules must be an object")
    return rules, stable_rules_hash(rules)


def evaluate_routine_gate(
    *,
    instance_id: str,
    role: str,
    subject: dict[str, Any],
    project_root: Path | str = PROJECT_ROOT,
) -> dict[str, Any]:
    """Invoke a routine-owned gate and validate its generic identity evidence."""
    try:
        instance, definition = definition_for_instance(
            instance_id,
            project_root=project_root,
        )
        rules, rules_identity = _read_effective_rules(instance)
        routine_identity = {
            "definition_id": str(definition.definition_id),
            "routine_instance_id": str(instance.instance_id),
        }
        callback = load_routine_callable(definition, role)
        raw = callback(
            subject=deepcopy(subject),
            rules=deepcopy(rules),
            routine_identity=deepcopy(routine_identity),
            rules_identity=rules_identity,
        )
        if not isinstance(raw, dict) or not isinstance(raw.get("allowed"), bool):
            raise RoutineContractError("routine gate result is invalid")
        if raw.get("routine_identity") != routine_identity:
            raise RoutineContractError("routine gate identity mismatch")
        if raw.get("rules_identity") != rules_identity:
            raise RoutineContractError("routine gate rules identity mismatch")
        reasons = raw.get("reasons")
        if not isinstance(reasons, list):
            reason = str(raw.get("reason") or "").strip()
            reasons = [reason] if reason else []
        return {
            "allowed": raw["allowed"],
            "reason": str(raw.get("reason") or (reasons[0] if reasons else "")),
            "reasons": [str(item) for item in reasons if str(item).strip()],
            "routine_identity": deepcopy(routine_identity),
            "rules_identity": rules_identity,
        }
    except Exception as exc:
        return {
            "allowed": False,
            "reason": "ROUTINE_GATE_UNAVAILABLE",
            "reasons": [f"ROUTINE_GATE_UNAVAILABLE:{type(exc).__name__}"],
            "routine_identity": {
                "routine_instance_id": str(instance_id or "").strip(),
            },
            "rules_identity": None,
        }


def routine_trace_contract(
    definition: RoutineDefinitionRecord,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    metadata = getattr(definition, "decision_trace", None)
    data = metadata if isinstance(metadata, dict) else {}
    files: list[Path] = []
    for item in data.get("engine_files", []):
        try:
            files.append(_package_file(definition, item))
        except RoutineContractError:
            continue
    excluded = tuple(
        str(item).strip().lower()
        for item in data.get("rules_excluded_keys", [])
        if str(item).strip()
    )
    return tuple(files), excluded


def required_locator_files(
    definition: RoutineDefinitionRecord,
) -> tuple[str, ...]:
    root = _project_root_for_definition(definition)
    required: list[str] = []
    for locator in _definition_locators(definition).values():
        if not isinstance(locator, dict):
            continue
        if str(locator.get("file") or "").strip():
            path = _package_file(definition, locator.get("file"))
        elif str(locator.get("project_file") or "").strip():
            path = _project_file(definition, locator.get("project_file"))
        else:
            continue
        required.append(path.relative_to(root).as_posix())
    return tuple(dict.fromkeys(required))


def validate_routine_definition_capabilities(
    definition: RoutineDefinitionRecord,
    *,
    load_targets: bool = True,
) -> dict[str, Any]:
    required_roles = (
        EVALUATION_ROLE,
        SETTINGS_ROLE,
        RULE_MAPPER_ROLE,
        EXECUTION_ADMISSION_ROLE,
        FINAL_SAFETY_ROLE,
    )
    errors: list[str] = []
    resolved: dict[str, bool] = {}
    for role in required_roles:
        try:
            locator = routine_locator(definition, role)
            if load_targets:
                if role == RULE_MAPPER_ROLE and not str(locator.get("callable") or "").strip():
                    load_routine_module(definition, role)
                else:
                    load_routine_callable(definition, role)
            resolved[role] = True
        except Exception as exc:
            resolved[role] = False
            errors.append(f"{role}:{type(exc).__name__}")
    return {
        "ok": not errors,
        "definition_id": definition.definition_id,
        "resolved": resolved,
        "errors": errors,
        "required_files": list(required_locator_files(definition)),
    }
