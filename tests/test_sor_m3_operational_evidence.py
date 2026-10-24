"""Direct M3 operating evidence must stay separate from the legacy M1 gate."""

from copy import deepcopy
from datetime import datetime, timezone, timedelta

from sor_routine_signal_evidence import build_source_marker


RULES = {"bar": {"bar_minutes": 3}, "buy": {"groups": []}}
STAMP = "2026-10-24T09:00:00+09:00"
CANDLE = {"bar_time": STAMP, "timeframe_minutes": 3, "market_source": "INTEGRATED",
          "market_data_identity": "005930_AL", "display_only": True,
          "is_complete": True, "open": 100, "high": 101, "low": 99,
          "close": 100, "volume": 10}
SOURCE = {"available": True, "stock_code": "005930", "candles": [CANDLE],
          "timeframe_minutes": 3, "market_source": "INTEGRATED",
          "market_data_identity": "005930_AL",
          "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
          "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
          "projection": "COMPLETED_TIMEFRAME", "complete": True,
          "source_identity": "a" * 64,
          "direct_source_receipt": {"stock_code": "005930", "timeframe_key": "M3",
              "source_content_sha256": "a" * 64, "connection_epoch": 1,
              "session_sha256": "b" * 64,
              "source_as_of": "2026-10-24T09:05:00+09:00"},
          "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
          "calculation_only": True, "execution_enabled": False,
          "queue_connected": False, "send_order_connected": False}


def test_direct_m3_marker_binds_source_prefix_and_canonical_rules():
    marker = build_source_marker(stock="005930", rules=RULES,
                                 candles=[CANDLE], source=SOURCE, side="NONE")
    assert marker["timeframe"] == "M3"
    assert marker["evaluation_time"] == "20261024090000"
    assert marker["source_identity"] == "a" * 64
    assert marker["side"] == "NONE"


def test_direct_m3_rejects_krx_forming_and_m1_fallback():
    for field, value in (("market_source", "KRX"),
                         ("direct_history_source", "M1_REBUILT"),
                         ("source_identity", "")):
        source = deepcopy(SOURCE)
        source[field] = value
        assert build_source_marker(stock="005930", rules=RULES,
                                   candles=[CANDLE], source=source, side="BUY") is None
    for row in (dict(CANDLE, is_complete=False),
                dict(CANDLE, timeframe_minutes=1),
                dict(CANDLE, market_source="KRX")):
        source = deepcopy(SOURCE)
        source["candles"] = [row]
        assert build_source_marker(stock="005930", rules=RULES,
                                   candles=[row], source=source, side="BUY") is None
    for receipt in (dict(SOURCE["direct_source_receipt"], connection_epoch=0),
                    dict(SOURCE["direct_source_receipt"], session_sha256=""),
                    dict(SOURCE["direct_source_receipt"], timeframe_key="M1")):
        source = deepcopy(SOURCE)
        source["direct_source_receipt"] = receipt
        assert build_source_marker(stock="005930", rules=RULES,
                                   candles=[CANDLE], source=source, side="BUY") is None
    assert build_source_marker(stock="005930", rules={"bar": {"bar_minutes": 1}},
                               candles=[CANDLE], source=SOURCE, side="BUY") is None


def test_real_probe_accepts_direct_m3_without_order_or_m1_fallback(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    import routine_signal_probe
    from tests.test_sor_core_m1_input import NOW, _installed_host, _probe_facts, _project

    host = _installed_host()
    direct = dict(host._display_timeframe_candles[("005930", 1)][0])
    direct["timeframe_minutes"] = 3
    host._display_timeframe_candles[("005930", 3)] = (direct,)
    host._display_timeframe_session_identity[("005930", 3)] = (1, "SESSION-1")
    host._display_timeframe_candles.pop(("005930", 1))
    supply = _project(host, 3)
    assert supply["available"] is True
    assert supply["calculation_source"] == "BROKER_AL_CONFIRMED_DIRECT"
    selected = Mock(return_value={"signal": "BUY", "signal_source_index": 0})
    routine = SimpleNamespace(
        ROUTINE_TYPE="isolated",
        market_bar_projection_request=lambda _rules: {"projection": "COMPLETED_TIMEFRAME"},
        project_cycle_context=lambda **_facts: {"status": "resolved"},
        evaluate=Mock(side_effect=AssertionError("no trade")),
        evaluate_signal_selection=selected,
    )
    with patch.object(routine_signal_probe, "_load_instance_rules", return_value=RULES), \
         patch.object(routine_signal_probe, "_maybe_enqueue_signal",
                      side_effect=AssertionError("no queue")), \
         patch.object(routine_signal_probe, "project_candle_supply",
                      side_effect=AssertionError("no M1 fallback")), \
         patch.object(routine_signal_probe, "read_reference_price", return_value=None), \
         patch.object(routine_signal_probe, "find_library_stock_by_code", return_value=None), \
         patch.object(routine_signal_probe, "observe_owner_failure_transition"), \
         patch.object(routine_signal_probe, "_append_log"), \
         patch.object(routine_signal_probe, "_enqueue_sor_m3_preview") as capture:
        output = routine_signal_probe.probe_routine_for_stock(
            routine, "isolated", tmp_path / "005930_Test",
            NOW.isoformat(timespec="seconds"), decision_trace_observer=None,
            main_facts=_probe_facts(supply))
    assert output["signal"] == "SKIP"
    assert output["sor_preview_evidence"]["timeframe"] == "M3"
    assert output["sor_preview_evidence"]["receipt_hash"]
    assert output["send_order_connected"] is False
    assert selected.call_count == 1
    capture.assert_called_once()


def test_mock_existing_journal_records_direct_m3_no_signal():
    from unittest.mock import patch
    from mock_validation_virtual_execution import MockExecutionPolicy
    from tests.test_mock_indicator_follow_adapter import (
        MockIndicatorFollowAdapterTest, NOW, SESSION_ID, _buy_rules, _market,
    )
    case = MockIndicatorFollowAdapterTest("runTest")
    try:
        rules = _buy_rules()
        rules["bar"] = {"bar_minutes": 3}
        repository, _service, _engine, adapter, _selected = case.build({"A": rules})
        candle = dict(CANDLE, bar_time=NOW.isoformat(), trade_date=NOW.date().isoformat())
        source = deepcopy(SOURCE)
        source["candles"] = [candle]
        with patch("mock_validation_indicator_follow_adapter._pure_signal_selection",
                   return_value=lambda *_a, **_k: {"signal": None, "reason": "NO_SIGNAL"}):
            result = adapter.evaluate_cycle(
                SESSION_ID, routine_instance_id="A", candles=[candle],
                market=_market(), policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
                evaluation_cycle_id="MC-m3-none", evaluated_at=NOW,
                candle_availability=source)
        assert result["status"] == "NO_SIGNAL"
        events = [e for e in repository.read_events(SESSION_ID)
                  if e["event_type"] == "ROUTINE_EVALUATED"]
        assert len(events) == 1
        assert events[0]["payload"]["signal"] == "NONE"
        assert events[0]["payload"]["sor_source_evidence"]["timeframe"] == "M3"
    finally:
        case.doCleanups()
