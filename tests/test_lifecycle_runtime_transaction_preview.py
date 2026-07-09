# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest

from lifecycle_runtime_transaction_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_runtime_transaction_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


def _ready_engine_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "preview_type": "LIFECYCLE_RUNTIME_APPLY_ENGINE",
        "status": "ENGINE_READY",
        "preview_only": True,
        "engine_executed": False,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "engine_execution_plan": {
            "plan_type": "ENGINE_EXECUTION_PLAN",
            "status": "ENGINE_READY",
            "ready": True,
            "engine_run_id": "ENGINE_RUN_1",
            "source_contract_id": "ENGINE_CONTRACT_1",
            "preview_only": True,
            "engine_executed": False,
        },
        "transaction_plan_preview": {
            "plan_type": "TRANSACTION_PLAN_PREVIEW",
            "status": "ENGINE_READY",
            "ready": True,
            "preview_only": True,
            "transaction_opened": False,
            "sqlite_write": False,
        },
        "file_write_plan_preview": {
            "plan_type": "FILE_WRITE_PLAN_PREVIEW",
            "status": "ENGINE_READY",
            "ready": True,
            "runtime_file_write_preview": {"payload": {"runtime": "preview"}},
            "position_file_write_preview": {"payload": {"position": "preview"}},
            "balance_file_write_preview": {"payload": {"balance": "preview"}},
            "backup_preview": {"backup": "preview"},
            "rollback_preview": {"rollback": "preview"},
            "preview_only": True,
            "file_write_called": False,
            "runtime_write": False,
            "position_write": False,
            "balance_write": False,
        },
        "final_engine_decision": {
            "approved_for_future_engine": True,
            "blocked": False,
            "invalid": False,
            "preview_only": True,
            "engine_executed": False,
        },
        "engine_validation": {
            "ready": True,
            "blocked": False,
            "invalid": False,
            "issues": [],
            "warnings": [],
            "preview_only": True,
        },
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class LifecycleRuntimeTransactionPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def test_ready_engine_result_builds_transaction_preview_ready(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["pre_transaction_validation"]["ready"])
        self.assertTrue(result["final_transaction_decision"]["approved"])

    def test_blocked_engine_result_builds_blocked_preview(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result(status="BLOCKED", issues=["blocked upstream"]))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertTrue(result["pre_transaction_validation"]["blocked"])
        self.assertFalse(result["final_transaction_decision"]["approved"])

    def test_invalid_and_malformed_engine_result_are_invalid(self) -> None:
        invalid = build_runtime_transaction_preview(_ready_engine_result(status="INVALID"))
        malformed = build_runtime_transaction_preview({"status": "ENGINE_READY"})

        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertTrue(invalid["pre_transaction_validation"]["invalid"])
        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertTrue(malformed["pre_transaction_validation"]["invalid"])

    def test_transaction_boundary_shape(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result())
        boundary = result["transaction_boundary"]

        self.assertEqual("RUNTIME_APPLY_TRANSACTION", boundary["boundary_type"])
        self.assertTrue(boundary["atomic"])
        self.assertTrue(boundary["all_or_nothing"])
        self.assertTrue(boundary["requires_backup"])
        self.assertTrue(boundary["requires_rollback"])
        self.assertTrue(boundary["preview_only"])
        self.assertFalse(boundary["transaction_executed"])

    def test_transaction_token_preview_shape(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result())
        token = result["transaction_token_preview"]

        self.assertTrue(token["token_required"])
        self.assertFalse(token["token_issued"])
        self.assertFalse(token["token_consumed"])
        self.assertTrue(token["preview_only"])

    def test_apply_group_preview_shape(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result())
        apply_group = result["apply_group_preview"]

        self.assertTrue(apply_group["runtime_group"])
        self.assertTrue(apply_group["position_group"])
        self.assertTrue(apply_group["balance_group"])
        self.assertTrue(apply_group["file_write_group"])
        self.assertFalse(apply_group["file_write_group"]["file_write_called"])
        self.assertTrue(apply_group["preview_only"])

    def test_rollback_plan_preview_shape(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result())
        rollback = result["rollback_plan_preview"]

        self.assertTrue(rollback["rollback_required_on_failure"])
        self.assertFalse(rollback["rollback_executed"])
        self.assertTrue(rollback["rollback_source"])
        self.assertTrue(rollback["backup_source"])
        self.assertTrue(rollback["preview_only"])

    def test_final_transaction_decision_shape(self) -> None:
        ready = build_runtime_transaction_preview(_ready_engine_result())
        blocked = build_runtime_transaction_preview(_ready_engine_result(status="BLOCKED"))

        self.assertTrue(ready["final_transaction_decision"]["approved"])
        self.assertFalse(ready["final_transaction_decision"]["transaction_executed"])
        self.assertFalse(blocked["final_transaction_decision"]["approved"])
        self.assertTrue(blocked["final_transaction_decision"]["blocked"])

    def test_safety_flags_are_fixed(self) -> None:
        result = build_runtime_transaction_preview(_ready_engine_result())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["transaction_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["position_write"])
        self.assertFalse(result["balance_write"])
        self.assertFalse(result["file_write_called"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["gui_update_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["chejan_called"])

    def test_input_is_not_mutated(self) -> None:
        engine_result = _ready_engine_result()
        original = copy.deepcopy(engine_result)

        build_runtime_transaction_preview(engine_result)

        self.assertEqual(original, engine_result)

    def test_protected_files_hash_unchanged(self) -> None:
        build_runtime_transaction_preview(_ready_engine_result())
        build_runtime_transaction_preview(_ready_engine_result(status="BLOCKED"))
        build_runtime_transaction_preview(_ready_engine_result(status="INVALID"))

        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
