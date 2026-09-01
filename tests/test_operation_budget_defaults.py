# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QTableWidget, QTableWidgetItem

import gui_main_table_loader as main_loader
import gui_auto_trade_setting_window as auto_trade_window
import gui_operation_environment as environment
import gui_windows
from stock_buy_limit_provenance import (
    BUY_LIMIT_SOURCE_MANUAL,
    BUY_LIMIT_SOURCE_RECOMMENDED,
    BUY_LIMIT_SOURCE_UNKNOWN,
    normalized_stock_buy_limit_source,
)


class OperationBudgetDefaultsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_policy_fields_use_backward_compatible_defaults(self) -> None:
        self.assertEqual(
            {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100.0,
                "limit_minimum_multiplier": 25.0,
            },
            environment.starting_budget_defaults({}),
        )

    def test_new_stock_config_defaults_to_unset_provenance(self) -> None:
        config = main_loader.default_config()
        self.assertFalse(config["buy_limit_enabled"])
        self.assertIsNone(config["buy_limit_amount"])
        self.assertIsNone(config["buy_limit_source"])

    def test_policy_reader_merges_missing_budget_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            policy_path.write_text(
                json.dumps({"regular_market": {"start_time": "09:10:00"}}),
                encoding="utf-8",
            )
            with patch.object(environment, "OPERATION_POLICY_PATH", policy_path):
                loaded = environment.read_operation_policy()
        self.assertEqual("09:10:00", loaded["regular_market"]["start_time"])
        self.assertEqual(1.5, loaded["starting_budget_defaults"]["amount_multiplier"])

    def test_dialog_loads_and_builds_all_four_values(self) -> None:
        policy = environment.default_operation_policy()
        policy["starting_budget_defaults"] = {
            "quantity": 3,
            "amount_multiplier": 2.5,
            "limit_recommended_multiplier": 80.0,
            "limit_minimum_multiplier": 20.0,
        }
        with patch.object(environment, "read_operation_policy", return_value=policy):
            dialog = environment.OperationEnvironmentSettingsDialog()
        self.addCleanup(dialog.deleteLater)

        self.assertEqual("3", dialog.starting_quantity.text())
        self.assertEqual("2.5", dialog.starting_amount_multiplier.text())
        self.assertEqual("80", dialog.limit_recommended_multiplier.text())
        self.assertEqual("20", dialog.limit_minimum_multiplier.text())
        self.assertIn(
            "7. 시작 예산 설정",
            [label.text() for label in dialog.findChildren(QLabel)],
        )
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn("■ 한도금액 : 시작예산 × 권장", labels)
        self.assertNotIn("■ 한도금액 : 현재가 × 권장", labels)

        dialog.starting_quantity.setText("4")
        dialog.starting_amount_multiplier.setText("1.75")
        built = dialog.build_policy_from_widgets()
        self.assertEqual(4, built["starting_budget_defaults"]["quantity"])
        self.assertEqual(1.75, built["starting_budget_defaults"]["amount_multiplier"])

    def test_stock_limit_digit_alignment_setting_persists_and_reloads(self) -> None:
        class MemorySettings:
            def __init__(self) -> None:
                self.values = {}

            def value(self, key, default=None):
                return self.values.get(key, default)

            def setValue(self, key, value) -> None:
                self.values[key] = value

        settings = MemorySettings()
        with patch.object(environment, "_ui_settings", return_value=settings):
            self.assertTrue(environment.stock_limit_digit_alignment_enabled())
            environment.set_stock_limit_digit_alignment_enabled(False)
            self.assertFalse(environment.stock_limit_digit_alignment_enabled())

            with patch.object(
                environment,
                "read_operation_policy",
                return_value=environment.default_operation_policy(),
            ):
                dialog = environment.OperationEnvironmentSettingsDialog()
            self.addCleanup(dialog.deleteLater)

            dialog.show()
            self.app.processEvents()
            dialog_width = dialog.width()
            button_width = dialog.limit_digit_alignment_toggle.width()
            button_height = dialog.limit_digit_alignment_toggle.height()
            self.assertEqual(
                dialog.limit_recommended_multiplier.width(),
                dialog.limit_minimum_multiplier.width(),
            )
            self.assertEqual("자리맞춤 OFF", dialog.limit_digit_alignment_toggle.text())
            self.assertEqual(
                dialog.limit_digit_alignment_toggle.minimumWidth(),
                dialog.limit_digit_alignment_toggle.maximumWidth(),
            )
            self.assertIn(
                "background-color: transparent",
                dialog.limit_digit_alignment_toggle.styleSheet(),
            )
            dialog.limit_digit_alignment_toggle.setChecked(True)
            self.app.processEvents()
            self.assertTrue(environment.stock_limit_digit_alignment_enabled())
            self.assertEqual(
                "자리맞춤 ON",
                dialog.limit_digit_alignment_toggle.text(),
            )
            self.assertEqual(button_width, dialog.limit_digit_alignment_toggle.width())
            self.assertEqual(button_height, dialog.limit_digit_alignment_toggle.height())
            self.assertEqual(dialog_width, dialog.width())
            self.assertIn("color: #000000", dialog.limit_digit_alignment_toggle.styleSheet())
            self.assertIn("border-color: #000000", dialog.limit_digit_alignment_toggle.styleSheet())
            self.assertIn("color: #9a9a9a", dialog.limit_digit_alignment_toggle.styleSheet())

    def test_dialog_rejects_minimum_multiplier_above_recommended(self) -> None:
        with patch.object(
            environment,
            "read_operation_policy",
            return_value=environment.default_operation_policy(),
        ):
            dialog = environment.OperationEnvironmentSettingsDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.limit_recommended_multiplier.setText("10")
        dialog.limit_minimum_multiplier.setText("11")
        with patch.object(environment.QMessageBox, "warning") as warning:
            self.assertIsNone(dialog._validated_starting_budget_defaults())
        warning.assert_called_once()

    def test_dialog_save_round_trip_persists_all_four_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            with (
                patch.object(environment, "OPERATION_POLICY_PATH", policy_path),
                patch.object(environment, "append_changelog"),
                patch.object(environment, "show_toast"),
            ):
                dialog = environment.OperationEnvironmentSettingsDialog()
                self.addCleanup(dialog.deleteLater)
                dialog.starting_quantity.setText("5")
                dialog.starting_amount_multiplier.setText("2.25")
                dialog.limit_recommended_multiplier.setText("120")
                dialog.limit_minimum_multiplier.setText("30")
                dialog.accept()
                loaded = environment.read_operation_policy()
        self.assertEqual(
            {
                "quantity": 5,
                "amount_multiplier": 2.25,
                "limit_recommended_multiplier": 120.0,
                "limit_minimum_multiplier": 30.0,
            },
            loaded["starting_budget_defaults"],
        )
        self.assertTrue(dialog._starting_budget_defaults_changed)

    def test_dialog_records_unchanged_starting_budget_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "operation_policy.json"
            with (
                patch.object(environment, "OPERATION_POLICY_PATH", policy_path),
                patch.object(environment, "append_changelog"),
                patch.object(environment, "show_toast"),
            ):
                environment.write_operation_policy(environment.default_operation_policy())
                dialog = environment.OperationEnvironmentSettingsDialog()
                self.addCleanup(dialog.deleteLater)
                dialog.accept()
        self.assertFalse(dialog._starting_budget_defaults_changed)

    def test_saved_starting_budget_change_refreshes_projection_without_limit_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = (
                Path(temp_dir) / "stocks" / "005930_삼성전자" / "config.json"
            )
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 3_000_000,
                        "buy_limit_source": BUY_LIMIT_SOURCE_RECOMMENDED,
                    }
                ),
                encoding="utf-8",
            )
            original = config_path.read_bytes()
            owner = SimpleNamespace(
                _recalculate_recommended_stock_buy_limits_for_starting_budget_change=(
                    MagicMock(return_value=1)
                )
            )
            host = SimpleNamespace(
                statusBarMessage=MagicMock(),
                refresh_all=MagicMock(),
            )
            dialog = SimpleNamespace(_starting_budget_defaults_changed=True)
            with patch.object(
                auto_trade_window,
                "persistent_feature_owner",
                return_value=owner,
            ) as owner_resolver:
                auto_trade_window.AutoTradeSettingWindow._handle_operation_environment_settings_saved(
                    host,
                    dialog,
                )

            owner_resolver.assert_not_called()
            owner._recalculate_recommended_stock_buy_limits_for_starting_budget_change.assert_not_called()
            host.statusBarMessage.assert_called_once_with("환경설정 저장 완료")
            host.refresh_all.assert_called_once_with()
            self.assertEqual(original, config_path.read_bytes())

    def test_amount_budget_applies_multiplier_and_floors_to_won(self) -> None:
        self.assertEqual(
            10_905,
            environment.effective_amount_starting_budget(7_270, 1.5),
        )
        self.assertEqual(
            120_000,
            environment.effective_amount_starting_budget(80_000, 1.5),
        )
        self.assertIsNone(environment.effective_amount_starting_budget(None, 1.5))

    def test_limit_rounding_keeps_only_leading_place_and_rounds_up(self) -> None:
        self.assertEqual(2_000_000, environment.round_up_to_leading_place(1_234_500))
        self.assertEqual(600_000, environment.round_up_to_leading_place(534_000))
        self.assertEqual(90_000, environment.round_up_to_leading_place(83_500))

    def test_stock_limit_digit_alignment_on_and_off_use_shared_calculator(self) -> None:
        self.assertEqual(
            13_000_000,
            environment.suggested_buy_limit(12_500_000, 1, align_digits=True),
        )
        self.assertEqual(
            12_500_000,
            environment.suggested_buy_limit(12_500_000, 1, align_digits=False),
        )

    def test_main_initial_budget_uses_default_only_when_stock_value_missing(self) -> None:
        policy = {
            "starting_budget_defaults": {
                "quantity": 2,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
        }
        self.assertEqual(
            2,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "QUANTITY", "buy_qty": 0},
                policy=policy,
            )["value"],
        )
        self.assertEqual(
            7,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "QUANTITY", "buy_qty": 7},
                policy=policy,
            )["value"],
        )
        self.assertEqual(
            120_000,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                current_price=80_000,
                policy=policy,
            )["value"],
        )
        self.assertEqual(
            120_000,
            main_loader.stock_initial_buy_display(
                {"trade_amount_type": "AMOUNT", "buy_amount": 120_000},
                current_price=80_000,
                policy=policy,
            )["value"],
        )

    def test_amount_without_current_price_uses_persistent_or_unset_display(self) -> None:
        display = main_loader.stock_initial_buy_display(
            {"trade_amount_type": "AMOUNT", "buy_amount": 0},
            current_price=None,
        )
        self.assertEqual("-", display["value_text"])
        self.assertNotEqual("0원", display["value_text"])

        explicit = main_loader.stock_initial_buy_display(
            {"trade_amount_type": "AMOUNT", "buy_amount": 350_000},
            current_price=None,
        )
        self.assertEqual("350,000원", explicit["value_text"])
        self.assertEqual(350_000, explicit["value"])

    def test_budget_waiting_display_is_limited_to_server_auth_pending(self) -> None:
        connection = SimpleNamespace(value=False)
        authentication_states: dict[str, str] = {}
        fresh = SimpleNamespace(value=None)
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: fresh.value,
        )
        window = SimpleNamespace(
            kiwoom_api=SimpleNamespace(is_connected=lambda: connection.value),
            selected_account_no=lambda: "12345678",
            _account_authentication_states=authentication_states,
            main_monitoring_auto_trade_operation_host=lambda: operation_host,
        )
        stock = {
            "code": "012210",
            "name": "삼미금속",
            "stock_path": "",
            "config": {
                "trade_amount_type": "AMOUNT",
                "buy_amount": 350_000,
                "buy_limit_enabled": True,
                "buy_limit_amount": 900_000,
            },
            "state": {},
        }

        def display_values() -> tuple[str, str]:
            initial_buy = main_loader.main_stock_resolved_initial_buy_display(
                window,
                stock,
                stock["config"],
            )
            limit_text = main_loader._routine_tree_stock_metric_values(window, stock)[2]
            return str(initial_buy["value_text"]), limit_text

        self.assertEqual(main_loader.LOGIN_NOT_STARTED, main_loader.main_budget_display_auth_state(window))
        self.assertEqual(("350,000원", "한도(900,000)"), display_values())

        connection.value = True
        self.assertEqual(main_loader.SERVER_AUTH_PENDING, main_loader.main_budget_display_auth_state(window))
        self.assertEqual(("대기", "한도(대기)"), display_values())

        authentication_states["12345678"] = "READY"
        self.assertEqual(main_loader.SERVER_AUTH_COMPLETE, main_loader.main_budget_display_auth_state(window))
        self.assertEqual(("대기", "한도(대기)"), display_values())

        fresh.value = SimpleNamespace(
            connection_epoch=7,
            login_session_id="SESSION-7",
            last_price=8_870,
        )
        self.assertEqual(("350,000원", "한도(900,000)"), display_values())

        connection.value = False
        authentication_states.clear()
        fresh.value = None
        self.assertEqual(("350,000원", "한도(900,000)"), display_values())
        connection.value = True
        self.assertEqual(("대기", "한도(대기)"), display_values())

    def test_auth_complete_without_price_keeps_budget_values_waiting(self) -> None:
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: None,
        )
        window = SimpleNamespace(
            kiwoom_api=SimpleNamespace(is_connected=lambda: True),
            selected_account_no=lambda: "12345678",
            _account_authentication_states={"12345678": "READY"},
            main_monitoring_auto_trade_operation_host=lambda: operation_host,
        )
        stock = {
            "code": "012210",
            "name": "삼미금속",
            "stock_path": "",
            "config": {
                "trade_amount_type": "AMOUNT",
                "buy_amount": 0,
                "buy_limit_enabled": True,
                "buy_limit_amount": None,
            },
            "state": {},
        }

        initial_buy = main_loader.main_stock_resolved_initial_buy_display(
            window,
            stock,
            stock["config"],
        )
        limit_text = main_loader._routine_tree_stock_metric_values(window, stock)[2]

        self.assertEqual("대기", initial_buy["value_text"])
        self.assertEqual("한도(대기)", limit_text)

    def test_auth_state_refresh_clears_budget_cache_and_reloads_canonical_values(self) -> None:
        cache = {"012210": {"amount": 350_000}}
        reload_table = MagicMock()
        window = SimpleNamespace(
            _main_stock_resolved_starting_budget_cache=cache,
            load_routine_table=reload_table,
        )

        gui_windows.MainWindow._refresh_start_budget_displays_for_auth_state(window)

        self.assertEqual({}, cache)
        reload_table.assert_called_once_with()

    def test_stock_starting_budget_amount_reuses_initial_budget_contract(self) -> None:
        policy = {
            "starting_budget_defaults": {
                "quantity": 2,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
        }
        self.assertEqual(
            36_350,
            main_loader.stock_starting_budget_amount(
                {"trade_amount_type": "QUANTITY", "buy_qty": 5},
                current_price=7_270,
                policy=policy,
            ),
        )
        self.assertEqual(
            350_000,
            main_loader.stock_starting_budget_amount(
                {"trade_amount_type": "AMOUNT", "buy_amount": 350_000},
                current_price=None,
                policy=policy,
            ),
        )
        self.assertEqual(
            10_905,
            main_loader.stock_starting_budget_amount(
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                current_price=7_270,
                policy=policy,
            ),
        )
        self.assertIsNone(
            main_loader.stock_starting_budget_amount(
                {"trade_amount_type": "QUANTITY", "buy_qty": 5},
                current_price=None,
                policy=policy,
            )
        )

    def test_limit_recalculates_when_current_price_changes(self) -> None:
        live = SimpleNamespace(value=None)
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: live.value,
        )
        window = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=lambda: operation_host,
        )
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
            "config": {
                "trade_amount_type": "QUANTITY",
                "buy_qty": 2,
                "buy_limit_enabled": True,
                "buy_limit_amount": None,
            },
            "state": {},
        }
        with patch.object(
            main_loader,
            "starting_budget_defaults",
            return_value={
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            },
        ), patch.object(main_loader, "stock_limit_digit_alignment_enabled", return_value=True):
            waiting_result = main_loader._routine_tree_stock_metric_values(window, stock)
            live.value = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=10_000,
            )
            first_result = main_loader._routine_tree_stock_metric_values(window, stock)
            live.value = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=12_000,
            )
            later_result = main_loader._routine_tree_stock_metric_values(window, stock)

        self.assertEqual("한도(미설정)", waiting_result[2])
        self.assertEqual(6, len(waiting_result[0]))
        self.assertEqual("권장(2,000,000)", first_result[2])
        self.assertEqual(6, len(first_result[0]))
        self.assertEqual("권장(2,400,000)", later_result[2])
        self.assertEqual(
            {
                "trade_amount_type": "QUANTITY",
                "buy_qty": 2,
                "buy_limit_enabled": True,
                "buy_limit_amount": None,
            },
            stock["config"],
        )

    def test_unconfigured_limit_stays_unconfigured_even_when_price_exists(self) -> None:
        result = main_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            {
                "code": "005930",
                "name": "삼성전자",
                "stock_path": "",
                "config": {
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                },
                "state": {"current_price": 12_345},
            },
            total_budget=40_000_000,
        )
        self.assertEqual("한도(미설정)", result[2])
        self.assertEqual(6, len(result[0]))
        self.assertEqual("소모", result[0][5].label)
        self.assertEqual("0", result[0][5].value1)
        self.assertEqual("0.0%", result[0][5].value2)

    def test_unconfigured_limit_stays_unset_without_or_with_price(self) -> None:
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
            "config": {
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
            },
            "state": {},
        }

        waiting = main_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            stock,
        )
        stock["state"] = {"current_price": 12_345}
        available = main_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            stock,
        )

        self.assertEqual("한도(미설정)", waiting[2])
        self.assertEqual("UNSET", waiting[4]["limit_source"])
        self.assertEqual("한도(미설정)", available[2])
        self.assertEqual("UNSET", available[4]["limit_source"])

    def test_explicit_limit_keeps_amount_without_current_price(self) -> None:
        result = main_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            {
                "code": "005930",
                "name": "삼성전자",
                "stock_path": "",
                "config": {
                    "buy_limit_enabled": True,
                    "buy_limit_amount": 750_000,
                },
                "state": {},
            },
        )
        self.assertEqual("한도(750,000)", result[2])
        self.assertEqual("CONFIGURED", result[4]["limit_source"])
        self.assertEqual(750_000, result[4]["configured_limit"])

    def test_main_limit_suggestion_uses_quantity_starting_budget_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_삼성전자"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            config = {"trade_amount_type": "QUANTITY", "buy_qty": 5}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (stock_dir / "state.json").write_text(
                json.dumps({"current_price": 12_345}),
                encoding="utf-8",
            )
            defaults = {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
            fresh_state = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=12_345,
            )
            operation_host = SimpleNamespace(
                fresh_monitoring_market_information_state=lambda _code: fresh_state,
            )
            window = SimpleNamespace(
                main_monitoring_auto_trade_operation_host=lambda: operation_host,
            )
            with patch.object(
                gui_windows, "starting_budget_defaults", return_value=defaults
            ), patch.object(
                gui_windows, "stock_limit_digit_alignment_enabled", return_value=True
            ):
                recommended = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    window=window,
                )
                minimum = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    minimum=True,
                    window=window,
                )
            self.assertEqual(6_200_000, recommended)
            self.assertEqual(1_500_000, minimum)
            self.assertEqual(config, json.loads(config_path.read_text(encoding="utf-8")))

    def test_main_limit_suggestion_prefers_current_session_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "012210_삼미금속"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            config = {"trade_amount_type": "AMOUNT", "buy_amount": 0}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            market_state = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=7_270,
            )
            operation_host = SimpleNamespace(
                fresh_monitoring_market_information_state=lambda _code: market_state,
            )
            window = SimpleNamespace(
                main_monitoring_auto_trade_operation_host=lambda: operation_host,
            )
            defaults = {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
            with patch.object(
                gui_windows, "starting_budget_defaults", return_value=defaults
            ), patch.object(
                gui_windows, "stock_limit_digit_alignment_enabled", return_value=True
            ):
                recommended = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    window=window,
                )
                minimum = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    minimum=True,
                    window=window,
                )

            self.assertEqual(1_100_000, recommended)
            self.assertEqual(270_000, minimum)
            self.assertEqual(config, json.loads(config_path.read_text(encoding="utf-8")))

    def test_prelogin_configured_amount_remains_visible_without_fresh_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "012210_삼미금속"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            config = {"trade_amount_type": "AMOUNT", "buy_amount": 350_000}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            defaults = {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }

            operation_host = SimpleNamespace(
                fresh_monitoring_market_information_state=lambda _code: None,
            )
            window = SimpleNamespace(
                main_monitoring_auto_trade_operation_host=lambda: operation_host,
            )
            with patch.object(gui_windows, "starting_budget_defaults", return_value=defaults):
                recommended = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    window=window,
                )
                minimum = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    minimum=True,
                    window=window,
                )
            display = main_loader.main_stock_resolved_initial_buy_display(
                window,
                {
                    "stock_path": str(stock_dir),
                    "code": "012210",
                    "name": "삼미금속",
                },
                config,
                policy={"starting_budget_defaults": defaults},
            )

            self.assertIsNone(recommended)
            self.assertIsNone(minimum)
            self.assertEqual(("금액", "350,000원"), (display["badge"], display["value_text"]))
            self.assertEqual(config, json.loads(config_path.read_text(encoding="utf-8")))

    def test_resolved_starting_budget_cache_tracks_all_calculation_inputs(self) -> None:
        live = SimpleNamespace(
            value=SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=10_000,
            )
        )
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: live.value,
        )
        window = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=lambda: operation_host,
        )
        stock = {"stock_path": "stocks/012210_삼미금속", "code": "012210"}
        quantity_config = {"trade_amount_type": "QUANTITY", "buy_qty": 2}
        defaults = {
            "starting_budget_defaults": {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
        }
        changed_defaults = {
            "starting_budget_defaults": {
                **defaults["starting_budget_defaults"],
                "amount_multiplier": 2.0,
            }
        }

        with patch.object(
            main_loader,
            "stock_starting_budget_amount",
            wraps=main_loader.stock_starting_budget_amount,
        ) as calculator:
            first = main_loader.main_stock_resolved_starting_budget(
                window, stock, quantity_config, policy=defaults
            )
            live.value = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=10_000.0,
            )
            same_price = main_loader.main_stock_resolved_starting_budget(
                window, stock, quantity_config, policy=defaults
            )
            live.value = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=12_000,
            )
            changed_price = main_loader.main_stock_resolved_starting_budget(
                window, stock, quantity_config, policy=defaults
            )
            changed_mode = main_loader.main_stock_resolved_starting_budget(
                window,
                stock,
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                policy=defaults,
            )
            changed_value = main_loader.main_stock_resolved_starting_budget(
                window,
                stock,
                {"trade_amount_type": "AMOUNT", "buy_amount": 30_000},
                policy=defaults,
            )
            changed_environment = main_loader.main_stock_resolved_starting_budget(
                window,
                stock,
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                policy=changed_defaults,
            )
            live.value = SimpleNamespace(
                connection_epoch=8,
                login_session_id="SESSION-8",
                last_price=13_000,
            )
            reauthenticated = main_loader.main_stock_resolved_starting_budget(
                window,
                stock,
                {"trade_amount_type": "AMOUNT", "buy_amount": 0},
                policy=changed_defaults,
            )

        self.assertEqual(20_000, first)
        self.assertEqual(first, same_price)
        self.assertEqual(24_000, changed_price)
        self.assertEqual(18_000, changed_mode)
        self.assertEqual(30_000, changed_value)
        self.assertEqual(24_000, changed_environment)
        self.assertEqual(26_000, reauthenticated)
        self.assertEqual(6, calculator.call_count)
        self.assertEqual(1, len(window._main_stock_resolved_starting_budget_cache))

    def test_first_fresh_price_refresh_preserves_waiting_limit_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "stocks" / "012210_삼미금속"
            stock_dir.mkdir(parents=True)
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trade_amount_type": "AMOUNT",
                        "buy_amount": 100_000,
                        "buy_limit_enabled": True,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            original = config_path.read_bytes()
            window = SimpleNamespace(
                _pending_main_market_information_codes={"012210"},
            )
            projection = MagicMock(return_value=1)
            writer = MagicMock()
            with patch.object(
                gui_windows,
                "main_refresh_market_information_only",
                projection,
            ), patch.object(
                gui_windows.MainWindow,
                "_write_stock_buy_limit_config",
                writer,
            ):
                refreshed = gui_windows.MainWindow._refresh_main_market_information_rows(
                    window
                )

            self.assertEqual(1, refreshed)
            self.assertEqual(set(), window._pending_main_market_information_codes)
            projection.assert_called_once_with(window, ("012210",))
            writer.assert_not_called()
            self.assertEqual(original, config_path.read_bytes())

    def test_recommended_limit_is_immutable_across_realtime_and_reconnect_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "stocks" / "012210_삼미금속"
            stock_dir.mkdir(parents=True)
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 600_000,
                        "buy_limit_source": BUY_LIMIT_SOURCE_RECOMMENDED,
                    }
                ),
                encoding="utf-8",
            )
            original = config_path.read_bytes()
            window = SimpleNamespace(_pending_main_market_information_codes=set())
            projection = MagicMock(return_value=1)
            writer = MagicMock()
            with patch.object(
                gui_windows,
                "main_refresh_market_information_only",
                projection,
            ), patch.object(
                gui_windows.MainWindow,
                "_write_stock_buy_limit_config",
                writer,
            ):
                window._pending_main_market_information_codes.add("012210")
                first = gui_windows.MainWindow._refresh_main_market_information_rows(
                    window
                )
                window._pending_main_market_information_codes.add("012210")
                second = gui_windows.MainWindow._refresh_main_market_information_rows(
                    window
                )

            self.assertEqual((1, 1), (first, second))
            self.assertEqual(2, projection.call_count)
            writer.assert_not_called()
            self.assertEqual(original, config_path.read_bytes())

    def test_over_total_recommendation_calculation_preserves_persistent_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "012210_삼미금속"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 900_000,
                        "buy_limit_source": BUY_LIMIT_SOURCE_RECOMMENDED,
                    }
                ),
                encoding="utf-8",
            )
            original = config_path.read_bytes()
            defaults = {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }
            with (
                patch.object(
                    gui_windows,
                    "starting_budget_defaults",
                    return_value=defaults,
                ),
                patch.object(
                    gui_windows,
                    "main_stock_resolved_starting_budget",
                    return_value=500_000,
                ),
                patch.object(
                    gui_windows,
                    "stock_limit_digit_alignment_enabled",
                    return_value=False,
                ),
            ):
                recommendation = gui_windows.MainWindow._stock_suggested_buy_limit(
                    config_path,
                    window=SimpleNamespace(),
                )

            self.assertEqual(50_000_000, recommendation)
            self.assertEqual(original, config_path.read_bytes())

    def test_main_table_reload_is_projection_only_for_stock_limit(self) -> None:
        window = SimpleNamespace(
            _install_routine_buy_limit_edit_filters=MagicMock(),
        )
        loader = MagicMock()
        writer = MagicMock()
        with patch.object(
            gui_windows,
            "main_load_routine_table",
            loader,
        ), patch.object(
            gui_windows.MainWindow,
            "_write_stock_buy_limit_config",
            writer,
        ):
            gui_windows.MainWindow.load_routine_table(window)

        loader.assert_called_once_with(window)
        window._install_routine_buy_limit_edit_filters.assert_called_once_with()
        writer.assert_not_called()

    def test_manual_and_applied_recommended_limits_survive_new_projection(self) -> None:
        defaults = {
            "quantity": 1,
            "amount_multiplier": 1.5,
            "limit_recommended_multiplier": 100,
            "limit_minimum_multiplier": 25,
        }
        for source in (BUY_LIMIT_SOURCE_MANUAL, BUY_LIMIT_SOURCE_RECOMMENDED):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temp_dir:
                stock_dir = Path(temp_dir) / "stocks" / "005930_삼성전자"
                stock_dir.mkdir(parents=True)
                (stock_dir / "state.json").write_text("{}", encoding="utf-8")
                config_path = stock_dir / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": True,
                            "buy_limit_amount": 3_000_000,
                            "buy_limit_source": source,
                        }
                    ),
                    encoding="utf-8",
                )
                original = config_path.read_bytes()
                with patch.object(
                    main_loader,
                    "main_stock_current_price",
                    return_value=32_000,
                ), patch.object(
                    main_loader,
                    "main_stock_resolved_starting_budget",
                    return_value=32_000,
                ), patch.object(
                    main_loader,
                    "main_budget_display_auth_state",
                    return_value=main_loader.SERVER_AUTH_COMPLETE,
                ), patch.object(
                    main_loader,
                    "starting_budget_defaults",
                    return_value=defaults,
                ), patch.object(
                    main_loader,
                    "stock_limit_digit_alignment_enabled",
                    return_value=False,
                ), patch.object(
                    main_loader,
                    "project_confirmable_cumulative_pnl",
                    return_value={"available": False},
                ):
                    _metrics, _led, limit_text, _consumed, sort_values = (
                        main_loader._routine_tree_stock_metric_values(
                            SimpleNamespace(),
                            {
                                "code": "005930",
                                "name": "삼성전자",
                                "stock_path": str(stock_dir),
                            },
                        )
                    )

                self.assertEqual("한도(3,000,000)", limit_text)
                self.assertEqual(3_200_000, sort_values["recommended_limit"])
                self.assertEqual(3_000_000, sort_values["configured_limit"])
                self.assertEqual(original, config_path.read_bytes())

    def test_legacy_configured_limit_source_is_unknown(self) -> None:
        self.assertEqual(
            BUY_LIMIT_SOURCE_UNKNOWN,
            normalized_stock_buy_limit_source(
                {"buy_limit_enabled": True, "buy_limit_amount": 750_000}
            ),
        )
        self.assertEqual(
            BUY_LIMIT_SOURCE_RECOMMENDED,
            normalized_stock_buy_limit_source(
                {"buy_limit_enabled": True, "buy_limit_amount": None}
            ),
        )
        self.assertIsNone(
            normalized_stock_buy_limit_source(
                {"buy_limit_enabled": False, "buy_limit_amount": None}
            )
        )

    def test_stock_limit_writer_persists_and_reads_back_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = (
                Path(temp_dir) / "stocks" / "005930_삼성전자" / "config.json"
            )
            config_path.parent.mkdir(parents=True)
            config_path.write_text("{}", encoding="utf-8")
            gui_windows.MainWindow._write_stock_buy_limit_config(
                config_path,
                enabled=True,
                amount=750_000,
                source=BUY_LIMIT_SOURCE_MANUAL,
            )
            saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["buy_limit_enabled"])
        self.assertEqual(750_000, saved["buy_limit_amount"])
        self.assertEqual(BUY_LIMIT_SOURCE_MANUAL, saved["buy_limit_source"])

    def test_limit_below_current_minimum_is_not_written(self) -> None:
        editor = QLineEdit("300000")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _stock_suggested_buy_limit=MagicMock(return_value=400_000),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
            refresh_auto_trade_assignment_views=MagicMock(),
        )
        with (
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(gui_windows.QMessageBox, "warning") as warning,
        ):
            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=True)
        toast.assert_called_once_with(
            host,
            "종목 한도는 현재 최소 금액 400,000원 이상이어야 합니다.",
            duration_ms=2500,
        )
        warning.assert_not_called()
        host._write_stock_buy_limit_config.assert_called_once_with(
            Path("C:/temp/config.json"),
            enabled=False,
            amount=None,
            source=None,
        )
        host.refresh_auto_trade_assignment_views.assert_called_once_with()

    def test_blank_limit_save_preserves_existing_limit_state(self) -> None:
        editor = QLineEdit("")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=None),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )
        gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=True)
        host._write_stock_buy_limit_config.assert_not_called()

    def test_invalid_offline_limit_save_does_not_write(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value):
                editor = QLineEdit(value)
                host = SimpleNamespace(
                    _routine_stock_buy_limit_editor=editor,
                    _routine_stock_buy_limit_edit_finishing=False,
                    _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
                    routine_table=SimpleNamespace(
                        _editing_stock_buy_limit_path="stock",
                        viewport=lambda: SimpleNamespace(update=MagicMock()),
                    ),
                    _parse_buy_limit_amount=(
                        gui_windows.MainWindow._parse_buy_limit_amount
                    ),
                    _stock_suggested_buy_limit=MagicMock(return_value=None),
                    _write_stock_buy_limit_config=MagicMock(),
                    load_routine_table=MagicMock(),
                )

                gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                    host,
                    save=True,
                )

                host._write_stock_buy_limit_config.assert_not_called()
                host.load_routine_table.assert_not_called()

    def test_cancel_offline_limit_edit_does_not_write(self) -> None:
        editor = QLineEdit("750000")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=None),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )

        gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=False)

        host._write_stock_buy_limit_config.assert_not_called()
        host.load_routine_table.assert_not_called()

    def test_waiting_text_uses_center_alignment_in_fixed_value_slots(self) -> None:
        painter = MagicMock()
        cell_rect = QRect(0, 0, 176, 24)
        before = gui_windows._initial_buy_component_rects(cell_rect)["value"]
        gui_windows._draw_initial_buy_display(
            painter,
            cell_rect,
            {"mode": "AMOUNT", "value_text": "대기"},
        )
        drawn_rect, alignment, text = painter.drawText.call_args_list[-1].args
        self.assertEqual(before, drawn_rect)
        self.assertEqual(Qt.AlignCenter, alignment)
        self.assertEqual("대기", text)
        self.assertEqual(
            Qt.AlignCenter | Qt.AlignVCenter,
            gui_windows._main_stock_value_alignment("대기"),
        )

    def test_limit_single_click_is_delayed_and_uses_stock_path_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 750_000,
                        "buy_limit_source": BUY_LIMIT_SOURCE_RECOMMENDED,
                    }
                ),
                encoding="utf-8",
            )
            item = QTableWidgetItem()
            item.setData(gui_windows.ROUTINE_STOCK_PATH_ROLE, "stocks/005930_test")
            timer = MagicMock()
            table = SimpleNamespace(item=MagicMock(return_value=item))
            host = SimpleNamespace(
                routine_table=table,
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _stock_suggested_buy_limit=MagicMock(return_value=400_000),
            )

            with patch.object(
                gui_windows.QApplication,
                "doubleClickInterval",
                return_value=420,
            ):
                gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                    host,
                    3,
                )

        self.assertEqual(
            "stocks/005930_test",
            host._routine_stock_buy_limit_pending_path,
        )
        timer.start.assert_called_once_with(445)

    def test_unconfigured_limit_single_click_does_nothing_without_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            timer = MagicMock()
            item = QTableWidgetItem()
            item.setData(
                gui_windows.ROUTINE_STOCK_PATH_ROLE,
                "stocks/005930_test",
            )
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
                _stock_suggested_buy_limit=MagicMock(return_value=None),
                routine_table=SimpleNamespace(
                    item=MagicMock(return_value=item),
                ),
            )

            with patch.object(
                gui_windows.QApplication,
                "doubleClickInterval",
                return_value=420,
            ):
                gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                    host,
                    0,
                )

        timer.start.assert_not_called()
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_unconfigured_limit_single_click_keeps_recommendation_path_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            timer = MagicMock()
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _stock_suggested_buy_limit=MagicMock(return_value=800_000),
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
            )

            gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                host,
                0,
            )

        host._stock_suggested_buy_limit.assert_not_called()
        timer.start.assert_not_called()
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_configured_limit_single_click_allows_offline_edit(self) -> None:
        amount = 750_000
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": amount,
                    }
                ),
                encoding="utf-8",
            )
            timer = MagicMock()
            item = QTableWidgetItem()
            item.setData(
                gui_windows.ROUTINE_STOCK_PATH_ROLE,
                "stocks/005930_test",
            )
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _stock_suggested_buy_limit=MagicMock(return_value=None),
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
                routine_table=SimpleNamespace(
                    item=MagicMock(return_value=item),
                ),
            )

            with patch.object(
                gui_windows.QApplication,
                "doubleClickInterval",
                return_value=420,
            ):
                gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                    host,
                    0,
                )

            timer.start.assert_called_once_with(445)
            self.assertEqual(
                "stocks/005930_test",
                host._routine_stock_buy_limit_pending_path,
            )

    def test_waiting_limit_single_click_does_not_open_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            timer = MagicMock()
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(
                    return_value=config_path
                ),
                _routine_stock_buy_limit_pending_path="",
                _routine_stock_buy_limit_click_timer=timer,
            )

            gui_windows.MainWindow.schedule_routine_stock_buy_limit_single_click(
                host,
                0,
            )

        timer.start.assert_not_called()
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_configured_limit_editor_opens_without_current_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 750_000,
                        "buy_limit_source": BUY_LIMIT_SOURCE_RECOMMENDED,
                    }
                ),
                encoding="utf-8",
            )
            table = QTableWidget(1, 1)
            item = QTableWidgetItem()
            item.setData(
                gui_windows.ROUTINE_STOCK_PATH_ROLE,
                "stocks/005930_test",
            )
            table.setItem(0, 0, item)
            table._editing_stock_buy_limit_path = ""
            host = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            host.routine_table = table
            host._stock_config_path_for_routine_row = MagicMock(
                return_value=config_path
            )
            host._stock_suggested_buy_limit = MagicMock(return_value=None)
            host._parse_buy_limit_amount = (
                gui_windows.MainWindow._parse_buy_limit_amount
            )
            host.finish_routine_instance_buy_limit_edit = MagicMock()
            host.finish_routine_stock_buy_limit_edit = MagicMock()
            host._routine_stock_buy_limit_value_rect = MagicMock(
                return_value=QRect(10, 5, 120, 24)
            )
            host._routine_buy_limit_edit_filter = QObject()

            host.start_routine_stock_buy_limit_edit(0)

            self.assertIsNotNone(host._routine_stock_buy_limit_editor)
            self.assertEqual("750000", host._routine_stock_buy_limit_editor.text())
            host._routine_stock_buy_limit_editor.deleteLater()
            table.close()

    def test_initial_budget_entry_opens_shared_dialog_without_current_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trade_amount_type": "AMOUNT",
                        "buy_amount": 350_000,
                    }
                ),
                encoding="utf-8",
            )
            table = QTableWidget(1, 1)
            item = QTableWidgetItem()
            item.setData(
                gui_windows.ROUTINE_STOCK_PATH_ROLE,
                "stocks/005930_test",
            )
            table.setItem(0, 0, item)
            table._editing_stock_initial_buy_path = ""
            host = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            host._main_routine_display_level = "stock"
            host.routine_table = table
            host._stock_config_path_for_routine_row = MagicMock(
                return_value=config_path
            )
            host._open_running_budget_adjustment_dialog = MagicMock()
            host.finish_routine_stock_buy_limit_edit = MagicMock()
            host._routine_stock_initial_buy_value_rect = MagicMock(
                return_value=QRect(10, 5, 120, 24)
            )
            host._routine_buy_limit_edit_filter = QObject()

            host.open_routine_stock_initial_buy_dialog(0)

            host._open_running_budget_adjustment_dialog.assert_called_once_with(
                0,
                config_path,
            )
            self.assertEqual(
                [],
                table.viewport().findChildren(
                    QLineEdit,
                    "routineStockInitialBuyEditor",
                ),
            )
            table.close()

    def test_initial_budget_projection_does_not_create_inline_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trade_amount_type": "AMOUNT",
                        "buy_amount": 20_000,
                    }
                ),
                encoding="utf-8",
            )
            table = QTableWidget(1, 1)
            item = QTableWidgetItem()
            item.setData(
                gui_windows.ROUTINE_STOCK_PATH_ROLE,
                "stocks/005930_test",
            )
            item.setData(
                gui_windows.ROUTINE_STOCK_INITIAL_BUY_ROLE,
                {"mode": "AMOUNT", "value": 60_000, "value_text": "60,000원"},
            )
            table.setItem(0, 0, item)
            table._editing_stock_initial_buy_path = ""
            host = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            host._main_routine_display_level = "stock"
            host.routine_table = table
            host._stock_config_path_for_routine_row = MagicMock(
                return_value=config_path
            )
            host._open_running_budget_adjustment_dialog = MagicMock()
            host.finish_routine_stock_buy_limit_edit = MagicMock()
            host._routine_stock_initial_buy_value_rect = MagicMock(
                return_value=QRect(10, 5, 120, 24)
            )
            host._routine_buy_limit_edit_filter = QObject()

            host.open_routine_stock_initial_buy_dialog(0)

            host._open_running_budget_adjustment_dialog.assert_called_once_with(
                0,
                config_path,
            )
            self.assertEqual(
                [],
                table.viewport().findChildren(
                    QLineEdit,
                    "routineStockInitialBuyEditor",
                ),
            )
            table.close()

    def test_initial_quantity_projection_does_not_create_inline_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trade_amount_type": "QUANTITY",
                        "buy_qty": 3,
                    }
                ),
                encoding="utf-8",
            )
            table = QTableWidget(1, 1)
            item = QTableWidgetItem()
            item.setData(
                gui_windows.ROUTINE_STOCK_PATH_ROLE,
                "stocks/005930_test",
            )
            item.setData(
                gui_windows.ROUTINE_STOCK_INITIAL_BUY_ROLE,
                {"mode": "QUANTITY", "value": 10, "value_text": "10주"},
            )
            table.setItem(0, 0, item)
            table._editing_stock_initial_buy_path = ""
            host = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            host._main_routine_display_level = "stock"
            host.routine_table = table
            host._stock_config_path_for_routine_row = MagicMock(
                return_value=config_path
            )
            host._open_running_budget_adjustment_dialog = MagicMock()
            host.finish_routine_stock_buy_limit_edit = MagicMock()
            host._routine_stock_initial_buy_value_rect = MagicMock(
                return_value=QRect(10, 5, 120, 24)
            )
            host._routine_buy_limit_edit_filter = QObject()

            host.open_routine_stock_initial_buy_dialog(0)

            host._open_running_budget_adjustment_dialog.assert_called_once_with(
                0,
                config_path,
            )
            self.assertEqual(
                [],
                table.viewport().findChildren(
                    QLineEdit,
                    "routineStockInitialBuyEditor",
                ),
            )
            table.close()

    def test_limit_save_uses_offline_config_writer_when_price_is_unavailable(self) -> None:
        editor = QLineEdit("750000")
        host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/temp/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="stock",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=None),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
            refresh_auto_trade_assignment_views=MagicMock(),
            kiwoom_api=MagicMock(),
        )

        with patch.object(
            gui_windows,
            "_system_total_budget_amount",
            return_value=2_000_000,
        ):
            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(host, save=True)

        host._write_stock_buy_limit_config.assert_called_once_with(
            Path("C:/temp/config.json"),
            enabled=True,
            amount=750_000,
            source=BUY_LIMIT_SOURCE_MANUAL,
        )
        host.refresh_auto_trade_assignment_views.assert_called_once_with()
        self.assertEqual([], host.kiwoom_api.method_calls)

    def test_unchanged_configured_limit_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 750_000,
                        "buy_limit_source": BUY_LIMIT_SOURCE_RECOMMENDED,
                    }
                ),
                encoding="utf-8",
            )
            editor = QLineEdit("750000")
            host = SimpleNamespace(
                _routine_stock_buy_limit_editor=editor,
                _routine_stock_buy_limit_edit_finishing=False,
                _routine_stock_buy_limit_editor_config_path=str(config_path),
                routine_table=SimpleNamespace(
                    _editing_stock_buy_limit_path="stock",
                    viewport=lambda: SimpleNamespace(update=MagicMock()),
                ),
                _parse_buy_limit_amount=(
                    gui_windows.MainWindow._parse_buy_limit_amount
                ),
                _stock_suggested_buy_limit=MagicMock(return_value=None),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
                refresh_auto_trade_assignment_views=MagicMock(),
            )

            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                host,
                save=True,
            )

            host._write_stock_buy_limit_config.assert_not_called()
            host.load_routine_table.assert_not_called()

    def test_changed_configured_limit_writes_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 500_000,
                    }
                ),
                encoding="utf-8",
            )
            editor = QLineEdit("750000")
            host = SimpleNamespace(
                _routine_stock_buy_limit_editor=editor,
                _routine_stock_buy_limit_edit_finishing=False,
                _routine_stock_buy_limit_editor_config_path=str(config_path),
                routine_table=SimpleNamespace(
                    _editing_stock_buy_limit_path="stock",
                    viewport=lambda: SimpleNamespace(update=MagicMock()),
                ),
                _parse_buy_limit_amount=(
                    gui_windows.MainWindow._parse_buy_limit_amount
                ),
                _stock_suggested_buy_limit=MagicMock(return_value=None),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
                refresh_auto_trade_assignment_views=MagicMock(),
            )

            with patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=2_000_000,
            ):
                gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                    host,
                    save=True,
                )

            host._write_stock_buy_limit_config.assert_called_once_with(
                config_path,
                enabled=True,
                amount=750_000,
                source=BUY_LIMIT_SOURCE_MANUAL,
            )
            host.refresh_auto_trade_assignment_views.assert_called_once_with()

    def test_invalid_configured_limit_forces_unset_after_toast(self) -> None:
        for invalid_text in ("1", "10000001"):
            with self.subTest(invalid_text=invalid_text), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": True,
                            "buy_limit_amount": 500_000,
                            "buy_limit_source": BUY_LIMIT_SOURCE_MANUAL,
                        }
                    ),
                    encoding="utf-8",
                )
                editor = QLineEdit(invalid_text)
                host = SimpleNamespace(
                    _routine_stock_buy_limit_editor=editor,
                    _routine_stock_buy_limit_edit_finishing=False,
                    _routine_stock_buy_limit_editor_config_path=str(config_path),
                    routine_table=SimpleNamespace(
                        _editing_stock_buy_limit_path="stock",
                        viewport=lambda: SimpleNamespace(update=MagicMock()),
                    ),
                    _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
                    _stock_suggested_buy_limit=MagicMock(return_value=250_000),
                    _write_stock_buy_limit_config=MagicMock(),
                    load_routine_table=MagicMock(),
                    refresh_auto_trade_assignment_views=MagicMock(),
                )
                total_budget = 10_000_000
                with patch.object(
                    gui_windows,
                    "_system_total_budget_amount",
                    return_value=total_budget,
                ), patch.object(gui_windows, "show_toast") as toast:
                    gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                        host,
                        save=True,
                    )

                host._write_stock_buy_limit_config.assert_not_called()
                host.refresh_auto_trade_assignment_views.assert_not_called()
                toast.assert_called_once()

    def test_invalid_unset_limit_forces_unset_after_toast(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                        "buy_limit_source": None,
                    }
                ),
                encoding="utf-8",
            )
            editor = QLineEdit("1")
            host = SimpleNamespace(
                _routine_stock_buy_limit_editor=editor,
                _routine_stock_buy_limit_edit_finishing=False,
                _routine_stock_buy_limit_editor_config_path=str(config_path),
                routine_table=SimpleNamespace(
                    _editing_stock_buy_limit_path="stock",
                    viewport=lambda: SimpleNamespace(update=MagicMock()),
                ),
                _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
                _stock_suggested_buy_limit=MagicMock(return_value=250_000),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
                refresh_auto_trade_assignment_views=MagicMock(),
            )
            with patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=10_000_000,
            ), patch.object(gui_windows, "show_toast"):
                gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                    host,
                    save=True,
                )

            host._write_stock_buy_limit_config.assert_called_once_with(
                config_path,
                enabled=False,
                amount=None,
                source=None,
            )
            host.refresh_auto_trade_assignment_views.assert_called_once_with()

    def test_limit_double_click_cancels_pending_single_click_release(self) -> None:
        timer = MagicMock()
        host = SimpleNamespace(
            _routine_stock_buy_limit_click_timer=timer,
            _routine_stock_buy_limit_pending_path="stocks/005930_test",
            _routine_stock_buy_limit_suppressed_release_row=-1,
        )

        gui_windows.MainWindow.cancel_routine_stock_buy_limit_single_click(
            host,
            suppress_release_row=4,
        )

        timer.stop.assert_called_once()
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)
        self.assertTrue(
            gui_windows.MainWindow.consume_routine_stock_buy_limit_release(host, 4)
        )
        self.assertFalse(
            gui_windows.MainWindow.consume_routine_stock_buy_limit_release(host, 4)
        )

    def test_limit_double_click_event_cancels_editor_before_second_release(self) -> None:
        class FakeIndex:
            def isValid(self):
                return True

            def column(self):
                return 0

            def row(self):
                return 2

            def data(self, role):
                if role == gui_windows.ROUTINE_ROW_KIND_ROLE:
                    return gui_windows.ROUTINE_ROW_STOCK
                return None

        class FakeEvent:
            def __init__(self, event_type):
                self._event_type = event_type
                self.accept = MagicMock()

            def type(self):
                return self._event_type

            def button(self):
                return Qt.LeftButton

            def pos(self):
                return QPoint(30, 10)

        index = FakeIndex()
        table = SimpleNamespace(
            indexAt=MagicMock(return_value=index),
            visualRect=MagicMock(return_value=QRect(0, 0, 500, 24)),
        )
        window = MagicMock()
        window.consume_routine_stock_buy_limit_release.side_effect = [False, True]
        window._main_routine_initial_buy_badge_enabled.return_value = True
        controller = gui_windows._RoutineTreeInteractionController.__new__(
            gui_windows._RoutineTreeInteractionController
        )
        controller.table = table
        controller.window = window
        controller._stock_metric_rect = MagicMock(return_value=QRect(20, 0, 100, 24))
        controller._stock_legacy_metric_rect = MagicMock(return_value=QRect())

        first_release = FakeEvent(QEvent.MouseButtonRelease)
        double_click = FakeEvent(QEvent.MouseButtonDblClick)
        second_release = FakeEvent(QEvent.MouseButtonRelease)
        with patch.object(gui_windows, "_routine_stock_token_rect", return_value=QRect()):
            self.assertTrue(controller.eventFilter(table, first_release))
            self.assertTrue(controller.eventFilter(table, double_click))
            self.assertTrue(controller.eventFilter(table, second_release))

        window.schedule_routine_stock_buy_limit_single_click.assert_called_once_with(2)
        window.cancel_routine_stock_buy_limit_single_click.assert_called_once_with(
            suppress_release_row=2
        )
        window.handle_routine_stock_buy_limit_double_click.assert_called_once_with(2)
        window.start_routine_stock_buy_limit_edit.assert_not_called()

    def test_limit_pending_click_resolves_current_row_by_stock_snapshot(self) -> None:
        other = QTableWidgetItem()
        other.setData(gui_windows.ROUTINE_STOCK_PATH_ROLE, "stocks/000660_other")
        target = QTableWidgetItem()
        target.setData(gui_windows.ROUTINE_STOCK_PATH_ROLE, "stocks/005930_test")
        table = SimpleNamespace(
            rowCount=MagicMock(return_value=2),
            item=MagicMock(side_effect=[other, target]),
        )
        host = SimpleNamespace(
            routine_table=table,
            _routine_stock_buy_limit_pending_path="stocks/005930_test",
            start_routine_stock_buy_limit_edit=MagicMock(),
        )

        gui_windows.MainWindow._execute_routine_stock_buy_limit_single_click(host)

        host.start_routine_stock_buy_limit_edit.assert_called_once_with(1)
        self.assertEqual("", host._routine_stock_buy_limit_pending_path)

    def test_limit_double_click_resets_waiting_and_numeric_states(self) -> None:
        for amount in (None, 750_000):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": True,
                            "buy_limit_amount": amount,
                        }
                    ),
                    encoding="utf-8",
                )
                host = SimpleNamespace(
                    _stock_config_path_for_routine_row=MagicMock(
                        return_value=config_path
                    ),
                    finish_routine_instance_buy_limit_edit=MagicMock(),
                    finish_routine_stock_buy_limit_edit=MagicMock(),
                    _write_stock_buy_limit_config=MagicMock(),
                    load_routine_table=MagicMock(),
                    refresh_auto_trade_assignment_views=MagicMock(),
                )

                with patch.object(
                    gui_windows,
                    "_system_total_budget_amount",
                    return_value=2_000_000,
                ):
                    gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                        host,
                        0,
                    )

                host.finish_routine_stock_buy_limit_edit.assert_called_once_with(
                    save=False
                )
                host._write_stock_buy_limit_config.assert_called_once_with(
                    config_path,
                    enabled=False,
                    amount=None,
                    source=None,
                )
                host.refresh_auto_trade_assignment_views.assert_called_once()

    def test_unconfigured_limit_double_click_requires_calculable_recommendation(self) -> None:
        for suggested_amount in (None, 2_000_000):
            with self.subTest(suggested_amount=suggested_amount), tempfile.TemporaryDirectory() as temp_dir:
                config_path = Path(temp_dir) / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "buy_limit_enabled": False,
                            "buy_limit_amount": None,
                        }
                    ),
                    encoding="utf-8",
                )
                host = SimpleNamespace(
                    _stock_config_path_for_routine_row=MagicMock(
                        return_value=config_path
                    ),
                    finish_routine_instance_buy_limit_edit=MagicMock(),
                    finish_routine_stock_buy_limit_edit=MagicMock(),
                    _stock_suggested_buy_limit=MagicMock(
                        return_value=suggested_amount
                    ),
                    _write_stock_buy_limit_config=MagicMock(),
                    load_routine_table=MagicMock(),
                    refresh_auto_trade_assignment_views=MagicMock(),
                )

                with patch.object(
                    gui_windows,
                    "_system_total_budget_amount",
                    return_value=2_000_000,
                ), patch.object(gui_windows, "show_toast") as toast:
                    gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                        host,
                        0,
                    )

                if suggested_amount is None:
                    host._write_stock_buy_limit_config.assert_not_called()
                    host.load_routine_table.assert_not_called()
                    toast.assert_called_once_with(
                        host,
                        "한도금액 계산 근거를 확인할 수 없어 적용하지 않았습니다.",
                    )
                else:
                    host._write_stock_buy_limit_config.assert_called_once_with(
                        config_path,
                        enabled=True,
                        amount=suggested_amount,
                        source=BUY_LIMIT_SOURCE_RECOMMENDED,
                    )
                    host.refresh_auto_trade_assignment_views.assert_called_once()
                    toast.assert_not_called()

    def test_unconfigured_limit_double_click_reports_real_recommendation_over_total(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "012210_삼미금속"
            stock_dir.mkdir()
            config_path = stock_dir / "config.json"
            original_config = {
                "trade_amount_type": "AMOUNT",
                "buy_amount": 500_000,
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "buy_limit_source": None,
            }
            config_path.write_text(json.dumps(original_config), encoding="utf-8")
            fresh_state = SimpleNamespace(
                connection_epoch=7,
                login_session_id="SESSION-7",
                last_price=7_460,
            )
            operation_host = SimpleNamespace(
                fresh_monitoring_market_information_state=lambda _code: fresh_state,
            )
            host = SimpleNamespace(
                _main_stock_resolved_starting_budget_cache={},
                main_monitoring_auto_trade_operation_host=lambda: operation_host,
                _stock_config_path_for_routine_row=MagicMock(return_value=config_path),
                finish_routine_instance_buy_limit_edit=MagicMock(),
                finish_routine_stock_buy_limit_edit=MagicMock(),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
                start_routine_stock_buy_limit_edit=MagicMock(),
            )
            def suggested_limit(target_path, *, minimum=False, window):
                if minimum:
                    return 900_000
                return gui_windows.MainWindow._stock_suggested_buy_limit(
                    target_path,
                    window=window,
                )

            host._stock_suggested_buy_limit = suggested_limit
            defaults = {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100,
                "limit_minimum_multiplier": 25,
            }

            with patch.object(
                gui_windows,
                "starting_budget_defaults",
                return_value=defaults,
            ), patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=2_000_000,
            ), patch.object(gui_windows, "show_toast") as toast:
                gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                    host,
                    0,
                )

            host._write_stock_buy_limit_config.assert_not_called()
            host.load_routine_table.assert_not_called()
            toast.assert_called_once_with(
                host,
                "권장한도가 전체예산을 초과 합니다",
                duration_ms=2500,
            )
            host.start_routine_stock_buy_limit_edit.assert_called_once_with(
                0,
                use_suggested_amount=False,
            )
            self.assertEqual(
                original_config,
                json.loads(config_path.read_text(encoding="utf-8")),
            )

    def test_unconfigured_limit_double_click_blocks_when_minimum_exceeds_total(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "stock" / "config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps(
                    {
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                    }
                ),
                encoding="utf-8",
            )
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(return_value=config_path),
                finish_routine_instance_buy_limit_edit=MagicMock(),
                finish_routine_stock_buy_limit_edit=MagicMock(),
                _stock_suggested_buy_limit=MagicMock(
                    side_effect=lambda _path, minimum=False, window=None: (
                        12_500_000 if minimum else 50_000_000
                    )
                ),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
                start_routine_stock_buy_limit_edit=MagicMock(),
            )
            with (
                patch.object(
                    gui_windows,
                    "_system_total_budget_amount",
                    return_value=2_000_000,
                ),
                patch.object(gui_windows, "show_toast") as toast,
            ):
                gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                    host,
                    0,
                )

            host._write_stock_buy_limit_config.assert_not_called()
            host.start_routine_stock_buy_limit_edit.assert_not_called()
            host.load_routine_table.assert_not_called()
            toast.assert_called_once_with(
                host,
                "최소한도가 전체예산을 초과합니다.",
                duration_ms=2500,
            )


if __name__ == "__main__":
    unittest.main()
