# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import payload_hash, validate_session_document
from mock_validation_repository import (
    MAX_MOCK_EVALUATION_CYCLE_CHECKPOINTS,
    MockValidationRepository,
)
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import (
    MAX_RECENT_MARK_TO_MARKET_COMMANDS,
    RESULT_NOOP,
    MockVirtualExecutionEngine,
)

SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 18, 10, 0, 0, tzinfo=SEOUL)
SESSION_ID = "MV-00000000000000000000000000009901"


def _reference():
    rules = {"instance": "A", "starting_budget": 1_000_000}
    snapshot = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "stock_identity_reference": "STOCK-005930",
        "snapshot_created_at": NOW.isoformat(),
        "routine_instances": [
            {
                "routine_instance_id": "A",
                "routine_definition_id": "indicator-follow",
                "routine_type": "지표추종매매",
                "rules_snapshot": rules,
                "rules_hash": payload_hash(rules),
            }
        ],
    }
    snapshot["snapshot_hash"] = payload_hash(snapshot)
    return snapshot


class MockValidationRuntimeCompactionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repository = MockValidationRepository(
            root / "mock_validation",
            project_root=root / "project",
        )
        self.service = MockValidationSessionService(
            self.repository,
            now_factory=lambda: NOW.isoformat(timespec="microseconds"),
        )
        self.service.create_stock_session(
            reference_snapshot=_reference(),
            validation_session_id=SESSION_ID,
            command_id="MC-create",
        )
        self.service.start_stock_mock_session(
            SESSION_ID,
            command_id="MC-start",
        )
        self.engine = MockVirtualExecutionEngine(
            self.repository,
            now_factory=lambda: NOW,
        )

    def _write_legacy_growth(self, *, cycle_count=800, mark_count=800):
        document = self.repository.read_session(SESSION_ID)
        adapter = document["progression_by_instance"]["A"].setdefault(
            "indicator_follow_mock_adapter",
            {"plans": [], "evaluation_cycles": {}, "version": 1},
        )
        cycles = adapter["evaluation_cycles"]
        for index in range(cycle_count):
            cycles[f"MC-cycle-{index:04d}"] = {
                "result": "NO_SIGNAL",
                "recorded_at": (
                    NOW + timedelta(seconds=index)
                ).isoformat(timespec="microseconds"),
            }
        for index in range(mark_count):
            document["applied_commands"][f"MC-mark-{index:04d}"] = {
                "operation": "VIRTUAL_MARK_TO_MARKET",
                "applied_at": (
                    NOW + timedelta(seconds=index)
                ).isoformat(timespec="microseconds"),
                "entity_id": "A",
                "market_identity": f"MMK-{index:04d}",
            }
        self.repository._atomic_write(
            self.repository._session_relative(SESSION_ID),
            document,
            validator=validate_session_document,
        )
        return self.repository.read_session(SESSION_ID)

    def test_next_mutation_compacts_legacy_high_frequency_ledgers(self):
        legacy = self._write_legacy_growth()
        path = self.repository._target(
            self.repository._session_relative(SESSION_ID)
        )
        legacy_size = path.stat().st_size

        result = self.repository.mutate_session(
            SESSION_ID,
            lambda document: document,
            expected_revision=legacy["revision"],
        )
        compacted = result["document"]
        cycles = compacted["progression_by_instance"]["A"][
            "indicator_follow_mock_adapter"
        ]["evaluation_cycles"]

        self.assertTrue(result["changed"])
        self.assertEqual(MAX_MOCK_EVALUATION_CYCLE_CHECKPOINTS, len(cycles))
        self.assertIn("MC-cycle-0799", cycles)
        self.assertNotIn("MC-cycle-0000", cycles)
        self.assertFalse(
            any(
                entry.get("operation") == "VIRTUAL_MARK_TO_MARKET"
                for entry in compacted["applied_commands"].values()
            )
        )
        recent = compacted["pnl"][0]["recent_mark_to_market_commands"]
        self.assertEqual(MAX_RECENT_MARK_TO_MARKET_COMMANDS, len(recent))
        self.assertEqual("MC-mark-0799", recent[-1])
        self.assertEqual("MC-mark-0799", compacted["pnl"][0]["mark_command_id"])
        self.assertIn("MC-create", compacted["applied_commands"])
        self.assertLess(path.stat().st_size, legacy_size // 2)

    def test_mark_to_market_uses_bounded_pnl_checkpoint(self):
        self.service.set_instance_position(
            SESSION_ID,
            "A",
            holding_qty=2,
            available_qty=2,
            average_price=100,
            realized_cost_basis=200,
            command_id="MC-position",
        )
        for index in range(MAX_RECENT_MARK_TO_MARKET_COMMANDS + 6):
            self.engine.mark_to_market(
                SESSION_ID,
                "A",
                current_price=90 + index,
                market_identity=f"MMK-{index}",
                command_id=f"MC-mark-{index}",
            )

        document = self.repository.read_session(SESSION_ID)
        pnl = document["pnl"][0]
        recent = pnl["recent_mark_to_market_commands"]

        self.assertEqual(MAX_RECENT_MARK_TO_MARKET_COMMANDS, len(recent))
        self.assertEqual(
            f"MC-mark-{MAX_RECENT_MARK_TO_MARKET_COMMANDS + 5}",
            recent[-1],
        )
        self.assertNotIn("MC-mark-0", recent)
        self.assertFalse(
            any(
                entry.get("operation") == "VIRTUAL_MARK_TO_MARKET"
                for entry in document["applied_commands"].values()
            )
        )

        revision = document["revision"]
        replay = self.engine.mark_to_market(
            SESSION_ID,
            "A",
            current_price=90 + MAX_RECENT_MARK_TO_MARKET_COMMANDS + 5,
            market_identity=f"MMK-{MAX_RECENT_MARK_TO_MARKET_COMMANDS + 5}",
            command_id=f"MC-mark-{MAX_RECENT_MARK_TO_MARKET_COMMANDS + 5}",
        )
        self.assertEqual(RESULT_NOOP, replay["status"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(
            revision,
            self.repository.read_session(SESSION_ID)["revision"],
        )

    def test_zero_holding_compacts_once_then_stays_write_free(self):
        legacy = self._write_legacy_growth(cycle_count=0, mark_count=100)
        first = self.engine.mark_to_market(
            SESSION_ID,
            "A",
            current_price=100,
            market_identity="MMK-zero-1",
            command_id="MC-zero-1",
        )
        compacted = self.repository.read_session(SESSION_ID)

        self.assertEqual(RESULT_NOOP, first["status"])
        self.assertFalse(
            any(
                entry.get("operation") == "VIRTUAL_MARK_TO_MARKET"
                for entry in compacted["applied_commands"].values()
            )
        )
        self.assertGreater(compacted["revision"], legacy["revision"])
        revision = compacted["revision"]

        second = self.engine.mark_to_market(
            SESSION_ID,
            "A",
            current_price=101,
            market_identity="MMK-zero-2",
            command_id="MC-zero-2",
        )
        self.assertEqual(RESULT_NOOP, second["status"])
        self.assertEqual(
            revision,
            self.repository.read_session(SESSION_ID)["revision"],
        )


if __name__ == "__main__":
    unittest.main()
