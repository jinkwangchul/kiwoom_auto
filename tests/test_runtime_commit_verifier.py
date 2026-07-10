# -*- coding: utf-8 -*-
"""Tests for runtime_commit_verifier (M6-4).

All tests exercise preview-only behaviour. No commit verification execution,
no protected runtime files (runtime/*.json, routines/*/rules.json) are touched,
and the Atomic Writer is never invoked.
"""

import tempfile
import unittest
from pathlib import Path

from runtime_commit_verifier import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    create_runtime_commit_verifier_plan,
    verify_runtime_commit,
)
from runtime_backup_manager import create_runtime_backup_plan
from runtime_rollback_manager import create_runtime_rollback_plan

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


class TestRuntimeCommitVerifier(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="runtime_verify_test_"))
        self.commit_id = "verify-abc-456"

    def tearDown(self):
        for child in self.tmp_dir.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp_dir.rmdir()
        except OSError:
            pass

    def _real_existing_file(self, name: str) -> Path:
        p = self.tmp_dir / name
        p.write_text("{}", encoding="utf-8")
        return p

    def _make_valid_backup_plan(self, targets: list[Path]) -> dict:
        return create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(t) for t in targets],
        )

    def _make_valid_rollback_plan(self, backup_plan: dict) -> dict:
        return create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )

    # 1. 정상 READY backup_plan + rollback_plan 입력 시 READY verify plan 생성
    def test_ready_with_valid_plans(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )

        self.assertEqual(plan["verify_status"], STATUS_READY)
        self.assertTrue(plan["preview_only"])

    # 2. commit_id 포함 확인
    def test_commit_id_present(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["commit_id"], self.commit_id)

    # 3. preview_only=True 확인
    def test_preview_only_true(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertTrue(plan["preview_only"])
        self.assertTrue(plan["verify_metadata"]["preview_only"])

    # 4. verify_metadata 포함 확인
    def test_verify_metadata_present(self):
        f1 = self._real_existing_file("a.json")
        f2 = self._real_existing_file("b.json")
        backup_plan = self._make_valid_backup_plan([f1, f2])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertIn("verify_metadata", plan)
        self.assertEqual(plan["verify_metadata"]["backup_target_count"], 2)

    # 5. verify_strategy 포함 확인
    def test_verify_strategy_present(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertIn("verify_strategy", plan)
        self.assertEqual(plan["verify_strategy"]["strategy"], "verify_before_commit")

    # 6. safety_flags rollback_executed=False 확인
    def test_rollback_executed_false(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertFalse(plan["safety_flags"]["rollback_executed"])

    # 7. 실제 파일 생성/수정 없음 확인
    def test_no_files_created_or_modified(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        before_mtime = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        after_mtime = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        self.assertEqual(before_mtime, after_mtime)

    # 8. commit_id 없으면 INVALID
    def test_invalid_without_commit_id(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id="",
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 9. backup_plan 없으면 INVALID
    def test_invalid_without_backup_plan(self):
        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=None,
            rollback_plan={},
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 10. rollback_plan 없으면 INVALID
    def test_invalid_without_rollback_plan(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=None,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 11. backup_plan이 dict 아니면 INVALID
    def test_invalid_with_non_dict_backup_plan(self):
        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan="not-a-dict",
            rollback_plan={},
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 12. rollback_plan이 dict 아니면 INVALID
    def test_invalid_with_non_dict_rollback_plan(self):
        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan={},
            rollback_plan="not-a-dict",
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 13. backup_plan.preview_only가 True 아니면 INVALID
    def test_invalid_with_backup_preview_only_false(self):
        backup_plan = {"preview_only": False, "backup_status": STATUS_READY, "commit_id": self.commit_id, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_READY, "commit_id": self.commit_id, "rollback_targets": []}

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 14. rollback_plan.preview_only가 True 아니면 INVALID
    def test_invalid_with_rollback_preview_only_false(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_READY, "commit_id": self.commit_id, "backup_targets": []}
        rollback_plan = {"preview_only": False, "rollback_status": STATUS_READY, "commit_id": self.commit_id, "rollback_targets": []}

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 15. commit_id 불일치 시 INVALID
    def test_invalid_with_commit_id_mismatch(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        plan = create_runtime_commit_verifier_plan(
            commit_id="different-commit",
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 16. backup_plan backup_status INVALID면 INVALID
    def test_invalid_with_backup_status_invalid(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_INVALID, "commit_id": self.commit_id, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_READY, "commit_id": self.commit_id, "rollback_targets": []}

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 17. rollback_plan rollback_status INVALID면 INVALID
    def test_invalid_with_rollback_status_invalid(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_READY, "commit_id": self.commit_id, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_INVALID, "commit_id": self.commit_id, "rollback_targets": []}

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_INVALID)

    # 18. backup_plan backup_status BLOCKED면 BLOCKED
    def test_blocked_with_backup_status_blocked(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_BLOCKED, "commit_id": self.commit_id, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_READY, "commit_id": self.commit_id, "rollback_targets": []}

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_BLOCKED)

    # 19. rollback_plan rollback_status BLOCKED면 BLOCKED
    def test_blocked_with_rollback_status_blocked(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_READY, "commit_id": self.commit_id, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_BLOCKED, "commit_id": self.commit_id, "rollback_targets": []}

        plan = create_runtime_commit_verifier_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(plan["verify_status"], STATUS_BLOCKED)

    # === verify_runtime_commit 추가 테스트 ===

    # 1. identical expected/actual → READY
    def test_identical_targets_ready(self):
        expected = {"a.json": {"value": 1}, "b.json": {"value": 2}}
        actual = {"a.json": {"value": 1}, "b.json": {"value": 2}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_READY)

    # 2. matched_targets 집계
    def test_matched_targets_aggregated(self):
        expected = {"a.json": {"value": 1}, "b.json": {"value": 2}}
        actual = {"a.json": {"value": 1}, "b.json": {"value": 2}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(len(result["matched_targets"]), 2)

    # 3. missing target → BLOCKED
    def test_missing_target_blocked(self):
        expected = {"a.json": {"value": 1}, "b.json": {"value": 2}}
        actual = {"a.json": {"value": 1}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_BLOCKED)
        self.assertTrue(any("b.json" in m for m in result["missing_targets"]))

    # 4. missing target → rollback_required=True
    def test_missing_target_rollback_required(self):
        expected = {"a.json": {"value": 1}, "b.json": {"value": 2}}
        actual = {"a.json": {"value": 1}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertTrue(result["rollback_required"])

    # 5. mismatch → BLOCKED
    def test_mismatch_blocked(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 999}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_BLOCKED)
        self.assertIn("a.json", result["mismatched_targets"])

    # 6. mismatch → rollback_required=True
    def test_mismatch_rollback_required(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 999}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertTrue(result["rollback_required"])

    # 7. unexpected target 기본 warning
    def test_unexpected_target_warning(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 1}, "c.json": {"value": 3}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_READY)
        self.assertTrue(any("c.json" in w for w in result["warnings"]))

    # 8. strict_compare unexpected → BLOCKED
    def test_strict_compare_unexpected_blocked(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 1}, "c.json": {"value": 3}}
        verification_plan = {"strict_compare": True}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
            verification_plan=verification_plan,
        )
        self.assertEqual(result["verification_status"], STATUS_BLOCKED)

    # 9. dict key 순서 차이 → READY
    def test_dict_key_order_ignored(self):
        expected = {"a.json": {"x": 1, "y": 2}}
        actual = {"a.json": {"y": 2, "x": 1}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_READY)

    # 10. list 순서 차이 → BLOCKED
    def test_list_order_matters(self):
        expected = {"a.json": {"items": [1, 2, 3]}}
        actual = {"a.json": {"items": [3, 2, 1]}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_BLOCKED)

    # 11. compare_fields 지정 비교
    def test_compare_fields_specified(self):
        expected = {"a.json": {"x": 1, "y": 2}}
        actual = {"a.json": {"x": 1, "y": 999}}
        verification_plan = {"compare_fields": ["x"]}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
            verification_plan=verification_plan,
        )
        self.assertEqual(result["verification_status"], STATUS_READY)

    # 12. 비지정 필드 차이 무시
    def test_uncompared_fields_ignored(self):
        expected = {"a.json": {"x": 1, "y": 2}}
        actual = {"a.json": {"x": 1, "z": 999}}
        verification_plan = {"compare_fields": ["x"]}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
            verification_plan=verification_plan,
        )
        self.assertEqual(result["verification_status"], STATUS_READY)

    # 13. hash 일치 → READY
    def test_hash_match_ready(self):
        expected = {"a.json": {"expected_hash": "abc123"}}
        actual = {"a.json": {"actual_hash": "abc123"}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_READY)

    # 14. hash 불일치 → BLOCKED
    def test_hash_mismatch_blocked(self):
        expected = {"a.json": {"expected_hash": "abc123"}}
        actual = {"a.json": {"actual_hash": "def456"}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_BLOCKED)

    # 15. commit_id 없음 → INVALID
    def test_invalid_without_commit_id(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 1}}

        result = verify_runtime_commit(
            commit_id="",
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_INVALID)

    # 16. expected_targets 타입 오류 → INVALID
    def test_invalid_expected_targets_type(self):
        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets="not-a-dict",
            actual_targets={},
        )
        self.assertEqual(result["verification_status"], STATUS_INVALID)

    # 17. actual_targets 타입 오류 → INVALID
    def test_invalid_actual_targets_type(self):
        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets={},
            actual_targets="not-a-dict",
        )
        self.assertEqual(result["verification_status"], STATUS_INVALID)

    # 18. verification_plan 타입 오류 → INVALID
    def test_invalid_verification_plan_type(self):
        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets={},
            actual_targets={},
            verification_plan="not-a-dict",
        )
        self.assertEqual(result["verification_status"], STATUS_INVALID)

    # 19. expected_targets 비어 있음 → BLOCKED
    def test_empty_expected_targets_blocked(self):
        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets={},
            actual_targets={},
        )
        self.assertEqual(result["verification_status"], STATUS_BLOCKED)

    # 20. rules.json target → INVALID
    def test_invalid_rules_json_target(self):
        expected = {"rules.json": {"value": 1}}
        actual = {"rules.json": {"value": 1}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        self.assertEqual(result["verification_status"], STATUS_INVALID)

    # 21. safety flags 전체 False
    def test_all_safety_flags_false(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 1}}

        result = verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        for value in result["safety_flags"].values():
            self.assertFalse(value)

    # 22. 실제 runtime 파일 변경 없음
    def test_no_runtime_files_changed(self):
        expected = {"a.json": {"value": 1}}
        actual = {"a.json": {"value": 1}}

        before_mtime = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        verify_runtime_commit(
            commit_id=self.commit_id,
            expected_targets=expected,
            actual_targets=actual,
        )
        after_mtime = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        self.assertEqual(before_mtime, after_mtime)

    # 23. 기존 19개 테스트 회귀 없음 (이미 테스트됨)


if __name__ == "__main__":
    unittest.main()