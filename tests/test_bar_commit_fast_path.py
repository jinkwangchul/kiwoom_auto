from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QObject

from candle_manager import commit_candles
import auto_candle_refresh
from gui_auto_trade_operation_host import AutoTradeOperationHost
import routine_signal_probe
import routine_signal_queue


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []
        self.connect_count = 0

    def connect(self, callback) -> None:
        self.connect_count += 1
        self.callbacks.append(callback)

    def emit(self, payload) -> None:
        for callback in tuple(self.callbacks):
            callback(payload)


class _Api:
    def __init__(self) -> None:
        self.bar_committed = _Signal()


class _Owner(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.kiwoom_api = _Api()


def _candle(close: int = 101) -> dict[str, object]:
    return {
        "time": "20260820100000",
        "open": 100,
        "high": 102,
        "low": 99,
        "close": close,
        "volume": 1000,
    }


class BarCommitFastPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.stock_dir = Path(self.temp.name) / "005930_Test"
        commit = commit_candles(self.stock_dir, [_candle()])
        self.assertTrue(commit.ok)
        self.content_hash = commit.canonical_content_hash
        self.owner = _Owner()
        self.host = AutoTradeOperationHost(self.owner)

    def tearDown(self) -> None:
        self.host.shutdown()
        self.temp.cleanup()

    def payload(self, *, rqname: str = "rq-A", commit_identity: str = "commit-A", **changes):
        payload = {
            "event_type": "BAR_COMMITTED",
            "stock_code": "005930",
            "stock_name": "Test",
            "timeframe_minutes": 1,
            "trade_date": "2026-08-20",
            "bar_time": "2026-08-20T10:00:00+09:00",
            "bar_key": "005930:1:2026-08-20T10:00:00+09:00",
            "bar_identity": "bar-A",
            "commit_identity": commit_identity,
            "canonical_content_hash": self.content_hash,
            "canonical_path": str(self.stock_dir / "candles.json"),
            "saved_count": 1,
            "source": "opt10080",
            "rqname": rqname,
            "trcode": "opt10080",
            "connection_epoch": 1,
        }
        payload.update(changes)
        return payload

    def register(self, rqname: str = "rq-A", *, code: str = "005930", minute_key: str = "2026-08-20 10:15"):
        return self.host.register_operation_candle_request(
            rqname,
            stock_code=code,
            stock_name="Test",
            stock_dir=self.stock_dir,
            operation_cycle_minute_key=minute_key,
        )

    def ready_snapshot(self, ready: bool = True):
        return SimpleNamespace(
            entries=(SimpleNamespace(execution_ready=ready, stock_dir=self.stock_dir),)
        )

    def ready_event(self, **changes):
        event = self.payload(**changes)
        event.update(
            stock_dir=self.stock_dir,
            evaluation_tick_key="2026-08-20 10:15",
        )
        return event

    def test_signal_binding_is_exactly_once(self) -> None:
        self.assertTrue(self.host._bind_bar_committed_signal_once())
        self.assertTrue(self.host._bind_bar_committed_signal_once())
        self.assertEqual(1, self.owner.kiwoom_api.bar_committed.connect_count)

    def test_operation_owned_event_schedules_then_evaluates_one_stock(self) -> None:
        scheduled: list[object] = []
        probe = Mock(return_value={"signal": "BUY", "queue_status": "queued"})
        pipeline = Mock(return_value={"orders_created": 1})
        self.assertTrue(self.register())

        with patch("gui_auto_trade_operation_host.QTimer.singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)), patch(
            "gui_auto_trade_operation_host.StockRepository"
        ) as repository, patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=self.ready_snapshot(),
        ), patch(
            "routine_signal_probe.probe_execution_stock_for_committed_bar",
            probe,
        ), patch(
            "gui_auto_trade_timer._process_pending_signal_pipeline",
            pipeline,
        ):
            repository.return_value.resolve_stock_dir.return_value = self.stock_dir
            self.owner.kiwoom_api.bar_committed.emit(self.payload())
            self.assertEqual(1, len(scheduled))
            probe.assert_not_called()
            while scheduled:
                scheduled.pop(0)()

        probe.assert_called_once()
        self.assertEqual(self.stock_dir, probe.call_args.args[1])
        self.assertEqual("2026-08-20 10:15", probe.call_args.args[2])
        self.assertEqual("commit-A", probe.call_args.kwargs["trigger_provenance"]["trigger_commit_identity"])
        pipeline.assert_called_once()
        self.assertTrue(self.host._last_bar_commit_fast_path_result["evaluated"])

    def test_manual_mismatch_and_malformed_events_do_not_schedule(self) -> None:
        scheduled: list[object] = []
        with patch("gui_auto_trade_operation_host.QTimer.singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)):
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="manual"))
            self.register("rq-code")
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-code", stock_code="006400"))
            self.register("rq-path")
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-path", canonical_path=str(self.stock_dir / "other.json")))
            self.register("rq-bad")
            malformed = self.payload(rqname="rq-bad")
            malformed.pop("bar_identity")
            self.owner.kiwoom_api.bar_committed.emit(malformed)

        while scheduled:
            scheduled.pop(0)()
        self.assertEqual([], list(self.host._bar_commit_trigger_queue))

    def test_consecutive_duplicate_is_accepted_once_but_a_b_a_is_allowed(self) -> None:
        scheduled: list[object] = []
        with patch("gui_auto_trade_operation_host.QTimer.singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)):
            self.register("rq-1")
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-1", commit_identity="A"))
            self.register("rq-2")
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-2", commit_identity="A"))
            self.register("rq-3")
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-3", commit_identity="B"))
            self.register("rq-4")
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-4", commit_identity="A"))
            scheduled.pop(0)()

        identities = [item["commit_identity"] for item in self.host._bar_commit_trigger_queue]
        self.assertEqual(["A", "B", "A"], identities)
        self.assertEqual(1, len(scheduled))

    def test_superseded_and_not_ready_events_skip_without_probe(self) -> None:
        probe = Mock()
        repository = Mock()
        repository.resolve_stock_dir.return_value = self.stock_dir
        superseded = self.ready_event(canonical_content_hash="0" * 64)
        with patch("gui_auto_trade_operation_host.StockRepository", return_value=repository), patch(
            "routine_signal_probe.probe_execution_stock_for_committed_bar", probe
        ):
            result = self.host._process_bar_commit_trigger(superseded)
        self.assertEqual("SUPERSEDED_BAR_COMMIT", result["reason_code"])
        probe.assert_not_called()

        stopped = self.ready_event(commit_identity="stopped")
        with patch("gui_auto_trade_operation_host.StockRepository", return_value=repository), patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=self.ready_snapshot(False),
        ), patch("routine_signal_probe.probe_execution_stock_for_committed_bar", probe):
            result = self.host._process_bar_commit_trigger(stopped)
        self.assertEqual("EXECUTION_NOT_READY", result["reason_code"])
        probe.assert_not_called()

    def test_drain_error_does_not_block_next_stock_trigger(self) -> None:
        self.host._bar_commit_trigger_queue.extend(
            [
                self.ready_event(commit_identity="A"),
                self.ready_event(commit_identity="B"),
            ]
        )
        with patch.object(
            self.host,
            "_process_bar_commit_trigger",
            side_effect=[RuntimeError("A failed"), {"accepted": True, "evaluated": True, "stock_code": "005930"}],
        ), patch("gui_auto_trade_operation_host.observe_production_exception") as observe:
            self.host._drain_bar_commit_triggers()

        observe.assert_called_once()
        self.assertEqual(0, len(self.host._bar_commit_trigger_queue))
        self.assertTrue(self.host._last_bar_commit_fast_path_result["evaluated"])

    def test_two_committed_stocks_are_processed_once_each_without_other_probe(self) -> None:
        second_dir = Path(self.temp.name) / "006400_Other"
        second_commit = commit_candles(second_dir, [_candle(202)])
        self.assertTrue(second_commit.ok)
        processed: list[str] = []

        self.register("rq-A")
        self.host.register_operation_candle_request(
            "rq-B",
            stock_code="006400",
            stock_name="Other",
            stock_dir=second_dir,
            operation_cycle_minute_key="2026-08-20 10:15",
        )
        with patch("gui_auto_trade_operation_host.QTimer.singleShot"):
            self.owner.kiwoom_api.bar_committed.emit(self.payload(rqname="rq-A"))
            self.owner.kiwoom_api.bar_committed.emit(
                self.payload(
                    rqname="rq-B",
                    stock_code="006400",
                    stock_name="Other",
                    commit_identity="commit-B",
                    bar_key="006400:1:2026-08-20T10:00:00+09:00",
                    bar_identity="bar-B",
                    canonical_content_hash=second_commit.canonical_content_hash,
                    canonical_path=str(second_dir / "candles.json"),
                )
            )
            self.host.market_data_host()._drain_canonical_events()
        with patch.object(
            self.host,
            "_process_bar_commit_trigger",
            side_effect=lambda trigger: processed.append(trigger["stock_code"])
            or {
                "accepted": True,
                "evaluated": True,
                "stock_code": trigger["stock_code"],
            },
        ):
            self.host._drain_bar_commit_triggers()

        self.assertEqual(["005930", "006400"], processed)


class TriggerProvenanceTests(unittest.TestCase):
    def test_single_stock_assignment_error_keeps_existing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"trade_enabled": True, "status": "RUNNING"}),
                encoding="utf-8",
            )
            (stock_dir / "config.json").write_text("{}", encoding="utf-8")
            snapshot = SimpleNamespace(
                entries=(SimpleNamespace(execution_ready=True, stock_dir=stock_dir),)
            )
            with patch.object(routine_signal_probe, "load_routine_definitions", return_value=[]), patch.object(
                routine_signal_probe,
                "_observe_routine_contract_failure",
            ) as observe:
                result = routine_signal_probe.probe_execution_stock_for_committed_bar(
                    SimpleNamespace(),
                    stock_dir,
                    "2026-08-20 10:15",
                    execution_universe_snapshot=snapshot,
                )

            self.assertEqual("ERROR", result["signal"])
            self.assertEqual("ROUTINE_ASSIGNMENT_UNRESOLVED", result["reason"])
            self.assertEqual("ROUTINE_ASSIGNMENT_UNRESOLVED", observe.call_args.kwargs["reason_code"])

    def test_probe_adds_trigger_identity_to_context_and_signal_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(json.dumps({"trade_enabled": True, "status": "RUNNING"}), encoding="utf-8")
            (stock_dir / "config.json").write_text(json.dumps({"assigned_routine_instance_id": "instance-A"}), encoding="utf-8")
            captured_context: dict[str, object] = {}
            queued_payload: dict[str, object] = {}

            def evaluate(context):
                captured_context.update(context)
                return {"signal": "BUY", "reason": "test", "signal_index": 0}

            def enqueue(result, **_kwargs):
                queued_payload.update(result)
                return {"status": "queued", "id": "signal-A"}

            provenance = {
                "trigger_commit_identity": "commit-A",
                "trigger_bar_key": "bar-key-A",
                "trigger_bar_identity": "bar-A",
                "trigger_canonical_content_hash": "hash-A",
            }
            with patch.object(routine_signal_probe, "_load_candles_from_stock_dir", return_value=[_candle()]), patch.object(
                routine_signal_probe,
                "_load_instance_rules",
                return_value={"bar": {"bar_minutes": 1}},
            ), patch.object(
                routine_signal_probe,
                "completed_timeframe_candles",
                return_value=[dict(_candle(), timeframe_minutes=1, trade_date="2026-08-20")],
            ), patch.object(routine_signal_probe, "enqueue_routine_signal", side_effect=enqueue):
                result = routine_signal_probe.probe_routine_for_stock(
                    SimpleNamespace(evaluate=evaluate, ROUTINE_TYPE="test"),
                    "TestRoutine",
                    stock_dir,
                    "2026-08-20 10:15",
                    decision_trace_observer=None,
                    trigger_provenance=provenance,
                )

            self.assertEqual("BUY", result["signal"])
            for field, value in provenance.items():
                self.assertEqual(value, captured_context[field])
                self.assertEqual(value, queued_payload[field])

    def test_signal_queue_persists_trigger_provenance_additively(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue_path = Path(temp) / "routine_signals.json"
            with patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                result = routine_signal_queue.enqueue_routine_signal(
                    {
                        "signal": "BUY",
                        "reason": "test",
                        "trigger_commit_identity": "commit-A",
                        "trigger_bar_key": "key-A",
                        "trigger_bar_identity": "bar-A",
                        "trigger_canonical_content_hash": "hash-A",
                    },
                    routine="TestRoutine",
                    code="005930",
                    name="Test",
                    tick_key="2026-08-20 10:15",
                )

            self.assertEqual("queued", result["status"])
            record = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]
            self.assertEqual("commit-A", record["trigger_commit_identity"])
            self.assertEqual("hash-A", record["trigger_canonical_content_hash"])


class OperationCandleOwnershipTests(unittest.TestCase):
    def test_refresh_registers_returned_rqname_and_cleans_it_on_terminal_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_Test"
            stock_dir.mkdir()
            callbacks: list[object] = []

            class Api:
                @staticmethod
                def is_available():
                    return True

                @staticmethod
                def is_connected():
                    return True

                @staticmethod
                def request_minute_candles(_code, _name, **kwargs):
                    callbacks.append(kwargs["callback"])
                    return {"ok": True, "rqname": "owned-rq"}

            window = SimpleNamespace(
                parent=lambda: SimpleNamespace(kiwoom_api=Api()),
                register_operation_candle_request=Mock(return_value=True),
                complete_operation_candle_request=Mock(return_value=True),
            )
            with patch.object(
                auto_candle_refresh,
                "_refresh_targets",
                return_value=[(stock_dir, "005930", "Test")],
            ), patch.object(auto_candle_refresh.QTimer, "singleShot"):
                result = auto_candle_refresh.refresh_operation_candles(
                    window,
                    "2026-08-20 10:15",
                )
                callbacks[0]({"ok": True, "rqname": "owned-rq"})

            self.assertTrue(result["accepted"])
            window.register_operation_candle_request.assert_called_once_with(
                "owned-rq",
                stock_code="005930",
                stock_name="Test",
                stock_dir=stock_dir,
                operation_cycle_minute_key="2026-08-20 10:15",
            )
            window.complete_operation_candle_request.assert_called_once_with("owned-rq")


if __name__ == "__main__":
    unittest.main()
