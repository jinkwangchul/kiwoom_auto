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
LIFECYCLE_ROLE = "lifecycle"
STARTUP_RECOVERY_ROLE = "startup_recovery"


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


def evaluate_routine_lifecycle(
    *,
    instance_id: str,
    main_facts: dict[str, Any],
    project_root: Path | str = PROJECT_ROOT,
) -> dict[str, Any]:
    """Invoke a routine-owned lifecycle coordinator and validate decisions."""
    from routine_lifecycle_decision import validate_routine_lifecycle_decision

    try:
        instance, definition = definition_for_instance(instance_id, project_root=project_root)
        rules, rules_identity = _read_effective_rules(instance)
        routine_identity = {
            "definition_id": str(definition.definition_id),
            "routine_instance_id": str(instance.instance_id),
        }
        callback = load_routine_callable(definition, LIFECYCLE_ROLE)
        raw = callback(
            main_facts=deepcopy(main_facts),
            rules=deepcopy(rules),
            routine_identity=deepcopy(routine_identity),
            rules_identity=rules_identity,
        )
        decisions = raw.get("decisions") if isinstance(raw, dict) else None
        if not isinstance(decisions, list):
            raise RoutineContractError("routine lifecycle result is invalid")
        for decision in decisions:
            valid, reason = validate_routine_lifecycle_decision(decision, main_facts=main_facts)
            if not valid:
                raise RoutineContractError(reason)
            if decision.get("routine_identity") != routine_identity:
                raise RoutineContractError("routine lifecycle identity mismatch")
        return {
            "ok": True,
            "decisions": deepcopy(decisions),
            "routine_identity": routine_identity,
            "rules_identity": rules_identity,
            "facts_revision": main_facts.get("revision"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "decisions": [],
            "reason": "ROUTINE_LIFECYCLE_UNAVAILABLE",
            "errors": [f"ROUTINE_LIFECYCLE_UNAVAILABLE:{type(exc).__name__}"],
            "routine_identity": {"routine_instance_id": str(instance_id or "").strip()},
            "rules_identity": None,
            "facts_revision": main_facts.get("revision") if isinstance(main_facts, dict) else None,
        }


def classify_routine_startup_recovery(
    *,
    instance_id: str,
    signal_id: str,
    main_facts: dict[str, Any],
    project_root: Path | str = PROJECT_ROOT,
) -> dict[str, Any]:
    """Ask the owning routine to classify one startup-pending signal."""
    from routine_main_facts import validate_routine_main_facts

    try:
        valid, reason = validate_routine_main_facts(main_facts)
        if not valid:
            raise RoutineContractError(reason)
        instance, definition = definition_for_instance(instance_id, project_root=project_root)
        rules, rules_identity = _read_effective_rules(instance)
        routine_identity = {
            "definition_id": str(definition.definition_id),
            "routine_instance_id": str(instance.instance_id),
        }
        callback = load_routine_callable(definition, STARTUP_RECOVERY_ROLE)
        raw = callback(
            signal_id=str(signal_id or "").strip(),
            main_facts=deepcopy(main_facts),
            rules=deepcopy(rules),
            routine_identity=deepcopy(routine_identity),
            rules_identity=rules_identity,
        )
        if not isinstance(raw, dict) or raw.get("classification") not in {
            "RECOVERABLE", "PENDING_VALID", "REVIEW_REQUIRED"
        }:
            raise RoutineContractError("routine startup recovery classification is invalid")
        if raw.get("routine_identity") != routine_identity:
            raise RoutineContractError("routine startup recovery identity mismatch")
        if raw.get("rules_identity") != rules_identity:
            raise RoutineContractError("routine startup recovery rules identity mismatch")
        if str(raw.get("signal_id") or "").strip() != str(signal_id or "").strip():
            raise RoutineContractError("routine startup recovery signal identity mismatch")
        if (
            raw.get("facts_revision") != main_facts.get("revision")
            or raw.get("facts_snapshot_hash") != main_facts.get("snapshot_hash")
        ):
            raise RoutineContractError("routine startup recovery facts identity mismatch")
        return deepcopy(raw)
    except Exception as exc:
        stock_code = ""
        if isinstance(main_facts, dict):
            signals = main_facts.get("signals")
            if isinstance(signals, list):
                match = next(
                    (
                        item for item in signals
                        if isinstance(item, dict)
                        and str(item.get("id") or "").strip() == str(signal_id or "").strip()
                    ),
                    None,
                )
                if isinstance(match, dict):
                    stock_code = str(match.get("code") or "").strip().lstrip("A")
        return {
            "classification": "REVIEW_REQUIRED",
            "reason": f"ROUTINE_STARTUP_RECOVERY_UNAVAILABLE:{type(exc).__name__}",
            "signal_id": str(signal_id or "").strip(),
            "stock_code": stock_code,
            "routine_identity": {"routine_instance_id": str(instance_id or "").strip()},
            "rules_identity": None,
            "facts_revision": main_facts.get("revision") if isinstance(main_facts, dict) else None,
            "facts_snapshot_hash": main_facts.get("snapshot_hash") if isinstance(main_facts, dict) else None,
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
        RULE_COMMIT_VALIDATOR_ROLE,
        EXECUTION_ADMISSION_ROLE,
        FINAL_SAFETY_ROLE,
    )
    locators = _definition_locators(definition)
    if LIFECYCLE_ROLE in locators:
        required_roles = (*required_roles, LIFECYCLE_ROLE)
    if STARTUP_RECOVERY_ROLE in locators:
        required_roles = (*required_roles, STARTUP_RECOVERY_ROLE)
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
    # A locator may declare additional Production entry points in the same
    # source module (for example settings registration and routine-owned
    # signal projections).  Registration must resolve every declared callback;
    # checking only the primary ``callable`` would let a packed routine install
    # successfully and fail later in the Production caller.
    required_additional_callables = {
        EVALUATION_ROLE: (
            "market_bar_projection_callable",
            "cycle_projection_callable",
        ),
        SETTINGS_ROLE: ("registration_callable",),
    }
    checked_additional: set[tuple[str, str]] = set()
    for role, callable_keys in required_additional_callables.items():
        for callable_key in callable_keys:
            capability = f"{role}.{callable_key}"
            checked_additional.add((role, callable_key))
            try:
                if load_targets:
                    load_routine_callable(
                        definition,
                        role,
                        callable_key=callable_key,
                    )
                resolved[capability] = True
            except Exception as exc:
                resolved[capability] = False
                errors.append(f"{capability}:{type(exc).__name__}")
    for role, locator in locators.items():
        if not isinstance(locator, dict):
            continue
        for callable_key in tuple(
            key for key in locator
            if key != "callable" and str(key).endswith("_callable")
        ):
            if (role, callable_key) in checked_additional:
                continue
            capability = f"{role}.{callable_key}"
            try:
                if load_targets:
                    load_routine_callable(
                        definition,
                        role,
                        callable_key=callable_key,
                    )
                resolved[capability] = True
            except Exception as exc:
                resolved[capability] = False
                errors.append(f"{capability}:{type(exc).__name__}")
    return {
        "ok": not errors,
        "definition_id": definition.definition_id,
        "resolved": resolved,
        "errors": errors,
        "required_files": list(required_locator_files(definition)),
    }
