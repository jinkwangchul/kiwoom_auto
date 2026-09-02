# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

import gui_windows
import gui_main_table_loader
import routine_signal_probe
from running_budget_adjustment import (
    ADJUSTMENT_KEY,
    STATE_APPLIED,
    STATE_WAIT_FIRST_BUY,
    STATE_WAIT_SELL,
    commit_running_budget_adjustment,
    project_running_budget_adjustment_display_config,
    project_running_budget_adjustment_config,
    running_budget_adjustment_snapshot,
    transition_running_budget_adjustment_for_signal,
)
from runtime_io import read_json_dict
from tests.participant_owner_fixture import attach_participant_owner, participant_owner


class _UiPreferenceSettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.sync_count = 0

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def sync(self) -> None:
        self.sync_count += 1


class RunningBudgetAdjustmentContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.stock_dir = Path(self.temp.name) / "stocks" / "005930_삼성전자"
        self.stock_dir.mkdir(parents=True)
        self.config = {
            "trade_amount_type": "AMOUNT",
            "buy_amount": 100,
            "buy_qty": 7,
            "buy_limit_enabled": True,
            "buy_limit_amount": 10_000,
            "buy_limit_source": "MANUAL",
        }
        self.state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-28 09:00:00",
        }
        self._write(self.stock_dir / "config.json", self.config)
        self._write(self.stock_dir / "state.json", self.state)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write(path: Path, payload: dict[str, object]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _commit(
        self,
        *,
        policy: str,
        value: int = 200,
        apply_limit: bool = False,
        adjusted_limit_amount: int | None = None,
    ) -> dict[str, object]:
        return commit_running_budget_adjustment(
            self.stock_dir,
            stock_code="005930",
            expected_mode="AMOUNT",
            requested_value=value,
            apply_policy=policy,
            apply_limit=apply_limit,
            adjusted_limit_amount=adjusted_limit_amount,
            confirmed_at="2026-08-28 10:00:00",
        )

    def _project(self) -> tuple[dict[str, object], dict[str, object]]:
        return project_running_budget_adjustment_config(
            read_json_dict(self.stock_dir / "config.json"),
            read_json_dict(self.stock_dir / "state.json"),
        )

    def test_immediate_first_buy_uses_new_budget_and_stays_effective(self) -> None:
        result = self._commit(policy="IMMEDIATE")
        self.assertTrue(result["ok"], result)
        projected, evidence = self._project()
        self.assertEqual(200, projected["buy_amount"])
        self.assertTrue(evidence["applied"])

        sell = transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="SELL",
            signal_id="sell-1",
        )
        self.assertFalse(sell["changed"])
        buy = transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="BUY",
            signal_id="buy-1",
        )
        self.assertTrue(buy["changed"], buy)
        self.assertEqual(STATE_APPLIED, running_budget_adjustment_snapshot(self.stock_dir)["state"])
        self.assertEqual(200, self._project()[0]["buy_amount"])

    def test_checked_limit_without_evidence_keeps_budget_request_and_prior_limit(self) -> None:
        result = self._commit(
            policy="IMMEDIATE",
            apply_limit=True,
            adjusted_limit_amount=None,
        )
        self.assertTrue(result["ok"], result)
        adjustment = running_budget_adjustment_snapshot(self.stock_dir)
        self.assertTrue(adjustment["apply_limit"])
        self.assertIsNone(adjustment["adjusted_limit_amount"])

        projected, evidence = self._project()
        self.assertEqual(200, projected["buy_amount"])
        self.assertTrue(projected["buy_limit_enabled"])
        self.assertEqual(10_000, projected["buy_limit_amount"])
        self.assertFalse(evidence["limit_recalculated"])

    def test_checked_limit_never_enables_disabled_limit(self) -> None:
        config = read_json_dict(self.stock_dir / "config.json")
        config.update(
            {
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "buy_limit_source": None,
            }
        )
        self._write(self.stock_dir / "config.json", config)
        result = self._commit(
            policy="IMMEDIATE",
            apply_limit=True,
            adjusted_limit_amount=20_000,
        )
        self.assertTrue(result["ok"], result)

        projected, evidence = self._project()
        self.assertEqual(200, projected["buy_amount"])
        self.assertFalse(projected["buy_limit_enabled"])
        self.assertIsNone(projected["buy_limit_amount"])
        self.assertFalse(evidence["limit_recalculated"])

    def test_next_cycle_buy_before_sell_uses_old_then_sell_buy_uses_new(self) -> None:
        result = self._commit(policy="NEXT_CYCLE")
        self.assertTrue(result["ok"], result)
        self.assertEqual(STATE_WAIT_SELL, running_budget_adjustment_snapshot(self.stock_dir)["state"])
        self.assertEqual(100, self._project()[0]["buy_amount"])

        early_buy = transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="BUY",
            signal_id="buy-old-budget",
        )
        self.assertFalse(early_buy["changed"])
        self.assertEqual(100, self._project()[0]["buy_amount"])

        sell = transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="SELL",
            signal_id="sell-boundary",
        )
        self.assertTrue(sell["changed"], sell)
        self.assertEqual(STATE_WAIT_FIRST_BUY, running_budget_adjustment_snapshot(self.stock_dir)["state"])
        self.assertEqual(200, self._project()[0]["buy_amount"])

        buy = transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="BUY",
            signal_id="buy-new-budget",
        )
        self.assertTrue(buy["changed"], buy)
        self.assertEqual(STATE_APPLIED, running_budget_adjustment_snapshot(self.stock_dir)["state"])

    def test_multiple_sell_and_holding_values_do_not_change_semantics(self) -> None:
        for holding_qty in (0, 99):
            with self.subTest(holding_qty=holding_qty):
                state = read_json_dict(self.stock_dir / "state.json")
                state["holding_qty"] = holding_qty
                self._write(self.stock_dir / "state.json", state)
                self.assertTrue(self._commit(policy="NEXT_CYCLE")["ok"])
                first = transition_running_budget_adjustment_for_signal(
                    self.stock_dir,
                    signal="SELL",
                    signal_id=f"sell-first-{holding_qty}",
                )
                second = transition_running_budget_adjustment_for_signal(
                    self.stock_dir,
                    signal="SELL",
                    signal_id=f"sell-second-{holding_qty}",
                )
                self.assertTrue(first["changed"])
                self.assertFalse(second["changed"])
                self.assertEqual(200, self._project()[0]["buy_amount"])

    def test_base_config_and_existing_candidate_are_never_rewritten(self) -> None:
        candidate = {"side": "BUY", "budget": 100, "quantity": 3}
        before_candidate = dict(candidate)
        before_config = read_json_dict(self.stock_dir / "config.json")
        self.assertTrue(self._commit(policy="IMMEDIATE")["ok"])
        transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="BUY",
            signal_id="new-buy",
        )
        self.assertEqual(before_config, read_json_dict(self.stock_dir / "config.json"))
        self.assertEqual(before_candidate, candidate)

    def test_mode_change_and_stale_operation_session_fail_closed(self) -> None:
        blocked = commit_running_budget_adjustment(
            self.stock_dir,
            stock_code="005930",
            expected_mode="QUANTITY",
            requested_value=8,
            apply_policy="IMMEDIATE",
            apply_limit=False,
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual("MODE_CHANGED", blocked["reason"])

        self.assertTrue(self._commit(policy="IMMEDIATE")["ok"])
        stale_state = read_json_dict(self.stock_dir / "state.json")
        stale_state["trade_started_at"] = "2026-08-28 11:00:00"
        self._write(self.stock_dir / "state.json", stale_state)
        projected, evidence = self._project()
        self.assertEqual(100, projected["buy_amount"])
        self.assertFalse(evidence["active"])

    def test_limit_option_projects_existing_recommended_limit_contract(self) -> None:
        self.assertTrue(
            self._commit(
                policy="IMMEDIATE",
                apply_limit=True,
                adjusted_limit_amount=20_000,
            )["ok"]
        )
        persisted = read_json_dict(self.stock_dir / "config.json")
        persisted["buy_limit_amount"] = 20_000
        persisted["buy_limit_source"] = "RECOMMENDED"
        self._write(self.stock_dir / "config.json", persisted)
        projected, evidence = self._project()
        self.assertTrue(projected["buy_limit_enabled"])
        self.assertEqual(20_000, projected["buy_limit_amount"])
        self.assertEqual("RECOMMENDED", projected["buy_limit_source"])
        self.assertTrue(evidence["apply_limit"])
        self.assertEqual(20_000, read_json_dict(self.stock_dir / "config.json")["buy_limit_amount"])

    def test_persisted_pending_request_is_restored_from_existing_state_owner(self) -> None:
        self.assertTrue(self._commit(policy="NEXT_CYCLE")["ok"])
        saved_state = read_json_dict(self.stock_dir / "state.json")
        self.assertIn(ADJUSTMENT_KEY, saved_state)
        projected, evidence = project_running_budget_adjustment_config(
            read_json_dict(self.stock_dir / "config.json"),
            saved_state,
        )
        self.assertEqual(100, projected["buy_amount"])
        self.assertEqual("WAITING_FOR_SELL", evidence["reason"])

    def test_generic_probe_uses_same_contract_for_different_names(self) -> None:
        observed: list[tuple[str, int]] = []

        def evaluate(context):
            observed.append((str(context["routine"]), int(context["stock_config"]["buy_amount"])))
            return {"signal": "BUY", "reason": "fixture"}

        module = SimpleNamespace(ROUTINE_TYPE="FIXTURE", evaluate=evaluate)
        queue_ids = iter(("signal-a", "signal-b"))

        def enqueue(*_args, **_kwargs):
            return {"status": "queued", "id": next(queue_ids)}

        with (
            patch.object(routine_signal_probe, "enqueue_routine_signal", side_effect=enqueue),
            patch.object(routine_signal_probe, "_append_log"),
            patch.object(routine_signal_probe, "_load_candles_from_stock_dir", return_value=[]),
            patch.object(routine_signal_probe, "_load_instance_rules", return_value={}),
        ):
            for name in ("fixture-alpha", "fixture-renamed"):
                self.assertTrue(self._commit(policy="IMMEDIATE")["ok"])
                result = routine_signal_probe.probe_routine_for_stock(
                    module,
                    name,
                    self.stock_dir,
                    "2026-08-28 12:00:00",
                    decision_trace_observer=None,
                )
                self.assertEqual("queued", result["queue_status"])
                self.assertEqual(STATE_APPLIED, running_budget_adjustment_snapshot(self.stock_dir)["state"])

        self.assertEqual(
            [("fixture-alpha", 200), ("fixture-renamed", 200)],
            observed,
        )

    def _dialog_host(self, *, settings=None):
        configuration_state = SimpleNamespace(
            connection_epoch=1,
            login_session_id="SESSION-1",
            last_price=70_000,
            field_sources=(("last_price", "SNAPSHOT"),),
        )
        operation_host = SimpleNamespace(
            configuration_market_information_state=lambda _code: configuration_state,
            fresh_monitoring_market_information_state=lambda _code: None,
        )
        role_values = {
            gui_windows.ROUTINE_STOCK_CODE_ROLE: "005930",
            gui_windows.ROUTINE_STOCK_NAME_ROLE: "삼성전자",
            gui_windows.ROUTINE_STOCK_TOOLTIP_DATA_ROLE: {"current_price": 70000},
        }
        item = SimpleNamespace(data=lambda role: role_values.get(role))
        host = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
            routine_table=SimpleNamespace(item=lambda _row, _column: item),
            _running_budget_adjustment_dialog=None,
            load_routine_table=MagicMock(),
            _stock_projection_for_config_path=lambda _path: {
                "stock_path": str(self.stock_dir),
                "code": "005930",
                "name": "삼성전자",
            },
            main_monitoring_auto_trade_operation_host=lambda: operation_host,
            parent=lambda: None,
            _account_memo_settings=settings,
        )
        host._write_stock_initial_buy_config = MethodType(
            gui_windows.MainWindow._write_stock_initial_buy_config,
            host,
        )
        host._adjusted_buy_limit_for_start_budget = MagicMock(return_value=20_000)
        return host

    def test_apply_limit_preference_round_trips_through_existing_qsettings(self) -> None:
        settings = _UiPreferenceSettings()
        host = self._dialog_host(settings=settings)

        self.assertFalse(gui_windows.MainWindow.start_budget_apply_limit_checked(host))
        gui_windows.MainWindow.set_start_budget_apply_limit_checked(host, True)
        self.assertTrue(gui_windows.MainWindow.start_budget_apply_limit_checked(host))
        gui_windows.MainWindow.set_start_budget_apply_limit_checked(host, False)
        self.assertFalse(gui_windows.MainWindow.start_budget_apply_limit_checked(host))
        self.assertEqual(
            False,
            settings.values[gui_windows.START_BUDGET_APPLY_LIMIT_SETTINGS_KEY],
        )
        self.assertEqual(2, settings.sync_count)

    def test_apply_limit_preference_survives_running_state_and_timing_changes(self) -> None:
        cases = (
            (False, False, True, "PRE_OPERATION", False),
            (False, True, False, "PRE_OPERATION", True),
            (True, False, True, "IMMEDIATE", False),
            (True, False, True, "NEXT_CYCLE", True),
            (True, True, False, "IMMEDIATE", False),
        )
        for running, initial, selected, timing, reopen_running in cases:
            with self.subTest(
                running=running,
                initial=initial,
                selected=selected,
                timing=timing,
                reopen_running=reopen_running,
            ):
                settings = _UiPreferenceSettings()
                host = self._dialog_host(settings=settings)
                gui_windows.MainWindow.set_start_budget_apply_limit_checked(
                    host,
                    initial,
                )
                captured_open: dict[str, object] = {}

                class AcceptedDialog:
                    result = {
                        "mode": "AMOUNT",
                        "value": 300,
                        "apply_timing": timing,
                        "apply_limit": selected,
                    }
                    requested_at = "2026-09-02 10:00:00"

                    def __init__(self, *_args, **kwargs):
                        captured_open.update(kwargs)

                    def exec_(self):
                        return gui_windows.QDialog.Accepted

                    def deleteLater(self):
                        pass

                with (
                    patch.object(
                        gui_windows,
                        "RunningBudgetAdjustmentDialog",
                        AcceptedDialog,
                    ),
                    patch.object(
                        gui_windows,
                        "auto_trade_start_budget_current_running",
                        return_value=running,
                    ),
                    patch.object(
                        gui_windows.MainWindow,
                        "_starting_budget_change_current_price",
                        side_effect=(70_000, None),
                    ),
                    patch.object(gui_windows, "show_toast"),
                ):
                    gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                        host,
                        0,
                        self.stock_dir / "config.json",
                    )

                self.assertEqual(initial, captured_open["apply_limit_checked"])
                self.assertEqual(
                    selected,
                    gui_windows.MainWindow.start_budget_apply_limit_checked(host),
                )

                captured_reopen: dict[str, object] = {}

                class CancelDialog:
                    result = {}

                    def __init__(self, *_args, **kwargs):
                        captured_reopen.update(kwargs)

                    def exec_(self):
                        return gui_windows.QDialog.Rejected

                    def deleteLater(self):
                        pass

                with (
                    patch.object(
                        gui_windows,
                        "RunningBudgetAdjustmentDialog",
                        CancelDialog,
                    ),
                    patch.object(
                        gui_windows,
                        "auto_trade_start_budget_current_running",
                        return_value=reopen_running,
                    ),
                    patch.object(
                        gui_windows.MainWindow,
                        "_starting_budget_change_current_price",
                        return_value=70_000,
                    ),
                ):
                    gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                        host,
                        0,
                        self.stock_dir / "config.json",
                    )

                self.assertEqual(selected, captured_reopen["apply_limit_checked"])

    def test_cancel_keeps_apply_limit_preference_when_pending_request_differs(self) -> None:
        settings = _UiPreferenceSettings()
        host = self._dialog_host(settings=settings)
        gui_windows.MainWindow.set_start_budget_apply_limit_checked(host, True)
        self.assertTrue(
            self._commit(
                policy="NEXT_CYCLE",
                apply_limit=False,
            )["ok"]
        )
        captured: dict[str, object] = {}

        class CancelDialog:
            result = {}

            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

            def exec_(self):
                return gui_windows.QDialog.Rejected

            def deleteLater(self):
                pass

        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", CancelDialog),
            patch.object(
                gui_windows,
                "auto_trade_start_budget_current_running",
                return_value=True,
            ),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        self.assertFalse(captured["pending_adjustment"]["apply_limit"])
        self.assertTrue(captured["apply_limit_checked"])
        self.assertTrue(gui_windows.MainWindow.start_budget_apply_limit_checked(host))

    def test_missing_configuration_price_blocks_direct_value_editor(self) -> None:
        unavailable_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: None,
        )
        for mode in ("AMOUNT", "QUANTITY"):
            with self.subTest(mode=mode):
                config = dict(self.config)
                config["trade_amount_type"] = mode
                self._write(self.stock_dir / "config.json", config)
                host = self._dialog_host()
                host.main_monitoring_auto_trade_operation_host = lambda: unavailable_host
                with (
                    patch.object(gui_windows, "RunningBudgetAdjustmentDialog") as dialog,
                    patch.object(gui_windows, "show_toast") as toast,
                ):
                    gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                        host,
                        0,
                        self.stock_dir / "config.json",
                    )

                dialog.assert_not_called()
                toast.assert_called_once_with(
                    host,
                    "현재 주가를 확인한 후 변경할 수 있습니다.",
                )

    def test_dialog_save_rechecks_current_session_price_before_mutation(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "PRE_OPERATION",
                "apply_limit": False,
            }
            requested_at = "2026-09-02 10:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        config_path = self.stock_dir / "config.json"
        before = config_path.read_bytes()
        host = self._dialog_host()
        with (
            patch.object(
                gui_windows.MainWindow,
                "_starting_budget_change_current_price",
                side_effect=(70_000, None),
            ),
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "commit_running_budget_adjustment") as commit,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                config_path,
            )

        self.assertEqual(before, config_path.read_bytes())
        commit.assert_not_called()
        toast.assert_called_once_with(
            host,
            "현재 주가를 확인한 후 변경할 수 있습니다.",
        )

    def test_dialog_cancel_has_no_runtime_commit(self) -> None:
        class CancelDialog:
            result = {}

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Rejected

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", CancelDialog),
            patch.object(gui_windows, "commit_running_budget_adjustment") as commit,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )
        commit.assert_not_called()

    def test_dialog_minimum_amount_tracks_environment_multiplier(self) -> None:
        captured_minimums: list[object] = []

        class CancelDialog:
            result = {}

            def __init__(self, *_args, **kwargs):
                captured_minimums.append(kwargs.get("minimum_amount"))

            def exec_(self):
                return gui_windows.QDialog.Rejected

            def deleteLater(self):
                pass

        fresh_state = SimpleNamespace(
            connection_epoch=7,
            login_session_id="SESSION-7",
            last_price=70_000,
        )
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: fresh_state,
        )
        host = self._dialog_host()
        host.main_monitoring_auto_trade_operation_host = lambda: operation_host
        host._main_stock_resolved_starting_budget_cache = {}
        defaults = (
            {
                "quantity": 1,
                "amount_multiplier": 1.5,
                "limit_recommended_multiplier": 100.0,
                "limit_minimum_multiplier": 25.0,
            },
            {
                "quantity": 1,
                "amount_multiplier": 2.0,
                "limit_recommended_multiplier": 100.0,
                "limit_minimum_multiplier": 25.0,
            },
        )
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", CancelDialog),
            patch.object(gui_windows, "starting_budget_defaults", side_effect=defaults),
        ):
            for _ in defaults:
                gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                    host,
                    0,
                    self.stock_dir / "config.json",
                )

        self.assertEqual([105_000, 140_000], captured_minimums)

    def test_stale_dialog_fails_closed_when_operation_is_no_longer_running(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 200,
                "apply_timing": "IMMEDIATE",
                "apply_limit": False,
            }

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(
                gui_windows,
                "auto_trade_start_budget_current_running",
                side_effect=(True, False),
            ),
            patch.object(gui_windows, "commit_running_budget_adjustment") as commit,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )
        commit.assert_not_called()
        toast.assert_called_once()

    def test_non_running_confirm_writes_base_config_and_preserves_unchecked_limit(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "PRE_OPERATION",
                "apply_limit": False,
            }

            def __init__(self, *_args, **kwargs):
                self.kwargs = kwargs

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=False),
            patch.object(gui_windows, "commit_running_budget_adjustment") as commit,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        saved = read_json_dict(self.stock_dir / "config.json")
        self.assertEqual(300, saved["buy_amount"])
        self.assertEqual(10_000, saved["buy_limit_amount"])
        self.assertEqual("MANUAL", saved["buy_limit_source"])
        commit.assert_not_called()

    def test_non_running_confirm_applies_recommended_limit_through_same_dialog(self) -> None:
        captured: dict[str, object] = {}

        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "PRE_OPERATION",
                "apply_limit": True,
            }

            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        host._adjusted_buy_limit_for_start_budget.return_value = 20_000
        original_patch = (
            gui_windows.CanonicalStockConfigRepository.patch_stock_config
        )
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=False),
            patch.object(gui_windows, "commit_running_budget_adjustment") as commit,
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(
                gui_windows.CanonicalStockConfigRepository,
                "patch_stock_config",
                autospec=True,
                side_effect=original_patch,
            ) as config_patch,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        saved = read_json_dict(self.stock_dir / "config.json")
        self.assertFalse(captured["timing_selection_enabled"])
        self.assertEqual(300, saved["buy_amount"])
        self.assertTrue(saved["buy_limit_enabled"])
        self.assertEqual(20_000, saved["buy_limit_amount"])
        self.assertEqual("RECOMMENDED", saved["buy_limit_source"])
        host._adjusted_buy_limit_for_start_budget.assert_called_once()
        commit.assert_not_called()
        self.assertEqual(2, config_patch.call_count)
        budget_patch = config_patch.call_args_list[0].args[2]
        limit_patch = config_patch.call_args_list[1].args[2]
        self.assertEqual(300, budget_patch["buy_amount"])
        self.assertEqual(20_000, limit_patch["buy_limit_amount"])
        self.assertEqual(
            "기본예산과 한도금액을 변경했습니다.",
            toast.call_args.args[1],
        )

    def test_non_running_limit_recalculation_failure_keeps_budget_change(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "PRE_OPERATION",
                "apply_limit": True,
            }

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        host._adjusted_buy_limit_for_start_budget.return_value = None
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(
                gui_windows,
                "auto_trade_start_budget_current_running",
                return_value=False,
            ),
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        saved = read_json_dict(self.stock_dir / "config.json")
        self.assertEqual(300, saved["buy_amount"])
        self.assertTrue(saved["buy_limit_enabled"])
        self.assertEqual(10_000, saved["buy_limit_amount"])
        self.assertEqual("MANUAL", saved["buy_limit_source"])
        self.assertEqual(
            "기본예산을 변경했습니다. 한도금액은 기존 설정을 유지합니다.",
            toast.call_args.args[1],
        )

    def test_non_running_same_budget_field_conflict_fails_closed(self) -> None:
        config_path = self.stock_dir / "config.json"

        class AcceptedStaleDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "PRE_OPERATION",
                "apply_limit": False,
            }

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                latest = read_json_dict(config_path)
                latest["buy_amount"] = 250
                latest["external_marker"] = "preserved"
                config_path.write_text(json.dumps(latest), encoding="utf-8")
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(
                gui_windows,
                "RunningBudgetAdjustmentDialog",
                AcceptedStaleDialog,
            ),
            patch.object(
                gui_windows,
                "auto_trade_start_budget_current_running",
                return_value=False,
            ),
            patch.object(gui_windows, "observe_owner_failure_transition") as evidence,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                config_path,
            )

        saved = read_json_dict(config_path)
        self.assertEqual(250, saved["buy_amount"])
        self.assertEqual("preserved", saved["external_marker"])
        self.assertEqual("FAILED", evidence.call_args.kwargs["result"])
        self.assertFalse(evidence.call_args.kwargs["details"]["runtime_committed"])
        self.assertEqual(
            "기본예산 설정이 변경되어 적용하지 않았습니다.",
            toast.call_args.args[1],
        )
        host.load_routine_table.assert_called_once_with()

    def test_running_runtime_success_then_config_conflict_is_partial(self) -> None:
        config_path = self.stock_dir / "config.json"

        class AcceptedStaleDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "IMMEDIATE",
                "apply_limit": False,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                latest = read_json_dict(config_path)
                latest["buy_amount"] = 250
                config_path.write_text(json.dumps(latest), encoding="utf-8")
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        attach_participant_owner(host, {"005930"})
        host.startup_recovery_session_ready = lambda refresh=False: True
        with (
            patch.object(
                gui_windows,
                "RunningBudgetAdjustmentDialog",
                AcceptedStaleDialog,
            ),
            patch.object(gui_windows, "observe_owner_failure_transition") as evidence,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                config_path,
            )

        self.assertEqual(250, read_json_dict(config_path)["buy_amount"])
        adjustment = running_budget_adjustment_snapshot(self.stock_dir)
        self.assertEqual(300, adjustment["requested_value"])
        self.assertEqual("PARTIAL", evidence.call_args.kwargs["result"])
        self.assertTrue(evidence.call_args.kwargs["details"]["runtime_committed"])
        self.assertNotIn("저장했습니다", toast.call_args.args[1])

    def test_running_runtime_failure_does_not_write_base_config(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "IMMEDIATE",
                "apply_limit": False,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        original_config = read_json_dict(self.stock_dir / "config.json")
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(
                gui_windows,
                "auto_trade_start_budget_current_running",
                return_value=True,
            ),
            patch.object(
                gui_windows,
                "commit_running_budget_adjustment",
                return_value={"ok": False, "reason": "STATE_WRITE_FAILED"},
            ),
            patch.object(
                host,
                "_write_stock_initial_buy_config",
                wraps=host._write_stock_initial_buy_config,
            ) as config_writer,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        config_writer.assert_not_called()
        self.assertEqual(original_config, read_json_dict(self.stock_dir / "config.json"))
        self.assertEqual(
            "기본예산 변경 요청을 저장하지 못했습니다.",
            toast.call_args.args[1],
        )

    def test_running_immediate_confirm_persists_base_without_signal(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "IMMEDIATE",
                "apply_limit": False,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        attach_participant_owner(host, {"005930"})
        host.startup_recovery_session_ready = lambda refresh=False: True
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "show_toast"),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        saved_config = read_json_dict(self.stock_dir / "config.json")
        saved_state = read_json_dict(self.stock_dir / "state.json")
        self.assertEqual(300, saved_config["buy_amount"])
        self.assertEqual(STATE_WAIT_FIRST_BUY, saved_state[ADJUSTMENT_KEY]["state"])
        self.assertEqual(100, saved_state[ADJUSTMENT_KEY]["previous_value"])
        saved_state["trade_enabled"] = False
        projected, evidence = project_running_budget_adjustment_config(
            saved_config,
            saved_state,
        )
        self.assertEqual(300, projected["buy_amount"])
        self.assertFalse(evidence["active"])

    def test_running_next_cycle_persists_base_but_preserves_current_cycle(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "NEXT_CYCLE",
                "apply_limit": True,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        host._adjusted_buy_limit_for_start_budget.return_value = 20_000
        attach_participant_owner(host, {"005930"})
        host.startup_recovery_session_ready = lambda refresh=False: True
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "show_toast"),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        saved_config = read_json_dict(self.stock_dir / "config.json")
        saved_state = read_json_dict(self.stock_dir / "state.json")
        self.assertEqual(300, saved_config["buy_amount"])
        self.assertEqual(20_000, saved_config["buy_limit_amount"])
        self.assertEqual(100, saved_state[ADJUSTMENT_KEY]["previous_value"])
        self.assertEqual(
            10_000,
            saved_state[ADJUSTMENT_KEY]["previous_limit"]["buy_limit_amount"],
        )

        current_cycle, evidence = project_running_budget_adjustment_config(
            saved_config,
            saved_state,
        )
        self.assertEqual(100, current_cycle["buy_amount"])
        self.assertEqual(10_000, current_cycle["buy_limit_amount"])
        self.assertEqual("WAITING_FOR_SELL", evidence["reason"])

        transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="SELL",
            signal_id="sell-next-cycle",
        )
        next_cycle, _ = self._project()
        self.assertEqual(300, next_cycle["buy_amount"])
        self.assertEqual(20_000, next_cycle["buy_limit_amount"])

        stopped_state = read_json_dict(self.stock_dir / "state.json")
        stopped_state["trade_enabled"] = False
        restarted, evidence = project_running_budget_adjustment_config(
            saved_config,
            stopped_state,
        )
        self.assertEqual(300, restarted["buy_amount"])
        self.assertEqual(20_000, restarted["buy_limit_amount"])
        self.assertFalse(evidence["active"])

    def test_dialog_confirm_converts_amount_through_existing_safe_int_helper(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": "1,234",
                "apply_timing": "IMMEDIATE",
                "apply_limit": False,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=True),
            patch.object(gui_windows, "commit_running_budget_adjustment", return_value={"ok": True}) as commit,
            patch.object(gui_windows, "show_toast"),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )
        self.assertEqual(1234, commit.call_args.kwargs["requested_value"])
        self.assertEqual("IMMEDIATE", commit.call_args.kwargs["apply_policy"])

    def test_dialog_confirm_converts_quantity_for_next_cycle_without_mode_change(self) -> None:
        class AcceptedDialog:
            result = {
                "mode": "QUANTITY",
                "value": "52",
                "apply_timing": "NEXT_CYCLE",
                "apply_limit": False,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **_kwargs):
                pass

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        config_path = self.stock_dir / "config.json"
        config = read_json_dict(config_path)
        config["trade_amount_type"] = "QUANTITY"
        config["buy_qty"] = 7
        self._write(config_path, config)
        host = self._dialog_host()
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=True),
            patch.object(gui_windows, "commit_running_budget_adjustment", return_value={"ok": True}) as commit,
            patch.object(gui_windows, "show_toast"),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                config_path,
            )
        self.assertEqual(52, commit.call_args.kwargs["requested_value"])
        self.assertEqual("NEXT_CYCLE", commit.call_args.kwargs["apply_policy"])

    def test_running_confirm_reuses_common_limit_calculation_before_runtime_commit(self) -> None:
        captured: dict[str, object] = {}

        class AcceptedDialog:
            result = {
                "mode": "AMOUNT",
                "value": 300,
                "apply_timing": "IMMEDIATE",
                "apply_limit": True,
            }
            requested_at = "2026-08-28 12:00:00"

            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                pass

        host = self._dialog_host()
        host._adjusted_buy_limit_for_start_budget.return_value = 20_000
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", AcceptedDialog),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=True),
            patch.object(
                gui_windows,
                "commit_running_budget_adjustment",
                return_value={"ok": True},
            ) as commit,
            patch.object(gui_windows, "show_toast"),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )

        self.assertTrue(captured["timing_selection_enabled"])
        host._adjusted_buy_limit_for_start_budget.assert_called_once()
        self.assertEqual(20_000, commit.call_args.kwargs["adjusted_limit_amount"])
        self.assertTrue(commit.call_args.kwargs["apply_limit"])
        self.assertEqual("IMMEDIATE", commit.call_args.kwargs["apply_policy"])

    def test_pending_next_cycle_separates_display_from_execution_projection(self) -> None:
        config_path = self.stock_dir / "config.json"
        config = read_json_dict(config_path)
        config["buy_amount"] = 20_000
        config["buy_limit_amount"] = 2_000_000
        self._write(config_path, config)
        self.assertTrue(
            self._commit(
                policy="NEXT_CYCLE",
                value=60_000,
                apply_limit=True,
                adjusted_limit_amount=6_000_000,
            )["ok"]
        )
        persisted_config = dict(config)
        persisted_config["buy_amount"] = 60_000
        persisted_config["buy_limit_amount"] = 6_000_000
        persisted_config["buy_limit_source"] = "RECOMMENDED"
        self._write(config_path, persisted_config)
        state = read_json_dict(self.stock_dir / "state.json")
        display, evidence = project_running_budget_adjustment_display_config(
            persisted_config,
            state,
        )
        execution, execution_evidence = project_running_budget_adjustment_config(
            persisted_config,
            state,
        )
        self.assertEqual(60_000, display["buy_amount"])
        self.assertEqual(6_000_000, display["buy_limit_amount"])
        self.assertTrue(evidence["hydrated"])
        self.assertEqual(20_000, execution["buy_amount"])
        self.assertEqual(2_000_000, execution["buy_limit_amount"])
        self.assertEqual("WAITING_FOR_SELL", execution_evidence["reason"])

        transition_running_budget_adjustment_for_signal(
            self.stock_dir,
            signal="SELL",
            signal_id="sell-for-next-cycle",
        )
        execution, _ = project_running_budget_adjustment_config(
            persisted_config,
            read_json_dict(self.stock_dir / "state.json"),
        )
        self.assertEqual(60_000, execution["buy_amount"])
        self.assertEqual(6_000_000, execution["buy_limit_amount"])

    def test_dialog_reentry_receives_saved_request_and_cancel_preserves_it(self) -> None:
        self.assertTrue(
            self._commit(
                policy="NEXT_CYCLE",
                value=60_000,
                apply_limit=True,
                adjusted_limit_amount=6_000_000,
            )["ok"]
        )
        saved_before = running_budget_adjustment_snapshot(self.stock_dir)
        captured: dict[str, object] = {}

        class RehydratedCancelDialog:
            result = {}

            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

            def exec_(self):
                return gui_windows.QDialog.Rejected

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog", RehydratedCancelDialog),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=True),
            patch.object(gui_windows, "commit_running_budget_adjustment") as commit,
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                self.stock_dir / "config.json",
            )
        self.assertEqual(60_000, captured["config"]["buy_amount"])
        self.assertEqual("NEXT_CYCLE", captured["pending_adjustment"]["apply_policy"])
        self.assertTrue(captured["pending_adjustment"]["apply_limit"])
        commit.assert_not_called()
        self.assertEqual(saved_before, running_budget_adjustment_snapshot(self.stock_dir))

    def test_quantity_dialog_reentry_receives_latest_saved_request(self) -> None:
        config_path = self.stock_dir / "config.json"
        config = read_json_dict(config_path)
        config["trade_amount_type"] = "QUANTITY"
        config["buy_qty"] = 3
        self._write(config_path, config)
        committed = commit_running_budget_adjustment(
            self.stock_dir,
            stock_code="005930",
            expected_mode="QUANTITY",
            requested_value=10,
            apply_policy="IMMEDIATE",
            apply_limit=False,
            confirmed_at="2026-08-28 10:00:00",
        )
        self.assertTrue(committed["ok"], committed)
        captured: dict[str, object] = {}

        class RehydratedCancelDialog:
            result = {}

            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)

            def exec_(self):
                return gui_windows.QDialog.Rejected

            def deleteLater(self):
                pass

        host = self._dialog_host()
        with (
            patch.object(
                gui_windows,
                "RunningBudgetAdjustmentDialog",
                RehydratedCancelDialog,
            ),
            patch.object(gui_windows, "auto_trade_start_budget_current_running", return_value=True),
        ):
            gui_windows.MainWindow._open_running_budget_adjustment_dialog(
                host,
                0,
                config_path,
            )
        self.assertEqual("QUANTITY", captured["config"]["trade_amount_type"])
        self.assertEqual(10, captured["config"]["buy_qty"])

    def test_main_row_keeps_current_budget_and_separates_pending_projection(self) -> None:
        state = {
            **self.state,
            ADJUSTMENT_KEY: {
                "version": 1,
                "request_id": "request-display",
                "stock_code": "005930",
                "mode": "AMOUNT",
                "requested_value": 60_000,
                "apply_policy": "NEXT_CYCLE",
                "state": STATE_WAIT_SELL,
                "apply_limit": True,
                "adjusted_limit_amount": 6_000_000,
                "operation_session_started_at": self.state["trade_started_at"],
            },
        }
        fresh_state = SimpleNamespace(
            connection_epoch=1,
            login_session_id="SESSION-1",
            last_price=70_000,
        )
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: fresh_state,
        )
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(
                main_monitoring_auto_trade_operation_host=lambda: operation_host,
                _main_monitoring_auto_trade_operation_host=participant_owner({"005930"}),
            ),
            definition_id="fixture",
            instance_id="fixture-instance",
            stock={
                "code": "005930",
                "name": "삼성전자",
                "enabled": True,
                "stock_path": "",
                "state": state,
                "config": {
                    "trade_amount_type": "AMOUNT",
                    "buy_amount": 20_000,
                    "buy_limit_enabled": True,
                    "buy_limit_amount": 2_000_000,
                },
            },
        )
        self.assertEqual("20,000원", row["initial_buy"]["value_text"])
        self.assertEqual("한도(2,000,000)", row["stock_values"][11])
        adjustment = row["running_budget_adjustment_display"]
        self.assertEqual("WAITING_FOR_SELL", adjustment["reason"])
        self.assertTrue(adjustment["pending"])
        self.assertTrue(adjustment["pending_request"]["hydrated"])

    def test_non_running_main_row_uses_saved_config_not_stale_running_projection(self) -> None:
        state = {
            **self.state,
            ADJUSTMENT_KEY: {
                "version": 1,
                "request_id": "stale-request-display",
                "stock_code": "005930",
                "mode": "AMOUNT",
                "requested_value": 60_000,
                "apply_policy": "IMMEDIATE",
                "state": STATE_APPLIED,
                "apply_limit": True,
                "adjusted_limit_amount": 6_000_000,
                "operation_session_started_at": self.state["trade_started_at"],
            },
        }
        fresh_state = SimpleNamespace(
            connection_epoch=1,
            login_session_id="SESSION-1",
            last_price=37_800,
        )
        operation_host = SimpleNamespace(
            fresh_monitoring_market_information_state=lambda _code: fresh_state,
        )
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(
                main_monitoring_auto_trade_operation_host=lambda: operation_host,
                _main_monitoring_auto_trade_operation_host=participant_owner(),
            ),
            definition_id="fixture",
            instance_id="fixture-instance",
            stock={
                "code": "005930",
                "name": "삼성전자",
                "enabled": True,
                "stock_path": "",
                "state": state,
                "config": {
                    "trade_amount_type": "AMOUNT",
                    "buy_amount": 30_000,
                    "buy_limit_enabled": True,
                    "buy_limit_amount": 3_000_000,
                    "buy_limit_source": "RECOMMENDED",
                },
            },
        )

        self.assertEqual("30,000원", row["initial_buy"]["value_text"])
        self.assertEqual("한도(3,000,000)", row["stock_values"][11])
        self.assertFalse(row["running_budget_adjustment_display"]["active"])
        self.assertEqual(
            "NOT_CURRENT_RUNNING",
            row["running_budget_adjustment_display"]["reason"],
        )


if __name__ == "__main__":
    unittest.main()
