from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from mock_validation_host import MockValidationHost
from mock_validation_ui_actions import MockValidationUIActions
from tests.test_mock_validation_host_ui import NOW, _Api, _reference
from tests.test_mock_validation_market_data import _book, _trade_payload


class MockAtsImmediateCleanupBoundaryTest(unittest.TestCase):
    def test_extra2_current_price_immediate_liquidation_bypasses_regular_cleanup(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project_root = Path(temporary.name) / "project"
        project_root.mkdir()

        at = NOW.replace(hour=16, minute=0)
        clock = {"now": at}
        host = MockValidationHost(
            _Api(),
            project_root=project_root,
            now_factory=lambda: clock["now"],
            projection_changed=Mock(),
            operation_policy_provider=lambda: {
                "regular_market": {
                    "start_time": "09:00:00",
                    "end_time": "15:20:00",
                },
                "extra_sessions": [
                    {
                        "enabled": True,
                        "start_time": "08:00:00",
                        "end_time": "08:50:00",
                    },
                    {
                        "enabled": True,
                        "start_time": "15:40:00",
                        "end_time": "19:50:00",
                    },
                ],
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
            },
            candles_provider=lambda **_kwargs: {
                "available": False,
                "candles": [],
                "availability_state": "SOURCE_UNAVAILABLE",
            },
        )
        self.addCleanup(host.dispose)
        actions = MockValidationUIActions(host)

        created = actions.create_waiting_session(
            _reference("005380", ("A",), stock_name="현대차")
        )["document"]
        session_id = created["session"]["validation_session_id"]
        actions.set_instance_effective_settings(
            "005380",
            "A",
            operation_mode="CONTINUOUS",
            manual_ats={"selected_sessions": ["extra2"]},
        )
        host.start_instance_operation("005380", "A", as_of=at)
        host.session_service.set_instance_position(
            session_id,
            "A",
            holding_qty=2,
            available_qty=2,
            average_price=90,
            realized_cost_basis=180,
            command_id="MC-ATS-CURRENT-POSITION",
        )

        self.assertTrue(
            host.accept_orderbook(
                replace(
                    _book(sequence=1),
                    stock_code="005380",
                    received_at=at.isoformat(),
                )
            )
        )
        trade = _trade_payload(sequence=1)
        trade.update(
            {
                "stock_code": "005380",
                "execution_time_raw": at.strftime("%H%M%S"),
                "market_datetime": at.isoformat(),
                "received_at": at.isoformat(),
            }
        )
        self.assertTrue(host.accept_trade(trade))
        clock["now"] = at

        requested = actions.manual_ats_liquidation_instance(
            "005380",
            "A",
            method="현재가",
        )["document"]
        operation = requested["mock_operation_lifecycle"]["instance_operations"]["A"]
        self.assertEqual(("CLOSING", "IMMEDIATE", "CURRENT_PRICE"), (
            operation["state"],
            operation["close_source"],
            operation["close_method"],
        ))

        host.process_due_cycles(as_of=at + timedelta(milliseconds=100))
        progressed = host.current_session("005380")
        self.assertEqual(1, len(progressed["orders"]))
        order = progressed["orders"][0]
        self.assertEqual(("SELL", "LIMIT", 100), (
            order["side"],
            order["order_type"],
            order["requested_price"],
        ))

        host.process_due_cycles(as_of=at + timedelta(seconds=1))
        completed = host.current_session("005380")
        position = next(
            item
            for item in completed["positions"]
            if item["routine_instance_id"] == "A"
        )
        self.assertEqual(0, position["holding_qty"])
        self.assertEqual("ENDED", completed["instance_execution"]["A"]["state"])


if __name__ == "__main__":
    unittest.main()
