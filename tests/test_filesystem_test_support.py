from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from tests.filesystem_test_support import TemporaryProjectRoot, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FilesystemTestSupportTests(unittest.TestCase):
    def test_temp_layout_allows_mutation_and_cleans_itself(self) -> None:
        with TemporaryProjectRoot() as layout:
            root = layout.root
            target = write_json(
                layout.runtime / "order_queue.json",
                {"orders": []},
            )
            self.assertTrue(target.exists())
            self.assertTrue(layout.stocks.is_dir())
            self.assertTrue(layout.routines.is_dir())
        self.assertFalse(root.exists())

    def test_real_runtime_stock_and_routine_mutation_is_blocked(self) -> None:
        probes = (
            PROJECT_ROOT / "runtime" / "__g2_2_guard_probe__.json",
            PROJECT_ROOT / "stocks" / "__g2_2_guard_probe__" / "config.json",
        )
        for target in probes:
            with self.subTest(target=target):
                with self.assertRaisesRegex(AssertionError, "Production mutable"):
                    target.write_text("{}", encoding="utf-8")
                self.assertFalse(target.exists())

        with self.assertRaisesRegex(AssertionError, "Production mutable"):
            shutil.rmtree(PROJECT_ROOT / "routines")
        self.assertTrue((PROJECT_ROOT / "routines").is_dir())

    def test_guard_is_active_without_importing_test_000_first(self) -> None:
        source = (
            "from pathlib import Path; "
            "import tests.test_broker_dispatch_preview; "
            f"target=Path({str(PROJECT_ROOT)!r})/'runtime'/'__order_probe__.json'; "
            "\ntry:\n target.write_text('{}',encoding='utf-8')\n"
            "except AssertionError:\n pass\n"
            "else:\n raise AssertionError('guard was not installed')\n"
            "assert not target.exists()"
        )
        environment = os.environ.copy()
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            result.returncode,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
