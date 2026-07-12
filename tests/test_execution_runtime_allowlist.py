from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_allowlist import (
    OPERATION_WRITE,
    RuntimeAllowlistEntry,
    get_runtime_allowlist_entry,
    is_runtime_target_allowed,
    resolve_runtime_target,
    validate_runtime_target,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeAllowlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp.name) / "runtime"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_registered_logical_target_resolve_allowed(self) -> None:
        decision = validate_runtime_target("order_executions", runtime_root=self.runtime_root)

        self.assertEqual("ALLOWED", decision.status)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.registered)
        self.assertEqual("order_executions.json", decision.relative_path)
        self.assertEqual(str((self.runtime_root / "order_executions.json").resolve(strict=False)), decision.resolved_path)
        self.assertFalse(decision.runtime_write)

    def test_unregistered_logical_target_blocked(self) -> None:
        decision = validate_runtime_target("order_locks", runtime_root=self.runtime_root)

        self.assertEqual("BLOCKED", decision.status)
        self.assertFalse(decision.allowed)
        self.assertEqual("UNREGISTERED_LOGICAL_TARGET", decision.blocked_reason)

    def test_logical_target_case_variants_are_blocked(self) -> None:
        for target in ("ORDER_EXECUTIONS", "Order_Executions", "order_Executions"):
            with self.subTest(target=target):
                decision = validate_runtime_target(target, runtime_root=self.runtime_root)

                self.assertEqual("BLOCKED", decision.status)
                self.assertFalse(decision.allowed)
                self.assertEqual("UNREGISTERED_LOGICAL_TARGET", decision.blocked_reason)

    def test_logical_target_whitespace_bypass_blocked(self) -> None:
        for target in (" order_executions", "order_executions ", "\torder_executions\t"):
            with self.subTest(target=target):
                decision = validate_runtime_target(target, runtime_root=self.runtime_root)

                self.assertEqual("INVALID", decision.status)
                self.assertFalse(decision.allowed)
                self.assertEqual("LOGICAL_TARGET_WHITESPACE", decision.blocked_reason)

    def test_empty_target_blocked(self) -> None:
        decision = validate_runtime_target("", runtime_root=self.runtime_root)

        self.assertEqual("INVALID", decision.status)
        self.assertFalse(decision.allowed)
        self.assertEqual("MISSING_LOGICAL_TARGET", decision.blocked_reason)

    def test_malformed_target_path_blocked(self) -> None:
        for target in (
            "runtime/order_executions.json",
            r"runtime\order_executions.json",
            r"C:\runtime\order_executions.json",
            r"\\server\share\order_executions.json",
            r"\\?\C:\runtime\order_executions.json",
        ):
            with self.subTest(target=target):
                decision = validate_runtime_target(target, runtime_root=self.runtime_root)
                self.assertEqual("INVALID", decision.status)
                self.assertFalse(decision.allowed)

    def test_traversal_logical_target_blocked(self) -> None:
        decision = validate_runtime_target("..", runtime_root=self.runtime_root)

        self.assertEqual("INVALID", decision.status)
        self.assertFalse(decision.allowed)
        self.assertEqual("LOGICAL_TARGET_TRAVERSAL_BLOCKED", decision.blocked_reason)

    def test_absolute_allowlist_path_blocked(self) -> None:
        registry = {
            "bad": RuntimeAllowlistEntry(
                logical_target="bad",
                relative_path=str((self.runtime_root / "bad.json").resolve(strict=False)),
                file_name="bad.json",
            )
        }

        decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

        self.assertEqual("INVALID", decision.status)
        self.assertFalse(decision.allowed)
        self.assertEqual("ALLOWLIST_RELATIVE_PATH_ABSOLUTE_BLOCKED", decision.blocked_reason)

    def test_windows_absolute_and_unc_allowlist_paths_blocked(self) -> None:
        for relative_path in (
            r"C:\\runtime\\order_executions.json",
            r"\\\\server\\share\\order_executions.json",
            r"\\?\C:\runtime\order_executions.json",
        ):
            with self.subTest(relative_path=relative_path):
                registry = {
                    "bad": RuntimeAllowlistEntry(
                        logical_target="bad",
                        relative_path=relative_path,
                        file_name="order_executions.json",
                    )
                }

                decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

                self.assertEqual("INVALID", decision.status)
                self.assertFalse(decision.allowed)
                self.assertEqual("ALLOWLIST_RELATIVE_PATH_ABSOLUTE_BLOCKED", decision.blocked_reason)

    def test_windows_reserved_device_allowlist_paths_blocked(self) -> None:
        for relative_path in ("CON", "NUL.json", "aux.txt", "runtime/PRN"):
            with self.subTest(relative_path=relative_path):
                registry = {
                    "bad": RuntimeAllowlistEntry(
                        logical_target="bad",
                        relative_path=relative_path,
                        file_name=Path(relative_path).name,
                    )
                }

                decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

                self.assertEqual("INVALID", decision.status)
                self.assertFalse(decision.allowed)
                self.assertEqual("ALLOWLIST_RELATIVE_PATH_RESERVED_DEVICE_BLOCKED", decision.blocked_reason)

    def test_allowlist_traversal_path_blocked(self) -> None:
        registry = {
            "bad": RuntimeAllowlistEntry(
                logical_target="bad",
                relative_path="../order_executions.json",
                file_name="order_executions.json",
            )
        }

        decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

        self.assertEqual("INVALID", decision.status)
        self.assertFalse(decision.allowed)
        self.assertEqual("ALLOWLIST_RELATIVE_PATH_TRAVERSAL_BLOCKED", decision.blocked_reason)

    def test_runtime_root_escape_blocked(self) -> None:
        registry = {
            "bad": RuntimeAllowlistEntry(
                logical_target="bad",
                relative_path="nested/../../escape.json",
                file_name="escape.json",
            )
        }

        decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

        self.assertEqual("INVALID", decision.status)
        self.assertFalse(decision.allowed)

    def test_mixed_separator_allowlist_traversal_blocked(self) -> None:
        for relative_path in (r"nested\\..\\../escape.json", r"nested/..\\../escape.json"):
            with self.subTest(relative_path=relative_path):
                registry = {
                    "bad": RuntimeAllowlistEntry(
                        logical_target="bad",
                        relative_path=relative_path,
                        file_name="escape.json",
                    )
                }

                decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

                self.assertEqual("INVALID", decision.status)
                self.assertFalse(decision.allowed)
                self.assertEqual("ALLOWLIST_RELATIVE_PATH_TRAVERSAL_BLOCKED", decision.blocked_reason)

    def test_similar_file_name_bypass_blocked(self) -> None:
        registry = {
            "bad": RuntimeAllowlistEntry(
                logical_target="bad",
                relative_path="order_executions.json.bak",
                file_name="order_executions.json",
            )
        }

        decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

        self.assertEqual("BLOCKED", decision.status)
        self.assertFalse(decision.allowed)
        self.assertIn("FILE_NAME_MISMATCH", decision.blocked_reason)

    def test_subdirectory_bypass_blocked_by_file_name(self) -> None:
        registry = {
            "bad": RuntimeAllowlistEntry(
                logical_target="bad",
                relative_path="archive/order_executions.json",
                file_name="archive.json",
            )
        }

        decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

        self.assertEqual("BLOCKED", decision.status)
        self.assertFalse(decision.allowed)
        self.assertIn("FILE_NAME_MISMATCH", decision.blocked_reason)

    def test_subpath_suffix_bypass_blocked_by_file_name(self) -> None:
        registry = {
            "bad": RuntimeAllowlistEntry(
                logical_target="bad",
                relative_path="order_executions.json/backup.json",
                file_name="order_executions.json",
            )
        }

        decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

        self.assertEqual("BLOCKED", decision.status)
        self.assertFalse(decision.allowed)
        self.assertIn("FILE_NAME_MISMATCH", decision.blocked_reason)

    def test_windows_alias_forms_in_allowlist_path_blocked(self) -> None:
        cases = {
            "trailing_dot": ("order_executions.json.", "order_executions.json."),
            "trailing_space": ("order_executions.json ", "order_executions.json "),
            "alternate_data_stream": ("order_executions.json::$DATA", "order_executions.json::$DATA"),
        }
        for name, (relative_path, file_name) in cases.items():
            with self.subTest(name=name):
                registry = {
                    "bad": RuntimeAllowlistEntry(
                        logical_target="bad",
                        relative_path=relative_path,
                        file_name=file_name,
                    )
                }

                decision = validate_runtime_target("bad", runtime_root=self.runtime_root, registry=registry)

                self.assertEqual("INVALID", decision.status)
                self.assertFalse(decision.allowed)
                self.assertTrue(decision.blocked_reason.startswith("ALLOWLIST_RELATIVE_PATH_"))

    def test_windows_separator_registry_path_normalizes(self) -> None:
        registry = {
            "pilot": RuntimeAllowlistEntry(
                logical_target="pilot",
                relative_path=r"pilot\order_executions.json",
                file_name="order_executions.json",
            )
        }

        decision = resolve_runtime_target("pilot", self.runtime_root, registry=registry)

        self.assertTrue(decision["allowed"])
        self.assertIn("/pilot/order_executions.json", decision["normalized_path"])

    def test_write_operation_remains_fail_closed(self) -> None:
        decision = validate_runtime_target("order_executions", runtime_root=self.runtime_root, operation=OPERATION_WRITE)

        self.assertEqual("BLOCKED", decision.status)
        self.assertFalse(decision.allowed)
        self.assertIn("OPERATION_NOT_ALLOWLISTED", decision.blocked_reason)
        self.assertIn("RUNTIME_WRITE_DISABLED", decision.blocked_reason)

    def test_helper_functions_are_stable(self) -> None:
        self.assertIsNotNone(get_runtime_allowlist_entry("order_executions"))
        self.assertTrue(is_runtime_target_allowed("order_executions", runtime_root=self.runtime_root))
        self.assertFalse(is_runtime_target_allowed("order_queue", runtime_root=self.runtime_root))

    def test_no_file_write_mkdir_queue_send_order_or_commit(self) -> None:
        with (
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            decision = validate_runtime_target("order_executions", runtime_root=self.runtime_root)

        self.assertTrue(decision.allowed)
        mkdir.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()

    def test_project_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        validate_runtime_target("order_executions", runtime_root=ROOT / "runtime")
        validate_runtime_target("order_executions", runtime_root=ROOT / "runtime", operation=OPERATION_WRITE)
        validate_runtime_target("order_queue", runtime_root=ROOT / "runtime")

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
