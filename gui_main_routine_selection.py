"""Window-local checkbox state for routine monitoring rows."""

from __future__ import annotations


def sync_routine_selection_state(window, definitions, instances) -> None:
    definition_ids = {str(item.definition_id) for item in definitions}
    instance_definition = {
        str(item.instance_id): str(item.definition_id)
        for item in instances
    }
    definition_enabled = dict(getattr(window, "_routine_definition_enabled", {}))
    selected = dict(getattr(window, "_routine_instance_selection", {}))

    window._routine_definition_enabled = {
        definition_id: bool(definition_enabled.get(definition_id, True))
        for definition_id in definition_ids
    }
    window._routine_instance_selection = {
        instance_id: bool(selected.get(instance_id, True))
        for instance_id in instance_definition
    }
    window._routine_definition_by_instance = instance_definition
    window._routine_instance_ids_by_definition = {
        definition_id: tuple(
            sorted(
                instance_id
                for instance_id, owner_id in instance_definition.items()
                if owner_id == definition_id
            )
        )
        for definition_id in definition_ids
    }


def routine_definition_enabled(window, definition_id: str) -> bool:
    return bool(getattr(window, "_routine_definition_enabled", {}).get(definition_id, True))


def routine_instance_checked(window, instance_id: str) -> bool:
    return bool(getattr(window, "_routine_instance_selection", {}).get(instance_id, True))


def routine_instance_checkbox_enabled(window, instance_id: str) -> bool:
    definition_id = getattr(window, "_routine_definition_by_instance", {}).get(instance_id, "")
    return routine_definition_enabled(window, definition_id)


def toggle_routine_definition(window, definition_id: str) -> bool:
    enabled = not routine_definition_enabled(window, definition_id)
    window._routine_definition_enabled[definition_id] = enabled
    return enabled


def toggle_routine_instance(window, instance_id: str) -> bool:
    if not routine_instance_checkbox_enabled(window, instance_id):
        return routine_instance_checked(window, instance_id)

    checked = not routine_instance_checked(window, instance_id)
    window._routine_instance_selection[instance_id] = checked
    return checked


def selected_routine_instance_ids(window) -> tuple[str, ...]:
    current_ids = {
        instance_id
        for child_ids in getattr(window, "_routine_instance_ids_by_definition", {}).values()
        for instance_id in child_ids
    }
    selected = getattr(window, "_routine_instance_selection", {})
    return tuple(sorted(instance_id for instance_id in current_ids if selected.get(instance_id, True)))
