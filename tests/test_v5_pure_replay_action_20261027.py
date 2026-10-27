"""V5 replay and settings apply remain separate QWidget actions."""
from __future__ import annotations

from PyQt5.QtWidgets import QPushButton

from tests.test_indicator_follow_signal_validation_v5_baseline import case, window_for


def test_v5_pure_replay_action_emits_only_validation_and_blocks_duplicate_clicks(case):
    window = window_for(case)
    runs = []
    applies = []
    window.validation_run_requested.connect(runs.append)
    window.settings_apply_requested.connect(applies.append)

    row = window._signal_validation_action_layout
    replay_buttons = [
        row.itemAt(index).widget()
        for index in range(row.count())
        if isinstance(row.itemAt(index).widget(), QPushButton)
        and row.itemAt(index).widget().text() == "검증"
    ]
    assert len(replay_buttons) == 1
    replay_button = replay_buttons[0]
    assert replay_button is not window.primary_validation_action_button
    assert window.run_validation_button is window.primary_validation_action_button
    assert window.primary_validation_action_button.text() == "설정적용"

    replay_button.click()
    assert len(runs) == 1
    assert applies == []
    assert window._normal_base_requested is False
    assert not replay_button.isEnabled()
    replay_button.click()
    assert len(runs) == 1

    window.show_validation_error("검증 실패")
    assert replay_button.isEnabled()
    window.primary_validation_action_button.click()
    assert len(runs) == 1
    assert len(applies) == 1
