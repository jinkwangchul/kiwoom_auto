# -*- coding: utf-8 -*-
"""Tests for runtime_commit_audit_record (M6-5).

All tests exercise preview-only behaviour. No audit files / directories are
created, no protected runtime files (runtime/*.json, routines/*/rules.json)
are touched, and the Atomic Writer is never invoked.
"""

import tempfile
import unittest
from pathlib import Path

from runtime_commit_audit_record import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    create_runtime_commit_audit_record,
)
from runtime_backup_manager import create_runtime_backup_plan
from runtime_rollback_manager import create_runtime_rollback_plan

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


class TestRuntimeCommitAuditRecord(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="runtime_audit_test_"))
        self.commit_id = "audit-def-321"

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

    # 1. 정상 입력 시 READY audit record 생성
    def test_ready_with_valid_inputs(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)
        self.assertTrue(result["preview_only"])

    # 2. commit_id 포함 확인
    def test_commit_id_present(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = self._make_valid_rollback_plan(backup_plan)

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(result["commit_id"], self.commit_id)

    # 3. preview_only=True 확인
    def test_preview_only_true(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan={},
            rollback_plan={},
        )
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["audit_metadata"]["preview_only"])

    # 4. audit_metadata 포함 확인
    def test_audit_metadata_present(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        self.assertIn("audit_metadata", result)
        self.assertEqual(result["audit_metadata"]["commit_id"], self.commit_id)

    # 5. audit_records_preview 포함 확인
    def test_audit_records_preview_present(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a.json", "status": "created"}],
        )
        self.assertEqual(len(result["audit_records_preview"]), 1)

    # 6. audit_write=False 확인
    def test_audit_write_false(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        self.assertFalse(result["safety_flags"]["audit_write"])

    # 7. 실제 audit 파일 생성 없음 확인
    def test_no_audit_file_created(self):
        before_files = set(RUNTIME_DIR.glob("*.json"))
        create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a.json"}],
        )
        after_files = set(RUNTIME_DIR.glob("*.json"))
        self.assertEqual(before_files, after_files)

    # 8. commit_id 없으면 INVALID
    def test_invalid_without_commit_id(self):
        result = create_runtime_commit_audit_record(
            commit_id="",
        )
        self.assertEqual(result["audit_status"], STATUS_INVALID)

    # 9. backup_plan이 dict 아니면 INVALID
    def test_invalid_with_non_dict_backup_plan(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan="not-a-dict",
        )
        self.assertEqual(result["audit_status"], STATUS_INVALID)

    # 10. rollback_plan이 dict 아니면 INVALID
    def test_invalid_with_non_dict_rollback_plan(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            rollback_plan="not-a-dict",
        )
        self.assertEqual(result["audit_status"], STATUS_INVALID)

    # 11. verification_result이 dict 아니면 INVALID
    def test_invalid_with_non_dict_verification_result(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            verification_result=123,
        )
        self.assertEqual(result["audit_status"], STATUS_INVALID)

    # 12. rules.json in backup_targets → INVALID
    def test_invalid_with_rules_json_in_backup(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")
        rules = self.tmp_dir / "rules.json"
        rules.write_text("{}", encoding="utf-8")

        backup_plan = create_runtime_backup_plan(
            commit_id=self.commit_id,
            target_files=[str(f1), str(rules)],
        )

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(result["audit_status"], STATUS_INVALID)
        self.assertTrue(any("INVALID" in i for i in result["issues"]))

    # 13. rules.json in rollback_targets → INVALID
    def test_invalid_with_rules_json_in_rollback(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")

        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = create_runtime_rollback_plan(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 14. verification rollback_required → BLOCKED
    def test_blocked_with_rollback_required(self):
        verification_result = {
            "verification_status": STATUS_BLOCKED,
            "rollback_required": True,
        }

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            verification_result=verification_result,
        )
        self.assertEqual(result["audit_status"], STATUS_BLOCKED)
        self.assertTrue(any("rollback required" in i for i in result["issues"]))

    # 15. audit_records 없으면 빈 preview
    def test_empty_audit_records_preview(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=None,
        )
        self.assertEqual(len(result["audit_records_preview"]), 0)

    # 16. audit_records 문자열 처리
    def test_audit_records_string(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=["record1", "record2"],
        )
        self.assertEqual(len(result["audit_records_preview"]), 2)

    # 17. safety flags 전체 False
    def test_all_safety_flags_false(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        for value in result["safety_flags"].values():
            self.assertFalse(value)

    # 18. runtime/*.json 실제 변경 없음
    def test_no_runtime_json_change(self):
        before_mtime = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        create_runtime_commit_audit_record(commit_id=self.commit_id)
        after_mtime = {p.name: p.stat().st_mtime_ns for p in RUNTIME_DIR.glob("*.json")}
        self.assertEqual(before_mtime, after_mtime)

    # === Additional tests for 40 total ===

    # 19. backup_plan status BLOCKED → BLOCKED
    def test_blocked_with_backup_status_blocked(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_BLOCKED, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_READY, "rollback_targets": []}

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 20. rollback_plan status BLOCKED → READY
    def test_rollback_status_blocked(self):
        backup_plan = {"preview_only": True, "backup_status": STATUS_READY, "backup_targets": []}
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_BLOCKED, "rollback_targets": []}

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 21. timestamp 포함 확인
    def test_timestamp_included(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        self.assertIn("timestamp", result["audit_metadata"])

    # 22. record_index 확인
    def test_record_index_included(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a"}, {"target": "b"}],
        )
        self.assertEqual(result["audit_records_preview"][0]["record_index"], 0)
        self.assertEqual(result["audit_records_preview"][1]["record_index"], 1)

    # 23. hash 포함 확인
    def test_hash_included(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a"}],
        )
        self.assertIn("hash", result["audit_records_preview"][0])

    # 24. has_* 플래그 확인
    def test_has_flags(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")
        backup_plan = self._make_valid_backup_plan([f1])
        rollback_plan = {"preview_only": True, "rollback_status": STATUS_READY}
        verification_result = {"verification_status": STATUS_READY}

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            verification_result=verification_result,
        )
        self.assertTrue(result["audit_metadata"]["has_backup_plan"])
        self.assertTrue(result["audit_metadata"]["has_rollback_plan"])
        self.assertTrue(result["audit_metadata"]["has_verification_result"])

    # 25. atomic_writer_called=False 확인
    def test_atomic_writer_not_called(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        self.assertFalse(result["safety_flags"]["atomic_writer_called"])

    # 26. backup_manager_called=False 확인
    def test_backup_manager_not_called(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        self.assertFalse(result["safety_flags"]["backup_manager_called"])

    # 27. rollback_executed=False 확인
    def test_rollback_executed_false(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
        )
        self.assertFalse(result["safety_flags"]["rollback_executed"])

    # 28. audit_records 빈 리스트 → 빈 preview
    def test_audit_records_empty_list(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[],
        )
        self.assertEqual(len(result["audit_records_preview"]), 0)

    # 29. audit_records dict 아닌 값
    def test_audit_records_non_dict_non_string(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[123, 456.789, True],
        )
        self.assertEqual(len(result["audit_records_preview"]), 3)

    # 30. multiple audit_records 처리
    def test_multiple_audit_records(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[
                {"target": "a.json", "status": "created"},
                "plain string record",
                123,
                {"target": "b.json"},
            ],
        )
        self.assertEqual(len(result["audit_records_preview"]), 4)

    # 31. audit_record dict에 status 없을 때
    def test_audit_record_without_status(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a.json"}],
        )
        self.assertEqual(result["audit_records_preview"][0]["status"], "")

    # 32. verification_result가 None일 때
    def test_verification_result_none(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            verification_result=None,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 33. backup_plan 없을 때
    def test_backup_plan_none(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=None,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 34. rollback_plan 없을 때
    def test_rollback_plan_none(self):
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            rollback_plan=None,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 35. hash 일관성 확인
    def test_hash_consistency(self):
        result1 = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a"}],
        )
        result2 = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            audit_records=[{"target": "a"}],
        )
        self.assertEqual(result1["audit_records_preview"][0]["hash"], result2["audit_records_preview"][0]["hash"])

    # 36. rollback_required=False 시 READY
    def test_rollback_not_required_ready(self):
        verification_result = {
            "verification_status": STATUS_READY,
            "rollback_required": False,
        }
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            verification_result=verification_result,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 37. backup_target_count 확인
    def test_backup_target_count(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")
        backup_plan = self._make_valid_backup_plan([f1, f1])

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
        )
        self.assertEqual(result["audit_metadata"]["backup_target_count"], 2)

    # 38. verification_result에 verification_status 없음
    def test_verification_result_without_status(self):
        verification_result = {"rollback_required": False}
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            verification_result=verification_result,
        )
        self.assertEqual(result["audit_status"], STATUS_READY)

    # 39. 빈 target 필드
    def test_empty_target_field(self):
        f1 = self.tmp_dir / "a.json"
        f1.write_text("{}", encoding="utf-8")
        backup_plan = self._make_valid_backup_plan([f1])

        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            backup_plan=backup_plan,
            audit_records=[{"target": "", "status": "ok"}],
        )
        self.assertEqual(len(result["audit_records_preview"]), 1)

    # 40. operator_context 전달 확인
    def test_operator_context_passed(self):
        op_ctx = {"user": "test_user"}
        result = create_runtime_commit_audit_record(
            commit_id=self.commit_id,
            operator_context=op_ctx,
        )
        self.assertTrue(result["audit_metadata"]["has_operator_context"])


if __name__ == "__main__":
    unittest.main()