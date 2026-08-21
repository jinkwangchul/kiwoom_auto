from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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
    def _repository(self, root: Path, *, id_factory=None) -> RoutineInstanceRepository:
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
            id_factory=id_factory or (lambda: INSTANCE_ID),
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

    def test_clone_style_create_uses_new_id_without_copying_stock_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            source_instance_id = "b52f539d-4f18-4ef6-b0cf-f471567982a2"
            stock_dir = root / "stocks" / "000660_SK하이닉스"
            stock_dir.mkdir(parents=True)
            config_path = stock_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "routines": ["지표추종매매"],
                        "assigned_routine_instance_id": source_instance_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            before = config_path.read_bytes()

            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="지표추종매매C",
                ),
                {"buy": {"enabled": True}},
            )

            self.assertTrue(result.success)
            self.assertIsNotNone(result.instance)
            self.assertNotEqual(source_instance_id, result.instance.instance_id)
            self.assertEqual(before, config_path.read_bytes())
            saved = json.loads(
                (
                    root
                    / "routine_instances"
                    / result.instance.instance_id
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn("source_instance_id", saved)
            self.assertNotIn("group_id", saved)

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

    def test_create_round_trips_enabled_buy_limit_without_amount(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)

            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Waiting Limit",
                    buy_limit_enabled=True,
                    buy_limit_amount=None,
                ),
                {"buy": {"enabled": True}},
            )
            loaded = repository.get_instance(str(INSTANCE_ID))
            metadata = json.loads(
                (
                    root
                    / "routine_instances"
                    / str(INSTANCE_ID)
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(result.success)
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.buy_limit_enabled)
        self.assertIsNone(loaded.buy_limit_amount)
        self.assertTrue(metadata["buy_limit_enabled"])
        self.assertIsNone(metadata["buy_limit_amount"])

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

    def test_rename_updates_display_name_without_touching_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            create_result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Before Name",
                ),
                {"buy": {"enabled": True}},
            )

            result = repository.rename_instance(str(INSTANCE_ID), "After Name")

            instance_dir = root / "routine_instances" / str(INSTANCE_ID)
            metadata = json.loads((instance_dir / "instance.json").read_text(encoding="utf-8"))
            rules = json.loads((instance_dir / "rules.json").read_text(encoding="utf-8"))

        self.assertTrue(create_result.success)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.instance)
        self.assertEqual("After Name", result.instance.display_name)
        self.assertEqual("After Name", metadata["display_name"])
        self.assertEqual({"buy": {"enabled": True}}, rules)

    def test_rename_rejects_blank_and_duplicate_names(self) -> None:
        ids = iter(
            (
                UUID("a52f539d-4f18-4ef6-b0cf-f471567982a1"),
                UUID("b52f539d-4f18-4ef6-b0cf-f471567982a2"),
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root, id_factory=lambda: next(ids))
            first = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="First",
                ),
                {},
            )
            second = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Second",
                ),
                {},
            )

            blank = repository.rename_instance(str(INSTANCE_ID), "  ")
            duplicate = repository.rename_instance(str(INSTANCE_ID), "second")

            metadata = json.loads(
                (
                    root
                    / "routine_instances"
                    / str(INSTANCE_ID)
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertFalse(blank.success)
        self.assertEqual("DISPLAY_NAME_REQUIRED", blank.error_code)
        self.assertFalse(duplicate.success)
        self.assertEqual("DISPLAY_NAME_DUPLICATE", duplicate.error_code)
        self.assertEqual("First", metadata["display_name"])

    def test_delete_removes_only_target_instance_registration(self) -> None:
        ids = iter(
            (
                UUID("a52f539d-4f18-4ef6-b0cf-f471567982a1"),
                UUID("b52f539d-4f18-4ef6-b0cf-f471567982a2"),
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root, id_factory=lambda: next(ids))
            first = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="First",
                ),
                {},
            )
            second = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Second",
                ),
                {},
            )

            result = repository.delete_instance(str(INSTANCE_ID))

            first_dir = root / "routine_instances" / str(INSTANCE_ID)
            second_dir = root / "routine_instances" / str(second.instance.instance_id)
            first_exists = first_dir.exists()
            second_exists = second_dir.exists()

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(result.success)
        self.assertFalse(first_exists)
        self.assertTrue(second_exists)

    def test_delete_unknown_instance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = self._repository(Path(temp))
            result = repository.delete_instance("missing")

        self.assertFalse(result.success)
        self.assertEqual("INSTANCE_UNKNOWN", result.error_code)

    def test_update_buy_limit_toggles_enabled_amount_without_touching_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            create_result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Limit Routine",
                ),
                {"sell": {"enabled": True}},
            )

            enabled = repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=1_000_000,
            )
            disabled = repository.update_buy_limit(str(INSTANCE_ID), enabled=False)

            instance_dir = root / "routine_instances" / str(INSTANCE_ID)
            metadata = json.loads((instance_dir / "instance.json").read_text(encoding="utf-8"))
            rules = json.loads((instance_dir / "rules.json").read_text(encoding="utf-8"))

        self.assertTrue(create_result.success)
        self.assertTrue(enabled.success)
        self.assertTrue(disabled.success)
        self.assertFalse(metadata["buy_limit_enabled"])
        self.assertIsNone(metadata["buy_limit_amount"])
        self.assertEqual({"sell": {"enabled": True}}, rules)

    def test_update_buy_limit_waiting_round_trip_preserves_other_instance(self) -> None:
        other_id = UUID("b52f539d-4f18-4ef6-b0cf-f471567982a2")
        ids = iter((INSTANCE_ID, other_id))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root, id_factory=lambda: next(ids))
            first = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="First",
                ),
                {"first": True},
            )
            second = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Second",
                    buy_limit_enabled=True,
                    buy_limit_amount=2_000_000,
                ),
                {"second": True},
            )
            other_before = (
                root / "routine_instances" / str(other_id) / "instance.json"
            ).read_bytes()

            waiting = repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=None,
            )
            loaded = repository.get_instance(str(INSTANCE_ID))
            other_after = (
                root / "routine_instances" / str(other_id) / "instance.json"
            ).read_bytes()

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(waiting.success)
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.buy_limit_enabled)
        self.assertIsNone(loaded.buy_limit_amount)
        self.assertEqual(other_before, other_after)

    def test_manual_adjustment_ratio_round_trips_without_float(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Ratio Routine",
                ),
                {},
            )

            result = repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=8_000_000,
                adjustment_ratio=Decimal("0.8"),
            )
            loaded = repository.get_instance(str(INSTANCE_ID))
            metadata = json.loads(
                (
                    root
                    / "routine_instances"
                    / str(INSTANCE_ID)
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(result.success)
        self.assertEqual("0.8", metadata["buy_limit_adjustment_ratio"])
        self.assertIsInstance(loaded.buy_limit_adjustment_ratio, Decimal)
        self.assertEqual(Decimal("0.8"), loaded.buy_limit_adjustment_ratio)

    def test_disabling_buy_limit_clears_adjustment_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Ratio Routine",
                ),
                {},
            )
            repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=8_000_000,
                adjustment_ratio=Decimal("0.8"),
            )

            result = repository.update_buy_limit(str(INSTANCE_ID), enabled=False)
            metadata = json.loads(
                (
                    root
                    / "routine_instances"
                    / str(INSTANCE_ID)
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(result.success)
        self.assertNotIn("buy_limit_adjustment_ratio", metadata)

    def test_automatic_limit_uses_implicit_one_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Automatic Routine",
                ),
                {},
            )

            result = repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=4_000_000,
            )
            metadata = json.loads(
                (
                    root
                    / "routine_instances"
                    / str(INSTANCE_ID)
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(result.success)
        self.assertNotIn("buy_limit_adjustment_ratio", metadata)

    def test_invalid_adjustment_ratio_preserves_existing_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Ratio Routine",
                ),
                {},
            )
            repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=8_000_000,
                adjustment_ratio=Decimal("0.8"),
            )
            metadata_path = (
                root
                / "routine_instances"
                / str(INSTANCE_ID)
                / "instance.json"
            )
            before = metadata_path.read_bytes()

            result = repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=7_000_000,
                adjustment_ratio="not-a-decimal",
            )
            after = metadata_path.read_bytes()

        self.assertFalse(result.success)
        self.assertEqual("BUY_LIMIT_ADJUSTMENT_RATIO_INVALID", result.error_code)
        self.assertEqual(before, after)

    def test_update_buy_limit_rejects_non_positive_enabled_amount(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            create_result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="Limit Routine",
                ),
                {},
            )

            result = repository.update_buy_limit(
                str(INSTANCE_ID),
                enabled=True,
                amount=0,
            )

            metadata = json.loads(
                (
                    root
                    / "routine_instances"
                    / str(INSTANCE_ID)
                    / "instance.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(create_result.success)
        self.assertFalse(result.success)
        self.assertEqual("BUY_LIMIT_INVALID", result.error_code)
        self.assertFalse(metadata["buy_limit_enabled"])
        self.assertIsNone(metadata["buy_limit_amount"])

if __name__ == "__main__":
    unittest.main()
