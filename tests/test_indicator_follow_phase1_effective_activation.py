from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QDialog

import auto_trade_order_execution_boundary as execution_boundary
import gui_indicator_follow_routine_settings_dialog as dialog_module
import routine_signal_consumer
import routine_signal_probe
import rule_approval_session_file_service
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)
from tests.qt_test_support import create_qt_widget_shell, dispose_qt_widget


def _load_mapper():
    root = Path(__file__).resolve().parents[1]
    path = root / "routines" / "지표추종매매" / "routine_rule_mapper.py"
    spec = spec_from_file_location("phase1_rule_mapper", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IndicatorFollowEffectiveActivationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.instance_id = "11111111-1111-4111-8111-111111111111"
        self.instance_dir = root / "routine_instances" / self.instance_id
        self.instance_dir.mkdir(parents=True)
        self.rules_path = self.instance_dir / "rules.json"
        source_rules = (
            Path(__file__).resolve().parents[1]
            / "routines"
            / "지표추종매매"
            / "rules.json"
        )
        self.base_rules = json.loads(source_rules.read_text(encoding="utf-8"))
        self.base_rules["bar"]["bar_minutes"] = 1
        self.base_rules["buy"]["execution"] = None
        self.rules_path.write_text(
            json.dumps(self.base_rules, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.session_path = root / "approval_session.json"
        self.mapper = _load_mapper()
        self.ui_state = {
            "basic": {"basic_signal_interval_combo": "5"},
            "buy_ui": {
                "signal_filter": {},
                "base": {
                    "hoga_combo": "단일호가",
                    "order_combo": "주문가",
                    "up_line": "2",
                    "down_line": "1",
                    "time_mode_combo": "다중시간",
                    "time_value_line": "3",
                    "time_unit_combo": "분",
                    "time_range_combo": "이내",
                    "time_count_line": "4",
                    "time_order_combo": "현재가",
                    "ratio_left_combo": "주문가",
                    "ratio_right_combo": "평단가",
                    "ratio_direction_combo": "상향",
                    "ratio_value_line": "1.5",
                    "ratio_compare_combo": "이상",
                    "ratio_count_line": "2",
                },
            },
            "sell_ui": {},
        }
        self.instance = SimpleNamespace(
            instance_id=self.instance_id,
            definition_id="indicator_follow",
            display_name="테스트 루틴",
            enabled=True,
            rules_path=self.rules_path,
        )
        self.dialog = create_qt_widget_shell(
            IndicatorFollowRoutineSettingsDialog,
            QDialog,
        )
        self.dialog.instance_id = self.instance_id
        self.dialog.definition_id = "indicator_follow"
        self.dialog.settings_mode = "edit"
        self.dialog.rules_path = self.rules_path
        self.dialog.rules = deepcopy(self.base_rules)
        self.dialog.rules_data = deepcopy(self.base_rules)
        self.dialog._approval_session_path = self.session_path
        self.dialog._rule_approval_session_dirty = False
        self.dialog.collect_indicator_follow_ui_state = lambda: deepcopy(self.ui_state)
        self.dialog._load_indicator_follow_rule_mapper = lambda: self.mapper

    def tearDown(self) -> None:
        dispose_qt_widget(self.dialog)
        self.temp.cleanup()

    def _save_session(self, decisions: dict[str, str]) -> dict[str, object]:
        rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        preview = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(self.ui_state),
            deepcopy(rules),
        )
        session = self.mapper.build_rule_approval_session(preview, decisions)
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        saved = rule_approval_session_file_service.save_rule_approval_session(
            session,
            self.session_path,
        )
        self.assertTrue(saved["saved"], saved)
        return preview

    def _commit(self):
        with mock.patch.object(
            dialog_module,
            "routine_instance_by_id",
            return_value=self.instance,
        ):
            return self.dialog.commit_saved_approved_rule_changes(
                manual_rule_commit_confirmed=True,
            )

    def test_ui_and_pending_save_do_not_change_effective_core(self) -> None:
        calls: list[str] = []
        owner = SimpleNamespace(
            save_indicator_follow_ui_state_to_rules=lambda: calls.append("ui") or {"success": True},
            save_indicator_follow_rule_pending_to_rules=lambda: calls.append("pending") or {"success": True},
            close=mock.Mock(),
        )
        with mock.patch.object(dialog_module, "_refresh_routine_assignment_views"):
            result = IndicatorFollowRoutineSettingsDialog.save_edit_settings_and_close(owner)
        self.assertTrue(result["success"])
        self.assertEqual(calls, ["ui", "pending"])
        self.assertEqual(self.base_rules["bar"]["bar_minutes"], 1)

    def test_manual_confirmation_is_required(self) -> None:
        self._save_session({"bar.bar_minutes": "APPROVED"})
        before = self.rules_path.read_bytes()
        result = self.dialog.commit_saved_approved_rule_changes(
            manual_rule_commit_confirmed=False,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(before, self.rules_path.read_bytes())

    def test_gui_apply_entrypoint_requires_confirmation_and_calls_production_commit(self) -> None:
        expected = {"ok": True, "committed": True, "blocked_reasons": []}
        self.dialog.commit_saved_approved_rule_changes = mock.Mock(return_value=expected)
        self.dialog.load_rules = mock.Mock()
        with mock.patch.object(
            dialog_module.QMessageBox,
            "question",
            return_value=dialog_module.QMessageBox.Yes,
        ), mock.patch.object(dialog_module.QMessageBox, "information"):
            result = self.dialog._handle_approved_rule_commit_clicked()
        self.assertEqual(result, expected)
        self.dialog.commit_saved_approved_rule_changes.assert_called_once_with(
            manual_rule_commit_confirmed=True,
        )
        self.dialog.load_rules.assert_called_once_with()

    def test_pending_rejected_and_deferred_do_not_commit(self) -> None:
        for decision in ("PENDING", "REJECTED", "DEFERRED"):
            with self.subTest(decision=decision):
                self.rules_path.write_text(
                    json.dumps(self.base_rules, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                self._save_session({"bar.bar_minutes": decision})
                result = self._commit()
                saved = json.loads(self.rules_path.read_text(encoding="utf-8"))
                self.assertFalse(result["ok"])
                self.assertEqual(saved["bar"]["bar_minutes"], 1)

    def test_approved_commit_updates_bar_buy_execution_and_read_back(self) -> None:
        self._save_session(
            {
                "bar.bar_minutes": "APPROVED",
                "buy.execution.base": "APPROVED",
            }
        )
        result = self._commit()
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["committed"])
        self.assertTrue(all(result["read_back_checks"].values()))
        self.assertEqual(saved["bar"]["bar_minutes"], 5)
        self.assertEqual(saved["buy"]["execution"]["base"]["buy_round"], 1)
        self.assertEqual(saved["buy"]["execution"]["base"]["hoga_mode"], "SINGLE")

        reloaded = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["bar"]["bar_minutes"], 5)
        self.assertIn("base", reloaded["buy"]["execution"])

    def test_stale_approval_is_blocked_without_overwrite(self) -> None:
        self._save_session({"bar.bar_minutes": "APPROVED"})
        changed = json.loads(self.rules_path.read_text(encoding="utf-8"))
        changed["rules_version"] = "concurrent-change"
        self.rules_path.write_text(
            json.dumps(changed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = self._commit()
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertEqual(saved["rules_version"], "concurrent-change")
        self.assertEqual(saved["bar"]["bar_minutes"], 1)

    def test_writer_conflict_preserves_concurrent_effective_rules(self) -> None:
        self._save_session({"bar.bar_minutes": "APPROVED"})
        original = dialog_module.rule_apply_commit_service.commit_approved_rule_patch_to_rules

        def conflict_then_commit(path, apply_preview, gate, context):
            if Path(path).resolve() == self.rules_path.resolve():
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                data["rules_version"] = "writer-conflict"
                Path(path).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            return original(path, apply_preview, gate, context)

        with mock.patch.object(
            dialog_module,
            "routine_instance_by_id",
            return_value=self.instance,
        ), mock.patch.object(
            dialog_module.rule_apply_commit_service,
            "commit_approved_rule_patch_to_rules",
            side_effect=conflict_then_commit,
        ):
            result = self.dialog.commit_saved_approved_rule_changes(
                manual_rule_commit_confirmed=True,
            )
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertEqual(saved["rules_version"], "writer-conflict")
        self.assertEqual(saved["bar"]["bar_minutes"], 1)

    def test_read_back_mismatch_is_not_success_and_rolls_back(self) -> None:
        self._save_session({"bar.bar_minutes": "APPROVED"})
        original = dialog_module.rule_apply_commit_service.commit_approved_rule_patch_to_rules

        def mutate_after_real_commit(path, apply_preview, gate, context):
            commit = original(path, apply_preview, gate, context)
            if commit.get("ok") is True and Path(path).resolve() == self.rules_path.resolve():
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                data["bar"]["bar_minutes"] = 99
                Path(path).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            return commit

        with mock.patch.object(
            dialog_module,
            "routine_instance_by_id",
            return_value=self.instance,
        ), mock.patch.object(
            dialog_module.rule_apply_commit_service,
            "commit_approved_rule_patch_to_rules",
            side_effect=mutate_after_real_commit,
        ):
            result = self.dialog.commit_saved_approved_rule_changes(
                manual_rule_commit_confirmed=True,
            )
        saved = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["rollback_result"]["ok"], result)
        self.assertEqual(saved["bar"]["bar_minutes"], 1)

    def test_committed_five_minute_rules_are_reloaded_by_engine_caller(self) -> None:
        self._save_session({"bar.bar_minutes": "APPROVED"})
        result = self._commit()
        self.assertTrue(result["ok"], result)
        captured: dict[str, object] = {}
        stock_dir = Path(self.temp.name) / "005930_Test"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text(
            json.dumps({"trade_enabled": True, "status": "RUNNING"}),
            encoding="utf-8",
        )
        (stock_dir / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": self.instance_id}),
            encoding="utf-8",
        )

        def completed(_candles, rules, **_kwargs):
            captured["bar_minutes"] = rules["bar"]["bar_minutes"]
            return []

        def evaluate(context):
            captured["context_rules"] = context["rules"]
            return {"signal": None, "reason": "test"}

        with mock.patch.object(
            routine_signal_probe,
            "routine_instance_by_id",
            return_value=self.instance,
        ), mock.patch.object(
            routine_signal_probe,
            "_load_candles_from_stock_dir",
            return_value=[],
        ), mock.patch.object(
            routine_signal_probe,
            "completed_timeframe_candles",
            side_effect=completed,
        ), mock.patch.object(
            routine_signal_probe,
            "_maybe_enqueue_signal",
            return_value=None,
        ), mock.patch.object(routine_signal_probe, "_append_log"):
            routine_signal_probe.probe_routine_for_stock(
                SimpleNamespace(ROUTINE_TYPE="INDICATOR_FOLLOW", evaluate=evaluate),
                "테스트 루틴",
                stock_dir,
                "2026-08-20 10:15",
                decision_trace_observer=None,
            )
        self.assertEqual(captured["bar_minutes"], 5)
        self.assertEqual(captured["context_rules"]["bar"]["bar_minutes"], 5)


class IndicatorFollowExecutionKillSwitchTest(unittest.TestCase):
    def test_disabled_instance_is_blocked_at_committed_bar_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"trade_enabled": True, "status": "RUNNING"}),
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "assigned_routine_instance_id": "instance-A",
                        "routine_definition_id": "indicator_follow",
                        "routine_instance_name": "테스트 루틴",
                    }
                ),
                encoding="utf-8",
            )
            snapshot = SimpleNamespace(
                entries=(SimpleNamespace(execution_ready=True, stock_dir=stock_dir),)
            )
            definition = SimpleNamespace(
                definition_id="indicator_follow",
                display_name="지표추종매매",
                package_dir=Path(temp),
            )
            instance = SimpleNamespace(
                definition_id="indicator_follow",
                display_name="테스트 루틴",
                enabled=False,
            )
            with mock.patch.object(
                routine_signal_probe,
                "routine_instance_by_id",
                return_value=instance,
            ), mock.patch.object(routine_signal_probe, "_load_routine_module") as loader:
                result = routine_signal_probe.probe_execution_stock_for_committed_bar(
                    SimpleNamespace(),
                    stock_dir,
                    "2026-08-20 10:15",
                    execution_universe_snapshot=snapshot,
                    _definitions={"indicator_follow": definition},
                )
            self.assertEqual(result["signal"], "SKIP")
            self.assertEqual(result["reason"], "ROUTINE_INSTANCE_DISABLED")
            loader.assert_not_called()

    def test_enabled_instance_continues_to_existing_routine_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"trade_enabled": True, "status": "RUNNING"}),
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "assigned_routine_instance_id": "instance-A",
                        "routine_definition_id": "indicator_follow",
                        "routine_instance_name": "테스트 루틴",
                    }
                ),
                encoding="utf-8",
            )
            snapshot = SimpleNamespace(
                entries=(SimpleNamespace(execution_ready=True, stock_dir=stock_dir),)
            )
            definition = SimpleNamespace(
                definition_id="indicator_follow",
                display_name="지표추종매매",
                package_dir=Path(temp),
            )
            instance = SimpleNamespace(
                definition_id="indicator_follow",
                display_name="테스트 루틴",
                enabled=True,
            )
            expected = {"signal": "NONE", "reason": "test"}
            with mock.patch.object(
                routine_signal_probe,
                "routine_instance_by_id",
                return_value=instance,
            ), mock.patch.object(
                routine_signal_probe,
                "_load_routine_module",
                return_value=SimpleNamespace(),
            ) as loader, mock.patch.object(
                routine_signal_probe,
                "probe_routine_for_stock",
                return_value=expected,
            ) as probe:
                result = routine_signal_probe.probe_execution_stock_for_committed_bar(
                    SimpleNamespace(),
                    stock_dir,
                    "2026-08-20 10:15",
                    execution_universe_snapshot=snapshot,
                    _definitions={"indicator_follow": definition},
                )
            self.assertEqual(result, expected)
            loader.assert_called_once_with(definition)
            probe.assert_called_once()

    def test_principle_execution_switch_blocks_candidate_but_not_signal_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rules_path = Path(temp) / "rules.json"
            rules_path.write_text(
                json.dumps({"principle": {"execution_enabled": False}}),
                encoding="utf-8",
            )
            instance = SimpleNamespace(rules_path=rules_path)
            signal = {
                "id": "SIG-1",
                "status": "PENDING",
                "signal": "BUY",
                "routine_type": "INDICATOR_FOLLOW",
                "routine_instance_id": "instance-A",
            }
            candidate = mock.Mock(return_value={"id": "ORDER-1", "execution_enabled": False})
            append = mock.Mock()
            with mock.patch.object(
                routine_signal_consumer,
                "evaluate_routine_gate",
                return_value={"allowed": False, "reason": "EXECUTION_DISABLED"},
            ), mock.patch.object(
                routine_signal_consumer,
                "read_order_queue",
                return_value={"orders": []},
            ), mock.patch.object(
                routine_signal_consumer,
                "signal_to_order_candidate",
                candidate,
            ), mock.patch.object(
                routine_signal_consumer,
                "append_order_candidates",
                append,
            ):
                result = routine_signal_consumer._build_order_queue_candidates_for_signals(
                    [signal]
                )
            self.assertEqual(result["orders_created"], 0)
            self.assertEqual(result["execution_switch_blocked"], 1)
            candidate.assert_not_called()
            append.assert_not_called()
            self.assertEqual(signal["signal"], "BUY")

    def test_execution_enabled_true_preserves_order_approval_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rules_path = Path(temp) / "rules.json"
            rules_path.write_text(
                json.dumps({"principle": {"execution_enabled": True}}),
                encoding="utf-8",
            )
            instance = SimpleNamespace(rules_path=rules_path)
            signal = {
                "id": "SIG-1",
                "status": "PENDING",
                "signal": "BUY",
                "routine_type": "INDICATOR_FOLLOW",
                "routine_instance_id": "instance-A",
            }
            order = {"id": "ORDER-1", "source_signal_id": "SIG-1"}
            order_approval = mock.Mock(
                return_value={
                    "approval_status": "APPROVED",
                    "approval_reason": "existing order approval",
                }
            )
            append_result = {
                "ok": True,
                "orders_created": 1,
                "duplicates": 0,
                "order_queue_written": True,
                "created_orders": [order],
            }
            with mock.patch.object(
                routine_signal_consumer,
                "evaluate_routine_gate",
                return_value={"allowed": True, "reason": ""},
            ), mock.patch.object(
                routine_signal_consumer,
                "read_order_queue",
                return_value={"orders": []},
            ), mock.patch.object(
                routine_signal_consumer,
                "signal_to_order_candidate",
                return_value=order,
            ), mock.patch.object(
                routine_signal_consumer,
                "append_order_candidates",
                return_value=append_result,
            ), mock.patch.object(
                routine_signal_consumer,
                "evaluate_order_approval",
                order_approval,
            ), mock.patch.object(
                routine_signal_consumer,
                "_apply_operation_policy_to_created_orders",
                return_value={
                    "ok": True,
                    "reason": "",
                    "policy_checked": 0,
                    "policy_executable": 0,
                    "policy_blocked": 0,
                    "policy_errors": 0,
                    "policy_results": [],
                },
            ):
                result = routine_signal_consumer._build_order_queue_candidates_for_signals(
                    [signal],
                    apply_approval=True,
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["orders_created"], 1)
            self.assertEqual(result["execution_switch_blocked"], 0)
            self.assertEqual(result["approval_checked"], 1)
            self.assertEqual(result["approved"], 1)
            order_approval.assert_called_once_with(order)

    def test_real_order_switch_is_independent_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rules_path = Path(temp) / "rules.json"
            order = {
                "side": "BUY",
                "execution_intent": {
                    "routine_type": "INDICATOR_FOLLOW",
                    "routine_instance_id": "instance-A",
                },
            }
            instance = SimpleNamespace(rules_path=rules_path)
            boundary = execution_boundary.AutoTradeOrderExecutionBoundary.__new__(
                execution_boundary.AutoTradeOrderExecutionBoundary
            )

            rules_path.write_text(
                json.dumps({"safety": {"real_order_allowed": False}}),
                encoding="utf-8",
            )
            with mock.patch.object(
                execution_boundary,
                "evaluate_routine_gate",
                return_value={
                    "allowed": False,
                    "reason": "REAL_ORDER_BLOCKED",
                    "reasons": ["REAL_ORDER_BLOCKED"],
                },
            ):
                reasons = boundary.routine_real_order_block_reasons(order)
                preflight = boundary.evaluate_final_dispatch_fresh_preflight(
                    order,
                    {},
                    Path(temp) / "order_queue.json",
                )
            self.assertTrue(reasons)
            self.assertFalse(preflight["ok"])
            self.assertEqual(preflight["stage"], "routine_real_order_safety")

            rules_path.write_text(
                json.dumps({"safety": {"real_order_allowed": True}}),
                encoding="utf-8",
            )
            with mock.patch.object(
                execution_boundary,
                "evaluate_routine_gate",
                return_value={"allowed": True, "reason": "", "reasons": []},
            ):
                self.assertEqual(
                    boundary.routine_real_order_block_reasons(order),
                    [],
                )

    def test_real_order_switch_blocks_before_general_execution_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rules_path = Path(temp) / "rules.json"
            rules_path.write_text(
                json.dumps({"safety": {"real_order_allowed": False}}),
                encoding="utf-8",
            )
            instance = SimpleNamespace(rules_path=rules_path)
            order = {
                "execution_intent": {
                    "routine_type": "INDICATOR_FOLLOW",
                    "routine_instance_id": "instance-A",
                }
            }
            boundary = execution_boundary.AutoTradeOrderExecutionBoundary.__new__(
                execution_boundary.AutoTradeOrderExecutionBoundary
            )
            recovery = mock.Mock(return_value=[])
            boundary.production_recovery_block_reasons_for_order = recovery
            with mock.patch.object(
                execution_boundary,
                "evaluate_routine_gate",
                return_value={
                    "allowed": False,
                    "reason": "REAL_ORDER_BLOCKED",
                    "reasons": ["REAL_ORDER_BLOCKED"],
                },
            ):
                reasons = boundary.auto_trade_execution_block_reasons(order)
            self.assertEqual(reasons, ["REAL_ORDER_BLOCKED"])
            recovery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
