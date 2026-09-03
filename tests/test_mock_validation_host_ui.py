# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_auto_trade_context_menu as common_menu
import gui_main_stock_context_menu as main_context_menu
import gui_main_table_loader as main_table_loader
from gui_auto_trade_context_menu import StockContextMenuCallbacks
from gui_windows import MainWindow
from PyQt5.QtWidgets import QApplication, QPushButton
from mock_validation_contract import MockValidationError
from mock_validation_host import MockValidationHost
from mock_validation_reference_snapshot import build_mock_reference_snapshot
from mock_validation_ui_actions import MockValidationUIActions
from mock_validation_ui_projection import (
    MockEventReaderAdapter,
    current_mock_projections,
    mock_badge_count,
)
from tests.test_mock_validation_market_data import _book, _trade_payload


SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=SEOUL)


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.callbacks.remove(callback)

    def emit(self, payload=None) -> None:
        for callback in tuple(self.callbacks):
            callback(payload)


class _Api:
    def __init__(self) -> None:
        self.mock_orderbook_received = _Signal()
        self.realtime_shadow_tick_received = _Signal()
        self.login_state_changed = _Signal()
        self.targets = ()
        self.sync_calls = []
        self.clear_calls = []
        self.registration = SimpleNamespace(
            active=True,
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=(),
        )

    def sync_mock_orderbook_registration(self, stock_codes):
        self.targets = tuple(sorted(set(stock_codes)))
        self.sync_calls.append(self.targets)
        self.registration = SimpleNamespace(
            active=bool(self.targets),
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=self.targets,
        )
        return {"ok": True, "active": bool(self.targets), "snapshot": self.registration}

    def mock_orderbook_registration_snapshot(self):
        return self.registration

    def clear_mock_orderbook_registration(self, **kwargs):
        self.clear_calls.append(kwargs)
        self.targets = ()
        self.registration = SimpleNamespace(
            active=False,
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=(),
        )
        return {"ok": True}


class _MenuAction:
    def __init__(self, text="") -> None:
        self._text = text
        self._enabled = True
        self._checkable = False
        self._checked = False

    def text(self):
        return self._text

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)

    def isEnabled(self):
        return self._enabled

    def setCheckable(self, value):
        self._checkable = bool(value)

    def setChecked(self, value):
        self._checked = bool(value)

    def isChecked(self):
        return self._checked


class _Menu:
    chosen_text = ""
    root = None

    def __init__(self, _parent=None, title="") -> None:
        self.title = title
        self.actions = []
        self.submenus = []
        if not title:
            _Menu.root = self

    def setToolTipsVisible(self, _visible):
        return None

    def addMenu(self, title):
        value = _Menu(title=title)
        self.submenus.append(value)
        return value

    def addAction(self, text):
        value = _MenuAction(text)
        self.actions.append(value)
        return value

    def addSeparator(self):
        return self.addAction("<separator>")

    def exec_(self, _position):
        for menu in (self, *self.submenus):
            for action in menu.actions:
                if action.text() == self.chosen_text:
                    return action
        return None


def _reference(stock_code="005930", instance_ids=("A", "B", "C")):
    return build_mock_reference_snapshot(
        stock={
            "code": stock_code,
            "name": "삼성전자",
            "stock_path": f"stocks/{stock_code}_삼성전자",
        },
        routine_instances=[
            {
                "instance_id": instance_id,
                "definition_id": "indicator_follow",
                "routine_type": "INDICATOR_FOLLOW",
                "display_name": f"루틴 {instance_id}",
            }
            for instance_id in instance_ids
        ],
        rules_by_instance_id={
            instance_id: {"version": 1, "instance": instance_id}
            for instance_id in instance_ids
        },
        created_at=NOW.isoformat(),
    )


class MockValidationHostUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project_root = Path(self.temporary.name) / "project"
        self.project_root.mkdir()
        self.api = _Api()
        self.clock = {"now": NOW}
        self.changed = Mock()
        self.host = MockValidationHost(
            self.api,
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            projection_changed=self.changed,
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
                "review_policy": {"long_term_holding_enabled": False},
            },
            candles_provider=lambda _document: [],
        )
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)

    def create(self, stock_code="005930"):
        return self.actions.create_waiting_session(_reference(stock_code))

    def test_host_connects_registers_unique_stock_and_disposes_only_mock_stream(self):
        self.assertEqual(1, len(self.api.mock_orderbook_received.callbacks))
        self.assertEqual(1, len(self.api.realtime_shadow_tick_received.callbacks))
        self.create()
        self.assertEqual(("005930",), self.api.targets)
        self.assertEqual(1, mock_badge_count(self.host.repository))
        self.host.dispose()
        self.assertEqual([], self.api.mock_orderbook_received.callbacks)
        self.assertEqual([], self.api.realtime_shadow_tick_received.callbacks)
        self.assertEqual(1, len(self.api.clear_calls))

    def test_waiting_buffers_every_tick_then_drains_without_routine_evaluation(self):
        self.create()
        self.host.routine_adapter.evaluate_cycle = Mock()
        for sequence in (1, 2, 3):
            payload = {
                **_trade_payload(sequence=sequence),
                "received_at": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "market_datetime": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "trade_volume_raw": -sequence,
                "trade_volume_abs": sequence,
            }
            self.assertTrue(self.host.accept_trade(payload))
        buffered = self.host.buffered_evidence("005930")
        self.assertEqual((1, 2, 3), tuple(item.market_sequence for item in buffered))
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual((), self.host.buffered_evidence("005930"))
        self.host.routine_adapter.evaluate_cycle.assert_not_called()

    def test_running_resting_order_receives_all_trade_ticks_in_order_once(self):
        self.create()
        self.host.start_stock_operation("005930", as_of=NOW)
        book = _book()
        self.assertTrue(self.host.accept_orderbook(book))
        policy = self.host._policy()
        submitted = self.host.engine.submit_order(
            self.host.current_session("005930")["session"]["validation_session_id"],
            routine_instance_id="A",
            side="BUY",
            order_type="LIMIT",
            requested_qty=10,
            limit_price=99,
            market=self.host.market_store.market_snapshot("005930"),
            policy=policy,
            execution_budget=100000,
            command_id="MC-resting-order",
        )
        self.assertEqual("OPEN", submitted["order"]["state"])
        calls = []
        original = self.host.engine.process_trade

        def recording(*args, **kwargs):
            calls.append(kwargs["trade"].receive_sequence)
            return original(*args, **kwargs)

        self.host.engine.process_trade = recording
        self.host.routine_adapter.evaluate_cycle = Mock()
        for sequence in (1, 2, 3):
            payload = {
                **_trade_payload(sequence=sequence),
                "current_price": 99,
                "received_at": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "market_datetime": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "trade_volume_raw": -1,
                "trade_volume_abs": 1,
            }
            self.host.accept_trade(payload)
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual([1, 2, 3], calls)
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual([1, 2, 3], calls)

    def test_sequence_conflict_stops_only_culprit_stock_for_mock_review(self):
        self.create()
        self.actions.create_waiting_session(_reference("000660", ("A",)))
        first = _trade_payload(sequence=1)
        self.assertTrue(self.host.accept_trade(first))
        self.assertFalse(self.host.accept_trade({**first, "current_price": 101}))
        result = self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        states = {
            row["stock_code"]: row["state"]
            for row in current_mock_projections(self.host.repository)
        }
        self.assertEqual("REVIEW_STOPPED", states["005930"])
        self.assertEqual("WAITING", states["000660"])
        self.assertEqual(2, result["processed"])

    def test_corrupt_session_read_does_not_stop_another_stock_cycle(self):
        first = self.create()
        self.actions.create_waiting_session(_reference("000660", ("A",)))
        corrupt_id = first["document"]["session"]["validation_session_id"]
        path = self.host.repository.root / "runtime" / "sessions" / f"{corrupt_id}.json"
        path.write_text("{broken", encoding="utf-8")
        original = self.host._process_stock
        processed = []

        def recording(document, now):
            processed.append(document["session"]["stock_code"])
            return original(document, now)

        self.host._process_stock = recording
        result = self.host.process_due_cycles(as_of=NOW)
        self.assertEqual(["000660"], processed)
        self.assertEqual("005930", result["errors"][0][0])

    def test_reentry_is_blocked_and_projection_refreshes_only_on_change(self):
        self.create()
        self.host._processing = True
        self.assertEqual(
            "MOCK_HOST_REENTRY_BLOCKED",
            self.host.process_due_cycles()["reason"],
        )
        self.host._processing = False
        self.changed.reset_mock()
        self.host.process_due_cycles(as_of=NOW)
        self.host.process_due_cycles(as_of=NOW)
        self.changed.assert_not_called()

    def test_review_state_discards_transport_backlog_without_execution_progression(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="B",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-review",
        )
        self.host.engine.process_trade = Mock()
        self.host.accept_trade({
            **_trade_payload(sequence=1),
            "trade_volume_raw": -1,
            "trade_volume_abs": 1,
        })
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual((), self.host.buffered_evidence("005930"))
        self.host.engine.process_trade.assert_not_called()

    def test_tax_is_waiting_only_and_is_frozen_in_operation_snapshot(self):
        self.create()
        self.actions.set_tax("005930", False)
        document = self.host.current_session("005930")
        self.assertFalse(document["session"]["mock_tax_enabled"])
        self.actions.start("005930")
        operation = self.host.current_session("005930")["mock_operation_lifecycle"]["current"]
        self.assertFalse(operation["operation_policy_snapshot"]["mock_tax_enabled"])
        with self.assertRaisesRegex(MockValidationError, "MOCK_TAX_CHANGE_REQUIRES_WAITING"):
            self.actions.set_tax("005930", True)

    def test_auto_close_due_is_idempotent_and_stops_routine_progression(self):
        self.create()
        self.actions.start("005930")
        self.host.routine_adapter.evaluate_cycle = Mock()
        due = NOW.replace(hour=15, minute=25, second=0)
        self.host.process_due_cycles(as_of=due)
        document = self.host.current_session("005930")
        self.assertEqual("CLOSING", document["session"]["state"])
        self.assertEqual("AUTO", document["mock_operation_lifecycle"]["current"]["close_source"])
        called = self.host.routine_adapter.evaluate_cycle.call_count
        self.host.process_due_cycles(as_of=due + timedelta(seconds=1))
        self.assertEqual(called, self.host.routine_adapter.evaluate_cycle.call_count)

    def test_return_saga_preserves_current_on_failure_and_archives_after_success(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        failed = self.actions.return_to_production(
            "005930",
            destination="WAITING",
            preflight=lambda: SimpleNamespace(allowed=True),
            execute=lambda: SimpleNamespace(ok=False),
        )
        self.assertFalse(failed["ok"])
        self.assertIsNotNone(self.host.current_session("005930"))
        event_types = [item["event_type"] for item in self.host.repository.read_events(session_id)]
        self.assertEqual(["RETURN_REQUESTED", "RETURN_FAILED"], event_types[-2:])

        completed = self.actions.return_to_production(
            "005930",
            destination="WAITING",
            preflight=lambda: SimpleNamespace(allowed=True),
            execute=lambda: SimpleNamespace(ok=True),
        )
        self.assertTrue(completed["ok"])
        self.assertIsNone(self.host.current_session("005930"))
        history = self.host.repository.read_history(session_id)
        self.assertEqual("ENDED", history["session_document"]["session"]["state"])
        event_types = [item["event_type"] for item in self.host.repository.read_events(session_id)]
        self.assertEqual("RETURN_COMPLETED", event_types[-1])

    def test_event_reader_uses_mock_journal_only(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        result = MockEventReaderAdapter(self.host.repository, session_id).read_events()
        self.assertEqual(1, result["count"])
        self.assertEqual("MOCK", result["events"][0]["category"])
        self.assertEqual("SESSION_CREATED", result["events"][0]["event_type"])

    def test_current_mock_context_exposes_only_mock_submenu_and_dispatches_statefully(self):
        called = Mock()
        callbacks = StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
            mock_actions=lambda: {
                "current": True,
                "can_start": True,
                "can_early_close": False,
                "can_immediate": False,
                "can_tax": True,
                "can_reset": False,
                "can_return": True,
                "tax_enabled": True,
                "start": called,
            },
        )
        _Menu.chosen_text = "운영시작"
        with patch.object(common_menu, "QMenu", _Menu):
            common_menu.show_monitor_stock_context_menu(
                object(),
                object(),
                has_selection=True,
                callbacks=callbacks,
            )
        self.assertEqual(["모의검증"], [menu.title for menu in _Menu.root.submenus])
        self.assertEqual([], _Menu.root.actions)
        called.assert_called_once_with()

    def test_mock_membership_is_an_overlay_and_excludes_normal_projection(self):
        stock = {
            "stock_dir": str(self.project_root / "stocks" / "005930_삼성전자"),
            "stock_path": "stocks/005930_삼성전자",
            "instance_id": "A",
            "operation_excluded": False,
            "code": "005930",
            "name": "삼성전자",
        }
        window = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=lambda: frozenset({"005930"})
            )
        )
        inspection = SimpleNamespace(state={"status": "STOPPED"}, review_required=False)
        with patch.object(main_table_loader, "inspect_review_state_data", return_value=inspection):
            counts = main_table_loader._instance_stock_counts(
                window=window,
                static_data={"stocks": (stock,)},
                state_by_stock_dir={stock["stock_dir"]: inspection.state},
            )
        self.assertEqual({}, counts)
        self.assertEqual("A", stock["instance_id"])
        self.assertFalse(stock["operation_excluded"])

    def test_mock_entry_guard_blocks_only_production_active_or_review_state(self):
        target = SimpleNamespace(
            stock_dir=self.project_root / "stocks" / "005930_삼성전자",
            code="005930",
        )
        target.stock_dir.mkdir(parents=True)
        target.stock_dir.joinpath("state.json").write_text("{}", encoding="utf-8")
        clear = SimpleNamespace(review_required=False)
        review = SimpleNamespace(review_required=True)
        with patch.object(main_context_menu, "inspect_stock_review_state", return_value=review):
            self.assertFalse(main_context_menu._mock_entry_allowed(object(), target)[0])
        with (
            patch.object(main_context_menu, "inspect_stock_review_state", return_value=clear),
            patch.object(main_context_menu, "auto_trade_setting_trade_started", return_value=True),
            patch.object(
                main_context_menu,
                "auto_trade_setting_current_session_trade_started",
                return_value=True,
            ),
        ):
            self.assertFalse(main_context_menu._mock_entry_allowed(object(), target)[0])
        with (
            patch.object(main_context_menu, "inspect_stock_review_state", return_value=clear),
            patch.object(main_context_menu, "auto_trade_setting_trade_started", return_value=False),
            patch.object(
                main_context_menu,
                "auto_trade_setting_current_session_trade_started",
                return_value=False,
            ),
        ):
            self.assertTrue(main_context_menu._mock_entry_allowed(object(), target)[0])

    def test_mock_scope_disables_production_bottom_buttons_and_restores_via_owners(self):
        host = SimpleNamespace(
            _main_routine_stock_scope="mock",
            btn_start=QPushButton(),
            btn_main_visible_early_close=QPushButton(),
            btn_emergency_stop=QPushButton(),
            update_global_operation_button_state=Mock(),
            update_emergency_button_state=Mock(),
            _visible_monitoring_early_close_targets=lambda: [object()],
        )
        for button in (
            host.btn_start,
            host.btn_main_visible_early_close,
            host.btn_emergency_stop,
        ):
            button.setEnabled(True)
            button.setToolTip("original")
        MainWindow._apply_mock_scope_button_isolation(host)
        self.assertTrue(all(not button.isEnabled() for button in (
            host.btn_start,
            host.btn_main_visible_early_close,
            host.btn_emergency_stop,
        )))
        self.assertEqual(
            {"모의 종목은 우클릭 모의 메뉴에서 조작합니다."},
            {button.toolTip() for button in (
                host.btn_start,
                host.btn_main_visible_early_close,
                host.btn_emergency_stop,
            )},
        )
        host._main_routine_stock_scope = "all"
        MainWindow._apply_mock_scope_button_isolation(host)
        host.update_global_operation_button_state.assert_called_once_with()
        host.update_emergency_button_state.assert_called_once_with()
        self.assertTrue(host.btn_main_visible_early_close.isEnabled())
        self.assertEqual("original", host.btn_start.toolTip())


if __name__ == "__main__":
    unittest.main()
