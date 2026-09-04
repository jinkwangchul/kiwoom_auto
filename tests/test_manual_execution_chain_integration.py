# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import operation_policy_gate
import order_approval_engine
from execution_enable_service import commit_execution_enable, preview_execution_enable
from execution_preview_order_service import preview_execution_for_real_ready_order
from production_recovery_contract import STOCK_RESTORED, create_recovery_session_identity
from production_recovery_state_registry import ProductionRecoveryStateRegistry
from real_order_preflight_service import commit_real_order_preflight, preview_real_order_preflight


class ManualExecutionChainIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime_dir = self.root / "runtime"
        self.stocks_dir = self.root / "stocks"
        self.stock_dir = self.stocks_dir / "003550_LG"
        self.queue_path = self.runtime_dir / "order_queue.json"
        self.positions_path = self.runtime_dir / "positions.json"
        self.guard_path = self.runtime_dir / "real_trade_guard.json"
        self.operation_state_path = self.runtime_dir / "operation_state.json"
        self.account_no = "12345678"
        self.runtime_dir.mkdir(parents=True)
        self.stock_dir.mkdir(parents=True)
        self.recovery_registry = self._ready_recovery_registry()
        self._patches = [
            mock.patch.object(order_approval_engine, "RUNTIME_DIR", self.runtime_dir),
            mock.patch.object(order_approval_engine, "ORDER_QUEUE_PATH", self.queue_path),
            mock.patch.object(operation_policy_gate, "RUNTIME_DIR", self.runtime_dir),
            mock.patch.object(operation_policy_gate, "STOCKS_DIR", self.stocks_dir),
            mock.patch.object(operation_policy_gate, "ORDER_QUEUE_PATH", self.queue_path),
            mock.patch.object(operation_policy_gate, "POSITIONS_PATH", self.positions_path),
            mock.patch.object(operation_policy_gate, "OPERATION_STATE_PATH", self.operation_state_path),
            mock.patch.object(
                operation_policy_gate,
                "read_system_total_budget_for_recalculation",
                return_value=4_000,
            ),
            mock.patch.object(
                operation_policy_gate,
                "production_recovery_registry",
                self.recovery_registry,
            ),
        ]
        for patcher in self._patches:
            patcher.start()
        self._write_json(self.operation_state_path, {})
        self._write_json(self.positions_path, {"positions": []})
        self._write_json(
            self.stock_dir / "state.json",
            {
                "status": "MONITORING",
                "trade_enabled": True,
                "review_required": False,
                "liquidating": False,
                "early_close": False,
                "auto_close": False,
            },
        )

    def _ready_recovery_registry(self) -> ProductionRecoveryStateRegistry:
        now = datetime.now()
        identity = create_recovery_session_identity(
            login_session_id="LOGIN_CHAIN_1",
            account_no=self.account_no,
            trading_day=now.date().isoformat(),
            requested_at=now.isoformat(timespec="seconds"),
        )
        registry = ProductionRecoveryStateRegistry()
        self.assertTrue(registry.begin_recovery(identity)["ok"])
        self.assertTrue(registry.mark_collecting(identity)["ok"])
        self.assertTrue(registry.mark_reconciling(identity)["ok"])
        self.assertTrue(
            registry.set_stock_result(
                identity,
                stock_code="003550",
                stock_status=STOCK_RESTORED,
                review_required=False,
            )["ok"]
        )
        self.assertTrue(registry.complete_account(identity)["ok"])
        return registry

    def tearDown(self) -> None:
        for patcher in reversed(self._patches):
            patcher.stop()
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_queue(self) -> dict:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _single_order(self) -> dict:
        orders = self._read_queue().get("orders", [])
        self.assertEqual(1, len(orders))
        return orders[0]

    def _queue_sha256(self) -> str:
        return hashlib.sha256(self.queue_path.read_bytes()).hexdigest().upper()

    def _write_pending_buy_order(self) -> None:
        self._write_json(
            self.queue_path,
            {
                "version": 1,
                "updated_at": "",
                "orders": [
                    {
                        "id": "ORDER_CHAIN_1",
                        "created_at": "2026-07-04 10:00:00",
                        "updated_at": "2026-07-04 10:00:00",
                        "status": "PENDING",
                        "source": "routine_signals",
                        "source_signal_id": "SIG_CHAIN_1",
                        "routine": "지표추종매매",
                        "code": "003550",
                        "name": "LG",
                        "side": "BUY",
                        "order_type": "BUY_SIGNAL_CANDIDATE",
                        "quantity": 10,
                        "amount": 1000,
                        "price": 100,
                        "candidate_status": "CANDIDATE_READY",
                        "candidate_reason": "BUY 주문후보 수량 산정 완료",
                        "budget_source": "entry_amount",
                        "price_basis": "latest_price",
                        "quantity_estimated": 10,
                        "execution_enabled": False,
                        "account_no": self.account_no,
                        "execution_intent": {
                            "side": "BUY",
                            "budget": 1000,
                            "account_no": self.account_no,
                        },
                        "order_intent": {
                            "side": "BUY",
                            "hoga": "시장가",
                        },
                    }
                ],
            },
        )

    def _guard(self) -> dict:
        return {
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }

    def test_manual_execution_pre_sendorder_chain_uses_temp_files_only(self) -> None:
        self._write_pending_buy_order()
        runtime_hash_before = hashlib.sha256(Path("runtime/order_queue.json").read_bytes()).hexdigest().upper()

        approval_result = order_approval_engine.apply_order_approval_to_queue()
        approved_order = self._single_order()
        self.assertEqual(1, approval_result["approved"])
        self.assertEqual("APPROVED", approved_order["status"])
        self.assertEqual("APPROVED", approved_order["approval_status"])
        self.assertFalse(approved_order["execution_enabled"])

        policy_result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_CHAIN_1",
            queue_path=self.queue_path,
        )
        executable_order = self._single_order()
        self.assertTrue(policy_result["ok"])
        self.assertEqual("EXECUTABLE", executable_order["status"])
        self.assertEqual("EXECUTABLE", executable_order["policy_status"])
        self.assertFalse(executable_order["execution_enabled"])
        policy_evidence = policy_result["policy_evidence"]
        self.assertTrue(policy_evidence["available"])
        self.assertTrue(policy_evidence["admitted"])
        self.assertEqual(self.account_no, policy_evidence["account_no"])
        self.assertEqual(4_000, policy_evidence["system_total_budget"])
        self.assertEqual(0, policy_evidence["account_consumed_amount"])
        self.assertEqual(1_000, policy_evidence["candidate_buy_amount"])

        enable_preview = preview_execution_enable(
            executable_order,
            {"operator_confirmed_for_execution_enable": True},
        )
        self.assertTrue(enable_preview["enable_preview"])
        before_enable_sha = self._queue_sha256()
        enable_commit = commit_execution_enable(
            enable_preview,
            self.queue_path,
            preview_queue_snapshot={"sha256": before_enable_sha},
            context={"manual_execution_enable_commit_confirmed": True},
        )
        enabled_order = self._single_order()
        self.assertTrue(enable_commit["enabled"])
        self.assertEqual("REAL_PREFLIGHT_REQUIRED", enable_commit["next_stage"])
        self.assertEqual("EXECUTABLE", enabled_order["status"])
        self.assertTrue(enabled_order["execution_enabled"])
        self.assertTrue(Path(enable_commit["backup_path"]).exists())

        guard = self._guard()
        self._write_json(self.guard_path, guard)
        preflight_preview = preview_real_order_preflight(
            enabled_order,
            guard,
            {"manual_real_preflight_confirmed": True},
        )
        self.assertTrue(preflight_preview["real_preflight_preview"])
        before_preflight_sha = self._queue_sha256()
        preflight_commit = commit_real_order_preflight(
            preflight_preview,
            self.queue_path,
            guard_path=self.guard_path,
            preview_queue_snapshot={"sha256": before_preflight_sha},
            context={"manual_real_preflight_commit_confirmed": True},
        )
        real_ready_order = self._single_order()
        self.assertTrue(preflight_commit["real_preflight_committed"])
        self.assertEqual("EXECUTION_PREVIEW_REQUIRED", preflight_commit["next_stage"])
        self.assertEqual("REAL_READY", real_ready_order["status"])
        self.assertEqual("REAL_READY", real_ready_order["real_preflight_status"])
        self.assertTrue(real_ready_order["execution_enabled"])
        self.assertTrue(Path(preflight_commit["backup_path"]).exists())

        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("execution_queue_writer.commit_execution_queue_write") as queue_write_commit,
        ):
            preview_result = preview_execution_for_real_ready_order(
                "ORDER_CHAIN_1",
                guard,
                queue_path=self.queue_path,
            )

        self.assertTrue(preview_result["ok"])
        self.assertTrue(preview_result["read_result"]["ok"])
        service_result = preview_result["preview_result"]
        self.assertTrue(service_result["ok"])
        self.assertIn("approval_result", service_result)
        self.assertIn("candidate_result", service_result)
        self.assertIn("queue_pending_result", service_result)
        self.assertIn("queue_write_preview_result", service_result)
        self.assertTrue(service_result["approval_result"]["approved"])
        self.assertTrue(service_result["candidate_result"]["candidate"])
        self.assertTrue(service_result["queue_pending_result"]["queue_pending"])

        queue_write_preview = service_result["queue_write_preview_result"]
        self.assertTrue(queue_write_preview["write_preview"])
        self.assertEqual(
            "ORDER_QUEUED",
            queue_write_preview["order_queued_record_preview"]["status"],
        )
        self.assertTrue(queue_write_preview["preview_only"])
        self.assertTrue(queue_write_preview["no_write"])

        send_order_stub.assert_not_called()
        queue_write_commit.assert_not_called()
        runtime_hash_after = hashlib.sha256(Path("runtime/order_queue.json").read_bytes()).hexdigest().upper()
        self.assertEqual(runtime_hash_before, runtime_hash_after)
        self.assertEqual("REAL_READY", self._single_order()["status"])


if __name__ == "__main__":
    unittest.main()
