# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gui_auto_trade_policy as policy
import gui_auto_trade_table_loader as setting_loader
import gui_ats_utils as ats_utils
import gui_main_table_loader as main_loader
import routine_order_permission as order_permission
from gui_ats_utils import (
    auto_trade_operation_activation_phase,
    auto_trade_operation_session_phase,
)
from final_execution_guard import evaluate_final_execution_guard


class OperationProjectionNormalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.operation_policy = {
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
            "extra_sessions": [
                {
                    "enabled": True,
                    "name": "장전프리",
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                },
                {
                    "enabled": True,
                    "name": "장후ATS",
                    "start_time": "15:40:00",
                    "end_time": "19:50:00",
                },
                {
                    "enabled": False,
                    "name": "추가3",
                    "start_time": "",
                    "end_time": "",
                },
            ],
            "scheduled_operation": {
                "default_start_time": "09:00:00",
                "default_end_buy_time": "13:30:00",
            },
            "manual_operation": {
                "use_regular_market": True,
                "use_liquidation_policy": False,
            },
            "liquidation": {
                "minutes_before_regular_close": "5",
                "method": "시장가",
            },
        }

    def _project(
        self,
        *,
        category: str,
        mode: str = "SCHEDULED",
        state: dict[str, object] | None = None,
        config: dict[str, object] | None = None,
        current_session: bool = False,
        now_dt: datetime | None = None,
    ) -> dict[str, object]:
        runtime_state = state or {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-26 09:00:00",
        }
        stock_config = {
            "operation_mode": mode,
            **(config or {}),
        }
        with patch.object(
            policy,
            "read_operation_policy",
            return_value=deepcopy(self.operation_policy),
        ), patch.object(
            ats_utils,
            "read_operation_policy",
            return_value=deepcopy(self.operation_policy),
        ):
            return policy.auto_trade_setting_row_projection(
                runtime_state,
                stock_config,
                operation_category=category,
                holding_qty=0,
                current_session_trade_started=current_session,
                persisted_trade_started=bool(runtime_state.get("trade_enabled")),
                now_dt=now_dt,
            )

    def assert_inactive_projection(
        self,
        result: dict[str, object],
        *,
        liquidation_text: str,
    ) -> None:
        self.assertEqual("감시/대기", result["display_status"])
        self.assertEqual("루틴", result["method_text"])
        self.assertEqual(liquidation_text, result["liquidation_text"])
        self.assertIs(result["status_cell_active"], False)
        self.assertIs(result["method_cell_active"], False)
        self.assertIs(result["liquidation_cell_active"], False)

    def test_waiting_review_and_excluded_share_inactive_projection_without_mutation(self) -> None:
        cases = {
            "waiting": {"status": "STOPPED", "trade_enabled": False},
            "review": {
                "status": "REVIEW_REQUIRED",
                "trade_enabled": True,
                "review_required": True,
            },
            "excluded": {"status": "RUNNING", "trade_enabled": True},
        }
        for category, state in cases.items():
            with self.subTest(category=category):
                before = deepcopy(state)
                result = self._project(
                    category=category,
                    mode="CONTINUOUS",
                    state=state,
                )
                self.assert_inactive_projection(result, liquidation_text="-")
                self.assertEqual(before, state)

    def test_scheduled_prestart_and_active_projection(self) -> None:
        state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-26 08:30:00",
        }
        config = {"start_time": "09:00:00", "end_buy_time": "13:30:00"}
        prestart = self._project(
            category="operation",
            state=state,
            config=config,
            current_session=True,
            now_dt=datetime(2026, 8, 26, 8, 50),
        )
        self.assertEqual("감시/대기", prestart["display_status"])
        self.assertEqual("루틴", prestart["method_text"])
        self.assertEqual("5분/시장가", prestart["liquidation_text"])
        self.assertIs(prestart["status_cell_active"], True)
        self.assertIs(prestart["method_cell_active"], False)
        self.assertIs(prestart["liquidation_cell_active"], False)

        active = self._project(
            category="operation",
            state=state,
            config=config,
            current_session=True,
            now_dt=datetime(2026, 8, 26, 10, 0),
        )
        self.assertEqual("매수/매도", active["display_status"])
        self.assertEqual("루틴", active["method_text"])
        self.assertEqual("5분/시장가", active["liquidation_text"])
        self.assertIs(active["status_cell_active"], True)
        self.assertIs(active["method_cell_active"], True)

    def test_retired_scheduled_and_continuous_rows_return_to_waiting_projection(self) -> None:
        stopped = {"status": "STOPPED", "trade_enabled": False}
        for mode, liquidation_text in (
            ("SCHEDULED", "5분/시장가"),
            ("CONTINUOUS", "-"),
        ):
            with self.subTest(mode=mode):
                result = self._project(
                    category="waiting",
                    mode=mode,
                    state=stopped,
                    current_session=False,
                )
                self.assert_inactive_projection(
                    result,
                    liquidation_text=liquidation_text,
                )

    def test_continuous_active_ignores_real_trade_permission_for_display_only(self) -> None:
        state = {
            "status": "MONITORING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-26 09:00:00",
        }
        with patch.object(
            policy,
            "auto_trade_setting_regular_market_active_now",
            return_value=True,
        ), patch.object(
            policy,
            "auto_trade_operation_session_phase",
            return_value={
                "evaluable": True,
                "phase": "ACTIVE_SESSION",
                "mode": "CONTINUOUS",
                "active": True,
                "active_sessions": ("regular",),
                "future_session_exists": False,
                "final_session_ended": False,
            },
        ):
            result = self._project(
                category="operation",
                mode="CONTINUOUS",
                state=state,
                config={"real_trade_enabled": False},
                current_session=True,
                now_dt=datetime(2026, 8, 26, 10, 0),
            )

        self.assertEqual("매수/매도", result["display_status"])
        self.assertEqual("루틴", result["method_text"])
        self.assertEqual("-", result["liquidation_text"])
        self.assertIs(result["method_cell_active"], True)

        guard = evaluate_final_execution_guard(
            order={"status": "REAL_READY", "execution_enabled": True},
            guard={"operator_confirmed": True, "real_trade_enabled": False},
            execution_preview={
                "unresolved": False,
                "hoga_preview": {"unresolved": False},
                "order_type_preview": {"unresolved": False},
            },
        )
        self.assertIs(guard["ok"], False)
        self.assertIn("guard.real_trade_enabled is not true", guard["blocked_reasons"])

    def test_stale_early_close_metadata_is_read_only_and_does_not_split_views(self) -> None:
        state = {
            "status": "EARLY_CLOSE",
            "trade_enabled": True,
            "early_close_requested_at": "2026-08-26 09:10:00",
            "early_close_method": "시장가",
            "trade_started_at": "2026-08-26 10:00:00",
        }
        before = deepcopy(state)
        with patch.object(
            policy,
            "auto_trade_setting_regular_market_active_now",
            return_value=True,
        ), patch.object(
            policy,
            "auto_trade_operation_session_phase",
            return_value={
                "evaluable": True,
                "phase": "ACTIVE_SESSION",
                "mode": "CONTINUOUS",
                "active": True,
                "active_sessions": ("regular",),
                "future_session_exists": False,
                "final_session_ended": False,
            },
        ):
            result = self._project(
                category="operation",
                mode="CONTINUOUS",
                state=state,
                current_session=True,
                now_dt=datetime(2026, 8, 26, 10, 0),
            )

        self.assertEqual("매수/매도", result["display_status"])
        self.assertEqual("루틴", result["method_text"])
        self.assertEqual(before, state)

    def _continuous_phase(
        self,
        now_dt: datetime,
        *,
        regular_end: str,
        ats_start: str,
        include_ats: bool = True,
    ) -> dict[str, object]:
        state = {
            "manual_ats_selection": {
                "selected_sessions": ["extra2"] if include_ats else [],
            },
        }
        operation_policy = {
            "manual_operation": {"use_regular_market": True},
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": regular_end,
            },
        }
        ats_session = {
            "enabled": True,
            "start_time": ats_start,
            "end_time": "19:50:00",
        }
        return auto_trade_operation_session_phase(
            {"operation_mode": "CONTINUOUS"},
            state,
            now_dt=now_dt,
            operation_policy_reader=lambda: deepcopy(operation_policy),
            ats_session_reader=lambda _key: deepcopy(ats_session),
        )

    def _between_projection(self, phase: dict[str, object]) -> dict[str, object]:
        state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-26 09:00:00",
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        with patch.object(
            policy,
            "auto_trade_operation_session_phase",
            return_value=deepcopy(phase),
        ):
            return self._project(
                category="operation",
                mode="CONTINUOUS",
                state=state,
                current_session=True,
                now_dt=datetime(2026, 8, 26, 15, 30),
            )

    def assert_between_projection(self, result: dict[str, object]) -> None:
        self.assertEqual("operation", result["operation_category"])
        self.assertEqual("BETWEEN_SESSIONS", result["session_phase"])
        self.assertEqual("INTER_SESSION_NON_TRADING_GAP", result["projection_phase"])
        self.assertIs(result["between_sessions"], True)
        self.assertIs(result["situation_active"], True)
        self.assertEqual("감시/대기", result["display_status"])
        self.assertEqual("루틴", result["method_text"])
        self.assertEqual("-", result["liquidation_text"])
        self.assertIs(result["status_cell_active"], True)
        self.assertIs(result["method_cell_active"], False)
        self.assertIs(result["liquidation_cell_active"], False)

    def test_dynamic_between_sessions_projection_preserves_participant_semantics(self) -> None:
        phase = self._continuous_phase(
            datetime(2026, 8, 26, 15, 30),
            regular_end="15:20:00",
            ats_start="15:40:00",
        )
        self.assertIs(phase["evaluable"], True)
        self.assertIs(phase["future_session_exists"], True)
        self.assertIs(phase["final_session_ended"], False)
        self.assert_between_projection(self._between_projection(phase))
        with patch.object(
            order_permission,
            "in_manual_trading_session",
            return_value=False,
        ), patch.object(
            order_permission,
            "manual_ats_active_now",
            return_value=False,
        ):
            time_status = order_permission.canonical_stock_trading_time_status(
                config={"operation_mode": "CONTINUOUS"},
                state={"manual_ats_selection": {"selected_sessions": ["extra2"]}},
                now_dt=datetime(2026, 8, 26, 15, 30),
            )
        self.assertIs(time_status["active"], False)
        self.assertEqual("OUTSIDE_OPERATION_TIME", time_status["reason"])

    def test_between_sessions_uses_configured_times_without_hardcoding(self) -> None:
        phase = self._continuous_phase(
            datetime(2026, 8, 26, 15, 0),
            regular_end="14:50:00",
            ats_start="15:10:00",
        )
        self.assertEqual("BETWEEN_SESSIONS", phase["phase"])
        self.assert_between_projection(self._between_projection(phase))

    def test_ats_start_boundary_transitions_from_between_to_active(self) -> None:
        before = self._continuous_phase(
            datetime(2026, 8, 26, 15, 39, 59),
            regular_end="15:20:00",
            ats_start="15:40:00",
        )
        active = self._continuous_phase(
            datetime(2026, 8, 26, 15, 40, 0),
            regular_end="15:20:00",
            ats_start="15:40:00",
        )
        self.assertEqual("BETWEEN_SESSIONS", before["phase"])
        self.assertEqual("ACTIVE_SESSION", active["phase"])
        active_projection = self._between_projection(active)
        self.assertIs(active_projection["between_sessions"], False)
        self.assertIs(active_projection["status_cell_active"], True)
        self.assertIs(active_projection["method_cell_active"], True)

    def test_no_future_ats_session_reaches_final_session_end(self) -> None:
        phase = self._continuous_phase(
            datetime(2026, 8, 26, 15, 30),
            regular_end="15:20:00",
            ats_start="15:40:00",
            include_ats=False,
        )
        self.assertEqual("FINAL_SESSION_ENDED", phase["phase"])
        self.assertIs(phase["future_session_exists"], False)
        self.assertIs(phase["final_session_ended"], True)

    def test_main_and_setting_import_the_same_canonical_projection(self) -> None:
        self.assertIs(
            setting_loader.auto_trade_setting_row_projection,
            main_loader.auto_trade_setting_row_projection,
        )

    def test_modeless_setting_resolves_main_participant_projection(self) -> None:
        main = SimpleNamespace(
            _current_session_operation_participant_stock_codes={"012210"},
            startup_recovery_session_ready=lambda **_kwargs: True,
        )
        setting = SimpleNamespace()

        def logical_owner(value):
            return main if value is setting else None

        with patch.object(policy, "persistent_feature_owner", side_effect=logical_owner):
            self.assertEqual(
                ("012210",),
                policy.auto_trade_current_session_operation_participant_codes(setting),
            )
            self.assertEqual(
                "operation",
                policy.auto_trade_stock_operation_category(
                    setting,
                    stock_code="012210",
                    persisted_trade_started=True,
                    operation_excluded=False,
                    review_required=False,
                ),
            )
            self.assertTrue(
                policy.auto_trade_setting_current_session_trade_started(
                    setting,
                    True,
                    "012210",
                )
            )

    def test_main_and_modeless_setting_have_equal_participant_visual_semantics(self) -> None:
        main = SimpleNamespace(
            _current_session_operation_participant_stock_codes={"012210"},
            startup_recovery_session_ready=lambda **_kwargs: True,
        )
        setting = SimpleNamespace()
        base_state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-27 07:50:00",
        }
        config = {
            "operation_mode": "CONTINUOUS",
            "start_time": "09:30:00",
            "end_buy_time": "13:30:00",
        }
        cases = (
            ("PRE_OPERATION_BOUNDARY", datetime(2026, 8, 27, 7, 50), (), "ROUTINE"),
            ("PREMARKET_ATS_ACTIVE_ROUTINE", datetime(2026, 8, 27, 8, 10), ("extra1",), "ROUTINE"),
            ("PREMARKET_ATS_ACTIVE_MARKET", datetime(2026, 8, 27, 8, 10), ("extra1",), "MARKET"),
            ("PREMARKET_ATS_ACTIVE_CURRENT_PRICE", datetime(2026, 8, 27, 8, 10), ("extra1",), "CURRENT_PRICE"),
            ("OPERATION_BOUNDARY_REACHED", datetime(2026, 8, 27, 9, 10), (), "ROUTINE"),
            ("ACTIVE_TRADING_WINDOW", datetime(2026, 8, 27, 10, 0), (), "ROUTINE"),
            ("NON_TRADING_GAP", datetime(2026, 8, 27, 15, 30), ("extra2",), "ROUTINE"),
            ("POSTMARKET_ATS_ACTIVE", datetime(2026, 8, 27, 16, 0), ("extra2",), "CURRENT_PRICE"),
        )
        semantic_keys = (
            "operation_category",
            "situation_active",
            "display_status",
            "status_cell_active",
            "method_text",
            "method_cell_active",
            "liquidation_text",
            "liquidation_cell_active",
        )

        def logical_owner(value):
            return main if value is setting else None

        def project(window, state, now_dt):
            current_session = policy.auto_trade_setting_current_session_trade_started(
                window,
                True,
                "012210",
            )
            category = policy.auto_trade_stock_operation_category(
                window,
                stock_code="012210",
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
            )
            return policy.auto_trade_setting_row_projection(
                state,
                config,
                operation_category=category,
                holding_qty=0,
                current_session_trade_started=current_session,
                persisted_trade_started=True,
                now_dt=now_dt,
            )

        with (
            patch.object(policy, "persistent_feature_owner", side_effect=logical_owner),
            patch.object(
                policy,
                "read_operation_policy",
                return_value=deepcopy(self.operation_policy),
            ),
            patch.object(
                ats_utils,
                "read_operation_policy",
                return_value=deepcopy(self.operation_policy),
            ),
        ):
            for label, now_dt, sessions, execution_method in cases:
                with self.subTest(label=label):
                    state = deepcopy(base_state)
                    state["manual_ats_selection"] = {
                        "selected_sessions": list(sessions),
                        "execution_method": execution_method,
                    }
                    main_projection = project(main, state, now_dt)
                    setting_projection = project(setting, state, now_dt)
                    self.assertEqual(
                        {key: main_projection[key] for key in semantic_keys},
                        {key: setting_projection[key] for key in semantic_keys},
                    )
                    self.assertIs(setting_projection["situation_active"], True)
                    self.assertIs(setting_projection["status_cell_active"], True)

    def test_nonparticipant_visual_semantics_remain_inactive(self) -> None:
        window = SimpleNamespace(
            _current_session_operation_participant_stock_codes=set(),
            startup_recovery_session_ready=lambda **_kwargs: True,
        )
        state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-27 07:50:00",
        }
        category = policy.auto_trade_stock_operation_category(
            window,
            stock_code="012210",
            persisted_trade_started=True,
            operation_excluded=False,
            review_required=False,
        )
        projection = self._project(
            category=category,
            mode="CONTINUOUS",
            state=state,
            current_session=False,
            now_dt=datetime(2026, 8, 27, 7, 50),
        )

        self.assertEqual("waiting", category)
        self.assertIs(projection["situation_active"], False)
        self.assertIs(projection["status_cell_active"], False)
        self.assertIs(projection["method_cell_active"], False)
        self.assertIs(projection["liquidation_cell_active"], False)

    def test_three_stock_preboundary_fixture_matches_main_activity_semantics(self) -> None:
        codes = {"012210", "063440", "130500"}
        main = SimpleNamespace(
            _current_session_operation_participant_stock_codes=set(codes),
            startup_recovery_session_ready=lambda **_kwargs: True,
        )
        setting = SimpleNamespace()
        state = {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-27 07:50:00",
        }
        configs = {
            "012210": {
                "operation_mode": "CONTINUOUS",
                "start_time": "09:30:00",
                "end_buy_time": "13:30:00",
            },
            "063440": {
                "operation_mode": "SCHEDULED",
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            "130500": {
                "operation_mode": "SCHEDULED",
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
        }

        def logical_owner(value):
            return main if value is setting else None

        with (
            patch.object(policy, "persistent_feature_owner", side_effect=logical_owner),
            patch.object(
                policy,
                "read_operation_policy",
                return_value=deepcopy(self.operation_policy),
            ),
            patch.object(
                ats_utils,
                "read_operation_policy",
                return_value=deepcopy(self.operation_policy),
            ),
        ):
            for code, config in configs.items():
                with self.subTest(code=code):
                    projections = []
                    for window in (main, setting):
                        current_session = (
                            policy.auto_trade_setting_current_session_trade_started(
                                window,
                                True,
                                code,
                            )
                        )
                        category = policy.auto_trade_stock_operation_category(
                            window,
                            stock_code=code,
                            persisted_trade_started=True,
                            operation_excluded=False,
                            review_required=False,
                        )
                        projections.append(
                            policy.auto_trade_setting_row_projection(
                                state,
                                config,
                                operation_category=category,
                                holding_qty=0,
                                current_session_trade_started=current_session,
                                persisted_trade_started=True,
                                now_dt=datetime(2026, 8, 27, 7, 50),
                            )
                        )

                    self.assertEqual(projections[0], projections[1])
                    result = projections[1]
                    self.assertEqual("operation", result["operation_category"])
                    self.assertIs(result["situation_active"], True)
                    self.assertEqual("감시/대기", result["display_status"])
                    self.assertIs(result["status_cell_active"], True)
                    self.assertEqual("루틴", result["method_text"])
                    self.assertIs(result["method_cell_active"], False)
                    expected_liquidation = "-" if code == "012210" else "5분/시장가"
                    self.assertEqual(expected_liquidation, result["liquidation_text"])
                    self.assertIs(result["liquidation_cell_active"], False)

    def test_protected_and_inactive_categories_keep_existing_visual_semantics(self) -> None:
        cases = (
            ("waiting", {"status": "STOPPED", "trade_enabled": False}, False),
            ("review", {"status": "REVIEW_REQUIRED", "trade_enabled": True}, False),
            ("excluded", {"status": "RUNNING", "trade_enabled": True}, False),
            ("operation", {"status": "EMERGENCY_STOP", "trade_enabled": True}, True),
        )
        for category, state, current_session in cases:
            with self.subTest(category=category, status=state["status"]):
                result = self._project(
                    category=category,
                    mode="CONTINUOUS",
                    state=state,
                    current_session=current_session,
                    now_dt=datetime(2026, 8, 27, 10, 0),
                )
                if category in {"waiting", "review", "excluded"}:
                    self.assertIs(result["situation_active"], False)
                    self.assertIs(result["status_cell_active"], False)
                    self.assertIs(result["method_cell_active"], False)
                    self.assertIs(result["liquidation_cell_active"], False)
                else:
                    self.assertEqual("긴급정지", result["display_status"])
                    self.assertIs(result["status_cell_active"], False)


class OperationTimeBoundaryContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.operation_policy = {
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
            "extra_sessions": [
                {
                    "enabled": True,
                    "name": "장전프리",
                    "start_time": "08:00:00",
                    "end_time": "08:50:00",
                },
                {
                    "enabled": True,
                    "name": "장후ATS",
                    "start_time": "15:40:00",
                    "end_time": "19:50:00",
                },
                {
                    "enabled": True,
                    "name": "추가3",
                    "start_time": "20:00:00",
                    "end_time": "21:00:00",
                },
            ],
            "scheduled_operation": {
                "default_start_time": "08:50:00",
                "default_end_buy_time": "13:30:00",
            },
            "manual_operation": {
                "use_regular_market": True,
                "use_liquidation_policy": True,
            },
            "liquidation": {
                "minutes_before_regular_close": "5",
                "method": "시장가",
            },
        }

    def _session(self, key: str) -> dict[str, object]:
        index = {"extra1": 0, "extra2": 1, "extra3": 2}[key]
        return deepcopy(self.operation_policy["extra_sessions"][index])

    def _state(
        self,
        *,
        sessions: tuple[str, ...] = (),
        method: str = "ROUTINE",
    ) -> dict[str, object]:
        return {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": "2026-08-27 07:50:00",
            "manual_ats_selection": {
                "selected_sessions": list(sessions),
                "execution_method": method,
            },
        }

    def _phase(
        self,
        config: dict[str, object],
        state: dict[str, object],
        now_dt: datetime,
    ) -> dict[str, object]:
        return auto_trade_operation_session_phase(
            config,
            state,
            now_dt=now_dt,
            operation_policy_reader=lambda: deepcopy(self.operation_policy),
            ats_session_reader=self._session,
        )

    def _activation(
        self,
        config: dict[str, object],
        state: dict[str, object],
        now_dt: datetime,
    ) -> dict[str, object]:
        phase = self._phase(config, state, now_dt)
        return auto_trade_operation_activation_phase(
            config,
            state,
            now_dt=now_dt,
            session_phase=phase,
            operation_policy_reader=lambda: deepcopy(self.operation_policy),
        )

    def _row(
        self,
        config: dict[str, object],
        state: dict[str, object],
        now_dt: datetime,
    ) -> dict[str, object]:
        phase = self._phase(config, state, now_dt)
        with (
            patch.object(policy, "read_operation_policy", return_value=deepcopy(self.operation_policy)),
            patch.object(policy, "auto_trade_operation_session_phase", return_value=phase),
        ):
            return policy.auto_trade_setting_row_projection(
                state,
                config,
                operation_category="operation",
                holding_qty=0,
                current_session_trade_started=True,
                persisted_trade_started=True,
                now_dt=now_dt,
            )

    def assert_row(
        self,
        result: dict[str, object],
        *,
        phase: str,
        status: str,
        method: str,
        method_active: bool,
        liquidation: str,
        liquidation_active: bool,
    ) -> None:
        self.assertEqual(phase, result["projection_phase"])
        self.assertEqual(status, result["display_status"])
        self.assertEqual(method, result["method_text"])
        self.assertIs(result["status_cell_active"], True)
        self.assertIs(result["method_cell_active"], method_active)
        self.assertEqual(liquidation, result["liquidation_text"])
        self.assertIs(result["liquidation_cell_active"], liquidation_active)

    def test_manual_operation_and_trade_start_boundaries_are_distinct(self) -> None:
        config = {
            "operation_mode": "CONTINUOUS",
            "start_time": "09:30:00",
            "end_buy_time": "13:30:00",
        }
        state = self._state()
        cases = (
            (datetime(2026, 8, 27, 8, 59, 59), "PRE_OPERATION_BOUNDARY", False),
            (datetime(2026, 8, 27, 9, 0, 0), "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY", True),
            (datetime(2026, 8, 27, 9, 29, 59), "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY", True),
            (datetime(2026, 8, 27, 9, 30, 0), "ACTIVE_SESSION", True),
        )
        for now_dt, phase, controls_active in cases:
            with self.subTest(now=now_dt.time()):
                row = self._row(config, state, now_dt)
                self.assert_row(
                    row,
                    phase=phase,
                    status="매수/매도" if phase == "ACTIVE_SESSION" else "감시/대기",
                    method="루틴",
                    method_active=controls_active,
                    liquidation="5분/시장가",
                    liquidation_active=controls_active,
                )

    def test_scheduled_environment_boundary_precedes_individual_trade_start(self) -> None:
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:10:00",
            "end_buy_time": "13:30:00",
        }
        state = self._state()
        expected = (
            (datetime(2026, 8, 27, 8, 49, 59), "PRE_OPERATION_BOUNDARY"),
            (datetime(2026, 8, 27, 8, 50, 0), "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY"),
            (datetime(2026, 8, 27, 9, 9, 59), "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY"),
            (datetime(2026, 8, 27, 9, 10, 0), "ACTIVE_SESSION"),
        )
        for now_dt, expected_phase in expected:
            with self.subTest(now=now_dt.time()):
                activation = self._activation(config, state, now_dt)
                self.assertEqual(expected_phase, activation["projection_phase"])

    def test_scheduled_without_individual_time_uses_environment_window(self) -> None:
        config = {"operation_mode": "SCHEDULED"}
        state = self._state()
        before = self._activation(
            config,
            state,
            datetime(2026, 8, 27, 8, 49, 59),
        )
        active = self._activation(
            config,
            state,
            datetime(2026, 8, 27, 8, 50, 0),
        )

        self.assertEqual("PRE_OPERATION_BOUNDARY", before["projection_phase"])
        self.assertEqual("ACTIVE_SESSION", active["projection_phase"])
        self.assertEqual("08:50:00", active["trade_window_start"])

    def test_pre_market_ats_is_active_then_returns_to_pre_operation_waiting(self) -> None:
        config = {
            "operation_mode": "CONTINUOUS",
            "start_time": "09:30:00",
            "end_buy_time": "13:30:00",
        }
        state = self._state(sessions=("extra1",), method="MARKET")
        cases = (
            (datetime(2026, 8, 27, 7, 59, 59), "PRE_OPERATION_BOUNDARY", "감시/대기", "루틴", False),
            (datetime(2026, 8, 27, 8, 0, 0), "ACTIVE_SESSION", "매수/매도", "시장가", True),
            (datetime(2026, 8, 27, 8, 49, 59), "ACTIVE_SESSION", "매수/매도", "시장가", True),
            (datetime(2026, 8, 27, 8, 50, 0), "PRE_OPERATION_BOUNDARY", "감시/대기", "루틴", False),
            (datetime(2026, 8, 27, 8, 59, 59), "PRE_OPERATION_BOUNDARY", "감시/대기", "루틴", False),
            (datetime(2026, 8, 27, 9, 0, 0), "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY", "감시/대기", "루틴", True),
        )
        for now_dt, phase, status, method, method_active in cases:
            with self.subTest(now=now_dt.time()):
                row = self._row(config, state, now_dt)
                self.assert_row(
                    row,
                    phase=phase,
                    status=status,
                    method=method,
                    method_active=method_active,
                    liquidation="-" if phase == "ACTIVE_SESSION" else "5분/시장가",
                    liquidation_active=(method_active and phase != "ACTIVE_SESSION"),
                )

    def test_post_market_gap_and_ats_boundaries_are_symmetric(self) -> None:
        config = {
            "operation_mode": "CONTINUOUS",
            "start_time": "09:30:00",
            "end_buy_time": "13:30:00",
        }
        state = self._state(sessions=("extra2",), method="CURRENT_PRICE")
        cases = (
            (datetime(2026, 8, 27, 15, 19, 59), "ACTIVE_SESSION", "매수/매도", "루틴", True),
            (datetime(2026, 8, 27, 15, 20, 0), "INTER_SESSION_NON_TRADING_GAP", "감시/대기", "루틴", False),
            (datetime(2026, 8, 27, 15, 39, 59), "INTER_SESSION_NON_TRADING_GAP", "감시/대기", "루틴", False),
            (datetime(2026, 8, 27, 15, 40, 0), "ACTIVE_SESSION", "매수/매도", "현재가", True),
            (datetime(2026, 8, 27, 19, 50, 0), "FINAL_END", "감시/대기", "루틴", False),
        )
        for now_dt, phase, status, method, method_active in cases:
            with self.subTest(now=now_dt.time()):
                row = self._row(config, state, now_dt)
                self.assertEqual(phase, row["projection_phase"])
                self.assertEqual(status, row["display_status"])
                self.assertEqual(method, row["method_text"])
                self.assertIs(row["method_cell_active"], method_active)
                if method in {"시장가", "현재가"}:
                    self.assertEqual("-", row["liquidation_text"])

    def test_multiple_ats_sessions_and_all_execution_methods(self) -> None:
        config = {
            "operation_mode": "CONTINUOUS",
            "start_time": "09:30:00",
            "end_buy_time": "13:30:00",
        }
        for method, expected in (
            ("ROUTINE", "루틴"),
            ("MARKET", "시장가"),
            ("CURRENT_PRICE", "현재가"),
        ):
            state = self._state(sessions=("extra1", "extra2"), method=method)
            for now_dt in (
                datetime(2026, 8, 27, 8, 10),
                datetime(2026, 8, 27, 16, 0),
            ):
                with self.subTest(method=method, now=now_dt.time()):
                    row = self._row(config, state, now_dt)
                    self.assertEqual("ACTIVE_SESSION", row["projection_phase"])
                    self.assertEqual(expected, row["method_text"])
                    self.assertIs(row["method_cell_active"], True)
                    self.assertEqual("-", row["liquidation_text"])

    def test_order_time_guard_uses_the_same_boundaries(self) -> None:
        config = {
            "operation_mode": "CONTINUOUS",
            "start_time": "09:30:00",
            "end_buy_time": "13:30:00",
        }
        state = self._state(sessions=("extra1",))
        with (
            patch.object(ats_utils, "read_operation_policy", return_value=deepcopy(self.operation_policy)),
            patch.object(order_permission, "in_manual_trading_session", return_value=True),
            patch.object(order_permission, "manual_ats_active_now", return_value=False),
        ):
            before = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=datetime(2026, 8, 27, 9, 29, 59),
            )
            active = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=datetime(2026, 8, 27, 9, 30, 0),
            )
        self.assertIs(before["active"], False)
        self.assertEqual("OUTSIDE_OPERATION_TIME", before["reason"])
        self.assertIs(active["active"], True)

        with (
            patch.object(ats_utils, "read_operation_policy", return_value=deepcopy(self.operation_policy)),
            patch.object(order_permission, "in_manual_trading_session", return_value=False),
            patch.object(order_permission, "manual_ats_active_now", return_value=True),
        ):
            ats_active = order_permission.canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=datetime(2026, 8, 27, 8, 0, 0),
            )
        self.assertIs(ats_active["active"], True)
        self.assertEqual("ACTIVE_ATS", ats_active["reason"])


if __name__ == "__main__":
    unittest.main()
