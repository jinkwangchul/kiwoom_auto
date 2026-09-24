# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
    merge_validation_candles,
)


def _candle(index: int, *, close: float | None = None) -> dict[str, object]:
    value = float(index if close is None else close)
    return {
        "time": f"2026091810{index:02d}00",
        "open": value - 1.0,
        "high": value + 1.0,
        "low": value - 2.0,
        "close": value,
        "volume": float(index + 10),
    }


class SignalValidationHistoricalCacheTest(unittest.TestCase):
    def test_period_keys_do_not_collide_with_same_production_bar_minutes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(2)]

            for key in ("D1", "W1", "MO1", "Y1"):
                self.assertTrue(cache.store(
                    stock_code="005930",
                    stock_name="Samsung",
                    timeframe_minutes=5,
                    timeframe_key=key,
                    requested_count=2,
                    candles=candles,
                ))
            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="Samsung",
                timeframe_minutes=1,
                requested_count=2,
                candles=candles,
            ))

            self.assertEqual(
                {
                    "005930_D1_2.json",
                    "005930_W1_2.json",
                    "005930_MO1_2.json",
                    "005930_Y1_2.json",
                    "005930_1_2.json",
                },
                {path.name for path in Path(temp_dir).glob("*.json")},
            )
            for key in ("D1", "W1", "MO1", "Y1"):
                entry = cache.load("005930", 5, 2, key)
                self.assertIsNotNone(entry)
                self.assertEqual(key, entry.timeframe_key)
            minute_entry = cache.load("005930", 1, 2)
            self.assertIsNotNone(minute_entry)
            self.assertEqual("M1", minute_entry.timeframe_key)

    def test_period_cache_identity_does_not_depend_on_production_bar_minutes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(2)]

            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="Samsung",
                timeframe_minutes=5,
                timeframe_key="D1",
                requested_count=2,
                candles=candles,
            ))

            entry = cache.load("005930", 3, 2, "D1")
            self.assertIsNotNone(entry)
            self.assertEqual("D1", entry.timeframe_key)
            self.assertEqual(candles, list(entry.candles))

    def test_store_round_trip_is_exact_keyed_and_readback_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(5)]

            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="삼성전자",
                timeframe_minutes=3,
                requested_count=5,
                candles=candles,
            ))

            entry = cache.load("005930", 3, 5)
            self.assertIsNotNone(entry)
            self.assertEqual("005930", entry.stock_code)
            self.assertEqual("삼성전자", entry.stock_name)
            self.assertEqual(3, entry.timeframe_minutes)
            self.assertEqual(5, entry.requested_count)
            self.assertEqual(candles, list(entry.candles))
            self.assertIsNone(cache.load("005930", 5, 5))
            self.assertIsNone(cache.load("005930", 3, 6))

    def test_identity_corruption_fails_closed_without_cross_key_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="삼성전자",
                timeframe_minutes=3,
                requested_count=3,
                candles=[_candle(index) for index in range(3)],
            ))
            path = cache.path_for("005930", 3, 3)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stock"]["code"] = "000660"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertIsNone(cache.load("005930", 3, 3))

    def test_merge_deduplicates_by_time_sorts_and_trims_to_requested_count(self):
        existing = [_candle(index) for index in range(4)]
        replacement = _candle(3, close=999.0)
        incoming = [replacement, _candle(4), _candle(5)]

        merged = merge_validation_candles(existing, incoming, 4)

        self.assertEqual(
            ["20260918100200", "20260918100300", "20260918100400", "20260918100500"],
            [item["time"] for item in merged],
        )
        self.assertEqual(999.0, merged[1]["close"])

    def test_load_covering_reuses_larger_persisted_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(8)]
            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="삼성전자",
                timeframe_minutes=3,
                requested_count=8,
                candles=candles,
            ))
            entry = cache.load_covering("005930", 3, 5)
            self.assertIsNotNone(entry)
            self.assertEqual(8, entry.requested_count)
            self.assertEqual(candles, list(entry.candles))
            self.assertIsNone(cache.load_covering("005930", 3, 9))

    def test_invalid_payload_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            path = cache.path_for("005930", 3, 5)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not-json", encoding="utf-8")

            self.assertIsNone(cache.load("005930", 3, 5))

    def test_delete_stock_removes_all_exact_cache_artifacts_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(2)]
            for timeframe, key, count in ((1, None, 2), (5, "D1", 20)):
                self.assertTrue(cache.store(
                    stock_code="005930",
                    stock_name="Samsung",
                    timeframe_minutes=timeframe,
                    timeframe_key=key,
                    requested_count=count,
                    candles=candles,
                ))
                path = cache.path_for("005930", timeframe, count, key)
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["signal_entries_by_settings_hash"] = {
                    "settings-a": [{"signal": "BUY"}]
                }
                path.write_text(json.dumps(payload), encoding="utf-8")
                path.with_name(f"{path.name}.market-source.json").write_text(
                    "{}", encoding="utf-8"
                )
            survivor = Path(temp_dir) / "0059300_1_2.json"
            survivor.write_text("{}", encoding="utf-8")
            same_prefix_non_cache = Path(temp_dir) / "005930_notes.json"
            same_prefix_non_cache.write_text("{}", encoding="utf-8")
            unrelated = Path(temp_dir) / "000660_1_2.json"
            unrelated.write_text("{}", encoding="utf-8")

            result = cache.delete_stock("005930")

            self.assertTrue(result["ok"])
            self.assertEqual(4, result["deleted_count"])
            self.assertFalse(any(
                path.name.startswith("005930_")
                and path.name != same_prefix_non_cache.name
                for path in Path(temp_dir).glob("005930_*.json")
            ))
            self.assertTrue(survivor.exists())
            self.assertTrue(same_prefix_non_cache.exists())
            self.assertTrue(unrelated.exists())

    def test_delete_failure_persistently_blocks_all_stale_stock_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(5)]
            for count in (2, 5):
                self.assertTrue(cache.store(
                    stock_code="005930",
                    stock_name="Samsung",
                    timeframe_minutes=1,
                    requested_count=count,
                    candles=candles[-count:],
                ))
                self.assertTrue(cache.store_signal_entries(
                    stock_code="005930",
                    timeframe_minutes=1,
                    requested_count=count,
                    settings_hash="settings-a",
                    entries=[{"signal": "BUY", "requested_count": count}],
                ))
            stale_paths = {
                cache.path_for("005930", 1, count).resolve()
                for count in (2, 5)
            }
            original_unlink = Path.unlink
            states_seen_before_unlink = []

            def locked_stock_artifact(path, *args, **kwargs):
                if path.resolve() in stale_paths:
                    states_seen_before_unlink.append(json.loads(
                        cache.state_path_for("005930").read_text(encoding="utf-8")
                    )["state"])
                    raise PermissionError("simulated cache file lock")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", locked_stock_artifact):
                result = cache.delete_stock("005930")

            self.assertFalse(result["ok"])
            self.assertTrue(result["invalidated"])
            self.assertEqual(2, len(result["failed_paths"]))
            self.assertEqual(["INVALIDATED", "INVALIDATED"], states_seen_before_unlink)
            self.assertTrue(all(path.exists() for path in stale_paths))

            restarted = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self.assertIsNone(restarted.load("005930", 1, 2))
            self.assertIsNone(restarted.load_covering("005930", 1, 1))
            self.assertIsNone(restarted.load_signal_entries(
                "005930", 1, 2, "settings-a"
            ))

    def test_fresh_store_reactivates_only_its_generation_after_failed_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            stale_candles = [_candle(index) for index in range(5)]
            for count in (2, 5):
                self.assertTrue(cache.store(
                    stock_code="005930",
                    stock_name="Old Samsung",
                    timeframe_minutes=1,
                    requested_count=count,
                    candles=stale_candles[-count:],
                ))
            self.assertTrue(cache.store_signal_entries(
                stock_code="005930",
                timeframe_minutes=1,
                requested_count=2,
                settings_hash="old-settings",
                entries=[{"signal": "SELL"}],
            ))
            stale_paths = {
                cache.path_for("005930", 1, count).resolve()
                for count in (2, 5)
            }
            original_unlink = Path.unlink

            def locked_stock_artifact(path, *args, **kwargs):
                if path.resolve() in stale_paths:
                    raise PermissionError("simulated cache file lock")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", locked_stock_artifact):
                self.assertFalse(cache.delete_stock("005930")["ok"])

            fresh_candles = [
                _candle(3, close=103.0),
                _candle(4, close=104.0),
            ]
            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="Fresh Samsung",
                timeframe_minutes=1,
                requested_count=2,
                candles=fresh_candles,
            ))

            restarted = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            fresh = restarted.load("005930", 1, 2)
            self.assertIsNotNone(fresh)
            self.assertEqual("Fresh Samsung", fresh.stock_name)
            self.assertEqual((103.0, 104.0), tuple(
                candle["close"] for candle in fresh.candles
            ))
            self.assertIsNone(restarted.load("005930", 1, 5))
            self.assertIsNone(restarted.load_covering("005930", 1, 3))
            self.assertIsNone(restarted.load_signal_entries(
                "005930", 1, 2, "old-settings"
            ))


    def test_signal_entries_round_trip_by_settings_hash_and_history_rewrite_invalidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(5)]
            entries = [{
                "evaluation_side": "BUY",
                "evaluation_index": 4,
                "evaluation_time": candles[4]["time"],
                "signal": "BUY",
                "reason": "fixture",
                "signal_index": 4,
                "signal_time": candles[4]["time"],
                "delay_bar": 0,
                "matched_groups": ["B"],
                "details": [],
                "trace": {"conditions": [], "groups": [], "aggregations": []},
            }]

            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="????",
                timeframe_minutes=3,
                requested_count=5,
                candles=candles,
            ))
            cache_path = cache.path_for("005930", 3, 5)
            before_updated_at = json.loads(
                cache_path.read_text(encoding="utf-8")
            )["updated_at"]
            self.assertTrue(cache.store_signal_entries(
                stock_code="005930",
                timeframe_minutes=3,
                requested_count=5,
                settings_hash="settings-a",
                entries=entries,
            ))
            after_updated_at = json.loads(
                cache_path.read_text(encoding="utf-8")
            )["updated_at"]
            self.assertEqual(before_updated_at, after_updated_at)
            self.assertEqual(
                entries,
                cache.load_signal_entries("005930", 3, 5, "settings-a"),
            )
            self.assertIsNone(
                cache.load_signal_entries("005930", 3, 5, "settings-b")
            )

            updated = list(candles)
            updated[-1] = _candle(4, close=999.0)
            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="????",
                timeframe_minutes=3,
                requested_count=5,
                candles=updated,
            ))
            self.assertIsNone(
                cache.load_signal_entries("005930", 3, 5, "settings-a")
            )


if __name__ == "__main__":
    unittest.main()
