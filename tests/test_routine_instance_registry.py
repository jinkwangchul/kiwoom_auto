import json
import tempfile
import unittest
from pathlib import Path

from routine_instance_registry import (
    LEGACY_INSTANCE_SOURCE,
    PERSISTED_INSTANCE_SOURCE,
    load_routine_definition_registry,
    load_routine_definitions,
    load_routine_instance_registry,
    load_routine_instances,
    load_persisted_routine_instances,
    routine_definition_by_id,
    routine_instance_by_id,
)


class RoutineInstanceRegistryTest(unittest.TestCase):
    def _write_routine(self, routines_root: Path, folder_name: str, **overrides: object) -> Path:
        package_dir = routines_root / folder_name
        package_dir.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {
            "schema_version": "1.0",
            "name": "지표추종매매",
            "enabled": True,
            "version": "0.1.0",
            "routine_type": "auto_trade",
            "entry_file": "routine.py",
            "module_name": "indicator_follow_routine",
            "settings_ui": "indicator_follow",
            "rules_file": "rules.json",
        }
        data.update(overrides)
        (package_dir / "routine.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return package_dir

    def _write_instance(
        self,
        root: Path,
        directory_id: str,
        **overrides: object,
    ) -> Path:
        instance_dir = root / "routine_instances" / directory_id
        instance_dir.mkdir(parents=True, exist_ok=True)
        data: dict[str, object] = {
            "schema_version": "1.0",
            "instance_id": directory_id,
            "definition_id": "indicator_follow",
            "display_name": "Large Cap Trend",
            "description": "",
            "enabled": False,
            "buy_limit_enabled": True,
            "buy_limit_amount": 12_000_000,
            "rules_file": "rules.json",
            "created_at": "2026-07-18T14:00:00+09:00",
            "updated_at": "2026-07-18T14:00:00+09:00",
        }
        data.update(overrides)
        (instance_dir / "instance.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (instance_dir / "rules.json").write_text("{}\n", encoding="utf-8")
        return instance_dir

    def test_definition_record_from_existing_routine_json_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routines_root = root / "routines"
            package_dir = self._write_routine(routines_root, "지표추종매매")

            definitions = load_routine_definitions(project_root=root)

        self.assertEqual(1, len(definitions))
        definition = definitions[0]
        self.assertEqual("indicator_follow", definition.definition_id)
        self.assertEqual("지표추종매매", definition.display_name)
        self.assertEqual(package_dir, definition.package_dir)
        self.assertEqual("1.0", definition.schema_version)
        self.assertEqual("0.1.0", definition.version)
        self.assertEqual("auto_trade", definition.routine_type)
        self.assertEqual("routine.py", definition.entry_file)
        self.assertEqual("indicator_follow_routine", definition.module_name)
        self.assertEqual("indicator_follow", definition.settings_ui)
        self.assertEqual("rules.json", definition.default_rules_file)
        self.assertTrue(definition.package_enabled)
        self.assertEqual("지표추종매매", definition.source_name)

    def test_legacy_virtual_instance_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routines_root = root / "routines"
            self._write_routine(routines_root, "지표추종매매")

            instances = load_routine_instances(project_root=root)

            self.assertFalse((root / "routine_instances").exists())

        self.assertEqual(1, len(instances))
        instance = instances[0]
        self.assertEqual("legacy::indicator_follow", instance.instance_id)
        self.assertEqual("indicator_follow", instance.definition_id)
        self.assertEqual("지표추종매매", instance.display_name)
        self.assertEqual("지표추종매매", instance.source_routine_name)
        self.assertFalse(instance.persisted)
        self.assertEqual(LEGACY_INSTANCE_SOURCE, instance.source)
        self.assertTrue(instance.enabled)
        self.assertFalse(instance.real_trade_allowed)

    def test_explicit_definition_id_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routines_root = root / "routines"
            self._write_routine(
                routines_root,
                "custom",
                definition_id="custom_definition",
                settings_ui="indicator_follow",
                module_name="indicator_follow_routine",
            )

            definition = routine_definition_by_id("custom_definition", project_root=root)
            instance = routine_instance_by_id("legacy::custom_definition", project_root=root)

        self.assertIsNotNone(definition)
        self.assertIsNotNone(instance)
        assert definition is not None
        assert instance is not None
        self.assertEqual("custom_definition", definition.definition_id)
        self.assertEqual("custom_definition", instance.definition_id)

    def test_malformed_routine_json_returns_diagnostic_without_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_dir = root / "routines" / "broken"
            package_dir.mkdir(parents=True)
            (package_dir / "routine.json").write_text("{broken", encoding="utf-8")

            registry = load_routine_definition_registry(project_root=root)

        self.assertEqual([], registry.definitions)
        self.assertEqual(["ROUTINE_JSON_PARSE_ERROR"], [item.code for item in registry.diagnostics])

    def test_duplicate_definition_id_is_reported_and_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routines_root = root / "routines"
            self._write_routine(routines_root, "first", name="First", settings_ui="duplicate")
            self._write_routine(routines_root, "second", name="Second", settings_ui="duplicate")

            definitions_registry = load_routine_definition_registry(project_root=root)
            instances_registry = load_routine_instance_registry(project_root=root)

        self.assertEqual([], definitions_registry.definitions)
        self.assertEqual([], instances_registry.instances)
        self.assertIn(
            "DEFINITION_ID_DUPLICATE",
            [item.code for item in definitions_registry.diagnostics],
        )
        self.assertIn(
            "DEFINITION_ID_DUPLICATE",
            [item.code for item in instances_registry.diagnostics],
        )

    def test_missing_routine_json_is_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "routines" / "missing").mkdir(parents=True)

            registry = load_routine_definition_registry(project_root=root)

        self.assertEqual([], registry.definitions)
        self.assertEqual(["ROUTINE_JSON_MISSING"], [item.code for item in registry.diagnostics])

    def test_repeated_load_returns_stable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            routines_root = root / "routines"
            self._write_routine(routines_root, "지표추종매매")

            first_definitions = load_routine_definitions(project_root=root)
            second_definitions = load_routine_definitions(project_root=root)
            first_instances = load_routine_instances(project_root=root)
            second_instances = load_routine_instances(project_root=root)

        self.assertEqual(
            [item.definition_id for item in first_definitions],
            [item.definition_id for item in second_definitions],
        )
        self.assertEqual(
            [item.instance_id for item in first_instances],
            [item.instance_id for item in second_instances],
        )

    def test_persisted_instance_is_loaded_without_mutation(self) -> None:
        instance_id = "a52f539d-4f18-4ef6-b0cf-f471567982a1"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_routine(root / "routines", "indicator")
            instance_dir = self._write_instance(root, instance_id)
            before = (instance_dir / "instance.json").read_bytes()

            instances = load_persisted_routine_instances(project_root=root)

            after = (instance_dir / "instance.json").read_bytes()

        self.assertEqual(before, after)
        self.assertEqual(1, len(instances))
        instance = instances[0]
        self.assertEqual(instance_id, instance.instance_id)
        self.assertEqual(PERSISTED_INSTANCE_SOURCE, instance.source)
        self.assertTrue(instance.persisted)
        self.assertFalse(instance.enabled)
        self.assertTrue(instance.buy_limit_enabled)
        self.assertEqual(12_000_000, instance.buy_limit_amount)
        self.assertEqual(instance_dir / "rules.json", instance.rules_path)
        self.assertFalse(instance.real_trade_allowed)

    def test_invalid_uuid_or_directory_mismatch_is_diagnostic_only(self) -> None:
        instance_id = "a52f539d-4f18-4ef6-b0cf-f471567982a1"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_routine(root / "routines", "indicator")
            self._write_instance(root, instance_id, instance_id="not-a-uuid")

            registry = load_routine_instance_registry(project_root=root)

        self.assertEqual([], [item for item in registry.instances if item.persisted])
        self.assertIn("INSTANCE_ID_INVALID", [item.code for item in registry.diagnostics])

    def test_enabled_buy_limit_requires_positive_integer(self) -> None:
        instance_id = "a52f539d-4f18-4ef6-b0cf-f471567982a1"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_routine(root / "routines", "indicator")
            self._write_instance(root, instance_id, buy_limit_amount=0)

            registry = load_routine_instance_registry(project_root=root)

        self.assertEqual([], [item for item in registry.instances if item.persisted])
        self.assertIn("INSTANCE_BUY_LIMIT_INVALID", [item.code for item in registry.diagnostics])

    def test_rules_path_must_stay_in_instance_directory(self) -> None:
        instance_id = "a52f539d-4f18-4ef6-b0cf-f471567982a1"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_routine(root / "routines", "indicator")
            self._write_instance(root, instance_id, rules_file="../rules.json")

            registry = load_routine_instance_registry(project_root=root)

        self.assertEqual([], [item for item in registry.instances if item.persisted])
        self.assertIn("INSTANCE_RULES_PATH_INVALID", [item.code for item in registry.diagnostics])

    def test_duplicate_display_name_is_scoped_to_definition(self) -> None:
        first = "a52f539d-4f18-4ef6-b0cf-f471567982a1"
        second = "b62f539d-4f18-4ef6-b0cf-f471567982a2"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_routine(root / "routines", "indicator")
            self._write_instance(root, first, display_name="Same Name")
            self._write_instance(root, second, display_name="same name")

            registry = load_routine_instance_registry(project_root=root)

        self.assertEqual([], [item for item in registry.instances if item.persisted])
        self.assertIn(
            "INSTANCE_DISPLAY_NAME_DUPLICATE",
            [item.code for item in registry.diagnostics],
        )


if __name__ == "__main__":
    unittest.main()
