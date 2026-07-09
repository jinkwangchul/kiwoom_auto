# -*- coding: utf-8 -*-
"""Tests for runtime_backup_manager (M6-2).

All tests exercise preview-only behaviour. No backup files / directories are
created, no protected runtime files (runtime/*.json, routines/*/rules.json)
are touched, and the Atomic Writer is never invoked.
"""

import os
import tempfile
import unittest
from pathlib import Path

from runtime_backup_manager import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    create_runtime_backup_plan,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ROUTINES_DIR = PROJECT_ROOT / "routines"


class TestRuntimeBackupManager(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="runtime_backup_test_"))
        self.commit_id = "commit-abc-123"

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

    # 1. 정상 target_files 입력 시 READY backup plan 생성
    def test_ready_with_valid_target_files(self):
        f1 = self._real_existing_file("order_executions.json")
        f2 = self._real_existing_file("order_locks.json")

        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(f1), f2],
        )

        self.assertEqual(plan["backup_status"], STATUS_READY)
        self.assertTrue(plan["preview_only"])

    # 2. commit_id 포함 확인
    def test_commit_id_present(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        self.assertEqual(plan["commit_id"], self.commit_id)

    # 3. preview_only=True 확인
    def test_preview_only_true(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        self.assertTrue(plan["preview_only"])
        self.assertTrue(plan["backup_metadata"]["preview_only"])

    # 4. backup_metadata 포함 확인
    def test_backup_metadata_present(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        self.assertIn("backup_metadata", plan)
        self.assertEqual(
            plan["backup_metadata"]["target_count"], 1
        )

    # 5. backup_targets 포함 확인
    def test_backup_targets_present(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        self.assertEqual(len(plan["backup_targets"]), 1)
        self.assertEqual(
            plan["backup_targets"][0]["source"], str(f1)
        )

    # 6. backup_created=False 확인
    def test_backup_not_created(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        self.assertFalse(plan["safety_flags"]["backup_created"])

    # 7. 실제 backup 디렉터리/파일 생성 없음 확인
    def test_no_backup_directory_created(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        backup_root_preview = Path(plan["backup_root_preview"])
        # preview 경로 자체도 실제로 생성되면 안됨
        self.assertFalse(backup_root_preview.exists())
        # 원본 파일 외에 backup 후보 파일도 생성되면 안됨
        candidate = Path(plan["backup_targets"][0]["backup_candidate"])
        self.assertFalse(candidate.exists())

    # 8. commit_id 없으면 INVALID
    def test_invalid_without_commit_id(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id="",
            target_files=[f1],
        )
        self.assertEqual(plan["backup_status"], STATUS_INVALID)
        self.assertTrue(any("commit_id" in i for i in plan["issues"]))

    def test_invalid_with_none_commit_id(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=None,
            target_files=[f1],
        )
        self.assertEqual(plan["backup_status"], STATUS_INVALID)

    # 9. target_files 없으면 INVALID
    def test_invalid_without_target_files(self):
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
        )
        self.assertEqual(plan["backup_status"], STATUS_INVALID)
        self.assertTrue(any("target_files" in i for i in plan["issues"]))

    def test_invalid_with_empty_target_files(self):
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[],
        )
        self.assertEqual(plan["backup_status"], STATUS_INVALID)

    def test_invalid_with_wrong_target_files_type(self):
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files="not-a-list",
        )
        self.assertEqual(plan["backup_status"], STATUS_INVALID)

    # 10. 존재하지 않는 target file은 BLOCKED
    def test_blocked_with_missing_target_file(self):
        f1 = self._real_existing_file("a.json")
        missing = self.tmp_dir / "does_not_exist.json"
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(f1), str(missing)],
        )
        self.assertEqual(plan["backup_status"], STATUS_BLOCKED)
        self.assertTrue(any("does not exist" in i for i in plan["issues"]))

    # 11. routines/*/rules.json 포함 시 INVALID
    def test_invalid_with_rules_json_target(self):
        f1 = self._real_existing_file("a.json")
        # 프로젝트 실제 routines/rules.json 이 아닌, 이름만 매칭되는 임시 rules.json
        rules = self.tmp_dir / "rules.json"
        rules.write_text("{}", encoding="utf-8")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(f1), str(rules)],
        )
        self.assertEqual(plan["backup_status"], STATUS_INVALID)
        self.assertTrue(any("rules.json" in i for i in plan["issues"]))

    # 12. atomic_writer_called=False 확인
    def test_atomic_writer_not_called(self):
        f1 = self._real_existing_file("a.json")
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        self.assertFalse(plan["safety_flags"]["atomic_writer_called"])
        # 모든 safety flag 가 False 여야 함
        for value in plan["safety_flags"].values():
            self.assertFalse(value)

    # 13. runtime/*.json 실제 변경 없음 확인
    def test_no_runtime_json_change(self):
        f1 = self._real_existing_file("a.json")
        before = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[f1],
        )
        after = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        self.assertEqual(before, after)
        self.assertEqual(plan["backup_status"], STATUS_READY)


if __name__ == "__main__":
    unittest.main()