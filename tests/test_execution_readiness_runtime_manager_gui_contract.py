from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_readiness_preview_controller import build_execution_readiness_preview_from_context


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _RuntimeManagerStub:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, dict]] = []

    def run_dry_run(self, order: dict, guard: dict, confirmations: dict) -> dict:
        self.calls.append((order, guard, confirmations))
        return {
            "manager_type": "EXECUTION_RUNTIME_MANAGER",
            "status": "READY",
            "runtime_write": False,
            "dry_run": True,
            "preview_only": True,
        }


class ExecutionReadinessRuntimeManagerGuiContractTest(unittest.TestCase):
    def _input_ready(self) -> dict:
        return {
            "status": "READY",
            "summary": "INPUTS_READY",
            "gate_result": {"gate_result": "OPEN"},
            "order_candidate": {"id": "ORDER_1", "status": "REAL_READY"},
            "queue_preview_result": {"preview_connected": True},
            "metadata": {"source": "gui_contract"},
            "warnings": [],
            "issues": [],
        }

    def _formatted_text(self) -> str:
        return "Execution Readiness Preview\nGUI TEXT CONTRACT"

    def _controller_result(self) -> dict:
        return {
            "status": "READY",
            "completed": True,
            "summary": "EXECUTION_READINESS_PREVIEW_READY",
            "preview_result": {
                "status": "READY",
                "summary": "EXECUTION_READINESS_PREVIEW_READY",
                "warnings": [],
                "issues": [],
            },
            "formatted_result": {
                "status": "READY",
                "summary": "EXECUTION_READINESS_PREVIEW_READY",
                "text": self._formatted_text(),
                "sections": {"Header": "Overall Status\nREADY"},
            },
            "view_model": {
                "status": "READY",
                "ready": True,
                "title": "Execution Readiness Preview",
                "table_rows": [("Overall Status", "READY")],
            },
            "warnings": [],
            "issues": [],
        }

    def _preview_context(self) -> dict:
        return {
            "source": "gui_execution_preview_button",
            "guard": {"operator_confirmed": True, "real_trade_enabled": True},
        }

    def _build(self, **kwargs) -> dict:
        with (
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_inputs",
                return_value=self._input_ready(),
            ),
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_preview",
                return_value=self._controller_result(),
            ),
        ):
            return build_execution_readiness_preview_from_context(
                order_id="ORDER_1",
                preview_context=self._preview_context(),
                **kwargs,
            )

    def test_default_off_keeps_formatted_text(self) -> None:
        result = self._build()

        self.assertEqual(self._formatted_text(), result["formatted_result"]["text"])

    def test_explicit_off_keeps_formatted_text(self) -> None:
        result = self._build(include_runtime_manager_preview=False)

        self.assertEqual(self._formatted_text(), result["formatted_result"]["text"])

    def test_off_keeps_view_model_equal(self) -> None:
        default_off = self._build()
        explicit_off = self._build(include_runtime_manager_preview=False)

        self.assertEqual(default_off["view_model"], explicit_off["view_model"])

    def test_off_has_no_extensions(self) -> None:
        result = self._build()

        self.assertNotIn("extensions", result["preview_result"])

    def test_on_keeps_gui_text(self) -> None:
        result = self._build(
            include_runtime_manager_preview=True,
            runtime_manager=_RuntimeManagerStub(),
            runtime_confirmations={"manual_execution_runtime_commit_confirmed": True},
        )

        self.assertEqual(self._formatted_text(), result["formatted_result"]["text"])

    def test_on_adds_payload_only_to_extensions(self) -> None:
        manager = _RuntimeManagerStub()

        result = self._build(
            include_runtime_manager_preview=True,
            runtime_manager=manager,
            runtime_confirmations={"manual_execution_runtime_commit_confirmed": True},
        )

        self.assertEqual(
            "READY",
            result["preview_result"]["extensions"]["runtime_manager_preview"]["status"],
        )
        self.assertEqual(self._formatted_text(), result["formatted_result"]["text"])
        self.assertEqual("READY", result["view_model"]["status"])

    def test_gui_files_do_not_need_import_or_modification(self) -> None:
        import execution_readiness_preview_controller

        module_text = execution_readiness_preview_controller.__loader__.get_source(
            execution_readiness_preview_controller.__name__
        )

        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)
        self.assertNotIn("gui_auto_trade_setting_window", module_text)

    def test_runtime_manager_not_called_when_off(self) -> None:
        manager = _RuntimeManagerStub()

        self._build(runtime_manager=manager)

        self.assertEqual([], manager.calls)

    def test_commit_and_send_paths_not_called(self) -> None:
        with (
            mock.patch("execution_runtime_storage.ExecutionRuntimeStorage.commit") as storage_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._build(
                include_runtime_manager_preview=True,
                runtime_manager=_RuntimeManagerStub(),
            )

        self.assertEqual("READY", result["status"])
        storage_commit.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._build(
            include_runtime_manager_preview=True,
            runtime_manager=_RuntimeManagerStub(),
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
