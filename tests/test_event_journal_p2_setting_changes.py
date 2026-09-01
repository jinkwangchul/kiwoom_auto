from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import event_journal_production
from event_journal_contract import (
    EVENT_TYPE_CATEGORIES,
    EVENT_TYPE_LABELS,
    render_summary,
)
import gui_auto_trade_ats_ops
import gui_auto_trade_setting_window
import gui_auto_trade_status_ops
import gui_operation_environment as operation_environment
import gui_windows
import routine_instance_repository
import stock_repository
from assignment_episode_linkage import (
    AssignmentTransactionResult,
    assign_stock_routine,
    unassign_stock_routine,
)
from routine_instance_repository import (
    RoutineInstanceCreateRequest,
    RoutineInstanceRepository,
)
from stock_repository import StockRepository


INSTANCE_ID = UUID("ab02beab-3aa4-4bde-9d53-c02b03f6090f")
GROUP_ID = UUID("01f74a69-df36-4df3-8e32-ab50e3d585ef")


class EventJournalP2SettingChangeTests(unittest.TestCase):
    @staticmethod
    def _write_assignment_foundation(root: Path) -> None:
        routine_dir = root / "routines" / "indicator_follow"
        routine_dir.mkdir(parents=True)
        (routine_dir / "routine.py").write_text("", encoding="utf-8")
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "definition_id": "indicator_follow",
                    "name": "지표추종매매",
                    "entry_file": "routine.py",
                    "rules_file": "rules.json",
                    "enabled": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        group_dir = root / "groups" / str(GROUP_ID)
        group_dir.mkdir(parents=True)
        (group_dir / "group.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "group_id": str(GROUP_ID),
                    "definition_id": "indicator_follow",
                    "base_name": "그룹A",
                    "display_name": "그룹A",
                    "slot": 0,
                    "created_at": "2026-08-29T09:00:00+09:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "groups" / "registry.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "mode": "logical",
                    "group_ids": [str(GROUP_ID)],
                    "cutover_at": "2026-08-29T09:00:00+09:00",
                }
            ),
            encoding="utf-8",
        )
        instance_dir = root / "routine_instances" / str(INSTANCE_ID)
        instance_dir.mkdir(parents=True)
        (instance_dir / "rules.json").write_text("{}", encoding="utf-8")
        (instance_dir / "instance.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "instance_id": str(INSTANCE_ID),
                    "definition_id": "indicator_follow",
                    "display_name": "루틴A",
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                    "created_at": "2026-08-29T09:00:00+09:00",
                    "updated_at": "2026-08-29T09:00:00+09:00",
                    "group_id": str(GROUP_ID),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_system_budget_change_appends_once_and_same_value_appends_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_policy.json"
            events = Mock()
            with patch.object(operation_environment, "append_production_event", events):
                operation_environment.write_system_budget_policy(
                    total_budget=4_000_000,
                    available_budget_percent=70,
                    path=path,
                )
                self.assertEqual(1, events.call_count)
                event = events.call_args.args[0]
                changes = events.call_args.kwargs["changes"]
                operation_environment.write_system_budget_policy(
                    total_budget=4_000_000,
                    available_budget_percent=70,
                    path=path,
                )

        self.assertEqual("SETTING_CHANGED", event)
        self.assertEqual(
            {"total_budget", "available_budget_percent"},
            {item["field_key"] for item in changes},
        )
        self.assertEqual(1, events.call_count)

    def test_system_budget_writer_failure_has_no_success_event(self):
        events = Mock()
        with (
            patch.object(operation_environment, "append_production_event", events),
            patch.object(
                operation_environment,
                "write_operation_policy",
                side_effect=OSError("write failed"),
            ),
        ):
            with self.assertRaises(OSError):
                operation_environment.write_system_budget_policy(
                    total_budget=2_000_000,
                    available_budget_percent=100,
                )
        events.assert_not_called()

    def test_buffer_response_records_only_changed_leaf_keys(self):
        first = operation_environment.default_buffer_response_policy()
        second = operation_environment.default_buffer_response_policy()
        second["application_mode"] = "SEGMENTED"
        second["threshold_percent"] = 60
        second["strategies"]["loss"]["direction"] = "높은순"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_policy.json"
            with patch.object(operation_environment, "append_production_event") as events:
                operation_environment.write_buffer_response_policy(first, path=path)
                events.reset_mock()
                operation_environment.write_buffer_response_policy(second, path=path)

        self.assertEqual(1, events.call_count)
        self.assertEqual(
            {
                "application_mode",
                "threshold_percent",
                "strategies.loss.direction",
            },
            {item["field_key"] for item in events.call_args.kwargs["changes"]},
        )

    def test_journal_failure_does_not_fail_successful_setting_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_policy.json"
            with patch.object(
                event_journal_production._WRITER,
                "append_event",
                side_effect=OSError("journal unavailable"),
            ):
                saved = operation_environment.write_system_budget_policy(
                    total_budget=3_000_000,
                    available_budget_percent=90,
                    path=path,
                )
        self.assertEqual(3_000_000, saved["total_budget"])

    def test_stock_routine_assignment_move_unassign_and_same_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text("{}", encoding="utf-8")
            self._write_assignment_foundation(root)
            repository = StockRepository(root)
            with patch.object(stock_repository, "append_production_event") as events:
                self.assertTrue(
                    assign_stock_routine(
                        root,
                        "005930",
                        "삼성전자",
                        instance_id=str(INSTANCE_ID),
                        instance_name="루틴A",
                        definition_id="indicator_follow",
                        routine_type="지표추종매매",
                        stock_repository=repository,
                    ).ok
                )
                self.assertTrue(
                    assign_stock_routine(
                        root,
                        "005930",
                        "삼성전자",
                        instance_id=str(INSTANCE_ID),
                        instance_name="루틴A",
                        definition_id="indicator_follow",
                        routine_type="지표추종매매",
                        stock_repository=repository,
                    ).ok
                )
                self.assertTrue(
                    unassign_stock_routine(
                        root,
                        "005930",
                        "삼성전자",
                        [],
                        stock_repository=repository,
                    ).ok
                )

        self.assertEqual(2, events.call_count)
        self.assertTrue(
            all(call.args[0] == "ROUTINE_CHANGED" for call in events.call_args_list)
        )
        self.assertEqual(
            "",
            events.call_args_list[-1].kwargs["changes"][0]["after"],
        )

    def test_stock_routine_transaction_failure_has_no_success_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text("{}", encoding="utf-8")
            repository = StockRepository(root)
            with (
                patch.object(stock_repository, "append_production_event") as events,
                patch(
                    "assignment_episode_linkage.execute_assignment_transaction_foundation",
                    return_value=AssignmentTransactionResult(
                        False,
                        error_code="CONFIG_FAILED",
                        error="failed",
                    ),
                ),
            ):
                self.assertFalse(
                    unassign_stock_routine(
                        root,
                        "005930",
                        "삼성전자",
                        [],
                        stock_repository=repository,
                    ).ok
                )
        events.assert_not_called()

    def _routine_repository(self, root: Path) -> RoutineInstanceRepository:
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
            id_factory=lambda: INSTANCE_ID,
            now_factory=lambda: datetime(2026, 8, 16, tzinfo=timezone.utc),
        )

    def test_routine_instance_lifecycle_and_settings_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = self._routine_repository(Path(temp))
            with patch.object(
                routine_instance_repository,
                "append_production_event",
            ) as events:
                created = repository.create_instance(
                    RoutineInstanceCreateRequest(
                        definition_id="indicator_follow",
                        display_name="루틴A",
                    ),
                    {"buy": {}},
                )
                self.assertTrue(created.success)
                self.assertTrue(repository.rename_instance(str(INSTANCE_ID), "루틴B").success)
                self.assertTrue(
                    repository.update_buy_limit(
                        str(INSTANCE_ID), enabled=True, amount=1_000_000
                    ).success
                )
                self.assertTrue(repository.delete_instance(str(INSTANCE_ID)).success)

        self.assertEqual(4, events.call_count)
        self.assertEqual("ROUTINE_INSTANCE_CREATED", events.call_args_list[0].args[0])
        self.assertEqual(
            "display_name",
            events.call_args_list[1].kwargs["changes"][0]["field_key"],
        )
        self.assertEqual(
            {"buy_limit_enabled", "buy_limit_amount"},
            {
                item["field_key"]
                for item in events.call_args_list[2].kwargs["changes"]
            },
        )
        self.assertEqual("ROUTINE_INSTANCE_DELETED", events.call_args_list[3].args[0])
        self.assertFalse(
            any(call.args[0] == "ROUTINE_CHANGED" for call in events.call_args_list)
        )

    def test_routine_instance_lifecycle_contract_metadata(self):
        self.assertEqual("SETTING", EVENT_TYPE_CATEGORIES["ROUTINE_INSTANCE_CREATED"])
        self.assertEqual("SETTING", EVENT_TYPE_CATEGORIES["ROUTINE_INSTANCE_DELETED"])
        self.assertEqual(
            "루틴 인스턴스 생성",
            EVENT_TYPE_LABELS["ROUTINE_INSTANCE_CREATED"],
        )
        self.assertEqual(
            "루틴 인스턴스 삭제",
            EVENT_TYPE_LABELS["ROUTINE_INSTANCE_DELETED"],
        )
        self.assertEqual(
            "루틴A 루틴 인스턴스가 생성되었습니다.",
            render_summary(
                "ROUTINE_INSTANCE_CREATED", {"routine": "루틴A"}
            )["summary"],
        )
        self.assertEqual(
            "루틴A 루틴 인스턴스가 삭제되었습니다.",
            render_summary(
                "ROUTINE_INSTANCE_DELETED", {"routine": "루틴A"}
            )["summary"],
        )

    def test_routine_instance_validation_and_delete_failures_emit_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = self._routine_repository(Path(temp))
            with patch.object(
                routine_instance_repository,
                "append_production_event",
            ) as events:
                invalid = repository.create_instance(
                    RoutineInstanceCreateRequest(
                        definition_id="indicator_follow",
                        display_name="",
                    ),
                    {},
                )
                self.assertFalse(invalid.success)
                events.assert_not_called()
                created = repository.create_instance(
                    RoutineInstanceCreateRequest(
                        definition_id="indicator_follow",
                        display_name="루틴A",
                    ),
                    {},
                )
                self.assertTrue(created.success)
                events.reset_mock()
                with patch.object(
                    routine_instance_repository.shutil,
                    "rmtree",
                    side_effect=OSError("delete failed"),
                ):
                    deleted = repository.delete_instance(str(INSTANCE_ID))
                self.assertFalse(deleted.success)
                events.assert_not_called()
                self.assertFalse(repository.delete_instance("missing").success)
                events.assert_not_called()

    def test_routine_instance_create_write_failure_emits_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = self._routine_repository(Path(temp))
            with (
                patch.object(
                    routine_instance_repository,
                    "append_production_event",
                ) as events,
                patch.object(
                    repository,
                    "_write_json",
                    side_effect=OSError("create failed"),
                ),
            ):
                created = repository.create_instance(
                    RoutineInstanceCreateRequest(
                        definition_id="indicator_follow",
                        display_name="루틴A",
                    ),
                    {},
                )
        self.assertFalse(created.success)
        events.assert_not_called()

    def test_stock_trading_time_change_and_same_value(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "SCHEDULED"}),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED"}),
                encoding="utf-8",
            )
            with (
                patch.object(
                    gui_auto_trade_status_ops,
                    "operation_mode_change_decision",
                    return_value={"allowed": True},
                ),
                patch.object(
                    gui_auto_trade_status_ops,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch.object(
                    gui_auto_trade_status_ops,
                    "append_production_event",
                ) as events,
            ):
                updates = {"start_time": "09:10:00", "end_buy_time": "13:20:00"}
                self.assertTrue(
                    gui_auto_trade_status_ops.auto_trade_update_stock_operation_mode(
                        None, stock_dir, "005930", "삼성전자", "SCHEDULED", updates
                    )
                )
                self.assertTrue(
                    gui_auto_trade_status_ops.auto_trade_update_stock_operation_mode(
                        None, stock_dir, "005930", "삼성전자", "SCHEDULED", updates
                    )
                )

        self.assertEqual(1, events.call_count)
        self.assertEqual("TRADING_TIME_CHANGED", events.call_args.args[0])
        self.assertIn(
            "start_time",
            {item["field_key"] for item in events.call_args.kwargs["changes"]},
        )

    def test_ats_change_appends_once_and_manual_liquidation_is_not_emitted(self):
        class Window:
            def capture_stock_table_view_state(self):
                return set(), 0

            def load_selected_routine_stocks(self):
                return None

            def restore_stock_table_view_state(self, *_args):
                return None

            def update_action_buttons(self):
                return None

            def parent(self):
                return None

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "CONTINUOUS"}),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            targets = [(stock_dir, "005930", "삼성전자")]
            with patch.object(
                gui_auto_trade_ats_ops,
                "append_production_event",
            ) as events:
                gui_auto_trade_ats_ops.auto_trade_save_manual_ats_state_for_targets(
                    Window(), targets, {"extra1": True, "extra2": False, "extra3": False}
                )
                gui_auto_trade_ats_ops.auto_trade_save_manual_ats_state_for_targets(
                    Window(), targets, {"extra1": True, "extra2": False, "extra3": False}
                )

        self.assertEqual(1, events.call_count)
        self.assertEqual("ATS_CHANGED", events.call_args.args[0])
        self.assertNotEqual("MANUAL_ATS_LIQUIDATION", events.call_args.args[0])

    def test_stock_override_save_reset_and_same_value(self):
        class Value:
            def __init__(self, value):
                self.value = value

            def isChecked(self):
                return bool(self.value)

            def toPlainText(self):
                return str(self.value)

        class Surface:
            OVERRIDE_KEYS = gui_auto_trade_setting_window.StockPolicyOverrideDialog.OVERRIDE_KEYS

            def __init__(self, stock_dir, config, *, enabled=True, memo="운영 메모"):
                self.stock_dir = stock_dir
                self.config_path = stock_dir / "config.json"
                self.code = "005930"
                self.name = "삼성전자"
                self.config = dict(config)
                self._policy_override_opening_config = dict(config)
                self.use_override = Value(enabled)
                self.memo = Value(memo)

            def write_config(self, patch, *, expected_fields):
                return gui_auto_trade_setting_window.StockPolicyOverrideDialog.write_config(
                    self,
                    patch,
                    expected_fields=expected_fields,
                )

            def _append_override_changed(self, before, after):
                return gui_auto_trade_setting_window.StockPolicyOverrideDialog._append_override_changed(
                    self, before, after
                )

            def accept(self):
                return None

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            initial = {"policy_override_enabled": False}
            (stock_dir / "config.json").write_text(
                json.dumps(initial, ensure_ascii=False), encoding="utf-8"
            )
            surface = Surface(stock_dir, initial)
            with (
                patch.object(
                    gui_auto_trade_setting_window,
                    "append_production_event",
                ) as events,
                patch.object(gui_auto_trade_setting_window, "append_stock_log"),
                patch.object(gui_auto_trade_setting_window, "append_changelog"),
                patch.object(gui_auto_trade_setting_window.QMessageBox, "information"),
                patch.object(gui_auto_trade_setting_window.QMessageBox, "critical"),
            ):
                gui_auto_trade_setting_window.StockPolicyOverrideDialog.save_override(surface)
                saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                same_value = Surface(stock_dir, saved)
                gui_auto_trade_setting_window.StockPolicyOverrideDialog.save_override(same_value)
                reset = Surface(stock_dir, saved)
                gui_auto_trade_setting_window.StockPolicyOverrideDialog.reset_all_to_global(reset)

        self.assertEqual(2, events.call_count)
        self.assertTrue(
            all(call.args[0] == "SETTING_CHANGED" for call in events.call_args_list)
        )
        self.assertTrue(
            all(
                item["field_key"] != "policy_override_memo"
                for call in events.call_args_list
                for item in call.kwargs["changes"]
            )
        )

    def test_saved_account_deletion_uses_masked_identity(self):
        class Settings:
            def __init__(self):
                self.values = {
                    gui_windows.ACCOUNT_HISTORY_SETTINGS_KEY: json.dumps(["81291234"]),
                    gui_windows.ACCOUNT_MEMOS_SETTINGS_KEY: json.dumps(
                        {"81291234": "자동매매"}, ensure_ascii=False
                    ),
                }

            def value(self, key, default=""):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

            def sync(self):
                return None

        class Window:
            def __init__(self):
                self._account_memo_settings = Settings()

            def kiwoom_account_numbers(self):
                return []

            def remembered_account_numbers(self):
                return gui_windows.MainWindow.remembered_account_numbers(self)

            def account_memos(self):
                return gui_windows.MainWindow.account_memos(self)

            def refresh_kiwoom_accounts(self):
                return []

        window = Window()
        with patch.object(gui_windows, "append_production_event") as events:
            self.assertTrue(
                gui_windows.MainWindow.delete_saved_account_info(window, "81291234")
            )
            self.assertTrue(
                gui_windows.MainWindow.delete_saved_account_info(window, "81291234")
            )

        self.assertEqual(1, events.call_count)
        self.assertEqual("8129****", events.call_args.kwargs["target_id"])
        self.assertNotIn("81291234", str(events.call_args))


if __name__ == "__main__":
    unittest.main()
