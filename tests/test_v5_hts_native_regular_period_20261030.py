"""Native FULL requests and rejection of legacy REGULAR period evidence (offline)."""
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
import gui_indicator_follow_signal_validation_v5_flow as v5
from tests.test_v5_direct_forming_tail_20261026 import _flow
from tests.test_indicator_follow_signal_validation_v5_baseline import case

TR_CODES = {
    "D1": "opt10101", "W1": "opt10102",
    "MO1": "opt10103", "Y1": "opt10104",
}


@pytest.mark.parametrize("key,trcode", TR_CODES.items())
def test_full_period_uses_native_al_tr_even_during_nxt_premarket(
    monkeypatch, key, trcode,
):
    """A period filter must never silently switch its history source to NX at 08:30."""
    now = datetime(2026, 10, 30, 8, 30, tzinfo=SEOUL_TIMEZONE)
    stock = v5.ValidationStockRef("005930", "Samsung")
    calls = []

    class Broker:
        def request_period_candles_read_only_integrated(
            self, code, name, *, timeframe_key, count, callback, nxt_available,
        ):
            calls.append(("FULL_INTEGRATED", code, name, timeframe_key, count))
            started = {
                "ok": True, "rqname": "FULL_" + key,
                "market_data_identity": "005930_AL",
                "market_source": "INTEGRATED",
                "market_session_scope": "FULL",
            }
            callback({
                **started, "type": "period_candles",
                "request_id": started["rqname"],
                "code": code, "timeframe_key": timeframe_key,
                "trcode": {"D1": "opt10081", "W1": "opt10082",
                           "MO1": "opt10083", "Y1": "opt10094"}[key],
                "rows": [{"일자": "20261029"}],
            })
            return started

        def request_period_candles_read_only_nxt(self, *_args, **_kwargs):
            pytest.fail("Regular-only period must never use NX broker TR")

    monkeypatch.setattr(v5, "read_stock_market_source_plan",
                        lambda *_args: pytest.fail("No NX plan for a regular-only period"))
    requester = v5._V5SelectedSourceRequester(
        Broker(), stock, key, True, regular_only=True, now_factory=lambda: now,
    )
    replies = []
    requester.request_period_candles_read_only(
        stock.code, stock.name, timeframe_key=key, count=3,
        callback=replies.append,
    )
    assert calls == [("FULL_INTEGRATED", "005930", "Samsung", key, 3)]
    assert len(replies) == 1 and replies[0]["ok"] is True


@pytest.mark.parametrize("key", TR_CODES)
def test_fixed_full_cannot_reuse_regular_period_pool(key):
    flow = _flow()
    flow._validation_source_by_code["005930"] = True
    stock = v5.ValidationStockRef("005930", "Samsung")
    now = datetime(2026, 10, 30, 18, 0, tzinfo=SEOUL_TIMEZONE)
    flow._now_factory = lambda: now
    full_pool = {
        "stock": stock, "timeframe_key": key,
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "market_session_scope": "FULL",
        "requested_count": 100,
        "candles": [{"time": "20261029000000", "open": 100, "high": 101,
                     "low": 99, "close": 100, "volume": 5}],
        "source_verified_at": now.isoformat(),
    }
    assert flow._pool_matches(full_pool, stock, key, 1, market_session_scope="FULL")
    regular_pool = dict(full_pool, market_session_scope="REGULAR")
    assert not flow._pool_matches(regular_pool, stock, key, 1, market_session_scope="FULL")


@pytest.mark.parametrize("key,trcode", TR_CODES.items())
@pytest.mark.parametrize("integrated", [False, True])
def test_full_period_rejects_legacy_regular_receipt_for_al_and_krx(
    monkeypatch, key, trcode, integrated,
):
    """The same native rows must carry an authenticated REGULAR request receipt."""
    from types import SimpleNamespace
    from routines.지표추종매매.routine_validation_historical import ValidationHistoricalSnapshot

    flow = _flow()
    code, name = "005930", "Samsung"
    flow._validation_source_by_code[code] = integrated
    if not integrated:
        flow._validation_frozen_krx_codes.add(code)
    stock = v5.ValidationStockRef(code, name)
    observed = datetime(2026, 10, 30, 18, 0, tzinfo=SEOUL_TIMEZONE)
    before = [
        {"time": "20250102000000", "open": 100, "high": 102,
         "low": 99, "close": 101, "volume": 10},
        {"time": "20261030000000", "open": 103, "high": 105,
         "low": 102, "close": 104, "volume": 11},
    ]
    rows = flow._historical_rows_from_candles(before)
    source = "INTEGRATED" if integrated else "KRX"
    identity = code + "_AL" if integrated else code
    receipt_scope = ["REGULAR"]

    class Broker:
        def broker_session_snapshot(self):
            return SimpleNamespace(connected=True, connection_epoch=2,
                                   login_session_id="CONNECTED-2")

        def request(self, request_code, request_name, **kwargs):
            assert (request_code, request_name) == (code, name)
            started = dict(ok=True, rqname=receipt_scope[0] + "-" + key + "-" + source,
                           market_data_identity=identity, market_source=source,
                           market_session_scope=receipt_scope[0])
            kwargs["callback"]({
                **started, "type": "period_candles",
                "request_id": started["rqname"], "code": code,
                "timeframe_key": key, "trcode": (trcode if receipt_scope[0] == "REGULAR" else {
                    "D1": "opt10081", "W1": "opt10082", "MO1": "opt10083", "Y1": "opt10094",
                }[key]), "rows": rows,
                "rows_count": len(rows),
            })
            return started

        request_period_candles_read_only_regular_integrated = request
        request_period_candles_read_only_regular = request
        request_period_candles_read_only_integrated = request
        request_period_candles_read_only = request

    monkeypatch.setattr(v5, "read_stock_market_source_plan",
                        lambda *_args: {"nxt_available": False, "stock_name": name})
    broker = Broker()
    replies = []
    # Preserve an independently observed legacy receipt after the request option retires.
    started = broker.request(code, name, timeframe_key=key, count=2, callback=replies.append)
    flow._remember_verified_al_period_response(
        code, key, replies[0], started, observed, observed,
        broker.broker_session_snapshot(), broker.broker_session_snapshot())
    assert replies and replies[0]["ok"] is True
    snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=1, timeframe_key=key,
        requested_count=2, request_id=replies[0]["request_id"],
        rows=rows, market_data_identity=identity, market_source=source,
        request_as_of=observed.isoformat(),
    )
    candles, forming, proof = flow._direct_selected_history_parts(snapshot)
    assert candles == before
    assert forming is None
    assert proof is not None
    assert proof["trcode"] == trcode
    assert proof["market_session_scope"] == "REGULAR"
    assert flow._direct_al_period_query_valid(
        proof, stock, key, 1, identity, candles)

    full_session = v5.ValidationSession(
        v5.ValidationRequest(stock, v5.ValidationSettingsSnapshot({
            "bar": {"bar_minutes": 1}, "validation_timeframe": {"key": key},
            "validation_market_scope": {"regular_market_only": False},
        }), 1), operation_active_reader=lambda: False,
    )
    relabeled = dict(proof, market_session_scope="FULL", trcode={
        "D1": "opt10081", "W1": "opt10082", "MO1": "opt10083", "Y1": "opt10094",
    }[key])
    for incompatible in (proof, dict(proof, market_session_scope="FULL"), relabeled):
        with pytest.raises(ValueError, match="PERIOD_RECEIPT_SCOPE_MISMATCH"):
            flow._pool_from_candles(
                full_session, candles, requested_count=len(candles),
                request_id=snapshot.request_id, market_data_identity=identity,
                market_source=source, direct_selected_query=incompatible,
            )
    with pytest.raises(ValueError, match="PERIOD_RECEIPT_SCOPE_MISMATCH"):
        flow._pool_from_candles(
            full_session, candles, requested_count=len(candles),
            request_id=snapshot.request_id, market_data_identity=identity,
            market_source=source, direct_selected_query=None)
    forged = dict(
        stock=stock, timeframe_key=key, timeframe_minutes=1,
        candles=candles, market_data_identity=identity, market_source=source,
        market_session_scope="FULL", requested_count=2, direct_selected_query=proof,
        source_verified_at=observed.isoformat(),
    )
    assert not flow._pool_matches(forged, stock, key, 1)
    with pytest.raises(ValueError, match="PERIOD_RECEIPT_SCOPE_MISMATCH"):
        flow._require_selected_timeframe_pool(full_session, forged)
    for bad in (None, dict(proof, request_id=""), dict(proof, session_sha256="")):
        assert not flow._direct_al_period_query_valid(bad, stock, key, 1, identity, candles)
    assert not flow._direct_al_period_query_valid(
        proof, stock, key, 1, identity, [dict(candles[0], close=999), candles[1]])

    regular_proof = proof
    receipt_scope[0] = "FULL"
    replies.clear()
    requester = v5._V5SelectedSourceRequester(
        broker, stock, key, integrated, now_factory=lambda: observed,
        on_verified_al_period_response=flow._remember_verified_al_period_response)
    requester.request_period_candles_read_only(
        code, name, timeframe_key=key, count=2, callback=replies.append)
    assert replies[0]["ok"] is True
    snapshot = ValidationHistoricalSnapshot(
        stock=stock, timeframe_minutes=1, timeframe_key=key,
        requested_count=2, request_id=replies[0]["request_id"], rows=rows,
        market_data_identity=identity, market_source=source,
        request_as_of=observed.isoformat())
    candles, forming, proof = flow._direct_selected_history_parts(snapshot)
    if not integrated:
        # FULL KRX retains its existing frozen native route and its own request identity.
        assert proof is None
        native_pool = flow._pool_from_candles(
            full_session, candles, requested_count=2, request_id=snapshot.request_id,
            market_data_identity=identity, market_source=source)
        flow._require_selected_timeframe_pool(full_session, native_pool)
        assert replies[0]["market_session_scope"] == "FULL"
        return
    assert proof["market_session_scope"] == "FULL"

    settings = v5.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 1},
        "validation_timeframe": {"key": key},
        "validation_market_scope": {"regular_market_only": False},
    })
    session = v5.ValidationSession(
        v5.ValidationRequest(stock, settings, 1),
        operation_active_reader=lambda: False,
    )
    flow._now_factory = lambda: observed
    native_pool = flow._pool_from_candles(
        session, candles, requested_count=len(candles),
        request_id=snapshot.request_id,
        market_data_identity=identity, market_source=source,
        direct_selected_query=proof,
    )
    flow._require_selected_timeframe_pool(session, native_pool)
    # The calculation view is a suffix of the broker-authenticated full pool.
    tail_view = flow._validation_pool_for_session(
        session, native_pool, target_count=1,
    )
    assert tail_view["candles"] == candles[-1:]
    flow._require_selected_timeframe_pool(session, tail_view)

    if integrated:
        # A legacy REGULAR receipt cannot be rebranded as FULL.
        assert flow._direct_al_period_query_valid(
            regular_proof, stock, key, 1, identity, candles,
        )
        with pytest.raises(ValueError, match="PERIOD_RECEIPT_SCOPE_MISMATCH"):
            flow._pool_from_candles(
                session, candles, requested_count=len(candles),
                request_id=snapshot.request_id,
                market_data_identity=identity, market_source=source,
                direct_selected_query=regular_proof,
            )
        forged_pool = dict(native_pool, market_session_scope="FULL", direct_selected_query=regular_proof)
        regular_settings = v5.ValidationSettingsSnapshot({
            "bar": {"bar_minutes": 1},
            "validation_timeframe": {"key": key},
        })
        full_session = v5.ValidationSession(
            v5.ValidationRequest(stock, regular_settings, 1),
            operation_active_reader=lambda: False,
        )
        with pytest.raises(ValueError):
            flow._require_selected_timeframe_pool(full_session, forged_pool)

        if key == "D1":
            # Warm-up source 5,901 -> calculation 5,900 must retain its
            # original broker proof; do not manufacture a new sliced receipt.
            start_date = observed.date() - timedelta(days=5900)
            long_rows = [
                dict(before[0], time=(start_date + timedelta(days=index)).strftime(
                    "%Y%m%d") + "000000")
                for index in range(5901)
            ]
            content = json.dumps(
                long_rows, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            long_proof = dict(
                proof,
                last_completed_time=long_rows[-2]["time"],
                source_content_sha256=hashlib.sha256(content).hexdigest(),
            )
            long_pool = flow._pool_from_candles(
                session, long_rows, requested_count=5901,
                request_id=snapshot.request_id,
                market_data_identity=identity, market_source=source,
                direct_selected_query=long_proof,
            )
            long_view = flow._validation_pool_for_session(
                session, long_pool, target_count=5900,
            )
            assert len(long_view["candles"]) == 5900
            flow._require_selected_timeframe_pool(session, long_view)
            # A precomputed display view may be routed through normal-base
            # preparation before range selection. Its suffix still needs proof.
            normal_view, _deficit = flow._normal_base_history_view(
                session, long_pool, history_target=5000, display_view=long_view,
            )
            flow._require_selected_timeframe_pool(session, normal_view)
            tampered = dict(long_view, candles=[
                dict(long_view["candles"][0], close=999_999),
                *long_view["candles"][1:],
            ])
            with pytest.raises(ValueError, match="SOR_DIRECT_PERIOD_SOURCE_SUFFIX_UNVERIFIED"):
                flow._require_selected_timeframe_pool(session, tampered)

            # This FULL replay is entirely historical; CURRENT policy is unchanged.
            flow._now_factory = lambda: observed + timedelta(days=1)
            def reached_native_replay(_view):
                raise RuntimeError("REGULAR_NATIVE_REPLAY_VERIFIED")

            # Exercise the actual replay gate up to the marker worker boundary.
            # A FULL SOR M1-tail-complete flag is intentionally absent.
            with pytest.raises(RuntimeError, match="REGULAR_NATIVE_REPLAY_VERIFIED"):
                flow._prepare_replay_result_from_pool(
                    session, long_pool, history_target=5900,
                    evaluation_count=100, prepared_view_pool=long_view,
                    verified_source_identity=flow._pool_computation_source_identity(
                        long_pool),
                    marking_view_check=reached_native_replay,
                )

@pytest.mark.parametrize("key", TR_CODES)
def test_period_legacy_on_restore_requests_full(monkeypatch, case, key):
    """Legacy configuration cannot re-enable the retired V5 session option."""
    from PyQt5.QtCore import QSignalBlocker
    from copy import deepcopy
    from unittest.mock import patch
    from tests.test_indicator_follow_signal_validation_v5_baseline import dialog_module, window_module

    rules, state = deepcopy(case.rules), deepcopy(case.ui_state)
    rules["validation_market_scope"] = {"regular_market_only": True}
    state["validation_market_scope"] = {"regular_market_only": True}
    seed = case._seed(rules, state)
    original = seed.settings_snapshot.to_dict()
    with patch.object(dialog_module.QTimer, "singleShot"):
        window = window_module.IndicatorFollowSignalValidationV5Window(case.stock, seed)
    case.widgets.append(window)
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText(key)

    legacy = {"regular_market_only": True}
    window._restore_validation_preferences(legacy)
    assert window.collect_indicator_follow_ui_state()["validation_market_scope"] == {
        "regular_market_only": False,
    }
    assert not hasattr(window, "validation_regular_market_only_check")
    assert "regular_market_only" not in window._validation_preferences_snapshot()
    assert legacy == {"regular_market_only": True}
    requests = []
    window.validation_run_requested.connect(requests.append)
    window._request_validation(normal_base=True)
    assert len(requests) == 1
    assert requests[0].settings_snapshot.to_dict()["validation_market_scope"] == {
        "regular_market_only": False,
    }
    assert window._marking_current_settings().to_dict()["validation_market_scope"] == {
        "regular_market_only": False,
    }
    payload = window._request_settings_apply()
    # The shared apply contract excludes Validation-only settings from the parent dialog.
    assert "validation_market_scope" not in payload.to_ui_state()
    assert window._pending_ocr_turn_ui_state["validation_market_scope"] == {"regular_market_only": False}
    window.reset_button.click()
    assert len(requests) == 1
    assert window.collect_indicator_follow_ui_state()["validation_market_scope"] == {
        "regular_market_only": False}
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText("3")
    window._on_validation_timeframe_changed()
    assert len(requests) == 2
    assert requests[-1].settings_snapshot.to_dict()["validation_timeframe"]["key"] == "M3"
    for request in requests:
        effective = request.settings_snapshot.to_dict()
        assert effective["validation_market_scope"] == {"regular_market_only": False}
        assert effective["indicator_follow_ui_state"]["state"]["validation_market_scope"] == {
            "regular_market_only": False}
        assert request.settings_snapshot.rules_hash == v5.ValidationSettingsSnapshot(effective).rules_hash
    assert seed.settings_snapshot.to_dict() == original


def test_legacy_on_run_normalizes_before_retention_and_source_selection(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace

    flow = _flow()
    stock = v5.ValidationStockRef("005930", "Samsung")
    window = SimpleNamespace(stock=stock)
    flow._open_windows[id(window)] = window
    rules = {
        "bar": {"bar_minutes": 1}, "validation_timeframe": {"key": "D1"},
        "validation_market_scope": {"regular_market_only": True, "retained": 7},
        "indicator_follow_ui_state": {"state": {
            "validation_market_scope": {"regular_market_only": True, "retained": 9},
        }},
    }
    original = deepcopy(rules)
    settings = v5.ValidationSettingsSnapshot(rules)
    run = v5.IndicatorFollowSignalValidationRunRequest(settings, 3)
    selected = []
    monkeypatch.setattr(flow, "_verified_nxt_validation_stock", lambda _: True)
    monkeypatch.setattr(flow, "_fail_window", lambda *_: None)
    def select(*args, **kwargs):
        selected.append(args)
        raise ValueError("OFFLINE_STOP_AFTER_SELECTION")
    monkeypatch.setattr(v5, "_select_v5_source", select)
    flow._run_validation(window, run)
    effective = flow._last_run_requests[id(window)].settings_snapshot
    expected = deepcopy(original)
    expected["validation_market_scope"]["regular_market_only"] = False
    expected["indicator_follow_ui_state"]["state"]["validation_market_scope"]["regular_market_only"] = False
    assert effective.to_dict() == expected
    assert effective.rules_hash == v5.ValidationSettingsSnapshot(expected).rules_hash
    assert selected[0][3] is False
    assert run.settings_snapshot.to_dict() == original and rules == original


def test_legacy_on_month_period_uses_full_native_source():
    stock = v5.ValidationStockRef("005930", "Samsung")
    selected = v5._select_v5_source(
        stock, "MO1", True, True, datetime(2026, 10, 30, 8, 30, tzinfo=SEOUL_TIMEZONE))
    assert selected.timeframe_key == "MO1"
    assert selected.market_session_scope == "FULL"
    assert (selected.market_data_identity, selected.market_source) == ("005930_AL", "INTEGRATED")
