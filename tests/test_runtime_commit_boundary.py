# -*- coding: utf-8 -*-
"""Tests for runtime_commit_boundary."""

import unittest
from unittest.mock import patch

from runtime_commit_boundary import evaluate_runtime_commit_boundary


class TestRuntimeCommitBoundary(unittest.TestCase):

    def setUp(self):
        # A minimal valid orchestrator result that should pass eligibility
        self.valid_orchestrator_result = {
            "preview_type": "LIFECYCLE_EXECUTION_PREVIEW_ORCHESTRATOR",
            "status": "ORCHESTRATOR_READY",
            "preview_only": True,
            "execution_allowed": False,
            "execution_started": False,
            "execution_completed": False,
            "dispatch_allowed": False,
            "dispatch_started": False,
            "dispatch_completed": False,
            "send_order_called": False,
            "send_order_result_recorded": False,
            "recorder_called": False,
            "chejan_called": False,
            "runtime_write": False,
            "position_write": False,
            "balance_write": False,
            "audit_write": False,
            "file_write_called": False,
            "gui_update_called": False,
            "backup_created": False,
            "rollback_executed": False,
            "orchestrator_steps": [
                {
                    "step_index": 1,
                    "step_name": "execution_transaction_contract",
                    "status": "EXECUTION_TRANSACTION_CONTRACT_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 2,
                    "step_name": "execution_engine_preview",
                    "status": "EXECUTION_ENGINE_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 3,
                    "step_name": "broker_adapter_contract_preview",
                    "status": "BROKER_ADAPTER_CONTRACT_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 4,
                    "step_name": "order_router_contract_preview",
                    "status": "ORDER_ROUTER_CONTRACT_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 5,
                    "step_name": "sendorder_contract_preview",
                    "status": "SENDORDER_CONTRACT_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 6,
                    "step_name": "sendorder_call_preview",
                    "status": "SENDORDER_CALL_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 7,
                    "step_name": "sendorder_result_review_preview",
                    "status": "SENDORDER_RESULT_REVIEW_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 8,
                    "step_name": "execution_final_approval_preview",
                    "status": "EXECUTION_FINAL_APPROVAL_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 9,
                    "step_name": "execution_dispatcher_preview",
                    "status": "EXECUTION_DISPATCHER_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 10,
                    "step_name": "execution_commit_preview",
                    "status": "EXECUTION_COMMIT_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
                {
                    "step_index": 11,
                    "step_name": "execution_runtime_apply_preview",
                    "status": "EXECUTION_RUNTIME_APPLY_PREVIEW_READY",
                    "preview_only": True,
                    "completed": True,
                    "blocked": False,
                    "invalid": False,
                },
            ],
            "failed_step": "",
            "orchestrator_summary": {
                "total_steps": 11,
                "completed_steps": 11,
                "blocked_step": "",
                "invalid_step": "",
                "final_status": "ORCHESTRATOR_READY",
                "preview_only": True,
            },
            "final_orchestrator_decision": {
                "approved": True,
                "execution_allowed": False,
                "runtime_write": False,
                "send_order_called": False,
                "preview_only": True,
            },
            "step_results": {
                # We don't need the full step results for the boundary tests, but we need at least the commit and runtime apply previews
                "execution_commit_preview": {
                    "preview_type": "LIFECYCLE_EXECUTION_COMMIT_PREVIEW",
                    "status": "EXECUTION_COMMIT_PREVIEW_READY",
                    "preview_only": True,
                    "execution_commit_allowed": False,
                    "execution_commit_started": False,
                    "execution_commit_completed": False,
                    "dispatch_allowed": False,
                    "dispatch_started": False,
                    "dispatch_completed": False,
                    "execution_allowed": False,
                    "execution_started": False,
                    "execution_completed": False,
                    "send_order_called": False,
                    "send_order_result_recorded": False,
                    "recorder_called": False,
                    "chejan_called": False,
                    "runtime_write": False,
                    "position_write": False,
                    "balance_write": False,
                    "audit_write": False,
                    "file_write_called": False,
                    "gui_update_called": False,
                    "backup_created": False,
                    "rollback_executed": False,
                    "execution_commit_candidate_preview": {
                        "candidates": [{"candidate_id": "EXECUTION_COMMIT_CANDIDATE_001", "candidate_ready": True, "preview_only": True}],
                        "total_candidates": 1,
                        "execution_commit_candidate_ready": True,
                        "execution_commit_candidate_blocked": False,
                        "dispatch_reference": "",
                        "preview_only": True,
                    },
                    "execution_commit_route_preview": {
                        "route_ready": True,
                        "route_target": "PREVIEW_ONLY_COMMIT_TARGET",
                        "route_strategy": "PREVIEW_ONLY_COMMIT_STRATEGY",
                        "route_blocked": False,
                        "route_reason": "preview-only execution commit route",
                        "preview_only": True,
                    },
                    "execution_commit_queue_preview": {
                        "queue_ready": True,
                        "queue_name": "PREVIEW_ONLY_COMMIT_QUEUE",
                        "queue_position": 0,
                        "queue_size": 0,
                        "queue_enqueued": False,
                        "queue_started": False,
                        "queue_reason": "preview-only execution commit queue",
                        "preview_only": True,
                    },
                    "post_commit_verification_preview": {
                        "post_commit_verification_required": True,
                        "post_commit_verification_completed": False,
                        "verification_items": [
                            {
                                "verification_index": 1,
                                "verification_name": "dispatch_preview_ready",
                                "verification_description": "Confirm dispatcher preview is ready",
                                "verification_required": True,
                                "verification_completed": False,
                                "preview_only": True,
                            }
                        ],
                        "total_items": 1,
                        "verification_reason": "preview-only post-commit verification plan",
                        "preview_only": True,
                    },
                    "commit_safety_validation": {
                        "ready": True,
                        "issues": [],
                        "warnings": [],
                        "preview_only": True,
                    },
                    "final_commit_decision": {
                        "committed": True,
                        "blocked": False,
                        "invalid": False,
                        "rejection_reason": "",
                        "commit_reason": "commit safety validation ready",
                        "execution_commit_allowed": False,
                        "execution_commit_started": False,
                        "execution_commit_completed": False,
                        "dispatch_allowed": False,
                        "dispatch_started": False,
                        "dispatch_completed": False,
                        "execution_allowed": False,
                        "execution_started": False,
                        "execution_completed": False,
                        "send_order_called": False,
                        "send_order_result_recorded": False,
                        "recorder_called": False,
                        "chejan_called": False,
                        "runtime_write": False,
                        "position_write": False,
                        "balance_write": False,
                        "audit_write": False,
                        "file_write_called": False,
                        "gui_update_called": False,
                        "backup_created": False,
                        "rollback_executed": False,
                        "preview_only": True,
                    },
                    "preview_only": True,
                },
                "execution_runtime_apply_preview": {
                    "preview_type": "LIFECYCLE_EXECUTION_RUNTIME_APPLY_PREVIEW",
                    "status": "EXECUTION_RUNTIME_APPLY_PREVIEW_READY",
                    "preview_only": True,
                    "runtime_apply_allowed": False,
                    "runtime_apply_started": False,
                    "runtime_apply_completed": False,
                    "execution_commit_allowed": False,
                    "execution_commit_started": False,
                    "execution_commit_completed": False,
                    "dispatch_allowed": False,
                    "dispatch_started": False,
                    "dispatch_completed": False,
                    "execution_allowed": False,
                    "execution_started": False,
                    "execution_completed": False,
                    "send_order_called": False,
                    "send_order_result_recorded": False,
                    "recorder_called": False,
                    "chejan_called": False,
                    "runtime_write": False,
                    "position_write": False,
                    "balance_write": False,
                    "audit_write": False,
                    "file_write_called": False,
                    "gui_update_called": False,
                    "backup_created": False,
                    "rollback_executed": False,
                    "runtime_apply_candidate_preview": {
                        "candidates": [{"candidate_index": 1, "candidate_id": "RUNTIME_APPLY_CANDIDATE_001", "candidate_source": "EXECUTION_COMMIT_PREVIEW", "candidate_ready": True, "preview_only": True}],
                        "total_candidates": 1,
                        "runtime_apply_candidate_ready": True,
                        "runtime_apply_candidate_blocked": False,
                        "commit_reference": "",
                        "preview_only": True,
                    },
                    "runtime_apply_target_preview": {
                        "targets": [
                            "runtime/order_queue.json",
                            "runtime/order_executions.json",
                            "runtime/order_locks.json",
                        ],
                        "total_targets": 3,
                        "target_ready": True,
                        "target_written": False,
                        "target_reason": "preview-only runtime apply targets",
                        "preview_only": True,
                    },
                    "runtime_apply_sequence_preview": {
                        "sequence_ready": True,
                        "steps": [
                            {
                                "step_index": 1,
                                "step_name": "lock_runtime",
                                "step_description": "Preview-only lock runtime before apply",
                                "step_executed": False,
                                "preview_only": True,
                            }
                        ],
                        "total_steps": 1,
                        "sequence_executed": False,
                        "sequence_reason": "preview-only runtime apply sequence",
                        "preview_only": True,
                    },
                    "runtime_apply_verification_preview": {
                        "runtime_apply_verification_required": True,
                        "runtime_apply_verification_completed": False,
                        "verification_items": [
                            {
                                "verification_index": 1,
                                "verification_name": "commit_preview_ready",
                                "verification_description": "Confirm commit preview is ready",
                                "verification_required": True,
                                "verification_completed": False,
                                "preview_only": True,
                            }
                        ],
                        "total_items": 1,
                        "verification_reason": "preview-only runtime apply verification plan",
                        "preview_only": True,
                    },
                    "runtime_apply_safety_validation": {
                        "ready": True,
                        "issues": [],
                        "warnings": [],
                        "preview_only": True,
                    },
                    "final_runtime_apply_decision": {
                        "applied": True,
                        "blocked": False,
                        "invalid": False,
                        "rejection_reason": "",
                        "apply_reason": "runtime apply safety validation ready",
                        "runtime_apply_allowed": False,
                        "runtime_apply_started": False,
                        "runtime_apply_completed": False,
                        "execution_commit_allowed": False,
                        "execution_commit_started": False,
                        "execution_commit_completed": False,
                        "dispatch_allowed": False,
                        "dispatch_started": False,
                        "dispatch_completed": False,
                        "execution_allowed": False,
                        "execution_started": False,
                        "execution_completed": False,
                        "send_order_called": False,
                        "send_order_result_recorded": False,
                        "recorder_called": False,
                        "chejan_called": False,
                        "runtime_write": False,
                        "position_write": False,
                        "balance_write": False,
                        "audit_write": False,
                        "file_write_called": False,
                        "gui_update_called": False,
                        "backup_created": False,
                        "rollback_executed": False,
                        "preview_only": True,
                    },
                    "preview_only": True,
                },
            },
            "generated_at": "2026-07-09 08:00:00",
        }

    def test_orchestrator_ready_returns_ready(self):
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_READY")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["ready"])
        self.assertFalse(result["final_runtime_commit_boundary_decision"]["blocked"])
        self.assertFalse(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_orchestrator_result_none_is_invalid(self):
        result = evaluate_runtime_commit_boundary(None)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_INVALID")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_orchestrator_status_blocked_returns_blocked(self):
        blocked_result = self.valid_orchestrator_result.copy()
        blocked_result["status"] = "BLOCKED"
        result = evaluate_runtime_commit_boundary(blocked_result)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_BLOCKED")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["blocked"])

    def test_orchestrator_status_invalid_returns_invalid(self):
        invalid_result = self.valid_orchestrator_result.copy()
        invalid_result["status"] = "INVALID"
        result = evaluate_runtime_commit_boundary(invalid_result)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_INVALID")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_orchestrator_step_not_ready_returns_blocked(self):
        # Make one step not completed
        not_ready_result = self.valid_orchestrator_result.copy()
        not_ready_result["orchestrator_steps"][0]["completed"] = False
        not_ready_result["orchestrator_steps"][0]["status"] = "EXECUTION_TRANSACTION_CONTRACT_READY"  # status doesn't matter for completed check
        result = evaluate_runtime_commit_boundary(not_ready_result)
        # The eligibility check will fail because a step is not completed, leading to INVALID (because issues are added)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_INVALID")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_safety_flag_runtime_write_true_returns_invalid(self):
        invalid_result = self.valid_orchestrator_result.copy()
        invalid_result["runtime_write"] = True
        result = evaluate_runtime_commit_boundary(invalid_result)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_INVALID")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_safety_flag_send_order_called_true_returns_invalid(self):
        invalid_result = self.valid_orchestrator_result.copy()
        invalid_result["send_order_called"] = True
        result = evaluate_runtime_commit_boundary(invalid_result)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_INVALID")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_safety_flag_chejan_called_true_returns_invalid(self):
        invalid_result = self.valid_orchestrator_result.copy()
        invalid_result["chejan_called"] = True
        result = evaluate_runtime_commit_boundary(invalid_result)
        self.assertEqual(result["status"], "RUNTIME_COMMIT_BOUNDARY_INVALID")
        self.assertTrue(result["final_runtime_commit_boundary_decision"]["invalid"])

    def test_contract_contains_atomic_apply_plan_verification_plan_rollback_plan(self):
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        contract = result["runtime_commit_contract"]
        self.assertIn("atomic_apply_plan", contract)
        self.assertIn("verification_plan", contract)
        self.assertIn("rollback_plan", contract)
        self.assertIsInstance(contract["atomic_apply_plan"], dict)
        self.assertIsInstance(contract["verification_plan"], dict)
        self.assertIsInstance(contract["rollback_plan"], dict)

    def test_atomic_apply_plan_is_not_a_separate_layer(self):
        # The atomic_apply_plan is inside the contract, not a top-level section in the result.
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        self.assertNotIn("runtime_commit_atomic_apply_plan", result)
        self.assertIn("runtime_commit_contract", result)
        contract = result["runtime_commit_contract"]
        self.assertIn("atomic_apply_plan", contract)

    def test_no_separate_dry_run_section(self):
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        self.assertNotIn("runtime_commit_dry_run", result)
        self.assertNotIn("runtime_commit_dry_run_preview", result)

    def test_no_separate_result_review_section(self):
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        self.assertNotIn("runtime_commit_result_review", result)
        self.assertNotIn("runtime_commit_result_review_preview", result)

    def test_protected_files_unchanged_verification_item_exists(self):
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        verification_plan = result["runtime_commit_contract"]["verification_plan"]
        self.assertIn("items", verification_plan)
        item_names = [item["verification_name"] for item in verification_plan["items"]]
        self.assertIn("protected_files_unchanged", item_names)

    def test_all_safety_flags_false_in_result(self):
        result = evaluate_runtime_commit_boundary(self.valid_orchestrator_result)
        decision = result["final_runtime_commit_boundary_decision"]
        for flag in [
            "runtime_write",
            "position_write",
            "balance_write",
            "audit_write",
            "file_write_called",
            "backup_created",
            "rollback_executed",
            "gui_update_called",
            "send_order_called",
            "chejan_called",
            "broker_called",
            "sqlite_write",
            "rules_write",
            "execution_allowed",
            "dispatch_allowed",
            "execution_commit_allowed",
            "runtime_apply_allowed",
        ]:
            self.assertFalse(decision[flag], f"Flag {flag} should be False")


if __name__ == "__main__":
    unittest.main()