"""Direct-source evidence is selected by exact chart frame."""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from sor_m3_operational_evidence import build_v5_records, compare_records, main
from sor_m3_production_capture import _safe_record
from sor_routine_signal_evidence import build_source_marker
from tests.test_sor_m3_operational_collector import _records
from tests.test_sor_m3_operational_evidence import CANDLE, RULES, SOURCE


def _direct(frame):
    minutes = int(frame[1:])
    candle = dict(CANDLE, timeframe_minutes=minutes)
    source = deepcopy(SOURCE)
    source.update(candles=[candle], timeframe_minutes=minutes)
    source["direct_source_receipt"]["timeframe_key"] = frame
    rules = deepcopy(RULES)
    rules["bar"]["bar_minutes"] = minutes
    return candle, source, rules


def _stock_records(stock, frame="M3"):
    candle, source, rules = _direct(frame)
    candle["market_data_identity"] = f"{stock}_AL"
    source["stock_code"] = stock
    source["market_data_identity"] = f"{stock}_AL"
    source["direct_source_receipt"]["stock_code"] = stock
    source["candles"] = [candle]
    marker = build_source_marker(stock=stock, rules=rules,
                                 candles=[candle], source=source, side="BUY")
    assert marker is not None
    v5, production, mock = _records()
    for record in (v5[0], production[0]["sor_preview_evidence"],
                   mock["events"][0]["payload"]["sor_source_evidence"]):
        record.update(marker)
    production[0]["code"] = stock
    mock["events"][0]["stock_code"] = stock
    mock["events"][0]["payload"]["rules_hash"] = marker["rules_hash"]
    return v5, production, mock


@pytest.mark.parametrize("frame", ["M5", "M240"])
def test_direct_marker_and_collector_compare_only_selected_frame(frame):
    candle, source, rules = _direct(frame)
    marker = build_source_marker(stock="005930", rules=rules,
                                 candles=[candle], source=source, side="BUY")
    assert marker is not None and marker["timeframe"] == frame
    v5, production, mock = _records()
    for record in (v5[0], production[0]["sor_preview_evidence"],
                   mock["events"][0]["payload"]["sor_source_evidence"]):
        record.update(marker)
    mock["events"][0]["payload"]["rules_hash"] = marker["rules_hash"]
    assert _safe_record(production[0]) is not None
    result = compare_records(v5, production, mock, cutoff="20261024090000",
                             timeframe=frame)
    assert result["comparison"] == "MATCH"
    assert result["status"] == "BLOCKED"
    assert result["live_parity"] is False
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["comparison"] == "UNAVAILABLE"
    production[0]["sor_preview_evidence"]["receipt_hash"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe=frame)["reason"] == "SOURCE_RULES_OR_PREFIX_MISMATCH"


def test_v5_worker_builds_direct_frame_records_from_exact_pool():
    row = {"time": "20261024090000", "open": 100, "high": 101,
           "low": 99, "close": 100, "volume": 10}
    for frame in ("M5", "M240"):
        minutes = int(frame[1:])
        pool = {"stock": SimpleNamespace(code="005930"), "timeframe_key": frame,
                "timeframe_minutes": minutes, "market_source": "INTEGRATED",
                "market_data_identity": "005930_AL", "candles": [row]}
        marker = {"canonical_stock_code": "005930", "market_source": "INTEGRATED",
                  "market_data_identity": "005930_AL",
                  "candle_content_sha256": sha256(json.dumps([row], sort_keys=True,
                      separators=(",", ":")).encode()).hexdigest()}
        snapshot = SimpleNamespace(stock=pool["stock"], timeframe_key=frame,
            timeframe_minutes=minutes, settings_hash="a" * 64,
            historical_request_id="AL-1", evaluated_start_index=0,
            evaluated_end_index=0)
        records = build_v5_records(pool, marker, snapshot, [row], [],
                                   generation=1, request_generation=1)
        assert len(records) == 1 and records[0]["timeframe"] == frame
        assert build_v5_records(dict(pool, timeframe_key="M3"), marker,
                                snapshot, [row], [], generation=1,
                                request_generation=1) == []


def test_new_direct_frames_use_bounded_capture_routes(tmp_path, monkeypatch):
    import sor_m3_mock_capture as mock_capture
    import sor_m3_production_capture as production_capture

    monkeypatch.setattr(production_capture, "_DIRECT_PATH", tmp_path / "production.jsonl")
    monkeypatch.setattr(mock_capture, "_DIRECT_PATH", tmp_path / "mock.jsonl")
    for frame in ("M5", "M240"):
        candle, source, rules = _direct(frame)
        marker = build_source_marker(stock="005930", rules=rules,
                                     candles=[candle], source=source, side="BUY")
        v5, production, mock = _records()
        production[0]["sor_preview_evidence"] = marker
        production[0]["sor_preview_routine_instance_id"] = f"RI-{frame}"
        production[0]["sor_preview_cycle_hash"] = sha256(frame.encode()).hexdigest()
        event = mock["events"][0]
        event["event_id"] = f"ME-{frame}"
        event["routine_instance_id"] = f"RI-{frame}"
        event["payload"]["evaluation_cycle_id"] = f"MC-{frame}"
        event["payload"]["rules_hash"] = marker["rules_hash"]
        event["payload"]["sor_source_evidence"] = marker
        assert production_capture.enqueue_preview(production[0]) is True
        assert mock_capture.enqueue_event(event) is True
    production_capture._QUEUE.join()
    for name in ("production.jsonl", "mock.jsonl"):
        records = [json.loads(line) for line in (tmp_path / name).read_text(
            encoding="utf-8").splitlines()]
        assert [record["sor_preview_evidence"]["timeframe"] if name == "production.jsonl"
                else record["payload"]["sor_source_evidence"]["timeframe"]
                for record in records] == ["M5", "M240"]


def test_m1_and_period_frames_fail_closed_without_direct_receipt_contract():
    v5, production, mock = _records()
    for frame in ("M1", "D1", "W1", "MO1", "Y1"):
        result = compare_records(v5, production, mock, cutoff="20261024090000",
                                 timeframe=frame)
        assert result["status"] == "BLOCKED"
        assert result["comparison"] == "UNAVAILABLE"
    candle, source, rules = _direct("M5")
    source["direct_source_receipt"]["timeframe_key"] = "M3"
    assert build_source_marker(stock="005930", rules=rules,
                               candles=[candle], source=source, side="BUY") is None


def test_mixed_frame_hash_duplicate_and_missing_routes_block():
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["timeframe"] = "M5"
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["comparison"] == "UNAVAILABLE"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["input_hash"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["reason"] == "SOURCE_RULES_OR_PREFIX_MISMATCH"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["source_identity"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["reason"] == "PRODUCTION_MOCK_SOURCE_RECEIPT_MISMATCH"
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"]["source_connection_epoch"] = 0
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M3")["comparison"] == "UNAVAILABLE"
    v5, production, mock = _records()
    assert compare_records(v5 + v5, production, mock,
                           cutoff="20261024090000", timeframe="M3")["reason"] == "V5_DUPLICATE_EVALUATION"
    assert compare_records([], production, mock, cutoff="20261024090000",
                           timeframe="M3")["status"] == "BLOCKED"
    assert compare_records(v5, [], mock, cutoff="20261024090000",
                           timeframe="M3")["status"] == "BLOCKED"
    assert compare_records(v5, production, [], cutoff="20261024090000",
                           timeframe="M3")["status"] == "BLOCKED"


def test_explicit_frame_selects_from_shared_direct_files(tmp_path, capsys):
    selected = _records()
    other = _records()
    for records, frame in ((selected, "M5"), (other, "M240")):
        candle, source, rules = _direct(frame)
        marker = build_source_marker(stock="005930", rules=rules,
                                     candles=[candle], source=source, side="BUY")
        v5, production, mock = records
        for item in (v5[0], production[0]["sor_preview_evidence"],
                     mock["events"][0]["payload"]["sor_source_evidence"]):
            item.update(marker)
        mock["events"][0]["payload"]["rules_hash"] = marker["rules_hash"]
    other[2]["events"][0]["event_id"] = "ME-other"
    other[2]["events"][0]["payload"]["evaluation_cycle_id"] = "MC-other"
    v5 = selected[0] + other[0]
    production = selected[1] + other[1]
    mock = {**selected[2], "events": selected[2]["events"] + other[2]["events"]}
    for frame in ("M5", "M240"):
        assert compare_records(v5, production, mock,
                               cutoff="20261024090000",
                               timeframe=frame)["comparison"] == "MATCH"
    paths = [tmp_path / name for name in ("v5.jsonl", "production.json", "mock.json")]
    paths[0].write_text("".join(json.dumps(row) + "\n" for row in v5), encoding="utf-8")
    paths[1].write_text(json.dumps(production), encoding="utf-8")
    paths[2].write_text(json.dumps(mock), encoding="utf-8")
    argv = ["--v5", str(paths[0]), "--production", str(paths[1]),
            "--mock", str(paths[2]), "--cutoff", "20261024090000"]
    for frame in ("M5", "M240"):
        assert main(argv + ["--timeframe", frame]) == 2
        assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"


def test_cli_requires_explicit_frame_for_non_m3(tmp_path, capsys):
    v5, production, mock = _records()
    for record in (v5[0], production[0]["sor_preview_evidence"],
                   mock["events"][0]["payload"]["sor_source_evidence"]):
        record["timeframe"] = "M5"
    paths = [tmp_path / name for name in ("v5.jsonl", "production.json", "mock.json")]
    paths[0].write_text(json.dumps(v5[0]) + "\n", encoding="utf-8")
    paths[1].write_text(json.dumps(production), encoding="utf-8")
    paths[2].write_text(json.dumps(mock), encoding="utf-8")
    argv = ["--v5", str(paths[0]), "--production", str(paths[1]),
            "--mock", str(paths[2]), "--cutoff", "20261024090000"]
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "UNAVAILABLE"
    assert main(argv + ["--timeframe", "M5"]) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"


def test_two_stocks_share_direct_routes_and_explicit_selection(tmp_path, capsys):
    samsung = _stock_records("005930")
    hynix = _stock_records("000660")
    assert _safe_record(hynix[1][0])["code"] == "000660"
    v5 = samsung[0] + hynix[0]
    production = samsung[1] + hynix[1]
    mock = {**samsung[2], "events": samsung[2]["events"] + hynix[2]["events"]}
    for stock in ("005930", "000660"):
        result = compare_records(v5, production, mock,
                                 cutoff="20261024090000", stock=stock)
        assert result["comparison"] == "MATCH"
        assert result["matched_bars"] == 1
    paths = [tmp_path / name for name in ("v5.jsonl", "production.json", "mock.json")]
    paths[0].write_text("".join(json.dumps(row) + "\n" for row in v5), encoding="utf-8")
    paths[1].write_text(json.dumps(production), encoding="utf-8")
    paths[2].write_text(json.dumps(mock), encoding="utf-8")
    argv = ["--v5", str(paths[0]), "--production", str(paths[1]),
            "--mock", str(paths[2]), "--cutoff", "20261024090000"]
    assert main(argv + ["--stock", "000660"]) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"
    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["comparison"] == "MATCH"


def test_multi_stock_mismatch_and_corrupt_source_fail_closed():
    v5, production, mock = _stock_records("000660", "M5")
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           stock="000660", timeframe="M5")["comparison"] == "MATCH"
    production[0]["sor_preview_evidence"]["receipt_hash"] = "f" * 64
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           stock="000660", timeframe="M5")["reason"] == "SOURCE_RULES_OR_PREFIX_MISMATCH"
    v5, production, mock = _stock_records("000660", "M5")
    production[0]["sor_preview_evidence"]["market_data_identity"] = "005930_AL"
    assert _safe_record(production[0]) is None
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           stock="000660", timeframe="M5")["status"] == "BLOCKED"
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           stock="A00660", timeframe="M5")["reason"] == "INVALID_STOCK"
    v5, production, mock = _stock_records("000660", "M5")
    production[0]["sor_preview_evidence"]["stock"] = "005930"
    production[0]["sor_preview_evidence"]["market_data_identity"] = "005930_AL"
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           stock="000660", timeframe="M5")["status"] == "BLOCKED"


def test_capture_sinks_accept_other_stock_on_shared_direct_file(tmp_path, monkeypatch):
    import sor_m3_mock_capture as mock_capture
    import sor_m3_production_capture as production_capture

    monkeypatch.setattr(production_capture, "_DIRECT_PATH", tmp_path / "production.jsonl")
    monkeypatch.setattr(mock_capture, "_DIRECT_PATH", tmp_path / "mock.jsonl")
    _, production, mock = _stock_records("000660", "M5")
    mock["events"][0]["event_id"] = "ME-multi-stock-M5"
    assert production_capture.enqueue_preview(production[0]) is True
    assert mock_capture.enqueue_event(mock["events"][0]) is True
    production_capture._QUEUE.join()
    assert json.loads((tmp_path / "production.jsonl").read_text(
        encoding="utf-8").splitlines()[0])["code"] == "000660"
    assert json.loads((tmp_path / "mock.jsonl").read_text(
        encoding="utf-8").splitlines()[0])["stock_code"] == "000660"


def test_v5_worker_accepts_other_stock_only_with_matching_direct_cache():
    row = {"time": "20261024090000", "open": 100, "high": 101,
           "low": 99, "close": 100, "volume": 10}
    stock = SimpleNamespace(code="000660")
    pool = {"stock": stock, "timeframe_key": "M3", "timeframe_minutes": 3,
            "market_source": "INTEGRATED", "market_data_identity": "000660_AL",
            "candles": [row]}
    marker = {"canonical_stock_code": "000660", "market_source": "INTEGRATED",
              "market_data_identity": "000660_AL",
              "candle_content_sha256": sha256(json.dumps([row], sort_keys=True,
                  separators=(",", ":")).encode()).hexdigest()}
    snapshot = SimpleNamespace(stock=stock, timeframe_key="M3",
        timeframe_minutes=3, settings_hash="a" * 64,
        historical_request_id="AL-1", evaluated_start_index=0,
        evaluated_end_index=0)
    assert build_v5_records(pool, marker, snapshot, [row], [], generation=1,
                            request_generation=1)[0]["market_data_identity"] == "000660_AL"
    assert build_v5_records(pool, dict(marker, market_data_identity="005930_AL"),
                            snapshot, [row], [], generation=1,
                            request_generation=1) == []


def test_direct_m1_host_receipt_uses_completed_broker_rows():
    from tests.test_sor_core_m1_input import _installed_host, _project
    from tests.test_display_only_integrated_history import _opt10080_row
    from routines.지표추종매매.routine_validation_contract import ValidationStockRef
    from routines.지표추종매매.routine_validation_historical import ValidationHistoricalSnapshot
    from routines.지표추종매매.routine_validation_replay import project_validation_candles
    from tests.test_sor_core_m1_input import NOW

    projected = _project(_installed_host(), 1)
    receipt = projected["direct_source_receipt"]
    assert projected["available"] is True
    assert receipt["stock_code"] == "005930"
    assert receipt["timeframe_key"] == "M1"
    assert receipt["connection_epoch"] == 1
    assert receipt["session_sha256"] == sha256(b"SESSION-1").hexdigest()
    assert all(row["is_complete"] is True for row in projected["candles"])
    snapshot = ValidationHistoricalSnapshot(
        stock=ValidationStockRef("005930", "Samsung"), timeframe_minutes=1,
        requested_count=5, request_id="AL-1",
        rows=[_opt10080_row(f"20261020090{i}00", 100 + i) for i in range(5)],
        market_data_identity="005930_AL", market_source="INTEGRATED")
    normalized, _ = project_validation_candles(snapshot, as_of=NOW)
    assert receipt["source_content_sha256"] == sha256(json.dumps(normalized,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("current_session", [None, (2, "SESSION-2")])
def test_direct_m1_session_receipt_failure_keeps_calculation_available(current_session):
    from unittest.mock import patch

    from gui_market_data_host import MarketDataHost
    from tests.test_sor_core_m1_input import _installed_host, _project

    host = _installed_host()
    expected = _project(host, 1)
    with patch.object(MarketDataHost, "_current_display_session_identity",
                      side_effect=[(1, "SESSION-1"), current_session]):
        projected = _project(host, 1)

    assert projected["available"] is True
    assert projected["candles"] == expected["candles"]
    assert "direct_source_receipt" not in projected
    marker = build_source_marker(stock="005930", rules={"bar": {"bar_minutes": 1}},
                                 candles=projected["candles"], source=projected, side="BUY")
    assert marker is not None
    assert "direct_m1_source_proof" not in marker
    assert _safe_record({"code": "005930", "signal": "SKIP",
                         "reason": "SOR_CALCULATION_ONLY_NO_ORDER",
                         "execution_enabled": False, "queue_connected": False,
                         "send_order_connected": False,
                         "sor_preview_evidence": marker}) is None
    v5, production, mock = _records()
    production[0]["sor_preview_evidence"] = marker
    assert compare_records(v5, production, mock, cutoff=marker["evaluation_time"],
                           timeframe="M1")["status"] == "BLOCKED"


def test_direct_m1_three_consumer_receipt_and_decision_gate():
    row = {"time": "20261024090000", "open": 100.0, "high": 101.0,
           "low": 99.0, "close": 100.0, "volume": 10.0}
    content_hash = sha256(json.dumps([row], sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    candle = dict(CANDLE, timeframe_minutes=1)
    source = deepcopy(SOURCE)
    source.update(candles=[candle], timeframe_minutes=1,
                  calculation_source="BROKER_AL_CONFIRMED_M1",
                  projection="COMPLETED_TIMEFRAME", forming_included=False,
                  direct_source_receipt={
                      "stock_code": "005930", "timeframe_key": "M1",
                      "source_content_sha256": content_hash,
                      "connection_epoch": 1,
                      "session_sha256": sha256(b"SESSION-1").hexdigest(),
                      "source_as_of": "2026-10-24T09:02:00+09:00"})
    rules = {"bar": {"bar_minutes": 1}}
    marker = build_source_marker(stock="005930", rules=rules,
                                 candles=[candle], source=source, side="BUY")
    assert marker is not None and marker["source_identity"] == content_hash
    assert _safe_record({"code": "005930", "signal": "SKIP",
        "reason": "SOR_CALCULATION_ONLY_NO_ORDER", "execution_enabled": False,
        "queue_connected": False, "send_order_connected": False,
        "sor_preview_signal": "BUY", "sor_preview_evidence": marker,
        "sor_preview_cycle_hash": "a" * 64,
        "sor_preview_routine_instance_id": "RI-1",
        "sor_preview_observed_at": "2026-10-24T09:03:00+09:00",
        "sor_preview_activation_time": "2026-10-24T09:00:00+09:00"}) is not None
    stock = SimpleNamespace(code="005930")
    pool = {"stock": stock, "timeframe_key": "M1", "timeframe_minutes": 1,
            "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
            "candles": [row]}
    cache_marker = {"canonical_stock_code": "005930",
                    "market_source": "INTEGRATED", "market_data_identity": "005930_AL",
                    "candle_content_sha256": content_hash,
                    "direct_m1_receipt": source["direct_source_receipt"]}
    snapshot = SimpleNamespace(stock=stock, timeframe_key="M1",
        timeframe_minutes=1, settings_hash=marker["rules_hash"],
        historical_request_id="AL-1", evaluated_start_index=0,
        evaluated_end_index=0)
    entry = SimpleNamespace(signal="BUY", evaluation_index=0,
                            evaluation_time=row["time"], signal_time=row["time"])
    v5 = build_v5_records(pool, cache_marker, snapshot, [row], [entry],
                          generation=1, request_generation=1)
    assert len(v5) == 1
    assert build_v5_records(pool, dict(cache_marker, direct_m1_receipt=None),
                            snapshot, [row], [], generation=1,
                            request_generation=1) == []
    base_v5, production, mock = _records()
    production[0]["sor_preview_evidence"] = marker
    production[0]["sor_preview_activation_time"] = "2026-10-24T09:00:00+09:00"
    mock["events"][0]["payload"]["sor_source_evidence"] = marker
    mock["events"][0]["payload"]["rules_hash"] = marker["rules_hash"]
    result = compare_records(v5, production, mock, cutoff="20261024090000",
                             timeframe="M1")
    assert result["comparison"] == "MATCH"
    assert result["status"] == "BLOCKED"  # Operational cycle remains unproven.
    production[0]["sor_preview_evidence"] = dict(marker, source_connection_epoch=2)
    assert compare_records(v5, production, mock, cutoff="20261024090000",
                           timeframe="M1")["comparison"] == "UNAVAILABLE"


def test_v5_direct_m1_cache_receipt_requires_matching_broker_session_and_closed_row():
    from gui_indicator_follow_signal_validation_v5_flow import (
        IndicatorFollowSignalValidationV5Flow,
    )

    row = {"time": "20261024090000", "open": 100, "high": 101,
           "low": 99, "close": 100, "volume": 10}
    raw = [{"체결시간": "20261024090000"}]
    snapshot = SimpleNamespace(timeframe_key="M1", stock=SimpleNamespace(code="000660"),
        market_data_identity="000660_AL", market_source="INTEGRATED",
        request_id="AL-1", to_rows=lambda: raw)
    started = {"rqname": "AL-1", "request_as_of": "2026-10-24T09:02:00+09:00",
               "connection_epoch": 3, "login_session_id": "SESSION-3"}
    received = {"request_id": "AL-1", "trcode": "opt10080", "code": "000660",
                "rows": raw, "received_at": "2026-10-24T09:02:10+09:00",
                "connection_epoch": 3, "login_session_id": "SESSION-3"}
    result = SimpleNamespace(snapshot=snapshot,
        broker_al_request_receipt=(started, received))
    receipt = IndicatorFollowSignalValidationV5Flow._direct_m1_cache_receipt(
        result, [row])
    assert receipt["stock_code"] == "000660"
    assert receipt["connection_epoch"] == 3
    assert receipt["source_content_sha256"] == sha256(json.dumps([row],
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert IndicatorFollowSignalValidationV5Flow._direct_m1_cache_receipt(
        SimpleNamespace(snapshot=snapshot,
            broker_al_request_receipt=(started, dict(received, connection_epoch=4))),
        [row]) is None
    assert IndicatorFollowSignalValidationV5Flow._direct_m1_cache_receipt(
        result, [dict(row, time="20261024090200")]) is None
