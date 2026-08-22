# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from group_scope import build_group_scope
from stock_repository import StockRecord


def _group(group_id: str, display_name: str, path: Path):
    return SimpleNamespace(
        group_id=group_id,
        display_name=display_name,
        name=display_name,
        path=path,
    )


def _instance(instance_id: str, group_id: str = ""):
    return SimpleNamespace(
        instance_id=instance_id,
        display_name=instance_id,
        group_id=group_id,
    )


def _stock(code: str, routine: str, instance_id: str) -> StockRecord:
    return StockRecord(
        code=code,
        name=f"Stock {code}",
        routine=routine,
        enabled=True,
        stock_path=f"stocks/{code}_Stock",
        assigned_routine_instance_id=instance_id,
    )


class GroupScopeTests(unittest.TestCase):
    def test_explicit_group_id_wins_over_legacy_routine_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_a = _group("group-a", "지표추종매매", root / "renamed-a")
            group_b = _group("group-b", "변경된그룹명", root / "renamed-b")
            snapshot = build_group_scope(
                root,
                [group_a, group_b],
                [_instance("instance-b", "group-b")],
                [_stock("105560", "지표추종매매", "instance-b")],
            )

        self.assertEqual(("instance-b",), snapshot.group_instance_ids("group-b"))
        self.assertEqual((), snapshot.group_instance_ids("group-a"))
        self.assertEqual(["105560"], [stock.code for stock in snapshot.group_stocks("group-b")])
        self.assertEqual(["105560"], [stock.code for stock in snapshot.instance_stocks("instance-b")])

    def test_instance_without_group_id_never_uses_stock_routine_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _group("group-a", "지표추종매매", root / "no-stock-children")
            snapshot = build_group_scope(
                root,
                [group],
                [_instance("legacy-instance")],
                [_stock("000660", "지표추종매매", "legacy-instance")],
            )

        self.assertEqual((), snapshot.group_instance_ids("group-a"))
        self.assertEqual([], [stock.code for stock in snapshot.group_stocks("group-a")])

    def test_orphan_instance_is_not_in_any_group_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = build_group_scope(
                root,
                [_group("group-a", "지표추종매매", root / "group")],
                [_instance("orphan")],
                [],
            )

        self.assertEqual((), snapshot.group_instance_ids("group-a"))
        self.assertEqual((), snapshot.instance_stocks("orphan"))
        self.assertEqual((), snapshot.all_group_stock_dirs())

    def test_instance_stock_scope_does_not_require_group_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = build_group_scope(
                root,
                [_group("group-a", "지표추종매매", root / "group")],
                [_instance("unresolved-instance")],
                [_stock("000001", "없는그룹", "unresolved-instance")],
            )

        self.assertEqual((), snapshot.group_instance_ids("group-a"))
        self.assertEqual(
            ["000001"],
            [stock.code for stock in snapshot.instance_stocks("unresolved-instance")],
        )
