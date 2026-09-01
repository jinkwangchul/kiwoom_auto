from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from stock_repository import (
    STOCK_CONFIG_DELETE_FIELD,
    STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED,
    STOCK_CONFIG_WRITE_CONFIG_NOT_FOUND,
    STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED,
    STOCK_CONFIG_WRITE_FIELD_CONFLICT,
    STOCK_CONFIG_WRITE_INVALID_CONFIG,
    STOCK_CONFIG_WRITE_INVALID_PATCH,
    STOCK_CONFIG_WRITE_NO_CHANGE,
    STOCK_CONFIG_WRITE_OK,
    STOCK_CONFIG_WRITE_READBACK_FAILED,
    STOCK_CONFIG_EXPECTED_MISSING,
    StockRepository,
)


class StockRepositoryConfigPatchTest(unittest.TestCase):
    def _config_path(self, root: Path, config: dict) -> Path:
        stock_dir = root / "stocks" / "005930_삼성전자"
        stock_dir.mkdir(parents=True)
        config_path = stock_dir / "config.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return config_path

    @staticmethod
    def _read_config(config_path: Path) -> dict:
        return json.loads(config_path.read_text(encoding="utf-8"))

    def test_basic_patch_changes_only_requested_field(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})

            result = StockRepository(root).patch_stock_config("005930", {"A": 3})

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(STOCK_CONFIG_WRITE_OK, result.reason_code)
            self.assertTrue(result.read_back_verified)
            self.assertNotEqual(result.before_fingerprint, result.after_fingerprint)
            self.assertEqual({"A": 3, "B": 2}, self._read_config(config_path))

    def test_no_change_skips_atomic_write(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 3, "B": 2})
            repository = StockRepository(root)

            with patch.object(
                repository,
                "_atomic_write_stock_config",
                wraps=repository._atomic_write_stock_config,
            ) as atomic_writer:
                result = repository.patch_stock_config("005930", {"A": 3})

            self.assertTrue(result.ok)
            self.assertFalse(result.changed)
            self.assertEqual(STOCK_CONFIG_WRITE_NO_CHANGE, result.reason_code)
            self.assertEqual(result.before_fingerprint, result.after_fingerprint)
            atomic_writer.assert_not_called()
            self.assertEqual({"A": 3, "B": 2}, self._read_config(config_path))

    def test_unrelated_concurrent_change_is_merged_from_latest_config(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 5})

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"A": 3},
                expected_fields={"A": 1},
            )

            self.assertTrue(result.ok)
            self.assertEqual({"A": 3, "B": 5}, self._read_config(config_path))

    def test_same_field_conflict_preserves_original_file(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 2, "B": 5})
            before = config_path.read_bytes()

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"A": 3},
                expected_fields={"A": 1},
            )

            self.assertFalse(result.ok)
            self.assertFalse(result.changed)
            self.assertTrue(result.conflict_detected)
            self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, result.reason_code)
            self.assertEqual(before, config_path.read_bytes())

    def test_conflict_comparison_preserves_json_value_types(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"enabled": 1})

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"enabled": False},
                expected_fields={"enabled": True},
            )

            self.assertFalse(result.ok)
            self.assertTrue(result.conflict_detected)
            self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, result.reason_code)
            self.assertEqual({"enabled": 1}, self._read_config(config_path))

    def test_expected_missing_field_conflicts_when_another_writer_adds_it(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"buy_amount": 50000})

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"buy_amount": 70000},
                expected_fields={"buy_amount": STOCK_CONFIG_EXPECTED_MISSING},
            )

            self.assertFalse(result.ok)
            self.assertTrue(result.conflict_detected)
            self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, result.reason_code)
            self.assertEqual({"buy_amount": 50000}, self._read_config(config_path))

    def test_same_field_change_during_write_is_retried_then_conflicts(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})
            repository = StockRepository(root)
            original_writer = repository._atomic_write_stock_config
            injected = False

            def change_same_field(*args, **kwargs) -> None:
                nonlocal injected
                if not injected:
                    injected = True
                    config_path.write_text(
                        json.dumps({"A": 2, "B": 2}) + "\n", encoding="utf-8"
                    )
                original_writer(*args, **kwargs)

            with patch.object(
                repository,
                "_atomic_write_stock_config",
                side_effect=change_same_field,
            ):
                result = repository.patch_stock_config(
                    "005930",
                    {"A": 3},
                    expected_fields={"A": 1},
                )

            self.assertFalse(result.ok)
            self.assertTrue(result.conflict_detected)
            self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, result.reason_code)
            self.assertEqual({"A": 2, "B": 2}, self._read_config(config_path))

    def test_unrelated_change_during_write_is_retried_and_merged(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})
            repository = StockRepository(root)
            original_writer = repository._atomic_write_stock_config
            injected = False

            def change_unrelated_field(*args, **kwargs) -> None:
                nonlocal injected
                if not injected:
                    injected = True
                    config_path.write_text(
                        json.dumps({"A": 1, "B": 5}) + "\n", encoding="utf-8"
                    )
                original_writer(*args, **kwargs)

            with patch.object(
                repository,
                "_atomic_write_stock_config",
                side_effect=change_unrelated_field,
            ):
                result = repository.patch_stock_config(
                    "005930",
                    {"A": 3},
                    expected_fields={"A": 1},
                )

            self.assertTrue(result.ok)
            self.assertEqual(STOCK_CONFIG_WRITE_OK, result.reason_code)
            self.assertEqual({"A": 3, "B": 5}, self._read_config(config_path))

    def test_repeated_concurrent_changes_exhaust_retry_without_overwrite(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})
            repository = StockRepository(root)
            original_writer = repository._atomic_write_stock_config
            external_value = 2

            def keep_changing_unrelated_field(*args, **kwargs) -> None:
                nonlocal external_value
                external_value += 1
                config_path.write_text(
                    json.dumps({"A": 1, "B": external_value}) + "\n",
                    encoding="utf-8",
                )
                original_writer(*args, **kwargs)

            with patch.object(
                repository,
                "_atomic_write_stock_config",
                side_effect=keep_changing_unrelated_field,
            ):
                result = repository.patch_stock_config("005930", {"A": 3})

            self.assertFalse(result.ok)
            self.assertFalse(result.changed)
            self.assertTrue(result.conflict_detected)
            self.assertEqual(
                STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED,
                result.reason_code,
            )
            self.assertEqual(
                {"A": 1, "B": external_value}, self._read_config(config_path)
            )

    def test_invalid_json_and_non_dict_documents_fail_closed(self) -> None:
        invalid_documents = ("{broken", "[1, 2, 3]")
        for document in invalid_documents:
            with self.subTest(document=document), TemporaryDirectory() as temp:
                root = Path(temp)
                config_path = self._config_path(root, {})
                config_path.write_text(document, encoding="utf-8")
                before = config_path.read_bytes()

                result = StockRepository(root).patch_stock_config(
                    "005930", {"A": 3}
                )

                self.assertFalse(result.ok)
                self.assertEqual(STOCK_CONFIG_WRITE_INVALID_CONFIG, result.reason_code)
                self.assertEqual(before, config_path.read_bytes())

    def test_missing_config_is_not_created(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)

            result = StockRepository(root).patch_stock_config("005930", {"A": 3})

            self.assertFalse(result.ok)
            self.assertEqual(STOCK_CONFIG_WRITE_CONFIG_NOT_FOUND, result.reason_code)
            self.assertFalse((stock_dir / "config.json").exists())

    def test_invalid_patch_is_rejected_without_mutation(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1})
            before = config_path.read_bytes()

            result = StockRepository(root).patch_stock_config(
                "005930", {"A": object()}
            )

            self.assertFalse(result.ok)
            self.assertEqual(STOCK_CONFIG_WRITE_INVALID_PATCH, result.reason_code)
            self.assertEqual(before, config_path.read_bytes())

    def test_temp_write_failure_preserves_original_and_removes_temp(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})
            before = config_path.read_bytes()
            original_open = Path.open

            def fail_temp_open(path: Path, *args, **kwargs):
                if path.name.startswith(".config.json."):
                    raise OSError("failed")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", new=fail_temp_open):
                result = StockRepository(root).patch_stock_config(
                    "005930", {"A": 3}
                )

            self.assertFalse(result.ok)
            self.assertEqual(
                STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED, result.reason_code
            )
            self.assertEqual(before, config_path.read_bytes())
            self.assertEqual([], list(config_path.parent.glob(".config.json.*.tmp")))

    def test_replace_failure_preserves_original_and_removes_temp(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})
            before = config_path.read_bytes()

            with patch("stock_repository.os.replace", side_effect=OSError("failed")):
                result = StockRepository(root).patch_stock_config(
                    "005930", {"A": 3}
                )

            self.assertFalse(result.ok)
            self.assertEqual(
                STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED, result.reason_code
            )
            self.assertEqual(before, config_path.read_bytes())
            self.assertEqual([], list(config_path.parent.glob(".config.json.*.tmp")))

    def test_readback_failure_is_reported_after_atomic_replace(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"A": 1, "B": 2})
            original_read_bytes = Path.read_bytes
            reads = 0

            def fail_readback(path: Path) -> bytes:
                nonlocal reads
                if path == config_path:
                    reads += 1
                    if reads == 3:
                        raise OSError("readback failed")
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", new=fail_readback):
                result = StockRepository(root).patch_stock_config(
                    "005930", {"A": 3}
                )

            self.assertFalse(result.ok)
            self.assertTrue(result.changed)
            self.assertFalse(result.read_back_verified)
            self.assertEqual(STOCK_CONFIG_WRITE_READBACK_FAILED, result.reason_code)
            self.assertEqual({"A": 3, "B": 2}, self._read_config(config_path))

    def test_mixed_fields_unicode_and_none_value_are_preserved(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(
                root,
                {
                    "routine_instance_name": "한글 루틴",
                    "buy_amount": 20000,
                    "buy_limit_amount": 100000,
                    "operation_excluded": False,
                    "policy_override_memo": "기존 정책",
                },
            )

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"buy_amount": 70000, "policy_override_memo": None},
                expected_fields={"buy_amount": 20000},
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                {
                    "routine_instance_name": "한글 루틴",
                    "buy_amount": 70000,
                    "buy_limit_amount": 100000,
                    "operation_excluded": False,
                    "policy_override_memo": None,
                },
                self._read_config(config_path),
            )

    def test_delete_field_removes_only_requested_key(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(
                root,
                {"manual_operation_override": {"extra1": True}, "buy_amount": 70000},
            )

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"manual_operation_override": STOCK_CONFIG_DELETE_FIELD},
                expected_fields={
                    "manual_operation_override": {"extra1": True},
                },
            )

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual({"buy_amount": 70000}, self._read_config(config_path))

    def test_delete_missing_field_is_no_change(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = self._config_path(root, {"buy_amount": 70000})
            before = config_path.read_bytes()

            result = StockRepository(root).patch_stock_config(
                "005930",
                {"manual_operation_override": STOCK_CONFIG_DELETE_FIELD},
                expected_fields={
                    "manual_operation_override": STOCK_CONFIG_EXPECTED_MISSING,
                },
            )

            self.assertTrue(result.ok)
            self.assertFalse(result.changed)
            self.assertEqual(STOCK_CONFIG_WRITE_NO_CHANGE, result.reason_code)
            self.assertEqual(before, config_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
