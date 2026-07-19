from __future__ import annotations

from types import SimpleNamespace
import unittest

from gui_main_routine_selection import (
    routine_definition_enabled,
    routine_instance_checkbox_enabled,
    routine_instance_checked,
    selected_routine_instance_ids,
    sync_routine_selection_state,
    toggle_routine_definition,
    toggle_routine_instance,
)


class MainRoutineSelectionTest(unittest.TestCase):
    @staticmethod
    def _definition(definition_id: str = "indicator_follow") -> SimpleNamespace:
        return SimpleNamespace(definition_id=definition_id)

    @staticmethod
    def _instance(instance_id: str, definition_id: str = "indicator_follow") -> SimpleNamespace:
        return SimpleNamespace(instance_id=instance_id, definition_id=definition_id)

    def test_parent_off_preserves_children_and_blocks_child_changes(self) -> None:
        window = SimpleNamespace()
        definitions = [self._definition()]
        instances = [self._instance("a"), self._instance("b"), self._instance("c")]
        sync_routine_selection_state(window, definitions, instances)
        toggle_routine_instance(window, "b")
        self.assertEqual(("a", "c"), selected_routine_instance_ids(window))

        toggle_routine_definition(window, "indicator_follow")
        self.assertFalse(routine_definition_enabled(window, "indicator_follow"))
        self.assertFalse(routine_instance_checkbox_enabled(window, "a"))
        toggle_routine_instance(window, "a")
        self.assertTrue(routine_instance_checked(window, "a"))
        self.assertFalse(routine_instance_checked(window, "b"))
        self.assertTrue(routine_instance_checked(window, "c"))

        toggle_routine_definition(window, "indicator_follow")
        self.assertTrue(routine_definition_enabled(window, "indicator_follow"))
        self.assertEqual(("a", "c"), selected_routine_instance_ids(window))

    def test_all_children_unchecked_do_not_change_parent_state(self) -> None:
        window = SimpleNamespace()
        definitions = [self._definition()]
        instances = [self._instance("a"), self._instance("b")]
        sync_routine_selection_state(window, definitions, instances)

        toggle_routine_instance(window, "a")
        self.assertTrue(routine_definition_enabled(window, "indicator_follow"))
        toggle_routine_instance(window, "b")

        self.assertTrue(routine_definition_enabled(window, "indicator_follow"))
        self.assertFalse(routine_instance_checked(window, "a"))
        self.assertFalse(routine_instance_checked(window, "b"))
        self.assertEqual((), selected_routine_instance_ids(window))

    def test_refresh_sort_add_and_delete_preserve_current_instance_ids(self) -> None:
        window = SimpleNamespace()
        definitions = [self._definition()]
        instances = [self._instance("a"), self._instance("b")]
        sync_routine_selection_state(window, definitions, instances)
        toggle_routine_instance(window, "a")

        sync_routine_selection_state(window, definitions, list(reversed(instances)))
        self.assertFalse(routine_instance_checked(window, "a"))
        self.assertTrue(routine_instance_checked(window, "b"))

        sync_routine_selection_state(
            window,
            definitions,
            [self._instance("a"), self._instance("b"), self._instance("c")],
        )
        self.assertTrue(routine_instance_checked(window, "c"))

        sync_routine_selection_state(window, definitions, [self._instance("b"), self._instance("c")])
        self.assertNotIn("a", window._routine_instance_selection)
        self.assertEqual(("b", "c"), selected_routine_instance_ids(window))


if __name__ == "__main__":
    unittest.main()
