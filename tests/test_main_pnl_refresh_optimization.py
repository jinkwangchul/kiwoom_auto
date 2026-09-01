# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui_auto_trade_display import RatioMetricDisplay
import gui_main_table_loader as table_loader
from gui_windows import MainWindow


class _EmptyTable:
    @staticmethod
    def rowCount() -> int:
        return 0


class _CountingLabel:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self.set_text_count = 0

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text
        self.set_text_count += 1


class _StockItem:
    def __init__(self, metrics: tuple[RatioMetricDisplay, ...]) -> None:
        self.metrics = metrics
        self.set_data_count = 0

    def data(self, role: int):
        if role == table_loader.ROUTINE_ROW_KIND_ROLE:
            return table_loader.ROUTINE_ROW_STOCK
        if role == table_loader.ROUTINE_STOCK_CODE_ROLE:
            return "000001"
        if role == table_loader.ROUTINE_STOCK_METRICS_ROLE:
            return self.metrics
        return None

    def setData(self, role: int, value) -> None:
        if role == table_loader.ROUTINE_STOCK_METRICS_ROLE:
            self.metrics = value
            self.set_data_count += 1


class _Viewport:
    def __init__(self) -> None:
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1


class _SingleRowTable:
    def __init__(self, item: _StockItem) -> None:
        self._item = item
        self._viewport = _Viewport()

    @staticmethod
    def rowCount() -> int:
        return 1

    def item(self, row: int, column: int):
        return self._item if row == 0 and column == 0 else None

    @staticmethod
    def cellWidget(_row: int, _column: int):
        return None

    def viewport(self) -> _Viewport:
        return self._viewport


class MainPnlRefreshOptimizationTests(unittest.TestCase):
    def test_consecutive_ticks_reuse_static_metadata_and_preloaded_state(self) -> None:
        instance = SimpleNamespace(instance_id="instance-a")
        window = SimpleNamespace(
            routine_table=_EmptyTable(),
            _update_main_routine_summary=MagicMock(),
        )
        read_counts = {"config": 0, "state": 0}

        def read_json(path: Path) -> dict[str, object]:
            if Path(path).name == "config.json":
                read_counts["config"] += 1
                return {"assigned_routine_instance_id": "instance-a"}
            read_counts["state"] += 1
            return {"status": "STOPPED", "trade_enabled": False}

        definitions_loader = MagicMock(return_value=[SimpleNamespace(definition_id="d")])
        instances_loader = MagicMock(return_value=[instance])
        base_loader = MagicMock(
            return_value=[
                {
                    "code": "000001",
                    "name": "stock",
                    "stock_path": "stocks/000001_stock",
                }
            ]
        )
        group_loader = MagicMock(return_value=[])
        category_projector = MagicMock(return_value="waiting")
        batch_projector = MagicMock(return_value={})

        def inspect_state(stock_dir: Path):
            return SimpleNamespace(
                state=read_json(Path(stock_dir) / "state.json"),
                review_required=False,
                issue_reason="",
            )

        with (
            patch.object(table_loader, "load_routine_definitions", definitions_loader),
            patch.object(table_loader, "load_persisted_routine_instances", instances_loader),
            patch.object(table_loader, "get_group_records", group_loader),
            patch.object(table_loader, "read_base_stocks", base_loader),
            patch.object(table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                table_loader,
                "inspect_stock_review_state",
                side_effect=inspect_state,
            ),
            patch.object(
                table_loader,
                "auto_trade_stock_operation_category",
                category_projector,
            ),
            patch.object(
                table_loader,
                "project_current_stock_pnl_snapshot",
                batch_projector,
            ),
        ):
            table_loader.main_refresh_pnl_only(window)
            table_loader.main_refresh_pnl_only(window)

        self.assertEqual(1, definitions_loader.call_count)
        self.assertEqual(1, instances_loader.call_count)
        self.assertEqual(1, group_loader.call_count)
        self.assertEqual(1, base_loader.call_count)
        self.assertEqual(1, read_counts["config"])
        self.assertEqual(2, read_counts["state"])
        self.assertEqual(2, batch_projector.call_count)
        for call in batch_projector.call_args_list:
            self.assertEqual(
                {"000001": {"status": "STOPPED", "trade_enabled": False}},
                call.kwargs["state_by_code"],
            )
        self.assertEqual(2, category_projector.call_count)
        for call in category_projector.call_args_list:
            self.assertEqual("000001", call.kwargs["stock_code"])
            self.assertFalse(call.kwargs["persisted_trade_started"])

    def test_invalidation_reloads_changed_instance_and_assignment_metadata(self) -> None:
        window = SimpleNamespace()
        instances_loader = MagicMock(
            side_effect=[
                [SimpleNamespace(instance_id="instance-a", display_name="before")],
                [SimpleNamespace(instance_id="instance-b", display_name="after")],
            ]
        )
        base_loader = MagicMock(
            side_effect=[
                [{"code": "000001", "name": "A", "stock_path": "stocks/A"}],
                [{"code": "000002", "name": "B", "stock_path": "stocks/B"}],
            ]
        )
        group_loader = MagicMock(
            side_effect=[
                [SimpleNamespace(group_id="group-before")],
                [SimpleNamespace(group_id="group-after")],
            ]
        )
        config_by_parent = {
            "A": {"assigned_routine_instance_id": "instance-a"},
            "B": {"assigned_routine_instance_id": "instance-b"},
        }
        with (
            patch.object(table_loader, "load_routine_definitions", return_value=[]),
            patch.object(table_loader, "load_persisted_routine_instances", instances_loader),
            patch.object(table_loader, "get_group_records", group_loader),
            patch.object(table_loader, "read_base_stocks", base_loader),
            patch.object(
                table_loader,
                "read_json_dict",
                side_effect=lambda path: config_by_parent[Path(path).parent.name],
            ),
        ):
            first = table_loader._main_pnl_refresh_static_cache(window)
            reused = table_loader._main_pnl_refresh_static_cache(window)
            table_loader._invalidate_main_pnl_refresh_cache(window)
            refreshed = table_loader._main_pnl_refresh_static_cache(window)

        self.assertIs(first, reused)
        self.assertEqual("group-before", first["groups"][0].group_id)
        self.assertEqual("instance-a", first["instances"][0].instance_id)
        self.assertEqual("000001", first["stocks"][0]["code"])
        self.assertEqual("group-after", refreshed["groups"][0].group_id)
        self.assertEqual("instance-b", refreshed["instances"][0].instance_id)
        self.assertEqual("000002", refreshed["stocks"][0]["code"])
        self.assertEqual(2, instances_loader.call_count)
        self.assertEqual(2, group_loader.call_count)
        self.assertEqual(2, base_loader.call_count)

    def test_unchanged_summary_skips_all_label_and_style_setters(self) -> None:
        labels = {
            key: (_CountingLabel(label_text), _CountingLabel("0"))
            for key, label_text in (
                ("group", "그룹"),
                ("routine", "루틴"),
                ("stock", "종목"),
                ("operation", "정지"),
                ("excluded", "제외"),
                ("review", "검토"),
            )
        }
        host = SimpleNamespace(_main_routine_summary_count_labels=labels)
        first = {
            "count_badges": (
                ("group", "그룹", 1),
                ("routine", "루틴", 2),
                ("stock", "종목", 3),
                ("operation", "정지", 3),
                ("excluded", "제외", 0),
                ("review", "검토", 0),
            )
        }
        changed = dict(first)
        changed["count_badges"] = first["count_badges"][:-1] + (("review", "검토", 1),)
        with patch.object(MainWindow, "_update_main_routine_summary_badge_styles") as styles:
            MainWindow._update_main_routine_summary(host, first)
            counts_after_first = {
                key: (label.set_text_count, value.set_text_count)
                for key, (label, value) in labels.items()
            }
            MainWindow._update_main_routine_summary(host, first)
            self.assertEqual(
                counts_after_first,
                {
                    key: (label.set_text_count, value.set_text_count)
                    for key, (label, value) in labels.items()
                },
            )
            MainWindow._update_main_routine_summary(host, changed)

        self.assertEqual(1, styles.call_count)
        self.assertEqual(0, labels["review"][1].set_text_count)
        self.assertEqual(1, labels["stock"][1].set_text_count)

    def test_stock_cell_set_data_runs_only_when_projected_pnl_changes(self) -> None:
        initial_profit_metric, _amount, _rate = (
            table_loader.confirmable_stock_profit_metric(
                {
                    "available": True,
                    "cumulative_profit": 0,
                    "cumulative_rate": 0,
                }
            )
        )
        metrics = tuple(
            RatioMetricDisplay(
                label=label,
                value1=value1,
                value2=value2,
                value1_sample=value1,
                value2_sample=value2,
            )
            for label, value1, value2 in (
                ("보유", "0주", "0"),
                ("가격", "0", "0"),
            )
        ) + (initial_profit_metric,)
        item = _StockItem(metrics)
        table = _SingleRowTable(item)
        window = SimpleNamespace(routine_table=table)
        counts = {"instance-a": {"pnl_stock_codes": ["000001"]}}
        snapshots = [
            {
                "000001": {
                    "available": True,
                    "cumulative_profit": 0,
                    "cumulative_rate": 0,
                }
            },
            {
                "000001": {
                    "available": True,
                    "cumulative_profit": 0,
                    "cumulative_rate": 0,
                }
            },
            {
                "000001": {
                    "available": True,
                    "cumulative_profit": 1250,
                    "cumulative_rate": 1.25,
                }
            },
        ]
        with (
            patch.object(table_loader, "_instance_stock_counts", return_value=counts),
            patch.object(table_loader, "_refresh_instance_pnl_from_batch", side_effect=snapshots),
            patch.object(table_loader, "_main_pnl_refresh_routine_metadata", return_value=([], [])),
            patch.object(table_loader, "_update_main_routine_summary"),
        ):
            table_loader.main_refresh_pnl_only(window)
            table_loader.main_refresh_pnl_only(window)
            self.assertEqual(0, item.set_data_count)
            table_loader.main_refresh_pnl_only(window)

        self.assertEqual(1, item.set_data_count)
        self.assertEqual(1, table.viewport().update_count)
        self.assertEqual("+1,250", item.metrics[2].value1)
        self.assertEqual("+1.25%", item.metrics[2].value2)


if __name__ == "__main__":
    unittest.main()
