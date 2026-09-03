# -*- coding: utf-8 -*-

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import unittest

from mock_validation_isolation_guard import assert_mock_dependency_isolation, mock_foundation_module_paths


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "MISSING"
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class MockVirtualExecutionIsolationTest(unittest.TestCase):
    def test_all_mock_modules_remain_dependency_isolated(self):
        result = assert_mock_dependency_isolation(mock_foundation_module_paths(PROJECT_ROOT))
        self.assertTrue(result["ok"])
        self.assertEqual([], result["violations"])

    def test_engine_has_no_broker_or_chejan_import_or_call(self):
        path = PROJECT_ROOT / "mock_validation_virtual_execution.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(str(node.module or ""))
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        self.assertFalse(any("kiwoom" in name.lower() or "chejan" in name.lower() for name in names))
        self.assertNotIn("SendOrder", calls)
        self.assertNotIn("CommRqData", calls)

    def test_importing_engine_does_not_mutate_production_trees(self):
        roots = [PROJECT_ROOT / name for name in ("runtime", "stocks", "routine_instances", "performance_ledger")]
        before = {str(path): _tree_hash(path) for path in roots}
        __import__("mock_validation_virtual_execution")
        after = {str(path): _tree_hash(path) for path in roots}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
