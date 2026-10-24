"""SOR production signal routing; every queue and order effect stays in memory."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import routine_signal_probe
from routine_main_facts import build_routine_main_facts_from_projection
from tests.test_sor_core_m1_input import (
    NOW, _forming_host, _installed_host, _project, _probe_facts,
)


def _facts(supply, *, probe_only=False, account="12345678", holdings=None):
    original = _probe_facts(supply)
    content = {key: deepcopy(value) for key, value in original.items()
               if key not in {"revision", "captured_at", "snapshot_hash"}}
    content["selected_account_no"] = account
    content["allowed_stock_codes"] = ["005930"]
    content["holdings"] = [] if holdings is None else holdings
    content["stock_states"]["005930"]["signal_probe_only"] = probe_only
    return build_routine_main_facts_from_projection(content).to_payload()


def _run(tmp_path, supply, *, side="BUY", authorized=True, probe_only=False,
         cycle_status="resolved", account="12345678", holdings=None,
         readiness_reader=None):
    evaluated = Mock(return_value={"signal": side, "signal_index": 0})
    selected = Mock(return_value={"signal": side})
    routine = SimpleNamespace(
        ROUTINE_TYPE="isolated",
        market_bar_projection_request=lambda _rules: {"projection": supply["projection"]},
        project_cycle_context=lambda **_facts: {"status": cycle_status},
        evaluate=evaluated,
        evaluate_signal_selection=selected,
    )
    facts = _facts(supply, probe_only=probe_only, account=account, holdings=holdings)
    rules = {"bar": {"bar_minutes": supply["timeframe_minutes"]}}
    queued = []

    def enqueue(result, **kwargs):
        queued.append((deepcopy(result), kwargs))
        return {"status": "queued", "id": "memory-signal"}

    with patch.object(routine_signal_probe, "_load_instance_rules", return_value=rules), \
         patch.object(routine_signal_probe, "enqueue_routine_signal", side_effect=enqueue), \
         patch.object(routine_signal_probe, "project_candle_supply",
                      side_effect=AssertionError("no KRX fallback")), \
         patch.object(routine_signal_probe, "read_reference_price", return_value=None), \
         patch.object(routine_signal_probe, "find_library_stock_by_code", return_value=None), \
         patch.object(routine_signal_probe, "observe_owner_failure_transition"), \
         patch.object(routine_signal_probe, "_append_log"), \
         patch("event_journal_trade_observer.observe_signal_created"), \
         patch.object(routine_signal_probe, "transition_running_budget_adjustment_for_signal"):
        result = routine_signal_probe.probe_routine_for_stock(
            routine, "isolated", tmp_path / "005930_Test",
            NOW.isoformat(timespec="seconds"), decision_trace_observer=None,
            main_facts=facts, fresh_main_facts_provider=lambda: facts,
            execution_authorized=authorized,
            execution_readiness_reader=readiness_reader,
        )
    return result, evaluated, selected, queued


@pytest.mark.parametrize("side,minutes", [("BUY", 1), ("SELL", 1), ("BUY", 3)])
def test_execution_ready_native_al_signal_uses_existing_queue(tmp_path, side, minutes):
    host = _installed_host()
    if minutes == 3:
        direct = dict(host._display_timeframe_candles[("005930", 1)][0])
        direct["timeframe_minutes"] = 3
        host._display_timeframe_candles[("005930", 3)] = (direct,)
        host._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
        host._display_timeframe_candles.pop(("005930", 1))
    supply = _project(host, minutes)
    assert supply["available"] is True
    result, evaluated, selected, queued = _run(tmp_path, supply, side=side)
    assert result["signal"] == side
    assert result["queue_status"] == "queued"
    assert evaluated.call_count == 1
    assert evaluated.call_args.args[0]["candles"] == supply["candles"]
    selected.assert_not_called()
    assert len(queued) == 1
    assert queued[0][0]["signal"] == side
    assert queued[0][0]["routine_instance_id"] == "isolated"
    assert supply["calculation_only"] is True
    assert supply["queue_connected"] is False


@pytest.mark.parametrize("override", [
    {"authorized": False}, {"probe_only": True}, {"cycle_status": "unresolved"},
    {"account": ""}, {"holdings": [{"code": "005930", "holding_quantity": 1}]},
])
def test_unapproved_or_unresolved_sor_keeps_queue_empty(tmp_path, override):
    supply = _project(_installed_host(), 1)
    result, evaluated, _selected, queued = _run(tmp_path, supply, **override)
    assert result["signal"] == "SKIP"
    assert evaluated.call_count == 0
    assert queued == []


def test_mismatched_native_timeframe_never_reaches_queue(tmp_path):
    supply = _project(_installed_host(), 1)
    supply["candles"] = [dict(row, timeframe_minutes=3) for row in supply["candles"]]
    result, evaluated, _selected, queued = _run(tmp_path, supply)
    assert result["signal"] == "SKIP"
    assert evaluated.call_count == 0
    assert queued == []


def test_native_forming_m1_keeps_tick_bar_and_routes_signal(tmp_path):
    supply = _project(_forming_host(), 1, projection="FORMING_BASE_BAR")
    assert supply["available"] is True
    assert supply["candles"][-1]["is_complete"] is False
    result, evaluated, _selected, queued = _run(tmp_path, supply)
    assert result["signal"] == "BUY"
    assert result["queue_status"] == "queued"
    assert evaluated.call_args.args[0]["candles"][-1] == supply["candles"][-1]
    assert len(queued) == 1


def test_forming_m1_with_mismatched_snapshot_receipt_stays_preview_only(tmp_path):
    supply = _project(_forming_host(), 1, projection="FORMING_BASE_BAR")
    supply["sor_source_receipt"]["source_snapshot_key"] = "0" * 64
    result, evaluated, _selected, queued = _run(tmp_path, supply)
    assert result["signal"] == "SKIP"
    evaluated.assert_not_called()
    assert queued == []


def test_probe_only_pipeline_does_not_consume_or_create_order(tmp_path):
    from gui_auto_trade_timer import _process_pending_signal_pipeline

    entry = SimpleNamespace(
        stock_code="005930", stock_dir=tmp_path / "005930_Test",
        execution_ready=True, signal_probe_only=True,
    )
    snapshot = SimpleNamespace(entries=(entry,))
    with patch("gui_auto_trade_timer.consume_pending_routine_signals_dry_run") as consume, \
         patch("gui_auto_trade_timer.capture_routine_main_facts") as capture:
        result = _process_pending_signal_pipeline(SimpleNamespace(), snapshot)
    assert result == {}
    consume.assert_not_called()
    capture.assert_not_called()


def test_execution_readiness_revoked_after_evaluation_never_enqueues(tmp_path):
    readiness = iter((True, False))
    supply = _project(_installed_host(), 1)
    result, evaluated, _selected, queued = _run(
        tmp_path, supply, readiness_reader=lambda: next(readiness),
    )
    assert evaluated.call_count == 1
    assert result["signal"] == "SKIP"
    assert result["reason"] == "SOR_EXECUTION_READINESS_CHANGED"
    assert queued == []


def test_completed_m1_without_broker_receipt_remains_preview_only(tmp_path):
    supply = _project(_installed_host(), 1)
    supply.pop("direct_source_receipt")
    result, evaluated, selected, queued = _run(tmp_path, supply)
    assert result["signal"] == "SKIP"
    evaluated.assert_not_called()
    selected.assert_called_once()
    assert queued == []


def test_mixed_pipeline_captures_and_consumes_only_live_stock(tmp_path):
    from gui_auto_trade_timer import _process_pending_signal_pipeline

    entries = []
    for code, probe_only in (("005930", False), ("000660", True)):
        stock_dir = tmp_path / f"{code}_Test"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text("{}", encoding="utf-8")
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        entries.append(SimpleNamespace(
            stock_code=code, stock_name="Test", stock_dir=stock_dir,
            execution_ready=True, signal_probe_only=probe_only,
        ))
    snapshot = SimpleNamespace(entries=tuple(entries))
    facts = {"stock_configs": {
        "005930": {"assigned_routine_instance_id": "live-instance"},
        "000660": {"assigned_routine_instance_id": "probe-instance"},
    }, "revision": "memory"}
    with patch("gui_auto_trade_timer.capture_routine_main_facts",
               return_value=SimpleNamespace(to_payload=lambda: facts)) as capture, \
         patch("gui_auto_trade_timer.actionable_current_price", return_value=None), \
         patch("gui_auto_trade_timer.evaluate_routine_lifecycle",
               return_value={"ok": True, "decisions": []}) as lifecycle, \
         patch("gui_auto_trade_timer.consume_pending_routine_signals_dry_run",
               return_value={"summary": {}}) as consume:
        _process_pending_signal_pipeline(SimpleNamespace(), snapshot)
    assert capture.call_args.kwargs["allowed_stock_codes"] == ("005930",)
    assert set(capture.call_args.kwargs["stock_dirs"]) == {"005930"}
    assert lifecycle.call_count == 1
    assert lifecycle.call_args.kwargs["instance_id"] == "live-instance"
    assert consume.call_args.kwargs["allowed_stock_codes"] == ("005930",)
