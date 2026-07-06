from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_readiness_preview_controller import (
    RUNTIME_MANAGER_PREVIEW_MISSING,
    build_execution_readiness_preview_from_context,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _RuntimeManagerStub:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[dict, dict, dict]] = []

    def run_dry_run(self, order: dict, guard: dict, confirmations: dict) -> dict:
        self.calls.append((deepcopy(order), deepcopy(guard), deepcopy(confirmations)))
        return deepcopy(self.payload)


class ExecutionReadinessRuntimeManagerExtensionContractTest(unittest.TestCase):
    def _inputs(self, *, status: str = "READY") -> dict:
        return {
            "status": status,
            "summary": f"INPUTS_{status}",
            "gate_result": {"gate_result": "OPEN"},
            "order_candidate": {
                "id": "ORDER_1",
                "status": "REAL_READY",
                "price": 85000,
                "quantity": 10,
            },
            "queue_preview_result": {"preview_connected": True},
            "metadata": {"source": "contract"},
            "warnings": ["Input warning"],
            "issues": [] if status == "READY" else [f"INPUT_{status}"],
        }

    def _controller_result(self, *, status: str = "READY") -> dict:
        return {
            "status": status,
            "completed": status == "READY",
            "summary": f"EXECUTION_READINESS_PREVIEW_{status}",
            "preview_result": {
                "status": status,
                "completed": status == "READY",
                "summary": f"EXECUTION_READINESS_PREVIEW_{status}",
                "warnings": ["Preview mode"],
                "issues": [],
            },
            "formatted_result": {
                "status": status,
                "summary": f"EXECUTION_READINESS_PREVIEW_{status}",
                "text": "Execution Readiness Preview",
            },
            "view_model": {"status": status, "ready": status == "READY"},
            "warnings": ["Preview mode"],
            "issues": [],
        }

    def _preview_context(self) -> dict:
        return {
            "source": "contract",
            "guard": {
                "operator_confirmed": True,
                "real_trade_enabled": True,
                "account_no": "12345678",
            },
        }

    def _build_with_mocks(
        self,
        *,
        input_status: str = "READY",
        preview_status: str = "READY",
        include_runtime_manager_preview: bool = False,
        runtime_manager=None,
        runtime_confirmations=None,
    ) -> dict:
        with (
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_inputs",
                return_value=self._inputs(status=input_status),
            ),
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_preview",
                return_value=self._controller_result(status=preview_status),
            ),
        ):
            return build_execution_readiness_preview_from_context(
                order_id="ORDER_1",
                preview_context=self._preview_context(),
                include_runtime_manager_preview=include_runtime_manager_preview,
                runtime_manager=runtime_manager,
                runtime_confirmations=runtime_confirmations,
            )

    def test_default_off_result_equals_explicit_off(self) -> None:
        default_off = self._build_with_mocks()
        explicit_off = self._build_with_mocks(include_runtime_manager_preview=False)

        self.assertEqual(default_off, explicit_off)

    def test_off_state_has_no_extensions_and_does_not_call_manager(self) -> None:
        manager = _RuntimeManagerStub({"status": "READY"})

        result = self._build_with_mocks(runtime_manager=manager)

        self.assertNotIn("extensions", result["preview_result"])
        self.assertEqual([], manager.calls)

    def test_on_with_manager_adds_runtime_manager_extension(self) -> None:
        payload = {"status": "READY", "runtime_write": False, "session": {"id": "SESSION_1"}}
        manager = _RuntimeManagerStub(payload)
        confirmations = {"manual_execution_runtime_commit_confirmed": True}

        result = self._build_with_mocks(
            include_runtime_manager_preview=True,
            runtime_manager=manager,
            runtime_confirmations=confirmations,
        )

        self.assertEqual(
            payload,
            result["preview_result"]["extensions"]["runtime_manager_preview"],
        )
        self.assertEqual("ORDER_1", manager.calls[0][0]["id"])
        self.assertEqual("12345678", manager.calls[0][1]["account_no"])
        self.assertEqual(confirmations, manager.calls[0][2])
        self.assertEqual("READY", result["status"])

    def test_on_with_missing_manager_adds_warning_only(self) -> None:
        result = self._build_with_mocks(include_runtime_manager_preview=True)

        self.assertEqual("READY", result["status"])
        self.assertIn(RUNTIME_MANAGER_PREVIEW_MISSING, result["warnings"])
        self.assertNotIn("extensions", result["preview_result"])

    def test_input_builder_blocked_or_invalid_does_not_call_manager(self) -> None:
        for status in ("BLOCKED", "INVALID"):
            with self.subTest(status=status):
                manager = _RuntimeManagerStub({"status": "READY"})
                with mock.patch(
                    "execution_readiness_preview_controller.build_execution_readiness_inputs",
                    return_value=self._inputs(status=status),
                ):
                    result = build_execution_readiness_preview_from_context(
                        order_id="ORDER_1",
                        preview_context=self._preview_context(),
                        include_runtime_manager_preview=True,
                        runtime_manager=manager,
                    )

                self.assertEqual(status, result["status"])
                self.assertEqual([], manager.calls)
                self.assertIsNone(result["preview_result"])

    def test_preview_not_ready_does_not_call_manager(self) -> None:
        manager = _RuntimeManagerStub({"status": "READY"})

        result = self._build_with_mocks(
            preview_status="BLOCKED",
            include_runtime_manager_preview=True,
            runtime_manager=manager,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual([], manager.calls)
        self.assertNotIn("extensions", result["preview_result"])

    def test_manager_payload_is_deepcopy_isolated(self) -> None:
        payload = {"status": "READY", "nested": {"value": "original"}}
        manager = _RuntimeManagerStub(payload)

        result = self._build_with_mocks(
            include_runtime_manager_preview=True,
            runtime_manager=manager,
        )
        payload["nested"]["value"] = "mutated-source"
        manager.payload["nested"]["value"] = "mutated-manager"

        self.assertEqual(
            "original",
            result["preview_result"]["extensions"]["runtime_manager_preview"]["nested"]["value"],
        )

    def test_storage_runtime_queue_send_order_and_gui_are_not_called(self) -> None:
        manager = _RuntimeManagerStub({"status": "READY"})
        with (
            mock.patch("execution_runtime_storage.ExecutionRuntimeStorage.commit") as storage_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._build_with_mocks(
                include_runtime_manager_preview=True,
                runtime_manager=manager,
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

        self._build_with_mocks(
            include_runtime_manager_preview=True,
            runtime_manager=_RuntimeManagerStub({"status": "READY"}),
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
