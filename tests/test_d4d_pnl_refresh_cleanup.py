# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PyQt5.QtGui import QBrush, QColor

import gui_auto_trade_setting_window as setting_window
import gui_main_table_loader as main_loader
import pnl_ui_refresh


class _CodeItem:
    def __init__(self, code: str) -> None:
        self._code = code

    def text(self) -> str:
        return self._code


class _PnlItem:
    def __init__(self) -> None:
        self._text = "-"
        self._color = QColor("#000000")
        self.text_write_count = 0
        self.color_write_count = 0

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:
        self._text = value
        self.text_write_count += 1

    def foreground(self) -> QBrush:
        return QBrush(self._color)

    def setForeground(self, color: QColor) -> None:
        self._color = QColor(color)
        self.color_write_count += 1


class _StockTable:
    def __init__(self, codes: list[str]) -> None:
        self.rows = [(_CodeItem(code), _PnlItem()) for code in codes]

    def rowCount(self) -> int:
        return len(self.rows)

    def item(self, row: int, column: int):
        if column == 0:
            return self.rows[row][0]
        if column == 9:
            return self.rows[row][1]
        return None


class _EmptyTable:
    @staticmethod
    def rowCount() -> int:
        return 0


class D4dPnlRefreshCleanupTests(unittest.TestCase):
    def test_preloaded_batch_skips_stock_directory_and_state_rereads(self) -> None:
        states = {
            "000001": {"current_price": 10_000},
            "000002": {"current_price": 20_000},
        }
        snapshot = MagicMock()
        projected_states: list[tuple[str, dict[str, object]]] = []

        def project(code, state, loaded_snapshot):
            self.assertIs(snapshot, loaded_snapshot)
            projected_states.append((code, state))
            return {"available": True, "cumulative_profit": 0}

        with (
            patch.object(
                pnl_ui_refresh,
                "load_confirmable_pnl_runtime_snapshot",
                return_value=snapshot,
            ) as runtime_snapshot,
            patch.object(
                pnl_ui_refresh.StockRepository,
                "list_stock_dirs",
            ) as list_stock_dirs,
            patch.object(pnl_ui_refresh, "read_json_dict") as read_state,
            patch.object(
                pnl_ui_refresh,
                "_project_current_stock_pnl_from_state",
                side_effect=project,
            ),
        ):
            result = pnl_ui_refresh.project_current_stock_pnl_snapshot(
                states,
                project_root=Path("."),
                state_by_code=states,
            )

        self.assertEqual({"000001", "000002"}, set(result))
        self.assertEqual(
            [("000001", states["000001"]), ("000002", states["000002"])],
            projected_states,
        )
        runtime_snapshot.assert_called_once()
        list_stock_dirs.assert_not_called()
        read_state.assert_not_called()

    def test_settings_tick_uses_one_batch_and_skips_unchanged_cell_writes(self) -> None:
        table = _StockTable(["A000001", "000002", "000003"])
        host = SimpleNamespace(stock_table=table)
        seen_batches: list[list[str]] = []

        def project(codes, *, project_root):
            self.assertEqual(setting_window.PROJECT_ROOT, project_root)
            clean_codes = list(codes)
            seen_batches.append(clean_codes)
            return {
                code: {
                    "available": True,
                    "cumulative_profit": index * 1_000,
                    "cumulative_rate": float(index),
                }
                for index, code in enumerate(clean_codes, start=1)
            }

        with patch.object(
            setting_window,
            "project_current_stock_pnl_snapshot",
            side_effect=project,
        ) as batch:
            setting_window.AutoTradeSettingWindow.refresh_stock_pnl_cells(host)
            first_counts = [
                (item.text_write_count, item.color_write_count)
                for _code, item in table.rows
            ]
            setting_window.AutoTradeSettingWindow.refresh_stock_pnl_cells(host)

        self.assertEqual(
            [["000001", "000002", "000003"]] * 2,
            seen_batches,
        )
        self.assertEqual(2, batch.call_count)
        self.assertEqual(
            first_counts,
            [
                (item.text_write_count, item.color_write_count)
                for _code, item in table.rows
            ],
        )
        self.assertTrue(all(text_count == 1 for text_count, _color_count in first_counts))

    def test_main_tick_reuses_one_state_and_one_pnl_snapshot_across_groups(self) -> None:
        codes = ["000001", "000002", "000003"]
        stocks = tuple(
            {
                "stock_path": f"stocks/{code}_Test",
                "stock_dir": Path(f"stocks/{code}_Test"),
                "stock_dir_key": str(Path(f"stocks/{code}_Test")),
                "instance_id": "instance-a",
                "operation_excluded": False,
                "code": code,
                "name": f"Stock {code}",
                "enabled": True,
                "routines": ("Routine",),
                "assigned_routine_instance_id": "instance-a",
            }
            for code in codes
        )
        instance = SimpleNamespace(
            instance_id="instance-a",
            definition_id="definition-a",
        )
        projected_instance = SimpleNamespace(
            instance_id="instance-a",
            stocks=stocks,
        )
        projected_group = SimpleNamespace(
            group_id="group-a",
            instances=(projected_instance,),
        )
        static_cache = {
            "definitions": (SimpleNamespace(definition_id="definition-a"),),
            "instances": (instance,),
            "groups": (SimpleNamespace(),),
            "stocks": stocks,
        }
        state_reads: list[str] = []
        batch_calls: list[dict[str, dict[str, object]]] = []

        def inspect(stock_dir: Path):
            code = stock_dir.name.partition("_")[0]
            state_reads.append(code)
            return SimpleNamespace(
                state={"status": "STOPPED", "current_price": 10_000},
                review_required=False,
                issue_reason="",
            )

        def project(codes_arg, *, project_root, state_by_code):
            self.assertTrue(project_root.name == "kiwoom_auto")
            batch_calls.append(dict(state_by_code))
            return {
                code: {
                    "available": True,
                    "cumulative_profit": 1_000,
                    "cumulative_rate": 1.0,
                    "completed_buy_cost": 100_000,
                    "open_cost": 0,
                }
                for code in codes_arg
            }

        host = SimpleNamespace(
            routine_table=_EmptyTable(),
            _update_main_routine_summary=MagicMock(),
        )
        with (
            patch.object(
                main_loader,
                "_main_pnl_refresh_static_cache",
                return_value=static_cache,
            ),
            patch.object(
                main_loader,
                "_main_pnl_refresh_routine_metadata",
                return_value=(list(static_cache["definitions"]), [instance]),
            ),
            patch.object(main_loader, "inspect_stock_review_state", side_effect=inspect),
            patch.object(
                main_loader,
                "auto_trade_stock_operation_category",
                return_value="waiting",
            ),
            patch.object(
                main_loader,
                "project_current_stock_pnl_snapshot",
                side_effect=project,
            ),
            patch.object(
                main_loader,
                "build_main_group_projection",
                return_value=(projected_group,),
            ),
        ):
            main_loader.main_refresh_pnl_only(host)

        self.assertEqual(codes, state_reads)
        self.assertEqual(1, len(batch_calls))
        self.assertEqual(set(codes), set(batch_calls[0]))


if __name__ == "__main__":
    unittest.main()
