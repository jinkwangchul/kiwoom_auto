from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit

import gui_main_table_loader as main_loader
import gui_windows


class RoutineLimitRecommendationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _stock(root: Path, name: str, price: int | None, **config_values):
        stock_dir = root / name
        stock_dir.mkdir()
        state = {} if price is None else {"current_price": price}
        (stock_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (stock_dir / "config.json").write_text(
            json.dumps(config_values),
            encoding="utf-8",
        )
        code, stock_name = name.split("_", 1)
        return {
            "stock_path": f"stocks/{name}",
            "stock_dir": stock_dir,
            "instance_id": "instance-a",
            "code": code,
            "name": stock_name,
            "enabled": config_values.get("enabled", False),
        }

    @staticmethod
    def _defaults():
        return {
            "quantity": 1,
            "amount_multiplier": 1.5,
            "limit_recommended_multiplier": 100,
            "limit_minimum_multiplier": 25,
        }

    def _amounts(self, stocks):
        window = SimpleNamespace()
        with (
            patch.object(
                main_loader,
                "_main_pnl_refresh_static_cache",
                return_value={"stocks": tuple(stocks)},
            ),
            patch.object(
                main_loader,
                "starting_budget_defaults",
                return_value=self._defaults(),
            ),
        ):
            return main_loader.routine_instance_suggested_buy_limits(
                window,
                "instance-a",
            )

    def test_recommendation_uses_all_registered_stocks_and_ignores_saved_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self._stock(
                root,
                "000001_First",
                12_345,
                buy_limit_enabled=True,
                buy_limit_amount=99_000_000,
                enabled=False,
            )
            second = self._stock(
                root,
                "000002_Second",
                20_000,
                buy_limit_enabled=True,
                buy_limit_amount=1,
                enabled=False,
            )

            recommended, minimum = self._amounts([first, second])

        self.assertEqual(4_000_000, recommended)
        self.assertEqual(900_000, minimum)

    def test_single_stock_matches_existing_stock_recommendation_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000001_First", 12_345)
            with patch.object(
                gui_windows,
                "starting_budget_defaults",
                return_value=self._defaults(),
            ):
                stock_recommended = gui_windows.MainWindow._stock_suggested_buy_limit(
                    stock["stock_dir"] / "config.json"
                )

            recommended, minimum = self._amounts([stock])

        self.assertEqual(stock_recommended, recommended)
        self.assertEqual(400_000, minimum)

    def test_missing_price_or_empty_registration_forbids_partial_sum(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ready = self._stock(root, "000001_Ready", 12_345)
            waiting = self._stock(root, "000002_Waiting", None)

            self.assertEqual((None, None), self._amounts([ready, waiting]))
            self.assertEqual((None, None), self._amounts([]))

    def test_reference_price_is_stable_after_first_valid_price(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "000001_First", 12_345)
            window = SimpleNamespace()
            with (
                patch.object(
                    main_loader,
                    "_main_pnl_refresh_static_cache",
                    return_value={"stocks": (stock,)},
                ),
                patch.object(
                    main_loader,
                    "starting_budget_defaults",
                    return_value=self._defaults(),
                ),
            ):
                first = main_loader.routine_instance_suggested_buy_limits(
                    window,
                    "instance-a",
                )
                (stock["stock_dir"] / "state.json").write_text(
                    json.dumps({"current_price": 99_999}),
                    encoding="utf-8",
                )
                second = main_loader.routine_instance_suggested_buy_limits(
                    window,
                    "instance-a",
                )

        self.assertEqual(first, second)

    @staticmethod
    def _routine_host(editor_text: str = ""):
        editor = QLineEdit(editor_text)
        label = QLabel()
        host = SimpleNamespace(
            _routine_instance_buy_limit_editor=editor,
            _routine_instance_buy_limit_editor_instance_id="instance-a",
            _routine_instance_buy_limit_editor_label=label,
            _routine_instance_buy_limit_edit_finishing=False,
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _routine_instance_id_for_buy_limit_widget=MagicMock(
                return_value="instance-a"
            ),
            finish_routine_stock_buy_limit_edit=MagicMock(),
            finish_routine_instance_buy_limit_edit=MagicMock(),
            refresh_all=MagicMock(),
        )
        return host, editor, label

    def test_unconfigured_double_click_uses_recommendation_or_waiting(self):
        cases = (
            ((4_000_000, 900_000), 5_000_000, True, 4_000_000),
            ((5_000_000, 900_000), 5_000_000, True, 5_000_000),
            ((5_000_001, 900_000), 5_000_000, False, None),
            ((None, None), 5_000_000, True, None),
        )
        for amounts, total_budget, expected_enabled, expected_amount in cases:
            with self.subTest(amounts=amounts, total_budget=total_budget):
                host, _editor, label = self._routine_host()
                label.setProperty("routine_instance_id", "instance-a")
                instance = SimpleNamespace(buy_limit_enabled=False)
                repository = MagicMock()
                repository.update_buy_limit.return_value = SimpleNamespace(
                    success=True,
                    error="",
                )
                with (
                    patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                    patch.object(
                        gui_windows,
                        "routine_instance_suggested_buy_limits",
                        return_value=amounts,
                    ),
                    patch.object(
                        gui_windows,
                        "_system_total_budget_amount",
                        return_value=total_budget,
                    ),
                    patch.object(
                        gui_windows,
                        "RoutineInstanceRepository",
                        return_value=repository,
                    ),
                ):
                    gui_windows.MainWindow.handle_routine_instance_buy_limit_double_click(
                        host,
                        label,
                    )
                repository.update_buy_limit.assert_called_once_with(
                    "instance-a",
                    enabled=expected_enabled,
                    amount=expected_amount,
                )

    def test_manual_boundaries_and_exact_adjustment_ratio(self):
        for amount in (900_000, 5_000_000):
            with self.subTest(amount=amount):
                host, _editor, _label = self._routine_host(str(amount))
                repository = MagicMock()
                repository.update_buy_limit.return_value = SimpleNamespace(
                    success=True,
                    error="",
                )
                with (
                    patch.object(
                        gui_windows,
                        "routine_instance_suggested_buy_limits",
                        return_value=(4_000_000, 900_000),
                    ),
                    patch.object(
                        gui_windows,
                        "_system_total_budget_amount",
                        return_value=5_000_000,
                    ),
                    patch.object(
                        gui_windows,
                        "RoutineInstanceRepository",
                        return_value=repository,
                    ),
                ):
                    gui_windows.MainWindow.finish_routine_instance_buy_limit_edit(
                        host,
                        save=True,
                    )
                adjustment_ratio = repository.update_buy_limit.call_args.kwargs[
                    "adjustment_ratio"
                ]
                self.assertIsInstance(adjustment_ratio, Decimal)
                self.assertEqual(Decimal(amount) / Decimal(4_000_000), adjustment_ratio)

    def test_manual_below_minimum_toasts_and_preserves_limit(self):
        host, _editor, _label = self._routine_host("899999")
        with (
            patch.object(
                gui_windows,
                "routine_instance_suggested_buy_limits",
                return_value=(4_000_000, 900_000),
            ),
            patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=5_000_000,
            ),
            patch.object(gui_windows, "RoutineInstanceRepository") as repository,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.finish_routine_instance_buy_limit_edit(
                host,
                save=True,
            )
        repository.assert_not_called()
        toast.assert_called_once_with(
            host,
            "최저금액은 900,000원입니다.",
            duration_ms=2500,
        )

    def test_manual_over_total_disables_and_clears_ratio(self):
        host, _editor, _label = self._routine_host("5000001")
        repository = MagicMock()
        repository.update_buy_limit.return_value = SimpleNamespace(success=True, error="")
        with (
            patch.object(
                gui_windows,
                "routine_instance_suggested_buy_limits",
                return_value=(4_000_000, 900_000),
            ),
            patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=5_000_000,
            ),
            patch.object(
                gui_windows,
                "RoutineInstanceRepository",
                return_value=repository,
            ),
        ):
            gui_windows.MainWindow.finish_routine_instance_buy_limit_edit(
                host,
                save=True,
            )
        repository.update_buy_limit.assert_called_once_with(
            "instance-a",
            enabled=False,
            amount=None,
        )

    def test_invalid_manual_inputs_preserve_existing_ratio(self):
        for text in ("", "invalid"):
            host, _editor, _label = self._routine_host(text)
            with (
                patch.object(gui_windows, "RoutineInstanceRepository") as repository,
                patch.object(
                    gui_windows,
                    "routine_instance_suggested_buy_limits",
                ) as amounts,
            ):
                gui_windows.MainWindow.finish_routine_instance_buy_limit_edit(
                    host,
                    save=True,
                )
            repository.assert_not_called()
            amounts.assert_not_called()

    def test_unavailable_calculation_or_budget_preserves_existing_ratio(self):
        for amounts, total_budget in (
            ((None, None), 5_000_000),
            ((4_000_000, 900_000), None),
        ):
            host, _editor, _label = self._routine_host("1000000")
            with (
                patch.object(
                    gui_windows,
                    "routine_instance_suggested_buy_limits",
                    return_value=amounts,
                ),
                patch.object(
                    gui_windows,
                    "_system_total_budget_amount",
                    return_value=total_budget,
                ),
                patch.object(gui_windows, "RoutineInstanceRepository") as repository,
            ):
                gui_windows.MainWindow.finish_routine_instance_buy_limit_edit(
                    host,
                    save=True,
                )
            repository.assert_not_called()

    def test_stock_recommendation_and_manual_over_total_disable_only_target(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "First" / "config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps({"buy_limit_enabled": False}),
                encoding="utf-8",
            )
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(return_value=config_path),
                finish_routine_instance_buy_limit_edit=MagicMock(),
                finish_routine_stock_buy_limit_edit=MagicMock(),
                _stock_suggested_buy_limit=MagicMock(return_value=5_000_001),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
            )
            with patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=5_000_000,
            ):
                gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                    host,
                    0,
                )
            host._write_stock_buy_limit_config.assert_called_once_with(
                config_path,
                enabled=False,
                amount=None,
            )

        editor = QLineEdit("5000001")
        manual_host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/target/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="target",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=900_000),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )
        with patch.object(
            gui_windows,
            "_system_total_budget_amount",
            return_value=5_000_000,
        ):
            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                manual_host,
                save=True,
            )
        manual_host._write_stock_buy_limit_config.assert_called_once_with(
            Path("C:/target/config.json"),
            enabled=False,
            amount=None,
        )

    def test_stock_values_equal_to_total_budget_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "First" / "config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps({"buy_limit_enabled": False}),
                encoding="utf-8",
            )
            host = SimpleNamespace(
                _stock_config_path_for_routine_row=MagicMock(return_value=config_path),
                finish_routine_instance_buy_limit_edit=MagicMock(),
                finish_routine_stock_buy_limit_edit=MagicMock(),
                _stock_suggested_buy_limit=MagicMock(return_value=5_000_000),
                _write_stock_buy_limit_config=MagicMock(),
                load_routine_table=MagicMock(),
            )
            with patch.object(
                gui_windows,
                "_system_total_budget_amount",
                return_value=5_000_000,
            ):
                gui_windows.MainWindow.handle_routine_stock_buy_limit_double_click(
                    host,
                    0,
                )
            host._write_stock_buy_limit_config.assert_called_once_with(
                config_path,
                enabled=True,
                amount=5_000_000,
            )

        editor = QLineEdit("5000000")
        manual_host = SimpleNamespace(
            _routine_stock_buy_limit_editor=editor,
            _routine_stock_buy_limit_edit_finishing=False,
            _routine_stock_buy_limit_editor_config_path="C:/target/config.json",
            routine_table=SimpleNamespace(
                _editing_stock_buy_limit_path="target",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            ),
            _parse_buy_limit_amount=gui_windows.MainWindow._parse_buy_limit_amount,
            _stock_suggested_buy_limit=MagicMock(return_value=900_000),
            _write_stock_buy_limit_config=MagicMock(),
            load_routine_table=MagicMock(),
        )
        with patch.object(
            gui_windows,
            "_system_total_budget_amount",
            return_value=5_000_000,
        ):
            gui_windows.MainWindow.finish_routine_stock_buy_limit_edit(
                manual_host,
                save=True,
            )
        manual_host._write_stock_buy_limit_config.assert_called_once_with(
            Path("C:/target/config.json"),
            enabled=True,
            amount=5_000_000,
        )


if __name__ == "__main__":
    unittest.main()
