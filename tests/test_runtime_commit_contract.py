# -*- coding: utf-8 -*-
"""Tests for canonical Runtime Commit contract normalization (M6-8)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import hashlib
import math
import unittest
from unittest import mock

from runtime_commit_contract import (
    COMPONENT_ATOMIC_WRITER,
    COMPONENT_AUDIT_MANIFEST_PREVIEW,
    COMPONENT_BACKUP_PLAN,
    COMPONENT_COMMIT_VERIFIER_PLAN,
    COMPONENT_COMMIT_VERIFIER_RESULT,
    COMPONENT_EXECUTION_GATE_PREVIEW,
    COMPONENT_EXECUTION_PLAN_PREVIEW,
    COMPONENT_ROLLBACK_PLAN,
    CONTRACT_VERSION,
    FORBIDDEN_SAFETY_FLAGS,
    build_runtime_commit_contract,
    build_runtime_commit_contract_hash,
    normalize_runtime_commit_component_result,
    validate_runtime_commit_contract,
)


def safety_flags(**overrides):
    flags = {flag: False for flag in FORBIDDEN_SAFETY_FLAGS}
    flags.update(overrides)
    return flags


def protected_hashes():
    paths = [
        Path("runtime/order_queue.json"),
        Path("runtime/order_executions.json"),
        Path("runtime/order_locks.json"),
        Path("routines/지표추종매매/rules.json"),
    ]
    result = {}
    for path in paths:
        if path.exists():
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            result[str(path)] = None
    return result


class RuntimeCommitContractTests(unittest.TestCase):
    def base_contract(self, **overrides):
        params = {
            "component": COMPONENT_BACKUP_PLAN,
            "commit_id": "commit-001",
            "status": "READY",
            "preview_only": True,
            "payload": {"target_paths": ["runtime/order_queue.json"]},
            "issues": [],
            "warnings": [],
            "safety_flags": safety_flags(),
            "metadata": {"timestamp": "ignored"},
        }
        params.update(overrides)
        return build_runtime_commit_contract(**params)

    def base_source(self, **overrides):
        source = {
            "commit_id": "commit-001",
            "backup_status": "READY",
            "preview_only": True,
            "backup_targets": [{"source": "runtime/order_queue.json"}],
            "issues": [],
            "warnings": [],
            "safety_flags": safety_flags(),
        }
        source.update(overrides)
        return source

    def test_canonical_contract_created(self):
        contract = self.base_contract()
        self.assertEqual(contract["contract_version"], CONTRACT_VERSION)
        self.assertEqual(contract["component"], COMPONENT_BACKUP_PLAN)
        self.assertEqual(contract["status"], "READY")

    def test_contract_version_fixed(self):
        self.assertEqual(self.base_contract()["contract_version"], "M6_RUNTIME_COMMIT_V1")

    def test_component_validation(self):
        contract = self.base_contract(component="NOPE")
        validation = validate_runtime_commit_contract(contract)
        self.assertFalse(validation["valid"])
        self.assertTrue(any("component" in issue for issue in validation["issues"]))

    def test_commit_id_normal(self):
        contract = self.base_contract(commit_id="abc")
        self.assertEqual(contract["commit_id"], "abc")
        self.assertTrue(validate_runtime_commit_contract(contract)["valid"])

    def test_empty_commit_id_invalid(self):
        contract = self.base_contract(commit_id="")
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_whitespace_commit_id_invalid(self):
        contract = self.base_contract(commit_id=" commit-001 ")
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_bool_commit_id_invalid(self):
        contract = self.base_contract(commit_id=True)
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_int_commit_id_invalid(self):
        contract = self.base_contract(commit_id=123)
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_expected_commit_mismatch_invalid(self):
        contract = self.base_contract()
        validation = validate_runtime_commit_contract(contract, expected_commit_id="other")
        self.assertFalse(validation["valid"])

    def test_source_not_mutated(self):
        source = self.base_source()
        snapshot = deepcopy(source)
        normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(source, snapshot)

    def test_issues_deterministic_dedupe(self):
        contract = self.base_contract(issues=["a", "b", "a"])
        self.assertEqual(contract["issues"], ["a", "b"])

    def test_warnings_deterministic_dedupe(self):
        contract = self.base_contract(warnings=["a", "b", "a"])
        self.assertEqual(contract["warnings"], ["a", "b"])

    def test_atomic_writer_ok_to_succeeded(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_ATOMIC_WRITER,
            {"commit_id": "commit-001", "status": "OK", "target_path": "runtime/order_queue.json"},
        )
        self.assertEqual(result["status"], "SUCCEEDED")

    def test_atomic_writer_error_to_failed(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_ATOMIC_WRITER,
            {"commit_id": "commit-001", "status": "ERROR", "target_path": "runtime/order_queue.json"},
        )
        self.assertEqual(result["status"], "FAILED")

    def test_atomic_writer_missing_source_commit_id_uses_expected_commit_id(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_ATOMIC_WRITER,
            {"status": "OK", "target_path": "runtime/order_queue.json"},
            expected_commit_id="commit-001",
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["commit_id"], "commit-001")

    def test_atomic_writer_without_any_commit_id_invalid(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_ATOMIC_WRITER,
            {"status": "OK", "target_path": "runtime/order_queue.json"},
        )
        self.assertEqual(result["status"], "INVALID")

    def test_atomic_writer_source_expected_commit_id_mismatch_invalid(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_ATOMIC_WRITER,
            {"commit_id": "commit-001", "status": "OK", "target_path": "runtime/order_queue.json"},
            expected_commit_id="commit-002",
        )
        self.assertEqual(result["status"], "INVALID")

    def test_backup_ready_to_ready(self):
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, self.base_source())
        self.assertEqual(result["status"], "READY")

    def test_backup_blocked_to_blocked(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_BACKUP_PLAN, self.base_source(backup_status="BLOCKED")
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_backup_invalid_to_invalid(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_BACKUP_PLAN, self.base_source(backup_status="INVALID")
        )
        self.assertEqual(result["status"], "INVALID")

    def test_rollback_status_mapping(self):
        source = self.base_source(backup_status="READY")
        source.pop("backup_status")
        source["rollback_status"] = "READY"
        self.assertEqual(
            normalize_runtime_commit_component_result(COMPONENT_ROLLBACK_PLAN, source)["status"],
            "READY",
        )

    def test_verifier_plan_status_mapping(self):
        source = self.base_source()
        source.pop("backup_status")
        source["verify_status"] = "READY"
        self.assertEqual(
            normalize_runtime_commit_component_result(COMPONENT_COMMIT_VERIFIER_PLAN, source)["status"],
            "READY",
        )

    def test_verifier_actual_status_mapping(self):
        source = self.base_source()
        source.pop("backup_status")
        source["verification_status"] = "READY"
        self.assertEqual(
            normalize_runtime_commit_component_result(COMPONENT_COMMIT_VERIFIER_RESULT, source)["status"],
            "SUCCEEDED",
        )

    def test_verify_alias_same_value_allowed(self):
        source = self.base_source()
        source.pop("backup_status")
        source["verify_status"] = "READY"
        source["verification_status"] = "READY"
        result = normalize_runtime_commit_component_result(COMPONENT_COMMIT_VERIFIER_RESULT, source)
        self.assertEqual(result["status"], "SUCCEEDED")

    def test_verify_alias_conflict_invalid(self):
        source = self.base_source()
        source.pop("backup_status")
        source["verify_status"] = "READY"
        source["verification_status"] = "INVALID"
        result = normalize_runtime_commit_component_result(COMPONENT_COMMIT_VERIFIER_RESULT, source)
        self.assertEqual(result["status"], "INVALID")
        self.assertTrue(any("conflict" in issue for issue in result["issues"]))

    def test_audit_preview_component_mapping(self):
        source = self.base_source()
        source.pop("backup_status")
        source["audit_status"] = "READY"
        result = normalize_runtime_commit_component_result(COMPONENT_AUDIT_MANIFEST_PREVIEW, source)
        self.assertEqual(result["component"], COMPONENT_AUDIT_MANIFEST_PREVIEW)

    def test_executor_maps_to_execution_plan_preview(self):
        source = self.base_source()
        source.pop("backup_status")
        source["executor_status"] = "READY"
        result = normalize_runtime_commit_component_result(COMPONENT_EXECUTION_PLAN_PREVIEW, source)
        self.assertEqual(result["component"], COMPONENT_EXECUTION_PLAN_PREVIEW)

    def test_gate_approved_to_ready(self):
        source = self.base_source()
        source.pop("backup_status")
        source["gate_status"] = "APPROVED"
        result = normalize_runtime_commit_component_result(COMPONENT_EXECUTION_GATE_PREVIEW, source)
        self.assertEqual(result["status"], "READY")

    def test_gate_original_status_preserved_in_payload(self):
        source = self.base_source()
        source.pop("backup_status")
        source["gate_status"] = "APPROVED"
        result = normalize_runtime_commit_component_result(COMPONENT_EXECUTION_GATE_PREVIEW, source)
        self.assertEqual(result["payload"]["gate_status"], "APPROVED")

    def test_required_component_missing_invalid(self):
        contract = self.base_contract(component="")
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_routines_rules_json_blocked(self):
        source = self.base_source(backup_targets=[{"source": "routines/지표추종매매/rules.json"}])
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_windows_separator_rules_json_blocked(self):
        source = self.base_source(backup_targets=[{"source": r"routines\foo\rules.json"}])
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_posix_separator_rules_json_blocked(self):
        source = self.base_source(backup_targets=[{"source": "routines/foo/rules.json"}])
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_rules_json_case_variant_blocked(self):
        source = self.base_source(backup_targets=[{"source": "ROUTINES/foo/RULES.JSON"}])
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_path_traversal_blocked(self):
        source = self.base_source(backup_targets=[{"source": "runtime/../routines/foo/rules.json"}])
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_preview_safety_flag_true_invalid(self):
        source = self.base_source(safety_flags=safety_flags(runtime_write=True))
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_preview_safety_flag_zero_invalid(self):
        source = self.base_source(safety_flags=safety_flags(runtime_write=0))
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_preview_safety_flag_none_invalid(self):
        source = self.base_source(safety_flags=safety_flags(runtime_write=None))
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_required_safety_flag_missing_invalid(self):
        flags = safety_flags()
        flags.pop("runtime_write")
        source = self.base_source(safety_flags=flags)
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_inherited_safety_flag_missing_defaults_false(self):
        flags = safety_flags()
        flags.pop("gui_update_called")
        source = self.base_source(safety_flags=flags)
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["safety_flags"]["gui_update_called"])

    def test_audit_write_alias_maps_to_audit_written(self):
        flags = safety_flags()
        flags.pop("audit_written")
        flags["audit_write"] = False
        source = self.base_source(safety_flags=flags)
        source.pop("backup_status")
        source["audit_status"] = "READY"
        result = normalize_runtime_commit_component_result(COMPONENT_AUDIT_MANIFEST_PREVIEW, source)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["safety_flags"]["audit_written"])

    def test_unknown_true_safety_flag_invalid(self):
        source = self.base_source(safety_flags={**safety_flags(), "new_write": True})
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "INVALID")

    def test_hash_deterministic(self):
        contract = self.base_contract()
        self.assertEqual(build_runtime_commit_contract_hash(contract), build_runtime_commit_contract_hash(contract))

    def test_key_order_same_hash(self):
        a = self.base_contract(payload={"a": 1, "b": 2})
        b = self.base_contract(payload={"b": 2, "a": 1})
        self.assertEqual(build_runtime_commit_contract_hash(a), build_runtime_commit_contract_hash(b))

    def test_payload_change_changes_hash(self):
        a = self.base_contract(payload={"a": 1})
        b = self.base_contract(payload={"a": 2})
        self.assertNotEqual(build_runtime_commit_contract_hash(a), build_runtime_commit_contract_hash(b))

    def test_metadata_timestamp_change_keeps_hash(self):
        a = self.base_contract(metadata={"timestamp": "1"})
        b = self.base_contract(metadata={"timestamp": "2"})
        self.assertEqual(build_runtime_commit_contract_hash(a), build_runtime_commit_contract_hash(b))

    def test_warnings_change_keeps_hash(self):
        a = self.base_contract(warnings=["a"])
        b = self.base_contract(warnings=["b"])
        self.assertEqual(build_runtime_commit_contract_hash(a), build_runtime_commit_contract_hash(b))

    def test_nan_invalid(self):
        contract = self.base_contract(payload={"bad": math.nan})
        with self.assertRaises(ValueError):
            build_runtime_commit_contract_hash(contract)

    def test_infinity_invalid(self):
        contract = self.base_contract(payload={"bad": math.inf})
        with self.assertRaises(ValueError):
            build_runtime_commit_contract_hash(contract)

    def test_path_object_invalid(self):
        contract = self.base_contract(payload={"path": Path("runtime/order_queue.json")})
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_datetime_object_invalid(self):
        contract = self.base_contract(payload={"time": datetime(2026, 1, 1)})
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_set_invalid(self):
        contract = self.base_contract(payload={"items": {1, 2}})
        self.assertFalse(validate_runtime_commit_contract(contract)["valid"])

    def test_contract_validation_normal(self):
        self.assertTrue(validate_runtime_commit_contract(self.base_contract())["valid"])

    def test_component_mismatch_invalid(self):
        validation = validate_runtime_commit_contract(
            self.base_contract(), expected_component=COMPONENT_ROLLBACK_PLAN
        )
        self.assertFalse(validation["valid"])

    def test_contract_hash_input_not_mutated(self):
        contract = self.base_contract()
        snapshot = deepcopy(contract)
        build_runtime_commit_contract_hash(contract)
        self.assertEqual(contract, snapshot)

    def test_other_m6_apis_not_called(self):
        source = self.base_source()
        with mock.patch("runtime_atomic_writer.write_json_atomic") as writer:
            normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        writer.assert_not_called()

    def test_no_actual_file_read_write(self):
        before = protected_hashes()
        normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, self.base_source())
        after = protected_hashes()
        self.assertEqual(after, before)

    def test_runtime_routines_unchanged(self):
        before = protected_hashes()
        validate_runtime_commit_contract(self.base_contract())
        after = protected_hashes()
        self.assertEqual(after, before)

    def test_validation_rejects_non_dict_contract(self):
        self.assertFalse(validate_runtime_commit_contract(["bad"])["valid"])

    def test_normalize_rejects_non_dict_source(self):
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, ["bad"])
        self.assertEqual(result["status"], "INVALID")

    def test_payload_is_selected_not_whole_source(self):
        source = self.base_source(secret="do-not-copy")
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertNotIn("secret", result["payload"])

    def test_expected_preview_only_mismatch_invalid(self):
        result = normalize_runtime_commit_component_result(
            COMPONENT_BACKUP_PLAN,
            self.base_source(preview_only=False),
            preview_only=True,
        )
        self.assertEqual(result["status"], "INVALID")

    def test_external_rules_json_not_blocked_by_name_only(self):
        source = self.base_source(backup_targets=[{"source": "docs/rules.json"}])
        result = normalize_runtime_commit_component_result(COMPONENT_BACKUP_PLAN, source)
        self.assertEqual(result["status"], "READY")


if __name__ == "__main__":
    unittest.main()
