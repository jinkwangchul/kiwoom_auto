from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from execution_universe import NOT_CURRENT_SESSION_PARTICIPANT, project_execution_universe
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    auto_trade_register_current_session_operation_participants,
    auto_trade_retire_current_session_operation_participants,
    auto_trade_stock_operation_category,
)
import gui_auto_trade_run_control as run_control
import routine_signal_order_bridge
import routine_signal_consumer
import gui_auto_trade_timer
from tests.participant_owner_fixture import (
    attach_participant_owner,
    participant_codes,
    participant_owner,
)


TODAY = "2026-08-26"


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _runtime_files(root: Path) -> tuple[Path, Path, Path]:
    queue = root / "order_queue.json"
    executions = root / "order_executions.json"
    locks = root / "order_locks.json"
    _write_json(queue, {"version": 1, "revision": 0, "orders": []})
    _write_json(executions, {"version": 1, "executions": []})
    _write_json(locks, {"version": 1, "locks": []})
    return queue, executions, locks


def _stock(
    root: Path,
    code: str,
    *,
    config: dict[str, object] | None = None,
    state: dict[str, object] | None = None,
) -> Path:
    stock_dir = root / f"{code}_Stock"
    stock_dir.mkdir()
    _write_json(
        stock_dir / "config.json",
        config
        or {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
            "operation_excluded": False,
        },
    )
    _write_json(
        stock_dir / "state.json",
        state
        or {
            "status": "RUNNING",
            "trade_enabled": True,
            "trade_started_at": f"{TODAY} 09:00:00",
            "holding_qty": 0,
            "avg_price": 0,
            "review_required": False,
        },
    )
    _write_json(stock_dir / "orders.json", {"orders": []})
    return stock_dir


def _operation_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "operation_date": TODAY,
        "operation_status": "RUNNING",
        "operation_participant_stock_codes": ["012210"],
    }
    state.update(updates)
    return state


def _manual_policy() -> dict[str, object]:
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "manual_operation": {"use_regular_market": True},
    }


def _extra_session(key: str) -> dict[str, object]:
    if key == "extra2":
        return {
            "enabled": True,
            "start_time": "15:40:00",
            "end_time": "19:50:00",
        }
    return {}


class ParticipantWriterTests(unittest.TestCase):
    def test_retirement_is_canonical_idempotent_and_process_local(self) -> None:
        owner = SimpleNamespace()
        setting = SimpleNamespace(_owner=owner)
        host = participant_owner()
        owner.auto_trade_setting_window = setting
        owner._main_monitoring_auto_trade_operation_host = host

        auto_trade_register_current_session_operation_participants(
            setting, [" 012210 ", "A12345"]
        )
        first = auto_trade_retire_current_session_operation_participants(
            host, ["012210", "missing"]
        )
        second = auto_trade_retire_current_session_operation_participants(
            owner, ["012210"]
        )

        self.assertEqual(("012210", "A12345"), first["before"])
        self.assertEqual(("012210", "MISSING"), first["requested"])
        self.assertEqual(("012210",), first["removed"])
        self.assertEqual(("A12345",), first["remaining"])
        self.assertEqual((), second["removed"])
        for context in (owner, setting, host):
            self.assertEqual(
                ("A12345",),
                auto_trade_current_session_operation_participant_codes(context),
            )


class FinalSessionEvaluatorTests(unittest.TestCase):
    def _continuous_phase(self, hour: int, minute: int) -> dict[str, object]:
        config = {"operation_mode": "CONTINUOUS"}
        state = {
            "manual_ats_selection": {"selected_sessions": ["extra2"]},
        }
        with patch.object(
            run_control, "read_operation_policy", return_value=_manual_policy()
        ), patch.object(
            run_control,
            "manual_ats_session_definition",
            side_effect=_extra_session,
        ):
            return run_control.auto_trade_final_session_phase(
                config,
                state,
                now_dt=datetime(2026, 8, 26, hour, minute),
            )

    def test_before_between_active_and_final_selected_sessions(self) -> None:
        self.assertEqual("BEFORE_FIRST_SESSION", self._continuous_phase(8, 0)["phase"])
        self.assertEqual("BETWEEN_SESSIONS", self._continuous_phase(15, 30)["phase"])
        self.assertEqual("ACTIVE_SESSION", self._continuous_phase(16, 0)["phase"])
        final = self._continuous_phase(19, 51)
        self.assertEqual("FINAL_SESSION_ENDED", final["phase"])
        self.assertTrue(final["final_session_ended"])

    def test_scheduled_final_end_is_candidate(self) -> None:
        phase = run_control.auto_trade_final_session_phase(
            {
                "operation_mode": "SCHEDULED",
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            {},
            now_dt=datetime(2026, 8, 26, 13, 31),
        )
        self.assertEqual("FINAL_SESSION_ENDED", phase["phase"])


class RetirementEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stock_dir = _stock(self.root, "012210")
        self.queue, self.executions, self.locks = _runtime_files(self.root)
        self.config = json.loads((self.stock_dir / "config.json").read_text())
        self.state = json.loads((self.stock_dir / "state.json").read_text())
        self.now = datetime(2026, 8, 26, 13, 31)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def evaluate(
        self,
        *,
        state: dict[str, object] | None = None,
        operation_state: dict[str, object] | None = None,
        close_completion_status: str = "",
    ) -> dict[str, object]:
        return run_control.auto_trade_time_end_retirement_eligibility(
            stock_dir=self.stock_dir,
            stock_code="012210",
            config=self.config,
            state=state or self.state,
            operation_state=operation_state or _operation_state(),
            now_dt=self.now,
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            order_locks_path=self.locks,
            close_completion_status=close_completion_status,
        )

    def test_obligation_free_final_time_is_eligible(self) -> None:
        self.assertTrue(self.evaluate()["eligible"])

    def test_same_day_restart_can_retire_while_global_ledger_is_normal_ended(self) -> None:
        result = self.evaluate(
            operation_state=_operation_state(operation_status="NORMAL_ENDED")
        )
        self.assertTrue(result["eligible"])

    def test_queue_and_execution_obligations_block(self) -> None:
        _write_json(
            self.queue,
            {
                "version": 1,
                "revision": 1,
                "orders": [
                    {
                        "stock_code": "012210",
                        "status": "ORDER_QUEUED",
                        "quantity": 1,
                    }
                ],
            },
        )
        self.assertIn("PENDING_ORDER", self.evaluate()["blockers"])

        _write_json(self.queue, {"version": 1, "revision": 2, "orders": []})
        _write_json(
            self.executions,
            {
                "version": 1,
                "executions": [
                    {"stock_code": "012210", "status": "DISPATCH_CLAIMED"}
                ],
            },
        )
        self.assertIn("UNRESOLVED_EXECUTION", self.evaluate()["blockers"])

    def test_close_review_emergency_and_ambiguous_session_block(self) -> None:
        close_state = dict(self.state, status="LIQUIDATING")
        self.assertIn("CLOSE_LIQUIDATION_ACTIVE", self.evaluate(state=close_state)["blockers"])

        review_state = dict(self.state, review_required=True)
        self.assertIn("REVIEW_REQUIRED", self.evaluate(state=review_state)["blockers"])

        emergency_state = _operation_state(emergency_stop=True)
        self.assertIn("GLOBAL_EMERGENCY_STOP", self.evaluate(operation_state=emergency_state)["blockers"])

        ambiguous_state = _operation_state(operation_date="2026-08-25")
        self.assertIn(
            "OPERATION_SESSION_EVIDENCE_UNRESOLVED",
            self.evaluate(operation_state=ambiguous_state)["blockers"],
        )

    def test_zero_holding_auto_close_done_is_eligible(self) -> None:
        state = dict(
            self.state,
            status="AUTO_CLOSE",
            auto_close_requested_at=f"{TODAY} 13:30:00",
        )
        result = self.evaluate(state=state, close_completion_status="DONE")

        self.assertTrue(result["eligible"])
        self.assertTrue(result["close_completion_done"])
        self.assertNotIn("CLOSE_LIQUIDATION_ACTIVE", result["blockers"])

    def test_auto_close_done_does_not_relax_other_safety_blockers(self) -> None:
        close_state = dict(
            self.state,
            status="AUTO_CLOSE",
            auto_close_requested_at=f"{TODAY} 13:30:00",
        )
        holding = self.evaluate(
            state=dict(close_state, holding_qty=1),
            close_completion_status="DONE",
        )
        self.assertFalse(holding["eligible"])
        self.assertIn("HOLDING_OR_POSITION_UNRESOLVED", holding["blockers"])

        _write_json(
            self.queue,
            {
                "version": 1,
                "revision": 1,
                "orders": [
                    {
                        "stock_code": "012210",
                        "status": "ORDER_QUEUED",
                        "quantity": 1,
                    }
                ],
            },
        )
        pending = self.evaluate(
            state=close_state,
            close_completion_status="DONE",
        )
        self.assertFalse(pending["eligible"])
        self.assertIn("PENDING_ORDER", pending["blockers"])

        _write_json(self.queue, {"version": 1, "revision": 2, "orders": []})
        not_done = self.evaluate(
            state=close_state,
            close_completion_status="HOLDING_REMAINS",
        )
        self.assertFalse(not_done["eligible"])
        self.assertIn("CLOSE_LIQUIDATION_ACTIVE", not_done["blockers"])

        review = self.evaluate(
            state=dict(close_state, review_required=True),
            close_completion_status="DONE",
        )
        self.assertIn("REVIEW_REQUIRED", review["blockers"])

        emergency = self.evaluate(
            state=close_state,
            operation_state=_operation_state(emergency_stop=True),
            close_completion_status="DONE",
        )
        self.assertIn("GLOBAL_EMERGENCY_STOP", emergency["blockers"])


class RetirementServiceAndHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue, self.executions, self.locks = _runtime_files(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run_service(
        self,
        window: object,
        targets: list[tuple[Path, str, str]],
        now: datetime,
        *,
        operation_state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        session_state = operation_state or _operation_state()
        _write_json(self.root / "operation_state.json", session_state)
        with patch.object(
            run_control,
            "auto_trade_registered_operation_targets",
            return_value=targets,
        ), patch.object(
            run_control, "read_operation_state", return_value=session_state
        ), patch.object(
            run_control, "read_operation_policy", return_value=_manual_policy()
        ), patch.object(
            run_control,
            "manual_ats_session_definition",
            side_effect=_extra_session,
        ):
            return run_control.auto_trade_retire_time_ended_current_session_participants(
                window,
                now_dt=now,
                order_queue_path=self.queue,
                order_executions_path=self.executions,
                order_locks_path=self.locks,
            )

    def test_scheduled_done_stocks_retire_while_continuous_stock_remains(self) -> None:
        scheduled_codes = ("002810", "005070", "063440", "130500")
        targets: list[tuple[Path, str, str]] = []
        for code in scheduled_codes:
            stock_dir = _stock(
                self.root,
                code,
                state={
                    "status": "AUTO_CLOSE",
                    "trade_enabled": True,
                    "trade_started_at": f"{TODAY} 09:00:00",
                    "holding_qty": 0,
                    "avg_price": 0,
                    "review_required": False,
                    "auto_close_requested_at": f"{TODAY} 13:30:00",
                },
            )
            targets.append((stock_dir, code, code))
        continuous = _stock(
            self.root,
            "012210",
            config={"operation_mode": "CONTINUOUS", "operation_excluded": False},
            state={
                "status": "MONITORING",
                "trade_enabled": True,
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 0,
                "avg_price": 0,
                "review_required": False,
            },
        )
        targets.append((continuous, "012210", "012210"))
        all_codes = {*scheduled_codes, "012210"}
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(all_codes),
            startup_recovery_session_ready=lambda refresh=False: True,
        )
        operation_state = _operation_state(
            operation_status="CLOSING",
            operation_participant_stock_codes=sorted(all_codes),
        )

        result = self._run_service(
            window,
            targets,
            datetime(2026, 8, 26, 13, 31),
            operation_state=operation_state,
        )

        self.assertEqual(tuple(sorted(scheduled_codes)), result["removed"])
        self.assertEqual(("012210",), result["remaining"])
        statuses = {
            item["stock_code"]: item["close_completion_status"]
            for item in result["evaluations"]
        }
        self.assertTrue(all(statuses[code] == "DONE" for code in scheduled_codes))
        self.assertEqual("CLOSE_NOT_STARTED", statuses["012210"])

        category_by_code = {
            code: auto_trade_stock_operation_category(
                window,
                stock_code=code,
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
            )
            for code in all_codes
        }
        self.assertEqual("operation", category_by_code["012210"])
        self.assertTrue(
            all(category_by_code[code] == "waiting" for code in scheduled_codes)
        )

    def test_012210_final_ats_retirement_preserves_persisted_ledger(self) -> None:
        stock_dir = _stock(
            self.root,
            "012210",
            config={"operation_mode": "CONTINUOUS", "operation_excluded": False},
            state={
                "status": "MONITORING",
                "trade_enabled": True,
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 0,
                "avg_price": 0,
                "review_required": False,
                "manual_ats_selection": {"selected_sessions": ["extra2"]},
            },
        )
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"012210"}),
            startup_recovery_session_ready=lambda refresh=False: True,
        )
        ledger = self.root / "operation_state.json"
        _write_json(ledger, _operation_state())
        before_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()

        result = self._run_service(
            window,
            [(stock_dir, "012210", "Stock")],
            datetime(2026, 8, 26, 19, 51),
        )
        snapshot = project_execution_universe(window, stock_dirs=[stock_dir])

        self.assertEqual(("012210",), result["removed"])
        self.assertEqual((), result["remaining"])
        self.assertFalse(result["persisted_operation_state_changed"])
        self.assertEqual(before_hash, hashlib.sha256(ledger.read_bytes()).hexdigest())
        self.assertFalse(snapshot.entries[0].execution_member)
        self.assertFalse(snapshot.entries[0].execution_ready)
        self.assertIn(NOT_CURRENT_SESSION_PARTICIPANT, snapshot.entries[0].blockers)

    def test_two_stock_partial_retirement_keeps_future_ats_participant(self) -> None:
        ended = _stock(self.root, "012210")
        future = _stock(
            self.root,
            "063440",
            config={"operation_mode": "CONTINUOUS", "operation_excluded": False},
            state={
                "status": "RUNNING",
                "trade_enabled": True,
                "trade_started_at": f"{TODAY} 09:00:00",
                "holding_qty": 0,
                "avg_price": 0,
                "manual_ats_selection": {"selected_sessions": ["extra2"]},
            },
        )
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(
                {"012210", "063440"}
            )
        )
        result = self._run_service(
            window,
            [(ended, "012210", "Ended"), (future, "063440", "Future")],
            datetime(2026, 8, 26, 15, 30),
        )
        self.assertEqual(("012210",), result["removed"])
        self.assertEqual(("063440",), result["remaining"])

    def test_same_day_explicit_restart_readds_retired_participant(self) -> None:
        window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"012210"})
        )
        auto_trade_retire_current_session_operation_participants(window, ["012210"])
        added = auto_trade_register_current_session_operation_participants(
            window, ["012210"]
        )
        self.assertEqual(("012210",), added)
        self.assertEqual(
            ("012210",),
            auto_trade_current_session_operation_participant_codes(window),
        )

    def test_host_syncs_execution_shadow_only_and_stops_last_timer(self) -> None:
        snapshot = SimpleNamespace(execution_stock_codes=())
        host = SimpleNamespace(
            sync_realtime_shadow_targets=Mock(
                return_value={"ok": True, "active": False, "changed": True}
            ),
            stop_operation_timers=Mock(
                return_value={"stopped": True, "stopped_count": 1}
            ),
            sync_monitoring_universe_for_current_session=Mock(),
        )
        service_result = {
            "before": ("012210",),
            "requested": ("012210",),
            "removed": ("012210",),
            "remaining": (),
        }
        with patch.object(
            run_control,
            "auto_trade_retire_time_ended_current_session_participants",
            return_value=service_result,
        ), patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=snapshot,
        ):
            result = AutoTradeOperationHost.retire_time_ended_current_session_participants(
                host, now_dt=datetime(2026, 8, 26, 19, 51)
            )

        host.sync_realtime_shadow_targets.assert_called_once_with(snapshot)
        host.stop_operation_timers.assert_called_once_with()
        host.sync_monitoring_universe_for_current_session.assert_not_called()
        self.assertTrue(result["operation_timer_stop_result"]["stopped"])

    def test_last_participant_shadow_failure_keeps_timer_for_empty_retry(self) -> None:
        snapshot = SimpleNamespace(execution_stock_codes=())
        host = SimpleNamespace(
            sync_realtime_shadow_targets=Mock(
                side_effect=[RuntimeError("sync failed"), {"ok": True, "active": False}]
            ),
            stop_operation_timers=Mock(
                return_value={"stopped": True, "stopped_count": 1}
            ),
        )
        first = {
            "before": ("012210",),
            "removed": ("012210",),
            "remaining": (),
        }
        retry = {"before": (), "removed": (), "remaining": ()}
        with patch.object(
            run_control,
            "auto_trade_retire_time_ended_current_session_participants",
            side_effect=[first, retry],
        ), patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=snapshot,
        ):
            failed = AutoTradeOperationHost.retire_time_ended_current_session_participants(host)
            succeeded = AutoTradeOperationHost.retire_time_ended_current_session_participants(host)

        self.assertFalse(failed["execution_shadow_sync_result"]["ok"])
        self.assertNotIn("operation_timer_stop_result", failed)
        self.assertTrue(succeeded["execution_shadow_sync_result"]["ok"])
        host.stop_operation_timers.assert_called_once_with()

    def test_operation_cycle_refreshes_ui_immediately_after_retirement(self) -> None:
        events: list[str] = []
        snapshot = SimpleNamespace(entries=(), execution_stock_codes=())
        host = Mock()
        host.startup_recovery_session_ready.return_value = True
        host._last_time_policy_minute_key = ""
        host.recalculate_all_status_by_operation_policy.return_value = {
            "changed": 1,
            "failed": 0,
        }
        host.retire_time_ended_current_session_participants.side_effect = (
            lambda **_kwargs: (
                events.append("retire")
                or {
                    "removed": ("012210",),
                    "remaining": (),
                    "execution_universe_snapshot": snapshot,
                    "execution_shadow_sync_result": {
                        "ok": True,
                        "active": False,
                    },
                }
            )
        )
        host.market_data_host = None

        with (
            patch.object(
                gui_auto_trade_timer,
                "auto_trade_current_time_policy_minute_key",
                return_value="2026-08-26 19:51",
            ),
            patch.object(
                gui_auto_trade_timer,
                "refresh_auto_trade_views",
                side_effect=lambda _owner: events.append("refresh"),
            ) as refresh_views,
            patch.object(
                gui_auto_trade_timer,
                "project_execution_universe",
                return_value=snapshot,
            ),
            patch.object(
                gui_auto_trade_timer,
                "auto_trade_continue_pending_close_liquidations",
                return_value={"processed": 0, "blocked": 0},
            ),
            patch.object(
                gui_auto_trade_timer,
                "auto_trade_continue_pending_manual_ats_liquidations",
                return_value={"processed": 0, "failed": 0},
            ),
            patch.object(
                gui_auto_trade_timer,
                "_process_pending_signal_pipeline",
                side_effect=lambda _owner: events.append("signal") or {},
            ),
            patch.object(gui_auto_trade_timer, "observe_owner_failure_transition"),
        ):
            result = gui_auto_trade_timer.auto_trade_run_operation_cycle(host)

        self.assertTrue(result["processed"])
        self.assertEqual(("012210",), result["participant_retirement_result"]["removed"])
        refresh_views.assert_called_once_with(host)
        self.assertLess(events.index("retire"), events.index("refresh"))
        self.assertLess(events.index("refresh"), events.index("signal"))


class StaleSignalReentryAuditTests(unittest.TestCase):
    def test_operation_pipeline_passes_execution_ready_cutoff_to_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "012210_Stock"
            stock_dir.mkdir()
            _write_json(
                stock_dir / "state.json",
                {"ignore_signals_before": "2026-08-26 14:00:00"},
            )
            entry = SimpleNamespace(
                stock_code="012210",
                stock_dir=stock_dir,
                execution_ready=True,
                signal_probe_only=True,
            )
            snapshot = SimpleNamespace(entries=(entry,))
            window = SimpleNamespace(statusBarMessage=Mock())
            consumer = Mock(return_value={"summary": {}})
            with patch.object(
                gui_auto_trade_timer,
                "consume_pending_routine_signals_dry_run",
                consumer,
            ), patch.object(
                gui_auto_trade_timer,
                "observe_owner_failure_transition",
            ):
                gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)

        consumer.assert_called_once_with(
            limit=5,
            mark_previewed=True,
            write_order_queue=True,
            apply_approval=True,
            allowed_stock_codes=("012210",),
            signal_cutoff_by_stock_code={
                "012210": "2026-08-26 14:00:00"
            },
        )

    def test_restart_cutoff_excludes_old_signal_and_allows_new_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            signal_path = Path(temp) / "routine_signals.json"
            _write_json(
                signal_path,
                {
                    "signals": [
                        {
                            "id": "OLD-SIGNAL",
                            "code": "012210",
                            "signal": "BUY",
                            "status": "PENDING",
                            "execution_enabled": False,
                            "created_at": "2026-08-26 12:00:00",
                        },
                        {
                            "id": "NEW-SIGNAL",
                            "code": "012210",
                            "signal": "BUY",
                            "status": "PENDING",
                            "execution_enabled": False,
                            "created_at": "2026-08-26 14:00:01",
                        }
                    ]
                },
            )
            before_hash = hashlib.sha256(signal_path.read_bytes()).hexdigest()
            with patch.object(
                routine_signal_order_bridge, "SIGNAL_QUEUE_PATH", signal_path
            ):
                retired_scope = routine_signal_order_bridge.load_pending_routine_signals(
                    allowed_stock_codes=()
                )
                restarted_scope = routine_signal_order_bridge.load_pending_routine_signals(
                    allowed_stock_codes=("012210",),
                    signal_cutoff_by_stock_code={
                        "012210": "2026-08-26 14:00:00"
                    },
                )
            after_hash = hashlib.sha256(signal_path.read_bytes()).hexdigest()

        self.assertEqual([], retired_scope)
        self.assertEqual(["NEW-SIGNAL"], [item["id"] for item in restarted_scope])
        self.assertEqual(before_hash, after_hash)

    def test_consumer_boundary_receives_only_post_restart_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            signal_path = Path(temp) / "routine_signals.json"
            _write_json(
                signal_path,
                {
                    "signals": [
                        {
                            "id": "OLD-SIGNAL",
                            "code": "012210",
                            "signal": "BUY",
                            "status": "PENDING",
                            "execution_enabled": False,
                            "created_at": "2026-08-26 12:00:00",
                        },
                        {
                            "id": "NEW-SIGNAL",
                            "code": "012210",
                            "signal": "BUY",
                            "status": "PENDING",
                            "execution_enabled": False,
                            "created_at": "2026-08-26 14:00:01",
                        },
                    ]
                },
            )
            preview = {
                "signal_id": "NEW-SIGNAL",
                "order_manager": {"ok": True, "allowed": True},
                "payload_built": True,
                "order_manager_allowed": True,
            }
            with patch.object(
                routine_signal_order_bridge, "SIGNAL_QUEUE_PATH", signal_path
            ), patch.object(
                routine_signal_consumer,
                "dry_run_order_manager_for_signal_with_payload_preview",
                return_value=preview,
            ) as processor:
                result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
                    allowed_stock_codes=("012210",),
                    signal_cutoff_by_stock_code={
                        "012210": "2026-08-26 14:00:00"
                    },
                )

        self.assertEqual(1, result["summary"]["signals_checked"])
        self.assertEqual("NEW-SIGNAL", processor.call_args.args[0]["id"])

    def test_missing_signal_time_or_cutoff_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            signal_path = Path(temp) / "routine_signals.json"
            _write_json(
                signal_path,
                {
                    "signals": [
                        {
                            "id": "UNKNOWN-TIME",
                            "code": "012210",
                            "signal": "BUY",
                            "status": "PENDING",
                            "execution_enabled": False,
                        }
                    ]
                },
            )
            with patch.object(
                routine_signal_order_bridge, "SIGNAL_QUEUE_PATH", signal_path
            ):
                missing_created_at = (
                    routine_signal_order_bridge.load_pending_routine_signals(
                        allowed_stock_codes=("012210",),
                        signal_cutoff_by_stock_code={
                            "012210": "2026-08-26 14:00:00"
                        },
                    )
                )
                missing_cutoff = routine_signal_order_bridge.load_pending_routine_signals(
                    allowed_stock_codes=("012210",),
                    signal_cutoff_by_stock_code={},
                )

        self.assertEqual([], missing_created_at)
        self.assertEqual([], missing_cutoff)


if __name__ == "__main__":
    unittest.main()
