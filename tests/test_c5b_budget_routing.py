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
            field_sources=(("last_price", "SNAPSHOT"),),
        )
        operation_host = SimpleNamespace(
            configuration_market_information_state=(
                lambda _code: market if fresh else None
            ),
            fresh_monitoring_market_information_state=(
                lambda _code: None
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

    def test_mode_change_without_configuration_price_does_not_write(self) -> None:
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
            self.assertTrue(unavailable["allowed"])
            self.assertEqual("", unavailable["reason"])
            self.assertIsNone(unavailable["current_price"])
            self.assertEqual(before, config_path.read_bytes())

            (config_path.parent / "state.json").write_text(
                json.dumps({"current_price": 99_999}),
                encoding="utf-8",
            )
            persisted_only = inspect_budget_value_entry(
                self._host(fresh=False),
                BudgetValueChangeRequest(config_path),
            )
            self.assertIsNone(persisted_only["current_price"])

    def test_amount_to_quantity_does_not_resolve_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["trade_amount_type"] = "AMOUNT"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            writer = MagicMock(
                return_value={"allowed": True, "changed": True, "reason": ""}
            )
            price_reader = MagicMock(return_value=None)

            result = execute_budget_mode_change(
                self._host(fresh=False),
                BudgetModeChangeRequest(config_path, "QUANTITY"),
                writer=writer,
                configuration_price_reader=price_reader,
            )

            self.assertTrue(result["allowed"], result)
            self.assertEqual(1, result["value"])
            price_reader.assert_not_called()
            writer.assert_called_once_with(
                config_path,
                mode="QUANTITY",
                value=1,
                expected_fields={"trade_amount_type": "AMOUNT"},
            )

    def test_quantity_to_amount_uses_snapshot_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            writer = MagicMock(
                return_value={"allowed": True, "changed": True, "reason": ""}
            )
            state = SimpleNamespace(
                last_price=70_000,
                field_sources=(("last_price", "SNAPSHOT"),),
            )
            price_reader = MagicMock(return_value=state)
            host = self._host()

            result = execute_budget_mode_change(
                host,
                BudgetModeChangeRequest(config_path, "AMOUNT"),
                writer=writer,
                configuration_price_reader=price_reader,
            )

            self.assertTrue(result["allowed"], result)
            self.assertEqual(105_000, result["value"])
            self.assertEqual("SNAPSHOT", result["price_source"])
            price_reader.assert_called_once_with(host, config_path)

    def test_configuration_commands_issue_no_broker_or_order_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            host = self._host()
            broker = SimpleNamespace(
                CommRqData=MagicMock(),
                SetRealReg=MagicMock(),
                SendOrder=MagicMock(),
                dynamicCall=MagicMock(),
            )
            host.kiwoom_api = broker
            writer = MagicMock(
                return_value={"allowed": True, "changed": True, "reason": ""}
            )

            result = execute_budget_mode_change(
                host,
                BudgetModeChangeRequest(config_path, "AMOUNT"),
                writer=writer,
            )
            availability = inspect_budget_value_entry(
                host,
                BudgetValueChangeRequest(config_path),
            )

            self.assertTrue(result["allowed"], result)
            self.assertTrue(availability["allowed"])
            broker.CommRqData.assert_not_called()
            broker.SetRealReg.assert_not_called()
            broker.SendOrder.assert_not_called()
            broker.dynamicCall.assert_not_called()

    def test_quantity_to_amount_accepts_realtime_configuration_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._fixture(Path(temp_dir))
            writer = MagicMock(
                return_value={"allowed": True, "changed": True, "reason": ""}
            )
            state = SimpleNamespace(
                last_price=71_000,
                field_sources=(("last_price", "REALTIME"),),
            )

            result = execute_budget_mode_change(
                self._host(),
                BudgetModeChangeRequest(config_path, "AMOUNT"),
                writer=writer,
                configuration_price_reader=MagicMock(return_value=state),
            )

            self.assertTrue(result["allowed"], result)
            self.assertEqual(106_500, result["value"])
            self.assertEqual("REALTIME", result["price_source"])

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
