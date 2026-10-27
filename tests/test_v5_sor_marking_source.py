"""Offline source proof for V5 marking with a detached NXT premarket row."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import gui_indicator_follow_signal_validation_v5_flow as module
from tests.test_v5_sor_bundle_c import prepared


class InlineThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def marking_case():
    flow, session, raw, identity, _calls = prepared("M1")
    verified = flow._verified_sor_view_for_session(
        session, raw, dict(raw), flow._now_factory(), identity,
    )
    pre = {"time": "20261020083000", "open": 80, "high": 81,
           "low": 79, "close": 80, "volume": 10}
    calculation = [verified["candles"][0], pre, verified["candles"][-1]]
    tooltips = []
    window = SimpleNamespace(
        stock=session.request.stock, _marking_generation=1, _marking_key="mark",
        _disable_marking=lambda **_kwargs: None,
        marking_display_check=SimpleNamespace(setToolTip=tooltips.append),
    )
    key = id(window)
    flow._open_windows[key] = window
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = raw
    flow._request_generation[key] = 1
    flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)] = {
        "stock_code": "005930", "timeframe_key": "M1", "timeframe_minutes": 1,
        "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
        "display_only": True, "verified_nxt_eligible": True,
        "request_as_of": "2026-10-20T08:45:00+09:00", "candles": [deepcopy(pre)],
    }
    request = {
        "settings": session.request.settings_snapshot,
        "calculation": calculation, "display": deepcopy(calculation),
        "mixed": False, "generation": 1, "key": "mark", "range": (0, 2),
        "times": (calculation[0]["time"], calculation[-1]["time"]),
        "descriptors": (),
    }
    return flow, session, raw, window, request, tooltips


def run_marking(flow, window, request):
    seen, payloads = [], []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)

    class StopReplay:
        def evaluate(self, snapshot, **_kwargs):
            seen.append(snapshot)
            raise RuntimeError("STOP_AFTER_REPLAY_INPUT")

    flow._replay_factory = lambda _session: StopReplay()
    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)
    return seen, payloads


def test_marking_replays_verified_sor_with_sidecar_premarket_as_composite():
    flow, _session, _raw, window, request, _tooltips = marking_case()
    seen, payloads = run_marking(flow, window, request)

    assert len(seen) == 1, payloads[-1]["error"] if payloads else _tooltips
    assert seen[0].market_source == "VALIDATION_COMPOSITE"
    assert seen[0].market_data_identity == "005930_AL_NXT_PRE_VALIDATION"
    assert all(
        candle["time"] in row.values()
        for candle, row in zip(request["calculation"], seen[0].to_rows())
    )
    assert "STOP_AFTER_REPLAY_INPUT" in payloads[-1]["error"]


def test_verified_pre_arrival_routes_active_marking_to_source_revision(monkeypatch):
    flow, session, _raw, window, _request, _tooltips = marking_case()
    flow._validation_source_by_code[session.request.stock.code] = True
    window.marking_display_check.isChecked = lambda: True
    revisions = []
    monkeypatch.setattr(
        flow, "_start_marking_source_revision",
        lambda received_window, received_session: revisions.append(
            (received_window, received_session)
        ),
    )

    flow._apply_integrated_display_sidecar(window, session)

    assert revisions == [(window, session)]


def test_marking_rejects_unproved_or_changed_premarket_without_replay():
    flow, session, _raw, window, request, tooltips = marking_case()
    sidecar = flow._integrated_display_sidecars[
        flow._integrated_display_sidecar_key(session)
    ]
    sidecar["candles"][0]["close"] = 81
    seen, payloads = run_marking(flow, window, request)

    assert seen == []
    assert "MARKING_PREMARKET_SOURCE_NOT_VERIFIED" in tooltips[-1]


def test_marking_keeps_verified_tail_with_regular_display_subset():
    flow, _session, _raw, window, request, _tooltips = marking_case()
    request["display"] = [request["calculation"][0]]
    request["range"] = (0, 0)
    request["times"] = (request["display"][0]["time"],) * 2
    seen, payloads = run_marking(flow, window, request)

    assert len(seen) == 1, payloads[-1]["error"] if payloads else _tooltips
    assert "STOP_AFTER_REPLAY_INPUT" in payloads[-1]["error"]


def test_marking_rejects_display_row_absent_from_calculation():
    flow, _session, _raw, window, request, tooltips = marking_case()
    request["calculation"] = [request["calculation"][0], request["calculation"][-1]]
    seen, payloads = run_marking(flow, window, request)

    assert seen == []
    assert "MARKING_SOURCE_CHANGED" in (
        payloads[-1]["error"] if payloads else tooltips[-1]
    )


def test_marking_rejects_request_that_skips_a_current_sor_candle():
    flow, _session, raw, window, request, tooltips = marking_case()
    raw["candles"].insert(1, {
        "time": "20261020090000", "open": 90, "high": 91,
        "low": 89, "close": 90, "volume": 10,
    })
    identity = flow._pool_computation_source_identity(raw)
    raw["_dispatch_snapshot"] = (
        raw["candles"], raw["display_candles"], {}, identity,
    )
    seen, payloads = run_marking(flow, window, request)

    assert seen == []
    assert "MARKING_SOURCE_CHANGED" in (
        payloads[-1]["error"] if payloads else tooltips[-1]
    )
