from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import buffer_response_coordinator as buffer_coordinator
import event_journal_production
import gui_auto_trade_run_control as run_control
import gui_main_emergency_ops as emergency_ops
from event_journal_writer import EventJournalWriter


class _Owner:
    pass


class EventJournalP3OperationalFailureTests(unittest.TestCase):
    def test_p3_event_uses_existing_contract_and_temp_writer(self):
        owner = _Owner()
        with tempfile.TemporaryDirectory() as temp:
            writer = EventJournalWriter(Path(temp))
            with patch.object(event_journal_production, "_WRITER", writer):
                result = event_journal_production.observe_owner_failure_transition(
                    owner,
                    "runtime",
                    active=True,
                    signature="RUNTIME_DAMAGED",
                    event_type="RUNTIME_WARNING",
                    severity="WARNING",
                    result="BLOCKED",
                    source="test",
                    template_args={"target": "자동매매 Runtime"},
                    target_type="RUNTIME",
                    target_id="auto_trade_runtime",
                    target_name="자동매매 Runtime",
                    reason_code="RUNTIME_DAMAGED",
                    details={"stage": "operation_start"},
                )
        self.assertTrue(result["appended"])
        self.assertEqual("RUNTIME_WARNING", result["event"]["event_type"])

    def test_failure_transition_records_once_resets_and_records_again(self):
        owner = _Owner()
        with patch.object(event_journal_production, "append_production_event") as append:
            for _ in range(2):
                event_journal_production.observe_owner_failure_transition(
                    owner,
                    "scope",
                    active=True,
                    signature="DAMAGED_RUNTIME",
                    event_type="RUNTIME_WARNING",
                    source="test",
                    template_args={"target": "Runtime"},
                )
            self.assertEqual(1, append.call_count)
            event_journal_production.observe_owner_failure_transition(
                owner,
                "scope",
                active=False,
            )
            event_journal_production.observe_owner_failure_transition(
                owner,
                "scope",
                active=True,
                signature="DAMAGED_RUNTIME",
                event_type="RUNTIME_WARNING",
                source="test",
                template_args={"target": "Runtime"},
            )
        self.assertEqual(2, append.call_count)

    def test_operation_start_records_only_operational_failures(self):
        owner = _Owner()
        runtime_failure = {
            "reason": "START_FAILED",
            "internal_reason": ("RUNTIME_DAMAGED",),
            "requested_count": 1,
            "failed_count": 1,
            "target_stock_code": "005930",
            "target_stock_name": "삼성전자",
        }
        with patch.object(event_journal_production, "append_production_event") as append:
            run_control._record_operation_start_p3_result(owner, runtime_failure)
            run_control._record_operation_start_p3_result(owner, runtime_failure)
            run_control._record_operation_start_p3_result(
                owner,
                {
                    "reason": "NO_TARGETS",
                    "internal_reason": ("MISSING_REQUIRED_SETTINGS",),
                },
            )
        self.assertEqual(1, append.call_count)
        self.assertEqual("RUNTIME_WARNING", append.call_args.args[0])
        self.assertEqual("RUNTIME_DAMAGED", append.call_args.kwargs["reason_code"])
        self.assertNotIn("user_message", append.call_args.kwargs.get("details", {}))

    def test_operation_start_processing_error_is_separate_from_recovery_block(self):
        owner = _Owner()
        with patch.object(event_journal_production, "append_production_event") as append:
            run_control._record_operation_start_p3_result(
                owner,
                {"reason": "TARGET_COLLECTION_FAILED", "requested_count": 0},
            )
            run_control._record_operation_start_p3_result(
                owner,
                {"reason": "RECOVERY_NOT_READY", "requested_count": 1},
            )
        self.assertEqual(1, append.call_count)
        self.assertEqual("PROCESSING_ERROR", append.call_args.args[0])
        self.assertEqual(
            "TARGET_COLLECTION_FAILED",
            append.call_args.kwargs["reason_code"],
        )

    def test_emergency_stock_write_failure_records_once_and_success_resets(self):
        owner = _Owner()
        failed = SimpleNamespace(ok=False, reason="WRITE_FAILED", before_status="RUNNING")
        succeeded = SimpleNamespace(ok=True, reason="", before_status="RUNNING")
        with (
            patch.object(event_journal_production, "append_production_event") as append,
            patch.object(emergency_ops, "mutate_runtime_stock_state", side_effect=[failed, failed, succeeded, failed]),
            patch.object(emergency_ops.QMessageBox, "critical"),
            patch.object(emergency_ops, "append_stock_log"),
        ):
            for _ in range(4):
                emergency_ops.update_runtime_stock_status(
                    owner,
                    SimpleNamespace(),
                    "005930",
                    "삼성전자",
                    "EMERGENCY_STOPPED",
                )
        self.assertEqual(2, append.call_count)
        self.assertTrue(
            all(call.args[0] == "PROCESSING_ERROR" for call in append.call_args_list)
        )

    def test_global_emergency_stop_failure_is_not_duplicated_by_ui_notifications(self):
        owner = _Owner()
        owner.statusBar = lambda: SimpleNamespace(showMessage=lambda _message: None)
        with (
            patch.object(event_journal_production, "append_production_event") as append,
            patch.object(emergency_ops, "read_operation_state", return_value={}),
            patch.object(emergency_ops, "write_global_emergency_stop_state", return_value={"ok": False}),
            patch.object(emergency_ops.QMessageBox, "critical"),
            patch.object(emergency_ops, "update_emergency_button_state"),
            patch.object(emergency_ops, "show_toast"),
        ):
            emergency_ops.execute_emergency_stop(owner)
            emergency_ops.execute_emergency_stop(owner)
        self.assertEqual(1, append.call_count)
        self.assertEqual("PROCESSING_ERROR", append.call_args.args[0])

    def test_global_emergency_release_failure_records_once(self):
        owner = _Owner()
        owner.all_runtime_stock_dirs = lambda: []
        owner.statusBar = lambda: SimpleNamespace(showMessage=lambda _message: None)
        owner.refresh_all = Mock()
        with (
            patch.object(event_journal_production, "append_production_event") as append,
            patch.object(emergency_ops, "read_operation_state", return_value={"emergency_stop": True}),
            patch.object(emergency_ops, "write_global_emergency_stop_state", return_value={"ok": False}),
            patch.object(emergency_ops, "append_changelog"),
            patch.object(emergency_ops, "update_emergency_button_state"),
            patch.object(emergency_ops, "show_toast"),
        ):
            emergency_ops.release_emergency_stop(owner)
            emergency_ops.release_emergency_stop(owner)
        self.assertEqual(1, append.call_count)
        self.assertEqual(
            "GLOBAL_EMERGENCY_RELEASE_WRITE_FAILED",
            append.call_args.kwargs["reason_code"],
        )

    def test_budget_evidence_failure_transition_and_recovery(self):
        owner = _Owner()
        unavailable = {
            "available": False,
            "failure_stage": "budget_activity",
            "buffer_entry_active": False,
        }
        with patch.object(event_journal_production, "append_production_event") as append:
            buffer_coordinator._observe_main_window_buffer_evidence_state(owner, unavailable)
            buffer_coordinator._observe_main_window_buffer_evidence_state(owner, unavailable)
            buffer_coordinator._observe_main_window_buffer_evidence_state(
                owner, {"available": True}
            )
            buffer_coordinator._observe_main_window_buffer_evidence_state(owner, unavailable)
        self.assertEqual(2, append.call_count)
        self.assertTrue(
            all(call.args[0] == "RUNTIME_WARNING" for call in append.call_args_list)
        )
        self.assertTrue(
            all(
                call.kwargs["reason_code"] == "BUDGET_EVIDENCE_UNAVAILABLE"
                for call in append.call_args_list
            )
        )

    def test_recovery_and_account_unavailable_do_not_duplicate_existing_events(self):
        owner = _Owner()
        with patch.object(event_journal_production, "append_production_event") as append:
            for stage in ("account_identity", "recovery"):
                buffer_coordinator._observe_main_window_buffer_evidence_state(
                    owner,
                    {
                        "available": False,
                        "failure_stage": stage,
                        "buffer_entry_active": False,
                    },
                )
        append.assert_not_called()

    def test_buffer_evidence_warns_only_during_confirmed_entry(self):
        inactive_owner = _Owner()
        active_owner = _Owner()
        with patch.object(event_journal_production, "append_production_event") as append:
            buffer_coordinator._observe_main_window_buffer_evidence_state(
                inactive_owner,
                {
                    "available": False,
                    "failure_stage": "pnl_candidates",
                    "buffer_entry_active": False,
                },
            )
            buffer_coordinator._observe_main_window_buffer_evidence_state(
                active_owner,
                {
                    "available": False,
                    "failure_stage": "pnl_candidates",
                    "buffer_entry_active": True,
                },
            )
            buffer_coordinator._observe_main_window_buffer_evidence_state(
                active_owner,
                {
                    "available": False,
                    "failure_stage": "pnl_candidates",
                    "buffer_entry_active": True,
                },
            )
        self.assertEqual(1, append.call_count)
        self.assertEqual(
            "BUFFER_RESPONSE_EVIDENCE_UNAVAILABLE",
            append.call_args.kwargs["reason_code"],
        )

    def test_normal_policy_disabled_is_not_warning_but_malformed_active_policy_is(self):
        owner = _Owner()
        collected = {"budget_activity": {"entry_amount": 10}}
        with patch.object(event_journal_production, "append_production_event") as append:
            buffer_coordinator._observe_main_window_buffer_policy_state(
                owner,
                collected=collected,
                lifecycle={"reason": "BUFFER_RESPONSE_POLICY_NOT_CONFIGURED"},
            )
            buffer_coordinator._observe_main_window_buffer_policy_state(
                owner,
                collected=collected,
                lifecycle={"reason": "BUFFER_RESPONSE_POLICY_MALFORMED"},
            )
            buffer_coordinator._observe_main_window_buffer_policy_state(
                owner,
                collected=collected,
                lifecycle={"reason": "BUFFER_RESPONSE_POLICY_MALFORMED"},
            )
        self.assertEqual(1, append.call_count)
        self.assertEqual(
            "BUFFER_RESPONSE_POLICY_MALFORMED",
            append.call_args.kwargs["reason_code"],
        )
        details = append.call_args.kwargs["details"]
        self.assertEqual({"stage": "buffer_response_policy"}, details)

    def test_sensitive_or_bulk_values_are_not_written(self):
        owner = _Owner()
        with patch.object(event_journal_production, "append_production_event") as append:
            buffer_coordinator._observe_main_window_buffer_evidence_state(
                owner,
                {
                    "available": False,
                    "failure_stage": "runtime_snapshot",
                    "buffer_entry_active": False,
                    "reason": "C:/runtime/account-12345678 traceback broker raw",
                },
            )
        payload = append.call_args.kwargs
        serialized = repr(payload)
        self.assertNotIn("12345678", serialized)
        self.assertNotIn("traceback", serialized)
        self.assertNotIn("C:/runtime", serialized)


if __name__ == "__main__":
    unittest.main()
