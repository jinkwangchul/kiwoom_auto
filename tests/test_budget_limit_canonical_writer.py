# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import gui_windows
from runtime_io import read_json_dict
from stock_repository import (
    STOCK_CONFIG_WRITE_FIELD_CONFLICT,
    STOCK_CONFIG_WRITE_INVALID_CONFIG,
    StockConfigWriteResult,
)


class BudgetLimitCanonicalWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.stock_dir = Path(self.temp.name) / "stocks" / "005930_삼성전자"
        self.stock_dir.mkdir(parents=True)
        self.config_path = self.stock_dir / "config.json"
        self.config = {
            "trade_amount_type": "AMOUNT",
            "buy_amount": 100_000,
            "buy_qty": 7,
            "buy_limit_enabled": True,
            "buy_limit_amount": 1_000_000,
            "buy_limit_source": "MANUAL",
            "unrelated": {"owner": "fixture"},
        }
        self._write(self.config)
        (self.stock_dir / "state.json").write_text(
            json.dumps({"status": "STOPPED", "trade_enabled": False}),
            encoding="utf-8",
        )
        self.host = SimpleNamespace()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, payload: dict[str, object]) -> None:
        self.config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _decision(*, changed: bool) -> dict[str, object]:
        return {
            "allowed": True,
            "changed": changed,
            "current_running": False,
            "reason": "",
        }

    def test_budget_and_limit_use_independent_patches_and_preserve_inactive_value(self) -> None:
        expected = gui_windows.MainWindow._start_budget_config_expected_fields(
            self.config,
            mode="AMOUNT",
            apply_limit=True,
        )
        original_patch = (
            gui_windows.CanonicalStockConfigRepository.patch_stock_config
        )
        with (
            patch.object(
                gui_windows,
                "auto_trade_start_budget_mutation_decision",
                return_value=self._decision(changed=True),
            ),
            patch.object(
                gui_windows.CanonicalStockConfigRepository,
                "patch_stock_config",
                autospec=True,
                side_effect=original_patch,
            ) as canonical_patch,
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=200_000,
                apply_limit=True,
                adjusted_limit_amount=2_000_000,
                expected_fields=expected,
            )

        self.assertTrue(result["allowed"], result)
        self.assertEqual(2, canonical_patch.call_count)
        budget_patch = canonical_patch.call_args_list[0].args[2]
        limit_patch = canonical_patch.call_args_list[1].args[2]
        self.assertEqual(200_000, budget_patch["buy_amount"])
        self.assertNotIn("buy_limit_amount", budget_patch)
        self.assertEqual(2_000_000, limit_patch["buy_limit_amount"])
        self.assertNotIn("buy_amount", limit_patch)
        saved = read_json_dict(self.config_path)
        self.assertEqual(7, saved["buy_qty"])
        self.assertEqual({"owner": "fixture"}, saved["unrelated"])

    def test_checked_recalculation_preserves_disabled_limit_contract(self) -> None:
        disabled = dict(self.config)
        disabled.update(
            {
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "buy_limit_source": None,
            }
        )
        self._write(disabled)
        with patch.object(
            gui_windows,
            "auto_trade_start_budget_mutation_decision",
            return_value=self._decision(changed=True),
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=200_000,
                apply_limit=True,
                adjusted_limit_amount=2_000_000,
            )

        saved = read_json_dict(self.config_path)
        self.assertTrue(result["allowed"], result)
        self.assertFalse(result["limit_applied"])
        self.assertFalse(saved["buy_limit_enabled"])
        self.assertIsNone(saved["buy_limit_amount"])
        self.assertIsNone(saved["buy_limit_source"])
        self.assertEqual(200_000, saved["buy_amount"])

    def test_limit_evidence_failure_does_not_block_budget_patch(self) -> None:
        with patch.object(
            gui_windows,
            "auto_trade_start_budget_mutation_decision",
            return_value=self._decision(changed=True),
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=200_000,
                apply_limit=True,
                adjusted_limit_amount=None,
            )

        saved = read_json_dict(self.config_path)
        self.assertTrue(result["allowed"], result)
        self.assertFalse(result["limit_applied"])
        self.assertEqual("INVALID_ADJUSTED_LIMIT", result["limit_reason"])
        self.assertEqual(200_000, saved["buy_amount"])
        self.assertTrue(saved["buy_limit_enabled"])
        self.assertEqual(1_000_000, saved["buy_limit_amount"])
        self.assertEqual("MANUAL", saved["buy_limit_source"])

    def test_limit_writer_conflict_keeps_successful_budget_patch(self) -> None:
        conflict = StockConfigWriteResult(
            ok=False,
            changed=False,
            field_keys=(
                "buy_limit_enabled",
                "buy_limit_amount",
                "buy_limit_source",
            ),
            conflict_detected=True,
            read_back_verified=False,
            reason_code=STOCK_CONFIG_WRITE_FIELD_CONFLICT,
        )
        with (
            patch.object(
                gui_windows,
                "auto_trade_start_budget_mutation_decision",
                return_value=self._decision(changed=True),
            ),
            patch.object(
                gui_windows.MainWindow,
                "_write_stock_buy_limit_config",
                return_value=conflict,
            ),
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=200_000,
                apply_limit=True,
                adjusted_limit_amount=2_000_000,
            )

        saved = read_json_dict(self.config_path)
        self.assertTrue(result["allowed"], result)
        self.assertFalse(result["limit_applied"])
        self.assertEqual(
            STOCK_CONFIG_WRITE_FIELD_CONFLICT,
            result["limit_reason"],
        )
        self.assertEqual(200_000, saved["buy_amount"])
        self.assertEqual(1_000_000, saved["buy_limit_amount"])

    def test_quantity_patch_preserves_inactive_amount(self) -> None:
        with patch.object(
            gui_windows,
            "auto_trade_start_budget_mutation_decision",
            return_value=self._decision(changed=True),
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="QUANTITY",
                value=11,
            )

        self.assertTrue(result["allowed"], result)
        saved = read_json_dict(self.config_path)
        self.assertEqual("QUANTITY", saved["trade_amount_type"])
        self.assertEqual(11, saved["buy_qty"])
        self.assertEqual(100_000, saved["buy_amount"])

    def test_budget_no_change_does_not_call_repository(self) -> None:
        before = self.config_path.read_bytes()
        with (
            patch.object(
                gui_windows,
                "auto_trade_start_budget_mutation_decision",
                return_value=self._decision(changed=False),
            ),
            patch.object(
                gui_windows.CanonicalStockConfigRepository,
                "patch_stock_config",
            ) as canonical_patch,
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=100_000,
            )

        self.assertTrue(result["allowed"], result)
        self.assertFalse(result["changed"])
        canonical_patch.assert_not_called()
        self.assertEqual(before, self.config_path.read_bytes())

    def test_budget_same_field_conflict_blocks_but_unrelated_change_merges(self) -> None:
        expected = gui_windows.MainWindow._start_budget_config_expected_fields(
            self.config,
            mode="AMOUNT",
            apply_limit=False,
        )
        latest = dict(self.config)
        latest["unrelated"] = {"owner": "other-writer"}
        self._write(latest)
        with patch.object(
            gui_windows,
            "auto_trade_start_budget_mutation_decision",
            return_value=self._decision(changed=True),
        ):
            merged = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=200_000,
                expected_fields=expected,
            )
        self.assertTrue(merged["allowed"], merged)
        self.assertEqual(
            {"owner": "other-writer"},
            read_json_dict(self.config_path)["unrelated"],
        )

        conflict_expected = gui_windows.MainWindow._start_budget_config_expected_fields(
            read_json_dict(self.config_path),
            mode="AMOUNT",
            apply_limit=False,
        )
        concurrent = read_json_dict(self.config_path)
        concurrent["buy_amount"] = 250_000
        self._write(concurrent)
        with patch.object(
            gui_windows,
            "auto_trade_start_budget_mutation_decision",
            return_value=self._decision(changed=True),
        ):
            conflict = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=300_000,
                expected_fields=conflict_expected,
            )
        self.assertFalse(conflict["allowed"])
        self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, conflict["reason"])
        self.assertEqual(250_000, read_json_dict(self.config_path)["buy_amount"])

    def test_budget_invalid_config_fails_closed_without_replacement(self) -> None:
        invalid_payload = b'{"trade_amount_type": "AMOUNT"'
        self.config_path.write_bytes(invalid_payload)
        with patch.object(
            gui_windows,
            "auto_trade_start_budget_mutation_decision",
            return_value=self._decision(changed=True),
        ):
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self.host,
                self.config_path,
                mode="AMOUNT",
                value=200_000,
            )

        self.assertFalse(result["allowed"])
        self.assertEqual(STOCK_CONFIG_WRITE_INVALID_CONFIG, result["reason"])
        self.assertEqual(invalid_payload, self.config_path.read_bytes())

    def test_limit_no_change_has_no_file_write(self) -> None:
        before = self.config_path.read_bytes()
        result = gui_windows.MainWindow._write_stock_buy_limit_config(
            self.config_path,
            enabled=True,
            amount=1_000_000,
            source="MANUAL",
        )

        self.assertTrue(result.ok, result)
        self.assertFalse(result.changed)
        self.assertEqual(before, self.config_path.read_bytes())

    def test_limit_same_field_conflict_blocks_and_unrelated_change_merges(self) -> None:
        limit_fields = (
            "buy_limit_enabled",
            "buy_limit_amount",
            "buy_limit_source",
        )
        expected = gui_windows.MainWindow._stock_config_expected_fields(
            self.config,
            limit_fields,
        )
        latest = dict(self.config)
        latest["unrelated"] = {"owner": "other-writer"}
        self._write(latest)
        merged = gui_windows.MainWindow._write_stock_buy_limit_config(
            self.config_path,
            enabled=True,
            amount=2_000_000,
            source="MANUAL",
            expected_fields=expected,
        )
        self.assertTrue(merged.ok, merged)
        self.assertEqual(
            {"owner": "other-writer"},
            read_json_dict(self.config_path)["unrelated"],
        )

        conflict_expected = gui_windows.MainWindow._stock_config_expected_fields(
            read_json_dict(self.config_path),
            limit_fields,
        )
        concurrent = read_json_dict(self.config_path)
        concurrent["buy_limit_amount"] = 2_500_000
        self._write(concurrent)
        conflict = gui_windows.MainWindow._write_stock_buy_limit_config(
            self.config_path,
            enabled=True,
            amount=3_000_000,
            source="MANUAL",
            expected_fields=conflict_expected,
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, conflict.reason_code)
        self.assertEqual(
            2_500_000,
            read_json_dict(self.config_path)["buy_limit_amount"],
        )


if __name__ == "__main__":
    unittest.main()
