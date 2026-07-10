# -*- coding: utf-8 -*-
"""Integration validation between real M6 public outputs and M6-8 contracts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

from runtime_atomic_writer import write_json_atomic
from runtime_backup_manager import create_runtime_backup_plan
from runtime_commit_audit_record import create_runtime_commit_audit_record
from runtime_commit_contract import (
    COMPONENT_ATOMIC_WRITER,
    COMPONENT_AUDIT_MANIFEST_PREVIEW,
    COMPONENT_BACKUP_PLAN,
    COMPONENT_COMMIT_VERIFIER_PLAN,
    COMPONENT_COMMIT_VERIFIER_RESULT,
    COMPONENT_EXECUTION_GATE_PREVIEW,
    COMPONENT_EXECUTION_PLAN_PREVIEW,
    COMPONENT_ROLLBACK_PLAN,
    build_runtime_commit_contract_hash,
    normalize_runtime_commit_component_result,
    validate_runtime_commit_contract,
)
from runtime_commit_execution_gate import build_execution_plan_hash, evaluate_runtime_commit_execution_gate
from runtime_commit_executor import create_runtime_commit_execution_plan
from runtime_commit_verifier import create_runtime_commit_verifier_plan, verify_runtime_commit
from runtime_rollback_manager import create_runtime_rollback_plan


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
RULES_PATH = PROJECT_ROOT / "routines" / "지표추종매매" / "rules.json"


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str | None]:
    hashes = {str(path): _sha256(path) for path in sorted(RUNTIME_DIR.glob("*.json"))}
    hashes[str(RULES_PATH)] = _sha256(RULES_PATH)
    return hashes


class RuntimeCommitContractIntegrationTests(unittest.TestCase):
    """Exercise actual M6 public APIs, then normalize their real outputs."""

    def setUp(self) -> None:
        self.commit_id = "commit-contract-integration-1"
        self.protected_hashes = _protected_hashes()

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, _protected_hashes())

    def _actual_outputs(self) -> dict[str, dict[str, object]]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name) / "order_queue.json"
        temp_path.write_text('{"orders":[]}\n', encoding="utf-8")

        atomic_result = write_json_atomic(temp_path, {"orders": [{"order_id": "order-1"}]})
        backup_plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(temp_path)],
            backup_root=str(Path(temp_dir.name) / "backup"),
        )
        rollback_plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        verifier_plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        verifier_result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets={"queue": {"orders": [{"order_id": "order-1"}]}},
            actual_targets={"queue": {"orders": [{"order_id": "order-1"}]}},
        )
        audit_record = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verification_result=verifier_result,
        )
        boundary_result = {
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "commit_id": self.commit_id,
            "preview_only": True,
            "issues": [],
            "warnings": [],
        }
        execution_plan = create_runtime_commit_execution_plan(
            commit_id=self.commit_id,
            boundary_result=boundary_result,
            atomic_writer_plan=atomic_result,
            backup_plan=backup_plan,
            rollback_plan=None,
            verifier_result=verifier_result,
            audit_record=audit_record,
        )
        plan_hash = build_execution_plan_hash(execution_plan)
        approval_context = {
            "approved": True,
            "approved_commit_id": self.commit_id,
            "approved_plan_hash": plan_hash,
            "approved_by": "operator",
            "approval_reason": "manual approval",
            "approval_scope": "RUNTIME_COMMIT",
            "single_use": True,
        }
        execution_token = {
            "token_id": "token-contract-integration-1",
            "commit_id": self.commit_id,
            "plan_hash": plan_hash,
            "scope": "RUNTIME_COMMIT_EXECUTION",
            "single_use": True,
            "consumed": False,
        }
        gate_result = evaluate_runtime_commit_execution_gate(
            commit_id=self.commit_id,
            execution_plan=execution_plan,
            approval_context=approval_context,
            execution_token=execution_token,
            expected_plan_hash=plan_hash,
        )
        return {
            COMPONENT_ATOMIC_WRITER: atomic_result,
            COMPONENT_BACKUP_PLAN: backup_plan,
            COMPONENT_ROLLBACK_PLAN: rollback_plan,
            COMPONENT_COMMIT_VERIFIER_PLAN: verifier_plan,
            COMPONENT_COMMIT_VERIFIER_RESULT: verifier_result,
            COMPONENT_AUDIT_MANIFEST_PREVIEW: audit_record,
            COMPONENT_EXECUTION_PLAN_PREVIEW: execution_plan,
            COMPONENT_EXECUTION_GATE_PREVIEW: gate_result,
        }

    def _normalize_outputs(self) -> dict[str, dict[str, object]]:
        return {
            component: normalize_runtime_commit_component_result(
                component,
                output,
                expected_commit_id=self.commit_id,
            )
            for component, output in self._actual_outputs().items()
        }

    def test_actual_public_apis_are_called_and_return_dicts(self) -> None:
        outputs = self._actual_outputs()
        self.assertEqual(set(outputs), {
            COMPONENT_ATOMIC_WRITER,
            COMPONENT_BACKUP_PLAN,
            COMPONENT_ROLLBACK_PLAN,
            COMPONENT_COMMIT_VERIFIER_PLAN,
            COMPONENT_COMMIT_VERIFIER_RESULT,
            COMPONENT_AUDIT_MANIFEST_PREVIEW,
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            COMPONENT_EXECUTION_GATE_PREVIEW,
        })
        self.assertTrue(all(isinstance(value, dict) for value in outputs.values()))

    def test_component_normalization_matrix_classifies_current_state_as_a(self) -> None:
        normalized = self._normalize_outputs()
        self.assertEqual("SUCCEEDED", normalized[COMPONENT_ATOMIC_WRITER]["status"])
        self.assertEqual("READY", normalized[COMPONENT_BACKUP_PLAN]["status"])
        self.assertEqual("READY", normalized[COMPONENT_ROLLBACK_PLAN]["status"])
        self.assertEqual("READY", normalized[COMPONENT_COMMIT_VERIFIER_PLAN]["status"])
        self.assertEqual("SUCCEEDED", normalized[COMPONENT_COMMIT_VERIFIER_RESULT]["status"])
        self.assertEqual("READY", normalized[COMPONENT_AUDIT_MANIFEST_PREVIEW]["status"])
        self.assertEqual("READY", normalized[COMPONENT_EXECUTION_PLAN_PREVIEW]["status"])
        self.assertEqual("READY", normalized[COMPONENT_EXECUTION_GATE_PREVIEW]["status"])

    def test_current_actual_outputs_have_no_canonical_issues(self) -> None:
        normalized = self._normalize_outputs()
        self.assertTrue(all(contract["issues"] == [] for contract in normalized.values()))

    def test_all_normalized_outputs_validate_and_hash(self) -> None:
        normalized = self._normalize_outputs()
        for component, contract in normalized.items():
            validation = validate_runtime_commit_contract(
                contract,
                expected_component=component,
                expected_commit_id=self.commit_id,
            )
            self.assertTrue(validation["valid"], (component, validation["issues"]))
            self.assertRegex(build_runtime_commit_contract_hash(contract), r"^[0-9a-f]{64}$")

    def test_normalization_is_repeatable_and_does_not_mutate_source(self) -> None:
        outputs = self._actual_outputs()
        source = outputs[COMPONENT_EXECUTION_PLAN_PREVIEW]
        snapshot = deepcopy(source)
        first = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            source,
            expected_commit_id=self.commit_id,
        )
        second = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            source,
            expected_commit_id=self.commit_id,
        )
        self.assertEqual(first, second)
        self.assertEqual(source, snapshot)

    def test_e2e_preview_chain_is_contract_compatible(self) -> None:
        normalized = self._normalize_outputs()
        self.assertEqual("READY", normalized[COMPONENT_EXECUTION_GATE_PREVIEW]["status"])
        self.assertFalse(normalized[COMPONENT_EXECUTION_GATE_PREVIEW]["payload"]["ready_for_real_executor"] is False)
        self.assertFalse(any(contract["status"] == "INVALID" for contract in normalized.values()))

    def test_forged_status_alias_conflict_is_blocked_by_normalization(self) -> None:
        outputs = self._actual_outputs()
        forged = deepcopy(outputs[COMPONENT_EXECUTION_GATE_PREVIEW])
        forged["status"] = "INVALID"
        normalized = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_GATE_PREVIEW,
            forged,
            expected_commit_id=self.commit_id,
        )
        self.assertEqual("INVALID", normalized["status"])
        self.assertTrue(any("status alias conflict" in issue for issue in normalized["issues"]))

    def test_forged_safety_flag_true_is_blocked(self) -> None:
        outputs = self._actual_outputs()
        forged = deepcopy(outputs[COMPONENT_EXECUTION_PLAN_PREVIEW])
        forged["safety_flags"]["runtime_write"] = True
        normalized = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            forged,
            expected_commit_id=self.commit_id,
        )
        self.assertEqual("INVALID", normalized["status"])

    def test_forged_rules_target_is_blocked(self) -> None:
        outputs = self._actual_outputs()
        forged = deepcopy(outputs[COMPONENT_EXECUTION_PLAN_PREVIEW])
        forged["execution_plan"]["target_paths"] = ["routines/지표추종매매/rules.json"]
        normalized = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_PLAN_PREVIEW,
            forged,
            expected_commit_id=self.commit_id,
        )
        self.assertEqual("INVALID", normalized["status"])

    def test_execution_plan_hash_coverage_includes_execution_steps(self) -> None:
        normalized = self._normalize_outputs()[COMPONENT_EXECUTION_PLAN_PREVIEW]
        self.assertEqual("READY", normalized["status"])
        original_hash = build_runtime_commit_contract_hash(normalized)
        forged = deepcopy(normalized)
        forged["payload"]["execution_steps"] = list(reversed(forged["payload"]["execution_steps"]))
        self.assertNotEqual(original_hash, build_runtime_commit_contract_hash(forged))

    def test_warnings_and_metadata_do_not_change_hash(self) -> None:
        normalized = self._normalize_outputs()[COMPONENT_EXECUTION_GATE_PREVIEW]
        original_hash = build_runtime_commit_contract_hash(normalized)
        changed = deepcopy(normalized)
        changed["warnings"] = ["new warning"]
        changed["metadata"]["timestamp"] = "2026-07-10 10:00:00"
        self.assertEqual(original_hash, build_runtime_commit_contract_hash(changed))

    def test_gate_ready_is_lost_when_rollback_required(self) -> None:
        outputs = self._actual_outputs()
        plan = deepcopy(outputs[COMPONENT_EXECUTION_PLAN_PREVIEW])
        plan["execution_plan"]["rollback_required"] = True
        plan_hash = build_execution_plan_hash(plan)
        gate = evaluate_runtime_commit_execution_gate(
            commit_id=self.commit_id,
            execution_plan=plan,
            approval_context={
                "approved": True,
                "approved_commit_id": self.commit_id,
                "approved_plan_hash": plan_hash,
                "approved_by": "operator",
                "approval_reason": "manual approval",
                "approval_scope": "RUNTIME_COMMIT",
                "single_use": True,
            },
            execution_token={
                "token_id": "rollback-required",
                "commit_id": self.commit_id,
                "plan_hash": plan_hash,
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "consumed": False,
            },
            expected_plan_hash=plan_hash,
        )
        normalized = normalize_runtime_commit_component_result(
            COMPONENT_EXECUTION_GATE_PREVIEW,
            gate,
            expected_commit_id=self.commit_id,
        )
        self.assertNotEqual("READY", normalized["status"])


if __name__ == "__main__":
    unittest.main()
