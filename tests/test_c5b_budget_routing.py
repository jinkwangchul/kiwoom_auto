# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from budget_command import (
    BUDGET_MODE_AMOUNT,
    BUDGET_MODE_CHANGE,
    BudgetValueChangeRequest,
    CURRENT_PRICE_UNAVAILABLE,
    BudgetModeChangeRequest,
    execute_budget_mode_change,
    inspect_budget_value_entry,
)
from tests.participant_owner_fixture import participant_owner


class BudgetRoutingCommandTest(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path) -> Path:
        stock_dir = root / "stocks" / "005930_삼성전자"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "trade_amount_type": "QUANTITY",
                    "buy_qty": 7,
                    "buy_amount": 100_000,
                }
            ),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "STOPPED", "trade_enabled": False}),
            encoding="utf-8",
        )
        return stock_dir / "config.json"

    @staticmethod
    def _host(*, fresh: bool = True, running: bool = False):
        market = SimpleNamespace(
            last_price=70_000,
            login_session_id="SESSION-1",
            connection_epoch=1,
        )
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=(
                lambda _code: market if fresh else None
            ),
        )
        return SimpleNamespace(
            main_monitoring_auto_trade_operation_host=lambda: operation_host,
            _main_monitoring_auto_trade_operation_host=participant_owner(
                {"005930"} if running else ()
            ),
        )

    def test_mode_change_uses_one_command_and_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            writer = MagicMock(
                return_value={"allowed": True, "changed": True, "reason": ""}
            )
            result = execute_budget_mode_change(
                self._host(),
                BudgetModeChangeRequest(config_path, BUDGET_MODE_AMOUNT),
                writer=writer,
                default_value_reader=lambda _host, _path, _mode: 100_000,
            )

            self.assertEqual(BUDGET_MODE_CHANGE, result["command"])
            self.assertTrue(result["allowed"])
            writer.assert_called_once_with(
                config_path,
                mode=BUDGET_MODE_AMOUNT,
                value=100_000,
                expected_fields={"trade_amount_type": "QUANTITY"},
            )

    def test_mode_change_without_fresh_price_does_not_open_or_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            writer = MagicMock()
            result = execute_budget_mode_change(
                self._host(fresh=False),
                BudgetModeChangeRequest(config_path, BUDGET_MODE_AMOUNT),
                writer=writer,
            )

            self.assertFalse(result["allowed"])
            self.assertEqual(CURRENT_PRICE_UNAVAILABLE, result["reason"])
            writer.assert_not_called()

    def test_mode_change_current_running_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            (config_path.parent / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            writer = MagicMock()
            result = execute_budget_mode_change(
                self._host(running=True),
                BudgetModeChangeRequest(config_path, BUDGET_MODE_AMOUNT),
                writer=writer,
            )

            self.assertFalse(result["allowed"])
            self.assertEqual("START_BUDGET_MUTATION_BLOCKED", result["reason"])
            writer.assert_not_called()

    def test_value_entry_availability_is_read_only_and_shared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            before = config_path.read_bytes()
            availability = inspect_budget_value_entry(
                self._host(),
                BudgetValueChangeRequest(config_path),
            )

            self.assertTrue(availability["allowed"])
            self.assertEqual(70_000, availability["current_price"])
            self.assertEqual(before, config_path.read_bytes())

            unavailable = inspect_budget_value_entry(
                self._host(fresh=False),
                BudgetValueChangeRequest(config_path),
            )
            self.assertFalse(unavailable["allowed"])
            self.assertEqual(CURRENT_PRICE_UNAVAILABLE, unavailable["reason"])
            self.assertEqual(before, config_path.read_bytes())

    def test_settings_like_caller_uses_persistent_main_price_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            main_host = self._host()
            settings_host = SimpleNamespace()

            with patch(
                "budget_command.persistent_feature_owner",
                return_value=main_host,
            ) as owner_resolver:
                availability = inspect_budget_value_entry(
                    settings_host,
                    BudgetValueChangeRequest(config_path),
                )

            self.assertTrue(availability["allowed"])
            self.assertEqual(70_000, availability["current_price"])
            owner_resolver.assert_called_once_with(settings_host)


if __name__ == "__main__":
    unittest.main()
