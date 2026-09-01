from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests.qt_test_support import QCORE_ONLY_TEST_MODULES


ROOT = Path(__file__).resolve().parents[1]


class QtTestSupportTest(unittest.TestCase):
    def run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.setdefault("QT_QPA_PLATFORM", "offscreen")
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_process_passed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            0,
            result.returncode,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_qcore_only_inventory_is_explicit(self) -> None:
        self.assertEqual(7, len(QCORE_ONLY_TEST_MODULES))
        self.assertTrue(all(name.startswith("tests.test_") for name in QCORE_ONLY_TEST_MODULES))

    def test_qapplication_helper_keeps_one_process_instance(self) -> None:
        result = self.run_python(
            "from tests.qt_test_support import ensure_qapplication; "
            "first=ensure_qapplication(); second=ensure_qapplication(); "
            "assert first is second"
        )
        self.assert_process_passed(result)

    def test_existing_qcore_is_rejected_without_constructing_widgets(self) -> None:
        result = self.run_python(
            "from PyQt5.QtCore import QCoreApplication; "
            "core=QCoreApplication([]); "
            "from tests.qt_test_support import "
            "QtApplicationTypeConflict,ensure_qapplication; "
            "\ntry:\n ensure_qapplication()\nexcept QtApplicationTypeConflict:\n pass\n"
            "else:\n raise AssertionError('QCore/QApplication conflict was not rejected')"
        )
        self.assert_process_passed(result)

    def test_real_qttest_import_is_available_in_clean_process(self) -> None:
        result = self.run_python("from PyQt5 import QtTest; assert QtTest.QTest is not None")
        self.assert_process_passed(result)

    def test_shared_gui_tests_do_not_install_pyqt_stubs(self) -> None:
        paths = (
            "test_gui_execution_preview_button.py",
            "test_gui_indicator_follow_buy_composite_ui_state.py",
            "test_gui_indicator_follow_rule_approval_preview.py",
            "test_indicator_follow_buy_expr_restore.py",
        )
        for filename in paths:
            source = (ROOT / "tests" / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn("_install_pyqt5_import_stubs", source)
                self.assertNotIn('sys.modules["PyQt5"]', source)


if __name__ == "__main__":
    unittest.main()
