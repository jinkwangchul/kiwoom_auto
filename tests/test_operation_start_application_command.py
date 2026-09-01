from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from gui_auto_trade_run_control import (
    OperationStartCommandRequest,
    OperationStartIntent,
    execute_operation_start_command,
)


class OperationStartApplicationCommandTest(TestCase):
    def _host(self, *, start_targets=(), running_targets=()):
        return SimpleNamespace(
            registered_operation_start_targets=Mock(return_value=list(start_targets)),
            running_registered_operation_targets=Mock(return_value=list(running_targets)),
            update_global_operation_button_state=Mock(),
            statusBarMessage=Mock(),
            parent=Mock(return_value=None),
        )

    def test_full_start_returns_typed_result_from_canonical_backend(self) -> None:
        target = (Path("stocks/000001_target"), "000001", "target")
        host = self._host(start_targets=[target])
        backend = Mock(
            return_value={
                "ok": True,
                "reason": "STARTED",
                "requested": ("000001 target",),
                "requested_count": 1,
                "completed": ("000001 target",),
                "started_count": 1,
                "blocked_count": 0,
            }
        )

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(
                intent=OperationStartIntent.FULL_START,
                source="auto_trade_global_start_button",
            ),
            start_backend=backend,
            operation_state_reader=lambda: {},
        )

        self.assertEqual(OperationStartIntent.FULL_START, result.intent)
        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertEqual(1, result.requested_count)
        self.assertEqual(1, result.started_count)
        self.assertEqual("STARTED", result.results[0]["status"])
        backend.assert_called_once_with(
            host,
            request_scope="multiple",
            selected_targets=[target],
            source="auto_trade_global_start_button",
        )

    def test_full_start_with_running_target_preserves_additional_waiting_intent(self) -> None:
        running = (Path("stocks/000001_running"), "000001", "running")
        waiting = (Path("stocks/000002_waiting"), "000002", "waiting")
        host = self._host(start_targets=[running, waiting], running_targets=[running])
        backend = Mock(
            return_value={
                "ok": True,
                "reason": "STARTED",
                "requested_count": 2,
                "completed": ("000002 waiting",),
                "started_count": 1,
                "already_running_targets": (running,),
                "blocked_count": 0,
            }
        )

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(OperationStartIntent.FULL_START),
            start_backend=backend,
            operation_state_reader=lambda: {},
        )

        self.assertEqual(OperationStartIntent.ADDITIONAL_WAITING_START, result.intent)
        backend.assert_called_once_with(
            host,
            request_scope="multiple",
            selected_targets=[waiting],
            source="auto_trade_global_start_button",
            already_running_targets=[running],
        )

    def test_selective_start_uses_same_command_without_full_start_precheck(self) -> None:
        host = self._host()
        selective_backend = Mock(
            return_value={
                "ok": False,
                "reason": "REVIEW_REQUIRED",
                "requested_count": 1,
                "started_count": 0,
                "blocked_count": 1,
                "blocked_target_details": (
                    {"stock_code": "000001", "reason": "REVIEW_REQUIRED"},
                ),
            }
        )
        operation_state_reader = Mock(side_effect=AssertionError("full-start only"))

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(
                intent=OperationStartIntent.SELECTIVE_START,
                source="auto_trade_context_menu",
            ),
            selective_backend=selective_backend,
            operation_state_reader=operation_state_reader,
        )

        self.assertEqual(OperationStartIntent.SELECTIVE_START, result.intent)
        self.assertFalse(result.ok)
        self.assertEqual(1, result.blocked_count)
        self.assertEqual("REVIEW_REQUIRED", result.reason_code)
        selective_backend.assert_called_once_with(host)
        operation_state_reader.assert_not_called()

    def test_all_running_returns_summary_without_backend_mutation(self) -> None:
        running = (Path("stocks/000001_running"), "000001", "running")
        host = self._host(start_targets=[running], running_targets=[running])
        backend = Mock()
        presenter = Mock()

        result = execute_operation_start_command(
            host,
            OperationStartCommandRequest(OperationStartIntent.FULL_START),
            start_backend=backend,
            operation_state_reader=lambda: {},
            summary_presenter=presenter,
        )

        self.assertEqual("ALREADY_RUNNING", result.reason_code)
        self.assertEqual(1, result.requested_count)
        self.assertEqual(0, result.started_count)
        self.assertEqual(0, result.blocked_count)
        backend.assert_not_called()
        presenter.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
