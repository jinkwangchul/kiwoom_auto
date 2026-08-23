from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from assignment_episode_repository import CanonicalAssignmentEpisodeRepository, EpisodeMutationResult
from production_performance_linkage import (
    append_performance_from_realization,
    prepare_sell_fifo_realization,
    record_buy_entry_lot,
)
from realized_pnl_ledger import record_realized_pnl
from stock_repository import StockRepository


class ProductionPerformanceLinkageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.group_a = str(uuid4())
        self.group_b = str(uuid4())
        self.instance_a = str(uuid4())
        self.instance_b = str(uuid4())
        self._write_foundation()
        self.stock_repository = StockRepository(self.root)
        self.event_patch = patch("stock_repository._append_routine_changed")
        self.event_patch.start()
        self.addCleanup(self.event_patch.stop)

    def _json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_foundation(self) -> None:
        routine = self.root / "routines" / "Sample"
        self._json(
            routine / "routine.json",
            {
                "schema_version": "1.0",
                "definition_id": "sample",
                "name": "Sample",
                "entry_file": "routine.py",
                "rules_file": "rules.json",
                "enabled": True,
            },
        )
        (routine / "routine.py").write_text("", encoding="utf-8")
        for group_id, name, slot in ((self.group_a, "Group", 0), (self.group_b, "Group_1", 1)):
            self._json(
                self.root / "groups" / group_id / "group.json",
                {
                    "schema_version": "1.0",
                    "group_id": group_id,
                    "definition_id": "sample",
                    "base_name": "Group",
                    "display_name": name,
                    "slot": slot,
                    "created_at": "2026-08-23T08:00:00+09:00",
                },
            )
        self._json(
            self.root / "groups" / "registry.json",
            {
                "schema_version": "1.0",
                "mode": "logical",
                "group_ids": [self.group_a, self.group_b],
                "cutover_at": "2026-08-23T08:00:00+09:00",
            },
        )
        for instance_id, group_id, name in (
            (self.instance_a, self.group_a, "Alpha"),
            (self.instance_b, self.group_b, "Beta"),
        ):
            self._json(
                self.root / "routine_instances" / instance_id / "instance.json",
                {
                    "schema_version": "1.0",
                    "instance_id": instance_id,
                    "definition_id": "sample",
                    "display_name": name,
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                    "created_at": "2026-08-23T08:00:00+09:00",
                    "updated_at": "2026-08-23T08:00:00+09:00",
                    "group_id": group_id,
                },
            )
            self._json(self.root / "routine_instances" / instance_id / "rules.json", {})
        stock = self.root / "stocks" / "005930_Sample"
        self._json(stock / "config.json", {"routines": [], "assigned_routine_instance_id": ""})
        self._json(stock / "state.json", {"status": "STOPPED"})
        self._json(stock / "orders.json", {"orders": []})

    def _assign(self, instance_id: str, name: str) -> None:
        success = self.stock_repository.update_stock_routine_instance(
                "005930",
                "Sample",
                instance_id=instance_id,
                instance_name=name,
                definition_id="sample",
                routine_type="Sample",
            )
        self.assertTrue(success, self.stock_repository.last_assignment_linkage_result)

    @staticmethod
    def _fill(side: str, identity: str, quantity: int, price: int, at: str) -> dict[str, object]:
        return {
            "fill_id": f"FILL-{identity}",
            "execution_identity": identity,
            "execution_identity_source": "fid_909",
            "broker": "KIWOOM",
            "account_no": "12345678",
            "broker_order_no": f"ORDER-{identity}",
            "code": "005930",
            "side": side,
            "filled_quantity": quantity,
            "filled_price": price,
            "received_at": at,
        }

    def _buy(self, identity: str, quantity: int, price: int, at: str):
        fill = self._fill("BUY", identity, quantity, price, at)
        result = record_buy_entry_lot(self.root, fill, {"fill_delta_applied": quantity})
        self.assertTrue(result["success"], result)
        return fill, result

    def _sell(
        self,
        identity: str,
        quantity: int,
        price: int,
        at: str,
        *,
        fee: int | None = None,
        tax: int | None = None,
    ):
        fill = self._fill("SELL", identity, quantity, price, at)
        if fee is not None:
            fill["fee"] = fee
        if tax is not None:
            fill["tax"] = tax
        position = {
            "position_updated": True,
            "fill_delta_applied": quantity,
            "previous_average_price": 1_040,
            "positions_path": str(self.root / "runtime" / "positions.json"),
            "position_id": "POSITION-1",
        }
        fifo = prepare_sell_fifo_realization(self.root, fill, position)
        self.assertTrue(fifo["success"], fifo)
        realized = record_realized_pnl(
            fill,
            position,
            {"routine_provenance": {"routine_instance_id": self.instance_b}},
            self.root / "runtime" / "realized_pnl.json",
            context={
                "manual_realized_pnl_confirmed": True,
                "canonical_fifo_allocations": fifo["allocations"],
            },
        )
        self.assertTrue(realized["realized_pnl_recorded"], realized)
        performance = append_performance_from_realization(self.root, fill, realized, fifo)
        self.assertTrue(performance["success"], performance)
        return fill, position, fifo, realized, performance

    def test_assignment_bootstrap_and_a_to_b_are_canonical(self) -> None:
        self._assign(self.instance_a, "Alpha")
        self._assign(self.instance_b, "Beta")

        episodes = CanonicalAssignmentEpisodeRepository(self.root).list_episodes("005930")

        self.assertEqual(["UNASSIGNED", "ASSIGNED", "ASSIGNED"], [item.ownership_kind for item in episodes])
        self.assertEqual("BOOTSTRAP_UNASSIGNED", episodes[0].start_reason)
        self.assertEqual([self.instance_a, self.instance_b], [item.instance_id for item in episodes[1:]])

    def test_a_buy_then_b_sell_keeps_entry_owner_and_exit_episode(self) -> None:
        self._assign(self.instance_a, "Alpha")
        _fill, buy = self._buy("BUY-A", 10, 1_000, "2026-08-23T09:00:00+09:00")
        entry_episode = buy["entry_episode_id"]
        self._assign(self.instance_b, "Beta")

        _fill, _position, fifo, _realized, performance = self._sell(
            "SELL-B", 10, 1_100, "2026-08-23T10:00:00+09:00"
        )
        event = performance["performance_event"]

        self.assertEqual(entry_episode, event.allocations[0].entry_episode_id)
        self.assertEqual(fifo["exit_episode_id"], event.exit_episode_id)
        self.assertNotEqual(entry_episode, event.exit_episode_id)
        self.assertEqual(1_000, event.gross_pnl)

    def test_fifo_sell_across_a_and_b_lots_and_restart_is_idempotent(self) -> None:
        self._assign(self.instance_a, "Alpha")
        self._buy("BUY-A", 3, 1_000, "2026-08-23T09:00:00+09:00")
        self._assign(self.instance_b, "Beta")
        self._buy("BUY-B", 2, 1_100, "2026-08-23T09:30:00+09:00")

        fill, position, fifo, realized, first = self._sell(
            "SELL-AB", 5, 1_200, "2026-08-23T10:00:00+09:00", fee=30, tax=20
        )
        event = first["performance_event"]
        self.assertEqual([3, 2], [item.quantity for item in event.allocations])
        self.assertEqual(800, sum(item.gross_pnl for item in event.allocations))
        self.assertEqual(750, event.net_pnl)
        self.assertEqual([None, None], [item.net_pnl for item in event.allocations])

        replay_fifo = prepare_sell_fifo_realization(
            self.root,
            fill,
            {"fill_delta_applied": 0},
        )
        replay_realized = record_realized_pnl(
            fill,
            position,
            {},
            self.root / "runtime" / "realized_pnl.json",
            context={"manual_realized_pnl_confirmed": True},
        )
        replay = append_performance_from_realization(self.root, fill, replay_realized, replay_fifo)
        self.assertTrue(replay["success"], replay)
        self.assertTrue(replay["idempotent"])
        document = json.loads((self.root / "performance_ledger" / "005930" / "events.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(document["events"]))

    def test_buy_assignment_episode_mismatch_fails_without_lot(self) -> None:
        self._assign(self.instance_a, "Alpha")
        config_path = self.root / "stocks" / "005930_Sample" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["assigned_routine_instance_id"] = self.instance_b
        config_path.write_text(json.dumps(config), encoding="utf-8")

        fill = self._fill("BUY", "BUY-MISMATCH", 1, 1_000, "2026-08-23T09:00:00+09:00")
        result = record_buy_entry_lot(self.root, fill, {"fill_delta_applied": 1})

        self.assertFalse(result["success"])
        self.assertEqual("BUY_OWNERSHIP_MISMATCH", result["stage"])
        self.assertFalse((self.root / "performance_ledger" / "005930" / "entry_lots.json").exists())

    def test_episode_write_failure_restores_current_assignment_bytes(self) -> None:
        self._assign(self.instance_a, "Alpha")
        config_path = self.root / "stocks" / "005930_Sample" / "config.json"
        episode_path = CanonicalAssignmentEpisodeRepository(self.root).document_path("005930")
        before = (config_path.read_bytes(), episode_path.read_bytes())

        with patch.object(
            CanonicalAssignmentEpisodeRepository,
            "transition_episode",
            return_value=EpisodeMutationResult(False, error="injected"),
        ):
            success = self.stock_repository.update_stock_routine_instance(
                "005930",
                "Sample",
                instance_id=self.instance_b,
                instance_name="Beta",
                definition_id="sample",
                routine_type="Sample",
            )

        self.assertFalse(success)
        self.assertEqual(before, (config_path.read_bytes(), episode_path.read_bytes()))
        self.assertTrue(self.stock_repository.last_assignment_linkage_result.rollback_complete)

    def test_performance_append_failure_keeps_fifo_pending_for_retry(self) -> None:
        self._assign(self.instance_a, "Alpha")
        self._buy("BUY-A", 2, 1_000, "2026-08-23T09:00:00+09:00")
        self._assign(self.instance_b, "Beta")
        fill = self._fill("SELL", "SELL-RETRY", 2, 1_100, "2026-08-23T10:00:00+09:00")
        position = {"position_updated": True, "fill_delta_applied": 2, "previous_average_price": 1_000}
        fifo = prepare_sell_fifo_realization(self.root, fill, position)
        realized = record_realized_pnl(
            fill,
            position,
            {},
            self.root / "runtime" / "realized_pnl.json",
            context={"manual_realized_pnl_confirmed": True, "canonical_fifo_allocations": fifo["allocations"]},
        )

        with patch("performance_ledger_repository.os.replace", side_effect=OSError("injected")):
            failed = append_performance_from_realization(self.root, fill, realized, fifo)

        self.assertFalse(failed["success"])
        lots_path = self.root / "performance_ledger" / "005930" / "entry_lots.json"
        pending = json.loads(lots_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(pending["pending_consumptions"]))
        self.assertEqual(2, pending["lots"][0]["remaining_quantity"])

        retry_fifo = prepare_sell_fifo_realization(self.root, fill, {"fill_delta_applied": 0})
        retried = append_performance_from_realization(self.root, fill, realized, retry_fifo)
        self.assertTrue(retried["success"], retried)
        committed = json.loads(lots_path.read_text(encoding="utf-8"))
        self.assertEqual([], committed["pending_consumptions"])
        self.assertEqual(0, committed["lots"][0]["remaining_quantity"])

    def test_episode_snapshot_and_performance_survive_instance_removal(self) -> None:
        self._assign(self.instance_a, "Alpha")
        _fill, buy = self._buy("BUY-A", 1, 1_000, "2026-08-23T09:00:00+09:00")
        instance_path = self.root / "routine_instances" / self.instance_a / "instance.json"
        metadata = json.loads(instance_path.read_text(encoding="utf-8"))
        metadata["display_name"] = "Alpha Renamed"
        instance_path.write_text(json.dumps(metadata), encoding="utf-8")
        self._assign(self.instance_b, "Beta")
        self._sell("SELL-HISTORY", 1, 1_100, "2026-08-23T10:00:00+09:00")

        for path in (instance_path, instance_path.parent / "rules.json"):
            path.unlink()
        instance_path.parent.rmdir()

        episode = CanonicalAssignmentEpisodeRepository(self.root).get_episode(buy["entry_episode_id"], stock_code="005930")
        ledger = json.loads((self.root / "performance_ledger" / "005930" / "events.json").read_text(encoding="utf-8"))
        self.assertEqual("Alpha", episode.instance_name_snapshot)
        self.assertEqual(buy["entry_episode_id"], ledger["events"][0]["allocations"][0]["entry_episode_id"])


if __name__ == "__main__":
    unittest.main()
