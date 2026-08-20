from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import candle_manager
from candle_manager import append_candle, commit_candles, load_candles
import kiwoom_candle_adapter


def _candles(count: int, *, close_offset: int = 0) -> list[dict[str, object]]:
    start = datetime(2026, 8, 20, 9, 0)
    return [
        {
            "time": (start + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"),
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index + close_offset,
            "volume": 1000 + index,
        }
        for index in range(count)
    ]


def _rows(candles: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "체결시간": candle["time"],
            "시가": candle["open"],
            "고가": candle["high"],
            "저가": candle["low"],
            "현재가": candle["close"],
            "거래량": candle["volume"],
        }
        for candle in reversed(candles)
    ]


class CanonicalCandleFileCommitTests(unittest.TestCase):
    def test_atomic_initial_commit_and_duplicate_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            candles = _candles(3)

            first = commit_candles(stock_dir, candles)
            second = commit_candles(stock_dir, candles)

            self.assertTrue(first.ok)
            self.assertTrue(first.changed)
            self.assertTrue(first.readback_verified)
            self.assertEqual(candles, load_candles(stock_dir))
            self.assertTrue(second.ok)
            self.assertFalse(second.changed)
            self.assertEqual(first.canonical_content_hash, second.canonical_content_hash)
            self.assertEqual([], list(stock_dir.glob(".candles.json.*.tmp")))

    def test_append_candle_preserves_compatibility_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            saved = append_candle(stock_dir, _candles(1)[0])

            self.assertEqual(1, len(saved))
            self.assertEqual(saved, load_candles(stock_dir))

    def test_temp_write_failure_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            original = _candles(1)
            self.assertTrue(commit_candles(stock_dir, original).ok)

            with patch.object(candle_manager, "_write_temp_payload", side_effect=OSError("disk full")):
                result = commit_candles(stock_dir, _candles(2))

            self.assertFalse(result.ok)
            self.assertEqual("CANDLE_TEMP_WRITE_FAILED", result.error_kind)
            self.assertEqual(original, load_candles(stock_dir))

    def test_partial_temp_write_failure_cleans_created_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            original = _candles(1)
            self.assertTrue(commit_candles(stock_dir, original).ok)

            with patch.object(candle_manager.os, "fsync", side_effect=OSError("flush failed")):
                result = commit_candles(stock_dir, _candles(2))

            self.assertFalse(result.ok)
            self.assertEqual("CANDLE_TEMP_WRITE_FAILED", result.error_kind)
            self.assertEqual(original, load_candles(stock_dir))
            self.assertEqual([], list(stock_dir.glob(".candles.json.*.tmp")))

    def test_temp_verify_failure_preserves_existing_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            original = _candles(1)
            self.assertTrue(commit_candles(stock_dir, original).ok)
            real_read = candle_manager._read_candles_strict

            def mismatched_temp(path: Path):
                if path.name.endswith(".tmp"):
                    return _candles(1, close_offset=50)
                return real_read(path)

            with patch.object(candle_manager, "_read_candles_strict", side_effect=mismatched_temp):
                result = commit_candles(stock_dir, _candles(2))

            self.assertFalse(result.ok)
            self.assertEqual("CANDLE_TEMP_VERIFY_FAILED", result.error_kind)
            self.assertEqual(original, load_candles(stock_dir))
            self.assertEqual([], list(stock_dir.glob(".candles.json.*.tmp")))

    def test_replace_failure_preserves_existing_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            original = _candles(1)
            self.assertTrue(commit_candles(stock_dir, original).ok)

            with patch.object(candle_manager.os, "replace", side_effect=OSError("replace failed")):
                result = commit_candles(stock_dir, _candles(2))

            self.assertFalse(result.ok)
            self.assertEqual("CANDLE_REPLACE_FAILED", result.error_kind)
            self.assertEqual("", result.canonical_content_hash)
            self.assertEqual(original, load_candles(stock_dir))
            self.assertEqual([], list(stock_dir.glob(".candles.json.*.tmp")))

    def test_final_readback_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            intended = _candles(2)
            real_read = candle_manager._read_candles_strict
            read_count = 0

            def mismatched_final(path: Path):
                nonlocal read_count
                read_count += 1
                value = real_read(path)
                if read_count == 2:
                    return _candles(1, close_offset=99)
                return value

            with patch.object(candle_manager, "_read_candles_strict", side_effect=mismatched_final):
                result = commit_candles(stock_dir, intended)

            self.assertFalse(result.ok)
            self.assertTrue(result.changed)
            self.assertFalse(result.readback_verified)
            self.assertEqual("CANDLE_FINAL_VERIFY_FAILED", result.error_kind)
            self.assertEqual([], list(stock_dir.glob(".candles.json.*.tmp")))

    def test_different_stock_locks_do_not_block_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_entered = threading.Event()
            release_first = threading.Event()
            second_entered = threading.Event()

            def hold_first() -> None:
                with candle_manager.candle_commit_lock(root / "A"):
                    first_entered.set()
                    release_first.wait(2)

            def enter_second() -> None:
                first_entered.wait(2)
                with candle_manager.candle_commit_lock(root / "B"):
                    second_entered.set()

            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(hold_first)
                second = executor.submit(enter_second)
                self.assertTrue(second_entered.wait(1))
                release_first.set()
                first.result()
                second.result()


class CanonicalMinuteCandleIdentityTests(unittest.TestCase):
    def _repository(self, stock_dir: Path):
        return type("Repository", (), {"resolve_stock_dir": lambda _self, _code, _name="": stock_dir})()

    def test_existing_merge_and_same_stock_concurrent_updates_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            repository = self._repository(stock_dir)
            first = _candles(1)
            second = _candles(2)[1]
            third = _candles(3)[2]
            self.assertTrue(commit_candles(stock_dir, first).ok)

            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(
                        lambda candle: kiwoom_candle_adapter.commit_minute_candles_for_stock(
                            "005930", "Test", _rows([candle])
                        ),
                        (second, third),
                    ))

            self.assertTrue(all(result.ok for result in results))
            self.assertEqual(3, len(load_candles(stock_dir)))

    def test_identity_is_deterministic_and_changed_close_keeps_bar_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            repository = self._repository(stock_dir)
            candles = _candles(2)
            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                first = kiwoom_candle_adapter.commit_minute_candles_for_stock(
                    "005930", "Test", _rows(candles), rqname="first", connection_epoch=1
                )
                duplicate = kiwoom_candle_adapter.commit_minute_candles_for_stock(
                    "005930", "Test", _rows(candles), rqname="second", connection_epoch=9
                )
                changed = [dict(item) for item in candles]
                changed[-1]["close"] = 999
                corrected = kiwoom_candle_adapter.commit_minute_candles_for_stock("005930", "Test", _rows(changed))

            self.assertTrue(first.changed)
            self.assertFalse(duplicate.changed)
            self.assertIsNone(duplicate.notification)
            self.assertEqual(first.bar_identity, duplicate.bar_identity)
            self.assertEqual(first.commit_identity, duplicate.commit_identity)
            self.assertEqual(first.bar_key, corrected.bar_key)
            self.assertNotEqual(first.bar_identity, corrected.bar_identity)
            self.assertNotEqual(first.commit_identity, corrected.commit_identity)

    def test_historical_correction_changes_commit_identity_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            repository = self._repository(stock_dir)
            candles = _candles(2)
            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                first = kiwoom_candle_adapter.commit_minute_candles_for_stock("005930", "Test", _rows(candles))
                corrected = [dict(item) for item in candles]
                corrected[0]["close"] = 777
                second = kiwoom_candle_adapter.commit_minute_candles_for_stock("005930", "Test", _rows(corrected))

            self.assertEqual(first.bar_key, second.bar_key)
            self.assertEqual(first.bar_identity, second.bar_identity)
            self.assertNotEqual(first.canonical_content_hash, second.canonical_content_hash)
            self.assertNotEqual(first.commit_identity, second.commit_identity)

    def test_multiple_rows_create_one_notification_and_invalid_inputs_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            repository = self._repository(stock_dir)
            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                committed = kiwoom_candle_adapter.commit_minute_candles_for_stock(
                    "005930", "Test", _rows(_candles(5)), rqname="rq", connection_epoch=7
                )
                invalid = kiwoom_candle_adapter.commit_minute_candles_for_stock("005930", "Test", [{"현재가": "bad"}])
                no_time = kiwoom_candle_adapter.commit_minute_candles_for_stock("005930", "Test", [{"현재가": "100"}])

            self.assertEqual(5, committed.saved_count)
            self.assertIsNotNone(committed.notification)
            self.assertEqual("BAR_COMMITTED", committed.notification.event_type)
            self.assertEqual("NO_VALID_CANDLES", invalid.error_kind)
            self.assertEqual("NO_VALID_CANDLE_TIME", no_time.error_kind)
            self.assertFalse(invalid.ok)
            self.assertFalse(no_time.ok)
            self.assertEqual(5, len(load_candles(stock_dir)))


if __name__ == "__main__":
    unittest.main()
