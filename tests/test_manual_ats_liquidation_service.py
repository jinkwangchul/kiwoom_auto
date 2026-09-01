from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from manual_ats_liquidation_service import (
    METHOD_CURRENT_PRICE,
    METHOD_MARKET,
    build_manual_ats_liquidation_preview,
    commit_manual_ats_liquidation_preview,
    ensure_manual_ats_liquidation_request,
)
from operation_command_service import MANUAL_ATS_LIQUIDATION_REQUEST_KEY
from operation_command_service import StockOperationCommandResult
from order_hoga_mapper import map_order_hoga_preview


TEST_PROGRAM_SESSION_ID = "test-program-session"


class ManualAtsLiquidationServiceTest(unittest.TestCase):
    @staticmethod
    def _stock(
        root: Path,
        *,
        mode: str = "CONTINUOUS",
        holding_qty: int = 7,
        trade_date: str = "2026-07-25",
        program_session_id: str | None = None,
    ) -> Path:
        stock = root / "stocks" / "005930_삼성전자"
        stock.mkdir(parents=True)
        (stock / "config.json").write_text(
            json.dumps(
                {
                    "operation_mode": mode,
                    "assigned_routine_instance_id": "instance-1",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (stock / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                    "signal_probe_only": False,
                    "review_required": False,
                    "holding_qty": holding_qty,
                    "operation_sequence": 0,
                    "manual_ats_selection": {
                        "selected_sessions": ["extra1"],
                        "trade_date": trade_date,
                        "program_session_id": (
                            program_session_id or TEST_PROGRAM_SESSION_ID
                        ),
                        "source": "ATS_SETTINGS",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return stock

    @staticmethod
    def _session(_key: str) -> dict[str, object]:
        return {"name": "장전프리", "start_time": "08:00:00", "end_time": "09:00:00"}

    def test_market_preview_uses_actual_holding_and_zero_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            current_price_reader = MagicMock(
                side_effect=AssertionError("MARKET must not read price")
            )
            with patch(
                "manual_ats_liquidation_service.manual_ats_session_definition",
                side_effect=self._session,
            ):
                preview = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "시장가",
                    now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                    command_id="ats-market-1",
                    current_price_reader=current_price_reader,
                )

        self.assertTrue(preview["ok"])
        self.assertEqual(METHOD_MARKET, preview["sell_method"])
        self.assertEqual(7, preview["holding_qty"])
        self.assertEqual(0, preview["price"])
        self.assertEqual("SELL", preview["order_candidate"]["side"])
        self.assertEqual("MARKET", preview["order_candidate"]["order_intent"]["hoga"])
        self.assertEqual("MARKET", preview["order_candidate"]["price_basis"])
        current_price_reader.assert_not_called()

    def test_current_price_preview_uses_limit_hoga_and_current_price(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            with patch(
                "manual_ats_liquidation_service.manual_ats_session_definition",
                side_effect=self._session,
            ):
                preview = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "현재가",
                    now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                    current_price_reader=lambda _code, _name: 72500,
                )

        self.assertTrue(preview["ok"])
        self.assertEqual(METHOD_CURRENT_PRICE, preview["sell_method"])
        self.assertEqual(72500, preview["price"])
        self.assertEqual("CURRENT_PRICE", preview["order_candidate"]["order_intent"]["hoga"])
        self.assertEqual("CURRENT_PRICE", preview["order_candidate"]["price_basis"])
        mapped = map_order_hoga_preview(preview["order_candidate"])
        self.assertEqual("LIMIT", mapped["hoga"])
        self.assertFalse(mapped["unresolved"])

    def test_current_price_preview_requires_actionable_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp))
            with patch(
                "manual_ats_liquidation_service.manual_ats_session_definition",
                side_effect=self._session,
            ):
                preview = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "현재가",
                    now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                )

        self.assertFalse(preview["ok"])
        self.assertIn(
            "current-price liquidation requires an actionable current price",
            preview["blocked_reasons"],
        )

    def test_reconciled_holding_override_replaces_stale_state_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp), holding_qty=100)
            with patch(
                "manual_ats_liquidation_service.manual_ats_session_definition",
                side_effect=self._session,
            ):
                market = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "시장가",
                    now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                    holding_qty_override=70,
                    current_price_reader=lambda _code, _name: 72500,
                )
                current = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "현재가",
                    now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                    holding_qty_override=110,
                    current_price_reader=lambda _code, _name: 72500,
                )

        self.assertEqual(70, market["order_candidate"]["quantity"])
        self.assertEqual(0, market["order_candidate"]["price"])
        self.assertEqual("MARKET", market["order_candidate"]["hoga"])
        self.assertEqual(110, current["order_candidate"]["quantity"])
        self.assertEqual(72500, current["order_candidate"]["price"])
        self.assertEqual("CURRENT_PRICE", current["order_candidate"]["hoga"])
        self.assertEqual(
            "positions_broker_reconciliation",
            market["order_candidate"]["holding_source"],
        )

    def test_preview_is_fail_closed_outside_ats_or_without_real_holding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = self._stock(Path(temp), holding_qty=0)
            with patch(
                "manual_ats_liquidation_service.manual_ats_session_definition",
                side_effect=self._session,
            ):
                preview = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "시장가",
                    now_dt=datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
                    current_price_reader=lambda _code, _name: 72500,
                )

        self.assertFalse(preview["ok"])
        self.assertIn(
            "current time is outside the selected ATS sessions",
            preview["blocked_reasons"],
        )
        self.assertIn(
            "actual holding quantity is missing or zero",
            preview["blocked_reasons"],
        )

    def test_preview_accepts_persisted_selection_from_previous_day_or_program(self) -> None:
        for trade_date, session_id in (
            ("2026-07-24", TEST_PROGRAM_SESSION_ID),
            ("2026-07-25", "previous-program"),
        ):
            with self.subTest(trade_date=trade_date, session_id=session_id):
                with tempfile.TemporaryDirectory() as temp:
                    stock = self._stock(
                        Path(temp),
                        trade_date=trade_date,
                        program_session_id=session_id,
                    )
                    with patch(
                        "manual_ats_liquidation_service.manual_ats_session_definition",
                        side_effect=self._session,
                    ):
                        preview = build_manual_ats_liquidation_preview(
                            stock,
                            "005930",
                            "삼성전자",
                            ["extra1"],
                            "시장가",
                            now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                            current_price_reader=lambda _code, _name: 72500,
                        )
                self.assertTrue(preview["ok"])
                self.assertEqual(trade_date, preview["trade_date"])
                self.assertEqual(session_id, preview["program_session_id"])

    def test_commit_records_command_then_uses_existing_approval_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock = self._stock(root)
            with patch(
                "manual_ats_liquidation_service.manual_ats_session_definition",
                side_effect=self._session,
            ):
                preview = build_manual_ats_liquidation_preview(
                    stock,
                    "005930",
                    "삼성전자",
                    ["extra1"],
                    "시장가",
                    now_dt=datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
                    command_id="ats-commit-1",
                    current_price_reader=lambda _code, _name: 72500,
                )

            appender = MagicMock(
                return_value={"ok": True, "orders_created": 1, "created_orders": [preview["order_candidate"]]}
            )
            policy = MagicMock(
                return_value={"ok": True, "after_status": "EXECUTABLE", "reason": ""}
            )
            result = commit_manual_ats_liquidation_preview(
                preview,
                project_root=root,
                candidate_appender=appender,
                policy_applier=policy,
            )
            state = json.loads((stock / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        appender.assert_called_once()
        policy.assert_called_once_with("ATS_LIQUIDATION_ats-commit-1")
        committed_candidate = appender.call_args.args[0][0]
        self.assertEqual("APPROVED", committed_candidate["status"])
        self.assertEqual("APPROVED", committed_candidate["approval_status"])
        self.assertEqual("ATS_SETTINGS", committed_candidate["manual_ats_liquidation"]["source"])
        self.assertEqual(
            "2026-07-25",
            committed_candidate["manual_ats_liquidation"]["trade_date"],
        )
        self.assertEqual(
            TEST_PROGRAM_SESSION_ID,
            committed_candidate["manual_ats_liquidation"]["program_session_id"],
        )
        request = state[MANUAL_ATS_LIQUIDATION_REQUEST_KEY]
        self.assertEqual("ORDER_EXECUTABLE", request["status"])
        self.assertEqual("ats-commit-1", request["command_id"])
        self.assertEqual("2026-07-25", request["trade_date"])
        self.assertEqual(TEST_PROGRAM_SESSION_ID, request["program_session_id"])

    def test_runtime_status_readback_failure_blocks_send_order_entry(self) -> None:
        preview = {
            "ok": True,
            "command_id": "ats-readback-failure",
            "stock_dir": "C:/temp/005930",
            "requested_at": "2026-07-25T08:30:00+09:00",
            "sell_method": METHOD_MARKET,
            "selected_ats_sessions": ["extra1"],
            "order_candidate": {
                "id": "ATS_LIQUIDATION_ats-readback-failure",
                "status": "PENDING",
                "candidate_status": "CANDIDATE_READY",
                "side": "SELL",
                "quantity": 1,
                "order_intent": {"side": "SELL", "hoga": "MARKET"},
            },
        }
        command_service = MagicMock()
        command_service.apply_manual_ats_liquidation.return_value = MagicMock(
            status="SUCCESS",
            stock_results=[MagicMock(status="APPLIED")],
            error="",
        )
        command_service.record_manual_ats_liquidation_status.return_value = (
            StockOperationCommandResult(
                "005930",
                "C:/temp/005930",
                "FAILED",
                error="read-back verification failed",
            )
        )
        result = commit_manual_ats_liquidation_preview(
            preview,
            project_root="C:/temp",
            command_service_factory=lambda _root: command_service,
            candidate_appender=lambda *_args, **_kwargs: {
                "ok": True,
                "orders_created": 1,
            },
            approval_evaluator=lambda _candidate: {
                "approval_status": "APPROVED",
                "approval_reason": "",
            },
            policy_applier=lambda _order_id: {
                "ok": True,
                "after_status": "EXECUTABLE",
                "reason": "",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual("runtime_status_readback", result["stage"])
        self.assertIn("read-back verification failed", result["blocked_reasons"])

    def test_waiting_duplicate_request_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "005930_삼성전자"
            stock.mkdir()
            (stock / "state.json").write_text(
                json.dumps(
                    {
                        MANUAL_ATS_LIQUIDATION_REQUEST_KEY: {
                            "command_id": "ats-duplicate-waiting",
                            "status": "WAITING_CANCEL_CONFIRMATION",
                        }
                    }
                ),
                encoding="utf-8",
            )
            preview = {
                "ok": True,
                "command_id": "ats-new-click-while-waiting",
                "stock_dir": str(stock),
                "requested_at": "2026-08-09T16:00:00+09:00",
                "sell_method": "MARKET",
                "selected_ats_sessions": ["extra2"],
            }
            command_service = MagicMock()
            result = ensure_manual_ats_liquidation_request(
                preview,
                project_root=temp,
                command_service_factory=lambda _root: command_service,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("runtime_request", result["stage"])
        self.assertIn("already waiting", result["blocked_reasons"][0])
        command_service.apply_manual_ats_liquidation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
