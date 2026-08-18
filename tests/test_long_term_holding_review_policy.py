# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_review_required_window as review_window
import gui_operation_environment as environment
from gui_auto_trade_policy import auto_trade_setting_liquidation_result_policy
from stock_long_hold_policy import long_hold_excludes_holding_review


class LongTermHoldingReviewPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _state(self, **updates: object) -> dict[str, object]:
        value: dict[str, object] = {
            "status": "REVIEW_REQUIRED",
            "review_required": True,
            "review_status": "PENDING",
            "review_reason": "재시작 시 보유수량 존재",
            "holding_qty": 10,
            "avg_price": 1000,
        }
        value.update(updates)
        return value

    def _excluded(
        self,
        state: dict[str, object],
        *,
        enabled: bool = True,
        holding_qty: int = 10,
        buy_pending_qty: object = 0,
        sell_pending_qty: object = 0,
        safety_issue: bool = False,
    ) -> bool:
        return long_hold_excludes_holding_review(
            enabled,
            state,
            holding_qty=holding_qty,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
            safety_issue=safety_issue,
        )

    def test_close_carryover_long_hold_on_excludes_review(self) -> None:
        self.assertTrue(self._excluded(self._state(early_close_method="이월")))

    def test_close_carryover_long_hold_off_remains_review(self) -> None:
        self.assertFalse(self._excluded(self._state(early_close_method="이월"), enabled=False))

    def test_latest_individual_market_always_remains_review(self) -> None:
        state = self._state(
            early_close_method="이월",
            individual_liquidation_request={"status": "REQUESTED", "method": "시장가"},
        )
        self.assertFalse(self._excluded(state))

    def test_latest_individual_current_price_always_remains_review(self) -> None:
        state = self._state(
            auto_close_method="이월",
            individual_liquidation_request={"status": "REQUESTED", "method": "현재가"},
        )
        self.assertFalse(self._excluded(state))

    def test_latest_individual_carryover_can_be_excluded(self) -> None:
        state = self._state(
            early_close_method="이월",
            individual_liquidation_request={"status": "REQUESTED", "method": "이월"},
        )
        self.assertTrue(self._excluded(state))

    def test_invalid_active_individual_request_fails_closed(self) -> None:
        state = self._state(
            early_close_method="이월",
            individual_liquidation_request={"status": "REQUESTED", "method": ""},
        )
        self.assertFalse(self._excluded(state))

    def test_manual_holding_without_close_can_be_excluded(self) -> None:
        self.assertTrue(self._excluded(self._state()))

    def test_manual_holding_without_close_and_long_hold_off_remains_review(self) -> None:
        self.assertFalse(self._excluded(self._state(), enabled=False))

    def test_safety_and_pending_issues_never_excluded(self) -> None:
        self.assertFalse(self._excluded(self._state(), safety_issue=True))
        self.assertFalse(self._excluded(self._state(), buy_pending_qty=1))
        self.assertFalse(self._excluded(self._state(), sell_pending_qty="?"))
        self.assertFalse(
            self._excluded(self._state(recovery_status="FAILED"))
        )

    def test_holding_zero_has_no_holding_review_exclusion(self) -> None:
        self.assertFalse(self._excluded(self._state(holding_qty=0), holding_qty=0))

    def test_active_liquidation_residual_is_not_normal_carryover(self) -> None:
        for method in ("시장가", "현재가"):
            with self.subTest(method=method):
                result = auto_trade_setting_liquidation_result_policy(
                    {},
                    {
                        "liquidation_completed_at": datetime.now().astimezone().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "individual_liquidation_request": {
                            "status": "REQUESTED",
                            "method": method,
                        },
                    },
                    3,
                    0,
                    0,
                )
                self.assertEqual("RED_STOP", result)

    def test_collector_applies_policy_classification_not_row_hiding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "111111_Test"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps({"long_term_holding_enabled": True}), encoding="utf-8"
            )
            (stock_dir / "state.json").write_text(
                json.dumps(self._state()), encoding="utf-8"
            )
            (stock_dir / "orders.json").write_text(
                json.dumps({"orders": []}), encoding="utf-8"
            )

            class Repo:
                def list_stocks(self):
                    return [SimpleNamespace(code="111111", name="Test", routine="Routine")]

                def resolve_stock_dir(self, code, name):
                    return stock_dir

            with patch.object(review_window, "stock_repository_factory", return_value=Repo()):
                with patch.object(
                    review_window,
                    "read_review_policy",
                    return_value={"long_term_holding_enabled": True},
                ):
                    self.assertEqual([], review_window.collect_global_review_required_rows())
                with patch.object(
                    review_window,
                    "read_review_policy",
                    return_value={"long_term_holding_enabled": False},
                ):
                    self.assertEqual(1, len(review_window.collect_global_review_required_rows()))

    def test_legacy_stock_config_value_never_controls_global_policy(self) -> None:
        for legacy_value in (True, False):
            with self.subTest(legacy_value=legacy_value), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                stock_dir = root / "111111_Test"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps({"long_term_holding_enabled": legacy_value}),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps(self._state()), encoding="utf-8"
                )
                (stock_dir / "orders.json").write_text(
                    json.dumps({"orders": []}), encoding="utf-8"
                )

                class Repo:
                    def list_stocks(self):
                        return [SimpleNamespace(code="111111", name="Test", routine="Routine")]

                    def resolve_stock_dir(self, code, name):
                        return stock_dir

                with (
                    patch.object(review_window, "stock_repository_factory", return_value=Repo()),
                    patch.object(
                        review_window,
                        "read_review_policy",
                        return_value={"long_term_holding_enabled": False},
                    ),
                ):
                    self.assertEqual(1, len(review_window.collect_global_review_required_rows()))

    def test_global_policy_writer_defaults_false_persists_and_is_observer_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            self.assertFalse(
                environment.read_review_policy(path=policy_path)[
                    "long_term_holding_enabled"
                ]
            )
            with patch.object(
                environment,
                "append_production_event",
                side_effect=RuntimeError("observer down"),
            ):
                saved = environment.write_long_term_holding_policy(
                    True,
                    path=policy_path,
                )
            self.assertTrue(saved["long_term_holding_enabled"])
            persisted = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {"long_term_holding_enabled": True},
                persisted["review_policy"],
            )
            with patch.object(environment, "append_production_event") as event_writer:
                environment.write_long_term_holding_policy(
                    False,
                    path=policy_path,
                )
                environment.write_long_term_holding_policy(
                    False,
                    path=policy_path,
                )
            event_writer.assert_called_once()
            self.assertEqual("SETTING_CHANGED", event_writer.call_args.args[0])

    def test_global_badge_toggles_without_selection_and_recalculates_collector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            policy_path.write_text(
                json.dumps(environment.default_operation_policy()),
                encoding="utf-8",
            )
            with (
                patch.object(environment, "OPERATION_POLICY_PATH", policy_path),
                patch.object(
                    review_window,
                    "collect_global_review_required_rows",
                    return_value=[],
                ) as collector,
                patch.object(review_window, "show_toast") as toast,
                patch.object(environment, "append_production_event"),
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.assertFalse(hasattr(window, "btn_long_hold_on"))
                self.assertFalse(hasattr(window, "btn_long_hold_off"))
                window.show()
                self.app.processEvents()
                standard_button_height = window.btn_return.height()
                self.assertEqual(standard_button_height, window.long_hold_toggle_button.height())
                self.assertEqual(standard_button_height, window.btn_unassign.height())
                self.assertEqual(standard_button_height, window.btn_delete.height())
                self.assertEqual(standard_button_height, window.btn_close.height())
                standard_button_center_y = window.btn_return.geometry().center().y()
                self.assertEqual(
                    standard_button_center_y,
                    window.long_hold_toggle_button.geometry().center().y(),
                )
                for row_count in (0, 1, 3):
                    window.table.setRowCount(row_count)
                    window.resize(window.width() + 1, window.height())
                    self.app.processEvents()
                    self.assertEqual(
                        window.btn_return.height(),
                        window.long_hold_toggle_button.height(),
                    )
                    self.assertEqual(
                        window.btn_return.geometry().center().y(),
                        window.long_hold_toggle_button.geometry().center().y(),
                    )
                badge_size = window.long_hold_toggle_button.size()
                self.assertEqual("장기보유 OFF", window.long_hold_toggle_button.text())

                window.long_hold_toggle_button.click()
                self.assertEqual("장기보유 ON", window.long_hold_toggle_button.text())
                self.assertTrue(environment.read_review_policy()["long_term_holding_enabled"])
                self.assertIn(
                    review_window.LONG_HOLD_BADGE_ACTIVE_COLOR,
                    window.long_hold_toggle_button.styleSheet(),
                )
                self.assertIn(
                    "margin: 1px 0",
                    window.long_hold_toggle_button.styleSheet(),
                )
                self.assertEqual(badge_size, window.long_hold_toggle_button.size())

                window.long_hold_toggle_button.click()
                self.assertEqual("장기보유 OFF", window.long_hold_toggle_button.text())
                self.assertFalse(environment.read_review_policy()["long_term_holding_enabled"])
                self.assertEqual(window.btn_return.height(), window.long_hold_toggle_button.height())
                self.assertGreaterEqual(collector.call_count, 3)
                self.assertFalse(
                    any("종목을 선택" in str(call) for call in toast.call_args_list)
                )
                window.close()

                reopened_window = review_window.GlobalReviewRequiredWindow()
                reopened_window.show()
                self.app.processEvents()
                self.assertEqual(
                    reopened_window.btn_return.height(),
                    reopened_window.long_hold_toggle_button.height(),
                )
                self.assertEqual(
                    reopened_window.btn_return.geometry().center().y(),
                    reopened_window.long_hold_toggle_button.geometry().center().y(),
                )
                reopened_window.close()

    def test_global_badge_save_failure_keeps_read_back_state(self) -> None:
        with (
            patch.object(
                review_window,
                "collect_global_review_required_rows",
                return_value=[],
            ),
            patch.object(
                review_window,
                "read_review_policy",
                return_value={"long_term_holding_enabled": False},
            ),
            patch.object(
                review_window,
                "write_long_term_holding_policy",
                side_effect=OSError("write failed"),
            ),
            patch.object(review_window, "show_toast") as toast,
        ):
            window = review_window.GlobalReviewRequiredWindow()
            window.long_hold_toggle_button.click()
            self.assertEqual("장기보유 OFF", window.long_hold_toggle_button.text())
            toast.assert_called_once_with(window, "장기보유 설정 저장에 실패했습니다.")
            window.close()


if __name__ == "__main__":
    unittest.main()
