import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSettings

from indicator_follow_signal_validation_preferences import (
    IndicatorFollowSignalValidationPreferenceStore,
    LEGACY_PREFERENCES_SETTINGS_KEY,
    PREFERENCES_SETTINGS_KEY,
)


class IndicatorFollowSignalValidationPreferenceStoreTest(unittest.TestCase):
    def _store(self, directory):
        settings = QSettings(
            str(Path(directory) / "validation-v2.ini"),
            QSettings.IniFormat,
        )
        settings.clear()
        return IndicatorFollowSignalValidationPreferenceStore(settings=settings), settings

    def test_round_trip_preserves_chart_local_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _settings = self._store(directory)
            values = {
                "averaging_enabled": True,
                "first_buy_quantity": "7",
                "repeat_mode": "능동매수",
                "trading_cost_enabled": True,
                "regular_market_only": True,
                "filter_display_enabled": False,
            }
            store.save(values)
            loaded = store.load()
            for key, value in values.items():
                self.assertEqual(value, loaded[key])

    def test_loads_legacy_nested_schema_into_ui_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store, settings = self._store(directory)
            settings.setValue(PREFERENCES_SETTINGS_KEY, json.dumps({
                "schema": "indicator_follow_signal_validation_preferences",
                "version": 1,
                "validation_execution": {
                    "enabled": False,
                    "first_buy_quantity": 1,
                    "repeat_mode": "ROUND",
                    "round_operator": "ADD",
                    "round_budget_value": 2.0,
                    "budget_ratio": 2.0,
                    "active_direction": "UP",
                    "active_ratio": 0.45,
                    "active_compare": "<=",
                    "trading_cost_enabled": True,
                    "trading_cost_percent": 0.2,
                },
                "validation_market_scope": {"regular_market_only": False},
                "filter_display": {"enabled": True, "side": "ALL", "scope": "ALL"},
            }, ensure_ascii=False))
            settings.sync()

            loaded = store.load()
            self.assertEqual("회차기준", loaded["repeat_mode"])
            self.assertEqual("+", loaded["round_operator"])
            self.assertEqual("상향", loaded["active_direction"])
            self.assertEqual("이하", loaded["active_compare"])
            self.assertTrue(loaded["filter_display_enabled"])
            self.assertNotIn("filter_display_side", loaded)
            self.assertNotIn("filter_display_scope", loaded)

    def test_invalid_payload_fails_open_to_empty_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store, settings = self._store(directory)
            settings.setValue(PREFERENCES_SETTINGS_KEY, "{broken")
            settings.sync()
            self.assertEqual({}, store.load())

    def test_store_serializes_one_v5_local_settings_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store, settings = self._store(directory)
            store.save({
                "filter_display_enabled": True,
                "filter_display_side": "매도",
                "filter_display_scope": "전체",
            })
            raw = settings.value(PREFERENCES_SETTINGS_KEY)
            payload = json.loads(str(raw))
            self.assertEqual(
                "indicator_follow_signal_validation_preferences",
                payload["schema"],
            )
            self.assertEqual(1, payload["version"])
            self.assertEqual(
                {"enabled": True},
                payload["filter_display"],
            )
            self.assertEqual(
                {"regular_market_only": False},
                payload["validation_market_scope"],
            )
            self.assertIn("validation_execution", payload)

    def test_legacy_only_reads_values_without_creating_v5_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store, settings = self._store(directory)
            legacy = json.dumps({
                "validation_execution": {
                    "enabled": False, "first_buy_quantity": 0,
                    "round_budget_value": 0, "budget_ratio": 0,
                    "trading_cost_enabled": False, "trading_cost_percent": 0,
                },
                "validation_market_scope": {"regular_market_only": False},
                "filter_display": {"enabled": False},
                "settings_hash": "keep-this-document-unchanged",
            })
            settings.setValue(LEGACY_PREFERENCES_SETTINGS_KEY, legacy)
            settings.sync()

            loaded = store.load()
            self.assertEqual(False, loaded["averaging_enabled"])
            self.assertEqual("0", loaded["first_buy_quantity"])
            self.assertEqual("0.0", loaded["round_value"])
            self.assertEqual("0.0", loaded["budget_ratio"])
            self.assertEqual(False, loaded["trading_cost_enabled"])
            self.assertEqual("0.0", loaded["trading_cost_percent"])
            self.assertEqual(False, loaded["regular_market_only"])
            self.assertEqual(False, loaded["filter_display_enabled"])
            self.assertFalse(settings.contains(PREFERENCES_SETTINGS_KEY))
            self.assertEqual(legacy, settings.value(LEGACY_PREFERENCES_SETTINGS_KEY))

    def test_v5_present_wins_and_malformed_v5_does_not_restore_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            store, settings = self._store(directory)
            old = json.dumps({"validation_execution": {"enabled": True}})
            settings.setValue(LEGACY_PREFERENCES_SETTINGS_KEY, old)
            settings.setValue(PREFERENCES_SETTINGS_KEY, json.dumps({
                "validation_execution": {"enabled": False},
            }))
            self.assertFalse(store.load()["averaging_enabled"])
            settings.setValue(PREFERENCES_SETTINGS_KEY, "{broken")
            self.assertEqual({}, store.load())
            self.assertEqual(old, settings.value(LEGACY_PREFERENCES_SETTINGS_KEY))

    def test_save_writes_only_v5_once_and_preserves_legacy(self):
        class Settings:
            def __init__(self):
                self.values = {LEGACY_PREFERENCES_SETTINGS_KEY: "legacy"}
                self.writes = []
                self.syncs = 0

            def value(self, key, default=None):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.writes.append(key)
                self.values[key] = value

            def sync(self):
                self.syncs += 1

        settings = Settings()
        store = IndicatorFollowSignalValidationPreferenceStore(settings=settings)
        self.assertEqual({}, store.load())
        self.assertEqual([], settings.writes)
        store.save({"filter_display_enabled": False})
        self.assertEqual([PREFERENCES_SETTINGS_KEY], settings.writes)
        self.assertEqual(1, settings.syncs)
        self.assertEqual("legacy", settings.values[LEGACY_PREFERENCES_SETTINGS_KEY])


if __name__ == "__main__":
    unittest.main()
