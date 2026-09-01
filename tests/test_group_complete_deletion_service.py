# -*- coding: utf-8 -*-

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import group_complete_deletion_service as deletion
from group_deletion_transaction import group_delete_pending
from gui_routine_registry import scan_group_records
from logical_group_registry import LogicalGroupRepository


GROUP_A = "11111111-1111-4111-8111-111111111111"
GROUP_B = "22222222-2222-4222-8222-222222222222"
INSTANCE_A = "33333333-3333-4333-8333-333333333333"
INSTANCE_B = "44444444-4444-4444-8444-444444444444"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_group(root: Path, group_id: str, name: str, slot: int) -> Path:
    group = root / "groups" / group_id
    _write_json(
        group / "group.json",
        {
            "schema_version": "1.0",
            "group_id": group_id,
            "definition_id": "definition_a",
            "base_name": "지표추종매매",
            "display_name": name,
            "slot": slot,
            "created_at": "2026-08-22T09:30:00+09:00",
        },
    )
    return group


def _promote(root: Path, *group_ids: str) -> None:
    LogicalGroupRepository(root).promote_logical_cutover(
        group_ids,
        cutover_at="2026-08-22T10:00:00+09:00",
    )


def _write_instance(root: Path, instance_id: str, group_id: str) -> SimpleNamespace:
    routine_dir = root / "routines" / "definition-a"
    _write_json(
        routine_dir / "routine.json",
        {
            "schema_version": "1.0",
            "definition_id": "definition_a",
            "name": "지표추종매매",
            "entry_file": "routine.py",
            "rules_file": "rules.json",
            "enabled": True,
        },
    )
    (routine_dir / "routine.py").write_text("", encoding="utf-8")
    instance_dir = root / "routine_instances" / instance_id
    _write_json(
        instance_dir / "instance.json",
        {
            "schema_version": "1.0",
            "instance_id": instance_id,
            "definition_id": "definition_a",
            "display_name": f"인스턴스-{instance_id[:4]}",
            "enabled": False,
            "buy_limit_enabled": False,
            "buy_limit_amount": None,
            "rules_file": "rules.json",
            "created_at": "2026-08-22T09:30:00+09:00",
            "updated_at": "2026-08-22T09:30:00+09:00",
            "group_id": group_id,
        },
    )
    _write_json(instance_dir / "rules.json", {"instance": instance_id})
    return SimpleNamespace(
        instance_id=instance_id,
        group_id=group_id,
        definition_id="definition_a",
        display_name=f"인스턴스-{instance_id[:4]}",
    )


def _write_stock(root: Path, code: str, name: str, instance_id: str) -> Path:
    stock_dir = root / "stocks" / f"{code}_{name}"
    _write_json(
        stock_dir / "config.json",
        {
            "routines": ["legacy-name"],
            "assigned_routine_instance_id": instance_id,
            "sentinel": f"keep-{code}",
        },
    )
    _write_json(stock_dir / "state.json", {"holding_qty": 0, "status": "STOPPED"})
    _write_json(stock_dir / "orders.json", [])
    return stock_dir


class GroupCompleteDeletionServiceTests(unittest.TestCase):
    def test_logical_group_complete_deletion_preserves_stock_and_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, GROUP_A, "지표추종매매", 0)
            _promote(root, GROUP_A)
            definition = root / "routines" / "definition-a"
            _write_json(definition / "routine.json", {"definition_id": "definition-a"})
            instance = _write_instance(root, INSTANCE_A, GROUP_A)
            stock = _write_stock(root, "000001", "대상종목", INSTANCE_A)

            with (
                patch.object(deletion, "load_persisted_routine_instances", return_value=[instance]),
                patch("stock_repository._append_routine_changed"),
            ):
                scope = deletion.collect_group_deletion_scope(root, GROUP_A)
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertTrue(result.success, result.error)
            self.assertFalse(group.exists())
            self.assertFalse((root / "routine_instances" / INSTANCE_A).exists())
            self.assertTrue(stock.exists())
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))
            self.assertEqual([], config["routines"])
            self.assertEqual("", config["assigned_routine_instance_id"])
            self.assertEqual("keep-000001", config["sentinel"])
            self.assertTrue(definition.exists())
            self.assertFalse(group_delete_pending(root, GROUP_A))
            self.assertEqual([], scan_group_records(project_root=root))
            self.assertEqual((), LogicalGroupRepository(root).registry_state().group_ids)

    def test_deletion_removes_only_target_group_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target_group = _write_group(root, GROUP_A, "지표추종매매", 0)
            other_group = _write_group(root, GROUP_B, "지표추종매매_1", 1)
            _promote(root, GROUP_A, GROUP_B)
            target_instance = _write_instance(root, INSTANCE_A, GROUP_A)
            other_instance = _write_instance(root, INSTANCE_B, GROUP_B)
            target_stock = _write_stock(root, "000001", "대상종목", INSTANCE_A)
            other_stock = _write_stock(root, "000002", "다른종목", INSTANCE_B)

            with (
                patch.object(
                    deletion,
                    "load_persisted_routine_instances",
                    return_value=[target_instance, other_instance],
                ),
                patch("stock_repository._append_routine_changed"),
            ):
                scope = deletion.collect_group_deletion_scope(root, GROUP_A)
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertTrue(result.success, result.error)
            self.assertFalse(target_group.exists())
            self.assertTrue(other_group.exists())
            self.assertFalse((root / "routine_instances" / INSTANCE_A).exists())
            self.assertTrue((root / "routine_instances" / INSTANCE_B).exists())
            self.assertEqual("keep-000001", json.loads((target_stock / "config.json").read_text(encoding="utf-8"))["sentinel"])
            self.assertEqual("keep-000002", json.loads((other_stock / "config.json").read_text(encoding="utf-8"))["sentinel"])
            self.assertEqual([GROUP_B], [group.group_id for group in scan_group_records(project_root=root)])

    def test_preflight_block_changes_nothing_and_writes_no_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, GROUP_A, "지표추종매매", 0)
            _promote(root, GROUP_A)
            instance = _write_instance(root, INSTANCE_A, GROUP_A)
            stock = _write_stock(root, "000001", "대상종목", INSTANCE_A)
            group_before = (group / "group.json").read_bytes()
            config_before = (stock / "config.json").read_bytes()
            _write_json(
                stock / "state.json",
                {
                    "holding_qty": 1,
                    "holding_amount": 100,
                    "avg_price": 100,
                    "pending_order": False,
                    "status": "STOPPED",
                    "trade_enabled": False,
                },
            )

            with patch.object(deletion, "load_persisted_routine_instances", return_value=[instance]):
                scope = deletion.collect_group_deletion_scope(root, GROUP_A)
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertFalse(result.success)
            self.assertEqual(("대상종목: 장기보유 1주",), result.blocked_reasons)
            self.assertEqual(group_before, (group / "group.json").read_bytes())
            self.assertEqual(config_before, (stock / "config.json").read_bytes())
            self.assertFalse(group_delete_pending(root, GROUP_A))

    def test_failure_with_complete_rollback_clears_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, GROUP_A, "지표추종매매", 0)
            _promote(root, GROUP_A)
            instance = _write_instance(root, INSTANCE_A, GROUP_A)
            stock = _write_stock(root, "000001", "대상종목", INSTANCE_A)
            config_before = (stock / "config.json").read_bytes()

            with (
                patch.object(deletion, "load_persisted_routine_instances", return_value=[instance]),
                patch.object(
                    deletion,
                    "unassign_stock_routine",
                    return_value=SimpleNamespace(ok=False),
                ),
            ):
                scope = deletion.collect_group_deletion_scope(root, GROUP_A)
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertFalse(result.success)
            self.assertTrue(group.exists())
            self.assertTrue((root / "routine_instances" / INSTANCE_A).exists())
            self.assertEqual(config_before, (stock / "config.json").read_bytes())
            self.assertFalse(group_delete_pending(root, GROUP_A))

    def test_failure_with_incomplete_rollback_keeps_marker_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_group(root, GROUP_A, "지표추종매매", 0)
            _promote(root, GROUP_A)
            instance = _write_instance(root, INSTANCE_A, GROUP_A)
            stock = _write_stock(root, "000001", "대상종목", INSTANCE_A)
            config_path = stock / "config.json"

            def corrupt_then_fail(_project_root, _code, _name, _routines, **_kwargs):
                config_path.write_text("{}\n", encoding="utf-8")
                return SimpleNamespace(ok=False)

            with (
                patch.object(deletion, "load_persisted_routine_instances", return_value=[instance]),
                patch.object(deletion, "unassign_stock_routine", new=corrupt_then_fail),
                patch.object(deletion, "_restore_file", side_effect=OSError("rollback failed")),
            ):
                scope = deletion.collect_group_deletion_scope(root, GROUP_A)
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertFalse(result.success)
            self.assertIn("rollback", result.error)
            self.assertTrue(stock.exists())
            self.assertTrue(group_delete_pending(root, GROUP_A))


if __name__ == "__main__":
    unittest.main()
