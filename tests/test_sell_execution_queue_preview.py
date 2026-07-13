from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path
import unittest
from unittest import mock

import sell_execution_queue_preview as subject
from sell_execution_queue_preview import build_sell_execution_queue_preview


def _real_ready_order(
    *,
    status: str = "REAL_READY",
    side: str = "SELL",
    order_type: str = "SELL",
    order_id: str = "ORDER_1",
    source_signal_id: str = "SIG_1",
    hoga: str = "LIMIT",
    quantity: int = 10,
) -> dict:
    return {
        "id": order_id,
        "order_id": order_id,
        "status": status,
        "source_signal_id": source_signal_id,
        "code": "003550",
        "symbol": "003550",
        "side": side,
        "order_type": order_type,
        "hoga": hoga,
        "quantity": quantity,
        "price": None if hoga == "MARKET" else 80000,
        "execution_enabled": True,
        "order_intent": {"side": side, "hoga": hoga},
    }


def _readiness_candidate(order: dict | None = None, *, action_source: str = "METHOD") -> dict:
    return {
        "status": "READY",
        "readiness_ready": True,
        "candidate_index": 0,
        "action_source": action_source,
        "candidate_snapshot": {
            "status": "READY",
            "candidate_index": 0,
            "action_source": action_source,
            "candidate_snapshot": deepcopy(order or _real_ready_order()),
        },
        "stage_checks": {
            "execution_preview": True,
            "final_guard": True,
            "lock_preview": True,
            "request_hash_preview": True,
            "execution_request_preview": True,
        },
        "reasons": [],
        "warnings": ["readiness_warning"],
    }


def _gate_candidate(
    *,
    index: int = 0,
    action_source: str = "METHOD",
    order: dict | None = None,
    status: str = "READY",
    gate_result: str = "OPEN",
    signal: str = "SELL",
    gate_stage: str = "SIGNAL_QUEUE_GATE",
) -> dict:
    order_payload = order or _real_ready_order(order_id=f"ORDER_{index + 1}", source_signal_id=f"SIG_{index + 1}")
    return {
        "source_candidate_index": index,
        "source_signal_id": order_payload.get("source_signal_id"),
        "source_order_id": order_payload.get("id") or order_payload.get("order_id"),
        "action_source": action_source,
        "signal": signal,
        "source_readiness_candidate": _readiness_candidate(order_payload, action_source=action_source),
        "signal_queue_candidate": {
            "stage": "SIGNAL_QUEUE_CANDIDATE",
            "candidate_result": "READY",
            "signal": "SELL",
        },
        "gate_preview": {
            "ok": True,
            "stage": gate_stage,
            "gate_result": gate_result,
            "gate_reason": "gate open",
            "candidate_result": "READY",
            "signal": signal,
            "decision": "SELL",
            "policy_result": "PASS",
            "rule_source": action_source,
            "matched_rule_paths": [],
            "condition_summary": [],
            "applied_policies": [],
            "blocked_policy": None,
            "signal_index": index,
            "delay_bar": 0,
            "queue_connected": False,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        },
        "gate_result": gate_result,
        "status": status,
        "reasons": [],
        "warnings": ["gate_warning"],
        "priority_selected": False,
        "auto_selected": False,
    }


def _signal_gate_preview(*gates: dict, status: str = "READY", signal_gate_ready: bool = True) -> dict:
    return {
        "preview_type": "SELL_SIGNAL_GATE_PREVIEW",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Signal Gate Preview",
        "routine_dependency": None,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "queue_writer_preview_called": False,
        "status": status,
        "signal_gate_ready": signal_gate_ready,
        "opened_gates": list(gates),
        "blocked_gates": [],
        "ignored_gates": [],
        "candidate_gates": list(gates),
        "upstream_blocked_candidates": [],
        "execution_readiness_preview_snapshot": {},
        "warnings": ["top_warning"],
        "reasons": [],
        "summary": {
            "candidate_count": len(gates),
            "opened_gate_count": len(gates),
            "blocked_gate_count": 0,
            "ignored_gate_count": 0,
            "invalid_candidate_count": 0,
            "upstream_blocked_count": 0,
            "priority_selected": False,
            "auto_selected": False,
            "queue_preview_called": False,
        },
    }


def _queue_write_preview(
    *,
    write_preview: bool = True,
    write_stage: str = "order_queued_record_preview_created",
    next_stage: str = "QUEUE_WRITE_REQUIRED",
    preview_only: bool = True,
    no_write: bool = True,
    record: dict | None = None,
    blocked_reasons: list[str] | None = None,
) -> dict:
    return {
        "write_preview": write_preview,
        "write_stage": write_stage,
        "next_stage": next_stage,
        "preview_only": preview_only,
        "no_write": no_write,
        "blocked_reasons": blocked_reasons or [],
        "order_queued_record_preview": record
        if record is not None
        else {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "EXEC_CANDIDATE_ORDER_1",
            "queue_pending_id": "QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1",
            "request_hash": "a" * 64,
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "execution_request": {"execution_id": "EXEC_1", "request_hash": "a" * 64, "lock_id": "LOCK_1"},
            "queue_contract_version": "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        },
    }


def _bridge(
    *,
    ok: bool = True,
    stage: str = "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
    bridge_result: str = "QUEUE_WRITER_PREVIEW_READY",
    connected: bool = True,
    queue_preview: dict | None = None,
    reason: str = "bridge ok",
) -> dict:
    return {
        "ok": ok,
        "stage": stage,
        "bridge_result": bridge_result,
        "bridge_reason": reason,
        "gate_result": "OPEN",
        "gate_stage": "SIGNAL_QUEUE_GATE",
        "candidate_result": "READY",
        "signal": "SELL",
        "order_id": "ORDER_1",
        "order_status": "REAL_READY",
        "queue_writer_preview_connected": connected,
        "queue_write_preview_result": _queue_write_preview() if queue_preview is None else queue_preview,
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


class SellExecutionQueuePreviewTests(unittest.TestCase):
    def _guard(self) -> dict:
        return {"operator_confirmed": True, "real_trade_enabled": True, "account_no": "12345678"}

    def test_open_gate_and_real_ready_sell_builds_queue_preview_ready(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["execution_queue_ready"])
        self.assertEqual(result["summary"]["queue_ready_count"], 1)
        self.assertEqual(result["queue_ready_candidates"][0]["queue_write_preview_result"]["write_stage"], "order_queued_record_preview_created")

    def test_multiple_open_gates_are_all_preserved(self):
        first = _gate_candidate(index=0)
        second = _gate_candidate(index=1, action_source="COMPLETION")

        result = build_sell_execution_queue_preview(_signal_gate_preview(first, second), guard_context=self._guard())

        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["candidate_queue_results"]), 2)
        self.assertEqual(len(result["queue_ready_candidates"]), 2)

    def test_upstream_unknown_status_invalid(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(status="UNKNOWN"))

        self.assertEqual(result["status"], "INVALID")

    def test_upstream_ready_with_signal_gate_ready_false_invalid(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate(), signal_gate_ready=False))

        self.assertEqual(result["status"], "INVALID")

    def test_opened_gates_must_be_list(self):
        preview = _signal_gate_preview(_gate_candidate())
        preview["opened_gates"] = {}

        result = build_sell_execution_queue_preview(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("opened_gates must be a list", result["reasons"])

    def test_gate_candidate_must_be_dict(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview("bad"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["candidate_queue_results"][0]["status"], "INVALID")

    def test_gate_result_not_open_blocked(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate(gate_result="BLOCKED")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("gate_result is not OPEN", result["candidate_queue_results"][0]["reasons"])

    def test_gate_signal_not_sell_invalid(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate(signal="BUY")))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("gate candidate signal must be SELL", result["candidate_queue_results"][0]["reasons"])

    def test_gate_stage_error_invalid(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate(gate_stage="OTHER")))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("gate_preview stage is invalid", result["candidate_queue_results"][0]["reasons"])

    def test_real_ready_order_missing_invalid(self):
        gate = _gate_candidate()
        gate["source_readiness_candidate"]["candidate_snapshot"].pop("candidate_snapshot")

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("real_ready_order must be a dict", result["candidate_queue_results"][0]["reasons"])

    def test_order_status_not_real_ready_blocked(self):
        gate = _gate_candidate(order=_real_ready_order(status="READY"))

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("order status is not REAL_READY", result["candidate_queue_results"][0]["reasons"])

    def test_side_not_sell_invalid(self):
        gate = _gate_candidate(order=_real_ready_order(side="BUY"))

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("order side must be SELL", result["candidate_queue_results"][0]["reasons"])

    def test_order_type_not_sell_invalid(self):
        gate = _gate_candidate(order=_real_ready_order(order_type="BUY"))

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("order_type must be SELL", result["candidate_queue_results"][0]["reasons"])

    def test_source_signal_id_missing_blocked(self):
        gate = _gate_candidate(order=_real_ready_order(source_signal_id=""))

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("source_signal_id is required", result["candidate_queue_results"][0]["reasons"])

    def test_order_id_missing_blocked(self):
        gate = _gate_candidate(order=_real_ready_order(order_id=""))

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("order id is required", result["candidate_queue_results"][0]["reasons"])

    def test_market_blocked(self):
        gate = _gate_candidate(order=_real_ready_order(hoga="MARKET"))

        result = build_sell_execution_queue_preview(_signal_gate_preview(gate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MARKET candidates stay blocked", result["candidate_queue_results"][0]["reasons"][0])

    def test_pending_blocked(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate(action_source="PENDING")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["candidate_queue_results"][0]["reasons"][0])

    def test_cancel_pending_order_blocked(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate(action_source="CANCEL_PENDING_ORDER")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("separate cancel execution path", result["candidate_queue_results"][0]["reasons"][0])

    def test_bridge_result_must_be_dict(self):
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value="bad"):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("bridge_preview must be a dict", result["candidate_queue_results"][0]["reasons"])

    def test_bridge_stage_error_invalid(self):
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(stage="OTHER")):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("bridge_preview stage is invalid", result["candidate_queue_results"][0]["reasons"])

    def test_bridge_blocked_blocks(self):
        with mock.patch(
            "sell_execution_queue_preview.build_signal_gate_execution_queue_bridge",
            return_value=_bridge(ok=False, bridge_result="BLOCKED", connected=False, queue_preview=None, reason="bridge blocked"),
        ):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("bridge blocked", result["candidate_queue_results"][0]["reasons"])

    def test_bridge_ignore_is_preserved(self):
        with mock.patch(
            "sell_execution_queue_preview.build_signal_gate_execution_queue_bridge",
            return_value=_bridge(ok=True, bridge_result="IGNORE", connected=False, queue_preview=None),
        ):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["ignored_queue_candidates"][0]["status"], "IGNORE")

    def test_queue_writer_preview_not_connected_blocked(self):
        with mock.patch(
            "sell_execution_queue_preview.build_signal_gate_execution_queue_bridge",
            return_value=_bridge(ok=True, bridge_result="QUEUE_WRITER_PREVIEW_READY", connected=False),
        ):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("queue_writer_preview_connected is not True", result["candidate_queue_results"][0]["reasons"])

    def test_write_preview_false_blocked(self):
        queue_preview = _queue_write_preview(write_preview=False, blocked_reasons=["queue pending blocked"])
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=queue_preview)):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("queue pending blocked", result["candidate_queue_results"][0]["reasons"])

    def test_write_stage_error_blocked(self):
        queue_preview = _queue_write_preview(write_stage="queue_pending")
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=queue_preview)):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("write_stage is not order_queued_record_preview_created", result["candidate_queue_results"][0]["reasons"])

    def test_next_stage_error_blocked(self):
        queue_preview = _queue_write_preview(next_stage="BLOCKED")
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=queue_preview)):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("next_stage is not QUEUE_WRITE_REQUIRED", result["candidate_queue_results"][0]["reasons"])

    def test_preview_only_false_invalid(self):
        queue_preview = _queue_write_preview(preview_only=False)
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=queue_preview)):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")

    def test_no_write_false_invalid(self):
        queue_preview = _queue_write_preview(no_write=False)
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=queue_preview)):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")

    def test_order_queued_record_missing_invalid(self):
        queue_preview = _queue_write_preview(record=None)
        queue_preview["order_queued_record_preview"] = None
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=queue_preview)):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")

    def test_record_status_not_order_queued_invalid(self):
        record = _queue_write_preview()["order_queued_record_preview"]
        record["status"] = "OTHER"
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=_queue_write_preview(record=record))):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")

    def test_send_order_called_not_false_invalid(self):
        record = _queue_write_preview()["order_queued_record_preview"]
        record["send_order_called"] = True
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=_queue_write_preview(record=record))):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")

    def test_execution_enabled_not_false_invalid(self):
        record = _queue_write_preview()["order_queued_record_preview"]
        record["execution_enabled"] = True
        with mock.patch("sell_execution_queue_preview.build_signal_gate_execution_queue_bridge", return_value=_bridge(queue_preview=_queue_write_preview(record=record))):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()))

        self.assertEqual(result["status"], "INVALID")

    def test_duplicate_request_hash_blocked(self):
        first = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())
        request_hash = first["queue_ready_candidates"][0]["order_queued_record_preview"]["request_hash"]

        result = build_sell_execution_queue_preview(
            _signal_gate_preview(_gate_candidate()),
            guard_context=self._guard(),
            existing_orders=[{"request_hash": request_hash, "lock_id": "OTHER", "order_id": "OTHER"}],
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate request_hash", result["candidate_queue_results"][0]["reasons"])
        self.assertEqual(result["summary"]["duplicate_blocked_count"], 1)

    def test_duplicate_lock_id_blocked(self):
        first = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())
        lock_id = first["queue_ready_candidates"][0]["order_queued_record_preview"]["lock_id"]

        result = build_sell_execution_queue_preview(
            _signal_gate_preview(_gate_candidate()),
            guard_context=self._guard(),
            existing_orders=[{"request_hash": "OTHER", "lock_id": lock_id, "order_id": "OTHER"}],
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate lock_id", result["candidate_queue_results"][0]["reasons"])

    def test_duplicate_order_id_blocked(self):
        result = build_sell_execution_queue_preview(
            _signal_gate_preview(_gate_candidate()),
            guard_context=self._guard(),
            existing_orders=[{"request_hash": "OTHER", "lock_id": "OTHER", "order_id": "ORDER_1"}],
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate order_id", result["candidate_queue_results"][0]["reasons"])

    def test_queue_commit_function_not_called(self):
        with mock.patch("execution_queue_writer.commit_execution_queue_write") as commit_write:
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())

        self.assertEqual(result["status"], "READY")
        commit_write.assert_not_called()

    def test_no_actual_file_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())

        self.assertEqual(result["status"], "READY")
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_input_not_mutated(self):
        preview = _signal_gate_preview(_gate_candidate())
        original = deepcopy(preview)

        result = build_sell_execution_queue_preview(preview, guard_context=self._guard())
        result["candidate_queue_results"][0]["real_ready_order"]["id"] = "MUTATED"

        self.assertEqual(preview, original)

    def test_priority_selected_false(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())

        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["candidate_queue_results"][0]["priority_selected"])

    def test_auto_selected_false(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())

        self.assertFalse(result["summary"]["auto_selected"])
        self.assertFalse(result["candidate_queue_results"][0]["auto_selected"])

    def test_safety_flags_false(self):
        result = build_sell_execution_queue_preview(_signal_gate_preview(_gate_candidate()), guard_context=self._guard())

        for flag in (
            "execution_connected",
            "runtime_write",
            "queue_write",
            "file_write",
            "send_order",
            "broker_api_called",
            "real_ready_state_changed",
            "order_request_created",
            "queue_committed",
        ):
            self.assertFalse(result[flag])

    def test_upstream_blocked_and_ignored_gates_preserved(self):
        preview = _signal_gate_preview(status="BLOCKED", signal_gate_ready=False)
        preview["blocked_gates"] = [{"status": "BLOCKED", "reasons": ["blocked upstream"]}]
        preview["ignored_gates"] = [{"status": "IGNORE"}]

        result = build_sell_execution_queue_preview(preview)

        self.assertEqual(result["upstream_blocked_gates"], [{"status": "BLOCKED", "reasons": ["blocked upstream"]}])
        self.assertEqual(result["upstream_ignored_gates"], [{"status": "IGNORE"}])

    def test_does_not_import_queue_commit_or_runtime_paths(self):
        source = inspect.getsource(subject)

        self.assertNotIn("commit_execution_queue_write", source)
        self.assertNotIn("runtime/order_queue.json", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("ORDER_QUEUED creation", source)


if __name__ == "__main__":
    unittest.main()
