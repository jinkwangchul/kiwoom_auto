from __future__ import annotations

from types import SimpleNamespace
import unittest

from mock_validation_context_menu import _individual_liquidation_menu_policy


class MockIndividualLiquidationMenuSourceTest(unittest.TestCase):
    def window(self, *, program_session_id: str = "PROGRAM-A") -> SimpleNamespace:
        return SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                _program_session_id=program_session_id,
                _operation_policy_provider=lambda: {
                    "liquidation": {
                        "method": "시장가",
                        "minutes_before_regular_close": "5",
                    }
                },
            )
        )

    @staticmethod
    def reservation(*, method: str, minutes: str) -> dict[str, object]:
        return {
            "version": 1,
            "routine_instance_id": "A",
            "reservation_scope": "NEXT_OPERATION",
            "method": method,
            "minutes_before_regular_close": minutes,
            "reserved_at": "2026-09-12T09:00:00+09:00",
            "command_id": "menu-source-test",
            "program_session_id": "PROGRAM-A",
        }

    def test_stopped_historical_snapshot_falls_back_to_global(self) -> None:
        document = {
            "mock_operation_lifecycle": {
                "instance_operations": {
                    "A": {
                        "state": "VALIDATION_STOPPED",
                        "individual_liquidation_time_snapshot": {
                            "method": "CURRENT_PRICE",
                            "minutes_before_regular_close": "10",
                        },
                    }
                }
            }
        }

        policy = _individual_liquidation_menu_policy(self.window(), document, "A")

        self.assertEqual("시장가", policy["liquidation"]["method"])
        self.assertEqual(
            "5", policy["liquidation"]["minutes_before_regular_close"]
        )

    def test_active_operation_snapshot_has_first_priority(self) -> None:
        document = {
            "mock_operation_lifecycle": {
                "instance_operations": {
                    "A": {
                        "state": "RUNNING",
                        "operation_policy_snapshot": {
                            "liquidation": {
                                "method": "시장가",
                                "minutes_before_regular_close": "5",
                            }
                        },
                        "individual_liquidation_time_snapshot": {
                            "method": "CURRENT_PRICE",
                            "minutes_before_regular_close": "10",
                        },
                    }
                }
            },
            "individual_liquidation_reservations_by_instance": {
                "A": self.reservation(method="CARRYOVER", minutes="")
            },
        }

        policy = _individual_liquidation_menu_policy(self.window(), document, "A")

        self.assertEqual("현재가", policy["liquidation"]["method"])
        self.assertEqual(
            "10", policy["liquidation"]["minutes_before_regular_close"]
        )

    def test_current_process_pending_overrides_global(self) -> None:
        document = {
            "individual_liquidation_reservations_by_instance": {
                "A": self.reservation(method="CARRYOVER", minutes="")
            }
        }

        policy = _individual_liquidation_menu_policy(self.window(), document, "A")

        self.assertEqual("이월", policy["liquidation"]["method"])
        self.assertEqual("", policy["liquidation"]["minutes_before_regular_close"])

    def test_current_pending_overrides_stopped_historical_snapshot(self) -> None:
        document = {
            "mock_operation_lifecycle": {
                "instance_operations": {
                    "A": {
                        "state": "VALIDATION_STOPPED",
                        "individual_liquidation_time_snapshot": {
                            "method": "MARKET",
                            "minutes_before_regular_close": "7",
                        },
                    }
                }
            },
            "individual_liquidation_reservations_by_instance": {
                "A": self.reservation(method="CURRENT_PRICE", minutes="10")
            },
        }

        policy = _individual_liquidation_menu_policy(self.window(), document, "A")

        self.assertEqual("현재가", policy["liquidation"]["method"])
        self.assertEqual(
            "10", policy["liquidation"]["minutes_before_regular_close"]
        )


if __name__ == "__main__":
    unittest.main()
