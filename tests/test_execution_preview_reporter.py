# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

from execution_preview_reporter import build_execution_preview_report


class ExecutionPreviewReporterTest(unittest.TestCase):
    def _success_result(self) -> dict:
        return {
            "ok": True,
            "stage": "REAL_READY_ORDER_EXECUTION_PREVIEW",
            "read_result": {
                "ok": True,
                "stage": "ORDER_QUEUE_REAL_READY_READ",
                "order": {"id": "ORDER_1"},
                "blocked_reasons": [],
                "error": None,
            },
            "preview_result": {
                "ok": True,
                "stage": "EXECUTION_PREVIEW_SERVICE",
                "pipeline_result": {
                    "ok": True,
                    "blocked_reason": None,
                    "stage_diagnostics": [
                        {
                            "stage": "hoga_mapper",
                            "ok": True,
                            "reason": "ok",
                            "preview_keys": ["hoga", "ok", "source", "unresolved", "warnings"],
                        },
                        {
                            "stage": "order_type_mapper",
                            "ok": True,
                            "reason": "ok",
                            "preview_keys": ["ok", "order_type", "source", "unresolved", "warnings"],
                        },
                        {
                            "stage": "guard",
                            "ok": True,
                            "reason": "ok",
                            "output_keys": ["blocked_reasons", "ok", "stage", "warnings"],
                        },
                    ],
                    "pipeline": {
                        "execution_preview": {"ok": True, "unresolved": False},
                        "final_guard": {"ok": True},
                        "lock_preview": {"ok": True, "unresolved": False},
                        "request_hash_preview": {"ok": True, "unresolved": False},
                        "execution_request_preview": {
                            "ok": True,
                            "unresolved": False,
                            "execution_request": {
                                "guard_snapshot": {
                                    "operator_confirmed": True,
                                    "real_trade_enabled": True,
                                    "account_no": "12345678",
                                }
                            },
                        },
                    },
                },
                "summary": {
                    "ok": True,
                    "blocked_stage": None,
                    "ready_for_execution_request": True,
                    "order_id": "ORDER_1",
                    "execution_id": "EXEC_PREVIEW_ORDER_1",
                    "request_hash": "a" * 64,
                    "blocked_reason": None,
                    "stage_diagnostics": [
                        {
                            "stage": "hoga_mapper",
                            "ok": True,
                            "reason": "ok",
                            "preview_keys": ["hoga", "ok", "source", "unresolved", "warnings"],
                        },
                        {
                            "stage": "order_type_mapper",
                            "ok": True,
                            "reason": "ok",
                            "preview_keys": ["ok", "order_type", "source", "unresolved", "warnings"],
                        },
                        {
                            "stage": "guard",
                            "ok": True,
                            "reason": "ok",
                            "output_keys": ["blocked_reasons", "ok", "stage", "warnings"],
                        },
                    ],
                    "warnings": [],
                    "blocked_reasons": [],
                },
                "approval_result": {
                    "approved": True,
                    "approval_stage": "approved",
                    "blocked_reasons": [],
                    "next_stage": "EXECUTION_CANDIDATE",
                },
                "candidate_result": {
                    "candidate": True,
                    "candidate_stage": "candidate_created",
                    "candidate_id": "EXEC_CANDIDATE_ORDER_1",
                    "next_stage": "QUEUE_PENDING",
                    "blocked_reasons": [],
                },
                "queue_pending_result": {
                    "queue_pending": True,
                    "queue_pending_stage": "queue_pending_created",
                    "queue_pending_id": "QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1",
                    "next_stage": "QUEUE_WRITER_REQUIRED",
                    "preview_only": True,
                    "no_write": True,
                    "blocked_reasons": [],
                },
                "queue_write_preview_result": {
                    "write_preview": True,
                    "write_stage": "order_queued_record_preview_created",
                    "next_stage": "QUEUE_WRITE_REQUIRED",
                    "preview_only": True,
                    "no_write": True,
                    "blocked_reasons": [],
                    "order_queued_record_preview": {
                        "id": "ORDER_QUEUED_ORDER_1",
                        "status": "ORDER_QUEUED",
                    },
                },
            },
        }

    def test_normal_report(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertTrue(report["ok"])
        self.assertEqual("ORDER_1", report["order_id"])
        self.assertIsNone(report["blocked_stage"])
        self.assertTrue(report["ready_for_execution_request"])
        self.assertEqual("EXEC_PREVIEW_ORDER_1", report["execution_id"])
        self.assertEqual("a" * 64, report["request_hash"])
        self.assertEqual([], report["blocked_reasons"])
        self.assertIn("[Summary]", report["text"])
        self.assertIn("result: PREVIEW_INPUTS_RESOLVED", report["text"])

    def test_approval_section_is_included(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertIn("[Approval]", report["text"])
        self.assertIn("approved: True", report["text"])
        self.assertIn("approval_stage: approved", report["text"])
        self.assertIn("next_stage: EXECUTION_CANDIDATE", report["text"])
        self.assertIn("blocked_reasons:\n-", report["text"])

    def test_candidate_section_is_included(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertIn("[Candidate]", report["text"])
        self.assertIn("candidate: True", report["text"])
        self.assertIn("candidate_stage: candidate_created", report["text"])
        self.assertIn("candidate_id: EXEC_CANDIDATE_ORDER_1", report["text"])
        self.assertIn("next_stage: QUEUE_PENDING", report["text"])

    def test_queue_pending_section_is_included(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertIn("[Queue Pending]", report["text"])
        self.assertIn("queue_pending: True", report["text"])
        self.assertIn("queue_pending_stage: queue_pending_created", report["text"])
        self.assertIn("queue_pending_id: QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1", report["text"])
        self.assertIn("next_stage: QUEUE_WRITER_REQUIRED", report["text"])
        self.assertIn("preview_only: True", report["text"])
        self.assertIn("no_write: True", report["text"])

    def test_queue_writer_dry_run_section_is_included(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertIn("[Queue Writer Dry-Run]", report["text"])
        self.assertIn("write_preview: True", report["text"])
        self.assertIn("write_stage: order_queued_record_preview_created", report["text"])
        self.assertIn("next_stage: QUEUE_WRITE_REQUIRED", report["text"])
        self.assertIn("preview_only: True", report["text"])
        self.assertIn("no_write: True", report["text"])
        self.assertIn("record_preview_status: ORDER_QUEUED", report["text"])

    def test_pipeline_section_includes_stage_diagnostics(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertIn("stage_diagnostics:", report["text"])
        self.assertIn("hoga_mapper: ok=True reason=ok", report["text"])
        self.assertIn("order_type_mapper: ok=True reason=ok", report["text"])
        self.assertIn("guard: ok=True reason=ok", report["text"])
        self.assertIn("preview_keys=", report["text"])
        self.assertIn("output_keys=", report["text"])

    def test_read_failure_report(self) -> None:
        preview_result = {
            "ok": False,
            "stage": "REAL_READY_ORDER_EXECUTION_PREVIEW",
            "read_result": {
                "ok": False,
                "stage": "ORDER_QUEUE_REAL_READY_READ",
                "order": None,
                "blocked_reasons": ["order_id not found"],
                "error": None,
            },
            "preview_result": None,
        }

        report = build_execution_preview_report(preview_result)

        self.assertFalse(report["ok"])
        self.assertFalse(report["ready_for_execution_request"])
        self.assertIn("order_id not found", report["blocked_reasons"])
        self.assertIn("result: BLOCKED", report["text"])

    def test_wrong_order_id_lookup_failure_is_blocked_report_without_runtime_write(self) -> None:
        preview_result = {
            "ok": False,
            "stage": "REAL_READY_ORDER_EXECUTION_PREVIEW",
            "read_result": {
                "ok": False,
                "stage": "ORDER_QUEUE_REAL_READY_READ",
                "order": None,
                "blocked_reasons": ["order_id not found: MISSING_ORDER"],
                "error": None,
            },
            "preview_result": None,
        }

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            report = build_execution_preview_report(preview_result)

        self.assertFalse(report["ok"])
        self.assertFalse(report["ready_for_execution_request"])
        self.assertIn("order_id not found: MISSING_ORDER", report["blocked_reasons"])
        self.assertIn("result: BLOCKED", report["text"])
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_guard_block_report(self) -> None:
        preview_result = self._success_result()
        preview_result["ok"] = False
        preview_result["preview_result"]["ok"] = False
        preview_result["preview_result"]["summary"]["ok"] = False
        preview_result["preview_result"]["summary"]["blocked_stage"] = "final_guard"
        preview_result["preview_result"]["summary"]["blocked_reason"] = (
            "guard.operator_confirmed is not true"
        )
        preview_result["preview_result"]["summary"]["ready_for_execution_request"] = False
        preview_result["preview_result"]["summary"]["blocked_reasons"] = [
            "guard.operator_confirmed is not true"
        ]
        preview_result["preview_result"]["summary"]["stage_diagnostics"] = [
            {
                "stage": "guard",
                "ok": False,
                "reason": "guard.operator_confirmed is not true",
                "output_keys": ["blocked_reasons", "ok", "stage", "warnings"],
            }
        ]
        preview_result["preview_result"]["approval_result"] = {
            "approved": False,
            "approval_stage": "preview_result",
            "blocked_reasons": ["preview result is not ok"],
            "next_stage": "BLOCKED",
        }
        preview_result["preview_result"]["candidate_result"] = {
            "candidate": False,
            "candidate_stage": "approval",
            "next_stage": "BLOCKED",
            "blocked_reasons": ["approval_result.approved is not true"],
        }
        preview_result["preview_result"]["queue_pending_result"] = {
            "queue_pending": False,
            "queue_pending_stage": "candidate",
            "next_stage": "BLOCKED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": ["candidate_result.candidate is not true"],
        }
        preview_result["preview_result"]["queue_write_preview_result"] = {
            "write_preview": False,
            "write_stage": "queue_pending",
            "next_stage": "BLOCKED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": ["queue_pending_result.queue_pending is not true"],
            "order_queued_record_preview": None,
        }

        report = build_execution_preview_report(preview_result)

        self.assertFalse(report["ok"])
        self.assertEqual("final_guard", report["blocked_stage"])
        self.assertIn("guard.operator_confirmed is not true", report["blocked_reasons"])
        self.assertIn("blocked_stage: final_guard", report["text"])
        self.assertIn("top_blocked_reason: guard.operator_confirmed is not true", report["text"])
        self.assertIn("guard: ok=False reason=guard.operator_confirmed is not true", report["text"])

    def test_approval_blocked_report_shows_stage_and_reasons(self) -> None:
        preview_result = self._success_result()
        preview_result["preview_result"]["approval_result"] = {
            "approved": False,
            "approval_stage": "operator_confirmed",
            "blocked_reasons": ["context.operator_confirmed is not true"],
            "next_stage": "BLOCKED",
        }
        preview_result["preview_result"]["candidate_result"] = {
            "candidate": False,
            "candidate_stage": "approval",
            "next_stage": "BLOCKED",
            "blocked_reasons": ["approval_result.approved is not true"],
        }
        preview_result["preview_result"]["queue_pending_result"] = {
            "queue_pending": False,
            "queue_pending_stage": "candidate",
            "next_stage": "BLOCKED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": ["candidate_result.candidate is not true"],
        }
        preview_result["preview_result"]["queue_write_preview_result"] = {
            "write_preview": False,
            "write_stage": "queue_pending",
            "next_stage": "BLOCKED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": ["queue_pending_result.queue_pending is not true"],
            "order_queued_record_preview": None,
        }

        report = build_execution_preview_report(preview_result)

        self.assertIn("[Approval]", report["text"])
        self.assertIn("approved: False", report["text"])
        self.assertIn("approval_stage: operator_confirmed", report["text"])
        self.assertIn("next_stage: BLOCKED", report["text"])
        self.assertIn("- context.operator_confirmed is not true", report["text"])
        self.assertIn("[Candidate]", report["text"])
        self.assertIn("candidate: False", report["text"])
        self.assertIn("candidate_stage: approval", report["text"])
        self.assertIn("- approval_result.approved is not true", report["text"])
        self.assertIn("[Queue Pending]", report["text"])
        self.assertIn("queue_pending: False", report["text"])
        self.assertIn("queue_pending_stage: candidate", report["text"])
        self.assertIn("- candidate_result.candidate is not true", report["text"])
        self.assertIn("[Queue Writer Dry-Run]", report["text"])
        self.assertIn("write_preview: False", report["text"])
        self.assertIn("write_stage: queue_pending", report["text"])
        self.assertIn("- queue_pending_result.queue_pending is not true", report["text"])

    def test_mapper_unresolved_report_uses_blocked_diagnostics_without_order_available_wording(self) -> None:
        preview_result = self._success_result()
        preview_result["ok"] = False
        preview_result["preview_result"]["ok"] = False
        preview_result["preview_result"]["pipeline_result"]["ok"] = False
        preview_result["preview_result"]["pipeline_result"]["blocked_reason"] = "unresolved"
        preview_result["preview_result"]["pipeline_result"]["stage_diagnostics"] = [
            {
                "stage": "hoga_mapper",
                "ok": False,
                "reason": "unresolved",
                "preview_keys": ["hoga", "ok", "source", "unresolved", "warnings"],
            }
        ]
        preview_result["preview_result"]["summary"]["ok"] = False
        preview_result["preview_result"]["summary"]["blocked_stage"] = "execution_preview"
        preview_result["preview_result"]["summary"]["blocked_reason"] = "unresolved"
        preview_result["preview_result"]["summary"]["ready_for_execution_request"] = False
        preview_result["preview_result"]["summary"]["stage_diagnostics"] = [
            {
                "stage": "hoga_mapper",
                "ok": False,
                "reason": "unresolved",
                "preview_keys": ["hoga", "ok", "source", "unresolved", "warnings"],
            }
        ]
        preview_result["preview_result"]["candidate_result"] = {
            "candidate": False,
            "candidate_stage": "approval",
            "next_stage": "BLOCKED",
            "blocked_reasons": ["approval_result.approved is not true"],
        }
        preview_result["preview_result"]["queue_pending_result"] = {
            "queue_pending": False,
            "queue_pending_stage": "candidate",
            "next_stage": "BLOCKED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": ["candidate_result.candidate is not true"],
        }
        preview_result["preview_result"]["queue_write_preview_result"] = {
            "write_preview": False,
            "write_stage": "queue_pending",
            "next_stage": "BLOCKED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": ["queue_pending_result.queue_pending is not true"],
            "order_queued_record_preview": None,
        }

        report = build_execution_preview_report(preview_result)

        self.assertFalse(report["ok"])
        self.assertEqual("execution_preview", report["blocked_stage"])
        self.assertIn("top_blocked_reason: unresolved", report["text"])
        self.assertIn("hoga_mapper: ok=False reason=unresolved", report["text"])
        for phrase in (
            "\uc8fc\ubb38 \uac00\ub2a5",
            "\uc2e4\uc8fc\ubb38 \uac00\ub2a5",
            "\uc804\uc1a1 \uac00\ub2a5",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, report["text"])

    def test_blocked_reasons_and_warnings_are_included(self) -> None:
        preview_result = self._success_result()
        preview_result["preview_result"]["summary"]["blocked_reasons"] = ["reason one"]
        preview_result["preview_result"]["summary"]["warnings"] = ["warning one"]

        report = build_execution_preview_report(preview_result)

        self.assertIn("reason one", report["blocked_reasons"])
        self.assertIn("warning one", report["warnings"])
        self.assertIn("- reason one", report["text"])
        self.assertIn("- warning one", report["text"])

    def test_ok_report_does_not_use_real_order_available_wording(self) -> None:
        report = build_execution_preview_report(self._success_result())

        forbidden = [
            "실주문 가능",
            "주문 가능",
            "전송 가능",
        ]
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, report["text"])

    def test_ok_report_does_not_use_korean_order_available_wording(self) -> None:
        report = build_execution_preview_report(self._success_result())

        for phrase in (
            "\uc8fc\ubb38 \uac00\ub2a5",
            "\uc2e4\uc8fc\ubb38 \uac00\ub2a5",
            "\uc804\uc1a1 \uac00\ub2a5",
            "\uc8fc\ubb38 \ub300\uae30\uc5f4 \uc0dd\uc131",
            "ORDER_QUEUED \uc0dd\uc131",
            "ORDER_QUEUED \uc0dd\uc131 \uc644\ub8cc",
            "\ud050 \uc800\uc7a5 \uc644\ub8cc",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, report["text"])

    def test_order_queued_status_is_shown_only_as_record_preview_status(self) -> None:
        report = build_execution_preview_report(self._success_result())

        self.assertIn("record_preview_status: ORDER_QUEUED", report["text"])
        self.assertNotIn("status: ORDER_QUEUED", report["text"].splitlines())

    def test_report_sections_are_included(self) -> None:
        report = build_execution_preview_report(self._success_result())

        for section in (
            "[Summary]",
            "[Order]",
            "[Guard]",
            "[Pipeline]",
            "[Approval]",
            "[Candidate]",
            "[Queue Pending]",
            "[Queue Writer Dry-Run]",
            "[Blocked Reason]",
            "[Safety / No-Write]",
        ):
            with self.subTest(section=section):
                self.assertIn(section, report["text"])

    def test_reporter_does_not_write_runtime_files(self) -> None:
        preview_result = self._success_result()

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            build_execution_preview_report(preview_result)

        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_input_dict_is_not_mutated(self) -> None:
        preview_result = self._success_result()
        original = deepcopy(preview_result)

        build_execution_preview_report(preview_result)

        self.assertEqual(original, preview_result)


if __name__ == "__main__":
    unittest.main()
