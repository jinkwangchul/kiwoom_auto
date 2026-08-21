# -*- coding: utf-8 -*-

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt5.QtWidgets import QApplication, QWidget

import gui_main_table_loader as loader
import gui_windows
from gui_routine_registry import GroupRecord
from main_group_projection import build_main_group_projection
from routine_instance_registry import RoutineInstanceRecord


def _group(root: Path, name: str) -> GroupRecord:
    path = root / f"_{name}"
    return GroupRecord(
        name=name,
        path=path,
        source_type="legacy_folder",
        budget={},
        valid=True,
    )


def _instance(instance_id: str, name: str | None = None) -> RoutineInstanceRecord:
    return RoutineInstanceRecord(
        instance_id=instance_id,
        definition_id="definition-a",
        display_name=name or instance_id,
        source_routine_name="definition-name",
        persisted=True,
        source="PERSISTED",
        enabled=True,
        real_trade_allowed=True,
    )


def _stock(code: str, group: str | None, instance_id: str) -> dict[str, object]:
    return {
        "code": code,
        "name": f"Stock {code}",
        "stock_path": f"stocks/{code}_Stock",
        "routines": [group] if group else [],
        "assigned_routine_instance_id": instance_id,
    }


class MainGroupProjectionTests(unittest.TestCase):
    def test_one_group_one_instance_one_stock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projection = build_main_group_projection(
                [_group(root, "동전주")],
                [_instance("instance-a", "A")],
                [_stock("000001", "동전주", "instance-a")],
            )

        self.assertEqual(["동전주"], [group.display_name for group in projection])
        self.assertEqual(["instance-a"], [item.instance_id for item in projection[0].instances])
        self.assertEqual(["000001"], [stock["code"] for stock in projection[0].instances[0].stocks])

    def test_one_group_multiple_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projection = build_main_group_projection(
                [_group(root, "동전주")],
                [_instance("instance-a", "A"), _instance("instance-b", "B")],
                [
                    _stock("000001", "동전주", "instance-a"),
                    _stock("000002", "동전주", "instance-b"),
                ],
            )

        self.assertEqual(
            ["instance-a", "instance-b"],
            [item.instance_id for item in projection[0].instances],
        )

    def test_multiple_groups_can_project_the_same_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projection = build_main_group_projection(
                [_group(root, "동전주"), _group(root, "대형주")],
                [_instance("instance-a", "A")],
                [
                    _stock("000001", "동전주", "instance-a"),
                    _stock("000002", "대형주", "instance-a"),
                ],
            )

        by_name = {group.display_name: group for group in projection}
        self.assertEqual("000001", by_name["동전주"].instances[0].stocks[0]["code"])
        self.assertEqual("000002", by_name["대형주"].instances[0].stocks[0]["code"])

    def test_same_group_and_definition_name_do_not_collapse_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _group(root, "지표추종매매")
            instance = _instance("instance-a")
            projection = build_main_group_projection(
                [group],
                [instance],
                [_stock("000001", "지표추종매매", "instance-a")],
            )

        self.assertEqual(str(group.path.resolve()), projection[0].group_id)
        self.assertEqual("definition-a", projection[0].instances[0].instance.definition_id)
        self.assertNotEqual(projection[0].group_id, instance.definition_id)

    def test_missing_instance_assignment_keeps_group_without_routine_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projection = build_main_group_projection(
                [_group(root, "동전주")],
                [_instance("instance-a")],
                [_stock("000001", "동전주", "")],
            )

        self.assertEqual(1, len(projection))
        self.assertEqual((), projection[0].instances)

    def test_missing_valid_group_does_not_attach_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            projection = build_main_group_projection(
                [],
                [_instance("instance-a")],
                [_stock("000001", "없는그룹", "instance-a")],
            )

        self.assertEqual((), projection)

    def test_unknown_instance_id_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projection = build_main_group_projection(
                [_group(root, "동전주")],
                [_instance("instance-a")],
                [_stock("000001", "동전주", "unknown")],
            )

        self.assertEqual((), projection[0].instances)

    def test_refresh_is_idempotent_and_does_not_duplicate_stocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            groups = [_group(root, "동전주")]
            instances = [_instance("instance-a")]
            stock = _stock("000001", "동전주", "instance-a")

            first = build_main_group_projection(groups, instances, [stock, stock])
            second = build_main_group_projection(groups, instances, [stock, stock])

        self.assertEqual(first, second)
        self.assertEqual(1, len(first[0].instances[0].stocks))

    def test_ambiguous_display_name_does_not_choose_a_group_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projection = build_main_group_projection(
                [_group(root / "one", "동전주"), _group(root / "two", "동전주")],
                [_instance("instance-a")],
                [_stock("000001", "동전주", "instance-a")],
            )

        self.assertTrue(all(not group.instances for group in projection))


class _RoutineTable:
    def __init__(self) -> None:
        self.rows = 0
        self.items = {}
        self.widgets = {}

    def columnCount(self):
        return len(loader.ROUTINE_MONITORING_HEADERS)

    def rowCount(self):
        return self.rows

    def setRowCount(self, rows):
        self.rows = rows

    def clearSpans(self):
        pass

    def setSpan(self, *_args):
        pass

    def setRowHeight(self, *_args):
        pass

    def setItem(self, row, column, item):
        self.items[(row, column)] = item

    def item(self, row, column):
        return self.items.get((row, column))

    def setCellWidget(self, row, column, widget):
        self.widgets[(row, column)] = widget

    def cellWidget(self, row, column):
        return self.widgets.get((row, column))

    def removeCellWidget(self, row, column):
        self.widgets.pop((row, column), None)


class MainGroupTreeLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_main_rows_use_group_then_instance_then_stock_and_refresh_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _group(root, "동전주")
            instance = _instance("instance-a", "A")
            stock = _stock("000001", "동전주", "instance-a")
            table = _RoutineTable()
            window = SimpleNamespace(
                routine_table=table,
                _main_routine_sort_column=-1,
                _main_routine_sort_order=0,
                _main_routine_valid_only=False,
                _main_routine_display_level="stock",
                _main_routine_display_level_applied=True,
                _main_routine_stock_scope="all",
                _main_routine_excluded_only=False,
                _collapsed_main_group_ids=set(),
                _collapsed_main_group_instance_ids=set(),
                _routine_definition_enabled={},
                _routine_instance_selection={},
            )
            count = {
                "registered": 1,
                "operation_running": 0,
                "waiting": 1,
                "normal": 1,
                "excluded": 0,
                "review": 0,
                "operation_or_stopped": 1,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": 0,
                "profit_cost_basis": 0,
                "profit_unknown": False,
                "pnl_stock_codes": [],
                "pnl_flat_stock_codes": [],
                "stocks": [stock],
            }

            def stock_row(_window, *, definition_id, instance_id, stock, **_kwargs):
                return {
                    "kind": loader.ROUTINE_ROW_STOCK,
                    "definition_id": definition_id,
                    "instance_id": instance_id,
                    "code": stock["code"],
                    "name": stock["name"],
                    "stock_path": stock["stock_path"],
                    "enabled": True,
                    "stock_values": [],
                    "stock_display_tokens": [],
                    "stock_metrics": (),
                    "initial_buy": {},
                    "operation_status": "",
                    "registered": 0,
                    "excluded": 0,
                    "operation_or_stopped": 0,
                    "review": 0,
                    "buy_limit_display": "",
                    "consumed_display": "",
                }

            with (
                patch.object(loader, "get_group_records", return_value=[group]),
                patch.object(
                    loader,
                    "_main_pnl_refresh_routine_metadata",
                    return_value=([], [instance]),
                ),
                patch.object(
                    loader,
                    "_main_pnl_refresh_static_cache",
                    return_value={"stocks": (stock,)},
                ),
                patch.object(
                    loader,
                    "_instance_stock_counts",
                    return_value={"instance-a": count},
                ),
                patch.object(loader, "_refresh_instance_pnl_from_batch", return_value={}),
                patch.object(loader, "current_stock_trade_counts_by_code", return_value={}),
                patch.object(loader, "_routine_tree_stock_row", side_effect=stock_row),
                patch.object(loader, "create_routine_instance_status_widget", return_value=QWidget()),
                patch.object(loader, "main_apply_routine_sort"),
                patch.object(loader, "_update_main_routine_summary"),
            ):
                loader.main_load_routine_table(window)
                first_rows = [
                    (
                        table.item(row, 0).data(loader.ROUTINE_ROW_KIND_ROLE),
                        table.item(row, 0).data(loader.ROUTINE_GROUP_ID_ROLE),
                        table.item(row, 0).data(loader.ROUTINE_INSTANCE_ID_ROLE),
                    )
                    for row in range(table.rowCount())
                ]
                loader.main_load_routine_table(window)
                second_rows = [
                    (
                        table.item(row, 0).data(loader.ROUTINE_ROW_KIND_ROLE),
                        table.item(row, 0).data(loader.ROUTINE_GROUP_ID_ROLE),
                        table.item(row, 0).data(loader.ROUTINE_INSTANCE_ID_ROLE),
                    )
                    for row in range(table.rowCount())
                ]

        self.assertEqual(
            [loader.ROUTINE_ROW_PARENT, loader.ROUTINE_ROW_CHILD, loader.ROUTINE_ROW_STOCK],
            [row[0] for row in first_rows],
        )
        self.assertTrue(all(row[1] == str(group.path.resolve()) for row in first_rows))
        self.assertEqual("", first_rows[0][2])
        self.assertEqual("instance-a", first_rows[1][2])
        self.assertEqual(first_rows, second_rows)

    def test_group_operation_delegates_only_projected_instances_and_stocks(self) -> None:
        delegate = MagicMock()
        owner = SimpleNamespace(
            _routine_instance_ids_by_group={"group-id": ("instance-a",)},
            _routine_stock_paths_by_group={"group-id": ("stocks/000001_Stock",)},
            request_routine_definition_operation=delegate,
        )

        gui_windows.MainWindow.request_routine_group_operation(
            owner,
            "group-id",
            "동전주",
            "루틴",
            loader.ROUTINE_STATUS_EARLY_CLOSE,
        )

        delegate.assert_called_once_with(
            "group-id",
            "동전주",
            "루틴",
            loader.ROUTINE_STATUS_EARLY_CLOSE,
            instance_ids_override=("instance-a",),
            stock_paths=("stocks/000001_Stock",),
            target_type="ROUTINE_GROUP",
            scope_label="그룹",
            event_source="gui_windows.MainWindow.request_routine_group_operation",
        )

    def test_group_instance_stock_dirs_do_not_leak_shared_instance_stocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_a = root / "stocks" / "000001_A"
            stock_b = root / "stocks" / "000002_B"
            stock_a.mkdir(parents=True)
            stock_b.mkdir(parents=True)
            relation_a = loader.main_group_instance_relation_id(
                "group-a",
                "instance-a",
            )
            owner = SimpleNamespace(
                _routine_stock_paths_by_group_instance={
                    relation_a: (str(stock_a),),
                }
            )

            result = gui_windows.MainWindow._projected_group_instance_stock_dirs(
                owner,
                "group-a",
                "instance-a",
            )

        self.assertEqual([stock_a], result)
        self.assertNotIn(stock_b, result)


if __name__ == "__main__":
    unittest.main()
