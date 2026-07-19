from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import gui_main_table_loader
from operation_command_service import OperationCommandService
from routine_instance_registry import RoutineInstanceRecord
from stock_repository import StockRepository


INSTANCE_B = "187a8fde-bc01-4c8d-bc4d-90071066ca56"
INSTANCE_COIN = "8d1d3149-8fae-4d51-8950-aebf18363e37"


def instance(instance_id: str, name: str) -> RoutineInstanceRecord:
    return RoutineInstanceRecord(
        instance_id=instance_id,
        definition_id="indicator_follow",
        display_name=name,
        source_routine_name="지표추종매매",
        persisted=True,
        source="PERSISTED",
        enabled=False,
        real_trade_allowed=False,
    )


class _Header:
    def setSortIndicator(self, *_args) -> None:
        return None


class _Table:
    def __init__(self) -> None:
        self.row_count = 0
        self.items: dict[tuple[int, int], object] = {}

    def columnCount(self) -> int:
        return 10

    def setRowCount(self, count: int) -> None:
        self.row_count = count

    def setItem(self, row: int, column: int, item: object) -> None:
        self.items[(row, column)] = item

    def sortItems(self, *_args) -> None:
        return None

    def horizontalHeader(self) -> _Header:
        return _Header()


class _Item:
    def __init__(self, text: str = "") -> None:
        self._text = str(text)

    def text(self) -> str:
        return self._text

    def setData(self, *_args) -> None:
        return None

    def setTextAlignment(self, *_args) -> None:
        return None

    def setForeground(self, *_args) -> None:
        return None


class RoutineInstanceStockAssignmentTest(unittest.TestCase):
    def _stock(self, root: Path, folder: str, config: dict) -> Path:
        stock = root / "stocks" / folder
        stock.mkdir(parents=True)
        (stock / "config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )
        (stock / "state.json").write_text(
            json.dumps({"status": "STOPPED", "holding_qty": 0}),
            encoding="utf-8",
        )
        return stock

    def test_repository_writes_and_reads_canonical_instance_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root, "003550_LG", {})
            repository = StockRepository(root)

            saved = repository.update_stock_routine_instance(
                "003550",
                "LG",
                instance_id=INSTANCE_B,
                instance_name="지표추종매매B",
                definition_id="indicator_follow",
                routine_type="지표추종매매",
            )
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))
            record = repository.find_by_code("003550")

        self.assertTrue(saved)
        self.assertEqual(INSTANCE_B, config["assigned_routine_instance_id"])
        self.assertEqual("지표추종매매B", config["routine_instance_name"])
        self.assertEqual("indicator_follow", config["routine_definition_id"])
        self.assertEqual("지표추종매매", config["routine_type"])
        self.assertEqual("지표추종매매", config["routine"])
        self.assertIsNotNone(record)
        self.assertEqual(INSTANCE_B, record.assigned_routine_instance_id)

    def test_legacy_assignment_does_not_retain_stale_instance_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(
                root,
                "003550_LG",
                {"assigned_routine_instance_id": INSTANCE_B},
            )
            repository = StockRepository(root)
            self.assertTrue(
                repository.update_stock_routine("003550", "LG", ["지표추종매매"])
            )
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("", config["assigned_routine_instance_id"])
        self.assertEqual("", config["routine_instance_name"])

    def test_monitoring_displays_instance_name_and_marks_legacy_as_review_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assigned = self._stock(
                root,
                "003550_LG",
                {
                    "routine": "지표추종매매",
                    "assigned_routine_instance_id": INSTANCE_B,
                    "operation_mode": "SCHEDULED",
                },
            )
            legacy = self._stock(
                root,
                "005380_현대차",
                {"routine": "지표추종매매", "operation_mode": "SCHEDULED"},
            )
            stocks = [
                {
                    "code": "003550",
                    "name": "LG",
                    "routines": ["지표추종매매"],
                    "stock_path": str(assigned),
                    "assigned_routine_instance_id": INSTANCE_B,
                },
                {
                    "code": "005380",
                    "name": "현대차",
                    "routines": ["지표추종매매"],
                    "stock_path": str(legacy),
                    "assigned_routine_instance_id": "",
                },
            ]
            window = SimpleNamespace(
                running_stock_table=_Table(),
                _main_running_sort_column=-1,
                _main_running_sort_order=0,
                startup_recovery_session_ready=lambda **_kwargs: False,
            )
            with (
                patch.object(gui_main_table_loader, "read_base_stocks", return_value=stocks),
                patch.object(
                    gui_main_table_loader,
                    "load_persisted_routine_instances",
                    return_value=[instance(INSTANCE_B, "지표추종매매B")],
                ),
                patch.object(gui_main_table_loader, "SortableTableWidgetItem", _Item),
                patch.object(
                    gui_main_table_loader,
                    "create_auto_trade_situation_item",
                    side_effect=lambda *_args, **_kwargs: _Item("-"),
                ),
                patch.object(
                    gui_main_table_loader,
                    "create_auto_trade_setting_status_item",
                    side_effect=lambda value: _Item(value),
                ),
                patch.object(
                    gui_main_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
            ):
                gui_main_table_loader.main_load_running_stock_table(window)

        self.assertEqual(2, window.running_stock_table.row_count)
        routines = {
            window.running_stock_table.items[(row, 0)].text():
            window.running_stock_table.items[(row, 2)].text()
            for row in range(2)
        }
        self.assertEqual("지표추종매매B", routines["003550"])
        self.assertEqual("배정 확인 필요", routines["005380"])

    def test_counts_and_command_targets_use_exact_instance_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_b = self._stock(
                root,
                "003550_LG",
                {"assigned_routine_instance_id": INSTANCE_B},
            )
            self._stock(
                root,
                "005380_현대차",
                {"assigned_routine_instance_id": INSTANCE_COIN},
            )
            service = OperationCommandService(root)
            targets, error = service._resolve_targets("ROUTINE_INSTANCE", INSTANCE_B)

            base_stocks = [
                {
                    "stock_path": str(stock_b),
                    "assigned_routine_instance_id": INSTANCE_B,
                }
            ]
            with (
                patch.object(gui_main_table_loader, "read_base_stocks", return_value=base_stocks),
                patch.object(
                    gui_main_table_loader,
                    "load_persisted_routine_instances",
                    return_value=[
                        instance(INSTANCE_B, "지표추종매매B"),
                        instance(INSTANCE_COIN, "동전주"),
                    ],
                ),
            ):
                counts = gui_main_table_loader._instance_stock_counts()

        self.assertEqual("", error)
        self.assertEqual([stock_b.resolve()], targets)
        self.assertEqual(1, counts[INSTANCE_B]["registered"])
        self.assertNotIn(INSTANCE_COIN, counts)


if __name__ == "__main__":
    unittest.main()
