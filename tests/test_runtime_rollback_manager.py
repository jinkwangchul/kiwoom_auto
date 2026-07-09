# -*- coding: utf-8 -*-
"""Tests for runtime_rollback_manager (M6-3).

All tests exercise preview-only behaviour. No rollback files / directories are
created, no protected runtime files (runtime/*.json, routines/*/rules.json)
are touched, and the Atomic Writer is never invoked.
"""

import os
import tempfile
import unittest
from pathlib import Path

from runtime_rollback_manager import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    create_runtime_rollback_plan,
)
from runtime_backup_manager import create_runtime_backup_plan

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ROUTINES_DIR = PROJECT_ROOT / "routines"


class TestRuntimeRollbackManager(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="runtime_rollback_test_"))
        self.commit_id = "commit-xyz-789"

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

    # 1. 정상 READY backup_plan 입력 시 READY rollback plan 생성
    def test_ready_with_valid_backup_plan(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )

        self.assertEqual(plan["rollback_status"], STATUS_READY)
        self.assertTrue(plan["preview_only"])

    # 2. commit_id 포함 확인
    def test_commit_id_present(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["commit_id"], self.commit_id)

    # 3. preview_only=True 확인
    def test_preview_only_true(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertTrue(plan["preview_only"])
        self.assertTrue(plan["rollback_metadata"]["preview_only"])

    # 4. rollback_metadata 포함 확인
    def test_rollback_metadata_present(self):
        f1 = self._real_existing_file("a.json")
        f2 = self._real_existing_file("b.json")
        backup_plan = self._make_valid_backup_plan([f1, f2])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertIn("rollback_metadata", plan)
        self.assertEqual(plan["rollback_metadata"]["target_count"], 2)

    # 5. rollback_targets 포함 확인
    def test_rollback_targets_present(self):
        f1 = self._real_existing_file("a.json")
        f2 = self._real_existing_file("b.json")
        backup_plan = self._make_valid_backup_plan([f1, f2])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(len(plan["rollback_targets"]), 2)

    # 6. rollback_executed=False 확인
    def test_rollback_not_executed(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertFalse(plan["safety_flags"]["rollback_executed"])

    # 7. 실제 rollback 파일 생성/수정 없음 확인
    def test_no_rollback_directory_created(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertFalse(plan["safety_flags"]["rollback_executed"])
        for t in plan["rollback_targets"]:
            backup_candidate = Path(t.get("backup_candidate", ""))
            self.assertFalse(backup_candidate.exists())

    # 8. commit_id 없으면 INVALID
    def test_invalid_without_commit_id(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id="",
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)
        self.assertTrue(any("commit_id" in i for i in plan["issues"]))

    def test_invalid_with_none_commit_id(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=None,
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)

    # 9. backup_plan 없으면 INVALID
    def test_invalid_without_backup_plan(self):
        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=None,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)
        self.assertTrue(any("backup_plan" in i for i in plan["issues"]))

    # 10. backup_plan이 dict 아니면 INVALID
    def test_invalid_with_non_dict_backup_plan(self):
        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan="not-a-dict",
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)
        self.assertTrue(any("backup_plan" in i for i in plan["issues"]))

    # 11. backup_plan.preview_only가 True 아니면 INVALID
    def test_invalid_with_preview_only_false(self):
        backup_plan = {"preview_only": False, "backup_status": STATUS_READY, "commit_id": self.commit_id, "backup_targets": []}

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)

    # 12. backup_plan commit_id 불일치 시 INVALID
    def test_invalid_with_commit_id_mismatch(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id="different-commit",
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)
        self.assertTrue(any("mismatch" in i for i in plan["issues"]))

    # 13. backup_plan backup_status INVALID면 INVALID
    def test_invalid_with_backup_status_invalid(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_INVALID, "commit_id": self.commit_id, "backup_targets": []}

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)

    # 14. backup_plan backup_status BLOCKED면 BLOCKED
    def test_blocked_with_backup_status_blocked(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_BLOCKED, "commit_id": self.commit_id, "backup_targets": []}

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_BLOCKED)

    # 15. failed_targets None이면 전체 backup_targets rollback 후보
    def test_failed_targets_none_rolls_all(self):
        f1 = self._real_existing_file("a.json")
        f2 = self._real_existing_file("b.json")
        backup_plan = self._make_valid_backup_plan([f1, f2])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            failed_targets=None,
        )
        self.assertEqual(len(plan["rollback_targets"]), 2)

    # 16. failed_targets 지정 시 해당 target만 rollback 후보
    def test_failed_targets_specific(self):
        f1 = self._real_existing_file("a.json")
        f2 = self._real_existing_file("b.json")
        f3 = self._real_existing_file("c.json")
        backup_plan = self._make_valid_backup_plan([f1, f2, f3])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            failed_targets=[f2],
        )
        self.assertEqual(len(plan["rollback_targets"]), 1)
        self.assertEqual(plan["rollback_targets"][0]["source"], str(f2))

    # 17. failed_targets가 backup_targets와 매칭 안 되면 BLOCKED
    def test_blocked_with_non_matching_failed_targets(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        non_matching = Path(tempfile.mktemp(suffix=".json"))

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            failed_targets=[non_matching],
        )
        self.assertEqual(plan["rollback_status"], STATUS_BLOCKED)

    # 18. routines/*/rules.json 포함 시 INVALID
    def test_invalid_with_rules_json_target(self):
        f1 = self._real_existing_file("a.json")
        rules = self.tmp_dir / "rules.json"
        rules.write_text("{}", encoding="utf-8")
        backup_plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(f1), str(rules)],
        )

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(plan["rollback_status"], STATUS_INVALID)

    # 19. atomic_writer_called=False 확인
    def test_atomic_writer_not_called(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertFalse(plan["safety_flags"]["atomic_writer_called"])

    # 20. backup_manager_called=False 확인
    def test_backup_manager_not_called(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertFalse(plan["safety_flags"]["backup_manager_called"])

    # 21. runtime/*.json 실제 변경 없음 확인
    def test_no_runtime_json_change(self):
        f1 = self._real_existing_file("a.json")
        backup_plan = self._make_valid_backup_plan([f1])

        before = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        after = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        self.assertEqual(before, after)
        self.assertEqual(plan["rollback_status"], STATUS_READY)


if __name__ == "__main__":
    unittest.main()