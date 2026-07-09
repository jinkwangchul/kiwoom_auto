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


if __name__ == "__main__":
    unittest.main()