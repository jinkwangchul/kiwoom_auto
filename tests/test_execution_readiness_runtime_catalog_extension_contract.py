from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_readiness_full_preview_orchestrator import run_execution_readiness_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionReadinessRuntimeCatalogExtensionContractTest(unittest.TestCase):
    def _gate(self) -> dict:
        return {
            "ok": True,
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": "OPEN",
            "signal": "BUY",
            "blocked_reasons": [],
        }

    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
            "price": 85000,
            "quantity": 10,
            "order_intent": {"side": "BUY", "hoga": "시장가"},
        }

    def _queue_preview(self) -> dict:
        return {
            "ok": True,
            "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
            "gate_result": "OPEN",
            "order_status": "REAL_READY",
            "queue_writer_preview_connected": True,
            "queue_write_preview_result": {
                "write_preview": True,
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": [],
            },
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _report(self) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_PREVIEW_REPORT",
            "eligible": True,
            "gate": "OPEN",
            "candidate": "REAL_READY",
            "preview_connected": True,
            "blocked_reasons": [],
            "warnings": ["Preview mode"],
        }

    def _inspection(self) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_CANDIDATE_INSPECTION",
            "status": "READY",
            "eligible": True,
            "issues": [],
            "warnings": ["Runtime write disabled", "Execution disabled", "SendOrder disabled"],
            "summary": "READY_FOR_EXECUTION_PREVIEW",
        }

    def _summary(self) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_READINESS_SUMMARY",
            "overall_status": "READY",
            "ready": True,
            "score": 100,
            "decision": "READY_FOR_EXECUTION_PREVIEW",
            "summary": "READY_FOR_EXECUTION_PREVIEW",
            "checks": {"Gate": "PASS"},
            "warnings": ["Preview mode"],
            "issues": [],
        }

    def _audit(self) -> dict:
        return {
            "record_version": 1,
            "created_at": "2026-07-05T10:30:00",
            "record_type": "EXECUTION_READINESS_PREVIEW",
            "decision": "READY_FOR_EXECUTION_PREVIEW",
            "overall_status": "READY",
            "ready": True,
            "score": 100,
            "summary": "READY_FOR_EXECUTION_PREVIEW",
            "checks": {},
            "warnings": ["Preview mode"],
            "issues": [],
            "preview_mode": True,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
            "metadata": {},
        }

    def _snapshot(self) -> dict:
        return {
            "status": "READY",
            "completed": True,
            "summary": "SNAPSHOT_PIPELINE_READY",
            "pipeline_steps": {
                "ExportPreview": "PASS",
                "WriterDryrun": "PASS",
                "ApprovalGate": "PASS",
                "CommitPlanValidation": "PASS",
            },
            "warnings": ["Commit disabled"],
            "issues": [],
        }

    def _payload(self) -> dict:
        return {
            "adapter_type": "EXECUTION_RUNTIME_CATALOG_ADAPTER_PREVIEW",
            "preview_only": True,
            "runtime_write": False,
            "status": "READY",
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "runtime_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
        }

    def _run_preview(self, **kwargs) -> dict:
        with (
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_preview_report", return_value=self._report()),
            mock.patch("execution_readiness_full_preview_orchestrator.inspect_execution_candidate", return_value=self._inspection()),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_summary", return_value=self._summary()),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_audit_record", return_value=self._audit()),
            mock.patch("execution_readiness_full_preview_orchestrator.run_snapshot_pipeline_preview", return_value=self._snapshot()),
        ):
            return run_execution_readiness_preview(
                self._gate(),
                self._order(),
                self._queue_preview(),
                **kwargs,
            )

    def test_default_omitted_matches_explicit_off(self) -> None:
        default_result = self._run_preview()
        explicit_off_result = self._run_preview(
            include_runtime_catalog_preview=False,
            runtime_catalog_payload=self._payload(),
        )

        self.assertEqual(default_result, explicit_off_result)

    def test_default_omitted_has_no_extensions_key(self) -> None:
        result = self._run_preview()

        self.assertNotIn("extensions", result)

    def test_explicit_off_has_no_extensions_key(self) -> None:
        result = self._run_preview(
            include_runtime_catalog_preview=False,
            runtime_catalog_payload=self._payload(),
        )

        self.assertNotIn("extensions", result)

    def test_on_with_payload_adds_runtime_catalog_extension(self) -> None:
        payload = self._payload()

        result = self._run_preview(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload=payload,
        )

        self.assertEqual(
            result["extensions"]["runtime_catalog_preview"],
            payload,
        )

    def test_payload_is_deepcopied_from_original(self) -> None:
        payload = self._payload()
        original_payload = deepcopy(payload)

        result = self._run_preview(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload=payload,
        )
        result["extensions"]["runtime_catalog_preview"]["runtime_targets"]["order_locks"] = "changed"

        self.assertEqual(payload, original_payload)

    def test_on_with_none_payload_keeps_status_and_adds_warning_only(self) -> None:
        baseline = self._run_preview()

        result = self._run_preview(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload=None,
        )

        self.assertEqual(result["status"], baseline["status"])
        self.assertEqual(result["completed"], baseline["completed"])
        self.assertNotIn("extensions", result)
        self.assertIn("RUNTIME_CATALOG_PREVIEW_MISSING", result["warnings"])
        self.assertEqual(result["issues"], baseline["issues"])

    def test_on_with_non_dict_payload_keeps_status_and_adds_warning_only(self) -> None:
        baseline = self._run_preview()

        result = self._run_preview(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload="bad-payload",
        )

        self.assertEqual(result["status"], baseline["status"])
        self.assertEqual(result["completed"], baseline["completed"])
        self.assertNotIn("extensions", result)
        self.assertIn("RUNTIME_CATALOG_PREVIEW_MISSING", result["warnings"])
        self.assertEqual(result["issues"], baseline["issues"])

    def test_on_keeps_preview_only_runtime_write_boundaries(self) -> None:
        result = self._run_preview(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload=self._payload(),
        )

        extension = result["extensions"]["runtime_catalog_preview"]
        self.assertTrue(extension["preview_only"])
        self.assertFalse(extension["runtime_write"])
        self.assertFalse(result["readiness_summary"]["runtime_write"] if "runtime_write" in result["readiness_summary"] else False)
        self.assertFalse(result["audit_record"]["runtime_write"])

    def test_extension_contract_does_not_perform_file_io_or_side_effects(self) -> None:
        runtime_path = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_path)
        before_rules = _sha256(rules_path)

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = self._run_preview(
                include_runtime_catalog_preview=True,
                runtime_catalog_payload=self._payload(),
            )

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()
        logger_info.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, _sha256(runtime_path))
        self.assertEqual(before_rules, _sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
