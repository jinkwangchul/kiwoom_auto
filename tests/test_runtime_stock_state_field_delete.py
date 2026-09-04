# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from runtime_stock_state_mutation import (
    RUNTIME_STOCK_STATE_EXPECTED_MISSING,
    STATE_FIELD_DELETE_ALREADY_MISSING,
    STATE_FIELD_DELETE_DELETED,
    STATE_FIELD_DELETE_EXPECTED_FIELD_MISSING,
    STATE_FIELD_DELETE_EXPECTED_FIELD_VALUE_MISMATCH,
    STATE_FIELD_DELETE_EXPECTED_HASH_MISMATCH,
    STATE_FIELD_DELETE_READBACK_FAILED,
    STATE_FIELD_DELETE_STATE_INVALID,
    STATE_FIELD_DELETE_WRITE_FAILED,
    delete_runtime_stock_state_fields,
    mutate_runtime_stock_state,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class RuntimeStockStateFieldDeleteTests(unittest.TestCase):
    def _state(self, root: Path, value: dict[str, object]) -> tuple[Path, Path]:
        stock_dir = root / "stocks" / "005930_Test"
        stock_dir.mkdir(parents=True)
        path = stock_dir / "state.json"
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return stock_dir, path

    def test_deletes_only_expected_field_and_preserves_status_and_timestamp(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(
                Path(temp),
                {
                    "status": "MONITORING",
                    "updated_at": "2026-09-01 10:00:00",
                    "trade_enabled": True,
                    "real_trade_enabled": False,
                    "close_policy": {"method": "MARKET"},
                },
            )
            before = json.loads(path.read_text(encoding="utf-8"))
            result = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256=_sha256(path),
                expected_fields={"real_trade_enabled": False},
            )
            after = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(STATE_FIELD_DELETE_DELETED, result.reason_code)
            self.assertEqual(("real_trade_enabled",), result.deleted_fields)
            self.assertNotIn("real_trade_enabled", after)
            self.assertEqual(
                {key: value for key, value in before.items() if key != "real_trade_enabled"},
                after,
            )
            self.assertEqual("MONITORING", after["status"])
            self.assertEqual("2026-09-01 10:00:00", after["updated_at"])

    def test_missing_field_is_noop_and_does_not_rewrite(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(Path(temp), {"status": "STOPPED"})
            before = path.read_bytes()
            result = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256=_sha256(path),
                expected_fields={
                    "real_trade_enabled": RUNTIME_STOCK_STATE_EXPECTED_MISSING,
                },
            )
            self.assertTrue(result.ok)
            self.assertFalse(result.changed)
            self.assertEqual(STATE_FIELD_DELETE_ALREADY_MISSING, result.reason_code)
            self.assertEqual(before, path.read_bytes())

    def test_hash_and_expected_value_conflicts_do_not_mutate(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(
                Path(temp), {"status": "MONITORING", "real_trade_enabled": False}
            )
            before = path.read_bytes()
            hash_conflict = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256="0" * 64,
                expected_fields={"real_trade_enabled": False},
            )
            value_conflict = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256=_sha256(path),
                expected_fields={"real_trade_enabled": True},
            )
            self.assertEqual(
                STATE_FIELD_DELETE_EXPECTED_HASH_MISMATCH,
                hash_conflict.reason_code,
            )
            self.assertEqual(
                STATE_FIELD_DELETE_EXPECTED_FIELD_VALUE_MISMATCH,
                value_conflict.reason_code,
            )
            self.assertEqual(before, path.read_bytes())

    def test_expected_present_field_missing_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(Path(temp), {"status": "STOPPED"})
            before = path.read_bytes()
            result = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256=_sha256(path),
                expected_fields={"real_trade_enabled": False},
            )
            self.assertFalse(result.ok)
            self.assertEqual(STATE_FIELD_DELETE_EXPECTED_FIELD_MISSING, result.reason_code)
            self.assertEqual(before, path.read_bytes())

    def test_malformed_state_and_writer_failure_fail_closed(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir, path = self._state(root, {"real_trade_enabled": False})
            path.write_text("{broken", encoding="utf-8")
            malformed = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
            )
            self.assertEqual(STATE_FIELD_DELETE_STATE_INVALID, malformed.reason_code)

            path.write_text(
                json.dumps({"real_trade_enabled": False}), encoding="utf-8"
            )
            before = path.read_bytes()
            with patch(
                "runtime_stock_state_mutation.write_json_atomic",
                return_value={"status": "ERROR", "written": False},
            ):
                failed = delete_runtime_stock_state_fields(
                    stock_dir,
                    ("real_trade_enabled",),
                    expected_file_sha256=_sha256(path),
                    expected_fields={"real_trade_enabled": False},
                )
            self.assertEqual(STATE_FIELD_DELETE_WRITE_FAILED, failed.reason_code)
            self.assertEqual(before, path.read_bytes())

    def test_readback_failure_is_not_reported_as_success(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(
                Path(temp), {"status": "STOPPED", "real_trade_enabled": False}
            )
            original_reader = __import__(
                "runtime_stock_state_mutation"
            )._read_state_document
            calls = 0

            def read_with_failed_readback(target: Path):
                nonlocal calls
                calls += 1
                if calls >= 3:
                    return {"status": "CORRUPTED"}, b"{}", ""
                return original_reader(target)

            with patch(
                "runtime_stock_state_mutation._read_state_document",
                side_effect=read_with_failed_readback,
            ):
                result = delete_runtime_stock_state_fields(
                    stock_dir,
                    ("real_trade_enabled",),
                    expected_file_sha256=_sha256(path),
                    expected_fields={"real_trade_enabled": False},
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.changed)
            self.assertEqual(STATE_FIELD_DELETE_READBACK_FAILED, result.reason_code)

    def test_second_call_is_idempotent(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(
                Path(temp), {"status": "STOPPED", "real_trade_enabled": False}
            )
            first = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256=_sha256(path),
                expected_fields={"real_trade_enabled": False},
            )
            first_after = path.read_bytes()
            second = delete_runtime_stock_state_fields(
                stock_dir,
                ("real_trade_enabled",),
                expected_file_sha256=_sha256(path),
                expected_fields={
                    "real_trade_enabled": RUNTIME_STOCK_STATE_EXPECTED_MISSING,
                },
            )
            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertFalse(second.changed)
            self.assertEqual(first_after, path.read_bytes())

    def test_existing_mutation_contract_is_unchanged(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir, path = self._state(
                Path(temp), {"status": "STOPPED", "updated_at": "old"}
            )
            result = mutate_runtime_stock_state(
                stock_dir,
                "RUNNING",
                {"trade_enabled": True},
                updated_at="2026-09-04 10:00:00",
                verify_readback=True,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(result.ok)
            self.assertEqual("RUNNING", saved["status"])
            self.assertEqual("2026-09-04 10:00:00", saved["updated_at"])
            self.assertTrue(saved["trade_enabled"])


if __name__ == "__main__":
    unittest.main()
