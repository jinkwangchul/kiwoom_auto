import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import gui_auto_trade_context_menu as context_menu
from gui_user_reason import user_reason_message, user_reason_messages
from tests.participant_owner_fixture import participant_owner


class ContextMenuAvailabilityNormalizationTests(unittest.TestCase):
    def test_user_reason_mapping_hides_internal_vocabulary(self) -> None:
        messages = (
            user_reason_message("RECOVERY_STOCK_PENDING"),
            user_reason_message("CURRENTLY_RUNNING"),
            user_reason_message("HAS_HOLDING"),
            user_reason_message("HAS_PENDING_ORDER"),
            user_reason_message("CURRENT_PRICE_UNAVAILABLE"),
            *user_reason_messages(
                ["RECOVERY_CONTEXT_MISSING: internal detail", "REVIEW_REQUIRED"]
            ),
        )
        forbidden = (
            "registry",
            "canonical",
            "recovery",
            "instance",
            "repository",
            "writer",
            "mutation",
            "participant",
            "current-session",
            "authority",
            "sot",
            "runtime",
        )
        rendered = " ".join(messages).lower()
        self.assertFalse(any(term in rendered for term in forbidden), rendered)

    def _target(self, root: str, *, review: bool):
        stock_dir = Path(root) / "stocks" / "111111_Test"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps({"operation_excluded": False}),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "REVIEW_REQUIRED" if review else "STOPPED",
                    "review_required": review,
                }
            ),
            encoding="utf-8",
        )
        return stock_dir, "111111", "Test"

    @staticmethod
    def _callbacks(*, trade_allowed: bool):
        return context_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
        )

    def test_main_and_settings_inputs_share_review_projection(self) -> None:
        with TemporaryDirectory() as temp:
            target = self._target(temp, review=True)
            paths = (Path(target[0]) / "config.json", Path(target[0]) / "state.json")
            before = {path.name: path.read_bytes() for path in paths}
            main_runtime = {"owner": "main"}
            settings_runtime = {"owner": "settings"}
            operation_host = participant_owner()
            decision = SimpleNamespace(allowed=False, reason_code="REVIEW_REQUIRED")

            with patch.object(
                context_menu,
                "inspect_auto_trade_operation_exclusion_availability",
                return_value=decision,
            ):
                main_result = context_menu.inspect_stock_context_menu_availability(
                    SimpleNamespace(
                        runtime_state=main_runtime,
                        _main_monitoring_auto_trade_operation_host=operation_host,
                    ),
                    has_selection=True,
                    callbacks=self._callbacks(trade_allowed=False),
                    selected_targets=[target],
                    operation_excluded=False,
                    operation_exclusion_action="set",
                    stock_register_enabled=True,
                    scheduled_excluded_management=False,
                )
                settings_result = context_menu.inspect_stock_context_menu_availability(
                    SimpleNamespace(
                        runtime_state=settings_runtime,
                        _main_monitoring_auto_trade_operation_host=operation_host,
                    ),
                    has_selection=True,
                    callbacks=self._callbacks(trade_allowed=False),
                    selected_targets=[target],
                    operation_excluded=False,
                    operation_exclusion_action="set",
                    stock_register_enabled=True,
                    scheduled_excluded_management=False,
                )

            self.assertEqual(main_result, settings_result)
            self.assertTrue(main_result.review_managed)
            self.assertFalse(main_result.start_allowed)
            self.assertFalse(main_result.exclusion_allowed)
            self.assertFalse(main_result.unregister_allowed)
            self.assertTrue(main_result.time_management_allowed)
            self.assertTrue(main_result.ats_settings_allowed)
            self.assertEqual(
                before,
                {path.name: path.read_bytes() for path in paths},
            )
            self.assertEqual(main_runtime, {"owner": "main"})
            self.assertEqual(settings_runtime, {"owner": "settings"})

    def test_backend_availability_reasons_flow_into_ui_projection(self) -> None:
        with TemporaryDirectory() as temp:
            target = self._target(temp, review=False)
            callbacks = self._callbacks(trade_allowed=False)
            decision = SimpleNamespace(
                allowed=False,
                reason_code="CURRENTLY_RUNNING",
            )

            with patch.object(
                context_menu,
                "inspect_auto_trade_operation_exclusion_availability",
                return_value=decision,
            ) as exclusion_inspector:
                result = context_menu.inspect_stock_context_menu_availability(
                    SimpleNamespace(),
                    has_selection=True,
                    callbacks=callbacks,
                    selected_targets=[target],
                    operation_excluded=False,
                    operation_exclusion_action="set",
                    stock_register_enabled=True,
                    scheduled_excluded_management=False,
                )

            self.assertFalse(result.review_managed)
            self.assertFalse(result.exclusion_allowed)
            self.assertEqual(result.reason_for("exclusion"), "CURRENTLY_RUNNING")
            exclusion_inspector.assert_called_once_with(
                ANY,
                target,
                True,
            )

    def test_unregister_backend_availability_flows_into_ui_without_mutation(self) -> None:
        with TemporaryDirectory() as temp:
            target = self._target(temp, review=False)
            state_path = Path(target[0]) / "state.json"
            before = state_path.read_bytes()
            runtime_state = {"participant": "unchanged"}
            callbacks = replace(
                self._callbacks(trade_allowed=True),
                unregister=Mock(),
                unregister_available=Mock(return_value=False),
            )

            with patch.object(
                context_menu,
                "inspect_auto_trade_operation_exclusion_availability",
                return_value=SimpleNamespace(allowed=True, reason_code="ALLOWED"),
            ):
                result = context_menu.inspect_stock_context_menu_availability(
                    SimpleNamespace(runtime_state=runtime_state),
                    has_selection=True,
                    callbacks=callbacks,
                    selected_targets=[target],
                    operation_excluded=False,
                    operation_exclusion_action="set",
                    stock_register_enabled=True,
                    scheduled_excluded_management=False,
                )

            self.assertFalse(result.unregister_allowed)
            self.assertEqual(
                "UNREGISTER_UNAVAILABLE",
                result.reason_for("unregister"),
            )
            callbacks.unregister_available.assert_called_once_with()
            self.assertEqual(before, state_path.read_bytes())
            self.assertEqual({"participant": "unchanged"}, runtime_state)


if __name__ == "__main__":
    unittest.main()
