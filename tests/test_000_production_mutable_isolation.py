from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

try:
    from tests.production_mutable_guard import install
except ImportError:  # bare ``unittest discover -s tests`` import mode
    from production_mutable_guard import install

install()

import event_journal_production


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProductionMutableIsolationTest(unittest.TestCase):
    def test_temp_fixture_write_is_allowed(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "runtime" / "operation_state.json"
            target.parent.mkdir(parents=True)
            target.write_text("{}", encoding="utf-8")
            self.assertEqual("{}", target.read_text(encoding="utf-8"))

    def test_production_changelog_write_is_blocked_before_mutation(self) -> None:
        target = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
        before = target.read_bytes()

        with self.assertRaisesRegex(AssertionError, "Production mutable"):
            with target.open("a", encoding="utf-8") as stream:
                stream.write("test pollution\n")

        self.assertEqual(before, target.read_bytes())

    def test_production_stock_write_is_blocked_before_mutation(self) -> None:
        target = PROJECT_ROOT / "stocks" / "005930_삼성전자" / "config.json"
        before = target.read_bytes()

        with self.assertRaisesRegex(AssertionError, "Production mutable"):
            target.write_text("{}", encoding="utf-8")

        self.assertEqual(before, target.read_bytes())

    def test_atomic_replace_into_production_runtime_is_blocked(self) -> None:
        with TemporaryDirectory() as temp:
            source = Path(temp) / "operation_state.json.tmp"
            source.write_text("{}", encoding="utf-8")
            target = PROJECT_ROOT / "runtime" / "operation_state.json"
            before = target.read_bytes()

            with self.assertRaisesRegex(AssertionError, "Production mutable"):
                os.replace(source, target)

            self.assertEqual(before, target.read_bytes())

    def test_production_event_writer_uses_test_temp_directory(self) -> None:
        journal_dir = event_journal_production._WRITER.journal_dir.resolve()
        with self.assertRaises(ValueError):
            journal_dir.relative_to(PROJECT_ROOT.resolve())


if __name__ == "__main__":
    unittest.main()
