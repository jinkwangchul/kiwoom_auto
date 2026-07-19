from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from uuid import UUID

from routine_instance_repository import (
    RoutineInstanceCreateRequest,
    RoutineInstanceRepository,
)


INSTANCE_ID = UUID("a52f539d-4f18-4ef6-b0cf-f471567982a1")


class RoutineInstanceRepositoryTest(unittest.TestCase):
    def _repository(self, root: Path) -> RoutineInstanceRepository:
        routine_dir = root / "routines" / "indicator_follow"
        routine_dir.mkdir(parents=True)
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "definition_id": "indicator_follow",
                    "name": "지표추종매매",
                    "settings_ui": "indicator_follow",
                    "module_name": "indicator_follow_routine",
                    "rules_file": "rules.json",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return RoutineInstanceRepository(
            root,
            id_factory=lambda: INSTANCE_ID,
            now_factory=lambda: datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc),
        )

    def test_create_writes_complete_disabled_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            rules = {"buy": {"enabled": True}}

            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="대형주 추세형",
                    description="대형주 중심",
                    buy_limit_enabled=True,
                    buy_limit_amount=12_000_000,
                ),
                rules,
            )

            instance_dir = root / "routine_instances" / str(INSTANCE_ID)
            metadata = json.loads((instance_dir / "instance.json").read_text(encoding="utf-8"))
            saved_rules = json.loads((instance_dir / "rules.json").read_text(encoding="utf-8"))

        self.assertTrue(result.success)
        self.assertIsNotNone(result.instance)
        self.assertFalse(metadata["enabled"])
        self.assertEqual(12_000_000, metadata["buy_limit_amount"])
        self.assertEqual(rules, saved_rules)
        self.assertFalse(result.instance.real_trade_allowed)

    def test_duplicate_name_within_definition_is_rejected_without_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            request = RoutineInstanceCreateRequest(
                definition_id="indicator_follow",
                display_name="대형주 추세형",
            )
            first = repository.create_instance(request, {"buy": {}})
            second = repository.create_instance(request, {"buy": {}})

            instance_dirs = [path for path in (root / "routine_instances").iterdir() if path.is_dir()]

        self.assertTrue(first.success)
        self.assertFalse(second.success)
        self.assertEqual("DISPLAY_NAME_DUPLICATE", second.error_code)
        self.assertEqual(1, len(instance_dirs))

    def test_invalid_buy_limit_is_rejected_before_storage_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)

            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Invalid Limit",
                    buy_limit_enabled=True,
                    buy_limit_amount=0,
                ),
                {},
            )

        self.assertFalse(result.success)
        self.assertEqual("BUY_LIMIT_INVALID", result.error_code)
        self.assertFalse((root / "routine_instances").exists())

    def test_unknown_definition_is_rejected_without_storage_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)

            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="missing",
                    display_name="Unknown",
                ),
                {},
            )

        self.assertFalse(result.success)
        self.assertEqual("DEFINITION_UNKNOWN", result.error_code)
        self.assertFalse((root / "routine_instances").exists())

    def test_rules_input_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            rules = {"buy": {"conditions": ["A"]}}
            before = json.dumps(rules, sort_keys=True)

            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="No Mutation",
                ),
                rules,
            )

        self.assertTrue(result.success)
        self.assertEqual(before, json.dumps(rules, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
