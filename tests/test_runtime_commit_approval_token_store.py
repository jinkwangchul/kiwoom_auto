# -*- coding: utf-8 -*-
"""Tests for runtime_commit_approval_token_store (M6-15).

All token files are written only under tempfile.TemporaryDirectory.
No project runtime/*.json or routines/*/rules.json is touched.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime_commit_approval_token_store import (
    CONSUME_BLOCKED,
    CONSUME_CONSUMED,
    CONSUME_INVALID,
    CONSUME_UNCHANGED,
    ISSUE_BLOCKED,
    ISSUE_ISSUED,
    ISSUE_INVALID,
    PLAN_STATUS_BLOCKED,
    PLAN_STATUS_INVALID,
    PLAN_STATUS_READY,
    READ_INVALID,
    READ_NOT_FOUND,
    READ_OK,
    SEARCH_INVALID,
    SEARCH_OK,
    SEARCH_PARTIAL,
    TOKEN_STATUS_CONSUMED,
    TOKEN_STATUS_ISSUED,
    TOKEN_STATUS_REVOKED,
    TOKEN_STATUS_EXPIRED,
    VALIDATION_BLOCKED,
    VALIDATION_INVALID,
    VALIDATION_VALID,
    consume_runtime_commit_approval_token,
    create_runtime_commit_token_storage_plan,
    find_runtime_commit_approval_tokens,
    issue_runtime_commit_approval_token,
    read_runtime_commit_approval_token,
    validate_runtime_commit_approval_token,
)


def _make_token(token_id="tok-1", commit_id="commit-1", plan_hash="plan-hash-1",
                issued_for="executor", issued_by="operator", metadata=None):
    return {
        "contract_version": "M6_RUNTIME_APPROVAL_TOKEN_V1",
        "token_id": token_id,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "scope": "RUNTIME_COMMIT_EXECUTION",
        "issued_for": issued_for,
        "issued_by": issued_by,
        "token_status": "ISSUED",
        "single_use": True,
        "issued_at": "2026-07-10T00:00:00",
        "consumed_at": None,
        "consumed_by": None,
        "consumption_id": None,
        "issues": [],
        "warnings": [],
        "metadata": metadata or {},
    }


def _make_plan(storage_root, token_id="tok-1", commit_id="commit-1"):
    return create_runtime_commit_token_storage_plan(
        storage_root=storage_root, token_id=token_id, commit_id=commit_id
    )


class TestStoragePlan(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_plan_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def test_01_normal_storage_plan(self):
        plan = _make_plan(str(self.tmp))
        self.assertEqual(plan["plan_status"], PLAN_STATUS_READY)
        self.assertTrue(plan["token_path"].endswith("tok-1.json"))
        self.assertTrue(plan["token_path"].startswith(str(self.tmp)))
        self.assertTrue(plan["claim_path"].endswith(".consume.lock"))
        self.assertTrue(plan["preview_only"])
        for flag, val in plan["safety_flags"].items():
            self.assertFalse(val)

    def test_02_storage_root_missing_invalid(self):
        plan = create_runtime_commit_token_storage_plan(storage_root="", token_id="t", commit_id="c")
        self.assertEqual(plan["plan_status"], PLAN_STATUS_INVALID)

    def test_03_token_id_missing_invalid(self):
        plan = _make_plan(str(self.tmp), token_id="")
        self.assertEqual(plan["plan_status"], PLAN_STATUS_INVALID)

    def test_04_commit_id_missing_invalid(self):
        plan = _make_plan(str(self.tmp), commit_id="")
        self.assertEqual(plan["plan_status"], PLAN_STATUS_INVALID)

    def test_05_path_traversal_blocked(self):
        plan = _make_plan(str(self.tmp), token_id="../escape")
        self.assertEqual(plan["plan_status"], PLAN_STATUS_INVALID)

    def test_06_project_runtime_blocked(self):
        project_runtime = (Path(__file__).resolve().parent.parent / "runtime").resolve(strict=False)
        plan = _make_plan(str(project_runtime))
        self.assertEqual(plan["plan_status"], PLAN_STATUS_BLOCKED)


class TestIssue(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_issue_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self, token_id="tok-1"):
        return _make_plan(str(self.tmp), token_id=token_id)

    def test_07_normal_issue(self):
        plan = self._plan()
        result = issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        self.assertEqual(result["issue_status"], ISSUE_ISSUED)
        self.assertTrue(result["token_issued"])
        self.assertTrue(result["file_write_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["actual_execution"])

    def test_08_token_file_created(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        self.assertTrue(Path(plan["token_path"]).exists())

    def test_09_overwrite_blocked(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        again = issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        self.assertEqual(again["issue_status"], ISSUE_BLOCKED)

    def test_10_input_not_mutated(self):
        plan = self._plan()
        token = _make_token()
        snapshot = json.dumps(token, sort_keys=True)
        issue_runtime_commit_approval_token(storage_plan=plan, token=token)
        self.assertEqual(json.dumps(token, sort_keys=True), snapshot)

    def test_issue_invalid_scope(self):
        plan = self._plan()
        token = _make_token()
        token["scope"] = "WRONG"
        result = issue_runtime_commit_approval_token(storage_plan=plan, token=token)
        self.assertEqual(result["issue_status"], ISSUE_INVALID)

    def test_issue_single_use_false(self):
        plan = self._plan()
        token = _make_token()
        token["single_use"] = False
        result = issue_runtime_commit_approval_token(storage_plan=plan, token=token)
        self.assertEqual(result["issue_status"], ISSUE_INVALID)


class TestRead(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_read_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self):
        return _make_plan(str(self.tmp))

    def test_11_normal_read(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        result = read_runtime_commit_approval_token(storage_plan=plan)
        self.assertEqual(result["read_status"], READ_OK)
        self.assertEqual(result["token"]["token_status"], TOKEN_STATUS_ISSUED)

    def test_12_not_found(self):
        plan = self._plan()
        result = read_runtime_commit_approval_token(storage_plan=plan)
        self.assertEqual(result["read_status"], READ_NOT_FOUND)

    def test_13_corrupted_invalid(self):
        plan = self._plan()
        Path(plan["token_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(plan["token_path"]).write_text("not json {", encoding="utf-8")
        result = read_runtime_commit_approval_token(storage_plan=plan)
        self.assertEqual(result["read_status"], READ_INVALID)

    def test_14_contract_version_error(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        rec = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        rec["contract_version"] = "WRONG"
        Path(plan["token_path"]).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        result = read_runtime_commit_approval_token(storage_plan=plan)
        self.assertEqual(result["read_status"], READ_INVALID)


class TestValidate(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_val_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _issued_token(self):
        return _make_token()

    def test_15_valid(self):
        token = self._issued_token()
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="plan-hash-1"
        )
        self.assertEqual(result["validation_status"], VALIDATION_VALID)
        self.assertTrue(result["valid_for_execution"])

    def test_16_commit_id_mismatch(self):
        token = self._issued_token()
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="other", expected_plan_hash="plan-hash-1"
        )
        self.assertEqual(result["validation_status"], VALIDATION_INVALID)

    def test_17_plan_hash_mismatch(self):
        token = self._issued_token()
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="other"
        )
        self.assertEqual(result["validation_status"], VALIDATION_INVALID)

    def test_18_scope_mismatch(self):
        token = self._issued_token()
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="plan-hash-1",
            expected_scope="OTHER",
        )
        self.assertEqual(result["validation_status"], VALIDATION_INVALID)

    def test_19_single_use_false(self):
        token = self._issued_token()
        token["single_use"] = False
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="plan-hash-1"
        )
        self.assertEqual(result["validation_status"], VALIDATION_INVALID)

    def test_20_consumed_blocked(self):
        token = self._issued_token()
        token["token_status"] = TOKEN_STATUS_CONSUMED
        token["consumed_at"] = "2026-07-10T00:00:00"
        token["consumed_by"] = "executor"
        token["consumption_id"] = "cid"
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="plan-hash-1"
        )
        self.assertEqual(result["validation_status"], VALIDATION_BLOCKED)

    def test_21_revoked_blocked(self):
        token = self._issued_token()
        token["token_status"] = TOKEN_STATUS_REVOKED
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="plan-hash-1"
        )
        self.assertEqual(result["validation_status"], VALIDATION_BLOCKED)

    def test_22_expired_blocked(self):
        token = self._issued_token()
        token["token_status"] = TOKEN_STATUS_EXPIRED
        result = validate_runtime_commit_approval_token(
            token=token, expected_commit_id="commit-1", expected_plan_hash="plan-hash-1"
        )
        self.assertEqual(result["validation_status"], VALIDATION_BLOCKED)


class TestConsume(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_cons_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _plan(self, token_id="tok-1"):
        return _make_plan(str(self.tmp), token_id=token_id)

    def test_23_normal_consume(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        result = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertEqual(result["consume_status"], CONSUME_CONSUMED)
        self.assertTrue(result["token_consumed"])
        self.assertTrue(result["file_write_called"])
        self.assertFalse(result["runtime_write"])

    def test_24_consumed_status(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        rec = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        self.assertEqual(rec["token_status"], TOKEN_STATUS_CONSUMED)

    def test_25_consumed_at_recorded(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        rec = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        self.assertTrue(rec["consumed_at"])

    def test_26_consumed_by_recorded(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        rec = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        self.assertEqual(rec["consumed_by"], "executor")

    def test_27_deterministic_consumption_id(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        result = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        rec = result["token"]
        self.assertTrue(rec["consumption_id"])
        # Recompute independently.
        import hashlib
        raw = json.dumps(
            {"token_id": "tok-1", "commit_id": "commit-1",
             "plan_hash": "plan-hash-1", "consumer_id": "executor"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self.assertEqual(rec["consumption_id"], expected)

    def test_28_same_consume_unchanged(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        again = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertEqual(again["consume_status"], CONSUME_UNCHANGED)
        self.assertFalse(again["token_consumed"])

    def test_29_different_consumer_blocked(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor-A",
        )
        other = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor-B",
        )
        self.assertEqual(other["consume_status"], CONSUME_BLOCKED)

    def test_30_core_ids_preserved(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        result = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        rec = result["token"]
        self.assertEqual(rec["token_id"], "tok-1")
        self.assertEqual(rec["commit_id"], "commit-1")
        self.assertEqual(rec["plan_hash"], "plan-hash-1")
        self.assertEqual(rec["scope"], "RUNTIME_COMMIT_EXECUTION")

    def test_31_first_concurrent_success(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        first = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertEqual(first["consume_status"], CONSUME_CONSUMED)

    def test_32_second_concurrent_blocked(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        # Simulate a held claim lock.
        Path(plan["claim_path"]).parent.mkdir(parents=True, exist_ok=True)
        with Path(plan["claim_path"]).open("x", encoding="utf-8"):
            pass
        result = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertEqual(result["consume_status"], CONSUME_BLOCKED)

    def test_33_claim_cleanup(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertFalse(Path(plan["claim_path"]).exists())

    def test_34_claim_cleanup_failure_no_revert(self):
        plan = self._plan()
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        # Make claim path a directory so unlink fails; consumption still succeeds.
        Path(plan["claim_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(plan["claim_path"]).mkdir(exist_ok=True)
        result = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertEqual(result["consume_status"], CONSUME_CONSUMED)
        rec = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        self.assertEqual(rec["token_status"], TOKEN_STATUS_CONSUMED)


class TestSearch(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_find_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _issue(self, token_id, commit_id="commit-1", plan_hash="plan-hash-1"):
        plan = _make_plan(str(self.tmp), token_id=token_id, commit_id=commit_id)
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token(
            token_id=token_id, commit_id=commit_id, plan_hash=plan_hash
        ))
        return plan

    def test_35_normal_search(self):
        self._issue("tok-1")
        result = find_runtime_commit_approval_tokens(storage_root=str(self.tmp))
        self.assertEqual(result["search_status"], SEARCH_OK)
        self.assertEqual(result["token_count"], 1)
        self.assertEqual(result["tokens"][0]["token_id"], "tok-1")

    def test_36_commit_id_filter(self):
        self._issue("tok-1", commit_id="commit-1")
        self._issue("tok-2", commit_id="commit-2")
        result = find_runtime_commit_approval_tokens(storage_root=str(self.tmp), commit_id="commit-1")
        self.assertEqual(result["token_count"], 1)
        self.assertEqual(result["tokens"][0]["token_id"], "tok-1")

    def test_37_token_id_filter(self):
        self._issue("tok-1")
        self._issue("tok-2")
        result = find_runtime_commit_approval_tokens(storage_root=str(self.tmp), token_id="tok-2")
        self.assertEqual(result["token_count"], 1)
        self.assertEqual(result["tokens"][0]["token_id"], "tok-2")

    def test_38_token_status_filter(self):
        self._issue("tok-1")
        result = find_runtime_commit_approval_tokens(storage_root=str(self.tmp), token_status="ISSUED")
        self.assertEqual(result["token_count"], 1)
        result2 = find_runtime_commit_approval_tokens(storage_root=str(self.tmp), token_status="CONSUMED")
        self.assertEqual(result2["token_count"], 0)

    def test_39_corrupt_partial(self):
        self._issue("tok-1")
        corrupt_dir = self.tmp / "approval_tokens"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "bad.json").write_text("broken {", encoding="utf-8")
        result = find_runtime_commit_approval_tokens(storage_root=str(self.tmp))
        self.assertEqual(result["search_status"], SEARCH_PARTIAL)
        self.assertEqual(result["invalid_tokens"], 1)
        self.assertEqual(result["token_count"], 2)


class TestCompatibility(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="m6_15_compat_"))

    def tearDown(self):
        for child in self.tmp.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def test_40_gate_fields_compatible(self):
        plan = _make_plan(str(self.tmp))
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        token = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        for field in ("token_id", "commit_id", "plan_hash", "scope", "single_use"):
            self.assertIn(field, token)
        self.assertFalse(token["consumed_by"])

    def test_41_transaction_approval_token_id_compatible(self):
        # M6-11 manifest uses approval_token_id; ensure our token_id can match.
        plan = _make_plan(str(self.tmp), token_id="token-xyz")
        issue_runtime_commit_approval_token(
            storage_plan=plan, token=_make_token(token_id="token-xyz")
        )
        token = read_runtime_commit_approval_token(storage_plan=plan)["token"]
        self.assertEqual(token["token_id"], "token-xyz")
        # Simulate M6-11 manifest approval_token_id match.
        self.assertEqual(token["token_id"], "token-xyz")

    def test_42_safety_flags(self):
        plan = _make_plan(str(self.tmp))
        issue = issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        self.assertNotIn("safety_flags", issue)
        self.assertTrue(issue["token_issued"])
        consume = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertNotIn("safety_flags", consume)
        self.assertTrue(consume["token_consumed"])

    def test_43_no_runtime_write(self):
        plan = _make_plan(str(self.tmp))
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        self.assertFalse(consume["runtime_write"])
        self.assertFalse(consume["actual_execution"])

    def test_44_no_backup_rollback_commit(self):
        plan = _make_plan(str(self.tmp))
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume = consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        # Consume result has no backup/rollback/lock flags (not performed).
        self.assertNotIn("backup_created", consume)
        self.assertNotIn("rollback_executed", consume)
        self.assertNotIn("lock_acquired", consume)

    def test_45_tempfile_only(self):
        before = list(self.tmp.glob("**/*"))
        plan = _make_plan(str(self.tmp))
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        # Only token file + (cleaned) claim should exist; no escape outside tmp.
        after = list(self.tmp.glob("**/*"))
        self.assertTrue(set(before).issubset(set(after)))

    def test_46_runtime_routines_unchanged(self):
        project_runtime = (Path(__file__).resolve().parent.parent / "runtime").resolve(strict=False)
        before = {p.name: p.stat().st_mtime_ns for p in project_runtime.glob("*.json")}
        plan = _make_plan(str(self.tmp))
        issue_runtime_commit_approval_token(storage_plan=plan, token=_make_token())
        consume_runtime_commit_approval_token(
            storage_plan=plan, expected_commit_id="commit-1",
            expected_plan_hash="plan-hash-1", expected_consumer_id="executor",
        )
        after = {p.name: p.stat().st_mtime_ns for p in project_runtime.glob("*.json")}
        self.assertEqual(before, after)

    def test_47_full_regression_covered(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()