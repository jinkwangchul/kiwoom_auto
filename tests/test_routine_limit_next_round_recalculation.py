from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

import gui_auto_trade_run_control as run_control
import gui_operation_environment as operation_environment
import gui_windows
import operation_policy_gate
from routine_instance_registry import default_routine_limit_response_policy
from routine_instance_repository import RoutineInstanceRepository
from routine_limit_recalculation import (
    recalculate_enabled_routine_limits_for_new_session,
)


INSTANCE_IDS = tuple(
    UUID(value)
    for value in (
        "29aed0d9-bd9d-4ab5-ae38-8c59fd824461",
        "31a91468-a5bf-41f4-85eb-1f52bfc47ad2",
        "5423f1b4-fc60-4fc9-a52b-97e24108963e",
        "8c976e1b-64fd-47a7-86de-b3a6234c8fc3",
    )
)
BUFFER_ZERO_WARNING = (
    "※완충 0%설정은 심각한 손실을 초래할수 있습니다. 권장 완충은 20%입니다."
)


class _PercentEditor:
    def __init__(self, value: str) -> None:
        self.value = value
        self.finish_count = 0

    def text(self) -> str:
        return self.value

    def finish_display(self) -> None:
        self.finish_count += 1


class RoutineLimitNextRoundRecalculationTest(unittest.TestCase):
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
            ),
            encoding="utf-8",
        )
        return RoutineInstanceRepository(
            root,
            now_factory=lambda: datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
        )

    def _write_instance(
        self,
        root: Path,
        instance_id: UUID,
        *,
        enabled: bool,
        amount: int | None,
        ratio: str | None = None,
        policy: dict[str, object] | None = None,
    ) -> Path:
        instance_dir = root / "routine_instances" / str(instance_id)
        instance_dir.mkdir(parents=True)
        metadata: dict[str, object] = {
            "schema_version": "1.0",
            "instance_id": str(instance_id),
            "definition_id": "indicator_follow",
            "display_name": f"루틴-{str(instance_id)[:4]}",
            "description": "preserve",
            "enabled": False,
            "buy_limit_enabled": enabled,
            "buy_limit_amount": amount,
            "rules_file": "rules.json",
            "unrelated_metadata": {"keep": True},
            "created_at": "2026-08-21T08:00:00+00:00",
            "updated_at": "2026-08-21T08:00:00+00:00",
        }
        if ratio is not None:
            metadata["buy_limit_adjustment_ratio"] = ratio
        if policy is not None:
            metadata["buy_limit_response_policy"] = deepcopy(policy)
        path = instance_dir / "instance.json"
        path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (instance_dir / "rules.json").write_text(
            '{"preserve": true}\n',
            encoding="utf-8",
        )
        return path

    def _recalculate(
        self,
        repository: RoutineInstanceRepository,
        recommendations: dict[str, int | None],
        *,
        total_budget: int | None = 40_000_000,
    ) -> dict[str, object]:
        with patch("routine_instance_repository.append_production_event"):
            return recalculate_enabled_routine_limits_for_new_session(
                object(),
                repository=repository,
                recommendation_provider=lambda _window, instance_id: (
                    recommendations[instance_id],
                    recommendations[instance_id],
                ),
                total_budget_provider=lambda: total_budget,
                invalidate_assignments=lambda _window: None,
            )

    def test_basic_recalculation_changes_only_enabled_instances(self) -> None:
        policy = default_routine_limit_response_policy()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            disabled_path = self._write_instance(
                root, INSTANCE_IDS[0], enabled=False, amount=None, policy=policy
            )
            first_path = self._write_instance(
                root, INSTANCE_IDS[1], enabled=True, amount=8_000_000, policy=policy
            )
            self._write_instance(
                root, INSTANCE_IDS[2], enabled=True, amount=None, policy=policy
            )
            disabled_before = disabled_path.read_bytes()
            first_before = json.loads(first_path.read_text(encoding="utf-8"))
            rules_path = first_path.with_name("rules.json")
            rules_before = rules_path.read_bytes()

            result = self._recalculate(
                repository,
                {
                    str(INSTANCE_IDS[1]): 10_000_000,
                    str(INSTANCE_IDS[2]): 12_000_000,
                },
            )

            first = repository.get_instance(str(INSTANCE_IDS[1]))
            second = repository.get_instance(str(INSTANCE_IDS[2]))
            self.assertTrue(result["ok"])
            self.assertEqual(disabled_before, disabled_path.read_bytes())
            self.assertEqual(10_000_000, first.buy_limit_amount)
            self.assertEqual(12_000_000, second.buy_limit_amount)
            self.assertIsNone(first.buy_limit_adjustment_ratio)
            self.assertEqual(policy, first.buy_limit_response_policy)
            self.assertEqual(policy, second.buy_limit_response_policy)
            first_after = json.loads(first_path.read_text(encoding="utf-8"))
            for key in ("buy_limit_enabled", "buy_limit_amount", "updated_at"):
                first_before.pop(key, None)
                first_after.pop(key, None)
            self.assertEqual(first_before, first_after)
            self.assertEqual(rules_before, rules_path.read_bytes())

    def test_decimal_ratio_is_floored_to_won_and_never_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(
                root,
                INSTANCE_IDS[0],
                enabled=True,
                amount=8_000_000,
                ratio="0.8",
            )

            self._recalculate(
                repository,
                {str(INSTANCE_IDS[0]): 12_000_001},
            )
            loaded = repository.get_instance(str(INSTANCE_IDS[0]))

            self.assertEqual(9_600_000, loaded.buy_limit_amount)
            self.assertIsInstance(loaded.buy_limit_adjustment_ratio, Decimal)
            self.assertEqual(Decimal("0.8"), loaded.buy_limit_adjustment_ratio)

    def test_current_composition_is_requeried_and_stock_limits_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_instance(
                root, INSTANCE_IDS[0], enabled=True, amount=1_000_000
            )
            assignments = {str(INSTANCE_IDS[0]): [2_000_000, 3_000_000]}
            stock_limits = [99_000_000, 88_000_000]
            stock_config = root / "stock_config.json"
            stock_config.write_text(
                json.dumps({"buy_limit_amount": stock_limits[0]}),
                encoding="utf-8",
            )
            stock_before = stock_config.read_bytes()

            def recommendation(_window, instance_id):
                return sum(assignments[instance_id]), min(assignments[instance_id])

            def run():
                with patch("routine_instance_repository.append_production_event"):
                    return recalculate_enabled_routine_limits_for_new_session(
                        object(),
                        repository=repository,
                        recommendation_provider=recommendation,
                        total_budget_provider=lambda: 40_000_000,
                        invalidate_assignments=lambda _window: None,
                    )

            run()
            self.assertEqual(
                5_000_000,
                repository.get_instance(str(INSTANCE_IDS[0])).buy_limit_amount,
            )
            assignments[str(INSTANCE_IDS[0])] = [3_000_000, 4_000_000]
            stock_limits[:] = [1, 1]
            run()
            self.assertEqual(
                7_000_000,
                repository.get_instance(str(INSTANCE_IDS[0])).buy_limit_amount,
            )
            self.assertEqual(stock_before, stock_config.read_bytes())

    def test_incomplete_recommendation_enters_waiting_and_preserves_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            policy = default_routine_limit_response_policy()
            self._write_instance(
                root,
                INSTANCE_IDS[0],
                enabled=True,
                amount=8_000_000,
                ratio="0.8",
                policy=policy,
            )

            self._recalculate(repository, {str(INSTANCE_IDS[0]): None})
            waiting = repository.get_instance(str(INSTANCE_IDS[0]))
            self.assertTrue(waiting.buy_limit_enabled)
            self.assertIsNone(waiting.buy_limit_amount)
            self.assertEqual(Decimal("0.8"), waiting.buy_limit_adjustment_ratio)
            self.assertEqual(policy, waiting.buy_limit_response_policy)

            self._recalculate(
                repository,
                {str(INSTANCE_IDS[0]): 12_000_000},
            )
            applied = repository.get_instance(str(INSTANCE_IDS[0]))
            self.assertEqual(9_600_000, applied.buy_limit_amount)
            self.assertEqual(Decimal("0.8"), applied.buy_limit_adjustment_ratio)

    def test_ceiling_contract_for_recommendation_and_effective_amount(self) -> None:
        cases = (
            (9_999_999, None, True, 9_999_999, None),
            (10_000_000, None, True, 10_000_000, None),
            (10_000_001, None, False, None, None),
            (9_000_000, "0.8", True, 7_200_000, Decimal("0.8")),
            (10_000_000, "1", True, 10_000_000, Decimal("1")),
            (9_000_000, "1.2", False, None, None),
        )
        for recommended, ratio, enabled, amount, expected_ratio in cases:
            with self.subTest(recommended=recommended, ratio=ratio):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    repository = self._repository(root)
                    self._write_instance(
                        root,
                        INSTANCE_IDS[0],
                        enabled=True,
                        amount=5_000_000,
                        ratio=ratio,
                    )
                    self._recalculate(
                        repository,
                        {str(INSTANCE_IDS[0]): recommended},
                        total_budget=10_000_000,
                    )
                    loaded = repository.get_instance(str(INSTANCE_IDS[0]))
                    self.assertEqual(enabled, loaded.buy_limit_enabled)
                    self.assertEqual(amount, loaded.buy_limit_amount)
                    self.assertEqual(expected_ratio, loaded.buy_limit_adjustment_ratio)

    def test_unavailable_total_budget_preserves_instance_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            path = self._write_instance(
                root,
                INSTANCE_IDS[0],
                enabled=True,
                amount=8_000_000,
                ratio="0.8",
                policy=default_routine_limit_response_policy(),
            )
            before = path.read_bytes()

            result = self._recalculate(
                repository,
                {str(INSTANCE_IDS[0]): 12_000_000},
                total_budget=None,
            )

            self.assertFalse(result["ok"])
            self.assertEqual("TOTAL_BUDGET_UNAVAILABLE", result["reason"])
            self.assertEqual(before, path.read_bytes())

    def test_waiting_ratio_round_trip_and_disabled_orphan_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            path = self._write_instance(
                root,
                INSTANCE_IDS[0],
                enabled=True,
                amount=None,
                ratio="0.75",
            )
            loaded = repository.get_instance(str(INSTANCE_IDS[0]))
            self.assertEqual(Decimal("0.75"), loaded.buy_limit_adjustment_ratio)

            metadata = json.loads(path.read_text(encoding="utf-8"))
            metadata["buy_limit_enabled"] = False
            path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(repository.get_instance(str(INSTANCE_IDS[0])))

    def test_floor_and_strict_total_budget_read_contract(self) -> None:
        self.assertEqual(
            9_600_000,
            operation_environment.floor_money_to_won(Decimal("9600000.8")),
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_policy.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(
                operation_environment.read_system_total_budget_for_recalculation(
                    path=path
                )
            )
            path.write_text(
                json.dumps({"system_budget": {"total_budget": 10_000_000}}),
                encoding="utf-8",
            )
            self.assertEqual(
                10_000_000,
                operation_environment.read_system_total_budget_for_recalculation(
                    path=path
                ),
            )

    def test_operation_writer_marks_only_a_new_running_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_state.json"
            path.write_text("{}", encoding="utf-8")
            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", path),
                patch.object(
                    operation_policy_gate,
                    "now_text",
                    side_effect=(
                        "2026-08-21 09:00:00",
                        "2026-08-21 09:10:00",
                        "2026-08-22 09:00:00",
                    ),
                ),
            ):
                first = operation_policy_gate.write_global_operation_running_state(
                    participant_stock_codes=["005930"]
                )
                duplicate = operation_policy_gate.write_global_operation_running_state(
                    participant_stock_codes=["000660"]
                )
                next_session = operation_policy_gate.write_global_operation_running_state(
                    participant_stock_codes=["035420"]
                )

            self.assertTrue(first["started_new_session"])
            self.assertFalse(duplicate["started_new_session"])
            self.assertTrue(next_session["started_new_session"])

    def test_start_integration_runs_only_after_successful_new_session_commit(self) -> None:
        window = MagicMock()
        window.recalculate_routine_limits_for_new_operation_session.return_value = {
            "ok": True
        }
        self.assertIsNone(
            run_control._apply_new_session_routine_limit_recalculation(
                window, {"ok": False, "started_new_session": True}
            )
        )
        self.assertIsNone(
            run_control._apply_new_session_routine_limit_recalculation(
                window, {"ok": True, "started_new_session": False}
            )
        )
        self.assertEqual(
            {"ok": True},
            run_control._apply_new_session_routine_limit_recalculation(
                window, {"ok": True, "started_new_session": True}
            ),
        )
        window.recalculate_routine_limits_for_new_operation_session.assert_called_once_with()

    def test_accepted_start_calls_recalculation_once_and_duplicate_session_does_not(self) -> None:
        from tests.test_auto_trade_same_day_restart_guard import _StartWindow

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "assigned_routine_instance_id": "instance-a",
                        "routine_instance_name": "루틴 A",
                        "operation_mode": "SCHEDULED",
                        "real_trade_enabled": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            queue_path = root / "order_queue.json"
            queue_path.write_text('{"orders": []}', encoding="utf-8")
            window = _StartWindow((stock_dir, "005930", "삼성전자"))
            window.recalculate_routine_limits_for_new_operation_session = MagicMock(
                return_value={"ok": True}
            )

            with (
                patch.object(run_control, "ORDER_QUEUE_PATH", queue_path),
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(
                    run_control,
                    "initial_buy_start_validation",
                    return_value={"allowed": True},
                ),
                patch.object(
                    run_control,
                    "auto_trade_same_day_restart_guard",
                    return_value={"allowed": True},
                ),
                patch.object(run_control, "append_production_event"),
                patch.object(run_control, "append_changelog"),
                patch.object(run_control, "refresh_auto_trade_views"),
                patch.object(
                    run_control,
                    "auto_trade_register_current_session_operation_participants",
                ),
                patch.object(
                    run_control,
                    "write_global_operation_running_state",
                    side_effect=(
                        {"ok": True, "started_new_session": True},
                        {"ok": True, "started_new_session": False},
                    ),
                ),
            ):
                first = run_control.auto_trade_start_selected_auto_trades(
                    window,
                    selected_targets=[(stock_dir, "005930", "삼성전자")],
                )
                second = run_control.auto_trade_start_selected_auto_trades(
                    window,
                    selected_targets=[(stock_dir, "005930", "삼성전자")],
                )

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            window.recalculate_routine_limits_for_new_operation_session.assert_called_once_with()

    def test_buffer_zero_warning_requires_successful_explicit_commit(self) -> None:
        window = MagicMock()
        window.budget_available_percent_edit = _PercentEditor("100")
        window.budget_buffer_percent_edit = _PercentEditor("0")
        window.update_budget_panel = MagicMock()
        toasts: list[str] = []
        with (
            patch.object(
                gui_windows,
                "collect_main_budget_summary",
                return_value={
                    "available_budget_percent": 90,
                    "buffer_budget_percent": 10,
                },
            ),
            patch.object(
                gui_windows,
                "persist_main_budget_percent",
                return_value={
                    "available_budget_percent": 100,
                    "buffer_budget_percent": 0,
                },
            ) as persist,
            patch.object(
                gui_windows,
                "show_toast",
                side_effect=lambda **kwargs: toasts.append(kwargs["message"]),
            ),
        ):
            gui_windows.MainWindow._commit_main_budget_percent(window, "buffer")

        persist.assert_called_once_with("buffer", "0")
        self.assertEqual([BUFFER_ZERO_WARNING], toasts)

    def test_buffer_zero_read_nonzero_invalid_and_failed_save_do_not_warn(self) -> None:
        cases = (
            ("0", 0, None),
            ("20", 10, {"buffer_budget_percent": 20}),
            ("bad", 10, ValueError("invalid")),
            ("0", 10, RuntimeError("write failed")),
        )
        for raw, current, save_result in cases:
            with self.subTest(raw=raw, current=current, save_result=save_result):
                window = MagicMock()
                window.budget_available_percent_edit = _PercentEditor("100")
                window.budget_buffer_percent_edit = _PercentEditor(raw)
                window.update_budget_panel = MagicMock()
                toasts: list[str] = []
                if isinstance(save_result, Exception):
                    persist = MagicMock(side_effect=save_result)
                else:
                    persist = MagicMock(return_value=save_result)
                with (
                    patch.object(
                        gui_windows,
                        "collect_main_budget_summary",
                        return_value={
                            "available_budget_percent": 100 - current,
                            "buffer_budget_percent": current,
                        },
                    ),
                    patch.object(gui_windows, "persist_main_budget_percent", persist),
                    patch.object(
                        gui_windows,
                        "show_toast",
                        side_effect=lambda **kwargs: toasts.append(kwargs["message"]),
                    ),
                ):
                    gui_windows.MainWindow._commit_main_budget_percent(window, "buffer")
                self.assertNotIn(BUFFER_ZERO_WARNING, toasts)


if __name__ == "__main__":
    unittest.main()
